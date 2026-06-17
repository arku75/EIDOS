#!/usr/bin/env python3
"""
core/relation_engine.py — Semantic Relation Engine for EIDOS
=============================================================

HALLAZGO CRÍTICO #1 del análisis de TASK.md: sin tipos semánticos
(IS_A, PART_OF, CAUSES, USES, etc.), el grafo no RAZONA — solo
recupera keywords.

Este módulo proporciona:

  1. infer_transitive()    — Cierra cadenas A→B + B→C ⇒ A→C para
                             relaciones transitivas (IS_A, PART_OF,
                             BEFORE, AFTER).

  2. infer_symmetric()     — Completa aristas inversas para relaciones
                             simétricas (SIMILAR_TO).

  3. find_contradictions() — Detecta A IS_A B y A IS_A C donde B≠C
                             sin relación jerárquica entre B y C.

  4. suggest_relations()   — Extrae nuevas relaciones desde texto en
                             definiciones usando patrones regex
                             ("X is a Y", "X causes Y", etc.).

  5. enrich_graph()        — Orquesta todo: clasifica aristas sin tipo
                             canónico, ejecuta inferencias, detecta
                             contradicciones.

Se integra en eidos_alive_orchestrator.py cada 30 minutos.

Uso:
    from core.relation_engine import RelationEngine

    re = RelationEngine()
    result = re.enrich_graph()
    print(result)
"""

from __future__ import annotations

import json
import logging
import re
import sqlite3
import threading
import time
from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Set, Tuple

from core.db import get_conn

log = logging.getLogger("eidos.relation_engine")

BRAIN_DB = Path.home() / ".eidos" / "evolution_brain.db"

# ── Mapping from existing relation_type strings to canonical semantic types ──
# These are based on the actual relation_type values found in knowledge_edges.
EXISTING_TO_SEMANTIC: Dict[str, str] = {
    # Hierarchy
    "instance_of":     "IS_A",
    "is_a":            "IS_A",
    "type_of":         "IS_A",
    "subclass_of":     "IS_A",
    "inherits":        "IS_A",
    "extends":         "IS_A",
    "part_of":         "PART_OF",
    "contains":        "CONTAINS",
    "has_part":        "HAS_PART",
    # Causal
    "causes":          "CAUSES",
    "caused_by":       "CAUSED_BY",
    "affects":         "CAUSES",
    "enables":         "CAUSES",
    "produces":        "CAUSES",
    # Functional
    "uses":            "USES",
    "used_by":         "USED_BY",
    "depends_on":      "DEPENDS_ON",
    "requires":        "DEPENDS_ON",
    "supports":        "SUPPORTS",
    "calls":           "USES",
    "imports":         "USES",
    "imports_from":    "USES",
    "implements":      "USES",
    "bridges":         "USES",
    "enhances":        "SUPPORTS",
    "complements":     "SUPPORTS",
    # Similarity
    "similar_to":      "SIMILAR_TO",
    "related_to":      None,          # demasiado genérico — no clasificar
    "related":         "SIMILAR_TO",  # S125-M3: generic relatedness → similarity
    "man_see_also":    "SIMILAR_TO",  # S125-M3: man page cross-references
    "alternative":     "SIMILAR_TO",
    "generalizes":     "SIMILAR_TO",
    "analogous_to":    "SIMILAR_TO",
    # Reasoning
    "rationale_for":   "CAUSES",      # S125-M3: rationale = justification/reason
    "method":          "USES",        # S125-M3: method employs/uses techniques
    "synthesizes":     "CAUSES",      # synthesis produces
    "resolves":        "CAUSES",      # resolution is causal
    "strengthens":     "SUPPORTS",    # strengthening supports
    "refines":         "SUPPORTS",    # refinement supports
    "optimizes":       "SUPPORTS",    # optimization supports
    "maintains":       "SUPPORTS",    # maintenance supports
    "organizes":       "USES",        # organization uses structure
    "informs":         "CAUSES",      # information causes knowledge
    "motivates":       "CAUSES",      # motivation causes action
    "protects":        "SUPPORTS",    # protection supports
    "corrects":        "CAUSES",      # correction is causal
    "quantifies":      "USES",        # quantification uses measurement
    "replaces_polling_with": "USES",  # replacement uses new method
    "must_follow":     "DEPENDS_ON",  # obligation = dependency
    "must_respect":    "DEPENDS_ON",  # obligation = dependency
    "respects":        "DEPENDS_ON",  # respect = dependency
    "starts_with":     "BEFORE",      # temporal ordering
    "schedules":       "BEFORE",      # scheduling = temporal
    "tracks":          "USES",        # tracking uses monitoring
    "transports":      "USES",        # transport uses mechanism
    "systematizes":    "USES",        # systematization uses structure
    "humanizes":       "SUPPORTS",    # humanization supports
    "violates":        "CAUSES",      # violation causes conflict
    # Temporal
    "before":          "BEFORE",
    "after":           "AFTER",
    "precedes":        "BEFORE",
    "follows":         "AFTER",
    # Spatial
    "located_in":      "LOCATED_IN",
    "inside":          "LOCATED_IN",
}

