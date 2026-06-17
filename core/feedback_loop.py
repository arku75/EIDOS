"""
EIDOS core/feedback_loop.py — Self-Evaluation Feedback Loop
=============================================================
EIDOS evalua la calidad de sus propias respuestas para mejorar continuamente.

Evalua:
- Completitud: respondio toda la pregunta?
- Precision: la respuesta tiene sentido logico?
- Eficiencia: uso las herramientas correctas con minimo overhead?
- Satisfaccion inferida: el usuario parece satisfecho? (keywords, follow-ups)

Flujo:
  User asks -> EIDOS responds -> FeedbackLoop.evaluate() -> score + lessons
  -> MetaLearner records -> future prompts mejoran

Uso:
    from core.feedback_loop import get_feedback_loop
    fl = get_feedback_loop()
    score = fl.evaluate(user_input, eidos_response, tools_used, duration)
"""
from __future__ import annotations

import json
import logging
import os
import re
import sqlite3
import time
from dataclasses import dataclass, field
from typing import Optional
from core.db import get_conn
from core.db import get_conn_ctx

log = logging.getLogger("eidos.feedback")

DB_PATH = os.path.expanduser("~/.eidos/feedback.db")

# Signals of user satisfaction / dissatisfaction
POSITIVE_SIGNALS = [
    "gracias", "perfecto", "genial", "bien", "ok", "vale", "correcto",
    "thanks", "great", "perfect", "good", "nice", "awesome", "exactly",
    "si", "yes", "funciona", "works", "listo",
]

NEGATIVE_SIGNALS = [
    "no", "mal", "error", "fallo", "wrong", "incorrect", "otra vez",
    "again", "retry", "fix", "arregla", "no funciona", "doesn't work",
    "pero", "but", "todavia", "still", "sigue", "repite",
]

FRUSTRATION_SIGNALS = [
    "joder", "mierda", "hostia", "damn", "fuck", "shit", "wtf",
    "no entiendes", "you don't understand", "olvida", "forget it",
]


@dataclass
class FeedbackScore:
    """Score of a single interaction."""
    completeness: float = 0.5    # 0-1: did we answer the full question?
    efficiency: float = 0.5      # 0-1: tool usage efficiency
    user_satisfaction: float = 0.5  # 0-1: inferred from user response
    overall: float = 0.5         # weighted average
    notes: str = ""


