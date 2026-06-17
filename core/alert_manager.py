"""
EIDOS core/alert_manager.py — Unified Alert Pipeline
=====================================================
Centraliza alertas de todos los subsistemas de EIDOS:
- SystemHealth (CPU, RAM, disk, Ollama)
- LogWatcher (security events)
- NetworkDiscovery (new hosts, changes)
- AutonomousCore (autonomous findings)

Enruta alertas a múltiples destinos:
- Console (Rich panel)
- Desktop notification (notify-send)
- Telegram (si configurado)
- Dashboard WebSocket
- Brain Memory (para aprendizaje)

Uso:
    from core.alert_manager import get_alert_manager
    am = get_alert_manager()
    am.alert("high", "New host detected", "192.168.1.50 with open ports 22,80")
    am.get_recent()
"""
from __future__ import annotations

import json
import logging
import os
import sqlite3
import subprocess
import threading
import time
import urllib.request
from dataclasses import dataclass, field
from typing import Callable, Optional
from core.db import get_conn
from core.db import get_conn_ctx

log = logging.getLogger("eidos.alerts")

DB_PATH = os.path.expanduser("~/.eidos/alerts.db")
OLLAMA_URL = os.environ.get("OLLAMA_URL", "http://127.0.0.1:11434")


@dataclass
class Alert:
    """A unified alert from any EIDOS subsystem."""
    severity: str      # info, low, medium, high, critical
    title: str
    detail: str = ""
    source: str = ""   # health, logwatch, network, autonomous, user
    ip: str = ""
    timestamp: float = field(default_factory=time.time)
    acknowledged: bool = False
    id: int = 0

    @property
    def icon(self) -> str:
        return {"info": "i", "low": ".", "medium": "!", "high": "!!", "critical": "!!!"}.\
            get(self.severity, "?")

    @property
    def color(self) -> str:
        return {"info": "#00ccff", "low": "#888", "medium": "#ff9900",
                "high": "#e74c3c", "critical": "#ff0000"}.get(self.severity, "#fff")