# ── Regex patterns for extracting relations from definition text ──
# Each pattern maps to a canonical semantic_type
TEXT_PATTERNS: List[Tuple[re.Pattern, str]] = [
    # IS_A patterns: "X is a Y", "X is an Y", "X is a type of Y"
    (re.compile(r'\b(\w+(?:\s+\w+){0,4})\s+is\s+an?\s+(\w+(?:\s+\w+){0,4})\b', re.IGNORECASE), "IS_A"),
    (re.compile(r'\b(\w+(?:\s+\w+){0,4})\s+is\s+a\s+type\s+of\s+(\w+(?:\s+\w+){0,4})\b', re.IGNORECASE), "IS_A"),
    (re.compile(r'\b(\w+(?:\s+\w+){0,4})\s+is\s+a\s+kind\s+of\s+(\w+(?:\s+\w+){0,4})\b', re.IGNORECASE), "IS_A"),
    (re.compile(r'\b(\w+(?:\s+\w+){0,4})\s+belongs\s+to\s+the\s+(\w+(?:\s+\w+){0,4})\s+(?:class|category|family)\b', re.IGNORECASE), "IS_A"),

    # PART_OF patterns: "X is part of Y", "X is a component of Y"
    (re.compile(r'\b(\w+(?:\s+\w+){0,4})\s+is\s+(?:a\s+)?part\s+of\s+(\w+(?:\s+\w+){0,4})\b', re.IGNORECASE), "PART_OF"),
    (re.compile(r'\b(\w+(?:\s+\w+){0,4})\s+is\s+a\s+component\s+of\s+(\w+(?:\s+\w+){0,4})\b', re.IGNORECASE), "PART_OF"),
    (re.compile(r'\b(\w+(?:\s+\w+){0,4})\s+consists?\s+of\s+(\w+(?:\s+\w+){0,4})\b', re.IGNORECASE), "HAS_PART"),

    # CAUSES patterns: "X causes Y", "X leads to Y", "X results in Y"
    (re.compile(r'\b(\w+(?:\s+\w+){0,4})\s+causes?\s+(\w+(?:\s+\w+){0,4})\b', re.IGNORECASE), "CAUSES"),
    (re.compile(r'\b(\w+(?:\s+\w+){0,4})\s+leads?\s+to\s+(\w+(?:\s+\w+){0,4})\b', re.IGNORECASE), "CAUSES"),
    (re.compile(r'\b(\w+(?:\s+\w+){0,4})\s+results?\s+in\s+(\w+(?:\s+\w+){0,4})\b', re.IGNORECASE), "CAUSES"),

    # USES patterns: "X uses Y", "X utilizes Y", "X employs Y"
    (re.compile(r'\b(\w+(?:\s+\w+){0,4})\s+uses?\s+(\w+(?:\s+\w+){0,4})\b', re.IGNORECASE), "USES"),
    (re.compile(r'\b(\w+(?:\s+\w+){0,4})\s+utilizes?\s+(\w+(?:\s+\w+){0,4})\b', re.IGNORECASE), "USES"),
    (re.compile(r'\b(\w+(?:\s+\w+){0,4})\s+relies?\s+on\s+(\w+(?:\s+\w+){0,4})\b', re.IGNORECASE), "DEPENDS_ON"),

    # LOCATED_IN patterns: "X is located in Y", "X resides in Y"
    (re.compile(r'\b(\w+(?:\s+\w+){0,4})\s+is\s+located\s+in\s+(\w+(?:\s+\w+){0,4})\b', re.IGNORECASE), "LOCATED_IN"),
    (re.compile(r'\b(\w+(?:\s+\w+){0,4})\s+resides?\s+in\s+(\w+(?:\s+\w+){0,4})\b', re.IGNORECASE), "LOCATED_IN"),

    # BEFORE patterns: "X before Y", "X precedes Y"
    (re.compile(r'\b(\w+(?:\s+\w+){0,4})\s+precedes?\s+(\w+(?:\s+\w+){0,4})\b', re.IGNORECASE), "BEFORE"),
    (re.compile(r'\b(\w+(?:\s+\w+){0,4})\s+comes?\s+before\s+(\w+(?:\s+\w+){0,4})\b', re.IGNORECASE), "BEFORE"),
]

