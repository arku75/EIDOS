"""
core/eidos_practice.py — Spaced Repetition & Active Recall [S125]
==================================================================
Sistema de práctica espaciada con recuerdo activo para EIDOS.

No es relectura pasiva: EIDOS genera resúmenes DESDE MEMORIA y los compara
con la definición almacenada. Esto fuerza el recuerdo activo, que es mucho
más efectivo que releer.

Colas de repaso (intervalos entre revisiones):
  NOVICE:      [1h, 6h, 24h, 3d, 7d, 14d, 30d]
  APPRENTICE:  [6h, 24h, 3d, 7d, 14d, 30d, 60d]
  JOURNEYMAN:  [24h, 3d, 7d, 14d, 30d, 60d, 120d]
  EXPERT:      [3d, 7d, 14d, 30d, 60d, 120d, 240d]
  MASTER:      [7d, 14d, 30d, 60d, 120d, 240d, 365d]

Gates de maestría (para avanzar de nivel):
  NOVICE → APPRENTICE:  3 ejecuciones exitosas O 3 prácticas correctas
  APPRENTICE → JOURNEYMAN:
    - 5 ejecuciones en 3 contextos diferentes
    - 5 aristas cross-domain
  JOURNEYMAN → EXPERT:
    - 10 ejecuciones en 5 contextos
    - 10 aristas cross-domain
    - 7 días consecutivos en nivel actual
  EXPERT → MASTER:
    - 20 ejecuciones en 10 contextos
    - 20 aristas cross-domain
    - 30 días consecutivos en nivel actual
    - Verificación de SER (flag manual) O 5 tests superados

Active Recall: EIDOS recibe el nombre del concepto y debe generar una
definición/resumen desde memoria. Luego se compara con la definición real.
"""
from __future__ import annotations

import json
import logging
import math
import re
import threading
import time
from collections import defaultdict
from dataclasses import dataclass, field
from difflib import SequenceMatcher
from pathlib import Path
from typing import Any, Dict, List, Optional, Set, Tuple

from core.db import get_conn

log = logging.getLogger("eidos.practice")

BRAIN_DB = Path.home() / ".eidos" / "evolution_brain.db"

# ── Schedule de repetición espaciada ─────────────────────────────────────────

# Intervalos en segundos
PRACTICE_SCHEDULES: Dict[str, List[float]] = {
    "NOVICE":      [3600, 21600, 86400, 259200, 604800, 1209600, 2592000],
    #               1h     6h     24h    3d       7d       14d      30d
    "APPRENTICE":  [21600, 86400, 259200, 604800, 1209600, 2592000, 5184000],
    #               6h     24h    3d      7d      14d      30d      60d
    "JOURNEYMAN":  [86400, 259200, 604800, 1209600, 2592000, 5184000, 10368000],
    #               24h    3d      7d      14d      30d      60d      120d
    "EXPERT":      [259200, 604800, 1209600, 2592000, 5184000, 10368000, 20736000],
    #               3d      7d      14d      30d      60d      120d     240d
    "MASTER":      [604800, 1209600, 2592000, 5184000, 10368000, 20736000, 31536000],
    #               7d      14d      30d      60d      120d     240d     365d
}

# ── Gates de maestría ────────────────────────────────────────────────────────

@dataclass
class MasteryGate:
    """Condiciones para avanzar de un nivel al siguiente."""
    from_level: str
    to_level: str
    min_executions: int = 0
    min_contexts: int = 0
    min_cross_domain_edges: int = 0
    min_days_at_level: int = 0
    requires_ser_verification: bool = False
    min_tests_passed: int = 0


MASTERY_GATES = [
    MasteryGate("NOVICE", "APPRENTICE",
                min_executions=3, min_contexts=1, min_cross_domain_edges=1),
    MasteryGate("APPRENTICE", "JOURNEYMAN",
                min_executions=5, min_contexts=3, min_cross_domain_edges=5),
    MasteryGate("JOURNEYMAN", "EXPERT",
                min_executions=10, min_contexts=5, min_cross_domain_edges=10,
                min_days_at_level=7),
    MasteryGate("EXPERT", "MASTER",
                min_executions=20, min_contexts=10, min_cross_domain_edges=20,
                min_days_at_level=30, requires_ser_verification=True, min_tests_passed=5),
]


# ── Esquema de la DB ─────────────────────────────────────────────────────────

_PRACTICE_SCHEMA = """
CREATE TABLE IF NOT EXISTS practice_queue (
    concept TEXT PRIMARY KEY,
    node_id TEXT,
    mastery_level TEXT DEFAULT 'NOVICE',
    schedule_index INTEGER DEFAULT 0,
    next_review_at REAL NOT NULL,
    last_review_at REAL,
    review_count INTEGER DEFAULT 0,
    total_recall_score REAL DEFAULT 0.0,
    avg_recall_score REAL DEFAULT 0.0,
    streak_correct INTEGER DEFAULT 0,
    streak_wrong INTEGER DEFAULT 0,
    best_recall_score REAL DEFAULT 0.0,
    created_at REAL,
    updated_at REAL,
    FOREIGN KEY (node_id) REFERENCES knowledge_nodes(id)
);

CREATE TABLE IF NOT EXISTS practice_sessions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    concept TEXT NOT NULL,
    node_id TEXT,
    ts REAL NOT NULL,
    recall_type TEXT DEFAULT 'active_recall',
    prompt TEXT,
    response_generated TEXT,
    reference_definition TEXT,
    similarity_score REAL,
    recall_success INTEGER DEFAULT 0,
    duration_ms INTEGER,
    notes TEXT
);

CREATE TABLE IF NOT EXISTS mastery_gate_progress (
    concept TEXT PRIMARY KEY,
    node_id TEXT,
    current_level TEXT DEFAULT 'NOVICE',
    target_level TEXT,
    executions_done INTEGER DEFAULT 0,
    contexts_visited INTEGER DEFAULT 0,
    cross_domain_edges INTEGER DEFAULT 0,
    days_at_current_level REAL DEFAULT 0.0,
    level_achieved_at REAL,
    ser_verified INTEGER DEFAULT 0,
    tests_passed INTEGER DEFAULT 0,
    gate_satisfied INTEGER DEFAULT 0,
    updated_at REAL,
    FOREIGN KEY (node_id) REFERENCES knowledge_nodes(id)
);

CREATE INDEX IF NOT EXISTS idx_practice_next_review ON practice_queue(next_review_at);
CREATE INDEX IF NOT EXISTS idx_practice_level ON practice_queue(mastery_level);
CREATE INDEX IF NOT EXISTS idx_practice_sessions_concept ON practice_sessions(concept);
CREATE INDEX IF NOT EXISTS idx_practice_sessions_ts ON practice_sessions(ts DESC);
CREATE INDEX IF NOT EXISTS idx_gate_progress_level ON mastery_gate_progress(current_level);
"""


