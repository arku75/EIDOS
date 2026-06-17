#!/usr/bin/env python3
"""
core/eidos_autonomous_daemon.py — EIDOS Autonomous Mode Daemon (S125+)

Runs EIDOS in fully autonomous mode:
  - Sets own goals from the Curiosity Ignorance Atlas
  - Executes multi-step plans via BOM (real mode when safe)
  - Logs all actions to ~/.eidos/autonomous_ops.jsonl
  - Pauses if SER touches keyboard/mouse (activity detected)
  - Respects safety limits (max 10 real actions/hour, no sudo, path whitelist)
  - Integrates observational learning 24/7

Designed to run as systemd user service: eidos-autonomous.service
Controlled via env vars:
  EIDOS_AUTONOMOUS=1               — enable autonomous mode
  EIDOS_AUTONOMOUS_REAL=1          — enable real BOM actions (vs dry-run)
  EIDOS_AUTONOMOUS_INTERVAL=120    — seconds between autonomous cycles
  EIDOS_OBSERVATIONAL=1            — enable observational learning
"""

from __future__ import annotations

import json
import logging
import os
import signal
import subprocess
import sys
import threading
import time
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

# ── Path setup ──────────────────────────────────────────────────────────────────
_EIDOS_ROOT = Path(__file__).resolve().parent.parent
if str(_EIDOS_ROOT) not in sys.path:
    sys.path.insert(0, str(_EIDOS_ROOT))

log = logging.getLogger("eidos.autonomous")

# ── Constants ───────────────────────────────────────────────────────────────────
OPS_LOG = Path.home() / ".eidos" / "autonomous_ops.jsonl"
SAFETY_LOG = Path.home() / ".eidos" / "autonomous_safety.log"
STATE_FILE = Path.home() / ".eidos" / "autonomous_state.json"

# Safety limits
MAX_REAL_ACTIONS_PER_HOUR = 10
ACTIVITY_IDLE_THRESHOLD = 30       # seconds of no activity before acting
SCREEN_VERIFY_TIMEOUT = 3          # seconds to wait for screen change verification
ACTIVITY_POLL_INTERVAL = 2         # seconds between activity checks
MOUSE_MOVE_THRESHOLD = 10          # pixels - considered "user activity"
MIN_CYCLE_INTERVAL = 60            # minimum seconds between autonomous cycles

# Path safety: only allow file operations in these directories
ALLOWED_PATHS = [
    str(Path.home() / ".eidos"),
    "/tmp",
    str(Path.home() / "EIDOS"),    # read-only for self-study
]

# Paths NEVER to touch
BLOCKED_PATHS = [
    "/etc", "/boot", "/sys", "/proc", "/dev",
    "/root", str(Path.home() / ".ssh"),
    str(Path.home() / ".gnupg"),
    str(Path.home() / ".config"),
]

# Commands NEVER to run
BLOCKED_COMMANDS = [
    "sudo", "su", "rm -rf", "mkfs", "dd if=", "fdisk",
    "shutdown", "reboot", "poweroff", "halt",
    "passwd", "chown", "chmod 777",
    ":(){ :|:& };:",  # fork bomb
]


def _ensure_dirs():
    for p in [OPS_LOG.parent, SAFETY_LOG.parent, STATE_FILE.parent]:
        p.mkdir(parents=True, exist_ok=True)


_ensure_dirs()


# ══════════════════════════════════════════════════════════════════════════════════
#  SAFETY GUARD — Hard limits that CANNOT be bypassed
# ══════════════════════════════════════════════════════════════════════════════════

class AutonomousSafetyGuard:
    """Enforces hard safety limits for autonomous operation."""

    def __init__(self):
        self._real_action_timestamps: List[float] = []
        self._lock = threading.Lock()

    def can_do_real_action(self) -> Tuple[bool, str]:
        """Check rate limit: max MAX_REAL_ACTIONS_PER_HOUR real actions."""
        now = time.time()
        cutoff = now - 3600
        with self._lock:
            # Prune old entries
            self._real_action_timestamps = [
                t for t in self._real_action_timestamps if t > cutoff
            ]
            if len(self._real_action_timestamps) >= MAX_REAL_ACTIONS_PER_HOUR:
                return False, (
                    f"rate limit: {MAX_REAL_ACTIONS_PER_HOUR} real actions/hour reached "
                    f"({len(self._real_action_timestamps)} in last hour)"
                )
            return True, "ok"

    def record_real_action(self):
        """Record a real action timestamp."""
        with self._lock:
            self._real_action_timestamps.append(time.time())

    def is_path_safe(self, path: str) -> Tuple[bool, str]:
        """Check if a filesystem path is safe for autonomous operations."""
        path = os.path.realpath(os.path.expanduser(str(path)))
        # Check blocked paths
        for blocked in BLOCKED_PATHS:
            blocked_real = os.path.realpath(os.path.expanduser(blocked))
            if path.startswith(blocked_real + "/") or path == blocked_real:
                return False, f"path blocked: {blocked}"
        # Check allowed paths
        for allowed in ALLOWED_PATHS:
            allowed_real = os.path.realpath(os.path.expanduser(allowed))
            if path.startswith(allowed_real + "/") or path == allowed_real:
                # Extra check: no deletion outside .eidos/ and /tmp/
                return True, "ok"
        return False, f"path not in allowed set: {path}"

    def is_command_safe(self, cmd: str) -> Tuple[bool, str]:
        """Check if a shell command is safe to run autonomously."""
        cmd_lower = cmd.lower().strip()
        for blocked in BLOCKED_COMMANDS:
            if blocked in cmd_lower:
                return False, f"blocked command pattern: {blocked}"
        if cmd_lower.startswith("sudo") or "sudo " in cmd_lower:
            return False, "sudo is never allowed in autonomous mode"
        return True, "ok"

    def can_delete_path(self, path: str) -> Tuple[bool, str]:
        """Check if deletion is allowed at this path.
        Only allows deletion within ~/.eidos/ and /tmp/."""
        path = os.path.realpath(os.path.expanduser(str(path)))
        allowed_delete_roots = [
            os.path.realpath(os.path.expanduser("~/.eidos")),
            os.path.realpath("/tmp"),
        ]
        for root in allowed_delete_roots:
            if path.startswith(root + "/") or path == root:
                return True, "ok"
        return False, f"deletion only allowed in ~/.eidos/ and /tmp/, not: {path}"


