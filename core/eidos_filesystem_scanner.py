"""
core/eidos_filesystem_scanner.py — Autarquía Ontológica [S85 Fase 1.1]

Escáner del filesystem local de Kali/Luka. Convierte el entorno en conocimiento
interno del grafo neuronal SIN internet ni APIs externas.

Capacidades:
  - Lectura de eBooks (EPUB vía zipfile+xml, PDF vía PyPDF2, TXT nativo)
  - Escaneo de logs del sistema (/var/log/)
  - Metadatos de imágenes (PIL/Pillow)
  - Estructura de directorios relevantes
  - Ingesta selectiva al grafo neuronal

Uso:
    scanner = get_filesystem_scanner()
    results = scanner.scan(paths=["~/Descargas", "/var/log"], max_depth=2)
    for node in scanner.to_knowledge_nodes(results):
        reasoner.inject_node(node)
"""

from __future__ import annotations

import json
import logging
import os
import re
import sqlite3
import time
import xml.etree.ElementTree as ET
import zipfile
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Dict, List, Optional, Set
from core.db import get_conn

log = logging.getLogger("eidos.fsscanner")

BRAIN_DB = Path.home() / ".eidos" / "evolution_brain.db"
SCANNER_STATE = Path.home() / ".eidos" / "fsscanner_state.json"

# ── Extensiones y paths de interés ──────────────────────────────────────────

TEXT_EXTENSIONS = {".txt", ".md", ".log", ".rst", ".cfg", ".conf", ".ini",
                   ".toml", ".yaml", ".yml", ".json", ".xml", ".html",
                   ".py", ".sh", ".bash", ".zsh", ".js", ".ts", ".css"}

EBOOK_EXTENSIONS = {".epub", ".pdf", ".mobi"}

IMAGE_EXTENSIONS = {".png", ".jpg", ".jpeg", ".gif", ".bmp", ".webp", ".tiff"}

SYSTEM_LOG_PATHS = [
    "/var/log/syslog",
    "/var/log/kern.log",
    "/var/log/auth.log",
    "/var/log/dpkg.log",
    "/var/log/apt/history.log",
    "/var/log/journal/",
]

# Directorios a ignorar (system noise)
IGNORED_DIRS = {
    "/proc", "/sys", "/dev", "/run", "/snap", "/var/cache",
    "/usr/lib", "/usr/share", "/lib", "/lib64", "/boot",
    "node_modules", ".git", "__pycache__", ".cache", ".local/share",
}