def _ensure_schema():
    try:
        conn = get_conn(BRAIN_DB, timeout=10)
        conn.executescript(_PRACTICE_SCHEMA)
        conn.commit()
    except Exception as e:
        log.error("Error creando schema practice: %s", e)


_ensure_schema()


# ── Utilidades ────────────────────────────────────────────────────────────────

def _score_to_level(score: float) -> str:
    if score >= 0.90:
        return "MASTER"
    elif score >= 0.75:
        return "EXPERT"
    elif score >= 0.55:
        return "JOURNEYMAN"
    elif score >= 0.33:
        return "APPRENTICE"
    return "NOVICE"


def _level_index(level: str) -> int:
    levels = ["NOVICE", "APPRENTICE", "JOURNEYMAN", "EXPERT", "MASTER"]
    return levels.index(level) if level in levels else 0


def _next_level(level: str) -> Optional[str]:
    levels = ["NOVICE", "APPRENTICE", "JOURNEYMAN", "EXPERT", "MASTER"]
    idx = levels.index(level) if level in levels else -1
    return levels[idx + 1] if 0 <= idx < len(levels) - 1 else None


# ── PracticeQueue ─────────────────────────────────────────────────────────────

class PracticeQueue:
    """Cola de repetición espaciada.

    Cada concepto tiene un next_review_at. Cuando llega la hora, EIDOS debe
    hacer active recall del concepto. Según el resultado, el concepto avanza
    o retrocede en el schedule."""

    def __init__(self):
        self._lock = threading.Lock()

    def schedule_concept(self, concept: str, node_id: str,
                         mastery_level: str = "NOVICE",
                         schedule_index: int = 0) -> Dict[str, Any]:
        """Añade o actualiza un concepto en la cola de práctica.

        Args:
            concept: Nombre del concepto
            node_id: ID en knowledge_nodes
            mastery_level: Nivel de maestría actual
            schedule_index: Índice en el schedule (0 = primera revisión)
        """
        now = time.time()
        level = mastery_level if mastery_level in PRACTICE_SCHEDULES else "NOVICE"
        idx = min(schedule_index, len(PRACTICE_SCHEDULES[level]) - 1)
        interval = PRACTICE_SCHEDULES[level][idx]
        next_review = now + interval

        try:
            conn = get_conn(BRAIN_DB, timeout=10)
            conn.execute("PRAGMA journal_mode=WAL")

            existing = conn.execute(
                "SELECT 1 FROM practice_queue WHERE concept = ?", (concept,)
            ).fetchone()

            if existing:
                conn.execute("""
                    UPDATE practice_queue
                    SET node_id = ?, mastery_level = ?, schedule_index = ?,
                        next_review_at = ?, updated_at = ?
                    WHERE concept = ?
                """, (node_id, level, idx, next_review, now, concept))
            else:
                conn.execute("""
                    INSERT INTO practice_queue
                    (concept, node_id, mastery_level, schedule_index,
                     next_review_at, created_at, updated_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?)
                """, (concept, node_id, level, idx, next_review, now, now))

                # También crear entrada en mastery_gate_progress
                conn.execute("""
                    INSERT OR IGNORE INTO mastery_gate_progress
                    (concept, node_id, current_level, target_level,
                     level_achieved_at, updated_at)
                    VALUES (?, ?, ?, ?, ?, ?)
                """, (concept, node_id, level, _next_level(level), now, now))

            conn.commit()

            return {
                "ok": True,
                "concept": concept,
                "mastery_level": level,
                "schedule_index": idx,
                "interval_hours": round(interval / 3600, 1),
                "next_review_at": next_review,
                "next_review_iso": time.strftime(
                    "%Y-%m-%d %H:%M:%S", time.localtime(next_review)
                ),
            }

        except Exception as e:
            log.error("schedule_concept(%s): %s", concept, e)
            return {"ok": False, "error": str(e)}

    def get_due_concepts(self, limit: int = 20) -> List[Dict[str, Any]]:
        """Obtiene conceptos que toca repasar AHORA (next_review_at <= now).

        Ordenados por prioridad:
          1. Más atrasados primero (overdue)
          2. Nivel más bajo primero (NOVICE antes que MASTER)
          3. Peor avg_recall_score primero
        """
        now = time.time()
        try:
            conn = get_conn(BRAIN_DB, timeout=5, read_only=True)
            rows = conn.execute("""
                SELECT pq.concept, pq.node_id, pq.mastery_level,
                       pq.schedule_index, pq.next_review_at,
                       pq.review_count, pq.avg_recall_score,
                       pq.streak_correct, pq.streak_wrong,
                       kn.definition
                FROM practice_queue pq
                LEFT JOIN knowledge_nodes kn ON pq.node_id = kn.id
                WHERE pq.next_review_at <= ?
                ORDER BY
                    (? - pq.next_review_at) DESC,
                    CASE pq.mastery_level
                        WHEN 'NOVICE' THEN 0
                        WHEN 'APPRENTICE' THEN 1
                        WHEN 'JOURNEYMAN' THEN 2
                        WHEN 'EXPERT' THEN 3
                        WHEN 'MASTER' THEN 4
                    END ASC,
                    pq.avg_recall_score ASC
                LIMIT ?
            """, (now, now, limit)).fetchall()

            return [
                {
                    "concept": r[0],
                    "node_id": r[1],
                    "mastery_level": r[2],
                    "schedule_index": r[3],
                    "next_review_at": r[4],
                    "overdue_hours": round((now - r[4]) / 3600, 1),
                    "review_count": r[5],
                    "avg_recall_score": round(r[6] or 0.0, 3),
                    "streak_correct": r[7],
                    "streak_wrong": r[8],
                    "definition": (r[9] or "")[:200] if r[9] else "",
                }
                for r in rows
            ]
        except Exception as e:
            log.error("get_due_concepts error: %s", e)
            return []

    def record_review(self, concept: str, recall_score: float,
                      duration_ms: int = 0,
                      response_generated: str = "",
                      notes: str = "") -> Dict[str, Any]:
        """Registra una sesión de práctica y actualiza la cola.

        Según el recall_score:
          - >= 0.80: ÉXITO → avanzar schedule_index (más espaciado)
          - >= 0.50: PARCIAL → mantener schedule_index
          - < 0.50:  FALLO → retroceder schedule_index (más frecuente)

        Args:
            concept: Concepto practicado
            recall_score: 0.0-1.0 calidad del recuerdo
            duration_ms: Duración de la práctica
            response_generated: Lo que EIDOS generó desde memoria
            notes: Notas adicionales

        Returns: Resultado con nueva programación
        """
        now = time.time()
        success = recall_score >= 0.80
        partial = 0.50 <= recall_score < 0.80

        try:
            conn = get_conn(BRAIN_DB, timeout=10)
            conn.execute("PRAGMA journal_mode=WAL")

            # Obtener estado actual de la cola
            row = conn.execute(
                "SELECT node_id, mastery_level, schedule_index, review_count, "
                "total_recall_score, best_recall_score, streak_correct, streak_wrong "
                "FROM practice_queue WHERE concept = ?",
                (concept,)
            ).fetchone()

            if not row:
                # Primera revisión: programar desde NOVICE
                node_id = self._resolve_node_id(conn, concept)
                level = "NOVICE"
                idx = 0
                total_reviews = 0
                total_score = 0.0
                best_score = 0.0
                streak_correct = 0
                streak_wrong = 0
            else:
                node_id, level, idx, total_reviews, total_score, best_score, \
                    streak_correct, streak_wrong = row
                if total_score is None:
                    total_score = 0.0
                if best_score is None:
                    best_score = 0.0

            # Actualizar estadísticas
            new_total_reviews = (total_reviews or 0) + 1
            new_total_score = (total_score or 0.0) + recall_score
            new_avg_score = new_total_score / new_total_reviews
            new_best_score = max(best_score or 0.0, recall_score)

            if success:
                new_streak_correct = (streak_correct or 0) + 1
                new_streak_wrong = 0
                # Avanzar índice (más espaciado)
                new_idx = min(idx + 1, len(PRACTICE_SCHEDULES.get(level, PRACTICE_SCHEDULES["NOVICE"])) - 1)
            elif partial:
                new_streak_correct = (streak_correct or 0)
                new_streak_wrong = (streak_wrong or 0) + 1
                # Mantener índice
                new_idx = idx
            else:
                new_streak_correct = 0
                new_streak_wrong = (streak_wrong or 0) + 1
                # Retroceder índice (más frecuente)
                new_idx = max(0, idx - 1)

            # Calcular nuevo next_review_at
            interval = PRACTICE_SCHEDULES.get(level, PRACTICE_SCHEDULES["NOVICE"])[new_idx]
            next_review = now + interval

            # Actualizar practice_queue
            conn.execute("""
                INSERT INTO practice_queue
                (concept, node_id, mastery_level, schedule_index,
                 next_review_at, last_review_at, review_count,
                 total_recall_score, avg_recall_score,
                 streak_correct, streak_wrong, best_recall_score,
                 updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(concept) DO UPDATE SET
                    mastery_level = excluded.mastery_level,
                    schedule_index = excluded.schedule_index,
                    next_review_at = excluded.next_review_at,
                    last_review_at = excluded.last_review_at,
                    review_count = excluded.review_count,
                    total_recall_score = excluded.total_recall_score,
                    avg_recall_score = excluded.avg_recall_score,
                    streak_correct = excluded.streak_correct,
                    streak_wrong = excluded.streak_wrong,
                    best_recall_score = excluded.best_recall_score,
                    updated_at = excluded.updated_at
            """, (
                concept, node_id, level, new_idx,
                next_review, now, new_total_reviews,
                new_total_score, new_avg_score,
                new_streak_correct, new_streak_wrong,
                new_best_score, now,
            ))

            # Registrar sesión
            conn.execute("""
                INSERT INTO practice_sessions
                (concept, node_id, ts, recall_type, response_generated,
                 similarity_score, recall_success, duration_ms, notes)
                VALUES (?, ?, ?, 'active_recall', ?, ?, ?, ?, ?)
            """, (
                concept, node_id, now,
                response_generated[:2000],
                recall_score,
                1 if success else 0,
                duration_ms,
                notes[:500],
            ))

            conn.commit()

            # Actualizar GrowthEngine para reflejar la practica
            try:
                from core.eidos_growth import get_growth_engine
                engine = get_growth_engine()
                delta = 0.03 if success else (0.01 if partial else 0.0)
                engine.update_mastery(
                    concept,
                    declarative_delta=delta,
                    metacognitive_delta=0.02,
                    event_type="practice_recall",
                    source="practice_queue",
                )
            except Exception as ge_err:
                log.debug("GrowthEngine update fallo en record_review: %s", ge_err)

            return {
                "ok": True,
                "concept": concept,
                "recall_score": round(recall_score, 3),
                "success": success,
                "new_schedule_index": new_idx,
                "interval_hours": round(interval / 3600, 1),
                "next_review_at": next_review,
                "avg_recall_score": round(new_avg_score, 3),
                "streak_correct": new_streak_correct,
                "streak_wrong": new_streak_wrong,
            }

        except Exception as e:
            log.error("record_review(%s): %s", concept, e)
            return {"ok": False, "error": str(e)}

    def _resolve_node_id(self, conn, concept: str) -> Optional[str]:
        row = conn.execute(
            "SELECT id FROM knowledge_nodes WHERE concept = ?", (concept,)
        ).fetchone()
        return row[0] if row else None

    def get_queue_stats(self) -> Dict[str, Any]:
        """Estadísticas de la cola de práctica."""
        try:
            conn = get_conn(BRAIN_DB, timeout=5, read_only=True)
            now = time.time()

            total = conn.execute("SELECT COUNT(*) FROM practice_queue").fetchone()[0]
            due = conn.execute(
                "SELECT COUNT(*) FROM practice_queue WHERE next_review_at <= ?",
                (now,)
            ).fetchone()[0]
            by_level = dict(conn.execute(
                "SELECT mastery_level, COUNT(*) FROM practice_queue GROUP BY mastery_level"
            ).fetchall())
            avg_recall = conn.execute(
                "SELECT COALESCE(AVG(avg_recall_score), 0) FROM practice_queue "
                "WHERE review_count > 0"
            ).fetchone()[0]

            sessions_today = conn.execute(
                "SELECT COUNT(*) FROM practice_sessions "
                "WHERE ts >= ?",
                (now - 86400,)
            ).fetchone()[0]

            return {
                "total_concepts": total,
                "due_now": due,
                "due_ratio": round(due / max(total, 1), 3),
                "by_level": {k: v for k, v in by_level.items()},
                "avg_recall_score": round(avg_recall, 3),
                "sessions_today": sessions_today,
            }
        except Exception as e:
            log.error("get_queue_stats error: %s", e)
            return {"error": str(e)}

    def bootstrap_queue(self, max_concepts: int = 200) -> int:
        """Inicializa la cola desde concept_mastery para conceptos no programados."""
        added = 0
        now = time.time()
        try:
            conn = get_conn(BRAIN_DB, timeout=10)
            conn.execute("PRAGMA journal_mode=WAL")

            # Obtener conceptos desde GrowthEngine (fuente autoritativa de maestria)
            from core.eidos_growth import get_growth_engine
            engine = get_growth_engine()
            all_mastery = engine.get_all_mastery(limit=max_concepts * 2)

            # Filtrar: solo conceptos que NO estan ya en practice_queue
            existing_concepts = set(
                row[0] for row in conn.execute(
                    "SELECT concept FROM practice_queue"
                ).fetchall()
            )

            for ms in all_mastery:
                if ms.concept in existing_concepts:
                    continue
                node_id = ms.node_id
                concept = ms.concept
                overall = ms.overall_score
                level = _score_to_level(overall)
                schedule = PRACTICE_SCHEDULES.get(level, PRACTICE_SCHEDULES["NOVICE"])
                # Empezar en indice inicial para su nivel
                idx = 0
                next_review = now + schedule[idx]

                conn.execute("""
                    INSERT OR IGNORE INTO practice_queue
                    (concept, node_id, mastery_level, schedule_index,
                     next_review_at, created_at, updated_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?)
                """, (concept, node_id, level, idx, next_review, now, now))
                added += 1

                if added >= max_concepts:
                    break

            conn.commit()
            log.info("Practice queue bootstrap: %d conceptos añadidos", added)
        except Exception as e:
            log.error("bootstrap_queue error: %s", e)
        return added

    def populate_queue_from_graph(self, min_confidence: float = 0.55,
                                   max_concepts: int = 0,
                                   stagger_initial_reviews: bool = True) -> Dict[str, Any]:
        """P0: Populate practice_queue from ALL quality nodes in the knowledge graph.

        Queries knowledge_nodes with confidence >= min_confidence and creates
        practice_queue entries for each one not already in the queue.

        For each quality node:
        - Sets initial mastery_level based on confidence score
        - Schedules first review using PRACTICE_SCHEDULES
        - If the node is a command (has man page / binary), marks it as 'command' type
        - If the node is a concept, marks it as 'concept' type
        - Staggers initial reviews to avoid all 2,836+ items being due at once

        Args:
            min_confidence: Minimum confidence to include (default 0.55)
            max_concepts: Max concepts to add (0 = unlimited)
            stagger_initial_reviews: If True, stagger review times across the first
                                     schedule period so they don't all become due at once.

        Returns:
            {added, skipped_existing, total_quality_nodes, by_level, by_type, errors}
        """
        added = 0
        skipped_existing = 0
        errors = 0
        now = time.time()
        by_level: Dict[str, int] = defaultdict(int)
        by_type: Dict[str, int] = defaultdict(int)

        # Command-like categories: nodes that are likely executables with man pages
        COMMAND_CATEGORIES = {
            "kali_tool", "kali_app", "kali_tools", "linux_commands",
            "system_binaries", "system_command", "tool", "tools",
            "debian_package", "debian_packages", "ai_tool", "ai_tools",
            "dev_tools", "network_tools", "github",
        }

        try:
            conn = get_conn(BRAIN_DB, timeout=30)
            conn.execute("PRAGMA journal_mode=WAL")

            # Get existing concepts in practice_queue
            existing_concepts = set(
                row[0] for row in conn.execute(
                    "SELECT concept FROM practice_queue"
                ).fetchall()
            )

            # Query all quality nodes, ordered by confidence DESC so the best
            # concepts get processed first
            quality_nodes = conn.execute("""
                SELECT id, concept, definition, category, confidence, source
                FROM knowledge_nodes
                WHERE confidence >= ?
                ORDER BY confidence DESC
            """, (min_confidence,)).fetchall()

            total_quality = len(quality_nodes)
            log.info("populate_queue_from_graph: %d quality nodes found (conf >= %.2f), "
                     "%d already in practice_queue",
                     total_quality, min_confidence,
                     len(existing_concepts.intersection(
                         r[1] for r in quality_nodes)))

            batch = []
            for i, row in enumerate(quality_nodes):
                node_id, concept, definition, category, confidence, source = row

                if concept in existing_concepts:
                    skipped_existing += 1
                    continue

                if max_concepts > 0 and (added + len(batch)) >= max_concepts:
                    break

                # Determine mastery level from confidence
                level = _score_to_level(confidence)

                # Determine node type (command vs concept)
                cat_lower = (category or "").lower()
                is_command = (
                    cat_lower in COMMAND_CATEGORIES or
                    any(w in cat_lower for w in ["tool", "command", "binary", "kali_", "cmd"])
                )

                # Determine practice_exercise type
                # (stored in practice_queue via concept; actual exercise generated
                #  at review time by ActiveRecall)
                node_type = "command" if is_command else "concept"

                # Schedule first review
                schedule = PRACTICE_SCHEDULES.get(level, PRACTICE_SCHEDULES["NOVICE"])
                idx = 0  # Start at beginning of the schedule for their level

                # Stagger initial reviews across the first interval period
                # to avoid everything being due at the same moment
                if stagger_initial_reviews and added > 0:
                    # Spread reviews across 0-100% of the first interval
                    stagger_fraction = (added % 100) / 100.0
                    stagger_offset = schedule[0] * stagger_fraction
                    next_review = now + stagger_offset
                else:
                    next_review = now + schedule[0]

                batch.append((concept, node_id, level, idx, next_review, now, now))

                # Batch insert every 500 nodes
                if len(batch) >= 500:
                    conn.executemany("""
                        INSERT OR IGNORE INTO practice_queue
                        (concept, node_id, mastery_level, schedule_index,
                         next_review_at, created_at, updated_at)
                        VALUES (?, ?, ?, ?, ?, ?, ?)
                    """, batch)
                    added += len(batch)
                    for _, _, lvl, _, _, _, _ in batch:
                        by_level[lvl] += 1
                        by_type[node_type] += 1
                    batch = []

                # Also ensure mastery_gate_progress entry exists
                try:
                    conn.execute("""
                        INSERT OR IGNORE INTO mastery_gate_progress
                        (concept, node_id, current_level, target_level,
                         level_achieved_at, updated_at)
                        VALUES (?, ?, ?, ?, ?, ?)
                    """, (concept, node_id, level, _next_level(level), now, now))
                except Exception:
                    pass

            # Insert remaining batch
            if batch:
                conn.executemany("""
                    INSERT OR IGNORE INTO practice_queue
                    (concept, node_id, mastery_level, schedule_index,
                     next_review_at, created_at, updated_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?)
                """, batch)
                added += len(batch)

            conn.commit()

            log.info("populate_queue_from_graph: COMPLETE. %d added, %d skipped (existing), "
                     "%d errors, %d total quality nodes. "
                     "By level: %s",
                     added, skipped_existing, errors, total_quality,
                     dict(by_level))

            return {
                "ok": True,
                "added": added,
                "skipped_existing": skipped_existing,
                "errors": errors,
                "total_quality_nodes": total_quality,
                "by_level": dict(by_level),
                "by_type": dict(by_type),
                "min_confidence": min_confidence,
            }

        except Exception as e:
            log.error("populate_queue_from_graph error: %s", e)
            return {
                "ok": False,
                "error": str(e),
                "added": added,
                "skipped_existing": skipped_existing,
                "total_quality_nodes": 0,
            }


