"""
EIDOS core/meta_learner.py — Meta-Learning Engine
===================================================
Inspirado en MetaClaw: EIDOS aprende de sus propias interacciones.

NO usa RL con GPU (no tenemos). En su lugar:
- Registra cada interacción (input → output → feedback)
- Detecta patrones de éxito/fracaso
- Genera "lecciones" que se inyectan en futuros prompts
- Evoluciona estrategias basado en resultados reales

Integración:
    from core.meta_learner import get_meta_learner

    ml = get_meta_learner()
    ml.record_interaction(task, result, success=True)
    lessons = ml.get_relevant_lessons(new_task)
    ml.inject_lessons(messages, new_task)  # modifica messages in-place
"""
from __future__ import annotations

import json
import logging
import os
import sqlite3
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional
from core.db import get_conn
from core.db import get_conn_ctx

log = logging.getLogger("eidos.meta_learner")

DB_PATH = os.path.expanduser("~/.eidos/meta_learner.db")
LESSONS_FILE = os.path.expanduser("~/.eidos/meta_lessons.json")


# ══════════════════════════════════════════════════════════════════════════════
#  TIPOS
# ══════════════════════════════════════════════════════════════════════════════

@dataclass
class Interaction:
    """Un registro de interacción completa."""
    id: str
    task: str               # Qué se pidió
    task_category: str      # coding, security, research, system, creative
    approach: str           # Cómo se abordó
    result_summary: str     # Qué pasó
    success: bool           # Éxito o fracaso
    tools_used: list[str]   # Herramientas utilizadas
    duration_s: float       # Tiempo total
    timestamp: float
    feedback: str = ""      # Feedback del usuario (si lo hay)
    error_type: str = ""    # Tipo de error (si falló)


@dataclass
class Lesson:
    """Una lección aprendida de interacciones pasadas."""
    id: str
    category: str           # Categoría de la lección
    trigger: str            # Cuándo aplicar esta lección
    content: str            # La lección en sí
    confidence: float       # 0.0-1.0
    source_count: int       # De cuántas interacciones se derivó
    created_at: float
    last_applied: float = 0.0
    times_applied: int = 0
    times_helpful: int = 0  # Veces que ayudó (feedback positivo)


# ══════════════════════════════════════════════════════════════════════════════
#  CATEGORIZACIÓN
# ══════════════════════════════════════════════════════════════════════════════

CATEGORY_KEYWORDS = {
    "coding": ["code", "script", "función", "function", "bug", "error",
               "python", "rust", "javascript", "class", "import", "variable",
               "compilar", "compile", "refactor", "test", "debug"],
    "security": ["scan", "nmap", "exploit", "vuln", "pentest", "recon",
                 "brute", "hash", "crack", "shell", "reverse", "payload",
                 "osint", "dns", "port", "firewall", "injection"],
    "research": ["buscar", "search", "investigar", "research", "aprender",
                 "learn", "documentación", "docs", "tutorial", "artículo",
                 "explicar", "explain", "qué es", "what is"],
    "system": ["instalar", "install", "configurar", "config", "servicio",
               "service", "proceso", "process", "disco", "disk", "red",
               "network", "ram", "cpu", "systemd", "docker", "apt"],
    "creative": ["video", "imagen", "image", "arte", "art", "historia",
                 "story", "música", "music", "generar", "generate",
                 "crear", "create", "diseñar", "design"],
}


def categorize_task(task: str) -> str:
    """Categoriza una tarea por keywords."""
    task_lower = task.lower()
    scores = {}
    for cat, keywords in CATEGORY_KEYWORDS.items():
        scores[cat] = sum(1 for kw in keywords if kw in task_lower)
    best = max(scores, key=scores.get)
    return best if scores[best] > 0 else "general"


# ══════════════════════════════════════════════════════════════════════════════
#  META LEARNER
# ══════════════════════════════════════════════════════════════════════════════

