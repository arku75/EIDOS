"""
core/uitars_scaffold.py — UI-TARS Agentic Scaffold COMPLETO (port a Python)
===========================================================================
Lógica AGÉNTICA COMPLETA portada del proyecto open-source
**bytedance/UI-TARS-desktop** (Apache-2.0). NO incluye el modelo UI-TARS
(eso necesita GPU): incluye TODO el MÉTODO — cómo piensa, planifica,
actúa, duda, reintenta, aprende y cuándo te pregunta — que funciona con
CUALQUIER modelo (incluido lfm2.5-thinking:1.2b local en Kali, SIN GPU).

Qué se portó (del repo UI-TARS, Apache-2.0):
  - sdk/src/constants.ts            → action space + system prompt
  - action-parser/src/actionParser.ts → parser Thought/Action + escalado
  - sdk/src/GUIAgent.ts             → loop, StatusEnum, errores, reintentos,
                                       maxLoop, snapshotErr, pause/abort
  - agent-sdk/src/prompts.ts        → estilo de pensamiento (plan + 1 frase),
                                       aprender-haciendo, hipótesis→prueba→
                                       verifica, multi-acción, call_user

DOS MODOS:
  • mode="gui"  → ve la pantalla (VLM). Topado por GPU (visión lenta sin GPU).
  • mode="text" → razona sobre shell/tools/preguntas con hermes3 LOCAL.
                  SIN visión = SIN muro de GPU. Funciona HOY, rápido-ish.
  Así EIDOS piensa con ESTE método para TODO, no solo para la pantalla.

Extras EIDOS conectados:
  • call_user()  → te escribe a TI (SER) por Telegram y espera tu guía.
  • aprende-haciendo → lo que descubre en `Thought` lo guarda en el brain.
  • ejecución reutiliza computer_use_v2.DesktopController (no duplica).

Uso:
    from core.uitars_scaffold import UITarsScaffold
    a = UITarsScaffold(mode="text")
    print(a.run("revisa cuánta RAM libre hay y dime si es poca").summary())

    a = UITarsScaffold(mode="gui")          # necesita VLM (GPU para ir bien)
    a.run("abre Firefox y ve a github.com")

CLI:
    python3 core/uitars_scaffold.py --test
    python3 core/uitars_scaffold.py --run-text "<tarea>"
    python3 core/uitars_scaffold.py --run "<tarea gui>" [--dry]
"""
from __future__ import annotations

import os
import re
import json
import time
import base64
import io
import enum
import urllib.request
import urllib.error
import ssl


def _ssl_ctx() -> "ssl.SSLContext":
    """Contexto SSL robusto. En macOS el Python framework no trae CA
    certs → CERTIFICATE_VERIFY_FAILED. Usa certifi si está; si no,
    el default del sistema. NO desactiva verificación (seguro)."""
    try:
        import certifi
        return ssl.create_default_context(cafile=certifi.where())
    except Exception:  # noqa: BLE001
        try:
            return ssl.create_default_context()
        except Exception:  # noqa: BLE001
            return None  # urlopen usará el default


_SSL_CTX = _ssl_ctx()
import subprocess
import logging
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Optional, Callable

log = logging.getLogger("eidos.uitars")

def _load_secrets() -> dict:
    """Lee ~/.eidos/secrets.env (KEY=valor, chmod 600) + env. Las keys
    NUNCA se loguean ni se escriben en TASK.md — solo se usan en memoria."""
    sec: dict[str, str] = {}
    path = os.path.expanduser("~/.eidos/secrets.env")
    try:
        if os.path.exists(path):
            for line in open(path, encoding="utf-8"):
                line = line.strip()
                if line and not line.startswith("#") and "=" in line:
                    k, _, v = line.partition("=")
                    sec[k.strip()] = v.strip().strip('"\'')
    except Exception as e:  # noqa: BLE001
        log.debug("secrets.env no leído: %s", e)
    for k in ("GROQ_API_KEY", "HF_TOKEN", "ANTHROPIC_API_KEY", "OPENAI_API_KEY"):
        if os.environ.get(k):
            sec[k] = os.environ[k]
    return sec


_SECRETS = _load_secrets()

def _collect_keys(prefix: str) -> list[str]:
    """Junta TODAS las keys de un servicio para rotación:
       PREFIX, PREFIX_2, PREFIX_3...  y  PREFIX(S)=k1,k2,k3 (coma).
       Permite que EIDOS rote cuando una se agota (free-tier límite)."""
    out: list[str] = []
    base = _SECRETS.get(prefix, "")
    if base:
        out.append(base)
    multi = _SECRETS.get(prefix + "S", "") or _SECRETS.get(prefix + "_LIST", "")
    for k in multi.split(","):
        k = k.strip()
        if k:
            out.append(k)
    i = 2
    while True:
        k = _SECRETS.get(f"{prefix}_{i}", "")
        if not k:
            break
        out.append(k)
        i += 1
    # únicas, orden estable
    seen, uniq = set(), []
    for k in out:
        if k not in seen:
            seen.add(k)
            uniq.append(k)
    return uniq


# Backend del cerebro: "ollama" (local, sin GPU, lento) | "groq" (API
# remota, rapidísima). Auto: si hay alguna GROQ key → groq; si no → ollama.
GROQ_KEYS    = _collect_keys("GROQ_API_KEY")
HF_TOKENS    = _collect_keys("HF_TOKEN")
GROQ_API_KEY = GROQ_KEYS[0] if GROQ_KEYS else ""   # compat retro
_GROQ_IDX    = 0                                    # índice de rotación
GROQ_MODEL   = os.environ.get("EIDOS_GROQ_MODEL", "llama-3.1-8b-instant")
GROQ_URL     = "https://api.groq.com/openai/v1/chat/completions"
LLM_BACKEND  = os.environ.get(
    "EIDOS_LLM_BACKEND", "groq" if GROQ_KEYS else "ollama")

