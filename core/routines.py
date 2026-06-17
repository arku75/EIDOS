"""
EIDOS core/routines.py — Advanced Routine System
==================================================
Inspirado en IronClaw Routines: cron + event triggers + webhooks.

Permite a EIDOS ejecutar tareas programadas y reactivas:
- Cron: "cada 30 min comprueba si hay updates en mis repos"
- Event: "cuando RAM > 80% limpia cache"
- Webhook: "cuando reciba POST en /trigger/deploy ejecuta deploy.sh"

Integración:
    from core.routines import get_routine_manager

    rm = get_routine_manager()
    rm.add_cron("cleanup", "*/30 * * * *", cleanup_fn)
    rm.add_event("high_ram", lambda: ram_usage() > 80, alert_fn)
    rm.start()
"""
from __future__ import annotations

import json
import logging
import os
import re
import sqlite3
import threading
import time
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Callable, Optional
from core.db import get_conn
from core.db import get_conn_ctx

log = logging.getLogger("eidos.routines")

DB_PATH = os.path.expanduser("~/.eidos/routines.db")


# ══════════════════════════════════════════════════════════════════════════════
#  CRON PARSER (mini-cron sin dependencias)
# ══════════════════════════════════════════════════════════════════════════════

def _match_cron_field(field_val: str, current: int, max_val: int) -> bool:
    """Comprueba si un campo cron coincide con el valor actual."""
    if field_val == "*":
        return True
    # */N
    m = re.match(r"\*/(\d+)", field_val)
    if m:
        return current % int(m.group(1)) == 0
    # N-M
    m = re.match(r"(\d+)-(\d+)", field_val)
    if m:
        return int(m.group(1)) <= current <= int(m.group(2))
    # N,M,P
    if "," in field_val:
        return current in [int(x) for x in field_val.split(",")]
    # N exacto
    return current == int(field_val)


def cron_matches_now(cron_expr: str) -> bool:
    """Comprueba si una expresión cron coincide con el momento actual.

    Formato: MIN HOUR DOM MON DOW (5 campos estándar)
    """
    parts = cron_expr.strip().split()
    if len(parts) != 5:
        return False

    now = datetime.now()
    checks = [
        (parts[0], now.minute, 59),
        (parts[1], now.hour, 23),
        (parts[2], now.day, 31),
        (parts[3], now.month, 12),
        (parts[4], now.weekday(), 6),  # 0=Monday
    ]
    return all(_match_cron_field(f, c, m) for f, c, m in checks)


# ══════════════════════════════════════════════════════════════════════════════
#  TIPOS
# ══════════════════════════════════════════════════════════════════════════════

@dataclass
class Routine:
    """Una rutina programada o reactiva."""
    id: str
    name: str
    type: str                  # "cron", "event", "interval"
    schedule: str              # cron expr, event name, or interval seconds
    action: str                # shell command, function name, or Python code
    enabled: bool = True
    last_run: float = 0.0
    run_count: int = 0
    last_result: str = ""
    last_success: bool = True
    created_at: float = field(default_factory=time.time)
    max_failures: int = 5      # desactivar tras N fallos seguidos
    consecutive_failures: int = 0

    def to_dict(self) -> dict:
        return {
            "id": self.id, "name": self.name, "type": self.type,
            "schedule": self.schedule, "action": self.action,
            "enabled": self.enabled, "last_run": self.last_run,
            "run_count": self.run_count, "last_result": self.last_result[:200],
            "last_success": self.last_success,
        }


# ══════════════════════════════════════════════════════════════════════════════
#  ROUTINE MANAGER
# ══════════════════════════════════════════════════════════════════════════════

