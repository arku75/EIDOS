"""
core/eidos_ser_model.py — Modelo interno que EIDOS tiene de SER [S96]

"El cierre del circuito social" — DeepSeek

Permite a EIDOS mantener un modelo del dueño: qué temas le interesan,
cuál es su ritmo de interacción, qué tono emocional tiene, y qué tipos
de pregunta nunca ha hecho. Esto alimenta los triggers ser_context y
pattern_absence del daemon.

Sin LLM: usa FastText para clasificar preguntas por tipo (coseno contra
vectores prototipo) y heurísticas simples para tono emocional.

Uso:
    ser = get_ser_model()
    ser.observe(message, ts)           # al recibir mensaje de SER
    hours = ser.hours_since_topic('emotional_check')
    never = ser.get_never_asked_types()
    expected = ser.expected_next_interaction()
"""

from __future__ import annotations

import json
import logging
import time
from collections import defaultdict
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

log = logging.getLogger("eidos.ser_model")

SER_MODEL_FILE = Path.home() / ".eidos" / "ser_model.json"

# Tipos de pregunta que EIDOS puede clasificar
QUESTION_PROTOTYPES = {
    'emotional_check': [
        "cómo estás", "cómo te sientes", "qué tal estás",
        "cómo va todo", "cómo te encuentras",
    ],
    'opinion_request': [
        "qué opinas", "qué piensas", "qué crees",
        "cuál es tu opinión", "te gusta",
    ],
    'task_request': [
        "haz", "ejecuta", "corre", "busca", "encuentra",
        "analiza", "revisa", "mira", "comprueba",
    ],
    'explanation_request': [
        "explica", "por qué", "cómo funciona", "qué es",
        "dime", "descríbeme",
    ],
    'personal_question': [
        "recuerdas", "has pensado", "te has dado cuenta",
        "has soñado", "has aprendido",
    ],
    'greeting': [
        "hola", "buenos días", "buenas tardes", "hey",
        "buenas", "eidos",
    ],
}

# Palabras clave para inferencia de tono emocional
POSITIVE_WORDS = {
    'gracias', 'bien', 'genial', 'excelente', 'bueno', 'buena',
    'perfecto', 'me gusta', 'feliz', 'contento', 'alegre', 'bravo',
    ':)', ':-)', '😊', '👍', 'jaja', 'jeje',
}
NEGATIVE_WORDS = {
    'mal', 'triste', 'preocupado', 'cansado', 'frustrado',
    'error', 'fallo', 'roto', 'no funciona', 'problema',
    ':(', ':-(', '😞', '👎', 'qué mal',
}