# Gemini: backend de texto de RESPALDO (rotación tras Groq) y, lo
# importante, **VLM gratis** → visión sin GPU (sustituye moondream:latest).
GEMINI_KEYS  = _collect_keys("GEMINI_API_KEY")
_GEM_IDX     = 0
GEMINI_TEXT_MODEL = os.environ.get("EIDOS_GEMINI_MODEL", "gemini-2.0-flash")
GEMINI_VLM_MODEL  = os.environ.get("EIDOS_GEMINI_VLM", "gemini-2.0-flash")
GEMINI_URL   = "https://generativelanguage.googleapis.com/v1beta/models"

VISION_MODEL = os.environ.get("EIDOS_VLM_MODEL", "moondream:latest")
TEXT_MODEL   = os.environ.get("EIDOS_TEXT_MODEL", "lfm2.5-1.2b-instruct:q4_0")
DEFAULT_FACTOR = (1000, 1000)
SCREENSHOT_DIR = os.path.expanduser("~/.eidos/screenshots")
os.makedirs(SCREENSHOT_DIR, exist_ok=True)

MAX_LOOP_COUNT     = int(os.environ.get("EIDOS_UITARS_MAXLOOP", "20"))
MAX_SNAPSHOT_ERR   = int(os.environ.get("EIDOS_UITARS_MAXSNAPERR", "5"))
MODEL_RETRIES      = int(os.environ.get("EIDOS_UITARS_MODELRETRY", "2"))

_VLM_CANDIDATES = [
    os.environ.get("EIDOS_VLM_URL", ""),   # override explícito
    "http://localhost:11435",              # Mac vía túnel (moondream:latest)
    "http://localhost:11434",              # Kali local
]
_TEXT_CANDIDATES = [
    os.environ.get("EIDOS_TEXT_URL", ""),
    "http://localhost:11434",              # Kali local (hermes3) — preferido
    "http://localhost:11435",              # Mac vía túnel
]


def _resolve_url(model: str, candidates: list[str]) -> str:
    for url in candidates:
        if not url:
            continue
        try:
            with urllib.request.urlopen(f"{url}/api/tags", timeout=4) as r:
                names = [m["name"] for m in json.load(r).get("models", [])]
            if any(model.split(":")[0] in n for n in names):
                return url
        except Exception:  # noqa: BLE001
            continue
    return candidates[1] if len(candidates) > 1 and candidates[1] else "http://localhost:11434"


# ═══════════════════════════════════════════════════════════════════════════
#  ESTADOS  (port de StatusEnum / ErrorStatusEnum de GUIAgent.ts)
# ═══════════════════════════════════════════════════════════════════════════

class Status(enum.Enum):
    INIT = "init"
    RUNNING = "running"
    PAUSE = "pause"
    END = "end"
    ERROR = "error"
    USER_STOPPED = "user_stopped"
    CALL_USER = "call_user"
    MAX_LOOP = "max_loop"


class ErrCode(enum.Enum):
    REACH_MAXLOOP = "reach_maxloop_error"
    SCREENSHOT_RETRY = "screenshot_retry_error"
    INVOKE_RETRY = "model_invoke_retry_error"
    ENVIRONMENT = "environment_error"


# ═══════════════════════════════════════════════════════════════════════════
#  PROMPTS  (port + fusión de constants.ts y agent-sdk/prompts.ts)
#  Incluye: estilo de pensamiento, plan corto + 1 frase, aprender-haciendo,
#  método hipótesis→prueba→verifica, multi-acción, call_user.
# ═══════════════════════════════════════════════════════════════════════════

_THOUGHT_METHOD = """## How to think (Thought style — follow strictly)
- Write a SMALL plan, then summarize your next action (with its target) in ONE sentence.
- Reason like: observe → form a hypothesis → choose an action to test it → predict
  the result. Next turn: verify if the result matched; if not, update the hypothesis.
- If you discover a new rule, fact or behaviour while doing the task, STATE it
  explicitly in `Thought` ("I learned that ...") so it can be remembered and reused.
- If the task is unsolvable or you genuinely need the human, use call_user().
- Be concrete. No vague filler. Each Thought must change what happens next."""

UITARS_GUI_PROMPT = f"""You are a GUI agent. You are given a task and your action history, with screenshots. You need to perform the next action to complete the task.

## Output Format
```
Thought: ...
Action: ...
```

## Action Space
click(start_box='[x1, y1, x2, y2]')
left_double(start_box='[x1, y1, x2, y2]')
right_single(start_box='[x1, y1, x2, y2]')
drag(start_box='[x1, y1, x2, y2]', end_box='[x3, y3, x4, y4]')
hotkey(key='') # Split keys with a space, lowercase, max 3 keys.
type(content='') # Use \\n at the end of content to submit.
scroll(start_box='[x1, y1, x2, y2]', direction='down or up or right or left')
wait() # Sleep 5s and screenshot to check changes.
finished(content='') # Submit final report to the user.
call_user() # When the task is unsolvable or you need the user's help.

{_THOUGHT_METHOD}
- Coordinates are normalized 0-1000 (0,0 top-left, 1000,1000 bottom-right).
- You may provide multiple actions in one step separated by a blank line.

## User Instruction
"""

