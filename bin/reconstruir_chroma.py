#!/usr/bin/env python3
"""
reconstruir_chroma.py — Reconstruye la base de embeddings de ChromaDB [S119]
===========================================================================

Lee todos los nodos de calidad (quality_score >= 0.35) desde SQLite,
genera embeddings via Ollama (nomic-embed-text), y los indexa en ChromaDB
usando el servidor Rust CLI (HttpClient, sin riesgo SIGSEGV).

Uso:
  python3 bin/reconstruir_chroma.py [--batch 50] [--limit N]

Requisitos:
  - chroma CLI Rust corriendo en puerto 8767
  - Ollama corriendo con nomic-embed-text disponible
"""

from __future__ import annotations

import argparse
import json
import logging
import sqlite3
import sys
import time
from pathlib import Path
from typing import List, Optional, Tuple

log = logging.getLogger("reconstruir_chroma")
logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(name)s] %(levelname)s %(message)s")

# ── Config ──────────────────────────────────────────────────────────────────────
BRAIN_DB = Path.home() / ".eidos" / "evolution_brain.db"
CHROMA_HOST = "localhost"
CHROMA_PORT = 8767
COLLECTION_NAME = "eidos_knowledge"
EMBED_MODEL_NAME = "all-MiniLM-L6-v2"  # sentence-transformers, 384-dim, rápido en CPU
_embed_model = None
BATCH_SIZE = 50
QUALITY_THRESHOLD = 0.35
DOC_LIMIT = None  # None = todos


def read_quality_nodes(db_path: Path, min_quality: float = 0.35,
                       limit: Optional[int] = None) -> List[Tuple]:
    """Lee nodos de calidad desde SQLite, ordenados por quality_score DESC."""
    conn = sqlite3.connect(str(db_path))
    conn.execute("PRAGMA journal_mode=WAL")

    # Verificar si la columna quality_score existe
    cols = [r[1] for r in conn.execute("PRAGMA table_info(knowledge_nodes)").fetchall()]

    if "quality_score" in cols:
        query = """
            SELECT id, concept, definition, source, confidence, quality_score
            FROM knowledge_nodes
            WHERE quality_score >= ? AND definition IS NOT NULL AND definition != ''
            ORDER BY quality_score DESC
        """
    else:
        log.warning("quality_score column not found, using confidence-based filter")
        query = """
            SELECT id, concept, definition, source, confidence, 0.5 as quality_score
            FROM knowledge_nodes
            WHERE source NOT IN ('wordnet', 'mitre_attck', 'circl', 'nvd_nist')
            AND definition IS NOT NULL AND definition != ''
            ORDER BY confidence DESC
        """

    if limit:
        query += f" LIMIT {int(limit)}"

    rows = conn.execute(query, (min_quality,)).fetchall()
    conn.close()

    log.info("Leídos %d nodos de calidad desde SQLite (threshold=%.2f)", len(rows), min_quality)
    return rows


def _get_embed_model():
    """Singleton del modelo de embeddings (carga lazy)."""
    global _embed_model
    if _embed_model is None:
        from sentence_transformers import SentenceTransformer
        log.info("Cargando modelo %s...", EMBED_MODEL_NAME)
        _embed_model = SentenceTransformer(EMBED_MODEL_NAME)
        log.info("Modelo cargado: %s", _embed_model)
    return _embed_model


def generate_embeddings_batch(texts: List[str]) -> List[List[float]]:
    """Genera embeddings localmente con sentence-transformers (rápido en CPU)."""
    model = _get_embed_model()
    # all-MiniLM-L6-v2: ~10-50ms por texto en CPU, soporta batches de cualquier tamaño
    embeddings = model.encode(
        [t[:500] for t in texts],
        batch_size=len(texts),
        show_progress_bar=False,
        convert_to_numpy=True,
    )
    return embeddings.tolist()


