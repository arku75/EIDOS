"""
EIDOS NLG — Generador de Lenguaje Natural por plantillas (S111).

Toma los nodos que el grafo neuronal recuperó + el tipo de ruta semántica
y GENERA frases nuevas con conectores naturales, en vez de pegar fichas.

ZERO LLM. ZERO API. ZERO pesos. 100% determinista y reglas explícitas.

Filosofía:
  - El grafo RECUPERA hechos (qué es, para qué sirve, con qué se relaciona).
  - El NLG RECOMPONE esos hechos en prosa legible.
  - No inventa información: solo reordena y conecta lo que el grafo ya sabe.

Tres modos según la ruta del SemanticRouter:
  - factual:    "X es Y. Sirve para Z. Se relaciona con A y B."
  - evaluative: "Comparando A y B: A destaca por... B por... En tu caso..."
  - causal:     "X es importante porque... Esto permite... En consecuencia..."
"""

from __future__ import annotations

import re
from typing import Dict, List, Optional, Tuple, Any

# ── Conectores deterministas (elegidos por índice, nunca al azar) ────────────
_CONECTORES_ADICION = [
    "Además", "También", "Por otra parte", "Asimismo", "De hecho",
]
_CONECTORES_DETALLE = [
    "En concreto", "Concretamente", "Más en detalle", "Específicamente",
]
_CONECTORES_RELACION = [
    "Se relaciona con", "Está conectado con", "Tiene que ver con",
    "Va de la mano con",
]

# ── Verbos que indican función/propósito (para detectar la oración "para qué") ─
_VERBOS_FUNCION = (
    "sirve", "usa", "utiliza", "permite", "protege", "filtra", "gestiona",
    "controla", "evita", "previene", "asegura", "garantiza", "conecta",
    "redirige", "mantiene", "bloquea", "cifra", "encripta", "administra",
    "enables", "allows", "protects", "filters", "manages", "controls",
    "prevents", "secures", "connects", "redirects", "maintains", "blocks",
)

# ── Prefijos de concepto que hay que limpiar ("SSH: definicion" → "SSH") ──────
_PREFIJOS_CONCEPTO = (
    ": definicion", ": definición", ": concepto", ": tunel", ": túnel",
    ": identidad", ": arquitectura", ": kernel",
)

# ── Marcadores internos de EIDOS que se pueden omitir en respuesta a humano ──
# (No se borran siempre: solo si la frase queda redundante)
_RUIDO_INTERNO = re.compile(
    r"\b(MITRE ATT&CK Technique|Tactic:|Platforms:|Citation:|https?://\S+)\b",
    re.IGNORECASE,
)

# ── Palabras funcionales para detectar idioma de una oración ─────────────────
_ENGLISH_MARKERS = {
    "the", "may", "adversaries", "this", "that", "with", "for", "and",
    "can", "are", "was", "were", "which", "their", "they", "would",
    "abuse", "using", "include", "such", "into", "from", "have", "has",
    "or", "to", "in", "of", "system", "network", "controls", "modify",
    "disable", "bypass", "order", "limiting", "usage", "data", "be",
    "on", "by", "as", "an", "is", "it", "its", "these", "other", "well",
    "enable", "allow", "otherwise", "not", "all", "via", "command",
}
_SPANISH_MARKERS = {
    "que", "de", "la", "el", "en", "un", "una", "por", "para", "con",
    "los", "las", "es", "sirve", "usa", "del", "se", "su", "sus", "como",
    "más", "pero", "este", "esta", "según", "redirige", "filtra", "permite",
    "protege", "tráfico", "red", "puerto", "puertos", "conexión", "remota",
}


def _es_espanol(texto: str) -> bool:
    """¿El texto es español? Compara marcadores ES vs EN (no solo proporción)."""
    palabras = re.findall(r"[a-záéíóúñ]+", texto.lower())
    if not palabras:
        return True
    es = sum(1 for w in palabras if w in _SPANISH_MARKERS)
    en = sum(1 for w in palabras if w in _ENGLISH_MARKERS)
    # Si hay claramente más inglés que español → no es español
    if en > es and en >= 2:
        return False
    # Si proporción de inglés es alta y no hay apoyo español → no es español
    if (en / len(palabras)) >= 0.20 and es == 0:
        return False
    return True


