"""
EIDOS core/proactive.py — Proactive Intelligence Layer
========================================================
Capa de inteligencia proactiva que conecta todos los subsistemas
para generar acciones útiles SIN que el usuario las pida.

Triggers:
- Health alerts → auto-remediation
- Pattern detection → optimization suggestions
- Idle time → maintenance tasks
- Goal deadlines → progress nudges

Modo lazy: solo actúa cuando tiene confianza alta y el impacto es positivo.

Uso:
    from core.proactive import get_proactive_agent
    pa = get_proactive_agent()
    pa.evaluate()  # Check all triggers and maybe act
"""
from __future__ import annotations

import logging
import os
import subprocess
import time
from dataclasses import dataclass, field
from typing import Callable, Optional

log = logging.getLogger("eidos.proactive")


@dataclass
class ProactiveAction:
    """An action the proactive agent wants to take."""
    id: str
    description: str
    category: str      # "health", "maintenance", "optimization", "notification"
    confidence: float  # 0.0-1.0
    command: str = ""  # shell command (if any)
    notification: str = ""  # message for user (if any)
    executed: bool = False
    result: str = ""
    timestamp: float = field(default_factory=time.time)


class ProactiveAgent:
    """
    Evaluates system state and generates autonomous helpful actions.

    Design principle: better to do nothing than to do something wrong.
    Only acts when confidence > threshold and action is reversible/safe.
    """

    CONFIDENCE_THRESHOLD = 0.7
    COOLDOWN_S = 300  # min seconds between actions of same category
    MAX_ACTIONS_PER_HOUR = 10

    def __init__(self):
        self._actions_log: list[ProactiveAction] = []
        self._last_action_by_cat: dict[str, float] = {}
        self._action_count_hour: int = 0
        self._hour_start: float = time.time()
        self._notify_fn: Optional[Callable] = None

        # Import subsystems lazily
        self._health = None
        self._routines = None
        self._brain_memory = None

    def _ensure_subsystems(self) -> None:
        """Lazy import of subsystems."""
        if self._health is None:
            try:
                from core.system_health import get_health_monitor
                self._health = get_health_monitor()
            except Exception:
                pass  # error no crítico, continuar
        if self._brain_memory is None:
            try:
                from core.brain_memory import get_brain_memory
                self._brain_memory = get_brain_memory()
            except Exception:
                pass  # error no crítico, continuar
    def set_notify_callback(self, fn: Callable[[str, str], None]) -> None:
        """Set callback for notifications: fn(title, message)."""
        self._notify_fn = fn

    # ── Main Evaluation ──────────────────────────────────────────────────────

    def evaluate(self) -> list[ProactiveAction]:
        """Evaluate all triggers and return actions to take.

        Call this periodically (e.g., every 5 minutes).
        """
        self._ensure_subsystems()
        self._reset_hour_counter()

        actions = []

        # Health-based actions
        actions.extend(self._evaluate_health())

        # Maintenance actions
        actions.extend(self._evaluate_maintenance())

        # Execute high-confidence actions
        executed = []
        for action in actions:
            if self._should_execute(action):
                self._execute(action)
                executed.append(action)

        return executed

    def _should_execute(self, action: ProactiveAction) -> bool:
        """Decide if an action should be executed."""
        if action.confidence < self.CONFIDENCE_THRESHOLD:
            return False

        # Rate limiting
        if self._action_count_hour >= self.MAX_ACTIONS_PER_HOUR:
            return False

        # Category cooldown
        last = self._last_action_by_cat.get(action.category, 0)
        if time.time() - last < self.COOLDOWN_S:
            return False

        return True

    def _execute(self, action: ProactiveAction) -> None:
        """Execute a proactive action."""
        log.info("Proactive action: [%s] %s (conf=%.0f%%)",
                 action.category, action.description, action.confidence * 100)

        try:
            if action.command:
                result = subprocess.run(
                    action.command, shell=True,
                    capture_output=True, text=True, timeout=30,
                )
                action.result = (result.stdout or result.stderr or "")[:300]
                action.executed = True

            if action.notification:
                self._notify(action.notification, action.category)
                action.executed = True

        except Exception as e:
            action.result = f"Error: {e}"
            log.error("Proactive action failed: %s", e)

        self._actions_log.append(action)
        self._last_action_by_cat[action.category] = time.time()
        self._action_count_hour += 1

        # Store in brain memory
        if self._brain_memory:
            try:
                self._brain_memory.store_episode(
                    task=f"proactive:{action.category}",
                    success=action.executed,
                    approach=action.description,
                    learned=action.result[:100],
                )
            except Exception:
                pass  # error no crítico, continuar
    def _notify(self, message: str, category: str) -> None:
        """Send notification to user."""
        if self._notify_fn:
            try:
                self._notify_fn(f"EIDOS [{category}]", message)
                return
            except Exception:
                pass  # error no crítico, continuar
        # Fallback: notify-send
        try:
            subprocess.run(
                ["notify-send", "--urgency=normal",
                 f"EIDOS [{category}]", message],
                capture_output=True, timeout=5,
                env={**os.environ, "DISPLAY": os.environ.get("DISPLAY", ":0")},
            )
        except Exception:
            pass  # error no crítico, continuar
    # ── Health Evaluators ────────────────────────────────────────────────────

    def _evaluate_health(self) -> list[ProactiveAction]:
        """Generate actions based on system health."""
        if not self._health:
            return []

        actions = []
        report = self._health.quick_check()

        # High RAM — kill known memory hogs or clear caches
        ram_pct = report.get("ram", {}).get("percent", 0)
        if ram_pct > 85:
            actions.append(ProactiveAction(
                id="health_ram_cleanup",
                description="Clear system caches to free RAM",
                category="health",
                confidence=0.9,
                command="sync && echo 1 | sudo tee /proc/sys/vm/drop_caches > /dev/null 2>&1; "
                        "find ~/.eidos/cache -mmin +30 -delete 2>/dev/null; echo 'caches cleared'",
                notification=f"RAM at {ram_pct}% — clearing caches",
            ))

        # Ollama offline — try to restart
        if not report.get("ollama", {}).get("online"):
            actions.append(ProactiveAction(
                id="health_ollama_restart",
                description="Restart Ollama service",
                category="health",
                confidence=0.8,
                command="systemctl --user restart ollama 2>/dev/null || "
                        "sudo systemctl restart ollama 2>/dev/null || "
                        "ollama serve &>/dev/null &",
                notification="Ollama was offline — attempting restart",
            ))

        # Low disk — clean old logs and cache
        disk_free = report.get("disk", {}).get("free_gb", 999)
        if disk_free < 5:
            actions.append(ProactiveAction(
                id="health_disk_cleanup",
                description="Clean old logs and cache to free disk",
                category="health",
                confidence=0.85,
                command="find ~/.eidos -name '*.log' -mtime +7 -delete 2>/dev/null; "
                        "find /tmp -user $USER -mtime +3 -delete 2>/dev/null; "
                        "docker system prune -f 2>/dev/null; echo 'cleaned'",
                notification=f"Disk low ({disk_free}GB free) — cleaning old files",
            ))

        return actions

    def _evaluate_maintenance(self) -> list[ProactiveAction]:
        """Generate maintenance actions."""
        actions = []

        # Consolidate brain memory (end of day or after many interactions)
        if self._brain_memory:
            try:
                stats = self._brain_memory.stats
                working_count = stats.get("working", {}).get("entries", 0)
                if working_count > 30:
                    actions.append(ProactiveAction(
                        id="maint_consolidate",
                        description="Consolidate working memory to long-term",
                        category="maintenance",
                        confidence=0.75,
                    ))
            except Exception:
                pass  # error no crítico, continuar
        return actions

    # ── Helpers ──────────────────────────────────────────────────────────────

    def _reset_hour_counter(self) -> None:
        if time.time() - self._hour_start > 3600:
            self._action_count_hour = 0
            self._hour_start = time.time()

    def get_recent_actions(self, limit: int = 10) -> list[dict]:
        return [
            {
                "id": a.id, "description": a.description,
                "category": a.category, "confidence": a.confidence,
                "executed": a.executed, "result": a.result[:100],
                "timestamp": a.timestamp,
            }
            for a in self._actions_log[-limit:]
        ]

    @property
    def stats(self) -> dict:
        return {
            "total_actions": len(self._actions_log),
            "actions_this_hour": self._action_count_hour,
            "threshold": self.CONFIDENCE_THRESHOLD,
            "categories": list(set(a.category for a in self._actions_log)),
        }


# ══════════════════════════════════════════════════════════════════════════════
#  SINGLETON
# ══════════════════════════════════════════════════════════════════════════════

_agent: Optional[ProactiveAgent] = None


def get_proactive_agent() -> ProactiveAgent:
    global _agent
    if _agent is None:
        _agent = ProactiveAgent()
    return _agent
