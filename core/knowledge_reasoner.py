"""
core/knowledge_reasoner.py — Motor de razonamiento semántico para EIDOS

Transforma knowledge_nodes planos en una red semántica viva con capacidad de:
  - Construir grafos de conceptos (co-ocurrencia, categoría, fuente)
  - Razonar por inferencia: A→B + B→C ⇒ A→C
  - Responder preguntas combinando múltiples nodos
  - Generar nuevo conocimiento inferido (source="reasoned")
  - Auto-evolucionar: detectar gaps y llenarlos
  - Activación neuronal: spreading activation + hebbian learning + decay

Arquitectura neuronal:
  1. Cada nodo es una neurona semántica con potencial de activación
  2. Cada arista es una sinapsis con peso dinámico
  3. Las neuronas se activan por spreading activation desde consultas
  4. Las sinapsis se fortalecen por aprendizaje hebbiano
  5. Las conexiones no usadas decaen con el tiempo
  6. Cada neurona tiene período refractario tras activarse

Uso:
    from core.knowledge_reasoner import get_reasoner
    r = get_reasoner()
    respuesta = r.reason("qué es docker?")
    r.evolve()  # ciclo de evolución
"""
from __future__ import annotations

import json
import logging
import math
import os
import random
import re
import sqlite3
import threading
import time
import traceback
from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import Path

from core.paths import REPO_ROOT
from typing import Any, Dict, List, Optional, Set, Tuple
from difflib import SequenceMatcher
from core.db import get_conn

log = logging.getLogger("eidos.reasoner")

BRAIN_DB = Path.home() / ".eidos" / "evolution_brain.db"
INFERRED_SOURCE = "reasoned"
EVOLVE_INTERVAL = 1800  # 30 min entre ciclos de evolución

# ── Constantes neuronales ─────────────────────────────────────────────────────
NEURON_RESTING_POTENTIAL = 0.1      # Potencial en reposo
NEURON_FIRE_THRESHOLD = 0.7         # Umbral para disparar
NEURON_MAX_POTENTIAL = 1.0          # Potencial máximo
NEURON_DECAY_RATE = 0.1             # Decaimiento por segundo
NEURON_REFRACTORY_PERIOD = 5        # Segundos sin activarse tras disparo
NEURON_ACTIVATION_BOOST = 0.15      # Incremento al ser activado por query (menor = menos spread)
SYNAPSE_STRENGTHEN_RATE = 0.05      # Cuánto se fortalece una sinapsis al co-disparar
SYNAPSE_DECAY_RATE = 0.01           # Decaimiento sináptico por hora
SYNAPSE_MIN_WEIGHT = 0.05           # Peso mínimo antes de podar
HEBBIAN_WINDOW = 5.0                # Ventana de tiempo para co-activación (segundos)
MAX_SPREAD_DEPTH = 2                # S72: multi-salto (1→2) razonamiento más profundo, decay por salto

# ── Filtros de calidad para respuestas al usuario ─────────────────────────────
# Fuentes y categorías que son ruido para consultas del usuario
_EXCLUDED_SOURCES_FOR_QUERY = {
    "wordnet",          # 291K nodos con definiciones vacías o de 1 palabra
    "code_analyzer",    # Código interno de EIDOS
    "graphify",         # Estructura de código (calls, imports)
    "tabula_rasa:path_scan",  # Escaneo de archivos
    "tabula_rasa:ast",  # AST de código
    "self_index:class", "self_index:function", "self_index:module",
    "char:colony_centinela:metrics",
    "kali_tools",       # Descripciones de paquetes APT
    "research:duckduckgo",  # 1,145 definiciones con prefijo "DuckDuckGo: " — basura
}
_EXCLUDED_CATEGORIES_FOR_QUERY = {
    "dictionary",       # 174K nodos, avg 8 chars
    "synset",           # 117K nodos, definiciones vacías
    "code_structure",   # 9.5K nodos, estructura de código
    "eidos_function",   # Funciones internas
    "eidos_class",      # Clases internas
    "eidos_module",     # Módulos internos
    "system_command",   # Help de comandos
}
_MIN_DEFINITION_LENGTH = 30  # Definiciones más cortas que esto son basura
_BAD_DEFINITION_PREFIXES = (
    "=== help ===", "Documentación de '", "Paquete APT:", "DuckDuckGo: ",
    "[DEEP RESEARCH:", "core/", "Descubierto desde:",
)

REL_CO_OCCUR = "co_occur"
REL_CATEGORY = "same_category"
REL_SOURCE = "same_source"
REL_KEYWORD = "keyword_overlap"
REL_IMPLIES = "implies"
REL_IS_A = "is_a"
REL_HAS = "has_property"
REL_PART_OF = "part_of"

_STOP_WORDS = {
    # Español
    "que", "del", "las", "los", "con", "por", "para", "como", "qué", "cómo",
    "puede", "todo", "esta", "este", "más", "eso", "esa", "entre", "tiene",
    "ella", "ello", "ellos", "pero", "sino", "aunque", "porque", "cuando",
    "donde", "quien", "cual", "es", "el", "la", "un", "una", "se", "no",
    "al", "le", "su", "de", "en", "ha", "lo", "si", "ya", "son", "ser",
    "hay", "era", "fue", "han", "tan", "vez", "cada", "muy", "solo",
    "también", "entonces", "mientras", "hasta", "desde", "sobre", "bajo",
    # English
    "the", "this", "that", "what", "which", "when", "where", "why", "how",
    "can", "could", "will", "would", "shall", "should", "may", "might",
    "must", "has", "have", "had", "was", "were", "been", "is", "are", "am",
    "it", "its", "do", "does", "did", "to", "of", "in", "on", "at", "by",
    "for", "from", "with", "without", "about", "into", "through", "during",
    "before", "after", "above", "below", "between", "if", "and", "or", "not",
    "but", "than", "then", "also", "very", "just", "only", "some", "any",
}
MIN_CONFIDENCE = 0.15  # Umbral mínimo de confianza para considerar un match válido

def _is_quality_definition(source: str, category: str, definition: str) -> bool:
    """Determina si una definición es apta para mostrarse al usuario."""
    if not definition or len(definition) < _MIN_DEFINITION_LENGTH:
        return False
    if source in _EXCLUDED_SOURCES_FOR_QUERY:
        return False
    # Permitir graphify SOLO si la categoría NO es code_structure
    if source == "graphify" and category == "code_structure":
        return False
    if category in _EXCLUDED_CATEGORIES_FOR_QUERY:
        return False
    for prefix in _BAD_DEFINITION_PREFIXES:
        if definition.startswith(prefix):
            return False
    # Excluir definiciones que son código fuente
    if definition.startswith("def ") or definition.startswith("class "):
        return False
    return True

def _is_quality_source_category(source: str, category: str) -> bool:
    """Determina si source+category son aptos para construir el grafo de consultas."""
    if source in _EXCLUDED_SOURCES_FOR_QUERY:
        return False
    if source == "graphify" and category == "code_structure":
        return False
    if category in _EXCLUDED_CATEGORIES_FOR_QUERY:
        return False
    return True
# Palabras de enlace para inferir relaciones semánticas
IS_A_PATTERNS = re.compile(
    r'\b(es\s+un|es\s+una|son\s+un|son\s+una|is\s+a|is\s+an|are\s+a|'
    r'tipo\s+de|type\s+of|kind\s+of|forma\s+de|forma\s+de)\b',
    re.IGNORECASE
)
HAS_PATTERNS = re.compile(
    r'\b(tiene|contiene|incluye|posee|has|contains|includes|posee)\b',
    re.IGNORECASE
)
PART_OF_PATTERNS = re.compile(
    r'\b(parte\s+de|componente\s+de|miembro\s+de|part\s+of|component\s+of)\b',
    re.IGNORECASE
)
IMPLIES_PATTERNS = re.compile(
    r'\b(significa|implica|permite|permits|allows|enables|means|implies)\b',
    re.IGNORECASE
)


@dataclass
class ConceptNode:
    """Una neurona semántica en el grafo de conocimiento.
    
    Cada nodo es una neurona con:
    - activation: potencial de activación actual (0.0–1.0)
    - last_fired: timestamp del último disparo
    - fire_count: cuántas veces se ha activado
    - refractory_until: timestamp hasta cuando está en período refractario
    """
    id: str
    concept: str
    definition: str
    category: str = ""
    source: str = ""
    confidence: float = 0.5
    metadata: Dict[str, Any] = field(default_factory=dict)
    tokens: Set[str] = field(default_factory=set)
    activation: float = NEURON_RESTING_POTENTIAL
    last_fired: float = 0.0
    fire_count: int = 0
    refractory_until: float = 0.0


@dataclass
class RelationEdge:
    """Sinapsis entre dos neuronas semánticas.
    
    Cada arista es una sinapsis con:
    - weight: peso sináptico dinámico (0.0–1.0)
    - last_activated: timestamp de última co-activación
    - strengthen_count: cuántas veces se ha fortalecido
    """
    source_id: str
    target_id: str
    rel_type: str
    weight: float = 0.5
    last_activated: float = 0.0
    strengthen_count: int = 0


@dataclass
class InferenceResult:
    """Resultado de una inferencia."""
    concept: str
    definition: str
    source_concepts: List[str]
    confidence: float
    reasoning_path: List[str]