def _capitalizar_inicio(s: str) -> str:
    """Asegura que la oración empieza con mayúscula, sin romper acrónimos."""
    s = s.strip()
    if not s:
        return s
    return s[0].upper() + s[1:]


def _es_acronimo_o_propio(palabra: str) -> bool:
    """¿La palabra es un acrónimo (SSH, NAT) o nombre propio (EIDOS, Secure)?"""
    if not palabra:
        return False
    # Todo mayúsculas y >1 letra → acrónimo (SSH, NAT, DNS)
    if len(palabra) > 1 and palabra.isupper():
        return True
    # Empieza mayúscula y tiene otra mayúscula → CamelCase/acrónimo (EIDOS, FRITZ)
    if palabra[0].isupper() and any(c.isupper() for c in palabra[1:]):
        return True
    return False


def _limpiar_concepto(concept: str) -> str:
    """'SSH: definicion' → 'SSH'. 'book:completed:linux_basics' → 'linux basics'."""
    c = concept.strip()
    # Cortar por prefijos conocidos
    low = c.lower()
    for pref in _PREFIJOS_CONCEPTO:
        idx = low.find(pref)
        if idx > 0:
            c = c[:idx].strip()
            break
    # Limpiar prefijos tipo "book:completed:", "identidad:", "char:..."
    if ":" in c:
        partes = c.split(":")
        # Quedarse con la parte más legible (la última no vacía con espacios/letras)
        c = partes[-1].replace("_", " ").strip()
    return c


def _split_oraciones(texto: str) -> List[str]:
    """Divide un texto en oraciones limpias."""
    if not texto:
        return []
    # Normalizar saltos de línea como separadores
    texto = re.sub(r"\s*\n+\s*", ". ", texto)
    # Dividir por punto, signo de exclamación o interrogación seguido de espacio
    crudas = re.split(r"(?<=[.!?])\s+", texto)
    oraciones = []
    for o in crudas:
        o = o.strip()
        # Descartar fragmentos vacíos o demasiado cortos
        if len(o) < 8:
            continue
        oraciones.append(o)
    return oraciones


def _quitar_prefijo_concepto(oracion: str, concepto: str) -> str:
    """Si la oración empieza repitiendo el concepto+verbo, lo conserva tal cual.
    Solo normaliza espacios."""
    return re.sub(r"\s+", " ", oracion).strip()


def _es_oracion_funcional(oracion: str) -> bool:
    """¿Esta oración describe para qué sirve algo?"""
    low = oracion.lower()
    return any(v in low.split() or v in low for v in _VERBOS_FUNCION)


def _es_ruido(oracion: str) -> bool:
    """¿Es una oración de metadatos internos poco útil para un humano?"""
    if _RUIDO_INTERNO.search(oracion):
        # Solo es ruido puro si casi toda la oración son metadatos
        limpio = _RUIDO_INTERNO.sub("", oracion).strip(" .,:-")
        return len(limpio) < 15
    return False


