"""
core/eidos_skill_tree.py — Árbol de Habilidades Visual [S125]
==============================================================
Representación visual del grafo de conocimiento como árbol de habilidades.
Cada nodo = un concepto, dimensionado por profundidad de conocimiento,
coloreado por dominio, con brillo según nivel de maestría.
Las aristas tienen grosor proporcional a la fuerza de dependencia.

Navegación activa:
  - "¿Qué debería aprender ahora?" → basado en prerrequisitos y gaps
  - "¿Qué me falta para dominar X?" → camino de prerrequisitos
  - "¿Cuál es mi skill más fuerte/débil?" → ranking por maestría

Formatos de salida:
  - ASCII art (terminal)
  - JSON (web panel, dashboards)
  - DOT (Graphviz)
"""
from __future__ import annotations

import json
import logging
import math
import time
from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Set, Tuple

from core.db import get_conn

log = logging.getLogger("eidos.skilltree")

BRAIN_DB = Path.home() / ".eidos" / "evolution_brain.db"

# ── Paleta de colores por dominio ─────────────────────────────────────────────

DOMAIN_COLORS_HEX = {
    "security":       "#E74C3C",  # Rojo — seguridad
    "network":        "#3498DB",  # Azul — redes
    "system":         "#2ECC71",  # Verde — sistema
    "programming":    "#F39C12",  # Naranja — programación
    "web":            "#9B59B6",  # Púrpura — web
    "database":       "#1ABC9C",  # Turquesa — bases de datos
    "ai":             "#E91E63",  # Rosa — IA/ML
    "tool":           "#795548",  # Marrón — herramientas
    "concept":        "#607D8B",  # Gris azulado — conceptos teóricos
    "language":       "#FF5722",  # Naranja oscuro — idiomas
    "kali":           "#00BCD4",  # Cian — Kali Linux
    "eidos":          "#FFC107",  # Ámbar — EIDOS mismo
    "general":        "#95A5A6",  # Gris — general
    "research":       "#8BC34A",  # Verde claro — investigación
    "skill_general":  "#CDDC39",  # Lima — habilidades generales
    "inferred":       "#BDBDBD",  # Gris claro — inferido
}

DOMAIN_COLORS_ANSI = {
    "security":       "\033[31m",  # Rojo
    "network":        "\033[34m",  # Azul
    "system":         "\033[32m",  # Verde
    "programming":    "\033[33m",  # Amarillo/Naranja
    "web":            "\033[35m",  # Púrpura
    "database":       "\033[36m",  # Cian
    "ai":             "\033[95m",  # Magenta brillante
    "tool":           "\033[90m",  # Gris oscuro
    "concept":        "\033[37m",  # Blanco
    "language":       "\033[91m",  # Rojo brillante
    "kali":           "\033[96m",  # Cian brillante
    "eidos":          "\033[93m",  # Amarillo brillante
    "general":        "\033[37m",  # Blanco
}

ANSI_RESET = "\033[0m"
ANSI_BOLD = "\033[1m"
ANSI_DIM = "\033[2m"

# ── Mapeo de categoría → dominio ─────────────────────────────────────────────

CATEGORY_TO_DOMAIN: Dict[str, str] = {
    "security_tool": "security",
    "vulnerability": "security",
    "exploit": "security",
    "network_scan": "network",
    "network": "network",
    "protocol": "network",
    "system_command": "system",
    "system": "system",
    "os": "system",
    "kernel": "system",
    "programming_language": "programming",
    "library": "programming",
    "framework": "programming",
    "web": "web",
    "browser": "web",
    "http": "web",
    "database": "database",
    "sql": "database",
    "machine_learning": "ai",
    "deep_learning": "ai",
    "llm": "ai",
    "tool": "tool",
    "cli_tool": "tool",
    "gui_tool": "tool",
    "kali_tool": "kali",
    "kali": "kali",
    "language": "language",
    "skill:general": "skill_general",
    "skill_general": "skill_general",
    "eidos": "eidos",
    "researched": "research",
    "inferred": "inferred",
}

MAX_DEFAULT_DOMAIN = "general"


def _category_to_domain(category: str) -> str:
    """Mapea una categoría a un dominio de color."""
    cat_lower = (category or "").lower().strip()
    # Match exacto primero
    if cat_lower in CATEGORY_TO_DOMAIN:
        return CATEGORY_TO_DOMAIN[cat_lower]
    # Match por prefijo
    for key in sorted(CATEGORY_TO_DOMAIN, key=len, reverse=True):
        if cat_lower.startswith(key):
            return CATEGORY_TO_DOMAIN[key]
    return MAX_DEFAULT_DOMAIN


# ── Dataclasses ───────────────────────────────────────────────────────────────

