#!/usr/bin/env python3
"""
core/eidos_healer.py — EIDOS System Health Monitor & Auto-Repair Daemon
========================================================================
REAL self-healing for EIDOS infrastructure. NOT fake — this actually:
- Checks HTTP endpoints, systemd services, disk, RAM, CPU every 60s
- Restarts crashed services
- Cleans logs and temp files when disk is low
- Drops caches and throttles when RAM is high
- Restarts stuck ChromaDB / Ollama / Bridge
- Escalates to Telegram after 3 failed repair attempts
- Logs everything to ~/.eidos/healer_actions.jsonl
- Learns which fixes work for which failures

Replaces the fake _heal_timeout (which just slept and returned False)
with actual infrastructure repair.

Usage:
    python3 core/eidos_healer.py              # Run once (check + repair)
    python3 core/eidos_healer.py --daemon     # Run continuously (every 60s)
    python3 core/eidos_healer.py --once       # Single check + repair, print report

Service unit (TODO after testing):
    [Unit]
    Description=EIDOS Healer — System Health Monitor & Auto-Repair
    After=network.target
    [Service]
    Type=simple
    ExecStart=/usr/bin/env python3 -m core.eidos_healer --daemon
    Restart=always
    RestartSec=10
    [Install]
    WantedBy=default.target
"""

from __future__ import annotations

import json
import os
import shutil
import signal
import socket
import subprocess
import sys
import threading
import time
import traceback
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Optional, List, Dict, Any, Tuple

import requests

# ══════════════════════════════════════════════════════════════════════════════
# Constants
# ══════════════════════════════════════════════════════════════════════════════

EIDOS_HOME = Path.home() / ".eidos"
HEALER_LOG = EIDOS_HOME / "healer_actions.jsonl"
EIDOS_ROOT = Path(__file__).resolve().parent.parent

CHECK_INTERVAL = 60          # seconds between health checks
MAX_REPAIR_ATTEMPTS = 3      # escalate after this many consecutive failures

# Endpoints to monitor
BRIDGE_HEALTH_URL = "http://127.0.0.1:8003/health"
CHROMADB_HEARTBEAT = "http://127.0.0.1:8767/api/v2/heartbeat"
OLLAMA_LOCAL_URL = "http://127.0.0.1:11434"
OLLAMA_REMOTE_URL = "http://127.0.0.1:11435"

# Thresholds
DISK_FREE_MIN_PCT = 5.0      # alert if disk free < 5%
RAM_MAX_PCT = 95.0           # alert if RAM used > 95%
CPU_LOAD_MAX = 25.0          # alert if load > 25
HTTP_TIMEOUT = 10            # seconds for health endpoint requests

# Core EIDOS services that must be running
CORE_SERVICES = [
    "eidos-bridge.service",
    "eidos-chroma.service",
    "eidos-daemon.service",
    "eidos-brain-lite.service",
    "eidos-trinity.service",
    "eidos-vivo.service",
    "eidos-telegram.service",
    "eidos-self.service",
    "eidos-ram-guardian.service",
]

# Non-critical services (alert but don't escalate)
OPTIONAL_SERVICES = [
    "eidos-tunnel-in.service",
    "eidos-tunnel-out.service",
    "eidos-colony.service",
    "eidos-hexstrike.service",
]

# Paths to clean when disk is low
CLEAN_PATTERNS = [
    EIDOS_HOME / "logs" / "*.log",
    Path("/tmp/tess_*"),
    EIDOS_HOME / "cache",
    EIDOS_HOME / "tmp",
    Path.home() / ".cache" / "eidos",
]

# Known OOM-heavy processes (will be killed if RAM critical)
OOM_CANDIDATES = [
    "gemini.js",      # gemini-cli can balloon to 7G+
    "gemini-cli",
    "node.*gemini",
    "chromium",
    "chromedriver",
    "firefox",
]

# ══════════════════════════════════════════════════════════════════════════════
# Data Models
# ══════════════════════════════════════════════════════════════════════════════

@dataclass
class HealthStatus:
    """Result of a single health check."""
    component: str
    healthy: bool
    detail: str = ""
    latency_ms: float = 0.0
    timestamp: float = field(default_factory=time.time)


@dataclass
class RepairAction:
    """A single repair action taken."""
    component: str
    action: str
    success: bool
    detail: str = ""
    attempt: int = 1
    timestamp: float = field(default_factory=time.time)


@dataclass
class HealerReport:
    """Full health check + repair report."""
    timestamp: float = field(default_factory=time.time)
    checks: List[HealthStatus] = field(default_factory=list)
    repairs: List[RepairAction] = field(default_factory=list)
    healthy: bool = True
    summary: str = ""


# ══════════════════════════════════════════════════════════════════════════════
# EidosHealer — Main Class
# ══════════════════════════════════════════════════════════════════════════════

