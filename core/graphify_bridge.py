"""
core/graphify_bridge.py — Puente Graphify→EIDOS (S76 Fase 0)

Analiza el código fuente de EIDOS con Graphify (tree-sitter, 25 lenguajes,
100% determinista, sin LLM) e inyecta la estructura real (clases, funciones,
imports, herencias) en el grafo neuronal.

Objetivo: pasar de "bolsa de conceptos por keyword overlap" a "mapa estructural
real del código". EIDOS sabrá qué función llama a qué, qué clase hereda de cuál,
qué módulo importa a qué otro.

Arquitectura:
  1. _run_graphify() → subprocess con venv de Graphify → JSON {nodes, edges}
  2. _map_node() / _map_edge() → transforma a formato EIDOS
  3. _persist_nodes() / _persist_edges() → INSERT OR IGNORE en SQLite
  4. _inject_to_memory() → inject_node() + inject_edge() en grafo activo
  5. analyze_and_inject() → orquesta todo, retorna {nodes_added, edges_added, errors}

Uso:
    from core.graphify_bridge import GraphifyBridge
    gb = GraphifyBridge()
    result = gb.analyze_and_inject(force=True)
    print(f"Inyectados {result['nodes_added']} nodos, {result['edges_added']} aristas")
"""

from __future__ import annotations

import json
import logging
import os
import sqlite3
import subprocess
import sys
import time
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

log = logging.getLogger("eidos.graphify_bridge")

# ── Constantes ─────────────────────────────────────────────────────────────────
EIDOS_CORE = Path.home() / "EIDOS" / "core"
EIDOS_ROOT = Path.home() / "EIDOS"
GRAPHIFY_DIR = Path.home() / "MIS PROGRAMAS" / "ANALIZADOR" / "graphify"
GRAPHIFY_VENV_PYTHON = GRAPHIFY_DIR / "venv" / "bin" / "python3"
BRAIN_DB = Path.home() / ".eidos" / "evolution_brain.db"

# Mapeo de tipos Graphify → categoría EIDOS
_TYPE_TO_CATEGORY = {
    "function": "eidos_function",
    "method": "eidos_method",
    "class": "eidos_class",
    "module": "eidos_module",
    "symbol": "eidos_symbol",
    "file": "eidos_file",
}

# Mapeo de relaciones Graphify → aristas EIDOS
_RELATION_WEIGHTS = {
    "calls": 0.85,
    "imports": 0.90,
    "imports_from": 0.90,
    "contains": 0.95,
    "inherits": 0.90,
    "defines_method": 0.92,
    "uses": 0.80,
}

# Tipos de arista que son estructurales (se preservan en build_graph)
STRUCTURAL_RELATIONS = list(_RELATION_WEIGHTS.keys()) + [
    "rationale_for",
    "implements",
    "overrides",
]