@dataclass
class SkillTreeNode:
    """Un nodo en el árbol de habilidades."""
    node_id: str
    concept: str
    domain: str
    category: str
    # Maestría (0.0-1.0)
    declarative_score: float = 0.0
    procedural_score: float = 0.0
    applicational_score: float = 0.0
    metacognitive_score: float = 0.0
    overall_score: float = 0.0
    mastery_level: str = "NOVICE"
    # Metadatos
    definition_preview: str = ""
    cross_domain_edges: int = 0
    unique_contexts: int = 0
    executions_success: int = 0

    @property
    def depth(self) -> float:
        """Profundidad de conocimiento (declarative + procedural)."""
        return (self.declarative_score + self.procedural_score) / 2.0

    @property
    def brightness(self) -> float:
        """Brillo visual (0.0-1.0) basado en maestría general."""
        return self.overall_score

    @property
    def color_hex(self) -> str:
        """Color hexadecimal según dominio."""
        return DOMAIN_COLORS_HEX.get(self.domain, DOMAIN_COLORS_HEX["general"])

    @property
    def color_ansi(self) -> str:
        """Color ANSI según dominio."""
        return DOMAIN_COLORS_ANSI.get(self.domain, DOMAIN_COLORS_ANSI["general"])

    @property
    def size_symbol(self) -> str:
        """Símbolo de tamaño según profundidad."""
        d = self.depth
        if d >= 0.8:
            return "●"  # Grande
        elif d >= 0.6:
            return "◉"
        elif d >= 0.4:
            return "○"
        elif d >= 0.2:
            return "◦"
        else:
            return "·"  # Pequeño

    def to_dict(self) -> Dict[str, Any]:
        return {
            "node_id": self.node_id,
            "concept": self.concept,
            "domain": self.domain,
            "category": self.category,
            "scores": {
                "declarative": round(self.declarative_score, 3),
                "procedural": round(self.procedural_score, 3),
                "applicational": round(self.applicational_score, 3),
                "metacognitive": round(self.metacognitive_score, 3),
                "overall": round(self.overall_score, 3),
            },
            "mastery_level": self.mastery_level,
            "depth": round(self.depth, 3),
            "definition_preview": self.definition_preview[:120],
            "color_hex": self.color_hex,
            "cross_domain_edges": self.cross_domain_edges,
            "unique_contexts": self.unique_contexts,
            "executions_success": self.executions_success,
        }


@dataclass
class SkillTreeEdge:
    """Una arista en el árbol de habilidades."""
    from_node: str
    to_node: str
    relation_type: str
    strength: float
    from_domain: str = ""
    to_domain: str = ""
    is_cross_domain: bool = False

    @property
    def thickness(self) -> float:
        """Grosor visual (0.0-1.0)."""
        return min(1.0, self.strength)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "from": self.from_node,
            "to": self.to_node,
            "relation": self.relation_type,
            "strength": round(self.strength, 3),
            "is_cross_domain": self.is_cross_domain,
        }


@dataclass
class TreeLayout:
    """Layout completo del árbol."""
    nodes: List[SkillTreeNode] = field(default_factory=list)
    edges: List[SkillTreeEdge] = field(default_factory=list)
    domains: Dict[str, List[str]] = field(default_factory=dict)  # domain → [node_id]

    @property
    def total_nodes(self) -> int:
        return len(self.nodes)

    @property
    def total_edges(self) -> int:
        return len(self.edges)

    @property
    def domain_count(self) -> int:
        return len(self.domains)


# ── SkillTreeBuilder ─────────────────────────────────────────────────────────

