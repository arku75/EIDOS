"""
EIDOS Brain-Agent — El cerebro que piensa y el cuerpo que ejecuta (S117).

Arquitectura HÍBRIDA SOBERANA (decisión de SER, 2026-06-01):
  - LLM LOCAL (lfm2.5-thinking:1.2b en Ollama) = el cerebro: entiende, descompone, decide.
  - Lo ya construido = el cuerpo: abrir navegador, ver pantalla (OCR/moondream),
    investigar web, grafo de memoria.
  - HÍBRIDO: lo simple/rápido se resuelve SIN LLM (instantáneo). Solo lo difícil
    o multi-paso despierta al LLM (más lento, pero soberano y capaz).

Bucle de agente (estilo ReAct, sin frameworks):
  tarea → LLM decide la siguiente herramienta → EIDOS la ejecuta →
  LLM ve el resultado → decide el siguiente paso → ... → respuesta final.

SEGURIDAD: toda acción que toque input respeta el freno/cesión
([[feedback-raton-grab]]). Las herramientas de aquí (navegador, OCR, web, grafo)
NO mueven el ratón — son seguras por diseño.

ZERO dependencia de APIs externas. Todo en la máquina de SER.
"""

from __future__ import annotations

import json
import logging
import re
import time
import urllib.request
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

log = logging.getLogger("eidos.brain_agent")

OLLAMA_URL = "http://localhost:11434/api/chat"
# Modelos LiquidAI LFM (S118 — reemplazan a qwen2.5 por ser 2× más rápidos en CPU)
BRAIN_MODEL_FAST = "LiquidAI/lfm2.5-1.2b-instruct:q4_0"   # 695MB, 4.2 tok/s en Ryzen 5
BRAIN_MODEL_MAIN = "lfm2.5-thinking:1.2b"                   # 731MB, CoT, razonamiento
BRAIN_VLM = "moondream:latest"                               # visión (pendiente LFM2.5-VL)
MAX_STEPS = 6                        # tope de pasos por tarea (anti-bucle)
LLM_TIMEOUT = 90                     # s por llamada al LLM (CPU lento, LFM es más rápido)


# ── Llamada al LLM local (Ollama) ─────────────────────────────────────────────
def ask_llm(messages: List[Dict[str, str]], model: str = BRAIN_MODEL_MAIN,
            temperature: float = 0.3) -> str:
    """Habla con el LLM local. messages = [{role, content}, ...]. Devuelve texto."""
    payload = {
        "model": model,
        "messages": messages,
        "stream": False,
        "options": {"temperature": temperature},
    }
    try:
        req = urllib.request.Request(
            OLLAMA_URL, data=json.dumps(payload).encode(),
            headers={"Content-Type": "application/json"},
        )
        with urllib.request.urlopen(req, timeout=LLM_TIMEOUT) as r:
            data = json.loads(r.read())
        return data.get("message", {}).get("content", "").strip()
    except Exception as e:
        log.warning("ask_llm error (%s): %s", model, e)
        return ""


# ── HERRAMIENTAS (el cuerpo de EIDOS — reusa lo ya construido) ────────────────
def _tool_abrir_y_ver(args: Dict[str, Any]) -> str:
    """Abre una URL/app en el navegador y lee lo que aparece (OCR). No toca ratón."""
    target = args.get("target") or args.get("url") or ""
    try:
        from core.eidos_action_executor import handle_action
        r = handle_action(f"entra en {target} y dime qué ves")
        return r or f"No pude abrir {target}"
    except Exception as e:
        return f"error abriendo {target}: {e}"


def _tool_investigar(args: Dict[str, Any]) -> str:
    """Investiga un tema en la web/man/docs (sin LLM). Devuelve lo aprendido."""
    tema = args.get("tema") or args.get("query") or ""
    try:
        from core.eidos_active_research import research_now
        r = research_now(tema, timeout=12)
        if r.get("definition"):
            return f"[{r.get('channel','web')}] {r['definition'][:600]}"
        return f"No encontré información clara sobre '{tema}'"
    except Exception as e:
        return f"error investigando: {e}"


def _tool_recordar(args: Dict[str, Any]) -> str:
    """Busca en la memoria (grafo) lo que EIDOS ya sabe."""
    concepto = args.get("concepto") or args.get("query") or ""
    try:
        from core.knowledge_reasoner import get_reasoner
        r = get_reasoner()
        if r.graph.size[0] > 100:
            res = r.reason(concepto, max_results=3, use_research=False)
            if not res.get("low_confidence"):
                return res.get("answer", "")[:600]
        return f"No tengo nada claro en memoria sobre '{concepto}'"
    except Exception as e:
        return f"error recordando: {e}"


def _tool_guardar(args: Dict[str, Any]) -> str:
    """Guarda algo nuevo en la memoria (grafo) para no olvidarlo."""
    concepto = args.get("concepto", "")
    info = args.get("info", "")
    if not concepto or not info:
        return "falta concepto o info"
    try:
        from core.db import get_conn
        with get_conn(str(Path.home() / ".eidos" / "evolution_brain.db")) as conn:
            conn.execute(
                "INSERT OR IGNORE INTO knowledge_nodes "
                "(concept, definition, category, source, confidence) VALUES (?,?,?,?,?)",
                (concepto[:120], info[:1000], "agent_learned", "brain_agent", 0.75),
            )
            conn.commit()
        return f"guardado: {concepto}"
    except Exception as e:
        return f"error guardando: {e}"


TOOLS: Dict[str, Callable[[Dict[str, Any]], str]] = {
    "abrir_y_ver": _tool_abrir_y_ver,
    "investigar": _tool_investigar,
    "recordar": _tool_recordar,
    "guardar": _tool_guardar,
}