UITARS_TEXT_PROMPT = f"""You are EIDOS, a general AI agent that solves tasks by reasoning and using tools on a Linux/macOS system. You are given a task and your action history (tool results). You perform ONE next action per turn.

## Output Format
```
Thought: ...
Action: ...
```

## Action Space
shell(cmd='...') # Run a shell command, observe its output.
search_brain(query='...') # Search EIDOS knowledge base.
note(content='...') # Record something you learned (saved to the brain).
wait() # Sleep 3s (e.g. wait for a process).
finished(content='...') # Submit the final answer/report to the user.
call_user() # When the task is unsolvable or you need the user's help.

{_THOUGHT_METHOD}
- You may provide multiple actions in one step separated by a blank line.

## User Instruction
"""

UITARS_GUI_OCR_PROMPT = f"""You are a GUI agent. You DO NOT see images. Instead you are given the on-screen UI elements detected by OCR+OpenCV (an accessibility tree), the task, and your action history. Perform the next action.

## Output Format
```
Thought: ...
Action: ...
```

## On-screen elements
Each line: [ref] type "text" @(cx,cy) WxH
You act on elements BY THEIR ref (e.g. e5), never invent coordinates.

## Action Space
click(ref='e5')
left_double(ref='e5')
right_single(ref='e5')
type(content='') # types into the currently focused field; \\n submits.
hotkey(key='') # space-separated, lowercase, max 3 keys.
scroll(direction='down or up or left or right')
wait() # Sleep 5s, re-scan the screen.
finished(content='') # Final report to the user.
call_user() # Task unsolvable or you need the user's help.

{_THOUGHT_METHOD}
- If the target element is NOT in the list, scroll or wait and re-scan;
  if it never appears, use call_user(). Never guess a ref that isn't listed.

## User Instruction
"""

INTERNAL_STOP = {"finished", "call_user"}


# ═══════════════════════════════════════════════════════════════════════════
#  PARSER  (port de action-parser/src/actionParser.ts) — self-test PASS
# ═══════════════════════════════════════════════════════════════════════════

@dataclass
class ParsedAction:
    thought: str
    reflection: Optional[str]
    action_type: str
    action_inputs: dict
    raw: str

    def __str__(self) -> str:
        coord = ""
        if "start_coords" in self.action_inputs:
            coord = f" @{self.action_inputs['start_coords']}"
        return f"{self.action_type}({self.action_inputs}){coord}"


def _parse_action_call(action_str: str) -> Optional[dict]:
    """Port de parseAction(): 'click(start_box=\\'[..]\\')' → {function, args}."""
    try:
        s = action_str.strip()
        s = s.replace("<|box_start|>", "").replace("<|box_end|>", "")
        s = re.sub(r"(?<!start_)(?<!end_)point=", "start_box=", s)
        s = s.replace("start_point=", "start_box=").replace("end_point=", "end_box=")
        m = re.match(r"^(\w+)\((.*)\)$", s.strip(), re.DOTALL)
        if not m:
            return None
        fn, args_str = m.group(1), m.group(2)
        kwargs: dict[str, str] = {}
        # Acciones de UN argumento de texto libre (content/cmd): captura
        # GREEDY todo tras 'clave=' hasta el cierre. Modelos flojos
        # (hermes3) meten comas/comillas dentro → no partir por comas.
        _SINGLE = {"finished": "content", "type": "content",
                   "shell": "cmd", "note": "content", "search_brain": "query"}
        if fn in _SINGLE and args_str.strip():
            mm = re.match(r"\s*(\w+)\s*=\s*(.*)\s*$", args_str, re.DOTALL)
            if mm:
                val = mm.group(2).strip()
                if len(val) >= 2 and val[0] in "'\"" and val[-1] == val[0]:
                    val = val[1:-1]
                else:
                    val = val.strip("'\"")
                return {"function": fn, "args": {mm.group(1).strip(): val}}
            return {"function": fn, "args": {}}
        if args_str.strip():
            pairs = re.findall(r"(?:[^,']|'[^']*')+", args_str)
            for pair in pairs:
                if "=" not in pair:
                    continue
                key, _, val = pair.partition("=")
                key = key.strip()
                val = val.strip().strip("'\"")
                if "<bbox>" in val:
                    val = re.sub(r"</?bbox>", "", val)
                    val = "(" + re.sub(r"\s+", ",", val.strip()) + ")"
                if "<point>" in val:
                    val = re.sub(r"</?point>", "", val)
                    val = "(" + re.sub(r"\s+", ",", val.strip()) + ")"
                kwargs[key] = val
        return {"function": fn, "args": kwargs}
    except Exception as e:  # noqa: BLE001
        log.debug("parseAction fallo '%s': %s", action_str, e)
        return None


