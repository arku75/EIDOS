"""
core/task_list.py — TaskList persistente de EIDOS (equivalente a TaskCreate/TaskUpdate de Claude).

EIDOS puede crear, actualizar y listar sus propias tareas autónomas.
Se persiste en ~/.eidos/eidos_tasks.db (SQLite).

Uso:
    from core.task_list import TaskList
    tl = TaskList()
    tid = tl.create("Estudiar técnicas de SQL injection", priority="high")
    tl.update(tid, status="in_progress")
    tl.update(tid, status="completed", notes="Estudiados OWASP top 10 + ejemplos prácticos")
    tl.list_pending()

Integración con eidos_libre / curiosity daemon:
    Cuando EIDOS investiga un tema → crea tarea.
    Al terminar la investigación → marca completed.
    Cuando Colony responde sobre un tema → marca done con resultado.
"""
from __future__ import annotations

import logging
import os
import sqlite3
from core.db import get_conn
import time
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import List, Optional

log = logging.getLogger("eidos.task_list")

DB_PATH = Path.home() / ".eidos" / "eidos_tasks.db"


@dataclass
class EidosTask:
    id:          str
    subject:     str
    description: str
    status:      str
    priority:    str
    tags:        str
    notes:       str
    created_at:  float
    updated_at:  float
    agent_id:    str

    def __str__(self) -> str:
        icon = {"pending": "⬜", "in_progress": "🔄", "completed": "✅",
                "blocked": "🚧", "cancelled": "❌"}.get(self.status, "⬜")
        return (f"{icon} [{self.priority:5}] {self.subject[:60]}"
                f"  [{self.status}] — {self.tags}")


class TaskList:
    """Gestor de tareas persistente de EIDOS."""

    def __init__(self, db_path: Path = DB_PATH):
        DB_PATH.parent.mkdir(parents=True, exist_ok=True)
        self._db_path = str(db_path)
        self._init_db()

    # ── API pública ──────────────────────────────────────────────────────────

    def create(self, subject: str, description: str = "", priority: str = "normal",
               tags: str = "", agent_id: str = "eidos") -> str:
        """Crea una tarea nueva y devuelve su ID."""
        tid = str(uuid.uuid4())[:8]
        now = time.time()
        with self._conn() as conn:
            conn.execute(
                "INSERT INTO tasks (id,subject,description,status,priority,tags,notes,"
                "created_at,updated_at,agent_id) VALUES (?,?,?,?,?,?,?,?,?,?)",
                (tid, subject, description, "pending", priority, tags, "", now, now, agent_id)
            )
        log.info("Tarea creada: [%s] %s", tid, subject[:50])
        return tid

    def update(self, task_id: str, status: Optional[str] = None,
               notes: Optional[str] = None, subject: Optional[str] = None) -> bool:
        """Actualiza campos de una tarea. Devuelve True si existía."""
        with self._conn() as conn:
            task = conn.execute("SELECT id FROM tasks WHERE id=?", (task_id,)).fetchone()
            if not task:
                return False
            if status:
                conn.execute("UPDATE tasks SET status=?,updated_at=? WHERE id=?",
                             (status, time.time(), task_id))
            if notes:
                conn.execute("UPDATE tasks SET notes=notes||'\n'||?,updated_at=? WHERE id=?",
                             (notes, time.time(), task_id))
            if subject:
                conn.execute("UPDATE tasks SET subject=?,updated_at=? WHERE id=?",
                             (subject, time.time(), task_id))
        return True

    def get(self, task_id: str) -> Optional[EidosTask]:
        with self._conn() as conn:
            row = conn.execute("SELECT * FROM tasks WHERE id=?", (task_id,)).fetchone()
        return EidosTask(*row) if row else None

    def list_pending(self, limit: int = 20) -> List[EidosTask]:
        with self._conn() as conn:
            rows = conn.execute(
                "SELECT * FROM tasks WHERE status IN ('pending','in_progress','blocked') "
                "ORDER BY CASE priority WHEN 'critical' THEN 0 WHEN 'high' THEN 1 "
                "WHEN 'normal' THEN 2 ELSE 3 END, created_at DESC LIMIT ?",
                (limit,)
            ).fetchall()
        return [EidosTask(*r) for r in rows]

    def list_recent(self, limit: int = 10, status: str = "all") -> List[EidosTask]:
        with self._conn() as conn:
            if status == "all":
                rows = conn.execute(
                    "SELECT * FROM tasks ORDER BY updated_at DESC LIMIT ?", (limit,)
                ).fetchall()
            else:
                rows = conn.execute(
                    "SELECT * FROM tasks WHERE status=? ORDER BY updated_at DESC LIMIT ?",
                    (status, limit)
                ).fetchall()
        return [EidosTask(*r) for r in rows]

    def stats(self) -> dict:
        with self._conn() as conn:
            total = conn.execute("SELECT COUNT(*) FROM tasks").fetchone()[0]
            by_status = dict(conn.execute(
                "SELECT status, COUNT(*) FROM tasks GROUP BY status"
            ).fetchall())
        return {"total": total, "by_status": by_status}

    def display(self, tasks: Optional[List[EidosTask]] = None) -> str:
        if tasks is None:
            tasks = self.list_pending()
        if not tasks:
            return "  [sin tareas pendientes]"
        return "\n".join(str(t) for t in tasks)

    # ── Privados ─────────────────────────────────────────────────────────────

    def _conn(self):
        conn = get_conn(self._db_path, timeout=5)
        return conn

    def _init_db(self) -> None:
        with self._conn() as conn:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS tasks (
                    id          TEXT PRIMARY KEY,
                    subject     TEXT NOT NULL,
                    description TEXT DEFAULT '',
                    status      TEXT DEFAULT 'pending',
                    priority    TEXT DEFAULT 'normal',
                    tags        TEXT DEFAULT '',
                    notes       TEXT DEFAULT '',
                    created_at  REAL DEFAULT (strftime('%s','now')),
                    updated_at  REAL DEFAULT (strftime('%s','now')),
                    agent_id    TEXT DEFAULT 'eidos'
                )
            """)
            conn.execute("CREATE INDEX IF NOT EXISTS idx_tasks_status ON tasks(status)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_tasks_priority ON tasks(priority)")
