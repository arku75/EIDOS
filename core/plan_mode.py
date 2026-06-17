"""
core/plan_mode.py — Plan Mode de EIDOS (inspirado en Claude Code).

Antes de ejecutar cualquier tarea no-trivial, EIDOS genera un plan numerado
con lfm2.5-thinking:1.2b, lo muestra y espera aprobación.

Uso desde CLI:
    eidos plan "crea un script que monitorice el CPU"
    eidos plan --auto "añade ChromaDB sync al daemon"   # sin confirmación

Uso programático:
    from core.plan_mode import PlanMode
    plan = PlanMode()
    approved = plan.propose("tarea", auto_approve=False)
    if approved:
        plan.execute()
"""
from __future__ import annotations

import json
import logging
import os
import time
import urllib.request
from dataclasses import dataclass, field
from typing import Optional

log = logging.getLogger("eidos.plan_mode")

OLLAMA_URL  = os.environ.get("OLLAMA_URL", "http://localhost:11435")
PLAN_MODEL  = os.environ.get("EIDOS_PLAN_MODEL", "deepseek-r1:14b")


@dataclass
class PlanStep:
    n:           int
    description: str
    tool:        str = ""
    args:        dict = field(default_factory=dict)
    status:      str = "pending"   # pending / in_progress / done / skipped

    def __str__(self) -> str:
        icon = {"pending": "⬜", "in_progress": "🔄", "done": "✅", "skipped": "⏭"}.get(self.status, "⬜")
        return f"  {icon} {self.n}. {self.description}"


@dataclass
class Plan:
    task:   str
    steps:  list[PlanStep]
    model:  str
    raw:    str = ""

    def display(self) -> str:
        lines = [f"\n📋 PLAN — {self.task}\n"]
        for s in self.steps:
            lines.append(str(s))
        lines.append(f"\n  Modelo: {self.model} | {len(self.steps)} pasos")
        return "\n".join(lines)


_PLAN_SYSTEM = """\
Eres EIDOS generando un plan de trabajo claro para una tarea.

Responde SOLO con JSON en este formato:
{
  "summary": "resumen de 1 línea de lo que vas a hacer",
  "steps": [
    {"n": 1, "description": "qué harás exactamente", "tool": "herramienta_opcional"},
    {"n": 2, "description": "..."},
    ...
  ],
  "estimated_minutes": 5,
  "risks": ["riesgo 1 si aplica"]
}

Herramientas disponibles: bash, read_file, write_file, grep, glob,
web_search, web_fetch, search_brain, ask_colony, list_files, finish.

Sé específico. Máximo 10 pasos. Idioma español.
"""


class PlanMode:
    """Genera y gestiona planes de trabajo paso a paso."""

    def __init__(self):
        self._current_plan: Optional[Plan] = None

    def propose(self, task: str, context: str = "",
                auto_approve: bool = False, model: str = PLAN_MODEL) -> Optional[Plan]:
        """
        Genera un plan para la tarea y lo muestra.
        Retorna el Plan si aprobado, None si rechazado.
        """
        log.info("Generando plan para: %s", task[:60])
        raw = self._generate_plan(task, context, model)
        plan_data = self._parse_plan(raw, task, model)
        self._current_plan = plan_data

        print(plan_data.display())

        if auto_approve:
            print("\n  ✅ Auto-aprobado")
            return plan_data

        print("\n¿Proceder con este plan? [s/N]: ", end="", flush=True)
        try:
            answer = input().strip().lower()
        except (EOFError, KeyboardInterrupt):
            answer = "n"

        if answer in ("s", "si", "sí", "y", "yes"):
            return plan_data
        print("  ⏭ Plan cancelado")
        return None

    def mark_step(self, step_n: int, status: str = "done") -> None:
        if self._current_plan:
            for s in self._current_plan.steps:
                if s.n == step_n:
                    s.status = status

    def current_display(self) -> str:
        if self._current_plan:
            return self._current_plan.display()
        return "[sin plan activo]"

    # ── Privados ─────────────────────────────────────────────────────────────

    def _generate_plan(self, task: str, context: str, model: str) -> str:
        messages = [{"role": "system", "content": _PLAN_SYSTEM}]
        if context:
            messages.append({"role": "user", "content": f"Contexto: {context}"})
        messages.append({"role": "user", "content": f"Genera un plan para: {task}"})

        payload = json.dumps({
            "model": model,
            "messages": messages,
            "stream": False,
            "options": {"temperature": 0.3, "num_predict": 800, "num_ctx": 4096},
        }).encode()
        try:
            req = urllib.request.Request(
                f"{OLLAMA_URL}/api/chat",
                data=payload,
                headers={"Content-Type": "application/json"},
            )
            with urllib.request.urlopen(req, timeout=120) as r:
                return json.loads(r.read()).get("message", {}).get("content", "")
        except Exception as e:
            log.warning("plan generation error: %s", e)
            return json.dumps({
                "summary": task,
                "steps": [{"n": 1, "description": task}],
                "estimated_minutes": 1,
                "risks": [f"LLM no disponible: {e}"]
            })

    @staticmethod
    def _parse_plan(raw: str, task: str, model: str) -> Plan:
        import re
        text = raw.strip()
        m = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", text, re.DOTALL)
        if m:
            text = m.group(1)
        start, end = text.find("{"), text.rfind("}")
        if start != -1 and end != -1:
            try:
                data = json.loads(text[start:end + 1])
                steps = [PlanStep(s["n"], s.get("description", ""), s.get("tool", ""))
                         for s in data.get("steps", [])]
                return Plan(task=task, steps=steps, model=model, raw=raw)
            except Exception:
                pass
        # Fallback: un solo paso
        return Plan(task=task, steps=[PlanStep(1, task)], model=model, raw=raw)