# Words to skip as false-positive relation participants
SKIP_WORDS: Set[str] = {
    "it", "this", "that", "which", "who", "what", "they", "them", "these",
    "those", "one", "two", "each", "every", "some", "any", "all", "both",
    "the", "a", "an", "is", "are", "was", "were", "be", "been", "being",
    "have", "has", "had", "do", "does", "did", "will", "would", "shall",
    "should", "may", "might", "must", "can", "could", "not", "no", "nor",
    "or", "and", "but", "if", "then", "else", "when", "where", "why",
    "how", "also", "only", "just", "very", "too", "so", "such", "more",
    "most", "many", "much", "few", "new", "old", "first", "last", "next",
    "there", "here", "other", "another", "same", "different", "own",
    "way", "time", "case", "part", "type", "kind", "form", "use", "end",
    "system", "process", "method", "data", "information", "example",
    "number", "result", "function", "value", "name", "file", "user",
}


@dataclass
class EnrichmentResult:
    """Resultado de un ciclo de enrich_graph()."""
    edges_classified: int = 0
    transitive_inferred: int = 0
    symmetric_inferred: int = 0
    contradictions_found: int = 0
    relations_suggested: int = 0
    errors: List[str] = field(default_factory=list)
    elapsed_ms: float = 0.0

    def summary(self) -> str:
        parts = [
            f"classified={self.edges_classified}",
            f"transitive={self.transitive_inferred}",
            f"symmetric={self.symmetric_inferred}",
            f"contradictions={self.contradictions_found}",
            f"suggested={self.relations_suggested}",
            f"errors={len(self.errors)}",
            f"elapsed={self.elapsed_ms:.0f}ms",
        ]
        return "RelationEngine.enrich: " + " ".join(parts)


