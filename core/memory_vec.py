"""
EIDOS core/memory_vec.py — Lightweight Semantic Memory with sqlite-vec
=======================================================================
Memoria semántica ligera usando sqlite-vec para búsqueda vectorial.
Basado en: picoclaw_mosaxiv (Go sqlite-vec implementation)

Alternativa ligera a ChromaDB:
  - Sin servidor externo
  - Sin dependencias pesadas
  - SQLite nativo (ya incluido en Python)
  - sqlite-vec extension para KNN vectorial

Usa embeddings de Ollama (nomic-embed-text) para vectorizar.

Uso:
    from core.memory_vec import SemanticMemory

    mem = SemanticMemory()
    mem.store("nmap scan de 192.168.1.1 encontró puertos 22, 80, 443", tags=["scan", "nmap"])
    mem.store("La clave SSH del servidor es débil, usa RSA 1024", tags=["vuln", "ssh"])

    results = mem.search("vulnerabilidades de SSH", limit=5)
    for r in results:
        print(f"[{r['score']:.2f}] {r['content'][:80]}")
"""
from __future__ import annotations

import json
import sqlite3
import struct
import threading
import time
from pathlib import Path
from typing import Optional

import logging
from core.db import get_conn
log = logging.getLogger("eidos.memory_vec")

try:
    import sqlite_vec
    HAS_VEC = True
except ImportError:
    HAS_VEC = False

# Ollama embedding endpoint
OLLAMA_URL = "http://localhost:11434"
EMBED_MODEL = "nomic-embed-text"
EMBED_DIM = 768  # nomic-embed-text dimension

DB_PATH = Path.home() / ".eidos" / "memory_vec.db"


def _get_embedding(text: str) -> Optional[list[float]]:
    """Obtiene embedding de Ollama."""
    import urllib.request
    try:
        data = json.dumps({"model": EMBED_MODEL, "prompt": text[:2000]}).encode()
        req = urllib.request.Request(
            f"{OLLAMA_URL}/api/embeddings",
            data=data,
            headers={"Content-Type": "application/json"}
        )
        with urllib.request.urlopen(req, timeout=30) as resp:
            result = json.loads(resp.read())
            return result.get("embedding")
    except Exception as e:
        log.warning(f"[memory_vec] Embedding failed: {e}")
        return None


def _serialize_vec(vec: list[float]) -> bytes:
    """Serializa vector float32 para sqlite-vec."""
    return struct.pack(f"{len(vec)}f", *vec)


