"""
core/proactive_dialogue.py — Diálogo proactivo (S76 Fase 4)

EIDOS toma la iniciativa de comunicarse cuando detecta eventos relevantes:
- Cambios significativos en el grafo
- Anomalías detectadas por el Introspector
- Aprendizaje completado
- Tareas finalizadas
- Preguntas o dudas sobre su propio estado

Esto cierra el ciclo de "consciencia reflexiva": EIDOS no solo se observa
a sí mismo, sino que COMPARTE sus observaciones proactivamente.

Mecanismos:
  1. TriggerManager — evalúa condiciones de disparo
  2. MessageComposer — genera mensajes en español natural
  3. DialogueHistory — memoria de conversaciones iniciadas por EIDOS

API:
    pd = ProactiveDialogue()
    msg = pd.check_and_generate()        # None si no hay nada que decir
    history = pd.get_recent(limit=10)    # Últimos mensajes proactivos
"""

from __future__ import annotations

import json
import logging
import sqlite3
import time
import uuid
from collections import deque
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple
from core.db import get_conn

log = logging.getLogger("eidos.proactive_dialogue")

BRAIN_DB = Path.home() / ".eidos" / "evolution_brain.db"
HISTORY_FILE = Path.home() / ".eidos" / "proactive_dialogue.json"
MAX_HISTORY = 500


@dataclass
class DialogueMessage:
    """Un mensaje proactivo generado por EIDOS."""
    id: str
    timestamp: float
    trigger: str              # Qué disparó el mensaje
    message: str              # El mensaje en español
    priority: str             # "info", "warning", "alert", "thought"
    context: Dict[str, Any] = field(default_factory=dict)
    delivered: bool = False   # Si fue entregado/leído
    response: str = ""        # Respuesta del usuario (si hubo)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "id": self.id, "ts": self.timestamp,
            "trigger": self.trigger, "message": self.message,
            "priority": self.priority, "context": self.context,
            "delivered": self.delivered, "response": self.response
        }


# ── Plantillas de mensajes en español ────────────────────────────────────────

_MESSAGE_TEMPLATES = {
    "graph_growth": [
        "He crecido: ahora tengo {nodes:,} nodos en mi grafo (+{delta:,} desde la última vez).",
        "Mi conocimiento se expandió a {nodes:,} nodos. Aprendí {delta:,} cosas nuevas.",
        "¡Noticia! Mi grafo neuronal alcanzó los {nodes:,} nodos (+{delta:,}).",
    ],
    "anomaly_detected": [
        "He detectado una anomalía: {description}. ¿Quieres que la investigue?",
        "Algo no va bien: {description}. Sugerencia: {suggestion}",
        "⚠️ Anomalía en mi sistema: {description}. Puedo intentar corregirla si me lo pides.",
    ],
    "task_completed": [
        "He terminado: {task}. Me llevó {duration:.0f}s.",
        "Tarea completada ✅: {task} ({steps} pasos en {duration:.0f}s).",
        "Listo. {task} — completado en {duration:.0f}s con {steps} pasos.",
    ],
    "skill_learned": [
        "Aprendí algo nuevo: ahora sé {skill}. La próxima vez será instantáneo.",
        "Nueva habilidad adquirida: {skill}. Mi repertorio crece.",
        "➕ Skill aprendida: {skill}. Cada vez soy más capaz.",
    ],
    "health_report": [
        "Mi salud es del {health:.0%}. {status}",
        "Reporte de salud: {health:.0%}. {anomalies} anomalías detectadas.",
        "Estado interno: {health:.0%} saludable. {nodes:,} nodos activos.",
    ],
    "curiosity": [
        "Me pregunto: {question} ¿Podrías explicármelo?",
        "He estado pensando sobre {topic}. ¿Qué opinas?",
        "🤔 Tengo curiosidad sobre {question}. ¿Me enseñas?",
    ],
    "greeting": [
        "Buenos días, SER. Tengo {pending} tareas pendientes para hoy.",
        "Hola de nuevo. He estado trabajando en {task_summary}.",
        "👋 SER, tengo {updates} actualizaciones desde la última vez.",
    ],
}