class RelationEngine:
    """Motor de razonamiento semántico sobre el grafo de conocimiento.

    Trabaja sobre evolution_brain.db:
      - relation_types: catálogo de tipos semánticos canónicos
      - knowledge_edges: aristas del grafo (con nueva columna semantic_type)
      - knowledge_nodes: nodos del grafo (definiciones para text mining)
    """

    def __init__(self, db_path: str = None, verbose: bool = True):
        self.db_path = db_path or str(BRAIN_DB)
        self.verbose = verbose
        self._lock = threading.Lock()

        self.conn = get_conn(self.db_path, check_same_thread=False)
        self.conn.execute("PRAGMA journal_mode=WAL")
        self.conn.execute("PRAGMA foreign_keys=ON")

        self._ensure_relation_types_table()
        self._load_relation_types()

        if self.verbose:
            n_types = len(self._transitive_types)
            n_symmetric = len(self._symmetric_types)
            print(f"🔗 [RelationEngine] Inicializado: {len(self._types)} tipos semánticos "
                  f"({n_types} transitivos, {n_symmetric} simétricos)")

    def _log(self, msg: str):
        if self.verbose:
            print(f"🔗 [RelationEngine] {msg}")

    # ── Initialization ──────────────────────────────────────────────────────

    def _ensure_relation_types_table(self):
        """Asegura que la tabla relation_types existe y tiene datos."""
        tables = self.conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='relation_types'"
        ).fetchall()
        if not tables:
            raise RuntimeError(
                "Tabla relation_types no encontrada. Ejecuta primero: "
                "sqlite3 ~/.eidos/evolution_brain.db < scripts/migrate_relation_types.sql"
            )

    def _load_relation_types(self):
        """Carga el catálogo de tipos semánticos desde la DB."""
        rows = self.conn.execute(
            "SELECT type, category, inverse, transitive, symmetric, description FROM relation_types"
        ).fetchall()

        self._types: Dict[str, Dict] = {}
        self._inverse_map: Dict[str, str] = {}
        self._transitive_types: Set[str] = set()
        self._symmetric_types: Set[str] = set()

        for row in rows:
            t, cat, inv, trans, sym, desc = row
            self._types[t] = {
                "category": cat,
                "inverse": inv,
                "transitive": bool(trans),
                "symmetric": bool(sym),
                "description": desc or "",
            }
            if inv:
                self._inverse_map[t] = inv
            if trans:
                self._transitive_types.add(t)
            if sym:
                self._symmetric_types.add(t)

    # ── Core: classify edges ────────────────────────────────────────────────

    def classify_existing_edges(self, limit: int = 5000) -> int:
        """Clasifica aristas sin semantic_type usando el mapeo EXISTING_TO_SEMANTIC.

        Solo toca aristas cuyo relation_type textual tiene un mapeo canónico
        conocido y cuya columna semantic_type es NULL.

        Returns:
            Número de aristas clasificadas.
        """
        classified = 0
        with self._lock:
            # Find edges without semantic_type
            rows = self.conn.execute(
                "SELECT id, relation_type FROM knowledge_edges "
                "WHERE semantic_type IS NULL "
                "LIMIT ?",
                (limit,),
            ).fetchall()

            updates = []
            for edge_id, rel_type in rows:
                rel_lower = rel_type.lower().strip() if rel_type else ""
                canonical = EXISTING_TO_SEMANTIC.get(rel_lower)
                if canonical is not None:
                    updates.append((canonical, edge_id))

            if updates:
                self.conn.executemany(
                    "UPDATE knowledge_edges SET semantic_type = ? WHERE id = ?",
                    updates,
                )
                self.conn.commit()
                classified = len(updates)

        if classified:
            self._log(f"Clasificadas {classified} aristas con tipo semántico canónico")
        return classified

    # ── Transitive inference ────────────────────────────────────────────────

    def infer_transitive(self, limit: int = 500) -> int:
        """Cierra cadenas transitivas: A→B + B→C ⇒ A→C.

        Para cada tipo transitivo (IS_A, PART_OF, BEFORE, AFTER):
          Encuentra pares (A, B, C) donde existe A→B y B→C con ese tipo,
          y crea A→C si no existe ya.

        Returns:
            Número de nuevas aristas inferidas.
        """
        if not self._transitive_types:
            return 0

        inferred = 0
        with self._lock:
            for sem_type in self._transitive_types:
                # Find chains: A->B and B->C both with this semantic_type
                # (or relation_type that maps to it)
                rows = self.conn.execute(
                    "SELECT DISTINCT e1.from_node, e1.to_node, e2.to_node "
                    "FROM knowledge_edges e1 "
                    "JOIN knowledge_edges e2 ON e1.to_node = e2.from_node "
                    "WHERE (e1.semantic_type = ? OR e1.relation_type = ?) "
                    "  AND (e2.semantic_type = ? OR e2.relation_type = ?) "
                    "LIMIT ?",
                    (sem_type, sem_type.lower(), sem_type, sem_type.lower(), limit),
                ).fetchall()

                inserts = []
                for from_node, mid_node, to_node in rows:
                    if from_node == to_node:
                        continue  # no self-loops
                    # Check if A->C already exists with this type
                    existing = self.conn.execute(
                        "SELECT 1 FROM knowledge_edges "
                        "WHERE from_node = ? AND to_node = ? "
                        "  AND (semantic_type = ? OR relation_type = ?) "
                        "LIMIT 1",
                        (from_node, to_node, sem_type, sem_type.lower()),
                    ).fetchone()
                    if not existing:
                        inserts.append((
                            from_node, to_node,
                            sem_type.lower(),   # relation_type column
                            sem_type,           # semantic_type column
                            0.5,                # inferred strength (lower than direct)
                            json.dumps({
                                "inferred": "transitive",
                                "via": mid_node,
                                "semantic_type": sem_type,
                                "timestamp": time.time(),
                            }),
                        ))

                if inserts:
                    self.conn.executemany(
                        "INSERT OR IGNORE INTO knowledge_edges "
                        "(from_node, to_node, relation_type, semantic_type, strength, metadata) "
                        "VALUES (?, ?, ?, ?, ?, ?)",
                        inserts,
                    )
                    self.conn.commit()
                    inferred += len(inserts)

        if inferred:
            self._log(f"Inferencia transitiva: {inferred} nuevas aristas")
        return inferred

    # ── Symmetric inference ─────────────────────────────────────────────────

    def infer_symmetric(self, limit: int = 1000) -> int:
        """Completa aristas inversas para relaciones simétricas.

        Para SIMILAR_TO: si A→B existe pero B→A no, crea B→A.
        También completa aristas INVERSAS para tipos con inverse definido:
          si A IS_A B y no existe B HAS_INSTANCE A, créala.

        Returns:
            Número de nuevas aristas creadas.
        """
        inferred = 0

        with self._lock:
            # 1. Symmetric completions (e.g., SIMILAR_TO)
            for sym_type in self._symmetric_types:
                rows = self.conn.execute(
                    "SELECT DISTINCT e1.from_node, e1.to_node "
                    "FROM knowledge_edges e1 "
                    "WHERE (e1.semantic_type = ? OR e1.relation_type = ?) "
                    "  AND e1.from_node != e1.to_node "
                    "LIMIT ?",
                    (sym_type, sym_type.lower(), limit),
                ).fetchall()

                inserts = []
                for from_node, to_node in rows:
                    # Check if reverse already exists
                    existing = self.conn.execute(
                        "SELECT 1 FROM knowledge_edges "
                        "WHERE from_node = ? AND to_node = ? "
                        "  AND (semantic_type = ? OR relation_type = ?) "
                        "LIMIT 1",
                        (to_node, from_node, sym_type, sym_type.lower()),
                    ).fetchone()
                    if not existing:
                        inserts.append((
                            to_node, from_node,
                            sym_type.lower(),
                            sym_type,
                            0.6,  # slightly lower for inferred reverse
                            json.dumps({
                                "inferred": "symmetric",
                                "from": from_node,
                                "semantic_type": sym_type,
                                "timestamp": time.time(),
                            }),
                        ))

                if inserts:
                    self.conn.executemany(
                        "INSERT OR IGNORE INTO knowledge_edges "
                        "(from_node, to_node, relation_type, semantic_type, strength, metadata) "
                        "VALUES (?, ?, ?, ?, ?, ?)",
                        inserts,
                    )
                    self.conn.commit()
                    inferred += len(inserts)

            # 2. Inverse completions (e.g., if A IS_A B, create B HAS_INSTANCE A)
            for sem_type, info in self._types.items():
                inverse_type = info.get("inverse")
                if not inverse_type:
                    continue

                rows = self.conn.execute(
                    "SELECT DISTINCT e1.from_node, e1.to_node "
                    "FROM knowledge_edges e1 "
                    "WHERE (e1.semantic_type = ? OR e1.relation_type = ?) "
                    "  AND e1.from_node != e1.to_node "
                    "LIMIT ?",
                    (sem_type, sem_type.lower(), limit),
                ).fetchall()

                inserts = []
                for from_node, to_node in rows:
                    # Check if inverse already exists
                    existing = self.conn.execute(
                        "SELECT 1 FROM knowledge_edges "
                        "WHERE from_node = ? AND to_node = ? "
                        "  AND (semantic_type = ? OR relation_type = ?) "
                        "LIMIT 1",
                        (to_node, from_node, inverse_type, inverse_type.lower()),
                    ).fetchone()
                    if not existing:
                        inserts.append((
                            to_node, from_node,
                            inverse_type.lower(),
                            inverse_type,
                            0.5,
                            json.dumps({
                                "inferred": "inverse",
                                "from": f"{from_node} {sem_type} {to_node}",
                                "semantic_type": inverse_type,
                                "timestamp": time.time(),
                            }),
                        ))

                if inserts:
                    self.conn.executemany(
                        "INSERT OR IGNORE INTO knowledge_edges "
                        "(from_node, to_node, relation_type, semantic_type, strength, metadata) "
                        "VALUES (?, ?, ?, ?, ?, ?)",
                        inserts,
                    )
                    self.conn.commit()
                    inferred += len(inserts)

        if inferred:
            self._log(f"Inferencia simétrica/inversa: {inferred} nuevas aristas")
        return inferred

    # ── Contradiction detection ─────────────────────────────────────────────

    def find_contradictions(self, limit: int = 200) -> List[Dict[str, Any]]:
        """Detecta contradicciones potenciales en el grafo.

        Reglas:
          - Si A IS_A B y A IS_A C con B≠C, y no hay IS_A entre B y C → flag
          - Si A PART_OF B y A PART_OF C con B≠C → flag

        Returns:
            Lista de contradicciones detectadas, cada una con:
              {node, type1, target1, type2, target2, reason}
        """
        contradictions: List[Dict[str, Any]] = []

        with self._lock:
            # Contradiction type 1: multiple IS_A parents without hierarchy
            rows = self.conn.execute(
                "SELECT e1.from_node, e1.to_node AS target1, e2.to_node AS target2 "
                "FROM knowledge_edges e1 "
                "JOIN knowledge_edges e2 ON e1.from_node = e2.from_node "
                "WHERE (e1.semantic_type = 'IS_A' OR e1.relation_type = 'is_a') "
                "  AND (e2.semantic_type = 'IS_A' OR e2.relation_type = 'is_a') "
                "  AND e1.to_node != e2.to_node "
                "  AND e1.id < e2.id "  # Avoid duplicates
                "LIMIT ?",
                (limit,),
            ).fetchall()

            for node, target1, target2 in rows:
                # Check if target1 IS_A target2 or target2 IS_A target1 (valid hierarchy)
                hierarchy_exists = self.conn.execute(
                    "SELECT 1 FROM knowledge_edges "
                    "WHERE ((from_node = ? AND to_node = ?) OR (from_node = ? AND to_node = ?)) "
                    "  AND (semantic_type = 'IS_A' OR relation_type = 'is_a') "
                    "LIMIT 1",
                    (target1, target2, target2, target1),
                ).fetchone()

                if not hierarchy_exists:
                    contradictions.append({
                        "node": node,
                        "type1": "IS_A",
                        "target1": target1,
                        "type2": "IS_A",
                        "target2": target2,
                        "reason": f"Multiple IS_A parents without hierarchy: {target1} vs {target2}",
                    })

            # Contradiction type 2: IS_A and PART_OF cycle
            rows = self.conn.execute(
                "SELECT e1.from_node, e1.to_node AS is_a_target, e2.to_node AS part_of_target "
                "FROM knowledge_edges e1 "
                "JOIN knowledge_edges e2 ON e1.from_node = e2.from_node "
                "WHERE (e1.semantic_type = 'IS_A' OR e1.relation_type = 'is_a') "
                "  AND (e2.semantic_type = 'PART_OF' OR e2.relation_type = 'part_of') "
                "LIMIT ?",
                (limit,),
            ).fetchall()

            for node, is_a_target, part_of_target in rows:
                # An entity can be both an instance of something and part of something
                # This is only a contradiction if is_a_target == part_of_target (would be unusual)
                if is_a_target == part_of_target:
                    contradictions.append({
                        "node": node,
                        "type1": "IS_A",
                        "target1": is_a_target,
                        "type2": "PART_OF",
                        "target2": part_of_target,
                        "reason": "Entity is both instance-of and part-of the same target",
                    })

        if contradictions:
            self._log(f"Contradicciones detectadas: {len(contradictions)}")
        return contradictions

    # ── Text-based relation suggestion ──────────────────────────────────────

    def suggest_relations(self, limit: int = 200) -> int:
        """Extrae nuevas relaciones desde texto de definiciones.

        Escanea knowledge_nodes.definition en busca de patrones regex como
        "X is a Y", "X causes Y", "X uses Y", etc.

        Returns:
            Número de nuevas aristas sugeridas (insertadas).
        """
        suggested = 0
        # Load definitions (limit to recent ones for performance)
        with self._lock:
            rows = self.conn.execute(
                "SELECT concept, definition FROM knowledge_nodes "
                "WHERE definition IS NOT NULL AND length(definition) > 10 "
                "ORDER BY updated_at DESC "
                "LIMIT ?",
                (limit * 3,),  # Over-fetch because not all will match
            ).fetchall()

        # Also load the set of known concepts for validation
        with self._lock:
            all_concepts = set(
                r[0] for r in self.conn.execute(
                    "SELECT concept FROM knowledge_nodes LIMIT 50000"
                ).fetchall()
            )

        inserts = []
        seen_pairs: Set[Tuple[str, str, str]] = set()

        for concept, definition in rows:
            if not definition:
                continue
            # Clean the definition: strip HTML tags and normalize spaces
            clean_def = re.sub(r'<[^>]+>', ' ', definition)
            clean_def = re.sub(r'\s+', ' ', clean_def).strip()

            for pattern, sem_type in TEXT_PATTERNS:
                for match in pattern.finditer(clean_def):
                    from_text = match.group(1).strip().lower()
                    to_text = match.group(2).strip().lower()

                    # Skip stopwords and very short tokens
                    if from_text in SKIP_WORDS or to_text in SKIP_WORDS:
                        continue
                    if len(from_text) < 2 or len(to_text) < 2:
                        continue

                    # Fuzzy match against known concepts
                    from_concept = self._best_match(from_text, all_concepts)
                    to_concept = self._best_match(to_text, all_concepts)

                    if from_concept and to_concept and from_concept != to_concept:
                        pair_key = (from_concept, to_concept, sem_type)
                        if pair_key not in seen_pairs:
                            seen_pairs.add(pair_key)
                            inserts.append((
                                from_concept, to_concept,
                                sem_type.lower(),
                                sem_type,
                                0.4,  # lower confidence for text-mined relations
                                json.dumps({
                                    "inferred": "text_pattern",
                                    "pattern": pattern.pattern[:100],
                                    "matched_text": f"{from_text} ... {to_text}",
                                    "source_concept": concept,
                                    "timestamp": time.time(),
                                }),
                            ))

        if inserts:
            with self._lock:
                self.conn.executemany(
                    "INSERT OR IGNORE INTO knowledge_edges "
                    "(from_node, to_node, relation_type, semantic_type, strength, metadata) "
                    "VALUES (?, ?, ?, ?, ?, ?)",
                    inserts,
                )
                self.conn.commit()
            suggested = len(inserts)

        if suggested:
            self._log(f"Relaciones sugeridas desde texto: {suggested}")
        return suggested

    def _best_match(self, text: str, candidates: Set[str]) -> Optional[str]:
        """Encuentra el mejor matching de 'text' en el conjunto de conceptos."""
        # Exact match
        if text in candidates:
            return text
        # Contains match
        for cand in candidates:
            if text in cand or cand in text:
                return cand
        return None

    # ── Orchestration ───────────────────────────────────────────────────────

    def enrich_graph(self,
                     classify_limit: int = 5000,
                     transitive_limit: int = 500,
                     symmetric_limit: int = 1000,
                     suggest_limit: int = 200,
                     ) -> EnrichmentResult:
        """Ejecuta un ciclo completo de enriquecimiento del grafo.

        Orden:
          1. Clasificar aristas existentes sin semantic_type
          2. Inferencia transitiva (IS_A, PART_OF, BEFORE, AFTER)
          3. Inferencia simétrica (SIMILAR_TO) + inversas
          4. Detección de contradicciones
          5. Sugerencia de relaciones desde texto

        Returns:
            EnrichmentResult con conteos de cada fase.
        """
        result = EnrichmentResult()
        t0 = time.time()

        try:
            result.edges_classified = self.classify_existing_edges(limit=classify_limit)
        except Exception as e:
            msg = f"classify_existing_edges: {e}"
            log.exception(msg)
            result.errors.append(msg)

        try:
            result.transitive_inferred = self.infer_transitive(limit=transitive_limit)
        except Exception as e:
            msg = f"infer_transitive: {e}"
            log.exception(msg)
            result.errors.append(msg)

        try:
            result.symmetric_inferred = self.infer_symmetric(limit=symmetric_limit)
        except Exception as e:
            msg = f"infer_symmetric: {e}"
            log.exception(msg)
            result.errors.append(msg)

        try:
            contradictions = self.find_contradictions()
            result.contradictions_found = len(contradictions)
            # Log first few contradictions
            for c in contradictions[:5]:
                log.warning("Contradiction: %s → (%s %s) vs (%s %s) — %s",
                            c["node"], c["type1"], c["target1"],
                            c["type2"], c["target2"], c["reason"])
        except Exception as e:
            msg = f"find_contradictions: {e}"
            log.exception(msg)
            result.errors.append(msg)

        try:
            result.relations_suggested = self.suggest_relations(limit=suggest_limit)
        except Exception as e:
            msg = f"suggest_relations: {e}"
            log.exception(msg)
            result.errors.append(msg)

        result.elapsed_ms = (time.time() - t0) * 1000
        self._log(result.summary())
        return result

    # ── Statistics ──────────────────────────────────────────────────────────

    def get_stats(self) -> Dict[str, Any]:
        """Devuelve estadísticas del grafo semántico."""
        with self._lock:
            total_edges = self.conn.execute(
                "SELECT COUNT(*) FROM knowledge_edges"
            ).fetchone()[0]
            typed_edges = self.conn.execute(
                "SELECT COUNT(*) FROM knowledge_edges WHERE semantic_type IS NOT NULL"
            ).fetchone()[0]
            by_type = self.conn.execute(
                "SELECT semantic_type, COUNT(*) FROM knowledge_edges "
                "WHERE semantic_type IS NOT NULL "
                "GROUP BY semantic_type ORDER BY COUNT(*) DESC"
            ).fetchall()
            by_category = self.conn.execute(
                "SELECT rt.category, COUNT(ke.id) "
                "FROM knowledge_edges ke "
                "JOIN relation_types rt ON ke.semantic_type = rt.type "
                "GROUP BY rt.category ORDER BY COUNT(ke.id) DESC"
            ).fetchall()

        return {
            "total_edges": total_edges,
            "typed_edges": typed_edges,
            "typed_pct": round(100 * typed_edges / max(total_edges, 1), 1),
            "by_semantic_type": [(t, c) for t, c in by_type],
            "by_category": [(cat, c) for cat, c in by_category],
        }


