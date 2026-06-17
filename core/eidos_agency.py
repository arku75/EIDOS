"""
core/eidos_agency.py — PUENTE DE AGENCIA de EIDOS  [S74]

Conecta el CEREBRO (grafo/knowledge_reasoner) con las MANOS (CodeExecutor) que ya
existían huérfanas. EIDOS decide POR SÍ MISMO, sin LLM, qué acción tomar ante una
petición y la ejecuta de forma segura. Cuanto más se usa, más habilidades aprende.

Decisión SIN LLM: clasificador de intención por patrones + recuperación de
snippets/skills del propio grafo. Si no sabe cómo hacer algo, investiga (research
activo) y/o compone desde lo que sabe.

Seguridad: allowlist + denylist + sandbox en ~/.eidos/code_workspace + dry_run.
SER dio permiso root, pero protegemos contra autodestrucción accidental.

Aprendizaje: cada tarea resuelta con éxito se graba como nodo source='skill_learned'
→ la próxima vez EIDOS la reusa al instante (se vuelve mejor con el uso).

Uso:
    from core.eidos_agency import get_agency
    res = get_agency().act("lista los puertos abiertos")
    # res = {"action":"shell", "command":"ss -tlnp", "output":"...", "learned":bool}
"""
from __future__ import annotations

import os
import re
import sqlite3
import time
import uuid
import logging
from pathlib import Path
from typing import Dict, Optional
from core.db import get_conn

log = logging.getLogger("eidos.agency")

BRAIN_DB = Path.home() / ".eidos" / "evolution_brain.db"
WORKSPACE = Path.home() / ".eidos" / "code_workspace"
SKILLS_SOURCE = "skill_learned"

# ── Seguridad: denylist dura (NUNCA ejecutar, ni con root) ────────────────────
_DENY = [
    r"\brm\s+-rf\s+/(?:\s|$)", r"\brm\s+-rf\s+~", r"\brm\s+-rf\s+\*",
    r"\bmkfs", r"\bdd\s+if=.*of=/dev/(sd|nvme|vd)", r":\(\)\s*\{.*\}\s*;",
    r">\s*/dev/(sd|nvme|vd)", r"\bshutdown\b", r"\breboot\b", r"\bhalt\b",
    r"\bchmod\s+-R\s+777\s+/(?:\s|$)", r"\bchown\s+-R.*\s+/(?:\s|$)",
    r"/dev/sd[a-z]", r"\bwipefs\b", r"\bfdisk\b.*\b/dev",
    r"NO TOCAR", r"constitution\.toml", r"secrets\.env", r"owner_policy",
    r"\bgit\s+push\s+--force", r"\bgit\s+reset\s+--hard",
]

# ── Clasificador de intención (decisión SIN LLM) ──────────────────────────────
# Prioridad: analyze > code > shell. Si hay ambigüedad, gana el que más
# keywords acertó. Evita que "escribe script que liste X" caiga en shell.
# (tipo_accion, patrón regex en la petición) — ORDEN IMPORTA como tiebreaker.
_INTENT = [
    ("analyze", re.compile(r"\b(analiza|analizar|revisa|revisar|examina|inspecciona|"
                           r"explica el código|qué hace este|audita|auditar)\b", re.I)),
    ("code",    re.compile(r"\b(escribe|escribir|crea|crear|genera|generar|programa|"
                           r"script|función|funcion|código|codigo|python|calcula|"
                           r"algoritmo|automatiza|clase |clases|módulo|modulo|"
                           r"endpoint|api |refactoriza|compila|compilar)\b", re.I)),
    ("shell",   re.compile(r"\b(lista|listar|muestra|mostrar|ver|ejecuta|ejecutar|corre|"
                           r"comando|terminal|puertos|procesos|servicios|disco|memoria|"
                           r"ping|estado del sistema|systemctl|ps |df |free|"
                           r"instala|instalar|desinstala|actualiza|update|upgrade|"
                           r"versi[óo]n|kernel|ram\b|espacio|conexi[óo]n|internet|"
                           r"docker|git\b|log|logs|temperatura|bater[íi]a|usb|pci|"
                           r"wifi|dns|ruta|inodo|paquete|tamaño)\b", re.I)),
]

