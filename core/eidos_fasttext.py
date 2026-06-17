"""
core/eidos_fasttext.py — Embeddings semánticos con FastText (S83 "Refinamiento")

Búsqueda semántica SIN APIs externas, SIN GPU, SIN LLM. FastText entrena
sobre los conceptos del propio grafo neuronal → embeddings conscientes del
dominio interno de EIDOS.

API:
    engine = get_fasttext_engine()
    results = engine.search("nginx", top_k=10)
    # → [{"node_id": "...", "concept": "...", "similarity": 0.92}, ...]
"""

from __future__ import annotations

import json
import logging
import os
import time
from functools import lru_cache
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import numpy as np

from core.db import get_conn

log = logging.getLogger("eidos.fasttext")

BRAIN_DB = Path.home() / ".eidos" / "evolution_brain.db"
MODEL_DIR = Path.home() / ".eidos" / "fasttext"
MODEL_FILE = MODEL_DIR / "eidos_fasttext.bin"
INDEX_FILE = MODEL_DIR / "node_embeddings.npy"
FAISS_FILE = MODEL_DIR / "faiss.index"
MAPPING_FILE = MODEL_DIR / "node_ids.json"

# ── Entrenamiento desde el grafo ──────────────────────────────────────────

def _build_training_corpus() -> List[str]:
    """Extrae conceptos del grafo como 'oraciones' para entrenar FastText.

    Cada nodo se tokeniza y se usa como una línea de entrenamiento.
    Los nodos relacionados (misma categoría) se agrupan en la misma línea
    para que FastText aprenda relaciones semánticas.
    """
    sentences = []
    try:
        conn = get_conn(BRAIN_DB, timeout=30)
        conn.execute("PRAGMA busy_timeout=30000")

        # Agrupar nodos por categoría para dar contexto semántico
        rows = conn.execute(
            "SELECT category, GROUP_CONCAT(concept, ' ') "
            "FROM knowledge_nodes "
            "WHERE concept IS NOT NULL AND concept != '' "
            "GROUP BY category "
            "LIMIT 50000"
        ).fetchall()

        for _cat, concepts_text in rows:
            if concepts_text:
                # Normalizar: minúsculas, solo alfanuméricas+espacios
                text = concepts_text.lower()
                # Tokenizar en palabras de 2+ chars
                words = [w for w in text.split() if len(w) >= 2]
                if len(words) >= 3:
                    sentences.append(" ".join(words[:200]))  # máx 200 palabras por línea

        # También añadir conceptos individuales para cobertura
        rows2 = conn.execute(
            "SELECT concept FROM knowledge_nodes "
            "WHERE concept IS NOT NULL AND concept != '' "
            "ORDER BY confidence DESC LIMIT 100000"
        ).fetchall()
        for (concept,) in rows2:
            words = [w for w in concept.lower().split() if len(w) >= 2]
            if words:
                sentences.append(" ".join(words))

        log.info("FastText corpus: %d sentences del grafo neuronal", len(sentences))
    except Exception as e:
        log.warning("FastText corpus error: %s", e)

    return sentences


def _train_model(sentences: List[str]) -> Any:
    """Entrena un modelo FastText supervisado (SkipGram) sobre el corpus."""
    import fasttext

    # Guardar corpus temporal
    corpus_path = MODEL_DIR / "corpus.txt"
    corpus_path.parent.mkdir(parents=True, exist_ok=True)
    corpus_path.write_text("\n".join(sentences))

    log.info("FastText: entrenando sobre %d líneas...", len(sentences))
    t0 = time.time()

    model = fasttext.train_unsupervised(
        str(corpus_path),
        model="skipgram",
        dim=100,           # 100 dimensiones (ligero, suficiente para similitud interna)
        ws=5,              # window size
        epoch=10,
        minCount=2,
        minn=3,            # character n-grams (subword info)
        maxn=6,
        thread=4,
    )

    elapsed = time.time() - t0
    log.info("FastText: modelo entrenado en %.1fs (dim=%d, vocab=%d)",
             elapsed, model.get_dimension(), len(model.words))

    # Limpiar corpus temporal
    corpus_path.unlink(missing_ok=True)

    return model


