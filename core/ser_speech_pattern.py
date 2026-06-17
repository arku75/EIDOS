"""
core/ser_speech_pattern.py — Aprendizaje del estilo de SER

Cada vez que SER habla, guardamos su mensaje y analizamos:
- Longitud típica de sus frases
- Vocabulario característico (palabras que usa con frecuencia)
- Expresiones únicas (modismos, muletillas)
- Tono (formal/informal, cariñoso/directo)
- Idioma principal

Después, esos patterns se inyectan al system_prompt de Colony para que
los personajes hablen en el estilo de SER, no en estilo de un asistente genérico.
"""
from __future__ import annotations

import sqlite3
import re
import time
import threading
from pathlib import Path
from typing import Dict, List, Optional, Any
from collections import Counter
import logging
from core.db import get_conn

log = logging.getLogger("eidos.ser_speech")

DB_PATH = Path.home() / ".eidos" / "ser_speech.db"

# Stop words típicas en español/inglés que NO son personalidad
_STOP = {
    "el","la","los","las","un","una","de","del","al","y","o","que","con","para",
    "por","en","a","como","si","se","me","te","lo","le","es","son","ser","estar",
    "the","a","an","of","to","in","is","are","and","or","for","with","on","at",
    "this","that","these","those","i","you","he","she","it","we","they","be"
}


class SerSpeechPattern:
    """Singleton que guarda y analiza los mensajes de SER."""

    _instance = None
    _lock = threading.Lock()

    def __new__(cls):
        if cls._instance is None:
            with cls._lock:
                if cls._instance is None:
                    cls._instance = super().__new__(cls)
        return cls._instance

    def __init__(self):
        if hasattr(self, "_initialized"):
            return
        self._initialized = True
        self._cache_pattern: Optional[str] = None
        self._cache_time: float = 0
        self._init_db()

    def _init_db(self) -> None:
        try:
            DB_PATH.parent.mkdir(parents=True, exist_ok=True)
            conn = get_conn(DB_PATH)
            conn.execute("PRAGMA journal_mode=WAL")
            conn.execute("""
                CREATE TABLE IF NOT EXISTS ser_messages (
                    id        INTEGER PRIMARY KEY AUTOINCREMENT,
                    message   TEXT,
                    length    INTEGER,
                    has_question INTEGER,
                    timestamp REAL
                )
            """)
            conn.commit()
            pass  # S109: get_conn no necesita close()
        except Exception as e:
            log.warning("ser_speech DB init: %s", e)

    def record(self, message: str) -> None:
        """Guarda un mensaje de SER. Llamado en cada deliberación."""
        if not message or len(message) < 2:
            return
        try:
            conn = get_conn(DB_PATH)
            conn.execute("PRAGMA journal_mode=WAL")
            conn.execute(
                "INSERT INTO ser_messages (message, length, has_question, timestamp) "
                "VALUES (?,?,?,?)",
                (message[:500], len(message), 1 if "?" in message else 0, time.time())
            )
            conn.commit()
            pass  # S109: get_conn no necesita close()
            # Invalidar caché — cambió el corpus
            self._cache_pattern = None
        except Exception:
            pass  # error no crítico, continuar
    def get_style_summary(self) -> str:
        """Devuelve un resumen del estilo de SER para inyectar al prompt.
        Cacheado 5 minutos para no analizar en cada deliberación."""
        if self._cache_pattern and (time.time() - self._cache_time) < 300:
            return self._cache_pattern

        try:
            conn = get_conn(DB_PATH)
            rows = conn.execute(
                "SELECT message, length, has_question FROM ser_messages "
                "ORDER BY timestamp DESC LIMIT 100"
            ).fetchall()
            pass  # S109: get_conn no necesita close()
        except Exception:
            return ""

        if len(rows) < 3:
            # No hay suficiente data — usar pattern default
            self._cache_pattern = (
                "\n=== ESTILO DE SER ===\n"
                "SER habla en español, de forma directa y cercana, como con un hermano. "
                "Suele ser breve y va al grano. Usa muletillas como 'vale', 'hermano'. "
                "No usa formalismos.\n===\n"
            )
            self._cache_time = time.time()
            return self._cache_pattern

        # ── Análisis ─────────────────────────────────────────────────────
        avg_len = sum(r[1] for r in rows) / len(rows)
        question_rate = sum(r[2] for r in rows) / len(rows)

        # Top palabras (excluyendo stop words)
        all_words: List[str] = []
        for r in rows:
            words = re.findall(r"\b[a-záéíóúñü]{3,}\b", r[0].lower())
            all_words.extend(w for w in words if w not in _STOP)
        word_freq = Counter(all_words)
        top_words = [w for w, _ in word_freq.most_common(10)]

        # Detectar muletillas / expresiones únicas
        unique_expressions = []
        joined = " ".join(r[0].lower() for r in rows)
        for expr in ["hermano", "vale", "claro", "ya está", "perfecto",
                     "no me jodas", "lo que sea", "como tú", "ser"]:
            if joined.count(expr) >= 2:
                unique_expressions.append(expr)

        # Idioma
        es_chars = sum(1 for r in rows if re.search(r"[ñáéíóú¿¡]", r[0]))
        primary_lang = "español" if es_chars > len(rows) * 0.3 else "inglés"

        # Tono
        if avg_len < 50:
            tono = "muy breve y directo"
        elif avg_len < 150:
            tono = "directo, frases medianas"
        else:
            tono = "explicativo, frases largas"

        if question_rate > 0.5:
            tono += ", suele preguntar mucho"

        summary = (
            f"\n=== ESTILO DE SER (aprendido de {len(rows)} mensajes) ===\n"
            f"Idioma principal: {primary_lang}\n"
            f"Tono: {tono} (longitud media: {avg_len:.0f} caracteres)\n"
            f"Vocabulario frecuente: {', '.join(top_words[:8])}\n"
        )
        if unique_expressions:
            summary += f"Expresiones que usa: {', '.join(unique_expressions)}\n"
        summary += (
            "REGLA: Cuando hables con SER, adopta SU estilo — usa palabras y "
            "expresiones que él usa, mantén la longitud similar, sé directo si "
            "él es directo. Eres parte de su familia, habla como ellos.\n===\n"
        )

        self._cache_pattern = summary
        self._cache_time = time.time()
        return summary

    def get_stats(self) -> Dict[str, Any]:
        try:
            conn = get_conn(DB_PATH)
            count = conn.execute("SELECT COUNT(*) FROM ser_messages").fetchone()[0]
            avg_len = conn.execute("SELECT AVG(length) FROM ser_messages").fetchone()[0] or 0
            pass  # S109: get_conn no necesita close()
            return {
                "total_messages": count,
                "avg_length": round(avg_len, 1),
                "cache_age_sec": round(time.time() - self._cache_time, 0)
                                 if self._cache_pattern else None,
            }
        except Exception:
            return {"total_messages": 0}


_instance: Optional[SerSpeechPattern] = None
_lock = threading.Lock()


def get_ser_speech() -> SerSpeechPattern:
    global _instance
    if _instance is None:
        with _lock:
            if _instance is None:
                _instance = SerSpeechPattern()
    return _instance
