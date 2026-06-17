"""
core/eidos_human_tone.py — Inferencia de tono emocional humano [S85 Fase 1.2]

Analiza el tono emocional de textos escritos por Luka usando un léxico
de sentimiento en español (built-in, SIN APIs externas). Detecta descensos
crónicos del ánimo para activar políticas de cuidado.

Método: lexicon-based sentiment analysis. Cada palabra tiene un peso
de valencia (-1 a +1). El tono se infiere del promedio ponderado.

Uso:
    from core.eidos_human_tone import HumanToneAnalyzer
    hta = HumanToneAnalyzer()
    tone = hta.analyze("Hoy ha sido un día difícil pero productivo")
    # → {"valence": -0.2, "dominant_emotion": "melancolía", "confidence": 0.6}
"""

from __future__ import annotations

import json
import logging
import re
import time
from collections import Counter, deque
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

log = logging.getLogger("eidos.human_tone")

TONE_STATE_FILE = Path.home() / ".eidos" / "human_tone_state.json"
TONE_HISTORY_MAX = 200

# ── Léxico de sentimiento en español ────────────────────────────────────────
# Palabras con carga emocional: positivas (+), negativas (-), mixtas (~)

SENTIMENT_LEXICON = {
    # Positivas (alegría, satisfacción, gratitud)
    "gracias": 0.8, "feliz": 0.9, "contento": 0.7, "alegre": 0.8,
    "bien": 0.5, "genial": 0.9, "excelente": 0.9, "fantástico": 0.9,
    "maravilloso": 0.95, "estupendo": 0.85, "increíble": 0.8,
    "mejor": 0.6, "bueno": 0.5, "bonito": 0.6, "hermoso": 0.8,
    "amor": 0.9, "cariño": 0.8, "abrazo": 0.7, "paz": 0.7,
    "tranquilo": 0.5, "relajado": 0.5, "disfrutar": 0.7, "disfruto": 0.7,
    "logrado": 0.7, "conseguido": 0.6, "éxito": 0.8, "avanzar": 0.6,
    "avanzando": 0.6, "progreso": 0.7, "completado": 0.6,
    "orgulloso": 0.8, "satisfecho": 0.7, "motivado": 0.8,
    "interesante": 0.6, "curioso": 0.4, "aprender": 0.5,
    "gracias": 0.8, "perfecto": 0.9, "útil": 0.6, "vale": 0.3,
    "gracias": 0.8, "👍": 0.6, "😊": 0.9, "🎉": 0.9, "❤": 0.95,

    # Negativas (tristeza, frustración, enfado, ansiedad)
    "triste": -0.8, "mal": -0.6, "fatal": -0.9, "horrible": -0.9,
    "terrible": -0.85, "pésimo": -0.9, "tóxico": -0.8,
    "cansado": -0.5, "agotado": -0.8, "exhausto": -0.8,
    "frustrado": -0.8, "frustrante": -0.7, "enfadado": -0.8,
    "enfado": -0.7, "rabia": -0.85, "ira": -0.9, "odio": -0.95,
    "dolor": -0.9, "duele": -0.8, "sufrir": -0.9, "sufriendo": -0.9,
    "ansiedad": -0.8, "ansioso": -0.7, "nervioso": -0.6,
    "preocupado": -0.6, "preocupante": -0.7, "miedo": -0.85,
    "asustado": -0.8, "aterrado": -0.9, "pánico": -0.95,
    "solo": -0.7, "soledad": -0.8, "aislado": -0.7,
    "abandonado": -0.9, "olvidado": -0.8, "ignorado": -0.7,
    "inútil": -0.8, "fracaso": -0.9, "fracasado": -0.9,
    "error": -0.5, "fallo": -0.6, "roto": -0.7, "perdido": -0.6,
    "confuso": -0.4, "confundido": -0.5, "caos": -0.7,
    "deprimido": -0.9, "deprimente": -0.8, "desesperado": -0.9,
    "😢": -0.8, "😭": -0.9, "😡": -0.8, "😞": -0.7, "💔": -0.9,

    # Moduladores (intensifican o atenúan)
    "muy": 1.5, "mucho": 1.4, "bastante": 1.3, "extremadamente": 1.8,
    "poco": 0.5, "algo": 0.7, "ligeramente": 0.6,
    "no": -1.0,  # inversor: "no feliz" → -feliz
    "nunca": -0.8, "jamás": -0.9, "sin": -0.7,
}

# Emociones por rango de valencia
EMOTION_MAP = {
    (0.7, 1.0): "alegría",
    (0.4, 0.7): "satisfacción",
    (0.1, 0.4): "neutral-positivo",
    (-0.1, 0.1): "neutral",
    (-0.4, -0.1): "melancolía",
    (-0.7, -0.4): "preocupación",
    (-1.0, -0.7): "angustia",
}


