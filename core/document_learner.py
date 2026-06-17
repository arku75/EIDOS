"""
core/document_learner.py — EIDOS lee y aprende de cualquier tipo de documento.

Soporta:
  - PDF (pdftotext + pdfminer como fallback)
  - EPUB / MOBI
  - ZIP / TAR.GZ — extrae un archivo a la vez, aprende, borra lo extraído (mantiene el .zip)
  - GitHub repos — lee README, código fuente, docs (sin clonar completo)
  - DOCX / TXT / MD
  - URLs directas (delegado a estudio_exhaustivo)

Política de archivos:
  - NUNCA borrar el ZIP/TAR original
  - Borrar solo los archivos extraídos temporalmente
  - Todo lo aprendido va a brain.db + ChromaDB

Uso:
  from core.document_learner import DocumentLearner
  dl = DocumentLearner()
  dl.learn_pdf('/path/to/libro.pdf', 'Título del libro')
  dl.learn_zip('/path/to/docs.zip', 'Nombre del paquete')
  dl.learn_github('https://github.com/user/repo')
  dl.learn_file('/path/to/cualquier.archivo')
"""
from __future__ import annotations

import json
import logging
import os
import re
import shutil
import sqlite3
from core.db import get_conn
import subprocess
import tempfile
import time
import zipfile
import tarfile
from pathlib import Path
from typing import Optional, List, Dict, Any

log = logging.getLogger("eidos.document_learner")

BRAIN_DB  = Path.home() / '.eidos' / 'evolution_brain.db'
CHROMA_DB = Path.home() / '.eidos' / 'chroma'
TEMP_DIR  = Path(tempfile.gettempdir()) / 'eidos_extract'

# Extensiones de código fuente que vale la pena leer
CODE_EXTS = {'.py', '.js', '.ts', '.rs', '.go', '.java', '.cpp', '.c',
             '.sh', '.bash', '.md', '.txt', '.rst', '.yaml', '.yml',
             '.json', '.toml', '.ini', '.cfg', '.html', '.css'}

# Extensiones que ignorar dentro de archivos comprimidos
SKIP_EXTS = {'.png', '.jpg', '.jpeg', '.gif', '.svg', '.ico', '.woff',
             '.woff2', '.ttf', '.otf', '.eot', '.mp4', '.mp3', '.avi',
             '.exe', '.dll', '.so', '.pyc', '.pyo', '.egg-info',
             '.lock', '.min.js', '.min.css', '.map'}

# Tamaño máximo de texto a guardar por documento (chars)
MAX_TEXT = 4000


# ── Guardado en brain ──────────────────────────────────────────────────────────

def _save_brain(concept: str, text: str, source: str,
                category: str = 'document') -> bool:
    try:
        db = get_conn(BRAIN_DB, timeout=10)
        db.execute('PRAGMA journal_mode=WAL')
        ex = db.execute('SELECT rowid FROM knowledge_nodes WHERE concept=? LIMIT 1',
                        (concept,)).fetchone()
        if ex:
            db.execute('UPDATE knowledge_nodes SET definition=?, source=?, confidence=0.9 '
                       'WHERE concept=?', (text[:MAX_TEXT], source, concept))
        else:
            db.execute('INSERT INTO knowledge_nodes '
                       '(concept, definition, category, confidence, source, created_at) '
                       'VALUES (?,?,?,?,?,?)',
                       (concept, text[:MAX_TEXT], category, 0.9, source, time.time()))
        db.commit()
        db.close()
        return True
    except Exception as e:
        log.error("brain save %s: %s", concept, e)
        return False


def _save_chroma(concept: str, text: str, source: str) -> bool:
    try:
        import chromadb
        client = chromadb.PersistentClient(path=str(CHROMA_DB))
        col = client.get_or_create_collection('eidos_knowledge')
        col.upsert(ids=[concept],
                   documents=[text[:6000]],
                   metadatas=[{'source': source, 'concept': concept,
                               'ts': int(time.time())}])
        return True
    except Exception:
        return False