class FileSystemScanner:
    """Escáner offline del filesystem local → conocimiento interno."""

    def __init__(self):
        self._state = self._load_state()
        self._scan_count = 0

    # ── Escaneo principal ────────────────────────────────────────────────────

    def scan(self, paths: List[str] = None, *,
             max_depth: int = 3,
             max_files_per_dir: int = 100,
             include_ebooks: bool = True,
             include_logs: bool = True,
             include_images: bool = True,
             include_text: bool = True,
             ) -> Dict[str, Any]:
        """Escanea paths locales y retorna resultados estructurados.

        Retorna:
            {
                "files_found": int,
                "ebooks_parsed": int,
                "logs_analyzed": int,
                "images_catalogued": int,
                "items": [{"path": str, "type": str, "content_preview": str, ...}, ...],
                "elapsed_s": float,
            }
        """
        t0 = time.time()
        if paths is None:
            paths = ["~/Descargas", "~/Documentos", "~/Escritorio"]

        resolved = [str(Path(p).expanduser().resolve()) for p in paths]
        items = []
        ebooks_parsed = 0
        logs_analyzed = 0
        images_catalogued = 0

        for root_path in resolved:
            if not os.path.exists(root_path):
                continue

            if os.path.isfile(root_path):
                item = self._scan_file(Path(root_path))
                if item:
                    items.append(item)
                    if item["type"] == "ebook":
                        ebooks_parsed += 1
                    elif item["type"] == "image":
                        images_catalogued += 1
                continue

            # Walk directory
            for dirpath, dirnames, filenames in os.walk(root_path):
                # Limitar profundidad
                depth = dirpath[len(root_path):].count(os.sep)
                if depth >= max_depth:
                    dirnames.clear()
                    continue

                # Ignorar directorios de sistema
                dirnames[:] = [d for d in dirnames
                              if os.path.join(dirpath, d) not in IGNORED_DIRS
                              and d not in IGNORED_DIRS]

                for fname in filenames[:max_files_per_dir]:
                    fpath = Path(dirpath) / fname
                    item = self._scan_file(fpath)
                    if item:
                        items.append(item)
                        if item["type"] == "ebook":
                            ebooks_parsed += 1
                        elif item["type"] == "image":
                            images_catalogued += 1

        # Logs del sistema (si se solicitan)
        if include_logs:
            log_items = self._scan_system_logs()
            items.extend(log_items)
            logs_analyzed = len(log_items)

        # Actualizar estado
        self._scan_count += 1
        self._state["last_scan"] = time.time()
        self._state["total_scans"] = self._scan_count
        self._save_state()

        elapsed = time.time() - t0
        log.info("FileSystemScanner: %d items en %.1fs (ebooks=%d, logs=%d, images=%d)",
                 len(items), elapsed, ebooks_parsed, logs_analyzed, images_catalogued)

        return {
            "files_found": len(items),
            "ebooks_parsed": ebooks_parsed,
            "logs_analyzed": logs_analyzed,
            "images_catalogued": images_catalogued,
            "items": items,
            "elapsed_s": round(elapsed, 3),
        }

    def _scan_file(self, fpath: Path) -> Optional[Dict[str, Any]]:
        """Escanea un archivo individual."""
        if not fpath.exists() or fpath.is_symlink():
            return None

        suffix = fpath.suffix.lower()
        size_mb = fpath.stat().st_size / (1024 * 1024) if fpath.exists() else 0

        # Ignorar archivos > 100MB
        if size_mb > 100:
            return None

        try:
            # eBooks
            if suffix in EBOOK_EXTENSIONS:
                content = self._parse_ebook(fpath)
                if content:
                    return {
                        "path": str(fpath),
                        "type": "ebook",
                        "format": suffix.lstrip("."),
                        "name": fpath.stem[:120],
                        "size_mb": round(size_mb, 2),
                        "content_preview": content[:500],
                        "content_length": len(content),
                        "mtime": fpath.stat().st_mtime,
                    }

            # Imágenes
            elif suffix in IMAGE_EXTENSIONS:
                metadata = self._parse_image(fpath)
                if metadata:
                    return {
                        "path": str(fpath),
                        "type": "image",
                        "format": suffix.lstrip("."),
                        "name": fpath.stem[:120],
                        "size_mb": round(size_mb, 2),
                        "metadata": metadata,
                        "mtime": fpath.stat().st_mtime,
                    }

            # Texto
            elif suffix in TEXT_EXTENSIONS:
                content = self._read_text_file(fpath)
                if content:
                    return {
                        "path": str(fpath),
                        "type": "text",
                        "format": suffix.lstrip("."),
                        "name": fpath.stem[:120],
                        "size_mb": round(size_mb, 2),
                        "content_preview": content[:500],
                        "content_length": len(content),
                        "mtime": fpath.stat().st_mtime,
                    }

        except Exception as e:
            log.debug("scan_file %s: %s", fpath.name, e)

        return None

    # ── Parsers ───────────────────────────────────────────────────────────────

    def _parse_ebook(self, fpath: Path) -> Optional[str]:
        """Extrae texto de eBooks: EPUB (zip+xml), PDF (PyPDF2), TXT."""
        suffix = fpath.suffix.lower()

        if suffix == ".epub":
            return self._parse_epub(fpath)
        elif suffix == ".pdf":
            return self._parse_pdf(fpath)
        elif suffix == ".mobi":
            return self._parse_mobi(fpath)
        elif suffix == ".txt":
            return self._read_text_file(fpath)

        return None

    def _parse_epub(self, fpath: Path) -> Optional[str]:
        """Extrae texto de EPUB usando zipfile + xml (stdlib)."""
        try:
            with zipfile.ZipFile(fpath, 'r') as zf:
                # Encontrar archivos de contenido XHTML/HTML
                content_files = [n for n in zf.namelist()
                                if n.endswith(('.xhtml', '.html', '.htm'))
                                and not n.startswith('__')]

                texts = []
                for cf in content_files[:50]:  # máx 50 capítulos
                    try:
                        raw = zf.read(cf).decode('utf-8', errors='replace')
                        # Extraer texto limpio (sin HTML tags)
                        clean = re.sub(r'<[^>]+>', ' ', raw)
                        clean = re.sub(r'\s+', ' ', clean).strip()
                        if len(clean) > 50:
                            texts.append(clean)
                    except Exception:
                        continue

                if texts:
                    full = " ".join(texts)
                    log.info("EPUB: %s → %d chars (%d secciones)",
                            fpath.name, len(full), len(texts))
                    return full
        except Exception as e:
            log.debug("EPUB parse %s: %s", fpath.name, e)

        return None

    def _parse_pdf(self, fpath: Path) -> Optional[str]:
        """Extrae texto de PDF usando PyPDF2 si está disponible."""
        try:
            from PyPDF2 import PdfReader
            reader = PdfReader(str(fpath))
            texts = []
            for page in reader.pages[:100]:  # máx 100 páginas
                text = page.extract_text()
                if text and len(text.strip()) > 20:
                    texts.append(text.strip())
            if texts:
                full = " ".join(texts)
                log.info("PDF: %s → %d chars (%d páginas)",
                        fpath.name, len(full), len(texts))
                return full
        except ImportError:
            log.debug("PyPDF2 no instalado — skipping PDF: %s", fpath.name)
        except Exception as e:
            log.debug("PDF parse %s: %s", fpath.name, e)

        return None

    def _parse_mobi(self, fpath: Path) -> Optional[str]:
        """Intenta extraer texto de MOBI (formato PalmDOC)."""
        try:
            raw = fpath.read_bytes()
            # Buscar bloques de texto imprimible en el binario
            text_blocks = re.findall(rb'[\x20-\x7E\xC0-\xFF]{100,}', raw)
            if text_blocks:
                text = b"\n".join(text_blocks[:100]).decode('latin-1', errors='replace')
                clean = re.sub(r'[^\x20-\x7E\xA0-\xFF\n]', '', text)
                clean = re.sub(r'\n{3,}', '\n\n', clean)
                if len(clean) > 200:
                    log.info("MOBI: %s → %d chars (raw extract)", fpath.name, len(clean))
                    return clean
        except Exception as e:
            log.debug("MOBI parse %s: %s", fpath.name, e)
        return None

    def _read_text_file(self, fpath: Path) -> Optional[str]:
        """Lee archivo de texto plano con detección de encoding."""
        if fpath.stat().st_size > 50 * 1024 * 1024:  # 50MB límite
            return None
        try:
            raw = fpath.read_bytes()
            # Intentar UTF-8 primero, luego latin-1
            try:
                text = raw.decode('utf-8')
            except UnicodeDecodeError:
                text = raw.decode('latin-1', errors='replace')
            return text[:100_000]  # truncar a 100K chars
        except Exception:
            return None

    def _parse_image(self, fpath: Path) -> Optional[Dict[str, Any]]:
        """Extrae metadatos de imagen usando PIL (sin internet)."""
        try:
            from PIL import Image
            img = Image.open(fpath)
            meta = {
                "format": img.format,
                "size": img.size,  # (width, height)
                "mode": img.mode,
            }
            # EXIF si existe
            exif = img.getexif()
            if exif:
                for k, v in exif.items():
                    if isinstance(v, (str, int, float)):
                        meta[str(k)] = v
            img.close()
            return meta
        except ImportError:
            return {"note": "PIL no disponible"}
        except Exception as e:
            log.debug("Image parse %s: %s", fpath.name, e)
            return None

    def _scan_system_logs(self) -> List[Dict[str, Any]]:
        """Lee logs del sistema y extrae eventos recientes."""
        items = []
        for log_path in SYSTEM_LOG_PATHS:
            p = Path(log_path)
            if not p.exists():
                continue

            try:
                if p.is_file():
                    content = self._read_text_file(p)
                    if content:
                        # Extraer últimas 200 líneas
                        lines = content.splitlines()[-200:]
                        items.append({
                            "path": str(p),
                            "type": "system_log",
                            "format": "log",
                            "name": p.name,
                            "content_preview": "\n".join(lines[-20:])[:500],
                            "content_length": len(content),
                            "line_count": len(lines),
                            "mtime": p.stat().st_mtime,
                        })
                elif p.is_dir():
                    # Leer archivos recientes del directorio
                    for log_file in sorted(p.glob("*.log"), key=lambda x: x.stat().st_mtime, reverse=True)[:5]:
                        content = self._read_text_file(log_file)
                        if content:
                            items.append({
                                "path": str(log_file),
                                "type": "system_log",
                                "format": "log",
                                "name": log_file.name,
                                "content_preview": content[:500],
                                "content_length": len(content),
                                "mtime": log_file.stat().st_mtime,
                            })
            except Exception as e:
                log.debug("system_log %s: %s", log_path, e)

        return items

    # ── Conversión a knowledge_nodes ──────────────────────────────────────────

    def to_knowledge_nodes(self, scan_result: Dict[str, Any]) -> List[Dict[str, Any]]:
        """Convierte resultados de escaneo en nodos para el grafo neuronal."""
        nodes = []
        for item in scan_result.get("items", []):
            node_id = f"fs:{item['type']}:{item['name']}"[:150]
            concept = item["name"][:200]
            definition = item.get("content_preview", "")[:500]
            category = f"local_{item['type']}"
            source = "filesystem_scanner"

            nodes.append({
                "id": node_id,
                "concept": concept,
                "definition": definition,
                "category": category,
                "source": source,
                "confidence": 0.7,
                "metadata": {
                    "path": item["path"],
                    "format": item.get("format", ""),
                    "size_mb": item.get("size_mb", 0),
                    "scan_id": self._scan_count,
                },
            })
        return nodes

    def inject_to_graph(self, scan_result: Dict[str, Any]) -> int:
        """Inyecta resultados de escaneo directamente en el grafo neuronal."""
        nodes = self.to_knowledge_nodes(scan_result)
        if not nodes:
            return 0

        try:
            conn = get_conn(BRAIN_DB, timeout=10)
            conn.execute("PRAGMA busy_timeout=10000")
            inserted = 0
            for node in nodes:
                try:
                    conn.execute(
                        "INSERT OR IGNORE INTO knowledge_nodes (id, concept, definition, category, source, confidence) "
                        "VALUES (?, ?, ?, ?, ?, ?)",
                        (node["id"], node["concept"], node["definition"],
                         node["category"], node["source"], node["confidence"])
                    )
                    if conn.total_changes > 0:
                        inserted += 1
                except Exception:
                    pass
            conn.commit()

            log.info("FileSystemScanner: %d/%d nodos inyectados al grafo", inserted, len(nodes))
            return inserted
        except Exception as e:
            log.warning("FileSystemScanner inject: %s", e)
            return 0

    # ── Estado ────────────────────────────────────────────────────────────────

    def _load_state(self) -> Dict[str, Any]:
        try:
            if SCANNER_STATE.exists():
                return json.loads(SCANNER_STATE.read_text())
        except Exception:
            pass
        return {"last_scan": 0, "total_scans": 0}

    def _save_state(self):
        try:
            SCANNER_STATE.parent.mkdir(parents=True, exist_ok=True)
            SCANNER_STATE.write_text(json.dumps(self._state, indent=2))
        except Exception:
            pass

    def stats(self) -> Dict[str, Any]:
        return {
            "scans_completed": self._scan_count,
            "last_scan": self._state.get("last_scan", 0),
        }