class HumanToneAnalyzer:
    """Analizador de tono emocional en texto humano (lexicon-based).

    Mantiene un historial para detectar tendencias crónicas.
    """

    def __init__(self):
        self._history: deque = deque(maxlen=TONE_HISTORY_MAX)
        self._state = self._load_state()
        self._baseline = self._state.get("baseline", 0.15)  # tono base de Luka
        self._chronic_low_counter = self._state.get("chronic_low_counter", 0)

    def analyze(self, text: str) -> Dict[str, Any]:
        """Analiza el tono emocional de un texto.

        Retorna:
            {
                "valence": float,          # -1 (muy negativo) a +1 (muy positivo)
                "dominant_emotion": str,   # "alegría", "preocupación", etc.
                "confidence": float,       # 0-1, basado en cobertura del léxico
                "word_count": int,
                "emotional_words": int,
                "is_concerning": bool,     # True si el tono es preocupante
            }
        """
        words = re.findall(r'\b[a-záéíóúñüA-ZÁÉÍÓÚÑÜ]{2,}\b|[😊😢😭😡😞💔❤👍🎉]', text.lower())
        if not words:
            return self._neutral_result()

        scores = []
        emotional_words = 0
        i = 0
        while i < len(words):
            word = words[i]
            if word in SENTIMENT_LEXICON:
                weight = SENTIMENT_LEXICON[word]
                # Verificar modulador previo
                if i > 0 and words[i - 1] in SENTIMENT_LEXICON:
                    modulator = SENTIMENT_LEXICON[words[i - 1]]
                    if modulator > 1.0 or modulator < 0:  # intensificador o inversor
                        weight *= modulator
                scores.append(weight)
                emotional_words += 1
            i += 1

        if not scores:
            return self._neutral_result()

        valence = sum(scores) / max(1, len(scores))
        valence = max(-1.0, min(1.0, valence))

        # Determinar emoción dominante
        dominant = "neutral"
        for (lo, hi), emotion in EMOTION_MAP.items():
            if lo <= valence < hi or (hi == 1.0 and valence == 1.0):
                dominant = emotion
                break

        # Confianza basada en cobertura del léxico
        coverage = emotional_words / max(1, len(words))
        confidence = min(0.95, 0.3 + coverage * 0.65)

        # ¿Preocupante? Valencia < -0.3 con confianza > 0.4
        is_concerning = valence < -0.3 and confidence > 0.4

        result = {
            "valence": round(valence, 3),
            "dominant_emotion": dominant,
            "confidence": round(confidence, 3),
            "word_count": len(words),
            "emotional_words": emotional_words,
            "is_concerning": is_concerning,
            "timestamp": time.time(),
        }

        # Registrar en historial
        self._history.append(result)
        self._update_state(result)

        return result

    def analyze_batch(self, texts: List[str]) -> Dict[str, Any]:
        """Analiza múltiples textos y retorna tono agregado."""
        results = [self.analyze(t) for t in texts if t]
        if not results:
            return self._neutral_result()

        avg_valence = sum(r["valence"] for r in results) / len(results)
        emotions = Counter(r["dominant_emotion"] for r in results)
        dominant = emotions.most_common(1)[0][0] if emotions else "neutral"

        return {
            "valence": round(avg_valence, 3),
            "dominant_emotion": dominant,
            "confidence": round(sum(r["confidence"] for r in results) / len(results), 3),
            "texts_analyzed": len(results),
            "emotion_distribution": dict(emotions.most_common()),
            "is_concerning": avg_valence < -0.3,
            "timestamp": time.time(),
        }

    def get_trend(self, window: int = 50) -> Dict[str, Any]:
        """Analiza la tendencia del tono en las últimas N interacciones.

        Retorna:
            {
                "trend": "improving" | "stable" | "declining" | "concerning",
                "current_avg": float,
                "baseline": float,
                "deviation_from_baseline": float,
                "chronic_low": bool,
            }
        """
        recent = list(self._history)[-window:]
        if len(recent) < 5:
            return {"trend": "stable", "current_avg": self._baseline,
                    "baseline": self._baseline, "deviation": 0, "chronic_low": False}

        # Dividir en mitades para ver tendencia
        mid = len(recent) // 2
        first_half = recent[:mid]
        second_half = recent[mid:]

        first_avg = sum(r["valence"] for r in first_half) / len(first_half)
        second_avg = sum(r["valence"] for r in second_half) / len(second_half)
        current_avg = sum(r["valence"] for r in recent) / len(recent)

        deviation = current_avg - self._baseline

        if second_avg > first_avg + 0.1:
            trend = "improving"
        elif second_avg < first_avg - 0.15:
            trend = "declining"
        elif current_avg < -0.3:
            trend = "concerning"
        else:
            trend = "stable"

        chronic_low = self._chronic_low_counter >= 5

        return {
            "trend": trend,
            "current_avg": round(current_avg, 3),
            "baseline": round(self._baseline, 3),
            "deviation": round(deviation, 3),
            "chronic_low": chronic_low,
            "samples": len(recent),
        }

    def need_care(self) -> bool:
        """Determina si Luka necesita cuidado emocional.

        Retorna True si:
        - Tendencia "declining" o "concerning"
        - Crónico bajo (contador >= 5)
        - Último análisis muestra valencia < -0.4
        """
        if not self._history:
            return False

        trend = self.get_trend()
        last = self._history[-1]

        return (
            trend["trend"] in ("declining", "concerning")
            or trend["chronic_low"]
            or last["valence"] < -0.4
        )

    def _update_state(self, result: Dict[str, Any]):
        """Actualiza estado interno y detecta señales crónicas."""
        if result["is_concerning"]:
            self._chronic_low_counter += 1
        else:
            self._chronic_low_counter = max(0, self._chronic_low_counter - 1)

        # Ajustar baseline lentamente (promedio móvil)
        if len(self._history) >= 10:
            recent_avg = sum(r["valence"] for r in list(self._history)[-30:]) / min(30, len(self._history))
            self._baseline = self._baseline * 0.95 + recent_avg * 0.05

        self._state.update({
            "baseline": round(self._baseline, 3),
            "chronic_low_counter": self._chronic_low_counter,
            "last_analysis": time.time(),
        })
        self._save_state()

    def _neutral_result(self) -> Dict[str, Any]:
        return {
            "valence": 0.0,
            "dominant_emotion": "neutral",
            "confidence": 0.1,
            "word_count": 0,
            "emotional_words": 0,
            "is_concerning": False,
            "timestamp": time.time(),
        }

    def _load_state(self) -> Dict[str, Any]:
        try:
            if TONE_STATE_FILE.exists():
                return json.loads(TONE_STATE_FILE.read_text())
        except Exception:
            pass
        return {}

    def _save_state(self):
        try:
            TONE_STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
            TONE_STATE_FILE.write_text(json.dumps(self._state, indent=2))
        except Exception:
            pass

    def stats(self) -> Dict[str, Any]:
        trend = self.get_trend()
        return {
            "history_size": len(self._history),
            "baseline": round(self._baseline, 3),
            "chronic_low_counter": self._chronic_low_counter,
            "trend": trend["trend"],
            "need_care": self.need_care(),
        }