class SemanticGraph:
    """Red semántica neuronal con nodos como neuronas y aristas como sinapsis."""

    def __init__(self):
        self.nodes: Dict[str, ConceptNode] = {}
        self.edges: List[RelationEdge] = []
        self._adj: Dict[str, List[Tuple[str, str, float, int]]] = defaultdict(list)
        self._concept_index: Dict[str, str] = {}
        self._built = False
        self._neural_tick = 0
        self._last_global_decay: float = time.time()
        self._active_set: Set[str] = set()  # IDs de neuronas con activación > resting

    def add_node(self, node: ConceptNode):
        self.nodes[node.id] = node
        self._concept_index[node.concept.lower().strip()] = node.id

    def add_edge(self, edge: RelationEdge):
        self.edges.append(edge)
        self._adj[edge.source_id].append((edge.target_id, edge.rel_type, edge.weight, len(self.edges) - 1))
        self._adj[edge.target_id].append((edge.source_id, edge.rel_type, edge.weight, len(self.edges) - 1))

    def get_node_by_concept(self, concept: str) -> Optional[ConceptNode]:
        nid = self._concept_index.get(concept.lower().strip())
        return self.nodes.get(nid) if nid else None

    def find_similar_concepts(self, text: str, top_k: int = 10) -> List[Tuple[ConceptNode, float]]:
        if not self._built:
            return []
        query_tokens = set(re.findall(r'\w{3,}', text.lower()))
        scored = []
        for node in self.nodes.values():
            if not node.tokens:
                continue
            overlap = len(query_tokens & node.tokens)
            if overlap > 0:
                sim = overlap / max(len(query_tokens | node.tokens), 1)
                if sim > 0.05:
                    scored.append((node, sim * node.confidence))
        scored.sort(key=lambda x: -x[1])
        return scored[:top_k]

    def get_related(self, node_id: str, max_depth: int = 2,
                    max_results: int = 50) -> List[Tuple[ConceptNode, str, float, int]]:
        visited = set()
        results = []
        queue = [(node_id, 0)]
        visited.add(node_id)
        while queue and len(results) < max_results:
            current, depth = queue.pop(0)
            if depth > max_depth:
                continue
            for neighbor, rel_type, weight, _ in self._adj.get(current, []):
                if neighbor not in visited and len(results) < max_results:
                    visited.add(neighbor)
                    if neighbor in self.nodes:
                        results.append((self.nodes[neighbor], rel_type, weight, depth + 1))
                    if depth + 1 <= max_depth:
                        queue.append((neighbor, depth + 1))
        return results

    # ── Métodos neuronales ───────────────────────────────────────────────────

    def fire_neuron(self, node_id: str, boost: float = NEURON_ACTIVATION_BOOST,
                    depth: int = 0) -> List[str]:
        """Dispara una neurona con spreading activation controlado."""
        now = time.time()
        node = self.nodes.get(node_id)
        if not node or now < node.refractory_until:
            return []

        node.activation = min(node.activation + boost, NEURON_MAX_POTENTIAL)
        node.last_fired = now
        node.fire_count += 1
        node.refractory_until = now + NEURON_REFRACTORY_PERIOD
        self._active_set.add(node_id)

        activated = [node_id]
        if node.activation >= NEURON_FIRE_THRESHOLD and depth < MAX_SPREAD_DEPTH:
            node.activation = NEURON_RESTING_POTENTIAL
            for neighbor, rel_type, weight, edge_idx in self._adj.get(node_id, []):
                neighbor_node = self.nodes.get(neighbor)
                if not neighbor_node or now < neighbor_node.refractory_until:
                    continue
                spread = boost * weight * 0.3
                neighbor_node.activation = min(neighbor_node.activation + spread, NEURON_MAX_POTENTIAL)
                self.edges[edge_idx].last_activated = now
                self.edges[edge_idx].strengthen_count += 1
                self.edges[edge_idx].weight = min(
                    self.edges[edge_idx].weight + SYNAPSE_STRENGTHEN_RATE, 1.0
                )
                if neighbor_node.activation >= NEURON_FIRE_THRESHOLD:
                    activated.extend(
                        self.fire_neuron(neighbor, boost=boost * 0.5, depth=depth + 1)
                    )
        return activated

    def get_active_neurons(self, threshold: float = 0.2, max_results: int = 20) -> List[Tuple[str, float]]:
        """Devuelve neuronas activas sobre threshold, solo escaneando las que han sido activadas."""
        now = time.time()
        result = []
        dead = []
        for nid in self._active_set:
            node = self.nodes.get(nid)
            if not node:
                dead.append(nid)
                continue
            if node.last_fired > 0:
                elapsed = now - node.last_fired
                node.activation = max(node.activation - elapsed * NEURON_DECAY_RATE, NEURON_RESTING_POTENTIAL)
            if node.activation <= NEURON_RESTING_POTENTIAL + 0.01:
                dead.append(nid)
            elif node.activation >= threshold and now >= node.refractory_until:
                result.append((nid, node.activation))
        for nid in dead:
            self._active_set.discard(nid)
        result.sort(key=lambda x: -x[1])
        return result[:max_results]

    def global_decay(self):
        """Decaimiento global: reduce pesos sinápticos no usados y poda."""
        now = time.time()
        pruned = 0
        for i, edge in enumerate(self.edges):
            if edge.last_activated > 0:
                elapsed = now - edge.last_activated
                if elapsed > 3600:  # >1 hora sin uso
                    decay = elapsed / 3600 * SYNAPSE_DECAY_RATE
                    edge.weight = max(edge.weight - decay, SYNAPSE_MIN_WEIGHT)
        # Poda de sinapsis muy débiles
        self.edges = [e for e in self.edges if e.weight >= SYNAPSE_MIN_WEIGHT]
        # Reconstruir _adj
        self._adj.clear()
        for i, edge in enumerate(self.edges):
            self._adj[edge.source_id].append((edge.target_id, edge.rel_type, edge.weight, i))
            self._adj[edge.target_id].append((edge.source_id, edge.rel_type, edge.weight, i))
        self._last_global_decay = now
        log.info("Neural decay: %d sinapsis, pruned weak edges", len(self.edges))

    def reset_neuron_state(self):
        """Resetea toda activación neuronal y refractory para determinismo en consultas."""
        now = time.time()
        for node in self.nodes.values():
            node.activation = NEURON_RESTING_POTENTIAL
            node.refractory_until = 0.0
        self._active_set.clear()
        self._neural_tick = 0

    def hebbian_learn(self, concept_a: str, concept_b: str):
        """Refuerza la conexión entre dos conceptos que aparecen juntos."""
        node_a = self.get_node_by_concept(concept_a)
        node_b = self.get_node_by_concept(concept_b)
        if not node_a or not node_b or node_a.id == node_b.id:
            return
        # Buscar si existe arista
        for edge in self.edges:
            if (edge.source_id == node_a.id and edge.target_id == node_b.id) or \
               (edge.source_id == node_b.id and edge.target_id == node_a.id):
                edge.weight = min(edge.weight + SYNAPSE_STRENGTHEN_RATE, 1.0)
                edge.strengthen_count += 1
                edge.last_activated = time.time()
                return
        # No existe arista, crear una nueva
        self.add_edge(RelationEdge(
            source_id=node_a.id, target_id=node_b.id,
            rel_type=REL_KEYWORD, weight=0.3,
            last_activated=time.time(), strengthen_count=1
        ))

    @property
    def size(self) -> Tuple[int, int]:
        return len(self.nodes), len(self.edges)


