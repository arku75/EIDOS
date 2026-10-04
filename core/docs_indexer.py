"""
core/docs_indexer.py — Indexa docs locales como knowledge_nodes

Fuentes:
- ~/EIDOS/openclaw/**/*.md  (OpenClaw docs, AGENTS.md, etc)
- ~/EIDOS/openclaw_master_skills/**/*.md  (skills marketplace)
- ~/MIS PROGRAMAS/hermes/**/*.md  (Hermes docs si está)
- man pages de comandos comunes

EIDOS aprende cómo usar OpenClaw, Hermes y herramientas del sistema.

Uso:
    from core.docs_indexer import index_docs
    stats = index_docs()
"""
from __future__ import annotations

import os
import re
import sqlite3
import time
import hashlib
import logging
import subprocess
from pathlib import Path

from core.paths import REPO_ROOT
from typing import Dict, Any, List, Tuple
from core.db import get_conn

log = logging.getLogger("eidos.docs_indexer")

EIDOS_ROOT = REPO_ROOT
BRAIN_DB   = Path.home() / ".eidos" / "evolution_brain.db"

# Fuentes de docs a indexar
DOC_SOURCES = [
    ("openclaw",      EIDOS_ROOT / "openclaw"),
    ("openclaw_skills", EIDOS_ROOT / "openclaw_master_skills"),
    ("hermes",        Path.home() / "MIS PROGRAMAS" / "hermes" / "hermes-agent-main"),
    ("vseidos",       EIDOS_ROOT / "VSEIDOS"),
    ("sessions",      Path.home() / ".eidos" / "sessions"),  # Sesiones Claude→Colony
]

# Comandos cuyas man pages indexar
COMMANDS_TO_INDEX = [
    "nmap", "curl", "git", "docker", "systemctl", "ss", "tcpdump",
    "ssh", "ps", "find", "grep", "awk", "sed", "tar", "rsync",
    "lsof", "nc", "openssl", "iptables", "dig", "host", "wget",
    "python3", "node", "npm", "ollama",
]

# Patrones a saltar (ruido)
SKIP_PATTERNS = (
    "node_modules", "__pycache__", ".git", "Backups", ".mypy_cache",
    "graphify-out", "external_repos", "test", "spec",
)

MAX_DOC_LENGTH = 500   # caracteres por nodo (resumen del doc)
MAX_DOCS_PER_SOURCE = 50  # límite por fuente para no saturar


def _make_node_id(concept: str) -> str:
    return hashlib.md5(concept.encode()).hexdigest()[:16]


def _insert_or_update(conn: sqlite3.Connection, concept: str, definition: str,
                      source: str, confidence: float = 0.85) -> bool:
    """Inserta o actualiza un knowledge_node. Devuelve True si nuevo."""
    node_id = _make_node_id(concept)
    try:
        existing = conn.execute(
            "SELECT id FROM knowledge_nodes WHERE concept = ?", (concept,)
        ).fetchone()
        now = time.time()
        if existing:
            conn.execute(
                "UPDATE knowledge_nodes SET definition=?, last_used=?, "
                "confidence=MAX(confidence,?) WHERE concept=?",
                (definition[:500], now, confidence, concept)
            )
            return False
        conn.execute(
            "INSERT INTO knowledge_nodes "
            "(id, concept, definition, source, confidence, created_at, last_used, usage_count) "
            "VALUES (?,?,?,?,?,?,?,1)",
            (node_id, concept[:120], definition[:500], source, confidence, now, now)
        )
        return True
    except Exception as e:
        log.debug("insert_or_update fallo: %s", e)
        return False


def _index_md_file(conn: sqlite3.Connection, md_path: Path,
                   source_label: str) -> int:
    """Indexa un .md extrayendo cabeceras + primer párrafo de cada sección."""
    try:
        with open(md_path, "r", encoding="utf-8") as f:
            content = f.read()
    except Exception:
        return 0

    if len(content) < 50:
        return 0

    new_nodes = 0
    rel_path = md_path.relative_to(EIDOS_ROOT.parent) if md_path.is_relative_to(EIDOS_ROOT.parent) else md_path
    file_name = md_path.stem

    # Estrategia: extraer cada sección "## ..." con su primer párrafo
    sections = re.split(r"\n##\s+", content)
    for i, section in enumerate(sections[:8]):  # max 8 secciones por doc
        section = section.strip()
        if len(section) < 30:
            continue
        # Cabecera (primera línea)
        lines = section.split("\n", 1)
        header = lines[0].strip("# ").strip()[:80]
        body = lines[1] if len(lines) > 1 else section
        # Tomar primeros ~400 chars de contenido útil
        body = re.sub(r"\n+", " ", body).strip()
        body = re.sub(r"\s+", " ", body)
        if len(body) < 30:
            continue
        concept = f"doc:{source_label}:{file_name}:{header[:50]}"
        definition = f"[{rel_path}] {body[:MAX_DOC_LENGTH]}"
        if _insert_or_update(conn, concept, definition, f"docs:{source_label}"):
            new_nodes += 1
    return new_nodes


