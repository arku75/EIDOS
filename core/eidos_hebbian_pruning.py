"""
core/eidos_hebbian_pruning.py — Poda hebbiana del grafo (S78 "Supervivencia")

"Neurons that fire together wire together — neurons that don't, die."
Implementa el olvido biológico: conexiones débiles se podan, conexiones fuertes
se consolidan. Basado en el principio de Hebb + recomendación DeepSeek.

API:
    pruner = get_pruner()
    result = pruner.prune(dry_run=True)   # simular
    result = pruner.prune(dry_run=False)  # ejecutar
    pruner.consolidate()                   # reforzar conexiones fuertes
"""

from __future__ import annotations

import json
import logging
import os
import sqlite3
import time
from pathlib import Path
from typing import Any, Dict, List, Optional
from core.db import get_conn

log = logging.getLogger("eidos.pruning")

EIDOS_HOME = Path(os.environ.get("EIDOS_HOME", str(Path.home() / ".eidos"))).expanduser()
BRAIN_DB = EIDOS_HOME / "evolution_brain.db"
PRUNE_STATE_FILE = EIDOS_HOME / "prune_state.json"

# Umbrales de poda
PRUNE_THRESHOLD_WEAK = 0.05     # aristas con weight < 0.05 → podar
PRUNE_THRESHOLD_ORPHAN = 0.15   # nodos huérfanos con < 2 aristas → podar
PRUNE_THRESHOLD_OLD = 0.08      # aristas viejas (>30 días sin activar) → degradar
CONSOLIDATE_THRESHOLD = 0.85    # aristas con weight >= 0.85 → reforzar
MAX_PRUNABLE_PER_CYCLE = 500    # máximo aristas podadas por ciclo
MAX_ORPHAN_PER_CYCLE = 200      # máximo nodos huérfanos podados por ciclo

# Decaimiento hebbiano
HEBBIAN_DECAY_RATE = 0.002      # tasa de decaimiento por ciclo sin activación
HEBBIAN_STRENGTHEN_RATE = 0.05  # tasa de refuerzo por activación
MIN_EDGE_WEIGHT = 0.01          # peso mínimo para mantener arista
MAX_EDGE_WEIGHT = 1.0           # peso máximo


