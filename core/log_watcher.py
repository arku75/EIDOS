"""
EIDOS core/log_watcher.py — Autonomous Log Monitor
====================================================
Monitorea archivos de log del sistema en busca de anomalías,
eventos de seguridad, y patrones sospechosos.

Integra con AutonomousCore para reportar hallazgos.

Uso:
    from core.log_watcher import get_log_watcher
    watcher = get_log_watcher()
    findings = watcher.scan()
    # or continuous:
    watcher.start()  # background thread
"""
from __future__ import annotations

import logging
import os
import re
import sqlite3
import threading
import time
from dataclasses import dataclass, field
from typing import Optional
from core.db import get_conn
from core.db import get_conn_ctx

log = logging.getLogger("eidos.logwatch")

DB_PATH = os.path.expanduser("~/.eidos/log_watcher.db")

# Log files to monitor
LOG_SOURCES = [
    "/var/log/syslog",
    "/var/log/auth.log",
    "/var/log/kern.log",
    "/var/log/dpkg.log",
    "/var/log/ufw.log",
]

# Patterns that indicate security-relevant events
SECURITY_PATTERNS = {
    "ssh_brute": {
        "pattern": r"Failed password for .+ from (\S+)",
        "severity": "high",
        "description": "SSH brute force attempt",
    },
    "ssh_success": {
        "pattern": r"Accepted (password|publickey) for (\S+) from (\S+)",
        "severity": "info",
        "description": "SSH login success",
    },
    "sudo_fail": {
        "pattern": r"sudo:\s+\S+ : .+ ; TTY=.+ ; PWD=.+ ; USER=.+ ; COMMAND=",
        "severity": "medium",
        "description": "Sudo command execution",
    },
    "kernel_panic": {
        "pattern": r"Kernel panic|BUG:|Oops:",
        "severity": "critical",
        "description": "Kernel error",
    },
    "oom_kill": {
        "pattern": r"Out of memory:|oom-kill|Killed process",
        "severity": "high",
        "description": "OOM killer activated",
    },
    "firewall_block": {
        "pattern": r"\[UFW BLOCK\]|DENIED|DROP",
        "severity": "medium",
        "description": "Firewall blocked connection",
    },
    "service_fail": {
        "pattern": r"(Failed to start|Stopped|failed with result)",
        "severity": "medium",
        "description": "Service failure",
    },
    "disk_error": {
        "pattern": r"I/O error|Read-only file system|No space left",
        "severity": "high",
        "description": "Disk error",
    },
    "new_user": {
        "pattern": r"new user:|useradd|adduser",
        "severity": "medium",
        "description": "New user account created",
    },
    "package_install": {
        "pattern": r"status installed|install .+",
        "severity": "info",
        "description": "Package installation",
    },
    "port_scan": {
        "pattern": r"SYN flood|port scan|nmap",
        "severity": "high",
        "description": "Possible port scan detected",
    },
    "priv_escalation": {
        "pattern": r"setuid|setgid|capability|chmod \+s",
        "severity": "high",
        "description": "Privilege escalation indicator",
    },
}


@dataclass
class LogFinding:
    """A security-relevant log finding."""
    source: str
    pattern_name: str
    severity: str
    description: str
    line: str
    timestamp: float = field(default_factory=time.time)
    ip: str = ""


