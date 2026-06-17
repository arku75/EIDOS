"""
EIDOS core/task_scheduler.py — Internal Task Scheduler
=======================================================
Programador de tareas internas con scheduling tipo cron.
Permite programar scans, backups, checks, reports periódicos.

Uso:
    from core.task_scheduler import get_scheduler
    sched = get_scheduler()
    sched.add("health_check", interval_m=5, action="health")
    sched.add("nmap_scan", cron="0 */6 * * *", action="net_scan")
    sched.start()
"""
from __future__ import annotations

import json
import logging
import os
import sqlite3
import threading
import time
from dataclasses import dataclass, field
from typing import Any, Callable, Optional
from core.db import get_conn
from core.db import get_conn_ctx

log = logging.getLogger("eidos.scheduler")

DB_PATH = os.path.expanduser("~/.eidos/scheduler.db")


@dataclass
class ScheduledTask:
    """A scheduled task definition."""
    name: str
    action: str  # health, net_scan, log_scan, anomaly_check, report, custom
    interval_m: float = 0  # Interval in minutes (0 = cron-based)
    cron: str = ""  # Cron expression (simplified: m h dom mon dow)
    enabled: bool = True
    last_run: float = 0.0
    next_run: float = 0.0
    run_count: int = 0
    last_result: str = ""
    last_error: str = ""
    payload: dict = field(default_factory=dict)


