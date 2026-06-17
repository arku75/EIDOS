"""
EIDOS core/service_monitor.py — Systemd Service Monitor
========================================================
Monitors systemd services and daemons: uptime, restart counts, memory,
auto-restart for criticals, SQLite persistence, AlertManager integration.

Uso:
    from core.service_monitor import get_service_monitor
    sm = get_service_monitor()
    sm.start()                   # background daemon cada 60s
    statuses = sm.check_all()    # snapshot inmediato
    sm.restart_service("docker") # reinicio manual
"""
from __future__ import annotations

import logging
import os
import sqlite3
import subprocess
import threading
import time
from dataclasses import dataclass
from typing import Optional
from core.db import get_conn
from core.db import get_conn_ctx

log = logging.getLogger("eidos.service_monitor")

DB_PATH = os.path.expanduser("~/.eidos/service_monitor.db")

# Logical name -> systemd unit
DEFAULT_SERVICES: dict[str, str] = {
    "eidos-learning": "eidos-learning.service",
    "eidos-ram-guardian": "eidos-ram-guardian.service",
    "docker": "docker.service",
    "ollama": "ollama.service",
    "sshd": "sshd.service",
}
DEFAULT_CRITICAL: set[str] = {"docker", "ollama", "sshd"}


@dataclass
class ServiceStatus:
    """Snapshot of a single service state."""
    name: str
    active: bool
    state: str       # active, inactive, failed, unknown
    sub_state: str   # running, dead, exited ...
    pid: int
    memory_mb: float
    uptime_s: float
    restarts: int


