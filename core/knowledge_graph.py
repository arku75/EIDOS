#!/usr/bin/env python3
"""
EIDOS core/knowledge_graph.py — Knowledge Graph (LEGACY)
==========================================================
**LEGACY**: Este módulo usaba originalmente ~/.eidos/knowledge_graph.db (tablas `nodes`, `edges`).
La base de datos autoritativa AHORA es **~/.eidos/evolution_brain.db** (tablas `knowledge_nodes`,
`knowledge_edges`), gestionada por `core/db.py` vía `get_conn()`.

knowledge_graph.db se mantiene solo como respaldo histórico. NO escribir en ella.
Toda escritura/lectura nueva debe ir contra evolution_brain.db.

Grafo de conocimiento relacional para EIDOS.

Modela entidades (nodos) y relaciones (aristas) entre conceptos,
herramientas, skills, agentes, archivos, modulos y APIs.

Backend: SQLite en ~/.eidos/evolution_brain.db (tablas knowledge_nodes, knowledge_edges)
Visualizacion: export_dot() (Graphviz), export_json() (D3.js)
Inferencia: dependencias transitivas, PageRank simplificado
Auto-populate: ingest_from_codebase() analiza imports Python

Inspirado en AutoResearchClaw: construir mapa de conocimiento
que EIDOS pueda navegar para entender su propio ecosistema.

Uso:
    from core.knowledge_graph import KnowledgeGraph

    kg = KnowledgeGraph()
    kg.add_node("memory_vec", "module", {"desc": "Memoria vectorial"})
    kg.add_node("sqlite3", "tool", {"desc": "Base de datos"})
    kg.add_edge("memory_vec", "sqlite3", "depends_on")

    path = kg.find_path("memory_vec", "sqlite3")
    neighbors = kg.get_neighbors("memory_vec", depth=2)
    important = kg.pagerank(top_n=10)

    kg.export_dot("/tmp/eidos_graph.dot")
    kg.export_json("/tmp/eidos_graph.json")
"""
from __future__ import annotations

import ast
import json
import math
import os
import re
import sqlite3
import threading
import time
from collections import defaultdict, deque
from pathlib import Path
from typing import Optional, List, Dict, Any, Set, Tuple
from core.db import get_conn

# ══════════════════════════════════════════════════════════════════════════════
# Configuracion
# ══════════════════════════════════════════════════════════════════════════════

DB_PATH = Path.home() / ".eidos" / "evolution_brain.db"

VALID_NODE_TYPES = {"concept", "tool", "skill", "agent", "file", "module", "api"}
VALID_EDGE_TYPES = {"depends_on", "uses", "produces", "related_to", "part_of", "implements"}


# ══════════════════════════════════════════════════════════════════════════════
# Knowledge Graph
# ══════════════════════════════════════════════════════════════════════════════