class FeedbackLoop:
    """Self-evaluation system for EIDOS responses."""

    def __init__(self, db_path: str = DB_PATH):
        self.db_path = db_path
        self._meta = None
        self._history: list[dict] = []  # recent evaluations

        os.makedirs(os.path.dirname(db_path), exist_ok=True)
        self._init_db()
        self._connect_meta_learner()

    def _init_db(self) -> None:
        with get_conn_ctx(self.db_path) as c:
            c.execute("""
                CREATE TABLE IF NOT EXISTS feedback (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    timestamp REAL,
                    user_input TEXT,
                    response_preview TEXT,
                    completeness REAL,
                    efficiency REAL,
                    user_satisfaction REAL,
                    overall REAL,
                    tools_used TEXT,
                    duration_s REAL,
                    notes TEXT
                )
            """)

    def _connect_meta_learner(self) -> None:
        try:
            from core.meta_learner import get_meta_learner
            self._meta = get_meta_learner()
        except Exception:
            pass  # error no crítico, continuar
    # ── Evaluation ───────────────────────────────────────────────────────────

    def evaluate(self, user_input: str, response: str,
                 tools_used: list[str] = None, duration_s: float = 0.0,
                 success: bool = True) -> FeedbackScore:
        """Evaluate the quality of an EIDOS response.

        Args:
            user_input: What the user asked
            response: What EIDOS responded
            tools_used: Tools used during response
            duration_s: Time taken
            success: Whether execution succeeded

        Returns:
            FeedbackScore with detailed metrics
        """
        tools_used = tools_used or []
        score = FeedbackScore()

        # 1. Completeness — did we address the full request?
        score.completeness = self._eval_completeness(user_input, response, success)

        # 2. Efficiency — tool usage quality
        score.efficiency = self._eval_efficiency(tools_used, duration_s)

        # 3. Overall (user_satisfaction is updated later via learn_from_followup)
        score.overall = (
            score.completeness * 0.5 +
            score.efficiency * 0.2 +
            score.user_satisfaction * 0.3
        )

        # Build notes
        notes_parts = []
        if score.completeness < 0.4:
            notes_parts.append("response may be incomplete")
        if score.efficiency < 0.3:
            notes_parts.append("too many tools or too slow")
        if not success:
            notes_parts.append("execution failed")
        score.notes = "; ".join(notes_parts) if notes_parts else "OK"

        # Persist
        self._save_feedback(user_input, response, score, tools_used, duration_s)

        # Feed to MetaLearner
        if self._meta and score.overall < 0.4:
            try:
                self._meta.record_interaction(
                    task=f"feedback:{user_input[:100]}",
                    approach="self_eval",
                    result_summary=f"low_score:{score.overall:.2f} — {score.notes}",
                    success=False,
                    tools_used=tools_used,
                    duration_s=duration_s,
                    error_type="low_quality",
                )
            except Exception:
                pass  # error no crítico, continuar
        self._history.append({
            "ts": time.time(),
            "input": user_input[:100],
            "overall": score.overall,
            "notes": score.notes,
        })
        # Keep history bounded
        if len(self._history) > 100:
            self._history = self._history[-100:]

        return score

    def learn_from_followup(self, previous_input: str, followup: str) -> float:
        """Update satisfaction score based on user's next message.

        Call this with the user's follow-up message to infer if they
        were satisfied with the previous response.

        Returns:
            Updated satisfaction score (0-1)
        """
        satisfaction = self._infer_satisfaction(followup)

        # Update the last feedback entry
        try:
            with get_conn_ctx(self.db_path) as c:
                c.execute("""
                    UPDATE feedback SET user_satisfaction = ?,
                    overall = (completeness * 0.5 + efficiency * 0.2 + ? * 0.3)
                    WHERE id = (SELECT MAX(id) FROM feedback)
                """, (satisfaction, satisfaction))
        except Exception:
            pass  # error no crítico, continuar
        return satisfaction

    # ── Evaluators ───────────────────────────────────────────────────────────

    def _eval_completeness(self, user_input: str, response: str,
                           success: bool) -> float:
        """Heuristic completeness score."""
        if not success:
            return 0.2

        score = 0.5

        # Response length relative to question complexity
        q_words = len(user_input.split())
        r_words = len(response.split())

        if q_words > 20 and r_words < 10:
            score -= 0.2  # complex question, short answer
        elif r_words > q_words * 0.5:
            score += 0.2  # decent response length

        # Check if response contains error indicators
        error_patterns = ["error", "failed", "no pude", "couldn't", "unable",
                          "traceback", "exception", "timeout"]
        if any(p in response.lower() for p in error_patterns):
            score -= 0.15

        # Check if response contains action/result indicators
        result_patterns = ["done", "listo", "completado", "saved", "created",
                          "resultado", "output", "OK"]
        if any(p in response.lower() for p in result_patterns):
            score += 0.15

        # Question marks in input suggest questions needing answers
        if "?" in user_input:
            # Check response actually has substance (not just "I don't know")
            if r_words > 15:
                score += 0.1

        return max(0.0, min(1.0, score))

    def _eval_efficiency(self, tools_used: list[str], duration_s: float) -> float:
        """Evaluate tool usage efficiency."""
        score = 0.6

        n_tools = len(tools_used)

        # Penalize excessive tool calls
        if n_tools > 10:
            score -= 0.3
        elif n_tools > 5:
            score -= 0.1

        # Penalize very slow responses
        if duration_s > 120:
            score -= 0.2
        elif duration_s > 60:
            score -= 0.1
        elif duration_s < 5 and n_tools > 0:
            score += 0.1  # fast with tools = efficient

        # Penalize repeated same tool (likely stuck in loop)
        if tools_used:
            unique_ratio = len(set(tools_used)) / len(tools_used)
            if unique_ratio < 0.3:
                score -= 0.2  # too many repeated calls

        return max(0.0, min(1.0, score))

    def _infer_satisfaction(self, followup: str) -> float:
        """Infer user satisfaction from their follow-up message."""
        text = followup.lower().strip()

        # Check frustration first (strongest signal)
        if any(s in text for s in FRUSTRATION_SIGNALS):
            return 0.1

        # Negative signals
        neg_count = sum(1 for s in NEGATIVE_SIGNALS if s in text)
        pos_count = sum(1 for s in POSITIVE_SIGNALS if s in text)

        if neg_count > 0 and pos_count == 0:
            return 0.3
        if pos_count > 0 and neg_count == 0:
            return 0.9
        if pos_count > neg_count:
            return 0.7

        # New topic = neutral (neither good nor bad)
        return 0.5

    # ── Persistence ──────────────────────────────────────────────────────────

    def _save_feedback(self, user_input: str, response: str,
                       score: FeedbackScore, tools: list[str],
                       duration: float) -> None:
        try:
            with get_conn_ctx(self.db_path) as c:
                c.execute("""
                    INSERT INTO feedback
                    (timestamp, user_input, response_preview, completeness,
                     efficiency, user_satisfaction, overall, tools_used,
                     duration_s, notes)
                    VALUES (?,?,?,?,?,?,?,?,?,?)
                """, (
                    time.time(),
                    user_input[:500],
                    response[:200],
                    score.completeness,
                    score.efficiency,
                    score.user_satisfaction,
                    score.overall,
                    json.dumps(tools),
                    duration,
                    score.notes,
                ))
        except Exception as e:
            log.warning("Feedback save failed: %s", e)

    # ── Stats ────────────────────────────────────────────────────────────────

    def get_stats(self) -> dict:
        """Get feedback statistics."""
        try:
            with get_conn_ctx(self.db_path) as c:
                total = c.execute("SELECT COUNT(*) FROM feedback").fetchone()[0]
                if total == 0:
                    return {"total": 0, "avg_overall": 0, "avg_satisfaction": 0}
                avg = c.execute("""
                    SELECT AVG(overall), AVG(user_satisfaction), AVG(completeness),
                           AVG(efficiency)
                    FROM feedback
                """).fetchone()
                low = c.execute(
                    "SELECT COUNT(*) FROM feedback WHERE overall < 0.4"
                ).fetchone()[0]
                return {
                    "total": total,
                    "avg_overall": round(avg[0] or 0, 3),
                    "avg_satisfaction": round(avg[1] or 0, 3),
                    "avg_completeness": round(avg[2] or 0, 3),
                    "avg_efficiency": round(avg[3] or 0, 3),
                    "low_quality_count": low,
                    "low_quality_pct": round(low / total * 100, 1) if total else 0,
                }
        except Exception:
            return {"total": 0, "error": "db_unavailable"}

    def get_recent(self, limit: int = 10) -> list[dict]:
        """Get recent feedback entries."""
        return self._history[-limit:]


# ══════════════════════════════════════════════════════════════════════════════
#  SINGLETON
# ══════════════════════════════════════════════════════════════════════════════

_feedback: Optional[FeedbackLoop] = None


def get_feedback_loop() -> FeedbackLoop:
    global _feedback
    if _feedback is None:
        _feedback = FeedbackLoop()
    return _feedback