def _save(concept: str, text: str, source: str, category: str = 'document') -> int:
    """Guarda en brain.db + ChromaDB. Retorna chars guardados."""
    _save_brain(concept, text, source, category)
    _save_chroma(concept, text, source)
    return len(text)


# ── Extracción de texto ────────────────────────────────────────────────────────

def _text_from_pdf(path: Path) -> str:
    """Extrae texto de un PDF con pdftotext (rápido) o pdfminer (fallback)."""
    # Método 1: pdftotext (poppler, más rápido y limpio)
    try:
        r = subprocess.run(['pdftotext', '-layout', str(path), '-'],
                           capture_output=True, text=True, timeout=30)
        if r.returncode == 0 and r.stdout.strip():
            return r.stdout[:100_000]
    except Exception:
        pass
    # Método 2: pdfminer.six
    try:
        from pdfminer.high_level import extract_text
        return extract_text(str(path))[:100_000]
    except Exception as e:
        log.warning("pdfminer %s: %s", path.name, e)
    return ''


def _text_from_epub(path: Path) -> str:
    """Extrae texto de un EPUB."""
    try:
        from ebooklib import epub
        import html as _html
        book = epub.read_epub(str(path), options={'ignore_ncx': True})
        parts = []
        for item in book.get_items():
            if item.get_type() == 9:  # ITEM_DOCUMENT
                raw = item.get_content().decode('utf-8', errors='ignore')
                text = re.sub(r'<[^>]+>', ' ', raw)
                text = _html.unescape(re.sub(r'\s+', ' ', text).strip())
                if text:
                    parts.append(text)
        return ' '.join(parts)[:100_000]
    except Exception as e:
        log.warning("epub %s: %s", path.name, e)
        return ''


def _text_from_docx(path: Path) -> str:
    """Extrae texto de un DOCX."""
    try:
        import docx as _docx
        doc = _docx.Document(str(path))
        return '\n'.join(p.text for p in doc.paragraphs if p.text.strip())[:100_000]
    except Exception as e:
        log.warning("docx %s: %s", path.name, e)
        return ''


def _text_from_file(path: Path) -> str:
    """Lee texto plano / código fuente / markdown."""
    try:
        return path.read_text(errors='ignore')[:100_000]
    except Exception:
        return ''


def _text_from_mobi(path: Path) -> str:
    """Extrae texto de un MOBI/AZW usando mobi o calibre como fallback."""
    # Intentar con el paquete mobi
    try:
        import mobi
        _, unpacked = mobi.extract(str(path))
        text_parts = []
        for f in Path(unpacked).rglob('*'):
            if f.suffix.lower() in ('.html', '.htm', '.txt'):
                raw = f.read_text(errors='ignore')
                text = re.sub(r'<[^>]+>', ' ', raw)
                text = re.sub(r'\s+', ' ', text).strip()
                if text:
                    text_parts.append(text)
        return ' '.join(text_parts)[:100_000]
    except Exception:
        pass
    # Fallback: calibre ebook-convert → txt
    try:
        tmp = Path(tempfile.mktemp(suffix='.txt'))
        r = subprocess.run(['ebook-convert', str(path), str(tmp)],
                           capture_output=True, timeout=60)
        if tmp.exists() and tmp.stat().st_size > 0:
            text = tmp.read_text(errors='ignore')[:100_000]
            tmp.unlink()
            return text
    except Exception:
        pass
    # Último recurso: strings del archivo
    try:
        r = subprocess.run(['strings', str(path)], capture_output=True, timeout=10)
        text = r.stdout.decode('utf-8', errors='ignore')
        lines = [l for l in text.splitlines() if len(l) > 20]
        return '\n'.join(lines[:500])
    except Exception:
        return ''


