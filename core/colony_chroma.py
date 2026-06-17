"""
core/colony_chroma.py — Memoria semántica con ChromaDB  [Prioridad 1.4]

Reemplaza el fallback LIKE de colony_community._try_knowledge_first() con
búsqueda vectorial real. Usa sentence-transformers (all-MiniLM-L6-v2) local.

S119: Migrado de PersistentClient (SIGSEGV ≥1.5.x) a HttpClient contra
chroma CLI Rust (puerto 8767). Embeddings locales con sentence-transformers.

Características:
  - HttpClient → chroma Rust CLI en :8767 (sin riesgo SIGSEGV)
  - Embeddings: all-MiniLM-L6-v2 (384-dim, rápido en CPU, local)
  - search() devuelve los nodos más similares semánticamente
  - Fallback: si ChromaDB no responde, usa LIKE de SQLite

Uso:
    from core.colony_chroma import get_chroma_memory
    mem = get_chroma_memory()
    results = mem.search("cómo funciona Docker", limit=3)
    mem.add(node_id, concept, definition, source)
"""
from __future__ import annotations

import json
import logging
import os
import threading
from pathlib import Path
from typing import Optional

log = logging.getLogger("eidos.chroma")

CHROMA_HOST  = os.environ.get("EIDOS_CHROMA_HOST", "127.0.0.1")
CHROMA_PORT  = int(os.environ.get("EIDOS_CHROMA_PORT", "8767"))
COLLECTION   = "eidos_knowledge"
EMBED_MODEL  = "all-MiniLM-L6-v2"

# ── Embedding model singleton ──────────────────────────────────────────────────
_embed_model = None
_embed_lock = threading.Lock()


def _get_embed_model():
    """Singleton del modelo sentence-transformers (carga lazy, thread-safe)."""
    global _embed_model
    if _embed_model is None:
        with _embed_lock:
            if _embed_model is None:
                try:
                    from sentence_transformers import SentenceTransformer
                    _embed_model = SentenceTransformer(EMBED_MODEL)
                    log.info("Embed model loaded: %s", EMBED_MODEL)
                except Exception as e:
                    log.warning("Cannot load embed model: %s", e)
                    return None
    return _embed_model


def _get_embeddings(texts: list[str]) -> Optional[list]:
    """Genera embeddings localmente. Retorna None si el modelo no está disponible."""
    model = _get_embed_model()
    if model is None:
        return None
    try:
        embeddings = model.encode(
            [t[:500] for t in texts],
            batch_size=len(texts),
            show_progress_bar=False,
            convert_to_numpy=True,
        )
        return embeddings.tolist()
    except Exception as e:
        log.debug("embedding failed: %s", e)
        return None


