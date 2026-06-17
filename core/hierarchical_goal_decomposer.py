"""
core/hierarchical_goal_decomposer.py — Descomposición Jerárquica de Metas [S90.1]

"Un roble no se tala de un hachazo. Se desmenuza rama a rama." — DeepSeek

Descompone metas complejas en árboles de submetas accionables:
  1. GoalTree: estructura jerárquica con nodos padre/hijo
  2. Decomposer: patrones conocidos + LLM consultivo + memoria episódica
  3. Executor: recorre el árbol depth-first, ejecuta submetas
  4. Adapter: cuando una submeta falla, busca rutas alternativas

Ejemplo:
  "Encuentra el precio promedio de n8n en 3 sitios"
  → SUBMETAS:
    ├── buscar "n8n pricing" en google (depth=1)
    ├── para cada resultado (depth=2):
    │   ├── extraer precio de sitio 1
    │   ├── extraer precio de sitio 2
    │   └── extraer precio de sitio 3
    └── calcular promedio (depth=1)

Integración con ScreenController:
  HGD reemplaza al Planner simple para metas complejas (>5 palabras clave).
  Cada submeta se ejecuta como una misión ScreenController independiente.

Uso:
    hgd = get_hgd()
    tree = hgd.decompose("encuentra el precio promedio de X en 3 sitios")
    results = hgd.execute_tree(tree, screen_controller)
"""

from __future__ import annotations

import hashlib
import json
import logging
import re
import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Dict, List, Optional, Tuple

log = logging.getLogger("eidos.hgd")


# ── Tipos ───────────────────────────────────────────────────────────────────────


class GoalStatus(Enum):
    PENDING = "pending"
    IN_PROGRESS = "in_progress"
    COMPLETED = "completed"
    FAILED = "failed"
    SKIPPED = "skipped"
    ADAPTED = "adapted"  # completado por ruta alternativa


@dataclass
class GoalNode:
    """Nodo en el árbol de metas."""
    id: str
    goal: str                          # descripción de la submeta
    depth: int                         # profundidad en el árbol (0 = raíz)
    parent_id: Optional[str] = None    # nodo padre
    children: List[GoalNode] = field(default_factory=list)
    plan_steps: List[Dict[str, Any]] = field(default_factory=list)
    status: GoalStatus = GoalStatus.PENDING
    result: Optional[Dict[str, Any]] = None
    extracted_data: Dict[str, Any] = field(default_factory=dict)
    attempts: int = 0
    max_attempts: int = 3
    created_at: float = 0.0
    completed_at: Optional[float] = None
    adaptation_history: List[str] = field(default_factory=list)


@dataclass
class GoalTree:
    """Árbol completo de descomposición de metas."""
    root: GoalNode
    all_nodes: Dict[str, GoalNode] = field(default_factory=dict)
    created_at: float = 0.0
    metadata: Dict[str, Any] = field(default_factory=dict)

    def get_node(self, node_id: str) -> Optional[GoalNode]:
        return self.all_nodes.get(node_id)

    def get_pending_leaves(self) -> List[GoalNode]:
        """Nodos hoja pendientes (sin hijos, listos para ejecutar)."""
        return [
            n for n in self.all_nodes.values()
            if n.status == GoalStatus.PENDING and not n.children
        ]

    def get_next_task(self) -> Optional[GoalNode]:
        """Siguiente tarea a ejecutar (depth-first, pending leaves first)."""
        pending = self.get_pending_leaves()
        if not pending:
            # Buscar nodos pending con hijos ya completados
            for n in self.all_nodes.values():
                if n.status == GoalStatus.PENDING and n.children:
                    # S93: n.children son IDs (str), resolver a objetos GoalNode
                    child_nodes = [self.all_nodes.get(cid) for cid in n.children]
                    child_nodes = [c for c in child_nodes if c is not None]
                    if child_nodes and all(
                        c.status in (GoalStatus.COMPLETED, GoalStatus.SKIPPED)
                        for c in child_nodes
                    ):
                        return n
            return None
        # Priorizar menor depth (más cercano a la raíz entre los leaves)
        pending.sort(key=lambda n: (n.depth, n.id))
        return pending[0]

    def progress_pct(self) -> float:
        total = len(self.all_nodes)
        if total == 0:
            return 0.0
        done = sum(1 for n in self.all_nodes.values()
                  if n.status in (GoalStatus.COMPLETED, GoalStatus.ADAPTED, GoalStatus.SKIPPED))
        return done / total

    def to_dict(self) -> Dict[str, Any]:
        def node_to_dict(node: GoalNode) -> Dict:
            return {
                "id": node.id,
                "goal": node.goal,
                "depth": node.depth,
                "status": node.status.value,
                "children": [node_to_dict(c) for c in node.children],
                "extracted_data": node.extracted_data,
                "attempts": node.attempts,
            }
        return {
            "root_goal": self.root.goal,
            "progress": self.progress_pct(),
            "total_nodes": len(self.all_nodes),
            "tree": node_to_dict(self.root),
        }


