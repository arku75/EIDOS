"""
core/inference.py — LogicalInferenceEngine para EIDOS

Motor de razonamiento simbólico que opera sobre el grafo neuronal.
Reemplaza la dependencia de LLM para consultas que pueden resolverse
mediante lógica proposicional, caminos causales y detección de contradicciones.

Uso:
    from core.inference import get_inference
    engine = get_inference()
    result = engine.deduce("X implica Y")
    if result.confidence > 0.6:
        # Respuesta puramente neuronal, no hace falta LLM
"""

from __future__ import annotations

import logging
import re
import time
from typing import Any, Dict, List, Optional, Set, Tuple
from dataclasses import dataclass, field

log = logging.getLogger("eidos.inference")

# Umbrales de confianza para respuestas lógicas
DEDUCE_MIN_CONFIDENCE = 0.35
CAUSAL_MIN_CONFIDENCE = 0.40
CONTRADICTION_CONFIDENCE = 0.60


@dataclass
class Proposition:
    """Una proposición lógica con valor de verdad y confianza."""
    text: str
    truth_value: Optional[bool] = None  # True, False, o None (desconocido)
    confidence: float = 0.0
    source_nodes: List[str] = field(default_factory=list)
    reasoning_path: List[str] = field(default_factory=list)


@dataclass
class InferenceResult:
    """Resultado de una inferencia lógica."""
    resolved: bool = False
    answer: str = ""
    confidence: float = 0.0
    propositions: List[Proposition] = field(default_factory=list)
    method: str = ""  # deduce, causal, contradiction, graph_search
    reasoning_chain: List[str] = field(default_factory=list)


