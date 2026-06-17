"""
EIDOS core/anomaly_detector.py — Behavioral Anomaly Detection
==============================================================
Aprende patrones "normales" del sistema y alerta desviaciones.

Monitores:
- CPU/RAM usage patterns (por hora del día)
- Network connection count
- Process count
- Ollama response times
- Login patterns

Uso:
    from core.anomaly_detector import get_anomaly_detector
    ad = get_anomaly_detector()
    ad.record()  # Record current system state
    anomalies = ad.check()  # Check for anomalies
"""
from __future__ import annotations

import json
import logging
import math
import os
import sqlite3
import subprocess
import time
from dataclasses import dataclass, field
from typing import Optional
from core.db import get_conn
from core.db import get_conn_ctx

log = logging.getLogger("eidos.anomaly")

DB_PATH = os.path.expanduser("~/.eidos/anomaly.db")

# How many standard deviations from mean = anomaly
ANOMALY_THRESHOLD = 2.5
# Minimum samples before we can detect anomalies
MIN_SAMPLES = 10


@dataclass
class AnomalyReport:
    """A detected anomaly."""
    metric: str
    current_value: float
    mean: float
    std_dev: float
    z_score: float
    severity: str  # medium, high
    description: str
    timestamp: float = field(default_factory=time.time)


