"""
EIDOS core/doc_learner.py — Document & Media Learning
======================================================
Aprende de documentos locales: ePub, PDF, TXT, Markdown, HTML.
Extrae contenido, resume con LLM, almacena en BrainMemory.

Uso:
    from core.doc_learner import get_doc_learner
    dl = get_doc_learner()
    dl.learn_file("/path/to/book.epub")
    dl.learn_file("/path/to/paper.pdf")
    dl.learn_directory("~/Documents/tech/")
"""
from __future__ import annotations

import json
import logging
import os
import sqlite3
import time
import urllib.request
from pathlib import Path
from typing import Optional
from core.db import get_conn
from core.db import get_conn_ctx

log = logging.getLogger("eidos.doc_learner")

DB_PATH = os.path.expanduser("~/.eidos/doc_learner.db")
OLLAMA_URL = os.environ.get("OLLAMA_URL", "http://127.0.0.1:11434")
FAST_MODEL = os.environ.get("EIDOS_FAST_MODEL", "lfm2.5-thinking:1.2b")

# Supported extensions
SUPPORTED = {".epub", ".pdf", ".txt", ".md", ".html", ".htm", ".rst", ".json", ".csv"}


class DocLearner:
    """Learns from documents and stores knowledge."""

    def __init__(self, db_path: str = DB_PATH):
        self.db_path = db_path
        os.makedirs(os.path.dirname(db_path), exist_ok=True)
        self._init_db()

    def _init_db(self) -> None:
        with get_conn_ctx(self.db_path) as c:
            c.execute("""CREATE TABLE IF NOT EXISTS docs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                path TEXT UNIQUE, filename TEXT, ext TEXT,
                size_bytes INTEGER, chunks INTEGER DEFAULT 0,
                summary TEXT DEFAULT '', learned_at REAL,
                tags TEXT DEFAULT '[]')""")
            c.execute("""CREATE TABLE IF NOT EXISTS chunks (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                doc_id INTEGER, chunk_idx INTEGER,
                content TEXT, summary TEXT DEFAULT '',
                tokens_est INTEGER DEFAULT 0,
                FOREIGN KEY (doc_id) REFERENCES docs(id))""")
            c.execute("CREATE INDEX IF NOT EXISTS idx_docs_path ON docs(path)")

    # ── File Extractors ──────────────────────────────────────────────────

    def _extract_text(self, path: str) -> str:
        """Extract text from file based on extension."""
        ext = Path(path).suffix.lower()

        if ext == ".epub":
            return self._extract_epub(path)
        elif ext == ".pdf":
            return self._extract_pdf(path)
        elif ext in (".html", ".htm"):
            return self._extract_html(path)
        elif ext in (".txt", ".md", ".rst"):
            with open(path, "r", errors="replace") as f:
                return f.read()
        elif ext == ".json":
            with open(path, "r") as f:
                data = json.load(f)
                return json.dumps(data, indent=2, ensure_ascii=False)[:50000]
        elif ext == ".csv":
            with open(path, "r", errors="replace") as f:
                return f.read()[:50000]
        return ""

    @staticmethod
    def _extract_epub(path: str) -> str:
        """Extract text from ePub."""
        try:
            import zipfile
            from html.parser import HTMLParser

            class TextExtractor(HTMLParser):
                def __init__(self):
                    super().__init__()
                    self.text = []
                    self._skip = False

                def handle_starttag(self, tag, _):
                    if tag in ("script", "style"):
                        self._skip = True

                def handle_endtag(self, tag):
                    if tag in ("script", "style"):
                        self._skip = False

                def handle_data(self, data):
                    if not self._skip:
                        self.text.append(data.strip())

            texts = []
            with zipfile.ZipFile(path) as zf:
                for name in sorted(zf.namelist()):
                    if name.endswith((".html", ".xhtml", ".htm")):
                        raw = zf.read(name).decode("utf-8", errors="replace")
                        parser = TextExtractor()
                        parser.feed(raw)
                        texts.append(" ".join(parser.text))
            return "\n\n".join(texts)
        except Exception as e:
            log.warning("ePub extraction failed: %s", e)
            return ""

    @staticmethod
    def _extract_pdf(path: str) -> str:
        """Extract text from PDF."""
        try:
            import subprocess
            r = subprocess.run(
                ["pdftotext", path, "-"],
                capture_output=True, text=True, timeout=30
            )
            return r.stdout if r.returncode == 0 else ""
        except (FileNotFoundError, subprocess.TimeoutExpired):
            log.warning("pdftotext not available or timed out for %s", path)
            return ""

    @staticmethod
    def _extract_html(path: str) -> str:
        """Extract text from HTML file."""
        from html.parser import HTMLParser

        class TextExtractor(HTMLParser):
            def __init__(self):
                super().__init__()
                self.text = []

            def handle_data(self, data):
                self.text.append(data.strip())

        with open(path, "r", errors="replace") as f:
            parser = TextExtractor()
            parser.feed(f.read())
        return " ".join(parser.text)

    # ── Chunking ─────────────────────────────────────────────────────────

    @staticmethod
    def _chunk_text(text: str, chunk_size: int = 2000, overlap: int = 200) -> list[str]:
        """Split text into overlapping chunks."""
        if not text:
            return []
        chunks = []
        start = 0
        while start < len(text):
            end = start + chunk_size
            chunk = text[start:end]
            if chunk.strip():
                chunks.append(chunk.strip())
            start = end - overlap
        return chunks

    # ── LLM Summarization ────────────────────────────────────────────────

    @staticmethod
    def _summarize(text: str, context: str = "") -> str:
        """Summarize text using local Ollama."""
        prompt = f"Summarize this text concisely (max 100 words):\n\n{text[:2000]}"
        if context:
            prompt = f"Context: {context}\n\n{prompt}"
        try:
            payload = json.dumps({
                "model": FAST_MODEL,
                "prompt": prompt,
                "stream": False,
                "options": {"num_predict": 150, "temperature": 0.3, "num_ctx": 2048},
            }).encode()
            req = urllib.request.Request(
                f"{OLLAMA_URL}/api/generate", data=payload,
                headers={"Content-Type": "application/json"},
            )
            with urllib.request.urlopen(req, timeout=60) as resp:
                data = json.load(resp)
                return data.get("response", "").strip()[:500]
        except Exception as e:
            log.warning("Summarization failed: %s", e)
            return text[:200] + "..."

    # ── Core Learning ────────────────────────────────────────────────────

    def learn_file(self, path: str, tags: list[str] | None = None,
                   summarize: bool = False) -> dict:
        """Learn from a single file. Returns result dict."""
        path = os.path.expanduser(path)
        if not os.path.isfile(path):
            return {"error": f"File not found: {path}"}

        ext = Path(path).suffix.lower()
        if ext not in SUPPORTED:
            return {"error": f"Unsupported format: {ext}"}

        # Check if already learned
        with get_conn_ctx(self.db_path) as c:
            existing = c.execute("SELECT id FROM docs WHERE path=?", (path,)).fetchone()
            if existing:
                return {"skipped": True, "path": path, "reason": "already learned"}

        text = self._extract_text(path)
        if not text:
            return {"error": f"No text extracted from: {path}"}

        chunks = self._chunk_text(text)
        filename = os.path.basename(path)
        size = os.path.getsize(path)
        tags = tags or []

        # Auto-tag from extension
        ext_tags = {".epub": "book", ".pdf": "paper", ".md": "docs",
                    ".html": "web", ".csv": "data", ".json": "data"}
        if ext in ext_tags:
            tags.append(ext_tags[ext])

        summary = ""
        if summarize and chunks:
            summary = self._summarize(chunks[0], context=filename)

        # Store
        with get_conn_ctx(self.db_path) as c:
            cur = c.execute(
                "INSERT INTO docs (path,filename,ext,size_bytes,chunks,summary,learned_at,tags) "
                "VALUES (?,?,?,?,?,?,?,?)",
                (path, filename, ext, size, len(chunks), summary, time.time(), json.dumps(tags))
            )
            doc_id = cur.lastrowid
            for i, chunk in enumerate(chunks):
                c.execute(
                    "INSERT INTO chunks (doc_id,chunk_idx,content,tokens_est) VALUES (?,?,?,?)",
                    (doc_id, i, chunk, len(chunk) // 4)
                )

        # Store in brain memory if available
        try:
            from core.brain_memory import get_brain_memory
            bm = get_brain_memory()
            key = f"doc:{filename}"
            bm.store_semantic(key, f"Learned from {filename}: {summary or text[:300]}")
        except Exception:
            pass  # error no crítico, continuar
        result = {
            "path": path, "filename": filename, "ext": ext,
            "size": size, "chunks": len(chunks),
            "summary": summary[:200] if summary else "",
            "tags": tags,
        }
        log.info("Learned: %s (%d chunks, %d bytes)", filename, len(chunks), size)
        return result

    def learn_directory(self, directory: str, tags: list[str] | None = None,
                        summarize: bool = False) -> list[dict]:
        """Learn from all supported files in a directory."""
        directory = os.path.expanduser(directory)
        results = []
        if not os.path.isdir(directory):
            return [{"error": f"Not a directory: {directory}"}]

        for root, _, files in os.walk(directory):
            for f in sorted(files):
                ext = Path(f).suffix.lower()
                if ext in SUPPORTED:
                    path = os.path.join(root, f)
                    r = self.learn_file(path, tags=list(tags or []), summarize=summarize)
                    results.append(r)
        return results

    def search(self, query: str, limit: int = 10) -> list[dict]:
        """Search learned content."""
        words = query.lower().split()[:5]
        # Bug fix: query="" → words=[] → conditions="" daba 'WHERE  ORDER
        # BY' inválido. Guardar: si no hay términos, 1=1 (todas las filas).
        if words:
            conditions = " AND ".join("LOWER(c.content) LIKE ?" for _ in words)
            params = [f"%{w}%" for w in words] + [limit]
        else:
            conditions = "1=1"
            params = [limit]
        with get_conn_ctx(self.db_path) as c:
            rows = c.execute(f"""
                SELECT d.filename, d.ext, c.chunk_idx, c.content, d.tags
                FROM chunks c JOIN docs d ON d.id = c.doc_id
                WHERE {conditions}
                ORDER BY d.learned_at DESC LIMIT ?
            """, params).fetchall()
        return [{
            "filename": r[0], "ext": r[1], "chunk": r[2],
            "content": r[3][:300], "tags": json.loads(r[4]),
        } for r in rows]

    def get_docs(self, limit: int = 50) -> list[dict]:
        """List all learned documents."""
        with get_conn_ctx(self.db_path) as c:
            rows = c.execute(
                "SELECT filename, ext, size_bytes, chunks, summary, learned_at, tags "
                "FROM docs ORDER BY learned_at DESC LIMIT ?", (limit,)
            ).fetchall()
        return [{
            "filename": r[0], "ext": r[1], "size": r[2],
            "chunks": r[3], "summary": r[4][:100],
            "learned_at": r[5], "tags": json.loads(r[6]),
        } for r in rows]

    @property
    def stats(self) -> dict:
        with get_conn_ctx(self.db_path) as c:
            doc_count = c.execute("SELECT COUNT(*) FROM docs").fetchone()[0]
            chunk_count = c.execute("SELECT COUNT(*) FROM chunks").fetchone()[0]
            total_bytes = c.execute(
                "SELECT COALESCE(SUM(size_bytes),0) FROM docs"
            ).fetchone()[0]
            by_ext = {}
            for row in c.execute("SELECT ext, COUNT(*) FROM docs GROUP BY ext").fetchall():
                by_ext[row[0]] = row[1]
        return {
            "documents": doc_count,
            "chunks": chunk_count,
            "total_bytes": total_bytes,
            "total_mb": round(total_bytes / (1024 * 1024), 2),
            "by_extension": by_ext,
            "supported": sorted(SUPPORTED),
        }


# ══════════════════════════════════════════════════════════════════════════════
#  SINGLETON
# ══════════════════════════════════════════════════════════════════════════════

_learner: Optional[DocLearner] = None


def get_doc_learner() -> DocLearner:
    global _learner
    if _learner is None:
        _learner = DocLearner()
    return _learner


# ── CLI test ──────────────────────────────────────────────────────────────────
if __name__ == "__main__":
    dl = get_doc_learner()
    print("Doc Learner")
    print(f"  Stats: {dl.stats}")
    print(f"  Supported: {', '.join(sorted(SUPPORTED))}")

    # Learn some real files if available
    test_dirs = [
        os.path.expanduser("~/EIDOS/docs"),
        os.path.expanduser("~/EIDOS/docs"),
    ]
    for d in test_dirs:
        if os.path.isdir(d):
            results = dl.learn_directory(d, tags=["eidos", "docs"])
            learned = [r for r in results if not r.get("skipped") and not r.get("error")]
            print(f"  Learned from {d}: {len(learned)} new docs")

    docs = dl.get_docs(5)
    for doc in docs:
        print(f"    {doc['filename']} ({doc['ext']}, {doc['chunks']} chunks, {doc.get('tags', [])})")

    print(f"\n  Final stats: {dl.stats}")
    print("OK")