class RoutineManager:
    """Gestor de rutinas con cron, events e intervals."""

    CHECK_INTERVAL = 30  # segundos entre checks

    def __init__(self, db_path: str = DB_PATH) -> None:
        self.db_path = db_path
        self._routines: dict[str, Routine] = {}
        self._action_handlers: dict[str, Callable] = {}
        self._event_conditions: dict[str, Callable[[], bool]] = {}
        self._running = False
        self._thread: Optional[threading.Thread] = None

        os.makedirs(os.path.dirname(db_path), exist_ok=True)
        self._init_db()
        self._load_routines()
        self._register_builtin_handlers()
        log.info("RoutineManager: %d routines loaded", len(self._routines))

    def _init_db(self) -> None:
        with get_conn_ctx(self.db_path) as c:
            c.execute("""
                CREATE TABLE IF NOT EXISTS routines (
                    id          TEXT PRIMARY KEY,
                    name        TEXT NOT NULL,
                    type        TEXT NOT NULL,
                    schedule    TEXT NOT NULL,
                    action      TEXT NOT NULL,
                    enabled     INTEGER DEFAULT 1,
                    last_run    REAL DEFAULT 0,
                    run_count   INTEGER DEFAULT 0,
                    last_result TEXT DEFAULT '',
                    last_success INTEGER DEFAULT 1,
                    created_at  REAL,
                    max_failures INTEGER DEFAULT 5,
                    consecutive_failures INTEGER DEFAULT 0
                )
            """)
            c.execute("""
                CREATE TABLE IF NOT EXISTS routine_log (
                    id          INTEGER PRIMARY KEY AUTOINCREMENT,
                    routine_id  TEXT,
                    timestamp   REAL,
                    success     INTEGER,
                    result      TEXT,
                    duration_s  REAL
                )
            """)

    def _load_routines(self) -> None:
        with get_conn_ctx(self.db_path) as c:
            rows = c.execute("SELECT * FROM routines").fetchall()
        for r in rows:
            routine = Routine(
                id=r[0], name=r[1], type=r[2], schedule=r[3], action=r[4],
                enabled=bool(r[5]), last_run=r[6] or 0, run_count=r[7] or 0,
                last_result=r[8] or "", last_success=bool(r[9]),
                created_at=r[10] or time.time(), max_failures=r[11] or 5,
                consecutive_failures=r[12] or 0,
            )
            self._routines[routine.id] = routine

    def _save_routine(self, r: Routine) -> None:
        with get_conn_ctx(self.db_path) as c:
            c.execute(
                """INSERT OR REPLACE INTO routines
                   VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (r.id, r.name, r.type, r.schedule, r.action,
                 int(r.enabled), r.last_run, r.run_count,
                 r.last_result[:500], int(r.last_success),
                 r.created_at, r.max_failures, r.consecutive_failures)
            )

    def _register_builtin_handlers(self) -> None:
        """Registra acciones built-in."""
        import subprocess

        def shell_handler(command: str) -> str:
            result = subprocess.run(
                command, shell=True, capture_output=True, text=True, timeout=60
            )
            if result.returncode != 0:
                raise RuntimeError(result.stderr[:300])
            return result.stdout[:500]

        self._action_handlers["shell"] = shell_handler

    # ── API pública ───────────────────────────────────────────────────────────

    def add_cron(self, name: str, cron_expr: str, action: str,
                 handler: Optional[Callable] = None) -> str:
        """Añade una rutina cron.

        Args:
            name: Nombre descriptivo
            cron_expr: Expresión cron (MIN HOUR DOM MON DOW)
            action: Comando shell o nombre de handler
            handler: Función Python opcional
        """
        rid = f"cron_{name.replace(' ', '_').lower()}"
        routine = Routine(
            id=rid, name=name, type="cron",
            schedule=cron_expr, action=action,
        )
        self._routines[rid] = routine
        self._save_routine(routine)
        if handler:
            self._action_handlers[rid] = handler
        log.info("Added cron routine: %s [%s]", name, cron_expr)
        return rid

    def add_interval(self, name: str, seconds: int, action: str,
                     handler: Optional[Callable] = None) -> str:
        """Añade una rutina que se ejecuta cada N segundos."""
        rid = f"interval_{name.replace(' ', '_').lower()}"
        routine = Routine(
            id=rid, name=name, type="interval",
            schedule=str(seconds), action=action,
        )
        self._routines[rid] = routine
        self._save_routine(routine)
        if handler:
            self._action_handlers[rid] = handler
        log.info("Added interval routine: %s [%ds]", name, seconds)
        return rid

    def add_event(self, name: str, condition: Callable[[], bool],
                  action: str, handler: Optional[Callable] = None) -> str:
        """Añade una rutina que se ejecuta cuando se cumple una condición."""
        rid = f"event_{name.replace(' ', '_').lower()}"
        routine = Routine(
            id=rid, name=name, type="event",
            schedule="condition", action=action,
        )
        self._routines[rid] = routine
        self._save_routine(routine)
        self._event_conditions[rid] = condition
        if handler:
            self._action_handlers[rid] = handler
        log.info("Added event routine: %s", name)
        return rid

    def remove(self, routine_id: str) -> bool:
        """Elimina una rutina."""
        if routine_id in self._routines:
            del self._routines[routine_id]
            with get_conn_ctx(self.db_path) as c:
                c.execute("DELETE FROM routines WHERE id=?", (routine_id,))
            self._event_conditions.pop(routine_id, None)
            self._action_handlers.pop(routine_id, None)
            return True
        return False

    def enable(self, routine_id: str) -> None:
        if routine_id in self._routines:
            self._routines[routine_id].enabled = True
            self._routines[routine_id].consecutive_failures = 0
            self._save_routine(self._routines[routine_id])

    def disable(self, routine_id: str) -> None:
        if routine_id in self._routines:
            self._routines[routine_id].enabled = False
            self._save_routine(self._routines[routine_id])

    def list_routines(self) -> list[dict]:
        return [r.to_dict() for r in self._routines.values()]

    def get_stats(self) -> dict:
        total = len(self._routines)
        enabled = sum(1 for r in self._routines.values() if r.enabled)
        total_runs = sum(r.run_count for r in self._routines.values())
        return {
            "total": total,
            "enabled": enabled,
            "disabled": total - enabled,
            "total_runs": total_runs,
            "running": self._running,
        }

    # ── Ejecución ─────────────────────────────────────────────────────────────

    def start(self) -> None:
        """Inicia el manager en background thread."""
        if self._running:
            return
        self._running = True
        self._thread = threading.Thread(
            target=self._loop, daemon=True, name="RoutineManager"
        )
        self._thread.start()
        log.info("RoutineManager started (check every %ds)", self.CHECK_INTERVAL)

    def stop(self) -> None:
        self._running = False
        if self._thread:
            self._thread.join(timeout=5)
        log.info("RoutineManager stopped")

    def _loop(self) -> None:
        while self._running:
            try:
                self._check_all()
            except Exception as e:
                log.error("Routine check error: %s", e)
            time.sleep(self.CHECK_INTERVAL)

    def _check_all(self) -> None:
        """Comprueba todas las rutinas y ejecuta las que toca."""
        now = time.time()
        for routine in list(self._routines.values()):
            if not routine.enabled:
                continue

            should_run = False

            if routine.type == "cron":
                should_run = cron_matches_now(routine.schedule)
                # Evitar ejecutar más de 1 vez por minuto
                if should_run and (now - routine.last_run) < 55:
                    should_run = False

            elif routine.type == "interval":
                interval = int(routine.schedule)
                should_run = (now - routine.last_run) >= interval

            elif routine.type == "event":
                condition = self._event_conditions.get(routine.id)
                if condition:
                    try:
                        should_run = condition()
                    except Exception:
                        should_run = False
                # Cooldown: al menos 60s entre ejecuciones de events
                if should_run and (now - routine.last_run) < 60:
                    should_run = False

            if should_run:
                self._execute_routine(routine)

    def _execute_routine(self, routine: Routine) -> None:
        """Ejecuta una rutina."""
        t0 = time.time()
        success = False
        result = ""

        try:
            # Buscar handler registrado
            handler = self._action_handlers.get(routine.id)
            if handler:
                result = str(handler(routine.action))
            elif routine.action.startswith("shell:"):
                cmd = routine.action[6:].strip()
                shell_handler = self._action_handlers.get("shell")
                if shell_handler:
                    result = shell_handler(cmd)
            else:
                # Default: ejecutar como shell
                shell_handler = self._action_handlers.get("shell")
                if shell_handler:
                    result = shell_handler(routine.action)

            success = True
            routine.consecutive_failures = 0

        except Exception as e:
            result = str(e)[:300]
            routine.consecutive_failures += 1

            # Auto-disable tras demasiados fallos
            if routine.consecutive_failures >= routine.max_failures:
                routine.enabled = False
                log.warning(
                    "Routine '%s' disabled after %d consecutive failures",
                    routine.name, routine.consecutive_failures
                )

        elapsed = time.time() - t0
        routine.last_run = time.time()
        routine.run_count += 1
        routine.last_result = result[:500]
        routine.last_success = success
        self._save_routine(routine)

        # Log
        with get_conn_ctx(self.db_path) as c:
            c.execute(
                "INSERT INTO routine_log (routine_id, timestamp, success, result, duration_s) VALUES (?,?,?,?,?)",
                (routine.id, time.time(), int(success), result[:500], elapsed)
            )

        status = "OK" if success else "FAIL"
        log.info("Routine [%s] %s: %s (%.1fs)", routine.name, status, result[:60], elapsed)


# ══════════════════════════════════════════════════════════════════════════════
#  SINGLETON + BUILTIN ROUTINES
# ══════════════════════════════════════════════════════════════════════════════

_manager: RoutineManager | None = None


def get_routine_manager() -> RoutineManager:
    global _manager
    if _manager is None:
        _manager = RoutineManager()
        _setup_builtin_routines(_manager)
    return _manager


def _setup_builtin_routines(rm: RoutineManager) -> None:
    """Configura rutinas built-in si no existen ya."""
    existing = {r["id"] for r in rm.list_routines()}

    # Limpieza de cache cada 30 min
    if "cron_cache_cleanup" not in existing:
        rm.add_cron(
            "cache_cleanup",
            "*/30 * * * *",
            "shell:find ~/.eidos/cache -mmin +30 -delete 2>/dev/null; echo cleaned",
        )

    # Verificar Ollama cada 10 min
    if "interval_ollama_health" not in existing:
        rm.add_interval(
            "ollama_health",
            600,
            "shell:curl -s http://localhost:11434/api/tags | python3 -c 'import sys,json; d=json.load(sys.stdin); print(f\"{len(d.get(\\\"models\\\", []))} models OK\")' 2>/dev/null || echo 'Ollama DOWN'",
        )

    # Compactar logs cada hora
    if "cron_log_rotate" not in existing:
        rm.add_cron(
            "log_rotate",
            "0 * * * *",
            "shell:find ~/.eidos -name '*.log' -size +10M -exec truncate -s 5M {} \\; 2>/dev/null; echo rotated",
        )


# ══════════════════════════════════════════════════════════════════════════════
#  TEST
# ══════════════════════════════════════════════════════════════════════════════

if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)

    rm = get_routine_manager()
    print(f"Routines: {json.dumps(rm.get_stats(), indent=2)}")
    for r in rm.list_routines():
        print(f"  [{r['type']}] {r['name']}: {r['schedule']} → {r['action'][:60]}")