class TaskScheduler:
    """Internal task scheduler with interval and cron-like support."""

    # Built-in action handlers
    ACTIONS: dict[str, Callable] = {}

    def __init__(self, db_path: str = DB_PATH):
        self.db_path = db_path
        self._tasks: dict[str, ScheduledTask] = {}
        self._running = False
        self._thread: Optional[threading.Thread] = None
        self._custom_handlers: dict[str, Callable] = {}
        os.makedirs(os.path.dirname(db_path), exist_ok=True)
        self._init_db()
        self._load_tasks()
        self._register_builtin_actions()

    def _init_db(self) -> None:
        with get_conn_ctx(self.db_path) as c:
            c.execute("PRAGMA journal_mode=WAL")
            c.execute("""CREATE TABLE IF NOT EXISTS tasks (
                name TEXT PRIMARY KEY, action TEXT, interval_m REAL DEFAULT 0,
                cron TEXT DEFAULT '', enabled INTEGER DEFAULT 1,
                last_run REAL DEFAULT 0, run_count INTEGER DEFAULT 0,
                last_result TEXT DEFAULT '', last_error TEXT DEFAULT '',
                payload TEXT DEFAULT '{}')""")
            c.execute("""CREATE TABLE IF NOT EXISTS runs (
                id INTEGER PRIMARY KEY AUTOINCREMENT, task_name TEXT,
                started_at REAL, finished_at REAL, result TEXT DEFAULT '',
                error TEXT DEFAULT '', duration_s REAL DEFAULT 0)""")

    def _load_tasks(self) -> None:
        try:
            with get_conn_ctx(self.db_path) as c:
                for row in c.execute("SELECT * FROM tasks").fetchall():
                    t = ScheduledTask(
                        name=row[0], action=row[1], interval_m=row[2],
                        cron=row[3], enabled=bool(row[4]), last_run=row[5],
                        run_count=row[6], last_result=row[7], last_error=row[8],
                        payload=json.loads(row[9]) if row[9] else {}
                    )
                    t.next_run = self._calc_next(t)
                    self._tasks[t.name] = t
        except Exception as e:
            log.warning("Load tasks: %s", e)

    def _save_task(self, t: ScheduledTask) -> None:
        try:
            with get_conn_ctx(self.db_path) as c:
                c.execute("""INSERT OR REPLACE INTO tasks VALUES (?,?,?,?,?,?,?,?,?,?)""",
                          (t.name, t.action, t.interval_m, t.cron, int(t.enabled),
                           t.last_run, t.run_count, t.last_result, t.last_error,
                           json.dumps(t.payload)))
        except Exception as e:
            log.warning("Save task %s: %s", t.name, e)

    def _record_run(self, name: str, started: float, result: str, error: str) -> None:
        finished = time.time()
        try:
            with get_conn_ctx(self.db_path) as c:
                c.execute("INSERT INTO runs (task_name,started_at,finished_at,result,error,duration_s) "
                          "VALUES (?,?,?,?,?,?)",
                          (name, started, finished, result[:500], error[:500], finished - started))
        except Exception:
            pass  # error no crítico, continuar
    def _register_builtin_actions(self) -> None:
        """Register built-in action handlers."""
        self._custom_handlers["health"] = self._action_health
        self._custom_handlers["net_scan"] = self._action_net_scan
        self._custom_handlers["log_scan"] = self._action_log_scan
        self._custom_handlers["anomaly_check"] = self._action_anomaly
        self._custom_handlers["service_check"] = self._action_service_check
        self._custom_handlers["report"] = self._action_report

    # ── Built-in Actions ──────────────────────────────────────────────────

    @staticmethod
    def _action_health() -> str:
        from core.system_health import get_health_monitor
        hm = get_health_monitor()
        return hm.get_summary_line()

    @staticmethod
    def _action_net_scan() -> str:
        from core.network_discovery import get_network_discovery
        nd = get_network_discovery()
        results = nd.scan_network()
        return f"Found {len(results)} hosts"

    @staticmethod
    def _action_log_scan() -> str:
        from core.log_watcher import get_log_watcher
        lw = get_log_watcher()
        findings = lw.scan(max_lines=500)
        return f"Scanned: {len(findings)} findings"

    @staticmethod
    def _action_anomaly() -> str:
        from core.anomaly_detector import get_anomaly_detector
        ad = get_anomaly_detector()
        ad.record()
        anomalies = ad.check()
        return f"Anomalies: {len(anomalies)}"

    @staticmethod
    def _action_service_check() -> str:
        from core.service_monitor import get_service_monitor
        sm = get_service_monitor()
        statuses = sm.check_all()
        active = sum(1 for s in statuses if s.active)
        return f"Services: {active}/{len(statuses)} active"

    @staticmethod
    def _action_report() -> str:
        lines = []
        try:
            from core.system_health import get_health_monitor
            lines.append(f"Health: {get_health_monitor().get_summary_line()}")
        except Exception:
            pass  # error no crítico, continuar
        try:
            from core.alert_manager import get_alert_manager
            s = get_alert_manager().get_stats()
            lines.append(f"Alerts: {s['total']} total, {s['unacknowledged']} unacked")
        except Exception:
            pass  # error no crítico, continuar
        try:
            from core.service_monitor import get_service_monitor
            s = get_service_monitor().stats
            lines.append(f"Services: {s['active']}/{s['total']} active")
        except Exception:
            pass  # error no crítico, continuar
        return " | ".join(lines) if lines else "No data"

    # ── Core Methods ──────────────────────────────────────────────────────

    def add(self, name: str, action: str, interval_m: float = 0,
            cron: str = "", payload: dict | None = None) -> ScheduledTask:
        """Add or update a scheduled task."""
        t = ScheduledTask(
            name=name, action=action, interval_m=interval_m,
            cron=cron, payload=payload or {}
        )
        t.next_run = self._calc_next(t)
        self._tasks[name] = t
        self._save_task(t)
        log.info("Task added: %s (action=%s, interval=%sm)", name, action, interval_m)
        return t

    def remove(self, name: str) -> bool:
        if name in self._tasks:
            del self._tasks[name]
            try:
                with get_conn_ctx(self.db_path) as c:
                    c.execute("DELETE FROM tasks WHERE name=?", (name,))
            except Exception:
                pass  # error no crítico, continuar
            return True
        return False

    def enable(self, name: str) -> bool:
        t = self._tasks.get(name)
        if t:
            t.enabled = True
            self._save_task(t)
            return True
        return False

    def disable(self, name: str) -> bool:
        t = self._tasks.get(name)
        if t:
            t.enabled = False
            self._save_task(t)
            return True
        return False

    def register_handler(self, action: str, handler: Callable) -> None:
        """Register a custom action handler."""
        self._custom_handlers[action] = handler

    def run_task(self, name: str) -> str:
        """Run a task immediately."""
        t = self._tasks.get(name)
        if not t:
            return f"Task not found: {name}"
        return self._execute(t)

    def _execute(self, t: ScheduledTask) -> str:
        """Execute a single task."""
        started = time.time()
        handler = self._custom_handlers.get(t.action)
        if not handler:
            error = f"Unknown action: {t.action}"
            t.last_error = error
            self._save_task(t)
            return error

        try:
            result = handler(**t.payload) if t.payload else handler()
            result_str = str(result)[:500]
            t.last_run = time.time()
            t.run_count += 1
            t.last_result = result_str
            t.last_error = ""
            t.next_run = self._calc_next(t)
            self._save_task(t)
            self._record_run(t.name, started, result_str, "")
            log.info("Task %s executed: %s", t.name, result_str[:100])
            return result_str
        except Exception as e:
            error = str(e)
            t.last_error = error
            t.last_run = time.time()
            t.next_run = self._calc_next(t)
            self._save_task(t)
            self._record_run(t.name, started, "", error)
            log.error("Task %s failed: %s", t.name, error)
            return f"ERROR: {error}"

    def _calc_next(self, t: ScheduledTask) -> float:
        """Calculate next run time."""
        if t.interval_m > 0:
            base = t.last_run if t.last_run else time.time()
            return base + (t.interval_m * 60)
        return time.time() + 3600  # Default: 1h if no interval/cron

    # ── Background Daemon ────────────────────────────────────────────────

    def start(self, check_interval: float = 30.0) -> None:
        """Start the background scheduler."""
        if self._running:
            return
        self._running = True
        self._thread = threading.Thread(
            target=self._loop, args=(check_interval,),
            name="eidos-scheduler", daemon=True
        )
        self._thread.start()
        log.info("Scheduler started (%d tasks, check every %.0fs)",
                 len(self._tasks), check_interval)

    def stop(self) -> None:
        self._running = False
        if self._thread:
            self._thread.join(timeout=10)
        self._thread = None
        log.info("Scheduler stopped")

    def _loop(self, check_interval: float) -> None:
        while self._running:
            now = time.time()
            for t in list(self._tasks.values()):
                if t.enabled and now >= t.next_run:
                    try:
                        self._execute(t)
                    except Exception as e:
                        log.error("Scheduler loop error for %s: %s", t.name, e)
            time.sleep(check_interval)

    # ── Status ───────────────────────────────────────────────────────────

    def list_tasks(self) -> list[dict]:
        now = time.time()
        result = []
        for t in sorted(self._tasks.values(), key=lambda x: x.name):
            next_in = max(0, t.next_run - now) if t.next_run else 0
            result.append({
                "name": t.name, "action": t.action,
                "interval_m": t.interval_m, "enabled": t.enabled,
                "run_count": t.run_count,
                "last_result": t.last_result[:80],
                "last_error": t.last_error[:80] if t.last_error else "",
                "next_in_m": round(next_in / 60, 1),
            })
        return result

    @property
    def stats(self) -> dict:
        enabled = sum(1 for t in self._tasks.values() if t.enabled)
        total_runs = sum(t.run_count for t in self._tasks.values())
        return {
            "total_tasks": len(self._tasks),
            "enabled": enabled,
            "total_runs": total_runs,
            "daemon_running": self._running,
            "actions": sorted(self._custom_handlers.keys()),
        }

    def get_runs(self, task_name: str = "", limit: int = 20) -> list[dict]:
        try:
            with get_conn_ctx(self.db_path) as c:
                if task_name:
                    rows = c.execute(
                        "SELECT task_name,started_at,duration_s,result,error FROM runs "
                        "WHERE task_name=? ORDER BY started_at DESC LIMIT ?",
                        (task_name, limit)).fetchall()
                else:
                    rows = c.execute(
                        "SELECT task_name,started_at,duration_s,result,error FROM runs "
                        "ORDER BY started_at DESC LIMIT ?", (limit,)).fetchall()
                return [{"task": r[0], "ts": r[1], "duration": r[2],
                         "result": r[3][:80], "error": r[4][:80]} for r in rows]
        except Exception:
            return []