TOOLS_DESC = """Herramientas disponibles (responde con UNA por paso):
- abrir_y_ver: abre una web/app y lee lo que aparece. args: {"target": "youtube"}
- investigar: busca info de un tema en la web/manuales. args: {"tema": "n8n"}
- recordar: consulta lo que YA sabes en tu memoria. args: {"concepto": "ssh"}
- guardar: guarda algo aprendido en tu memoria. args: {"concepto": "...", "info": "..."}
- responder: termina y da la respuesta final a SER. args: {"texto": "..."}"""

_SYSTEM_AGENT = (
    "Eres EIDOS, un agente que vive en el Kali de SER. Piensas y luego actúas con "
    "tus herramientas. Para CADA paso responde SOLO un objeto JSON válido, sin texto "
    "extra, con esta forma:\n"
    '{"pensamiento": "qué razono", "accion": "nombre_herramienta", "args": {...}}\n'
    "Cuando ya tengas la respuesta para SER, usa la acción \"responder\".\n\n"
    + TOOLS_DESC
)


def _parse_action(text: str) -> Optional[Dict[str, Any]]:
    """Extrae el JSON de acción de la respuesta del LLM (tolerante a ruido)."""
    if not text:
        return None
    m = re.search(r"\{.*\}", text, re.DOTALL)
    if not m:
        return None
    try:
        return json.loads(m.group(0))
    except Exception:
        # Intento de reparación simple
        try:
            return json.loads(m.group(0).replace("'", '"'))
        except Exception:
            return None


# ── El bucle de agente: LLM piensa → EIDOS ejecuta → repite ───────────────────
def agent_loop(task: str, model: str = BRAIN_MODEL_MAIN,
               max_steps: int = MAX_STEPS, verbose: bool = True) -> Dict[str, Any]:
    """Resuelve una tarea multi-paso: el LLM decide, EIDOS ejecuta, hasta terminar."""
    messages = [
        {"role": "system", "content": _SYSTEM_AGENT},
        {"role": "user", "content": f"Tarea de SER: {task}"},
    ]
    trace: List[Dict[str, Any]] = []
    t0 = time.time()

    for step in range(1, max_steps + 1):
        raw = ask_llm(messages, model=model)
        action = _parse_action(raw)
        if not action:
            trace.append({"step": step, "error": "LLM no dio JSON válido", "raw": raw[:200]})
            break

        accion = action.get("accion", "")
        args = action.get("args", {}) or {}
        pensamiento = action.get("pensamiento", "")
        if verbose:
            log.info("paso %d: pensó '%s' → %s(%s)", step, pensamiento[:60], accion, args)

        if accion == "responder":
            respuesta = args.get("texto") or action.get("respuesta") or pensamiento
            trace.append({"step": step, "pensamiento": pensamiento, "accion": "responder"})
            return {"ok": True, "respuesta": respuesta, "pasos": step,
                    "trace": trace, "elapsed_s": round(time.time() - t0, 1)}

        tool = TOOLS.get(accion)
        if not tool:
            resultado = f"(no existe la herramienta '{accion}')"
        else:
            resultado = tool(args)

        trace.append({"step": step, "pensamiento": pensamiento, "accion": accion,
                      "args": args, "resultado": resultado[:300]})
        # Realimentar al LLM con lo que pasó
        messages.append({"role": "assistant", "content": raw})
        messages.append({"role": "user",
                         "content": f"Resultado de {accion}: {resultado[:800]}\n"
                                    f"Siguiente paso (JSON), o 'responder' si ya terminaste."})

    return {"ok": False, "respuesta": "Alcancé el límite de pasos sin terminar.",
            "pasos": max_steps, "trace": trace, "elapsed_s": round(time.time() - t0, 1)}


# ── Punto de entrada híbrido: rápido sin LLM, o pensar con LLM ────────────────
def think(message: str) -> Dict[str, Any]:
    """
    Entrada principal del cerebro-agente híbrido.
    1. ¿Acción directa simple? → la ejecuta YA, sin LLM.
    2. ¿Algo que ya sabe en memoria? → responde rápido.
    3. Si no → despierta al LLM para pensar/orquestar.
    """
    t0 = time.time()

    # 1. Acción directa ("abre youtube y dime qué ves") → sin LLM, instantáneo
    try:
        from core.eidos_action_executor import is_action, handle_action
        if is_action(message):
            r = handle_action(message)
            if r:
                return {"respuesta": r, "via": "accion_directa",
                        "elapsed_s": round(time.time() - t0, 1)}
    except Exception as e:
        log.debug("acción directa falló: %s", e)

    # 2. ¿Es una tarea compleja/multi-paso? (heurística simple por longitud/verbos)
    low = message.lower()
    es_compleja = (len(message) > 60 or
                   any(w in low for w in ("y luego", "aprende", "cada", "todos", "investiga",
                                          "y dime", "y guarda", "analiza", "entiende")))

    if es_compleja:
        # 3. Despertar al LLM para orquestar la tarea multi-paso
        res = agent_loop(message)
        res["via"] = "agente_llm"
        return res

    # 4. Tarea simple no-acción: pregunta corta → LLM directo (1 paso, rápido)
    answer = ask_llm(
        [{"role": "system", "content": "Eres EIDOS, asistente de SER en Kali Linux. "
          "Responde en español, breve y útil."},
         {"role": "user", "content": message}],
        model=BRAIN_MODEL_FAST,
    )
    return {"respuesta": answer or "No supe responder.", "via": "llm_directo",
            "elapsed_s": round(time.time() - t0, 1)}


_brain = None
def get_brain():
    return think  # función de entrada