def parse_prediction(
    text: str,
    screen_w: int = 1920,
    screen_h: int = 1080,
    factors: tuple[int, int] = DEFAULT_FACTOR,
) -> list[ParsedAction]:
    """Port de parseActionVlm(): extrae Thought + Action(s), escala cajas."""
    text = (text or "").strip()
    thought: Optional[str] = None
    reflection: Optional[str] = None

    if "Thought:" in text:
        mt = re.search(r"Thought: ([\s\S]+?)(?=\s*Action[:：]|$)", text)
        if mt:
            thought = mt.group(1).strip()
    elif text.startswith("Reflection:"):
        mr = re.search(r"Reflection: ([\s\S]+?)Action_Summary: ([\s\S]+?)(?=\s*Action[:：]|$)", text)
        if mr:
            reflection = mr.group(1).strip()
            thought = mr.group(2).strip()
    elif text.startswith("Action_Summary:"):
        ms = re.search(r"Action_Summary: (.+?)(?=\s*Action[:：]|$)", text)
        if ms:
            thought = ms.group(1).strip()

    if not re.search(r"Action[:：]", text):
        action_str = text
    else:
        action_str = re.split(r"Action[:：]", text)[-1]

    out: list[ParsedAction] = []
    for raw in action_str.split("\n\n"):
        raw = raw.strip()
        if not raw:
            continue
        inst = _parse_action_call(raw.replace("\n", r"\n"))
        if not inst:
            continue
        a_type = inst["function"]
        a_in: dict[str, Any] = {}
        for pname, pval in inst["args"].items():
            if not pval:
                continue
            pval = pval.strip()
            if "start_box" in pname or "end_box" in pname:
                nums = [n for n in re.sub(r"[()\[\]]", "", pval).split(",") if n != ""]
                try:
                    fl = [float(n) / factors[i % 2] for i, n in enumerate(nums)]
                except ValueError:
                    continue
                if len(fl) == 2:
                    fl += [fl[0], fl[1]]
                a_in[pname] = json.dumps(fl)
                x1, y1 = fl[0], fl[1]
                x2 = fl[2] if len(fl) > 2 else x1
                y2 = fl[3] if len(fl) > 3 else y1
                cx = round(((x1 + x2) / 2) * screen_w)
                cy = round(((y1 + y2) / 2) * screen_h)
                key = "start_coords" if "start_box" in pname else "end_coords"
                a_in[key] = [cx, cy]
            else:
                a_in[pname] = pval
        out.append(ParsedAction(thought or "", reflection, a_type, a_in, raw))
    return out


# ═══════════════════════════════════════════════════════════════════════════
#  AGENT LOOP COMPLETO  (port de GUIAgent.ts: estados, errores, reintentos)
# ═══════════════════════════════════════════════════════════════════════════

@dataclass
class Step:
    n: int
    thought: str
    action_type: str
    action_inputs: dict
    observation: str
    elapsed: float = 0.0

    def __str__(self) -> str:
        return (f"[{self.n}] 💭 {self.thought[:100]}\n"
                f"    🎯 {self.action_type}({json.dumps(self.action_inputs, ensure_ascii=False)[:90]})\n"
                f"    👁  {self.observation[:120]}")


@dataclass
class RunResult:
    task: str
    mode: str
    steps: list[Step] = field(default_factory=list)
    status: Status = Status.INIT
    error: Optional[ErrCode] = None
    learned: list[str] = field(default_factory=list)
    answer: str = ""
    total_s: float = 0.0

    @property
    def success(self) -> bool:
        return self.status == Status.END

    def summary(self) -> str:
        head = (f"UI-TARS scaffold [{self.mode}] · '{self.task}'\n"
                f"  pasos={len(self.steps)} estado={self.status.value}"
                f"{' err=' + self.error.value if self.error else ''}"
                f" {self.total_s:.1f}s")
        body = "\n".join(str(s) for s in self.steps)
        learn = ("\n  📚 aprendido: " + " | ".join(self.learned)) if self.learned else ""
        ans = ("\n  ✅ " + self.answer) if self.answer else ""
        return head + "\n" + body + learn + ans


