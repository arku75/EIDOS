"""
Semantic Router para EIDOS — Ruteo de queries sin LLM.

Inspirado en Dynamic Semantic Routing (Abraxas-365/llm-by-example).
Usa embeddings de ChromaDB para clasificar la INTENCIÓN del usuario
y rutear a la estrategia de razonamiento correcta.

TIPOS DE RUTA:
  - factual:    "¿qué es X?", "¿cómo funciona Y?"
  - evaluative: "¿es mejor X que Y?", "compara A vs B"
  - causal:     "¿por qué X?", "¿causa de Y?"
  - action:     "abre X", "ejecuta Y", "muestra Z"

Cada ruta tiene un handler especializado que usa el grafo neuronal
de forma distinta según la intención.

ZERO dependencias externas. Solo usa lo que EIDOS ya tiene:
ChromaDB (embeddings) + knowledge_reasoner (grafo neuronal).
"""

from __future__ import annotations

import logging
import re
import time
from typing import Dict, List, Optional, Tuple, Any

log = logging.getLogger(__name__)

# ── Patrones léxicos para clasificación rápida (<1ms, sin embeddings) ──────
# Son la primera línea de defensa. Si un patrón coincide, no hace falta ChromaDB.

_FACTUAL_PATTERNS = [
    r"\b(?:qué\s+es|que\s+es|definición\s+de|definicion\s+de|define|explica\s+qué\s+es)\b",
    r"\b(?:cómo\s+funciona|como\s+funciona|cómo\s+se\s+usa|como\s+se\s+usa)\b",
    r"\b(?:qué\s+significa|que\s+significa|a\s+qué\s+se\s+refiere)\b",
    r"\b(?:what\s+is|what\s+are|definition\s+of|define\s+)\b",
    r"\b(?:how\s+does|how\s+do|how\s+to\s+use|explain\s+what)\b",
    r"\b(?:describe|qué\s+son|que\s+son|quién\s+es|quien\s+es)\b",
    r"\b(?:cuál\s+es\s+el\s+concepto|concepto\s+de|cual\s+es\s+el)\b",
]

_EVALUATIVE_PATTERNS = [
    r"\b(?:es\s+mejor|es\s+peor|compara|comparativa|comparación|diferencia|ventajas?|desventajas?|conviene|conviene más|qué es mejor)\b",
    r"\b(?:vs\.?|versus|frente a|en comparación con|a diferencia de)\b",
    r"\b(?:cuál (?:es|sería) (?:mejor|peor|más|la diferencia))\b",
    r"\b(?:pros?\s+y\s+contras?|pros?\s*/\s*contras?)\b",
    r"\b(?:is\s+better|is\s+worse|compare|comparison|difference|advantages?|disadvantages?|vs\.?|versus)\b",
    r"\b(?:which\s+(?:is|would\s+be)\s+(?:better|worse|more|the\s+difference))\b",
]

_CAUSAL_PATTERNS = [
    r"\b(?:por\s+qué|porque|por\s+que|cuál\s+es\s+la\s+causa|causa\s+de|motivo\s+de|razón\s+de)\b",
    r"\b(?:para\s+qué\s+sirve|cuál\s+es\s+la\s+función|qué\s+función\s+tiene|por\s+qué\s+es\s+importante)\b",
    r"\b(?:cómo\s+es\s+que|qué\s+hace\s+que|cuál\s+es\s+el\s+propósito)\b",
    r"\b(?:why|what\s+is\s+the\s+cause|what\s+is\s+the\s+reason|why\s+is\s+it\s+important)\b",
    r"\b(?:what\s+is\s+the\s+purpose|how\s+come|how\s+does\s+it\s+happen)\b",
]

_ACTION_PATTERNS = [
    # Solo verbos imperativos CLAROS. Evitar "para" (ambiguo con preposición)
    # y "corre" (ambiguo con descripción "cómo corre X").
    r"\b(?:abre|abrir|ejecuta|ejecutar|lanza|lanzar|inicia|iniciar)\s+\w+",
    r"\b(?:muestra|mostrar|enseña|enseñar|lista|listar|busca|buscar)\s+\w+",
    r"\b(?:descarga|descargar|instala|instalar|desinstala|detén|detener|reinicia)\s+\w+",
    r"\b(?:open|run|start|launch|execute|show|display|list|search|find|download|install)\s+\w+",
]

# ── Embeddings de referencia para clasificación semántica (ChromaDB) ──────
# Frases canónicas que representan cada tipo de intención.
# Se comparan con la query del usuario vía cosine similarity.

