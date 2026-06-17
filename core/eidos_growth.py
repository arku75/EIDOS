"""
core/eidos_growth.py — Growth & Evolution Engine [S125]
========================================================
Reemplaza la métrica engañosa de "independencia 95.5%" con un Growth Index real,
basado en el diseño validado por debate de 24 agentes.

Dimensiones de Maestría:
  NOVICE      (0.00 - 0.33)  — Sabe que el concepto existe
  APPRENTICE  (0.33 - 0.55)  — Puede ejecutar con supervisión
  JOURNEYMAN  (0.55 - 0.75)  — Ejecuta autónomamente en contextos conocidos
  EXPERT      (0.75 - 0.90)  — Aplica en contextos nuevos, transfiere
  MASTER      (0.90 - 1.00)  — Enseña, innova, generaliza

Sub-dimensiones por concepto:
  - declarative:   puede definir el concepto (saber QUÉ)
  - procedural:    puede ejecutar recetas (saber CÓMO)
  - applicational: puede aplicar en contextos nuevos (saber DÓNDE/CUÁNDO)
  - metacognitive: sabe lo que no sabe (saber QUE NO SABE)

Growth Index:
  G = (Depth * 0.15) + (Momentum * 0.25) + (Breadth * 0.20)
    + (Transfer * 0.20) + (Autonomy * 0.20)

Hooks de actualización:
  - on_recipe_executed()  → disparado por eidos_procedural.execute_recipe()
  - on_ser_feedback()     → disparado por episodios con feedback_score
  - on_test_result()      → disparado por smoke tests u otras baterías
  - on_context_application() → disparado cuando EIDOS aplica un concepto en
                                 un contexto nuevo

dG/dt: tasa de crecimiento diaria (diferencia entre G actual y G hace 24h).
"""
from __future__ import annotations

import json
import logging
import math
import os
import sqlite3
import threading
import time
from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Set, Tuple

from core.db import get_conn

log = logging.getLogger("eidos.growth")

# ── Constantes ─────────────────────────────────────────────────────────────────

BRAIN_DB = Path.home() / ".eidos" / "evolution_brain.db"
PROCEDURAL_DB = Path.home() / ".eidos" / "procedural_memory.db"

# Niveles de maestría (rangos de score 0.0-1.0)
MASTERY_LEVELS = {
    "NOVICE":      (0.00, 0.33),
    "APPRENTICE":  (0.33, 0.55),
    "JOURNEYMAN":  (0.55, 0.75),
    "EXPERT":      (0.75, 0.90),
    "MASTER":      (0.90, 1.00),
}

# Niveles en orden para comparación
MASTERY_LEVEL_ORDER = ["NOVICE", "APPRENTICE", "JOURNEYMAN", "EXPERT", "MASTER"]

# Pesos del Growth Index (suman 1.0)
WEIGHT_DEPTH    = 0.15
WEIGHT_MOMENTUM = 0.25
WEIGHT_BREADTH  = 0.20
WEIGHT_TRANSFER = 0.20
WEIGHT_AUTONOMY = 0.20

# Sub-dimensiones iniciales basadas en fuente del nodo
INITIAL_SCORES_BY_SOURCE = {
    "research":          {"declarative": 0.45, "procedural": 0.10, "applicational": 0.05, "metacognitive": 0.10},
    "man":               {"declarative": 0.50, "procedural": 0.10, "applicational": 0.05, "metacognitive": 0.10},
    "apt":               {"declarative": 0.45, "procedural": 0.10, "applicational": 0.05, "metacognitive": 0.10},
    "distilled":         {"declarative": 0.40, "procedural": 0.15, "applicational": 0.10, "metacognitive": 0.15},
    "skill_general":     {"declarative": 0.35, "procedural": 0.40, "applicational": 0.30, "metacognitive": 0.20},
    "auto_learner":      {"declarative": 0.35, "procedural": 0.20, "applicational": 0.10, "metacognitive": 0.10},
    "reasoned":          {"declarative": 0.25, "procedural": 0.05, "applicational": 0.05, "metacognitive": 0.05},
    "seed_core":         {"declarative": 0.40, "procedural": 0.50, "applicational": 0.25, "metacognitive": 0.35},
}

DEFAULT_INITIAL_SCORES = {"declarative": 0.20, "procedural": 0.05, "applicational": 0.05, "metacognitive": 0.05}

# Impulso de actualización (cuánto cambia cada sub-dimensión por evento)
BOOST_RECIPE_SUCCESS    = 0.08
BOOST_RECIPE_FAIL       = 0.02  # Aprender del fallo
BOOST_SER_FEEDBACK_POS  = 0.12
BOOST_SER_FEEDBACK_NEG  = 0.05  # Corrección también enseña
BOOST_TEST_PASS         = 0.06
BOOST_TEST_FAIL         = 0.03
BOOST_CONTEXT_NEW       = 0.10  # Aplicar en contexto nuevo da mucho
BOOST_PRACTICE_RECALL   = 0.07

# Snapshot interno: cada 6 horas
SNAPSHOT_INTERVAL = 21600

# Decaimiento por inactividad (por día sin práctica)
DECAY_PER_DAY = 0.005  # 0.5% por día — lento pero real


# ── Dataclasses ───────────────────────────────────────────────────────────────

@dataclass
class MasteryState:
    """Estado de maestría de un concepto."""
    node_id: str
    concept: str
    declarative_score: float = 0.0
    procedural_score: float = 0.0
    applicational_score: float = 0.0
    metacognitive_score: float = 0.0
    executions_success: int = 0
    executions_fail: int = 0
    unique_contexts: int = 0
    cross_domain_edges: int = 0
    last_practiced_at: float = 0.0
    review_count: int = 0
    updated_at: float = field(default_factory=time.time)

    @property
    def overall_score(self) -> float:
        """Score global: media de las 4 sub-dimensiones."""
        return (self.declarative_score + self.procedural_score
                + self.applicational_score + self.metacognitive_score) / 4.0

    @property
    def mastery_level(self) -> str:
        """Nivel de maestría textual."""
        return score_to_level(self.overall_score)

    @property
    def mastery_level_index(self) -> int:
        """Índice numérico del nivel (0=NOVICE, 4=MASTER)."""
        return MASTERY_LEVEL_ORDER.index(self.mastery_level)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "node_id": self.node_id,
            "concept": self.concept,
            "declarative_score": round(self.declarative_score, 4),
            "procedural_score": round(self.procedural_score, 4),
            "applicational_score": round(self.applicational_score, 4),
            "metacognitive_score": round(self.metacognitive_score, 4),
            "overall_score": round(self.overall_score, 4),
            "mastery_level": self.mastery_level,
            "mastery_level_index": self.mastery_level_index,
            "executions_success": self.executions_success,
            "executions_fail": self.executions_fail,
            "unique_contexts": self.unique_contexts,
            "cross_domain_edges": self.cross_domain_edges,
            "last_practiced_at": self.last_practiced_at,
            "review_count": self.review_count,
            "updated_at": self.updated_at,
        }