class AlertManager:
    """Centralized alert management for all EIDOS subsystems."""

    def __init__(self, db_path: str = DB_PATH):
        self.db_path = db_path
        self._callbacks: list[Callable] = []
        self._rate_limit: dict[str, float] = {}  # key -> last_alert_ts
        self._rate_window = 300  # 5 min between duplicate alerts
        os.makedirs(os.path.dirname(db_path), exist_ok=True)
        self._init_db()

    def _init_db(self) -> None:
        with get_conn_ctx(self.db_path) as c:
            c.execute("""
                CREATE TABLE IF NOT EXISTS alerts (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    severity TEXT,
                    title TEXT,
                    detail TEXT DEFAULT '',
                    source TEXT DEFAULT '',
                    ip TEXT DEFAULT '',
                    timestamp REAL,
                    acknowledged INTEGER DEFAULT 0
                )
            """)

    def alert(self, severity: str, title: str, detail: str = "",
              source: str = "", ip: str = "") -> Optional[Alert]:
        """Create and route a new alert. Returns None if rate-limited."""
        # Rate limiting — same title within window
        key = f"{source}:{title}"
        now = time.time()
        if key in self._rate_limit:
            if now - self._rate_limit[key] < self._rate_window:
                return None
        self._rate_limit[key] = now

        a = Alert(severity=severity, title=title, detail=detail,
                  source=source, ip=ip, timestamp=now)

        # Persist
        try:
            with get_conn_ctx(self.db_path) as c:
                cur = c.execute(
                    "INSERT INTO alerts (severity, title, detail, source, ip, timestamp) "
                    "VALUES (?, ?, ?, ?, ?, ?)",
                    (a.severity, a.title, a.detail, a.source, a.ip, a.timestamp)
                )
                a.id = cur.lastrowid
        except Exception as e:
            log.warning("Alert persist failed: %s", e)

        # Route to destinations
        self._route(a)

        return a

    def _route(self, a: Alert) -> None:
        """Send alert to all configured destinations."""
        # 1. Brain Memory — always store
        try:
            from core.brain_memory import get_brain_memory
            bm = get_brain_memory()
            importance = {"info": 0.3, "low": 0.4, "medium": 0.6,
                          "high": 0.8, "critical": 1.0}.get(a.severity, 0.5)
            bm.working.add("alert", f"[{a.severity}/{a.source}] {a.title}: {a.detail}",
                           importance=importance)
        except Exception:
            pass  # error no crítico, continuar
        # 2. Desktop notification — medium and above
        if a.severity in ("medium", "high", "critical"):
            try:
                urgency = "critical" if a.severity == "critical" else "normal"
                subprocess.Popen(
                    ["notify-send", "-u", urgency, "-i", "dialog-warning",
                     f"EIDOS [{a.severity}]", f"{a.title}\n{a.detail[:100]}"],
                    stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                )
            except Exception:
                pass  # error no crítico, continuar
        # 3. Telegram — high and critical only
        if a.severity in ("high", "critical"):
            self._send_telegram(a)

        # 4. Callbacks (dashboard WS, CLI, etc.)
        for cb in self._callbacks:
            try:
                cb(a)
            except Exception:
                pass  # error no crítico, continuar
    def _send_telegram(self, a: Alert) -> None:
        """Send alert to Telegram if configured."""
        try:
            token = os.environ.get("EIDOS_TELEGRAM_TOKEN", "")
            chat_ids = os.environ.get("EIDOS_TELEGRAM_ALLOWED", "")
            if not token or not chat_ids:
                return
            text = f"[{a.icon}] EIDOS Alert [{a.severity.upper()}]\n{a.title}\n{a.detail[:500]}"
            if a.ip:
                text += f"\nIP: {a.ip}"
            for cid in chat_ids.split(","):
                cid = cid.strip()
                if cid.isdigit():
                    data = json.dumps({
                        "chat_id": int(cid),
                        "text": text,
                    }).encode()
                    req = urllib.request.Request(
                        f"https://api.telegram.org/bot{token}/sendMessage",
                        data=data,
                        headers={"Content-Type": "application/json"},
                    )
                    urllib.request.urlopen(req, timeout=5)
        except Exception:
            pass  # error no crítico, continuar
    def register_callback(self, cb: Callable) -> None:
        """Register a callback for new alerts (e.g., dashboard WS broadcast)."""
        self._callbacks.append(cb)

    # ── Queries ───────────────────────────────────────────────────────────

    def get_recent(self, n: int = 20, severity: str = "",
                   source: str = "") -> list[dict]:
        """Get recent alerts with optional filters."""
        try:
            with get_conn_ctx(self.db_path) as c:
                query = "SELECT id, severity, title, detail, source, ip, timestamp, acknowledged FROM alerts"
                params = []
                wheres = []
                if severity:
                    wheres.append("severity = ?")
                    params.append(severity)
                if source:
                    wheres.append("source = ?")
                    params.append(source)
                if wheres:
                    query += " WHERE " + " AND ".join(wheres)
                query += " ORDER BY timestamp DESC LIMIT ?"
                params.append(n)
                rows = c.execute(query, params).fetchall()
                return [
                    {"id": r[0], "severity": r[1], "title": r[2], "detail": r[3],
                     "source": r[4], "ip": r[5], "ts": r[6], "ack": bool(r[7])}
                    for r in rows
                ]
        except Exception:
            return []

    def acknowledge(self, alert_id: int) -> bool:
        """Mark an alert as acknowledged."""
        try:
            with get_conn_ctx(self.db_path) as c:
                c.execute("UPDATE alerts SET acknowledged = 1 WHERE id = ?", (alert_id,))
                return True
        except Exception:
            return False

    def get_stats(self) -> dict:
        """Alert statistics."""
        try:
            with get_conn_ctx(self.db_path) as c:
                total = c.execute("SELECT COUNT(*) FROM alerts").fetchone()[0]
                unacked = c.execute(
                    "SELECT COUNT(*) FROM alerts WHERE acknowledged = 0"
                ).fetchone()[0]
                by_severity = {}
                for row in c.execute(
                    "SELECT severity, COUNT(*) FROM alerts GROUP BY severity"
                ).fetchall():
                    by_severity[row[0]] = row[1]
                last_24h = c.execute(
                    "SELECT COUNT(*) FROM alerts WHERE timestamp > ?",
                    (time.time() - 86400,)
                ).fetchone()[0]
            return {
                "total": total, "unacknowledged": unacked,
                "by_severity": by_severity, "last_24h": last_24h,
            }
        except Exception:
            return {"total": 0, "unacknowledged": 0, "by_severity": {}, "last_24h": 0}

    def cleanup(self, days: int = 30) -> int:
        """Remove alerts older than N days."""
        cutoff = time.time() - (days * 86400)
        try:
            with get_conn_ctx(self.db_path) as c:
                c.execute("DELETE FROM alerts WHERE timestamp < ? AND acknowledged = 1",
                          (cutoff,))
                return c.execute("SELECT changes()").fetchone()[0]
        except Exception:
            return 0


# ══════════════════════════════════════════════════════════════════════════════
#  SINGLETON
# ══════════════════════════════════════════════════════════════════════════════

_manager: Optional[AlertManager] = None


def get_alert_manager() -> AlertManager:
    global _manager
    if _manager is None:
        _manager = AlertManager()
    return _manager


# ── CLI test ──────────────────────────────────────────────────────────────────
if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    am = get_alert_manager()

    # Test alerts
    am.alert("info", "EIDOS started", "All systems nominal", source="system")
    am.alert("medium", "Disk usage 75%", "/home at 75%", source="health")
    am.alert("high", "New host on network", "192.168.1.50 open ports 22,80",
             source="network", ip="192.168.1.50")

    print(f"Stats: {am.get_stats()}")
    print(f"\nRecent alerts:")
    for a in am.get_recent(10):
        print(f"  [{a['severity']}] {a['title']} — {a['detail'][:60]}")