class KnowledgeGraph:
    """
    Grafo de conocimiento relacional con SQLite backend.

    Nodos = entidades tipadas (concept, tool, skill, agent, file, module, api)
    Aristas = relaciones tipadas (depends_on, uses, produces, related_to, part_of, implements)
    """

    def __init__(self, db_path: str = None, verbose: bool = True):
        self.db_path = db_path or str(DB_PATH)
        self.verbose = verbose
        self._lock = threading.Lock()

        Path(self.db_path).parent.mkdir(parents=True, exist_ok=True)
        self.conn = get_conn(self.db_path, check_same_thread=False)
        self.conn.execute("PRAGMA journal_mode=WAL")
        self.conn.execute("PRAGMA foreign_keys=ON")

        self._init_db()
        self._log(f"Inicializado: {self.count_nodes()} nodos, {self.count_edges()} aristas")

    def _log(self, msg: str):
        if self.verbose:
            print(f"🕸️ [KnowledgeGraph] {msg}")

    def _init_db(self):
        """Verifica que las tablas knowledge_nodes y knowledge_edges existen."""
        tables = self.conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name IN ('knowledge_nodes', 'knowledge_edges')"
        ).fetchall()
        existing = {r[0] for r in tables}

        if "knowledge_nodes" not in existing:
            raise RuntimeError(
                "Tabla knowledge_nodes no encontrada en evolution_brain.db. "
                "Ejecuta primero las migraciones SQL."
            )
        if "knowledge_edges" not in existing:
            raise RuntimeError(
                "Tabla knowledge_edges no encontrada en evolution_brain.db. "
                "Ejecuta primero las migraciones SQL."
            )

        # Verificar columnas minimas necesarias
        node_cols = {r[1] for r in self.conn.execute("PRAGMA table_info(knowledge_nodes)")}
        for col in ("id", "concept", "category", "definition", "created_at"):
            if col not in node_cols:
                raise RuntimeError(f"Columna requerida knowledge_nodes.{col} no encontrada")

        edge_cols = {r[1] for r in self.conn.execute("PRAGMA table_info(knowledge_edges)")}
        for col in ("id", "from_node", "to_node", "relation_type", "strength"):
            if col not in edge_cols:
                raise RuntimeError(f"Columna requerida knowledge_edges.{col} no encontrada")

    # ──────────────────────────────────────────────────────────────────────
    # CRUD Nodos
    # ──────────────────────────────────────────────────────────────────────

    def add_node(self, name: str, node_type: str, metadata: Dict[str, Any] = None) -> bool:
        """
        Anade un nodo al grafo.

        Args:
            name: Identificador unico del nodo (se almacena en knowledge_nodes.concept)
            node_type: Tipo (concept, tool, skill, agent, file, module, api)
            metadata: Datos adicionales (se serializa en knowledge_nodes.definition)

        Returns:
            True si se creo, False si ya existia (se actualiza)
        """
        if node_type not in VALID_NODE_TYPES:
            self._log(f"⚠️ Tipo de nodo invalido: {node_type}")
            return False

        metadata = metadata or {}
        now = time.time()

        with self._lock:
            existing = self.conn.execute(
                "SELECT id FROM knowledge_nodes WHERE concept = ?", (name,)
            ).fetchone()

            if existing:
                self.conn.execute(
                    "UPDATE knowledge_nodes SET category = ?, definition = ?, updated_at = ? WHERE concept = ?",
                    (node_type, json.dumps(metadata), now, name)
                )
                self.conn.commit()
                return False
            else:
                self.conn.execute(
                    "INSERT INTO knowledge_nodes (concept, category, definition, created_at, updated_at) VALUES (?, ?, ?, ?, ?)",
                    (name, node_type, json.dumps(metadata), now, now)
                )
                self.conn.commit()
                return True

    def remove_node(self, name: str) -> bool:
        """Elimina un nodo y todas sus aristas."""
        with self._lock:
            # Obtener UUID del nodo
            row = self.conn.execute(
                "SELECT id FROM knowledge_nodes WHERE concept = ?", (name,)
            ).fetchone()
            if not row:
                return False
            node_id = row[0]

            # Eliminar aristas primero (no hay ON DELETE CASCADE en el schema)
            self.conn.execute(
                "DELETE FROM knowledge_edges WHERE from_node = ? OR to_node = ?",
                (node_id, node_id)
            )
            cur = self.conn.execute("DELETE FROM knowledge_nodes WHERE concept = ?", (name,))
            self.conn.commit()
            removed = cur.rowcount > 0

        if removed:
            self._log(f"🗑️ Nodo eliminado: {name}")
        return removed

    def get_node(self, name: str) -> Optional[Dict[str, Any]]:
        """Obtiene un nodo por nombre (concept)."""
        row = self.conn.execute(
            "SELECT concept, category, definition, created_at, updated_at FROM knowledge_nodes WHERE concept = ?",
            (name,)
        ).fetchone()

        if not row:
            return None

        return {
            "name": row[0],
            "node_type": row[1],
            "metadata": self._parse_definition(row[2]),
            "created_at": row[3],
            "updated_at": row[4],
        }

    @staticmethod
    def _parse_definition(definition: str) -> Dict[str, Any]:
        """Parsea definition de knowledge_nodes, manejando JSON y texto plano."""
        if not definition:
            return {}
        try:
            parsed = json.loads(definition)
            if isinstance(parsed, dict):
                return parsed
            return {"desc": str(parsed)}
        except (json.JSONDecodeError, TypeError):
            return {"desc": str(definition)}

    def merge_nodes(self, name_a: str, name_b: str, merged_name: str = None) -> bool:
        """
        Fusiona dos nodos en uno. Las aristas de ambos se transfieren al nodo resultante.
        Adaptado al schema knowledge_nodes/knowledge_edges con UUIDs (refactor ultraplan).
        """
        if merged_name is None:
            merged_name = name_a

        with self._lock:
            # Fase 0: obtener UUIDs
            uuid_a = self.conn.execute(
                "SELECT id FROM knowledge_nodes WHERE concept = ?", (name_a,)
            ).fetchone()
            uuid_b = self.conn.execute(
                "SELECT id FROM knowledge_nodes WHERE concept = ?", (name_b,)
            ).fetchone()

            if not uuid_a or not uuid_b:
                self._log(f"⚠️ merge_nodes: nodo no encontrado ({name_a}={bool(uuid_a)}, {name_b}={bool(uuid_b)})")
                return False
            uuid_a, uuid_b = uuid_a[0], uuid_b[0]

            # Obtener UUID del merged (puede ser nuevo o existente)
            if merged_name == name_a:
                uuid_merged = uuid_a
            elif merged_name == name_b:
                uuid_merged = uuid_b
            else:
                existing = self.conn.execute(
                    "SELECT id FROM knowledge_nodes WHERE concept = ?", (merged_name,)
                ).fetchone()
                if existing:
                    uuid_merged = existing[0]
                else:
                    # Crear nodo merged con metadata del nodo A
                    node_a = self.get_node(name_a)
                    node_b = self.get_node(name_b)
                    merged_meta = {}
                    if node_a and node_a.get("metadata"):
                        merged_meta.update(node_a["metadata"] if isinstance(node_a["metadata"], dict) else {})
                    if node_b and node_b.get("metadata"):
                        merged_meta.update(node_b["metadata"] if isinstance(node_b["metadata"], dict) else {})
                    now = time.time()
                    self.conn.execute(
                        "INSERT INTO knowledge_nodes (concept, category, definition, created_at, source) "
                        "VALUES (?, ?, ?, ?, ?)",
                        (merged_name, node_a.get("node_type","concept") if node_a else "concept",
                         json.dumps(merged_meta), now, "merge")
                    )
                    uuid_merged = self.conn.execute(
                        "SELECT id FROM knowledge_nodes WHERE concept = ?", (merged_name,)
                    ).fetchone()[0]

            # Fase 1: Reasignar aristas de B → merged
            if uuid_merged != uuid_b:
                self.conn.execute(
                    "UPDATE knowledge_edges SET from_node = ? WHERE from_node = ?",
                    (uuid_merged, uuid_b)
                )
                self.conn.execute(
                    "UPDATE knowledge_edges SET to_node = ? WHERE to_node = ?",
                    (uuid_merged, uuid_b)
                )

            # Fase 2: Reasignar aristas de A → merged (si A != merged)
            if uuid_merged != uuid_a:
                self.conn.execute(
                    "UPDATE knowledge_edges SET from_node = ? WHERE from_node = ?",
                    (uuid_merged, uuid_a)
                )
                self.conn.execute(
                    "UPDATE knowledge_edges SET to_node = ? WHERE to_node = ?",
                    (uuid_merged, uuid_a)
                )

            # Fase 3: Eliminar nodos viejos (solo los que no son merged)
            if uuid_merged != uuid_b:
                self.conn.execute("DELETE FROM knowledge_nodes WHERE id = ?", (uuid_b,))
            if uuid_merged != uuid_a:
                self.conn.execute("DELETE FROM knowledge_nodes WHERE id = ?", (uuid_a,))

            # Fase 4: Limpiar self-loops
            self.conn.execute("DELETE FROM knowledge_edges WHERE from_node = to_node")

            self.conn.commit()
            self._log(f"🔀 Nodos fusionados: {name_a} + {name_b} → {merged_name}")
            return True

    # ──────────────────────────────────────────────────────────────────────
    # CRUD Aristas
    # ──────────────────────────────────────────────────────────────────────

    def add_edge(self, source: str, target: str, edge_type: str,
                 weight: float = 1.0, metadata: Dict[str, Any] = None) -> bool:
        """
        Anade una arista (relacion) entre dos nodos.

        Args:
            source: Nodo origen (concept name)
            target: Nodo destino (concept name)
            edge_type: Tipo de relacion
            weight: Peso de la relacion (1.0 por defecto)
            metadata: Datos adicionales

        Returns:
            True si se creo la arista
        """
        if edge_type not in VALID_EDGE_TYPES:
            self._log(f"⚠️ Tipo de arista invalido: {edge_type}")
            return False

        # Auto-crear nodos si no existen
        if not self.get_node(source):
            self.add_node(source, "concept")
        if not self.get_node(target):
            self.add_node(target, "concept")

        metadata = metadata or {}
        now = time.time()

        with self._lock:
            # Obtener UUIDs de los concept names
            src_row = self.conn.execute(
                "SELECT id FROM knowledge_nodes WHERE concept = ?", (source,)
            ).fetchone()
            tgt_row = self.conn.execute(
                "SELECT id FROM knowledge_nodes WHERE concept = ?", (target,)
            ).fetchone()

            if not src_row or not tgt_row:
                self._log(f"⚠️ No se pudo crear arista: nodos no encontrados tras auto-creacion")
                return False

            src_id = src_row[0]
            tgt_id = tgt_row[0]

            try:
                self.conn.execute(
                    "INSERT INTO knowledge_edges (from_node, to_node, relation_type, strength, metadata, created_at) "
                    "VALUES (?, ?, ?, ?, ?, ?)",
                    (src_id, tgt_id, edge_type, weight, json.dumps(metadata), now)
                )
                self.conn.commit()
                return True
            except sqlite3.IntegrityError:
                return False

    def remove_edge(self, source: str, target: str, edge_type: str = None) -> bool:
        """Elimina una arista. Si edge_type es None, elimina todas entre source y target."""
        with self._lock:
            # Obtener UUIDs
            src_row = self.conn.execute(
                "SELECT id FROM knowledge_nodes WHERE concept = ?", (source,)
            ).fetchone()
            tgt_row = self.conn.execute(
                "SELECT id FROM knowledge_nodes WHERE concept = ?", (target,)
            ).fetchone()

            if not src_row or not tgt_row:
                return False

            src_id = src_row[0]
            tgt_id = tgt_row[0]

            if edge_type:
                cur = self.conn.execute(
                    "DELETE FROM knowledge_edges WHERE from_node = ? AND to_node = ? AND relation_type = ?",
                    (src_id, tgt_id, edge_type)
                )
            else:
                cur = self.conn.execute(
                    "DELETE FROM knowledge_edges WHERE from_node = ? AND to_node = ?",
                    (src_id, tgt_id)
                )
            self.conn.commit()
            return cur.rowcount > 0

    def get_edges(self, node: str, direction: str = "both") -> List[Dict[str, Any]]:
        """
        Obtiene aristas de un nodo.

        Args:
            node: Nombre del nodo (concept)
            direction: 'outgoing', 'incoming', o 'both'
        """
        results = []

        if direction in ("outgoing", "both"):
            rows = self.conn.execute(
                "SELECT kn1.concept, kn2.concept, ke.relation_type, ke.strength, ke.metadata "
                "FROM knowledge_edges ke "
                "JOIN knowledge_nodes kn1 ON ke.from_node = kn1.id "
                "JOIN knowledge_nodes kn2 ON ke.to_node = kn2.id "
                "WHERE kn1.concept = ?",
                (node,)
            ).fetchall()
            for r in rows:
                results.append({
                    "source": r[0], "target": r[1], "edge_type": r[2],
                    "weight": r[3], "metadata": self._parse_definition(r[4])
                })

        if direction in ("incoming", "both"):
            rows = self.conn.execute(
                "SELECT kn1.concept, kn2.concept, ke.relation_type, ke.strength, ke.metadata "
                "FROM knowledge_edges ke "
                "JOIN knowledge_nodes kn1 ON ke.from_node = kn1.id "
                "JOIN knowledge_nodes kn2 ON ke.to_node = kn2.id "
                "WHERE kn2.concept = ?",
                (node,)
            ).fetchall()
            for r in rows:
                results.append({
                    "source": r[0], "target": r[1], "edge_type": r[2],
                    "weight": r[3], "metadata": self._parse_definition(r[4])
                })

        return results

    # ──────────────────────────────────────────────────────────────────────
    # Queries
    # ──────────────────────────────────────────────────────────────────────

    def get_neighbors(self, node: str, depth: int = 1, edge_type: str = None) -> Dict[str, Any]:
        """
        Obtiene vecinos de un nodo hasta profundidad N.

        Returns:
            Dict con nodos y aristas encontrados
        """
        visited: Set[str] = set()
        found_nodes: List[Dict] = []
        found_edges: List[Dict] = []
        queue: deque = deque([(node, 0)])
        visited.add(node)

        while queue:
            current, current_depth = queue.popleft()
            if current_depth >= depth:
                continue

            edges = self.get_edges(current)

            for edge in edges:
                found_edges.append(edge)
                neighbor = edge["target"] if edge["source"] == current else edge["source"]

                if edge_type and edge["edge_type"] != edge_type:
                    continue

                if neighbor not in visited:
                    visited.add(neighbor)
                    n = self.get_node(neighbor)
                    if n:
                        n["depth"] = current_depth + 1
                        found_nodes.append(n)
                    queue.append((neighbor, current_depth + 1))

        return {
            "center": node,
            "depth": depth,
            "nodes": found_nodes,
            "edges": found_edges,
            "total_nodes": len(found_nodes),
        }

    def find_path(self, source: str, target: str) -> Optional[List[str]]:
        """
        Encuentra el camino mas corto entre dos nodos (BFS).

        Returns:
            Lista de nodos en el camino, o None si no hay camino
        """
        if source == target:
            return [source]

        visited: Set[str] = {source}
        queue: deque = deque([(source, [source])])

        while queue:
            current, path = queue.popleft()
            edges = self.get_edges(current)

            for edge in edges:
                neighbor = edge["target"] if edge["source"] == current else edge["source"]

                if neighbor == target:
                    return path + [neighbor]

                if neighbor not in visited:
                    visited.add(neighbor)
                    queue.append((neighbor, path + [neighbor]))

        return None  # No hay camino

    def find_clusters(self) -> List[List[str]]:
        """
        Encuentra clusters (componentes conexos) en el grafo.

        Returns:
            Lista de listas, cada una con los nodos de un cluster
        """
        all_nodes = [r[0] for r in self.conn.execute("SELECT concept FROM knowledge_nodes").fetchall()]
        visited: Set[str] = set()
        clusters: List[List[str]] = []

        for node in all_nodes:
            if node in visited:
                continue

            # BFS para encontrar componente conexo
            cluster: List[str] = []
            queue: deque = deque([node])

            while queue:
                current = queue.popleft()
                if current in visited:
                    continue
                visited.add(current)
                cluster.append(current)

                edges = self.get_edges(current)
                for edge in edges:
                    neighbor = edge["target"] if edge["source"] == current else edge["source"]
                    if neighbor not in visited:
                        queue.append(neighbor)

            if cluster:
                clusters.append(sorted(cluster))

        # Ordenar clusters por tamano (mayor primero)
        clusters.sort(key=len, reverse=True)
        return clusters

    # ──────────────────────────────────────────────────────────────────────
    # Inferencia
    # ──────────────────────────────────────────────────────────────────────

    def transitive_depends(self, node: str) -> List[str]:
        """
        Calcula dependencias transitivas: si A depends_on B y B depends_on C,
        entonces A transitive_depends [B, C].

        Returns:
            Lista de todas las dependencias transitivas
        """
        deps: List[str] = []
        visited: Set[str] = set()
        queue: deque = deque([node])

        while queue:
            current = queue.popleft()
            if current in visited:
                continue
            visited.add(current)

            # Solo seguir aristas "depends_on" salientes (usando JOIN para traducir UUIDs)
            rows = self.conn.execute(
                "SELECT kn2.concept FROM knowledge_edges ke "
                "JOIN knowledge_nodes kn1 ON ke.from_node = kn1.id "
                "JOIN knowledge_nodes kn2 ON ke.to_node = kn2.id "
                "WHERE kn1.concept = ? AND ke.relation_type = 'depends_on'",
                (current,)
            ).fetchall()

            for (target,) in rows:
                if target not in visited:
                    deps.append(target)
                    queue.append(target)

        return deps

    # ──────────────────────────────────────────────────────────────────────
    # Ranking: PageRank simplificado
    # ──────────────────────────────────────────────────────────────────────

    def pagerank(self, damping: float = 0.85, iterations: int = 30, top_n: int = 10) -> List[Tuple[str, float]]:
        """
        PageRank simplificado para encontrar nodos mas importantes.

        Args:
            damping: Factor de damping (0.85 estandar)
            iterations: Numero de iteraciones
            top_n: Cuantos resultados devolver

        Returns:
            Lista de (nombre, score) ordenada por importancia
        """
        all_nodes = [r[0] for r in self.conn.execute("SELECT concept FROM knowledge_nodes").fetchall()]
        n = len(all_nodes)

        if n == 0:
            return []

        # Inicializar scores uniformes
        scores: Dict[str, float] = {node: 1.0 / n for node in all_nodes}

        # Construir mapa de enlaces entrantes (usando JOINs para obtener concept names desde UUIDs)
        incoming: Dict[str, List[str]] = defaultdict(list)
        outgoing_count: Dict[str, int] = defaultdict(int)

        all_edges = self.conn.execute(
            "SELECT kn1.concept, kn2.concept FROM knowledge_edges ke "
            "JOIN knowledge_nodes kn1 ON ke.from_node = kn1.id "
            "JOIN knowledge_nodes kn2 ON ke.to_node = kn2.id"
        ).fetchall()
        for source, target in all_edges:
            if source in scores and target in scores:
                incoming[target].append(source)
                outgoing_count[source] += 1

        # Iterar PageRank
        for _ in range(iterations):
            new_scores: Dict[str, float] = {}
            for node in all_nodes:
                rank_sum = 0.0
                for src in incoming.get(node, []):
                    out_count = outgoing_count.get(src, 1)
                    rank_sum += scores[src] / out_count
                new_scores[node] = (1.0 - damping) / n + damping * rank_sum
            scores = new_scores

        # Ordenar por score descendente
        ranked = sorted(scores.items(), key=lambda x: x[1], reverse=True)
        return ranked[:top_n]

    # ──────────────────────────────────────────────────────────────────────
    # Busqueda
    # ──────────────────────────────────────────────────────────────────────

    def search_nodes(self, query: str, node_type: str = None, limit: int = 20) -> List[Dict[str, Any]]:
        """Busca nodos por nombre/concept (LIKE) y categoria."""
        sql = "SELECT concept, category, definition FROM knowledge_nodes WHERE LOWER(concept) LIKE ?"
        params: list = [f"%{query.lower()}%"]

        if node_type:
            sql += " AND category = ?"
            params.append(node_type)

        sql += " ORDER BY updated_at DESC LIMIT ?"
        params.append(limit)

        rows = self.conn.execute(sql, params).fetchall()
        return [
            {"name": r[0], "node_type": r[1], "metadata": self._parse_definition(r[2])}
            for r in rows
        ]

    def find_similar(self, query: str, limit: int = 10) -> List[Dict[str, Any]]:
        """
        Busqueda semantica simple: busca por palabras clave en nombre y metadata.
        Si hay embeddings disponibles (via memory_vec), los usa.
        """
        # Intentar busqueda con embeddings
        try:
            from core.memory_vec import get_semantic_memory
            mem = get_semantic_memory()
            results = mem.search(f"knowledge_graph node: {query}", limit=limit)
            if results:
                # Mapear resultados a nodos del grafo
                mapped = []
                for r in results:
                    content = r.get("content", "")
                    # Extraer nombre del nodo del contenido
                    parts = content.split("|")
                    if len(parts) >= 2:
                        node = self.get_node(parts[0].strip())
                        if node:
                            node["similarity_score"] = r.get("score", 0)
                            mapped.append(node)
                if mapped:
                    return mapped
        except Exception:
            pass  # error no crítico, continuar
        # Fallback: busqueda por texto
        words = query.lower().split()
        all_results = []

        for word in words:
            results = self.search_nodes(word, limit=limit)
            for r in results:
                if r not in all_results:
                    all_results.append(r)

        return all_results[:limit]

    # ──────────────────────────────────────────────────────────────────────
    # Auto-populate desde codebase
    # ──────────────────────────────────────────────────────────────────────

    def ingest_from_codebase(self, path: str) -> Dict[str, int]:
        """
        Analiza imports Python en un directorio y construye grafo automaticamente.

        Args:
            path: Directorio raiz a analizar

        Returns:
            Estadisticas de ingestion
        """
        path = Path(path)
        if not path.exists():
            self._log(f"⚠️ Ruta no encontrada: {path}")
            return {"error": "path_not_found"}

        stats = {"files": 0, "nodes": 0, "edges": 0, "errors": 0}
        self._log(f"📂 Ingiriendo codebase: {path}")

        py_files = list(path.rglob("*.py"))
        self._log(f"  Encontrados {len(py_files)} archivos Python")

        for py_file in py_files:
            try:
                content = py_file.read_text(errors="ignore")
                tree = ast.parse(content)

                module_name = py_file.stem
                rel_path = str(py_file.relative_to(path))

                # Crear nodo para el modulo
                self.add_node(module_name, "module", {
                    "path": rel_path,
                    "lines": len(content.splitlines()),
                })
                stats["nodes"] += 1

                # Extraer imports y crear aristas
                for node in ast.walk(tree):
                    dep_name = None
                    if isinstance(node, ast.Import):
                        for alias in node.names:
                            dep_name = alias.name.split(".")[0]
                    elif isinstance(node, ast.ImportFrom):
                        if node.module:
                            dep_name = node.module.split(".")[0]

                    if dep_name and dep_name != module_name:
                        if not self.get_node(dep_name):
                            self.add_node(dep_name, "module")
                            stats["nodes"] += 1
                        self.add_edge(module_name, dep_name, "depends_on")
                        stats["edges"] += 1

                # Extraer clases y funciones
                for node in ast.walk(tree):
                    if isinstance(node, ast.ClassDef):
                        class_name = f"{module_name}.{node.name}"
                        self.add_node(class_name, "concept", {"kind": "class"})
                        self.add_edge(class_name, module_name, "part_of")
                        stats["nodes"] += 1
                        stats["edges"] += 1

                stats["files"] += 1

            except Exception as e:
                stats["errors"] += 1

        self._log(f"✅ Ingestion completa: {stats['files']} archivos, {stats['nodes']} nodos, {stats['edges']} aristas")
        return stats

    # ──────────────────────────────────────────────────────────────────────
    # Visualizacion
    # ──────────────────────────────────────────────────────────────────────

    def export_dot(self, output_path: str = None) -> str:
        """
        Exporta el grafo en formato DOT para Graphviz.

        Args:
            output_path: Si se proporciona, escribe a archivo

        Returns:
            String con el grafo en formato DOT
        """
        type_colors = {
            "concept": "#4A90D9", "tool": "#E74C3C", "skill": "#2ECC71",
            "agent": "#F39C12", "file": "#9B59B6", "module": "#1ABC9C", "api": "#E67E22"
        }
        type_shapes = {
            "concept": "ellipse", "tool": "box", "skill": "diamond",
            "agent": "hexagon", "file": "note", "module": "component", "api": "pentagon"
        }

        lines = ["digraph EidosKnowledgeGraph {"]
        lines.append('    rankdir=LR;')
        lines.append('    node [fontname="Helvetica", fontsize=10];')
        lines.append('    edge [fontname="Helvetica", fontsize=8];')
        lines.append("")

        # Nodos
        nodes = self.conn.execute("SELECT concept, category FROM knowledge_nodes").fetchall()
        for name, ntype in nodes:
            color = type_colors.get(ntype, "#95A5A6")
            shape = type_shapes.get(ntype, "ellipse")
            safe_name = name.replace('"', '\\"').replace(".", "_")
            label = name.replace('"', '\\"')
            lines.append(f'    "{safe_name}" [label="{label}", shape={shape}, style=filled, fillcolor="{color}", fontcolor=white];')

        lines.append("")

        # Aristas (JOIN para traducir UUIDs a concept names)
        edge_styles = {
            "depends_on": "solid", "uses": "dashed", "produces": "bold",
            "related_to": "dotted", "part_of": "solid", "implements": "bold"
        }
        edges = self.conn.execute(
            "SELECT kn1.concept, kn2.concept, ke.relation_type FROM knowledge_edges ke "
            "JOIN knowledge_nodes kn1 ON ke.from_node = kn1.id "
            "JOIN knowledge_nodes kn2 ON ke.to_node = kn2.id"
        ).fetchall()
        for source, target, etype in edges:
            safe_source = source.replace('"', '\\"').replace(".", "_")
            safe_target = target.replace('"', '\\"').replace(".", "_")
            style = edge_styles.get(etype, "solid")
            lines.append(f'    "{safe_source}" -> "{safe_target}" [label="{etype}", style={style}];')

        lines.append("}")
        dot_content = "\n".join(lines)

        if output_path:
            Path(output_path).write_text(dot_content)
            self._log(f"📄 DOT exportado a: {output_path}")

        return dot_content

    def export_json(self, output_path: str = None) -> Dict[str, Any]:
        """
        Exporta el grafo en formato JSON para D3.js.

        Args:
            output_path: Si se proporciona, escribe a archivo

        Returns:
            Dict con nodos y links en formato D3.js
        """
        nodes = self.conn.execute(
            "SELECT concept, category, definition FROM knowledge_nodes"
        ).fetchall()

        edges = self.conn.execute(
            "SELECT kn1.concept, kn2.concept, ke.relation_type, ke.strength "
            "FROM knowledge_edges ke "
            "JOIN knowledge_nodes kn1 ON ke.from_node = kn1.id "
            "JOIN knowledge_nodes kn2 ON ke.to_node = kn2.id"
        ).fetchall()

        d3_data = {
            "nodes": [
                {"id": r[0], "group": r[1], "metadata": self._parse_definition(r[2])}
                for r in nodes
            ],
            "links": [
                {"source": r[0], "target": r[1], "type": r[2], "weight": r[3]}
                for r in edges
            ],
        }

        if output_path:
            Path(output_path).write_text(json.dumps(d3_data, indent=2))
            self._log(f"📄 JSON exportado a: {output_path}")

        return d3_data

    # ──────────────────────────────────────────────────────────────────────
    # Estadisticas
    # ──────────────────────────────────────────────────────────────────────

    def count_nodes(self) -> int:
        return self.conn.execute("SELECT COUNT(*) FROM knowledge_nodes").fetchone()[0]

    def count_edges(self) -> int:
        return self.conn.execute("SELECT COUNT(*) FROM knowledge_edges").fetchone()[0]

    def stats(self) -> Dict[str, Any]:
        """Estadisticas completas del grafo."""
        node_types = self.conn.execute(
            "SELECT category, COUNT(*) FROM knowledge_nodes GROUP BY category"
        ).fetchall()

        edge_types = self.conn.execute(
            "SELECT relation_type, COUNT(*) FROM knowledge_edges GROUP BY relation_type"
        ).fetchall()

        clusters = self.find_clusters()

        return {
            "total_nodes": self.count_nodes(),
            "total_edges": self.count_edges(),
            "node_types": {r[0]: r[1] for r in node_types},
            "edge_types": {r[0]: r[1] for r in edge_types},
            "clusters": len(clusters),
            "largest_cluster": len(clusters[0]) if clusters else 0,
            "db_path": self.db_path,
        }

    def close(self):
        """Cierra la conexion a la base de datos."""
        self.conn.close()