class LogWatcher:
    """Monitors system logs for security events and anomalies."""

    def __init__(self, db_path: str = DB_PATH):
        self.db_path = db_path
        self._offsets: dict[str, int] = {}  # file -> last read position
        self._running = False
        self._thread: Optional[threading.Thread] = None
        self._compiled = {
            name: re.compile(p["pattern"], re.IGNORECASE)
            for name, p in SECURITY_PATTERNS.items()
        }
        os.makedirs(os.path.dirname(db_path), exist_ok=True)
        self._init_db()
        self._load_offsets()

    def _init_db(self) -> None:
        with get_conn_ctx(self.db_path) as c:
            c.execute("""
                CREATE TABLE IF NOT EXISTS findings (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    source TEXT,
                    pattern_name TEXT,
                    severity TEXT,
                    description TEXT,
                    line TEXT,
                    ip TEXT DEFAULT '',
                    timestamp REAL,
                    acknowledged INTEGER DEFAULT 0
                )
            """)
            c.execute("""
                CREATE TABLE IF NOT EXISTS offsets (
                    source TEXT PRIMARY KEY,
                    offset INTEGER DEFAULT 0
                )
            """)

    def _load_offsets(self) -> None:
        try:
            with get_conn_ctx(self.db_path) as c:
                rows = c.execute("SELECT source, offset FROM offsets").fetchall()
                self._offsets = {r[0]: r[1] for r in rows}
        except Exception:
            pass  # error no crítico, continuar
    def _save_offset(self, source: str, offset: int) -> None:
        self._offsets[source] = offset
        try:
            with get_conn_ctx(self.db_path) as c:
                c.execute(
                    "INSERT OR REPLACE INTO offsets (source, offset) VALUES (?, ?)",
                    (source, offset)
                )
        except Exception:
            pass  # error no crítico, continuar
    def scan(self, max_lines: int = 500) -> list[LogFinding]:
        """Scan all log sources for new security events. Returns findings."""
        all_findings = []
        for source in LOG_SOURCES:
            try:
                findings = self._scan_file(source, max_lines)
                all_findings.extend(findings)
            except Exception as e:
                log.debug("Cannot scan %s: %s", source, e)
        # Store findings
        for f in all_findings:
            self._store_finding(f)
        return all_findings

    def _scan_file(self, path: str, max_lines: int) -> list[LogFinding]:
        """Scan a single log file from last known offset."""
        if not os.path.isfile(path):
            return []
        if not os.access(path, os.R_OK):
            return []

        findings = []
        offset = self._offsets.get(path, 0)

        # Check if file was rotated (smaller than offset)
        file_size = os.path.getsize(path)
        if file_size < offset:
            offset = 0

        with open(path, "r", errors="replace") as f:
            f.seek(offset)
            lines_read = 0
            for line in f:
                lines_read += 1
                if lines_read > max_lines:
                    break
                line = line.strip()
                if not line:
                    continue
                # Check against all patterns
                for name, compiled in self._compiled.items():
                    match = compiled.search(line)
                    if match:
                        pinfo = SECURITY_PATTERNS[name]
                        ip = ""
                        # Try to extract IP from match groups
                        for g in match.groups():
                            if g and re.match(r'\d+\.\d+\.\d+\.\d+', g):
                                ip = g
                                break
                        findings.append(LogFinding(
                            source=path,
                            pattern_name=name,
                            severity=pinfo["severity"],
                            description=pinfo["description"],
                            line=line[:300],
                            ip=ip,
                        ))
            new_offset = f.tell()

        self._save_offset(path, new_offset)
        return findings

    def _store_finding(self, f: LogFinding) -> None:
        try:
            with get_conn_ctx(self.db_path) as c:
                c.execute(
                    "INSERT INTO findings (source, pattern_name, severity, description, line, ip, timestamp) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?)",
                    (f.source, f.pattern_name, f.severity, f.description, f.line, f.ip, f.timestamp)
                )
        except Exception:
            pass  # error no crítico, continuar
    def get_recent(self, n: int = 20, severity: str = "") -> list[dict]:
        """Get recent findings, optionally filtered by severity."""
        try:
            with get_conn_ctx(self.db_path) as c:
                if severity:
                    rows = c.execute(
                        "SELECT source, pattern_name, severity, description, line, ip, timestamp "
                        "FROM findings WHERE severity = ? ORDER BY timestamp DESC LIMIT ?",
                        (severity, n)
                    ).fetchall()
                else:
                    rows = c.execute(
                        "SELECT source, pattern_name, severity, description, line, ip, timestamp "
                        "FROM findings ORDER BY timestamp DESC LIMIT ?",
                        (n,)
                    ).fetchall()
            return [
                {"source": r[0], "pattern": r[1], "severity": r[2],
                 "description": r[3], "line": r[4], "ip": r[5], "ts": r[6]}
                for r in rows
            ]
        except Exception:
            return []

    def get_summary(self) -> dict:
        """Get summary stats of findings."""
        try:
            with get_conn_ctx(self.db_path) as c:
                total = c.execute("SELECT COUNT(*) FROM findings").fetchone()[0]
                by_severity = {}
                for row in c.execute(
                    "SELECT severity, COUNT(*) FROM findings GROUP BY severity"
                ).fetchall():
                    by_severity[row[0]] = row[1]
                # Last 24h
                cutoff = time.time() - 86400
                recent = c.execute(
                    "SELECT COUNT(*) FROM findings WHERE timestamp > ?",
                    (cutoff,)
                ).fetchone()[0]
                # Unique IPs
                ips = c.execute(
                    "SELECT DISTINCT ip FROM findings WHERE ip != '' ORDER BY timestamp DESC LIMIT 20"
                ).fetchall()
            return {
                "total_findings": total,
                "by_severity": by_severity,
                "last_24h": recent,
                "unique_ips": [r[0] for r in ips],
            }
        except Exception:
            return {"total_findings": 0, "by_severity": {}, "last_24h": 0, "unique_ips": []}

    def start(self, interval: int = 300) -> None:
        """Start background log monitoring thread."""
        if self._running:
            return
        self._running = True

        def _loop():
            while self._running:
                try:
                    findings = self.scan()
                    if findings:
                        high = [f for f in findings if f.severity in ("high", "critical")]
                        if high:
                            log.warning("LogWatcher: %d high/critical findings", len(high))
                            # Route through AlertManager
                            try:
                                from core.alert_manager import get_alert_manager
                                am = get_alert_manager()
                                for f in high[:5]:
                                    am.alert(f.severity, f.description,
                                             f.line[:200], source="logwatch", ip=f.ip)
                            except Exception:
                                pass  # error no crítico, continuar
                            # Also notify AutonomousCore
                            try:
                                from core.autonomous import get_autonomous
                                auto = get_autonomous()
                                auto._record_thought(
                                    f"[logwatch] {len(findings)} findings ({len(high)} high): "
                                    + "; ".join(f.description for f in high[:3]),
                                    led=True,
                                )
                            except Exception:
                                pass  # error no crítico, continuar
                except Exception as e:
                    log.debug("LogWatcher scan error: %s", e)
                time.sleep(interval)

        self._thread = threading.Thread(target=_loop, daemon=True, name="eidos-logwatch")
        self._thread.start()
        log.info("LogWatcher started (interval=%ds)", interval)

    def stop(self) -> None:
        self._running = False

    @property
    def stats(self) -> dict:
        s = self.get_summary()
        s["running"] = self._running
        s["sources"] = len([p for p in LOG_SOURCES if os.path.isfile(p)])
        s["patterns"] = len(SECURITY_PATTERNS)
        return s


# ══════════════════════════════════════════════════════════════════════════════
#  SINGLETON
# ══════════════════════════════════════════════════════════════════════════════

_watcher: Optional[LogWatcher] = None


def get_log_watcher() -> LogWatcher:
    global _watcher
    if _watcher is None:
        _watcher = LogWatcher()
    return _watcher


# ── CLI test ──────────────────────────────────────────────────────────────────
if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    w = get_log_watcher()
    print(f"LogWatcher — {w.stats['sources']} sources, {w.stats['patterns']} patterns")
    print(f"Scanning...")
    findings = w.scan()
    print(f"Found {len(findings)} events")
    for f in findings[:10]:
        print(f"  [{f.severity}] {f.description} — {f.line[:80]}")
    print(f"\nSummary: {w.get_summary()}")