class KnowledgeReasoner:
    """Motor de razonamiento principal. Mantiene grafo + ciclos de evolución."""

    def __init__(self, auto_build: bool = True):
        self.graph = SemanticGraph()
        self._lock = threading.Lock()
        self._ready = False
        self._building = False
        self._build_error: Optional[str] = None
        self._last_evolve: float = 0
        self._inferred_count = 0
        self._kw_index = None
        # S68-P2: lock + cooldown para evitar tormenta de rebuilds
        self._build_lock = threading.Lock()
        self._last_build_time: float = 0
        self._build_call_history: list = []  # últimos 100 intentos con stack
        self._BUILD_COOLDOWN_S = 300  # 5 min entre builds
        if auto_build:
            t = threading.Thread(target=self._build_async, daemon=True,
                                 name="reasoner-build")
            t.start()

    def _record_build_attempt(self, skipped: bool, reason: str = None):
        """S68-P2: registra cada intento de build (incluso abortado) con stack."""
        import traceback as _tb
        entry = {
            "ts": time.time(),
            "skipped": skipped,
            "reason": reason or "started",
            "stack": _tb.format_stack(limit=5),
        }
        self._build_call_history.append(entry)
        if len(self._build_call_history) > 100:
            self._build_call_history = self._build_call_history[-100:]

    def get_recent_build_calls(self, n: int = 20) -> list:
        return self._build_call_history[-n:]

    def _build_async(self):
        """Construye el grafo en background — S68-P2: lock + cooldown 5min."""
        # Lock no bloqueante: si otro build corre, abortamos
        if not self._build_lock.acquire(blocking=False):
            self._record_build_attempt(skipped=True, reason="lock_busy")
            return
        try:
            now = time.time()
            elapsed = now - self._last_build_time
            if elapsed < self._BUILD_COOLDOWN_S and self._last_build_time > 0:
                self._record_build_attempt(
                    skipped=True,
                    reason=f"cooldown ({int(self._BUILD_COOLDOWN_S - elapsed)}s left)",
                )
                return
            self._last_build_time = now
            self._record_build_attempt(skipped=False)
            self._building = True
            try:
                self.build_graph()
            except Exception as e:
                self._build_error = str(e)
                log.error("build_async error: %s", e)
            finally:
                self._building = False
        finally:
            self._build_lock.release()

    def inject_node(self, node_id: str, concept: str, metadata: Optional[dict] = None) -> bool:
        """Inyecta un nodo directamente en el grafo. Retorna True si fue nuevo."""
        try:
            n = self.graph.get_node_by_concept(concept) or self.graph.nodes.get(node_id)
            if n:
                return False
            node = ConceptNode(id=node_id, concept=concept, definition="",
                               category="injected", source="vivo",
                               confidence=0.7, metadata=metadata or {})
            self.graph.add_node(node)
            return True
        except Exception:
            return False

    def inject_edge(self, source_concept: str, target_concept: str, weight: float = 0.7) -> bool:
        """Crea una arista entre dos conceptos en el grafo."""
        try:
            src = self.graph.get_node_by_concept(source_concept.lower().strip())
            tgt = self.graph.get_node_by_concept(target_concept.lower().strip())
            if src and tgt:
                edge = RelationEdge(source_id=src.id, target_id=tgt.id,
                                    rel_type="related", weight=weight)
                self.graph.add_edge(edge)
                return True
            return False
        except Exception:
            return False

    def evolve_from_text(self, topic: str, text: str) -> int:
        """Evoluciona el grafo extrayendo conceptos de un texto y creando aristas."""
        count = 0
        try:
            import re
            tokens = set(re.findall(r'\w{3,}', text.lower()))
            topic_node = self.graph.get_node_by_concept(topic.lower().strip())
            if topic_node:
                for token in tokens:
                    if token == topic.lower().strip():
                        continue
                    other = self.graph.get_node_by_concept(token)
                    if other:
                        edge = RelationEdge(source_id=topic_node.id, target_id=other.id,
                                            rel_type="related", weight=0.5)
                        self.graph.add_edge(edge)
                        count += 1
        except Exception:
            pass
        return count

    def build_graph(self) -> int:
        """Construye el grafo semántico desde brain.db.

        Filtra fuentes basura (wordnet, dictionary, synset, código interno)
        para que el grafo de consultas solo contenga definiciones de calidad.
        """
        try:
            conn = get_conn(BRAIN_DB, timeout=5)
            # Excluir fuentes y categorías que son ruido para consultas
            excluded_sources_sql = "','".join(_EXCLUDED_SOURCES_FOR_QUERY)
            excluded_cats_sql = "','".join(_EXCLUDED_CATEGORIES_FOR_QUERY)
            query = f"""
                SELECT id, concept, definition, category, source, confidence
                FROM knowledge_nodes
                WHERE confidence >= 0.2
                  AND source != 'reasoned'
                  AND source NOT IN ('{excluded_sources_sql}')
                  AND category NOT IN ('{excluded_cats_sql}')
                  AND LENGTH(definition) >= {_MIN_DEFINITION_LENGTH}
                ORDER BY confidence DESC
                LIMIT 50000
            """
            rows = conn.execute(query).fetchall()
            log.info("build_graph: SQL query returned %d rows (filtered garbage sources/categories)", len(rows))

            # S76: Cargar aristas estructurales persistidas por Graphify bridge
            structural_edges = conn.execute(
                "SELECT from_node, to_node, relation_type, strength FROM knowledge_edges "
                "WHERE relation_type IN ('calls','imports','imports_from','contains',"
                "'inherits','defines_method','uses','overrides','implements')"
            ).fetchall()
            log.info("build_graph: %d structural edges loaded", len(structural_edges))

            # S110: Cargar aristas SEMÁNTICAS (synonym, hypernym, part_of, antonym, etc.)
            # Estos tipos ya existen en knowledge_edges pero build_graph() nunca los cargaba.
            # Son cruciales para razonamiento evaluativo (antonym), causal (rationale_for),
            # y factual (synonym, hypernym, part_of).
            _SEMANTIC_REL_TYPES = (
                "'synonym','equivalent','hypernym','hyponym','part_of',"
                "'contains_lemma','antonym','related','rationale_for',"
                "'translates_to','meronym','man_see_also'"
            )
            semantic_edges = conn.execute(
                f"SELECT from_node, to_node, relation_type, strength FROM knowledge_edges "
                f"WHERE relation_type IN ({_SEMANTIC_REL_TYPES})"
            ).fetchall()
            log.info("build_graph: %d semantic edges loaded from knowledge_edges", len(semantic_edges))
        except Exception as e:
            log.error("build_graph: db error: %s", e)
            return 0

        # Clear graph and index during rebuild (prevents stale lookups)
        with self._lock:
            self._kw_index = None
            self.graph = SemanticGraph()

        new_graph = SemanticGraph()
        new_kw_index: Dict[str, List[Tuple[str, float]]] = {}

        for row in rows:
            nid, concept, definition, category, source, confidence = row
            if not concept or not definition:
                continue
            tokens = {w for w in re.findall(r'[a-zA-Záéíóúñ0-9_-]{2,}', (concept + " " + definition).lower())
                      if w not in _STOP_WORDS}
            new_graph.add_node(ConceptNode(
                id=str(nid),
                concept=str(concept or ""),
                definition=str(definition or ""),
                category=str(category or ""),
                source=str(source or ""),
                confidence=float(confidence or 0.5),
                tokens=tokens,
            ))
            # Build keyword index concurrently
            for token in tokens:
                if token not in new_kw_index:
                    new_kw_index[token] = []
                new_kw_index[token].append((str(nid), float(confidence or 0.5)))

        # Trim keyword index: keep top 50 per token
        for token in list(new_kw_index.keys()):
            if len(new_kw_index[token]) > 50:
                new_kw_index[token].sort(key=lambda x: -x[1])
                new_kw_index[token] = new_kw_index[token][:50]
        log.info("Keyword index: %d tokens, %d referencias",
                 len(new_kw_index), sum(len(v) for v in new_kw_index.values()))

        # Build edges on new_graph (outside lock, but it's a local object)
        nodes_by_token: Dict[str, List[str]] = defaultdict(list)
        for nid, node in new_graph.nodes.items():
            for token in node.tokens:
                if token not in _STOP_WORDS:
                    nodes_by_token[token].append(nid)

        for token, ids in nodes_by_token.items():
            if len(ids) < 2 or len(ids) > 50:
                continue
            for i in range(len(ids)):
                for j in range(i + 1, len(ids)):
                    similarity = self._compute_edge_weight(
                        new_graph.nodes[ids[i]], new_graph.nodes[ids[j]], token
                    )
                    if similarity > 0.1:
                        new_graph.add_edge(RelationEdge(
                            source_id=ids[i], target_id=ids[j],
                            rel_type=REL_KEYWORD, weight=similarity
                        ))

        # Aristas por misma categoría
        nodes_by_cat: Dict[str, List[str]] = defaultdict(list)
        for nid, node in new_graph.nodes.items():
            if node.category:
                nodes_by_cat[node.category].append(nid)
        for cat, ids in nodes_by_cat.items():
            for i in range(min(len(ids), 30)):
                for j in range(i + 1, min(len(ids), 30)):
                    if i != j:
                        w = 0.3 + (new_graph.nodes[ids[i]].confidence *
                                   new_graph.nodes[ids[j]].confidence) * 0.3
                        new_graph.add_edge(RelationEdge(
                            source_id=ids[i], target_id=ids[j],
                            rel_type=REL_CATEGORY, weight=min(w, 1.0)
                        ))

        # S76: Cargar aristas estructurales persistidas (Graphify bridge)
        # Solo añade aristas entre nodos que YA existen en new_graph
        structural_loaded = 0
        if structural_edges:
            for from_nid, to_nid, rel_type, strength in structural_edges:
                try:
                    if from_nid in new_graph.nodes and to_nid in new_graph.nodes:
                        new_graph.add_edge(RelationEdge(
                            source_id=str(from_nid), target_id=str(to_nid),
                            rel_type=str(rel_type), weight=float(strength or 0.8)))
                        structural_loaded += 1
                except Exception:
                    pass
            log.info("build_graph: cargadas %d/%d aristas estructurales",
                     structural_loaded, len(structural_edges))

        # S110: Cargar aristas semánticas persistidas (synonym, antonym, part_of, etc.)
        # Pesos por tipo de relación semántica (mapean fuerza lógica → peso sináptico)
        _SEMANTIC_WEIGHTS = {
            "synonym": 0.90, "equivalent": 0.90,
            "hypernym": 0.70, "hyponym": 0.70,
            "part_of": 0.75, "contains_lemma": 0.75,
            "antonym": 0.60,
            "rationale_for": 0.80,
            "related": 0.50,
            "translates_to": 0.85, "meronym": 0.65,
            "man_see_also": 0.75,  # P0: man page cross-references
        }
        semantic_loaded = 0
        if semantic_edges:
            for from_nid, to_nid, rel_type, strength in semantic_edges:
                try:
                    if from_nid in new_graph.nodes and to_nid in new_graph.nodes:
                        default_w = _SEMANTIC_WEIGHTS.get(str(rel_type), 0.5)
                        stored_w = float(strength or 0.0)
                        w = stored_w if 0.01 < stored_w <= 1.0 else default_w
                        new_graph.add_edge(RelationEdge(
                            source_id=str(from_nid), target_id=str(to_nid),
                            rel_type=str(rel_type), weight=w))
                        semantic_loaded += 1
                except Exception:
                    pass
            log.info("build_graph: cargadas %d/%d aristas semánticas",
                     semantic_loaded, len(semantic_edges))

        # Auto-descubrir conceptos mencionados en definiciones que no existen
        try:
            disc = self._discover_concepts(new_graph, max_discover=200, time_budget=30.0)
            if disc > 0:
                log.info("Conceptos descubiertos: %d", disc)
        except Exception as e:
            log.error("Concept discovery error: %s", e)

        log.info("build_graph: pre-swap new_graph has %d nodes, %d edges", len(new_graph.nodes), len(new_graph.edges))

        # Atomic swap: assign both graph and keyword index together
        with self._lock:
            self.graph = new_graph
            self._kw_index = new_kw_index
            new_graph._built = True
            self._ready = True
            self._building = False   # FIX: liberar flag para que is_ready()=True

        n_nodes, n_edges = self.graph.size
        log.info("Grafo semántico: %d nodos, %d aristas", n_nodes, n_edges)
        return n_nodes

    def _compute_edge_weight(self, a: ConceptNode, b: ConceptNode,
                             shared_token: str) -> float:
        """Peso de arista entre dos nodos basado en solapamiento."""
        overlap = len(a.tokens & b.tokens)
        total = len(a.tokens | b.tokens)
        if total == 0:
            return 0.0
        jaccard = overlap / total
        # Bonus por compartir token largo específico
        bonus = 0.2 if len(shared_token) >= 6 else 0.0
        # Bonus por misma fuente
        source_bonus = 0.15 if a.source == b.source and a.source else 0.0
        weight = jaccard * 0.6 + bonus + source_bonus
        return min(weight * max(a.confidence, b.confidence), 1.0)

    def _discover_concepts(self, graph: SemanticGraph, max_discover: int = 200,
                          time_budget: float = 30.0) -> int:
        """Escanea definiciones buscando conceptos capitalizados que falten como nodos."""
        pattern = re.compile(r'\b([A-Z][a-záéíóúñ]+(?:\s+[A-Z][a-záéíóúñ]+){1,3})\b')
        discovered = 0
        existing_lower: Set[str] = set()
        for nid, node in graph.nodes.items():
            existing_lower.add(node.concept.lower())
        t_end = time.time() + time_budget
        scanned = 0
        for nid, node in list(graph.nodes.items()):
            if time.time() > t_end or discovered >= max_discover:
                break
            scanned += 1
            for match in pattern.findall(node.definition):
                cl = match.strip().lower()
                if cl in existing_lower or len(cl) < 6:
                    continue
                existing_lower.add(cl)
                disc_id = f"disc_{node.id}_{discovered}"
                tokens = set(re.findall(r'\w{3,}', match.strip().lower()))
                graph.add_node(ConceptNode(
                    id=disc_id, concept=match.strip(),
                    definition=f"Descubierto desde: {node.concept}",
                    category=node.category or "discovered",
                    source="discovered", confidence=0.3, tokens=tokens,
                ))
                graph.add_edge(RelationEdge(
                    source_id=disc_id, target_id=node.id,
                    rel_type=REL_KEYWORD, weight=0.6
                ))
                discovered += 1
        if discovered > 0:
            log.info("Discover: %d concepts from %d defs", discovered, scanned)
        return discovered

    def reason(self, query: str, max_results: int = 6, use_research: bool = False) -> Dict[str, Any]:
        """
        Responde usando razonamiento neuronal: activa conceptos por keyword,
        dispara spreading activation, recoge neuronas activadas.
        """
        if not query.strip():
            return {"answer": "", "direct_hits": 0, "inferred": 0, "ready": self._ready, "building": self._building}
        
        if not self._ready:
            if self._building:
                return self._fallback_reason(query, max_results, building=True)
            return self._fallback_reason(query, max_results, building=False)

        # Resetear estado neuronal para determinismo (cada consulta parte de cero)
        self.graph.reset_neuron_state()

        t0 = time.time()

        # Nivel 1: Activar neuronas por keyword search
        direct_hits = self.keyword_search(query, limit=max_results)
        if len(direct_hits) < 2:
            token_hits = self.graph.find_similar_concepts(query, top_k=max_results)
            if token_hits:
                direct_hits = token_hits

        # Si los hits directos son débiles o irrelevantes, descomponer la query
        top_score = direct_hits[0][1] if direct_hits else 0.0
        query_words = {w for w in re.findall(r'[a-zA-Záéíóúñ]{2,}', query.lower())
                       if w not in _STOP_WORDS}
        if (len(direct_hits) < 2 or top_score < 0.2) and len(query_words) > 1:
            decomposed = self._concept_decompose(query, query_words, max_results)
            if decomposed and (not direct_hits or decomposed[0][1] > top_score):
                direct_hits = decomposed

        # Disparar las neuronas encontradas (spreading activation)
        activated_ids: Set[str] = set()
        reasoning_chain: List[Dict[str, Any]] = []
        neuron_firings: List[Tuple[ConceptNode, float]] = []
        for node, score in direct_hits:
            reasoning_chain.append({
                "type": "keyword_match",
                "concept": node.concept[:40],
                "score": round(score, 3),
                "activation_before": round(node.activation, 3),
            })
            fired = self.graph.fire_neuron(node.id, boost=NEURON_ACTIVATION_BOOST * (0.5 + score * 0.5))
            for fid in fired:
                if fid not in activated_ids:
                    activated_ids.add(fid)
                    fn = self.graph.nodes.get(fid)
                    if fn:
                        neuron_firings.append((fn, score * 0.8))
                        if fid != node.id:
                            reasoning_chain.append({
                                "type": "spread",
                                "from": node.concept[:30],
                                "to": fn.concept[:40],
                                "activation": round(fn.activation, 3),
                            })

        # Nivel 2: Recoger neuronas activadas (spreading activation)
        active = self.graph.get_active_neurons(threshold=0.25, max_results=15)
        for nid, act_level in active:
            if nid not in activated_ids:
                activated_ids.add(nid)
                node = self.graph.nodes.get(nid)
                if node:
                    neuron_firings.append((node, act_level))
                    reasoning_chain.append({
                        "type": "active_neuron",
                        "concept": node.concept[:40],
                        "activation": round(act_level, 3),
                    })

        # Nivel 3: BFS expandido desde neuronas activadas (limitado)
        expanded = set()
        for node, score in neuron_firings[:5]:
            related = self.graph.get_related(node.id, max_depth=1, max_results=10)
            for rel_node, rel_type, rel_weight, depth in related:
                if rel_node.id not in activated_ids:
                    activated_ids.add(rel_node.id)
                    final_score = score * rel_weight * 0.7
                    if final_score > 0.08:
                        neuron_firings.append((rel_node, final_score))
                        expanded.add(rel_node.id)
                        reasoning_chain.append({
                            "type": "bfs_expand",
                            "from": node.concept[:30],
                            "to": rel_node.concept[:40],
                            "relation": rel_type,
                            "weight": round(rel_weight, 3),
                        })

        # Nivel 4: Inferir nueva información
        inferences = self._infer_from_graph(query, neuron_firings[:10])
        for inf in inferences:
            reasoning_chain.append({
                "type": "inference",
                "result": inf.concept[:50],
                "confidence": round(inf.confidence, 3),
                "path": " → ".join(inf.reasoning_path[:3]),
            })

        # Ordenar por score (activación)
        neuron_firings.sort(key=lambda x: -x[1])

        # Aprendizaje hebbiano: conceptos que aparecen juntos se fortalecen
        if len(neuron_firings) >= 2:
            for i in range(min(len(neuron_firings), 5)):
                for j in range(i + 1, min(len(neuron_firings), 5)):
                    self.graph.hebbian_learn(
                        neuron_firings[i][0].concept,
                        neuron_firings[j][0].concept
                    )

        # Calcular confianza máxima entre todos los matches
        top_confidence = max((s for _, s in neuron_firings), default=0.0)

        # S110: Semantic Router — clasificar intención y ajustar estrategia
        _route_type = "factual"
        _route_conf = 0.0
        try:
            from core.semantic_router import get_semantic_router
            _router = get_semantic_router()
            _route_type, _route_conf, _route_method = _router.route(query)
            log.debug("semantic_router: %s (conf=%.2f, method=%s)",
                      _route_type, _route_conf, _route_method)
        except Exception as _e:
            log.debug("semantic_router unavailable: %s", _e)

        # Generar respuesta textual con prioridad a conceptos exactos
        answer_parts = []
        used_concepts = set()
        query_lower = query.lower().strip()
        # Primero: hits exactos (concepto coincide exactamente con query)
        exact_hits = []
        other_hits = []
        for node, score in neuron_firings[:max_results * 2]:
            # Saltar matches de muy baja confianza si no son exactos
            if score < MIN_CONFIDENCE:
                if not (query_lower.endswith(node.concept.lower().strip()) or
                        node.concept.lower().strip() in query_lower.split()):
                    continue
            # Verificar si el concepto es match exacto con la query
            is_exact = (node.concept.lower().strip() == query_lower or
                        query_lower.endswith(node.concept.lower().strip()) or
                        node.concept.lower().strip() in query_lower.split())
            if is_exact:
                exact_hits.append((node, score))
            else:
                other_hits.append((node, score))

        # Recalcular low_confidence: si hay hits exactos, no es baja confianza
        low_confidence = len(exact_hits) == 0 and top_confidence < MIN_CONFIDENCE

        # S112: Check de RELEVANCIA real (honestidad: no inventar).
        # Si las palabras clave significativas de la pregunta NO aparecen en los
        # conceptos ni definiciones de los nodos recuperados, EIDOS realmente NO
        # sabe — aunque la activación neuronal sea alta por matches espurios
        # (ej. "pangolín cuántico" matchea "Ubuntu Precise Pangolin").
        _irrelevant = False  # True si el mejor nodo no se relaciona con la query
        if not low_confidence:
            def _sin_tildes(s):
                for a, b in (("á","a"),("é","e"),("í","i"),("ó","o"),("ú","u"),("ñ","n")):
                    s = s.replace(a, b)
                return s
            _q_keywords = {_sin_tildes(w) for w in re.findall(r'[a-zA-Záéíóúñ]{4,}', query.lower())
                           if w not in _STOP_WORDS}
            _combined_check = exact_hits + other_hits
            if _q_keywords and _combined_check:
                # El MEJOR nodo (el que usará el NLG) debe contener alguna keyword.
                # Si no, es un match espurio (pangolín→Ubuntu Pangolin) → no sé.
                _top3_text = _sin_tildes(" ".join(
                    (n.concept + " " + n.definition[:200]).lower()
                    for n, _ in _combined_check[:3]
                ))
                _hits_kw = sum(1 for kw in _q_keywords if kw in _top3_text)
                if _hits_kw == 0:
                    low_confidence = True
                    _irrelevant = True
                    log.info("reason: ninguna keyword %s en top nodos → no sé", _q_keywords)

        combined = exact_hits + other_hits
        for node, score in combined[:max_results]:
            if node.concept not in used_concepts:
                used_concepts.add(node.concept)
                if node.source == INFERRED_SOURCE:
                    prefix = "🧬 [inferido]"
                elif score > 0.4 or node in [e[0] for e in exact_hits]:
                    prefix = "✓"
                else:
                    prefix = "→"
                answer_parts.append(
                    f"{prefix} {node.concept}: {node.definition[:450]}"
                )

        for inf in inferences[:3]:
            inf_text = f"🧬 [razonado] {inf.concept}: {inf.definition[:200]}"
            if inf_text not in answer_parts:
                answer_parts.append(inf_text)

        # S110: Enriquecer respuesta según tipo de ruta semántica
        # No es cosmético — busca activamente contenido adicional.
        if _route_type == "evaluative" and len(answer_parts) >= 2:
            answer_parts.insert(0, "🧠 [Análisis comparativo]")
            # Buscar los dos lados de la comparación y añadir contraste
            _cmp_entities = self._extract_comparison_entities(query)
            if len(_cmp_entities) >= 2:
                _a, _b = _cmp_entities[0], _cmp_entities[1]
                _a_nodes = self.keyword_search(_a, limit=3)
                _b_nodes = self.keyword_search(_b, limit=3)
                if _a_nodes and _b_nodes:
                    answer_parts.insert(1, f"🔷 {_a.upper()}: {_a_nodes[0][0].definition[:200]}")
                    answer_parts.insert(2, f"🔶 {_b.upper()}: {_b_nodes[0][0].definition[:200]}")
            answer_parts.append(
                "💡 Para decidir, prioriza el aspecto más crítico para tu caso. "
                "No hay respuesta universal: depende de requisitos específicos."
            )
        elif _route_type == "causal" and answer_parts:
            answer_parts.insert(0, "🧠 [Análisis causal]")
            # Buscar nodos con lenguaje funcional/causal en sus definiciones
            _topic_words = [w for w in re.findall(r'[a-zA-Záéíóúñ]{3,}', query.lower())
                           if w not in _STOP_WORDS][:3]
            _func_nodes = []
            for _tw in _topic_words:
                _candidates = self.keyword_search(_tw, limit=8)
                for _node, _score in _candidates:
                    _def = _node.definition.lower()
                    # Priorizar definiciones que explican función, causa o propósito
                    if any(_kw in _def for _kw in (
                        "permite", "protege", "evita", "filtra", "sirve para",
                        "previene", "asegura", "garantiza", "controla", "bloquea",
                        "enables", "protects", "prevents", "filters", "controls",
                        "because", "therefore", "importante", "esencial", "crítico",
                        "important", "essential", "critical", "fundamental",
                    )):
                        _func_nodes.append((_node, _score))
            if _func_nodes:
                _func_nodes.sort(key=lambda x: -x[1])
                answer_parts.append("🔗 [Relaciones funcionales encontradas]:")
                for _fn, _fs in _func_nodes[:3]:
                    if _fn.concept not in used_concepts:
                        used_concepts.add(_fn.concept)
                        answer_parts.append(f"  → {_fn.concept}: {_fn.definition[:250]}")
            if len(answer_parts) >= 2:
                answer_parts.append(
                    "💡 En conjunto, estos factores muestran que el tema es relevante "
                    "porque afecta directamente la protección, el control o la "
                    "integridad del sistema."
                )

        answer = "\n\n".join(answer_parts) if answer_parts else (
            "No encontré información directa. "
            "Puedo investigar con /study si me das un tema."
        )

        # LIKE fallback — solo fuentes de calidad, con filtros anti-basura
        try:
            conn = get_conn(BRAIN_DB, timeout=3)
            _BAD_SRC_SQL = "','".join(_EXCLUDED_SOURCES_FOR_QUERY)
            _BAD_CAT_SQL = "','".join(_EXCLUDED_CATEGORIES_FOR_QUERY)
            kw = [w for w in re.findall(r'[a-zA-Záéíóúñ0-9_-]{2,}', query.lower())
                  if w not in _STOP_WORDS][:5]
            for k in kw:
                extra = conn.execute(
                    f"SELECT concept, definition FROM knowledge_nodes "
                    f"WHERE concept = ? AND confidence >= 0.3 "
                    f"AND source != 'reasoned' "
                    f"AND source NOT IN ('{_BAD_SRC_SQL}') "
                    f"AND category NOT IN ('{_BAD_CAT_SQL}') "
                    f"AND LENGTH(definition) >= {_MIN_DEFINITION_LENGTH} "
                    f"LIMIT 1",
                    (k,)
                ).fetchall()
                if len(extra) < 1:
                    extra = conn.execute(
                        f"SELECT concept, definition FROM knowledge_nodes "
                        f"WHERE (concept LIKE ? OR definition LIKE ?) "
                        f"AND confidence >= 0.3 "
                        f"AND source != 'reasoned' "
                        f"AND source NOT IN ('{_BAD_SRC_SQL}') "
                        f"AND category NOT IN ('{_BAD_CAT_SQL}') "
                        f"AND LENGTH(definition) >= {_MIN_DEFINITION_LENGTH} "
                        f"ORDER BY confidence DESC LIMIT 4",
                        (f"%{k}%", f"%{k}%")
                    ).fetchall()
                for c, d in extra:
                    if c not in used_concepts and _is_quality_definition("", "", d or ""):
                        if any(concept.strip().lower() == k for concept, _ in [extra[0]] if extra):
                            answer_parts.insert(0, f"✓ {c}: {d[:200]}")
                        else:
                            answer_parts.append(f"→ {c}: {d[:200]}")
                        used_concepts.add(c)

        except Exception as e:
            log.debug("LIKE fallback error: %s", e)

        # Decaimiento global periódico
        # Recalcular low_confidence tras LIKE fallback (pudo añadir matches exactos).
        # PERO si fue marcado irrelevante (cobertura baja), NO revivir: aunque haya
        # un "✓" con score alto, es un match espurio (ej. pangolín→Ubuntu Pangolin).
        if low_confidence and answer_parts and not _irrelevant:
            for part in answer_parts:
                if part.startswith("✓"):
                    low_confidence = False
                    break
        if time.time() - self.graph._last_global_decay > 3600:
            self.graph.global_decay()

        elapsed = round(time.time() - t0, 3)
        # S70: research ACTIVO — si el grafo no tiene nada y viene de /talk humano (use_research),
        # investiga AHORA (man/apt/wikipedia/ddg/forums/github), aprende y responde. Sin LLM.
        _researched_ok = False
        if not answer_parts and use_research:
            try:
                from core.eidos_active_research import research_now
                res = research_now(query, timeout=10)
                if isinstance(res, dict) and res.get("definition"):
                    neural_answer = (f"No lo sabía, lo acabo de investigar "
                                     f"({res.get('channel','web')}): {res['definition']}")
                    _researched_ok = True  # fuerza low_confidence=False → colony usa esta respuesta
                    try:
                        self._build_async()  # recargar el nodo recién aprendido
                    except Exception:
                        pass
                else:
                    neural_answer = ("No encontré información y mi búsqueda externa no dio "
                                     "resultados claros.")
            except Exception as _e:
                log.debug("active research falló: %s", _e)
                neural_answer = ("No encontré información directa. "
                                 "Puedo investigar con /study si me das un tema.")
        else:
            # S111: NLG — generar prosa natural en vez de pegar fichas.
            # Detrás de flag EIDOS_NLG=1. Fallback a fichas si NLG no produce.
            _nlg_answer = ""
            if os.environ.get("EIDOS_NLG", "1").strip() == "1" and neuron_firings:
                try:
                    from core.eidos_nlg import get_nlg
                    _comp = (self._extract_comparison_entities(query)
                             if _route_type == "evaluative" else [])
                    _related = []
                    if neuron_firings:
                        _main_id = neuron_firings[0][0].id
                        for _rn, _rt, _w, _d in self.graph.get_related(
                                _main_id, max_depth=1, max_results=10):
                            _related.append(_rn.concept)
                    _nlg_answer = get_nlg().generate(
                        query, _route_type, neuron_firings[:8],
                        comparison_entities=_comp, related_concepts=_related,
                    )
                except Exception as _nlg_e:
                    log.debug("NLG falló, usando fichas: %s", _nlg_e)
            if _nlg_answer and len(_nlg_answer) > 40:
                neural_answer = _nlg_answer
            else:
                neural_answer = "\n\n".join(answer_parts[:max_results]) if answer_parts else (
                    "No encontré información directa. "
                    "Puedo investigar con /study si me das un tema."
                )
        return {
            "answer": neural_answer,
            "direct_hits": len(direct_hits),
            "expanded": len(expanded),
            "inferred": len(inferences),
            "nodes_consulted": len(neuron_firings),
            "reasoning_chain": reasoning_chain[:20],
            "elapsed_s": elapsed,
            "ready": self._ready,
            "building": self._building,
            "graph_nodes": self.graph.size[0],
            "graph_edges": self.graph.size[1],
            "low_confidence": low_confidence,
            "top_confidence": round(top_confidence, 4),
            "route_type": _route_type,
            "route_conf": _route_conf,
        }

    def _fallback_reason(self, query: str, max_results: int = 6,
                         building: bool = False) -> Dict[str, Any]:
        """Fallback cuando el grafo no está listo: búsqueda LIKE directa en brain.db."""
        t0 = time.time()
        try:
            conn = get_conn(BRAIN_DB, timeout=3)
            _STOP = {"que", "del", "las", "los", "con", "por", "para", "como", "qué", "cómo", "puede", "todo", "esta", "este", "más", "eso", "esa", "entre"}
            keywords = [w for w in re.findall(r'\w{3,}', query.lower())
                        if w not in _STOP][:3]
            answer_parts = []
            used = set()
            for kw in keywords:
                rows = conn.execute(
                    "SELECT concept, definition FROM knowledge_nodes "
                    "WHERE (concept LIKE ? OR definition LIKE ?) "
                    "AND confidence >= 0.3 "
                    "AND source != 'reasoned' "
                    "AND category NOT IN ('eidos_function', 'eidos_class') "
                    "ORDER BY confidence DESC LIMIT 4",
                    (f"%{kw}%", f"%{kw}%")
                ).fetchall()
                for c, d in rows:
                    if c not in used:
                        used.add(c)
                        answer_parts.append(f"→ {c}: {d[:200]}")

            elapsed = round(time.time() - t0, 3)
            return {
                "answer": "\n\n".join(answer_parts[:max_results]) if answer_parts else (
                    "El grafo semántico está construyéndose. "
                    "No encontré coincidencias directas en brain.db."),
                "direct_hits": len(answer_parts),
                "expanded": 0,
                "inferred": 0,
                "nodes_consulted": len(answer_parts),
                "elapsed_s": elapsed,
                "ready": self._ready,
                "building": building,
                "graph_nodes": 0,
                "graph_edges": 0,
            }
        except Exception as e:
            return {
                "answer": f"Error en fallback: {e}",
                "direct_hits": 0,
                "elapsed_s": round(time.time() - t0, 3),
                "ready": self._ready,
                "building": building,
            }

    def _extract_comparison_entities(self, query: str) -> List[str]:
        """Extrae las entidades a comparar en queries evaluativas.

        Ejemplos:
          "es mejor Linux que Windows" → ["linux", "windows"]
          "compara Python con JavaScript" → ["python", "javascript"]
          "diferencia entre TCP y UDP" → ["tcp", "udp"]
          "linux vs windows" → ["linux", "windows"]
        """
        q = query.lower().rstrip("?")
        entities = []

        # Patrones greedy (sin lazy) para capturar entidades completas.
        # Orden: más específicos primero.
        patterns = [
            # "A frente a B", "A vs B", "A versus B"
            r'(\w+)\s+(?:frente\s+a|vs\.?|versus)\s+(\w+)',
            # "mejor/peor A que B"
            r'(?:mejor|peor)\s+(\w+)\s+(?:que|than)\s+(\w+)',
            # "compara/comparar A con/y B"
            r'(?:compara|comparar|comparación|compare)\s+(\w+)\s+(?:con|y|vs\.?|and)\s+(\w+)',
            # "diferencia entre A y B"
            r'(?:diferencia|difference)\s+(?:entre|between)\s+(\w+)\s+(?:y|and)\s+(\w+)',
        ]

        for pat in patterns:
            m = re.search(pat, q)
            if m:
                a, b = m.group(1).strip(), m.group(2).strip()
                # Limpiar stopwords que puedan haberse colado
                for sw in ["el", "la", "los", "las", "un", "una", "the", "a", "an",
                          "de", "del", "en", "con", "por", "para"]:
                    if a.lower() == sw.lower() or b.lower() == sw.lower():
                        break
                else:
                    if a and b and len(a) > 1 and len(b) > 1 and a != b:
                        entities = [a, b]
                        break

        return entities

    def _infer_from_graph(self, query: str,
                          concepts: List[Tuple[ConceptNode, float]]) -> List[InferenceResult]:
        """Infiero nuevo conocimiento combinando nodos relacionados."""
        inferences = []
        if len(concepts) < 2:
            return inferences
        for i in range(len(concepts)):
            for j in range(i + 1, len(concepts)):
                a, score_a = concepts[i]
                b, score_b = concepts[j]
                combined_conf = (score_a + score_b) / 2
                if combined_conf < 0.2:
                    continue
                # Inferir relación transitiva
                inferred = self._try_transitive_inference(a, b)
                if inferred:
                    inferences.append(inferred)
                # Inferir por patrón IS_A
                is_a_inf = self._try_is_a_inference(a, b)
                if is_a_inf:
                    inferences.append(is_a_inf)
        return inferences

    def _get_chain_inferences(self, max_chains: int = 5) -> List[InferenceResult]:
        """Inferencia multi-salto: A→B→C produce relación A→C.
        Busca cadenas de 3+ nodos donde A conecta con B y B conecta con C.
        """
        chains = []
        nodes = list(self.graph.nodes.values())
        random.shuffle(nodes)
        neighbor_cache: Dict[str, Set[str]] = {}
        for nid in self.graph.nodes:
            neighbor_cache[nid] = set()
            for neighbor, _, w, _ in self.graph._adj.get(nid, []):
                if w >= 0.3:
                    neighbor_cache[nid].add(neighbor)
        checked: Set[Tuple[str, str, str]] = set()
        for a in nodes[:300]:
            if len(a.definition.strip()) < 20:
                continue
            a_neighbors = neighbor_cache.get(a.id, set())
            for b_id in a_neighbors:
                b = self.graph.nodes.get(b_id)
                if not b or len(b.definition.strip()) < 20:
                    continue
                b_neighbors = neighbor_cache.get(b.id, set())
                for c_id in b_neighbors:
                    if c_id == a.id:
                        continue
                    c = self.graph.nodes.get(c_id)
                    if not c or len(c.definition.strip()) < 20:
                        continue
                    key = (a.id, b.id, c.id)
                    if key in checked:
                        continue
                    checked.add(key)
                    if c_id in a_neighbors:
                        continue
                    concept = f"{a.concept} → {c.concept}"
                    definition = (
                        f"Relación multi-salto inferida: '{a.concept}' se relaciona "
                        f"con '{b.concept}' que a su vez se relaciona con '{c.concept}'. "
                        f"Por transitividad, '{a.concept}' se relaciona con '{c.concept}'."
                    )
                    chains.append(InferenceResult(
                        concept=concept[:100],
                        definition=definition[:300],
                        source_concepts=[a.concept, b.concept, c.concept],
                        confidence=min(a.confidence * b.confidence * 1.5, 0.45),
                        reasoning_path=[a.concept, b.concept, c.concept],
                    ))
                    if len(chains) >= max_chains:
                        return chains
        return chains

    def _get_analogy_inferences(self, max_analogies: int = 3) -> List[InferenceResult]:
        """Inferencia por analogía: si A y B comparten vecinos, son análogos."""
        analogies = []
        nodes = list(self.graph.nodes.values())
        random.shuffle(nodes)
        for a in nodes[:200]:
            if len(a.definition.strip()) < 20:
                continue
            a_neighbors = set()
            for neighbor, _, w, _ in self.graph._adj.get(a.id, []):
                if w >= 0.3:
                    a_neighbors.add(neighbor)
            if len(a_neighbors) < 2:
                continue
            for b in nodes[:200]:
                if b.id == a.id or len(b.definition.strip()) < 20:
                    continue
                b_neighbors = set()
                for neighbor, _, w, _ in self.graph._adj.get(b.id, []):
                    if w >= 0.3:
                        b_neighbors.add(neighbor)
                shared = a_neighbors & b_neighbors
                if len(shared) >= 2 and len(shared) >= len(a_neighbors) * 0.3:
                    shared_names = [self.graph.nodes.get(s) for s in shared if self.graph.nodes.get(s)]
                    shared_str = ", ".join([n.concept for n in shared_names[:3]])
                    concept = f"{a.concept} ≅ {b.concept}"
                    definition = (
                        f"Analogía inferida: '{a.concept}' es análogo a '{b.concept}' "
                        f"porque comparten {len(shared)} conexiones comunes: {shared_str}."
                    )
                    analogies.append(InferenceResult(
                        concept=concept[:100],
                        definition=definition[:300],
                        source_concepts=[a.concept, b.concept],
                        confidence=min(len(shared) / max(len(a_neighbors), 1) * 0.6, 0.5),
                        reasoning_path=list(shared)[:3],
                    ))
                    if len(analogies) >= max_analogies:
                        return analogies
        return analogies

    def _try_transitive_inference(self, a: ConceptNode,
                                  b: ConceptNode) -> Optional[InferenceResult]:
        if a.concept.lower().strip() == b.concept.lower().strip():
            return None
        if SequenceMatcher(None, a.concept.lower(), b.concept.lower()).ratio() > 0.85:
            return None
        if len(a.definition.strip()) < 20 or len(b.definition.strip()) < 20:
            return None
        # Use adjacency list instead of scanning all edges
        a_neighbors = set()
        for neighbor, _, w, _ in self.graph._adj.get(a.id, []):
            if w >= 0.2:
                a_neighbors.add(neighbor)
        b_neighbors = set()
        for neighbor, _, w, _ in self.graph._adj.get(b.id, []):
            if w >= 0.2:
                b_neighbors.add(neighbor)
        common = a_neighbors & b_neighbors
        if not common:
            return None
        common_node = self.graph.nodes.get(next(iter(common)))
        if not common_node or len(common_node.definition.strip()) < 20:
            return None
        concept = f"{a.concept} ↔ {b.concept}"
        definition = (f"Relación inferida: '{a.concept}' y '{b.concept}' "
                      f"conectados a través de '{common_node.concept}'. "
                      f"{a.definition[:80]} ... {b.definition[:80]}")
        return InferenceResult(
            concept=concept[:100],
            definition=definition[:300],
            source_concepts=[a.concept, b.concept, common_node.concept],
            confidence=min((a.confidence + b.confidence) / 3, 0.5),
            reasoning_path=[a.concept, common_node.concept, b.concept],
        )

    def is_ready(self) -> bool:
        return self._ready and not self._building

    def keyword_search(self, query: str, limit: int = 10) -> List[Tuple[ConceptNode, float]]:
        """Búsqueda por palabras clave en el índice."""
        if not hasattr(self, '_kw_index') or self._kw_index is None:
            return []
        try:
            query_tokens = {w for w in re.findall(r'[a-zA-Záéíóúñ0-9_-]{2,}', query.lower())
                           if w not in _STOP_WORDS}
            if not query_tokens:
                return []
            # Score each unique node by number of matching tokens
            scores: Dict[str, float] = {}
            for token in query_tokens:
                if token in self._kw_index:
                    for nid, confidence in self._kw_index[token]:
                        if nid in self.graph.nodes:
                            scores[nid] = scores.get(nid, 0) + (1.0 * confidence)
            if not scores:
                log.debug("keyword_search: query_tokens=%s, hits=0 (no matches)", query_tokens)
                return []
            log.info("keyword_search: query_tokens=%s, raw_hits=%d", query_tokens, len(scores))
            # Boost exact concept matches
            query_lower = query.lower()
            for nid, node in self.graph.nodes.items():
                if node.concept.lower().strip() == query_lower.strip():
                    scores[nid] = scores.get(nid, 0) + 5.0
                elif any(t in node.concept.lower() for t in query_tokens):
                    scores[nid] = scores.get(nid, 0) + 2.0
            # Sort by score and return top results
            sorted_scores = sorted(scores.items(), key=lambda x: -x[1])
            results = []
            for nid, score in sorted_scores[:limit]:
                node = self.graph.nodes.get(nid)
                if node:
                    norm_score = min(score / max(len(query_tokens), 1), 1.0)
                    results.append((node, norm_score))
            return results
        except Exception as e:
            log.debug("keyword_search error: %s", e)
            return []

    def _try_is_a_inference(self, a: ConceptNode,
                            b: ConceptNode) -> Optional[InferenceResult]:
        """Si A contiene patrón 'es un X' y B describe X, inferir A es X."""
        # Skip if same concept (self-referential)
        if a.concept.lower().strip() == b.concept.lower().strip():
            return None
        # Skip if concepts are too similar (>80% identical)
        if SequenceMatcher(None, a.concept.lower(), b.concept.lower()).ratio() > 0.8:
            return None
        for node in [a, b]:
            for other in [b, a]:
                if IS_A_PATTERNS.search(node.definition):
                    m = IS_A_PATTERNS.search(node.definition)
                    if m:
                        after = node.definition[m.end():].strip().split(",")[0].split(".")[0]
                        target_word = after.split()[0] if after else ""
                        if (target_word and len(target_word) >= 3
                                and target_word.lower() in other.concept.lower()
                                and other.concept.lower() not in node.concept.lower()):
                            concept = f"{node.concept} es {other.concept}"
                            definition = (f"Inferido: {node.concept} es un tipo de "
                                          f"{other.concept}. Basado en: "
                                          f"{node.definition[:150]}")
                            return InferenceResult(
                                concept=concept[:100],
                                definition=definition[:300],
                                source_concepts=[node.concept, other.concept],
                                confidence=min((node.confidence + other.confidence) / 2, 0.8),
                                reasoning_path=[node.concept, other.concept],
                            )
        return None

    def evolve(self) -> Dict[str, Any]:
        """
        Ciclo de evolución: infiere nuevo conocimiento y lo persiste.
        Se ejecuta cada EVOLVE_INTERVAL segundos.
        S68-P2: si grafo no listo, usar _build_async (con lock+cooldown)
        en lugar de build_graph() directo (que ignoraba el cooldown).
        """
        if not self._ready:
            # S68-P2: pasar por _build_async para respetar lock+cooldown
            self._build_async()
            return {"status": "build_requested",
                    "ready": self._ready, "inferred": 0}

        t0 = time.time()
        # 1. Encontrar nodos candidatos para inferencia
        candidates = list(self.graph.nodes.values())
        inferences_made = []
        
        # 2. Intentar inferencias por pares
        import random
        random.shuffle(candidates)
        for i in range(min(len(candidates), 200)):
            a = candidates[i]
            for j in range(i + 1, min(len(candidates), i + 50)):
                b = candidates[j]
                if a.id == b.id:
                    continue
                inf = self._try_transitive_inference(a, b)
                if inf:
                    inferences_made.append(inf)
                inf2 = self._try_is_a_inference(a, b)
                if inf2:
                    inferences_made.append(inf2)
                if len(inferences_made) >= 15:
                    break
            if len(inferences_made) >= 15:
                break

        # 2b. Inferencias multi-salto (cadenas A→B→C)
        if len(inferences_made) < 20:
            try:
                chains = self._get_chain_inferences(max_chains=5)
                for c in chains:
                    if len(inferences_made) < 25:
                        inferences_made.append(c)
            except Exception:
                pass

        # 2c. Inferencias por analogía
        if len(inferences_made) < 25:
            try:
                analogies = self._get_analogy_inferences(max_analogies=3)
                for a in analogies:
                    if len(inferences_made) < 28:
                        inferences_made.append(a)
            except Exception:
                pass

        # 3. Persistir inferencias como knowledge_nodes
        persisted = 0
        if inferences_made:
            try:
                conn = get_conn(BRAIN_DB, timeout=10)
                conn.execute("PRAGMA journal_mode=WAL")
                # S121: portero de calidad — solo persistir inferencias que pasen
                # el filtro (reutiliza _compute_quality). Evita llenar el grafo de
                # ruido. El reasoner igual excluye source='reasoned' al razonar, así
                # que dejar de persistir basura no afecta su inteligencia.
                from core.eidos_quality_gate import gate
                for inf in inferences_made:
                    verdict = gate.evaluate(inf.concept, inf.definition,
                                            INFERRED_SOURCE, "inferred", inf.confidence)
                    if not verdict.admit:
                        log.debug("Gate filtró '%s': %s", inf.concept[:40], verdict.reason)
                        continue
                    try:
                        conn.execute(
                            "INSERT OR IGNORE INTO knowledge_nodes "
                            "(id, concept, definition, category, confidence, source, quality_score) "
                            "VALUES (?, ?, ?, ?, ?, ?, ?)",
                            (
                                f"reasoned_{int(time.time())}_{persisted}_{hash(inf.concept) % 10000}",
                                inf.concept[:200],
                                inf.definition[:500],
                                "inferred",
                                inf.confidence,
                                INFERRED_SOURCE,
                                verdict.quality_score,
                            )
                        )
                        persisted += 1
                    except Exception:
                        continue
                conn.commit()

            except Exception as e:
                log.error("evolve persist error: %s", e)

        # 4. Sincronizar inferencias a ChromaDB
        if persisted > 0:
            try:
                from core.colony_chroma import get_chroma_memory
                chroma = get_chroma_memory()
                if chroma.is_ready():
                    # Esperar que chroma termine init
                    time.sleep(2)
                    log.info("Evolve: %d inferencias persistidas, syncing chroma...", persisted)
            except Exception:
                pass

        self._last_evolve = time.time()
        self._inferred_count += persisted
        elapsed = round(time.time() - t0, 3)

        return {
            "status": "ok",
            "inferences_found": len(inferences_made),
            "persisted": persisted,
            "total_inferred": self._inferred_count,
            "elapsed_s": elapsed,
        }

    # ─── S66 · SELF-RESEARCH MULTICANAL ─────────────────────────────────────
    # Orden de canales (todos timeout corto, ninguno usa LLM):
    #   1. código propio  (confidence 0.9)
    #   2. man pages      (confidence 0.85)
    #   3. --help         (confidence 0.85)
    #   4. apt-cache      (confidence 0.85)
    #   5. duckduckgo     (confidence 0.5)
    #   6. wikipedia ES   (confidence 0.6) [último recurso]

    def _research_code(self, concept: str) -> str:
        """Busca el concepto en el código de EIDOS (funciones, clases, docstrings)."""
        import subprocess as _sp
        try:
            out = _sp.check_output(
                ["grep", "-rIn", "-m", "5",
                 "--include=*.py",
                 "-E", rf"(def|class)\s+{re.escape(concept)}\b",
                 str(REPO_ROOT / "core")],
                stderr=_sp.DEVNULL, timeout=3, text=True, errors="replace",
            )
            if out.strip():
                first = out.strip().splitlines()[0]
                return f"Definido en EIDOS: {first[:400]}"
        except Exception:
            pass
        return ""

    def _research_man(self, concept: str) -> str:
        """Primer párrafo del man del comando."""
        import subprocess as _sp
        try:
            out = _sp.check_output(
                ["man", "-P", "cat", concept],
                stderr=_sp.DEVNULL, timeout=3, text=True, errors="replace",
            )
            # Extraer sección NAME y primera línea de DESCRIPTION
            chunks = []
            in_name = in_desc = False
            for line in out.splitlines():
                stripped = line.strip()
                if stripped == "NAME":
                    in_name = True; continue
                if stripped == "DESCRIPTION":
                    in_desc = True; in_name = False; continue
                if stripped in ("SYNOPSIS", "OPTIONS", "SEE ALSO"):
                    if chunks:
                        break
                    in_name = in_desc = False; continue
                if (in_name or in_desc) and stripped:
                    chunks.append(stripped)
                    if len("\n".join(chunks)) > 350:
                        break
            text = "\n".join(chunks).strip()
            if text:
                return f"Man: {text[:400]}"
        except Exception:
            pass
        return ""

    def _research_help(self, concept: str) -> str:
        """Primer bloque de --help del comando."""
        import shutil as _sh
        import subprocess as _sp
        if not _sh.which(concept):
            return ""
        for flag in ("--help", "-h"):
            try:
                out = _sp.check_output(
                    [concept, flag], stderr=_sp.STDOUT,
                    timeout=2, text=True, errors="replace",
                )
                lines = [l.strip() for l in out.splitlines() if l.strip()][:6]
                if lines:
                    return f"Comando '{concept}': {' '.join(lines)[:400]}"
            except Exception:
                continue
        return ""

    def _research_apt(self, concept: str) -> str:
        """Descripción de paquete APT."""
        import subprocess as _sp
        try:
            out = _sp.check_output(
                ["apt-cache", "show", concept],
                stderr=_sp.DEVNULL, timeout=3, text=True, errors="replace",
            )
            desc = []
            in_desc = False
            for line in out.splitlines():
                if line.startswith("Description-en:") or line.startswith("Description:"):
                    desc.append(line.split(":", 1)[1].strip())
                    in_desc = True
                elif in_desc and line.startswith(" "):
                    desc.append(line.strip())
                elif in_desc:
                    break
                if len(" ".join(desc)) > 350:
                    break
            text = " ".join(desc).strip()
            if text:
                return f"Paquete APT: {text[:400]}"
        except Exception:
            pass
        return ""

    def _research_ddg(self, concept: str) -> str:
        """Primer snippet de DuckDuckGo HTML."""
        import urllib.parse as _up
        import urllib.request as _ur
        try:
            url = f"https://html.duckduckgo.com/html/?q={_up.quote(concept + ' definition')}"
            req = _ur.Request(url, headers={
                "User-Agent": "Mozilla/5.0 (X11; Linux x86_64) EIDOS/1.0"
            })
            with _ur.urlopen(req, timeout=3) as resp:
                html = resp.read().decode("utf-8", errors="replace")
            m = re.search(r'<a class="result__snippet"[^>]*>(.*?)</a>',
                          html, re.DOTALL)
            if m:
                snippet = re.sub(r"<[^>]+>", "", m.group(1))
                snippet = re.sub(r"\s+", " ", snippet).strip()
                if snippet:
                    return f"DuckDuckGo: {snippet[:400]}"
        except Exception:
            pass
        return ""

    def _research_wikipedia(self, concept: str) -> str:
        """Extracto de Wikipedia ES."""
        import urllib.parse as _up
        import urllib.request as _ur
        try:
            url = (f"https://es.wikipedia.org/w/api.php?action=query"
                   f"&titles={_up.quote(concept)}&prop=extracts"
                   f"&exintro=1&explaintext=1&format=json")
            req = _ur.Request(url, headers={"User-Agent": "EIDOS/1.0"})
            with _ur.urlopen(req, timeout=3) as resp:
                data = json.loads(resp.read())
            pages = data.get("query", {}).get("pages", {})
            for pid, page in pages.items():
                if pid != "-1" and page.get("extract"):
                    extract = page["extract"].strip()
                    if extract:
                        return f"Wikipedia: {extract[:500]}"
        except Exception:
            pass
        return ""

    _RESEARCH_CHANNELS = [
        ("code",       "_research_code",      0.9),
        ("man",        "_research_man",       0.85),
        ("help",       "_research_help",      0.85),
        ("apt",        "_research_apt",       0.85),
        ("duckduckgo", "_research_ddg",       0.5),
        ("wikipedia",  "_research_wikipedia", 0.6),
    ]

    def _persist_research_node(self, concept: str, definition: str,
                               channel: str, confidence: float) -> bool:
        """Upsert directo en SQLite del nodo investigado.
        Funciona aunque inject_node tenga otra firma o falle."""
        import sqlite3 as _sql
        import os as _os
        from pathlib import Path as _P
        from datetime import datetime as _dt
        db_path = _os.environ.get("EIDOS_BRAIN_DB",
                                  str(_P.home() / ".eidos" / "evolution_brain.db"))
        try:
            with _sql.connect(db_path, timeout=10) as con:
                con.execute("PRAGMA journal_mode=WAL")
                con.execute("PRAGMA busy_timeout=10000")
                existing = con.execute(
                    "SELECT id FROM knowledge_nodes WHERE concept=?", (concept,)
                ).fetchone()
                source = f"research:{channel}"
                if existing:
                    con.execute(
                        "UPDATE knowledge_nodes SET definition=?, source=?, "
                        "confidence=MAX(confidence, ?) WHERE concept=?",
                        (definition, source, confidence, concept),
                    )
                else:
                    con.execute(
                        "INSERT INTO knowledge_nodes "
                        "(concept, definition, category, source, confidence, created_at) "
                        "VALUES (?,?,?,?,?,?)",
                        (concept, definition, "researched", source,
                         confidence, _dt.now().isoformat()),
                    )
                con.commit()
            return True
        except Exception as e:
            log.warning("_persist_research_node falló (%s): %s", concept, e)
            return False

    def _self_research(self, query: str) -> int:
        """Investiga conceptos desconocidos en múltiples fuentes locales+web.
        Inyecta nodos al grafo con source='research:<canal>'. Sin LLM.
        Retorna nº de nodos añadidos."""
        added = 0
        words = {w for w in re.findall(r"[a-zA-Záéíóúñ0-9_-]{3,}", query.lower())
                 if w not in _STOP_WORDS}
        truly_unknown = []
        for w in sorted(words, key=len, reverse=True)[:6]:
            existing = self.keyword_search(w, limit=1)
            if not existing:
                existing = self.graph.find_similar_concepts(w, top_k=1)
            if not existing or existing[0][1] < MIN_CONFIDENCE:
                truly_unknown.append(w)
        if not truly_unknown:
            return 0
        log.info("Self-research multicanal: %s", truly_unknown)

        for concept in truly_unknown[:3]:
            for channel, method_name, confidence in self._RESEARCH_CHANNELS:
                try:
                    method = getattr(self, method_name)
                    result = method(concept)
                except Exception as e:
                    log.debug("research %s.%s falló: %s", concept, channel, e)
                    continue
                if result and len(result) > 50:
                    if self._persist_research_node(
                        concept, result[:600], channel, confidence
                    ):
                        added += 1
                        log.info("Self-research: '%s' aprendido vía %s (%d chars)",
                                 concept, channel, len(result))
                        break  # ya tenemos definición, siguiente concepto
        if added > 0:
            try:
                self.evolve()
            except Exception:
                pass
        return added

    def _concept_decompose(self, query: str, query_words: Set[str],
                           max_results: int) -> List[Tuple[ConceptNode, float]]:
        """Descompone la query en conceptos individuales y busca cada uno.
        Útil cuando la búsqueda combinada no encuentra nada relevante."""
        results: Dict[str, Tuple[ConceptNode, float]] = {}
        for w in sorted(query_words, key=len, reverse=True)[:6]:
            w_hits = self.keyword_search(w, limit=3)
            if not w_hits:
                w_hits = self.graph.find_similar_concepts(w, top_k=3) or []
            for node, score in w_hits:
                if score > 0.15 and node.concept not in results:
                    results[node.concept] = (node, score)
        return sorted(results.values(), key=lambda x: -x[1])[:max_results]

    def get_stats(self) -> Dict[str, Any]:
        return {
            "ready": self._ready,
            "building": self._building,
            "graph_nodes": self.graph.size[0],
            "graph_edges": self.graph.size[1],
            "inferred_total": self._inferred_count,
            "last_evolve": self._last_evolve,
            "build_error": self._build_error,
        }


# ── Singleton ─────────────────────────────────────────────────────────────────
_instance = None
_lock = threading.Lock()


def get_reasoner(force_rebuild: bool = False) -> KnowledgeReasoner:
    global _instance
    if _instance is None:
        with _lock:
            if _instance is None:
                _instance = KnowledgeReasoner(auto_build=True)
    if force_rebuild and not _instance._building:
        _instance._building = True
        _instance._ready = False
        t = threading.Thread(target=_instance._build_async, daemon=True,
                             name="reasoner-rebuild")
        t.start()
    return _instance


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    r = get_reasoner()
    print(f"Grafo: {r.graph.size[0]} nodos, {r.graph.size[1]} aristas")
    while True:
        q = input("\n❓ Pregunta (o 'evolve', 'stats', 'quit'): ").strip()
        if q == "quit":
            break
        elif q == "evolve":
            res = r.evolve()
            print(json.dumps(res, indent=2))
        elif q == "stats":
            print(json.dumps(r.get_stats(), indent=2))
        else:
            res = r.reason(q)
            print(f"\nRespuesta ({res['elapsed_s']}s):")
            print(res['answer'])
            print(f"\nDirectos: {res['direct_hits']} | Expandidos: {res['expanded']} | Inferidos: {res['inferred']}")