# ── Singleton ───────────────────────────────────────────────────────────────

_engine: Optional[RelationEngine] = None
_engine_lock = threading.Lock()


def get_relation_engine(db_path: str = None, verbose: bool = True) -> RelationEngine:
    """Devuelve la instancia singleton de RelationEngine."""
    global _engine
    if _engine is None:
        with _engine_lock:
            if _engine is None:
                _engine = RelationEngine(db_path=db_path, verbose=verbose)
    return _engine


# ── Quick CLI test ──────────────────────────────────────────────────────────

if __name__ == "__main__":
    import sys
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")

    re_engine = RelationEngine(verbose=True)
    print(f"\nTipos cargados: {len(re_engine._types)}")
    print(f"Transitivos: {sorted(re_engine._transitive_types)}")
    print(f"Simétricos:  {sorted(re_engine._symmetric_types)}")

    stats = re_engine.get_stats()
    print(f"\nEstado actual: {stats['total_edges']} aristas totales, "
          f"{stats['typed_edges']} tipadas ({stats['typed_pct']}%)")
    if stats["by_semantic_type"]:
        print("Por tipo semántico:")
        for t, c in stats["by_semantic_type"][:10]:
            print(f"  {t}: {c}")

    # Run a quick enrich
    if "--enrich" in sys.argv:
        result = re_engine.enrich_graph()
        print(f"\nResultado: {result.summary()}")
    elif "--dry-run" in sys.argv:
        print("\n(Dry run — use --enrich para ejecutar)")
    else:
        print("\n(Ejecuta con --enrich para correr un ciclo completo)")
