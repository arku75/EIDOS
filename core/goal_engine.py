"""
EIDOS core/goal_engine.py — Goal-Driven Task Engine
=====================================================
Convierte objetivos de alto nivel en sub-tareas ejecutables.

Integra con autonomous.py (goals) para dar estructura a la autonomia.
En lugar de que EIDOS "piense" vagamente, descompone goals en steps
concretos que puede verificar.

Uso:
    from core.goal_engine import get_goal_engine
    ge = get_goal_engine()
    plan = ge.decompose("Escanear la red 192.168.1.0/24 y encontrar vulnerabilidades")
    for step in plan.steps:
        print(f"  [{step.status}] {step.action}")
"""
from __future__ import annotations

import json
import logging
import os
import sqlite3
import time
import urllib.request
from dataclasses import dataclass, field
from typing import Optional
from enum import Enum
from core.db import get_conn
from core.db import get_conn_ctx

log = logging.getLogger("eidos.goals")

DB_PATH = os.path.expanduser("~/.eidos/goals.db")
OLLAMA_URL = os.environ.get("OLLAMA_URL", "http://127.0.0.1:11434")
FAST_MODEL = os.environ.get("EIDOS_FAST_MODEL", "lfm2.5-thinking:1.2b")


class StepStatus(str, Enum):
    PENDING = "pending"
    RUNNING = "running"
    DONE = "done"
    FAILED = "failed"
    SKIPPED = "skipped"


@dataclass
class GoalStep:
    """A single executable step toward a goal."""
    id: int
    action: str          # What to do (human-readable)
    tool: str = ""       # Suggested tool (shell, browser, memory, etc.)
    command: str = ""    # Concrete command if applicable
    status: StepStatus = StepStatus.PENDING
    result: str = ""
    depends_on: list[int] = field(default_factory=list)
    estimated_s: float = 30.0


@dataclass
class GoalPlan:
    """A decomposed goal with ordered steps."""
    goal_id: str
    title: str
    steps: list[GoalStep]
    created_at: float = field(default_factory=time.time)
    completed: bool = False
    progress_pct: float = 0.0

    def next_step(self) -> Optional[GoalStep]:
        """Get next actionable step."""
        for step in self.steps:
            if step.status == StepStatus.PENDING:
                # Check dependencies
                deps_met = all(
                    self.steps[d].status == StepStatus.DONE
                    for d in step.depends_on
                    if d < len(self.steps)
                )
                if deps_met:
                    return step
        return None

    def update_progress(self) -> float:
        done = sum(1 for s in self.steps if s.status in (StepStatus.DONE, StepStatus.SKIPPED))
        self.progress_pct = (done / len(self.steps) * 100) if self.steps else 0
        self.completed = all(
            s.status in (StepStatus.DONE, StepStatus.SKIPPED, StepStatus.FAILED)
            for s in self.steps
        )
        return self.progress_pct


# ══════════════════════════════════════════════════════════════════════════════
#  TEMPLATES — common decomposition patterns (no LLM needed)
# ══════════════════════════════════════════════════════════════════════════════