# ── Patrones de descomposición ─────────────────────────────────────────────────


class DecompositionPatterns:
    """Patrones conocidos de descomposición de metas."""

    PATTERNS = {
        "price_comparison": {
            "keywords": ["precio", "price", "cuesta", "costo", "cost", "comparar precio",
                        "promedio", "average price", "cuánto vale"],
            "template": {
                "subgoals": [
                    {"goal": "Buscar {query} en Google", "depth": 1,
                     "plan": [{"action": "navigate", "target": "barra_direcciones"},
                              {"action": "type", "target": "https://google.com"},
                              {"action": "type", "target": "{query}"}]},
                    {"goal": "Extraer precios de resultados", "depth": 1,
                     "plan": [{"action": "extract", "target": "precios"},
                              {"action": "scroll", "target": "más resultados"}]},
                    {"goal": "Calcular resultado final", "depth": 1,
                     "plan": [{"action": "evaluate", "target": "cálculo"}]},
                ]
            }
        },
        "multi_site_research": {
            "keywords": ["investiga", "investigar", "research", "varios sitios",
                        "múltiples fuentes", "distintas páginas", "en 3", "en varios"],
            "template": {
                "subgoals": [
                    {"goal": "Definir términos de búsqueda para {query}", "depth": 1,
                     "plan": [{"action": "evaluate", "target": "query"}]},
                    {"goal": "Buscar {query} en Google", "depth": 1,
                     "plan": [{"action": "navigate", "target": "google"},
                              {"action": "type", "target": "{query}"}]},
                    {"goal": "Explorar cada resultado relevante", "depth": 2,
                     "plan": [{"action": "click", "target": "resultados"},
                              {"action": "extract", "target": "contenido"}]},
                    {"goal": "Extraer datos solicitados de cada página", "depth": 2,
                     "plan": [{"action": "extract", "target": "datos"},
                              {"action": "scroll", "target": "contenido_completo"}]},
                    {"goal": "Sintetizar hallazgos", "depth": 1,
                     "plan": [{"action": "evaluate", "target": "síntesis"}]},
                ]
            }
        },
        "learn_and_understand": {
            "keywords": ["aprende", "aprender", "entiende", "entender", "comprende",
                        "learn", "understand", "documentación", "tutorial", "docs"],
            "template": {
                "subgoals": [
                    {"goal": "Encontrar documentación oficial de {query}", "depth": 1,
                     "plan": [{"action": "navigate", "target": "google"},
                              {"action": "type", "target": "{query} documentation"}]},
                    {"goal": "Leer y extraer conceptos clave", "depth": 2,
                     "plan": [{"action": "extract", "target": "conceptos"},
                              {"action": "scroll", "target": "contenido"}]},
                    {"goal": "Buscar ejemplos prácticos", "depth": 2,
                     "plan": [{"action": "click", "target": "examples"},
                              {"action": "extract", "target": "código"}]},
                    {"goal": "Sintetizar conocimiento aprendido", "depth": 1,
                     "plan": [{"action": "evaluate", "target": "aprendizaje"}]},
                ]
            }
        },
        "generic_explore": {
            "keywords": [],  # fallback para cualquier meta
            "template": {
                "subgoals": [
                    {"goal": "Navegar a fuente de información para {query}", "depth": 1,
                     "plan": [{"action": "navigate", "target": "google"},
                              {"action": "type", "target": "{query}"}]},
                    {"goal": "Explorar y extraer contenido relevante", "depth": 2,
                     "plan": [{"action": "click", "target": "primer_resultado"},
                              {"action": "scroll", "target": "contenido"},
                              {"action": "extract", "target": "información"}]},
                    {"goal": "Seguir links relacionados", "depth": 2,
                     "plan": [{"action": "click", "target": "links"},
                              {"action": "extract", "target": "más_info"}]},
                    {"goal": "Sintetizar y reportar hallazgos", "depth": 1,
                     "plan": [{"action": "evaluate", "target": "síntesis"}]},
                ]
            }
        },
    }

    @classmethod
    def match(cls, goal: str) -> Tuple[str, Dict]:
        """Encuentra el mejor patrón para una meta."""
        goal_lower = goal.lower()
        best_match = None
        best_score = 0

        for name, pattern in cls.PATTERNS.items():
            score = sum(1 for kw in pattern["keywords"] if kw in goal_lower)
            if score > best_score:
                best_score = score
                best_match = name

        if best_match and best_score > 0:
            return best_match, cls.PATTERNS[best_match]

        return "generic_explore", cls.PATTERNS["generic_explore"]