class GraphifyBridge:
    """Puente entre Graphify (análisis estático tree-sitter) y el grafo EIDOS."""

    def __init__(self, core_path: Optional[Path] = None):
        self.core_path = core_path or EIDOS_CORE
        self._node_map: Dict[str, str] = {}  # graphify_id → eidos_node_id
        self._errors: List[str] = []

    # ── Paso 1: Ejecutar Graphify ──────────────────────────────────────────────

    def _run_graphify(self, target: Optional[Path] = None) -> Dict[str, Any]:
        """Ejecuta Graphify sobre el directorio target y retorna {nodes, edges}.

        Usa el venv de Graphify para no contaminar el entorno de EIDOS.
        El script inline importa graphify, llama collect_files + extract,
        y vuelca el JSON a stdout.
        """
        target_path = target or self.core_path
        if not target_path.exists():
            raise FileNotFoundError(f"No existe: {target_path}")

        script = f'''
import json, sys
from pathlib import Path
sys.path.insert(0, "{GRAPHIFY_DIR}")
from graphify.extract import extract, collect_files
from core.db import get_conn

target = Path("{target_path}")
files = collect_files(target)
if not files:
    print(json.dumps({{"nodes": [], "edges": []}}))
    sys.exit(0)

result = extract(files, parallel=True)
# Convertir a formato simple para stdout
nodes = []
for n in result.get("nodes", []):
    nodes.append({{
        "id": n.get("id", ""),
        "label": n.get("label", ""),
        "type": n.get("type", ""),
        "source_file": str(n.get("source_file", "")),
        "source_location": str(n.get("source_location", "")),
    }})
edges = []
for e in result.get("edges", []):
    edges.append({{
        "source": e.get("source", ""),
        "target": e.get("target", ""),
        "relation": e.get("relation", ""),
        "weight": e.get("weight", 0.8),
    }})

print(json.dumps({{"nodes": nodes, "edges": edges}}))
'''

        try:
            proc = subprocess.run(
                [str(GRAPHIFY_VENV_PYTHON), "-c", script],
                capture_output=True,
                text=True,
                timeout=120,
                env={**os.environ, "PYTHONPATH": str(GRAPHIFY_DIR)},
            )
            if proc.returncode != 0:
                stderr = proc.stderr[:500]
                log.error("Graphify falló (rc=%d): %s", proc.returncode, stderr)
                self._errors.append(f"Graphify error: {stderr}")
                return {"nodes": [], "edges": []}

            # Extraer solo la última línea JSON (ignorar warnings de tree-sitter)
            lines = proc.stdout.strip().split("\n")
            json_line = lines[-1] if lines else "{}"
            data = json.loads(json_line)
            log.info(
                "Graphify extrajo: %d nodos, %d aristas",
                len(data.get("nodes", [])),
                len(data.get("edges", [])),
            )
            return data

        except subprocess.TimeoutExpired:
            log.error("Graphify timeout (>120s)")
            self._errors.append("Timeout en análisis Graphify")
            return {"nodes": [], "edges": []}
        except json.JSONDecodeError as e:
            log.error("Graphify devolvió JSON inválido: %s", e)
            self._errors.append(f"JSON inválido: {e}")
            return {"nodes": [], "edges": []}
        except Exception as e:
            log.error("Error ejecutando Graphify: %s", e)
            self._errors.append(str(e))
            return {"nodes": [], "edges": []}

    # ── Paso 2: Mapear nodos ───────────────────────────────────────────────────

    def _map_node(self, gf_node: Dict[str, Any]) -> Tuple[str, Dict[str, Any]]:
        """Transforma un nodo Graphify → fila EIDOS.

        Retorna (eidos_node_id, row_dict).
        """
        gf_id = gf_node.get("id", "")
        gf_type = gf_node.get("type", "symbol")
        gf_label = gf_node.get("label", gf_id)
        gf_file = gf_node.get("source_file", "")
        gf_loc = gf_node.get("source_location", "")

        # ID único con prefijo gfy: para evitar colisiones
        eidos_id = f"gfy:{gf_id}"

        # Concepto legible: [tipo] nombre (archivo:linea)
        tipo_es = {
            "function": "funcion",
            "method": "metodo",
            "class": "clase",
            "module": "modulo",
            "symbol": "simbolo",
            "file": "archivo",
        }.get(gf_type, gf_type)

        concept = f"[{tipo_es}] {gf_label}"
        if gf_file:
            concept += f" ({Path(gf_file).name})"

        # Definición con metadata estructural
        definition = f"{gf_type} definido en {gf_file}"
        if gf_loc:
            definition += f" linea {gf_loc}"

        category = _TYPE_TO_CATEGORY.get(gf_type, "code_structure")

        row = {
            "id": eidos_id,
            "concept": concept,
            "definition": definition,
            "category": category,
            "confidence": 0.90,
            "source": "graphify",
            "agent_id": "graphify_bridge",
            "verified": 0,
        }

        self._node_map[gf_id] = eidos_id
        return eidos_id, row

    # ── Paso 3: Mapear aristas ─────────────────────────────────────────────────

    def _map_edge(
        self, gf_edge: Dict[str, Any]
    ) -> Optional[Tuple[str, str, str, float]]:
        """Transforma una arista Graphify → (from_eidos_id, to_eidos_id, relation_type, strength).

        Retorna None si source o target no están en el node_map.
        """
        gf_source = gf_edge.get("source", "")
        gf_target = gf_edge.get("target", "")
        gf_relation = gf_edge.get("relation", "related")
        gf_weight = gf_edge.get("weight", 0.8)

        src_id = self._node_map.get(gf_source)
        tgt_id = self._node_map.get(gf_target)

        if not src_id or not tgt_id:
            return None

        strength = _RELATION_WEIGHTS.get(gf_relation, float(gf_weight) if gf_weight else 0.75)
        return (src_id, tgt_id, gf_relation, min(strength, 1.0))

    # ── Paso 4: Persistir en SQLite ────────────────────────────────────────────

    def _ensure_columns(self, conn: sqlite3.Connection) -> None:
        """Asegura que las columnas necesarias existan (migraciones incrementales)."""
        try:
            conn.execute("ALTER TABLE knowledge_nodes ADD COLUMN agent_id TEXT")
        except sqlite3.OperationalError:
            pass  # Ya existe
        try:
            conn.execute("ALTER TABLE knowledge_nodes ADD COLUMN verified INTEGER DEFAULT 0")
        except sqlite3.OperationalError:
            pass

    def _persist_nodes(self, nodes: List[Dict[str, Any]]) -> int:
        """Inserta nodos en knowledge_nodes. Usa INSERT OR IGNORE para no duplicar."""
        if not nodes:
            return 0

        conn = get_conn(BRAIN_DB, timeout=10)
        self._ensure_columns(conn)

        added = 0
        batch = []
        for row in nodes:
            batch.append((
                row["id"],
                row["concept"],
                row["definition"],
                row["category"],
                row["confidence"],
                row["source"],
            ))

            if len(batch) >= 1000:
                conn.executemany(
                    "INSERT OR IGNORE INTO knowledge_nodes(id, concept, definition, category, confidence, source) "
                    "VALUES(?, ?, ?, ?, ?, ?)",
                    batch,
                )
                added += conn.total_changes
                batch = []

        if batch:
            conn.executemany(
                "INSERT OR IGNORE INTO knowledge_nodes(id, concept, definition, category, confidence, source) "
                "VALUES(?, ?, ?, ?, ?, ?)",
                batch,
            )
            added += conn.total_changes

        conn.commit()
        pass  # S109: get_conn no necesita close()
        log.info("Persistidos %d nodos estructurales en SQLite", added)
        return added

    def _persist_edges(self, edges: List[Tuple[str, str, str, float]]) -> int:
        """Inserta aristas estructurales en knowledge_edges."""
        if not edges:
            return 0

        conn = get_conn(BRAIN_DB, timeout=10)
        added = 0
        batch = []
        for from_id, to_id, rel_type, strength in edges:
            batch.append((from_id, to_id, rel_type, strength))

            if len(batch) >= 1000:
                conn.executemany(
                    "INSERT OR IGNORE INTO knowledge_edges(from_node, to_node, relation_type, strength) "
                    "VALUES(?, ?, ?, ?)",
                    batch,
                )
                added += conn.total_changes
                batch = []

        if batch:
            conn.executemany(
                "INSERT OR IGNORE INTO knowledge_edges(from_node, to_node, relation_type, strength) "
                "VALUES(?, ?, ?, ?)",
                batch,
            )
            added += conn.total_changes

        conn.commit()
        pass  # S109: get_conn no necesita close()
        log.info("Persistidas %d aristas estructurales en SQLite", added)
        return added

    # ── Paso 5: Inyectar en memoria ────────────────────────────────────────────

    def _inject_to_memory(self, nodes: List[Dict[str, Any]], edges: List[Tuple[str, str, str, float]]) -> int:
        """Inyecta nodos y aristas en el grafo activo via KnowledgeReasoner."""
        try:
            from core.knowledge_reasoner import get_reasoner
            reasoner = get_reasoner()
            if not reasoner or not reasoner.graph or not reasoner.graph.nodes:
                log.warning("Reasoner no disponible, omitiendo inyección en memoria")
                return 0

            injected = 0
            for row in nodes:
                try:
                    if reasoner.inject_node(row["id"], row["concept"], {
                        "definition": row.get("definition", ""),
                        "category": row.get("category", ""),
                        "source": "graphify",
                        "confidence": row.get("confidence", 0.9),
                    }):
                        injected += 1
                except Exception:
                    pass

            edge_count = 0
            for src_id, tgt_id, rel_type, strength in edges:
                try:
                    # inject_edge usa conceptos, no IDs — usamos get_node_by_concept
                    src_node = reasoner.graph.nodes.get(src_id)
                    tgt_node = reasoner.graph.nodes.get(tgt_id)
                    if src_node and tgt_node:
                        from core.knowledge_reasoner import RelationEdge
                        reasoner.graph.add_edge(RelationEdge(
                            source_id=src_id, target_id=tgt_id,
                            rel_type=rel_type, weight=strength,
                        ))
                        edge_count += 1
                except Exception:
                    pass

            log.info("Inyectados en memoria: %d nodos, %d aristas", injected, edge_count)
            return injected
        except ImportError:
            log.warning("knowledge_reasoner no disponible en este momento")
            return 0
        except Exception as e:
            log.error("Error inyectando en memoria: %s", e)
            return 0

    # ── Orquestador ────────────────────────────────────────────────────────────

    def analyze_and_inject(self, force: bool = False, target: Optional[Path] = None) -> Dict[str, Any]:
        """Ejecuta el pipeline completo: Graphify → mapeo → SQLite → memoria.

        Args:
            force: Si True, re-ejecuta aunque ya haya datos de graphify en la DB.
            target: Directorio a analizar (default: ~/EIDOS/core/).

        Returns:
            {"nodes_added": int, "edges_added": int, "errors": [str], "elapsed_s": float}
        """
        t0 = time.time()
        self._errors = []
        self._node_map = {}

        target_path = target or self.core_path
        log.info("GraphifyBridge: analizando %s (force=%s)", target_path, force)

        # Verificar si ya hay datos frescos (si no es force)
        if not force:
            try:
                conn = get_conn(BRAIN_DB, timeout=5)
                count = conn.execute(
                    "SELECT COUNT(*) FROM knowledge_nodes WHERE source='graphify'"
                ).fetchone()[0]
                pass  # S109: get_conn no necesita close()
                if count > 100:
                    log.info("Ya hay %d nodos graphify. Usa force=True para reanalizar.", count)
                    return {
                        "nodes_added": 0,
                        "edges_added": 0,
                        "errors": [],
                        "elapsed_s": time.time() - t0,
                        "cached": True,
                        "existing_nodes": count,
                    }
            except Exception:
                pass

        # 1. Ejecutar Graphify
        log.info("Paso 1/5: Ejecutando Graphify tree-sitter...")
        data = self._run_graphify(target_path)
        gf_nodes = data.get("nodes", [])
        gf_edges = data.get("edges", [])

        if not gf_nodes:
            log.warning("Graphify no encontró nodos en %s", target_path)
            return {
                "nodes_added": 0,
                "edges_added": 0,
                "errors": self._errors,
                "elapsed_s": time.time() - t0,
            }

        # 2. Mapear nodos
        log.info("Paso 2/5: Mapeando %d nodos Graphify → EIDOS...", len(gf_nodes))
        eidos_nodes = []
        for gf_node in gf_nodes:
            eidos_id, row = self._map_node(gf_node)
            eidos_nodes.append(row)

        # 3. Mapear aristas
        log.info("Paso 3/5: Mapeando %d aristas...", len(gf_edges))
        eidos_edges = []
        for gf_edge in gf_edges:
            mapped = self._map_edge(gf_edge)
            if mapped:
                eidos_edges.append(mapped)

        # 4. Persistir en SQLite
        log.info("Paso 4/5: Persistiendo en SQLite...")
        nodes_added = self._persist_nodes(eidos_nodes)
        edges_added = self._persist_edges(eidos_edges)

        # 5. Inyectar en memoria
        log.info("Paso 5/5: Inyectando en memoria activa...")
        mem_injected = self._inject_to_memory(eidos_nodes, eidos_edges)

        elapsed = time.time() - t0
        log.info(
            "GraphifyBridge completado en %.1fs: +%d nodos, +%d aristas, %d errores",
            elapsed, nodes_added, edges_added, len(self._errors),
        )

        return {
            "nodes_added": nodes_added,
            "edges_added": edges_added,
            "memory_injected": mem_injected,
            "errors": self._errors,
            "elapsed_s": elapsed,
            "cached": False,
        }

    # ── Consulta ───────────────────────────────────────────────────────────────

    def query_code_structure(self, concept_filter: str = "", limit: int = 50) -> List[Dict[str, Any]]:
        """Consulta nodos estructurales en SQLite."""
        conn = get_conn(BRAIN_DB, timeout=5)
        conn.row_factory = sqlite3.Row
        if concept_filter:
            rows = conn.execute(
                "SELECT * FROM knowledge_nodes WHERE source='graphify' AND concept LIKE ? LIMIT ?",
                (f"%{concept_filter}%", limit),
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT * FROM knowledge_nodes WHERE source='graphify' LIMIT ?",
                (limit,),
            ).fetchall()
        pass  # S109: get_conn no necesita close()
        return [dict(r) for r in rows]

    def get_stats(self) -> Dict[str, Any]:
        """Estadísticas de nodos/aristas graphify en la DB."""
        conn = get_conn(BRAIN_DB, timeout=5)
        node_count = conn.execute(
            "SELECT COUNT(*) FROM knowledge_nodes WHERE source='graphify'"
        ).fetchone()[0]
        edge_count = conn.execute(
            "SELECT COUNT(*) FROM knowledge_edges WHERE relation_type IN "
            f"({','.join('?' * len(STRUCTURAL_RELATIONS))})",
            STRUCTURAL_RELATIONS,
        ).fetchone()[0]
        # Desglose por tipo
        type_counts = {}
        for row in conn.execute(
            "SELECT category, COUNT(*) as cnt FROM knowledge_nodes "
            "WHERE source='graphify' GROUP BY category ORDER BY cnt DESC"
        ).fetchall():
            type_counts[row[0]] = row[1]
        pass  # S109: get_conn no necesita close()
        return {
            "nodes": node_count,
            "edges": edge_count,
            "by_category": type_counts,
        }