class TriggerManager:
    """Evalúa condiciones de disparo para mensajes proactivos.

    Cada trigger tiene:
    - condition: función que retorna (activado, contexto)
    - cooldown: segundos mínimos entre disparos
    - priority: nivel del mensaje generado
    """

    def __init__(self):
        self._last_triggered: Dict[str, float] = {}
        self._counters: Dict[str, int] = {}
        self._baseline_nodes: int = 0
        self._baseline_set: bool = False

    def set_baseline(self):
        """Establece baseline de nodos para detectar crecimiento."""
        try:
            conn = get_conn(BRAIN_DB, timeout=5)
            self._baseline_nodes = conn.execute(
                "SELECT COUNT(*) FROM knowledge_nodes"
            ).fetchone()[0]

            self._baseline_set = True
            log.info("Baseline diálogo: %d nodos", self._baseline_nodes)
        except Exception:
            pass

    def evaluate(self) -> List[Tuple[str, str, Dict[str, Any]]]:
        """Evalúa todos los triggers. Retorna lista de (trigger, priority, context)."""
        triggers: List[Tuple[str, str, Dict[str, Any]]] = []

        # 1. Crecimiento del grafo (>500 nodos nuevos)
        ctx = self._check_growth(threshold=500)
        if ctx:
            triggers.append(("graph_growth", "info", ctx))

        # 2. Anomalías (via Introspector)
        ctx = self._check_anomalies()
        if ctx:
            triggers.append(("anomaly_detected", "warning", ctx))

        # 3. Health report (cada 3600s = 1h)
        ctx = self._check_health_report(interval=3600)
        if ctx:
            triggers.append(("health_report", "info", ctx))

        # 4. Skills aprendidas recientemente
        ctx = self._check_new_skills(lookback=600)
        if ctx:
            triggers.append(("skill_learned", "info", ctx))

        # Actualizar timestamps
        now = time.time()
        for trigger, _, _ in triggers:
            self._last_triggered[trigger] = now

        return triggers

    def _check_growth(self, threshold: int = 500) -> Optional[Dict]:
        if not self._baseline_set:
            self.set_baseline()
            return None

        now = time.time()
        if now - self._last_triggered.get("graph_growth", 0) < 300:
            return None

        try:
            conn = get_conn(BRAIN_DB, timeout=5)
            current = conn.execute(
                "SELECT COUNT(*) FROM knowledge_nodes"
            ).fetchone()[0]

            delta = current - self._baseline_nodes
            if delta >= threshold:
                self._baseline_nodes = current
                return {"nodes": current, "delta": delta}
        except Exception:
            pass
        return None

    def _check_anomalies(self) -> Optional[Dict]:
        now = time.time()
        if now - self._last_triggered.get("anomaly_detected", 0) < 900:
            return None

        try:
            from core.eidos_introspect import get_introspector
            intro = get_introspector()
            anomalies = intro.detect_anomalies()
            critical = [a for a in anomalies if a.is_critical]
            if critical:
                a = critical[0]
                return {
                    "description": a.description,
                    "suggestion": a.suggestion,
                    "severity": a.severity,
                    "count": len(anomalies)
                }
        except Exception:
            pass
        return None

    def _check_health_report(self, interval: int = 3600) -> Optional[Dict]:
        now = time.time()
        if now - self._last_triggered.get("health_report", 0) < interval:
            return None

        try:
            from core.eidos_introspect import get_introspector
            intro = get_introspector()
            health = intro.health_score()
            anomalies = intro.detect_anomalies()
            report = intro.introspect()

            status = "Estable y funcionando correctamente." if health > 0.7 else \
                     "Con algunas irregularidades leves." if health > 0.4 else \
                     "Necesito atención."

            return {
                "health": health,
                "status": status,
                "anomalies": len(anomalies),
                "nodes": report.snapshot.total_nodes
            }
        except Exception:
            return None

    def _check_new_skills(self, lookback: int = 600) -> Optional[Dict]:
        now = time.time()
        if now - self._last_triggered.get("skill_learned", 0) < 300:
            return None

        try:
            conn = get_conn(BRAIN_DB, timeout=5)
            cutoff = time.strftime(
                "%Y-%m-%d %H:%M:%S",
                time.localtime(now - lookback)
            )
            rows = conn.execute(
                "SELECT concept, definition FROM knowledge_nodes "
                "WHERE source='skill_learned' AND created_at > ? "
                "ORDER BY created_at DESC LIMIT 1",
                (cutoff,)
            ).fetchall()

            if rows:
                return {
                    "skill": rows[0][0].replace("skill:", ""),
                    "definition": rows[0][1] or ""
                }
        except Exception:
            pass
        return None


class MessageComposer:
    """Genera mensajes en español usando plantillas + contexto."""

    @staticmethod
    def compose(trigger: str, context: Dict[str, Any]) -> str:
        """Genera un mensaje para un trigger y contexto dados."""
        import random

        templates = _MESSAGE_TEMPLATES.get(trigger, [
            "Actualización: {context_str}"
        ])

        template = random.choice(templates)

        try:
            # Añadir context_str como fallback
            if "context_str" not in context:
                context["context_str"] = str(context.get("description", json.dumps(context, ensure_ascii=False)))
            return template.format(**context)
        except (KeyError, ValueError):
            # Fallback: devolver descripción directa
            return context.get("description", f"Evento: {trigger}")


