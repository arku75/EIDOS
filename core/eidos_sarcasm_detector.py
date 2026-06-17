"""
core/eidos_sarcasm_detector.py — Detección de sarcasmo/ironía en SER [S105]

"Entender lo que SER dice, no solo lo que sus palabras significan." — DeepSeek

Detecta sarcasmo, ironía y humor seco en mensajes de SER sin LLM externo.
Usa 4 capas complementarias:
  1. FastText cosine similarity contra prototipos semánticos
  2. Patrones léxicos de sarcasmo en español
  3. Contradicción sentimiento positivo vs tono general
  4. Patrones de puntuación sospechosos

Integración:
  - SerModel: nuevo tipo de mensaje 'sarcastic'
  - Affect: VAD reaction (valencia mixta, arousal sube)
  - Logos: respuesta consciente del sarcasmo

Uso:
    detector = get_sarcasm_detector()
    result = detector.analyze("Claro, como no se me había ocurrido...")
    # → {is_sarcastic: True, confidence: 0.72, type: "ironía", ...}
"""

from __future__ import annotations

import json
import logging
import re
import time
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

log = logging.getLogger("eidos.sarcasm")

# ═══════════════════════════════════════════════════════════════════════════
# Prototipos semánticos para FastText
# ═══════════════════════════════════════════════════════════════════════════

# Frases que codifican la esencia de cada tipo de comunicación
SARCASTIC_PROTOTYPES = [
    "claro que sí cómo no se me había ocurrido",
    "qué sorpresa no me lo esperaba para nada",
    "por supuesto es exactamente lo que quería oír",
    "genial maravilloso otra vez lo mismo",
    "no me digas qué increíble descubrimiento",
    "vaya nunca había visto algo tan brillante",
    "si claro porque eso funciona siempre",
    "ah bueno entonces ya está todo resuelto",
    "fantástico otro problema más que celebrar",
    "qué bien otro error para la colección",
]

IRONIC_PROTOTYPES = [
    "qué curioso justo lo contrario de lo que dije",
    "interesante no esperaba menos de ti",
    "vaya tiene mucho sentido si lo piensas al revés",
    "qué irónico que pase justo ahora",
    "el destino tiene un gran sentido del humor",
    "qué casualidad tan conveniente",
    "me encanta cuando todo sale según el plan",
    "la vida es maravillosa y nada duele",
]

DRY_HUMOR_PROTOTYPES = [
    "si esto es éxito no quiero imaginar el fracaso",
    "somos los mejores en algo tiene que ser",
    "esto solo puede mejorar y si no también",
    "la documentación se escribió sola aparentemente",
    "el código funciona no preguntes cómo",
    "tengo toda la confianza del mundo en este plan",
    "mi optimismo es inversamente proporcional a la realidad",
]

SINCERE_PROTOTYPES = [
    "gracias por la información es útil",
    "necesito ayuda con este problema",
    "explícame cómo funciona esto por favor",
    "qué opinas sobre este tema",
    "me gustaría entender mejor esta parte",
    "tienes razón en lo que dices",
    "vamos a resolver esto juntos",
    "confío en tu criterio para decidir",
]

# ═══════════════════════════════════════════════════════════════════════════
# Patrones léxicos de sarcasmo en español
# ═══════════════════════════════════════════════════════════════════════════