class ServiceMonitor:
    """Monitors systemd services, persists history, auto-restarts criticals."""

    def __init__(self, services: Optional[dict[str, str]] = None,
                 critical: Optional[set[str]] = None,
                 db_path: str = DB_PATH, auto_restart: bool = True):
        self.services = dict(services or DEFAULT_SERVICES)
        self.critical = set(critical or DEFAULT_CRITICAL)
        self.auto_restart = auto_restart
        self.db_path = db_path
        self._lock = threading.Lock()
        self._running = False
        self._thread: Optional[threading.Thread] = None
        self._last_check: float = 0.0
        self._cache: list[ServiceStatus] = []
        self._restart_counts: dict[str, int] = {}
        os.makedirs(os.path.dirname(self.db_path), exist_ok=True)
        self._init_db()

    # ── Database ──────────────────────────────────────────────────────────

    def _init_db(self) -> None:
        with get_conn_ctx(self.db_path) as c:
            c.execute("""CREATE TABLE IF NOT EXISTS services (
                name TEXT PRIMARY KEY, unit TEXT,
                last_state TEXT DEFAULT 'unknown', last_pid INTEGER DEFAULT 0,
                last_memory_mb REAL DEFAULT 0, total_restarts INTEGER DEFAULT 0,
                first_seen REAL, last_seen REAL)""")
            c.execute("""CREATE TABLE IF NOT EXISTS events (
                id INTEGER PRIMARY KEY AUTOINCREMENT, service TEXT, event TEXT,
                old_state TEXT DEFAULT '', new_state TEXT DEFAULT '',
                detail TEXT DEFAULT '', timestamp REAL)""")
            c.execute("""CREATE INDEX IF NOT EXISTS idx_events_svc
                ON events (service, timestamp DESC)""")

    def _db_upsert(self, ss: ServiceStatus) -> None:
        now = time.time()
        try:
            with get_conn_ctx(self.db_path) as c:
                c.execute("""INSERT INTO services
                    (name,unit,last_state,last_pid,last_memory_mb,total_restarts,first_seen,last_seen)
                    VALUES (?,?,?,?,?,?,?,?)
                    ON CONFLICT(name) DO UPDATE SET last_state=excluded.last_state,
                    last_pid=excluded.last_pid, last_memory_mb=excluded.last_memory_mb,
                    total_restarts=excluded.total_restarts, last_seen=excluded.last_seen""",
                    (ss.name, self.services.get(ss.name, ""), ss.state,
                     ss.pid, ss.memory_mb, ss.restarts, now, now))
        except Exception as e:
            log.warning("DB upsert %s: %s", ss.name, e)

    def _db_event(self, service: str, event: str, old: str = "",
                  new: str = "", detail: str = "") -> None:
        try:
            with get_conn_ctx(self.db_path) as c:
                c.execute("INSERT INTO events (service,event,old_state,new_state,"
                          "detail,timestamp) VALUES (?,?,?,?,?,?)",
                          (service, event, old, new, detail, time.time()))
        except Exception as e:
            log.warning("DB event: %s", e)

    # ── Systemctl helpers ─────────────────────────────────────────────────

    @staticmethod
    def _run(cmd: list[str], timeout: float = 10.0) -> str:
        try:
            r = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
            return r.stdout.strip()
        except (subprocess.TimeoutExpired, FileNotFoundError, OSError):
            return ""

    def _query_service(self, name: str) -> ServiceStatus:
        unit = self.services.get(name, f"{name}.service")
        is_active = self._run(["systemctl", "is-active", unit])
        active = is_active == "active"

        props: dict[str, str] = {}
        prop_flag = "--property=ActiveState,SubState,MainPID,MemoryCurrent"
        for line in self._run(
                ["systemctl", "show", unit, prop_flag]).splitlines():
            if "=" in line:
                k, _, v = line.partition("=")
                props[k.strip()] = v.strip()

        state = props.get("ActiveState", is_active or "unknown")
        sub_state = props.get("SubState", "unknown")
        try:
            pid = int(props.get("MainPID", "0"))
        except ValueError:
            pid = 0

        mem_mb = 0.0
        mem_raw = props.get("MemoryCurrent", "")
        if mem_raw.isdigit():
            mem_mb = round(int(mem_raw) / (1024 * 1024), 2)

        uptime_s = 0.0
        if active:
            uptime_s = self._parse_uptime(self._run(
                ["systemctl", "show", unit, "--property=ActiveEnterTimestamp"]))

        restarts = self._restart_counts.get(name, 0)
        try:
            with get_conn_ctx(self.db_path) as c:
                row = c.execute("SELECT total_restarts FROM services WHERE name=?",
                                (name,)).fetchone()
                if row:
                    restarts = max(restarts, row[0])
        except Exception:
            pass  # error no crítico, continuar
        return ServiceStatus(name=name, active=active, state=state,
                             sub_state=sub_state, pid=pid, memory_mb=mem_mb,
                             uptime_s=uptime_s, restarts=restarts)

    @staticmethod
    def _parse_uptime(prop_line: str) -> float:
        if "=" not in prop_line:
            return 0.0
        _, _, ts_str = prop_line.partition("=")
        ts_str = ts_str.strip()
        if not ts_str or ts_str == "n/a":
            return 0.0
        try:
            from datetime import datetime
            parts = ts_str.split()
            if len(parts) >= 3:
                dt_str = f"{parts[1]} {parts[2]}" if len(parts) >= 4 else ts_str
                dt = datetime.strptime(dt_str, "%Y-%m-%d %H:%M:%S")
                return max(0.0, (datetime.now() - dt).total_seconds())
        except Exception:
            pass  # error no crítico, continuar
        return 0.0

    # ── Core methods ──────────────────────────────────────────────────────

    def check_all(self) -> list[ServiceStatus]:
        """Check all monitored services. Returns list of ServiceStatus."""
        results: list[ServiceStatus] = []
        prev: dict[str, str] = {}
        with self._lock:
            prev = {s.name: s.state for s in self._cache}

        for name in self.services:
            ss = self._query_service(name)
            results.append(ss)
            old = prev.get(name)
            if old and old != ss.state:
                self._on_state_change(name, old, ss.state)
            self._db_upsert(ss)

        with self._lock:
            self._cache = results
            self._last_check = time.time()
        return results

    def get_service(self, name: str) -> ServiceStatus:
        """Get status for a single service by logical name."""
        with self._lock:
            for ss in self._cache:
                if ss.name == name:
                    return ss
        if name not in self.services:
            return ServiceStatus(name=name, active=False, state="unknown",
                                 sub_state="unknown", pid=0, memory_mb=0.0,
                                 uptime_s=0.0, restarts=0)
        return self._query_service(name)

    def restart_service(self, name: str) -> bool:
        """Restart a service via systemctl. Returns True on success."""
        unit = self.services.get(name, f"{name}.service")
        log.info("Restarting %s (%s)", name, unit)
        self._run(["sudo", "systemctl", "restart", unit], timeout=30.0)
        time.sleep(1)
        ss = self._query_service(name)
        self._restart_counts[name] = self._restart_counts.get(name, 0) + 1
        ev = "restart_ok" if ss.active else "restart_fail"
        self._db_event(name, ev, new=ss.state, detail=f"pid={ss.pid}")
        if ss.active:
            log.info("Service %s restarted OK (PID %d)", name, ss.pid)
        else:
            log.error("Service %s restart FAILED (state=%s)", name, ss.state)
        return ss.active

    # ── State change / alerts ─────────────────────────────────────────────

    def _on_state_change(self, name: str, old: str, new: str) -> None:
        log.warning("Service %s: %s -> %s", name, old, new)
        self._db_event(name, "state_change", old=old, new=new)
        went_down = new in ("inactive", "failed")
        if went_down:
            sev = "high" if name in self.critical else "medium"
            self._alert(sev, f"Service DOWN: {name}",
                        f"{name} transitioned {old} -> {new}")
            if name in self.critical and self.auto_restart:
                log.warning("Auto-restarting critical service: %s", name)
                self._alert("info", f"Auto-restarting: {name}", "Attempting recovery")
                if not self.restart_service(name):
                    self._alert("critical", f"Auto-restart FAILED: {name}",
                                "Manual intervention required")
        elif new == "active" and old in ("inactive", "failed"):
            self._alert("info", f"Service UP: {name}", f"Recovered from {old}")

    @staticmethod
    def _alert(severity: str, title: str, detail: str) -> None:
        try:
            from core.alert_manager import get_alert_manager
            get_alert_manager().alert(severity, title, detail,
                                      source="service_monitor")
        except Exception:
            pass  # error no crítico, continuar
    # ── Background daemon ─────────────────────────────────────────────────

    def start(self, interval: float = 60.0) -> None:
        """Start background monitoring daemon thread."""
        if self._running:
            return
        self._running = True
        self._thread = threading.Thread(target=self._loop, args=(interval,),
                                        name="eidos-svc-mon", daemon=True)
        self._thread.start()
        log.info("ServiceMonitor started (interval=%.0fs)", interval)

    def stop(self) -> None:
        """Stop background daemon."""
        if not self._running:
            return
        self._running = False
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=10.0)
        self._thread = None
        log.info("ServiceMonitor stopped")

    def _loop(self, interval: float) -> None:
        try:
            self.check_all()
        except Exception as e:
            log.error("Initial check: %s", e)
        while self._running:
            try:
                time.sleep(interval)
                if self._running:
                    self.check_all()
            except Exception as e:
                log.error("Loop error: %s", e)
                time.sleep(5)

    # ── Stats & helpers ───────────────────────────────────────────────────

    @property
    def stats(self) -> dict:
        """Summary statistics across all monitored services."""
        with self._lock:
            cached = list(self._cache)
        active = sum(1 for s in cached if s.active)
        failed = [s.name for s in cached if s.state == "failed"]
        down = [s.name for s in cached if not s.active and s.state != "failed"]
        events_24h = 0
        try:
            with get_conn_ctx(self.db_path) as c:
                r = c.execute("SELECT COUNT(*) FROM events WHERE timestamp>?",
                              (time.time() - 86400,)).fetchone()
                events_24h = r[0] if r else 0
        except Exception:
            pass  # error no crítico, continuar
        return {"total": len(self.services), "active": active,
                "failed": len(failed), "failed_names": failed,
                "inactive": len(down), "inactive_names": down,
                "memory_mb": round(sum(s.memory_mb for s in cached), 2),
                "restarts": sum(s.restarts for s in cached),
                "events_24h": events_24h, "last_check": self._last_check,
                "daemon_running": self._running}

    def get_events(self, service: str = "", limit: int = 50) -> list[dict]:
        """Recent events, optionally filtered by service name."""
        try:
            with get_conn_ctx(self.db_path) as c:
                q = ("SELECT service,event,old_state,new_state,detail,timestamp "
                     "FROM events")
                p: list = []
                if service:
                    q += " WHERE service=?"
                    p.append(service)
                q += " ORDER BY timestamp DESC LIMIT ?"
                p.append(limit)
                return [{"service": r[0], "event": r[1], "old": r[2],
                         "new": r[3], "detail": r[4], "ts": r[5]}
                        for r in c.execute(q, p).fetchall()]
        except Exception:
            return []

    def add_service(self, name: str, unit: Optional[str] = None,
                    critical: bool = False) -> None:
        self.services[name] = unit or f"{name}.service"
        if critical:
            self.critical.add(name)

    def remove_service(self, name: str) -> None:
        self.services.pop(name, None)
        self.critical.discard(name)