# ── Singleton ─────────────────────────────────────────────────────────────────

_analyzer: Optional[HumanToneAnalyzer] = None


def get_human_tone_analyzer() -> HumanToneAnalyzer:
    global _analyzer
    if _analyzer is None:
        _analyzer = HumanToneAnalyzer()
    return _analyzer


# ── CLI ───────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    import argparse
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    p = argparse.ArgumentParser(description="EIDOS Human Tone Analyzer")
    p.add_argument("text", nargs="?", help="Texto a analizar")
    p.add_argument("--trend", action="store_true")
    p.add_argument("--care", action="store_true", help="¿Necesita cuidado?")
    p.add_argument("--stats", action="store_true")
    args = p.parse_args()

    hta = get_human_tone_analyzer()

    if args.stats:
        print(json.dumps(hta.stats(), indent=2, ensure_ascii=False))
    elif args.trend:
        print(json.dumps(hta.get_trend(), indent=2, ensure_ascii=False))
    elif args.care:
        print(f"need_care: {hta.need_care()}")
    elif args.text:
        result = hta.analyze(args.text)
        print(json.dumps(result, indent=2, ensure_ascii=False))
    else:
        # Demo interactivo
        test_texts = [
            "Hoy ha sido un día increíble, he logrado todo lo que me propuse. Estoy muy feliz.",
            "Me siento agotado y frustrado. Nada sale bien últimamente.",
            "Gracias por tu ayuda, eres genial. Me siento motivado para seguir adelante.",
        ]
        for t in test_texts:
            r = hta.analyze(t)
            print(f"\nTexto: {t[:70]}...")
            print(f"  Valencia: {r['valence']:.2f} | Emoción: {r['dominant_emotion']} | "
                  f"Confianza: {r['confidence']:.2f} | Preocupante: {r['is_concerning']}")

        print(f"\nNeed care: {hta.need_care()}")