# ── Indexación ────────────────────────────────────────────────────────────

def _build_embeddings_index(model) -> Tuple[np.ndarray, List[str], Any]:
    """Genera embeddings para todos los nodos del grafo, indexa con FAISS."""
    try:
        conn = get_conn(BRAIN_DB, timeout=30)
        conn.execute("PRAGMA busy_timeout=30000")
        rows = conn.execute(
            "SELECT id, concept FROM knowledge_nodes "
            "WHERE concept IS NOT NULL AND concept != ''"
        ).fetchall()
    except Exception as e:
        log.warning("FastText index query: %s", e)
        return np.array([]), [], None

    dim = model.get_dimension()
    embeddings = np.zeros((len(rows), dim), dtype=np.float32)
    node_ids = []
    skipped = 0

    for i, (nid, concept) in enumerate(rows):
        words = concept.lower().split()
        if words:
            vec = model.get_sentence_vector(" ".join(words))
            embeddings[i] = vec
            node_ids.append(nid)
        else:
            skipped += 1

    # Recortar al tamaño real
    embeddings = embeddings[:len(node_ids)]

    # Normalizar vectores para cosine similarity vía inner product
    norms = np.linalg.norm(embeddings, axis=1, keepdims=True)
    norms = np.where(norms == 0, 1e-8, norms)
    embeddings = embeddings / norms

    # Construir índice FAISS (IndexFlatIP = inner product = cosine similarity)
    import faiss
    faiss_index = faiss.IndexFlatIP(dim)
    faiss_index.add(embeddings)

    log.info("FastText: %d embeddings + FAISS index (dim=%d, cosine similarity)",
             len(node_ids), dim)

    return embeddings[:len(node_ids)], node_ids, faiss_index


def _faiss_search(query_vec: np.ndarray, faiss_index, top_k: int = 10,
                  ) -> List[Tuple[int, float]]:
    """Búsqueda FAISS: inner product sobre vectores normalizados = cosine similarity.
    Tiempo típico: <5ms para 350K vectores (vs 1.5s naive)."""
    if faiss_index is None or faiss_index.ntotal == 0:
        return []

    # Normalizar query
    query_norm = query_vec / (np.linalg.norm(query_vec) + 1e-8)
    query_batch = np.expand_dims(query_norm.astype(np.float32), axis=0)

    # FAISS search
    distances, indices = faiss_index.search(query_batch, min(top_k, faiss_index.ntotal))

    results = []
    for i in range(len(indices[0])):
        idx = int(indices[0][i])
        sim = float(distances[0][i])  # inner product de vectores normalizados = cosine sim
        if idx >= 0 and idx < faiss_index.ntotal:
            results.append((idx, sim))

    return results


# ── Motor principal ───────────────────────────────────────────────────────