class SemanticMemory:
    """
    Memoria semántica con búsqueda vectorial KNN.

    Almacena texto + embeddings + metadata en SQLite.
    Búsqueda por similaridad coseno via sqlite-vec.
    """

    def __init__(self, db_path: str = None):
        self.db_path = db_path or str(DB_PATH)
        Path(self.db_path).parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        self.conn = get_conn(self.db_path, check_same_thread=False)

        if HAS_VEC:
            self.conn.enable_load_extension(True)
            sqlite_vec.load(self.conn)

        self._init_db()
        self._count = self._get_count()
        log.info(f"💾 [SemanticMemory] Inicializado: {self._count} entries, vec={'ON' if HAS_VEC else 'OFF'}")

    def _init_db(self):
        """Crea tablas si no existen."""
        self.conn.executescript("""
            CREATE TABLE IF NOT EXISTS memories (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                content TEXT NOT NULL,
                tags TEXT DEFAULT '[]',
                agent TEXT DEFAULT 'eidos',
                timestamp REAL,
                metadata TEXT DEFAULT '{}'
            );
            CREATE INDEX IF NOT EXISTS idx_memories_ts ON memories(timestamp);
        """)

        if HAS_VEC:
            # Crear tabla virtual de vectores
            try:
                _vec_sql = "CREATE VIRTUAL TABLE IF NOT EXISTS memory_vectors USING vec0(embedding float[{}])".format(EMBED_DIM)
                self.conn.execute(_vec_sql)
            except Exception as e:
                log.warning(f"[memory_vec] Vec table creation: {e}")

        self.conn.commit()

    def _get_count(self) -> int:
        return self.conn.execute("SELECT COUNT(*) FROM memories").fetchone()[0]

    def store(self, content: str, tags: list[str] = None,
              agent: str = "eidos", metadata: dict = None) -> int:
        """
        Almacena un recuerdo con embedding vectorial.

        Returns:
            ID del recuerdo almacenado
        """
        tags = tags or []
        metadata = metadata or {}

        with self._lock:
            cur = self.conn.execute(
                "INSERT INTO memories (content, tags, agent, timestamp, metadata) VALUES (?, ?, ?, ?, ?)",
                (content, json.dumps(tags), agent, time.time(), json.dumps(metadata))
            )
            row_id = cur.lastrowid

            # Generar y almacenar embedding
            if HAS_VEC:
                embedding = _get_embedding(content)
                if embedding and len(embedding) == EMBED_DIM:
                    try:
                        self.conn.execute(
                            "INSERT INTO memory_vectors (rowid, embedding) VALUES (?, ?)",
                            (row_id, _serialize_vec(embedding))
                        )
                    except Exception as e:
                        log.warning(f"[memory_vec] Vec insert failed: {e}")

            self.conn.commit()
        self._count += 1
        return row_id

    def search(self, query: str, limit: int = 5, tag_filter: str = None) -> list[dict]:
        """
        Búsqueda semántica por similaridad vectorial.

        Args:
            query: Texto de búsqueda
            limit: Máximo de resultados
            tag_filter: Filtrar por tag (opcional)

        Returns:
            Lista de dicts con content, score, tags, timestamp
        """
        # Si sqlite-vec disponible, búsqueda vectorial
        if HAS_VEC:
            embedding = _get_embedding(query)
            if embedding and len(embedding) == EMBED_DIM:
                try:
                    with self._lock:
                        rows = self.conn.execute("""
                            SELECT m.id, m.content, m.tags, m.agent, m.timestamp, m.metadata,
                                   v.distance
                            FROM memory_vectors v
                            JOIN memories m ON m.id = v.rowid
                            WHERE v.embedding MATCH ?
                              AND k = ?
                            ORDER BY v.distance
                        """, (_serialize_vec(embedding), limit)).fetchall()

                    results = []
                    for row in rows:
                        tags = json.loads(row[2])
                        if tag_filter and tag_filter not in tags:
                            continue
                        results.append({
                            "id": row[0],
                            "content": row[1],
                            "tags": tags,
                            "agent": row[3],
                            "timestamp": row[4],
                            "metadata": json.loads(row[5]),
                            "score": round(1.0 / (1.0 + abs(row[6])), 3),  # Distancia → similaridad [0..1]
                        })
                    return results
                except Exception as e:
                    log.warning(f"[memory_vec] Vec search failed: {e}, falling back to FTS")

        # Fallback: búsqueda por texto (LIKE)
        return self._text_search(query, limit, tag_filter)

    def _text_search(self, query: str, limit: int, tag_filter: str = None) -> list[dict]:
        """Búsqueda por texto simple (fallback sin vectores)."""
        words = query.lower().split()[:5]
        conditions = " AND ".join("LOWER(content) LIKE ?" for _ in words)
        params: list = [f"%{w}%" for w in words]

        if tag_filter:
            conditions += " AND tags LIKE ?"
            params.append(f'%"{tag_filter}"%')

        params.append(limit)
        sql = f"SELECT id, content, tags, agent, timestamp, metadata FROM memories WHERE {conditions} ORDER BY timestamp DESC LIMIT ?"
        with self._lock:
            rows = self.conn.execute(sql, params).fetchall()

        return [{
            "id": r[0], "content": r[1], "tags": json.loads(r[2]),
            "agent": r[3], "timestamp": r[4], "metadata": json.loads(r[5]),
            "score": 0.5,  # Score fijo para fallback
        } for r in rows]

    def get_recent(self, limit: int = 10, agent: str = None) -> list[dict]:
        """Obtiene memorias recientes."""
        if agent:
            rows = self.conn.execute(
                "SELECT id, content, tags, agent, timestamp FROM memories WHERE agent = ? ORDER BY timestamp DESC LIMIT ?",
                (agent, limit)
            ).fetchall()
        else:
            rows = self.conn.execute(
                "SELECT id, content, tags, agent, timestamp FROM memories ORDER BY timestamp DESC LIMIT ?",
                (limit,)
            ).fetchall()

        return [{"id": r[0], "content": r[1], "tags": json.loads(r[2]), "agent": r[3], "timestamp": r[4]} for r in rows]

    def delete(self, memory_id: int) -> bool:
        """Elimina un recuerdo."""
        with self._lock:
            self.conn.execute("DELETE FROM memories WHERE id = ?", (memory_id,))
            if HAS_VEC:
                try:
                    self.conn.execute("DELETE FROM memory_vectors WHERE rowid = ?", (memory_id,))
                except Exception:
                    pass  # error no crítico, continuar
            self.conn.commit()
        self._count -= 1
        return True

    def count(self) -> int:
        return self._count

    @property
    def stats(self) -> dict:
        return {
            "total": self._count,
            "vec_enabled": HAS_VEC,
            "db_path": self.db_path,
            "embed_model": EMBED_MODEL,
        }

    def close(self):
        self.conn.close()


# ═══════════════════════════════════════════════════════════════════════════════
#  SINGLETON
# ═══════════════════════════════════════════════════════════════════════════════

_memory: Optional[SemanticMemory] = None

def get_semantic_memory() -> SemanticMemory:
    global _memory
    if _memory is None:
        _memory = SemanticMemory()
    return _memory