def _extract_text(path: Path) -> str:
    """Dispatch de extracción según extensión."""
    ext = path.suffix.lower()
    if ext == '.pdf':
        return _text_from_pdf(path)
    if ext in ('.epub', '.epub3'):
        return _text_from_epub(path)
    if ext in ('.mobi', '.azw', '.azw3'):
        return _text_from_mobi(path)
    if ext in ('.docx', '.doc'):
        return _text_from_docx(path)
    if ext in CODE_EXTS or ext in ('.txt', '.text', '.log'):
        return _text_from_file(path)
    return ''


# ── Chunking inteligente ───────────────────────────────────────────────────────

def _chunk_text(text: str, chunk_size: int = 3000) -> List[str]:
    """Divide texto largo en chunks que respetan párrafos."""
    if len(text) <= chunk_size:
        return [text]
    chunks = []
    while text:
        if len(text) <= chunk_size:
            chunks.append(text)
            break
        # Cortar en el último salto de línea antes del límite
        cut = text.rfind('\n', 0, chunk_size)
        if cut < 100:
            cut = chunk_size
        chunks.append(text[:cut].strip())
        text = text[cut:].strip()
    return chunks


# ── API pública ────────────────────────────────────────────────────────────────

class DocumentLearner:
    """EIDOS lee y aprende de cualquier tipo de documento."""

    def __init__(self):
        TEMP_DIR.mkdir(parents=True, exist_ok=True)

    # ── PDF ────────────────────────────────────────────────────────────────────

    def learn_pdf(self, path: str, title: str = '', category: str = 'document') -> Dict:
        """Lee un PDF, lo aprende en chunks y lo guarda en brain."""
        p = Path(path)
        if not p.exists():
            return {'ok': False, 'error': f'No existe: {path}'}

        title = title or p.stem
        log.info("📄 Leyendo PDF: %s", title)

        text = _text_from_pdf(p)
        if not text or len(text) < 50:
            return {'ok': False, 'error': 'No se pudo extraer texto del PDF'}

        chunks = _chunk_text(text)
        saved = 0
        for i, chunk in enumerate(chunks):
            concept = f"doc:{p.stem.lower().replace(' ', '_')}:chunk{i}"
            saved += _save(concept, chunk, str(p), category)

        # Resumen del documento completo
        summary_concept = f"doc:{p.stem.lower().replace(' ', '_')}:summary"
        summary = f"Libro/documento: '{title}'. Total: {len(text):,} chars, {len(chunks)} secciones. Primeras 500: {text[:500]}"
        _save(summary_concept, summary, str(p), category)

        log.info("✅ PDF '%s': %d chars, %d chunks → brain", title, len(text), len(chunks))
        return {'ok': True, 'title': title, 'chars': len(text), 'chunks': len(chunks)}

    # ── ZIP (extrae uno a uno, aprende, borra extraído, mantiene ZIP) ──────────

    def learn_zip(self, path: str, name: str = '', category: str = 'document',
                  max_files: int = 200) -> Dict:
        """
        Lee un ZIP/TAR extrayendo archivos uno a uno.
        Aprende cada archivo y borra el extraído INMEDIATAMENTE.
        NUNCA borra el ZIP/TAR original.
        """
        p = Path(path)
        if not p.exists():
            return {'ok': False, 'error': f'No existe: {path}'}

        name = name or p.stem
        extract_base = TEMP_DIR / f"zip_{int(time.time())}"
        extract_base.mkdir(parents=True, exist_ok=True)

        log.info("📦 Leyendo archivo comprimido: %s", name)

        learned = 0
        skipped = 0
        errors = 0

        try:
            # Determinar tipo
            if zipfile.is_zipfile(str(p)):
                members = self._learn_zip_members(p, name, extract_base, category, max_files)
            elif tarfile.is_tarfile(str(p)):
                members = self._learn_tar_members(p, name, extract_base, category, max_files)
            else:
                return {'ok': False, 'error': 'No es ZIP ni TAR'}

            learned = members.get('learned', 0)
            skipped = members.get('skipped', 0)
            errors = members.get('errors', 0)

        finally:
            # Borrar directorio temporal (archivos extraídos) — NUNCA el ZIP original
            if extract_base.exists():
                shutil.rmtree(str(extract_base), ignore_errors=True)
            log.info("🗑 Archivos temporales eliminados (ZIP original intacto: %s)", p.name)

        # Guardar índice del ZIP en brain
        idx_concept = f"zip:{name.lower().replace(' ', '_')}:index"
        idx_text = f"Archivo comprimido '{name}' ({p.name}). Archivos aprendidos: {learned}, omitidos: {skipped}. Ruta: {p}"
        _save(idx_concept, idx_text, str(p), category)

        log.info("✅ ZIP '%s': %d archivos aprendidos, %d omitidos", name, learned, skipped)
        return {'ok': True, 'name': name, 'learned': learned, 'skipped': skipped, 'errors': errors}

    def _learn_zip_members(self, zip_path: Path, name: str, extract_base: Path,
                           category: str, max_files: int) -> Dict:
        learned = skipped = errors = 0
        with zipfile.ZipFile(str(zip_path), 'r') as zf:
            members = [m for m in zf.namelist() if not m.endswith('/')]
            log.info("  ZIP contiene %d archivos", len(members))

            for i, member in enumerate(members[:max_files]):
                member_path = Path(member)
                ext = member_path.suffix.lower()

                if ext in SKIP_EXTS:
                    skipped += 1
                    continue
                if ext not in CODE_EXTS and ext not in {'.pdf', '.epub', '.docx', '.txt', '.md'}:
                    skipped += 1
                    continue

                # Extraer solo este archivo
                extract_path = extract_base / member_path.name
                try:
                    data = zf.read(member)
                    extract_path.write_bytes(data)

                    text = _extract_text(extract_path)
                    if text and len(text) > 50:
                        concept = f"zip:{name}:{member_path.stem.lower()[:40]}"
                        _save(concept, text, f"{zip_path.name}/{member}", category)
                        learned += 1
                        log.debug("  ✅ %s: %d chars", member_path.name, len(text))
                    else:
                        skipped += 1

                except Exception as e:
                    log.debug("  ⚠ %s: %s", member, e)
                    errors += 1
                finally:
                    # Borrar el archivo extraído inmediatamente
                    if extract_path.exists():
                        extract_path.unlink()

        return {'learned': learned, 'skipped': skipped, 'errors': errors}

    def _learn_tar_members(self, tar_path: Path, name: str, extract_base: Path,
                           category: str, max_files: int) -> Dict:
        learned = skipped = errors = 0
        with tarfile.open(str(tar_path), 'r:*') as tf:
            members = [m for m in tf.getmembers() if m.isfile()]
            log.info("  TAR contiene %d archivos", len(members))

            for member in members[:max_files]:
                member_path = Path(member.name)
                ext = member_path.suffix.lower()

                if ext in SKIP_EXTS:
                    skipped += 1
                    continue
                if ext not in CODE_EXTS and ext not in {'.pdf', '.epub', '.docx', '.txt', '.md'}:
                    skipped += 1
                    continue

                extract_path = extract_base / member_path.name
                try:
                    f = tf.extractfile(member)
                    if not f:
                        skipped += 1
                        continue
                    extract_path.write_bytes(f.read())
                    f.close()

                    text = _extract_text(extract_path)
                    if text and len(text) > 50:
                        concept = f"zip:{name}:{member_path.stem.lower()[:40]}"
                        _save(concept, text, f"{tar_path.name}/{member.name}", category)
                        learned += 1
                    else:
                        skipped += 1

                except Exception as e:
                    log.debug("  ⚠ %s: %s", member.name, e)
                    errors += 1
                finally:
                    if extract_path.exists():
                        extract_path.unlink()

        return {'learned': learned, 'skipped': skipped, 'errors': errors}

    # ── GitHub ─────────────────────────────────────────────────────────────────

    def learn_github(self, repo_url: str, max_files: int = 100) -> Dict:
        """
        Lee un repositorio de GitHub via API (sin clonar completo).
        Lee README, docs/, src/ y archivos .py/.md de interés.
        """
        import requests as _req

        # Normalizar URL
        match = re.search(r'github\.com/([^/]+)/([^/\s#?]+)', repo_url)
        if not match:
            return {'ok': False, 'error': 'URL de GitHub inválida'}

        owner = match.group(1)
        repo = match.group(2)
        if repo.endswith('.git'):
            repo = repo[:-4]
        api_base = f"https://api.github.com/repos/{owner}/{repo}"
        name = f"github:{owner}/{repo}"

        log.info("🐙 GitHub: %s/%s", owner, repo)

        headers = {'Accept': 'application/vnd.github.v3+json',
                   'User-Agent': 'EIDOS-Learner/1.0'}

        learned = 0

        # 1. Metadatos del repo
        try:
            r = _req.get(api_base, headers=headers, timeout=15)
            if r.status_code == 200:
                meta = r.json()
                summary = (
                    f"GitHub repo: {owner}/{repo}\n"
                    f"Descripción: {meta.get('description', 'N/A')}\n"
                    f"Stars: {meta.get('stargazers_count', 0):,}\n"
                    f"Lenguaje principal: {meta.get('language', 'N/A')}\n"
                    f"Topics: {', '.join(meta.get('topics', []))}\n"
                    f"URL: {repo_url}"
                )
                _save(f"{name}:meta", summary, repo_url, 'github')
                learned += 1
        except Exception as e:
            log.debug("GitHub meta: %s", e)

        # 2. README
        try:
            r = _req.get(f"{api_base}/readme", headers=headers, timeout=15)
            if r.status_code == 200:
                import base64
                content = base64.b64decode(r.json()['content']).decode('utf-8', errors='ignore')
                _save(f"{name}:readme", content[:MAX_TEXT * 2], repo_url, 'github')
                learned += 1
                log.info("  ✅ README: %d chars", len(content))
        except Exception as e:
            log.debug("GitHub README: %s", e)

        # 3. Árbol de archivos y leer los más relevantes
        try:
            r = _req.get(f"{api_base}/git/trees/HEAD?recursive=1",
                         headers=headers, timeout=15)
            if r.status_code == 200:
                tree = r.json().get('tree', [])
                # Priorizar: docs/, *.md, *.py principales, src/
                priority = []
                for item in tree:
                    if item['type'] != 'blob':
                        continue
                    p = Path(item['path'])
                    ext = p.suffix.lower()
                    if ext in SKIP_EXTS:
                        continue
                    score = 0
                    if ext == '.md':
                        score += 10
                    if ext == '.py' and p.parent.parts == ():
                        score += 8  # Python en raíz
                    if 'doc' in str(p).lower() or 'readme' in str(p).lower():
                        score += 5
                    if 'test' in str(p).lower() or 'example' in str(p).lower():
                        score += 3
                    if ext in {'.py', '.js', '.ts', '.rs', '.go'}:
                        score += 2
                    if score > 0:
                        priority.append((score, item))

                priority.sort(key=lambda x: -x[0])

                for score, item in priority[:max_files]:
                    sha = item.get('sha', '')
                    path_str = item['path']
                    try:
                        blob_r = _req.get(f"{api_base}/git/blobs/{sha}",
                                         headers=headers, timeout=10)
                        if blob_r.status_code == 200:
                            import base64 as _b64
                            blob = blob_r.json()
                            if blob.get('encoding') == 'base64':
                                content = _b64.b64decode(blob['content']).decode('utf-8', errors='ignore')
                            else:
                                content = blob.get('content', '')

                            if content and len(content) > 30:
                                concept = f"{name}:{Path(path_str).stem.lower()[:40]}"
                                _save(concept, content, f"{repo_url}/blob/main/{path_str}", 'github')
                                learned += 1
                                log.debug("  ✅ %s: %d chars", path_str, len(content))
                        time.sleep(0.2)  # respetar rate limit GitHub
                    except Exception as e:
                        log.debug("  blob %s: %s", path_str, e)

        except Exception as e:
            log.debug("GitHub tree: %s", e)

        # Guardar índice
        idx = f"GitHub repo aprendido: {owner}/{repo}. {learned} archivos indexados. URL: {repo_url}"
        _save(f"{name}:index", idx, repo_url, 'github')

        log.info("✅ GitHub %s/%s: %d archivos aprendidos", owner, repo, learned)
        return {'ok': True, 'repo': f"{owner}/{repo}", 'learned': learned}

    # ── Archivo genérico ───────────────────────────────────────────────────────

    def learn_file(self, path: str, name: str = '', category: str = 'document') -> Dict:
        """Lee cualquier archivo según su extensión."""
        p = Path(path)
        if not p.exists():
            return {'ok': False, 'error': f'No existe: {path}'}

        ext = p.suffix.lower()
        name = name or p.stem

        if ext == '.pdf':
            return self.learn_pdf(path, name, category)
        if ext in ('.zip',):
            return self.learn_zip(path, name, category)
        if ext in ('.tar', '.gz', '.tgz', '.bz2', '.xz'):
            return self.learn_zip(path, name, category)
        if ext in ('.epub', '.epub3'):
            return self._learn_epub(p, name, category)
        if ext in CODE_EXTS or ext in {'.txt', '.text', '.log', '.md'}:
            text = _text_from_file(p)
            if text:
                concept = f"file:{name.lower().replace(' ', '_')}"
                chars = _save(concept, text, str(p), category)
                return {'ok': True, 'name': name, 'chars': chars}

        return {'ok': False, 'error': f'Extensión no soportada: {ext}'}

    def _learn_epub(self, p: Path, name: str, category: str) -> Dict:
        text = _text_from_epub(p)
        if not text:
            return {'ok': False, 'error': 'No se pudo extraer texto del EPUB'}
        chunks = _chunk_text(text)
        for i, chunk in enumerate(chunks):
            concept = f"doc:{name.lower().replace(' ', '_')}:chunk{i}"
            _save(concept, chunk, str(p), category)
        log.info("✅ EPUB '%s': %d chars, %d chunks", name, len(text), len(chunks))
        return {'ok': True, 'name': name, 'chars': len(text), 'chunks': len(chunks)}

    # ── Scan de directorio ─────────────────────────────────────────────────────

    def learn_directory(self, path: str, name: str = '',
                        recursive: bool = True, max_files: int = 500) -> Dict:
        """
        Escanea un directorio y aprende todos los documentos relevantes.
        No modifica ni borra nada.
        """
        d = Path(path)
        if not d.is_dir():
            return {'ok': False, 'error': f'No es un directorio: {path}'}

        name = name or d.name
        pattern = '**/*' if recursive else '*'
        learned = skipped = 0

        for p in list(d.glob(pattern))[:max_files]:
            if not p.is_file():
                continue
            ext = p.suffix.lower()
            if ext in SKIP_EXTS:
                skipped += 1
                continue
            result = self.learn_file(str(p), p.stem, name)
            if result.get('ok'):
                learned += 1
            else:
                skipped += 1

        log.info("✅ Directorio '%s': %d aprendidos, %d omitidos", name, learned, skipped)
        return {'ok': True, 'name': name, 'learned': learned, 'skipped': skipped}


# ── Instancia global ───────────────────────────────────────────────────────────

_learner: Optional[DocumentLearner] = None


def get_learner() -> DocumentLearner:
    global _learner
    if _learner is None:
        _learner = DocumentLearner()
    return _learner