class NaturalLanguageGenerator:
    """Genera prosa natural desde los nodos del grafo. Sin LLM."""

    def generate(
        self,
        query: str,
        route_type: str,
        nodes: List[Tuple[Any, float]],
        comparison_entities: Optional[List[str]] = None,
        related_concepts: Optional[List[str]] = None,
    ) -> str:
        """
        Args:
            query: la pregunta del usuario
            route_type: factual | evaluative | causal | action
            nodes: lista de (ConceptNode, score) ordenada por relevancia
            comparison_entities: ['linux','windows'] si es evaluativa
            related_concepts: nombres de conceptos relacionados en el grafo
        Returns:
            Texto natural generado, o "" si no hay material suficiente.
        """
        if not nodes:
            return ""
        try:
            if route_type == "evaluative":
                return self._gen_evaluative(query, nodes, comparison_entities or [])
            if route_type == "causal":
                return self._gen_causal(query, nodes, related_concepts or [])
            # factual (y action sin handler propio caen aquí)
            return self._gen_factual(query, nodes, related_concepts or [])
        except Exception:
            return ""

    # ── FACTUAL: "X es Y. Sirve para Z. Se relaciona con A." ──────────────────
    def _gen_factual(self, query, nodes, related):
        principal, score = self._nodo_principal(query, nodes)
        if principal is None:
            return ""

        nombre = _limpiar_concepto(principal.concept)
        es_es = _es_espanol(query)
        oraciones = self._oraciones_utiles(principal.definition, es_es)
        if not oraciones:
            return ""

        usadas: List[str] = []
        partes: List[str] = []

        # 1. La esencia: primera oración (qué es)
        partes.append(_capitalizar_inicio(oraciones[0].rstrip(".") + "."))
        usadas.append(oraciones[0].rstrip(".").lower())

        # 2. La función: primera oración funcional distinta de la esencia
        for o in oraciones[1:]:
            if _es_oracion_funcional(o) and o.rstrip(".").lower() not in usadas:
                partes.append(_capitalizar_inicio(o.rstrip(".") + "."))
                usadas.append(o.rstrip(".").lower())
                break

        # 3. Un detalle adicional de otro nodo relevante (enriquece, no repite)
        for node, sc in nodes[1:5]:
            if node.id == principal.id or len(partes) >= 3:
                break
            ors = self._oraciones_utiles(node.definition, es_es)
            for o in ors:
                clave = o.rstrip(".").lower()
                if clave not in usadas and not self._muy_similar(clave, usadas):
                    partes.append(_capitalizar_inicio(o.rstrip(".") + "."))
                    usadas.append(clave)
                    break
            if len(partes) >= 3:
                break

        # 4. Relaciones del grafo (lo que el cerebro conectó) — oración generada
        rels = self._relacionados_legibles(related, exclude=nombre)
        if rels:
            conector = _CONECTORES_RELACION[len(nombre) % len(_CONECTORES_RELACION)]
            partes.append(f"{conector} {rels}.")

        return " ".join(partes)

    # ── EVALUATIVE: contraste real entre dos entidades ───────────────────────
    def _gen_evaluative(self, query, nodes, entidades):
        if len(entidades) < 2:
            # Sin dos lados claros: degradar a factual sobre el tema
            return self._gen_factual(query, nodes, [])

        a, b = entidades[0], entidades[1]
        es_es = _es_espanol(query)
        # Buscar el mejor nodo para cada entidad entre los recuperados
        nodo_a = self._mejor_nodo_para(a, nodes)
        nodo_b = self._mejor_nodo_para(b, nodes)

        partes: List[str] = [f"Comparando {a.upper()} y {b.upper()}:"]

        if nodo_a:
            ors = self._oraciones_utiles(nodo_a.definition, es_es)
            if ors:
                partes.append(f"{a.capitalize()} — {_capitalizar_inicio(ors[0].rstrip('.'))}.")
        if nodo_b:
            ors = self._oraciones_utiles(nodo_b.definition, es_es)
            if ors:
                partes.append(f"{b.capitalize()} — {_capitalizar_inicio(ors[0].rstrip('.'))}.")

        # Si no encontró material para ambos, no fingir comparación
        if len(partes) < 3:
            return self._gen_factual(query, nodes, [])

        # Cierre honesto: EIDOS no impone un veredicto absoluto
        partes.append(
            f"No hay un ganador universal: la mejor opción depende de qué "
            f"priorices en tu caso concreto (rendimiento, seguridad, "
            f"compatibilidad o coste)."
        )
        return " ".join(partes)

    # ── CAUSAL: "X importa porque... Esto permite... En consecuencia..." ──────
    def _gen_causal(self, query, nodes, related):
        principal, score = self._nodo_principal(query, nodes)
        if principal is None:
            return ""

        nombre = _limpiar_concepto(principal.concept)
        es_es = _es_espanol(query)
        partes: List[str] = []
        usadas: List[str] = []

        # 1. Qué es (base de la explicación causal)
        oraciones = self._oraciones_utiles(principal.definition, es_es)
        if oraciones:
            partes.append(_capitalizar_inicio(oraciones[0].rstrip(".") + "."))
            usadas.append(oraciones[0].rstrip(".").lower())

        # 2. Buscar la cadena causal: oraciones funcionales en TODOS los nodos
        #    (deduplicadas y distintas de la esencia ya usada)
        causas: List[str] = []
        for node, sc in nodes[:6]:
            for o in self._oraciones_utiles(node.definition, es_es):
                clave = o.rstrip(".").lower()
                if not _es_oracion_funcional(o):
                    continue
                if clave in usadas or self._muy_similar(clave, usadas):
                    continue
                if clave in [c.rstrip(".").lower() for c in causas]:
                    continue
                causas.append(o.rstrip("."))
                usadas.append(clave)
                if len(causas) >= 3:
                    break
            if len(causas) >= 3:
                break

        if causas:
            partes.append(f"Es importante porque {self._inicio_min(causas[0])}.")
            for c in causas[1:3]:
                conector = _CONECTORES_ADICION[len(partes) % len(_CONECTORES_ADICION)]
                partes.append(f"{conector} {self._inicio_min(c)}.")
        else:
            partes.append("Su relevancia viene de su papel dentro del sistema.")

        # 3. Síntesis causal
        partes.append(
            f"En conjunto, esto hace que {nombre} sea un elemento clave para "
            f"la seguridad y el correcto funcionamiento del sistema."
        )
        return " ".join(partes)

    # ── Helpers ──────────────────────────────────────────────────────────────
    def _oraciones_utiles(self, definicion, es_es=True):
        """Oraciones limpias, sin ruido y (si la query es ES) sin inglés."""
        out = []
        for o in _split_oraciones(definicion):
            if _es_ruido(o):
                continue
            if es_es and not _es_espanol(o):
                continue
            out.append(o)
        return out

    def _muy_similar(self, clave, usadas, umbral=0.7):
        """¿La oración 'clave' es casi idéntica a alguna ya usada? (anti-repetición)"""
        kw = set(clave.split())
        if not kw:
            return False
        for u in usadas:
            uw = set(u.split())
            if not uw:
                continue
            inter = len(kw & uw)
            union = len(kw | uw)
            if union and inter / union >= umbral:
                return True
        return False

    def _inicio_min(self, oracion):
        """Baja la primera letra solo si NO es acrónimo/nombre propio."""
        o = oracion.strip()
        if not o:
            return o
        primera = o.split()[0]
        if _es_acronimo_o_propio(primera):
            return o
        return o[0].lower() + o[1:]

    def _nodo_principal(self, query, nodes):
        """El nodo cuyo concepto mejor coincide con la query.

        S119 #224: Prefiere seed_knowledge y conocimiento destilado sobre
        research web (wikipedia, duckduckgo). Usa quality_score si existe.
        """
        qlow = query.lower()
        qwords = set(re.findall(r"[a-zA-Záéíóúñ0-9]{2,}", qlow))
        mejor = None
        mejor_score = -1.0
        for node, score in nodes:
            cl = _limpiar_concepto(node.concept).lower()
            bonus = 0.0
            # Bonus si el concepto aparece literal en la query
            if cl and cl in qlow:
                bonus += 2.0
            cwords = set(re.findall(r"[a-zA-Záéíóúñ0-9]{2,}", cl))
            bonus += len(qwords & cwords) * 0.5
            # Bonus si es la definición canónica (concepto "X: definicion")
            if "definicion" in node.concept.lower() or "definición" in node.concept.lower():
                bonus += 1.0

            # ── S119 #224+#225: Calidad de fuente + namespace ─────────────
            source = getattr(node, "source", "") or ""
            quality = getattr(node, "quality_score", None)
            category = getattr(node, "category", "") or ""

            # S119 #225: NUNCA usar nodos del namespace core (interno)
            # Estos son metadatos de código, estructura, inferencias — no
            # deben aparecer en respuestas a humanos.
            if source in ("code_analyzer", "reasoned", "graphify",
                          "self_index:class", "self_index:function",
                          "self_index:module", "tabula_rasa:path_scan",
                          "tabula_rasa:ast", "tabula_rasa:ps"):
                continue  # Saltar completamente

            if category in ("eidos_function", "eidos_class", "eidos_module",
                           "code_structure", "inferred", "lifecycle"):
                continue  # Saltar completamente

            # Penalizar fuentes basura (ATT&CK, CVE, wordnet)
            if source in ("mitre_attck", "circl", "nvd_nist", "wordnet"):
                bonus -= 2.0  # Penalización fuerte

            # Penalizar nodos inferidos/basura genéricos (legacy)
            if source in ("discovered", "reasoned"):
                bonus -= 1.0

            # Premiar conocimiento destilado/curado (SEED > research)
            if source in ("oro_skills", "kali_tools", "docs:hermes",
                          "docs:openclaw_skills", "docs:vseidos", "docs:openclaw",
                          "distilled_from_curiosity", "distilled_from_deliberation",
                          "user"):
                bonus += 1.5  # Máxima prioridad
            elif source and source.startswith("docs:"):
                bonus += 1.2
            elif source and source.startswith("research:man") or source == "research:apt":
                bonus += 0.8  # Man pages > web research
            elif source and source.startswith("research:"):
                bonus += 0.2  # Web research: útil pero no premium
            elif source == "auto_learner":
                bonus += 0.3

            # Bonus por quality_score (S119 #239)
            if quality is not None:
                bonus += float(quality) * 0.5  # 0.85 → +0.425, 0.0 → +0.0

            total = score + bonus
            if total > mejor_score:
                mejor_score = total
                mejor = node
        return mejor, mejor_score

    def _mejor_nodo_para(self, entidad, nodes):
        """El mejor nodo que habla de una entidad concreta (ej. 'linux').
        S119 #224: Prefiere fuentes de calidad (seed > docs > research)."""
        elow = entidad.lower()
        mejor = None
        mejor_score = -1.0
        for node, score in nodes:
            txt = (node.concept + " " + node.definition).lower()
            if elow in txt:
                # Preferir nodos donde la entidad está en el concepto
                bonus = 1.0 if elow in node.concept.lower() else 0.0
                # S119 #224+#225: Bonus por calidad + namespace filter
                source = getattr(node, "source", "") or ""
                category = getattr(node, "category", "") or ""
                # Saltar nodos del namespace core (interno)
                if source in ("code_analyzer", "reasoned", "graphify",
                              "self_index:class", "self_index:function",
                              "self_index:module") or \
                   category in ("eidos_function", "eidos_class", "eidos_module",
                               "code_structure", "inferred"):
                    continue
                if source in ("oro_skills", "kali_tools", "docs:hermes",
                              "docs:openclaw_skills", "docs:vseidos",
                              "distilled_from_curiosity", "distilled_from_deliberation",
                              "user"):
                    bonus += 1.0
                elif source and source.startswith("docs:"):
                    bonus += 0.7
                elif source and source.startswith("research:man") or source == "research:apt":
                    bonus += 0.5
                elif source in ("mitre_attck", "circl", "nvd_nist", "wordnet",
                                "reasoned", "code_analyzer"):
                    bonus -= 1.5
                if score + bonus > mejor_score:
                    mejor_score = score + bonus
                    mejor = node
        return mejor

    def _relacionados_legibles(self, related, exclude="", limit=3):
        """Convierte una lista de conceptos relacionados en texto legible."""
        if not related:
            return ""
        vistos = []
        for r in related:
            nombre = _limpiar_concepto(r)
            if not nombre or nombre.lower() == exclude.lower():
                continue
            if nombre.lower() in [v.lower() for v in vistos]:
                continue
            # Evitar nodos basura (IDs, rutas, técnicas MITRE, multi-palabra raro)
            low = nombre.lower()
            if any(x in low for x in ("att&ck", "t10", "t15", "t20", "/", "_", ":")):
                continue
            # Descartar conceptos descubiertos tipo "Sistema Operativo Software Mantiene"
            # (3+ palabras capitalizadas seguidas = ruido de descubrimiento)
            palabras = nombre.split()
            if len(palabras) >= 3 and sum(1 for p in palabras if p[:1].isupper()) >= 3:
                continue
            # Descartar nombres demasiado largos (probable frase, no concepto)
            if len(nombre) > 40:
                continue
            vistos.append(nombre)
            if len(vistos) >= limit:
                break
        if not vistos:
            return ""
        if len(vistos) == 1:
            return vistos[0]
        return ", ".join(vistos[:-1]) + " y " + vistos[-1]


# ── Singleton ────────────────────────────────────────────────────────────────
_nlg_instance: Optional[NaturalLanguageGenerator] = None


def get_nlg() -> NaturalLanguageGenerator:
    global _nlg_instance
    if _nlg_instance is None:
        _nlg_instance = NaturalLanguageGenerator()
    return _nlg_instance