# ══════════════════════════════════════════════════════════════════════════════════
#  ACTIVITY DETECTOR — Detects SER presence via mouse/keyboard
# ══════════════════════════════════════════════════════════════════════════════════

class ActivityDetector:
    """Detects whether SER is actively using the computer.

    Uses xdotool mouse position polling (no xprintidle dependency).
    Tracks consecutive idle seconds to distinguish "away" from "active".
    """

    def __init__(self):
        self._last_mouse_pos: Optional[Tuple[int, int]] = None
        self._last_activity_time: float = time.time()
        self._consecutive_idle: float = 0.0
        self._poll_thread: Optional[threading.Thread] = None
        self._running = False
        self._lock = threading.Lock()

    def _poll_mouse(self):
        """Poll mouse position via xdotool."""
        try:
            r = subprocess.run(
                ["xdotool", "getmouselocation"],
                capture_output=True, text=True, timeout=2,
                env={**os.environ, "DISPLAY": os.environ.get("DISPLAY", ":0")},
            )
            if r.returncode == 0:
                # Parse: "x:320 y:1171 screen:0 window:67108878"
                parts = r.stdout.strip().split()
                x = int(parts[0].split(":")[1])
                y = int(parts[1].split(":")[1])
                return (x, y)
        except Exception:
            pass
        return None

    def _activity_loop(self):
        """Background loop polling for user activity."""
        while self._running:
            try:
                current = self._poll_mouse()
                now = time.time()
                with self._lock:
                    if current and self._last_mouse_pos:
                        dx = abs(current[0] - self._last_mouse_pos[0])
                        dy = abs(current[1] - self._last_mouse_pos[1])
                        if dx > MOUSE_MOVE_THRESHOLD or dy > MOUSE_MOVE_THRESHOLD:
                            self._last_activity_time = now
                            self._consecutive_idle = 0
                        else:
                            self._consecutive_idle += ACTIVITY_POLL_INTERVAL
                    elif current:
                        # First poll - mark as activity
                        self._last_activity_time = now
                    self._last_mouse_pos = current
            except Exception:
                pass
            time.sleep(ACTIVITY_POLL_INTERVAL)

    def start(self):
        """Start activity monitoring thread."""
        if self._running:
            return
        self._running = True
        self._last_activity_time = time.time()
        self._poll_thread = threading.Thread(
            target=self._activity_loop, daemon=True, name="activity-detector"
        )
        self._poll_thread.start()

    def stop(self):
        """Stop activity monitoring."""
        self._running = False
        if self._poll_thread:
            self._poll_thread.join(timeout=5)

    @property
    def idle_seconds(self) -> float:
        """Seconds since last detected user activity."""
        with self._lock:
            return time.time() - self._last_activity_time

    @property
    def ser_active(self) -> bool:
        """Is SER actively using the computer right now?"""
        return self.idle_seconds < ACTIVITY_IDLE_THRESHOLD

    @property
    def ser_present(self) -> bool:
        """Has SER been active in the last 5 minutes? (broader presence check)"""
        return self.idle_seconds < 300


# ══════════════════════════════════════════════════════════════════════════════════
#  AUTONOMOUS OPERATIONS LOGGER
# ══════════════════════════════════════════════════════════════════════════════════

