"""
core/eidos_colbert.py — Re-ranker ColBERT (LFM2-ColBERT-350M) para EIDOS [S121]
==============================================================================
Qué hace: mejora la PRECISIÓN de la recuperación de conocimiento. ChromaDB
(nomic-embed-text) recupera candidatos rápido pero por similitud de un solo
vector; ColBERT los REORDENA con "late interaction" (compara token-a-token
query↔documento), que es más preciso para saber QUÉ nodo responde mejor.

Pipeline: ChromaDB top-K (rápido, recall) → ColBERT rerank (preciso) → top-N.

Modelo: LiquidAI/LFM2-ColBERT-350M (safetensors) vía la librería `pylate`.
SER lo pidió explícitamente ("no es opcional, lo necesito").

Diseño (CLAUDE.md):
  - Módulo NUEVO, no infla core/.
  - Carga PEREZOSA y singleton: el modelo (~1.4GB RAM) solo se carga la primera
    vez que se reordena, no al importar. Si pylate o el modelo no están,
    `rerank()` devuelve los candidatos SIN tocar → nunca rompe la búsqueda.
  - Gate por env: EIDOS_COLBERT_RERANK=1 lo activa. Por defecto OFF para no
    consumir RAM sin que SER lo decida.

Uso:
    from core.eidos_colbert import rerank
    items = rerank("que es nmap", candidates, top_k=5)  # candidates: list[dict]
"""
from __future__ import annotations

import logging
import os
import threading
import time
from typing import Any, Dict, List, Optional

log = logging.getLogger("eidos.colbert")

COLBERT_MODEL = os.environ.get("EIDOS_COLBERT_MODEL", "LiquidAI/LFM2-ColBERT-350M")
# Activación: por defecto OFF (no carga el modelo) salvo que SER lo encienda.
RERANK_ENABLED = os.environ.get("EIDOS_COLBERT_RERANK", "0") == "1"
# Cuántos candidatos pedir a ChromaDB antes de reordenar (recall amplio).
RERANK_FETCH_FACTOR = int(os.environ.get("EIDOS_COLBERT_FETCH_FACTOR", "4"))

_model = None
_model_lock = threading.Lock()
_load_failed = False  # si la carga falla una vez, no reintentar en bucle


def _get_model():
    """Carga perezosa del modelo ColBERT (singleton thread-safe).

    Devuelve None si pylate/modelo no están disponibles (degradación elegante).
    """
    global _model, _load_failed
    if _model is not None:
        return _model
    if _load_failed:
        return None
    with _model_lock:
        if _model is not None:
            return _model
        if _load_failed:
            return None
        try:
            from pylate import models  # import perezoso: no rompe si no está
            t0 = time.time()
            m = models.ColBERT(model_name_or_path=COLBERT_MODEL)
            # LFM2 usa eos como pad (recomendado en la model card).
            try:
                if m.tokenizer.pad_token is None:
                    m.tokenizer.pad_token = m.tokenizer.eos_token
            except Exception:
                pass
            _model = m
            log.info("ColBERT cargado (%s) en %.1fs", COLBERT_MODEL, time.time() - t0)
            return _model
        except Exception as e:  # noqa: BLE001
            _load_failed = True
            log.warning("ColBERT no disponible (%s) — rerank desactivado", e)
            return None


def is_available() -> bool:
    """¿Se puede usar ColBERT? (sin forzar la carga si ya falló)."""
    if _load_failed:
        return False
    if _model is not None:
        return True
    try:
        import pylate  # noqa: F401
        return True
    except Exception:
        return False


def rerank(query: str, candidates: List[Dict[str, Any]],
           top_k: int = 5, text_key: str = "definition",
           concept_key: str = "concept") -> List[Dict[str, Any]]:
    """Reordena `candidates` por relevancia ColBERT a `query`. Devuelve top_k.

    Cada candidato es un dict (de colony_chroma.search). Se añade 'colbert_score'.
    Si ColBERT no está disponible o algo falla, devuelve candidates[:top_k] intactos.
    """
    if not candidates:
        return []
    if not query or len(candidates) == 1:
        return candidates[:top_k]

    model = _get_model()
    if model is None:
        return candidates[:top_k]  # degradación elegante: orden original

    try:
        # Texto de cada candidato: "concepto: definición" (lo más informativo).
        docs = []
        for c in candidates:
            concept = str(c.get(concept_key, "") or "")
            definition = str(c.get(text_key, "") or "")
            docs.append(f"{concept}: {definition}".strip(": ").strip()[:512])

        # pylate: encode query (is_query=True) y documentos, luego MaxSim.
        q_emb = model.encode([query], is_query=True, show_progress_bar=False)
        d_emb = model.encode(docs, is_query=False, show_progress_bar=False)

        scores = _maxsim_scores(q_emb[0], d_emb)
        for c, s in zip(candidates, scores):
            c["colbert_score"] = round(float(s), 4)

        ranked = sorted(candidates, key=lambda c: c.get("colbert_score", 0.0), reverse=True)
        return ranked[:top_k]
    except Exception as e:  # noqa: BLE001
        log.debug("rerank falló (%s) — orden original", e)
        return candidates[:top_k]


def _maxsim_scores(query_emb, doc_embs) -> List[float]:
    """Puntuación late-interaction (MaxSim) entre 1 query y N documentos.

    query_emb: matriz (Lq, dim). doc_embs: lista de matrices (Ld, dim).
    MaxSim = suma sobre tokens de la query del máximo producto escalar con
    los tokens del documento (la métrica nativa de ColBERT).
    """
    import numpy as np
    q = np.asarray(query_emb, dtype=np.float32)  # (Lq, dim)
    out: List[float] = []
    for d in doc_embs:
        dd = np.asarray(d, dtype=np.float32)     # (Ld, dim)
        if dd.size == 0 or q.size == 0:
            out.append(0.0)
            continue
        sim = q @ dd.T            # (Lq, Ld)
        out.append(float(sim.max(axis=1).sum()))  # MaxSim
    return out


if __name__ == "__main__":
    import sys as _sys
    _sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    logging.basicConfig(level=logging.INFO)
    print(f"=== ColBERT re-ranker (modelo={COLBERT_MODEL}, enabled={RERANK_ENABLED}) ===")
    print("pylate disponible:", is_available())
    demo_q = "que es nmap"
    demo = [
        {"concept": "metasploit", "definition": "framework de explotación de vulnerabilidades."},
        {"concept": "nmap", "definition": "escáner de red para descubrir hosts y puertos abiertos."},
        {"concept": "wireshark", "definition": "analizador de tráfico de red."},
    ]
    res = rerank(demo_q, demo, top_k=3)
    print(f"\nQuery: {demo_q}")
    for r in res:
        print(f"  {r.get('colbert_score','-')}  {r['concept']}: {r['definition'][:50]}")
