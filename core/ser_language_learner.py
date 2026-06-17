"""
core/ser_language_learner.py — EIDOS aprende el idioma propio de SER.

SER tiene una forma única de comunicarse:
- Escribe rápido, con errores tipográficos
- Mezcla castellano e inglés técnico
- Da instrucciones largas en una frase
- Usa "eidos" y "claude" directamente
- No usa puntuación formal
- Dice "pulido", "hermano", "procede", "esto"

Cuanto más habla SER con EIDOS, más EIDOS aprende:
1. Qué palabras usa SER → vocabulario propio
2. Cómo construye frases → patrones de comando
3. Qué temas le interesan → prioridades implícitas
4. Cuándo está satisfecho vs. cuándo no → feedback implícito

Esto permite que EIDOS entienda a SER mejor que nadie:
- Cuando SER dice "pulido" → quiere calidad máxima, sin atajos
- Cuando dice "procede" → ejecútalo ya sin más preguntas
- Cuando dice "esto" → señala lo último que describió
- Cuando dice "hermano" → modo familiar, responde igual

Uso:
    from core.ser_language_learner import get_ser_learner
    learner = get_ser_learner()
    learner.record_message("hola procede con todo pulido")
    insight = learner.get_communication_style()
    print(insight)
"""
from __future__ import annotations

import json
import logging
import re
import sqlite3
import time
from collections import Counter
from pathlib import Path
from typing import Optional
from core.db import get_conn

log = logging.getLogger("eidos.ser_learner")

BRAIN_DB = Path.home() / ".eidos" / "evolution_brain.db"
LANG_DB  = Path.home() / ".eidos" / "ser_language.db"