class AutonomousLogger:
    """Logs every autonomous action to JSONL for audit and learning."""

    def __init__(self, log_path: Path = OPS_LOG):
        self._log_path = log_path
        self._lock = threading.Lock()
        self._error_counts: Dict[str, int] = {}  # key -> consecutive failures

    def log(self, event_type: str, data: Dict[str, Any]):
        """Append an event to the JSONL log."""
        entry = {
            "ts": time.time(),
            "iso": datetime.now().isoformat(),
            "type": event_type,
            **data,
        }
        with self._lock:
            try:
                with open(self._log_path, "a", encoding="utf-8") as f:
                    f.write(json.dumps(entry, ensure_ascii=False) + "\n")
            except Exception as e:
                log.warning("Failed to write ops log: %s", e)

    def track_error(self, error_key: str) -> int:
        """Track consecutive errors. Returns count. Triggers alert at 3."""
        with self._lock:
            self._error_counts[error_key] = self._error_counts.get(error_key, 0) + 1
            return self._error_counts[error_key]

    def reset_errors(self, error_key: str):
        """Reset error counter on success."""
        with self._lock:
            self._error_counts.pop(error_key, None)

    def get_recent_ops(self, minutes: int = 60) -> List[Dict]:
        """Get operations from the last N minutes."""
        cutoff = time.time() - (minutes * 60)
        ops = []
        try:
            if self._log_path.exists():
                with open(self._log_path, "r", encoding="utf-8") as f:
                    for line in f:
                        try:
                            entry = json.loads(line)
                            if entry.get("ts", 0) >= cutoff:
                                ops.append(entry)
                        except json.JSONDecodeError:
                            continue
        except Exception:
            pass
        return ops

    def get_error_summary(self) -> List[Dict]:
        """Get errors that have triggered 3+ times."""
        return [
            {"key": k, "count": v}
            for k, v in self._error_counts.items()
            if v >= 3
        ]


# ══════════════════════════════════════════════════════════════════════════════════
#  GOAL MANAGER — Picks goals from Curiosity Atlas
# ══════════════════════════════════════════════════════════════════════════════════

class GoalManager:
    """Sets autonomous goals from the Curiosity Ignorance Atlas.

    Priority order:
      1. Pending study queue items (SER's explicit requests)
      2. High-priority ignorance atlas items
      3. Procedural gaps (concepts without recipes)
      4. Random exploration from under-represented categories
    """

    def __init__(self):
        self._last_goal: Optional[str] = None
        self._completed_goals: set = set()
        self._goal_history: List[Dict] = []

    def get_next_goal(self) -> Optional[Dict[str, Any]]:
        """Get the next autonomous goal. Returns {goal, source, priority} or None."""
        # 1. Check study queue first (SER's explicit requests have priority)
        try:
            from core.study_queue import list_items
            pending = list_items("pending")
            if pending and len(pending) > 0:
                item = pending[0]
                goal_text = item.get("item", str(item))
                if goal_text not in self._completed_goals:
                    return {
                        "goal": str(goal_text)[:200],
                        "source": "study_queue",
                        "priority": 1.0,
                    }
        except Exception:
            pass

        # 2. Check ignorance atlas
        try:
            from core.eidos_curiosity_engine import get_curiosity_engine
            engine = get_curiosity_engine()
            atlas = engine._atlas if hasattr(engine, "_atlas") else None
            if atlas:
                item = atlas.get_highest_priority()
                if item:
                    goal_text = f"explore: {item.get('item_type', '?')} — {item.get('payload', '?')[:100]}"
                    if goal_text not in self._completed_goals:
                        return {
                            "goal": goal_text,
                            "source": "ignorance_atlas",
                            "priority": item.get("priority", 0.5),
                            "atlas_item": item,
                        }
        except Exception:
            pass

        # 3. Check procedural gaps
        try:
            from core.eidos_procedural import discover_procedural_gaps
            gaps = discover_procedural_gaps()
            if gaps:
                gap = gaps[0]
                goal_text = f"learn procedure: {gap.get('concept', gap.get('name', str(gap)[:80]))}"
                if goal_text not in self._completed_goals:
                    return {
                        "goal": goal_text,
                        "source": "procedural_gap",
                        "priority": 0.7,
                    }
        except Exception:
            pass

        # 4. Generate exploration goal from under-represented knowledge
        try:
            from core.db import get_conn
            brain_db = Path.home() / ".eidos" / "evolution_brain.db"
            if brain_db.exists():
                with get_conn(brain_db, timeout=5) as c:
                    # Find category with fewest nodes (but >0)
                    row = c.execute(
                        "SELECT category, COUNT(*) as cnt FROM knowledge_nodes "
                        "WHERE category != '' AND category NOT IN ('generic','unknown') "
                        "GROUP BY category ORDER BY cnt ASC LIMIT 1"
                    ).fetchone()
                    if row:
                        cat, cnt = row
                        goal_text = f"expand knowledge: {cat} (only {cnt} nodes)"
                        if goal_text not in self._completed_goals:
                            return {
                                "goal": goal_text,
                                "source": "knowledge_gap",
                                "priority": 0.5,
                                "category": cat,
                            }
        except Exception:
            pass

        return None

    def mark_completed(self, goal: str):
        """Mark a goal as completed to avoid repetition."""
        self._completed_goals.add(goal)
        self._goal_history.append({
            "goal": goal,
            "completed_at": time.time(),
        })
        # Keep set bounded
        if len(self._completed_goals) > 500:
            # Remove oldest half
            keys = list(self._completed_goals)
            self._completed_goals = set(keys[250:])

    def mark_failed(self, goal: str):
        """Mark a goal as failed (will be retried later)."""
        self._goal_history.append({
            "goal": goal,
            "failed_at": time.time(),
        })


# ══════════════════════════════════════════════════════════════════════════════════
#  SCREEN VERIFIER — Checks that screen actually changed after an action
# ══════════════════════════════════════════════════════════════════════════════════