# ── ActiveRecall ──────────────────────────────────────────────────────────────

class ActiveRecall:
    """Genera y evalúa ejercicios de recuerdo activo.

    En vez de relectura pasiva, EIDOS:
    1. Recibe el nombre del concepto
    2. Genera una definición/resumen DESDE MEMORIA
    3. Compara con la definición de referencia
    4. Recibe un score de similitud

    Esto fuerza el recuerdo activo, fortaleciendo las conexiones neuronales."""

    def __init__(self):
        self._session_count = 0

    def generate_prompt(self, concept: str) -> Dict[str, Any]:
        """Genera un prompt de recuerdo activo para un concepto.

        Devuelve la pregunta que EIDOS debe responder desde memoria,
        SIN acceso a la definición real.

        Args:
            concept: Concepto a practicar

        Returns: {concept, prompt_type, prompt, reference_definition}
        """
        try:
            conn = get_conn(BRAIN_DB, timeout=5, read_only=True)
            row = conn.execute(
                "SELECT id, concept, definition, category FROM knowledge_nodes "
                "WHERE concept = ? OR id = ?",
                (concept, concept)
            ).fetchone()

            if not row:
                # Buscar por LIKE
                row = conn.execute(
                    "SELECT id, concept, definition, category FROM knowledge_nodes "
                    "WHERE concept LIKE ? LIMIT 1",
                    (f"%{concept}%",)
                ).fetchone()

            if not row:
                return {"error": f"Concepto '{concept}' no encontrado"}

            node_id, found_concept, definition, category = row

            # Elegir tipo de prompt según categoría
            prompt_type = self._choose_prompt_type(category or "")
            prompt = self._build_prompt(found_concept, definition or "", prompt_type)

            return {
                "concept": found_concept,
                "node_id": node_id,
                "prompt_type": prompt_type,
                "prompt": prompt,
                "reference_definition": definition or "",
                "category": category or "",
            }

        except Exception as e:
            log.error("generate_prompt(%s): %s", concept, e)
            return {"error": str(e)}

    def _choose_prompt_type(self, category: str) -> str:
        """Elige el tipo de pregunta según la categoría."""
        cat_lower = category.lower()
        if any(w in cat_lower for w in ["tool", "comando", "command", "kali"]):
            return "how_to_use"      # "¿Cómo se usa X?"
        elif any(w in cat_lower for w in ["concept", "teoria", "protocol"]):
            return "explain"          # "Explica qué es X"
        elif any(w in cat_lower for w in ["security", "vulnerability"]):
            return "identify"         # "¿Cómo identificas X?"
        elif any(w in cat_lower for w in ["language", "programming"]):
            return "syntax"           # "¿Cuál es la sintaxis de X?"
        else:
            return "define"           # "Define X"

    def _build_prompt(self, concept: str, definition: str,
                      prompt_type: str) -> str:
        """Construye el prompt según el tipo."""
        prompts = {
            "how_to_use": (
                f"Describe CÓMO usar '{concept}' desde la terminal. "
                f"¿Qué flags/opciones son importantes? "
                f"¿Qué parámetros necesita?"
            ),
            "explain": (
                f"Explica qué es '{concept}' con tus propias palabras. "
                f"¿Para qué sirve? ¿En qué contexto se usa?"
            ),
            "identify": (
                f"¿Cómo identificarías '{concept}'? "
                f"¿Qué características o indicadores tiene?"
            ),
            "syntax": (
                f"Describe la sintaxis o forma de uso de '{concept}'. "
                f"¿Qué estructura tiene? ¿Qué variantes conoces?"
            ),
            "define": (
                f"Define '{concept}' desde memoria. "
                f"¿Qué es? ¿Para qué se usa? Da un ejemplo si puedes."
            ),
        }
        return prompts.get(prompt_type, prompts["define"])

    def evaluate_recall(self, concept: str,
                        generated_response: str) -> Dict[str, Any]:
        """Evalúa la respuesta generada por EIDOS contra la definición real.

        Usa múltiples métricas de similitud:
        - SequenceMatcher (similitud textual)
        - Keyword overlap (coincidencia de palabras clave)
        - Length ratio (la respuesta no debería ser demasiado corta)

        Args:
            concept: Concepto evaluado
            generated_response: Lo que EIDOS generó desde memoria

        Returns: {ok, score, metrics, feedback}
        """
        if not generated_response or len(generated_response.strip()) < 10:
            return {
                "ok": True,
                "score": 0.0,
                "metrics": {"textual_similarity": 0.0, "keyword_overlap": 0.0,
                            "length_score": 0.0},
                "feedback": "Respuesta demasiado corta — no se pudo evaluar",
            }

        try:
            conn = get_conn(BRAIN_DB, timeout=5, read_only=True)
            row = conn.execute(
                "SELECT definition FROM knowledge_nodes "
                "WHERE concept = ? OR id = ?",
                (concept, concept)
            ).fetchone()

            if not row:
                return {"ok": True, "score": 0.3, "feedback": "No hay referencia para comparar"}

            reference = (row[0] or "").lower().strip()
            generated = generated_response.lower().strip()

            # 1. Similitud textual (SequenceMatcher)
            textual_sim = SequenceMatcher(None, reference[:500], generated[:500]).ratio()

            # 2. Keyword overlap
            ref_words = set(re.findall(r'[a-záéíóúñ]{3,}', reference))
            gen_words = set(re.findall(r'[a-záéíóúñ]{3,}', generated))
            stopwords = {"que", "del", "las", "los", "con", "por", "para", "como",
                        "the", "and", "for", "that", "this", "with", "from", "what"}
            ref_keywords = ref_words - stopwords
            gen_keywords = gen_words - stopwords
            if ref_keywords:
                keyword_overlap = len(ref_keywords & gen_keywords) / len(ref_keywords)
            else:
                keyword_overlap = 0.0

            # 3. Length ratio (respuesta no demasiado corta ni demasiado larga)
            ref_len = len(reference)
            gen_len = len(generated)
            length_ratio = min(gen_len / max(ref_len, 1), 2.0)
            length_score = 1.0 if 0.3 <= length_ratio <= 1.5 else \
                           max(0.0, 1.0 - abs(length_ratio - 0.5))

            # Score combinado
            score = textual_sim * 0.35 + keyword_overlap * 0.50 + length_score * 0.15

            # Feedback cualitativo
            if score >= 0.80:
                feedback = "Excelente recuerdo — definición muy cercana a la referencia"
            elif score >= 0.60:
                feedback = "Buen recuerdo — capturaste lo esencial"
            elif score >= 0.40:
                feedback = "Recuerdo parcial — faltan detalles importantes"
            elif score >= 0.20:
                feedback = "Recuerdo débil — conviene repasar más frecuentemente"
            else:
                feedback = "Recuerdo muy bajo — necesita estudio inmediato"

            return {
                "ok": True,
                "score": round(score, 4),
                "metrics": {
                    "textual_similarity": round(textual_sim, 4),
                    "keyword_overlap": round(keyword_overlap, 4),
                    "length_score": round(length_score, 4),
                },
                "feedback": feedback,
                "reference_length": ref_len,
                "response_length": gen_len,
            }

        except Exception as e:
            log.error("evaluate_recall(%s): %s", concept, e)
            return {"ok": False, "error": str(e)}

    def practice_session(self, concept: str,
                         generated_response: str) -> Dict[str, Any]:
        """Sesión completa de práctica: prompt → respuesta → evaluación → registro.

        Args:
            concept: Concepto a practicar
            generated_response: Respuesta de EIDOS desde memoria

        Returns: Resultado completo incluyendo evaluación y siguiente revisión
        """
        t0 = time.time()

        # Evaluar
        evaluation = self.evaluate_recall(concept, generated_response)
        if not evaluation.get("ok"):
            return evaluation

        score = evaluation["score"]
        duration_ms = int((time.time() - t0) * 1000)

        # Registrar en la cola
        queue = get_practice_queue()
        result = queue.record_review(
            concept=concept,
            recall_score=score,
            duration_ms=duration_ms,
            response_generated=generated_response,
        )

        # El GrowthEngine ya se actualiza en PracticeQueue.record_review()

        result.update({
            "evaluation": evaluation,
            "duration_ms": duration_ms,
        })

        self._session_count += 1
        return result

    def stats(self) -> Dict[str, Any]:
        return {"total_sessions": self._session_count}