class ChromaMemory:
    """Búsqueda semántica con ChromaDB (HttpClient + sentence-transformers local)."""

    def __init__(self):
        self._lock = threading.RLock()
        self._client = None
        self._collection = None
        self._ready = False
        # Inicializar sync (HttpClient es rápido, sin riesgo SIGSEGV)
        t = threading.Thread(target=self._init_sync, daemon=True, name="chroma-init")
        t.start()

    def _init_sync(self) -> None:
        """Inicializa HttpClient contra chroma CLI Rust."""
        try:
            import chromadb
            self._client = chromadb.HttpClient(host=CHROMA_HOST, port=CHROMA_PORT)

            # Obtener o crear colección
            try:
                self._collection = self._client.get_collection(COLLECTION)
                existing = self._collection.count()
                log.info("ChromaDB (HttpClient): colección '%s' con %d vectores", COLLECTION, existing)
            except Exception:
                self._collection = self._client.create_collection(
                    name=COLLECTION,
                    metadata={"hnsw:space": "cosine"},
                )
                log.info("ChromaDB (HttpClient): colección '%s' creada", COLLECTION)

            self._ready = True
        except Exception as e:
            log.warning("ChromaDB (HttpClient) init falló: %s", e)

    def is_ready(self) -> bool:
        return self._ready and self._collection is not None

    def search(self, query: str, limit: int = 5, min_score: float = 0.3,
               min_quality: float = 0.0) -> list[dict]:
        """
        Búsqueda semántica. Genera embedding local y busca en ChromaDB.
        Si ChromaDB no está listo, devuelve [] y el caller usa LIKE fallback.

        S121: si EIDOS_COLBERT_RERANK=1, se recuperan más candidatos de ChromaDB
        (recall) y se reordenan con ColBERT (precisión late-interaction). Si ColBERT
        no está disponible, el resultado es idéntico al de siempre (degrada solo).
        """
        if not self.is_ready():
            return []
        try:
            # Generar embedding de la query
            emb = _get_embeddings([query])
            if emb is None:
                return []

            # ── S121: ¿reordenar con ColBERT? Si sí, pedimos más candidatos ──
            rerank_on = False
            fetch_n = limit
            try:
                from core.eidos_colbert import RERANK_ENABLED, RERANK_FETCH_FACTOR
                if RERANK_ENABLED:
                    rerank_on = True
                    fetch_n = max(limit, limit * RERANK_FETCH_FACTOR)
            except Exception:
                pass

            # Where filter por calidad
            where_filter = None
            if min_quality > 0:
                where_filter = {"quality_score": {"$gte": min_quality}}

            with self._lock:
                results = self._collection.query(
                    query_embeddings=emb,
                    n_results=min(fetch_n, max(1, self._collection.count())),
                    include=["documents", "metadatas", "distances"],
                    where=where_filter,
                )

            items = []
            docs      = results.get("documents", [[]])[0]
            metas     = results.get("metadatas", [[]])[0]
            distances = results.get("distances", [[]])[0]

            for doc, meta, dist in zip(docs, metas, distances):
                score = 1.0 - float(dist)  # cosine distance → similarity
                if score < min_score:
                    continue
                items.append({
                    "concept":       meta.get("concept", ""),
                    "definition":    doc,
                    "source":        meta.get("source", ""),
                    "confidence":    meta.get("confidence", 0.5),
                    "quality_score": meta.get("quality_score"),
                    "score":         round(score, 3),
                })

            # ── S121: reordenar con ColBERT (precisión) y recortar a `limit` ──
            if rerank_on and len(items) > 1:
                try:
                    from core.eidos_colbert import rerank
                    items = rerank(query, items, top_k=limit)
                except Exception as e:
                    log.debug("ColBERT rerank no aplicado: %s", e)
                    items = items[:limit]
            else:
                items = items[:limit]

            return items
        except Exception as e:
            log.debug("search error: %s", e)
            return []

    def add(self, node_id: str, concept: str, definition: str,
            source: str = "", confidence: float = 0.6,
            quality_score: float = 0.3) -> bool:
        """Añade un nodo al índice ChromaDB."""
        if not self.is_ready():
            return False
        try:
            text = f"{concept}: {definition}".strip()[:500]
            emb = _get_embeddings([text])
            if emb is None:
                return False

            with self._lock:
                self._collection.upsert(
                    ids=[str(node_id)],
                    embeddings=emb,
                    documents=[text],
                    metadatas=[{
                        "source": source or "",
                        "concept": concept[:100],
                        "confidence": float(confidence),
                        "quality_score": float(quality_score),
                    }],
                )
            return True
        except Exception as e:
            log.debug("add error: %s", e)
            return False

    def count(self) -> int:
        if not self.is_ready():
            return 0
        try:
            with self._lock:
                return self._collection.count()
        except Exception:
            return 0

    def stats(self) -> dict:
        return {
            "ready":  self._ready,
            "count":  self.count(),
            "engine": "chromadb-http",
            "host":   f"{CHROMA_HOST}:{CHROMA_PORT}",
            "embed_model": EMBED_MODEL,
        }


# ── No-op stub para EIDOS_NO_CHROMA=1 (evita SIGSEGV multi-proceso) ──────────

class _NoOpChromaMemory:
    """Sustituto sin ChromaDB — usado por lifecycle_daemon para no colisionar con eidos_cli."""
    def is_ready(self) -> bool: return False
    def search(self, query: str, limit: int = 5, min_score: float = 0.3) -> list: return []
    def add(self, *a, **kw) -> None: pass
    def count(self) -> int: return 0
    def stats(self) -> dict: return {"ready": False, "mode": "noop"}


# ── Singleton ─────────────────────────────────────────────────────────────────

_instance = None
_lock = threading.Lock()


def get_chroma_memory():
    global _instance
    if os.environ.get("EIDOS_NO_CHROMA") == "1":
        return _NoOpChromaMemory()
    if _instance is None:
        with _lock:
            if _instance is None:
                _instance = ChromaMemory()
    return _instance