class SerLanguageLearner:
    """Aprende y modela el lenguaje de SER para entenderle mejor."""

    def __init__(self):
        LANG_DB.parent.mkdir(parents=True, exist_ok=True)
        self._init_db()

    # ── API pública ──────────────────────────────────────────────────────────

    def record_message(self, message: str, context: str = "") -> None:
        """Registra un mensaje de SER y aprende de él."""
        if not message or len(message) < 3:
            return
        words = self._tokenize(message)
        patterns = self._extract_patterns(message)
        sentiment = self._detect_sentiment(message)

        conn = get_conn(LANG_DB, timeout=5)
        conn.execute(
            "INSERT INTO messages (text,context,word_count,sentiment,timestamp) VALUES (?,?,?,?,?)",
            (message[:500], context[:200], len(words), sentiment, time.time())
        )
        for w in words:
            conn.execute(
                "INSERT INTO word_freq (word,count,last_seen) VALUES (?,1,?) "
                "ON CONFLICT(word) DO UPDATE SET count=count+1, last_seen=excluded.last_seen",
                (w, time.time())
            )
        for p in patterns:
            conn.execute(
                "INSERT INTO patterns (pattern,count,example,last_seen) VALUES (?,1,?,?) "
                "ON CONFLICT(pattern) DO UPDATE SET count=count+1, last_seen=excluded.last_seen",
                (p, message[:100], time.time())
            )
        conn.commit()
        pass  # S109: get_conn no necesita close()
    def get_communication_style(self) -> str:
        """Devuelve un resumen del estilo comunicativo de SER para usar en prompts."""
        conn = get_conn(LANG_DB, timeout=5)
        total  = conn.execute("SELECT COUNT(*) FROM messages").fetchone()[0]
        if total < 3:
            pass  # S109: get_conn no necesita close()
            return ""

        top_words = conn.execute(
            "SELECT word, count FROM word_freq WHERE length(word)>3 "
            "ORDER BY count DESC LIMIT 20"
        ).fetchall()
        top_patterns = conn.execute(
            "SELECT pattern, count FROM patterns ORDER BY count DESC LIMIT 10"
        ).fetchall()
        sentiment_dist = conn.execute(
            "SELECT sentiment, COUNT(*) FROM messages GROUP BY sentiment"
        ).fetchall()
        pass  # S109: get_conn no necesita close()
        lines = [f"[Estilo comunicativo de SER — {total} mensajes analizados]"]
        if top_words:
            words_str = ", ".join(f"{w}({n})" for w,n in top_words[:10])
            lines.append(f"Palabras frecuentes: {words_str}")
        if top_patterns:
            pat_str = ", ".join(f"'{p}'({n})" for p,n in top_patterns[:5])
            lines.append(f"Patrones de comando: {pat_str}")

        # Señales clave inferidas
        signals = self._get_key_signals(top_words, top_patterns)
        if signals:
            lines.append(f"Señales clave: {signals}")

        return "\n".join(lines)

    def get_key_intent(self, message: str) -> Optional[str]:
        """Detecta la intención principal del mensaje basándose en patrones aprendidos."""
        msg_lower = message.lower()
        # Patrones de intención
        if any(w in msg_lower for w in ["procede", "hazlo", "implementa", "crea"]):
            return "execute"  # ejecutar inmediatamente
        if any(w in msg_lower for w in ["pulido", "perfecto", "completo", "sin dejar"]):
            return "max_quality"  # máxima calidad, sin atajos
        if any(w in msg_lower for w in ["para", "paras", "stop", "espera"]):
            return "stop"  # detenerse
        if any(w in msg_lower for w in ["dimelo", "dime", "explica", "qué"]):
            return "explain"  # explicar primero
        if any(w in msg_lower for w in ["sin mi", "solo", "autónomo", "sin parar"]):
            return "autonomous"  # modo autónomo sin intervención
        return None

    def get_vocabulary_expansion(self) -> dict:
        """Devuelve palabras que SER usa y cómo interpretarlas."""
        conn = get_conn(LANG_DB, timeout=5)
        words = dict(conn.execute(
            "SELECT word, count FROM word_freq ORDER BY count DESC LIMIT 100"
        ).fetchall())
        pass  # S109: get_conn no necesita close()
        # Mapa semántico de las palabras más usadas por SER
        semantic_map = {
            "pulido":   "calidad máxima, sin errores, completamente terminado",
            "procede":  "ejecuta ya, sin más preguntas ni confirmaciones",
            "hermano":  "modo familiar, EIDOS responde como hermano/colega",
            "esto":     "referencia a lo último mencionado en la conversación",
            "eidos":    "me dirijo a EIDOS directamente como entidad",
            "todo":     "incluye absolutamente todos los puntos, sin excepción",
            "sin dejar": "no omitir ningún detalle",
            "para":     "detener lo que estás haciendo",
        }
        # Añadir palabras frecuentes no conocidas
        for w, n in words.items():
            if w not in semantic_map and n > 5:
                semantic_map[w] = f"[frecuente, {n} veces — aprende su significado]"
        return semantic_map

    def evolve_from_feedback(self, original_msg: str, response: str,
                              feedback: str) -> None:
        """Aprende cuando SER corrige o aprueba una respuesta."""
        positive = any(w in feedback.lower() for w in
                       ["bien", "perfecto", "sí", "correcto", "exacto", "eso"])
        negative = any(w in feedback.lower() for w in
                       ["no", "mal", "incorrecto", "distinto", "otra vez"])
        conn = get_conn(LANG_DB, timeout=5)
        conn.execute(
            "INSERT INTO feedback (original,response,feedback_text,positive,timestamp) VALUES (?,?,?,?,?)",
            (original_msg[:300], response[:300], feedback[:300], int(positive), time.time())
        )
        conn.commit()
        pass  # S109: get_conn no necesita close()
        # Guardar en brain como insight de aprendizaje
        if positive:
            self._save_learning_to_brain(
                f"Respuesta aprobada por SER: {original_msg[:50]}",
                f"Contexto: '{original_msg[:100]}' → Respuesta buena: '{response[:150]}'"
            )
        elif negative:
            self._save_learning_to_brain(
                f"Respuesta mejorable: {original_msg[:50]}",
                f"Contexto: '{original_msg[:100]}' → Feedback: '{feedback[:100]}'"
            )

    # ── Privados ─────────────────────────────────────────────────────────────

    @staticmethod
    def _tokenize(text: str) -> list[str]:
        words = re.findall(r'\b[a-záéíóúüñA-ZÁÉÍÓÚÜÑ]{3,}\b', text.lower())
        stopwords = {"que", "con", "los", "las", "una", "para", "por", "del", "etc"}
        return [w for w in words if w not in stopwords]

    @staticmethod
    def _extract_patterns(text: str) -> list[str]:
        patterns = []
        t = text.lower()
        # Detectar patrones de comando
        if re.search(r'^(procede|hazlo|implementa|crea)\b', t):
            patterns.append("order_direct")
        if "sin dejar nada" in t or "sin nada pendiente" in t:
            patterns.append("completeness_demand")
        if "pulido" in t:
            patterns.append("quality_demand")
        if re.search(r'\bpor favor\b|\bgracias\b|\bporfavor\b', t):
            patterns.append("polite_request")
        if len(text) > 200:
            patterns.append("long_instruction")
        if re.search(r'\d+\s*(cosas|puntos|pasos|fases)', t):
            patterns.append("numbered_items")
        return patterns

    @staticmethod
    def _detect_sentiment(text: str) -> str:
        t = text.lower()
        if any(w in t for w in ["perfecto", "genial", "bien", "me gusta", "buen"]):
            return "positive"
        if any(w in t for w in ["mal", "error", "falla", "roto", "no funciona"]):
            return "problem"
        if any(w in t for w in ["urgente", "crítico", "inmediato"]):
            return "urgent"
        return "neutral"

    @staticmethod
    def _get_key_signals(top_words: list, top_patterns: list) -> str:
        signals = []
        word_map = dict(top_words)
        pat_map  = dict(top_patterns)
        if word_map.get("pulido", 0) > 2:
            signals.append("SER exige calidad máxima")
        if word_map.get("procede", 0) > 2:
            signals.append("SER prefiere acción directa")
        if pat_map.get("completeness_demand", 0) > 2:
            signals.append("SER quiere TODO completado, sin excepción")
        if pat_map.get("long_instruction", 0) > 3:
            signals.append("SER da instrucciones largas que deben leerse completas")
        return "; ".join(signals) if signals else ""

    @staticmethod
    def _save_learning_to_brain(concept: str, definition: str) -> None:
        try:
            conn = get_conn(BRAIN_DB, timeout=5)
            ex = conn.execute("SELECT 1 FROM knowledge_nodes WHERE concept=?",
                              (concept,)).fetchone()
            if not ex:
                conn.execute(
                    "INSERT INTO knowledge_nodes (concept,definition,category,confidence,source,created_at) VALUES (?,?,?,?,?,?)",
                    (concept, definition, "ser_learning", 0.95, "language_learner", time.time())
                )
                conn.commit()
            pass  # S109: get_conn no necesita close()
        except Exception:
            pass

    def _init_db(self) -> None:
        conn = get_conn(LANG_DB, timeout=5)
        conn.executescript("""
            CREATE TABLE IF NOT EXISTS messages (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                text TEXT, context TEXT, word_count INTEGER,
                sentiment TEXT, timestamp REAL
            );
            CREATE TABLE IF NOT EXISTS word_freq (
                word TEXT PRIMARY KEY, count INTEGER DEFAULT 1,
                last_seen REAL
            );
            CREATE TABLE IF NOT EXISTS patterns (
                pattern TEXT PRIMARY KEY, count INTEGER DEFAULT 1,
                example TEXT, last_seen REAL
            );
            CREATE TABLE IF NOT EXISTS feedback (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                original TEXT, response TEXT, feedback_text TEXT,
                positive INTEGER, timestamp REAL
            );
        """)
        conn.commit()
        pass  # S109: get_conn no necesita close()
_learner: Optional[SerLanguageLearner] = None


def get_ser_learner() -> SerLanguageLearner:
    global _learner
    if _learner is None:
        _learner = SerLanguageLearner()
    return _learner