class HebbianPruner:
    """Poda conexiones débiles, consolida las fuertes. Como el sueño biológico."""

    def __init__(self):
        self._prune_history: List[Dict[str, Any]] = []
        self._total_pruned_edges = 0
        self._total_pruned_nodes = 0
        self._total_consolidated = 0
        self._last_prune: float = 0
        self._load_state()
        self._init_db()

    # ── DB init ─────────────────────────────────────────────────────────────

    def _init_db(self):
        try:
            conn = get_conn(BRAIN_DB, timeout=10)
            conn.execute("PRAGMA busy_timeout=10000")
            # Columna para tracking de última activación hebbiana
            conn.execute("""CREATE TABLE IF NOT EXISTS edge_activation_log (
                edge_id TEXT PRIMARY KEY,
                last_activated REAL NOT NULL,
                activation_count INTEGER DEFAULT 1,
                initial_weight REAL DEFAULT 0.5)""")
            conn.execute("""CREATE INDEX IF NOT EXISTS idx_edge_act_time
                ON edge_activation_log(last_activated)""")
            # S82b B2 fix: índice para acelerar query de nodos huérfanos
            conn.execute("""CREATE INDEX IF NOT EXISTS idx_edges_to_node
                ON knowledge_edges(to_node)""")
            # Columna para nodos huérfanos
            conn.execute("""CREATE TABLE IF NOT EXISTS orphaned_nodes_log (
                node_id TEXT PRIMARY KEY,
                pruned_at REAL NOT NULL,
                concept TEXT DEFAULT '',
                edge_count INTEGER DEFAULT 0)""")
            conn.commit()

        except Exception as e:
            log.debug("HebbianPruner DB init: %s", e)

    # ── Estado ──────────────────────────────────────────────────────────────

    def _load_state(self):
        try:
            if PRUNE_STATE_FILE.exists():
                with open(PRUNE_STATE_FILE) as f:
                    data = json.load(f)
                self._total_pruned_edges = data.get("total_pruned_edges", 0)
                self._total_pruned_nodes = data.get("total_pruned_nodes", 0)
                self._total_consolidated = data.get("total_consolidated", 0)
                self._last_prune = data.get("last_prune", 0)
        except Exception:
            pass

    def _save_state(self):
        try:
            PRUNE_STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
            with open(PRUNE_STATE_FILE, "w") as f:
                json.dump({
                    "total_pruned_edges": self._total_pruned_edges,
                    "total_pruned_nodes": self._total_pruned_nodes,
                    "total_consolidated": self._total_consolidated,
                    "last_prune": self._last_prune,
                    "updated": time.time(),
                }, f, indent=2)
        except Exception:
            pass

    # ── Poda ────────────────────────────────────────────────────────────────

    def prune(self, dry_run: bool = True) -> Dict[str, Any]:
        """Ejecuta ciclo de poda hebbiana.

        Estrategia:
        1. Aristas con weight < PRUNE_THRESHOLD_WEAK → eliminar (conexiones insignificantes)
        2. Aristas viejas (>30 días sin activar) → degradar weight * 0.5
        3. Nodos huérfanos (< 2 aristas) → eliminar (ruido)
        4. Límite por ciclo para no destruir el grafo
        """
        result = {
            "dry_run": dry_run,
            "edges_pruned": 0,
            "nodes_pruned": 0,
            "edges_degraded": 0,
            "nodes_orphaned": 0,
            "nodes_pruned": 0,
            "timestamp": time.time(),
        }

        try:
            conn = get_conn(BRAIN_DB, timeout=30)
            conn.execute("PRAGMA busy_timeout=30000")

            # ── Fase 1: Aristas débiles ──
            weak_edges = conn.execute(
                "SELECT from_node, to_node, relation_type, strength FROM knowledge_edges "
                "WHERE strength IS NOT NULL AND strength < ? "
                "ORDER BY strength ASC LIMIT ?",
                (PRUNE_THRESHOLD_WEAK, MAX_PRUNABLE_PER_CYCLE)
            ).fetchall()

            if weak_edges:
                result["edges_pruned"] = len(weak_edges)
                result["weakest_weight"] = weak_edges[0][3] if weak_edges else 0
                if not dry_run:
                    for from_n, to_n, rel, strength in weak_edges:
                        conn.execute(
                            "DELETE FROM knowledge_edges WHERE from_node=? AND to_node=? "
                            "AND relation_type=? AND strength=?",
                            (from_n, to_n, rel, strength))
                    # Registrar en activation log como podadas
                    now = time.time()
                    for from_n, to_n, rel, strength in weak_edges:
                        eid = f"{from_n}→{to_n}:{rel}"
                        conn.execute(
                            "INSERT OR REPLACE INTO edge_activation_log (edge_id, last_activated, "
                            "activation_count, initial_weight) VALUES (?,?,0,?)",
                            (eid, now, strength))
                    conn.commit()
                    self._total_pruned_edges += len(weak_edges)

            # ── Fase 2: Aristas viejas sin activar → degradar ──
            thirty_days_ago = time.time() - (86400 * 30)
            old_edges = conn.execute(
                "SELECT e.from_node, e.to_node, e.relation_type, e.strength, "
                "COALESCE(a.last_activated, ?) as last_act "
                "FROM knowledge_edges e LEFT JOIN edge_activation_log a "
                "ON a.edge_id = e.from_node || '→' || e.to_node || ':' || e.relation_type "
                "WHERE e.strength IS NOT NULL AND e.strength > ? "
                "AND COALESCE(a.last_activated, ?) < ? "
                "LIMIT ?",
                (time.time() - 86400 * 365, PRUNE_THRESHOLD_WEAK,
                 time.time() - 86400 * 365, thirty_days_ago, MAX_PRUNABLE_PER_CYCLE)
            ).fetchall()

            if old_edges and not dry_run:
                degraded = 0
                for from_n, to_n, rel, strength, last_act in old_edges:
                    new_strength = max(0.01, (strength or 0.5) * 0.5)
                    conn.execute(
                        "UPDATE knowledge_edges SET strength=? WHERE from_node=? AND to_node=? "
                        "AND relation_type=?",
                        (new_strength, from_n, to_n, rel))
                    degraded += 1
                conn.commit()
                result["edges_degraded"] = degraded

            # ── Fase 3: Nodos huérfanos ──
            # Usar UNION ALL para evitar OR en JOIN (lento sin índices en 1M edges)
            try:
                orphan_nodes = conn.execute(
                    "SELECT n.id, n.concept, COALESCE(ec.ec, 0) as ec "
                    "FROM knowledge_nodes n "
                    "LEFT JOIN ("
                    "  SELECT node_id, SUM(cnt) as ec FROM ("
                    "    SELECT from_node as node_id, COUNT(*) as cnt "
                    "    FROM knowledge_edges GROUP BY from_node "
                    "    UNION ALL "
                    "    SELECT to_node as node_id, COUNT(*) as cnt "
                    "    FROM knowledge_edges GROUP BY to_node"
                    "  ) GROUP BY node_id"
                    ") ec ON n.id = ec.node_id "
                    "WHERE n.source NOT IN ('graphify', 'wordnet') "
                    "ORDER BY ec ASC LIMIT ?",
                    (MAX_ORPHAN_PER_CYCLE,)
                ).fetchall()
            except Exception:
                orphan_nodes = []  # fallback si tarda demasiado

            if orphan_nodes:
                result["nodes_orphaned"] = len(orphan_nodes)
                if not dry_run:
                    now = time.time()
                    for nid, concept, ec in orphan_nodes:
                        conn.execute("DELETE FROM knowledge_nodes WHERE id=?", (nid,))
                        conn.execute("DELETE FROM knowledge_edges WHERE from_node=? OR to_node=?",
                                   (nid, nid))
                        conn.execute(
                            "INSERT OR REPLACE INTO orphaned_nodes_log "
                            "(node_id, pruned_at, concept, edge_count) VALUES (?,?,?,?)",
                            (nid, now, concept[:200], ec))
                    conn.commit()
                    self._total_pruned_nodes += len(orphan_nodes)
                    result["nodes_pruned"] = len(orphan_nodes)

        except Exception as e:
            log.error("Prune error: %s", e)
            result["error"] = str(e)[:200]

        self._last_prune = time.time()
        self._prune_history.append(result)
        self._prune_history = self._prune_history[-50:]
        self._save_state()

        if not dry_run and (result["edges_pruned"] > 0 or result["nodes_pruned"] > 0):
            log.info("Poda: %d aristas, %d nodos eliminados, %d aristas degradadas",
                     result["edges_pruned"], result["nodes_pruned"], result["edges_degraded"])

            # Emitir evento
            try:
                from core.eidos_events import emit
                emit("structural_refresh", result, source="hebbian_pruner")
            except Exception:
                pass

        return result

    # ── Consolidación ───────────────────────────────────────────────────────

    def consolidate(self, dry_run: bool = True) -> Dict[str, Any]:
        """Refuerza conexiones fuertes (Hebbian strengthening).

        Aristas con weight >= CONSOLIDATE_THRESHOLD reciben refuerzo,
        simulando 'neurons that fire together wire together'.
        """
        result = {"dry_run": dry_run, "edges_strengthened": 0, "timestamp": time.time()}

        try:
            conn = get_conn(BRAIN_DB, timeout=10)
            conn.execute("PRAGMA busy_timeout=10000")

            strong = conn.execute(
                "SELECT from_node, to_node, relation_type, strength FROM knowledge_edges "
                "WHERE strength >= ? AND strength < ? "
                "LIMIT 200",
                (CONSOLIDATE_THRESHOLD, MAX_EDGE_WEIGHT)
            ).fetchall()

            if strong and not dry_run:
                strengthened = 0
                for from_n, to_n, rel, strength in strong:
                    new_strength = min(MAX_EDGE_WEIGHT, (strength or 0.5) + HEBBIAN_STRENGTHEN_RATE)
                    conn.execute(
                        "UPDATE knowledge_edges SET strength=? WHERE from_node=? AND to_node=? "
                        "AND relation_type=?",
                        (new_strength, from_n, to_n, rel))
                    strengthened += 1
                conn.commit()
                result["edges_strengthened"] = strengthened
                self._total_consolidated += strengthened

        except Exception as e:
            log.error("Consolidate error: %s", e)
            result["error"] = str(e)[:200]

        self._save_state()
        return result

    # ── Decaimiento natural ─────────────────────────────────────────────────

    def decay_all(self, decay_rate: float = HEBBIAN_DECAY_RATE) -> Dict[str, Any]:
        """Aplica decaimiento hebbiano a TODAS las aristas (llamado por ciclo o batch).

        Cada arista pierde `decay_rate` de su peso por ciclo sin activación.
        Esto simula el olvido natural del cerebro biológico.
        """
        try:
            conn = get_conn(BRAIN_DB, timeout=30)
            conn.execute("PRAGMA busy_timeout=30000")
            conn.execute(
                "UPDATE knowledge_edges SET strength = MAX(?, strength - ?) "
                "WHERE strength IS NOT NULL",
                (MIN_EDGE_WEIGHT, decay_rate))
            # Marcar las que cayeron a MIN_EDGE_WEIGHT para posible poda futura
            affected = conn.execute(
                "SELECT COUNT(*) FROM knowledge_edges WHERE strength = ?",
                (MIN_EDGE_WEIGHT,)).fetchone()[0]
            conn.commit()

            log.debug("Decaimiento hebbiano: %d aristas en peso mínimo", affected)
            return {"decayed": True, "at_min_weight": affected}
        except Exception as e:
            log.debug("Decay error: %s", e)
            return {"decayed": False, "error": str(e)[:100]}

    # ── Tracking de activación ──────────────────────────────────────────────

    def record_activation(self, from_node: str, to_node: str, relation_type: str = ""):
        """Registra que una arista fue activada (para tracking hebbiano)."""
        try:
            conn = get_conn(BRAIN_DB, timeout=5)
            conn.execute("PRAGMA busy_timeout=5000")
            eid = f"{from_node}→{to_node}:{relation_type}"
            conn.execute(
                "INSERT INTO edge_activation_log (edge_id, last_activated, activation_count) "
                "VALUES (?,?,1) ON CONFLICT(edge_id) DO UPDATE SET "
                "last_activated=excluded.last_activated, "
                "activation_count=activation_count+1",
                (eid, time.time()))
            conn.commit()

        except Exception:
            pass

    # ── Stats ───────────────────────────────────────────────────────────────

    def stats(self) -> Dict[str, Any]:
        return {
            "total_pruned_edges": self._total_pruned_edges,
            "total_pruned_nodes": self._total_pruned_nodes,
            "total_consolidated": self._total_consolidated,
            "last_prune_ago_s": round(time.time() - self._last_prune) if self._last_prune else None,
            "prune_cycles": len(self._prune_history),
        }

    def get_graph_health(self) -> Dict[str, Any]:
        """Métrica de salud del grafo post-poda."""
        try:
            conn = get_conn(BRAIN_DB, timeout=5)
            total_nodes = conn.execute("SELECT COUNT(*) FROM knowledge_nodes").fetchone()[0]
            total_edges = conn.execute("SELECT COUNT(*) FROM knowledge_edges").fetchone()[0]
            avg_weight = conn.execute(
                "SELECT AVG(strength) FROM knowledge_edges WHERE strength IS NOT NULL"
            ).fetchone()[0] or 0
            weak_count = conn.execute(
                "SELECT COUNT(*) FROM knowledge_edges WHERE strength < ?",
                (PRUNE_THRESHOLD_WEAK,)).fetchone()[0]
            strong_count = conn.execute(
                "SELECT COUNT(*) FROM knowledge_edges WHERE strength >= ?",
                (CONSOLIDATE_THRESHOLD,)).fetchone()[0]

            density = total_edges / max(1, total_nodes)
            return {
                "total_nodes": total_nodes,
                "total_edges": total_edges,
                "density": round(density, 3),
                "avg_weight": round(avg_weight, 4),
                "weak_edges": weak_count,
                "strong_edges": strong_count,
                "health_score": round(
                    (1.0 - weak_count / max(1, total_edges)) * 0.7 +
                    min(1.0, density / 5) * 0.3, 3
                ),
            }
        except Exception as e:
            return {"error": str(e)[:100]}


