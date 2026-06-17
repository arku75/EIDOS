"""
core/eidos_curate_graph.py — Curación del grafo de conocimiento (S119 #239).

EIDOS acumuló 367K+ nodos durante su evolución, pero solo ~1.7% son conocimiento
real y accionable. El resto son entradas de diccionario (WordNet 291K), artefactos
de código (60K+), y bases de datos de seguridad (ATT&CK/CVE 1.7K) que contaminan
las búsquedas semánticas y el motor lógico.

Esta herramienta:
  1. Añade columna quality_score (REAL 0.0-1.0) a knowledge_nodes
  2. Asigna scores basados en source + category heuristics
  3. Opcionalmente borra nodos con score 0.0 (ATT&CK, CVE, etc.)
  4. Provee estadísticas de calidad del grafo

Uso:
  python3 -c "from core.eidos_curate_graph import curate; print(curate(dry_run=False))"
  python3 -c "from core.eidos_curate_graph import get_quality_stats; print(get_quality_stats())"
"""

from __future__ import annotations

import logging
import sqlite3
import time
from pathlib import Path
from typing import Any, Dict, Optional

log = logging.getLogger("eidos.curate")

BRAIN_DB = Path.home() / ".eidos" / "evolution_brain.db"

# ── Reglas de calidad ──────────────────────────────────────────────────────────
# quality_score final = min(source_score, category_cap) si la categoría tiene cap
# Si no hay category_cap, se usa source_score directamente.
# Score 0.0 → basura total (ATT&CK, CVE, exploits)
# Score 0.05-0.15 → ruido (wordnet, artefactos de código)
# Score 0.20-0.35 → estructura de código (útil pero no conocimiento)
# Score 0.40-0.55 → conocimiento no verificado
# Score 0.60-0.75 → conocimiento investigado/documentado
# Score 0.80-1.0 → conocimiento verificado de alta calidad

SOURCE_QUALITY: Dict[str, float] = {
    # ── CONOCIMIENTO REAL (0.55-0.85) ──
    "kali_tools":                     0.85,
    "docs:hermes":                    0.80,
    "docs:openclaw_skills":           0.80,
    "docs:vseidos":                   0.80,
    "docs:openclaw":                  0.80,
    "docs:man":                       0.78,
    "docs:sessions":                  0.75,
    "oro_skills":                     0.75,
    "research:man":                   0.72,
    "research:apt":                   0.70,
    "research:help":                  0.70,
    "research:active:wikipedia":      0.70,
    "research:wikipedia":             0.68,
    "research:duckduckgo":            0.62,
    "research:code":                  0.55,
    "distilled_from_curiosity":       0.65,
    "distilled_from_deliberation":    0.68,
    "auto_learner":                   0.55,
    "eidos_crawler:openclaw":         0.70,

    # ── ESTRUCTURA DE CÓDIGO (0.20-0.35, útil internamente) ──
    "graphify":                       0.30,
    "self_index:class":               0.20,
    "self_index:function":            0.20,
    "self_index:module":              0.20,

    # ── ARTEFACTOS / RUIDO (0.05-0.20) ──
    "wordnet":                        0.05,
    "reasoned":                       0.10,
    "code_analyzer":                  0.12,
    "tabula_rasa:path_scan":          0.10,
    "tabula_rasa:ast":                0.10,
    "tabula_rasa:ps":                 0.10,
    "char:colony_centinela:metrics":  0.15,
    "char:colony_centinela:complexity": 0.15,
    "pc_explorer:colony_general":     0.18,
    "telegram_bot":                   0.25,

    # ── BASURA DE SEGURIDAD (0.0, intocable para conocimiento) ──
    "mitre_attck":                    0.0,
    "circl":                          0.0,
    "nvd_nist":                       0.0,
}

# Ajustes por categoría: cap máximo para ciertas categorías
# independientemente del source. None = sin cap.
CATEGORY_CAPS: Dict[str, float] = {
    "dictionary":        0.08,
    "synset":            0.08,
    "mitre_attack":      0.0,
    "cve_recent":        0.0,
    "code_structure":    0.35,
    "inferred":          0.20,
    "eidos_function":    0.25,
    "eidos_class":       0.25,
    "eidos_module":      0.25,
    "system_command":    0.25,
    "lifecycle":         0.30,
    "security":          0.10,  # security genérico, no confundir con ATT&CK
    "ai":                0.40,
    "general":           0.50,
}

# Threshold mínimo para considerar un nodo "conocimiento usable"
MIN_KNOWLEDGE_SCORE = 0.35

