"""
EIDOS core/task_queue.py — SQLite Task Queue (Fase 2 del Roadmap)
==================================================================
Según el Oráculo 2: "SQLite Task Queue con retry logic y dead letter queue.
Aquí nace la autosuficiencia operativa."

Features:
  ✅ Cola de tareas persistente en ~/.eidos/tasks.db
  ✅ Estados: PENDING → RUNNING → DONE | FAILED | DEAD
  ✅ Retry logic: 3 intentos antes de dead letter
  ✅ Dead letter queue: tareas fallidas permanentes
  ✅ Task boundary guard: no inicia nueva tarea si hay una en curso
  ✅ Interrupt capability: marcado limpio de tareas abortadas
  ✅ Recovery: retoma tareas RUNNING al reiniciar (crash recovery)
"""
from __future__ import annotations

import sqlite3
import json
import time
import uuid
import os
from datetime import datetime
from typing import Any
from core.db import get_conn

DB_PATH = os.path.expanduser("~/.eidos/tasks.db")
os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)

MAX_RETRIES = 3


def _get_conn() -> sqlite3.Connection:
    conn = get_conn(DB_PATH, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    return conn


def init_db() -> None:
    """Crea las tablas si no existen."""
    with _get_conn() as conn:
        conn.executescript("""
            CREATE TABLE IF NOT EXISTS tasks (
                id          TEXT PRIMARY KEY,
                task        TEXT NOT NULL,
                status      TEXT NOT NULL DEFAULT 'PENDING',
                retries     INTEGER NOT NULL DEFAULT 0,
                result      TEXT,
                error       TEXT,
                created_at  TEXT NOT NULL,
                updated_at  TEXT NOT NULL,
                metadata    TEXT DEFAULT '{}'
            );

            CREATE TABLE IF NOT EXISTS dead_letter (
                id          TEXT PRIMARY KEY,
                task        TEXT NOT NULL,
                error       TEXT,
                retries     INTEGER NOT NULL,
                created_at  TEXT NOT NULL,
                failed_at   TEXT NOT NULL,
                metadata    TEXT DEFAULT '{}'
            );

            CREATE INDEX IF NOT EXISTS idx_status ON tasks(status);
        """)

    # Recovery: marcar como PENDING las tareas que quedaron en RUNNING
    # (probablemente por crash del proceso anterior)
    with _get_conn() as conn:
        n = conn.execute(
            "UPDATE tasks SET status='PENDING', updated_at=? WHERE status='RUNNING'",
            (datetime.now().isoformat(),)
        ).rowcount
        if n > 0:
            print(f"\033[93m[TASK QUEUE] Recovery: {n} tarea(s) RUNNING→PENDING (crash recovery)\033[0m")


# Inicializar automáticamente al importar
init_db()


class TaskQueue:
    """
    Cola de tareas persistente con retry logic.
    
    Ejemplo:
        queue = TaskQueue()
        task_id = queue.push("escanea la red local con nmap")
        task = queue.pop_next()
        if task:
            result = run_task(task["task"])
            queue.mark_done(task["id"], result)
    """

    def push(self, task: str, metadata: dict | None = None) -> str:
        """
        Añade una tarea a la cola.
        Returns: task_id
        """
        task_id = str(uuid.uuid4())[:8]  # pyre-ignore[arg-type]
        now = datetime.now().isoformat()
        with _get_conn() as conn:
            conn.execute(
                "INSERT INTO tasks (id, task, status, created_at, updated_at, metadata) VALUES (?,?,?,?,?,?)",
                (task_id, task, "PENDING", now, now, json.dumps(metadata or {}))
            )
        print(f"\033[94m[TASK QUEUE]\033[0m Tarea encolada: {task_id} → {task[:60]}")  # pyre-ignore[arg-type]
        return task_id

    def pop_next(self) -> dict | None:
        """
        Obtiene la siguiente tarea PENDING y la marca como RUNNING.
        Returns: dict con id, task, retries, metadata — o None si no hay.
        """
        with _get_conn() as conn:
            row = conn.execute(
                "SELECT * FROM tasks WHERE status='PENDING' ORDER BY created_at LIMIT 1"
            ).fetchone()
            if row is None:
                return None
            now = datetime.now().isoformat()
            conn.execute(
                "UPDATE tasks SET status='RUNNING', updated_at=? WHERE id=?",
                (now, row["id"])
            )
        return dict(row)

    def mark_done(self, task_id: str, result: str) -> None:
        """Marca una tarea como completada con su resultado."""
        with _get_conn() as conn:
            conn.execute(
                "UPDATE tasks SET status='DONE', result=?, updated_at=? WHERE id=?",
                (result[:2000], datetime.now().isoformat(), task_id)  # pyre-ignore[arg-type]
            )
        print(f"\033[92m[TASK QUEUE]\033[0m ✅ Completada: {task_id}")

    def mark_failed(self, task_id: str, error: str) -> str:
        """
        Marca una tarea como fallida. Si supera MAX_RETRIES, va a dead letter.
        Returns: "retry" | "dead"
        """
        with _get_conn() as conn:
            row = conn.execute("SELECT * FROM tasks WHERE id=?", (task_id,)).fetchone()
            if row is None:
                return "dead"
            
            retries = row["retries"] + 1
            now = datetime.now().isoformat()
            
            if retries >= MAX_RETRIES:
                # Mover a dead letter queue
                conn.execute(
                    """INSERT INTO dead_letter (id, task, error, retries, created_at, failed_at, metadata)
                       VALUES (?,?,?,?,?,?,?)""",
                    (task_id, row["task"], error[:500], retries,  # pyre-ignore[arg-type]
                     row["created_at"], now, row["metadata"])
                )
                conn.execute("DELETE FROM tasks WHERE id=?", (task_id,))
                print(f"\033[91m[TASK QUEUE]\033[0m ☠️ Dead letter: {task_id} (falló {retries}x)")
                return "dead"
            else:
                conn.execute(
                    "UPDATE tasks SET status='PENDING', retries=?, error=?, updated_at=? WHERE id=?",
                    (retries, error[:500], now, task_id)  # pyre-ignore[arg-type]
                )
                print(f"\033[93m[TASK QUEUE]\033[0m 🔄 Retry {retries}/{MAX_RETRIES}: {task_id}")
                return "retry"

    def abort(self, task_id: str) -> None:
        """Marca una tarea como abortada (Ctrl+C limpio)."""
        with _get_conn() as conn:
            conn.execute(
                "UPDATE tasks SET status='ABORTED', updated_at=? WHERE id=?",
                (datetime.now().isoformat(), task_id)
            )
        print(f"\033[93m[TASK QUEUE]\033[0m ⚠️ Abortada: {task_id}")

    def has_running(self) -> bool:
        """Task boundary guard: ¿hay una tarea en ejecución?"""
        with _get_conn() as conn:
            n = conn.execute("SELECT COUNT(*) FROM tasks WHERE status='RUNNING'").fetchone()[0]  # pyre-ignore[arg-type]
        return n > 0

    def list_pending(self) -> list[dict]:
        """Lista todas las tareas pendientes."""
        with _get_conn() as conn:
            rows = conn.execute(
                "SELECT id, task, retries, created_at FROM tasks WHERE status='PENDING' ORDER BY created_at"
            ).fetchall()
        return [dict(r) for r in rows]

    def list_dead_letter(self) -> list[dict]:
        """Lista las tareas en dead letter queue."""
        with _get_conn() as conn:
            rows = conn.execute(
                "SELECT id, task, error, retries, failed_at FROM dead_letter ORDER BY failed_at DESC"
            ).fetchall()
        return [dict(r) for r in rows]

    def status_summary(self) -> dict[str, int]:
        """Resumen de estado de todas las tareas."""
        with _get_conn() as conn:
            rows = conn.execute(
                "SELECT status, COUNT(*) as n FROM tasks GROUP BY status"
            ).fetchall()
            dead = conn.execute("SELECT COUNT(*) FROM dead_letter").fetchone()[0]  # pyre-ignore[arg-type]
        summary = {r["status"]: r["n"] for r in rows}
        summary["DEAD_LETTER"] = dead
        return summary


# Singleton global
task_queue = TaskQueue()


# ── Test rápido ──────────────────────────────────────────────────────────────
if __name__ == "__main__":
    print("=== Test SQLite Task Queue ===")
    q = TaskQueue()
    
    t1 = q.push("escanea la red con nmap")
    t2 = q.push("toma un screenshot y describelo")
    
    print(f"Pendientes: {q.list_pending()}")
    print(f"¿Hay running? {q.has_running()}")
    
    task = q.pop_next()
    print(f"Pop: {task['id']} → {task['task']}")
    print(f"¿Hay running ahora? {q.has_running()}")
    
    q.mark_done(task["id"], "nmap completado: 5 hosts encontrados")
    
    # Test retry
    task2 = q.pop_next()
    q.mark_failed(task2["id"], "Error: moondream no responde")
    q.mark_failed(task2["id"], "Error: timeout")
    q.mark_failed(task2["id"], "Error: timeout")  # → Dead letter
    
    print(f"\nResumen: {q.status_summary()}")
    print(f"Dead letter: {q.list_dead_letter()}")
    print("\n✅ SQLite Task Queue operativa")