_pruner: Optional[HebbianPruner] = None


def get_pruner() -> HebbianPruner:
    global _pruner
    if _pruner is None:
        _pruner = HebbianPruner()
    return _pruner


if __name__ == "__main__":
    import argparse
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    p = argparse.ArgumentParser(description="EIDOS Hebbian Pruner")
    p.add_argument("--prune", action="store_true", help="Ejecutar poda")
    p.add_argument("--dry-run", action="store_true", help="Simular sin ejecutar")
    p.add_argument("--consolidate", action="store_true", help="Consolidar conexiones fuertes")
    p.add_argument("--decay", action="store_true", help="Aplicar decaimiento")
    p.add_argument("--stats", action="store_true", help="Estadísticas")
    p.add_argument("--health", action="store_true", help="Salud del grafo")
    args = p.parse_args()

    pruner = HebbianPruner()
    dry = args.dry_run

    if args.prune:
        result = pruner.prune(dry_run=dry)
        print(json.dumps(result, indent=2, ensure_ascii=False))
    elif args.consolidate:
        result = pruner.consolidate(dry_run=dry)
        print(json.dumps(result, indent=2, ensure_ascii=False))
    elif args.decay:
        result = pruner.decay_all()
        print(json.dumps(result, indent=2, ensure_ascii=False))
    elif args.stats:
        print(json.dumps(pruner.stats(), indent=2, ensure_ascii=False))
    elif args.health:
        print(json.dumps(pruner.get_graph_health(), indent=2, ensure_ascii=False))
    else:
        p.print_help()
