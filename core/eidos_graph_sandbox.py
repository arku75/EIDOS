"""
core/eidos_graph_sandbox.py — Simulación Parenética [S86 Fase 3.2]

Gemelo del grafo neuronal en sandbox aislado (SQLite :memory:). Aplica
operadores de mutación controlados y observa la evolución a largo plazo
ANTES de aplicar cualquier auto-modificación al grafo real.

"Conocer todas tus posibles muertes antes de elegir cómo vivir."

Capacidades:
  - snapshot(): captura el estado actual del grafo en :memory:
  - mutate(): aplica operadores de mutación (ruido, rewiring, poda agresiva)
  - simulate(n_cycles): ejecuta N ciclos de evolución en el sandbox
  - evaluate(): compara el grafo original con el evolucionado
  - converge() / diverge() / stabilize(): métricas de trayectoria

Uso:
    sandbox = get_graph_sandbox()
    sandbox.snapshot()                    # copiar grafo real → sandbox
    result = sandbox.simulate(cycles=100) # evolucionar en aislamiento
    if result["converged"]:
        print("Seguro aplicar esta mutación")
    else:
        print("La mutación lleva al caos — no aplicar")

El grafo real NUNCA es modificado por el sandbox.
"""

from __future__ import annotations

import hashlib
import json
import logging
import sqlite3
import time
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

log = logging.getLogger("eidos.sandbox")

BRAIN_DB = Path.home() / ".eidos" / "evolution_brain.db"
SANDBOX_STATE = Path.home() / ".eidos" / "sandbox_state.json"

# Operadores de mutación
MUTATION_TYPES = [
    "add_noise",        # añadir aristas aleatorias (baja probabilidad)
    "rewire",           # redirigir aristas existentes a nodos vecinos
    "aggressive_prune", # poda más agresiva que la normal
    "boost_novelty",    # reforzar conexiones entre categorías dispares
    "weaken_hub",       # debilitar nodos hub (demasiadas conexiones)
    "consolidate_strong",  # reforzar las conexiones más fuertes
]


