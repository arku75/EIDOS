"""
core/eidos_events.py — Event Bus mínimo para desacoplar módulos (S77)

Sistema pub/sub ligero. Los módulos publican eventos y otros se suscriben
sin conocerse entre sí. Persiste eventos en SQLite como log inmutable.

API:
    bus = get_event_bus()
    bus.publish("goal_completed", {"goal_id": "...", "success": True})
    bus.subscribe("goal_completed", my_handler)
"""

from __future__ import annotations

import json
import logging
import sqlite3
import time
import uuid
from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional
from core.db import get_conn

log = logging.getLogger("eidos.events")

BRAIN_DB = Path.home() / ".eidos" / "evolution_brain.db"
MAX_EVENT_LOG = 10000

SYSTEM_EVENTS = {
    "goal_completed": "Una meta se completó",
    "goal_failed": "Una meta falló",
    "anomaly_detected": "El introspector detectó una anomalía",
    "skill_learned": "Se aprendió una nueva habilidad",
    "knowledge_injected": "Se inyectó conocimiento nuevo al grafo",
    "health_changed": "La salud del sistema cambió significativamente",
    "cycle_completed": "Un ciclo vital completó",
    "error_occurred": "Ocurrió un error",
    "user_interaction": "SER interactuó con EIDOS",
    "structural_refresh": "Se completó refresh estructural",
    "affect_changed": "El estado emocional cambió",
    "rl_reward": "El sistema RL recibió recompensa",
    "system_startup": "EIDOS inició",
    "system_shutdown": "EIDOS se detuvo",
}

@dataclass
class Event:
    id: str
    type: str
    timestamp: float
    data: Dict[str, Any] = field(default_factory=dict)
    source: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return {"id": self.id, "type": self.type, "ts": self.timestamp,
                "data": self.data, "source": self.source}

EventHandler = Callable[["Event"], None]

class EventBus:
    def __init__(self):
        self._subscribers: Dict[str, List[EventHandler]] = defaultdict(list)
        self._publish_count = 0
        self._init_db()
        log.info("EventBus: inicializado con %d tipos de eventos", len(SYSTEM_EVENTS))

    def _init_db(self):
        try:
            conn = get_conn(BRAIN_DB, timeout=10)
            conn.execute("PRAGMA busy_timeout=10000")
            conn.execute("""CREATE TABLE IF NOT EXISTS event_log (
                id TEXT PRIMARY KEY, type TEXT NOT NULL,
                timestamp REAL NOT NULL, data TEXT DEFAULT '{}',
                source TEXT DEFAULT '')""")
            conn.execute("""CREATE INDEX IF NOT EXISTS idx_event_log_type_ts
                ON event_log(type, timestamp DESC)""")
            conn.commit()

        except Exception as e:
            log.warning("EventBus DB init: %s", e)

    def publish(self, event_type: str, data: Dict[str, Any] = None,
                source: str = "") -> Event:
        if data is None:
            data = {}
        event = Event(id=uuid.uuid4().hex[:16], type=event_type,
                     timestamp=time.time(), data=data, source=source or "unknown")
        self._persist(event)
        for handler in self._subscribers.get(event_type, []):
            try:
                handler(event)
            except Exception:
                log.exception("EventBus: handler falló para evento %s", event_type)
        for handler in self._subscribers.get("*", []):
            try:
                handler(event)
            except Exception:
                log.exception("EventBus: wildcard handler falló para evento %s", event_type)
        self._publish_count += 1
        if self._publish_count % 100 == 0:
            self._rotate_if_needed()
        return event

    def subscribe(self, event_type: str, handler: EventHandler):
        self._subscribers[event_type].append(handler)

    def recent(self, limit: int = 50, event_type: Optional[str] = None) -> List[Event]:
        try:
            conn = get_conn(BRAIN_DB, timeout=5)
            if event_type:
                rows = conn.execute(
                    "SELECT id, type, timestamp, data, source FROM event_log "
                    "WHERE type=? ORDER BY timestamp DESC LIMIT ?",
                    (event_type, limit)).fetchall()
            else:
                rows = conn.execute(
                    "SELECT id, type, timestamp, data, source FROM event_log "
                    "ORDER BY timestamp DESC LIMIT ?", (limit,)).fetchall()

            events = []
            for row in rows:
                try:
                    data = json.loads(row[3]) if row[3] else {}
                except Exception:
                    data = {}
                events.append(Event(id=row[0], type=row[1], timestamp=row[2],
                                   data=data, source=row[4] or ""))
            return events
        except Exception as e:
            log.debug("EventBus recent: %s", e)
            return []

    def stats(self) -> Dict[str, Any]:
        try:
            conn = get_conn(BRAIN_DB, timeout=5)
            total = conn.execute("SELECT COUNT(*) FROM event_log").fetchone()[0]
            by_type = {}
            for row in conn.execute(
                "SELECT type, COUNT(*) FROM event_log GROUP BY type "
                "ORDER BY COUNT(*) DESC LIMIT 10"):
                by_type[row[0]] = row[1]

            return {"total_events": total, "by_type": by_type,
                    "subscribers": {k: len(v) for k, v in self._subscribers.items()}}
        except Exception:
            return {"total_events": 0, "by_type": {}, "subscribers": {}}

    def _persist(self, event: Event):
        try:
            conn = get_conn(BRAIN_DB, timeout=10)
            conn.execute("PRAGMA busy_timeout=10000")
            conn.execute(
                "INSERT INTO event_log (id, type, timestamp, data, source) VALUES (?,?,?,?,?)",
                (event.id, event.type, event.timestamp,
                 json.dumps(event.data, ensure_ascii=False), event.source))
            conn.commit()

        except Exception as e:
            log.debug("EventBus _persist: %s", e)

    def _rotate_if_needed(self):
        try:
            conn = get_conn(BRAIN_DB, timeout=10)
            count = conn.execute("SELECT COUNT(*) FROM event_log").fetchone()[0]
            if count > MAX_EVENT_LOG:
                cutoff = conn.execute(
                    "SELECT timestamp FROM event_log ORDER BY timestamp DESC LIMIT 1 OFFSET 8000"
                ).fetchone()
                if cutoff:
                    deleted = conn.execute(
                        "DELETE FROM event_log WHERE timestamp < ?", (cutoff[0],)).rowcount
                    conn.commit()
                    log.info("EventBus: rotados %d eventos antiguos", deleted)

        except Exception as e:
            log.debug("EventBus rotate: %s", e)

_event_bus: Optional[EventBus] = None

def get_event_bus() -> EventBus:
    global _event_bus
    if _event_bus is None:
        _event_bus = EventBus()
    return _event_bus

def emit(event_type: str, data: Dict[str, Any] = None, source: str = ""):
    get_event_bus().publish(event_type, data, source)

def on(event_type: str, handler: EventHandler):
    get_event_bus().subscribe(event_type, handler)

if __name__ == "__main__":
    import argparse
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    p = argparse.ArgumentParser(description="EIDOS Event Bus")
    p.add_argument("--stats", action="store_true")
    p.add_argument("--recent", type=int, default=20)
    args = p.parse_args()
    bus = EventBus()
    if args.stats:
        print(json.dumps(bus.stats(), indent=2, ensure_ascii=False))
    if args.recent:
        for e in bus.recent(args.recent):
            ts = time.strftime("%H:%M:%S", time.localtime(e.timestamp))
            print(f"[{ts}] {e.type}: {json.dumps(e.data, ensure_ascii=False)[:100]}")