def build_chroma_collection(rows: List[Tuple], batch_size: int = BATCH_SIZE,
                            host: str = CHROMA_HOST, port: int = CHROMA_PORT,
                            collection_name: str = COLLECTION_NAME):
    """Construye la colección de ChromaDB con los nodos proporcionados."""
    from chromadb import HttpClient

    client = HttpClient(host=host, port=port)
    log.info("Conectado a ChromaDB Rust en %s:%d (v%s)", host, port, client.get_version())

    # Usar get_or_create para mantener el UUID estable (sin DELETE+CREATE)
    # así el ciclo autónomo no pierde la referencia y no hay race condition.
    collection = client.get_or_create_collection(
        name=collection_name,
        metadata={"hnsw:space": "cosine"},
    )
    log.info("Colección '%s' lista (get_or_create, UUID estable)", collection_name)

    total = len(rows)
    indexed = 0
    t_start = time.time()

    for batch_start in range(0, total, batch_size):
        batch = rows[batch_start:batch_start + batch_size]
        batch_num = batch_start // batch_size + 1
        total_batches = (total + batch_size - 1) // batch_size

        ids, docs, metas = [], [], []
        texts_for_embed = []

        for row in batch:
            node_id, concept, definition, source, confidence = row[0], row[1], row[2], row[3], row[4]
            quality = row[5] if len(row) > 5 else 0.5
            text = f"{concept}: {definition or ''}"[:500].strip()

            ids.append(str(node_id))
            docs.append(text)
            texts_for_embed.append(text)
            metas.append({
                "source": str(source or ""),
                "concept": str(concept or "")[:100],
                "confidence": float(confidence or 0.5),
                "quality_score": float(quality or 0.3),
            })

        # Generar embeddings localmente (sentence-transformers, rápido en CPU)
        log.info("Batch %d/%d: generando %d embeddings...", batch_num, total_batches, len(texts_for_embed))
        embeddings = generate_embeddings_batch(texts_for_embed)

        # Upsert en ChromaDB
        collection.upsert(
            ids=ids,
            documents=docs,
            embeddings=embeddings,
            metadatas=metas,
        )

        indexed += len(batch)
        elapsed = time.time() - t_start
        rate = indexed / elapsed if elapsed > 0 else 0
        eta = (total - indexed) / rate if rate > 0 else 0
        log.info("  → %d/%d indexados (%.1f nodos/s, ETA %.0fs)", indexed, total, rate, eta)

    total_time = time.time() - t_start
    final_count = collection.count()
    log.info("✅ ChromaDB reconstruido: %d vectores en %.0fs (%.1f nodos/s)",
             final_count, total_time, total / total_time)
    return final_count


def main():
    parser = argparse.ArgumentParser(description="Reconstruir ChromaDB con nodos de calidad")
    parser.add_argument("--batch", type=int, default=BATCH_SIZE, help="Tamaño de lote")
    parser.add_argument("--limit", type=int, default=None, help="Máx nodos (default: todos)")
    parser.add_argument("--quality", type=float, default=QUALITY_THRESHOLD, help="Umbral de calidad")
    parser.add_argument("--host", default=CHROMA_HOST, help="Host ChromaDB")
    parser.add_argument("--port", type=int, default=CHROMA_PORT, help="Puerto ChromaDB")
    parser.add_argument("--dry-run", action="store_true", help="Solo leer, no indexar")
    args = parser.parse_args()

    # 1. Leer nodos
    rows = read_quality_nodes(BRAIN_DB, args.quality, args.limit)
    if not rows:
        log.error("No hay nodos de calidad para indexar")
        sys.exit(1)

    # Mostrar stats
    sources = {}
    for r in rows:
        src = r[3] or "unknown"
        sources[src] = sources.get(src, 0) + 1
    log.info("Distribución por fuente: %s",
             ", ".join(f"{k}={v}" for k, v in sorted(sources.items(), key=lambda x: -x[1])[:10]))

    if args.dry_run:
        log.info("DRY RUN - no se indexa nada. %d nodos listos.", len(rows))
        return

    # 2. Indexar en ChromaDB
    count = build_chroma_collection(rows, args.batch, args.host, args.port)
    print(f"\n✅ ChromaDB reconstruido: {count} vectores en '{COLLECTION_NAME}'")


if __name__ == "__main__":
    main()