class GraphSandbox:
    """Sandbox aislado para simular evolución del grafo sin modificar el real.

    Principio parenético: "simular para prever, prever para elegir."
    """

    def __init__(self):
        self._sandbox_conn: Optional[sqlite3.Connection] = None
        self._original_stats: Dict[str, Any] = {}
        self._simulation_count = 0
        self._history: List[Dict[str, Any]] = []
        self._state = self._load_state()

    # ── Snapshot ───────────────────────────────────────────────────────────────

    def snapshot(self) -> Dict[str, Any]:
        """Captura el estado actual del grafo real en el sandbox (:memory:).

        Usa SQLite ATTACH para copia nativa ultrarrápida (sin row-by-row).
        El grafo real no se toca.
        """
        t0 = time.time()
        try:
            # Sandbox en memoria
            self._sandbox_conn = sqlite3.connect(":memory:")
            self._sandbox_conn.execute("PRAGMA busy_timeout=30000")

            # Attach base de datos real (read-only)
            self._sandbox_conn.execute(
                f"ATTACH DATABASE ? AS real_db", (str(BRAIN_DB),)
            )

            # Copiar schema + datos en una sola operación SQL
            self._sandbox_conn.execute("""
                CREATE TABLE knowledge_nodes AS
                SELECT * FROM real_db.knowledge_nodes
            """)
            self._sandbox_conn.execute("""
                CREATE TABLE knowledge_edges AS
                SELECT * FROM real_db.knowledge_edges
            """)

            # Crear índices en sandbox
            self._sandbox_conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_sb_nodes_cat ON knowledge_nodes(category)"
            )
            self._sandbox_conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_sb_edges_from ON knowledge_edges(from_node)"
            )
            self._sandbox_conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_sb_edges_to ON knowledge_edges(to_node)"
            )

            # Desattachear (no tocamos el real)
            self._sandbox_conn.execute("DETACH DATABASE real_db")
            self._sandbox_conn.commit()

            # Stats originales
            self._original_stats = self._compute_stats(self._sandbox_conn)

            elapsed = time.time() - t0
            log.info("Sandbox snapshot: %d nodos, %d aristas en %.1fs",
                     self._original_stats["nodes"], self._original_stats["edges"], elapsed)

            return {
                "status": "ok",
                "nodes": self._original_stats["nodes"],
                "edges": self._original_stats["edges"],
                "elapsed_s": round(elapsed, 3),
            }

        except Exception as e:
            log.error("Sandbox snapshot: %s", e)
            return {"status": "error", "reason": str(e)[:200]}

    # ── Mutación ───────────────────────────────────────────────────────────────

    def mutate(self, mutation_type: str = "add_noise",
               intensity: float = 0.01) -> Dict[str, Any]:
        """Aplica un operador de mutación al grafo en sandbox.

        Args:
            mutation_type: tipo de mutación (add_noise, rewire, aggressive_prune, etc.)
            intensity: 0.0-1.0, qué tan agresiva es la mutación
        """
        if not self._sandbox_conn:
            return {"status": "error", "reason": "no hay snapshot — ejecuta snapshot() primero"}

        t0 = time.time()
        result = {
            "mutation_type": mutation_type,
            "intensity": intensity,
            "changes": {},
        }

        try:
            if mutation_type == "add_noise":
                result["changes"] = self._mutate_add_noise(intensity)
            elif mutation_type == "rewire":
                result["changes"] = self._mutate_rewire(intensity)
            elif mutation_type == "aggressive_prune":
                result["changes"] = self._mutate_aggressive_prune(intensity)
            elif mutation_type == "boost_novelty":
                result["changes"] = self._mutate_boost_novelty(intensity)
            elif mutation_type == "weaken_hub":
                result["changes"] = self._mutate_weaken_hub(intensity)
            elif mutation_type == "consolidate_strong":
                result["changes"] = self._mutate_consolidate_strong(intensity)
            else:
                return {"status": "error", "reason": f"mutación desconocida: {mutation_type}"}

            self._sandbox_conn.commit()
            result["status"] = "ok"
            result["elapsed_s"] = round(time.time() - t0, 3)

        except Exception as e:
            log.debug("mutate %s: %s", mutation_type, e)
            result["status"] = "error"
            result["reason"] = str(e)[:200]

        return result

    def _mutate_add_noise(self, intensity: float) -> Dict[str, Any]:
        """Añade aristas aleatorias entre nodos no conectados."""
        conn = self._sandbox_conn
        n_add = max(1, int(100 * intensity))  # máximo ~100 aristas por ciclo

        import random
        # Tomar muestra de nodos aleatorios
        nodes = [r[0] for r in conn.execute(
            "SELECT id FROM knowledge_nodes ORDER BY RANDOM() LIMIT ?",
            (min(n_add * 3, 500),)).fetchall()]

        added = 0
        for _ in range(n_add):
            if len(nodes) < 2:
                break
            a, b = random.sample(nodes, 2)
            # Verificar unicidad con lookup puntual (no cargar todas las aristas)
            exists = conn.execute(
                "SELECT 1 FROM knowledge_edges WHERE from_node=? AND to_node=?",
                (a, b)).fetchone()
            if not exists:
                try:
                    conn.execute(
                        "INSERT INTO knowledge_edges (from_node, to_node, relation_type, strength) "
                        "VALUES (?,?,?,?)",
                        (a, b, "noise", 0.1 * intensity))
                    added += 1
                except Exception:
                    pass

        return {"edges_added": added}

    def _mutate_rewire(self, intensity: float) -> Dict[str, Any]:
        """Redirige aristas existentes a nodos vecinos aleatorios."""
        conn = self._sandbox_conn
        # Usar rowid para selección rápida (evita ORDER BY RANDOM() costoso)
        max_rowid = conn.execute(
            "SELECT MAX(rowid) FROM knowledge_edges").fetchone()[0] or 1
        n_rewire = max(1, int(200 * intensity))  # máx 200 por ciclo

        rewired = 0
        import random
        targets = [r[0] for r in conn.execute(
            "SELECT id FROM knowledge_nodes ORDER BY RANDOM() LIMIT ?",
            (min(n_rewire * 2, 500),)).fetchall()]

        for _ in range(n_rewire):
            rid = random.randint(1, max_rowid)
            edge = conn.execute(
                "SELECT from_node, to_node, relation_type FROM knowledge_edges WHERE rowid=?",
                (rid,)).fetchone()
            if edge and targets:
                new_target = random.choice([t for t in targets if t != edge[0]])
                conn.execute(
                    "UPDATE knowledge_edges SET to_node=?, relation_type='rewired' "
                    "WHERE rowid=?", (new_target, rid))
                rewired += 1

        return {"edges_rewired": rewired}

    def _mutate_aggressive_prune(self, intensity: float) -> Dict[str, Any]:
        """Poda más agresiva: elimina aristas con peso bajo."""
        conn = self._sandbox_conn
        threshold = 0.1 * (1 + intensity)
        max_prune = int(200 * intensity)  # máximo ~200 aristas por ciclo

        # Solo podar aristas débiles (limitado)
        cursor = conn.execute(
            "DELETE FROM knowledge_edges WHERE rowid IN "
            "(SELECT rowid FROM knowledge_edges WHERE strength < ? LIMIT ?)",
            (threshold, max_prune))
        pruned_edges = cursor.rowcount

        return {"edges_pruned": pruned_edges, "nodes_orphaned": 0}

    def _mutate_boost_novelty(self, intensity: float) -> Dict[str, Any]:
        """Refuerza conexiones entre categorías diferentes (novedad)."""
        conn = self._sandbox_conn
        # Encontrar pares de nodos de diferente categoría
        pairs = conn.execute(
            "SELECT a.id, b.id, a.category, b.category FROM knowledge_nodes a, knowledge_nodes b "
            "WHERE a.id < b.id AND a.category != b.category "
            "AND a.category != '' AND b.category != '' "
            "ORDER BY RANDOM() LIMIT ?",
            (int(100 * intensity),)).fetchall()

        boosted = 0
        for a_id, b_id, cat_a, cat_b in pairs:
            conn.execute(
                "INSERT OR IGNORE INTO knowledge_edges (from_node, to_node, relation_type, strength) "
                "VALUES (?,?,?,?)",
                (a_id, b_id, f"novelty_{cat_a}_{cat_b}", 0.5 * intensity))
            boosted += 1

        return {"novel_edges_boosted": boosted}

    def _mutate_weaken_hub(self, intensity: float) -> Dict[str, Any]:
        """Debilita nodos con demasiadas conexiones (anti-monopolio)."""
        conn = self._sandbox_conn
        # Identificar hubs (nodos con > 100 aristas)
        hubs = conn.execute(
            "SELECT node_id, COUNT(*) as degree FROM ("
            "  SELECT from_node as node_id FROM knowledge_edges "
            "  UNION ALL SELECT to_node FROM knowledge_edges"
            ") GROUP BY node_id HAVING degree > 100 ORDER BY degree DESC LIMIT ?",
            (int(50 * intensity),)).fetchall()

        weakened = 0
        for hub_id, degree in hubs:
            # Reducir peso de aristas del hub
            conn.execute(
                "UPDATE knowledge_edges SET strength = strength * 0.7 "
                "WHERE from_node=? OR to_node=?",
                (hub_id, hub_id))
            weakened += 1

        return {"hubs_weakened": weakened, "max_degree": hubs[0][1] if hubs else 0}

    def _mutate_consolidate_strong(self, intensity: float) -> Dict[str, Any]:
        """Refuerza las aristas más fuertes (>0.8)."""
        conn = self._sandbox_conn
        n_consolidate = int(200 * intensity)
        conn.execute(
            "UPDATE knowledge_edges SET strength = MIN(1.0, strength + 0.05) "
            "WHERE strength >= 0.8 LIMIT ?", (n_consolidate,))
        return {"edges_consolidated": n_consolidate}

    # ── Simulación ─────────────────────────────────────────────────────────────

    def simulate(self, cycles: int = 50,
                mutation_sequence: List[str] = None) -> Dict[str, Any]:
        """Ejecuta N ciclos de evolución en el sandbox.

        En cada ciclo:
        1. Aplica una mutación aleatoria (o de la secuencia dada)
        2. Registra métricas de salud del grafo
        3. Evalúa tendencia (convergencia, divergencia, caos)

        Retorna:
            {
                "cycles_completed": int,
                "initial_stats": {...},
                "final_stats": {...},
                "trajectory": [{cycle, nodes, edges, density, health}, ...],
                "converged": bool,
                "recommendation": str,
            }
        """
        if not self._sandbox_conn:
            return {"status": "error", "reason": "no hay snapshot — ejecuta snapshot() primero"}

        t0 = time.time()
        if mutation_sequence is None:
            import random
            mutation_sequence = [random.choice(MUTATION_TYPES) for _ in range(cycles)]

        trajectory = []
        initial = self._compute_stats(self._sandbox_conn)

        for cycle in range(cycles):
            mut_type = mutation_sequence[cycle % len(mutation_sequence)]
            intensity = 0.01 + (cycle / cycles) * 0.05  # intensidad creciente

            self.mutate(mut_type, intensity)

            checkpoint_interval = max(1, cycles // 5)  # muestrear ~5 puntos
            if cycle % checkpoint_interval == 0 or cycle == cycles - 1:
                stats = self._compute_stats(self._sandbox_conn)
                trajectory.append({
                    "cycle": cycle,
                    "mutation": mut_type,
                    "nodes": stats["nodes"],
                    "edges": stats["edges"],
                    "density": stats["density"],
                    "health": stats["health_score"],
                })

        final = self._compute_stats(self._sandbox_conn)

        # Evaluar convergencia
        evaluation = self._evaluate_trajectory(initial, final, trajectory)
        elapsed = time.time() - t0

        result = {
            "status": "ok",
            "cycles_completed": cycles,
            "initial_stats": initial,
            "final_stats": final,
            "trajectory": trajectory,
            "converged": evaluation["converged"],
            "diverged": evaluation["diverged"],
            "stabilized": evaluation["stabilized"],
            "recommendation": evaluation["recommendation"],
            "elapsed_s": round(elapsed, 3),
        }

        self._simulation_count += 1
        self._history.append({
            "ts": time.time(),
            "cycles": cycles,
            "converged": evaluation["converged"],
            "initial_nodes": initial["nodes"],
            "final_nodes": final["nodes"],
        })
        self._save_state()

        log.info("Sandbox simulate: %d ciclos → %s (nodes: %d→%d, edges: %d→%d)",
                 cycles, evaluation["recommendation"],
                 initial["nodes"], final["nodes"],
                 initial["edges"], final["edges"])

        return result

    def _evaluate_trajectory(self, initial: Dict, final: Dict,
                            trajectory: List[Dict]) -> Dict[str, Any]:
        """Evalúa si la simulación converge, diverge o se estabiliza."""
        if len(trajectory) < 2:
            return {"converged": True, "diverged": False, "stabilized": True,
                    "recommendation": "datos insuficientes"}

        # Calcular tendencia de salud
        health_start = trajectory[0]["health"]
        health_end = trajectory[-1]["health"]
        health_trend = health_end - health_start

        # Calcular cambio en número de nodos
        node_change = final["nodes"] - initial["nodes"]
        node_change_pct = node_change / max(1, initial["nodes"])

        # Calcular estabilidad (varianza de salud)
        healths = [t["health"] for t in trajectory]
        health_variance = sum((h - sum(healths)/len(healths))**2 for h in healths) / len(healths)

        converged = False
        diverged = False
        stabilized = False
        recommendation = ""

        if health_trend > 0.05 and abs(node_change_pct) < 0.2:
            converged = True
            recommendation = "CONVERGE: La mutación mejora la salud del grafo sin destruirlo. Seguro aplicar."
        elif health_trend < -0.1 or abs(node_change_pct) > 0.5:
            diverged = True
            recommendation = "DIVERGE: La mutación degrada el grafo. NO aplicar sin ajustes."
        elif health_variance < 0.01:
            stabilized = True
            recommendation = "STABLE: El grafo es resistente a esta mutación. Se puede aplicar con precaución."
        else:
            recommendation = "INCONCLUSIVE: Se necesitan más ciclos de simulación."

        return {
            "converged": converged,
            "diverged": diverged,
            "stabilized": stabilized,
            "health_trend": round(health_trend, 4),
            "node_change_pct": round(node_change_pct, 4),
            "health_variance": round(health_variance, 6),
            "recommendation": recommendation,
        }

    # ── Restore ────────────────────────────────────────────────────────────────

    def destroy_sandbox(self):
        """Destruye el sandbox y libera la memoria."""
        if self._sandbox_conn:
            self._sandbox_conn.close()
            self._sandbox_conn = None
            self._original_stats = {}

    # ── Stats internos ─────────────────────────────────────────────────────────

    def _compute_stats(self, conn: sqlite3.Connection) -> Dict[str, Any]:
        """Calcula métricas de salud del grafo (en sandbox o real)."""
        try:
            total_nodes = conn.execute("SELECT COUNT(*) FROM knowledge_nodes").fetchone()[0]
            total_edges = conn.execute("SELECT COUNT(*) FROM knowledge_edges").fetchone()[0]
            avg_weight = conn.execute(
                "SELECT AVG(strength) FROM knowledge_edges WHERE strength IS NOT NULL"
            ).fetchone()[0] or 0

            weak = conn.execute(
                "SELECT COUNT(*) FROM knowledge_edges WHERE strength < 0.05"
            ).fetchone()[0]
            strong = conn.execute(
                "SELECT COUNT(*) FROM knowledge_edges WHERE strength >= 0.85"
            ).fetchone()[0]

            density = total_edges / max(1, total_nodes)
            health = (1.0 - weak / max(1, total_edges)) * 0.7 + min(1.0, density / 5) * 0.3

            return {
                "nodes": total_nodes,
                "edges": total_edges,
                "density": round(density, 4),
                "avg_weight": round(avg_weight, 4),
                "weak_edges": weak,
                "strong_edges": strong,
                "health_score": round(health, 4),
            }
        except Exception as e:
            return {"error": str(e)[:100]}

    # ── Estado ─────────────────────────────────────────────────────────────────

    def _load_state(self) -> Dict[str, Any]:
        try:
            if SANDBOX_STATE.exists():
                return json.loads(SANDBOX_STATE.read_text())
        except Exception:
            pass
        return {"simulation_count": 0}

    def _save_state(self):
        try:
            SANDBOX_STATE.parent.mkdir(parents=True, exist_ok=True)
            SANDBOX_STATE.write_text(json.dumps({
                "simulation_count": self._simulation_count,
                "last_simulation": time.time(),
            }, indent=2))
        except Exception:
            pass

    def stats(self) -> Dict[str, Any]:
        return {
            "simulation_count": self._simulation_count,
            "has_snapshot": self._sandbox_conn is not None,
            "original_nodes": self._original_stats.get("nodes", 0),
            "history_size": len(self._history),
        }


# ── Singleton ─────────────────────────────────────────────────────────────────

_sandbox: Optional[GraphSandbox] = None


def get_graph_sandbox() -> GraphSandbox:
    global _sandbox
    if _sandbox is None:
        _sandbox = GraphSandbox()
    return _sandbox


# ── CLI ───────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    import argparse
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    p = argparse.ArgumentParser(description="EIDOS Graph Sandbox")
    p.add_argument("--snapshot", action="store_true", help="Crear snapshot del grafo")
    p.add_argument("--mutate", type=str, help="Tipo de mutación a aplicar")
    p.add_argument("--simulate", type=int, default=30, help="Número de ciclos")
    p.add_argument("--intensity", type=float, default=0.02)
    p.add_argument("--stats", action="store_true")
    args = p.parse_args()

    sandbox = get_graph_sandbox()

    if args.snapshot:
        result = sandbox.snapshot()
        print(json.dumps(result, indent=2, ensure_ascii=False))
    elif args.mutate:
        sandbox.snapshot()
        result = sandbox.mutate(args.mutate, args.intensity)
        print(json.dumps(result, indent=2, ensure_ascii=False))
    elif args.simulate:
        sandbox.snapshot()
        result = sandbox.simulate(cycles=args.simulate)
        print(json.dumps({
            "cycles": result.get("cycles_completed"),
            "initial": result.get("initial_stats"),
            "final": result.get("final_stats"),
            "converged": result.get("converged"),
            "diverged": result.get("diverged"),
            "recommendation": result.get("recommendation"),
            "elapsed_s": result.get("elapsed_s"),
        }, indent=2, ensure_ascii=False))
    elif args.stats:
        print(json.dumps(sandbox.stats(), indent=2, ensure_ascii=False))
    else:
        p.print_help()