_ROUTE_TEMPLATES = {
    "factual": [
        "qué es ssh",
        "cómo funciona el kernel de linux",
        "definición de protocolo de red",
        "qué significa tcp",
        "explica el concepto de firewall",
        "qué es una dirección ip",
        "what is a firewall",
        "how does dns work",
        "definition of encryption",
        "explain the concept of routing",
        "qué es eidos",
        "cómo se usa systemd",
        "describe el funcionamiento de http",
    ],
    "evaluative": [
        "es mejor linux que windows para servidores",
        "compara python con javascript",
        "ventajas y desventajas de systemd",
        "diferencia entre tcp y udp",
        "qué firewall es mejor",
        "compare linux vs windows for servers",
        "pros and cons of docker",
        "is python better than javascript",
        "cuál es mejor firewall o antivirus",
        "ventajas de usar ssh frente a telnet",
    ],
    "causal": [
        "por qué es importante un firewall",
        "para qué sirve un cortafuegos",
        "por qué usar cifrado en las comunicaciones",
        "cuál es la función de un router",
        "por qué se necesita un antivirus",
        "why is encryption important",
        "what is the purpose of a firewall",
        "why do we need dns",
        "por qué linux es más seguro",
        "para qué sirve un proxy",
    ],
    "action": [
        "abre firefox",
        "ejecuta el navegador",
        "muestra las ventanas abiertas",
        "lista los procesos",
        "busca archivos de configuración",
        "open the browser",
        "show me running processes",
        "list open ports",
        "descarga el archivo",
        "inicia el servicio",
    ],
}