@dataclass
class GrowthSnapshot:
    """Una foto instantánea del Growth Index."""
    ts: float
    depth_score: float = 0.0
    momentum_score: float = 0.0
    breadth_score: float = 0.0
    transfer_score: float = 0.0
    autonomy_score: float = 0.0
    growth_index: float = 0.0
    dg_dt: float = 0.0
    concepts_novice: int = 0
    concepts_apprentice: int = 0
    concepts_journeyman: int = 0
    concepts_expert: int = 0
    concepts_master: int = 0
    total_concepts: int = 0

    def to_dict(self) -> Dict[str, Any]:
        return {
            "ts": self.ts,
            "depth_score": round(self.depth_score, 4),
            "momentum_score": round(self.momentum_score, 4),
            "breadth_score": round(self.breadth_score, 4),
            "transfer_score": round(self.transfer_score, 4),
            "autonomy_score": round(self.autonomy_score, 4),
            "growth_index": round(self.growth_index, 4),
            "dg_dt": round(self.dg_dt, 6),
            "level_distribution": {
                "NOVICE": self.concepts_novice,
                "APPRENTICE": self.concepts_apprentice,
                "JOURNEYMAN": self.concepts_journeyman,
                "EXPERT": self.concepts_expert,
                "MASTER": self.concepts_master,
            },
            "total_concepts": self.total_concepts,
        }


# ── Utilidades de nivel ───────────────────────────────────────────────────────

def score_to_level(score: float) -> str:
    """Convierte un score 0.0-1.0 a nombre de nivel."""
    for level, (lo, hi) in MASTERY_LEVELS.items():
        if lo <= score < hi:
            return level
    return "MASTER" if score >= 1.0 else "NOVICE"


def level_to_range(level: str) -> Tuple[float, float]:
    """Retorna el rango (lo, hi) para un nivel."""
    return MASTERY_LEVELS.get(level, (0.0, 0.33))


def level_index(level: str) -> int:
    """Índice numérico del nivel."""
    return MASTERY_LEVEL_ORDER.index(level) if level in MASTERY_LEVEL_ORDER else 0


# ── Esquema de la DB ──────────────────────────────────────────────────────────

_GROWTH_SCHEMA = """
CREATE TABLE IF NOT EXISTS concept_mastery (
    node_id TEXT PRIMARY KEY,
    concept TEXT NOT NULL,
    declarative_score REAL DEFAULT 0.0,
    procedural_score REAL DEFAULT 0.0,
    applicational_score REAL DEFAULT 0.0,
    metacognitive_score REAL DEFAULT 0.0,
    executions_success INTEGER DEFAULT 0,
    executions_fail INTEGER DEFAULT 0,
    unique_contexts INTEGER DEFAULT 0,
    cross_domain_edges INTEGER DEFAULT 0,
    last_practiced_at REAL,
    review_count INTEGER DEFAULT 0,
    updated_at REAL
);

CREATE TABLE IF NOT EXISTS mastery_events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ts REAL NOT NULL,
    node_id TEXT,
    event_type TEXT NOT NULL,
    source TEXT,
    detail TEXT,
    declarative_delta REAL DEFAULT 0.0,
    procedural_delta REAL DEFAULT 0.0,
    applicational_delta REAL DEFAULT 0.0,
    metacognitive_delta REAL DEFAULT 0.0,
    FOREIGN KEY (node_id) REFERENCES knowledge_nodes(id)
);

CREATE TABLE IF NOT EXISTS growth_snapshots (
    ts REAL PRIMARY KEY,
    depth_score REAL DEFAULT 0.0,
    momentum_score REAL DEFAULT 0.0,
    breadth_score REAL DEFAULT 0.0,
    transfer_score REAL DEFAULT 0.0,
    autonomy_score REAL DEFAULT 0.0,
    growth_index REAL DEFAULT 0.0,
    dg_dt REAL DEFAULT 0.0,
    concepts_novice INTEGER DEFAULT 0,
    concepts_apprentice INTEGER DEFAULT 0,
    concepts_journeyman INTEGER DEFAULT 0,
    concepts_expert INTEGER DEFAULT 0,
    concepts_master INTEGER DEFAULT 0,
    total_concepts INTEGER DEFAULT 0
);

CREATE INDEX IF NOT EXISTS idx_mastery_concept ON concept_mastery(concept);
CREATE INDEX IF NOT EXISTS idx_mastery_updated ON concept_mastery(updated_at);
CREATE INDEX IF NOT EXISTS idx_mastery_events_ts ON mastery_events(ts DESC);
CREATE INDEX IF NOT EXISTS idx_mastery_events_type ON mastery_events(event_type);
CREATE INDEX IF NOT EXISTS idx_growth_snapshots_ts ON growth_snapshots(ts DESC);
"""


def _ensure_schema():
    """Crea/actualiza las tablas de growth en evolution_brain.db."""
    try:
        conn = get_conn(BRAIN_DB, timeout=10)
        conn.executescript(_GROWTH_SCHEMA)
        conn.commit()
    except Exception as e:
        log.error("Error creando schema growth: %s", e)


_ensure_schema()


# ── GrowthEngine ──────────────────────────────────────────────────────────────