class MetaLearner:
    """Motor de meta-aprendizaje: aprende de cada interacción."""

    # Mínimo de interacciones para generar lección
    MIN_INTERACTIONS_FOR_LESSON = 3
    # Máximo de lecciones a inyectar en un prompt
    MAX_INJECTED_LESSONS = 5

    def __init__(self, db_path: str = DB_PATH) -> None:
        self.db_path = db_path
        self._lessons: list[Lesson] = []
        os.makedirs(os.path.dirname(db_path), exist_ok=True)
        self._init_db()
        self._load_lessons()
        log.info("MetaLearner initialized: %d lessons loaded", len(self._lessons))

    def _init_db(self) -> None:
        with get_conn_ctx(self.db_path) as c:
            c.executescript("""
                CREATE TABLE IF NOT EXISTS interactions (
                    id              TEXT PRIMARY KEY,
                    task            TEXT NOT NULL,
                    task_category   TEXT,
                    approach        TEXT,
                    result_summary  TEXT,
                    success         INTEGER,
                    tools_json      TEXT DEFAULT '[]',
                    duration_s      REAL,
                    timestamp       REAL,
                    feedback        TEXT DEFAULT '',
                    error_type      TEXT DEFAULT ''
                );
                CREATE TABLE IF NOT EXISTS lessons (
                    id              TEXT PRIMARY KEY,
                    category        TEXT,
                    trigger_text    TEXT,
                    content         TEXT,
                    confidence      REAL,
                    source_count    INTEGER,
                    created_at      REAL,
                    last_applied    REAL DEFAULT 0,
                    times_applied   INTEGER DEFAULT 0,
                    times_helpful   INTEGER DEFAULT 0
                );
                CREATE INDEX IF NOT EXISTS idx_interactions_cat
                    ON interactions(task_category);
                CREATE INDEX IF NOT EXISTS idx_interactions_success
                    ON interactions(success);
                CREATE INDEX IF NOT EXISTS idx_lessons_cat
                    ON lessons(category);
            """)

    def _load_lessons(self) -> None:
        """Carga lecciones de la DB."""
        with get_conn_ctx(self.db_path) as c:
            rows = c.execute(
                "SELECT * FROM lessons ORDER BY confidence DESC"
            ).fetchall()
        self._lessons = []
        for r in rows:
            self._lessons.append(Lesson(
                id=r[0], category=r[1], trigger=r[2], content=r[3],
                confidence=r[4], source_count=r[5], created_at=r[6],
                last_applied=r[7] or 0, times_applied=r[8] or 0,
                times_helpful=r[9] or 0,
            ))

    # ── API pública ───────────────────────────────────────────────────────────

    def record_interaction(
        self,
        task: str,
        approach: str,
        result_summary: str,
        success: bool,
        tools_used: list[str] | None = None,
        duration_s: float = 0.0,
        feedback: str = "",
        error_type: str = "",
    ) -> str:
        """Registra una interacción completada."""
        iid = f"i_{int(time.time())}_{os.urandom(3).hex()}"
        category = categorize_task(task)

        with get_conn_ctx(self.db_path) as c:
            c.execute(
                "INSERT INTO interactions VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                (iid, task, category, approach, result_summary,
                 int(success), json.dumps(tools_used or []),
                 duration_s, time.time(), feedback, error_type)
            )

        log.info("Recorded interaction %s [%s] success=%s", iid, category, success)

        # Intentar generar lecciones después de cada N interacciones
        self._maybe_evolve_lessons(category)
        return iid

    def record_feedback(self, interaction_id: str, feedback: str, helpful: bool) -> None:
        """Registra feedback del usuario sobre una interacción."""
        with get_conn_ctx(self.db_path) as c:
            c.execute(
                "UPDATE interactions SET feedback=? WHERE id=?",
                (feedback, interaction_id)
            )

        # Actualizar confianza de lecciones aplicadas recientemente
        if helpful:
            for lesson in self._lessons:
                if lesson.last_applied > time.time() - 600:  # últimos 10 min
                    lesson.times_helpful += 1
                    lesson.confidence = min(1.0, lesson.confidence + 0.05)
                    self._save_lesson(lesson)

    def get_relevant_lessons(self, task: str, max_n: int = 5) -> list[Lesson]:
        """Obtiene lecciones relevantes para una tarea."""
        category = categorize_task(task)
        task_lower = task.lower()

        relevant = []
        for lesson in self._lessons:
            score = 0.0
            # Misma categoría: +0.5
            if lesson.category == category:
                score += 0.5
            # Trigger match en task
            trigger_words = lesson.trigger.lower().split()
            matches = sum(1 for w in trigger_words if w in task_lower)
            if trigger_words:
                score += 0.5 * (matches / len(trigger_words))
            # Confianza
            score *= lesson.confidence
            # Helpfulness bonus
            if lesson.times_applied > 0:
                helpfulness = lesson.times_helpful / max(lesson.times_applied, 1)
                score *= (0.5 + helpfulness * 0.5)

            if score > 0.1:
                relevant.append((score, lesson))

        relevant.sort(key=lambda x: x[0], reverse=True)
        return [l for _, l in relevant[:max_n]]

    def inject_lessons(self, messages: list[dict], task: str) -> list[dict]:
        """Inyecta lecciones relevantes en el system prompt."""
        lessons = self.get_relevant_lessons(task, self.MAX_INJECTED_LESSONS)
        if not lessons:
            return messages

        lesson_text = "\n".join(
            f"- [{l.category}] {l.content} (confidence: {l.confidence:.0%})"
            for l in lessons
        )
        injection = (
            f"\n\n[META-LEARNING] Lecciones de experiencia previa:\n"
            f"{lesson_text}\n"
            f"Aplica estas lecciones si son relevantes.\n"
        )

        # Inyectar en system message o crear uno
        if messages and messages[0].get("role") == "system":
            messages[0]["content"] += injection
        else:
            messages.insert(0, {"role": "system", "content": injection})

        # Marcar lecciones como aplicadas
        for lesson in lessons:
            lesson.times_applied += 1
            lesson.last_applied = time.time()
            self._save_lesson(lesson)

        log.info("Injected %d lessons for task: %s", len(lessons), task[:50])
        return messages

    def get_stats(self) -> dict:
        """Estadísticas del meta-learner."""
        with get_conn_ctx(self.db_path) as c:
            total = c.execute("SELECT COUNT(*) FROM interactions").fetchone()[0]
            success = c.execute(
                "SELECT COUNT(*) FROM interactions WHERE success=1"
            ).fetchone()[0]
            cats = c.execute(
                "SELECT task_category, COUNT(*), SUM(success) FROM interactions GROUP BY task_category"
            ).fetchall()

        return {
            "total_interactions": total,
            "successful": success,
            "success_rate": success / max(total, 1),
            "total_lessons": len(self._lessons),
            "categories": {
                r[0]: {"total": r[1], "success": r[2]}
                for r in cats
            },
        }

    # ── Evolución de lecciones ─────────────────────────────────────────────

    def _maybe_evolve_lessons(self, category: str) -> None:
        """Intenta generar nuevas lecciones si hay suficientes datos."""
        with get_conn_ctx(self.db_path) as c:
            count = c.execute(
                "SELECT COUNT(*) FROM interactions WHERE task_category=?",
                (category,)
            ).fetchone()[0]

        if count < self.MIN_INTERACTIONS_FOR_LESSON:
            return

        # Analizar patrones de éxito vs fracaso
        self._evolve_from_failures(category)
        self._evolve_from_successes(category)
        self._evolve_from_tools(category)

    def _evolve_from_failures(self, category: str) -> None:
        """Genera lecciones de fallos recurrentes."""
        with get_conn_ctx(self.db_path) as c:
            failures = c.execute(
                """SELECT error_type, COUNT(*) as cnt, GROUP_CONCAT(task, ' | ')
                   FROM interactions
                   WHERE success=0 AND task_category=? AND error_type != ''
                   GROUP BY error_type HAVING cnt >= 2
                   ORDER BY cnt DESC LIMIT 5""",
                (category,)
            ).fetchall()

        for error_type, count, tasks in failures:
            lid = f"l_fail_{category}_{error_type[:20]}".replace(" ", "_")
            if any(l.id == lid for l in self._lessons):
                continue

            lesson = Lesson(
                id=lid,
                category=category,
                trigger=f"{error_type} error in {category}",
                content=f"En tareas de {category}, el error '{error_type}' es recurrente "
                        f"({count} veces). Verifica antes de ejecutar: "
                        f"dependencias instaladas, permisos correctos, timeout suficiente.",
                confidence=min(0.9, 0.3 + count * 0.1),
                source_count=count,
                created_at=time.time(),
            )
            self._save_lesson(lesson)
            self._lessons.append(lesson)
            log.info("New lesson from failures: %s", lid)

    def _evolve_from_successes(self, category: str) -> None:
        """Genera lecciones de patrones exitosos."""
        with get_conn_ctx(self.db_path) as c:
            successes = c.execute(
                """SELECT approach, COUNT(*) as cnt
                   FROM interactions
                   WHERE success=1 AND task_category=?
                   GROUP BY approach HAVING cnt >= 2
                   ORDER BY cnt DESC LIMIT 3""",
                (category,)
            ).fetchall()

        for approach, count in successes:
            if not approach or len(approach) < 10:
                continue
            lid = f"l_ok_{category}_{hash(approach) % 99999}"
            if any(l.id == lid for l in self._lessons):
                continue

            lesson = Lesson(
                id=lid,
                category=category,
                trigger=f"successful approach in {category}",
                content=f"Enfoque que funciona en {category}: {approach[:200]}",
                confidence=min(0.9, 0.4 + count * 0.1),
                source_count=count,
                created_at=time.time(),
            )
            self._save_lesson(lesson)
            self._lessons.append(lesson)
            log.info("New lesson from successes: %s", lid)

    def _evolve_from_tools(self, category: str) -> None:
        """Genera lecciones sobre qué tools funcionan mejor por categoría."""
        with get_conn_ctx(self.db_path) as c:
            rows = c.execute(
                """SELECT tools_json, success FROM interactions
                   WHERE task_category=? AND tools_json != '[]'
                   ORDER BY timestamp DESC LIMIT 20""",
                (category,)
            ).fetchall()

        if len(rows) < 3:
            return

        tool_success = {}
        tool_fail = {}
        for tools_json, success in rows:
            tools = json.loads(tools_json)
            for tool in tools:
                if success:
                    tool_success[tool] = tool_success.get(tool, 0) + 1
                else:
                    tool_fail[tool] = tool_fail.get(tool, 0) + 1

        # Tools con alto éxito
        best_tools = sorted(
            tool_success.items(),
            key=lambda x: x[1] / max(x[1] + tool_fail.get(x[0], 0), 1),
            reverse=True
        )[:5]

        if best_tools:
            lid = f"l_tools_{category}"
            tools_str = ", ".join(f"{t}({c}x)" for t, c in best_tools)

            # Actualizar si existe
            existing = next((l for l in self._lessons if l.id == lid), None)
            if existing:
                existing.content = f"Tools más efectivas en {category}: {tools_str}"
                existing.source_count = len(rows)
                self._save_lesson(existing)
            else:
                lesson = Lesson(
                    id=lid,
                    category=category,
                    trigger=f"tool selection for {category}",
                    content=f"Tools más efectivas en {category}: {tools_str}",
                    confidence=0.6,
                    source_count=len(rows),
                    created_at=time.time(),
                )
                self._save_lesson(lesson)
                self._lessons.append(lesson)

    def _save_lesson(self, lesson: Lesson) -> None:
        """Persiste una lección a la DB."""
        with get_conn_ctx(self.db_path) as c:
            c.execute(
                """INSERT OR REPLACE INTO lessons
                   VALUES (?,?,?,?,?,?,?,?,?,?)""",
                (lesson.id, lesson.category, lesson.trigger, lesson.content,
                 lesson.confidence, lesson.source_count, lesson.created_at,
                 lesson.last_applied, lesson.times_applied, lesson.times_helpful)
            )

    def prune_stale_lessons(self, max_age_days: int = 30) -> int:
        """Elimina lecciones viejas y de baja confianza."""
        cutoff = time.time() - (max_age_days * 86400)
        removed = 0
        self._lessons = [
            l for l in self._lessons
            if not (l.confidence < 0.2 and l.created_at < cutoff)
            or not (removed := removed + 1)  # side effect counter
        ]
        with get_conn_ctx(self.db_path) as c:
            c.execute(
                "DELETE FROM lessons WHERE confidence < 0.2 AND created_at < ?",
                (cutoff,)
            )
        return removed