# Threshold para ChromaDB: solo indexar nodos con score >= esto
MIN_CHROMA_SCORE = 0.25


def _compute_quality(source: str, category: str, confidence: float) -> float:
    """Calcula quality_score para un nodo basado en source + category."""
    source_score = SOURCE_QUALITY.get(source, 0.30)  # default 0.30 para fuentes desconocidas

    # Ajuste por confianza: si confidence es muy baja, penalizar
    if confidence < 0.3:
        source_score *= 0.5
    elif confidence < 0.5:
        source_score *= 0.75

    # Aplicar cap de categoría si existe
    cap = CATEGORY_CAPS.get(category)
    if cap is not None:
        return min(source_score, cap)

    return source_score


def _ensure_column(conn: sqlite3.Connection) -> bool:
    """Añade quality_score column si no existe. Retorna True si se añadió."""
    cols = [r[1] for r in conn.execute("PRAGMA table_info(knowledge_nodes)").fetchall()]
    if "quality_score" not in cols:
        log.info("Añadiendo columna quality_score a knowledge_nodes...")
        conn.execute("ALTER TABLE knowledge_nodes ADD COLUMN quality_score REAL DEFAULT 0.3")
        conn.commit()
        return True
    return False


def curate(dry_run: bool = True, batch_size: int = 10000,
           delete_garbage: bool = False) -> Dict[str, Any]:
    """Cura el grafo completo asignando quality_score a todos los nodos.

    Args:
        dry_run: Si True, solo analiza sin modificar.
        delete_garbage: Si True, elimina nodos con score 0.0 (ATT&CK, CVE).
                        ¡CUIDADO! Esto es irreversible.

    Returns:
        Dict con estadísticas de la operación.
    """
    t0 = time.time()
    if not BRAIN_DB.exists():
        return {"error": "DB no encontrada", "path": str(BRAIN_DB)}

    from core.db import get_conn
    conn = get_conn(BRAIN_DB, timeout=30)
    # WAL + busy_timeout + mmap + cache_size ya aplicados por get_conn

    # 1. Asegurar columna
    col_added = _ensure_column(conn)

    # 2. Contar total
    total = conn.execute("SELECT COUNT(*) FROM knowledge_nodes").fetchone()[0]

    # 3. Obtener distribución de sources
    sources = conn.execute(
        "SELECT source, category, COUNT(*) as cnt "
        "FROM knowledge_nodes "
        "GROUP BY source, category "
        "ORDER BY cnt DESC"
    ).fetchall()

    stats = {
        "total_nodes": total,
        "column_added": col_added,
        "dry_run": dry_run,
        "delete_garbage": delete_garbage,
        "sources_found": len(sources),
        "quality_tiers": {},
        "deleted": 0,
        "updated": 0,
        "elapsed_s": 0.0,
    }

    if dry_run:
        # Solo análisis, no modificar
        tier_counts = {"conocimiento_real": 0, "estructura": 0, "ruido": 0, "basura": 0}
        for source, category, cnt in sources:
            qs = _compute_quality(source or "", category or "", 0.7)
            if qs >= 0.55:
                tier_counts["conocimiento_real"] += cnt
            elif qs >= 0.20:
                tier_counts["estructura"] += cnt
            elif qs >= 0.01:
                tier_counts["ruido"] += cnt
            else:
                tier_counts["basura"] += cnt
        stats["quality_tiers"] = tier_counts
        stats["elapsed_s"] = round(time.time() - t0, 2)
        conn.close()
        return stats

    # 4. Actualizar quality_score eficientemente (S119 fix: agrupar por score)
    # En vez de un UPDATE por source (4029 queries!), agrupamos fuentes con
    # el mismo score y hacemos ~20 UPDATEs con WHERE source IN (...).
    updated = 0
    deleted = 0

    # Agrupar fuentes por score
    score_groups: Dict[float, list] = {}
    for source in set(s[0] for s in sources if s[0]):
        score = _compute_quality(source, "", 0.7)
        score_groups.setdefault(score, []).append(source)

    # Un UPDATE por grupo de score (~20 queries en vez de 4029)
    # SQLite tiene límite de ~999 parámetros, así que procesamos en lotes
    for score, src_list in score_groups.items():
        for i in range(0, len(src_list), 200):
            batch = src_list[i:i + 200]
            placeholders = ",".join("?" * len(batch))
            conn.execute(
                f"UPDATE knowledge_nodes SET quality_score = ? "
                f"WHERE source IN ({placeholders})",
                [score] + batch,
            )

    # Luego ajustar por categoría (cap específico) — solo ~20 categorías
    for category, cap in CATEGORY_CAPS.items():
        conn.execute(
            "UPDATE knowledge_nodes SET quality_score = MIN(quality_score, ?) "
            "WHERE category = ? AND quality_score > ?",
            (cap, category, cap),
        )

    # Ajustar por confidence baja
    conn.execute(
        "UPDATE knowledge_nodes SET quality_score = quality_score * 0.5 "
        "WHERE confidence < 0.3 AND quality_score > 0"
    )
    conn.execute(
        "UPDATE knowledge_nodes SET quality_score = quality_score * 0.75 "
        "WHERE confidence >= 0.3 AND confidence < 0.5 AND quality_score > 0"
    )

    updated = conn.total_changes

    # 5. Eliminar basura extrema si se pide
    if delete_garbage:
        cur = conn.execute(
            "SELECT COUNT(*) FROM knowledge_nodes WHERE quality_score = 0.0"
        )
        garbage_count = cur.fetchone()[0]
        if garbage_count > 0:
            log.warning("Eliminando %d nodos basura (ATT&CK, CVE)...", garbage_count)
            # Primero borrar aristas asociadas
            conn.execute(
                "DELETE FROM knowledge_edges WHERE from_node IN "
                "(SELECT id FROM knowledge_nodes WHERE quality_score = 0.0)"
            )
            conn.execute(
                "DELETE FROM knowledge_edges WHERE to_node IN "
                "(SELECT id FROM knowledge_nodes WHERE quality_score = 0.0)"
            )
            # Luego borrar nodos
            conn.execute(
                "DELETE FROM knowledge_nodes WHERE quality_score = 0.0"
            )
            deleted = garbage_count

    conn.commit()

    # 6. Estadísticas post-curación
    tiers = conn.execute("""
        SELECT
            CASE
                WHEN quality_score >= 0.55 THEN 'conocimiento_real'
                WHEN quality_score >= 0.20 THEN 'estructura'
                WHEN quality_score >= 0.01 THEN 'ruido'
                ELSE 'basura'
            END as tier,
            COUNT(*) as cnt
        FROM knowledge_nodes
        GROUP BY tier
    """).fetchall()
    stats["quality_tiers"] = {t: c for t, c in tiers}
    stats["updated"] = updated
    stats["deleted"] = deleted
    stats["elapsed_s"] = round(time.time() - t0, 2)

    conn.close()
    log.info("Curación completada: %d actualizados, %d eliminados en %.1fs",
             updated, deleted, stats["elapsed_s"])
    return stats


