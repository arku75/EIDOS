"""
EIDOS core/smart_prioritizer.py — Smart Goal Prioritizer
==========================================================
Prioriza objetivos automáticamente basándose en:
- Urgencia (deadline proximity)
- Impacto (tags, dependencies)
- Esfuerzo estimado
- Contexto del sistema (health, resources)
- Historial de éxito (goals similares completados)

Uso:
    from core.smart_prioritizer import get_prioritizer
    p = get_prioritizer()
    ranked = p.prioritize(goals)  # Returns goals sorted by priority score
"""
from __future__ import annotations

import logging
import math
import time
from dataclasses import dataclass
from typing import Optional

log = logging.getLogger("eidos.prioritizer")

# Priority weights
W_URGENCY = 0.30
W_IMPACT = 0.25
W_EFFORT = 0.20
W_CONTEXT = 0.15
W_HISTORY = 0.10

# Tag impact scores
TAG_IMPACT = {
    "security": 0.9, "critical": 1.0, "vulnerability": 0.95,
    "network": 0.7, "monitoring": 0.6, "learning": 0.5,
    "maintenance": 0.4, "optimization": 0.5, "research": 0.4,
    "feature": 0.3, "cleanup": 0.2, "cosmetic": 0.1,
}


@dataclass
class PriorityScore:
    """Breakdown of a goal's priority score."""
    goal_id: str
    goal_title: str
    total: float
    urgency: float
    impact: float
    effort: float
    context: float
    history: float
    reason: str


class SmartPrioritizer:
    """Scores and ranks goals by multiple factors."""

    def __init__(self):
        self._completed_tags: dict[str, int] = {}  # tag -> success count
        self._system_context: dict = {}

    def prioritize(self, goals: list) -> list[PriorityScore]:
        """Score and rank goals. Returns PriorityScore list sorted by total (desc)."""
        self._update_context()
        scores = []
        for g in goals:
            score = self._score_goal(g)
            scores.append(score)
        scores.sort(key=lambda s: s.total, reverse=True)
        return scores

    def _score_goal(self, goal) -> PriorityScore:
        """Calculate priority score for a single goal."""
        urgency = self._calc_urgency(goal)
        impact = self._calc_impact(goal)
        effort = self._calc_effort(goal)
        context = self._calc_context(goal)
        history = self._calc_history(goal)

        total = (W_URGENCY * urgency + W_IMPACT * impact +
                 W_EFFORT * effort + W_CONTEXT * context +
                 W_HISTORY * history)

        # Generate human-readable reason
        factors = []
        if urgency > 0.7:
            factors.append("urgent")
        if impact > 0.7:
            factors.append("high-impact")
        if effort > 0.7:
            factors.append("low-effort")
        if context > 0.7:
            factors.append("good-timing")
        reason = ", ".join(factors) if factors else "standard"

        return PriorityScore(
            goal_id=goal.id, goal_title=goal.title,
            total=round(total, 3),
            urgency=round(urgency, 2), impact=round(impact, 2),
            effort=round(effort, 2), context=round(context, 2),
            history=round(history, 2), reason=reason,
        )

    def _calc_urgency(self, goal) -> float:
        """Score based on deadline proximity and age."""
        score = 0.5  # base

        # Deadline urgency
        if hasattr(goal, 'deadline') and goal.deadline:
            remaining = goal.deadline - time.time()
            if remaining <= 0:
                score = 1.0  # overdue
            elif remaining < 3600:
                score = 0.95  # < 1h
            elif remaining < 86400:
                score = 0.8  # < 1 day
            elif remaining < 604800:
                score = 0.6  # < 1 week
            else:
                score = 0.3

        # Age factor — older unfinished goals get slight urgency boost
        if hasattr(goal, 'created_at') and goal.created_at:
            age_h = (time.time() - goal.created_at) / 3600
            if age_h > 24:
                score = min(1.0, score + 0.1)
            if age_h > 168:  # > 1 week
                score = min(1.0, score + 0.15)

        # Priority level boost
        if hasattr(goal, 'priority'):
            if goal.priority <= 1:
                score = min(1.0, score + 0.2)
            elif goal.priority <= 3:
                score = min(1.0, score + 0.1)

        return min(1.0, score)

    def _calc_impact(self, goal) -> float:
        """Score based on tags and keywords."""
        score = 0.5

        tags = getattr(goal, 'tags', []) or []
        title = getattr(goal, 'title', '').lower()
        desc = getattr(goal, 'description', '').lower()
        combined = f"{title} {desc} {' '.join(tags)}"

        # Tag-based scoring
        max_tag_score = 0.0
        for tag, impact in TAG_IMPACT.items():
            if tag in combined:
                max_tag_score = max(max_tag_score, impact)

        if max_tag_score > 0:
            score = max_tag_score

        return score

    def _calc_effort(self, goal) -> float:
        """Score inversely proportional to estimated effort (low effort = high score)."""
        title = getattr(goal, 'title', '').lower()
        desc = getattr(goal, 'description', '').lower()
        combined = f"{title} {desc}"

        # Keywords suggesting low effort
        low_effort = ["check", "verify", "status", "list", "monitor",
                       "scan", "quick", "simple", "update"]
        # Keywords suggesting high effort
        high_effort = ["implement", "build", "create", "design", "migrate",
                        "refactor", "rewrite", "complete"]

        score = 0.5
        for kw in low_effort:
            if kw in combined:
                score = min(1.0, score + 0.15)
                break
        for kw in high_effort:
            if kw in combined:
                score = max(0.1, score - 0.15)
                break

        return score

    def _calc_context(self, goal) -> float:
        """Score based on current system state."""
        score = 0.5
        title = getattr(goal, 'title', '').lower()

        # If system has alerts, security goals get boost
        if self._system_context.get('has_alerts', False):
            if any(kw in title for kw in ['security', 'audit', 'scan', 'monitor']):
                score = 0.9

        # If high CPU/RAM, optimization goals get boost
        if self._system_context.get('ram_high', False):
            if any(kw in title for kw in ['optimize', 'memory', 'ram', 'clean']):
                score = 0.85

        # If Ollama is offline, model-related goals deprioritized
        if not self._system_context.get('ollama_ok', True):
            if any(kw in title for kw in ['model', 'train', 'learn', 'llm']):
                score = 0.2

        return score

    def _calc_history(self, goal) -> float:
        """Score based on historical success with similar goals."""
        tags = getattr(goal, 'tags', []) or []
        if not tags and not self._completed_tags:
            return 0.5

        # Higher score if we've successfully completed similar goals before
        matches = sum(self._completed_tags.get(t, 0) for t in tags)
        if matches > 5:
            return 0.8
        elif matches > 0:
            return 0.6
        return 0.4

    def _update_context(self) -> None:
        """Gather current system context for scoring."""
        # Health
        try:
            from core.system_health import get_health_monitor
            hm = get_health_monitor()
            alerts = hm.get_alerts()
            self._system_context['has_alerts'] = len(alerts) > 0
        except Exception:
            pass  # error no crítico, continuar
        # RAM
        try:
            with open("/proc/meminfo") as f:
                lines = f.readlines()
            mem = {}
            for line in lines[:5]:
                key, val = line.split(":")
                mem[key.strip()] = int(val.strip().split()[0])
            ram_pct = (1 - mem.get("MemAvailable", 0) / max(mem.get("MemTotal", 1), 1)) * 100
            self._system_context['ram_high'] = ram_pct > 75
        except Exception:
            pass  # error no crítico, continuar
        # Ollama
        try:
            import urllib.request
            urllib.request.urlopen("http://127.0.0.1:11434/api/tags", timeout=2)
            self._system_context['ollama_ok'] = True
        except Exception:
            self._system_context['ollama_ok'] = False

    def record_completion(self, tags: list[str]) -> None:
        """Record a completed goal's tags for history scoring."""
        for tag in tags:
            self._completed_tags[tag] = self._completed_tags.get(tag, 0) + 1

    @property
    def stats(self) -> dict:
        return {
            "completed_tag_history": len(self._completed_tags),
            "context_keys": list(self._system_context.keys()),
        }