class FastTextEngine:
    """Motor de búsqueda semántica sobre el grafo neuronal usando FastText + FAISS."""

    def __init__(self):
        self._model = None
        self._faiss_index = None        # índice FAISS (sub-10ms search)
        self._embeddings: Optional[np.ndarray] = None  # legacy (respaldo)
        self._node_ids: List[str] = []
        self._ready = False
        self._load_or_train()

    def _load_or_train(self):
        """Carga modelo existente o entrena uno nuevo."""
        try:
            import faiss
            if MODEL_FILE.exists() and FAISS_FILE.exists() and MAPPING_FILE.exists():
                import fasttext
                self._model = fasttext.load_model(str(MODEL_FILE))
                self._faiss_index = faiss.read_index(str(FAISS_FILE))
                self._node_ids = json.loads(MAPPING_FILE.read_text())
                self._ready = True
                log.info("FastTextEngine: modelo FAISS cargado (%d nodos, dim=%d)",
                         self._faiss_index.ntotal, self._model.get_dimension())
                # [S122] Auto-reconstrucción si el índice quedó obsoleto respecto al
                # grafo (p.ej. tras una curación masiva): evita servir nodos borrados.
                if self._index_is_stale():
                    log.info("FastTextEngine: índice obsoleto vs grafo curado → reentrenando")
                    self.train(force=True)
            elif MODEL_FILE.exists() and INDEX_FILE.exists() and MAPPING_FILE.exists():
                # Migrar de numpy a FAISS
                import fasttext
                self._model = fasttext.load_model(str(MODEL_FILE))
                self._embeddings = np.load(str(INDEX_FILE))
                self._node_ids = json.loads(MAPPING_FILE.read_text())
                self._build_faiss_from_embeddings()
                self._ready = True
                log.info("FastTextEngine: migrado a FAISS (%d nodos)", self._faiss_index.ntotal)
            else:
                log.info("FastTextEngine: no hay modelo cacheado, entrenando...")
                self.train()
        except Exception as e:
            log.warning("FastTextEngine load: %s", e)

    def _index_is_stale(self, threshold: float = 0.30) -> bool:
        """True si el nº de nodos indexados diverge >threshold del grafo actual.
        Permite que el índice se reconstruya solo tras curaciones/cambios grandes. [S122]"""
        try:
            conn = get_conn(BRAIN_DB, timeout=10, cache=False)
            graph_n = conn.execute("SELECT COUNT(*) FROM knowledge_nodes").fetchone()[0]
            conn.close()
            idx_n = self._faiss_index.ntotal if self._faiss_index is not None else 0
            if graph_n <= 0:
                return False
            return abs(idx_n - graph_n) / graph_n > threshold
        except Exception:
            return False

    def _build_faiss_from_embeddings(self):
        """Construye índice FAISS desde embeddings existentes."""
        if self._embeddings is None or len(self._embeddings) == 0:
            return
        import faiss
        # Normalizar
        norms = np.linalg.norm(self._embeddings, axis=1, keepdims=True)
        norms = np.where(norms == 0, 1e-8, norms)
        normalized = self._embeddings / norms
        dim = normalized.shape[1]
        self._faiss_index = faiss.IndexFlatIP(dim)
        self._faiss_index.add(normalized.astype(np.float32))
        # Persistir FAISS index
        MODEL_DIR.mkdir(parents=True, exist_ok=True)
        faiss.write_index(self._faiss_index, str(FAISS_FILE))

    def train(self, force: bool = False):
        """Entrena FastText desde el grafo neuronal y construye índice FAISS."""
        t0 = time.time()
        try:
            sentences = _build_training_corpus()
            if len(sentences) < 10:
                log.warning("FastText: corpus muy pequeño (%d líneas), abortando", len(sentences))
                return

            self._model = _train_model(sentences)
            self._embeddings, self._node_ids, self._faiss_index = \
                _build_embeddings_index(self._model)

            # Persistir
            MODEL_DIR.mkdir(parents=True, exist_ok=True)
            self._model.save_model(str(MODEL_FILE))
            if self._embeddings is not None and len(self._embeddings) > 0:
                np.save(str(INDEX_FILE), self._embeddings)
            if self._faiss_index is not None:
                import faiss
                faiss.write_index(self._faiss_index, str(FAISS_FILE))
            MAPPING_FILE.write_text(json.dumps(self._node_ids, ensure_ascii=False))

            self._ready = True
            elapsed = time.time() - t0
            log.info("FastTextEngine: entrenamiento FAISS completo en %.1fs (%d nodos indexados)",
                     elapsed, len(self._node_ids))
        except Exception as e:
            log.warning("FastTextEngine train: %s", e)

    def search(self, query: str, top_k: int = 10,
               min_similarity: float = 0.3) -> List[Dict[str, Any]]:
        """Busca los nodos más similares semánticamente a la query. FAISS: <10ms.

        Retorna lista de {node_id, concept, category, similarity, confidence}.
        """
        if not self._ready or self._model is None:
            return []

        try:
            query_vec = self._model.get_sentence_vector(query.lower())

            # FAISS search (preferido, sub-10ms)
            if self._faiss_index is not None and self._faiss_index.ntotal > 0:
                hits = _faiss_search(query_vec, self._faiss_index, top_k)
            elif self._embeddings is not None and len(self._embeddings) > 0:
                # Fallback: numpy coseno (legacy)
                query_norm = query_vec / (np.linalg.norm(query_vec) + 1e-8)
                emb_norms = self._embeddings / (np.linalg.norm(self._embeddings, axis=1, keepdims=True) + 1e-8)
                sims = np.dot(emb_norms, query_norm)
                idxs = np.argsort(sims)[::-1][:top_k]
                hits = [(int(i), float(sims[i])) for i in idxs]
            else:
                return []

            # Obtener info de los nodos hit
            conn = get_conn(BRAIN_DB, timeout=5)
            results = []
            for idx, sim in hits:
                if sim < min_similarity:
                    continue
                if idx >= len(self._node_ids):
                    continue
                nid = self._node_ids[idx]
                row = conn.execute(
                    "SELECT concept, category, confidence FROM knowledge_nodes WHERE id=?",
                    (nid,)
                ).fetchone()
                if row:
                    results.append({
                        "node_id": nid,
                        "concept": row[0],
                        "category": row[1] or "",
                        "confidence": row[2] or 0.5,
                        "similarity": round(sim, 4),
                    })
            return results
        except Exception as e:
            log.debug("FastTextEngine search: %s", e)
            return []

    @lru_cache(maxsize=10000)
    def get_vector(self, text: str) -> Optional[Any]:
        """[S105] Retorna el vector de embedding para un texto.

        Usado por SarcasmDetector para comparar cosine similarity
        entre mensajes y prototipos de sarcasmo/ironía/sinceridad.

        Args:
            text: texto a vectorizar

        Returns:
            numpy array del embedding, o None si no está disponible
        """
        if not self._ready or not self._model:
            return None
        try:
            import numpy as np
            text_clean = text.lower().strip()
            if not text_clean:
                return None
            vec = self._model.get_sentence_vector(text_clean)
            return np.array(vec)
        except Exception as e:
            log.debug("FastTextEngine get_vector: %s", e)
            return None

    def is_ready(self) -> bool:
        return self._ready

    def stats(self) -> Dict[str, Any]:
        faiss_ntotal = self._faiss_index.ntotal if self._faiss_index else 0
        return {
            "ready": self._ready,
            "nodes_indexed": faiss_ntotal or len(self._node_ids),
            "dim": int(self._model.get_dimension()) if self._model else 0,
            "vocab_size": len(self._model.words) if self._model else 0,
            "model_mb": round(MODEL_FILE.stat().st_size / 1e6, 1) if MODEL_FILE.exists() else 0,
            "faiss_index": faiss_ntotal > 0,
            "search_engine": "FAISS" if faiss_ntotal > 0 else "numpy",
        }


# ── Singleton ─────────────────────────────────────────────────────────────

_engine: Optional[FastTextEngine] = None


def get_fasttext_engine() -> FastTextEngine:
    global _engine
    if _engine is None:
        _engine = FastTextEngine()
    return _engine


# ── CLI ───────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    import argparse
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    p = argparse.ArgumentParser(description="EIDOS FastText Engine")
    p.add_argument("query", nargs="?", help="Texto a buscar")
    p.add_argument("--train", action="store_true", help="Forzar reentrenamiento")
    p.add_argument("--stats", action="store_true")
    p.add_argument("--top-k", type=int, default=10)
    args = p.parse_args()

    engine = get_fasttext_engine()

    if args.train:
        engine.train(force=True)

    if args.stats:
        print(json.dumps(engine.stats(), indent=2, ensure_ascii=False))

    if args.query:
        t0 = time.time()
        results = engine.search(args.query, top_k=args.top_k)
        elapsed = time.time() - t0
        print(f"\nResultados para '{args.query}' ({len(results)} hits, {elapsed*1000:.1f}ms):")
        for r in results:
            print(f"  [{r['similarity']:.3f}] {r['concept'][:80]:80s} ({r['category']})")