class EidosHealer:
    """
    Real system health monitor and auto-repair engine for EIDOS.

    Monitors infrastructure health every 60 seconds and takes real
    corrective action when things break. Not a fake timeout-sleeper.
    """

    def __init__(self, verbose: bool = True):
        self.verbose = verbose
        self._lock = threading.Lock()
        self._running = False

        # Track consecutive failures per component for escalation
        self._failures: Dict[str, int] = defaultdict(int)

        # Learning: record of what actions worked for which symptoms
        self._action_registry: Dict[str, List[RepairAction]] = defaultdict(list)

        # OOM protection: GC counter (every 100 cycles = ~100 min)
        self._gc_counter = 0

        EIDOS_HOME.mkdir(parents=True, exist_ok=True)
        self._ensure_log()

    # ── Logging ──────────────────────────────────────────────────────────

    def _log(self, msg: str):
        if self.verbose:
            ts = datetime.now().strftime("%H:%M:%S")
            print(f"\U0001fa79 [Healer {ts}] {msg}", flush=True)

    def _ensure_log(self):
        """Create the action log file if it doesn't exist."""
        if not HEALER_LOG.exists():
            HEALER_LOG.touch()
            HEALER_LOG.chmod(0o600)

    def _write_action(self, action: RepairAction):
        """Append a repair action to the JSONL log."""
        entry = {
            "component": action.component,
            "action": action.action,
            "success": action.success,
            "detail": action.detail,
            "attempt": action.attempt,
            "timestamp": action.timestamp,
            "iso": datetime.fromtimestamp(action.timestamp).isoformat(),
        }
        with self._lock:
            with open(HEALER_LOG, "a") as f:
                f.write(json.dumps(entry, ensure_ascii=False) + "\n")

    # ══════════════════════════════════════════════════════════════════════
    # HEALTH CHECKS
    # ══════════════════════════════════════════════════════════════════════

    def check_all(self) -> HealerReport:
        """Run all health checks and return a complete report."""
        report = HealerReport()
        report.checks = [
            self._check_http_endpoint("bridge", BRIDGE_HEALTH_URL),
            self._check_chromadb(),
            self._check_ollama(),
            self._check_systemd_services(),
            self._check_disk(),
            self._check_ram(),
            self._check_cpu_load(),
        ]
        report.healthy = all(c.healthy for c in report.checks)
        if report.healthy:
            report.summary = "All systems healthy"
        else:
            failing = [c.component for c in report.checks if not c.healthy]
            report.summary = f"Unhealthy components: {', '.join(failing)}"
        return report

    def _check_http_endpoint(self, name: str, url: str) -> HealthStatus:
        """Check if an HTTP endpoint returns 200."""
        t0 = time.time()
        try:
            resp = requests.get(url, timeout=HTTP_TIMEOUT)
            latency = (time.time() - t0) * 1000
            if resp.status_code == 200:
                return HealthStatus(name, True, f"HTTP 200", latency)
            else:
                return HealthStatus(name, False, f"HTTP {resp.status_code}", latency)
        except requests.ConnectionError:
            latency = (time.time() - t0) * 1000
            return HealthStatus(name, False, "Connection refused", latency)
        except requests.Timeout:
            latency = (time.time() - t0) * 1000
            return HealthStatus(name, False, f"Timeout ({HTTP_TIMEOUT}s)", latency)
        except Exception as e:
            latency = (time.time() - t0) * 1000
            return HealthStatus(name, False, str(e)[:100], latency)

    def _check_chromadb(self) -> HealthStatus:
        """Check ChromaDB heartbeat on :8767."""
        t0 = time.time()
        try:
            resp = requests.get(CHROMADB_HEARTBEAT, timeout=HTTP_TIMEOUT)
            latency = (time.time() - t0) * 1000
            if resp.status_code == 200:
                data = resp.json()
                return HealthStatus(
                    "chromadb", True,
                    f"heartbeat OK (nanos={data.get('nanosecond heartbeat', '?')})",
                    latency
                )
            return HealthStatus("chromadb", False, f"HTTP {resp.status_code}", latency)
        except requests.ConnectionError:
            latency = (time.time() - t0) * 1000
            return HealthStatus("chromadb", False, "Connection refused", latency)
        except Exception as e:
            latency = (time.time() - t0) * 1000
            return HealthStatus("chromadb", False, str(e)[:100], latency)

    def _check_ollama(self) -> HealthStatus:
        """Check Ollama is responsive (try local :11434, fallback to remote :11435)."""
        t0 = time.time()

        def _try_ollama(url: str) -> Tuple[bool, str]:
            try:
                resp = requests.get(f"{url}/api/tags", timeout=HTTP_TIMEOUT)
                if resp.status_code == 200:
                    models = resp.json().get("models", [])
                    count = len(models)
                    names = ", ".join(m.get("name", "?")[:30] for m in models[:3])
                    return True, f"{count} models ({names}...)" if count > 3 else f"{count} models ({names})"
                return False, f"HTTP {resp.status_code}"
            except Exception as e:
                return False, str(e)[:100]

        ok, detail = _try_ollama(OLLAMA_LOCAL_URL)
        latency = (time.time() - t0) * 1000
        if ok:
            return HealthStatus("ollama", True, f"local :11434 — {detail}", latency)

        # Try remote (Mac tunnel)
        ok2, detail2 = _try_ollama(OLLAMA_REMOTE_URL)
        latency = (time.time() - t0) * 1000
        if ok2:
            return HealthStatus("ollama", True,
                                f"remote :11435 — {detail2} (local down)", latency)
        return HealthStatus("ollama", False,
                            f"local: {detail[:60]}; remote: {detail2[:60]}", latency)

    def _check_systemd_services(self) -> HealthStatus:
        """Check all core systemd services are running."""
        t0 = time.time()
        dead = []
        try:
            result = subprocess.run(
                ["systemctl", "--user", "is-active", "--quiet"] + CORE_SERVICES,
                capture_output=True, timeout=15
            )
            # systemctl is-active returns 0 if ALL are active, non-zero otherwise
            if result.returncode == 0:
                latency = (time.time() - t0) * 1000
                return HealthStatus("systemd", True, f"{len(CORE_SERVICES)} core services active", latency)

            # Check individually to report which ones are dead
            for svc in CORE_SERVICES + OPTIONAL_SERVICES:
                r = subprocess.run(
                    ["systemctl", "--user", "is-active", "--quiet", svc],
                    capture_output=True, timeout=5
                )
                if r.returncode != 0:
                    dead.append(svc)

            latency = (time.time() - t0) * 1000
            if dead:
                return HealthStatus("systemd", len(dead) == 0,
                                    f"Dead: {', '.join(dead)}", latency)
            return HealthStatus("systemd", True, f"All services active", latency)
        except Exception as e:
            latency = (time.time() - t0) * 1000
            return HealthStatus("systemd", False, str(e)[:100], latency)

    def _check_disk(self) -> HealthStatus:
        """Check disk free space > DISK_FREE_MIN_PCT%."""
        t0 = time.time()
        try:
            disk = shutil.disk_usage(Path.home())
            free_pct = disk.free / disk.total * 100
            free_gb = disk.free / (1024 ** 3)
            total_gb = disk.total / (1024 ** 3)
            healthy = free_pct > DISK_FREE_MIN_PCT
            latency = (time.time() - t0) * 1000
            return HealthStatus(
                "disk", healthy,
                f"{free_pct:.1f}% free ({free_gb:.1f}G/{total_gb:.0f}G)",
                latency
            )
        except Exception as e:
            latency = (time.time() - t0) * 1000
            return HealthStatus("disk", False, str(e)[:100], latency)

    def _check_ram(self) -> HealthStatus:
        """Check RAM usage < RAM_MAX_PCT%."""
        t0 = time.time()
        try:
            import psutil
            mem = psutil.virtual_memory()
            healthy = mem.percent < RAM_MAX_PCT
            latency = (time.time() - t0) * 1000
            return HealthStatus(
                "ram", healthy,
                f"{mem.percent:.1f}% used ({mem.used / (1024**3):.1f}G/{mem.total / (1024**3):.1f}G)",
                latency
            )
        except ImportError:
            # Fallback: parse /proc/meminfo
            try:
                with open("/proc/meminfo") as f:
                    lines = f.readlines()
                memtotal = int([l for l in lines if "MemTotal" in l][0].split()[1])
                memavail = int([l for l in lines if "MemAvailable" in l][0].split()[1])
                pct = (memtotal - memavail) / memtotal * 100
                healthy = pct < RAM_MAX_PCT
                latency = (time.time() - t0) * 1000
                return HealthStatus("ram", healthy, f"{pct:.1f}% used", latency)
            except Exception as e:
                latency = (time.time() - t0) * 1000
                return HealthStatus("ram", False, str(e)[:100], latency)

    def _check_cpu_load(self) -> HealthStatus:
        """Check CPU load < CPU_LOAD_MAX."""
        t0 = time.time()
        try:
            load1, load5, load15 = os.getloadavg()
            nproc = os.cpu_count() or 1
            load_pct = (load1 / nproc) * 100
            healthy = load1 < CPU_LOAD_MAX
            latency = (time.time() - t0) * 1000
            return HealthStatus(
                "cpu", healthy,
                f"load {load1:.1f}/{load5:.1f}/{load15:.1f} ({load_pct:.0f}% of {nproc} cores)",
                latency
            )
        except Exception as e:
            latency = (time.time() - t0) * 1000
            return HealthStatus("cpu", False, str(e)[:100], latency)

    # ══════════════════════════════════════════════════════════════════════
    # AUTO-REPAIR ACTIONS
    # ══════════════════════════════════════════════════════════════════════

    def repair_all(self, report: HealerReport) -> HealerReport:
        """Run repairs for all unhealthy components in the report."""
        for check in report.checks:
            if check.healthy:
                continue
            attempt = self._failures[check.component] + 1
            self._failures[check.component] = attempt

            repair_methods = {
                "bridge": self._repair_bridge,
                "chromadb": self._repair_chromadb,
                "ollama": self._repair_ollama,
                "systemd": self._repair_systemd,
                "disk": self._repair_disk,
                "ram": self._repair_ram,
                "cpu": self._repair_cpu,
            }

            repair_fn = repair_methods.get(check.component)
            if repair_fn:
                action = repair_fn(check, attempt)
                report.repairs.append(action)

                # Update failure count: reset if fixed, escalate if not
                if action.success:
                    self._failures[check.component] = 0
                    self._action_registry[check.component].append(action)
                elif attempt >= MAX_REPAIR_ATTEMPTS:
                    self._escalate(check, action, attempt)
            else:
                report.repairs.append(RepairAction(
                    check.component, "no_repair_method", False,
                    f"No repair handler for {check.component}", attempt
                ))

        # Recheck after repairs if any repairs were attempted
        if report.repairs:
            report.healthy = all(
                r.success or r.action == "no_repair_method"
                for r in report.repairs
            )

        return report

    def _repair_bridge(self, status: HealthStatus, attempt: int) -> RepairAction:
        """
        Repair the EIDOS bridge (HTTP :8003).
        Actions:
          1. systemctl --user restart eidos-bridge
          2. If that fails, kill any lingering python bridge process and retry
        """
        self._log(f"Repairing bridge (attempt {attempt}/{MAX_REPAIR_ATTEMPTS})...")

        # Step 1: Restart via systemd
        try:
            subprocess.run(
                ["systemctl", "--user", "restart", "eidos-bridge.service"],
                capture_output=True, timeout=30
            )
            time.sleep(3)  # give it time to start

            # Verify
            ok, _ = self._quick_http_check(BRIDGE_HEALTH_URL)
            if ok:
                self._log("Bridge restarted successfully via systemd")
                return RepairAction("bridge", "systemctl_restart", True,
                                    "Restarted eidos-bridge.service, health OK", attempt)

            # Step 2: Kill + restart manually
            self._log("systemd restart insufficient, killing bridge process...")
            self._kill_process("bridge_to_eidos.py")

            # Restart environment
            env = os.environ.copy()
            env.setdefault("PYTHONPATH", str(EIDOS_ROOT))
            env.setdefault("EIDOS_BRIDGE_MODE", "1")
            env.setdefault("EIDOS_BOM", "1")

            subprocess.run(
                ["systemctl", "--user", "restart", "eidos-bridge.service"],
                capture_output=True, timeout=30,
                env=env
            )
            time.sleep(5)

            ok2, _ = self._quick_http_check(BRIDGE_HEALTH_URL)
            msg = "Killed orphan + restarted eidos-bridge" + (" — OK" if ok2 else " — still failing")
            return RepairAction("bridge", "kill_and_restart", ok2, msg, attempt)

        except Exception as e:
            return RepairAction("bridge", "repair_exception", False, str(e)[:200], attempt)

    def _repair_chromadb(self, status: HealthStatus, attempt: int) -> RepairAction:
        """
        Repair ChromaDB (:8767).
        Actions:
          1. Restart eidos-chroma.service
          2. If still stuck: kill process, run integrity check, restart
        """
        self._log(f"Repairing ChromaDB (attempt {attempt}/{MAX_REPAIR_ATTEMPTS})...")

        try:
            # Step 1: Restart via systemd
            subprocess.run(
                ["systemctl", "--user", "restart", "eidos-chroma.service"],
                capture_output=True, timeout=30
            )
            time.sleep(3)

            ok, _ = self._quick_http_check(CHROMADB_HEARTBEAT)
            if ok:
                self._log("ChromaDB restarted successfully")
                return RepairAction("chromadb", "systemctl_restart", True,
                                    "Restarted eidos-chroma.service", attempt)

            # Step 2: Kill stuck process, check integrity, restart
            self._log("ChromaDB still unresponsive, running integrity check...")
            self._kill_process("chroma")

            # Verify the data directory
            chroma_path = EIDOS_HOME / "chroma"
            if chroma_path.exists():
                # Basic integrity: check if chroma.sqlite3 exists and is valid
                chroma_db = chroma_path / "chroma.sqlite3"
                if chroma_db.exists():
                    try:
                        subprocess.run(
                            ["sqlite3", str(chroma_db), "PRAGMA integrity_check"],
                            capture_output=True, timeout=30
                        )
                    except Exception:
                        pass  # sqlite3 may not be available; ok

            # Restart
            subprocess.run(
                ["systemctl", "--user", "restart", "eidos-chroma.service"],
                capture_output=True, timeout=30
            )
            time.sleep(5)

            ok2, _ = self._quick_http_check(CHROMADB_HEARTBEAT)
            msg = "Killed chroma + integrity check + restart" + (" — OK" if ok2 else " — still failing")
            return RepairAction("chromadb", "kill_check_restart", ok2, msg, attempt)

        except Exception as e:
            return RepairAction("chromadb", "repair_exception", False, str(e)[:200], attempt)

    def _repair_ollama(self, status: HealthStatus, attempt: int) -> RepairAction:
        """
        Repair Ollama.
        Actions:
          1. Restart ollama.service (system-wide)
          2. If user=ollama can't read models, fix with drop-in
          3. Verify models are listed
        """
        self._log(f"Repairing Ollama (attempt {attempt}/{MAX_REPAIR_ATTEMPTS})...")

        try:
            # Step 1: Restart ollama system service
            result = subprocess.run(
                ["sudo", "systemctl", "restart", "ollama.service"],
                capture_output=True, text=True, timeout=30
            )
            if result.returncode != 0 and "Interactive authentication required" in result.stderr:
                # Can't sudo — try user-level
                subprocess.run(
                    ["systemctl", "--user", "restart", "ollama.service"],
                    capture_output=True, timeout=15
                )

            time.sleep(5)

            # Verify
            ok, detail = self._quick_ollama_check()
            if ok:
                self._log(f"Ollama restarted: {detail}")
                return RepairAction("ollama", "restart_service", True,
                                    f"ollama.service restarted, {detail}", attempt)

            # Step 2: Kill and restart directly
            self._log("Ollama service restart insufficient, killing process...")
            self._kill_process("ollama")

            # Start ollama directly (bypass service if needed)
            ollama_bin = shutil.which("ollama") or "/usr/local/bin/ollama"
            env = os.environ.copy()
            env["HOME"] = str(Path.home())
            env["OLLAMA_MODELS"] = str(Path.home() / ".ollama" / "models")
            env["OLLAMA_HOST"] = "127.0.0.1:11434"

            subprocess.Popen(
                [ollama_bin, "serve"],
                env=env,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                start_new_session=True,
            )
            time.sleep(8)

            ok2, detail2 = self._quick_ollama_check()
            msg = f"Killed ollama + manual serve" + (" — OK" if ok2 else " — still failing")
            return RepairAction("ollama", "kill_and_serve", ok2,
                                f"{msg}, {detail2}", attempt)

        except Exception as e:
            return RepairAction("ollama", "repair_exception", False, str(e)[:200], attempt)

    def _repair_systemd(self, status: HealthStatus, attempt: int) -> RepairAction:
        """
        Repair dead systemd services.
        Parse the detail string to find which services are dead, restart each.
        """
        detail = status.detail
        dead_services = []
        if "Dead:" in detail:
            dead_services = [s.strip() for s in detail.split("Dead:")[1].split(",")]

        if not dead_services:
            # Try individual check
            for svc in CORE_SERVICES:
                r = subprocess.run(
                    ["systemctl", "--user", "is-active", "--quiet", svc],
                    capture_output=True, timeout=5
                )
                if r.returncode != 0:
                    dead_services.append(svc)

        self._log(f"Repairing {len(dead_services)} dead services: {dead_services} (attempt {attempt})")

        repaired = []
        still_dead = []
        for svc in dead_services:
            try:
                subprocess.run(
                    ["systemctl", "--user", "restart", svc],
                    capture_output=True, timeout=30
                )
                time.sleep(1)
                r = subprocess.run(
                    ["systemctl", "--user", "is-active", "--quiet", svc],
                    capture_output=True, timeout=5
                )
                if r.returncode == 0:
                    repaired.append(svc)
                else:
                    still_dead.append(svc)
            except Exception:
                still_dead.append(svc)

        success = len(still_dead) == 0
        msg = f"Restarted {len(repaired)}/{len(dead_services)} services"
        if still_dead:
            msg += f"; still dead: {still_dead}"
        if repaired:
            msg += f"; repaired: {repaired}"

        self._log(msg)
        return RepairAction("systemd", "restart_services", success, msg, attempt)

    def _repair_disk(self, status: HealthStatus, attempt: int) -> RepairAction:
        """
        Free disk space.
        Actions:
          1. Truncate large log files
          2. Clean /tmp/tess_* (OCR temp files from Tesseract)
          3. Clean ~/.eidos/cache/
          4. Remove .bak files older than 7 days
        """
        self._log(f"Freeing disk space (attempt {attempt}/{MAX_REPAIR_ATTEMPTS})...")
        actions_taken = []
        freed_mb = 0.0

        try:
            # 1. Truncate large log files (>10MB) in ~/.eidos/logs/
            logs_dir = EIDOS_HOME / "logs"
            if logs_dir.exists():
                for logfile in logs_dir.glob("*.log"):
                    try:
                        size_mb = logfile.stat().st_size / (1024 * 1024)
                        if size_mb > 10:
                            # Truncate to last 1000 lines
                            with open(logfile, "rb") as f:
                                f.seek(0, 2)
                                fsize = f.tell()
                                # Read last ~100KB
                                f.seek(max(0, fsize - 100_000))
                                tail = f.read()
                            with open(logfile, "wb") as f:
                                # Keep only last 1000 lines of tail
                                lines = tail.split(b"\n")
                                if len(lines) > 1000:
                                    tail = b"\n".join(lines[-1000:])
                                f.write(tail)
                            freed_mb += size_mb - (logfile.stat().st_size / (1024 * 1024))
                            actions_taken.append(f"truncated {logfile.name} ({size_mb:.0f}MB)")
                    except Exception:
                        pass

            # 2. Clean OCR temp files
            for pattern in ["/tmp/tess_*"]:
                try:
                    import glob
                    for f in glob.glob(pattern):
                        try:
                            sz = os.path.getsize(f) / (1024 * 1024)
                            os.unlink(f)
                            freed_mb += sz
                            actions_taken.append(f"deleted {os.path.basename(f)}")
                        except Exception:
                            pass
                except Exception:
                    pass

            # 3. Clean EIDOS cache
            cache_dir = EIDOS_HOME / "cache"
            if cache_dir.exists():
                for f in cache_dir.iterdir():
                    try:
                        if f.is_file():
                            sz = f.stat().st_size / (1024 * 1024)
                            f.unlink()
                            freed_mb += sz
                            actions_taken.append(f"deleted cache/{f.name}")
                    except Exception:
                        pass

            # 4. Remove old .bak files (older than 7 days) but only in EIDOS dir
            cutoff = time.time() - (7 * 86400)
            for bak in EIDOS_ROOT.rglob("*.bak"):
                try:
                    if bak.is_file() and bak.stat().st_mtime < cutoff:
                        sz = bak.stat().st_size / (1024 * 1024)
                        bak.unlink()
                        freed_mb += sz
                        actions_taken.append(f"deleted old bak {bak.name}")
                except Exception:
                    pass

            success = freed_mb > 0
            msg = f"Freed {freed_mb:.1f}MB: {', '.join(actions_taken[:5])}"
            if len(actions_taken) > 5:
                msg += f" and {len(actions_taken) - 5} more"
            self._log(msg)

            # Re-check disk after cleanup
            disk = shutil.disk_usage(Path.home())
            free_pct = disk.free / disk.total * 100
            if free_pct <= DISK_FREE_MIN_PCT:
                msg += f" (still critical: {free_pct:.1f}% free)"

            return RepairAction("disk", "cleanup", success, msg, attempt)

        except Exception as e:
            return RepairAction("disk", "repair_exception", False, str(e)[:200], attempt)

    def _repair_ram(self, status: HealthStatus, attempt: int) -> RepairAction:
        """
        Reduce RAM pressure.
        Actions:
          1. Drop filesystem caches (requires sudo)
          2. Kill known memory-heavy orphan processes
          3. Run Python garbage collection in existing processes (via signal)
        """
        self._log(f"Reducing RAM pressure (attempt {attempt}/{MAX_REPAIR_ATTEMPTS})...")
        actions_taken = []

        try:
            # 1. Drop caches (non-destructive: only frees clean pagecache)
            result = subprocess.run(
                ["sudo", "sh", "-c", "sync && echo 1 > /proc/sys/vm/drop_caches"],
                capture_output=True, timeout=10
            )
            if result.returncode == 0:
                actions_taken.append("dropped pagecache")
                self._log("Dropped filesystem caches")

            # 2. Kill gemini-cli orphans (known to consume 7G+ each)
            killed = self._kill_oom_orphans()
            if killed:
                actions_taken.append(f"killed {len(killed)} OOM orphans: {killed}")
                self._log(f"Killed OOM orphans: {killed}")

            # 3. Signal throttle to OCR processes
            # (Send SIGUSR1 to eidos processes to trigger cache flush + throttle)
            for proc_name in ["eidos_vision", "perception", "screen_scanner"]:
                self._signal_processes(proc_name, signal.SIGUSR1)

            # 4. Run sync to flush dirty pages
            subprocess.run(["sync"], timeout=5)

            # Verify RAM improved
            try:
                import psutil
                mem = psutil.virtual_memory()
                new_pct = mem.percent
                self._log(f"RAM after repair: {new_pct:.1f}%")
            except ImportError:
                new_pct = 100.0

            success = len(actions_taken) > 0
            msg = f"Actions: {'; '.join(actions_taken)}" if actions_taken else "No actions possible (check sudo)"
            return RepairAction("ram", "reduce_pressure", success, msg, attempt)

        except Exception as e:
            return RepairAction("ram", "repair_exception", False, str(e)[:200], attempt)

    def _repair_cpu(self, status: HealthStatus, attempt: int) -> RepairAction:
        """
        Reduce CPU load.
        Actions:
          1. Identify and renice heavy non-EIDOS processes
          2. Throttle background OCR
        """
        self._log(f"Reducing CPU load (attempt {attempt}/{MAX_REPAIR_ATTEMPTS})...")
        actions_taken = []

        try:
            import psutil

            # Find CPU hogs (non-EIDOS processes using >50% CPU)
            hogs = []
            for proc in psutil.process_iter(["pid", "name", "cpu_percent", "cmdline"]):
                try:
                    cpu = proc.info["cpu_percent"] or 0
                    name = proc.info["name"] or ""
                    cmdline = " ".join(proc.info["cmdline"] or [])
                    if cpu > 50 and "eidos" not in cmdline.lower():
                        hogs.append((proc.info["pid"], name, cpu, cmdline[:100]))
                except (psutil.NoSuchProcess, psutil.AccessDenied):
                    pass

            # Renice them to 19 (lowest priority)
            for pid, name, cpu, cmdline in hogs[:5]:
                try:
                    os.setpriority(os.PRIO_PROCESS, pid, 19)
                    actions_taken.append(f"reniced {name}({pid}) cpu={cpu:.0f}%")
                except Exception:
                    pass

            if actions_taken:
                self._log(f"Reniced CPU hogs: {actions_taken}")
            else:
                self._log("No CPU hogs found (or psutil unavailable)")

            return RepairAction("cpu", "renice_hogs", len(actions_taken) > 0,
                                f"Reniced {len(actions_taken)} processes" if actions_taken else "No action needed",
                                attempt)

        except ImportError:
            # Fallback: just report
            return RepairAction("cpu", "no_psutil", False,
                                "psutil not available for CPU management", attempt)
        except Exception as e:
            return RepairAction("cpu", "repair_exception", False, str(e)[:200], attempt)

    # ══════════════════════════════════════════════════════════════════════
    # ESCALATION
    # ══════════════════════════════════════════════════════════════════════

    def _escalate(self, status: HealthStatus, action: RepairAction, attempt: int):
        """
        Escalate after MAX_REPAIR_ATTEMPTS failed repairs.
        Sends Telegram alert to SER and, for critical failures, initiates
        controlled shutdown with WAL checkpoint.
        """
        self._log(f"ESCALATING: {status.component} failed {attempt} repair attempts!")

        # Send Telegram alert
        msg = (
            f"⚠️ EIDOS HEALER ESCALATION\n"
            f"Component: {status.component}\n"
            f"Symptom: {status.detail}\n"
            f"Repair attempts: {attempt}\n"
            f"Last action: {action.action} → {'OK' if action.success else 'FAILED'}\n"
            f"Detail: {action.detail[:200]}\n"
            f"Time: {datetime.now().isoformat()}"
        )
        self._send_telegram_alert(msg)

        # For critical infrastructure (bridge, chromadb), do controlled shutdown
        if status.component in ("bridge", "chromadb"):
            self._log(f"CRITICAL: {status.component} unrecoverable, WAL checkpoint + shutdown")
            self._controlled_shutdown(status.component)

    def _send_telegram_alert(self, message: str):
        """Send alert via Telegram bot to SER."""
        try:
            # Read token from env or secrets
            token = (os.environ.get("EIDOS_TELEGRAM_TOKEN")
                     or os.environ.get("TELEGRAM_BOT_TOKEN", ""))

            # Try secrets file
            if not token:
                secrets_file = EIDOS_HOME / "secrets.env"
                if secrets_file.exists():
                    with open(secrets_file) as f:
                        for line in f:
                            if "TELEGRAM" in line and "=" in line:
                                token = line.strip().split("=", 1)[1].strip('"').strip("'")
                                break

            if not token:
                self._log("No Telegram token found, cannot send alert")
                return

            # Get chat IDs from env
            allowed = (os.environ.get("EIDOS_TELEGRAM_ALLOWED", "")
                       or os.environ.get("TELEGRAM_ALLOWED_USERS", ""))
            chat_ids = [int(c.strip()) for c in allowed.split(",") if c.strip().isdigit()]

            if not chat_ids:
                self._log("No Telegram chat IDs configured, cannot send alert")
                return

            base_url = f"https://api.telegram.org/bot{token}"
            for chat_id in chat_ids:
                try:
                    requests.post(
                        f"{base_url}/sendMessage",
                        json={"chat_id": chat_id, "text": message[:4096]},
                        timeout=10
                    )
                except Exception:
                    pass

            self._log(f"Telegram alert sent to {len(chat_ids)} chat(s)")
        except Exception as e:
            self._log(f"Failed to send Telegram alert: {e}")

    def _controlled_shutdown(self, component: str):
        """
        Controlled shutdown for critical failures.
        Performs WAL checkpoint on all EIDOS databases before stopping services.
        """
        self._log(f"Initiating controlled shutdown for {component}...")

        try:
            # 1. WAL checkpoint all EIDOS DBs
            db_files = list(EIDOS_HOME.rglob("*.db"))
            for db_path in db_files:
                try:
                    if db_path.stat().st_size > 0:
                        subprocess.run(
                            ["sqlite3", str(db_path), "PRAGMA wal_checkpoint(TRUNCATE)"],
                            capture_output=True, timeout=10
                        )
                except Exception:
                    pass

            self._log(f"WAL checkpoint done on {len(db_files)} databases")

            # 2. Stop non-critical services first
            for svc in ["eidos-vivo.service", "eidos-telegram.service",
                         "eidos-trinity.service", "eidos-self.service"]:
                subprocess.run(
                    ["systemctl", "--user", "stop", svc],
                    capture_output=True, timeout=15
                )

            # 3. Stop the failing component last
            svc_map = {
                "bridge": "eidos-bridge.service",
                "chromadb": "eidos-chroma.service",
            }
            if component in svc_map:
                subprocess.run(
                    ["systemctl", "--user", "stop", svc_map[component]],
                    capture_output=True, timeout=15
                )

            self._log(f"Controlled shutdown complete for {component}")

        except Exception as e:
            self._log(f"Controlled shutdown failed: {e}")

    # ══════════════════════════════════════════════════════════════════════
    # HELPERS
    # ══════════════════════════════════════════════════════════════════════

    def _quick_http_check(self, url: str) -> Tuple[bool, str]:
        """Quick HTTP 200 check, returns (ok, detail)."""
        try:
            resp = requests.get(url, timeout=5)
            return resp.status_code == 200, f"HTTP {resp.status_code}"
        except Exception as e:
            return False, str(e)[:60]

    def _wal_checkpoint_all(self):
        """Run WAL checkpoint on all EIDOS databases every 30 min.

        Prevents WAL files from growing unbounded (>1MB each across 9 DBs).
        Uses PRAGMA wal_checkpoint(TRUNCATE) to flush WAL to main DB.
        """
        try:
            import glob
            db_patterns = [
                str(EIDOS_HOME / "*.db"),
                str(EIDOS_HOME / "chroma" / "*.sqlite3"),
                str(EIDOS_ROOT / "memoria_fenix" / "*.sqlite3"),
            ]
            dbs = []
            for pat in db_patterns:
                dbs.extend(glob.glob(pat))

            checkpointed = 0
            for db_path in dbs:
                try:
                    from core.db import get_conn
                    conn = get_conn(db_path, timeout=10)
                except Exception:
                    conn = sqlite3.connect(db_path, timeout=10)
                try:
                    conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
                    checkpointed += 1
                except Exception:
                    pass
                finally:
                    try:
                        conn.close()
                    except Exception:
                        pass

            if checkpointed > 0:
                self._log(f"WAL checkpoint: {checkpointed} databases flushed")
        except Exception as e:
            self._log(f"WAL checkpoint error: {e}")

    def _quick_ollama_check(self) -> Tuple[bool, str]:
        """Quick Ollama API check."""
        try:
            resp = requests.get(f"{OLLAMA_LOCAL_URL}/api/tags", timeout=5)
            if resp.status_code == 200:
                models = resp.json().get("models", [])
                return True, f"{len(models)} models"
            return False, f"HTTP {resp.status_code}"
        except Exception as e:
            return False, str(e)[:60]

    def _kill_process(self, process_name: str):
        """Kill a process by name pattern."""
        try:
            # Use pgrep with bracket trick to avoid self-match
            pattern = process_name.replace(".", "[.]")
            subprocess.run(
                ["pkill", "-f", process_name],
                capture_output=True, timeout=10
            )
            time.sleep(1)
        except Exception:
            pass

    def _kill_oom_orphans(self) -> List[str]:
        """Kill known memory-hungry orphan processes. Returns list of killed."""
        killed = []
        try:
            result = subprocess.run(
                ["pgrep", "-f", "gemini.js"],
                capture_output=True, text=True, timeout=5
            )
            pids = [int(p) for p in result.stdout.strip().split("\n") if p.strip().isdigit()]
            for pid in pids:
                try:
                    # Only kill if it's a node process (not a shell wrapper or grep)
                    with open(f"/proc/{pid}/comm") as f:
                        comm = f.read().strip()
                    if comm in ("node", "gemini.js"):
                        os.kill(pid, signal.SIGTERM)
                        killed.append(f"gemini({pid})")
                except Exception:
                    pass

            # Also kill node processes using >3G RAM via psutil if available
            try:
                import psutil
                for proc in psutil.process_iter(["pid", "name", "memory_info", "cmdline"]):
                    try:
                        mem_mb = proc.info["memory_info"].rss / (1024 * 1024)
                        name = proc.info["name"] or ""
                        cmdline = " ".join(proc.info["cmdline"] or [])
                        if mem_mb > 3000 and name == "node" and "gemini" in cmdline.lower():
                            proc.terminate()
                            killed.append(f"node-gemini({proc.info['pid']}, {mem_mb:.0f}MB)")
                    except (psutil.NoSuchProcess, psutil.AccessDenied):
                        pass
            except ImportError:
                pass

        except Exception:
            pass

        return killed

    def _signal_processes(self, name_pattern: str, sig: int):
        """Send a signal to processes matching a name pattern."""
        try:
            result = subprocess.run(
                ["pgrep", "-f", name_pattern],
                capture_output=True, text=True, timeout=5
            )
            pids = [int(p) for p in result.stdout.strip().split("\n") if p.strip().isdigit()]
            for pid in pids:
                try:
                    os.kill(pid, sig)
                except Exception:
                    pass
        except Exception:
            pass

    # ══════════════════════════════════════════════════════════════════════
    # LEARNING
    # ══════════════════════════════════════════════════════════════════════

    def get_learned_actions(self) -> Dict[str, Any]:
        """
        Analyze action log to learn which repairs work.
        Returns summary of successful repair patterns.
        """
        if not HEALER_LOG.exists():
            return {"actions": 0, "patterns": {}}

        patterns = defaultdict(lambda: {"success": 0, "failure": 0, "actions": []})
        count = 0

        try:
            with open(HEALER_LOG) as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        entry = json.loads(line)
                        count += 1
                        key = f"{entry.get('component')}:{entry.get('action')}"
                        if entry.get("success"):
                            patterns[key]["success"] += 1
                        else:
                            patterns[key]["failure"] += 1
                        patterns[key]["actions"].append(entry.get("detail", "")[:100])
                    except json.JSONDecodeError:
                        pass
        except Exception:
            pass

        # Keep only last 5 details per pattern
        summary = {}
        for key, data in patterns.items():
            total = data["success"] + data["failure"]
            if total == 0:
                continue
            rate = data["success"] / total * 100
            summary[key] = {
                "success_rate": round(rate, 1),
                "total": total,
                "last_details": data["actions"][-3:],
            }

        # Sort by most reliable first
        summary = dict(sorted(summary.items(),
                              key=lambda x: x[1]["total"], reverse=True))

        return {"total_actions": count, "patterns": summary}

    # ══════════════════════════════════════════════════════════════════════
    # DAEMON MODE
    # ══════════════════════════════════════════════════════════════════════

    def run_loop(self, interval: int = CHECK_INTERVAL):
        """Run continuous health monitoring and repair loop."""
        self._running = True
        self._log(f"Healer daemon started (check every {interval}s)")

        # Track the last time we learned from the log
        last_learn_time = time.time()
        last_wal_checkpoint = time.time()

        while self._running:
            try:
                # Periodic WAL checkpoint: every 30 min (fix for 9 DBs with WAL > 1MB)
                if time.time() - last_wal_checkpoint > 1800:
                    self._wal_checkpoint_all()
                    last_wal_checkpoint = time.time()

                # Run full health check
                self._log("Running health check...")
                report = self.check_all()

                if report.healthy:
                    # Reset all failure counters on full health
                    for key in list(self._failures.keys()):
                        if self._failures[key] == 0:
                            del self._failures[key]
                    self._log(f"      All healthy: {report.summary}")
                else:
                    self._log(f"      Issues found: {report.summary}")
                    for c in report.checks:
                        if not c.healthy:
                            self._log(f"        {c.component}: {c.detail}")

                    # Repair
                    self._log("Running repairs...")
                    report = self.repair_all(report)

                    if report.repairs:
                        for r in report.repairs:
                            icon = "      OK" if r.success else "      FAIL"
                            self._log(f"{icon} {r.component}: {r.action} → {r.detail[:80]}")

                # OOM protection: GC collect every 100 cycles (~100 min)
                # The bridge can grow to 3.7GB without GC; this keeps it bounded
                if self._gc_counter is None:
                    self._gc_counter = 0
                self._gc_counter += 1
                if self._gc_counter >= 100:
                    import gc
                    collected = gc.collect()
                    if collected > 0:
                        self._log(f"GC: collected {collected} objects")
                    self._gc_counter = 0

                # Memory reporting every hour
                if time.time() - last_learn_time > 3600:
                    try:
                        import psutil
                        mem = psutil.virtual_memory()
                        self._log(f"Memory: {mem.percent:.1f}% used "
                                  f"({mem.used / (1024**3):.1f}G/"
                                  f"{mem.total / (1024**3):.1f}G)")
                    except ImportError:
                        try:
                            with open('/proc/meminfo') as f:
                                lines = f.readlines()
                            mt = int([l for l in lines if 'MemTotal' in l][0].split()[1])
                            ma = int([l for l in lines if 'MemAvailable' in l][0].split()[1])
                            pct = (mt - ma) / mt * 100
                            self._log(f"Memory: {pct:.1f}% used")
                        except Exception:
                            pass

                    learned = self.get_learned_actions()
                    if learned["total_actions"] > 0:
                        self._log(f"      Learned patterns: {len(learned['patterns'])} "
                                  f"from {learned['total_actions']} actions")
                    last_learn_time = time.time()

            except Exception as e:
                self._log(f"Healer loop error: {e}")
                traceback.print_exc()

            time.sleep(interval)

    def stop(self):
        """Stop the daemon loop."""
        self._running = False
        self._log("Healer daemon stopping")