# ══════════════════════════════════════════════════════════════════════════════
#  SINGLETON
# ══════════════════════════════════════════════════════════════════════════════

_scheduler: Optional[TaskScheduler] = None


def get_scheduler() -> TaskScheduler:
    global _scheduler
    if _scheduler is None:
        _scheduler = TaskScheduler()
    return _scheduler


# ── Default tasks ────────────────────────────────────────────────────────────

def setup_default_tasks() -> None:
    """Create default scheduled tasks if none exist."""
    sched = get_scheduler()
    if not sched.list_tasks():
        sched.add("health_check", action="health", interval_m=5)
        sched.add("log_scan", action="log_scan", interval_m=15)
        sched.add("anomaly_check", action="anomaly_check", interval_m=10)
        sched.add("service_check", action="service_check", interval_m=5)
        sched.add("daily_report", action="report", interval_m=1440)  # 24h
        log.info("Default tasks created")


# ── CLI test ──────────────────────────────────────────────────────────────────
if __name__ == "__main__":
    sched = get_scheduler()
    setup_default_tasks()
    print("Task Scheduler")
    print(f"  Stats: {sched.stats}")
    for t in sched.list_tasks():
        print(f"  [{t['action']}] {t['name']}: every {t['interval_m']}m, runs={t['run_count']}, "
              f"next in {t['next_in_m']}m")

    # Run health check immediately
    result = sched.run_task("health_check")
    print(f"\n  health_check result: {result}")

    result = sched.run_task("service_check")
    print(f"  service_check result: {result}")

    print(f"\n  Final stats: {sched.stats}")
    print("OK")