# ══════════════════════════════════════════════════════════════════════════════
#  SINGLETON
# ══════════════════════════════════════════════════════════════════════════════

_meta: MetaLearner | None = None


def get_meta_learner() -> MetaLearner:
    global _meta
    if _meta is None:
        _meta = MetaLearner()
    return _meta


# ══════════════════════════════════════════════════════════════════════════════
#  TEST
# ══════════════════════════════════════════════════════════════════════════════

if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    ml = get_meta_learner()

    # Simular interacciones
    ml.record_interaction(
        task="escanear red 192.168.1.0/24",
        approach="nmap -sn rápido",
        result_summary="5 hosts encontrados",
        success=True,
        tools_used=["exec_shell", "nmap"],
        duration_s=12.5,
    )
    ml.record_interaction(
        task="escanear puertos 10.0.0.1",
        approach="nmap -sV completo",
        result_summary="timeout tras 30s",
        success=False,
        tools_used=["exec_shell", "nmap"],
        duration_s=30.0,
        error_type="timeout",
    )
    ml.record_interaction(
        task="escribir script Python para parsear logs",
        approach="generar con lfm2.5-1.2b-instruct:q4_0",
        result_summary="script funcional, 45 líneas",
        success=True,
        tools_used=["write_file", "exec_shell"],
        duration_s=8.2,
    )

    stats = ml.get_stats()
    print(f"\nStats: {json.dumps(stats, indent=2)}")

    lessons = ml.get_relevant_lessons("escanear red local")
    print(f"\nLessons for 'escanear red local': {len(lessons)}")
    for l in lessons:
        print(f"  [{l.category}] {l.content[:80]}")