TEMPLATES = {
    "scan_network": {
        "keywords": ["scan", "escanear", "red", "network", "nmap", "puertos"],
        "steps": [
            {"action": "Verificar conectividad con el gateway", "tool": "shell", "command": "ping -c 2 192.168.1.1"},
            {"action": "Descubrimiento de hosts activos", "tool": "shell", "command": "nmap -sn {target}"},
            {"action": "Escaneo de puertos TCP top 1000", "tool": "shell", "command": "nmap -sV -sC {target}"},
            {"action": "Identificar servicios y versiones", "tool": "shell", "command": "nmap -sV --version-intensity 5 {target}"},
            {"action": "Analizar resultados y generar reporte", "tool": "memory", "command": ""},
        ],
    },
    "recon_target": {
        "keywords": ["recon", "reconocimiento", "reconnaissance", "osint", "objetivo"],
        "steps": [
            {"action": "WHOIS lookup del dominio", "tool": "shell", "command": "whois {target}"},
            {"action": "DNS enumeration", "tool": "shell", "command": "dig {target} ANY +noall +answer"},
            {"action": "Subdomain discovery", "tool": "shell", "command": "subfinder -d {target} -silent 2>/dev/null || echo 'subfinder not installed'"},
            {"action": "Web technology detection", "tool": "shell", "command": "whatweb {target} 2>/dev/null || curl -sI {target}"},
            {"action": "Consolidar hallazgos en memoria", "tool": "memory", "command": ""},
        ],
    },
    "learn_topic": {
        "keywords": ["aprende", "learn", "estudia", "study", "investiga", "research"],
        "steps": [
            {"action": "Buscar informacion relevante online", "tool": "browser", "command": ""},
            {"action": "Leer y extraer conceptos clave", "tool": "memory", "command": ""},
            {"action": "Almacenar en knowledge base", "tool": "memory", "command": ""},
            {"action": "Generar resumen para SER", "tool": "shell", "command": ""},
        ],
    },
    "fix_code": {
        "keywords": ["fix", "arregla", "bug", "error", "corrige", "repair"],
        "steps": [
            {"action": "Leer el archivo con el error", "tool": "shell", "command": ""},
            {"action": "Identificar la causa raiz", "tool": "shell", "command": ""},
            {"action": "Aplicar el fix", "tool": "shell", "command": ""},
            {"action": "Verificar que funciona", "tool": "shell", "command": ""},
        ],
    },
    "monitor_system": {
        "keywords": ["monitorea", "monitor", "vigila", "watch", "estado", "status"],
        "steps": [
            {"action": "Check system health", "tool": "shell", "command": "python3 -c 'from core.system_health import get_health_monitor; print(get_health_monitor().quick_check())'"},
            {"action": "Check Ollama status", "tool": "shell", "command": "curl -s http://localhost:11434/api/tags | python3 -c 'import sys,json; d=json.load(sys.stdin); print(len(d.get(\"models\",[]))); print(\"models OK\")'"},
            {"action": "Check disk and RAM", "tool": "shell", "command": "free -h && df -h /home"},
            {"action": "Reportar a SER si hay problemas", "tool": "notify", "command": ""},
        ],
    },
    "audit_security": {
        "keywords": ["audit", "auditar", "seguridad", "security", "hardening", "vulnerab"],
        "steps": [
            {"action": "Scan de la red local", "tool": "shell", "command": "nmap -sn --open 10.183.89.0/24"},
            {"action": "Verificar puertos abiertos localmente", "tool": "shell", "command": "ss -tlnp"},
            {"action": "Check logs de seguridad", "tool": "shell", "command": "tail -20 /var/log/auth.log"},
            {"action": "Verificar usuarios activos", "tool": "shell", "command": "who && last -5"},
            {"action": "Check servicios en ejecucion", "tool": "shell", "command": "systemctl list-units --type=service --state=running --no-pager"},
            {"action": "Reporte de hallazgos", "tool": "memory", "command": ""},
        ],
    },
    "discover_network": {
        "keywords": ["descubr", "discover", "mapea", "map", "host", "dispositiv", "device"],
        "steps": [
            {"action": "Detectar redes locales", "tool": "shell", "command": "ip -4 route show scope link"},
            {"action": "ARP discovery", "tool": "shell", "command": "nmap -sn --open {target}"},
            {"action": "Identificar servicios principales", "tool": "shell", "command": "nmap -sV --top-ports 20 {target}"},
            {"action": "Guardar mapa de red", "tool": "memory", "command": ""},
        ],
    },
}