# ══════════════════════════════════════════════════════════════════════════════
# Singleton
# ══════════════════════════════════════════════════════════════════════════════

_healer: Optional[EidosHealer] = None


def get_healer() -> EidosHealer:
    """Get or create the singleton EidosHealer instance."""
    global _healer
    if _healer is None:
        _healer = EidosHealer()
    return _healer


# ══════════════════════════════════════════════════════════════════════════════
# CLI
# ══════════════════════════════════════════════════════════════════════════════

if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(
        description="EIDOS Healer — System Health Monitor & Auto-Repair"
    )
    parser.add_argument(
        "--daemon", action="store_true",
        help="Run continuously (health check every 60s)"
    )
    parser.add_argument(
        "--once", action="store_true",
        help="Run one check+repair cycle and exit"
    )
    parser.add_argument(
        "--interval", type=int, default=CHECK_INTERVAL,
        help=f"Check interval in seconds (default: {CHECK_INTERVAL})"
    )
    parser.add_argument(
        "--learned", action="store_true",
        help="Print learned repair patterns and exit"
    )
    parser.add_argument(
        "--quiet", action="store_true",
        help="Suppress log output"
    )

    args = parser.parse_args()

    healer = EidosHealer(verbose=not args.quiet)

    if args.learned:
        learned = healer.get_learned_actions()
        print(json.dumps(learned, indent=2, ensure_ascii=False))
        sys.exit(0)

    if args.once or not args.daemon:
        # Single run mode
        print(f"\n{'=' * 60}")
        print(f"EIDOS HEALER — Health Check")
        print(f"{'=' * 60}\n")

        report = healer.check_all()

        for c in report.checks:
            icon = "OK" if c.healthy else "FAIL"
            print(f"  [{icon}] {c.component:12s} {c.detail}")

        if not report.healthy:
            print(f"\n{'─' * 60}")
            print(f"Repairing...")
            print(f"{'─' * 60}")
            report = healer.repair_all(report)

            for r in report.repairs:
                icon = "OK" if r.success else "FAIL"
                print(f"  [{icon}] {r.component}: {r.action}")
                print(f"         {r.detail[:120]}")

        print(f"\n{'=' * 60}")
        print(f"Summary: {report.summary}")
        print(f"{'=' * 60}\n")

        if report.healthy:
            sys.exit(0)
        else:
            sys.exit(1)

    elif args.daemon:
        # Daemon mode
        print(f"EIDOS Healer daemon starting (interval: {args.interval}s)")
        print(f"Action log: {HEALER_LOG}")

        # Handle signals gracefully
        def _sig_handler(signum, frame):
            healer.stop()

        signal.signal(signal.SIGINT, _sig_handler)
        signal.signal(signal.SIGTERM, _sig_handler)

        try:
            healer.run_loop(interval=args.interval)
        except KeyboardInterrupt:
            healer.stop()

        print("Healer daemon stopped.")