class GrowthEngine:
    """Motor de Growth Index. Mantiene el tracking de maestría por concepto
    y computa el Growth Index G con dG/dt."""

    def __init__(self):
        self._lock = threading.Lock()
        self._last_snapshot_ts: float = 0.0
        self._cached_snapshot: Optional[GrowthSnapshot] = None
        self._bootstrap_pending = True

    # ── Bootstrap ──────────────────────────────────────────────────────────

    def bootstrap_from_knowledge_graph(self, max_concepts: int = 5000) -> int:
        """Inicializa concept_mastery desde los conceptos existentes en knowledge_nodes.
        Solo hace bootstrap de conceptos que aún no tienen entrada en concept_mastery.
        Retorna cuántos conceptos nuevos se añadieron."""
        added = 0
        try:
            conn = get_conn(BRAIN_DB, timeout=10)
            conn.execute("PRAGMA journal_mode=WAL")

            # Conceptos que ya están en concept_mastery
            existing = set(
                row[0] for row in
                conn.execute("SELECT node_id FROM concept_mastery").fetchall()
            )

            # Nodos de calidad en knowledge_nodes
            rows = conn.execute("""
                SELECT id, concept, source, confidence, category
                FROM knowledge_nodes
                WHERE confidence >= 0.2
                  AND LENGTH(definition) >= 20
                  AND source NOT IN ('wordnet', 'code_analyzer')
                  AND category NOT IN ('dictionary', 'synset', 'code_structure',
                                       'eidos_function', 'eidos_class', 'eidos_module')
                ORDER BY confidence DESC
                LIMIT ?
            """, (max_concepts,)).fetchall()

            now = time.time()
            for row in rows:
                node_id, concept, source, confidence, category = row
                if node_id in existing:
                    continue

                # Determinar scores iniciales según fuente
                source_key = (source or "").lower()
                initials = INITIAL_SCORES_BY_SOURCE.get(source_key, None)
                if initials is None:
                    # Buscar por prefijo (ej: "research:duckduckgo" → "research")
                    for prefix in INITIAL_SCORES_BY_SOURCE:
                        if source_key.startswith(prefix):
                            initials = INITIAL_SCORES_BY_SOURCE[prefix]
                            break
                    if initials is None:
                        initials = dict(DEFAULT_INITIAL_SCORES)

                # Ajustar por confidence del nodo
                conf_factor = min(1.0, (confidence or 0.5))

                conn.execute("""
                    INSERT INTO concept_mastery
                    (node_id, concept, declarative_score, procedural_score,
                     applicational_score, metacognitive_score, updated_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?)
                """, (
                    node_id, concept,
                    round(initials["declarative"] * conf_factor, 4),
                    round(initials["procedural"] * conf_factor, 4),
                    round(initials["applicational"] * conf_factor, 4),
                    round(initials["metacognitive"] * conf_factor, 4),
                    now,
                ))
                added += 1

            conn.commit()
            log.info("Bootstrap: %d conceptos añadidos a concept_mastery", added)
            self._bootstrap_pending = False

            # Bootstrap tambien la cola de practica para los conceptos nuevos
            if added > 0:
                try:
                    from core.eidos_practice import get_practice_queue
                    pq = get_practice_queue()
                    pq_added = pq.bootstrap_queue(max_concepts=added)
                    log.info("Bootstrap: %d conceptos añadidos a practice_queue", pq_added)
                except Exception as pq_err:
                    log.warning("Bootstrap practice_queue fallo (no critico): %s", pq_err)

        except Exception as e:
            log.error("Bootstrap falló: %s", e)
        return added

    # ── CRUD de maestría ────────────────────────────────────────────────────

    def get_mastery(self, concept_or_id: str) -> Optional[MasteryState]:
        """Obtiene el estado de maestría de un concepto (por nombre o node_id)."""
        try:
            conn = get_conn(BRAIN_DB, timeout=5, read_only=True)
            row = conn.execute("""
                SELECT node_id, concept, declarative_score, procedural_score,
                       applicational_score, metacognitive_score, executions_success,
                       executions_fail, unique_contexts, cross_domain_edges,
                       last_practiced_at, review_count,
                       COALESCE(updated_at, 0)
                FROM concept_mastery
                WHERE node_id = ? OR concept = ?
            """, (concept_or_id, concept_or_id)).fetchone()

            if not row:
                return None

            return MasteryState(
                node_id=row[0], concept=row[1],
                declarative_score=row[2], procedural_score=row[3],
                applicational_score=row[4], metacognitive_score=row[5],
                executions_success=row[6], executions_fail=row[7],
                unique_contexts=row[8], cross_domain_edges=row[9],
                last_practiced_at=row[10] or 0.0,
                review_count=row[11],
                updated_at=row[12],
            )
        except Exception as e:
            log.error("get_mastery(%s): %s", concept_or_id, e)
            return None

    def _resolve_node(self, concept_or_id: str) -> Optional[Tuple[str, str, str]]:
        """Resuelve un concepto a (node_id, concept, category) desde knowledge_nodes."""
        try:
            conn = get_conn(BRAIN_DB, timeout=5, read_only=True)
            row = conn.execute(
                "SELECT id, concept, category FROM knowledge_nodes WHERE id = ? OR concept = ?",
                (concept_or_id, concept_or_id)
            ).fetchone()
            if row:
                return (row[0], row[1], row[2] or "")
            return None
        except Exception:
            return None

    def update_mastery(self, concept_or_id: str,
                       declarative_delta: float = 0.0,
                       procedural_delta: float = 0.0,
                       applicational_delta: float = 0.0,
                       metacognitive_delta: float = 0.0,
                       event_type: str = "manual",
                       source: str = "",
                       detail: str = "") -> Optional[MasteryState]:
        """Actualiza los scores de maestría de un concepto.

        Clampea cada sub-dimensión a [0.0, 1.0] y aplica decaimiento por
        inactividad si el concepto no se ha practicado recientemente.

        Retorna el nuevo MasteryState o None si el concepto no existe.
        """
        resolved = self._resolve_node(concept_or_id)
        if not resolved:
            log.debug("update_mastery: concepto '%s' no existe en knowledge_nodes",
                      concept_or_id)
            return None

        node_id, concept, category = resolved
        now = time.time()

        with self._lock:
            try:
                conn = get_conn(BRAIN_DB, timeout=10)
                conn.execute("PRAGMA journal_mode=WAL")

                # Asegurar que existe en concept_mastery
                existing = conn.execute(
                    "SELECT * FROM concept_mastery WHERE node_id = ?", (node_id,)
                ).fetchone()

                if not existing:
                    initials = dict(DEFAULT_INITIAL_SCORES)
                    conn.execute("""
                        INSERT INTO concept_mastery
                        (node_id, concept, declarative_score, procedural_score,
                         applicational_score, metacognitive_score, updated_at)
                        VALUES (?, ?, ?, ?, ?, ?, ?)
                    """, (node_id, concept,
                          initials["declarative"], initials["procedural"],
                          initials["applicational"], initials["metacognitive"], now))
                    current = {
                        "declarative": initials["declarative"],
                        "procedural": initials["procedural"],
                        "applicational": initials["applicational"],
                        "metacognitive": initials["metacognitive"],
                        "exec_success": 0,
                        "exec_fail": 0,
                    }
                else:
                    current = {
                        "declarative": existing[2],
                        "procedural": existing[3],
                        "applicational": existing[4],
                        "metacognitive": existing[5],
                        "exec_success": existing[6],
                        "exec_fail": existing[7],
                    }

                # Aplicar deltas, clampeando a [0.0, 1.0]
                new_d = min(1.0, max(0.0, current["declarative"] + declarative_delta))
                new_p = min(1.0, max(0.0, current["procedural"] + procedural_delta))
                new_a = min(1.0, max(0.0, current["applicational"] + applicational_delta))
                new_m = min(1.0, max(0.0, current["metacognitive"] + metacognitive_delta))

                # Actualizar
                conn.execute("""
                    UPDATE concept_mastery
                    SET declarative_score = ?, procedural_score = ?,
                        applicational_score = ?, metacognitive_score = ?,
                        last_practiced_at = ?, review_count = review_count + 1,
                        updated_at = ?
                    WHERE node_id = ?
                """, (new_d, new_p, new_a, new_m, now, now, node_id))

                # Registrar evento
                conn.execute("""
                    INSERT INTO mastery_events
                    (ts, node_id, event_type, source, detail,
                     declarative_delta, procedural_delta,
                     applicational_delta, metacognitive_delta)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """, (now, node_id, event_type, source, detail[:500],
                      round(declarative_delta, 4), round(procedural_delta, 4),
                      round(applicational_delta, 4), round(metacognitive_delta, 4)))

                conn.commit()

                return MasteryState(
                    node_id=node_id, concept=concept,
                    declarative_score=new_d,
                    procedural_score=new_p,
                    applicational_score=new_a,
                    metacognitive_score=new_m,
                    executions_success=current["exec_success"],
                    executions_fail=current["exec_fail"],
                    last_practiced_at=now,
                    review_count=(existing[11] if existing else 0) + 1,
                    updated_at=now,
                )

            except Exception as e:
                log.error("update_mastery(%s): %s", concept_or_id, e)
                return None

    # ── Hooks de actualización ──────────────────────────────────────────────

    def on_recipe_executed(self, recipe_name: str, success: bool,
                           tags: Optional[List[str]] = None,
                           trigger_words: Optional[List[str]] = None) -> int:
        """Hook disparado cuando una receta se ejecuta (éxito o fallo).

        Busca conceptos relacionados con la receta en knowledge_nodes y
        actualiza sus scores procedurales (y applicational si es éxito).

        Args:
            recipe_name: Nombre de la receta
            success: True si la receta se completó exitosamente
            tags: Tags de la receta (para matching de conceptos)
            trigger_words: Palabras trigger de la receta

        Returns: Número de conceptos actualizados.
        """
        # Extraer keywords de la receta
        keywords = set()
        name_words = recipe_name.lower().split()
        keywords.update(w for w in name_words if len(w) >= 3)
        if tags:
            keywords.update(t.lower() for t in tags if len(t) >= 3)
        if trigger_words:
            for tw in trigger_words:
                keywords.update(w for w in tw.lower().split() if len(w) >= 3)

        if not keywords:
            return 0

        boost = BOOST_RECIPE_SUCCESS if success else BOOST_RECIPE_FAIL
        updated = 0

        try:
            conn = get_conn(BRAIN_DB, timeout=10, read_only=True)
            for kw in keywords:
                # Buscar conceptos que contengan la keyword
                rows = conn.execute(
                    "SELECT id FROM knowledge_nodes WHERE concept LIKE ? LIMIT 5",
                    (f"%{kw}%",)
                ).fetchall()
                for (node_id,) in rows:
                    result = self.update_mastery(
                        node_id,
                        procedural_delta=boost,
                        applicational_delta=boost * 0.5 if success else 0.0,
                        event_type="recipe_success" if success else "recipe_fail",
                        source=recipe_name[:200],
                    )
                    if result:
                        # Actualizar contador de ejecuciones
                        conn2 = get_conn(BRAIN_DB, timeout=5)
                        field = "executions_success" if success else "executions_fail"
                        conn2.execute(
                            f"UPDATE concept_mastery SET {field} = {field} + 1 "
                            "WHERE node_id = ?", (node_id,)
                        )
                        conn2.commit()
                        updated += 1

                        # Tambien registrar en la cola de practica
                        try:
                            from core.eidos_practice import get_practice_queue
                            pq = get_practice_queue()
                            pq.schedule_concept(
                                concept=result.concept,
                                node_id=node_id,
                                mastery_level=result.mastery_level,
                            )
                        except Exception as pq_err:
                            log.debug("schedule_concept fallo: %s", pq_err)

            log.debug("on_recipe_executed(%s, success=%s): %d conceptos actualizados",
                      recipe_name[:40], success, updated)
        except Exception as e:
            log.error("on_recipe_executed error: %s", e)

        return updated

    def on_ser_feedback(self, concept: str, feedback_score: float,
                        context: str = "") -> bool:
        """Hook disparado cuando SER da feedback sobre una respuesta de EIDOS.

        El feedback_score de episodic_memory (0.0-1.0) se usa para ajustar
        la sub-dimensión metacognitive (EIDOS aprende qué tan bien está
        respondiendo).

        Args:
            concept: Concepto sobre el que SER dio feedback
            feedback_score: Score de feedback (0.0 = muy mal, 1.0 = perfecto)
            context: Contexto de la interacción

        Returns: True si se actualizó algún concepto.
        """
        if feedback_score < 0.0 or feedback_score > 1.0:
            return False

        # Mapear feedback a deltas
        if feedback_score >= 0.8:
            # Feedback muy positivo → boost metacognitive + declarative
            result = self.update_mastery(
                concept,
                declarative_delta=BOOST_SER_FEEDBACK_POS * 0.5,
                metacognitive_delta=BOOST_SER_FEEDBACK_POS,
                event_type="ser_feedback_positive",
                source="ser",
                detail=context[:500],
            )
        elif feedback_score >= 0.5:
            # Feedback neutro → pequeño boost metacognitive
            result = self.update_mastery(
                concept,
                metacognitive_delta=BOOST_SER_FEEDBACK_POS * 0.3,
                event_type="ser_feedback_neutral",
                source="ser",
                detail=context[:500],
            )
        else:
            # Feedback negativo → EIDOS aprende que no sabe bien esto
            result = self.update_mastery(
                concept,
                metacognitive_delta=BOOST_SER_FEEDBACK_NEG,
                event_type="ser_feedback_negative",
                source="ser",
                detail=context[:500],
            )

        return result is not None

    def on_test_result(self, concept: str, passed: bool,
                       test_name: str = "") -> bool:
        """Hook disparado cuando un test (smoke, integración, etc.) pasa o falla.

        Un test pass boostea todas las sub-dimensiones moderadamente.
        Un test fail boostea metacognitive (aprender del fallo).

        Args:
            concept: Concepto evaluado
            passed: True si el test pasó
            test_name: Nombre del test

        Returns: True si se actualizó el concepto.
        """
        if passed:
            result = self.update_mastery(
                concept,
                declarative_delta=BOOST_TEST_PASS * 0.5,
                procedural_delta=BOOST_TEST_PASS,
                applicational_delta=BOOST_TEST_PASS * 0.5,
                metacognitive_delta=BOOST_TEST_PASS * 0.3,
                event_type="test_pass",
                source=test_name[:200],
            )
        else:
            result = self.update_mastery(
                concept,
                procedural_delta=BOOST_TEST_FAIL * 0.3,
                metacognitive_delta=BOOST_TEST_FAIL,
                event_type="test_fail",
                source=test_name[:200],
            )
        return result is not None

    def on_context_application(self, concept: str, context_label: str) -> bool:
        """Hook disparado cuando EIDOS aplica un concepto en un contexto NUEVO.

        Este es el hook más valioso: transferencia de conocimiento a un
        contexto nuevo boostea significativamente la sub-dimensión applicational.

        Args:
            concept: Concepto aplicado
            context_label: Etiqueta del contexto (ej: "nmap en Windows", "docker en Mac")

        Returns: True si se actualizó.
        """
        # Verificar si el contexto es realmente nuevo para este concepto
        try:
            conn = get_conn(BRAIN_DB, timeout=5, read_only=True)
            existing = conn.execute(
                "SELECT source FROM mastery_events WHERE node_id = "
                "(SELECT id FROM knowledge_nodes WHERE concept = ?) "
                "AND event_type = 'context_application' AND source = ?",
                (concept, context_label[:200])
            ).fetchone()
            if existing:
                # Contexto ya aplicado antes — boost menor
                boost = BOOST_CONTEXT_NEW * 0.2
            else:
                boost = BOOST_CONTEXT_NEW
                # Incrementar contador de contextos únicos
                try:
                    resolved = self._resolve_node(concept)
                    if resolved:
                        conn2 = get_conn(BRAIN_DB, timeout=5)
                        conn2.execute(
                            "UPDATE concept_mastery SET unique_contexts = unique_contexts + 1 "
                            "WHERE node_id = ?", (resolved[0],)
                        )
                        conn2.commit()
                except Exception:
                    pass
        except Exception:
            boost = BOOST_CONTEXT_NEW * 0.5

        result = self.update_mastery(
            concept,
            applicational_delta=boost,
            procedural_delta=boost * 0.3,
            declarative_delta=boost * 0.2,
            event_type="context_application",
            source=context_label[:200],
        )
        return result is not None

    # ── Growth Index computation ────────────────────────────────────────────

    def compute_growth_index(self) -> GrowthSnapshot:
        """Calcula el Growth Index G completo con todos sus componentes.

        G = (Depth*0.15) + (Momentum*0.25) + (Breadth*0.20)
          + (Transfer*0.20) + (Autonomy*0.20)
        """
        now = time.time()
        snapshot = GrowthSnapshot(ts=now)

        try:
            conn = get_conn(BRAIN_DB, timeout=10, read_only=True)

            # ── Depth: maestría media de todos los conceptos ────────────────
            row = conn.execute("""
                SELECT COUNT(*),
                       COALESCE(AVG(declarative_score), 0),
                       COALESCE(AVG(procedural_score), 0),
                       COALESCE(AVG(applicational_score), 0),
                       COALESCE(AVG(metacognitive_score), 0)
                FROM concept_mastery
            """).fetchone()

            count, avg_d, avg_p, avg_a, avg_m = row
            if count > 0:
                avg_overall = (avg_d + avg_p + avg_a + avg_m) / 4.0
                snapshot.depth_score = avg_overall
            else:
                snapshot.depth_score = 0.0

            # ── Momentum: tasa de cambio del Depth en 7 dias + practice ────
            week_ago = now - 7 * 86400
            old_row = conn.execute(
                "SELECT depth_score FROM growth_snapshots "
                "WHERE ts <= ? ORDER BY ts DESC LIMIT 1",
                (week_ago,)
            ).fetchone()

            if old_row and old_row[0] > 0:
                old_depth = old_row[0]
                delta = snapshot.depth_score - old_depth
                # Normalizar: delta positivo → momentum alto
                snapshot.momentum_score = min(1.0, max(0.0, 0.5 + delta * 5.0))
            else:
                # Sin historial, asumir momentum moderado
                snapshot.momentum_score = 0.3

            # Blend in practice momentum from PracticeQueue
            try:
                from core.eidos_practice import get_practice_queue
                pq = get_practice_queue()
                pq_stats = pq.get_queue_stats()
                if "error" not in pq_stats:
                    sessions_today = pq_stats.get("sessions_today", 0)
                    avg_recall = pq_stats.get("avg_recall_score", 0.0)
                    due_ratio = pq_stats.get("due_ratio", 0.0)
                    # 10+ sessions/day = full practice momentum
                    practice_momentum = min(1.0, sessions_today / 10.0) * 0.6
                    practice_momentum += avg_recall * 0.3
                    practice_momentum += (1.0 - due_ratio) * 0.1
                    # Blend: 60% growth momentum, 40% practice momentum
                    snapshot.momentum_score = (
                        snapshot.momentum_score * 0.6 + practice_momentum * 0.4
                    )
            except Exception as pq_err:
                log.debug("Practice momentum no disponible: %s", pq_err)

            # ── Breadth: % de categorías con al menos 1 concepto ≥ APPRENTICE ─
            categories_row = conn.execute("""
                SELECT COUNT(DISTINCT kn.category) as total_cats
                FROM knowledge_nodes kn
                WHERE kn.category NOT IN ('dictionary', 'synset', 'code_structure',
                                           'eidos_function', 'eidos_class')
            """).fetchone()
            total_cats = categories_row[0] if categories_row else 1

            filled_cats = conn.execute("""
                SELECT COUNT(DISTINCT kn.category)
                FROM concept_mastery cm
                JOIN knowledge_nodes kn ON cm.node_id = kn.id
                WHERE (cm.declarative_score + cm.procedural_score
                       + cm.applicational_score + cm.metacognitive_score) / 4.0 >= 0.33
                  AND kn.category NOT IN ('dictionary', 'synset', 'code_structure')
            """).fetchone()[0]

            snapshot.breadth_score = min(1.0, filled_cats / max(total_cats, 1))

            # ── Transfer: cross-domain edges por concepto ───────────────────
            transfer_row = conn.execute("""
                SELECT COALESCE(AVG(cm.cross_domain_edges), 0),
                       COUNT(*)
                FROM concept_mastery cm
                WHERE cm.cross_domain_edges > 0
            """).fetchone()
            if transfer_row[1] > 0:
                avg_cross = transfer_row[0]
                # Normalizar: 10+ cross-domain edges = transferencia completa
                snapshot.transfer_score = min(1.0, avg_cross / 10.0)
            else:
                snapshot.transfer_score = 0.0

            # ── Autonomy: ratio de ejecuciones autónomas exitosas ────────────
            # Usamos procedural_memory + mastery_events
            try:
                proc_row = conn.execute("""
                    SELECT COALESCE(SUM(executions_success), 0),
                           COALESCE(SUM(executions_fail), 0)
                    FROM concept_mastery
                """).fetchone()
                total_executions = proc_row[0] + proc_row[1]
                if total_executions > 0:
                    success_rate = proc_row[0] / total_executions
                    # Leer también de procedural_memory si existe
                    try:
                        proc_conn = get_conn(PROCEDURAL_DB, timeout=5, read_only=True)
                        proc_counts = proc_conn.execute(
                            "SELECT COALESCE(SUM(success_count), 0), "
                            "COALESCE(SUM(fail_count), 0) FROM recipes"
                        ).fetchone()
                        if proc_counts:
                            total_p = proc_counts[0] + proc_counts[1]
                            if total_p > 0:
                                proc_rate = proc_counts[0] / total_p
                                # Combinar tasas (70% mastery_events, 30% procedural)
                                success_rate = success_rate * 0.7 + proc_rate * 0.3
                    except Exception:
                        pass
                    snapshot.autonomy_score = success_rate
                else:
                    snapshot.autonomy_score = 0.1  # Mínimo por existir
            except Exception:
                snapshot.autonomy_score = 0.1

            # ── Computar G ──────────────────────────────────────────────────
            snapshot.growth_index = (
                snapshot.depth_score    * WEIGHT_DEPTH +
                snapshot.momentum_score * WEIGHT_MOMENTUM +
                snapshot.breadth_score  * WEIGHT_BREADTH +
                snapshot.transfer_score * WEIGHT_TRANSFER +
                snapshot.autonomy_score * WEIGHT_AUTONOMY
            )

            # ── dG/dt: diferencia con el snapshot de hace 24h ────────────────
            day_ago = now - 86400
            prev = conn.execute(
                "SELECT growth_index, ts FROM growth_snapshots "
                "WHERE ts <= ? ORDER BY ts DESC LIMIT 1",
                (day_ago,)
            ).fetchone()

            if prev and prev[1] > 0:
                dt_days = max(0.01, (now - prev[1]) / 86400.0)
                snapshot.dg_dt = (snapshot.growth_index - prev[0]) / dt_days
            else:
                snapshot.dg_dt = 0.0

            # ── Distribución de niveles ─────────────────────────────────────
            for level_name, (lo, hi) in MASTERY_LEVELS.items():
                count_level = conn.execute("""
                    SELECT COUNT(*) FROM concept_mastery
                    WHERE (declarative_score + procedural_score
                           + applicational_score + metacognitive_score) / 4.0 >= ?
                      AND (declarative_score + procedural_score
                           + applicational_score + metacognitive_score) / 4.0 < ?
                """, (lo, hi if hi < 1.0 else 2.0)).fetchone()[0]

                if level_name == "NOVICE":
                    snapshot.concepts_novice = count_level
                elif level_name == "APPRENTICE":
                    snapshot.concepts_apprentice = count_level
                elif level_name == "JOURNEYMAN":
                    snapshot.concepts_journeyman = count_level
                elif level_name == "EXPERT":
                    snapshot.concepts_expert = count_level
                elif level_name == "MASTER":
                    snapshot.concepts_master = count_level

            snapshot.total_concepts = count

        except Exception as e:
            log.error("compute_growth_index error: %s", e)

        self._cached_snapshot = snapshot
        return snapshot

    def snapshot_growth(self, force: bool = False) -> Optional[GrowthSnapshot]:
        """Toma un snapshot del Growth Index y lo persiste.

        Solo toma snapshot si han pasado al menos SNAPSHOT_INTERVAL segundos
        desde el último, a menos que force=True.

        Returns: GrowthSnapshot persistido o None si no se tomó.
        """
        now = time.time()
        if not force and (now - self._last_snapshot_ts) < SNAPSHOT_INTERVAL:
            return None

        snapshot = self.compute_growth_index()

        try:
            conn = get_conn(BRAIN_DB, timeout=10)
            conn.execute("PRAGMA journal_mode=WAL")
            conn.execute("""
                INSERT OR REPLACE INTO growth_snapshots
                (ts, depth_score, momentum_score, breadth_score, transfer_score,
                 autonomy_score, growth_index, dg_dt,
                 concepts_novice, concepts_apprentice, concepts_journeyman,
                 concepts_expert, concepts_master, total_concepts)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                snapshot.ts,
                snapshot.depth_score, snapshot.momentum_score,
                snapshot.breadth_score, snapshot.transfer_score,
                snapshot.autonomy_score, snapshot.growth_index,
                snapshot.dg_dt,
                snapshot.concepts_novice, snapshot.concepts_apprentice,
                snapshot.concepts_journeyman, snapshot.concepts_expert,
                snapshot.concepts_master, snapshot.total_concepts,
            ))
            conn.commit()
            self._last_snapshot_ts = now
            log.info("Growth snapshot: G=%.4f, dG/dt=%.6f, %d conceptos",
                     snapshot.growth_index, snapshot.dg_dt, snapshot.total_concepts)
        except Exception as e:
            log.error("snapshot_growth persist error: %s", e)

        return snapshot

    # ── Decaimiento ─────────────────────────────────────────────────────────

    def apply_decay(self) -> int:
        """Aplica decaimiento a conceptos no practicados recientemente.

        Por cada día sin práctica, el score decae DECAY_PER_DAY (0.5%).
        Esto evita que conceptos abandonados mantengan scores altos.

        Returns: Número de conceptos afectados.
        """
        now = time.time()
        affected = 0
        try:
            conn = get_conn(BRAIN_DB, timeout=10)
            conn.execute("PRAGMA journal_mode=WAL")
            rows = conn.execute("""
                SELECT node_id, last_practiced_at, declarative_score,
                       procedural_score, applicational_score, metacognitive_score
                FROM concept_mastery
                WHERE last_practiced_at IS NOT NULL
                  AND last_practiced_at > 0
            """).fetchall()

            for row in rows:
                node_id, last_prac, d, p, a, m = row
                if not last_prac:
                    continue
                days_inactive = (now - last_prac) / 86400.0
                if days_inactive < 1.0:
                    continue

                # Decaimiento proporcional a días inactivos
                decay_factor = 1.0 - min(0.5, DECAY_PER_DAY * days_inactive)
                new_d = max(0.0, d * decay_factor)
                new_p = max(0.0, p * decay_factor)
                new_a = max(0.0, a * decay_factor)
                new_m = max(0.0, m * decay_factor)

                if (new_d, new_p, new_a, new_m) != (d, p, a, m):
                    conn.execute("""
                        UPDATE concept_mastery
                        SET declarative_score = ?, procedural_score = ?,
                            applicational_score = ?, metacognitive_score = ?,
                            updated_at = ?
                        WHERE node_id = ?
                    """, (new_d, new_p, new_a, new_m, now, node_id))
                    affected += 1

            conn.commit()
            if affected > 0:
                log.info("Decay: %d conceptos decaídos", affected)
        except Exception as e:
            log.error("apply_decay error: %s", e)
        return affected

    # ── Recalcular cross-domain edges ───────────────────────────────────────

    def recalculate_cross_domain_edges(self) -> int:
        """Recalcula los cross_domain_edges para todos los conceptos.

        Una arista es cross-domain si conecta dos nodos con categorías distintas
        (las primeras 2 partes separadas por ':' o '_').
        """
        updated = 0
        try:
            conn = get_conn(BRAIN_DB, timeout=10)
            conn.execute("PRAGMA journal_mode=WAL")

            # Obtener categoría de cada node_id en concept_mastery
            cat_map = {}
            rows = conn.execute("""
                SELECT cm.node_id, kn.category
                FROM concept_mastery cm
                JOIN knowledge_nodes kn ON cm.node_id = kn.id
            """).fetchall()
            for node_id, cat in rows:
                cat_map[node_id] = (cat or "").split(":")[0].split("_")[0].lower()

            # Para cada concepto, contar aristas a conceptos de otra categoría
            for node_id, cat in cat_map.items():
                cross = 0
                edge_rows = conn.execute("""
                    SELECT ke.from_node, ke.to_node
                    FROM knowledge_edges ke
                    WHERE (ke.from_node = ? OR ke.to_node = ?)
                      AND ke.strength >= 0.3
                """, (node_id, node_id)).fetchall()

                for from_n, to_n in edge_rows:
                    other_id = to_n if from_n == node_id else from_n
                    other_cat = cat_map.get(other_id, "")
                    if other_cat and other_cat != cat:
                        cross += 1

                conn.execute(
                    "UPDATE concept_mastery SET cross_domain_edges = ?, updated_at = ? "
                    "WHERE node_id = ?",
                    (cross, time.time(), node_id)
                )
                updated += 1

            conn.commit()
            log.info("Cross-domain edges recalculados para %d conceptos", updated)
        except Exception as e:
            log.error("recalculate_cross_domain_edges error: %s", e)
        return updated

    # ── Queries ─────────────────────────────────────────────────────────────

    def get_all_mastery(self, limit: int = 100,
                        min_level: Optional[str] = None) -> List[MasteryState]:
        """Obtiene lista de estados de maestría, opcionalmente filtrada por nivel."""
        try:
            conn = get_conn(BRAIN_DB, timeout=5, read_only=True)
            if min_level:
                lo, _ = MASTERY_LEVELS.get(min_level, (0.0, 0.33))
                rows = conn.execute("""
                    SELECT * FROM concept_mastery
                    WHERE (declarative_score + procedural_score
                           + applicational_score + metacognitive_score) / 4.0 >= ?
                    ORDER BY updated_at DESC LIMIT ?
                """, (lo, limit)).fetchall()
            else:
                rows = conn.execute(
                    "SELECT * FROM concept_mastery ORDER BY updated_at DESC LIMIT ?",
                    (limit,)
                ).fetchall()

            results = []
            for row in rows:
                results.append(MasteryState(
                    node_id=row[0], concept=row[1],
                    declarative_score=row[2], procedural_score=row[3],
                    applicational_score=row[4], metacognitive_score=row[5],
                    executions_success=row[6], executions_fail=row[7],
                    unique_contexts=row[8], cross_domain_edges=row[9],
                    last_practiced_at=row[10] or 0.0,
                    review_count=row[11],
                    updated_at=row[12] or 0.0,
                ))
            return results
        except Exception as e:
            log.error("get_all_mastery error: %s", e)
            return []

    def get_latest_snapshot(self) -> Optional[GrowthSnapshot]:
        """Obtiene el snapshot de growth más reciente."""
        if self._cached_snapshot:
            return self._cached_snapshot
        try:
            conn = get_conn(BRAIN_DB, timeout=5, read_only=True)
            row = conn.execute(
                "SELECT * FROM growth_snapshots ORDER BY ts DESC LIMIT 1"
            ).fetchone()
            if row:
                snapshot = GrowthSnapshot(
                    ts=row[0],
                    depth_score=row[1], momentum_score=row[2],
                    breadth_score=row[3], transfer_score=row[4],
                    autonomy_score=row[5], growth_index=row[6],
                    dg_dt=row[7],
                    concepts_novice=row[8], concepts_apprentice=row[9],
                    concepts_journeyman=row[10], concepts_expert=row[11],
                    concepts_master=row[12], total_concepts=row[13],
                )
                self._cached_snapshot = snapshot
                return snapshot
        except Exception as e:
            log.error("get_latest_snapshot error: %s", e)
        return None

    def get_growth_history(self, days: int = 30) -> List[Dict[str, Any]]:
        """Obtiene historial de growth de los últimos N días."""
        try:
            conn = get_conn(BRAIN_DB, timeout=5, read_only=True)
            cutoff = time.time() - days * 86400
            rows = conn.execute(
                "SELECT * FROM growth_snapshots WHERE ts >= ? ORDER BY ts ASC",
                (cutoff,)
            ).fetchall()
            return [GrowthSnapshot(
                ts=r[0], depth_score=r[1], momentum_score=r[2],
                breadth_score=r[3], transfer_score=r[4],
                autonomy_score=r[5], growth_index=r[6], dg_dt=r[7],
                concepts_novice=r[8], concepts_apprentice=r[9],
                concepts_journeyman=r[10], concepts_expert=r[11],
                concepts_master=r[12], total_concepts=r[13],
            ).to_dict() for r in rows]
        except Exception as e:
            log.error("get_growth_history error: %s", e)
            return []

    def get_stats(self) -> Dict[str, Any]:
        """Estadísticas resumidas del sistema de growth."""
        snapshot = self.get_latest_snapshot()
        if not snapshot:
            snapshot = self.compute_growth_index()

        level_counts = {
            "NOVICE": snapshot.concepts_novice,
            "APPRENTICE": snapshot.concepts_apprentice,
            "JOURNEYMAN": snapshot.concepts_journeyman,
            "EXPERT": snapshot.concepts_expert,
            "MASTER": snapshot.concepts_master,
        }

        return {
            "growth_index": round(snapshot.growth_index, 4),
            "dg_dt": round(snapshot.dg_dt, 6),
            "components": {
                "depth": round(snapshot.depth_score, 4),
                "momentum": round(snapshot.momentum_score, 4),
                "breadth": round(snapshot.breadth_score, 4),
                "transfer": round(snapshot.transfer_score, 4),
                "autonomy": round(snapshot.autonomy_score, 4),
            },
            "level_distribution": level_counts,
            "total_concepts": snapshot.total_concepts,
        }


    # ── Unified system access ────────────────────────────────────────────────

    def get_skill_tree(self):
        """Obtiene el SkillTreeBuilder vinculado a este GrowthEngine.

        El arbol de habilidades usa los datos de maestria de GrowthEngine
        como fuente autoritativa de scores."""
        from core.eidos_skill_tree import get_skill_tree
        return get_skill_tree()

    def get_practice_queue(self):
        """Obtiene la PracticeQueue vinculada a este GrowthEngine.

        La cola de practica se sincroniza con los niveles de maestria
        gestionados por GrowthEngine."""
        from core.eidos_practice import get_practice_queue
        return get_practice_queue()

# ── Singleton ─────────────────────────────────────────────────────────────────

_instance: Optional[GrowthEngine] = None
_lock = threading.Lock()


def get_growth_engine() -> GrowthEngine:
    """Obtiene la instancia singleton del GrowthEngine."""
    global _instance
    if _instance is None:
        with _lock:
            if _instance is None:
                _instance = GrowthEngine()
                log.info("GrowthEngine inicializado")
    return _instance


# ── Hooks públicos (para ser llamados desde otros módulos) ────────────────────

def on_recipe_executed(recipe_name: str, success: bool,
                       tags: Optional[List[str]] = None,
                       trigger_words: Optional[List[str]] = None) -> int:
    """Hook público: llamado desde eidos_procedural tras ejecutar una receta."""
    return get_growth_engine().on_recipe_executed(
        recipe_name, success, tags, trigger_words
    )


def on_ser_feedback(concept: str, feedback_score: float,
                    context: str = "") -> bool:
    """Hook público: llamado desde episodic_memory cuando SER da feedback."""
    return get_growth_engine().on_ser_feedback(concept, feedback_score, context)


def on_test_result(concept: str, passed: bool, test_name: str = "") -> bool:
    """Hook público: llamado desde smoke tests u otras baterías."""
    return get_growth_engine().on_test_result(concept, passed, test_name)


def on_context_application(concept: str, context_label: str) -> bool:
    """Hook público: llamado cuando EIDOS aplica un concepto en contexto nuevo."""
    return get_growth_engine().on_context_application(concept, context_label)


# ── CLI rápido ────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    import sys

    engine = get_growth_engine()

    if len(sys.argv) < 2:
        print("Uso: python eidos_growth.py <cmd> [args]")
        print("  bootstrap     — inicializar concept_mastery desde knowledge_nodes")
        print("  snapshot      — tomar snapshot de growth ahora")
        print("  stats         — mostrar estadísticas de growth")
        print("  history [días]— historial de growth")
        print("  list [nivel]  — listar conceptos por nivel de maestría")
        print("  decay         — aplicar decaimiento")
        print("  recalc-edges  — recalcular cross-domain edges")
        sys.exit(1)

    cmd = sys.argv[1]

    if cmd == "bootstrap":
        n = engine.bootstrap_from_knowledge_graph()
        print(f"Bootstrap: {n} conceptos añadidos")

    elif cmd == "snapshot":
        snap = engine.snapshot_growth(force=True)
        if snap:
            print(json.dumps(snap.to_dict(), indent=2, ensure_ascii=False))

    elif cmd == "stats":
        stats = engine.get_stats()
        print(json.dumps(stats, indent=2, ensure_ascii=False))

    elif cmd == "history":
        days = int(sys.argv[2]) if len(sys.argv) > 2 else 30
        history = engine.get_growth_history(days)
        print(f"Últimos {days} días ({len(history)} snapshots):")
        for h in history:
            print(f"  G={h['growth_index']:.4f}  dG/dt={h['dg_dt']:.6f}  "
                  f"ts={time.strftime('%Y-%m-%d %H:%M', time.localtime(h['ts']))}")

    elif cmd == "list":
        level = sys.argv[2] if len(sys.argv) > 2 else None
        concepts = engine.get_all_mastery(limit=50, min_level=level)
        for ms in concepts:
            print(f"  [{ms.mastery_level}] {ms.concept[:50]}: "
                  f"O={ms.overall_score:.3f} "
                  f"(D={ms.declarative_score:.2f} P={ms.procedural_score:.2f} "
                  f"A={ms.applicational_score:.2f} M={ms.metacognitive_score:.2f})")

    elif cmd == "decay":
        affected = engine.apply_decay()
        print(f"Decaimiento aplicado: {affected} conceptos afectados")

    elif cmd == "recalc-edges":
        updated = engine.recalculate_cross_domain_edges()
        print(f"Cross-domain edges recalculados: {updated} conceptos")

    else:
        print(f"Comando desconocido: {cmd}")