class AnomalyDetector:
    """Learns normal system behavior and detects anomalies."""

    def __init__(self, db_path: str = DB_PATH):
        self.db_path = db_path
        os.makedirs(os.path.dirname(db_path), exist_ok=True)
        self._init_db()

    def _init_db(self) -> None:
        with get_conn_ctx(self.db_path) as c:
            c.execute("""
                CREATE TABLE IF NOT EXISTS metrics (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    metric TEXT,
                    value REAL,
                    hour INTEGER,
                    timestamp REAL
                )
            """)
            c.execute("""
                CREATE TABLE IF NOT EXISTS anomalies (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    metric TEXT,
                    value REAL,
                    mean REAL,
                    std_dev REAL,
                    z_score REAL,
                    severity TEXT,
                    description TEXT,
                    timestamp REAL
                )
            """)
            c.execute("CREATE INDEX IF NOT EXISTS idx_metrics_metric ON metrics(metric)")

    # ── Data Collection ───────────────────────────────────────────────────

    def record(self) -> dict[str, float]:
        """Record current system metrics. Call periodically."""
        metrics = {}
        hour = time.localtime().tm_hour

        # CPU usage
        try:
            with open("/proc/stat") as f:
                parts = f.readline().split()
            idle = int(parts[4])
            total = sum(int(x) for x in parts[1:])
            # Quick approximation — not perfect but good enough for anomaly detection
            cpu_pct = 100 - (idle / max(total, 1) * 100)
            metrics["cpu_pct"] = round(cpu_pct, 1)
        except Exception:
            pass  # error no crítico, continuar
        # RAM usage
        try:
            with open("/proc/meminfo") as f:
                lines = f.readlines()
            mem = {}
            for line in lines[:5]:
                key, val = line.split(":")
                mem[key.strip()] = int(val.strip().split()[0])
            total_kb = mem.get("MemTotal", 1)
            avail_kb = mem.get("MemAvailable", 0)
            ram_pct = (1 - avail_kb / total_kb) * 100
            metrics["ram_pct"] = round(ram_pct, 1)
        except Exception:
            pass  # error no crítico, continuar
        # Process count
        try:
            result = subprocess.run(
                ["ps", "aux", "--no-headers"],
                capture_output=True, text=True, timeout=5
            )
            metrics["process_count"] = len(result.stdout.strip().split("\n"))
        except Exception:
            pass  # error no crítico, continuar
        # Network connections
        try:
            result = subprocess.run(
                ["ss", "-tun", "--no-header"],
                capture_output=True, text=True, timeout=5
            )
            metrics["net_connections"] = len(result.stdout.strip().split("\n"))
        except Exception:
            pass  # error no crítico, continuar
        # Disk usage /home
        try:
            st = os.statvfs("/home")
            used_pct = (1 - st.f_bavail / max(st.f_blocks, 1)) * 100
            metrics["disk_home_pct"] = round(used_pct, 1)
        except Exception:
            pass  # error no crítico, continuar
        # Active users
        try:
            result = subprocess.run(
                ["who"], capture_output=True, text=True, timeout=5
            )
            metrics["active_users"] = len([l for l in result.stdout.strip().split("\n") if l])
        except Exception:
            pass  # error no crítico, continuar
        # Store all metrics
        try:
            with get_conn_ctx(self.db_path) as c:
                now = time.time()
                for name, value in metrics.items():
                    c.execute(
                        "INSERT INTO metrics (metric, value, hour, timestamp) VALUES (?, ?, ?, ?)",
                        (name, value, hour, now)
                    )
        except Exception as e:
            log.debug("Record metrics failed: %s", e)

        return metrics

    # ── Anomaly Detection ─────────────────────────────────────────────────

    def check(self) -> list[AnomalyReport]:
        """Check current metrics against learned baselines. Returns anomalies."""
        current = self.record()
        anomalies = []
        hour = time.localtime().tm_hour

        for metric, value in current.items():
            stats = self._get_stats(metric, hour)
            if not stats:
                continue

            mean, std_dev, count = stats
            if count < MIN_SAMPLES:
                continue
            if std_dev < 0.01:
                continue  # No variance yet

            z_score = abs(value - mean) / std_dev

            if z_score >= ANOMALY_THRESHOLD:
                severity = "high" if z_score >= 4.0 else "medium"
                direction = "above" if value > mean else "below"
                desc = (f"{metric} is {value:.1f} ({direction} normal "
                        f"{mean:.1f} +/- {std_dev:.1f}, z={z_score:.1f})")

                report = AnomalyReport(
                    metric=metric, current_value=value,
                    mean=mean, std_dev=std_dev, z_score=z_score,
                    severity=severity, description=desc,
                )
                anomalies.append(report)

                # Store anomaly
                self._store_anomaly(report)

                # Send to AlertManager
                try:
                    from core.alert_manager import get_alert_manager
                    am = get_alert_manager()
                    am.alert(severity, f"Anomaly: {metric}",
                             desc, source="anomaly")
                except Exception:
                    pass  # error no crítico, continuar
        return anomalies

    def _get_stats(self, metric: str, hour: int) -> Optional[tuple]:
        """Get mean, std_dev, count for a metric at this hour (+/- 2h window)."""
        try:
            with get_conn_ctx(self.db_path) as c:
                # Use a 5-hour window around current hour for seasonal patterns
                hours = [(hour + i) % 24 for i in range(-2, 3)]
                placeholders = ",".join("?" * len(hours))
                rows = c.execute(
                    f"SELECT value FROM metrics WHERE metric = ? AND hour IN ({placeholders})",
                    (metric, *hours)
                ).fetchall()

                if len(rows) < MIN_SAMPLES:
                    return None

                values = [r[0] for r in rows]
                n = len(values)
                mean = sum(values) / n
                variance = sum((v - mean) ** 2 for v in values) / n
                std_dev = math.sqrt(variance)

                return (mean, std_dev, n)
        except Exception:
            return None

    def _store_anomaly(self, report: AnomalyReport) -> None:
        try:
            with get_conn_ctx(self.db_path) as c:
                c.execute(
                    "INSERT INTO anomalies (metric, value, mean, std_dev, z_score, severity, description, timestamp) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                    (report.metric, report.current_value, report.mean,
                     report.std_dev, report.z_score, report.severity,
                     report.description, report.timestamp)
                )
        except Exception:
            pass  # error no crítico, continuar
    # ── Queries ───────────────────────────────────────────────────────────

    def get_baselines(self) -> dict[str, dict]:
        """Get current baselines for all metrics."""
        baselines = {}
        hour = time.localtime().tm_hour
        metrics = ["cpu_pct", "ram_pct", "process_count", "net_connections",
                    "disk_home_pct", "active_users"]
        for m in metrics:
            stats = self._get_stats(m, hour)
            if stats:
                mean, std, count = stats
                baselines[m] = {"mean": round(mean, 1), "std_dev": round(std, 1),
                                "samples": count}
        return baselines

    def get_recent_anomalies(self, n: int = 10) -> list[dict]:
        try:
            with get_conn_ctx(self.db_path) as c:
                rows = c.execute(
                    "SELECT metric, value, mean, z_score, severity, description, timestamp "
                    "FROM anomalies ORDER BY timestamp DESC LIMIT ?", (n,)
                ).fetchall()
                return [
                    {"metric": r[0], "value": r[1], "mean": r[2], "z_score": r[3],
                     "severity": r[4], "description": r[5], "ts": r[6]}
                    for r in rows
                ]
        except Exception:
            return []

    @property
    def stats(self) -> dict:
        try:
            with get_conn_ctx(self.db_path) as c:
                total_records = c.execute("SELECT COUNT(*) FROM metrics").fetchone()[0]
                total_anomalies = c.execute("SELECT COUNT(*) FROM anomalies").fetchone()[0]
                metrics = c.execute(
                    "SELECT DISTINCT metric FROM metrics"
                ).fetchall()
        except Exception:
            return {"total_records": 0, "total_anomalies": 0, "metrics_tracked": 0}
        return {
            "total_records": total_records,
            "total_anomalies": total_anomalies,
            "metrics_tracked": len(metrics),
            "baselines": self.get_baselines(),
        }


# ══════════════════════════════════════════════════════════════════════════════
#  SINGLETON
# ══════════════════════════════════════════════════════════════════════════════

_detector: Optional[AnomalyDetector] = None


def get_anomaly_detector() -> AnomalyDetector:
    global _detector
    if _detector is None:
        _detector = AnomalyDetector()
    return _detector


# ── CLI test ──────────────────────────────────────────────────────────────────
if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    ad = get_anomaly_detector()

    print("Anomaly Detector")
    print("Recording current metrics...")
    metrics = ad.record()
    for k, v in metrics.items():
        print(f"  {k}: {v}")

    print(f"\nStats: {ad.stats}")

    print("\nChecking for anomalies...")
    anomalies = ad.check()
    if anomalies:
        for a in anomalies:
            print(f"  [{a.severity}] {a.description}")
    else:
        print("  No anomalies (need more baseline data)")