class LogicalInferenceEngine:
    """Motor de razonamiento simbólico sobre el grafo neuronal de EIDOS.
    
    Opera sobre dos grafos:
    - NeuralGraph (knowledge_reasoner): ConceptNode + RelationEdge con activación
    - KnowledgeGraph (knowledge_graph.py): nodos SQLite con relaciones
    """

    def __init__(self):
        self._reasoner = None
        self._kgraph = None
        self._ready = False
        self._causal_patterns = re.compile(
            r'\b(causa|provoca|genera|produce|origina|resulta|conduce|'
            r'lleva\s+a|deriva\s+en\s|causes|leads\s+to|results\s+in|'
            r'triggers|produces|creates)\b',
            re.IGNORECASE
        )
        self._contradiction_patterns = re.compile(
            r'\b(pero|sin\s+embargo|no\s+obstante|however|but|although|'
            r'en\s+cambio|por\s+el\s+contrario|on\s+the\s+contrary|'
            r'contradice|contradicts|opuesto|opposite)\b',
            re.IGNORECASE
        )

    def _ensure_loaded(self) -> bool:
        """Carga los grafos si es necesario."""
        if self._ready:
            return True
        try:
            from core.knowledge_reasoner import get_reasoner
            self._reasoner = get_reasoner()
            if self._reasoner._ready:
                self._ready = True
                return True
        except Exception as e:
            log.debug("inference: no se pudo cargar reasoner: %s", e)
        return False

    def _ensure_kgraph(self):
        """Carga el KnowledgeGraph SQLite."""
        if self._kgraph is not None:
            return
        try:
            from core.knowledge_graph import get_knowledge_graph
            self._kgraph = get_knowledge_graph()
        except Exception as e:
            log.debug("inference: no se pudo cargar kgraph: %s", e)

    # ─── API principal ─────────────────────────────────────────────────

    def deduce(self, proposition: str) -> InferenceResult:
        """Intenta deducir el valor de verdad de una proposición desde el grafo.
        
        Estrategias:
        1. Búsqueda directa del concepto en el grafo
        2. Caminos de implicación (A → B → C)
        3. Propagación de confianza por relaciones IS-A, PART_OF, IMPLIES
        """
        result = InferenceResult(method="deduce")
        if not self._ensure_loaded():
            return result

        # 1. Buscar el concepto directamente
        neural = self._reasoner.reason(proposition, max_results=5)
        if neural.get("low_confidence", True):
            result.propositions.append(Proposition(
                text=proposition, truth_value=None,
                confidence=0.0, source_nodes=[],
                reasoning_path=["no encontrado en grafo"]
            ))
            return result

        # 2. Extraer proposiciones de la respuesta neuronal
        answer = neural.get("answer", "")
        direct_hits = neural.get("direct_hits", 0)
        top_conf = neural.get("top_confidence", 0.0)

        if answer and top_conf >= DEDUCE_MIN_CONFIDENCE:
            result.resolved = True
            result.answer = answer
            result.confidence = top_conf
            result.propositions.append(Proposition(
                text=proposition, truth_value=True,
                confidence=top_conf,
                source_nodes=[c.get("concept", "") 
                              for c in neural.get("reasoning_chain", [])
                              if c.get("type") == "keyword_match"],
                reasoning_path=[c.get("concept", "")
                                for c in neural.get("reasoning_chain", [])[:5]]
            ))
            return result

        # 3. Si no hay suficiente confianza, buscar caminos de implicación
        return self._find_implication_paths(proposition, result)

    def _find_implication_paths(self, proposition: str,
                                result: InferenceResult) -> InferenceResult:
        """Busca cadenas de implicación en el grafo para una proposición."""
        words = {w for w in re.findall(r'[a-zA-Záéíóúñ]{4,}', proposition.lower())
                 if w not in _STOP_WORDS}
        if not words:
            return result

        # Buscar cada palabra como concepto individual
        connected_concepts = []
        for w in words:
            w_hits = self._reasoner.keyword_search(w, limit=2)
            if w_hits and w_hits[0][1] >= DEDUCE_MIN_CONFIDENCE:
                connected_concepts.append(w_hits[0][0])

        # Si hay múltiples conceptos conectados, buscar caminos entre ellos
        if len(connected_concepts) >= 2:
            paths = []
            graph = self._reasoner.graph
            for i in range(len(connected_concepts)):
                for j in range(i + 1, len(connected_concepts)):
                    c1 = connected_concepts[i].concept
                    c2 = connected_concepts[j].concept
                    related = graph.get_related(
                        connected_concepts[i].id, 
                        max_depth=2, max_results=10
                    )
                    for rn, rt, rw, *_ in related:
                        if rn.concept == c2 and rw >= DEDUCE_MIN_CONFIDENCE:
                            paths.append(f"{c1} →({rt})→ {c2}")
                            result.reasoning_chain.append(
                                f"[implicación] {c1} → {c2} (tipo={rt}, peso={rw:.2f})"
                            )

            if paths:
                result.resolved = True
                result.answer = " → ".join(paths)
                result.confidence = min(1.0, len(paths) * 0.15)
                result.method = "implication_path"

        return result

    def check_contradiction(self, fact: str) -> InferenceResult:
        """Verifica si un hecho contradice conocimiento existente en el grafo.
        
        Busca nodos con definiciones opuestas al hecho proporcionado.
        """
        result = InferenceResult(method="contradiction")
        if not self._ensure_loaded():
            return result

        # Buscar el concepto en el grafo
        neural = self._reasoner.reason(fact, max_results=3)
        if neural.get("low_confidence", True):
            # No hay suficiente conocimiento para verificar contradicción → no resolver
            result.reasoning_chain.append("sin conocimiento para verificar contradicción")
            return result

        # Verificar si el hecho propuesto contradice definiciones existentes
        negations = {"no", "not", "nunca", "never", "sin", "without",
                     "contradice", "contradicts", "falso", "false"}
        fact_lower = fact.lower()
        has_negation = any(n in fact_lower for n in negations)
        key_terms = {w for w in re.findall(r'\w{4,}', fact_lower)
                     if w not in _STOP_WORDS and w not in negations}

        for term in key_terms:
            term_hits = self._reasoner.keyword_search(term, limit=1)
            if term_hits and term_hits[0][1] >= 0.3:
                node = term_hits[0][0]
                def_lower = node.definition.lower()
                if has_negation and term in def_lower:
                    affirmations = {"es un", "es una", "is a", "tipo de", 
                                    "significa", "consiste en"}
                    if any(a in def_lower for a in affirmations):
                        result.resolved = True
                        result.confidence = CONTRADICTION_CONFIDENCE
                        result.answer = (
                            f"Contradicción: '{term}' está definido como "
                            f"'{node.definition[:100]}' en el grafo, "
                            f"pero tu afirmación sugiere lo contrario."
                        )
                        result.propositions.append(Proposition(
                            text=fact, truth_value=False,
                            confidence=CONTRADICTION_CONFIDENCE,
                            source_nodes=[node.concept]
                        ))
                        return result

        # No se encontró contradicción → no resolver (no es una respuesta útil)
        result.reasoning_chain.append("sin contradicción detectada")
        return result

    def explain_causality(self, event: str) -> InferenceResult:
        """Explica relaciones causales de un evento usando el grafo.
        
        Busca:
        1. Cadenas de implicación causal en el grafo
        2. Relaciones del tipo "causa", "provoca", "lleva a"
        3. Secuencias temporales de eventos
        """
        result = InferenceResult(method="causal")
        if not self._ensure_loaded():
            return result

        neural = self._reasoner.reason(event, max_results=8)
        if neural.get("low_confidence", True):
            result.resolved = True
            result.answer = "No tengo suficientes conexiones causales en mi grafo sobre este tema."
            return result

        # Buscar relaciones causales entre los conceptos activados
        chain = neural.get("reasoning_chain", [])
        causal_links = []

        for i, step in enumerate(chain):
            concept = step.get("concept", "")
            if self._causal_patterns.search(concept):
                causal_links.append(f"[causal] {concept}")

        # Buscar en el grafo las relaciones IMPLIES
        graph = self._reasoner.graph
        for node, score in neural.get("neuron_firings", 
                                       neural.get("nodes_consulted", [])):
            pass  # Already used above via reasoning_chain

        # Extraer caminos causales del grafo
        concept_words = {w for w in re.findall(r'[a-zA-Záéíóúñ]{4,}', event.lower())
                         if w not in _STOP_WORDS}
        causal_chains = []
        for w in list(concept_words)[:3]:
            w_hits = self._reasoner.keyword_search(w, limit=1)
            if w_hits:
                node = w_hits[0][0]
                related = graph.get_related(node.id, max_depth=2, max_results=8)
                for rn, rt, rw, *_ in related:
                    if rt in ("implies", "causa", "provoca", "lleva_a") and rw >= 0.3:
                        causal_chains.append(
                            f"{node.concept} →({rt})→ {rn.concept}"
                        )

        if causal_chains or causal_links:
            result.resolved = True
            result.confidence = min(1.0, (len(causal_chains) + len(causal_links)) * 0.2)
            parts = []
            if causal_chains:
                parts.append("Cadenas causales en el grafo:\n" + "\n".join(causal_chains[:5]))
            if causal_links:
                parts.append("Relaciones causales detectadas:\n" + "\n".join(causal_links[:5]))
            result.answer = "\n\n".join(parts)
            result.reasoning_chain = causal_chains + causal_links
            return result

        result.resolved = False
        result.reasoning_chain.append("sin relaciones causales encontradas")
        return result

    def resolve(self, query: str) -> InferenceResult:
        """Método principal: intenta resolver una consulta sin LLM.
        
        Estrategia secuencial:
        1. deduce() — búsqueda directa de conocimiento
        2. check_contradiction() — verificación de consistencia
        3. explain_causality() — relaciones causales
        4. graph composition — síntesis desde el grafo
        """
        # Estrategia 1: Deducir directamente
        result = self.deduce(query)
        if result.resolved and result.confidence >= DEDUCE_MIN_CONFIDENCE:
            result.method = "resolve:deduce"
            return result

        # Estrategia 2: Verificar contradicciones
        result = self.check_contradiction(query)
        if result.resolved and result.confidence >= CONTRADICTION_CONFIDENCE:
            result.method = "resolve:contradiction"
            return result

        # Estrategia 3: Explicar causalidad
        result = self.explain_causality(query)
        if result.resolved and result.confidence >= CAUSAL_MIN_CONFIDENCE:
            result.method = "resolve:causal"
            return result

        # Estrategia 4: Síntesis por composición de conceptos
        return self._synthesize_from_graph(query)

    def _synthesize_from_graph(self, query: str) -> InferenceResult:
        """Sintetiza una respuesta combinando múltiples conceptos del grafo."""
        result = InferenceResult(method="synthesis")
        if not self._ensure_loaded():
            return result

        neural = self._reasoner.reason(query, max_results=6)
        if neural.get("low_confidence", True):
            return result

        answer = neural.get("answer", "")
        if answer:
            result.resolved = True
            result.answer = f"[Composición desde el grafo]\n{answer}"
            result.confidence = neural.get("top_confidence", 0.3)
            result.propositions.append(Proposition(
                text=query, truth_value=True,
                confidence=result.confidence,
                reasoning_path=[c.get("concept", "")
                                for c in neural.get("reasoning_chain", [])[:5]]
            ))
        return result


# ─── Singleton ───────────────────────────────────────────────────

_instance = None

def get_inference() -> LogicalInferenceEngine:
    global _instance
    if _instance is None:
        _instance = LogicalInferenceEngine()
    return _instance


# ─── Stop words locaux (evitar import circular con knowledge_reasoner) ───
_STOP_WORDS = {
    "que", "del", "las", "los", "con", "por", "para", "como", "qué", "cómo",
    "puede", "todo", "esta", "este", "más", "eso", "esa", "entre", "tiene",
    "the", "this", "that", "what", "which", "when", "where", "why", "how",
    "from", "with", "without", "about", "into", "through", "during",
    "para", "pero", "sino", "cada", "muy", "solo", "entonces",
}


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    eng = get_inference()
    while True:
        q = input("\n❓ Consulta lógica: ").strip()
        if q in ("quit", "exit", "q"):
            break
        r = eng.resolve(q)
        print(f"  Resuelto: {r.resolved}")
        print(f"  Confianza: {r.confidence:.3f}")
        print(f"  Método: {r.method}")
        print(f"  Respuesta: {r.answer[:200]}")
        if r.reasoning_chain:
            print("  Cadena:")
            for c in r.reasoning_chain[:5]:
                print(f"    {c}")