# ── MasteryGateChecker ───────────────────────────────────────────────────────

class MasteryGateChecker:
    """Verifica si un concepto cumple las condiciones para avanzar de nivel."""

    def check_gate(self, concept: str) -> Dict[str, Any]:
        """Verifica si un concepto puede avanzar al siguiente nivel.

        Comprueba todas las condiciones del gate correspondiente y retorna
        qué falta para avanzar.

        Returns: {can_advance, current_level, target_level, conditions, ...}
        """
        try:
            conn = get_conn(BRAIN_DB, timeout=10, read_only=True)

            # Obtener estado actual
            row = conn.execute(
                "SELECT node_id, current_level, executions_done, "
                "contexts_visited, cross_domain_edges, "
                "level_achieved_at, ser_verified, tests_passed "
                "FROM mastery_gate_progress WHERE concept = ?",
                (concept,)
            ).fetchone()

            if not row:
                return {"can_advance": False, "reason": "Concepto no registrado en gates"}

            node_id, current_level, exec_done, ctx_done, cross_done, \
                achieved_at, ser_verified, tests_passed = row

            next_level = _next_level(current_level)
            if not next_level:
                return {"can_advance": False, "reason": "Ya está en MASTER — nivel máximo",
                        "current_level": current_level}

            # Encontrar el gate correspondiente
            gate = None
            for g in MASTERY_GATES:
                if g.from_level == current_level:
                    gate = g
                    break

            if not gate:
                return {"can_advance": False, "reason": f"No hay gate definido para {current_level}",
                        "current_level": current_level}

            # Verificar cada condición
            conditions = []
            all_met = True

            # 1. Ejecuciones mínimas
            exec_met = (exec_done or 0) >= gate.min_executions
            conditions.append({
                "name": "min_executions",
                "required": gate.min_executions,
                "current": exec_done or 0,
                "met": exec_met,
            })
            if not exec_met:
                all_met = False
                # Actualizar desde concept_mastery
                cm_row = conn.execute(
                    "SELECT executions_success FROM concept_mastery WHERE node_id = ?",
                    (node_id,)
                ).fetchone()
                if cm_row:
                    conditions[-1]["current"] = cm_row[0] or 0
                    conditions[-1]["met"] = (cm_row[0] or 0) >= gate.min_executions

            # 2. Contextos mínimos
            ctx_met = (ctx_done or 0) >= gate.min_contexts
            conditions.append({
                "name": "min_contexts",
                "required": gate.min_contexts,
                "current": ctx_done or 0,
                "met": ctx_met,
            })
            if not ctx_met:
                all_met = False
                cm_row = conn.execute(
                    "SELECT unique_contexts FROM concept_mastery WHERE node_id = ?",
                    (node_id,)
                ).fetchone()
                if cm_row:
                    conditions[-1]["current"] = cm_row[0] or 0
                    conditions[-1]["met"] = (cm_row[0] or 0) >= gate.min_contexts

            # 3. Cross-domain edges
            cross_met = (cross_done or 0) >= gate.min_cross_domain_edges
            conditions.append({
                "name": "min_cross_domain_edges",
                "required": gate.min_cross_domain_edges,
                "current": cross_done or 0,
                "met": cross_met,
            })
            if not cross_met:
                all_met = False
                cm_row = conn.execute(
                    "SELECT cross_domain_edges FROM concept_mastery WHERE node_id = ?",
                    (node_id,)
                ).fetchone()
                if cm_row:
                    conditions[-1]["current"] = cm_row[0] or 0
                    conditions[-1]["met"] = (cm_row[0] or 0) >= gate.min_cross_domain_edges

            # 4. Días en nivel
            if gate.min_days_at_level > 0 and achieved_at:
                days_at_level = (time.time() - achieved_at) / 86400.0
                days_met = days_at_level >= gate.min_days_at_level
            elif gate.min_days_at_level > 0:
                days_met = False
                days_at_level = 0.0
            else:
                days_met = True
                days_at_level = 0.0

            conditions.append({
                "name": "min_days_at_level",
                "required": gate.min_days_at_level,
                "current": round(days_at_level, 1),
                "met": days_met,
            })
            if not days_met:
                all_met = False

            # 5. Verificación de SER
            if gate.requires_ser_verification:
                ser_met = (ser_verified or 0) >= 1
                conditions.append({
                    "name": "ser_verification",
                    "required": 1,
                    "current": ser_verified or 0,
                    "met": ser_met,
                })
                if not ser_met:
                    all_met = False

            # 6. Tests superados
            if gate.min_tests_passed > 0:
                tests_met = (tests_passed or 0) >= gate.min_tests_passed
                conditions.append({
                    "name": "min_tests_passed",
                    "required": gate.min_tests_passed,
                    "current": tests_passed or 0,
                    "met": tests_met,
                })
                if not tests_met:
                    all_met = False

            return {
                "can_advance": all_met,
                "concept": concept,
                "current_level": current_level,
                "target_level": next_level,
                "conditions": conditions,
                "all_met": all_met,
            }

        except Exception as e:
            log.error("check_gate(%s): %s", concept, e)
            return {"can_advance": False, "error": str(e)}

    def advance_level(self, concept: str) -> Dict[str, Any]:
        """Avanza un concepto al siguiente nivel si cumple el gate.

        Returns: {advanced, new_level, ...}
        """
        gate_result = self.check_gate(concept)

        if not gate_result.get("can_advance"):
            return {
                "advanced": False,
                "concept": concept,
                "reason": "Gate no superado",
                "gate_check": gate_result,
            }

        new_level = gate_result["target_level"]
        now = time.time()

        try:
            conn = get_conn(BRAIN_DB, timeout=10)
            conn.execute("PRAGMA journal_mode=WAL")

            conn.execute("""
                UPDATE mastery_gate_progress
                SET current_level = ?, target_level = ?,
                    level_achieved_at = ?, gate_satisfied = 1,
                    updated_at = ?
                WHERE concept = ?
            """, (new_level, _next_level(new_level), now, now, concept))

            # También actualizar practice_queue si existe
            conn.execute("""
                UPDATE practice_queue
                SET mastery_level = ?, schedule_index = 0, updated_at = ?
                WHERE concept = ?
            """, (new_level, now, concept))

            conn.commit()

            log.info("Gate avanzado: %s de %s → %s", concept,
                     gate_result["current_level"], new_level)

            return {
                "advanced": True,
                "concept": concept,
                "previous_level": gate_result["current_level"],
                "new_level": new_level,
                "gate_conditions": gate_result["conditions"],
            }

        except Exception as e:
            log.error("advance_level(%s): %s", concept, e)
            return {"advanced": False, "error": str(e)}

    def sync_from_mastery(self) -> int:
        """Sincroniza mastery_gate_progress desde concept_mastery."""
        updated = 0
        try:
            conn = get_conn(BRAIN_DB, timeout=10)
            conn.execute("PRAGMA journal_mode=WAL")

            rows = conn.execute("""
                SELECT cm.node_id, cm.concept,
                       (cm.declarative_score + cm.procedural_score
                        + cm.applicational_score + cm.metacognitive_score) / 4.0,
                       cm.executions_success, cm.unique_contexts,
                       cm.cross_domain_edges
                FROM concept_mastery cm
            """).fetchall()

            now = time.time()
            for node_id, concept, overall, exec_s, ctxs, cross in rows:
                level = _score_to_level(overall)
                conn.execute("""
                    INSERT INTO mastery_gate_progress
                    (concept, node_id, current_level, target_level,
                     executions_done, contexts_visited, cross_domain_edges,
                     level_achieved_at, updated_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(concept) DO UPDATE SET
                        current_level = excluded.current_level,
                        target_level = excluded.target_level,
                        executions_done = excluded.executions_done,
                        contexts_visited = excluded.contexts_visited,
                        cross_domain_edges = excluded.cross_domain_edges,
                        updated_at = excluded.updated_at
                """, (concept, node_id, level, _next_level(level),
                      exec_s or 0, ctxs or 0, cross or 0, now, now))
                updated += 1

            conn.commit()
        except Exception as e:
            log.error("sync_from_mastery error: %s", e)
        return updated

    def get_all_gates(self) -> List[Dict[str, Any]]:
        """Lista todos los conceptos con su progreso de gate."""
        try:
            conn = get_conn(BRAIN_DB, timeout=5, read_only=True)
            rows = conn.execute("""
                SELECT concept, current_level, target_level,
                       executions_done, contexts_visited, cross_domain_edges,
                       days_at_current_level, ser_verified, tests_passed,
                       gate_satisfied
                FROM mastery_gate_progress
                ORDER BY
                    CASE current_level
                        WHEN 'NOVICE' THEN 0
                        WHEN 'APPRENTICE' THEN 1
                        WHEN 'JOURNEYMAN' THEN 2
                        WHEN 'EXPERT' THEN 3
                        WHEN 'MASTER' THEN 4
                    END ASC
            """).fetchall()

            return [
                {
                    "concept": r[0],
                    "current_level": r[1],
                    "target_level": r[2] or _next_level(r[1]),
                    "executions_done": r[3],
                    "contexts_visited": r[4],
                    "cross_domain_edges": r[5],
                    "days_at_level": round(r[6] or 0.0, 1),
                    "ser_verified": bool(r[7]),
                    "tests_passed": r[8],
                    "gate_satisfied": bool(r[9]),
                }
                for r in rows
            ]
        except Exception as e:
            log.error("get_all_gates error: %s", e)
            return []