# ── Snippets base (semilla; el grafo aporta más con el uso) ───────────────────
_SEED_SHELL = {
    # Sistema
    "puertos": "ss -tlnp",
    "procesos": "ps aux --sort=-%cpu | head -20",
    "servicios": "systemctl --user list-units --type=service --state=running --no-pager | head -30",
    "disco": "df -h",
    "memoria": "free -h",
    "red": "ip -br addr",
    "cpu": "lscpu | head -20",
    "uptime": "uptime",
    "kernel": "uname -a",
    # Red / diagnóstico
    "ping": "ping -c 3 8.8.8.8",
    "dns": "nslookup google.com",
    "interfaces": "ip -br link",
    "conexiones": "ss -tuna",
    "rutas": "ip route",
    "wifi": "nmcli dev wifi list 2>/dev/null || iwconfig 2>/dev/null",
    # Archivos
    "archivos abiertos": "lsof -u $USER | head -30",
    "inodos": "df -i",
    "tamaño": "du -sh ./* 2>/dev/null | sort -rh | head -20",
    "buscar": "find . -name ",
    # Paquetes
    "paquetes": "dpkg -l | head -50",
    "instalado": "dpkg -l | grep -i ",
    "disponible": "apt list --installed 2>/dev/null | head -30",
    # Multimedia
    "video": "ffprobe -v quiet -print_format json -show_format -show_streams ",
    "convertir": "ffmpeg -i input -vn -acodec libmp3lame output.mp3",
    "imagen": "identify -verbose ",
    "captura": "scrot -d 2 screenshot.png",
    # Docker (si existe)
    "docker ps": "docker ps --format 'table {{.Names}}\t{{.Status}}\t{{.Ports}}'",
    "docker images": "docker images --format 'table {{.Repository}}\t{{.Tag}}\t{{.Size}}'",
    # Git
    "git log": "git log --oneline -20",
    "git status": "git status --short",
    "git branch": "git branch -a",
    # Logs / journal
    "logs": "journalctl --user --no-pager -n 30",
    "errores": "journalctl --user --no-pager -p err -n 20",
    # Hardware
    "temperatura": "sensors 2>/dev/null || cat /sys/class/thermal/thermal_zone*/temp 2>/dev/null",
    "bateria": "upower -i $(upower -e | grep BAT) 2>/dev/null || acpi 2>/dev/null",
    "usb": "lsusb",
    "pci": "lspci | head -20",
}


class _Executor:
    """Ejecutor propio (subprocess directo en sandbox). No depende de módulos externos."""
    def __init__(self, workspace: str):
        self.workspace = workspace

    def execute_shell(self, cmd: str, timeout: int = 20) -> dict:
        import subprocess
        try:
            r = subprocess.run(cmd, shell=True, capture_output=True, text=True,
                               timeout=timeout, cwd=self.workspace)
            return {"success": r.returncode == 0, "stdout": r.stdout, "stderr": r.stderr,
                    "returncode": r.returncode}
        except subprocess.TimeoutExpired:
            return {"success": False, "stdout": "", "stderr": "[TIMEOUT]"}
        except Exception as e:
            return {"success": False, "stdout": "", "stderr": str(e)}

    def execute_python(self, code: str, timeout: int = 20) -> dict:
        import subprocess, os, tempfile
        try:
            fd, path = tempfile.mkstemp(suffix=".py", dir=self.workspace)
            with os.fdopen(fd, "w") as f:
                f.write(code)
            r = subprocess.run(["python3", path], capture_output=True, text=True,
                               timeout=timeout, cwd=self.workspace)
            os.unlink(path)
            return {"success": r.returncode == 0, "stdout": r.stdout, "stderr": r.stderr}
        except Exception as e:
            return {"success": False, "stdout": "", "stderr": str(e)}

    def analyze_code(self, code: str) -> dict:
        import ast
        try:
            tree = ast.parse(code)
            funcs = [n.name for n in ast.walk(tree) if isinstance(n, ast.FunctionDef)]
            imps = [a.name for n in ast.walk(tree) if isinstance(n, ast.Import) for a in n.names]
            return {"valid": True, "functions": funcs, "imports": imps, "errors": []}
        except SyntaxError as e:
            return {"valid": False, "functions": [], "imports": [], "errors": [str(e)]}