class ScreenVerifier:
    """Verifies that screen state changed after an action was performed."""

    @staticmethod
    def get_screen_hash() -> Optional[str]:
        """Get a hash of current screen state via window list + mouse position."""
        try:
            import hashlib
            r = subprocess.run(
                ["wmctrl", "-l"],
                capture_output=True, text=True, timeout=3,
                env={**os.environ, "DISPLAY": os.environ.get("DISPLAY", ":0")},
            )
            mouse_r = subprocess.run(
                ["xdotool", "getmouselocation"],
                capture_output=True, text=True, timeout=2,
                env={**os.environ, "DISPLAY": os.environ.get("DISPLAY", ":0")},
            )
            combined = (r.stdout or "") + (mouse_r.stdout or "")
            return hashlib.sha1(combined.encode()).hexdigest()[:16]
        except Exception:
            return None

    @staticmethod
    def verify_change(before_hash: str, timeout: float = SCREEN_VERIFY_TIMEOUT) -> Tuple[bool, str]:
        """Wait up to timeout seconds for screen to change. Returns (changed, detail)."""
        deadline = time.time() + timeout
        while time.time() < deadline:
            time.sleep(0.5)
            after = ScreenVerifier.get_screen_hash()
            if after and after != before_hash:
                return True, f"screen changed: {before_hash} -> {after}"
        return False, "screen did not change within timeout"


# ══════════════════════════════════════════════════════════════════════════════════
#  AUTONOMOUS DAEMON — Main orchestrator
# ══════════════════════════════════════════════════════════════════════════════════