# ── Singleton ──────────────────────────────────────────────────────────────────

_graphify_bridge: Optional[GraphifyBridge] = None


def get_graphify_bridge() -> GraphifyBridge:
    """Retorna la instancia singleton de GraphifyBridge."""
    global _graphify_bridge
    if _graphify_bridge is None:
        _graphify_bridge = GraphifyBridge()
    return _graphify_bridge


# ── CLI rápido ─────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    import argparse
    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")

    parser = argparse.ArgumentParser(description="Graphify Bridge — Análisis estructural de código")
    parser.add_argument("--force", action="store_true", help="Forzar reanálisis")
    parser.add_argument("--target", type=str, default=None, help="Directorio a analizar")
    parser.add_argument("--stats", action="store_true", help="Mostrar estadísticas")
    parser.add_argument("--query", type=str, default="", help="Filtrar nodos por concepto")
    args = parser.parse_args()

    gb = GraphifyBridge()

    if args.stats:
        stats = gb.get_stats()
        print(json.dumps(stats, indent=2, ensure_ascii=False))
    elif args.query:
        results = gb.query_code_structure(args.query)
        for r in results:
            print(f"  {r['concept']}")
            print(f"    {r['definition']}")
            print()
    else:
        target = Path(args.target) if args.target else None
        result = gb.analyze_and_inject(force=args.force, target=target)
        print(json.dumps(result, indent=2, ensure_ascii=False))