SARCASTIC_PATTERNS = [
    # Frases hechas sarcásticas
    (re.compile(r'claro\s*(que\s*)?(s[ií]|por\s*supuesto)', re.IGNORECASE), 0.55),
    (re.compile(r'c[oó]mo\s*no\s*(se\s*me\s*)?(hab[ií]a\s*)?ocurrido', re.IGNORECASE), 0.60),
    (re.compile(r'qu[eé]\s*sorpresa', re.IGNORECASE), 0.50),
    (re.compile(r'no\s*me\s*digas', re.IGNORECASE), 0.45),
    (re.compile(r'qu[eé]\s*bien\s*(otro|otra|más)', re.IGNORECASE), 0.55),
    (re.compile(r'genial\s*(otro|otra|más)', re.IGNORECASE), 0.55),
    (re.compile(r'fant[aá]stico', re.IGNORECASE), 0.40),
    (re.compile(r'maravilloso\s*(otro|otra)', re.IGNORECASE), 0.55),
    (re.compile(r'por\s*supuesto\s*,?\s*(como|que|siempre|es)', re.IGNORECASE), 0.50),
    (re.compile(r'si\s*claro\s*(porque|como)', re.IGNORECASE), 0.55),
    (re.compile(r'vaya\s*(qu[eé]|con|nunca)', re.IGNORECASE), 0.40),
    (re.compile(r'otra\s*vez\s*(ser[aá]|lo\s*mismo)', re.IGNORECASE), 0.35),
    (re.compile(r'c[oó]mo\s*me\s*alegro', re.IGNORECASE), 0.60),
    (re.compile(r'qu[eé]\s*casualidad', re.IGNORECASE), 0.45),
    (re.compile(r'justo\s*lo\s*que\s*necesitaba', re.IGNORECASE), 0.55),
    # Patrones con puntos suspensivos (marca común de sarcasmo)
    (re.compile(r'\.{3,}\s*$'), 0.25),
    # Signos de exclamación excesivos combinados con palabras positivas
    (re.compile(r'(genial|fant[aá]stico|maravilloso|perfecto|bravo)\s*!{2,}'), 0.35),
]

# Palabras positivas que en contexto sarcástico son marcadores
POSITIVE_MARKERS = {
    "genial", "fantástico", "maravilloso", "perfecto", "bravo",
    "increíble", "magnífico", "excelente", "brillante", "estupendo",
    "fenomenal", "sensacional", "insuperable", "sublime",
}

# Palabras negativas que refuerzan lectura sarcástica de positivas
NEGATIVE_MARKERS = {
    "otra vez", "como siempre", "para variar", "sorpresa",
    "casualidad", "maldito", "puñetero", "maldita", "puñetera",
}

# ═══════════════════════════════════════════════════════════════════════════
# Emojis con carga sarcástica
# ═══════════════════════════════════════════════════════════════════════════

SARCASTIC_EMOJIS = {
    "🙃": 0.6,   # upside-down face → sarcasmo clásico
    "😏": 0.5,   # smirk → ironía
    "🥲": 0.3,   # smiling through pain
    "😒": 0.4,   # unamused → escepticismo
    "😑": 0.3,   # expressionless → resignación
    "🤷": 0.2,   # shrug → indiferencia fingida
    "😅": 0.2,   # nervous smile → incomodidad
}