# ── Singleton ─────────────────────────────────────────────────────────────
_instance: Optional[ServiceMonitor] = None

def get_service_monitor() -> ServiceMonitor:
    global _instance
    if _instance is None:
        _instance = ServiceMonitor()
    return _instance


# ── CLI test ──────────────────────────────────────────────────────────────
if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO,
                        format="%(asctime)s [%(name)s] %(levelname)s: %(message)s")
    sm = get_service_monitor()
    print("=" * 64)
    print("  EIDOS Service Monitor")
    print("=" * 64)
    for s in sm.check_all():
        tag = "[OK]" if s.active else "[--]"
        mem = f"{s.memory_mb:.1f}MB" if s.memory_mb else "n/a"
        up = f"{s.uptime_s/3600:.1f}h" if s.uptime_s else "down"
        pid = str(s.pid) if s.pid else "-"
        print(f"  {tag} {s.name:<22} {s.state:<10} {s.sub_state:<10} "
              f"pid={pid:<8} mem={mem:<10} up={up:<8} restarts={s.restarts}")
    print()
    st = sm.stats
    print(f"  Active: {st['active']}/{st['total']}  Mem: {st['memory_mb']:.1f}MB  "
          f"Events24h: {st['events_24h']}  Restarts: {st['restarts']}")
    if st["failed_names"]:
        print(f"  FAILED: {', '.join(st['failed_names'])}")
    if st["inactive_names"]:
        print(f"  Inactive: {', '.join(st['inactive_names'])}")
    evts = sm.get_events(limit=10)
    if evts:
        print("\n  Recent events:")
        for e in evts:
            t = time.strftime("%H:%M:%S", time.localtime(e["ts"]))
            print(f"    {t} [{e['service']}] {e['event']} {e['old']}->{e['new']}")
    print("=" * 64)