# ── Singleton ─────────────────────────────────────────────────────────────────

_practice_queue: Optional[PracticeQueue] = None
_active_recall: Optional[ActiveRecall] = None
_gate_checker: Optional[MasteryGateChecker] = None
_lock = threading.Lock()


def get_practice_queue() -> PracticeQueue:
    global _practice_queue
    if _practice_queue is None:
        with _lock:
            if _practice_queue is None:
                _practice_queue = PracticeQueue()
    return _practice_queue


def get_active_recall() -> ActiveRecall:
    global _active_recall
    if _active_recall is None:
        with _lock:
            if _active_recall is None:
                _active_recall = ActiveRecall()
    return _active_recall


def get_gate_checker() -> MasteryGateChecker:
    global _gate_checker
    if _gate_checker is None:
        with _lock:
            if _gate_checker is None:
                _gate_checker = MasteryGateChecker()
    return _gate_checker


# ── CLI ───────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    import sys

    if len(sys.argv) < 2:
        print("Uso: python eidos_practice.py <cmd> [args]")
        print("  queue              — mostrar cola (conceptos pendientes)")
        print("  due [N]            — conceptos que toca repasar AHORA")
        print("  prompt <concepto>  — generar prompt de recuerdo activo")
        print("  eval <concepto>    — evaluar respuesta (lee stdin)")
        print("  stats              — estadísticas de la cola")
        print("  bootstrap          — inicializar cola desde concept_mastery")
        print("  populate [conf] [max] — P0: poblar cola desde grafo (todos nodos calidad)")
        print("  gate <concepto>    — verificar gate de maestría")
        print("  advance <concepto> — intentar avanzar nivel")
        print("  gates              — listar progreso de todos los gates")
        print("  sync-gates         — sincronizar gates desde concept_mastery")
        sys.exit(1)

    cmd = sys.argv[1]

    if cmd == "queue":
        queue = get_practice_queue()
        concepts = queue.get_due_concepts(50)
        print(f"Cola de práctica ({len(concepts)} pendientes):")
        for c in concepts[:20]:
            print(f"  [{c['mastery_level']:12s}] {c['concept'][:45]} "
                  f"| Overdue: {c['overdue_hours']:.1f}h "
                  f"| Avg: {c['avg_recall_score']:.3f} "
                  f"| Streak: +{c['streak_correct']}/-{c['streak_wrong']}")

    elif cmd == "due":
        limit = int(sys.argv[2]) if len(sys.argv) > 2 else 20
        queue = get_practice_queue()
        due = queue.get_due_concepts(limit)
        print(f"Conceptos pendientes de repaso AHORA ({len(due)}):")
        for c in due:
            print(f"  [{c['mastery_level']}] {c['concept'][:50]} "
                  f"| Overdue: {c['overdue_hours']:.1f}h "
                  f"| Avg recall: {c['avg_recall_score']:.3f}")

    elif cmd == "prompt":
        concept = sys.argv[2] if len(sys.argv) > 2 else ""
        if not concept:
            print("Especifica un concepto")
            sys.exit(1)
        ar = get_active_recall()
        prompt_data = ar.generate_prompt(concept)
        if "error" in prompt_data:
            print(f"Error: {prompt_data['error']}")
        else:
            print(f"Tipo: {prompt_data['prompt_type']}")
            print(f"Concepto: {prompt_data['concept']}")
            print(f"\n{prompt_data['prompt']}")
            print(f"\n(Definición de referencia oculta — {len(prompt_data['reference_definition'])} chars)")

    elif cmd == "eval":
        concept = sys.argv[2] if len(sys.argv) > 2 else ""
        if not concept:
            print("Especifica un concepto")
            sys.exit(1)
        print(f"Pega la respuesta generada para '{concept}' (Ctrl+D para terminar):")
        response = sys.stdin.read().strip()
        ar = get_active_recall()
        result = ar.practice_session(concept, response)
        print(json.dumps(result, indent=2, ensure_ascii=False, default=str))

    elif cmd == "stats":
        queue = get_practice_queue()
        stats = queue.get_queue_stats()
        print(json.dumps(stats, indent=2, ensure_ascii=False))

    elif cmd == "bootstrap":
        queue = get_practice_queue()
        n = queue.bootstrap_queue()
        print(f"Cola inicializada: {n} conceptos añadidos")

    elif cmd == "populate":
        min_conf = float(sys.argv[2]) if len(sys.argv) > 2 else 0.55
        max_n = int(sys.argv[3]) if len(sys.argv) > 3 else 0
        no_stagger = "--no-stagger" in sys.argv
        queue = get_practice_queue()
        print(f"Poblando practice_queue desde knowledge_nodes (conf >= {min_conf}, "
              f"max={max_n or 'ilimitado'})...")
        result = queue.populate_queue_from_graph(
            min_confidence=min_conf,
            max_concepts=max_n,
            stagger_initial_reviews=not no_stagger,
        )
        if result["ok"]:
            print(f"OK: {result['added']} añadidos, {result['skipped_existing']} ya existían")
            print(f"     Total nodos calidad: {result['total_quality_nodes']}")
            print(f"     Por nivel: {result['by_level']}")
            print(f"     Por tipo: {result['by_type']}")
        else:
            print(f"ERROR: {result.get('error', 'desconocido')}")
            print(f"     Progreso parcial: {result['added']} añadidos")

    elif cmd == "gate":
        concept = sys.argv[2] if len(sys.argv) > 2 else ""
        if not concept:
            print("Especifica un concepto")
            sys.exit(1)
        gc = get_gate_checker()
        result = gc.check_gate(concept)
        print(json.dumps(result, indent=2, ensure_ascii=False))

    elif cmd == "advance":
        concept = sys.argv[2] if len(sys.argv) > 2 else ""
        if not concept:
            print("Especifica un concepto")
            sys.exit(1)
        gc = get_gate_checker()
        result = gc.advance_level(concept)
        print(json.dumps(result, indent=2, ensure_ascii=False))

    elif cmd == "gates":
        gc = get_gate_checker()
        gates = gc.get_all_gates()
        print(f"Progreso de gates ({len(gates)}):")
        for g in gates:
            satisfied = "✓" if g["gate_satisfied"] else "✗"
            print(f"  {satisfied} [{g['current_level']:12s} → {g['target_level']:12s}] "
                  f"{g['concept'][:40]}")
            print(f"    Exec={g['executions_done']} Ctx={g['contexts_visited']} "
                  f"Cross={g['cross_domain_edges']} Days={g['days_at_level']}")

    elif cmd == "sync-gates":
        gc = get_gate_checker()
        n = gc.sync_from_mastery()
        print(f"Gates sincronizados: {n} conceptos")

    else:
        print(f"Comando desconocido: {cmd}")