class GoalEngine:
    """Decomposes high-level goals into executable step plans."""

    def __init__(self, db_path: str = DB_PATH):
        self.db_path = db_path
        self._plans: dict[str, GoalPlan] = {}

        os.makedirs(os.path.dirname(db_path), exist_ok=True)
        self._init_db()

    def _init_db(self) -> None:
        with get_conn_ctx(self.db_path) as c:
            c.execute("""
                CREATE TABLE IF NOT EXISTS goal_plans (
                    id TEXT PRIMARY KEY,
                    title TEXT,
                    steps_json TEXT,
                    created_at REAL,
                    completed INTEGER DEFAULT 0,
                    progress REAL DEFAULT 0
                )
            """)

    # ── Decompose ────────────────────────────────────────────────────────────

    def decompose(self, goal: str, context: dict = None) -> GoalPlan:
        """Decompose a goal into executable steps.

        First tries template matching (instant, no LLM).
        Falls back to LLM decomposition if no template matches.

        Args:
            goal: High-level goal description
            context: Optional context (target, files, etc.)

        Returns:
            GoalPlan with ordered steps
        """
        context = context or {}
        goal_id = f"goal_{int(time.time())}_{hash(goal) % 10000:04d}"

        # 1. Try template matching (fast, no LLM)
        plan = self._match_template(goal_id, goal, context)
        if plan:
            self._save_plan(plan)
            return plan

        # 2. LLM decomposition (slower but more flexible)
        plan = self._llm_decompose(goal_id, goal, context)
        if plan:
            self._save_plan(plan)
            return plan

        # 3. Fallback: single generic step
        plan = GoalPlan(
            goal_id=goal_id,
            title=goal[:100],
            steps=[GoalStep(id=0, action=goal, tool="shell")],
        )
        self._save_plan(plan)
        return plan

    def decompose_template_only(self, goal: str, context: dict = None) -> Optional[GoalPlan]:
        """Decompose using templates only (no LLM). Returns None if no template matches."""
        context = context or {}
        goal_id = f"goal_{int(time.time())}_{hash(goal) % 10000:04d}"
        plan = self._match_template(goal_id, goal, context)
        if plan:
            self._save_plan(plan)
        return plan

    def _match_template(self, goal_id: str, goal: str,
                        context: dict) -> Optional[GoalPlan]:
        """Match goal against known templates."""
        goal_lower = goal.lower()
        target = context.get("target", "")

        # Extract target from goal if not in context
        if not target:
            # Simple extraction: look for IPs, domains, paths
            import re
            ip_match = re.search(r'\d+\.\d+\.\d+\.\d+(?:/\d+)?', goal)
            if ip_match:
                target = ip_match.group()
            domain_match = re.search(r'[a-zA-Z0-9-]+\.[a-zA-Z]{2,}', goal)
            if domain_match and not target:
                target = domain_match.group()

        best_match = None
        best_score = 0

        for tpl_name, tpl in TEMPLATES.items():
            score = sum(1 for kw in tpl["keywords"] if kw in goal_lower)
            if score > best_score:
                best_score = score
                best_match = tpl_name

        if best_match and best_score >= 2:
            tpl = TEMPLATES[best_match]
            steps = []
            for i, s in enumerate(tpl["steps"]):
                cmd = s["command"].replace("{target}", target) if target else s["command"]
                steps.append(GoalStep(
                    id=i,
                    action=s["action"],
                    tool=s["tool"],
                    command=cmd,
                    depends_on=[i - 1] if i > 0 else [],
                ))
            return GoalPlan(goal_id=goal_id, title=goal[:100], steps=steps)

        return None

    def _llm_decompose(self, goal_id: str, goal: str,
                       context: dict) -> Optional[GoalPlan]:
        """Use LLM to decompose an arbitrary goal."""
        prompt = f"""Descompone este objetivo en 3-6 pasos concretos ejecutables.
Objetivo: {goal}

Responde SOLO JSON valido:
{{"steps": [
  {{"action": "descripcion del paso", "tool": "shell|browser|memory|notify", "command": "comando concreto si aplica"}},
  ...
]}}
Solo pasos necesarios, concretos y verificables. Sin explicaciones."""

        try:
            data = json.dumps({
                "model": FAST_MODEL,
                "messages": [{"role": "user", "content": prompt}],
                "stream": False,
                "options": {"temperature": 0.3, "num_predict": 500},
            }).encode()

            req = urllib.request.Request(
                f"{OLLAMA_URL}/api/chat",
                data=data,
                headers={"Content-Type": "application/json"},
            )
            with urllib.request.urlopen(req, timeout=90) as resp:
                result = json.loads(resp.read())
                raw = result.get("message", {}).get("content", "")

            # Parse JSON
            start = raw.find("{")
            end = raw.rfind("}") + 1
            if start == -1:
                return None

            parsed = json.loads(raw[start:end])
            raw_steps = parsed.get("steps", [])

            steps = []
            for i, s in enumerate(raw_steps[:8]):  # max 8 steps
                steps.append(GoalStep(
                    id=i,
                    action=str(s.get("action", "")),
                    tool=str(s.get("tool", "shell")),
                    command=str(s.get("command", "")),
                    depends_on=[i - 1] if i > 0 else [],
                ))

            if steps:
                return GoalPlan(goal_id=goal_id, title=goal[:100], steps=steps)

        except Exception as e:
            log.warning("LLM decompose failed: %s", e)

        return None

    # ── Step Execution ───────────────────────────────────────────────────────

    def mark_step(self, plan_id: str, step_id: int,
                  status: StepStatus, result: str = "") -> None:
        """Mark a step as done/failed."""
        plan = self._plans.get(plan_id)
        if plan and step_id < len(plan.steps):
            plan.steps[step_id].status = status
            plan.steps[step_id].result = result[:500]
            plan.update_progress()
            self._save_plan(plan)

    # ── Persistence ──────────────────────────────────────────────────────────

    def _save_plan(self, plan: GoalPlan) -> None:
        self._plans[plan.goal_id] = plan
        try:
            steps_json = json.dumps([{
                "id": s.id, "action": s.action, "tool": s.tool,
                "command": s.command, "status": s.status.value,
                "result": s.result[:200], "depends_on": s.depends_on,
            } for s in plan.steps])

            with get_conn_ctx(self.db_path) as c:
                c.execute("""
                    INSERT OR REPLACE INTO goal_plans
                    (id, title, steps_json, created_at, completed, progress)
                    VALUES (?,?,?,?,?,?)
                """, (plan.goal_id, plan.title, steps_json,
                      plan.created_at, int(plan.completed), plan.progress_pct))
        except Exception as e:
            log.warning("Plan save failed: %s", e)

    def get_active_plans(self) -> list[GoalPlan]:
        """Get all non-completed plans."""
        return [p for p in self._plans.values() if not p.completed]

    @property
    def stats(self) -> dict:
        total = len(self._plans)
        active = sum(1 for p in self._plans.values() if not p.completed)
        return {
            "total_plans": total,
            "active_plans": active,
            "completed_plans": total - active,
        }


# ══════════════════════════════════════════════════════════════════════════════
#  SINGLETON
# ══════════════════════════════════════════════════════════════════════════════

_engine: Optional[GoalEngine] = None


def get_goal_engine() -> GoalEngine:
    global _engine
    if _engine is None:
        _engine = GoalEngine()
    return _engine