# ── Singleton ─────────────────────────────────────────────────────────────────

_scanner: Optional[FileSystemScanner] = None


def get_filesystem_scanner() -> FileSystemScanner:
    global _scanner
    if _scanner is None:
        _scanner = FileSystemScanner()
    return _scanner


# ── CLI ───────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    import argparse
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    p = argparse.ArgumentParser(description="EIDOS FileSystem Scanner")
    p.add_argument("paths", nargs="*", default=["~/Descargas"])
    p.add_argument("--max-depth", type=int, default=2)
    p.add_argument("--inject", action="store_true", help="Inyectar al grafo")
    p.add_argument("--stats", action="store_true")
    args = p.parse_args()

    scanner = get_filesystem_scanner()

    if args.stats:
        print(json.dumps(scanner.stats(), indent=2, ensure_ascii=False))
    else:
        result = scanner.scan(args.paths, max_depth=args.max_depth)
        print(f"Escaneados {result['files_found']} archivos en {result['elapsed_s']}s")
        print(f"  eBooks: {result['ebooks_parsed']}")
        print(f"  Logs:   {result['logs_analyzed']}")
        print(f"  Images: {result['images_catalogued']}")

        if args.inject:
            n = scanner.inject_to_graph(result)
            print(f"  Inyectados al grafo: {n} nodos")

        for item in result["items"][:10]:
            print(f"  [{item['type']:12s}] {item['name'][:70]:70s} {item.get('content_length', 0)} chars")
