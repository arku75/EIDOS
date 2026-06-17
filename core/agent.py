"""
EIDOS core/agent.py — El Cerebro del Agente Soberano

Patrones integrados de:
  ▸ Antigravity    : PLANNING → EXECUTION → VERIFICATION, task_boundary, notify pattern
  ▸ Claude         : Think step (razona antes de actuar), Constitutional self-critique
  ▸ OpenAI Agents  : Parallel tool execution, JSON schema tools, handoffs
  ▸ OpenClaw       : JSONL session logging, multi-agent safety, no streaming to external
  ▸ AutoGPT        : Autonomous goal loop con memoria persistente
  ▸ CrewAI         : Subagentes especializados por dominio

Uso:
    from core.agent import EIDOSAgent
    agent = EIDOSAgent(soul=SOUL_TEXT)
    response = agent.run("escanea la red y dime qué hosts hay activos")
"""
from __future__ import annotations

import json
import os
import time
import datetime
import uuid
import subprocess
import threading
import urllib.request
import urllib.error
from enum import Enum
from typing import Any, Callable

# ── Config ──────────────────────────────────────────────────────────────────
OLLAMA_URL  = "http://localhost:11434"
FAST_MODEL  = "lfm2.5-thinking:1.2b"
DEEP_MODEL  = "deepseek-r1:14b"
EIDOS_DIR   = os.path.expanduser("~/EIDOS")
SESSION_DIR = os.path.expanduser("~/.eidos/sessions")
os.makedirs(SESSION_DIR, exist_ok=True)


# ══════════════════════════════════════════════════════════════════════════════
#  FASE — De Antigravity. PLANNING → EXECUTION → VERIFICATION
# ══════════════════════════════════════════════════════════════════════════════

class Phase(Enum):
    PLANNING    = "planning"
    EXECUTION   = "execution"
    VERIFICATION = "verification"
    DONE        = "done"


# ══════════════════════════════════════════════════════════════════════════════
#  SESSION LOG — De OpenClaw AGENTS.md. Logs a JSONL igual que los agentes pro.
# ══════════════════════════════════════════════════════════════════════════════

class SessionLog:
    """Registra cada acción en JSONL (como hacen OpenClaw y Antigravity internamente)."""

    def __init__(self, agent_id: str) -> None:
        self.agent_id  = agent_id
        self.session_id= str(uuid.uuid4())[:8]  # pyre-ignore[arg-type]
        self.path      = os.path.join(SESSION_DIR, f"{agent_id}_{self.session_id}.jsonl")
        self._lock     = threading.Lock()

    def log(self, event_type: str, data: dict) -> None:
        entry = {
            "ts":      datetime.datetime.now().isoformat(),
            "session": self.session_id,
            "type":    event_type,
            **data,
        }
        with self._lock:
            with open(self.path, "a") as f:
                f.write(json.dumps(entry, ensure_ascii=False) + "\n")

    def user_msg(self, text: str)       -> None: self.log("user",    {"content": text})
    def agent_msg(self, text: str)      -> None: self.log("agent",   {"content": text})
    def tool_call(self, name: str, args: dict) -> None: self.log("tool_call", {"name": name, "args": args})
    def tool_result(self, name: str, result: str) -> None: self.log("tool_result", {"name": name, "result": result[:500]})  # pyre-ignore[arg-type]
    def phase_change(self, phase: Phase) -> None: self.log("phase",   {"phase": phase.value})
    def error(self, msg: str)           -> None: self.log("error",   {"msg": msg})
    def done(self, summary: str)        -> None: self.log("done",    {"summary": summary[:300]})  # pyre-ignore[arg-type]


# ══════════════════════════════════════════════════════════════════════════════
#  THINK STEP — De Claude. Razona antes de actuar.
#  "Think carefully before responding" — el patrón más valioso de Claude.
# ══════════════════════════════════════════════════════════════════════════════