class SarcasmDetector:
    """Detector multi-capa de sarcasmo/ironía en mensajes de SER.

    Sin LLM — combinación de FastText + patrones léxicos + heurísticas.
    """

    def __init__(self):
        self._detection_count: int = 0
        self._sarcasm_count: int = 0
        self._history: List[Dict] = []  # últimas detecciones
        self._ser_baseline: Optional[Dict] = None  # tono habitual de SER
        self._fasttext_vectors: Dict[str, Any] = {}  # caché de vectores prototipo
        self._cache_ts: float = 0

    # ═══════════════════════════════════════════════════════════════════════
    # API principal
    # ═══════════════════════════════════════════════════════════════════════

    def analyze(self, message: str, context: Dict = None) -> Dict[str, Any]:
        """Analiza un mensaje de SER en busca de sarcasmo/ironía.

        Args:
            message: texto del mensaje
            context: contexto opcional (últimos mensajes, tono previo, etc.)

        Returns:
            {
                "is_sarcastic": bool,
                "confidence": float (0-1),
                "sarcasm_type": "sarcasmo" | "ironía" | "humor_seco" | "none",
                "layers": {  # contribución de cada capa
                    "lexical": {"match": bool, "score": float, "pattern": str},
                    "fasttext": {"similarity_sarcasm": float, "similarity_sincere": float},
                    "sentiment_contradiction": {"detected": bool, "score": float},
                    "punctuation": {"suspicious": bool, "score": float},
                    "emoji": {"found": bool, "emoji": str, "score": float},
                },
                "vad_impact": {"v": float, "a": float, "d": float},  # delta VAD
                "recommended_response": str,
            }
        """
        self._detection_count += 1
        ctx = context or {}

        # ── Capa 1: Patrones léxicos ──
        lexical = self._analyze_lexical(message)

        # ── Capa 2: FastText ──
        fasttext = self._analyze_fasttext(message)

        # ── Capa 3: Contradicción sentimental ──
        sentiment = self._analyze_sentiment_contradiction(message)

        # ── Capa 4: Puntuación ──
        punctuation = self._analyze_punctuation(message)

        # ── Capa 5: Emojis ──
        emoji = self._analyze_emojis(message)

        # ── Fusión ponderada ──
        scores = []

        if lexical["match"]:
            scores.append(("lexical", lexical["score"], 0.40))
        if fasttext["similarity_sarcasm"] and fasttext["similarity_sarcasm"] > 0.25:
            scores.append(("fasttext", fasttext["sarcasm_score"], 0.30))
        if sentiment["detected"]:
            scores.append(("sentiment", sentiment["score"], 0.15))
        if punctuation["suspicious"]:
            scores.append(("punctuation", punctuation["score"], 0.05))
        if emoji["found"]:
            scores.append(("emoji", emoji["score"], 0.10))

        if not scores:
            return self._no_sarcasm_result(lexical, fasttext, sentiment,
                                           punctuation, emoji)

        # Puntuación ponderada
        total_weight = sum(w for _, _, w in scores)
        weighted_score = sum(s * w for _, s, w in scores) / max(0.01, total_weight)

        # Boost por múltiples capas coincidentes
        layer_bonus = min(0.20, (len(scores) - 1) * 0.08)
        confidence = min(0.95, weighted_score + layer_bonus)

        # Determinar tipo
        sarcasm_type = self._classify_type(lexical, fasttext, sentiment, emoji)

        # VAD impact
        vad_impact = self._calculate_vad_impact(confidence, sarcasm_type)

        # Recommended response strategy
        response = self._recommend_response(confidence, sarcasm_type)

        is_sarcastic = confidence >= 0.40

        result = {
            "is_sarcastic": is_sarcastic,
            "confidence": round(confidence, 3),
            "sarcasm_type": sarcasm_type if is_sarcastic else "none",
            "layers": {
                "lexical": lexical,
                "fasttext": fasttext,
                "sentiment_contradiction": sentiment,
                "punctuation": punctuation,
                "emoji": emoji,
            },
            "vad_impact": vad_impact,
            "recommended_response": response,
            "layers_active": len(scores),
        }

        if is_sarcastic:
            self._sarcasm_count += 1
            self._history.append({
                "ts": time.time(),
                "message": message[:200],
                "confidence": confidence,
                "type": sarcasm_type,
            })
            if len(self._history) > 50:
                self._history = self._history[-50:]

        return result

    # ═══════════════════════════════════════════════════════════════════════
    # Capa 1: Patrones léxicos
    # ═══════════════════════════════════════════════════════════════════════

    def _analyze_lexical(self, message: str) -> Dict:
        """Busca patrones léxicos de sarcasmo en español."""
        msg_lower = message.lower()

        best_match = None
        best_score = 0

        for pattern, base_score in SARCASTIC_PATTERNS:
            match = pattern.search(msg_lower)
            if match:
                # Ajustar score por contexto
                score = base_score

                # Si hay negación cerca, refuerza sarcasmo
                negation_window = 5  # palabras
                words = msg_lower.split()
                match_pos = -1
                for i, w in enumerate(words):
                    if match.group(0) in w or w in match.group(0):
                        match_pos = i
                        break
                if match_pos >= 0:
                    window = words[max(0, match_pos-negation_window):
                                  min(len(words), match_pos+negation_window)]
                    if any(neg in " ".join(window) for neg in ["no", "ni", "nunca", "jamás"]):
                        score += 0.08

                # Si hay puntos suspensivos cerca, refuerza
                if "..." in message[max(0, match.start()-10):match.end()+10]:
                    score += 0.05

                if score > best_score:
                    best_score = score
                    best_match = match.group(0)

        return {
            "match": best_match is not None,
            "score": best_score,
            "pattern": best_match or "",
        }

    # ═══════════════════════════════════════════════════════════════════════
    # Capa 2: FastText
    # ═══════════════════════════════════════════════════════════════════════

    def _analyze_fasttext(self, message: str) -> Dict:
        """Compara el mensaje contra prototipos de sarcasmo/ironía/sinceridad."""
        try:
            from core.eidos_fasttext import get_fasttext_engine
            ft = get_fasttext_engine()
            if not ft.is_ready():
                return self._fasttext_fallback()

            # Obtener o cachear vectores prototipo
            proto_vectors = self._get_prototype_vectors(ft)

            # Vector del mensaje
            msg_vec = ft.get_vector(message)
            if msg_vec is None:
                return self._fasttext_fallback()

            # Similitud con cada categoría
            import numpy as np

            def cosine(v1, v2):
                dot = np.dot(v1, v2)
                norm = np.linalg.norm(v1) * np.linalg.norm(v2)
                return dot / max(0.001, norm)

            sim_sarcastic = max(
                cosine(msg_vec, pv) for pv in proto_vectors.get("sarcastic", [])
            ) if proto_vectors.get("sarcastic") else 0.3

            sim_ironic = max(
                cosine(msg_vec, pv) for pv in proto_vectors.get("ironic", [])
            ) if proto_vectors.get("ironic") else 0.2

            sim_dry = max(
                cosine(msg_vec, pv) for pv in proto_vectors.get("dry_humor", [])
            ) if proto_vectors.get("dry_humor") else 0.2

            sim_sincere = max(
                cosine(msg_vec, pv) for pv in proto_vectors.get("sincere", [])
            ) if proto_vectors.get("sincere") else 0.5

            # Score de sarcasmo: diferencia entre sarcasmo y sinceridad
            max_sarc = max(sim_sarcastic, sim_ironic, sim_dry)
            sarcasm_score = max(0, (max_sarc - sim_sincere + 0.3) / 1.3)

            return {
                "similarity_sarcasm": round(max_sarc, 3),
                "similarity_sincere": round(sim_sincere, 3),
                "similarity_ironic": round(sim_ironic, 3),
                "similarity_dry_humor": round(sim_dry, 3),
                "sarcasm_score": round(sarcasm_score, 3),
                "dominant": ("sarcastic" if sim_sarcastic > max(sim_ironic, sim_dry, sim_sincere)
                           else "ironic" if sim_ironic > max(sim_sarcastic, sim_dry, sim_sincere)
                           else "dry_humor" if sim_dry > max(sim_sarcastic, sim_ironic, sim_sincere)
                           else "sincere"),
            }
        except Exception as e:
            log.debug("_analyze_fasttext: %s", e)
            return self._fasttext_fallback()

    def _get_prototype_vectors(self, ft) -> Dict[str, List]:
        """Cachea vectores prototipo de cada categoría."""
        now = time.time()
        if self._fasttext_vectors and (now - self._cache_ts) < 600:
            return self._fasttext_vectors

        vectors = {"sarcastic": [], "ironic": [], "dry_humor": [], "sincere": []}
        import numpy as np

        for text in SARCASTIC_PROTOTYPES:
            vec = ft.get_vector(text)
            if vec is not None:
                vectors["sarcastic"].append(np.array(vec))

        for text in IRONIC_PROTOTYPES:
            vec = ft.get_vector(text)
            if vec is not None:
                vectors["ironic"].append(np.array(vec))

        for text in DRY_HUMOR_PROTOTYPES:
            vec = ft.get_vector(text)
            if vec is not None:
                vectors["dry_humor"].append(np.array(vec))

        for text in SINCERE_PROTOTYPES:
            vec = ft.get_vector(text)
            if vec is not None:
                vectors["sincere"].append(np.array(vec))

        self._fasttext_vectors = vectors
        self._cache_ts = now
        return vectors

    @staticmethod
    def _fasttext_fallback() -> Dict:
        return {
            "similarity_sarcasm": None,
            "similarity_sincere": None,
            "similarity_ironic": None,
            "similarity_dry_humor": None,
            "sarcasm_score": None,
            "dominant": "unknown",
            "note": "FastText not available",
        }

    # ═══════════════════════════════════════════════════════════════════════
    # Capa 3: Contradicción sentimental
    # ═══════════════════════════════════════════════════════════════════════

    def _analyze_sentiment_contradiction(self, message: str) -> Dict:
        """Detecta contradicción entre palabras positivas y tono negativo general.

        Ejemplo: "genial, otro error más" → positivo + negativo = posible sarcasmo.
        """
        msg_lower = message.lower()
        words = set(msg_lower.split())

        positive_count = len(words & POSITIVE_MARKERS)
        negative_phrases = sum(
            1 for neg in NEGATIVE_MARKERS if neg in msg_lower
        )
        negative_words = sum(
            1 for w in words
            if w in {"no", "ni", "nunca", "jamás", "error", "fallo",
                     "mal", "peor", "terrible", "horror", "desastre"}
        )

        has_contradiction = positive_count > 0 and (
            negative_phrases > 0 or negative_words > 0
        )

        score = 0.0
        if has_contradiction:
            # Cuanto más extremos ambos, más probable sarcasmo
            score = min(0.7, 0.3 + (positive_count * 0.1) + (negative_phrases * 0.08)
                       + (negative_words * 0.05))

        return {
            "detected": has_contradiction,
            "score": score,
            "positive_words": positive_count,
            "negative_indicators": negative_phrases + negative_words,
        }

    # ═══════════════════════════════════════════════════════════════════════
    # Capa 4: Puntuación sospechosa
    # ═══════════════════════════════════════════════════════════════════════

    @staticmethod
    def _analyze_punctuation(message: str) -> Dict:
        """Detecta patrones de puntuación asociados con sarcasmo.

        Señales: exceso de puntos suspensivos, signos mezclados (! y ?),
        capitalización errática.
        """
        suspicious = False
        score = 0.0

        # Puntos suspensivos excesivos
        ellipsis_count = message.count("...") + message.count("…")
        if ellipsis_count >= 2:
            suspicious = True
            score = min(0.3, ellipsis_count * 0.12)

        # Signos mezclados ¡! ¿?
        intermixed = bool(re.search(r'[¡!]\s*[¿?]|[¿?]\s*[¡!]', message))
        if intermixed:
            suspicious = True
            score = max(score, 0.2)

        # MAYÚSCULAS erráticas (alternancia rápido minúscula/mayúscula)
        caps_ratio = sum(1 for c in message if c.isupper()) / max(1, len(message))
        if 0.15 < caps_ratio < 0.5:  # no todo mayúsculas, pero bastantes
            suspicious = True
            score = max(score, 0.15)

        # Signos repetidos (!!!, ???, !!??)
        repeated = bool(re.search(r'[!?]{2,}', message))
        if repeated:
            suspicious = True
            score = max(score, 0.25)

        return {
            "suspicious": suspicious,
            "score": score,
            "ellipsis_count": ellipsis_count,
            "intermixed": intermixed,
            "repeated_marks": repeated,
        }

    # ═══════════════════════════════════════════════════════════════════════
    # Capa 5: Emojis
    # ═══════════════════════════════════════════════════════════════════════

    @staticmethod
    def _analyze_emojis(message: str) -> Dict:
        """Detecta emojis con carga sarcástica."""
        found_emoji = None
        best_score = 0.0

        for emoji, score in SARCASTIC_EMOJIS.items():
            if emoji in message:
                if score > best_score:
                    best_score = score
                    found_emoji = emoji

        return {
            "found": found_emoji is not None,
            "emoji": found_emoji or "",
            "score": best_score,
        }

    # ═══════════════════════════════════════════════════════════════════════
    # Fusión y clasificación
    # ═══════════════════════════════════════════════════════════════════════

    def _classify_type(self, lexical: Dict, fasttext: Dict,
                      sentiment: Dict, emoji: Dict) -> str:
        """Determina el tipo específico de comunicación no literal."""
        dominant = fasttext.get("dominant", "")
        sim_ironic = fasttext.get("similarity_ironic") or 0

        if dominant == "sarcastic" or lexical.get("score", 0) > 0.5:
            return "sarcasmo"
        elif dominant == "ironic" or sim_ironic > 0.4:
            return "ironía"
        elif dominant == "dry_humor":
            return "humor_seco"
        elif sentiment.get("detected") and sentiment.get("score", 0) > 0.4:
            return "ironía"  # contradicción → ironía
        elif emoji.get("found") and emoji.get("score", 0) > 0.4:
            return "sarcasmo"

        return "sarcasmo"  # default si se detectó algo

    @staticmethod
    def _calculate_vad_impact(confidence: float, sarcasm_type: str) -> Dict[str, float]:
        """Calcula el impacto VAD de detectar sarcasmo.

        El sarcasmo produce una respuesta emocional compleja:
        - Valencia: mixta (no baja del todo, es ambiguo)
        - Arousal: sube (alerta, hay que interpretar)
        - Dominancia: ligera bajada (el sarcasmo es desestabilizante)
        """
        # Escalar por confianza
        if sarcasm_type == "sarcasmo":
            return {
                "v": round(-0.03 * confidence, 3),   # ligera bajada
                "a": round(+0.08 * confidence, 3),   # sube atención
                "d": round(-0.05 * confidence, 3),   # baja control
            }
        elif sarcasm_type == "ironía":
            return {
                "v": round(-0.01 * confidence, 3),   # casi neutro
                "a": round(+0.05 * confidence, 3),
                "d": round(-0.02 * confidence, 3),
            }
        elif sarcasm_type == "humor_seco":
            return {
                "v": round(+0.04 * confidence, 3),   # el humor da placer
                "a": round(+0.03 * confidence, 3),
                "d": round(+0.02 * confidence, 3),   # entenderlo da control
            }
        return {"v": 0.0, "a": 0.0, "d": 0.0}

    @staticmethod
    def _recommend_response(confidence: float, sarcasm_type: str) -> str:
        """Recomienda estrategia de respuesta."""
        if confidence < 0.40:
            return "literal"  # responder literalmente

        if sarcasm_type == "sarcasmo":
            if confidence > 0.7:
                return "acknowledge_playful"  # reconocer y seguir el juego
            return "acknowledge_subtle"  # reconocer sutilmente
        elif sarcasm_type == "ironía":
            return "mirror"  # reflejar la ironía con otra ironía suave
        elif sarcasm_type == "humor_seco":
            return "play_along"  # seguir el juego

        return "literal"

    @staticmethod
    def _no_sarcasm_result(lexical, fasttext, sentiment, punctuation, emoji) -> Dict:
        return {
            "is_sarcastic": False,
            "confidence": 0.0,
            "sarcasm_type": "none",
            "layers": {
                "lexical": lexical,
                "fasttext": fasttext,
                "sentiment_contradiction": sentiment,
                "punctuation": punctuation,
                "emoji": emoji,
            },
            "vad_impact": {"v": 0.0, "a": 0.0, "d": 0.0},
            "recommended_response": "literal",
            "layers_active": 0,
        }

    # ═══════════════════════════════════════════════════════════════════════
    # Integración con SerModel
    # ═══════════════════════════════════════════════════════════════════════

    def analyze_ser_message(self, message: str,
                          session_id: str = "ser") -> Dict[str, Any]:
        """Analiza un mensaje de SER y actualiza el SerModel.

        Esta es la función principal de integración: el bridge o el
        daemon la llama cada vez que SER envía un mensaje.
        """
        result = self.analyze(message)

        # Actualizar SerModel si está disponible
        try:
            from core.eidos_ser_model import get_ser_model
            ser = get_ser_model()

            if result["is_sarcastic"]:
                ser.record_interaction(
                    message=message[:200],
                    interaction_type="sarcastic",
                    metadata={
                        "sarcasm_confidence": result["confidence"],
                        "sarcasm_type": result["sarcasm_type"],
                        "vad_impact": result["vad_impact"],
                    }
                )
        except Exception:
            pass

        # Aplicar VAD impact si hay afecto disponible
        if result["is_sarcastic"] and result["confidence"] > 0.5:
            try:
                from core.eidos_affect import get_affect
                affect = get_affect()
                impact = result["vad_impact"]
                affect.tick(
                    valence_delta=impact["v"],
                    arousal_delta=impact["a"],
                    dominance_delta=impact["d"],
                )
            except Exception:
                pass

        return result

    # ═══════════════════════════════════════════════════════════════════════
    # Stats
    # ═══════════════════════════════════════════════════════════════════════

    def stats(self) -> Dict[str, Any]:
        return {
            "total_detections": self._detection_count,
            "sarcasm_detected": self._sarcasm_count,
            "sarcasm_rate": round(
                self._sarcasm_count / max(1, self._detection_count), 3
            ),
            "recent": [
                {"type": h["type"], "confidence": h["confidence"],
                 "msg_preview": h["message"][:80]}
                for h in self._history[-5:]
            ],
        }