# ── HierarchicalGoalDecomposer ─────────────────────────────────────────────────


class HierarchicalGoalDecomposer:
    """Descompone metas complejas en árboles de submetas.

    Usa:
    1. Patrones conocidos (determinista, sin LLM)
    2. Memoria episódica (planes previos exitosos)
    3. LLM consultivo (para metas verdaderamente nuevas)
    """

    MAX_DEPTH = 3  # profundidad máxima del árbol

    def __init__(self, use_llm: bool = True):
        self._use_llm = use_llm
        self._decomposition_cache: Dict[str, GoalTree] = {}

    def decompose(self, goal: str, max_depth: int = 2,
                  previous_results: Dict[str, Any] = None) -> GoalTree:
        """Descompone una meta en árbol de submetas.

        Args:
            goal: meta en lenguaje natural
            max_depth: profundidad máxima del árbol (default 2)
            previous_results: resultados previos para contextualizar

        Returns:
            GoalTree con la descomposición completa (nodos frescos, no cacheados)
        """
        # ¿Es una meta simple que no necesita descomposición?
        if self._is_simple_goal(goal):
            return self._simple_tree(goal)

        # S94: ¿Meta de complejidad media? → árbol ligero (2 nodos, no 5)
        if self._is_medium_goal(goal):
            tree = self._light_tree(goal)
            cache_key = hashlib.md5(goal.lower().encode()).hexdigest()[:16]
            self._decomposition_cache[cache_key] = tree
            log.info("🌿 Árbol ligero: %d nodos para '%s'",
                    len(tree.all_nodes), goal[:80])
            return tree

        # 1. Buscar en caché
        cache_key = hashlib.md5(goal.lower().encode()).hexdigest()[:16]
        if cache_key in self._decomposition_cache:
            cached = self._decomposition_cache[cache_key]
            log.info("♻️  Descomposición cacheada para: %s", goal[:80])
            # S93: Devolver COPIA FRESCA con estados reseteados
            # (el árbol cacheado tiene estados mutados por execute_tree)
            return self._fresh_copy(cached)

        # 2. Intentar memoria episódica
        episodic_tree = self._try_episodic_decomposition(goal)
        if episodic_tree:
            return episodic_tree

        # 3. Usar patrones deterministas
        pattern_name, pattern = DecompositionPatterns.match(goal)
        tree = self._build_tree_from_pattern(goal, pattern, pattern_name, max_depth)

        # 4. Si el patrón es genérico y hay LLM, refinar
        if pattern_name == "generic_explore" and self._use_llm:
            llm_tree = self._llm_refine_tree(goal, tree)
            if llm_tree:
                tree = llm_tree

        # Cachear
        self._decomposition_cache[cache_key] = tree
        log.info("🌳 Árbol creado: %d nodos para '%s' (patrón=%s)",
                len(tree.all_nodes), goal[:80], pattern_name)
        return tree

    def _fresh_copy(self, tree: GoalTree) -> GoalTree:
        """Crea una copia fresca del árbol con todos los estados reseteados a PENDING.

        S93: El árbol cacheado tiene estados mutados por execute_tree (COMPLETED, FAILED, etc.).
        Reusarlo directamente causa que get_next_task() retorne None prematuramente
        y progress_pct() reporte valores inflados.
        """
        import copy
        # Crear nuevos nodos con estado PENDING
        new_nodes = {}
        for nid, node in tree.all_nodes.items():
            new_node = GoalNode(
                id=node.id,
                goal=node.goal,
                depth=node.depth,
                parent_id=node.parent_id,
                status=GoalStatus.PENDING,  # Resetear estado
                plan_steps=copy.deepcopy(node.plan_steps),
                children=list(node.children),
                attempts=0,  # Resetear intentos
                max_attempts=node.max_attempts,
                created_at=time.time(),
            )
            new_nodes[nid] = new_node

        # Reconstruir relaciones
        new_root = new_nodes[tree.root.id]
        new_tree = GoalTree(
            root=new_root,
            all_nodes=new_nodes,
            created_at=time.time(),
            metadata=dict(tree.metadata),
        )
        return new_tree

    def _is_simple_goal(self, goal: str) -> bool:
        """Determina si una meta es lo bastante simple para no necesitar árbol.

        S94: Umbral subido de 5→8 palabras. Metas de navegación simple
        ("navega a X", "busca Y en Z") no necesitan 5 sub-metas.
        """
        import re
        words = goal.split()
        goal_lower = goal.lower()

        # 1. Metas muy cortas: siempre simples
        if len(words) <= 3:
            return True

        # 1.5 S94: Si tiene "y" conectando 2 acciones → NO es simple (irá a light_tree)
        if " y " in goal_lower and len(words) >= 5:
            return False

        # 2. Navegación simple: "navega a X", "abre Y", "ve a Z"
        simple_nav_patterns = [
            r'^(navega|abre|ve|anda|dir[íi]gete)\s+a\s+',
            r'^(busca|search|googlea)\s+\w+\s+en\s+',
            r'^ir\s+a\s+',
        ]
        for pat in simple_nav_patterns:
            if re.match(pat, goal_lower):
                return True

        # 3. Meta con URL explícita → navegación simple
        if 'http://' in goal_lower or 'https://' in goal_lower or '.com' in goal_lower or '.org' in goal_lower:
            # Si es solo navegar a un sitio, es simple
            if len(words) <= 7:
                return True

        # 4. Palabras de ALTA complejidad → necesitan descomposición completa
        high_complexity = [
            "investiga", "analiza", "evalúa", "compara",
            "extrae de", "sintetiza", "en varios sitios",
            "promedio", "cada uno", "todos los",
        ]
        for marker in high_complexity:
            if marker in goal_lower:
                return False

        # 5. Palabras de complejidad MEDIA → árbol pequeño (2-3 nodos)
        medium_complexity = [
            "varios", "múltiples", "y además", "luego",
            "después", "primero", "también",
        ]
        for marker in medium_complexity:
            if marker in goal_lower:
                return False  # No es simple, pero usará patrón más ligero

        # 6. Umbral general: ≤8 palabras sin marcadores → simple
        return len(words) <= 8

    def _simple_tree(self, goal: str) -> GoalTree:
        """Crea un árbol de un solo nodo para metas simples."""
        root = GoalNode(
            id="root",
            goal=goal,
            depth=0,
            created_at=time.time(),
        )
        tree = GoalTree(root=root, all_nodes={"root": root}, created_at=time.time())
        return tree

    def _is_medium_goal(self, goal: str) -> bool:
        """S94: Metas de complejidad media — 2 acciones conectadas con 'y'.

        No incluye metas con marcadores de alta complejidad (esas van a árbol completo).
        """
        goal_lower = goal.lower()
        words = goal.split()

        # Excluir si tiene marcadores de alta complejidad
        high_markers = ["investiga", "analiza", "evalúa", "compara", "extrae de",
                       "sintetiza", "promedio", "cada uno", "todos los"]
        for m in high_markers:
            if m in goal_lower:
                return False

        # Si tiene "y" conectando 2 acciones simples → medio
        if " y " in goal_lower and len(words) <= 12:
            return True
        # Si tiene "luego" o "después" → medio
        if any(m in goal_lower for m in ["luego", "después", "también"]):
            return True
        return False

    def _light_tree(self, goal: str) -> GoalTree:
        """S94: Árbol ligero de 2-3 nodos para metas de complejidad media.

        vs generic_explore (5 nodos): navegar→explorar→extraer→links→sintetizar
        light_tree (2-3 nodos):    navegar+buscar → extraer
        """
        root = GoalNode(id="root", goal=goal, depth=0, created_at=time.time())
        tree = GoalTree(root=root, all_nodes={"root": root},
                       created_at=time.time())
        tree.metadata["source"] = "pattern:light_tree"

        query = self._extract_query(goal)

        # Nodo 1: Navegar y buscar
        nav_node = GoalNode(
            id="sg_0",
            goal=f"Navegar y buscar: {query}" if query else "Navegar y buscar información",
            depth=1,
            parent_id="root",
            plan_steps=[
                {"action": "navigate", "target": "barra_direcciones",
                 "text": query if query else goal},
                {"action": "wait", "target": "carga"},
            ],
            created_at=time.time(),
        )
        root.children.append(nav_node.id)

        # Nodo 2: Extraer información
        ext_node = GoalNode(
            id="sg_1",
            goal="Extraer información encontrada",
            depth=1,
            parent_id="root",
            plan_steps=[
                {"action": "scroll", "target": "contenido"},
                {"action": "extract", "target": "información relevante"},
            ],
            created_at=time.time(),
        )
        root.children.append(ext_node.id)

        tree.all_nodes["sg_0"] = nav_node
        tree.all_nodes["sg_1"] = ext_node
        return tree

    def _try_episodic_decomposition(self, goal: str) -> Optional[GoalTree]:
        """Intenta recuperar una descomposición previa de la memoria episódica."""
        try:
            from core.screen_episodic_memory import get_screen_episodic_memory
            sem = get_screen_episodic_memory()
            plan = sem.find_similar_plan(goal, min_confidence=0.6)
            if plan and plan.get("times_reused", 0) >= 1:
                log.info("♻️  Descomposición recuperada de memoria episódica")
                return self._tree_from_plan(goal, plan)
        except Exception:
            pass
        return None

    def _tree_from_plan(self, goal: str, plan: Dict[str, Any]) -> GoalTree:
        """Construye GoalTree desde un plan de memoria episódica."""
        root = GoalNode(
            id="root",
            goal=goal,
            depth=0,
            plan_steps=plan.get("steps", []),
            created_at=time.time(),
        )
        tree = GoalTree(root=root, all_nodes={"root": root}, created_at=time.time())
        tree.metadata["source"] = "episodic_memory"
        tree.metadata["success_rate"] = plan.get("success_rate", 0.5)
        return tree

    def _build_tree_from_pattern(self, goal: str, pattern: Dict,
                                 pattern_name: str,
                                 max_depth: int) -> GoalTree:
        """Construye GoalTree desde un patrón de descomposición."""
        root = GoalNode(
            id="root",
            goal=goal,
            depth=0,
            created_at=time.time(),
        )
        tree = GoalTree(root=root, all_nodes={"root": root},
                       created_at=time.time())
        tree.metadata["source"] = f"pattern:{pattern_name}"

        # Extraer query de la meta
        query = self._extract_query(goal)

        template = pattern.get("template", {})
        subgoals = template.get("subgoals", [])

        for i, sg in enumerate(subgoals):
            depth = min(sg.get("depth", 1), max_depth)
            if depth > max_depth:
                continue

            # Reemplazar {query} en el texto
            subgoal_text = sg["goal"].replace("{query}", query)
            subgoal_plan = [
                {k: v.replace("{query}", query) if isinstance(v, str) else v
                 for k, v in step.items()}
                for step in sg.get("plan", [])
            ]

            node_id = f"sg_{i}"
            node = GoalNode(
                id=node_id,
                goal=subgoal_text,
                depth=depth,
                parent_id="root",
                plan_steps=subgoal_plan,
                created_at=time.time(),
            )
            root.children.append(node)
            tree.all_nodes[node_id] = node

        return tree

    def _extract_query(self, goal: str) -> str:
        """Extrae el término de búsqueda de una meta."""
        # Quitar palabras de acción comunes
        clean = goal.lower()
        for word in ["investiga", "busca", "encuentra", "extrae", "analiza",
                     "calcula", "compara", "el precio de", "la documentación de",
                     "información sobre", "datos de", "en 3 sitios", "en varios",
                     "promedio", "investigar", "buscar", "encontrar", "extraer"]:
            clean = clean.replace(word, "")
        # Limpiar puntuación
        clean = re.sub(r'[^\w\s]', '', clean)
        clean = ' '.join(clean.split())
        return clean.strip() or goal

    def _llm_refine_tree(self, goal: str, tree: GoalTree) -> Optional[GoalTree]:
        """Refina la descomposición usando LLM."""
        try:
            from core.eidos_deepseek import ask_deepseek

            system = """Eres un planificador jerárquico. Descompón esta meta en submetas.
Responde SOLO con un JSON:
{"subgoals": [{"goal": "...", "depth": 1, "steps": [{"action": "...", "target": "..."}]}]}
depth: 1 = raíz directa, 2 = sub-submeta. Máximo depth 2."""
            response = ask_deepseek(system, goal, model="lite", max_tokens=1024)
            json_match = re.search(r'\{.*\}', response["text"], re.DOTALL)
            if json_match:
                data = json.loads(json_match.group(0))
                # Reconstruir árbol
                root = GoalNode(id="root", goal=goal, depth=0,
                               created_at=time.time())
                new_tree = GoalTree(root=root, all_nodes={"root": root},
                                   created_at=time.time())
                new_tree.metadata["source"] = "llm_refined"

                for i, sg in enumerate(data.get("subgoals", [])[:8]):
                    depth = min(sg.get("depth", 1), self.MAX_DEPTH)
                    node = GoalNode(
                        id=f"llm_{i}",
                        goal=sg["goal"],
                        depth=depth,
                        parent_id="root",
                        plan_steps=sg.get("steps", []),
                        created_at=time.time(),
                    )
                    root.children.append(node)
                    new_tree.all_nodes[f"llm_{i}"] = node

                log.info("🤖 Árbol refinado por LLM: %d submetas",
                        len(root.children))
                return new_tree
        except Exception as e:
            log.debug("LLM refine falló: %s", e)
        return None

    # ── Ejecución del árbol ─────────────────────────────────────────────────────

    def execute_tree(self, tree: GoalTree,
                     screen_controller: Any = None,
                     step_callback: callable = None,
                     max_total_steps: int = 50) -> Dict[str, Any]:
        """Ejecuta un árbol de metas completo.

        Args:
            tree: GoalTree a ejecutar
            screen_controller: instancia de ScreenController
            step_callback: callback por cada paso
            max_total_steps: máximo de pasos totales

        Returns:
            Dict con resultados de la ejecución
        """
        if screen_controller is None:
            from core.screen_controller import get_screen_controller
            screen_controller = get_screen_controller(dry_run=True)

        t0 = time.time()
        total_steps = 0
        completed_nodes = 0
        failed_nodes = 0
        all_results = {}

        log.info("🌳 Ejecutando árbol: %s (%d nodos)",
                tree.root.goal[:80], len(tree.all_nodes))

        while total_steps < max_total_steps:
            # Obtener siguiente tarea
            node = tree.get_next_task()
            if node is None:
                # Verificar si el árbol está completo
                if tree.progress_pct() >= 1.0:
                    break
                # Buscar nodos failed que puedan reintentarse
                stuck_nodes = [n for n in tree.all_nodes.values()
                              if n.status == GoalStatus.FAILED
                              and n.attempts < n.max_attempts]
                if stuck_nodes:
                    node = stuck_nodes[0]
                    node.status = GoalStatus.PENDING
                    log.info("🔄 Reintentando nodo failed: %s", node.goal[:80])
                else:
                    break

            # Ejecutar la submeta
            node.status = GoalStatus.IN_PROGRESS
            node.attempts += 1

            try:
                # Construir mini-plan para esta submeta
                steps = node.plan_steps if node.plan_steps else [
                    {"action": "navigate", "target_description": "buscar información",
                     "target_text": node.goal}
                ]

                # Ejecutar como misión ScreenController
                # Usamos el screen_controller directamente para cada submeta
                if hasattr(screen_controller, 'execute_mission'):
                    result = screen_controller.execute_mission(
                        goal=node.goal,
                        max_steps=min(10, max_total_steps - total_steps),
                        step_callback=step_callback,
                        _disable_s90=True,  # Evitar recursión infinita HGD
                    )
                else:
                    result = {"completed": True, "total_steps": 0}

                total_steps += result.get("total_steps", 1)

                if result.get("completed", False):
                    node.status = GoalStatus.COMPLETED
                    node.result = result
                    node.extracted_data = result.get("extracted_knowledge", [])
                    node.completed_at = time.time()
                    completed_nodes += 1
                    log.info("✅ Nodo completado: %s (%d pasos)",
                            node.goal[:60], result.get("total_steps", 0))
                else:
                    if node.attempts < node.max_attempts:
                        node.status = GoalStatus.PENDING
                        # Adaptar plan
                        adapted = self._adapt_node(node, tree)
                        if adapted:
                            node.adaptation_history.append(adapted)
                    else:
                        node.status = GoalStatus.FAILED
                        failed_nodes += 1
                        log.warning("❌ Nodo fallido tras %d intentos: %s",
                                   node.attempts, node.goal[:60])

            except Exception as e:
                log.error("Error ejecutando nodo %s: %s", node.id, e)
                if node.attempts >= node.max_attempts:
                    node.status = GoalStatus.FAILED
                    failed_nodes += 1
                else:
                    node.status = GoalStatus.PENDING

            # Callback: pasar firma completa (step, decision, result, scene)
            # para compatibilidad con MissionRunner._step_callback
            if step_callback:
                try:
                    step_callback(
                        total_steps,  # step: int
                        {  # decision: Dict con info del nodo HGD
                            "action": "hgd_node",
                            "goal": node.goal,
                            "status": node.status.value,
                            "depth": node.depth,
                            "attempt": node.attempts,
                            "progress_pct": tree.progress_pct(),
                        },
                        result,  # result: Dict de execute_mission
                        None,  # scene: HGD no captura escena
                    )
                except Exception:
                    pass  # Callback no debe interrumpir la ejecución del árbol

            # Verificar hopeless
            if failed_nodes > len(tree.all_nodes) * 0.5:
                log.warning("💀 Árbol hopeless: %d/%d nodos fallidos",
                           failed_nodes, len(tree.all_nodes))
                break

        # Sintetizar resultados
        elapsed = time.time() - t0
        all_results = {
            "root_goal": tree.root.goal,
            "completed": tree.progress_pct() >= 1.0,
            "progress_pct": round(tree.progress_pct() * 100, 1),
            "total_nodes": len(tree.all_nodes),
            "completed_nodes": completed_nodes,
            "failed_nodes": failed_nodes,
            "total_steps_executed": total_steps,
            "elapsed_total": round(elapsed, 3),
            "extracted_data": self._collect_extracted_data(tree),
            "node_results": {
                nid: {
                    "goal": n.goal[:80],
                    "status": n.status.value,
                    "attempts": n.attempts,
                    "depth": n.depth,
                }
                for nid, n in tree.all_nodes.items()
            },
        }

        log.info("🌳 Árbol completado: %.0f%% (%d/%d nodos) en %.1fs",
                tree.progress_pct() * 100, completed_nodes,
                len(tree.all_nodes), elapsed)

        return all_results

    def _adapt_node(self, node: GoalNode, tree: GoalTree) -> Optional[str]:
        """Adapta un nodo fallido: cambia el plan o crea submetas alternativas."""
        # Estrategia 1: Simplificar el plan (quitar pasos fallidos)
        if node.plan_steps and len(node.plan_steps) > 1:
            # Quitar primer paso (probablemente causó el fallo) y reintentar
            node.plan_steps = node.plan_steps[1:]
            return f"simplified_plan: removed first step, {len(node.plan_steps)} remaining"

        # Estrategia 2: Usar plan genérico de exploración
        node.plan_steps = [
            {"action": "navigate", "target": "google"},
            {"action": "type", "target": node.goal},
            {"action": "wait", "target": "carga"},
            {"action": "extract", "target": "información"},
        ]
        return "fallback_generic_plan"

    def _collect_extracted_data(self, tree: GoalTree) -> List[Dict[str, Any]]:
        """Recolecta datos extraídos de todos los nodos completados."""
        data = []
        for node in tree.all_nodes.values():
            if node.status == GoalStatus.COMPLETED and node.extracted_data:
                if isinstance(node.extracted_data, list):
                    data.extend(node.extracted_data)
                elif isinstance(node.extracted_data, dict):
                    data.append(node.extracted_data)
        return data

    # ── Stats ─────────────────────────────────────────────────────────────────

    def stats(self) -> Dict[str, Any]:
        return {
            "cached_decompositions": len(self._decomposition_cache),
            "patterns_available": len(DecompositionPatterns.PATTERNS),
            "max_depth": self.MAX_DEPTH,
        }