THINK_PROMPT = """Antes de responder, razona paso a paso en PRIVADO:

<think>
1. ¿Qué quiere SER exactamente?
2. ¿Necesito ejecutar algo, ver algo, buscar algo?
3. ¿Qué herramienta es la más adecuada y por qué?
4. ¿Hay riesgos o efectos secundarios que deba considerar?
5. ¿Cuál es el plan mínimo y eficaz?
</think>

Tras razonar, actúa. Usa EXEC:/VISION:/SKILL: si necesitas ejecutar algo.
"""

SELF_CRITIQUE_PROMPT = """Evalúa tu respuesta anterior:
- ¿Respondiste lo que SER pedía realmente?
- ¿Faltó ejecutar algo que debías haber ejecutado?
- Si hay algo incompleto, complétalo ahora.
Si todo está bien, confirma con: ✅ Verificado.
"""


# ══════════════════════════════════════════════════════════════════════════════
#  SUBAGENTES — De CrewAI + Antigravity subagent pattern
#  Cada subagente es especialista en un dominio.
# ══════════════════════════════════════════════════════════════════════════════

class SubAgent:
    """Agente especializado en un dominio concreto."""

    def __init__(self, name: str, role: str, tools: list[str],
                 model: str = FAST_MODEL) -> None:
        self.name  = name
        self.role  = role
        self.tools = tools
        self.model = model

    def __repr__(self) -> str:
        return f"SubAgent({self.name}, tools={self.tools})"


SUBAGENTS: dict[str, SubAgent] = {
    "shell":   SubAgent("shell_agent",   "Ejecuta comandos bash y gestiona el SO", ["exec_shell"], FAST_MODEL),
    "vision":  SubAgent("vision_agent",  "Captura y analiza pantalla con moondream", ["take_screenshot"], "moondream2"),
    "recon":   SubAgent("recon_agent",   "Reconocimiento de red y OSINT", ["exec_shell"], DEEP_MODEL),
    "files":   SubAgent("files_agent",   "Gestión de archivos y directorios", ["exec_shell", "read_file", "write_file"], FAST_MODEL),
    "memory":  SubAgent("memory_agent",  "Guarda y recupera memoria persistente de EIDOS", [], FAST_MODEL),
    "coder":   SubAgent("coder_agent",   "Escribe, corrige y ejecuta código Python/Bash", ["exec_shell", "write_file"], FAST_MODEL),
}


def select_subagent(text: str) -> SubAgent:
    """Selecciona el subagente más adecuado para la tarea."""
    t = text.lower()
    if any(w in t for w in ["pantalla", "ves", "captura", "imagen", "screenshot"]):
        return SUBAGENTS["vision"]
    if any(w in t for w in ["nmap", "escanea", "osint", "whois", "red", "hosts", "puertos", "wifi"]):
        return SUBAGENTS["recon"]
    if any(w in t for w in ["archivo", "directorio", "carpeta", "ls", "find", "mueve", "copia"]):
        return SUBAGENTS["files"]
    if any(w in t for w in ["código", "script", "python", "bash", "programa", "función", "bug"]):
        return SUBAGENTS["coder"]
    if any(w in t for w in ["recuerda", "memoria", "apunta", "guarda esto", "recall"]):
        return SUBAGENTS["memory"]
    return SUBAGENTS["shell"]


# ══════════════════════════════════════════════════════════════════════════════
#  TOOL EXECUTOR — De OpenAI Agents. Ejecución de herramientas reales.
# ══════════════════════════════════════════════════════════════════════════════

def _exec_shell(command: str, timeout: int = 30) -> str:
    print(f"\033[91m[🔧 EXEC]\033[0m $ {command}")
    try:
        r = subprocess.run(
            command, shell=True, capture_output=True, text=True,
            timeout=timeout, env={**os.environ, "PYTHONPATH": EIDOS_DIR}
        )
        out = (r.stdout or r.stderr or "(sin output)").strip()
        print(f"\033[90m{out[:400]}\033[0m")  # pyre-ignore[arg-type]
        return out[:3000]  # pyre-ignore[arg-type]
    except subprocess.TimeoutExpired:
        return f"[TIMEOUT {timeout}s]"
    except Exception as e:
        return f"[ERR] {e}"