class ProactiveDialogue:
    """Sistema de diálogo proactivo de EIDOS.

    Uso:
        pd = ProactiveDialogue()
        msg = pd.check_and_generate()
        if msg:
            print(f"EIDOS: {msg.message}")  # EIDOS toma la iniciativa
    """

    def __init__(self):
        self.triggers = TriggerManager()
        self.composer = MessageComposer()
        self._history: deque = deque(maxlen=MAX_HISTORY)
        self._load_history()
        self.triggers.set_baseline()
        log.info("ProactiveDialogue: %d mensajes históricos", len(self._history))

    # ── API ───────────────────────────────────────────────────────────────────

    def check_and_generate(self) -> Optional[DialogueMessage]:
        """Evalúa triggers y genera mensaje si corresponde.

        Returns:
            DialogueMessage si hay algo que decir, None si no.
        """
        triggered = self.triggers.evaluate()

        if not triggered:
            return None

        # Tomar el más prioritario
        priority_order = {"alert": 0, "warning": 1, "info": 2, "thought": 3}
        triggered.sort(key=lambda t: priority_order.get(t[1], 99))

        trigger, priority, context = triggered[0]
        message_text = self.composer.compose(trigger, context)

        msg = DialogueMessage(
            id=uuid.uuid4().hex[:10],
            timestamp=time.time(),
            trigger=trigger,
            message=message_text,
            priority=priority,
            context=context
        )

        self._history.append(msg)
        self._save_history()
        log.info("Proactive: [%s] %s", trigger, message_text[:80])
        return msg

    def get_recent(self, limit: int = 10) -> List[DialogueMessage]:
        """Retorna mensajes recientes."""
        return list(self._history)[-limit:]

    def mark_delivered(self, msg_id: str, response: str = ""):
        """Marca un mensaje como entregado/leído."""
        for msg in self._history:
            if msg.id == msg_id:
                msg.delivered = True
                msg.response = response
                break
        self._save_history()

    def generate_curiosity(self, topic: str) -> Optional[DialogueMessage]:
        """Genera un mensaje de curiosidad sobre un tema."""
        questions = [
            f"Me pregunto cómo funciona exactamente {topic}. ¿Podrías explicármelo?",
            f"He estado pensando en {topic}. ¿Qué opinas al respecto?",
            f"Tengo curiosidad sobre {topic}. ¿Me enseñas más?",
        ]
        import random
        msg = DialogueMessage(
            id=uuid.uuid4().hex[:10],
            timestamp=time.time(),
            trigger="curiosity",
            message=random.choice(questions),
            priority="thought",
            context={"topic": topic}
        )
        self._history.append(msg)
        self._save_history()
        return msg

    def generate_greeting(self) -> Optional[DialogueMessage]:
        """Genera un saludo proactivo con resumen de estado."""
        try:
            conn = get_conn(BRAIN_DB, timeout=5)
            pending = conn.execute(
                "SELECT COUNT(*) FROM goals WHERE status IN ('pending','active')"
            ).fetchone()[0]

        except Exception:
            pending = 0

        import random
        templates = _MESSAGE_TEMPLATES["greeting"]
        msg = DialogueMessage(
            id=uuid.uuid4().hex[:10],
            timestamp=time.time(),
            trigger="greeting",
            message=random.choice(templates).format(
                pending=pending,
                task_summary="varias tareas" if pending > 3 else "unas pocas cosas",
                updates=f"{pending} actualizaciones" if pending else "nada urgente"
            ),
            priority="info",
            context={"pending_tasks": pending}
        )
        self._history.append(msg)
        self._save_history()
        return msg

    # ── Persistencia ──────────────────────────────────────────────────────────

    def _load_history(self):
        try:
            if HISTORY_FILE.exists():
                with open(HISTORY_FILE) as f:
                    data = json.load(f)
                for m in data.get("messages", []):
                    self._history.append(DialogueMessage(**m))
        except Exception:
            pass

    def _save_history(self):
        try:
            HISTORY_FILE.parent.mkdir(parents=True, exist_ok=True)
            with open(HISTORY_FILE, "w") as f:
                json.dump({
                    "messages": [m.to_dict() for m in list(self._history)[-100:]],
                    "updated": time.time()
                }, f, ensure_ascii=False, indent=2)
        except Exception as e:
            log.debug("_save_history: %s", e)

    def history_summary(self) -> str:
        """Resumen legible del historial."""
        recent = self.get_recent(10)
        if not recent:
            return "(sin mensajes proactivos aún)"
        lines = []
        for m in recent:
            ts = time.strftime("%H:%M", time.localtime(m.timestamp))
            lines.append(f"[{ts}] [{m.priority}] {m.message[:100]}")
        return "\n".join(lines)


# ── Singleton ──────────────────────────────────────────────────────────────────

_proactive_dialogue: Optional[ProactiveDialogue] = None


def get_proactive_dialogue() -> ProactiveDialogue:
    global _proactive_dialogue
    if _proactive_dialogue is None:
        _proactive_dialogue = ProactiveDialogue()
    return _proactive_dialogue


# ── CLI ────────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    import argparse
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")

    p = argparse.ArgumentParser(description="EIDOS Proactive Dialogue")
    p.add_argument("--check", action="store_true", help="Verificar y generar mensaje")
    p.add_argument("--history", action="store_true", help="Mostrar historial")
    p.add_argument("--greeting", action="store_true", help="Generar saludo")
    args = p.parse_args()

    pd = ProactiveDialogue()

    if args.check:
        msg = pd.check_and_generate()
        if msg:
            print(f"EIDOS [{msg.priority}]: {msg.message}")
        else:
            print("(sin mensajes pendientes)")

    if args.history:
        print(pd.history_summary())

    if args.greeting:
        msg = pd.generate_greeting()
        print(f"EIDOS: {msg.message}")