class SkillTreeBuilder:
    """Constructor del árbol de habilidades.

    Lee concept_mastery + knowledge_nodes + knowledge_edges y construye
    una representación navegable del árbol de habilidades de EIDOS."""

    def __init__(self):
        self._cache_ts: float = 0.0
        self._cached_layout: Optional[TreeLayout] = None
        self._cache_ttl: float = 300.0  # 5 minutos

    def build(self, min_mastery: float = 0.0,
              max_nodes: int = 200,
              domains: Optional[List[str]] = None) -> TreeLayout:
        """Construye el árbol de habilidades desde la DB.

        Args:
            min_mastery: Filtrar conceptos con maestría >= este valor
            max_nodes: Máximo de nodos a incluir
            domains: Filtrar por dominios específicos (None = todos)
        """
        now = time.time()
        if (self._cached_layout and self._cache_ts > 0
                and (now - self._cache_ts) < self._cache_ttl
                and min_mastery == 0.0 and domains is None):
            return self._cached_layout

        layout = TreeLayout()
        node_map: Dict[str, SkillTreeNode] = {}

        try:
            conn = get_conn(BRAIN_DB, timeout=10, read_only=True)

            # Cargar concept_mastery con info de knowledge_nodes
            where_clauses = []
            params: List[Any] = []

            if domains:
                domain_conditions = []
                for d in domains:
                    # Mapear dominio a categorías que lo producen
                    matching_cats = [
                        cat for cat, dom in CATEGORY_TO_DOMAIN.items()
                        if dom == d
                    ]
                    if matching_cats:
                        placeholders = ",".join(["?"] * len(matching_cats))
                        domain_conditions.append(
                            f"kn.category IN ({placeholders})"
                        )
                        params.extend(matching_cats)
                if domain_conditions:
                    where_clauses.append("(" + " OR ".join(domain_conditions) + ")")

            where_sql = " AND ".join(where_clauses) if where_clauses else "1=1"

            rows = conn.execute(f"""
                SELECT cm.node_id, cm.concept,
                       cm.declarative_score, cm.procedural_score,
                       cm.applicational_score, cm.metacognitive_score,
                       cm.executions_success, cm.unique_contexts,
                       cm.cross_domain_edges,
                       kn.category, kn.definition
                FROM concept_mastery cm
                JOIN knowledge_nodes kn ON cm.node_id = kn.id
                WHERE (cm.declarative_score + cm.procedural_score
                       + cm.applicational_score + cm.metacognitive_score) / 4.0 >= ?
                  AND {where_sql}
                ORDER BY (cm.declarative_score + cm.procedural_score
                          + cm.applicational_score + cm.metacognitive_score) DESC
                LIMIT ?
            """, [min_mastery] + params + [max_nodes]).fetchall()

            # Obtener datos de maestria autoritativos de GrowthEngine
            growth_mastery_map = {}
            try:
                from core.eidos_growth import get_growth_engine
                engine = get_growth_engine()
                all_mastery = engine.get_all_mastery(limit=max_nodes * 2)
                for ms in all_mastery:
                    growth_mastery_map[ms.node_id] = ms
            except Exception as ge_err:
                log.debug("GrowthEngine data no disponible para skill tree: %s", ge_err)

            for row in rows:
                node_id, concept, d, p, a, m, exec_s, ctxs, cross, cat, defn = row
                # Preferir datos de GrowthEngine si estan disponibles
                if node_id in growth_mastery_map:
                    gms = growth_mastery_map[node_id]
                    d = gms.declarative_score
                    p = gms.procedural_score
                    a = gms.applicational_score
                    m = gms.metacognitive_score
                    exec_s = gms.executions_success
                    ctxs = gms.unique_contexts
                    cross = gms.cross_domain_edges
                overall = (d + p + a + m) / 4.0
                domain = _category_to_domain(cat or "")

                sn = SkillTreeNode(
                    node_id=node_id,
                    concept=(concept or "")[:100],
                    domain=domain,
                    category=(cat or "")[:80],
                    declarative_score=d or 0.0,
                    procedural_score=p or 0.0,
                    applicational_score=a or 0.0,
                    metacognitive_score=m or 0.0,
                    overall_score=overall,
                    mastery_level=_score_to_level(overall),
                    definition_preview=(defn or "")[:200],
                    cross_domain_edges=cross or 0,
                    unique_contexts=ctxs or 0,
                    executions_success=exec_s or 0,
                )
                node_map[node_id] = sn
                layout.nodes.append(sn)

                # Agrupar por dominio
                if domain not in layout.domains:
                    layout.domains[domain] = []
                layout.domains[domain].append(node_id)

            # Cargar aristas entre los nodos cargados
            if len(node_map) > 1:
                node_ids = list(node_map.keys())
                # Batch en chunks para evitar SQL muy largo
                chunk_size = 100
                for i in range(0, len(node_ids), chunk_size):
                    chunk = node_ids[i:i + chunk_size]
                    placeholders = ",".join(["?"] * len(chunk))
                    edge_rows = conn.execute(f"""
                        SELECT from_node, to_node, relation_type, strength
                        FROM knowledge_edges
                        WHERE strength >= 0.2
                          AND from_node IN ({placeholders})
                          AND to_node IN ({placeholders})
                    """, chunk + chunk).fetchall()

                    for from_n, to_n, rel_type, strength in edge_rows:
                        if from_n in node_map and to_n in node_map:
                            from_dom = node_map[from_n].domain
                            to_dom = node_map[to_n].domain
                            layout.edges.append(SkillTreeEdge(
                                from_node=from_n,
                                to_node=to_n,
                                relation_type=(rel_type or "related"),
                                strength=float(strength or 0.5),
                                from_domain=from_dom,
                                to_domain=to_dom,
                                is_cross_domain=(from_dom != to_dom),
                            ))

        except Exception as e:
            log.error("SkillTreeBuilder.build error: %s", e)

        self._cached_layout = layout
        self._cache_ts = now
        return layout

    # ── Navegación activa ──────────────────────────────────────────────────

    def what_should_i_learn(self, target_domain: Optional[str] = None,
                            top_n: int = 10) -> List[Dict[str, Any]]:
        """Responde "¿Qué debería aprender ahora?".

        Estrategia:
        1. Conceptos con baja maestría pero muchas aristas entrantes
           (son "hubs" — importantes pero no dominados)
        2. Prerrequisitos para conceptos de alta maestría
           (lo que falta para desbloquear niveles superiores)
        3. Gaps: conceptos en categorías objetivo sin representación

        Args:
            target_domain: Dominio objetivo (None = todos)
            top_n: Número de recomendaciones

        Returns: Lista de recomendaciones [{concept, reason, priority, ...}]
        """
        layout = self.build(min_mastery=0.0, max_nodes=500,
                            domains=[target_domain] if target_domain else None)
        recommendations = []

        # Estrategia 1: Hubs de baja maestría
        # Nodos con muchas aristas pero bajo overall_score
        edge_count: Dict[str, int] = defaultdict(int)
        for edge in layout.edges:
            edge_count[edge.from_node] += 1
            edge_count[edge.to_node] += 1

        for node in layout.nodes:
            connections = edge_count.get(node.node_id, 0)
            if connections >= 3 and node.overall_score < 0.55:
                # Es un hub no dominado
                priority = connections * (1.0 - node.overall_score)
                recommendations.append({
                    "concept": node.concept,
                    "domain": node.domain,
                    "current_level": node.mastery_level,
                    "overall_score": round(node.overall_score, 3),
                    "connections": connections,
                    "reason": f"Hub con {connections} conexiones — aprenderlo desbloquea "
                              f"mucho conocimiento conectado",
                    "priority": round(priority, 2),
                    "strategy": "hub_mastery",
                })

        # Estrategia 2: Prerrequisitos para MASTER/EXPERT
        # Encontrar nodos de alta maestría y recomendar sus vecinos débiles
        high_mastery = [n for n in layout.nodes
                        if n.overall_score >= 0.70]
        high_ids = {n.node_id for n in high_mastery}

        neighbor_weakness: Dict[str, float] = defaultdict(float)
        seen_pairs = set()
        for edge in layout.edges:
            if edge.from_node in high_ids and edge.to_node not in high_ids:
                pair = (edge.from_node, edge.to_node)
                if pair not in seen_pairs:
                    seen_pairs.add(pair)
                    to_node = next(
                        (n for n in layout.nodes if n.node_id == edge.to_node), None
                    )
                    if to_node and to_node.overall_score < 0.55:
                        gap = 0.70 - to_node.overall_score
                        neighbor_weakness[edge.to_node] += gap * edge.strength

        for node_id, weakness in sorted(neighbor_weakness.items(),
                                         key=lambda x: -x[1])[:top_n]:
            node = next((n for n in layout.nodes if n.node_id == node_id), None)
            if node:
                # Encontrar qué concepto experto depende de este
                depends_on = []
                for edge in layout.edges:
                    if edge.to_node == node_id and edge.from_node in high_ids:
                        expert_node = next(
                            (n for n in layout.nodes if n.node_id == edge.from_node), None
                        )
                        if expert_node:
                            depends_on.append(expert_node.concept[:40])

                recommendations.append({
                    "concept": node.concept,
                    "domain": node.domain,
                    "current_level": node.mastery_level,
                    "overall_score": round(node.overall_score, 3),
                    "prerequisite_for": depends_on[:3],
                    "reason": f"Prerrequisito para dominar: {', '.join(depends_on[:2])}",
                    "priority": round(weakness, 2),
                    "strategy": "prerequisite",
                })

        # Estrategia 3: Gaps en dominios con poca representación
        if target_domain:
            domain_nodes = layout.domains.get(target_domain, [])
            if len(domain_nodes) < 3:
                recommendations.append({
                    "concept": f"[Cualquier concepto en {target_domain}]",
                    "domain": target_domain,
                    "current_level": "NOVICE",
                    "overall_score": 0.0,
                    "reason": f"El dominio '{target_domain}' tiene solo "
                              f"{len(domain_nodes)} conceptos — necesita expansión",
                    "priority": 5.0,
                    "strategy": "domain_expansion",
                })

        # Estrategia 4: Conceptos pendientes en la cola de practica (due items)
        try:
            from core.eidos_practice import get_practice_queue
            pq = get_practice_queue()
            due_concepts = pq.get_due_concepts(limit=20)
            for dc in due_concepts:
                overdue_hours = dc.get("overdue_hours", 0)
                avg_recall = dc.get("avg_recall_score", 0.5)
                practice_priority = (overdue_hours / 24.0) * (1.0 - avg_recall) * 3.0
                if practice_priority > 1.0:
                    recommendations.append({
                        "concept": dc["concept"],
                        "domain": "general",
                        "current_level": dc.get("mastery_level", "NOVICE"),
                        "overall_score": avg_recall,
                        "due_hours": overdue_hours,
                        "reason": f"Pendiente de repaso ({overdue_hours:.0f}h atrasado) — "
                                  f"la practica espaciada lo necesita",
                        "priority": round(practice_priority, 2),
                        "strategy": "spaced_repetition_due",
                    })
        except Exception as pq_err:
            log.debug("Practice queue no disponible para what_should_i_learn: %s", pq_err)

        # Ordenar por prioridad descendente y des-duplicar por concepto
        seen = set()
        unique_recs = []
        for rec in sorted(recommendations, key=lambda r: -r["priority"]):
            if rec["concept"] not in seen:
                seen.add(rec["concept"])
                unique_recs.append(rec)

        return unique_recs[:top_n]

    def prerequisites_for(self, concept: str) -> List[Dict[str, Any]]:
        """Responde "¿Qué me falta para dominar X?".

        Encuentra la cadena de prerrequisitos para alcanzar un concepto objetivo.

        Args:
            concept: Concepto objetivo

        Returns: Lista de prerrequisitos ordenados por dependencia.
        """
        layout = self.build(min_mastery=0.0, max_nodes=500)
        target_node = next(
            (n for n in layout.nodes if n.concept.lower() == concept.lower()), None
        )

        if not target_node:
            # Buscar por substring
            target_node = next(
                (n for n in layout.nodes if concept.lower() in n.concept.lower()), None
            )

        if not target_node:
            return [{"error": f"Concepto '{concept}' no encontrado en el árbol"}]

        # Construir grafo invertido (dependencias)
        # Una arista A→B significa que B depende de A (A es prerrequisito de B)
        reverse_adj: Dict[str, List[Tuple[str, float]]] = defaultdict(list)
        for edge in layout.edges:
            reverse_adj[edge.to_node].append((edge.from_node, edge.strength))

        # BFS hacia atrás desde el target
        prereqs = []
        visited = set()
        queue = [(target_node.node_id, 0, [])]  # (node_id, depth, path)

        while queue:
            current_id, depth, path = queue.pop(0)
            if current_id in visited or depth > 3:
                continue
            visited.add(current_id)

            for prereq_id, strength in reverse_adj.get(current_id, []):
                prereq_node = next(
                    (n for n in layout.nodes if n.node_id == prereq_id), None
                )
                if prereq_node and prereq_id not in visited:
                    prereqs.append({
                        "concept": prereq_node.concept,
                        "domain": prereq_node.domain,
                        "current_level": prereq_node.mastery_level,
                        "overall_score": round(prereq_node.overall_score, 3),
                        "dependency_strength": round(strength, 3),
                        "depth": depth + 1,
                        "path": path + [target_node.concept[:40]],
                    })
                    queue.append((prereq_id, depth + 1, path + [prereq_node.concept[:40]]))

        # Ordenar: primero los que ya se dominan, luego los débiles
        prereqs.sort(key=lambda p: p["overall_score"])
        return prereqs

    def strongest_skills(self, top_n: int = 10) -> List[Dict[str, Any]]:
        """Top habilidades más fuertes de EIDOS."""
        layout = self.build(min_mastery=0.0, max_nodes=500)
        sorted_nodes = sorted(layout.nodes,
                              key=lambda n: (n.overall_score, n.cross_domain_edges),
                              reverse=True)
        return [
            {
                "concept": n.concept,
                "domain": n.domain,
                "mastery_level": n.mastery_level,
                "overall_score": round(n.overall_score, 3),
                "depth": round(n.depth, 3),
                "cross_domain_edges": n.cross_domain_edges,
                "definition": n.definition_preview[:120],
            }
            for n in sorted_nodes[:top_n]
        ]

    def weakest_skills(self, top_n: int = 10) -> List[Dict[str, Any]]:
        """Top habilidades más débiles que más conviene mejorar."""
        layout = self.build(min_mastery=0.0, max_nodes=500)

        # Priorizar: baja maestría pero alta conectividad
        edge_count: Dict[str, int] = defaultdict(int)
        for edge in layout.edges:
            edge_count[edge.from_node] += 1
            edge_count[edge.to_node] += 1

        scored = []
        for n in layout.nodes:
            if n.overall_score < 0.55:  # Solo NOVICE y APPRENTICE bajo
                conn = edge_count.get(n.node_id, 0)
                score = (1.0 - n.overall_score) * (1 + math.log(1 + conn))
                scored.append((n, score))

        scored.sort(key=lambda x: -x[1])
        return [
            {
                "concept": n.concept,
                "domain": n.domain,
                "mastery_level": n.mastery_level,
                "overall_score": round(n.overall_score, 3),
                "connections": edge_count.get(n.node_id, 0),
                "improvement_priority": round(score, 2),
            }
            for n, score in scored[:top_n]
        ]

    # ── Renderizado ──────────────────────────────────────────────────────────

    def render_ascii(self, max_nodes: int = 40,
                     group_by_domain: bool = True) -> str:
        """Renderiza el árbol de habilidades en ASCII para terminal.

        Args:
            max_nodes: Máximo de nodos a mostrar
            group_by_domain: Agrupar por dominio
        """
        layout = self.build(min_mastery=0.0, max_nodes=max_nodes)
        lines = []
        lines.append(f"{ANSI_BOLD}EIDOS Skill Tree{ANSI_RESET}")
        lines.append(f"{ANSI_DIM}{'─' * 60}{ANSI_RESET}")
        lines.append(f"Nodes: {layout.total_nodes} | "
                     f"Edges: {layout.total_edges} | "
                     f"Domains: {layout.domain_count}")
        lines.append("")

        if group_by_domain:
            for domain, node_ids in sorted(layout.domains.items()):
                domain_nodes = [n for n in layout.nodes if n.node_id in node_ids]
                domain_nodes.sort(key=lambda n: -n.overall_score)

                color = DOMAIN_COLORS_ANSI.get(domain, ANSI_RESET)
                lines.append(f"{color}{ANSI_BOLD}  [{domain.upper()}]{ANSI_RESET} "
                             f"({len(domain_nodes)} conceptos)")

                for i, node in enumerate(domain_nodes[:8]):
                    prefix = "  ├─" if i < min(7, len(domain_nodes) - 1) else "  └─"
                    level_indicator = _level_indicator(node.mastery_level)
                    bar = _mastery_bar(node.overall_score)
                    lines.append(
                        f"{color}{prefix}{ANSI_RESET} {node.size_symbol} "
                        f"{level_indicator} {node.concept[:45]} "
                        f"{ANSI_DIM}{bar}{ANSI_RESET}"
                    )
                lines.append("")
        else:
            # Flat list ordenado por maestría
            sorted_nodes = sorted(layout.nodes, key=lambda n: -n.overall_score)
            for i, node in enumerate(sorted_nodes[:max_nodes]):
                color = node.color_ansi
                line_color = color if node.overall_score >= 0.4 else ANSI_DIM
                level_indicator = _level_indicator(node.mastery_level)
                bar = _mastery_bar(node.overall_score)
                lines.append(
                    f"{line_color}{node.size_symbol} {level_indicator} "
                    f"{node.concept[:50]} "
                    f"[{node.domain[:10]}] {ANSI_DIM}{bar}{ANSI_RESET}"
                )

        if layout.total_nodes > max_nodes:
            lines.append(f"{ANSI_DIM}... y {layout.total_nodes - max_nodes} más{ANSI_RESET}")

        return "\n".join(lines)

    def render_json(self, max_nodes: int = 200) -> Dict[str, Any]:
        """Renderiza el árbol en formato JSON para web panel / dashboards."""
        layout = self.build(min_mastery=0.0, max_nodes=max_nodes)

        # Agrupar nodos por dominio
        domains_json = {}
        for domain, node_ids in layout.domains.items():
            domain_nodes = [n for n in layout.nodes if n.node_id in node_ids]
            domain_nodes.sort(key=lambda n: -n.overall_score)
            domains_json[domain] = {
                "color_hex": DOMAIN_COLORS_HEX.get(domain, "#95A5A6"),
                "node_count": len(domain_nodes),
                "avg_mastery": round(
                    sum(n.overall_score for n in domain_nodes) / max(len(domain_nodes), 1), 3
                ),
                "nodes": [n.to_dict() for n in domain_nodes],
            }

        # Aristas (solo las que conectan nodos en el layout)
        node_id_set = {n.node_id for n in layout.nodes}
        edges_json = [
            e.to_dict() for e in layout.edges
            if e.from_node in node_id_set and e.to_node in node_id_set
        ]

        return {
            "total_nodes": layout.total_nodes,
            "total_edges": len(edges_json),
            "domain_count": layout.domain_count,
            "domains": domains_json,
            "cross_domain_edges": [
                e.to_dict() for e in layout.edges if e.is_cross_domain
            ],
            "flat_nodes": [n.to_dict() for n in layout.nodes[:100]],
        }

    def render_dot(self, max_nodes: int = 100) -> str:
        """Renderiza en formato DOT (Graphviz)."""
        layout = self.build(min_mastery=0.0, max_nodes=max_nodes)
        lines = ["digraph EIDOS_SkillTree {",
                 "  rankdir=TB;",
                 "  node [shape=box, style=filled, fontname=\"Helvetica\"];",
                 "  edge [fontname=\"Helvetica\", fontsize=8];",
                 ""]

        # Nodos
        for node in layout.nodes:
            # Tamaño basado en profundidad
            fontsize = 10 + int(node.depth * 10)
            penwidth = 1 + int(node.depth * 3)
            color = node.color_hex
            # Brillo: añadir transparencia para baja maestría
            alpha = int(0x40 + node.overall_score * 0xBF)
            alpha_hex = f"{alpha:02x}"
            lines.append(
                f'  "{node.node_id}" [label="{_escape_dot(node.concept[:40])}", '
                f'fontsize={fontsize}, penwidth={penwidth}, '
                f'fillcolor="{color}{alpha_hex}", color="{color}"];'
            )

        # Aristas
        node_id_set = {n.node_id for n in layout.nodes}
        for edge in layout.edges:
            if edge.from_node not in node_id_set or edge.to_node not in node_id_set:
                continue
            penwidth = 0.5 + edge.thickness * 3
            style = "dashed" if edge.is_cross_domain else "solid"
            color = "#666666" if edge.is_cross_domain else "#999999"
            lines.append(
                f'  "{edge.from_node}" -> "{edge.to_node}" '
                f'[penwidth={penwidth:.1f}, style={style}, color="{color}", '
                f'label="{edge.relation_type[:15]}"];'
            )

        lines.append("}")
        return "\n".join(lines)

    def domain_summary(self) -> List[Dict[str, Any]]:
        """Resumen por dominio: cuántos conceptos, maestría media, gaps."""
        layout = self.build(min_mastery=0.0, max_nodes=500)
        summaries = []

        for domain, node_ids in sorted(layout.domains.items()):
            domain_nodes = [n for n in layout.nodes if n.node_id in node_ids]
            if not domain_nodes:
                continue

            avg_score = sum(n.overall_score for n in domain_nodes) / len(domain_nodes)
            level_dist = defaultdict(int)
            for n in domain_nodes:
                level_dist[n.mastery_level] += 1

            # Calcular gaps: conceptos del dominio en knowledge_nodes que NO
            # están en concept_mastery (y por tanto no en el árbol)
            gaps = 0
            try:
                conn = get_conn(BRAIN_DB, timeout=5, read_only=True)
                # Contar cuántos knowledge_nodes de este dominio hay en total
                matching_cats = [
                    cat for cat, dom in CATEGORY_TO_DOMAIN.items() if dom == domain
                ]
                if matching_cats:
                    placeholders = ",".join(["?"] * len(matching_cats))
                    total_in_kn = conn.execute(
                        f"SELECT COUNT(*) FROM knowledge_nodes "
                        f"WHERE category IN ({placeholders}) "
                        f"AND confidence >= 0.3",
                        matching_cats
                    ).fetchone()[0]
                    # Contar cuántos están en concept_mastery
                    tracked = conn.execute(
                        f"SELECT COUNT(*) FROM concept_mastery cm "
                        f"JOIN knowledge_nodes kn ON cm.node_id = kn.id "
                        f"WHERE kn.category IN ({placeholders})",
                        matching_cats
                    ).fetchone()[0]
                    gaps = max(0, total_in_kn - tracked)
            except Exception:
                pass

            summaries.append({
                "domain": domain,
                "color_hex": DOMAIN_COLORS_HEX.get(domain, "#95A5A6"),
                "node_count": len(domain_nodes),
                "avg_mastery": round(avg_score, 3),
                "level_distribution": dict(level_dist),
                "untracked_gaps": gaps,
            })

        return sorted(summaries, key=lambda s: -s["node_count"])


