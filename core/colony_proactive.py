"""
core/colony_proactive.py — Iniciativa propia de EIDOS

Colony puede poner mensajes en cola para contarle algo a SER
sin esperar a que SER pregunte primero.

El CLI los muestra al inicio de cada turno:
  "💡 EIDOS: Mientras estudiabas, descubrí que n8n tiene webhooks nativos —
             perfectamente integrable con el sistema que construiste."

Uso:
    from core.colony_proactive import push_message, pop_messages
    push_message("Colony", "Aprendí algo sobre n8n que puede interesarte...",
                 topic="n8n", priority=7)
    msgs = pop_messages()   # en el CLI al inicio de cada turno
"""
from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Dict, List

QUEUE_FILE = Path.home() / ".eidos" / "proactive_queue.json"
MAX_QUEUE   = 10  # máx mensajes pendientes


def push_message(
    actor: str,
    message: str,
    topic: str = "",
    priority: int = 5,
) -> None:
    """Añade un mensaje proactivo para mostrar a SER en el próximo turno.

    priority: 1-10 (10 = máxima urgencia).
    """
    QUEUE_FILE.parent.mkdir(parents=True, exist_ok=True)
    msgs = _load()

    # Fix 9: deduplicar — si ya hay un mensaje del mismo topic no añadir otro
    if topic:
        existing_topics = {m.get("topic", "").split(":")[0] for m in msgs}
        topic_base = topic.split(":")[0]
        if topic_base and topic_base in existing_topics:
            return  # ya hay un mensaje sobre este tema

    msgs.append({
        "actor":     actor[:40],
        "message":   message[:600],
        "topic":     topic[:80],
        "priority":  priority,
        "timestamp": time.time(),
    })
    # Ordenar por prioridad descendente y limitar tamaño de cola
    msgs.sort(key=lambda m: -m["priority"])
    msgs = msgs[:MAX_QUEUE]
    QUEUE_FILE.write_text(json.dumps(msgs, ensure_ascii=False, indent=2))


def pop_messages(limit: int = 3) -> List[Dict]:
    """Extrae y elimina los mensajes más prioritarios de la cola."""
    msgs = _load()
    if not msgs:
        return []
    to_show   = msgs[:limit]
    remaining = msgs[limit:]
    if remaining:
        QUEUE_FILE.write_text(json.dumps(remaining, ensure_ascii=False, indent=2))
    else:
        try:
            QUEUE_FILE.unlink(missing_ok=True)
        except Exception:
            pass
    return to_show


def peek_messages() -> List[Dict]:
    """Ve los mensajes pendientes sin eliminarlos."""
    return _load()


def clear_queue() -> None:
    """Vacía la cola de mensajes proactivos."""
    try:
        QUEUE_FILE.unlink(missing_ok=True)
    except Exception:
        pass


def _load() -> List[Dict]:
    try:
        if QUEUE_FILE.exists():
            return json.loads(QUEUE_FILE.read_text(encoding="utf-8"))
    except Exception:
        pass
    return []