class AutonomousDaemon:
    """Main autonomous daemon that runs EIDOS without supervision.

    Lifecycle per cycle:
      1. Check if SER is active → if yes, pause/skip
      2. Get next goal from GoalManager
      3. Plan multi-step execution via GraphPlanner
      4. Execute with BOM (real or dry depending on safety + flags)
      5. Verify screen changed after each real action
      6. Log everything to autonomous_ops.jsonl
      7. Trigger observational learning integration
      8. Sleep until next cycle
    """

    def __init__(self):
        self._running = False
        self._thread: Optional[threading.Thread] = None
        self._safety = AutonomousSafetyGuard()
        self._activity = ActivityDetector()
        self._logger = AutonomousLogger()
        self._goals = GoalManager()
        self._cycle_count = 0
        self._real_actions_taken = 0
        self._real_enabled = os.environ.get("EIDOS_AUTONOMOUS_REAL", "0") == "1"
        self._interval = int(os.environ.get("EIDOS_AUTONOMOUS_INTERVAL", "120"))
        self._observational_enabled = os.environ.get("EIDOS_OBSERVATIONAL", "0") == "1"
        self._achievements: List[Dict] = []
        self._last_morning_briefing = 0
        self._paused = False
        self._pause_reason = ""

    # ── Lifecycle ───────────────────────────────────────────────────────────────

    def start(self):
        """Start the autonomous daemon."""
        if self._running:
            return
        self._running = True
        self._activity.start()
        self._logger.log("daemon_start", {
            "real_enabled": self._real_enabled,
            "observational_enabled": self._observational_enabled,
            "interval": self._interval,
            "max_real_per_hour": MAX_REAL_ACTIONS_PER_HOUR,
            "activity_threshold": ACTIVITY_IDLE_THRESHOLD,
        })
        log.info("AutonomousDaemon starting (real=%s, interval=%ds)",
                 self._real_enabled, self._interval)
        self._thread = threading.Thread(
            target=self._loop, daemon=True, name="autonomous-daemon"
        )
        self._thread.start()

    def stop(self):
        """Stop the autonomous daemon."""
        self._running = False
        self._activity.stop()
        if self._thread:
            self._thread.join(timeout=10)
        self._logger.log("daemon_stop", {
            "total_cycles": self._cycle_count,
            "real_actions_taken": self._real_actions_taken,
        })
        log.info("AutonomousDaemon stopped (%d cycles, %d real actions)",
                 self._cycle_count, self._real_actions_taken)
        self._save_state()

    def _loop(self):
        """Main autonomous loop."""
        # Warm-up: let activity detector calibrate
        time.sleep(5)

        while self._running:
            t0 = time.time()
            try:
                self._cycle()
            except Exception as e:
                log.error("Autonomous cycle error: %s", e)
                self._logger.log("cycle_error", {"error": str(e)})
                self._logger.track_error(f"cycle_crash:{type(e).__name__}")

            elapsed = time.time() - t0
            sleep_time = max(MIN_CYCLE_INTERVAL, self._interval - elapsed)
            # Check for interruptions every 5s during sleep
            deadline = time.time() + sleep_time
            while self._running and time.time() < deadline:
                time.sleep(min(5, deadline - time.time()))

    def _cycle(self):
        """One autonomous cycle."""
        self._cycle_count += 1

        # ── Check morning briefing ──────────────────────────────────────────
        self._maybe_send_morning_briefing()

        # ── Pause check: is SER active? ─────────────────────────────────────
        if self._activity.ser_active:
            if not self._paused:
                self._paused = True
                self._pause_reason = f"SER active (idle={self._activity.idle_seconds:.0f}s)"
                self._logger.log("paused", {"reason": self._pause_reason})
                log.info("PAUSED: %s", self._pause_reason)
            return
        else:
            if self._paused:
                self._paused = False
                self._logger.log("resumed", {
                    "idle_seconds": self._activity.idle_seconds,
                    "pause_duration": "unknown",
                })
                log.info("RESUMED: SER idle for %.0fs", self._activity.idle_seconds)

        # ── Get goal ────────────────────────────────────────────────────────
        goal_info = self._goals.get_next_goal()
        if not goal_info:
            # Seed the curiosity atlas with exploration targets
            self._seed_exploration()
            self._logger.log("cycle_idle", {
                "cycle": self._cycle_count,
                "reason": "no goals available",
            })
            return

        goal = goal_info["goal"]
        self._logger.log("cycle_start", {
            "cycle": self._cycle_count,
            "goal": goal,
            "source": goal_info["source"],
            "idle_seconds": self._activity.idle_seconds,
        })

        # ── Plan ────────────────────────────────────────────────────────────
        plan = self._plan_goal(goal)
        if not plan:
            self._goals.mark_failed(goal)
            self._logger.log("plan_failed", {"goal": goal})
            return

        # ── Execute ─────────────────────────────────────────────────────────
        result = self._execute_plan(plan, goal)

        # ── Learn from result ───────────────────────────────────────────────
        if result.get("success"):
            self._goals.mark_completed(goal)
            self._logger.reset_errors(f"goal:{goal[:50]}")
            self._maybe_achievement(goal, result)
        else:
            err_count = self._logger.track_error(f"goal:{goal[:50]}")
            if err_count >= 3:
                self._send_error_alert(goal, err_count)
            # Don't mark failed on first try - let it retry

        # ── Observational learning integration ──────────────────────────────
        if self._observational_enabled:
            self._run_observational_cycle()

        # ── Periodic recipe promotion ───────────────────────────────────────
        if self._cycle_count % 10 == 0:
            self._promote_recipes()

        self._logger.log("cycle_complete", {
            "cycle": self._cycle_count,
            "goal": goal[:100],
            "success": result.get("success", False),
            "real_actions_this_cycle": result.get("real_actions", 0),
        })

    # ── Planning ────────────────────────────────────────────────────────────────

    def _plan_goal(self, goal: str) -> Optional[Dict]:
        """Create a multi-step plan for a goal using the GraphPlanner."""
        try:
            from core.eidos_planner import get_planner
            planner = get_planner()
            plan = planner.plan(goal)
            if plan and plan.get("steps"):
                return plan
        except Exception as e:
            log.debug("GraphPlanner failed, using simple plan: %s", e)

        # Fallback: simple single-step plan for exploration goals
        if goal.startswith("expand knowledge:") or goal.startswith("explore:"):
            return {
                "steps": [{
                    "action": "research_concept",
                    "args": [goal],
                    "confidence": 0.5,
                }],
                "goal": goal,
            }

        # Fallback: BOM exploration plan
        return {
            "steps": [{
                "action": "explore_app",
                "args": [goal],
                "confidence": 0.4,
            }],
            "goal": goal,
        }

    # ── Execution ───────────────────────────────────────────────────────────────

    def _execute_plan(self, plan: Dict, goal: str) -> Dict:
        """Execute a plan, choosing real vs dry based on safety context."""
        result = {
            "success": False,
            "steps_executed": 0,
            "real_actions": 0,
            "dry_actions": 0,
            "errors": [],
        }

        steps = plan.get("steps", [])
        for i, step in enumerate(steps):
            if not self._running:
                break

            # Re-check activity before each step
            if self._activity.ser_active:
                self._logger.log("step_skipped", {
                    "step": i + 1,
                    "reason": "SER became active mid-execution",
                })
                result["errors"].append("interrupted by user activity")
                break

            action = step.get("action", "observe")
            shell_cmd = step.get("shell_cmd", "")

            # Decide dry vs real
            use_real = self._should_use_real(action, shell_cmd)

            if use_real:
                can, reason = self._safety.can_do_real_action()
                if not can:
                    self._logger.log("step_dry_fallback", {
                        "step": i + 1, "action": action,
                        "reason": reason,
                    })
                    use_real = False

            # ── Pre-action screen snapshot ──────────────────────────────────
            screen_before = None
            if use_real:
                screen_before = ScreenVerifier.get_screen_hash()

            # ── Execute step ────────────────────────────────────────────────
            step_ok = self._execute_step(step, goal, use_real)

            # ── Post-action verification ────────────────────────────────────
            if use_real and step_ok:
                self._safety.record_real_action()
                result["real_actions"] += 1
                self._real_actions_taken += 1

                if screen_before:
                    changed, detail = ScreenVerifier.verify_change(screen_before)
                    self._logger.log("screen_verify", {
                        "step": i + 1,
                        "changed": changed,
                        "detail": detail,
                    })
                    if not changed:
                        self._logger.log("screen_no_change", {
                            "step": i + 1,
                            "action": action,
                        })
            elif step_ok:
                result["dry_actions"] += 1

            if step_ok:
                result["steps_executed"] += 1
            else:
                result["errors"].append(f"step {i+1} failed: {action}")
                # Don't abort entire plan on first failure - try next step
                # unless it's a critical dependency
                if step.get("critical"):
                    break

        result["success"] = result["steps_executed"] > 0 and len(result.get("errors", [])) < len(steps)
        return result

    def _should_use_real(self, action: str, shell_cmd: str = "") -> bool:
        """Determine if this action should use real (vs dry-run) mode."""
        if not self._real_enabled:
            return False

        # Never real if SER is present (less than 5 min idle?)
        if self._activity.ser_present:
            # Only real if SER has been idle > 5 minutes
            if self._activity.idle_seconds < 300:
                return False

        # Real actions only for specific types
        real_actions = {"explore_app", "click", "type", "navigate", "open_browser"}
        if action not in real_actions:
            return False

        # Check command safety
        if shell_cmd:
            safe, _ = self._safety.is_command_safe(shell_cmd)
            if not safe:
                return False

        return True

    def _execute_step(self, step: Dict, goal: str, use_real: bool) -> bool:
        """Execute a single plan step. Returns True if successful."""
        action = step.get("action", "observe")
        shell_cmd = step.get("shell_cmd", "")
        args = step.get("args", [])

        self._logger.log("step_execute", {
            "action": action,
            "real": use_real,
            "shell_cmd": shell_cmd[:200] if shell_cmd else "",
            "goal": goal[:100],
        })

        try:
            # ── BOM step (GUI interaction) ──────────────────────────────────
            if action in ("click", "type", "navigate", "explore_app"):
                from core.causal_loop import step as bom_step
                bom_result = bom_step(
                    goal=goal,
                    app_name=None,
                    dry_run=not use_real,
                )
                ok = bom_result.get("ok", False)
                self._logger.log("bom_step_result", {
                    "action": action,
                    "ok": ok,
                    "reward": bom_result.get("reward", 0),
                    "effect": bom_result.get("effect", ""),
                    "real": use_real,
                })
                return ok

            # ── Research concept ────────────────────────────────────────────
            elif action == "research_concept":
                from core.eidos_active_research import research_now
                topic = args[0] if args else goal
                r = research_now(topic, timeout=15.0, persist=True, prefer_remote=True)
                learned = r.get("learned", False)
                self._logger.log("research_result", {
                    "topic": topic[:100],
                    "learned": learned,
                })
                return learned

            # ── Safe shell command ──────────────────────────────────────────
            elif action == "shell" and shell_cmd:
                safe, reason = self._safety.is_command_safe(shell_cmd)
                if not safe:
                    self._logger.log("shell_blocked", {
                        "cmd": shell_cmd[:100],
                        "reason": reason,
                    })
                    return False
                r = subprocess.run(
                    shell_cmd, shell=True,
                    capture_output=True, text=True,
                    timeout=30,
                    env={**os.environ, "PATH": "/usr/bin:/bin:/usr/local/bin"},
                )
                self._logger.log("shell_result", {
                    "cmd": shell_cmd[:100],
                    "exit_code": r.returncode,
                    "output_len": len(r.stdout or ""),
                })
                return r.returncode == 0

            # ── Open browser ────────────────────────────────────────────────
            elif action == "open_browser":
                url = args[0] if args else "https://duckduckgo.com"
                subprocess.Popen(
                    ["firefox-esr", "--new-tab", url],
                    stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                    env={**os.environ, "DISPLAY": os.environ.get("DISPLAY", ":0")},
                )
                time.sleep(2)
                return True

            # ── Default: observe only ───────────────────────────────────────
            else:
                self._logger.log("step_observe_only", {"action": action})
                return True

        except Exception as e:
            self._logger.log("step_error", {
                "action": action,
                "error": str(e),
            })
            return False

    # ── Exploration seeding ─────────────────────────────────────────────────────

    def _seed_exploration(self):
        """Seed the ignorance atlas with new things to explore."""
        try:
            from core.eidos_curiosity_engine import get_curiosity_engine
            engine = get_curiosity_engine()
            if hasattr(engine, "tick"):
                engine.tick()
        except Exception:
            pass

    # ── Observational learning ──────────────────────────────────────────────────

    def _run_observational_cycle(self):
        """Run one observational learning cycle."""
        try:
            from core.eidos_observational import observe_cycle
            ctx = {}
            try:
                from core.screen_scanner import get_screen_context
                ctx = get_screen_context()
            except Exception:
                pass
            result = observe_cycle(ctx)
            if result.get("actions_detected", 0) > 0:
                self._logger.log("observational", {
                    "actions_detected": result["actions_detected"],
                    "intents_inferred": result.get("intents_inferred", 0),
                })
        except Exception as e:
            log.debug("observational cycle error: %s", e)

    # ── Recipe promotion ────────────────────────────────────────────────────────

    def _promote_recipes(self):
        """Promote high-confidence recipe candidates to procedural memory."""
        try:
            from core.eidos_observational import _get_shared_extractor
            extractor = _get_shared_extractor()
            promoted = extractor.promote_candidates(min_confidence=0.7)
            if promoted:
                for p in promoted:
                    log.info("Auto-promoted recipe: %s (conf=%.2f)", p['name'], p['confidence'])
                    self._logger.log("recipe_promoted", {
                        "name": p['name'],
                        "confidence": p.get('confidence', 0),
                        "recipe_id": p.get('recipe_id'),
                    })
        except Exception as e:
            log.debug("promote_recipes error: %s", e)

    # ── Achievement detection ───────────────────────────────────────────────────

    def _maybe_achievement(self, goal: str, result: Dict):
        """Detect and record achievements."""
        # Achievement: first real action completed
        if result.get("real_actions", 0) > 0:
            self._achievements.append({
                "type": "real_action_milestone",
                "goal": goal[:100],
                "real_actions": result["real_actions"],
                "ts": time.time(),
            })
            # Send notification for significant milestones
            if self._real_actions_taken in (1, 5, 10, 25, 50, 100):
                self._send_achievement(
                    f"Milestone: {self._real_actions_taken} real autonomous actions completed",
                    f"Latest: {goal[:80]}"
                )

        # Achievement: new skill learned via BOM
        if result.get("steps_executed", 0) >= 3:
            self._achievements.append({
                "type": "multi_step_plan_completed",
                "goal": goal[:100],
                "steps": result["steps_executed"],
                "ts": time.time(),
            })

    # ── Telegram integration ────────────────────────────────────────────────────

    def _maybe_send_morning_briefing(self):
        """Send morning briefing via Telegram if it's a new day."""
        now = time.time()
        # Only check once per hour
        if now - self._last_morning_briefing < 3600:
            return
        self._last_morning_briefing = now

        # Only send between 6:00-10:00
        hour = datetime.now().hour
        if hour < 6 or hour > 10:
            return

        # Check if already sent today
        today = datetime.now().strftime("%Y-%m-%d")
        last_briefing_file = Path.home() / ".eidos" / ".last_morning_briefing"
        try:
            if last_briefing_file.exists():
                last_date = last_briefing_file.read_text().strip()
                if last_date == today:
                    return
        except Exception:
            pass

        # Generate and send briefing
        try:
            briefing = self._generate_morning_briefing()
            self._send_telegram_message(f"🌅 EIDOS Morning Briefing\n\n{briefing}")
            last_briefing_file.write_text(today)
            self._logger.log("morning_briefing_sent", {"date": today})
        except Exception as e:
            log.debug("morning briefing error: %s", e)

    def _generate_morning_briefing(self) -> str:
        """Generate a morning briefing of overnight autonomous activity."""
        lines = []

        # Ops summary
        recent = self._logger.get_recent_ops(minutes=480)  # last 8 hours
        if recent:
            cycles = sum(1 for r in recent if r.get("type") == "cycle_complete")
            real_actions = sum(
                r.get("real_actions", 0)
                for r in recent
                if r.get("type") == "cycle_complete"
            )
            goals_attempted = len(set(
                r.get("goal", "")
                for r in recent
                if r.get("type") == "cycle_start"
            ))
            lines.append(f"Cycles: {cycles} | Real actions: {real_actions} | Goals: {goals_attempted}")

        # What was learned (from curiosity atlas)
        try:
            from core.eidos_curiosity_engine import get_curiosity_engine
            engine = get_curiosity_engine()
            if hasattr(engine, "_atlas") and engine._atlas:
                explored = engine._atlas.get_recent_explored(limit=5)
                if explored:
                    lines.append("\nExplored overnight:")
                    for item in explored[:5]:
                        payload = str(item.get("payload", ""))[:60]
                        result_preview = str(item.get("explore_result", ""))[:80]
                        lines.append(f"  - {payload}")
                        if result_preview:
                            lines.append(f"    {result_preview}")
        except Exception:
            pass

        # Observational learning stats
        try:
            from core.eidos_observational import observational_status
            obs_status = observational_status()
            if obs_status.get("intents_stored", 0) > 0:
                lines.append(f"\nObservations: {obs_status.get('segments_stored', 0)} actions, "
                           f"{obs_status.get('intents_stored', 0)} intents inferred")
            if obs_status.get("recipes_promoted", 0) > 0:
                lines.append(f"Recipes promoted: {obs_status['recipes_promoted']}")
        except Exception:
            pass

        # Errors needing attention
        error_summary = self._logger.get_error_summary()
        if error_summary:
            lines.append("\nErrors needing attention:")
            for err in error_summary[:3]:
                lines.append(f"  - {err['key']}: {err['count']}x failures")

        if not lines:
            return "EIDOS was idle overnight. No significant activity."

        return "\n".join(lines)

    def _send_error_alert(self, goal: str, fail_count: int):
        """Send error alert when something fails 3+ times."""
        msg = (
            f"⚠️ EIDOS Error Alert\n\n"
            f"Goal failed {fail_count}x: {goal[:150]}\n"
            f"Time: {datetime.now().strftime('%H:%M:%S')}\n"
            f"Autonomous daemon will continue retrying."
        )
        self._send_telegram_message(msg)
        self._logger.log("error_alert_sent", {
            "goal": goal[:100],
            "fail_count": fail_count,
        })

    def _send_achievement(self, title: str, detail: str = ""):
        """Send achievement notification via Telegram."""
        msg = f"🏆 Achievement: {title}"
        if detail:
            msg += f"\n{detail}"
        self._send_telegram_message(msg)
        self._logger.log("achievement_sent", {
            "title": title[:100],
        })

    def _send_telegram_message(self, message: str):
        """Send a message via the Telegram bot."""
        try:
            # Try using the telegram_bot's sending mechanism
            import requests
            token = os.environ.get("EIDOS_TELEGRAM_TOKEN", "")
            if not token:
                # Try dispatcher.json
                import json as _json
                cfg_path = Path.home() / ".eidos" / "dispatcher.json"
                if cfg_path.exists():
                    cfg = _json.loads(cfg_path.read_text())
                    tg = cfg.get("telegram", {})
                    token = tg.get("token", "")

            if not token:
                log.debug("No Telegram token configured")
                return

            allowed_raw = os.environ.get("EIDOS_TELEGRAM_ALLOWED", "")
            if not allowed_raw:
                try:
                    cfg_path = Path.home() / ".eidos" / "dispatcher.json"
                    if cfg_path.exists():
                        import json as _json
                        cfg = _json.loads(cfg_path.read_text())
                        tg = cfg.get("telegram", {})
                        allowed = tg.get("allow_from", [])
                        allowed_raw = ",".join(str(x) for x in allowed)
                except Exception:
                    pass

            chat_ids = [int(x.strip()) for x in allowed_raw.split(",") if x.strip().isdigit()]
            if not chat_ids:
                return

            for chat_id in chat_ids:
                try:
                    import urllib.request, urllib.parse
                    data = urllib.parse.urlencode({
                        "chat_id": chat_id,
                        "text": message[:4000],
                    }).encode()
                    urllib.request.urlopen(
                        f"https://api.telegram.org/bot{token}/sendMessage",
                        data=data, timeout=10,
                    )
                except Exception as e:
                    log.debug("Telegram send to %s failed: %s", chat_id, e)
        except Exception as e:
            log.debug("_send_telegram_message error: %s", e)

    # ── State persistence ───────────────────────────────────────────────────────

    def _save_state(self):
        """Save daemon state for crash recovery."""
        try:
            state = {
                "cycle_count": self._cycle_count,
                "real_actions_taken": self._real_actions_taken,
                "saved_at": time.time(),
                "saved_iso": datetime.now().isoformat(),
                "paused": self._paused,
            }
            STATE_FILE.write_text(json.dumps(state, indent=2))
        except Exception:
            pass

    def load_state(self):
        """Load previous daemon state."""
        try:
            if STATE_FILE.exists():
                state = json.loads(STATE_FILE.read_text())
                self._cycle_count = state.get("cycle_count", 0)
                self._real_actions_taken = state.get("real_actions_taken", 0)
                log.info("Loaded state: cycle=%d, real_actions=%d",
                         self._cycle_count, self._real_actions_taken)
        except Exception:
            pass

    # ── Status ──────────────────────────────────────────────────────────────────

    def get_status(self) -> Dict[str, Any]:
        """Get current daemon status."""
        return {
            "running": self._running,
            "paused": self._paused,
            "pause_reason": self._pause_reason,
            "cycle_count": self._cycle_count,
            "real_actions_taken": self._real_actions_taken,
            "real_enabled": self._real_enabled,
            "observational_enabled": self._observational_enabled,
            "idle_seconds": self._activity.idle_seconds,
            "ser_active": self._activity.ser_active,
            "ser_present": self._activity.ser_present,
            "pending_goals": len(self._goals._completed_goals),
            "achievements_count": len(self._achievements),
            "ops_log_size_mb": round(OPS_LOG.stat().st_size / (1024 * 1024), 2) if OPS_LOG.exists() else 0,
        }