# ── Helpers ───────────────────────────────────────────────────────────────────

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


def _level_indicator(level: str) -> str:
    """Indicador visual de nivel para terminal."""
    return {
        "MASTER":     "★★★",
        "EXPERT":     "★★☆",
        "JOURNEYMAN": "★☆☆",
        "APPRENTICE": "•☆☆",
        "NOVICE":     "···",
    }.get(level, "···")


def _mastery_bar(score: float, width: int = 10) -> str:
    """Barra de progreso de maestría."""
    filled = int(score * width)
    empty = width - filled
    return f"[{'█' * filled}{'░' * empty}]"


def _escape_dot(text: str) -> str:
    """Escapa texto para DOT."""
    return text.replace('"', '\\"').replace('\n', '\\n')


# ── Singleton ─────────────────────────────────────────────────────────────────

_instance: Optional[SkillTreeBuilder] = None


def get_skill_tree() -> SkillTreeBuilder:
    """Singleton del SkillTreeBuilder."""
    global _instance
    if _instance is None:
        _instance = SkillTreeBuilder()
    return _instance


# ── Funciones de conveniencia ─────────────────────────────────────────────────

def what_should_i_learn(domain: Optional[str] = None,
                        top_n: int = 10) -> List[Dict[str, Any]]:
    """API rápida: ¿qué debería aprender EIDOS ahora?"""
    return get_skill_tree().what_should_i_learn(domain, top_n)