# ══════════════════════════════════════════════════════════════════════════════
# Singleton
# ══════════════════════════════════════════════════════════════════════════════

_kg: Optional[KnowledgeGraph] = None

def get_knowledge_graph() -> KnowledgeGraph:
    """Obtiene la instancia singleton de KnowledgeGraph."""
    global _kg
    if _kg is None:
        _kg = KnowledgeGraph()
    return _kg


# ══════════════════════════════════════════════════════════════════════════════
# CLI Testing
# ══════════════════════════════════════════════════════════════════════════════

if __name__ == "__main__":
    import sys
    import tempfile

    print(f"\n{'=' * 70}")
    print(f"EIDOS KNOWLEDGE GRAPH — Test Suite")
    print(f"{'=' * 70}\n")

    # Usar DB temporal para tests
    test_db = tempfile.mktemp(suffix=".db")
    kg = KnowledgeGraph(db_path=test_db, verbose=True)

    passed = 0
    failed = 0

    def test(name, condition):
        global passed, failed
        if condition:
            print(f"  ✅ {name}")
            passed += 1
        else:
            print(f"  ❌ {name}")
            failed += 1

    # ── Test 1: Crear nodos ──
    print("\n📌 Test 1: Crear nodos")
    test("add_node module", kg.add_node("memory_vec", "module", {"desc": "Memoria vectorial"}))
    test("add_node tool", kg.add_node("sqlite3", "tool", {"desc": "Base de datos"}))
    test("add_node skill", kg.add_node("shell_exec", "skill"))
    test("add_node agent", kg.add_node("guardian", "agent"))
    test("add_node api", kg.add_node("ollama", "api"))
    test("count = 5", kg.count_nodes() == 5)
    test("duplicate returns False", not kg.add_node("memory_vec", "module"))

    # ── Test 2: Crear aristas ──
    print("\n📌 Test 2: Crear aristas")
    test("add_edge depends_on", kg.add_edge("memory_vec", "sqlite3", "depends_on"))
    test("add_edge uses", kg.add_edge("guardian", "shell_exec", "uses"))
    test("add_edge uses api", kg.add_edge("memory_vec", "ollama", "uses"))
    test("add_edge related", kg.add_edge("sqlite3", "ollama", "related_to"))
    test("count_edges = 4", kg.count_edges() == 4)

    # ── Test 3: Queries ──
    print("\n📌 Test 3: Queries")
    neighbors = kg.get_neighbors("memory_vec", depth=1)
    test("neighbors encontrados", neighbors["total_nodes"] >= 2)

    path = kg.find_path("memory_vec", "ollama")
    test("find_path directo", path is not None and len(path) == 2)

    path2 = kg.find_path("guardian", "sqlite3")
    test("find_path no directo", path2 is None or len(path2) > 2)

    clusters = kg.find_clusters()
    test("find_clusters", len(clusters) >= 1)

    # ── Test 4: Inferencia ──
    print("\n📌 Test 4: Inferencia transitiva")
    kg.add_node("core_lib", "module")
    kg.add_edge("sqlite3", "core_lib", "depends_on")
    trans = kg.transitive_depends("memory_vec")
    test("transitive_depends", "sqlite3" in trans and "core_lib" in trans)

    # ── Test 5: PageRank ──
    print("\n📌 Test 5: PageRank")
    ranked = kg.pagerank(top_n=5)
    test("pagerank retorna resultados", len(ranked) > 0)
    test("pagerank tiene scores", all(isinstance(s, float) for _, s in ranked))
    print(f"      Top 3: {[(n, round(s, 4)) for n, s in ranked[:3]]}")

    # ── Test 6: Merge nodos ──
    print("\n📌 Test 6: Merge nodos")
    kg.add_node("db_engine", "tool")
    kg.add_edge("db_engine", "core_lib", "uses")
    nodes_before = kg.count_nodes()
    test("merge_nodes", kg.merge_nodes("sqlite3", "db_engine", "sqlite_unified"))
    test("nodo fusionado existe", kg.get_node("sqlite_unified") is not None)

    # ── Test 7: Busqueda ──
    print("\n📌 Test 7: Busqueda")
    results = kg.search_nodes("memory")
    test("search_nodes", len(results) >= 1)

    similar = kg.find_similar("base de datos SQL")
    test("find_similar retorna algo", isinstance(similar, list))

    # ── Test 8: Export ──
    print("\n📌 Test 8: Exportacion")
    dot = kg.export_dot()
    test("export_dot genera DOT", "digraph" in dot and "EidosKnowledgeGraph" in dot)

    d3 = kg.export_json()
    test("export_json tiene nodes", "nodes" in d3 and len(d3["nodes"]) > 0)
    test("export_json tiene links", "links" in d3 and len(d3["links"]) > 0)

    # ── Test 9: Stats ──
    print("\n📌 Test 9: Estadisticas")
    s = kg.stats()
    test("stats tiene total_nodes", s["total_nodes"] > 0)
    test("stats tiene node_types", len(s["node_types"]) > 0)
    print(f"      Stats: {json.dumps(s, indent=2)}")

    # ── Test 10: Remove ──
    print("\n📌 Test 10: Remove")
    count_before = kg.count_nodes()
    test("remove_node", kg.remove_node("guardian"))
    test("count decremented", kg.count_nodes() == count_before - 1)

    # ── Cleanup ──
    kg.close()
    os.unlink(test_db)

    print(f"\n{'=' * 70}")
    print(f"RESULTADOS: {passed} passed, {failed} failed, {passed + failed} total")
    print(f"{'=' * 70}\n")

    if failed > 0:
        sys.exit(1)
    print("✅ Knowledge Graph funcional\n")