# ══════════════════════════════════════════════════════════════════════════════
#  SINGLETON
# ══════════════════════════════════════════════════════════════════════════════

_prioritizer: Optional[SmartPrioritizer] = None


def get_prioritizer() -> SmartPrioritizer:
    global _prioritizer
    if _prioritizer is None:
        _prioritizer = SmartPrioritizer()
    return _prioritizer


# ── CLI test ──────────────────────────────────────────────────────────────────
if __name__ == "__main__":
    from core.autonomous import get_autonomous, Goal, GoalStatus
    import time as _t

    p = get_prioritizer()

    # Create test goals
    test_goals = [
        Goal(id="g1", title="Scan network for vulnerabilities",
             description="Find open ports", priority=1,
             status=GoalStatus.ACTIVE, created_at=_t.time() - 3600,
             tags=["security", "network"]),
        Goal(id="g2", title="Learn about Kubernetes",
             description="Research k8s basics", priority=3,
             status=GoalStatus.ACTIVE, created_at=_t.time(),
             tags=["learning"]),
        Goal(id="g3", title="Clean up old logs",
             description="Remove logs older than 30 days", priority=5,
             status=GoalStatus.ACTIVE, created_at=_t.time() - 86400,
             tags=["maintenance", "cleanup"]),
        Goal(id="g4", title="Monitor system health",
             description="Check CPU and RAM", priority=2,
             status=GoalStatus.ACTIVE, created_at=_t.time(),
             tags=["monitoring"]),
    ]

    ranked = p.prioritize(test_goals)
    print("Smart Prioritizer — Goal Rankings:")
    for i, s in enumerate(ranked, 1):
        print(f"  {i}. [{s.total:.3f}] {s.goal_title}")
        print(f"     urgency={s.urgency} impact={s.impact} effort={s.effort} "
              f"context={s.context} history={s.history} ({s.reason})")
