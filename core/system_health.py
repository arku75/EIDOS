"""
EIDOS core/system_health.py — System Health Monitor
=====================================================
Monitoreo continuo del estado del sistema para EIDOS.

Provee:
- CPU/RAM/Disk usage
- Service status (Ollama, Docker, etc.)
- Network connectivity
- GPU status (ROCm)
- Process monitoring

Uso por autonomía:
    from core.system_health import get_health_monitor
    hm = get_health_monitor()
    report = hm.quick_check()
    alerts = hm.get_alerts()
"""
from __future__ import annotations

import logging
import os
import subprocess
import time
from dataclasses import dataclass, field
from typing import Optional

log = logging.getLogger("eidos.health")


@dataclass
class HealthAlert:
    level: str      # "warning", "critical"
    source: str     # "ram", "disk", "ollama", etc.
    message: str
    timestamp: float = field(default_factory=time.time)
    resolved: bool = False


class SystemHealthMonitor:
    """Monitors system health and generates alerts."""

    def __init__(self):
        self._alerts: list[HealthAlert] = []
        self._last_check: float = 0.0
        self._cache: dict = {}
        self._cache_ttl: float = 30.0  # seconds

    # ── Quick Check (all at once) ────────────────────────────────────────────

    def quick_check(self) -> dict:
        """Run all health checks and return a summary dict."""
        now = time.time()
        if now - self._last_check < self._cache_ttl and self._cache:
            return self._cache

        report = {
            "timestamp": now,
            "ram": self._check_ram(),
            "disk": self._check_disk(),
            "cpu": self._check_cpu(),
            "ollama": self._check_ollama(),
            "docker": self._check_docker(),
            "gpu": self._check_gpu(),
            "network": self._check_network(),
            "alerts": [a.__dict__ for a in self._alerts if not a.resolved],
        }

        # Generate alerts based on checks
        self._evaluate_alerts(report)

        report["status"] = self._overall_status(report)
        self._cache = report
        self._last_check = now
        return report

    def get_alerts(self, include_resolved: bool = False) -> list[dict]:
        """Get current alerts."""
        alerts = self._alerts if include_resolved else [a for a in self._alerts if not a.resolved]
        return [a.__dict__ for a in alerts]

    def get_summary_line(self) -> str:
        """One-line status for status bar / prompt."""
        r = self.quick_check()
        ram = r.get("ram", {})
        pct = ram.get("percent", 0)
        ollama = "ON" if r.get("ollama", {}).get("online") else "OFF"
        alerts_n = len([a for a in self._alerts if not a.resolved])
        status = r.get("status", "unknown")
        return f"[{status}] RAM:{pct}% Ollama:{ollama} Alerts:{alerts_n}"

    # ── Individual Checks ────────────────────────────────────────────────────

    def _check_ram(self) -> dict:
        try:
            import psutil
            mem = psutil.virtual_memory()
            swap = psutil.swap_memory()
            return {
                "total_gb": round(mem.total / (1024**3), 1),
                "used_gb": round(mem.used / (1024**3), 1),
                "available_gb": round(mem.available / (1024**3), 1),
                "percent": mem.percent,
                "swap_percent": swap.percent,
            }
        except ImportError:
            return self._check_ram_fallback()

    def _check_ram_fallback(self) -> dict:
        try:
            result = subprocess.run(
                ["free", "-b"], capture_output=True, text=True, timeout=5
            )
            for line in result.stdout.split("\n"):
                if line.startswith("Mem:"):
                    parts = line.split()
                    total = int(parts[1])
                    used = int(parts[2])
                    return {
                        "total_gb": round(total / (1024**3), 1),
                        "used_gb": round(used / (1024**3), 1),
                        "available_gb": round((total - used) / (1024**3), 1),
                        "percent": round(used / total * 100, 1) if total else 0,
                    }
        except Exception:
            pass  # error no crítico, continuar
        return {"error": "could not read RAM"}

    def _check_disk(self) -> dict:
        try:
            import shutil
            home = os.path.expanduser("~")
            usage = shutil.disk_usage(home)
            return {
                "total_gb": round(usage.total / (1024**3), 1),
                "used_gb": round(usage.used / (1024**3), 1),
                "free_gb": round(usage.free / (1024**3), 1),
                "percent": round(usage.used / usage.total * 100, 1),
            }
        except Exception as e:
            return {"error": str(e)}

    def _check_cpu(self) -> dict:
        try:
            # Load average (1, 5, 15 min)
            load = os.getloadavg()
            ncpu = os.cpu_count() or 1
            return {
                "load_1m": round(load[0], 2),
                "load_5m": round(load[1], 2),
                "load_15m": round(load[2], 2),
                "cores": ncpu,
                "load_percent": round(load[0] / ncpu * 100, 1),
            }
        except Exception:
            return {"cores": os.cpu_count() or 1}

    def _check_ollama(self) -> dict:
        try:
            import urllib.request
            import json
            req = urllib.request.Request("http://localhost:11434/api/tags")
            with urllib.request.urlopen(req, timeout=3) as resp:
                data = json.loads(resp.read())
                models = [m["name"] for m in data.get("models", [])]
                return {"online": True, "models": models, "count": len(models)}
        except Exception:
            return {"online": False, "models": [], "count": 0}

    def _check_docker(self) -> dict:
        try:
            result = subprocess.run(
                ["docker", "info", "--format", "{{.ContainersRunning}}"],
                capture_output=True, text=True, timeout=5,
            )
            if result.returncode == 0:
                running = int(result.stdout.strip() or "0")
                return {"available": True, "running_containers": running}
            return {"available": True, "error": "not accessible"}
        except FileNotFoundError:
            return {"available": False}
        except Exception as e:
            return {"available": False, "error": str(e)}

    def _check_gpu(self) -> dict:
        # AMD ROCm
        if os.path.exists("/dev/kfd"):
            try:
                result = subprocess.run(
                    ["rocm-smi", "--showid", "--json"],
                    capture_output=True, text=True, timeout=5,
                )
                if result.returncode == 0:
                    return {"type": "amd_rocm", "available": True}
            except FileNotFoundError:
                return {"type": "amd_rocm", "available": True, "note": "rocm-smi not found"}
            except Exception:
                pass  # error no crítico, continuar
        # NVIDIA
        try:
            result = subprocess.run(
                ["nvidia-smi", "--query-gpu=name,memory.used,memory.total",
                 "--format=csv,noheader,nounits"],
                capture_output=True, text=True, timeout=5,
            )
            if result.returncode == 0:
                return {"type": "nvidia", "available": True, "info": result.stdout.strip()}
        except FileNotFoundError:
            pass

        return {"type": "cpu_only", "available": False}

    def _check_network(self) -> dict:
        try:
            import urllib.request
            urllib.request.urlopen("http://1.1.1.1", timeout=3)
            return {"internet": True}
        except Exception:
            return {"internet": False}

    # ── Alert System ─────────────────────────────────────────────────────────

    def _evaluate_alerts(self, report: dict) -> None:
        """Generate alerts based on health report."""
        # RAM alerts
        ram_pct = report.get("ram", {}).get("percent", 0)
        if ram_pct > 90:
            self._add_alert("critical", "ram", f"RAM at {ram_pct}% — system may become unstable")
        elif ram_pct > 80:
            self._add_alert("warning", "ram", f"RAM at {ram_pct}% — consider freeing memory")
        else:
            self._resolve_alerts("ram")

        # Disk alerts
        disk_pct = report.get("disk", {}).get("percent", 0)
        disk_free = report.get("disk", {}).get("free_gb", 999)
        if disk_free < 2:
            self._add_alert("critical", "disk", f"Only {disk_free}GB disk free!")
        elif disk_pct > 90:
            self._add_alert("warning", "disk", f"Disk at {disk_pct}%")
        else:
            self._resolve_alerts("disk")

        # Ollama
        if not report.get("ollama", {}).get("online"):
            self._add_alert("warning", "ollama", "Ollama is offline — LLM capabilities unavailable")
        else:
            self._resolve_alerts("ollama")

        # CPU load
        cpu_load = report.get("cpu", {}).get("load_percent", 0)
        if cpu_load > 95:
            self._add_alert("warning", "cpu", f"CPU load at {cpu_load}%")
        else:
            self._resolve_alerts("cpu")

    def _add_alert(self, level: str, source: str, message: str) -> None:
        """Add alert if not already active for this source."""
        active = [a for a in self._alerts if a.source == source and not a.resolved]
        if not active:
            self._alerts.append(HealthAlert(level=level, source=source, message=message))
            log.warning("Health alert [%s] %s: %s", level, source, message)

    def _resolve_alerts(self, source: str) -> None:
        """Mark alerts for a source as resolved."""
        for alert in self._alerts:
            if alert.source == source and not alert.resolved:
                alert.resolved = True

    def _overall_status(self, report: dict) -> str:
        """Determine overall system status."""
        active_alerts = [a for a in self._alerts if not a.resolved]
        if any(a.level == "critical" for a in active_alerts):
            return "CRITICAL"
        if any(a.level == "warning" for a in active_alerts):
            return "WARNING"
        return "OK"

    # Keep alerts list from growing unbounded
    def _prune_alerts(self, max_keep: int = 50) -> None:
        if len(self._alerts) > max_keep:
            self._alerts = self._alerts[-max_keep:]


# ══════════════════════════════════════════════════════════════════════════════
#  SINGLETON
# ══════════════════════════════════════════════════════════════════════════════

_monitor: Optional[SystemHealthMonitor] = None


def get_health_monitor() -> SystemHealthMonitor:
    global _monitor
    if _monitor is None:
        _monitor = SystemHealthMonitor()
    return _monitor