class SemanticRouter:
    """Rutea queries a la estrategia de razonamiento correcta sin LLM.

    S119 #227 FASE 4: Optimizado — 1 query ChromaDB en vez de 43.
    Con caché LRU + ampliación de cobertura léxica.
    """

    # Máximo de entradas en caché
    _MAX_CACHE_SIZE = 256

    def __init__(self):
        self._chroma = None
        # S119 #227: Caché ahora FUNCIONAL (antes estaba declarado pero no usado)
        self._routes_cache: Dict[str, Tuple[str, float]] = {}
        self._cache_hits = 0
        self._built = False
        self._stats = {
            "lexical_hits": 0,
            "embedding_hits": 0,
            "cache_hits": 0,
            "fallback": 0,
            "total": 0,
        }
        # Precomputar palabras clave por tipo de ruta (para scoring rápido)
        self._template_keywords: Dict[str, List[set]] = {}
        for route_type, templates in _ROUTE_TEMPLATES.items():
            kw_sets = []
            for t in templates:
                words = set(w for w in t.lower().split()
                           if len(w) > 3 and w not in _STOP_WORDS)
                if words:
                    kw_sets.append(words)
            self._template_keywords[route_type] = kw_sets

    def _get_chroma(self):
        """Acceso lazy a ChromaDB para no cargarlo si no se necesita."""
        if self._chroma is None:
            try:
                from core.colony_chroma import get_chroma_memory
                self._chroma = get_chroma_memory()
            except Exception as e:
                log.debug("ChromaDB no disponible para semantic router: %s", e)
        return self._chroma

    def classify_lexical(self, query: str) -> Optional[str]:
        """
        Clasificación rápida por patrones léxicos (regex).
        Retorna el tipo de ruta o None si no hay match claro.
        <1ms, sin dependencias externas.
        """
        qlower = query.lower()

        # Orden importa: action puede solaparse con factual en casos como
        # "abre firefox" vs "qué es firefox". Action primero porque si el
        # usuario dice "abre X", es casi seguro una acción.
        for pattern in _ACTION_PATTERNS:
            if re.search(pattern, qlower):
                self._stats["lexical_hits"] += 1
                return "action"

        for pattern in _EVALUATIVE_PATTERNS:
            if re.search(pattern, qlower):
                self._stats["lexical_hits"] += 1
                return "evaluative"

        for pattern in _CAUSAL_PATTERNS:
            if re.search(pattern, qlower):
                self._stats["lexical_hits"] += 1
                return "causal"

        for pattern in _FACTUAL_PATTERNS:
            if re.search(pattern, qlower):
                self._stats["lexical_hits"] += 1
                return "factual"

        # No hay match léxico → necesita embeddings o fallback
        return None

    def classify_embedding(self, query: str) -> Tuple[str, float]:
        """
        Clasificación semántica optimizada (S119 #227 FASE 4).

        ANTES: 43 queries ChromaDB (1 por template) → 30-48s
        AHORA: 1 query ChromaDB → keyword scoring → <2s

        Estrategia:
        1. Buscar la query del usuario UNA SOLA VEZ en ChromaDB
        2. Combinar conceptos/devueltos en texto agregado
        3. Puntuar cada tipo de ruta por solapamiento de keywords
           entre los templates y los resultados de ChromaDB
        4. Caché: si la misma query se repite, respuesta instantánea
        """
        # ── Cache check ──────────────────────────────────────────────────
        cache_key = query.lower().strip().rstrip("?¿!¡.,;:")
        if cache_key in self._routes_cache:
            self._stats["cache_hits"] += 1
            return self._routes_cache[cache_key]

        # ── Single ChromaDB query ────────────────────────────────────────
        chroma = self._get_chroma()
        if chroma is None or not chroma.is_ready():
            return self._fallback(cache_key)

        try:
            results = chroma.search(query, limit=15, min_score=0.25)
        except Exception as e:
            log.debug("ChromaDB search error: %s", e)
            return self._fallback(cache_key)

        if not results:
            return self._fallback(cache_key)

        # ── Aggregate result text ────────────────────────────────────────
        text_parts = []
        for r in results[:12]:
            concept = r.get("concept", "")
            definition = r.get("definition", "")
            if concept:
                text_parts.append(concept.lower())
            if definition:
                text_parts.append(definition.lower()[:200])
        combined = " ".join(text_parts)

        if not combined.strip():
            return self._fallback(cache_key)

        # ── Score each route type by keyword overlap ─────────────────────
        route_scores: Dict[str, float] = {}
        for route_type, kw_list in self._template_keywords.items():
            score = 0.0
            matched_templates = 0
            for kw_set in kw_list:
                overlap = sum(1 for w in kw_set if w in combined)
                if overlap >= 2:  # Al menos 2 keywords del template en resultados
                    score += 0.08 * overlap
                    matched_templates += 1
            # Bonus por múltiples templates del mismo tipo
            if matched_templates >= 2:
                score += 0.15 * matched_templates
            route_scores[route_type] = min(score, 1.0)

        if not route_scores:
            return self._fallback(cache_key)

        best_type = max(route_scores, key=route_scores.get)
        best_score = route_scores[best_type]

        # Si la mejor puntuación es muy baja, default a factual
        if best_score < 0.1:
            result = ("factual", 0.0)
        else:
            result = (best_type, round(best_score, 3))

        # ── Guardar en caché ─────────────────────────────────────────────
        if len(self._routes_cache) >= self._MAX_CACHE_SIZE:
            # LRU simple: eliminar la entrada más antigua (first key)
            first_key = next(iter(self._routes_cache))
            del self._routes_cache[first_key]
        self._routes_cache[cache_key] = result
        self._stats["embedding_hits"] += 1
        return result

    def _fallback(self, cache_key: str = "") -> Tuple[str, float]:
        """Fallback a factual con caché."""
        result = ("factual", 0.0)
        if cache_key:
            if len(self._routes_cache) >= self._MAX_CACHE_SIZE:
                first_key = next(iter(self._routes_cache))
                del self._routes_cache[first_key]
            self._routes_cache[cache_key] = result
        self._stats["fallback"] += 1
        return result

    def route(self, query: str) -> Tuple[str, float, str]:
        """
        Clasifica la query y retorna (tipo_ruta, confidence, método).

        Orden de resolución:
        1. Patrones léxicos (<1ms) — cubre ~80% de queries
        2. Caché de rutas (<0.1ms) — queries repetidas
        3. Embedding ChromaDB (1 query, <2s) — queries nuevas ambiguas
        4. Fallback a 'factual' — por defecto seguro

        S119 #227 FASE 4: 43 queries → 1 query + caché funcional.
        """
        self._stats["total"] += 1

        # 1. Léxico (instantáneo)
        lexical = self.classify_lexical(query)
        if lexical:
            return (lexical, 1.0, "lexical")

        # 2. Embeddings optimizado (1 query + caché)
        route_type, confidence = self.classify_embedding(query)
        if confidence > 0.0:
            return (route_type, confidence, "embedding")

        # 3. Fallback
        return ("factual", 0.0, "fallback")

    def get_stats(self) -> Dict[str, Any]:
        s = dict(self._stats)
        s["cache_size"] = len(self._routes_cache)
        return s


# ── Stopwords para filtrado de keywords en templates ────────────────────────
_STOP_WORDS = {
    "para", "como", "entre", "frente", "sobre", "desde", "hasta",
    "the", "for", "and", "with", "from", "that", "this", "than",
    "what", "when", "where", "which", "does", "have", "been",
    "qué", "cómo", "cuál", "por", "los", "las", "una", "uno",
    "del", "con", "sus", "son", "era", "has", "had", "can",
    "all", "not", "are", "was", "were", "will", "would",
}


# ── Singleton ────────────────────────────────────────────────────────────────
_router_instance: Optional[SemanticRouter] = None


def get_semantic_router() -> SemanticRouter:
    global _router_instance
    if _router_instance is None:
        _router_instance = SemanticRouter()
    return _router_instance