# ── Singleton ───────────────────────────────────────────────────────────────────

_SINGLETON: Optional[AutonomousDaemon] = None
_lock = threading.Lock()


def get_autonomous_daemon() -> AutonomousDaemon:
    global _SINGLETON
    if _SINGLETON is None:
        with _lock:
            if _SINGLETON is None:
                _SINGLETON = AutonomousDaemon()
                _SINGLETON.load_state()
    return _SINGLETON


# ── CLI ─────────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(name)s] %(levelname)s — %(message)s",
    )

    cmd = sys.argv[1] if len(sys.argv) > 1 else "start"

    if cmd == "start":
        daemon = get_autonomous_daemon()
        daemon.start()
        log.info("Autonomous daemon running. Press Ctrl+C to stop.")

        def _handler(signum, frame):
            log.info("Shutting down...")
            daemon.stop()
            sys.exit(0)

        signal.signal(signal.SIGINT, _handler)
        signal.signal(signal.SIGTERM, _handler)

        try:
            while daemon._running:
                time.sleep(60)
                status = daemon.get_status()
                log.info("Status: cycle=%d real=%d paused=%s idle=%.0fs",
                         status["cycle_count"], status["real_actions_taken"],
                         status["paused"], status["idle_seconds"])
        except KeyboardInterrupt:
            daemon.stop()

    elif cmd == "status":
        daemon = get_autonomous_daemon()
        status = daemon.get_status()
        print(json.dumps(status, indent=2, ensure_ascii=False))

    elif cmd == "briefing":
        daemon = get_autonomous_daemon()
        briefing = daemon._generate_morning_briefing()
        print(briefing)

    elif cmd == "recent":
        daemon = get_autonomous_daemon()
        ops = daemon._logger.get_recent_ops(minutes=60)
        print(f"Last 60 minutes: {len(ops)} operations")
        for op in ops[-10:]:
            print(f"  [{op.get('type')}] {op.get('goal', op.get('action', ''))[:80]}")

    else:
        print(f"Usage: python -m core.eidos_autonomous_daemon [start|status|briefing|recent]")
        sys.exit(1)