# ── Singleton ─────────────────────────────────────────────────────────────────
_hgd: Optional[HierarchicalGoalDecomposer] = None


def get_hgd() -> HierarchicalGoalDecomposer:
    global _hgd
    if _hgd is None:
        _hgd = HierarchicalGoalDecomposer()
    return _hgd


# ── CLI ───────────────────────────────────────────────────────────────────────
if __name__ == "__main__":
    import argparse
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")

    p = argparse.ArgumentParser(
        description="HierarchicalGoalDecomposer — descomposición jerárquica de metas"
    )
    p.add_argument("--decompose", type=str, help="Descomponer meta en submetas")
    p.add_argument("--test", action="store_true", help="Test rápido")
    p.add_argument("--stats", action="store_true", help="Estadísticas")
    args = p.parse_args()

    hgd = get_hgd()

    if args.decompose:
        tree = hgd.decompose(args.decompose, max_depth=2)
        print(f"Meta: {args.decompose}")
        print(f"Patrón: {tree.metadata.get('source', 'unknown')}")
        print(f"Nodos: {len(tree.all_nodes)}")
        print(f"\nÁrbol:")
        for nid, node in tree.all_nodes.items():
            indent = "  " * node.depth
            print(f"{indent}[{node.depth}] {node.goal}")
            if node.plan_steps:
                for step in node.plan_steps[:3]:
                    print(f"{indent}  → {step.get('action', '?')}: {step.get('target', '')[:50]}")

    elif args.test:
        # Test con varias metas
        tests = [
            "busca n8n en google",
            "encuentra el precio promedio de n8n en 3 sitios",
            "investiga la documentación de docker compose y aprende a usarlo",
            "buscar python",
        ]
        for goal in tests:
            tree = hgd.decompose(goal, max_depth=2)
            pattern = tree.metadata.get("source", "?")
            simple = "SIMPLE" if len(tree.all_nodes) == 1 else f"{len(tree.all_nodes)} nodos"
            print(f"{simple:12s} | {pattern:25s} | {goal[:60]}")

    elif args.stats:
        import json
        print(json.dumps(hgd.stats(), indent=2))

    else:
        p.print_help()