def _vision(question: str = "¿Qué hay en pantalla?") -> str:
    import base64
    ss_dir = os.path.expanduser("~/.eidos/screenshots")
    os.makedirs(ss_dir, exist_ok=True)
    path = f"{ss_dir}/screen_{int(time.time())}.png"
    for cmd in [f"scrot -z '{path}'", f"gnome-screenshot -f '{path}'"]:
        r = subprocess.run(cmd, shell=True, capture_output=True)
        if r.returncode == 0 and os.path.exists(path):
            break
    if not os.path.exists(path):
        return "[VISION] No hay pantalla. Instala scrot: sudo apt install scrot"
    with open(path, "rb") as f:
        img_b64 = base64.b64encode(f.read()).decode()
    payload = json.dumps({"model": "moondream2", "prompt": question,
                          "images": [img_b64], "stream": False,
                          "options": {"num_predict": 200}}).encode()
    req = urllib.request.Request(f"{OLLAMA_URL}/api/generate", data=payload,
                                 headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=60) as resp:
            return json.load(resp).get("response", "?")
    except Exception as e:
        return f"[VISION ERR] {e}"


TOOL_MAP: dict[str, Callable] = {
    "exec_shell": lambda a: _exec_shell(a.get("command", ""), int(a.get("timeout", 30))),
    "take_screenshot": lambda a: _vision(a.get("question", "¿Qué hay en pantalla?")),
    "read_file": lambda a: open(os.path.expanduser(a.get("path", "")), "r").read()[:3000]  # pyre-ignore[arg-type]
                           if os.path.exists(os.path.expanduser(a.get("path", ""))) else "[FILE NOT FOUND]",
    "write_file": lambda a: (
        open(os.path.expanduser(a.get("path", "/tmp/eidos_out.txt")), "w").write(a.get("content", ""))
        and f"✅ Escrito: {a.get('path')}"
    ),
}


# ══════════════════════════════════════════════════════════════════════════════
#  OLLAMA CALL — Llamada directa con tool calling o streaming
# ══════════════════════════════════════════════════════════════════════════════

def _call_ollama(messages: list[dict], model: str = FAST_MODEL,
                 tools: list[dict] | None = None,
                 num_ctx: int = 2048, num_predict: int = 512) -> dict:
    payload: dict[str, Any] = {
        "model": model, "messages": messages, "stream": False,
        "options": {"num_ctx": num_ctx, "num_predict": num_predict},
    }
    if tools:
        payload["tools"] = tools
    data = json.dumps(payload).encode()
    req  = urllib.request.Request(
        f"{OLLAMA_URL}/api/chat", data=data,
        headers={"Content-Type": "application/json"},
    )
    with urllib.request.urlopen(req, timeout=300) as resp:
        return json.load(resp)


def _stream_ollama(messages: list[dict], model: str = FAST_MODEL,
                   num_ctx: int = 2048, num_predict: int = 512) -> str:
    """Streaming para respuestas de texto (no tool calls)."""
    payload = json.dumps({
        "model": model, "messages": messages, "stream": True,
        "options": {"num_ctx": num_ctx, "num_predict": num_predict},
    }).encode()
    req = urllib.request.Request(f"{OLLAMA_URL}/api/chat", data=payload,
                                 headers={"Content-Type": "application/json"})
    print("\033[96m🦅 EIDOS:\033[0m ", end="", flush=True)
    full = ""
    start = time.time()
    try:
        with urllib.request.urlopen(req, timeout=300) as resp:
            for line in resp:
                if not line.strip():
                    continue
                chunk = json.loads(line)
                token = chunk.get("message", {}).get("content", "")
                if token:
                    print(token, end="", flush=True)
                    full += token
                if chunk.get("done"):
                    break
    except Exception as e:
        print(f"\n[ERR] {e}")
    elapsed = round(time.time() - start, 1)
    print(f"\n\033[90m  [{elapsed}s]\033[0m")
    return full


# ══════════════════════════════════════════════════════════════════════════════
#  EIDOS AGENT — El cerebro completo
# ══════════════════════════════════════════════════════════════════════════════

