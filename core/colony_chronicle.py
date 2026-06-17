"""
EIDOS core/colony_chronicle.py — Colony Chronicle
===================================================
Historial cronológico de TODAS las decisiones, eventos y acciones
de la colonia. Inspirado en ClawColony chronicle_api.go.

Cada evento queda registrado con timestamp, actor, tipo, contenido
y metadatos. Es la "memoria histórica" de la colonia — permite
auditar, analizar patrones, y que los agentes aprendan de su historia.

Uso:
    from core.colony_chronicle import get_chronicle
    ch = get_chronicle()
    ch.record("colony_coder", "task_completed", "Refactored smart_router.py",
              metadata={"lines_changed": 42, "confidence": 0.9})
    events = ch.query(actor="colony_coder", limit=10)
    summary = ch.summarize_period(hours=24)
"""
from __future__ import annotations

import json
import logging
import os
import sqlite3
import time
import threading
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional
from core.db import get_conn

log = logging.getLogger("eidos.chronicle")

DB_PATH = os.path.expanduser("~/.eidos/colony_chronicle.db")

# Event types
EVENT_TYPES = {
    "task_completed", "task_failed", "task_assigned",
    "decision_made", "proposal_created", "proposal_voted", "proposal_executed",
    "knowledge_learned", "knowledge_shared",
    "agent_spawned", "agent_died", "agent_hibernated", "agent_revived",
    "ollama_query", "ollama_response", "ollama_error",
    "mail_sent", "mail_received",
    "skill_evolved", "skill_created",
    "system_event", "security_event",
    "colony_discussion", "colony_consensus",
}

_SCHEMA = """
CREATE TABLE IF NOT EXISTS chronicle (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    timestamp REAL NOT NULL,
    actor TEXT NOT NULL,
    event_type TEXT NOT NULL,
    content TEXT NOT NULL,
    metadata TEXT DEFAULT '{}',
    importance REAL DEFAULT 0.5,
    session_id TEXT
);
CREATE INDEX IF NOT EXISTS idx_chronicle_ts ON chronicle(timestamp);
CREATE INDEX IF NOT EXISTS idx_chronicle_actor ON chronicle(actor);
CREATE INDEX IF NOT EXISTS idx_chronicle_type ON chronicle(event_type);

CREATE TABLE IF NOT EXISTS chronicle_summaries (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    period_start REAL NOT NULL,
    period_end REAL NOT NULL,
    summary TEXT NOT NULL,
    event_count INTEGER NOT NULL,
    created_at REAL NOT NULL
);
"""


@dataclass
class ChronicleEvent:
    id: int
    timestamp: float
    actor: str
    event_type: str
    content: str
    metadata: Dict[str, Any] = field(default_factory=dict)
    importance: float = 0.5
    session_id: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        return {
            "id": self.id,
            "timestamp": self.timestamp,
            "actor": self.actor,
            "event_type": self.event_type,
            "content": self.content,
            "metadata": self.metadata,
            "importance": self.importance,
        }


class ColonyChronicle:
    """Historial cronológico de la colonia."""

    def __init__(self, db_path: str = DB_PATH):
        os.makedirs(os.path.dirname(db_path), exist_ok=True)
        self._db_path = db_path
        self._lock = threading.Lock()
        self._conn = get_conn(db_path, check_same_thread=False)
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.execute("PRAGMA busy_timeout=5000")
        self._conn.executescript(_SCHEMA)
        self._conn.commit()
        log.info("Colony Chronicle initialized at %s", db_path)

    def record(self, actor: str, event_type: str, content: str,
               metadata: Optional[Dict[str, Any]] = None,
               importance: float = 0.5,
               session_id: Optional[str] = None) -> int:
        """Registra un evento en la crónica."""
        metadata = metadata or {}
        with self._lock:
            cur = self._conn.execute(
                "INSERT INTO chronicle (timestamp, actor, event_type, content, metadata, importance, session_id) "
                "VALUES (?, ?, ?, ?, ?, ?, ?)",
                (time.time(), actor, event_type, content,
                 json.dumps(metadata), importance, session_id)
            )
            self._conn.commit()
            return cur.lastrowid

    def query(self, actor: Optional[str] = None,
              event_type: Optional[str] = None,
              since: Optional[float] = None,
              until: Optional[float] = None,
              min_importance: float = 0.0,
              limit: int = 50) -> List[ChronicleEvent]:
        """Consulta eventos de la crónica."""
        conditions = ["importance >= ?"]
        params: list = [min_importance]

        if actor:
            conditions.append("actor = ?")
            params.append(actor)
        if event_type:
            conditions.append("event_type = ?")
            params.append(event_type)
        if since:
            conditions.append("timestamp >= ?")
            params.append(since)
        if until:
            conditions.append("timestamp <= ?")
            params.append(until)

        where = " AND ".join(conditions)
        with self._lock:
            rows = self._conn.execute(
                f"SELECT id, timestamp, actor, event_type, content, metadata, importance, session_id "
                f"FROM chronicle WHERE {where} ORDER BY timestamp DESC LIMIT ?",
                params + [limit]
            ).fetchall()

        return [
            ChronicleEvent(
                id=r[0], timestamp=r[1], actor=r[2], event_type=r[3],
                content=r[4], metadata=json.loads(r[5] or "{}"),
                importance=r[6], session_id=r[7]
            )
            for r in rows
        ]

    def get_recent(self, limit: int = 20) -> List[ChronicleEvent]:
        """Últimos N eventos."""
        return self.query(limit=limit)

    def count(self, actor: Optional[str] = None,
              event_type: Optional[str] = None,
              since: Optional[float] = None) -> int:
        """Cuenta eventos."""
        conditions = ["1=1"]
        params: list = []
        if actor:
            conditions.append("actor = ?")
            params.append(actor)
        if event_type:
            conditions.append("event_type = ?")
            params.append(event_type)
        if since:
            conditions.append("timestamp >= ?")
            params.append(since)

        where = " AND ".join(conditions)
        with self._lock:
            row = self._conn.execute(
                f"SELECT COUNT(*) FROM chronicle WHERE {where}", params
            ).fetchone()
        return row[0] if row else 0

    def summarize_period(self, hours: float = 24) -> Dict[str, Any]:
        """Genera un resumen de un período."""
        since = time.time() - (hours * 3600)
        events = self.query(since=since, limit=500)

        by_actor: Dict[str, int] = {}
        by_type: Dict[str, int] = {}
        for ev in events:
            by_actor[ev.actor] = by_actor.get(ev.actor, 0) + 1
            by_type[ev.event_type] = by_type.get(ev.event_type, 0) + 1

        return {
            "period_hours": hours,
            "total_events": len(events),
            "by_actor": by_actor,
            "by_type": by_type,
            "most_active": max(by_actor, key=by_actor.get) if by_actor else None,
            "most_common_event": max(by_type, key=by_type.get) if by_type else None,
        }

    def close(self):
        with self._lock:
            self._conn.close()


# Singleton
_instance: Optional[ColonyChronicle] = None

def get_chronicle() -> ColonyChronicle:
    global _instance
    if _instance is None:
        _instance = ColonyChronicle()
    return _instance