def get_quality_stats() -> Dict[str, Any]:
    """Estadísticas rápidas de calidad del grafo sin modificar nada."""
    if not BRAIN_DB.exists():
        return {"error": "DB no encontrada"}

    from core.db import get_conn
    conn = get_conn(BRAIN_DB, timeout=30)

    total = conn.execute("SELECT COUNT(*) FROM knowledge_nodes").fetchone()[0]

    # Ver si la columna existe
    cols = [r[1] for r in conn.execute("PRAGMA table_info(knowledge_nodes)").fetchall()]
    has_quality = "quality_score" in cols

    result = {
        "total_nodes": total,
        "has_quality_column": has_quality,
    }

    if has_quality:
        tiers = conn.execute("""
            SELECT
                CASE
                    WHEN quality_score >= 0.55 THEN 'conocimiento_real'
                    WHEN quality_score >= 0.20 THEN 'estructura'
                    WHEN quality_score >= 0.01 THEN 'ruido'
                    ELSE 'basura'
                END as tier,
                COUNT(*) as cnt,
                ROUND(AVG(quality_score), 3) as avg_score
            FROM knowledge_nodes
            GROUP BY tier
            ORDER BY avg_score DESC
        """).fetchall()
        result["tiers"] = [
            {"tier": t, "count": c, "avg_score": s} for t, c, s in tiers
        ]

    # Top garbage sources
    garbage = conn.execute(
        "SELECT source, COUNT(*) as cnt FROM knowledge_nodes "
        "WHERE source IN ('mitre_attck', 'circl', 'nvd_nist', 'wordnet', 'reasoned') "
        "GROUP BY source ORDER BY cnt DESC"
    ).fetchall()
    result["garbage_sources"] = {s: c for s, c in garbage}

    conn.close()
    return result