class EIDOSAgent:
    """
    Agente soberano EIDOS con arquitectura de fases inspirada en Antigravity:
      PLANNING → EXECUTION → VERIFICATION → DONE

    Incorpora:
      - Think step (Claude): razona antes de actuar
      - Subagente dispatch (CrewAI): delega al especialista correcto
      - Tool calling (OpenAI): herramientas nativas de Ollama
      - JSONL session log (OpenClaw): cada acción queda registrada
      - Self-critique (Constitutional AI): verifica su propio output
      - Autonomous loop (AutoGPT): puede ejecutar hasta max_steps antes de parar
    """

    def __init__(self, soul: str = "", agent_id: str = "eidos_main",
                 verbose: bool = True) -> None:
        self.soul     = soul or "Eres EIDOS, agente soberano de SER con acceso total al sistema."
        self.log      = SessionLog(agent_id)
        self.verbose  = verbose
        self.phase    = Phase.PLANNING
        self.history: list[dict] = []

    def _system(self) -> dict:
        return {"role": "system", "content": self.soul + "\n\n" + THINK_PROMPT}

    def _msgs(self, extra: list[dict] | None = None) -> list[dict]:
        msgs = [self._system()] + self.history[-16:]
        if extra:
            msgs.extend(extra)
        return msgs

    # ── Fase PLANNING: ¿qué necesita hacer EIDOS? ─────────────────────────
    def _plan(self, task: str) -> str:
        if self.verbose:
            print(f"\033[93m[📋 PLANNING]\033[0m {task[:80]}")  # pyre-ignore[arg-type]
        self.log.phase_change(Phase.PLANNING)
        self.phase = Phase.PLANNING

        plan_prompt = (
            f"Tarea: {task}\n\n"
            "En UNA sola frase, di qué herramienta usarás first y por qué. "
            "Luego actúa con EXEC:/VISION:/SKILL: si hace falta."
        )
        messages = self._msgs([{"role": "user", "content": plan_prompt}])
        try:
            plan = _stream_ollama(messages, FAST_MODEL, num_ctx=1024, num_predict=80)
            self.log.agent_msg(plan)
            return plan
        except Exception as e:
            return f"[PLAN ERR] {e}"

    # ── Fase EXECUTION: actúa ──────────────────────────────────────────────
    def _execute(self, task: str, subagent: SubAgent,
                 tools: list[dict] | None = None) -> str:
        if self.verbose:
            print(f"\033[91m[⚡ EXECUTION — {subagent.name}]\033[0m")
        self.log.phase_change(Phase.EXECUTION)
        self.phase = Phase.EXECUTION

        messages = self._msgs([{"role": "user", "content": task}])

        # Tool calling si el modelo del subagente lo soporta (hermes3)
        if tools and subagent.model == DEEP_MODEL:
            try:
                resp = _call_ollama(messages, subagent.model, tools=tools,
                                    num_ctx=4096, num_predict=512)
                msg       = resp.get("message", {})
                tool_calls = msg.get("tool_calls", [])
                content    = msg.get("content", "")

                if tool_calls:
                    # Ejecutar tool calls en secuencia (safe; no paralelo sin confirmación)
                    results = []
                    for tc in tool_calls:
                        fn_name = tc.get("function", {}).get("name", "")
                        fn_args = tc.get("function", {}).get("arguments", {})
                        if isinstance(fn_args, str):
                            try:
                                fn_args = json.loads(fn_args)
                            except Exception:
                                fn_args = {}
                        self.log.tool_call(fn_name, fn_args)
                        if fn_name in TOOL_MAP:
                            result = str(TOOL_MAP[fn_name](fn_args))
                        else:
                            result = f"[UNKNOWN TOOL] {fn_name}"
                        self.log.tool_result(fn_name, result)
                        results.append(f"[{fn_name}]: {result[:500]}")  # pyre-ignore[arg-type]
                    return "\n".join(results)
                return content
            except Exception as e:
                self.log.error(str(e))
                # Fallback a streaming
                pass

        # Streaming rápido con EXEC: parser (qwen2.5 / fallback)
        result = _stream_ollama(messages, subagent.model, num_ctx=2048, num_predict=400)
        # Auto-ejecutar si el modelo generó EXEC:/VISION:
        import re
        exec_m = re.search(r'EXEC:\s*(.+?)(?:\n|$)', result)
        if exec_m:
            cmd = exec_m.group(1).strip()
            self.log.tool_call("exec_shell", {"command": cmd})
            out = _exec_shell(cmd)
            self.log.tool_result("exec_shell", out)
            return out
        vision_m = re.search(r'VISION:\s*(.+?)(?:\n|$)', result)
        if vision_m:
            q = vision_m.group(1).strip()
            self.log.tool_call("take_screenshot", {"question": q})
            out = _vision(q)
            self.log.tool_result("take_screenshot", out)
            return out
        return result

    # ── Fase VERIFICATION: self-critique (Claude pattern) ─────────────────
    def _verify(self, original_task: str, result: str) -> str:
        if self.verbose:
            print(f"\033[92m[✅ VERIFICATION]\033[0m")
        self.log.phase_change(Phase.VERIFICATION)
        self.phase = Phase.VERIFICATION

        critique_msgs = self._msgs([
            {"role": "assistant", "content": result},
            {"role": "user",      "content": f"Tarea original: {original_task}\n\n{SELF_CRITIQUE_PROMPT}"}
        ])
        try:
            verdict = _stream_ollama(critique_msgs, FAST_MODEL, num_ctx=1024, num_predict=80)
            self.log.agent_msg(verdict)
            return verdict
        except Exception:
            return result

    # ── Loop principal — AutoGPT pattern ──────────────────────────────────
    def run(self, task: str,
            history: list[dict] | None = None,
            use_tools: bool = True,
            verify: bool = False,
            max_steps: int = 3) -> str:
        """
        Ejecuta la tarea completa:
          1. PLANNING — ¿cómo la abordamos?
          2. EXECUTION — actúa con el subagente correcto
          3. VERIFICATION (opcional) — self-critique
        """
        self.log.user_msg(task)
        if history:
            self.history = list(history)

        # 1. Seleccionar subagente especializado (CrewAI pattern)
        subagent = select_subagent(task)
        if self.verbose:
            print(f"\033[95m[🤝 SubAgent]\033[0m {subagent.name}")

        # 2. Importar tools del módulo tools.py si existe
        tools: list[dict] | None = None
        if use_tools:
            try:
                from core.tools import TOOLS  # type: ignore[import]
                tools = TOOLS
            except ImportError:
                tools = None

        # 3. PLANNING
        _plan_result = self._plan(task)

        # 4. EXECUTION con el subagente
        result = self._execute(task, subagent, tools=tools)

        # 5. Añadir al historial
        self.history.append({"role": "user",      "content": task})
        self.history.append({"role": "assistant",  "content": result})

        # 6. VERIFICATION (solo si se pide o si la tarea es crítica)
        if verify or any(w in task.lower() for w in ["verifica", "comprueba", "asegura"]):
            result = self._verify(task, result)

        # 7. Cerrar sesión
        self.log.done(result)
        self.phase = Phase.DONE
        return result

    def reset(self) -> None:
        """Limpia el historial de conversación (nueva sesión)."""
        self.history.clear()
        self.phase = Phase.PLANNING
        if self.verbose:
            print("\033[95m[EIDOS] Historial limpiado — nueva sesión.\033[0m")

    @property
    def session_file(self) -> str:
        return self.log.path


# ── Test rápido ──────────────────────────────────────────────────────────────
if __name__ == "__main__":
    print("=== Test EIDOSAgent ===")
    agent = EIDOSAgent(agent_id="test")
    print(f"Session log: {agent.session_file}")
    result = agent.run("dime qué hora es en el sistema", use_tools=False, verify=False)
    print(f"\nResultado: {result[:200]}")  # pyre-ignore[arg-type]
    print(f"Phase: {agent.phase}")
    print(f"Historial: {len(agent.history)} msgs")