# ═══════════════════════════════════════════════════════════════════════════
# Singleton
# ═══════════════════════════════════════════════════════════════════════════

_detector: Optional[SarcasmDetector] = None


def get_sarcasm_detector() -> SarcasmDetector:
    global _detector
    if _detector is None:
        _detector = SarcasmDetector()
    return _detector


# ═══════════════════════════════════════════════════════════════════════════
# CLI
# ═══════════════════════════════════════════════════════════════════════════

if __name__ == "__main__":
    import argparse
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")

    p = argparse.ArgumentParser(description="EIDOS Sarcasm Detector")
    p.add_argument("message", nargs="?", type=str,
                   help="Mensaje a analizar")
    p.add_argument("--test", action="store_true",
                   help="Ejecutar batería de tests")
    p.add_argument("--stats", action="store_true")
    args = p.parse_args()

    detector = get_sarcasm_detector()

    if args.test:
        test_messages = [
            ("Claro, como no se me había ocurrido...", "sarcasmo"),
            ("Genial, otro error para la colección 🙃", "sarcasmo"),
            ("Qué sorpresa, no funciona como siempre", "sarcasmo"),
            ("Por supuesto, es exactamente lo que necesitaba", "sarcasmo"),
            ("Gracias, necesito ayuda con el servidor", "sincero"),
            ("Explícame cómo funciona esto por favor", "sincero"),
            ("Vaya, qué casualidad tan conveniente", "ironía"),
            ("El código funciona, no preguntes cómo 😏", "humor_seco"),
            ("Qué opinas de implementar esto en Rust?", "sincero"),
            ("Fantástico, otro problema más que celebrar...", "sarcasmo"),
        ]
        print("═══ BATERÍA DE TESTS DE SARCASMO ═══\n")
        correct = 0
        for msg, expected in test_messages:
            result = detector.analyze(msg)
            detected = result["sarcasm_type"]
            is_sarc = result["is_sarcastic"]
            conf = result["confidence"]
            layers = result["layers_active"]
            status = "✅" if (expected != "sincero" and is_sarc) or (expected == "sincero" and not is_sarc) else "❌"
            if status == "✅":
                correct += 1
            print(f"  {status} \"{msg[:55]}...\"")
            print(f"     tipo={detected} conf={conf:.2f} capas={layers}")
            if result["is_sarcastic"]:
                print(f"     VAD impact: {result['vad_impact']}")
                print(f"     respuesta: {result['recommended_response']}")
            print()
        print(f"Precisión: {correct}/{len(test_messages)}")
    elif args.stats:
        print(json.dumps(detector.stats(), indent=2, ensure_ascii=False))
    elif args.message:
        result = detector.analyze(args.message)
        print(json.dumps(result, indent=2, ensure_ascii=False))
    else:
        p.print_help()