def rebuild_chroma_from_quality(min_score: float = MIN_CHROMA_SCORE) -> Dict[str, Any]:
    """Reconstruye la colección ChromaDB solo con nodos de calidad.

    Esto es necesario después de curar el grafo: los vectores existentes
    en ChromaDB incluyen basura (wordnet, ATT&CK). Al reconstruir solo
    con nodos de calidad, _is_known_semantically() devolverá matches reales.

    Args:
        min_score: quality_score mínimo para indexar en ChromaDB.

    Returns:
        Dict con conteo de nodos indexados.
    """
    t0 = time.time()
    try:
        from core.colony_chroma import get_chroma_memory  # NO importar CHROMA_DIR (no existe en HttpClient)
        from chromadb import HttpClient
    except ImportError:
        return {"error": "chromadb no disponible"}

    mem = get_chroma_memory()
    # Esperar a que ChromaDB esté listo (max 30s)
    waited = 0
    while not mem.is_ready() and waited < 30:
        time.sleep(0.5)
        waited += 0.5

    if not mem.is_ready():
        return {"error": "ChromaDB no se inicializó en 30s"}

    # Contar nodos de calidad
    from core.db import get_conn
    conn = get_conn(BRAIN_DB, timeout=30)

    # Asegurar que la columna existe
    cols = [r[1] for r in conn.execute("PRAGMA table_info(knowledge_nodes)").fetchall()]
    if "quality_score" not in cols:
        conn.close()
        return {"error": "quality_score column no existe. Ejecuta curate() primero."}

    quality_count = conn.execute(
        "SELECT COUNT(*) FROM knowledge_nodes WHERE quality_score >= ?",
        (min_score,),
    ).fetchone()[0]

    log.info("Reconstruyendo ChromaDB con %d nodos de calidad (score >= %.2f)...",
             quality_count, min_score)

    # Borrar colección existente via HttpClient (sin riesgo SIGSEGV)
    try:
        from core.colony_chroma import CHROMA_HOST, CHROMA_PORT
        client = HttpClient(host=CHROMA_HOST, port=CHROMA_PORT)
        try:
            client.delete_collection("eidos_knowledge")
            log.info("Colección ChromaDB antigua eliminada")
        except Exception:
            pass  # No existía
    except Exception as e:
        log.debug("Error limpiando colección: %s", e)

    # Reinicializar ChromaMemory (creará nueva colección)
    from core.colony_chroma import _instance as _chroma_instance, _lock as _chroma_lock
    with _chroma_lock:
        # Forzar reinicialización
        import core.colony_chroma as chroma_mod
        chroma_mod._instance = None

    # Obtener nueva instancia (se iniciará async)
    mem2 = get_chroma_memory()
    waited2 = 0
    while not mem2.is_ready() and waited2 < 30:
        time.sleep(0.5)
        waited2 += 0.5

    if not mem2.is_ready():
        conn.close()
        return {"error": "ChromaDB no se reinicializó en 30s"}

    # Importar nodos de calidad
    rows = conn.execute(
        "SELECT id, concept, definition, source, confidence, quality_score "
        "FROM knowledge_nodes "
        "WHERE quality_score >= ? AND definition IS NOT NULL "
        "ORDER BY quality_score DESC "
        "LIMIT 25000",
        (min_score,),
    ).fetchall()
    conn.close()

    added = 0
    batch_size = 100
    for i in range(0, len(rows), batch_size):
        batch = rows[i:i + batch_size]
        ids, docs, metas = [], [], []
        for node_id, concept, definition, source, confidence, qscore in batch:
            text = f"{concept}: {definition or ''}".strip()[:500]
            ids.append(str(node_id))
            docs.append(text)
            metas.append({
                "source": source or "",
                "concept": (concept or "")[:100],
                "confidence": float(confidence or 0.5),
                "quality_score": float(qscore or 0.3),
            })
        try:
            # Usar ChromaMemory.add() que genera embeddings con sentence-transformers
            # NOTA: add() añade de uno en uno (internamente usa _get_embeddings + upsert)
            for nid, doc, meta in zip(ids, docs, metas):
                mem2.add(
                    node_id=nid,
                    concept=meta.get("concept", ""),
                    definition=doc.replace(meta.get("concept", "") + ": ", "", 1) if ": " in doc else doc,
                    source=meta.get("source", ""),
                    confidence=meta.get("confidence", 0.5),
                    quality_score=meta.get("quality_score", 0.3),
                )
            added += len(ids)
        except Exception as e:
            log.debug("batch add error: %s", e)

    elapsed = time.time() - t0
    log.info("ChromaDB reconstruido: %d vectores de calidad en %.1fs", added, elapsed)

    return {
        "ok": True,
        "vectors_added": added,
        "quality_threshold": min_score,
        "elapsed_s": round(elapsed, 1),
    }