class SerModel:
    """Modelo interno que EIDOS mantiene de su dueño (SER)."""

    def __init__(self):
        self.last_words: str = ""
        self.last_mood_inferred: str = "neutral"  # positivo/negativo/neutral
        self.last_interaction_ts: float = 0
        self.topics_of_interest: List[str] = []  # últimos N temas
        self.interaction_rhythm: List[float] = []  # timestamps de interacciones
        self.total_interactions: int = 0
        self.question_type_counts: Dict[str, int] = defaultdict(int)
        self._fasttext_model = None
        self._prototype_vectors: Dict[str, List[Any]] = {}
        self._load()

    # ── Observación ───────────────────────────────────────────────────────

    def observe(self, message: str, ts: float = None):
        """Actualiza el modelo al recibir un mensaje de SER.

        Args:
            message: texto del mensaje de SER
            ts: timestamp (default: ahora)
        """
        if ts is None:
            ts = time.time()

        self.last_words = message[:500]
        self.last_interaction_ts = ts
        self.total_interactions += 1

        # Actualizar ritmo
        self.interaction_rhythm.append(ts)
        if len(self.interaction_rhythm) > 100:
            self.interaction_rhythm = self.interaction_rhythm[-100:]

        # Inferir tono
        self.last_mood_inferred = self._infer_mood(message)

        # Clasificar tipo de pregunta
        qtype = self._classify_question(message)
        if qtype:
            self.question_type_counts[qtype] += 1

        # Extraer temas
        self._extract_topics(message)

        self._save()

    # ── Inferencia ────────────────────────────────────────────────────────

    def _infer_mood(self, message: str) -> str:
        """Infiere tono emocional de SER desde el mensaje (positivo/negativo/neutral)."""
        msg_lower = message.lower()
        pos_count = sum(1 for w in POSITIVE_WORDS if w in msg_lower)
        neg_count = sum(1 for w in NEGATIVE_WORDS if w in msg_lower)

        if pos_count > neg_count:
            return 'positivo'
        elif neg_count > pos_count:
            return 'negativo'
        return 'neutral'

    def _classify_question(self, message: str) -> Optional[str]:
        """Clasifica el tipo de pregunta de SER usando FastText + coseno.

        Returns:
            Tipo de pregunta o None si no se puede clasificar.
        """
        msg_lower = message.lower().strip()

        # Si es muy corto, intentar clasificación rápida
        if len(msg_lower) < 5:
            if any(g in msg_lower for g in ['hola', 'hey', 'buenas']):
                return 'greeting'
            return None

        # FastText: comparar contra vectores prototipo
        try:
            vec_msg = self._get_vector(msg_lower)
            if vec_msg is None:
                return self._fallback_classify(msg_lower)

            best_type = None
            best_sim = 0.3  # umbral mínimo

            for qtype, proto_vecs in self._get_prototype_vectors().items():
                for pvec in proto_vecs:
                    sim = self._cosine_similarity(vec_msg, pvec)
                    if sim > best_sim:
                        best_sim = sim
                        best_type = qtype

            return best_type

        except Exception:
            return self._fallback_classify(msg_lower)

    def _fallback_classify(self, message: str) -> Optional[str]:
        """Clasificación por palabras clave (respaldo si FastText no disponible)."""
        msg_lower = message.lower()
        scores = {}
        for qtype, prototypes in QUESTION_PROTOTYPES.items():
            score = sum(1 for p in prototypes if p in msg_lower)
            if score > 0:
                scores[qtype] = score

        if scores:
            return max(scores, key=scores.get)
        return None

    def _get_vector(self, text: str):
        """Obtiene embedding FastText de un texto."""
        try:
            if self._fasttext_model is None:
                from core.eidos_fasttext import get_fasttext_model
                self._fasttext_model = get_fasttext_model()
            if self._fasttext_model:
                return self._fasttext_model.get_sentence_vector(text)
        except Exception:
            pass
        return None

    def _get_prototype_vectors(self) -> Dict[str, List]:
        """Cachea y retorna vectores prototipo para cada tipo de pregunta."""
        if self._prototype_vectors:
            return self._prototype_vectors

        for qtype, phrases in QUESTION_PROTOTYPES.items():
            vecs = []
            for phrase in phrases:
                v = self._get_vector(phrase)
                if v is not None:
                    vecs.append(v)
            if vecs:
                self._prototype_vectors[qtype] = vecs

        return self._prototype_vectors

    @staticmethod
    def _cosine_similarity(a, b) -> float:
        """Similitud coseno entre dos vectores numpy."""
        try:
            import numpy as np
            dot = np.dot(a, b)
            norm_a = np.linalg.norm(a)
            norm_b = np.linalg.norm(b)
            if norm_a == 0 or norm_b == 0:
                return 0.0
            return float(dot / (norm_a * norm_b))
        except Exception:
            return 0.0

    def _extract_topics(self, message: str):
        """Extrae temas del mensaje (palabras clave sustantivas)."""
        # Simple: palabras de 5+ letras que no son stopwords
        stopwords = {'sobre', 'para', 'como', 'cuando', 'donde', 'porque',
                     'tambien', 'aunque', 'entonces', 'despues', 'antes',
                     'esto', 'nada', 'todo', 'algo', 'mucho', 'poco'}
        words = message.lower().replace('?', ' ').replace('¿', ' ').split()
        topics = [
            w for w in words
            if len(w) >= 5 and w not in stopwords and w.isalpha()
        ]
        for t in topics[:3]:
            self.topics_of_interest.append(t)
        if len(self.topics_of_interest) > 50:
            self.topics_of_interest = self.topics_of_interest[-50:]

    # ── Consultas ─────────────────────────────────────────────────────────

    def hours_since_topic(self, qtype: str) -> Optional[float]:
        """Horas desde la última pregunta de este tipo."""
        # Simplificado: retorna tiempo desde última interacción si el tipo
        # no está en los contadores recientes
        if self.question_type_counts.get(qtype, 0) == 0:
            if self.total_interactions > 0:
                return (time.time() - self.last_interaction_ts) / 3600
        return None  # hay registros de este tipo, no podemos calcular sin historial fino

    def get_never_asked_types(self) -> List[str]:
        """Retorna tipos de pregunta que SER nunca ha hecho."""
        if self.total_interactions < 10:
            return []
        all_types = set(QUESTION_PROTOTYPES.keys())
        asked = set(self.question_type_counts.keys())
        never = all_types - asked
        # Traducir a español para el pensamiento
        translations = {
            'emotional_check': 'cómo estoy',
            'opinion_request': 'mi opinión sobre algo',
            'personal_question': 'si recuerdo algo o he soñado',
            'explanation_request': 'que te explique algo',
        }
        return [translations.get(t, t) for t in never if t in translations]

    def expected_next_interaction(self) -> Optional[float]:
        """Predice cuándo SER interactuará próximamente (timestamp).

        Basado en media móvil del ritmo de interacción.
        """
        if len(self.interaction_rhythm) < 3:
            return None

        # Calcular intervalos entre interacciones
        sorted_rhythm = sorted(self.interaction_rhythm)
        intervals = []
        for i in range(1, len(sorted_rhythm)):
            interval = sorted_rhythm[i] - sorted_rhythm[i - 1]
            if 60 < interval < 86400 * 3:  # entre 1 min y 3 días
                intervals.append(interval)

        if not intervals:
            return None

        # Media móvil simple de los últimos 10 intervalos
        recent = intervals[-10:]
        avg_interval = sum(recent) / len(recent)

        # Esperar que la siguiente sea ~avg_interval después de la última
        return self.last_interaction_ts + avg_interval

    def interaction_summary(self) -> Dict[str, Any]:
        """Resumen del modelo de SER para uso en TUI o autoinforme."""
        return {
            'total_interactions': self.total_interactions,
            'last_mood_inferred': self.last_mood_inferred,
            'last_words': self.last_words[:100],
            'hours_since_last': (
                round((time.time() - self.last_interaction_ts) / 3600, 1)
                if self.last_interaction_ts else None
            ),
            'topics': self.topics_of_interest[-10:],
            'question_types': dict(self.question_type_counts),
            'never_asked': self.get_never_asked_types(),
        }

    # ── Persistencia ──────────────────────────────────────────────────────

    def _save(self):
        try:
            data = {
                'total_interactions': self.total_interactions,
                'last_interaction_ts': self.last_interaction_ts,
                'last_mood_inferred': self.last_mood_inferred,
                'last_words': self.last_words[:200],
                'topics_of_interest': self.topics_of_interest[-30:],
                'interaction_rhythm': self.interaction_rhythm[-50:],
                'question_type_counts': dict(self.question_type_counts),
            }
            SER_MODEL_FILE.parent.mkdir(parents=True, exist_ok=True)
            SER_MODEL_FILE.write_text(json.dumps(data, indent=2, ensure_ascii=False))
        except Exception:
            pass

    def _load(self):
        try:
            if SER_MODEL_FILE.exists():
                data = json.loads(SER_MODEL_FILE.read_text())
                self.total_interactions = data.get('total_interactions', 0)
                self.last_interaction_ts = data.get('last_interaction_ts', 0)
                self.last_mood_inferred = data.get('last_mood_inferred', 'neutral')
                self.last_words = data.get('last_words', '')
                self.topics_of_interest = data.get('topics_of_interest', [])
                self.interaction_rhythm = data.get('interaction_rhythm', [])
                self.question_type_counts = defaultdict(
                    int, data.get('question_type_counts', {}))
        except Exception:
            pass


# ── Singleton ─────────────────────────────────────────────────────────────────

_ser_model: Optional[SerModel] = None


def get_ser_model() -> SerModel:
    global _ser_model
    if _ser_model is None:
        _ser_model = SerModel()
    return _ser_model


# ── CLI ───────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    import argparse
    logging.basicConfig(level=logging.INFO)

    p = argparse.ArgumentParser(description="EIDOS SerModel")
    p.add_argument("--observe", type=str, help="Registrar mensaje de SER")
    p.add_argument("--summary", action="store_true", help="Mostrar resumen")
    args = p.parse_args()

    sm = get_ser_model()

    if args.observe:
        sm.observe(args.observe)
        print(f"Registrado: mood={sm.last_mood_inferred}")
    elif args.summary:
        print(json.dumps(sm.interaction_summary(), indent=2, ensure_ascii=False))
    else:
        p.print_help()
