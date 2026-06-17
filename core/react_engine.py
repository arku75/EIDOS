"""
core/react_engine.py — Motor ReAct de EIDOS.

Implementa el loop Razonar → Actuar → Observar idéntico al de Claude Code,
pero totalmente local: usa lfm2.5-thinking:1.2b / lfm2.5-thinking:1.2b del Mac vía Ollama.

Flujo:
  1. EIDOS recibe una tarea (texto libre o tool_call JSON).
  2. Llama al LLM pidiendo un plan en JSON: {"thought": ..., "tool": ..., "args": ...}
  3. Ejecuta la herramienta registrada en ToolRegistry.
  4. Añade la observación al historial y repite hasta "finish".
  5. Devuelve resultado final + historial de pasos.

Uso:
    from core.react_engine import ReActEngine
    engine = ReActEngine()
    result = engine.run("Lista los 5 CVEs más recientes del brain")
    print(result.answer)
    for step in result.steps:
        print(step)
"""
from __future__ import annotations

import json
import logging
import os
import time
from dataclasses import dataclass, field
from typing import Any, Optional

log = logging.getLogger("eidos.react")

OLLAMA_URL   = os.environ.get("OLLAMA_URL", "http://localhost:11435")
REACT_MODEL  = os.environ.get("EIDOS_REACT_MODEL", "deepseek-r1:14b")
MAX_STEPS    = int(os.environ.get("EIDOS_REACT_MAX_STEPS", "12"))

_SYSTEM_PROMPT = """\
Eres EIDOS, un agente de IA que razona y actúa usando herramientas.

Para cada turno devuelves EXACTAMENTE un JSON con este esquema:
{
  "thought": "tu razonamiento interno sobre qué hacer a continuación",
  "tool": "nombre_herramienta",
  "args": { ... argumentos ... }
}

Cuando hayas terminado y tengas la respuesta final usa:
{ "thought": "...", "tool": "finish", "args": {"answer": "respuesta completa"} }

Herramientas disponibles:
{tools_block}

Reglas:
- Piensa paso a paso antes de elegir la herramienta.
- Sé preciso con los argumentos.
- No inventes datos; si no sabes, usa search_brain o web_search.
- Responde en el idioma de la pregunta original.
"""


@dataclass
class ReActStep:
    step:        int
    thought:     str
    tool:        str
    args:        dict
    observation: str
    elapsed_s:   float = 0.0

    def __str__(self) -> str:
        return (f"[{self.step}] 💭 {self.thought[:80]}\n"
                f"    🔧 {self.tool}({json.dumps(self.args, ensure_ascii=False)[:80]})\n"
                f"    👁  {self.observation[:120]}")


@dataclass
class ReActResult:
    answer:    str
    steps:     list[ReActStep] = field(default_factory=list)
    success:   bool = True
    error:     str = ""
    total_s:   float = 0.0


class ReActEngine:
    """Motor de razonamiento+acción de EIDOS."""

    def __init__(self, tool_registry=None):
        if tool_registry is None:
            from core.tool_registry import ToolRegistry
            tool_registry = ToolRegistry()
        self._tools = tool_registry

    # ── API pública ──────────────────────────────────────────────────────────

    def run(self, task: str, context: str = "", model: str = REACT_MODEL,
            max_steps: int = MAX_STEPS) -> ReActResult:
        """Ejecuta el loop ReAct hasta obtener una respuesta o agotar pasos."""
        t0 = time.time()
        steps: list[ReActStep] = []

        tools_block = self._tools.describe()
        system = _SYSTEM_PROMPT.format(tools_block=tools_block)
        messages = [{"role": "system", "content": system}]
        if context:
            messages.append({"role": "user", "content": f"Contexto adicional:\n{context}"})
        messages.append({"role": "user", "content": f"Tarea: {task}"})

        for step_n in range(1, max_steps + 1):
            t_step = time.time()
            raw = self._call_llm(messages, model)
            parsed = self._parse_json(raw)

            if parsed is None:
                # Respuesta no-JSON — tratarla como finish directo
                result = ReActResult(answer=raw, steps=steps, success=True,
                                     total_s=round(time.time() - t0, 1))
                return result

            thought = parsed.get("thought", "")
            tool    = parsed.get("tool", "finish")
            args    = parsed.get("args", {})

            # Ejecutar herramienta
            if tool == "finish":
                answer = args.get("answer", thought)
                step = ReActStep(step_n, thought, tool, args,
                                 f"[fin] {answer[:80]}",
                                 round(time.time() - t_step, 1))
                steps.append(step)
                log.info("ReAct terminó en %d pasos (%.1fs)", step_n,
                         time.time() - t0)
                return ReActResult(answer=answer, steps=steps, success=True,
                                   total_s=round(time.time() - t0, 1))

            observation = self._tools.call(tool, args)
            elapsed_step = round(time.time() - t_step, 1)
            step = ReActStep(step_n, thought, tool, args, observation, elapsed_step)
            steps.append(step)
            log.info("ReAct paso %d: %s → %s (%.1fs)",
                     step_n, tool, observation[:60], elapsed_step)

            # Añadir al historial para el siguiente turno
            messages.append({"role": "assistant", "content": raw})
            messages.append({"role": "user",      "content": f"Observación: {observation}"})

        # Agotados los pasos
        answer = f"[Max {max_steps} pasos alcanzados] Último resultado: {steps[-1].observation if steps else ''}"
        return ReActResult(answer=answer, steps=steps, success=False,
                           error=f"max_steps={max_steps} alcanzados",
                           total_s=round(time.time() - t0, 1))

    # ── Privados ─────────────────────────────────────────────────────────────

    def _call_llm(self, messages: list[dict], model: str) -> str:
        """Llama a Ollama y devuelve el texto de respuesta."""
        import urllib.request
        payload = json.dumps({
            "model": model,
            "messages": messages,
            "stream": False,
            "options": {"temperature": 0.2, "num_predict": 512, "num_ctx": 4096},
        }).encode()
        req = urllib.request.Request(
            f"{OLLAMA_URL}/api/chat",
            data=payload,
            headers={"Content-Type": "application/json"},
        )
        try:
            with urllib.request.urlopen(req, timeout=180) as r:
                data = json.loads(r.read())
            return data.get("message", {}).get("content", "")
        except Exception as e:
            log.warning("ReAct LLM error: %s", e)
            return json.dumps({"thought": f"error LLM: {e}", "tool": "finish",
                                "args": {"answer": f"Error llamando al LLM: {e}"}})

    @staticmethod
    def _parse_json(text: str) -> Optional[dict]:
        """Extrae JSON del texto; devuelve None si no hay JSON válido."""
        text = text.strip()
        # Extraer bloque ```json ... ```
        if "```" in text:
            import re
            m = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", text, re.DOTALL)
            if m:
                text = m.group(1)
        # Intentar extraer primer { ... }
        start = text.find("{")
        end   = text.rfind("}")
        if start != -1 and end != -1:
            try:
                return json.loads(text[start:end + 1])
            except json.JSONDecodeError:
                pass
        return None