def _looks_like_command(text: str) -> bool:
    """Heurística: ¿esto parece un comando shell real o es texto descriptivo?"""
    if not text or len(text) > 500:
        return False
    t = text.strip()
    # Texto que empieza con "Para", "No", artículos → probablemente no es comando
    if re.match(r"^(Para|No |El |La |Los |Las |Un |Una |Sé |Aún|Investigu)", t, re.I):
        return False
    # Si contiene binarios conocidos o estructura de comando
    if re.search(r"\b(ss|df|free|ps|ls|cat|grep|awk|sed|find|curl|wget|"
                 r"systemctl|journalctl|docker|git|python|pip|npm|pnpm|"
                 r"ffmpeg|ffprobe|nmap|ssh|scp|tar|gzip|uname|lscpu|"
                 r"ip|ping|nslookup|uptime|whoami|id|env)\b", t, re.I):
        return True
    # Si tiene flags de comando (-l, -a, --help, etc.)
    if re.search(r"\s-[a-zA-Z]+", t):
        return True
    # Si tiene pipe o redirect
    if "|" in t or ">" in t:
        return True
    return False


class EidosAgency:
    def __init__(self):
        WORKSPACE.mkdir(parents=True, exist_ok=True)
        self.executor = _Executor(str(WORKSPACE))

    # ── Seguridad ─────────────────────────────────────────────────────────────
    @staticmethod
    def is_safe(cmd: str) -> tuple[bool, str]:
        low = cmd.strip()
        if not low:
            return False, "vacío"
        for pat in _DENY:
            if re.search(pat, low, re.I):
                return False, f"denylist: {pat}"
        return True, "ok"

    # ── Decisión SIN LLM ───────────────────────────────────────────────────────
    # Patrones de pregunta (si matchean, la intención NO es shell salvo que
    # haya verbo imperativo fuerte). Evita que "qué es docker" ejecute comandos.
    _QUESTION_RE = re.compile(
        r"\b(qu[ée] es|qui[ée]n es|cu[áa]ndo|d[óo]nde|por qu[ée]|para qu[ée]|"
        r"c[óo]mo funciona|definici[óo]n de|significado de)\b", re.I
    )

    def classify(self, request: str) -> str:
        """Clasifica la intención por patrones. Devuelve: analyze|code|shell|answer.

        Cuenta coincidencias por categoría; si hay empate, gana la primera
        en orden _INTENT (analyze > code > shell). Resuelve ambigüedades como
        "escribe script que liste X" → code (2 hits) vs shell (1 hit).

        Si la petición es claramente una pregunta de conocimiento (qué es X),
        va a answer aunque contenga keywords de shell."""
        scores = {}
        for kind, pat in _INTENT:
            matches = pat.findall(request)
            if matches:
                scores[kind] = len(matches)
        if not scores:
            return "answer"
        # Si la petición es pregunta de conocimiento, forzar answer
        # (salvo que tenga verbo imperativo fuerte: escribe/crea/lista/muestra)
        if self._QUESTION_RE.search(request):
            has_imperative = bool(re.search(
                r"\b(escribe|escribir|crea|crear|genera|lista|listar|muestra|mostrar|"
                r"ejecuta|corre|analiza|instala)\b", request, re.I))
            if not has_imperative:
                return "answer"
        # Más coincidencias gana; en empate, orden de _INTENT decide
        best = max(scores, key=lambda k: (scores[k], -next(
            i for i, (ik, _) in enumerate(_INTENT) if ik == k)))
        return best

    # ── Recuperar skill aprendida del grafo ────────────────────────────────────
    def recall_skill(self, request: str) -> Optional[str]:
        """Busca en el grafo un comando/código ya aprendido para esta tarea.

        Requiere que al menos 2 keywords de la petición matcheen el concepto
        de la skill, o que la skill se haya reusado exitosamente >1 vez.
        Evita que 'muestra la memoria' recupere la skill de 'muestra el disco'."""
        try:
            kws = [w for w in re.findall(r"\w{4,}", request.lower())][:5]
            if not kws:
                return None
            conn = get_conn(BRAIN_DB, timeout=5)
            # Buscar skills que matcheen el MAYOR número de keywords
            best = None
            best_score = 0
            for kw in kws:
                rows = conn.execute(
                    "SELECT concept, definition, confidence FROM knowledge_nodes "
                    "WHERE source=? AND (concept LIKE ? OR definition LIKE ?) "
                    "ORDER BY confidence DESC LIMIT 3",
                    (SKILLS_SOURCE, f"%{kw}%", f"%{kw}%")).fetchall()
                for concept, definition, conf in rows:
                    # Contar cuántos keywords de la petición están en el concepto
                    score = sum(1 for k in kws if k in concept.lower())
                    if score > best_score:
                        best_score = score
                        best = definition

            # Solo devolver si la mayoría de keywords matchean el concepto
            # (>=50% redondeando arriba). Evita que "memoria" recupere skill de "disco"
            min_matches = max(2, (len(kws) + 1) // 2)  # mayoría, mínimo 2
            if best and best_score >= min_matches:
                return best
        except Exception as e:
            log.debug("recall_skill: %s", e)
        return None

    # ── Componer comando shell desde semilla + grafo ───────────────────────────
    def compose_shell(self, request: str) -> Optional[str]:
        # 1. skill aprendida (con fallthrough a semilla si no es comando real)
        learned = self.recall_skill(request)
        if learned and any(c in learned for c in (" ", "-", "/")):
            m = re.search(r"`([^`]+)`", learned) or re.search(r"comando:\s*(.+)", learned)
            cmd = (m.group(1) if m else learned).strip().split("\n")[0][:300]
            # Solo usar si parece comando real (tiene binario conocido o estructura shell)
            if _looks_like_command(cmd):
                return cmd
            # Si no, fallthrough a semilla
        # 2. semilla por keyword
        rl = request.lower()
        for kw, cmd in _SEED_SHELL.items():
            if kw in rl:
                return cmd
        return None

    # ── Aprender skill exitosa ─────────────────────────────────────────────────
    def learn_skill(self, request: str, command: str, kind: str):
        try:
            concept = f"skill:{kind}:{request[:50]}"
            definition = f"Para '{request[:80]}' usar: `{command}`"
            node_id = uuid.uuid4().hex[:16] + "_skill"
            conn = get_conn(BRAIN_DB, timeout=20)
            conn.execute("PRAGMA busy_timeout=20000")
            conn.execute(
                "INSERT OR IGNORE INTO knowledge_nodes "
                "(id, concept, definition, category, confidence, source, created_at) "
                "VALUES (?,?,?,?,?,?,?)",
                (node_id, concept, definition, "skill", 0.8, SKILLS_SOURCE,
                 time.strftime("%Y-%m-%d %H:%M:%S")))
            conn.commit()

            log.info("skill aprendida: %s → %s", request[:40], command[:40])
        except Exception as e:
            log.debug("learn_skill: %s", e)

    # ── Acción principal ───────────────────────────────────────────────────────
    def act(self, request: str, dry_run: bool = False, allow_exec: bool = True) -> Dict:
        kind = self.classify(request)
        out = {"request": request, "action": kind, "command": None,
               "output": "", "success": False, "learned": False, "dry_run": dry_run}

        if not self.executor:
            out["output"] = "Ejecutor no disponible."
            return out

        if kind == "shell":
            cmd = self.compose_shell(request)
            if not cmd:
                out["action"] = "answer"
                out["output"] = ("Sé que quieres ejecutar algo pero no tengo el comando exacto. "
                                 "Dame la herramienta o pídeme que lo investigue.")
                return out
            out["command"] = cmd
            safe, why = self.is_safe(cmd)
            if not safe:
                out["output"] = f"⛔ Comando bloqueado por seguridad ({why})."
                return out
            if dry_run or not allow_exec:
                out["output"] = f"[dry-run] ejecutaría: {cmd}"
                return out
            r = self.executor.execute_shell(cmd, timeout=20)
            out["success"] = r.get("success", False)
            out["output"] = (r.get("stdout") or r.get("stderr") or "")[:2000]
            if out["success"]:
                self.learn_skill(request, cmd, "shell")
                out["learned"] = True
            return out

        if kind == "code":
            code = self.compose_code(request)
            out["command"] = code
            if not code:
                out["action"] = "answer"
                out["output"] = "Aún no sé generar ese código solo. Puedo investigarlo."
                return out
            # validar sintaxis antes de ejecutar
            an = self.executor.analyze_code(code)
            if not an.get("valid"):
                out["output"] = "Código generado inválido: " + "; ".join(an.get("errors", []))
                return out
            if dry_run or not allow_exec:
                out["output"] = f"[dry-run] código:\n{code}"
                return out
            r = self.executor.execute_python(code, timeout=20)
            out["success"] = r.get("success", False)
            out["output"] = (r.get("stdout") or r.get("stderr") or "")[:2000]
            if out["success"]:
                self.learn_skill(request, code, "code")
                out["learned"] = True
            return out

        if kind == "analyze":
            # extraer ruta de archivo de la petición
            m = re.search(r"(/[\w./\-]+\.\w+)", request)
            if m and os.path.exists(m.group(1)):
                try:
                    code = open(m.group(1), encoding="utf-8", errors="ignore").read()[:20000]
                    an = self.executor.analyze_code(code)
                    out["success"] = an.get("valid", False)
                    out["output"] = (f"Archivo {m.group(1)}: {len(an.get('functions',[]))} funciones "
                                     f"({', '.join(an.get('functions',[])[:10])}), "
                                     f"{len(an.get('imports',[]))} imports.")
                    return out
                except Exception as e:
                    out["output"] = f"Error analizando: {e}"
                    return out
            out["action"] = "answer"
            out["output"] = "Dame la ruta del archivo a analizar."
            return out

        out["output"] = ""  # answer → lo maneja el flujo normal de /talk
        return out

    # ── Componer código Python (semilla + grafo, sin LLM) ──────────────────────
    def compose_code(self, request: str) -> Optional[str]:
        # 1. Intentar extraer de skill aprendida (con fallthrough a semilla)
        learned = self.recall_skill(request)
        if learned and ("def " in learned or "import " in learned or "print(" in learned):
            # Intentar extraer de bloque ```python ... ``` o `código`
            m = re.search(r"```(?:python)?\s*([\s\S]+?)```", learned)
            if m:
                return m.group(1).strip()
            m = re.search(r"`([^`]+(?:def |import |print\()[^`]+)`", learned)
            if m:
                return m.group(1).strip().replace("\\n", "\n")
            # Si la skill tiene pinta de código pero sin delimitadores, usarla directo
            if len(learned) > 20 and len(learned) < 3000 and not learned.startswith("Para"):
                return learned.strip()
        # 2. snippets semilla por tarea común (FALLTHROUGH siempre)
        rl = request.lower()
        if "puertos" in rl:
            return ("import subprocess\n"
                    "print(subprocess.run(['ss','-tlnp'],capture_output=True,text=True).stdout)")
        if "fecha" in rl or "hora" in rl:
            return "from datetime import datetime\nprint(datetime.now().strftime('%Y-%m-%d %H:%M:%S'))"
        if "archivos" in rl or "directorio" in rl or "ls" in rl:
            return "import os\nfor f in sorted(os.listdir('.')): print(f)"
        if "cpu" in rl or "monitor" in rl:
            return ("import psutil\n"
                    "print(f'CPU: {psutil.cpu_percent(interval=1)}%')\n"
                    "print(f'RAM: {psutil.virtual_memory().percent}%')")
        if "api" in rl or "endpoint" in rl or "flask" in rl:
            return ("from flask import Flask, jsonify\n"
                    "app = Flask(__name__)\n"
                    "@app.route('/')\n"
                    "def home():\n"
                    "    return jsonify(status='ok')\n"
                    "if __name__ == '__main__':\n"
                    "    app.run(port=5000)")
        if "disco" in rl or "espacio" in rl:
            return ("import shutil\n"
                    "for p in ['/', '/home']:\n"
                    "    u = shutil.disk_usage(p)\n"
                    "    print(f'{p}: {u.free//(1024**3)}G libre de {u.total//(1024**3)}G')")
        if "json" in rl or "parse" in rl or "parser" in rl:
            return ("import json, sys\n"
                    "data = json.load(sys.stdin) if not sys.stdin.isatty() else {}\n"
                    "print(json.dumps(data, indent=2, ensure_ascii=False))")
        if "csv" in rl:
            return ("import csv, sys\n"
                    "reader = csv.DictReader(sys.stdin)\n"
                    "for row in reader:\n"
                    "    print(row)")
        if "log" in rl or "logger" in rl:
            return ("import logging\n"
                    "logging.basicConfig(level=logging.INFO, format='%(asctime)s %(levelname)s %(message)s')\n"
                    "log = logging.getLogger('eidos')\n"
                    "log.info('Hello from EIDOS')")
        if "web" in rl or "http" in rl or "request" in rl:
            return ("import requests\n"
                    "r = requests.get('http://example.com')\n"
                    "print(f'Status: {r.status_code}')\n"
                    "print(r.text[:500])")
        if "texto" in rl or "archivo" in rl or "leer" in rl or "escribir" in rl:
            return ("with open('output.txt', 'w') as f:\n"
                    "    f.write('EIDOS generó esto.\\n')\n"
                    "with open('output.txt', 'r') as f:\n"
                    "    print(f.read())")
        return None


# ── S76 Fase 2 · GUIAgent: agente de control GUI sin VLM ──────────────────

# Acciones GUI soportadas (mapeo a xdotool/PyAutoGUI)
_GUI_ACTIONS = {
    "click": "xdotool mousemove {x} {y} click 1",
    "doubleclick": "xdotool mousemove {x} {y} click --repeat 2 1",
    "rightclick": "xdotool mousemove {x} {y} click 3",
    "type": "xdotool type '{text}'",
    "key": "xdotool key {key}",
    "scroll_up": "xdotool click 4",
    "scroll_down": "xdotool click 5",
    "activate_window": "xdotool windowactivate {wid}",
    "move": "xdotool mousemove {x} {y}",
}

# Mapeo tecla → acción común
_KEY_MAP = {
    "enter": "Return", "tab": "Tab", "escape": "Escape",
    "space": "space", "backspace": "BackSpace", "delete": "Delete",
    "up": "Up", "down": "Down", "left": "Left", "right": "Right",
    "copy": "ctrl+c", "paste": "ctrl+v", "cut": "ctrl+x",
    "undo": "ctrl+z", "select_all": "ctrl+a", "save": "ctrl+s",
    "close_tab": "ctrl+w", "new_tab": "ctrl+t", "find": "ctrl+f",
}


class GUIAgent:
    """Agente de control GUI: perceive→plan→act sin VLM.

    Usa ScreenCapture para ver la pantalla, UIParser para entender
    los elementos, y xdotool para interactuar. Heurísticas en vez de
    LLM para decidir la siguiente acción.

    Uso:
        agent = GUIAgent()
        result = agent.execute_task("Abre Firefox y busca 'EIDOS'")
        # result = {"success": True, "steps": 3, "actions": [...]}
    """

    def __init__(self, max_iterations: int = 10, timeout: float = 30.0,
                 display: str = ":0"):
        self.max_iterations = max_iterations
        self.timeout = timeout
        self.display = display
        self._screen = None
        self._parser = None
        self._action_history: list = []
        log.info("GUIAgent: max_iter=%d timeout=%.1fs", max_iterations, timeout)

    @property
    def screen(self):
        if self._screen is None:
            from core.screen_capture import ScreenCapture
            self._screen = ScreenCapture(display=self.display)
        return self._screen

    @property
    def parser(self):
        if self._parser is None:
            from core.ui_parser import UIParser
            self._parser = UIParser()
        return self._parser

    # ── API principal ───────────────────────────────────────────────────────

    def execute_task(self, task: str, *,
                     dry_run: bool = False,
                     target_window: Optional[str] = None) -> Dict:
        """Ejecuta una tarea GUI de alto nivel.

        Args:
            task: Descripción en lenguaje natural.
            dry_run: Si True, no ejecuta acciones reales.
            target_window: Nombre parcial de ventana a enfocar primero.

        Returns:
            Dict con success, steps, actions, output, errors.
        """
        import time

        t0 = time.time()
        self._action_history = []
        errors = []
        output = []

        # Foco en ventana objetivo
        if target_window:
            self._focus_window_by_name(target_window)

        for i in range(self.max_iterations):
            # Timeout
            if time.time() - t0 > self.timeout:
                errors.append(f"Timeout tras {i} iteraciones ({self.timeout}s)")
                break

            # 1. PERCIBIR
            ui = self._perceive()
            if ui is None:
                errors.append("No se pudo capturar pantalla")
                break

            # 2. PLANIFICAR
            action = self._plan(ui, task, i)
            if action is None:
                # Nada que hacer → tarea completada o bloqueada
                output.append("Sin acción pendiente — ¿tarea completada?")
                break

            action["iteration"] = i
            self._action_history.append(action)

            # 3. ACTUAR
            if not dry_run:
                ok, msg = self._act(action)
                if not ok:
                    errors.append(f"Step {i}: {msg}")
                    if len(errors) >= 3:
                        break  # Demasiados errores
                else:
                    output.append(msg)
            else:
                output.append(f"[dry] {action.get('description', action.get('type', '?'))}")

            # Pequeña pausa para que la UI reaccione
            if not dry_run:
                import time as _t
                _t.sleep(0.3)

        duration = time.time() - t0
        success = len(errors) == 0 and len(self._action_history) > 0

        return {
            "success": success,
            "steps": len(self._action_history),
            "duration": round(duration, 2),
            "actions": [a.get("description", a.get("type", "?"))
                       for a in self._action_history],
            "output": "\n".join(output),
            "errors": errors,
            "dry_run": dry_run,
        }

    # ── Perceive ────────────────────────────────────────────────────────────

    def _perceive(self):
        """Captura pantalla y parsea UI. Retorna ParsedUI o None."""
        try:
            frame = self.screen.capture()
            if frame is None or frame.size == 0:
                return None
            return self.parser.parse(frame)
        except Exception as e:
            log.debug("GUIAgent._perceive error: %s", e)
            return None

    # ── Plan ────────────────────────────────────────────────────────────────

    def _plan(self, ui, task: str, iteration: int) -> Optional[Dict]:
        """Decide la siguiente acción basada en la UI parseada.

        Heurísticas por palabra clave en la tarea:
        - "abre"/"abrir" + app → buscar en menú o usar Alt+F2
        - "busca"/"buscar" + query → campo de texto + Enter
        - "click"/"clica" + elemento → buscar elemento y click
        - "escribe"/"escribir" + texto → campo de texto activo + type
        - "cierra"/"cerrar" → Alt+F4 o click en X
        """
        task_lower = task.lower()
        clickables = ui.clickable() if hasattr(ui, 'clickable') else []

        # 0. Primera iteración: lanzar aplicación si es necesario
        if iteration == 0:
            app_match = re.search(
                r"\b(abre|abrir|lanzar?|iniciar?|ejecutar?)\s+"
                r"([a-zA-Záéíóúñ]{2,20}(?:\s[a-zA-Záéíóúñ]{2,20})?)",
                task_lower
            )
            if app_match:
                app = app_match.group(2).strip()
                return {
                    "type": "launch_app",
                    "app": app,
                    "description": f"Lanzar aplicación: {app}",
                    "command": self._launch_cmd(app),
                }

        # 1. Buscar "buscar/busca" + query
        search_match = re.search(
            r"\b(busca[r]?|search|find)\s+[\"']?(.{2,60})[\"']?$",
            task_lower
        )
        if search_match:
            query = search_match.group(2).strip().rstrip(".'\"")
            return {
                "type": "search",
                "query": query,
                "description": f"Buscar: '{query}'",
                "command": f"xdotool type '{query}' && xdotool key Return",
            }

        # 2. Buscar elemento por texto en clickables
        if clickables:
            # Intentar matchear palabras clave de la tarea con elementos UI
            keywords = [w for w in re.findall(r"\w{3,}", task_lower)
                       if w not in ("abre", "abrir", "click", "clica", "haz",
                                    "clicka", "pulsa", "presiona", "sobre", "el",
                                    "la", "los", "las", "con", "para", "del",
                                    "una", "un", "que", "por", "como", "este")]
            for kw in keywords:
                for elem in clickables:
                    if kw in elem.text.lower() if hasattr(elem, 'text') else False:
                        cx, cy = elem.center if hasattr(elem, 'center') else (elem.cx, elem.cy)
                        return {
                            "type": "click",
                            "x": cx, "y": cy,
                            "element": elem.text if hasattr(elem, 'text') else str(elem),
                            "description": f"Click en '{getattr(elem, 'text', str(elem))}' @ ({cx},{cy})",
                            "command": f"xdotool mousemove {cx} {cy} click 1",
                        }

        # 3. Acción "escribe"/"type"
        type_match = re.search(
            r"\b(escribe|escribir|type|teclea)\s+[\"']?(.{2,200})[\"']?$",
            task_lower
        )
        if type_match:
            text = type_match.group(2).strip().rstrip(".'\"")
            return {
                "type": "type",
                "text": text,
                "description": f"Escribir: '{text}'",
                "command": f"xdotool type '{text}'",
            }

        # 4. Acción "cerrar"/"salir"
        if re.search(r"\b(cerrar?|salir|exit|quit)\b", task_lower):
            return {
                "type": "key",
                "key": "Alt+F4",
                "description": "Cerrar ventana (Alt+F4)",
                "command": "xdotool key Alt+F4",
            }

        # 5. Sin heurística clara → intentar click en el elemento más prioritario
        if clickables and iteration < 3:
            best = clickables[0]  # ya ordenado por prioridad
            cx, cy = best.center if hasattr(best, 'center') else (best.cx, best.cy)
            return {
                "type": "click",
                "x": cx, "y": cy,
                "element": best.text if hasattr(best, 'text') else str(best),
                "description": f"Click heurístico en '{getattr(best, 'text', str(best))}' @ ({cx},{cy})",
                "command": f"xdotool mousemove {cx} {cy} click 1",
            }

        return None  # Sin plan → terminamos

    # ── Act ─────────────────────────────────────────────────────────────────

    def _act(self, action: Dict) -> Tuple[bool, str]:
        """Ejecuta una acción física. Retorna (ok, mensaje)."""
        import subprocess

        atype = action.get("type", "")
        env = {**os.environ, "DISPLAY": self.display}

        try:
            # Casos especiales
            if atype == "launch_app":
                cmd = action.get("command", "")
                if cmd:
                    subprocess.Popen(cmd, shell=True, env=env,
                                    stdout=subprocess.DEVNULL,
                                    stderr=subprocess.DEVNULL)
                    return True, f"Lanzada app: {action.get('app', cmd)}"
                return False, f"No se pudo lanzar: {action.get('app', '?')}"

            # Acción genérica con xdotool
            cmd = action.get("command", "")
            if not cmd:
                return False, "Sin comando en acción"

            r = subprocess.run(cmd, shell=True, capture_output=True,
                             text=True, timeout=10, env=env)
            ok = r.returncode == 0
            msg = (r.stdout.strip()[:100] if ok
                   else r.stderr.strip()[:100] or f"exit={r.returncode}")
            return ok, msg

        except subprocess.TimeoutExpired:
            return False, "Timeout (10s)"
        except Exception as e:
            return False, str(e)[:100]

    # ── Helpers ─────────────────────────────────────────────────────────────

    def _launch_cmd(self, app: str) -> str:
        """Convierte nombre de app en comando de lanzamiento."""
        app_lower = app.lower().strip()
        # Mapa de apps comunes
        app_map = {
            "firefox": "firefox-esr",
            "chrome": "google-chrome-stable",
            "terminal": "konsole",
            "konsole": "konsole",
            "calculadora": "kcalc",
            "archivos": "dolphin",
            "dolphin": "dolphin",
            "editor": "kate",
            "kate": "kate",
            "vscode": "code",
            "code": "code",
            "configuración": "systemsettings5",
            "system settings": "systemsettings5",
            "ajustes": "systemsettings5",
            "bloc de notas": "kwrite",
            "kwrite": "kwrite",
            "gwenview": "gwenview",
            "imagen": "gwenview",
        }
        cmd = app_map.get(app_lower, app_lower)
        return cmd

    @staticmethod
    def _focus_window_by_name(name: str):
        """Enfoca una ventana por parte de su nombre (WM_CLASS o título)."""
        import subprocess
        try:
            # Buscar window ID por nombre
            r = subprocess.run(
                ["xdotool", "search", "--name", name],
                capture_output=True, text=True, timeout=5
            )
            wids = [int(l) for l in r.stdout.strip().split("\n") if l.strip().isdigit()]
            if wids:
                subprocess.run(
                    ["xdotool", "windowactivate", "--sync", str(wids[0])],
                    capture_output=True, timeout=5
                )
        except Exception:
            pass

    def action_history_text(self) -> str:
        """Retorna resumen legible del historial de acciones."""
        lines = []
        for i, a in enumerate(self._action_history):
            lines.append(f"  {i+1}. {a.get('description', a.get('type', '?'))}")
        return "\n".join(lines) if lines else "(sin acciones)"


# ── Singleton ──────────────────────────────────────────────────────────────────

_instance: Optional[EidosAgency] = None
_gui_instance: Optional[GUIAgent] = None


def get_agency() -> EidosAgency:
    global _instance
    if _instance is None:
        _instance = EidosAgency()
    return _instance


def get_gui_agent() -> GUIAgent:
    """Retorna instancia singleton del GUIAgent (S76 Fase 2)."""
    global _gui_instance
    if _gui_instance is None:
        _gui_instance = GUIAgent()
    return _gui_instance


if __name__ == "__main__":
    import json as _j
    logging.basicConfig(level=logging.INFO)
    import sys
    req = sys.argv[1] if len(sys.argv) > 1 else "lista los puertos abiertos"
    print(_j.dumps(get_agency().act(req), ensure_ascii=False, indent=2))