class UITarsScaffold:
    """Loop agéntico estilo UI-TARS sobre el cuerpo + cerebro de EIDOS."""

    def __init__(self, mode: str = "text", dry_run: bool = False,
                 on_call_user: Optional[Callable[[str], str]] = None,
                 gui_prompt_variant: Optional[str] = None,
                 vision_backend: str = "vlm") -> None:
        assert mode in ("gui", "text"), "mode debe ser 'gui' o 'text'"
        assert vision_backend in ("vlm", "ocr"), "vision_backend: 'vlm' o 'ocr'"
        self.mode = mode
        self.dry_run = dry_run
        # vlm = ve la pantalla con moondream:latest (necesita GPU para ir bien).
        # ocr = OCR+OpenCV (ui_parser) → SIN GPU; el cerebro es el modelo
        #       de TEXTO razonando sobre la lista de elementos detectados.
        self.vision_backend = vision_backend
        self._uiparser = None
        self._elem_map: dict[str, Any] = {}
        # Cualquiera de las 7 variantes UI-TARS portadas (uitars_prompts.py)
        self._gui_variant = gui_prompt_variant
        self.on_call_user = on_call_user or self._telegram_call_user
        self._abort = False
        self._ctrl = None
        if mode == "gui" and vision_backend == "ocr":
            try:
                try:
                    from core.ui_parser import UIParser
                except ImportError:
                    import sys as _sys
                    _root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
                    if _root not in _sys.path:
                        _sys.path.insert(0, _root)
                    from core.ui_parser import UIParser
                self._uiparser = UIParser()
            except Exception as e:  # noqa: BLE001
                log.warning("UIParser no disponible (%s) — fallback vlm", e)
                self.vision_backend = "vlm"
        if mode == "gui":
            try:
                try:
                    from core.computer_use_v2 import DesktopController
                except ImportError:
                    import sys as _sys
                    _root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
                    if _root not in _sys.path:
                        _sys.path.insert(0, _root)
                    from core.computer_use_v2 import DesktopController
                self._ctrl = DesktopController()
            except Exception as e:  # noqa: BLE001
                log.warning("DesktopController no disponible (%s) — dry_run", e)
                self.dry_run = True
        # gui+ocr razona sobre TEXTO (lista de elementos) → modelo de texto.
        # gui+vlm necesita el modelo de visión. text → modelo de texto.
        use_vlm = (mode == "gui" and self.vision_backend == "vlm")
        self.model = VISION_MODEL if use_vlm else TEXT_MODEL
        self.url = (_resolve_url(VISION_MODEL, _VLM_CANDIDATES) if use_vlm
                    else _resolve_url(TEXT_MODEL, _TEXT_CANDIDATES))

    def abort(self) -> None:
        self._abort = True

    # ── pantalla (modo gui) ─────────────────────────────────────────────────

    def _screen_size(self) -> tuple[int, int]:
        try:
            import pyautogui
            w, h = pyautogui.size()
            return int(w), int(h)
        except Exception:
            try:
                out = subprocess.check_output(["xdotool", "getdisplaygeometry"],
                                              text=True).split()
                return int(out[0]), int(out[1])
            except Exception:
                return 1920, 1080

    def _screenshot(self) -> str:
        ts = datetime.now().strftime("%Y%m%d_%H%M%S_%f")
        path = os.path.join(SCREENSHOT_DIR, f"uitars_{ts}.png")
        try:
            subprocess.run(["scrot", "-z", path], check=True,
                           capture_output=True, timeout=15)
            return path
        except Exception as e:  # noqa: BLE001
            log.error("screenshot fallo: %s", e)
            return ""

    @staticmethod
    def _encode(path: str) -> str:
        from PIL import Image
        with Image.open(path) as img:
            img = img.convert("RGB")
            img.thumbnail((1280, 720), Image.Resampling.LANCZOS)
            buf = io.BytesIO()
            img.save(buf, format="JPEG", quality=85)
            return base64.b64encode(buf.getvalue()).decode()

    # ── modelo (con reintentos — port de asyncRetry) ────────────────────────

    def _gemini_call(self, prompt: str, img_b64: Optional[str]) -> Optional[str]:
        """Gemini REST. Texto Y visión (VLM gratis, sin GPU). Rota keys
        en 429/403/401. Devuelve None si todas fallan (para fallback)."""
        global _GEM_IDX
        if not GEMINI_KEYS:
            return None
        model = GEMINI_VLM_MODEL if img_b64 else GEMINI_TEXT_MODEL
        parts: list[dict] = [{"text": prompt}]
        if img_b64:
            parts.append({"inline_data": {"mime_type": "image/jpeg",
                                          "data": img_b64}})
        body = {"contents": [{"parts": parts}],
                "generationConfig": {"temperature": 0.0,
                                     "maxOutputTokens": 400}}
        n = len(GEMINI_KEYS)
        for hop in range(n):
            key = GEMINI_KEYS[(_GEM_IDX + hop) % n]
            try:
                req = urllib.request.Request(
                    f"{GEMINI_URL}/{model}:generateContent?key={key}",
                    data=json.dumps(body).encode(),
                    headers={"Content-Type": "application/json",
                             "User-Agent": "EIDOS/1.0"})
                with urllib.request.urlopen(req, timeout=40,
                                            context=_SSL_CTX) as r:
                    d = json.load(r)
                _GEM_IDX = (_GEM_IDX + hop) % n
                return d["candidates"][0]["content"]["parts"][0]["text"]
            except urllib.error.HTTPError as e:  # noqa: PERF203
                if e.code in (401, 403, 429):
                    log.warning("gemini key #%d límite (%s) → roto",
                                (_GEM_IDX + hop) % n + 1, e.code)
                    time.sleep(0.5)
                    continue
                log.warning("gemini error %s", e.code)
                break
            except Exception as e:  # noqa: BLE001
                log.warning("gemini fallo: %s", e)
                time.sleep(0.8)
        return None

    def _call_model(self, prompt: str, img_b64: Optional[str]) -> str:
        # ── Paso 4: TEXTO pasa por el gateway unificado eidos_llm ──────
        # (identidad EIDOS inyectada + guard determinista + fallback
        # multi-proveedor con rotación). La VISIÓN (img_b64) NO pasa por
        # aquí: sigue el path VLM de abajo (Gemini/Ollama). Si eidos_llm
        # falla a importar, cae a los branches a mano (defensivo: no
        # romper el scaffold).
        if img_b64 is None:
            try:
                try:
                    from core.eidos_llm import complete as _eidos_complete
                except ImportError:
                    import sys as _s
                    _s.path.insert(0, os.path.dirname(
                        os.path.dirname(os.path.abspath(__file__))))
                    from core.eidos_llm import complete as _eidos_complete
                _r = _eidos_complete(prompt, max_tokens=400)
                if _r and _r.ok and _r.text:
                    return _r.text
                log.warning("eidos_llm sin respuesta (%s) — fallback legacy",
                            getattr(_r, "provider", "?"))
            except Exception as e:  # noqa: BLE001
                log.warning("eidos_llm no usable (%s) — fallback legacy", e)

        # VISIÓN sin GPU: si hay imagen y key Gemini → Gemini VLM (gratis,
        # sustituye al moondream:latest que necesitaba GPU). Si falla → sigue al
        # path Ollama VLM de abajo.
        if img_b64 is not None and GEMINI_KEYS:
            g = self._gemini_call(prompt, img_b64)
            if g:
                return g
            log.warning("gemini VLM falló — fallback ollama VLM")

        # Groq: API remota rapidísima. Se usa para texto y gui+ocr (que
        # razona sobre texto). NO para gui+vlm (Groq no corre el VLM
        # local de visión por imagen → eso sigue en Ollama).
        use_groq = (LLM_BACKEND == "groq" and GROQ_KEYS
                    and img_b64 is None)
        if use_groq:
            global _GROQ_IDX
            gp = {
                "model": GROQ_MODEL,
                "messages": [{"role": "user", "content": prompt}],
                "temperature": 0.0,
                "max_tokens": 400,
            }
            last = ""
            n = len(GROQ_KEYS)
            # Rota por TODAS las keys; en 401/403/429 (agotada/límite)
            # pasa a la siguiente. Empieza por la última que funcionó.
            for hop in range(n):
                key = GROQ_KEYS[(_GROQ_IDX + hop) % n]
                try:
                    req = urllib.request.Request(
                        GROQ_URL, data=json.dumps(gp).encode(),
                        headers={"Content-Type": "application/json",
                                 "Authorization": f"Bearer {key}",
                                 # WAF de Groq bloquea el UA por defecto de
                                 # urllib (Python-urllib) → 403. UA normal.
                                 "User-Agent": "EIDOS/1.0 (+https://eidos.local)",
                                 "Accept": "application/json"})
                    with urllib.request.urlopen(req, timeout=30,
                                                context=_SSL_CTX) as r:
                        d = json.load(r)
                    _GROQ_IDX = (_GROQ_IDX + hop) % n   # recuerda la buena
                    return d["choices"][0]["message"]["content"]
                except urllib.error.HTTPError as e:  # noqa: PERF203
                    last = f"HTTP {e.code}"
                    if e.code in (401, 403, 429):
                        log.warning("groq key #%d agotada/límite (%s) → roto",
                                    (_GROQ_IDX + hop) % n + 1, e.code)
                        time.sleep(0.5)
                        continue          # siguiente key
                    log.warning("groq error %s (no rota)", e.code)
                    break
                except Exception as e:  # noqa: BLE001
                    last = str(e)
                    log.warning("groq fallo red: %s", e)
                    time.sleep(1.0)
            log.warning("groq: %d key(s) agotadas (%s) — pruebo gemini",
                        n, last)
            # Cadena de rotación: Groq → Gemini → Ollama (último recurso).
            g = self._gemini_call(prompt, None)
            if g:
                log.info("gemini (texto) respondió tras groq agotado")
                return g
            log.warning("gemini también agotado — fallback ollama")

        msg: dict[str, Any] = {"role": "user", "content": prompt}
        if img_b64:
            msg["images"] = [img_b64]
        payload = {
            "model": self.model,
            "messages": [msg],
            "stream": False,
            "options": {"num_ctx": 4096, "num_predict": 320, "temperature": 0.0},
        }
        last = ""
        for attempt in range(MODEL_RETRIES + 1):
            try:
                req = urllib.request.Request(
                    f"{self.url}/api/chat",
                    data=json.dumps(payload).encode(),
                    headers={"Content-Type": "application/json"})
                timeout = 180 if self.mode == "gui" else 90
                with urllib.request.urlopen(req, timeout=timeout) as r:
                    return json.load(r).get("message", {}).get("content", "")
            except Exception as e:  # noqa: BLE001
                last = str(e)
                log.warning("model intento %d/%d falló: %s",
                            attempt + 1, MODEL_RETRIES + 1, e)
                time.sleep(1.5)
        raise RuntimeError(f"model retries agotados: {last}")

    # ── call_user vía Telegram a SER ────────────────────────────────────────

    @staticmethod
    def _telegram_call_user(question: str) -> str:
        token = os.environ.get("EIDOS_TELEGRAM_TOKEN", "")
        chat_ids = os.environ.get("EIDOS_TELEGRAM_ALLOWED", "")
        if not token or not chat_ids:
            log.info("call_user (sin telegram): %s", question)
            return "[call_user: telegram no configurado — sin respuesta]"
        text = f"🟣 EIDOS necesita tu ayuda (UI-TARS scaffold):\n{question}"
        for cid in chat_ids.split(","):
            cid = cid.strip()
            if not cid.lstrip("-").isdigit():
                continue
            try:
                data = json.dumps({"chat_id": int(cid), "text": text}).encode()
                req = urllib.request.Request(
                    f"https://api.telegram.org/bot{token}/sendMessage",
                    data=data, headers={"Content-Type": "application/json"})
                urllib.request.urlopen(req, timeout=10)
            except Exception as e:  # noqa: BLE001
                log.warning("telegram call_user falló: %s", e)
        return "[call_user: pregunta enviada a SER por Telegram]"

    # ── aprende-haciendo → brain ────────────────────────────────────────────

    @staticmethod
    def _extract_learning(thought: str) -> Optional[str]:
        for pat in (r"I learned that (.+?)(?:\.|$)",
                    r"aprend[íi] que (.+?)(?:\.|$)",
                    r"new rule[:\s]+(.+?)(?:\.|$)",
                    r"descubr[íi] que (.+?)(?:\.|$)"):
            m = re.search(pat, thought, re.IGNORECASE)
            if m:
                return m.group(1).strip()
        return None

    @staticmethod
    def _remember(fact: str, task: str) -> None:
        try:
            try:
                from core.brain_memory import BrainMemory
            except ImportError:
                import sys as _sys
                _root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
                if _root not in _sys.path:
                    _sys.path.insert(0, _root)
                from core.brain_memory import BrainMemory
            BrainMemory().remember(
                f"[UI-TARS aprendizaje] {fact} (tarea: {task[:60]})",
                tags=["uitars", "autolearn"], importance=0.7,
                category="learned")
        except Exception as e:  # noqa: BLE001
            log.debug("no se pudo guardar aprendizaje: %s", e)

    # ── ejecución de acción ─────────────────────────────────────────────────

    def _exec_gui(self, a: ParsedAction) -> str:
        t, ai = a.action_type, a.action_inputs
        if self.dry_run:
            return f"[dry_run] {t} {ai}"
        c = self._ctrl

        def _xy() -> tuple[int, int]:
            # ref (backend OCR) → coords del elemento detectado; o start_coords
            ref = ai.get("ref", "").strip().strip("'\"")
            if ref and ref in self._elem_map:
                e = self._elem_map[ref]
                return e.cx, e.cy
            if ref:
                raise KeyError(f"ref '{ref}' no está en pantalla")
            return ai["start_coords"]

        try:
            if t == "click":
                x, y = _xy(); return c.click(x, y, verify=False)
            if t == "left_double":
                x, y = _xy(); return c.click(x, y, clicks=2, verify=False)
            if t == "right_single":
                x, y = _xy(); return c.click(x, y, button="right", verify=False)
            if t == "drag":
                x1, y1 = ai["start_coords"]; x2, y2 = ai["end_coords"]
                return c.drag(x1, y1, x2, y2) if hasattr(c, "drag") else "[no drag]"
            if t == "type":
                return c.type_text(ai.get("content", ""))
            if t == "hotkey":
                ks = [k for k in re.split(r"[+\s]+", ai.get("key", "").strip()) if k]
                return c.hotkey(*ks)
            if t == "scroll":
                sw, sh = self._screen_size()
                x, y = ai.get("start_coords", (sw // 2, sh // 2))
                amt = -3 if ai.get("direction", "down") in ("down", "right") else 3
                return c.scroll(x, y, amount=amt)
            if t == "wait":
                time.sleep(5); return "[wait 5s]"
            return f"[acción gui desconocida: {t}]"
        except KeyError as e:
            return f"[error: falta {e} para {t}]"
        except Exception as e:  # noqa: BLE001
            return f"[error ejecutando {t}: {e}]"

    def _exec_text(self, a: ParsedAction) -> str:
        t, ai = a.action_type, a.action_inputs
        try:
            if t == "shell":
                cmd = ai.get("cmd", "")
                if not cmd:
                    return "[shell sin cmd]"
                r = subprocess.run(cmd, shell=True, capture_output=True,
                                   text=True, timeout=30)
                out = (r.stdout or "") + (r.stderr or "")
                return out.strip()[:1500] or "[sin salida]"
            if t == "search_brain":
                q = ai.get("query", "")
                try:
                    from core.brain_memory import BrainMemory
                    res = BrainMemory().recall(q) if hasattr(BrainMemory(), "recall") else None
                    return str(res)[:1200] if res else "[brain sin resultados]"
                except Exception:  # noqa: BLE001
                    return "[brain no disponible]"
            if t == "note":
                content = ai.get("content", "")
                self._remember(content, a.thought)
                return f"[anotado en brain: {content[:80]}]"
            if t == "wait":
                time.sleep(3); return "[wait 3s]"
            return f"[acción text desconocida: {t}]"
        except subprocess.TimeoutExpired:
            return "[shell timeout 30s]"
        except Exception as e:  # noqa: BLE001
            return f"[error {t}: {e}]"

    # ── loop principal (port del while de GUIAgent.ts) ──────────────────────

    def run(self, task: str, max_steps: int = MAX_LOOP_COUNT) -> RunResult:
        t0 = time.time()
        res = RunResult(task=task, mode=self.mode, status=Status.RUNNING)
        ocr_gui = (self.mode == "gui" and self.vision_backend == "ocr"
                   and self._uiparser is not None)
        if self.mode != "gui":
            sys_prompt = UITARS_TEXT_PROMPT
        elif ocr_gui:
            sys_prompt = UITARS_GUI_OCR_PROMPT
        else:
            sys_prompt = UITARS_GUI_PROMPT
        if self.mode == "gui" and not ocr_gui and self._gui_variant:
            try:
                from core.uitars_prompts import get_prompt
                sys_prompt = get_prompt(self._gui_variant)
            except Exception as e:  # noqa: BLE001
                log.warning("variante '%s' no usable (%s) — prompt por defecto",
                            self._gui_variant, e)
        sw, sh = self._screen_size() if self.mode == "gui" else (1920, 1080)
        snap_err = 0

        for n in range(1, max_steps + 1):
            if self._abort:
                res.status = Status.USER_STOPPED
                break
            if n > max_steps:
                res.status, res.error = Status.MAX_LOOP, ErrCode.REACH_MAXLOOP
                break

            ts = time.time()
            img_b64 = None
            elems_text = ""
            if self.mode == "gui" and ocr_gui:
                # OCR+OpenCV → lista de elementos (SIN GPU, SIN VLM)
                try:
                    els = self._uiparser.parse_screen()
                    self._elem_map = {e.ref: e for e in els if e.ref}
                    elems_text = self._uiparser.to_text(els)
                except Exception as e:  # noqa: BLE001
                    snap_err += 1
                    if snap_err >= MAX_SNAPSHOT_ERR:
                        res.status, res.error = Status.ERROR, ErrCode.SCREENSHOT_RETRY
                        break
                    continue
            elif self.mode == "gui":
                shot = self._screenshot()
                if not shot:
                    snap_err += 1
                    if snap_err >= MAX_SNAPSHOT_ERR:
                        res.status, res.error = Status.ERROR, ErrCode.SCREENSHOT_RETRY
                        break
                    continue
                try:
                    img_b64 = self._encode(shot)
                except Exception:  # noqa: BLE001
                    snap_err += 1
                    continue

            hist = ""
            for s in res.steps[-5:]:
                hist += f"\nThought: {s.thought}\nAction: {s.action_type}(...)\nResult: {s.observation[:150]}"
            screen_block = (f"\n\n## On-screen elements\n{elems_text}\n"
                            if ocr_gui else "")
            prompt = (f"{sys_prompt}{task}{screen_block}"
                      f"\n\n## Action History{hist or ' (none)'}\n")

            try:
                pred = self._call_model(prompt, img_b64)
            except Exception as e:  # noqa: BLE001
                res.status, res.error = Status.ERROR, ErrCode.INVOKE_RETRY
                res.answer = f"modelo no respondió: {e}"
                break

            actions = parse_prediction(pred, sw, sh)
            if not actions:
                # Modelos locales débiles (hermes3) a veces dan un resumen
                # final en prosa SIN "Action: finished(...)". Si ya hubo al
                # menos un paso con observación real, lo tratamos como
                # finish implícito (robustez con modelos flojos, sin GPU).
                prior_ok = any(s.action_type not in ("noop",) and
                               not s.observation.startswith("[")
                               for s in res.steps)
                prose = re.sub(r"^\s*Thought:\s*", "", pred).strip()
                if prior_ok and len(prose) > 15:
                    res.answer = prose[:1200]
                    res.status = Status.END
                    res.steps.append(Step(n, prose[:140], "finished",
                                          {"content": prose[:200]},
                                          "[fin implícito: resumen en prosa]",
                                          round(time.time() - ts, 1)))
                    break
                res.steps.append(Step(n, pred[:140], "noop", {},
                                      "[sin acción parseable]",
                                      round(time.time() - ts, 1)))
                continue

            a = actions[0]
            # aprende-haciendo
            fact = self._extract_learning(a.thought)
            if fact:
                self._remember(fact, task)
                res.learned.append(fact)

            if a.action_type == "finished":
                res.answer = a.action_inputs.get("content", a.thought)
                res.status = Status.END
                res.steps.append(Step(n, a.thought, "finished",
                                      a.action_inputs, "[fin]",
                                      round(time.time() - ts, 1)))
                break
            if a.action_type == "call_user":
                q = a.thought or a.action_inputs.get("content", "Necesito tu ayuda")
                obs = self.on_call_user(q)
                res.steps.append(Step(n, a.thought, "call_user",
                                      a.action_inputs, obs,
                                      round(time.time() - ts, 1)))
                res.status = Status.CALL_USER
                break

            obs = self._exec_gui(a) if self.mode == "gui" else self._exec_text(a)
            res.steps.append(Step(n, a.thought, a.action_type,
                                  a.action_inputs, obs,
                                  round(time.time() - ts, 1)))
            time.sleep(0.4 if self.mode == "gui" else 0.1)
        else:
            res.status, res.error = Status.MAX_LOOP, ErrCode.REACH_MAXLOOP

        res.total_s = round(time.time() - t0, 1)
        log.info("UITarsScaffold[%s] '%s' → %s (%d pasos, %.1fs)",
                 self.mode, task[:40], res.status.value, len(res.steps),
                 res.total_s)
        return res


# ═══════════════════════════════════════════════════════════════════════════
#  SELF-TEST  (valida prompt + parser + estados; NO mueve ratón)
# ═══════════════════════════════════════════════════════════════════════════

def _self_test() -> bool:
    ok = True
    cases = [
        ("Thought: Abro el menú.\nAction: click(start_box='[500,300,520,320]')",
         "click", [979, 335]),
        ("Thought: Escribo.\nAction: type(content='hola mundo\\n')", "type", None),
        ("Thought: Atajo.\nAction: hotkey(key='ctrl c')", "hotkey", None),
        ("Thought: Listo.\nAction: finished(content='hecho')", "finished", None),
        ("Thought: Arrastro.\nAction: drag(start_box='[100,100]', end_box='[800,600]')",
         "drag", [192, 108]),
        ("Thought: Reviso RAM.\nAction: shell(cmd='free -m')", "shell", None),
        ("Thought: No sé esto, te pregunto.\nAction: call_user()", "call_user", None),
    ]
    for text, exp, coord in cases:
        acts = parse_prediction(text, 1920, 1080)
        if not acts:
            print(f"  ❌ sin parse: {text!r}"); ok = False; continue
        a = acts[0]
        tag = "✅" if a.action_type == exp else "❌"
        if a.action_type != exp:
            ok = False
        extra = ""
        if coord is not None:
            got = a.action_inputs.get("start_coords")
            extra = f" coords={'✅' if got == coord else f'❌{got}'}"
            if got != coord:
                ok = False
        print(f"  {tag} {a.action_type:12s} (esperado {exp}){extra}")
    # estados/aprendizaje
    learn = UITarsScaffold._extract_learning("Thought: I learned that scroll needs focus first.")
    lt = "✅" if learn == "scroll needs focus first" else f"❌({learn})"
    print(f"  {lt} extract_learning")
    if learn != "scroll needs focus first":
        ok = False
    print(f"  ✅ estados: {[s.value for s in Status]}")
    print("SELF-TEST:", "PASS ✅" if ok else "FAIL ❌")
    return ok


if __name__ == "__main__":
    import sys
    if len(sys.argv) > 1 and sys.argv[1] == "--test":
        raise SystemExit(0 if _self_test() else 1)
    if len(sys.argv) > 1 and sys.argv[1] == "--run-text":
        task = " ".join(sys.argv[2:]) or "di la fecha del sistema y termina"
        print(UITarsScaffold(mode="text").run(task, max_steps=8).summary())
    elif len(sys.argv) > 1 and sys.argv[1] == "--run":
        task = " ".join(a for a in sys.argv[2:] if a != "--dry") or "describe la pantalla y termina"
        print(UITarsScaffold(mode="gui", dry_run="--dry" in sys.argv)
              .run(task, max_steps=6).summary())
    else:
        print("Uso:")
        print("  python3 core/uitars_scaffold.py --test")
        print("  python3 core/uitars_scaffold.py --run-text \"<tarea>\"   # SIN GPU")
        print("  python3 core/uitars_scaffold.py --run \"<tarea gui>\" [--dry]")