def _index_man_page(conn: sqlite3.Connection, command: str) -> int:
    """Indexa la man page de un comando."""
    try:
        r = subprocess.run(
            ["man", "-P", "cat", command],
            capture_output=True, text=True, timeout=4,
            env={**os.environ, "MANPAGER": "cat", "PAGER": "cat"}
        )
        if r.returncode != 0 or len(r.stdout) < 200:
            return 0
        # Extraer la sección "DESCRIPTION" o "SYNOPSIS"
        text = r.stdout
        # Match "DESCRIPTION" or "SYNOPSIS"
        match = re.search(r"(SYNOPSIS|DESCRIPTION)\s*\n(.+?)(?=\n[A-Z]{4,}\s*\n|$)",
                          text, re.DOTALL)
        if match:
            desc = re.sub(r"\s+", " ", match.group(2)).strip()[:MAX_DOC_LENGTH]
        else:
            desc = re.sub(r"\s+", " ", text).strip()[:MAX_DOC_LENGTH]
        concept = f"comando:{command}"
        definition = f"[man {command}] {desc}"
        if _insert_or_update(conn, concept, definition, "docs:man"):
            return 1
    except Exception as e:
        log.debug("man %s: %s", command, e)
    return 0


def _walk_md_files(base_path: Path, max_files: int) -> List[Path]:
    """Encuentra .md respetando skips."""
    files = []
    if not base_path.exists():
        return []
    try:
        for md in base_path.rglob("*.md"):
            if any(p in str(md) for p in SKIP_PATTERNS):
                continue
            if md.stat().st_size > 200_000:  # >200KB → skip
                continue
            files.append(md)
            if len(files) >= max_files:
                break
    except Exception:
        pass  # error no crítico, continuar
    return files


def index_docs() -> Dict[str, Any]:
    """Indexa todas las fuentes. Devuelve stats."""
    t0 = time.time()
    stats = {
        "sources": {},
        "man_pages": 0,
        "total_new": 0,
        "elapsed_sec": 0,
    }

    if not BRAIN_DB.exists():
        return {"error": "Brain DB no existe"}

    try:
        conn = get_conn(BRAIN_DB, timeout=10)
        conn.execute("PRAGMA journal_mode=WAL")

        for source_label, source_path in DOC_SOURCES:
            if not source_path.exists():
                stats["sources"][source_label] = {"new": 0, "skipped": "path no existe"}
                continue
            new_in_source = 0
            md_files = _walk_md_files(source_path, MAX_DOCS_PER_SOURCE)
            for md in md_files:
                new_in_source += _index_md_file(conn, md, source_label)
            stats["sources"][source_label] = {
                "files": len(md_files),
                "new": new_in_source,
            }
            stats["total_new"] += new_in_source
            log.info("Indexado %s: %d archivos, %d nodos nuevos",
                     source_label, len(md_files), new_in_source)

        # Man pages de comandos comunes
        for cmd in COMMANDS_TO_INDEX:
            stats["man_pages"] += _index_man_page(conn, cmd)
        stats["total_new"] += stats["man_pages"]

        conn.commit()
        pass  # S109: get_conn no necesita close()
        stats["elapsed_sec"] = round(time.time() - t0, 1)

        # Registrar en chronicle
        try:
            from core.colony_chronicle import get_chronicle
            get_chronicle().record(
                "docs_indexer", "indexed",
                f"{stats['total_new']} nodos nuevos en {stats['elapsed_sec']}s",
                metadata={"sources": list(stats["sources"].keys()),
                          "man_pages": stats["man_pages"]},
                importance=0.7,
            )
        except Exception:
            pass  # error no crítico, continuar
        log.info("Docs indexados: %d nodos nuevos en %.1fs",
                 stats["total_new"], stats["elapsed_sec"])
        return stats

    except Exception as e:
        log.exception("index_docs falló")
        return {"error": str(e)}


if __name__ == "__main__":
    import json
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    result = index_docs()
    print(json.dumps(result, indent=2, ensure_ascii=False))