def prerequisites_for(concept: str) -> List[Dict[str, Any]]:
    """API rápida: prerrequisitos para dominar un concepto."""
    return get_skill_tree().prerequisites_for(concept)


def skill_tree_json(max_nodes: int = 200) -> Dict[str, Any]:
    """API rápida: árbol completo en JSON."""
    return get_skill_tree().render_json(max_nodes)


# ── CLI ───────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    import sys

    st = get_skill_tree()

    if len(sys.argv) < 2:
        print("Uso: python eidos_skill_tree.py <cmd> [args]")
        print("  tree               — mostrar árbol ASCII")
        print("  json [nodos]       — exportar JSON")
        print("  dot [nodos]        — exportar DOT (Graphviz)")
        print("  next [dominio]     — qué aprender ahora")
        print("  prereqs <concepto> — prerrequisitos para concepto")
        print("  strongest          — top habilidades más fuertes")
        print("  weakest            — top habilidades más débiles")
        print("  domains            — resumen por dominio")
        sys.exit(1)

    cmd = sys.argv[1]

    if cmd == "tree":
        print(st.render_ascii(max_nodes=50))

    elif cmd == "json":
        max_n = int(sys.argv[2]) if len(sys.argv) > 2 else 200
        print(json.dumps(st.render_json(max_n), indent=2, ensure_ascii=False))

    elif cmd == "dot":
        max_n = int(sys.argv[2]) if len(sys.argv) > 2 else 100
        print(st.render_dot(max_n))

    elif cmd == "next":
        domain = sys.argv[2] if len(sys.argv) > 2 else None
        recs = st.what_should_i_learn(domain, top_n=10)
        print(f"Recomendaciones de aprendizaje ({len(recs)}):")
        for i, r in enumerate(recs):
            print(f"  {i+1}. [{r['domain']}] {r['concept'][:50]}")
            print(f"     Nivel: {r['current_level']} | "
                  f"Score: {r['overall_score']:.3f} | "
                  f"Prioridad: {r['priority']:.2f}")
            print(f"     {r['reason'][:100]}")

    elif cmd == "prereqs":
        concept = sys.argv[2] if len(sys.argv) > 2 else ""
        if not concept:
            print("Especifica un concepto")
            sys.exit(1)
        prereqs = st.prerequisites_for(concept)
        print(f"Prerrequisitos para '{concept}' ({len(prereqs)}):")
        for p in prereqs:
            if "error" in p:
                print(f"  {p['error']}")
            else:
                print(f"  [{p['domain']}] {p['concept'][:50]} "
                      f"| Nivel: {p['current_level']} "
                      f"| Score: {p['overall_score']:.3f} "
                      f"| Profundidad: {p.get('depth', '?')}")

    elif cmd == "strongest":
        skills = st.strongest_skills(15)
        print(f"Top {len(skills)} habilidades más fuertes:")
        for i, s in enumerate(skills):
            print(f"  {i+1}. [{s['domain']}] {s['concept'][:50]} "
                  f"| {s['mastery_level']} "
                  f"| Score={s['overall_score']:.3f} "
                  f"| Cross={s['cross_domain_edges']}")

    elif cmd == "weakest":
        skills = st.weakest_skills(15)
        print(f"Top {len(skills)} habilidades a mejorar:")
        for i, s in enumerate(skills):
            print(f"  {i+1}. [{s['domain']}] {s['concept'][:50]} "
                  f"| {s['mastery_level']} "
                  f"| Score={s['overall_score']:.3f} "
                  f"| Prioridad={s['improvement_priority']:.2f}")

    elif cmd == "domains":
        summaries = st.domain_summary()
        print(f"Resumen por dominio ({len(summaries)}):")
        for s in summaries:
            print(f"  [{s['domain']:20s}] {s['node_count']:4d} conceptos | "
                  f"Media={s['avg_mastery']:.3f} | "
                  f"Gaps={s['untracked_gaps']} | "
                  f"Niveles={s['level_distribution']}")

    else:
        print(f"Comando desconocido: {cmd}")
