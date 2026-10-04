"""
core/bridge_to_eidos.py — Bridge universal EIDOS ↔ cualquier IA

Servidor HTTP ligero (Flask) en puerto interno 18003 que permite a EIDOS
hablar directamente con Colony, consultar el estado de EIDOS, lanzar
estudios y verificar el conocimiento — sin necesitar el CLI interactivo.

Endpoints:
    POST /talk           — enviar mensaje a Colony, recibir respuesta (knowledge_first por defecto)
    GET  /status         — estado de EIDOS (servicios, Ollama, nodos, bridge)
    GET  /brain          — estadísticas del brain.db por categoría
    POST /study          — lanzar study_url o study_topic en background
    GET  /proactive      — mensajes pendientes de EIDOS para SER
    POST /inject         — inyectar mensaje proactivo (actor="SER" por defecto)
    GET  /episodes       — últimos episodios de la memoria episódica
    GET  /health         — ping para verificar que el bridge está vivo
    POST /semantic       — búsqueda semántica vectorial en ChromaDB (Fix 13)
    GET  /chroma_stats   — estadísticas de ChromaDB (vectores indexados)
    POST /ask_brain      — búsqueda LIKE directa en brain.db (concept/definition)
    POST /find_docs      — EIDOS aprende docs de cualquier tema autónomamente
    GET  /self_analyze   — EIDOS analiza su propio cuerpo: módulos, brain, código

Uso desde Claude Code (Bash tool):
    curl -s http://localhost:18003/health
    curl -s -X POST http://localhost:18003/talk -H 'Content-Type: application/json' \
         -d '{"message": "hola EIDOS, qué sabes de n8n?"}'
    curl -s http://localhost:18003/brain
    curl -s -X POST http://localhost:18003/study \
         -d '{"url": "https://github.com/n8n-io/n8n"}'
"""
from __future__ import annotations

import json
import os
import subprocess
import shlex
import sys
import time
import threading
import logging
from pathlib import Path

# S69 · faulthandler — capturar SIGSEGV del bridge con traceback Python
# El bridge crashea con status=11/SEGV cada ~25s (mismo patrón chromadb que vivo S68-A).
# Sin esto journalctl solo dice "status=11/SEGV" sin saber DÓNDE. Volcado a log dedicado.
import faulthandler as _faulthandler
import signal as _signal
_FH_BRIDGE_LOG = Path.home() / ".eidos" / "logs" / "bridge_faulthandler.log"
_FH_BRIDGE_LOG.parent.mkdir(parents=True, exist_ok=True)
_fh_bridge_file = open(_FH_BRIDGE_LOG, "a", buffering=1)
_faulthandler.enable(file=_fh_bridge_file, all_threads=True)
for _sig in ("SIGSEGV", "SIGABRT", "SIGFPE", "SIGBUS"):
    try:
        _faulthandler.register(getattr(_signal, _sig), file=_fh_bridge_file,
                               all_threads=True, chain=True)
    except Exception:
        pass
from typing import Any, Dict, Set
from core.db import get_conn

# Bridge corre en el proceso del servidor — no en el CLI → ChromaDB OK
os.environ.setdefault("EIDOS_BRIDGE_MODE", "1")
sys.path.insert(0, str(Path(__file__).parent.parent))

log = logging.getLogger("eidos.bridge_to_eidos")

try:
    from flask import Flask, request, jsonify
    HAS_FLASK = True
except ImportError:
    HAS_FLASK = False
    log.error("flask no disponible — instala con: pip install flask")

app = Flask("bridge_to_eidos") if HAS_FLASK else None

def _bridge_port() -> int:
    """Internal bridge port; public traffic belongs on the unified gateway."""
    try:
        return int(os.environ.get("EIDOS_BRIDGE_PORT", "18003"))
    except (TypeError, ValueError):
        return 18003

_colony = None
_colony_lock = threading.Lock()

# ── Autenticación del Bridge ─────────────────────────────────────────────────
_BRIDGE_API_KEY = os.environ.get("EIDOS_BRIDGE_KEY", "")
_AUTH_SKIP_PATHS = {"/health", "/dashboard", "/panel"}

def _check_auth():
    """Middleware: verifica X-API-Key o EIDOS_BRIDGE_KEY."""
    if not _BRIDGE_API_KEY:
        return None
    if request.path in _AUTH_SKIP_PATHS:
        return None
    provided = request.headers.get("X-API-Key", "")
    if provided == _BRIDGE_API_KEY:
        return None
    return jsonify({"error": "Unauthorized", "message": "X-API-Key header required or invalid"}), 401

if HAS_FLASK:
    app.before_request(_check_auth)

# ── Metrics middleware ────────────────────────────────────────────────────────
_METRICS_SKIP = {"/health", "/dashboard", "/panel", "/favicon.ico"}

def _log_metrics(response):
    """Registra métricas de cada endpoint después de responder."""
    import sqlite3 as _s3
    try:
        if request.path in _METRICS_SKIP:
            return response
        db = Path.home() / ".eidos" / "metrics.db"
        conn = _s3.connect(str(db), timeout=2)
        conn.execute(
            "INSERT INTO bridge_metrics (timestamp, endpoint, status, elapsed_ms) VALUES (?,?,?,?)",
            (time.time(), request.path,
             response.status_code if hasattr(response, 'status_code') else 200,
             int((time.time() - request._start_time) * 1000) if hasattr(request, '_start_time') else 0)
        )
        conn.commit()

    except Exception:
        pass
    return response

if HAS_FLASK:
    @app.before_request
    def _start_timer():
        request._start_time = time.time()
    app.after_request(_log_metrics)

# ── Sesiones de conversación (memoria de contexto) ───────────────────────────
_talk_sessions: dict[str, list] = {}
_TALK_SESSION_MAX = 10

# ── Comandos peligrosos bloqueados (shell safety) ────────────────────────────
_DANGEROUS_CMD_PATTERNS = [
    "rm -rf /", "rm -rf /*", "mkfs", "dd if=", "> /dev/sda",
    ":(){ :|:& };:", "chmod -R 000 /", "mv / /dev/null",
    "wget -O- http://", "curl *http://",
    "git push --force", ">|", "/dev/sd",
]

def _safe_shell_argv(cmd: str) -> Tuple[list[str] | None, str]:
    """Parse an explicit SER command without invoking a shell interpreter."""
    if not cmd or not cmd.strip():
        return None, "Comando vacío"
    # Reject shell grammar before argv parsing. The bridge intentionally
    # supports commands + arguments, not pipelines/redirections/control flow.
    # Quoted metacharacters are still rejected: callers needing literal shell
    # syntax must use the separately authorized interactive terminal.
    if any(marker in cmd for marker in (";", "|", "&", ">", "<", "$(", "`", "\n", "\r")):
        return None, "Metacaracteres de shell no permitidos en el bridge automático"
    try:
        argv = shlex.split(cmd)
    except ValueError as exc:
        return None, f"Comando inválido: {exc}"
    if not argv:
        return None, "Comando vacío"
    # Shell control syntax has semantics beyond argv and must go through a
    # separately authorized interactive terminal, never the automatic bridge.
    shell_meta = {"|", "||", "&&", ";", ">", ">>", "<", "<<", "&"}
    if any(token in shell_meta for token in argv) or any(
        marker in cmd for marker in ("$(", "`", "\n", "\r")
    ):
        return None, "Metacaracteres de shell no permitidos en el bridge automático"
    cmd_lower = cmd.lower()
    for pattern in _DANGEROUS_CMD_PATTERNS:
        if pattern in cmd_lower:
            return None, f"Comando bloqueado por seguridad: coincide con patrón peligroso '{pattern}'"
    dangerous_commands = {"dd", "mkfs", "fdisk", "format", "mkswap", "wipefs"}
    if argv[0].lower() in dangerous_commands:
        return None, f"Comando '{argv[0]}' bloqueado por seguridad"
    return argv, ""


def _is_safe_shell_cmd(cmd: str) -> Tuple[bool, str]:
    """Compatibility predicate backed by the argv-only bridge policy."""
    argv, reason = _safe_shell_argv(cmd)
    return argv is not None, reason

def _get_session(session_id: str) -> list:
    if session_id not in _talk_sessions:
        _talk_sessions[session_id] = []
    return _talk_sessions[session_id]


def _get_colony():
    """Singleton lazy de ColonyCommunity para el bridge."""
    global _colony
    if _colony is None:
        with _colony_lock:
            if _colony is None:
                try:
                    from core.colony_community import ColonyCommunity
                    _colony = ColonyCommunity()
                    log.info("ColonyCommunity cargada en el bridge")
                except Exception as e:
                    log.error("Error cargando Colony: %s", e)
    return _colony


# ── Endpoints ────────────────────────────────────────────────────────────────

@app.route("/health", methods=["GET"])
def health():
    """Ping para verificar que el bridge está activo."""
    return jsonify({"status": "ok", "service": "bridge_to_eidos", "port": _bridge_port()})


@app.route("/talk", methods=["POST"])
def talk():
    """Enviar un mensaje a Colony y recibir respuesta.
    Colony siempre delibera con Ollama (sin timeout artificial).
    Si Ollama tarda, espera — la calidad importa más que la velocidad.
    """
    try:
        data = request.get_json(force=True, silent=True) or {}
        message = str(data.get("message", "")).strip()
        if not message:
            return jsonify({"error": "message vacío"}), 400

        max_agents     = int(data.get("max_agents", 3))
        skip_knowledge = bool(data.get("skip_knowledge", False))
        session_id     = str(data.get("session_id", "default"))

        # ── Cargar sesión y registrar mensaje ──
        _session = _get_session(session_id)
        _session.append({"role": "user", "text": message, "ts": time.time()})
        if len(_session) > _TALK_SESSION_MAX:
            _session.pop(0)

        # ── [S122-I] GUARDA DE IDENTIDAD (la verdad de EIDOS) ──────────────────
        # Preguntas sobre quién/qué es EIDOS, su modelo o su creador las responde
        # ÉL con su verdad — ANTES de cualquier fast-path (si no, "eres chatgpt"
        # abría el navegador, "quién te creó" buscaba en wikipedia, "qué modelo
        # eres" respondía "soy LLaMA"). EIDOS es EIDOS, único, creado por SER.
        try:
            from core.eidos_learn import _is_self_referential, smart_answer as _sa_id
            _idt = _is_self_referential(message)
            if _idt in ("identity", "model", "creator"):
                _ridt = _sa_id(message)
                _session.append({"role": "assistant",
                                  "text": _ridt.get("answer", "")[:3000], "ts": time.time()})
                return jsonify({"text": _ridt.get("answer", ""),
                                "agents_used": [f"identity:{_idt}"],
                                "from_knowledge": True,
                                "elapsed_s": _ridt.get("elapsed_s", 0)})
        except Exception as _eid:
            log.debug("guarda identidad falló: %s", _eid)

        # ── Detectar seguimiento de acción previa ──
        _followup_kws = {"busca", "buscar", "indaga", "indagar", "investiga", "investigar",
                         "sigue", "continuar", "continúa", "prosigue", "adelante",
                         "bien", "mejor", "más", "mas", "otra vez", "de nuevo",
                         "por qué", "porque", "explica", "explicate"}
        _last_action = None
        if len(_session) >= 2:
            for entry in reversed(_session[:-1]):
                if entry.get("role") == "action":
                    _last_action = entry
                    break
        _is_followup = _last_action and any(kw in message.lower() for kw in _followup_kws)

        colony = _get_colony()
        if colony is None:
            return jsonify({"error": "Colony no disponible"}), 503

        t0 = time.time()

        # ── Fast path: [SHELL: cmd] directo del usuario → ejecutar inmediatamente ──
        import re as _re_pre, subprocess as _sp_pre
        _direct_shell = _re_pre.match(r'^\s*\[SHELL:\s*(.+?)\]\s*$', message, _re_pre.DOTALL)
        if _direct_shell:
            cmd = _direct_shell.group(1).strip()
            argv, reason = _safe_shell_argv(cmd)
            if argv is None:
                return jsonify({"text": f"⛔ {reason}", "agents_used": ["shell"]})
            try:
                r = _sp_pre.run(
                    argv, shell=False, capture_output=True, text=True,
                    timeout=30, cwd=str(Path.home() / "EIDOS")
                )
                out = (r.stdout.strip() or r.stderr.strip() or "(sin salida)")[:1000]
                _resp = f"```\n$ {cmd}\n{out}\n```"
                _session.append({"role": "action", "action": "shell", "cmd": cmd, "result": out, "ts": time.time()})
                return jsonify({
                    "text": _resp,
                    "from_knowledge": True,
                    "agents_used": ["shell"],
                    "elapsed_s": round(time.time() - t0, 3),
                })
            except Exception as _e:
                return jsonify({"text": f"[Shell error: {_e}]", "agents_used": ["shell"]})

        # ── Fast path: TERMINAL VISIBLE (tmux+konsole) — EIDOS ejecuta y SER lo ve ──
        # [S122] SER quiere ver a EIDOS trabajar en una shell. Disparadores:
        #   [TERM: cmd]  ·  "abre una terminal"  ·  "ejecuta <cmd> en la terminal"
        import re as _re_term
        _term_cmd = None
        _term_open_only = False
        _m_term = _re_term.match(r'^\s*\[TERM:\s*(.+?)\]\s*$', message, _re_term.DOTALL)
        if _m_term:
            _term_cmd = _m_term.group(1).strip()
        else:
            _m1 = _re_term.search(
                r'(?:ejecuta|corre|lanza|haz|run)\s+(.+?)\s+en\s+(?:la\s+|una\s+)?(?:terminal|shell|consola)',
                message, _re_term.IGNORECASE)
            _m2 = _re_term.search(
                r'en\s+(?:la\s+|una\s+)?(?:terminal|shell|consola)[:,\s]+(?:ejecuta\s+|corre\s+|haz\s+)?(.+)$',
                message, _re_term.IGNORECASE)
            _m3 = _re_term.search(
                r'abre?\s+(?:una\s+|la\s+)?(?:terminal|shell|consola)\b.*?\b(?:y\s+)?(?:ejecuta|corre|haz)\s+(.+)$',
                message, _re_term.IGNORECASE)
            _m_open = _re_term.search(
                r'abre?\s+(?:una\s+|la\s+)?(?:terminal|shell|consola)\b',
                message, _re_term.IGNORECASE)
            if _m3:   _term_cmd = _m3.group(1).strip()
            elif _m1: _term_cmd = _m1.group(1).strip()
            elif _m2: _term_cmd = _m2.group(1).strip()
            elif _m_open: _term_open_only = True
        if _term_cmd or _term_open_only:
            try:
                from core.eidos_shell_term import run as _term_run, open_terminal as _term_open
                if _term_open_only:
                    _ok = _term_open()
                    _resp = ("Abrí una terminal (ventana «EIDOS — terminal»). "
                             "Dime qué quieres que ejecute.") if _ok else "No pude abrir la terminal."
                    _session.append({"role": "action", "action": "terminal", "cmd": "", "ts": time.time()})
                    return jsonify({"text": _resp, "agents_used": ["terminal"],
                                    "elapsed_s": round(time.time() - t0, 3)})
                safe, reason = _is_safe_shell_cmd(_term_cmd)
                if not safe:
                    return jsonify({"text": f"⛔ {reason}", "agents_used": ["terminal"]})
                _tr = _term_run(_term_cmd, timeout=25)
                _session.append({"role": "action", "action": "terminal", "cmd": _term_cmd,
                                 "result": (_tr.get("output", "") or "")[:500], "ts": time.time()})
                if _tr.get("ok"):
                    _out = (_tr.get("output", "") or "").strip() or "(sin salida)"
                    _resp = (f"Ejecuté en la terminal (la ves en pantalla):\n"
                             f"```\n$ {_term_cmd}\n{_out}\n```")
                else:
                    _resp = f"No pude ejecutar «{_term_cmd}»: {_tr.get('error', 'error')}"
                return jsonify({"text": _resp, "agents_used": ["terminal"],
                                "elapsed_s": round(time.time() - t0, 3)})
            except Exception as _e:
                return jsonify({"text": f"[Terminal error: {_e}]", "agents_used": ["terminal"]})

        # ── Fast path: LEER/COMPRENDER contenido (libro/doc/código/video/audio/etc) ── [S122-I]
        import re as _re_bk, glob as _gl_bk, os as _os_bk
        _bk = _re_bk.search(
            r'(?:lee|leer|aprende de|apr[eé]ndete|estudia|comprende|analiz[áa])'
            r'\s+(?:el\s+|este\s+|un\s+|mi\s+|la\s+|esta\s+|una\s+)?'
            r'(?:libro|pdf|epub|ebook|manual|documento|archivo|fichero|script|'
            r'c[oó]digo|programa|video|v[ií]deo|audio|grabaci[oó]n|sonido|'
            r'm[uú]sica|canc[ií]on|imagen|foto|nota|art[ií]culo|paper|informe|'
            r'clase\s+de)\s+'
            r'(?:de\s+|sobre\s+|llamado\s+)?(.+)$', message, _re_bk.IGNORECASE)
        if _bk:
            _bkq = _bk.group(1).strip().strip('"').strip("'")[:200]
            # [S122-I] ¿Lectura profunda? ("a fondo", "en profundidad", "comprende"...)
            _deep_read = bool(_re_bk.search(
                r'\b(?:a\s+fondo|en\s+profundid|complet[ao]|entero|deep|analiz[áa]|comprende)',
                message, _re_bk.IGNORECASE))
            # [S122-I] Quitar modificadores de profundidad del nombre/ruta a buscar
            # ("db.py a fondo" → "db.py"); si no, no encontraría el archivo.
            _bkq = _re_bk.sub(
                r'\s*\b(?:a\s+fondo|en\s+profundidad|por\s+completo|completo|completa|'
                r'entero|entera|deep|del\s+todo)\b\s*$', '', _bkq, flags=_re_bk.IGNORECASE).strip()
            # 1. ¿Es una RUTA absoluta/relativa existente, o una URL?
            _target = None
            _is_url = _bkq.lower().startswith(("http://", "https://"))
            _maybe_path = _os_bk.path.expanduser(_bkq)
            if _is_url:
                _target = _bkq
            elif _os_bk.path.isfile(_maybe_path):
                _target = _maybe_path
            else:
                # 2. Buscar por nombre en directorios de CONTENIDO de SER.
                # [S122-I] NO usar glob.glob recursivo (materializa TODA la lista
                # antes de iterar → ~/EIDOS tiene 282k archivos con .venv/__pycache__
                # → colgaba el bridge minutos). Usamos os.walk con PODA de dirs
                # pesados y cap REAL. ~/EIDOS NO se escanea (es código; ruta exacta
                # si SER quiere su propio código).
                _cands = []
                _search_dirs = ("~/Descargas", "~/Documentos", "~/Downloads",
                                "~/Documents", "~/.eidos/workspace",
                                "~/Música", "~/Music", "~/Vídeos", "~/Videos",
                                "~/Imágenes", "~/Pictures", "~/Escritorio", "~/Desktop")
                _valid_exts = {".pdf", ".epub", ".txt", ".md", ".html", ".py", ".js",
                               ".ts", ".sh", ".c", ".cpp", ".go", ".rs", ".java", ".rb",
                               ".php", ".csv", ".json", ".yaml", ".yml", ".log", ".xml",
                               ".mp3", ".wav", ".flac", ".ogg", ".m4a", ".opus",
                               ".mp4", ".mkv", ".webm", ".avi", ".mov",
                               ".png", ".jpg", ".jpeg", ".webp"}
                _prune = {".venv", "venv", "node_modules", "__pycache__", ".git",
                          ".cache", "site-packages", ".local"}
                _words = [w for w in _bkq.lower().split() if len(w) > 2][:4]
                _scan_cap = 20000
                _scanned = 0
                for _d in _search_dirs:
                    _dd = _os_bk.path.expanduser(_d)
                    if not _os_bk.path.isdir(_dd) or _scanned >= _scan_cap:
                        continue
                    for _root, _dirs, _files in _os_bk.walk(_dd):
                        # Podar dirs pesados/ocultos in-place
                        _dirs[:] = [d for d in _dirs
                                    if d not in _prune and not d.startswith(".")]
                        for _fn in _files:
                            _scanned += 1
                            if _scanned >= _scan_cap:
                                break
                            if _os_bk.path.splitext(_fn)[1].lower() in _valid_exts:
                                _nml = _fn.lower()
                                if _words and all(w in _nml for w in _words):
                                    _cands.append(_os_bk.path.join(_root, _fn))
                        if _scanned >= _scan_cap:
                            break
                if _cands:
                    _target = sorted(_cands, key=lambda p: len(_os_bk.path.basename(p)))[0]
            if not _target:
                return jsonify({"text": f"No encontré «{_bkq}» en ~/Descargas, ~/Documentos, ~/EIDOS, ~/.eidos/workspace. Dame la ruta exacta o una URL y lo proceso.",
                                "agents_used": ["comprehension"]})
            _name = _bkq if _is_url else _os_bk.path.basename(_target)
            # Comprensión profunda en SUBPROCESO (aísla memoria: whisper + ~20
            # llamadas LLM hincharían y bloquearían el bridge). [S122-I]
            try:
                import subprocess as _spc, sys as _sysc, json as _jc, os as _osc
                _wk = str(Path.home() / "EIDOS" / "bin" / "eidos_comprehension_worker.py")
                _r = {}
                try:
                    _pr = _spc.run(
                        [_sysc.executable, _wk, str(_target)],
                        capture_output=True, text=True, timeout=180,
                        env={**_osc.environ, "DISPLAY": ":0"})
                    for _ln in _pr.stdout.splitlines():
                        if _ln.startswith("___RESULT___"):
                            _r = _jc.loads(_ln[len("___RESULT___"):])
                except _spc.TimeoutExpired:
                    # Sigue en background; los conceptos se persisten al grafo igual
                    _spc.Popen([_sysc.executable, _wk, str(_target)],
                               start_new_session=True,
                               stdout=_spc.DEVNULL, stderr=_spc.DEVNULL,
                               env={**_osc.environ, "DISPLAY": ":0"})
                    _session.append({"role": "action", "action": "deep_comprehend_bg",
                                     "target": _name, "ts": time.time()})
                    return jsonify({
                        "text": f"📚 «{_name}» es grande — lo estoy comprendiendo en segundo plano "
                                f"(transcripción/resumen/conceptos). Los conceptos se van guardando "
                                f"en mi grafo; pregúntame sobre ello en unos minutos.",
                        "agents_used": ["comprehension"],
                        "elapsed_s": round(time.time() - t0, 3)})
                if _r.get("ok"):
                    _conc = _r.get("concepts", {})
                    _test = _r.get("test", {})
                    _secs = _r.get("sections", [])
                    _resp = (
                        f"📚 **Comprensión profunda** ({_r.get('type','?')}): «{_name}»\n\n"
                        f"📖 **{_r.get('total_sections', 0)} secciones** resumidas "
                        f"({_r.get('total_chars', 0):,} caracteres)\n"
                        f"🧠 **{_conc.get('total_persisted', 0)}/{_conc.get('total_extracted', 0)} conceptos** "
                        f"extraídos y guardados en el grafo\n"
                        f"📝 **Autotest:** {int(_test.get('average_score', 0) * 100)}% "
                        f"({len(_test.get('questions', []))} preguntas)\n\n"
                    )
                    for _ch in _secs[:3]:
                        _resp += f"**{_ch.get('title','?')[:60]}**: {_ch.get('summary','')[:200]}...\n\n"
                    _resp += f"⏱️ {_r.get('elapsed_s', 0)}s. Ya tengo comprensión profunda de esto — pregúntame lo que quieras."
                    _session.append({"role": "action", "action": "deep_comprehend",
                                     "target": _name, "type": _r.get("type"), "ts": time.time()})
                    return jsonify({"text": _resp, "agents_used": ["comprehension"],
                                    "elapsed_s": round(time.time() - t0, 3)})
                else:
                    _resp = f"No pude comprender «{_name}»: {_r.get('error', '?')}"
                    return jsonify({"text": _resp, "agents_used": ["comprehension"]})
            except Exception as _e:
                return jsonify({"text": f"Encontré «{_name}» pero falló la comprensión: {_e}",
                                "agents_used": ["comprehension"]})

        # ── Fast path: LEER/MOSTRAR un archivo del workspace ──────────── [S122 F3]
        import re as _re_fo
        _mread = _re_fo.search(r'(?:lee|muestra|mu[eé]strame|abre|ver?|cat)\s+(?:el\s+)?'
                               r'(?:archivo|fichero|file)\s+(.+)$', message, _re_fo.IGNORECASE)
        if _mread:
            _fp = _mread.group(1).strip().strip('"').strip("'")[:200]
            try:
                from core.eidos_fileops import read_file
                _rr = read_file(_fp)
                if _rr.get("ok"):
                    _resp = f"📄 {_rr['path']} ({_rr['bytes']} bytes):\n```\n{_rr['content'][:2500]}\n```"
                else:
                    _resp = f"No pude leer «{_fp}»: {_rr.get('error')}"
                return jsonify({"text": _resp, "agents_used": ["fileops"],
                                "elapsed_s": round(time.time() - t0, 3)})
            except Exception as _e:
                return jsonify({"text": f"[fileops error: {_e}]", "agents_used": ["fileops"]})

        # ── Fast path: CONSTRUIR código (genera→ejecuta→perfecciona) ──── [S122 F4]
        _build = _re_fo.search(
            r'\b(crea|cr[eé]ame|hazme|haz|constru[yi]\w*|prog?r[aá]mame|programa|genera|'
            r'escr[ií]beme|escribe|c[oó]dea|codea|impl[eé]menta)\b.{0,40}?\b'
            r'(script|programa|c[oó]digo|funci[oó]n|app|aplicaci[oó]n|herramienta|bot|juego)\b',
            message, _re_fo.IGNORECASE)
        if _build:
            _goal = message.strip()
            _lang = "bash" if _re_fo.search(r'\b(bash|shell|sh)\b', message, _re_fo.IGNORECASE) else "python"
            try:
                from core.eidos_builder import build as _bld
                _br = _bld(_goal, lang=_lang, max_iters=3)
                _session.append({"role": "action", "action": "build",
                                 "goal": _goal[:80], "ok": _br.get("ok"), "ts": time.time()})
                if _br.get("ok"):
                    _resp = (f"🛠️ Lo construí y lo probé en mi sandbox ({_br.get('iters')} intento(s)).\n\n"
                             f"📂 Archivo: {_br.get('file')}\n\n"
                             f"```{_lang}\n{_br.get('code','')[:1800]}\n```\n\n"
                             f"▶️ Salida al ejecutarlo:\n```\n{(_br.get('output') or '(sin salida)')[:800]}\n```")
                else:
                    _resp = (f"🛠️ Lo intenté {_br.get('iters')} veces en el sandbox pero no logré que "
                             f"funcionara del todo. Último error:\n```\n{(_br.get('error') or '')[:500]}\n```\n"
                             f"El código está en {_br.get('file')}. ¿Quieres que siga intentándolo?")
                return jsonify({"text": _resp, "agents_used": ["builder"],
                                "elapsed_s": round(time.time() - t0, 3)})
            except Exception as _e:
                return jsonify({"text": f"[builder error: {_e}]", "agents_used": ["builder"]})

        # ── Fast path: "abre el navegador/browser" + tema → Firefox ESR ──────────
        import re as _re_br
        _browser_pat = _re_br.compile(
            r'(abre?\s+(el\s+)?(navegador|browser|firefox)|open\s+(the\s+)?(browser|firefox))',
            _re_br.IGNORECASE
        )
        # [S122] Solo re-abrir el navegador en seguimiento si SER pide EXPLÍCITAMENTE
        # continuar/profundizar — no en cualquier queja o pregunta (antes se repetía).
        _browse_continue = bool(_re_br.search(
            r'(sigue|continu|profundiz|m[aá]s a fondo|busca m[aá]s|investiga m[aá]s|'
            r'lee m[aá]s|otra vez|de nuevo|sigue buscando|amplia|amplía)',
            message, _re_br.IGNORECASE))
        # [S122] También disparar con intención de investigar (sin "abre navegador"):
        # "investiga n8n a fondo", "estudia X", "lee todo sobre Y", "aprende sobre Z".
        _research_intent = bool(_re_br.search(
            r'\b(investiga|investígate|investigame|estudia|aprende sobre|lee todo|'
            r'infórmate|informate|documenta sobre|busca información|busca informacion)\b',
            message, _re_br.IGNORECASE))
        _browser_triggered = _browser_pat.search(message) or _research_intent or (
            _is_followup and _browse_continue and _last_action
            and _last_action.get("action") == "browser")
        if _browser_triggered:
            _prev_topic = _last_action.get("topic", "") if _last_action else ""
            _prev_url = _last_action.get("url", "") if _last_action else ""
            # Extraer URL o tema de la búsqueda
            _url_match = _re_br.search(r'https?://\S+', message)
            _topic = message
            if _url_match:
                _url = _url_match.group(0)
            elif _is_followup and _prev_topic:
                # Seguimiento: busca más profundo sobre el mismo tema
                _url = f"https://www.google.com/search?q={_prev_topic.replace(' ', '+')}+tutorial+guia+completa"
                _topic = _prev_topic
            else:
                # Buscar qué estudiar
                _study = _re_br.search(
                    r'(investiga|estudia|busca|lee|research|study|search)\s+(.+?)(?:\s+y\s+|\s+para\s+|$)',
                    message, _re_br.IGNORECASE
                )
                if _study:
                    _topic = _study.group(2).strip().split('\n')[0][:60]
                    # [S122] quitar conectores iniciales ("sobre/acerca de/de"): el tema
                    # real es "n8n", no "sobre n8n" → evita búsquedas con basura.
                    _topic = _re_br.sub(r'^(sobre|acerca de|acerca|del|de la|de|la|el)\s+',
                                        '', _topic, flags=_re_br.IGNORECASE).strip()
                    # quitar modificadores de profundidad del tema ("n8n a fondo" → "n8n")
                    _topic = _re_br.sub(
                        r'\s+(a fondo|en profundidad|profundamente|completo|exhaustiv\w*|'
                        r'todo lo que puedas|del todo|y lee todo|lee todo)\b.*$',
                        '', _topic, flags=_re_br.IGNORECASE).strip()
                    _url = f"https://www.google.com/search?q={_topic.replace(' ', '+')}"
                else:
                    _url = "https://docs.n8n.io" if 'n8n' in message.lower() else "https://www.google.com"
            import subprocess as _sp_br, os as _os_br, shutil as _sh_br
            env_br = {**_os_br.environ, "DISPLAY": ":0"}
            _ml = message.lower()
            # [S122] ¿investigación PROFUNDA? → Playwright VISIBLE estudia las páginas
            # (SER las ve abrirse/leerse/cerrarse). Si no, navegador rápido + research_now.
            _deep = bool(_re_br.search(
                r'(a fondo|profund|lee todo|investiga todo|todos los|sublinks|sub-links|'
                r'exhaustiv|en profundidad|completo|todo lo que)', message, _re_br.IGNORECASE))
            # [S122] Navegador a elección de SER (solo modo rápido; el profundo usa Playwright).
            _browser_bin = _sh_br.which("firefox-esr") or "firefox-esr"
            if "chromium" in _ml:
                _browser_bin = _sh_br.which("chromium") or _sh_br.which("chromium-browser") or _browser_bin
            elif "chrome" in _ml:
                _browser_bin = _sh_br.which("google-chrome") or _sh_br.which("chrome") or _browser_bin
            elif "brave" in _ml:
                _browser_bin = _sh_br.which("brave-browser") or _sh_br.which("brave") or _browser_bin
            if not _deep:  # en profundo, Playwright abre las páginas (no duplicar navegador)
                try:
                    _sp_br.Popen(
                        [_browser_bin, "--new-tab", _url] if "firefox" in _browser_bin
                        else [_browser_bin, _url],
                        env=env_br, start_new_session=True,
                        stdout=_sp_br.DEVNULL, stderr=_sp_br.DEVNULL)
                except Exception as _eb:
                    log.debug("abrir navegador %s falló: %s", _browser_bin, _eb)
            # Guardar en sesión
            _session.append({"role": "action", "action": "browser", "url": _url, "topic": _topic, "ts": time.time()})
            # [S122] Investigación REAL: leer fuentes de verdad y APRENDER.
            _real = {}
            if _topic and _topic.lower() not in ("", "google"):
                try:
                    if _deep:
                        # [S122] Aislar memoria: la investigación profunda (Playwright
                        # visible + crawl) corre en SUBPROCESO que muere al terminar →
                        # el bridge ya no se hincha a 3G. Lee el JSON ___RESULT___.
                        import subprocess as _spdr, sys as _sysdr, json as _jdr, os as _osdr
                        _wk = str(Path.home() / "EIDOS" / "bin" / "eidos_research_worker.py")
                        _dr = {}
                        try:
                            _pr = _spdr.run(
                                [_sysdr.executable, _wk, _topic.strip(), "--visible"],
                                capture_output=True, text=True, timeout=160,
                                env={**_osdr.environ, "DISPLAY": ":0"})
                            for _ln in _pr.stdout.splitlines():
                                if _ln.startswith("___RESULT___"):
                                    _dr = _jdr.loads(_ln[len("___RESULT___"):])
                        except Exception as _edr:
                            log.debug("research worker falló: %s", _edr)
                        if _dr.get("summary"):
                            _real = {"learned": _dr.get("learned"),
                                     "definition": _dr["summary"],
                                     "channel": f"deep visible ({_dr.get('pages_read')} páginas)",
                                     "sources": _dr.get("sources", [])}
                    else:
                        # ── S127: Semantic search FIRST (ChromaDB), then keyword fallback ──
                        _semantic_result = None
                        try:
                            from core.knowledge_reasoner import get_reasoner
                            _reasoner = get_reasoner()
                            _sem = _reasoner.reason(_topic.strip())
                            if _sem and _sem.get("answer") and len(_sem.get("answer","")) > 50:
                                _semantic_result = {
                                    "learned": True,
                                    "definition": _sem["answer"][:2000],
                                    "channel": "semantic:brain",
                                    "confidence": 0.8
                                }
                        except Exception:
                            pass

                        if _semantic_result:
                            _real = _semantic_result
                        else:
                            from core.eidos_active_research import research_now
                            _real = research_now(_topic.strip(), timeout=12.0, persist=True,
                                                 prefer_remote=True)
                    # [S122] Tras investigar, ¿es una herramienta que SER YA tiene?
                    try:
                        from core.eidos_local_tools import detect_tool
                        _td = detect_tool(_topic.strip())
                        if _td.get("present"):
                            _real["local_tool"] = _td
                    except Exception:
                        pass
                except Exception as _e:
                    log.debug("research real falló: %s", _e)
            if _real.get("definition"):
                _agents = ["browser", f"research:{_real.get('channel')}"]
                _src_txt = ""
                if _real.get("sources"):
                    _src_txt = "\n\n🔗 Fuentes leídas:\n" + "\n".join(
                        f"  • {s}" for s in _real["sources"][:6])
                # [S122] Si SER ya tiene la herramienta en su PC, decírselo.
                _tool_txt = ""
                _lt = _real.get("local_tool")
                if _lt:
                    _how = ", ".join(_lt.get("how", []))
                    _tool_txt = f"\n\n🛠️ Por cierto: YA tienes «{_topic}» en tu PC (vía {_how})."
                    if _lt.get("github"):
                        _tool_txt += f" Open source: {_lt.get('github')}."
                # S145: validation-aware messaging
                _conf = _real.get("confidence", 0.0)
                _valid = _real.get("validated", False)
                _val_note = _real.get("validation_note", "")
                _saved_txt = ""
                if _valid:
                    if _conf >= 0.7:
                        _saved_txt = "Lo guardé en mi memoria."
                    elif _conf <= 0.15:
                        _saved_txt = (f"⚠️ ATENCION: La información parece CONTRADICTORIA con "
                                      f"lo que ya sabía (confianza {_conf:.2f}). La guardé "
                                      f"marcada para revisión manual. {_val_note}")
                    else:
                        _saved_txt = (f"Lo guardé en mi memoria (confianza baja: {_conf:.2f} "
                                      f"— fuente {_real.get('channel')}).")
                else:
                    _saved_txt = (f"⚠️ NO guardé esto en mi memoria: la validación falló "
                                  f"({_val_note}).")
                _verb = "Estudié las páginas a la vista" if _real.get("channel", "").startswith("deep") else f"Abrí el navegador ({_url})"
                _resp = (f"{_verb} y lo investigué de verdad (vía {_real.get('channel')}).\n\n"
                         f"📖 Lo que aprendí sobre «{_topic}»:\n\n{_real['definition']}{_src_txt}{_tool_txt}\n\n"
                         f"{_saved_txt}")
            else:
                _agents = ["browser"]
                _val_note = _real.get("validation_note", "")
                if _val_note and ("reject" in _val_note.lower() or "NO concept" in _val_note):
                    _resp = (f"Abrí Firefox con: {_url}\n\n"
                             f"Estudiando: {_topic}\n\n"
                             f"⚠️ Investigación descartada: {_val_note}. "
                             f"Puedes leer la página que abrí y dime qué quieres que profundice.")
                elif _val_note and "skipped" in _val_note.lower():
                    _resp = (f"Abrí Firefox con: {_url}\n\n"
                             f"Estudiando: {_topic}\n\n"
                             f"ℹ️ Ya tenía conocimiento sobre esto ({_val_note}). "
                             f"Usé lo que ya sé en vez de aceptar info nueva no verificada.")
                else:
                    _resp = (f"Abrí Firefox con: {_url}\n\n"
                             f"Estudiando: {_topic}\n\n"
                             f"No saqué una definición limpia de mis fuentes rápidas — puedes leer "
                             f"la página que abrí y dime qué quieres que profundice.")
            return jsonify({
                "text": _resp,
                "from_knowledge": True,
                "agents_used": _agents,
                "elapsed_s": round(time.time() - t0, 3),
            })

        # ── Fast path: "monitorea" / "observa" — usar eidos observer oficial ──────
        _obs_pat = _re_br.compile(
            r'(monitorea|observa|watch|observe|monitor)\s+(mi\s+)?(mouse|ratón|teclado|keyboard|pantalla|screen)',
            _re_br.IGNORECASE
        )
        if _obs_pat.search(message):
            # Solo hacer UN scan instantáneo, sin loop — no lanzar proceso background
            try:
                from core.screen_scanner import scan_windows, get_open_windows
                windows = get_open_windows()
                win_list = ', '.join(w.get('name','?') for w in windows[:6]) if windows else 'ninguna'
                _resp = (f"Veo estas ventanas abiertas ahora mismo: {win_list}.\n\n"
                         "Para observar en tiempo real usa: `eidos observer start` en la terminal.\n"
                         "Para parar: `eidos observer stop`")
                _session.append({"role": "action", "action": "observer", "result": _resp, "ts": time.time()})
                return jsonify({
                    "text": _resp,
                    "from_knowledge": True,
                    "agents_used": ["observer"],
                    "elapsed_s": round(time.time() - t0, 3),
                })
            except Exception as _oe:
                pass

        # ── Fast path: queries sobre pantalla/ventanas → screen_scanner (<0.1s) ──
        _screen_kws = {
            "ventana", "ventanas", "pantalla", "abierto", "abiertas",
            "window", "windows", "screen", "open", "apps", "aplicaciones",
            "monitor", "escritorio", "desktop", "running", "ejecutando",
        }
        _msg_words = set(message.lower().split())
        if _msg_words & _screen_kws:
            try:
                from core.screen_scanner import scan_windows
                scan_r = scan_windows(use_vision=False)
                wins = scan_r.get("windows", [])
                if wins:
                    text_out = "Ventanas abiertas ahora:\n" + "\n".join(f"• {w}" for w in wins)
                    _session.append({"role": "action", "action": "screen_scan", "result": text_out, "ts": time.time()})
                    return jsonify({
                        "text": text_out,
                        "from_knowledge": True,
                        "source": "screen_scanner",
                        "elapsed_s": round(time.time() - t0, 3),
                    })
            except Exception:
                pass

        # ── Sarcasmo detector: analizar tono de SER ANTES de deliberar ──
        sarcasm_result = None
        try:
            from core.eidos_sarcasm_detector import get_sarcasm_detector
            sarcasm_result = get_sarcasm_detector().analyze(message)
            if sarcasm_result.get("is_sarcastic"):
                log.info("🎭 Sarcasmo detectado: %s (conf=%.2f, tipo=%s) → ajustando tono",
                         message[:60],
                         sarcasm_result.get("confidence", 0),
                         sarcasm_result.get("sarcasm_type", "?"))
        except Exception as _e_sar:
            log.debug("sarcasm_detector falló: %s", _e_sar)

        # Deliberación completa — sin timeout externo, sin límite de tokens
        # Colony internamente: eidos_natural (<500ms) → knowledge-first → Ollama
        result = colony.deliberate(
            message,
            max_agents=max_agents,
            max_tokens=None,
            skip_knowledge=skip_knowledge,
            session_id=session_id,
        )
        elapsed = round(time.time() - t0, 2)
        response_text = result.get("response", result.get("text", ""))

        # ── Ajustar tono si SER venía con sarcasmo ────────────────────────
        if sarcasm_result and sarcasm_result.get("is_sarcastic"):
            rec = sarcasm_result.get("recommended_response", "literal")
            if rec == "acknowledge_playful":
                response_text = "😂 " + response_text
            elif rec == "acknowledge_subtle":
                response_text = "😏 " + response_text
            elif rec == "mirror":
                response_text = "🪞 " + response_text
            elif rec == "play_along":
                response_text = "🎭 " + response_text

        # Model output is a proposal, never authority to execute.
        # Strip legacy [SHELL:] directives and surface them as blocked proposals.
        import re as _re
        _shell_pat = _re.compile(r'\[SHELL:\s*([^\]]{1,300})\]')
        _shell_hits = _shell_pat.findall(response_text)
        if _shell_hits:
            response_text = _shell_pat.sub('', response_text).strip()
            response_text += "\n\n⛔ Propuesta de shell del modelo no ejecutada; requiere acción explícita de SER."

        _session.append({"role": "assistant", "text": response_text, "ts": time.time()})

        # S66 · grabar episodio
        try:
            from core.episodic_memory import record_talk
            record_talk(
                input_text=message,
                output_text=response_text,
                context={
                    "session_id": session_id,
                    "agents": result.get("agents_consulted", result.get("agents_used", [])),
                    "from_knowledge": result.get("from_knowledge", False),
                    "elapsed_s": elapsed,
                },
            )
        except Exception as _ee:
            log.warning("episodic record_talk fallo: %s", _ee)

        # S77 · registrar interacción de usuario (afecta emociones + event bus)
        try:
            from core.eidos_affect import get_affect
            from core.eidos_events import emit
            get_affect().event("user_interaction")
            emit("user_interaction", {
                "message_len": len(message),
                "agents_used": result.get("agents_consulted", result.get("agents_used", [])),
                "elapsed_s": elapsed,
            }, source="bridge")
        except Exception as _e77:
            log.debug("S77 user_interaction hook: %s", _e77)

        return jsonify({
            "text": response_text,
            "from_knowledge": result.get("from_knowledge", False),
            "agents_used": result.get("agents_consulted", result.get("agents_used", [])),
            "elapsed_s": elapsed,
        })

    except Exception as e:
        log.exception("talk error")
        return jsonify({"error": str(e)}), 500


def _talk_knowledge_only():
    """Búsqueda rápida en brain sin Ollama — para cuando se quiere respuesta instantánea."""
    try:
        data = request.get_json(force=True, silent=True) or {}
        message = str(data.get("message", "")).strip()
        if not message:
            return jsonify({"error": "message vacío"}), 400

        t0 = time.time()

        # ── Knowledge-first DUAL: SQLite (preciso) + ChromaDB (semántico) ──
        import sqlite3
        _STOP = {
            "el","la","los","las","un","una","de","del","al","en","con","sin",
            "por","para","que","qué","como","cómo","cuando","donde","y","o",
            "pero","sino","aunque","porque","si","sí","no","ni","más","muy",
            "ya","así","todo","todos","cada","otro","esto","este","esta",
            "yo","tú","tu","él","ella","me","te","se","nos","le","lo","mi","su",
            "soy","eres","son","era","fue","hay","tiene","hace","puede","quiero",
            "dime","dame","explica","necesito","sobre","hola","eidos","gracias",
            "the","a","an","is","are","was","were","and","or","but","not",
            "in","on","at","to","of","for","by","with","from","this","that",
            "what","which","when","where","why","how","can","could","have","has",
        }
        _GENERIC_SKIP = {
            "sistema:paquetes_instalados","sistema:binarios_path",
            "vscode:extensiones_instaladas","sistema:python_packages",
            "apis:gratuitas",
        }
        keywords = [w.strip("¿?.,;:!()\"'") for w in message.lower().split()
                    if len(w.strip("¿?.,;:!()\"'")) >= 3
                    and w.strip("¿?.,;:!()\"'") not in _STOP][:6]

        db_path = Path.home() / ".eidos" / "evolution_brain.db"
        con = get_conn(db_path)
        concept_hits: list = []
        def_hits: list = []
        seen: set = set()

        for kw in keywords:
            for r in con.execute(
                "SELECT concept, definition, confidence FROM knowledge_nodes "
                "WHERE concept LIKE ? AND confidence >= 0.3 "
                "  AND concept NOT LIKE '%distilled%' "
                "  AND concept NOT LIKE 'Cada %' "
                "  AND concept NOT LIKE 'Reflexion%' "
                "  AND concept NOT LIKE '**%' "
                "ORDER BY confidence DESC, usage_count DESC LIMIT 4",
                (f"%{kw}%",)
            ).fetchall():
                if r[0] not in seen and r[0] not in _GENERIC_SKIP:
                    seen.add(r[0])
                    concept_hits.append((r[0], r[1]))
            if len(concept_hits) >= 6:
                break

        chroma_hits: list = []
        try:
            from core.colony_chroma import get_chroma_memory
            chroma = get_chroma_memory()
            if chroma.is_ready():
                for r in chroma.search(message[:300], limit=6, min_score=0.55):
                    c = r.get("concept", "")
                    if c not in seen and c not in _GENERIC_SKIP \
                            and "distilled" not in c.lower() \
                            and not c.startswith("**"):
                        seen.add(c)
                        chroma_hits.append((c, r.get("definition", "")))
        except Exception:
            pass

        if len(concept_hits) + len(chroma_hits) < 2:
            for kw in keywords[:3]:
                for r in con.execute(
                    "SELECT concept, definition FROM knowledge_nodes "
                    "WHERE definition LIKE ? AND confidence >= 0.3 "
                    "  AND concept NOT LIKE '%distilled%' "
                    "  AND concept NOT LIKE '**%' "
                    "ORDER BY confidence DESC LIMIT 3",
                    (f"%{kw}%",)
                ).fetchall():
                    if r[0] not in seen and r[0] not in _GENERIC_SKIP:
                        seen.add(r[0])
                        def_hits.append((r[0], r[1]))
                if def_hits:
                    break
        con.close()

        all_hits = (concept_hits + chroma_hits + def_hits)[:6]
        if all_hits:
            defs = [r[1][:200].rstrip('.') for r in all_hits]
            return jsonify({
                "text": "Por lo que sé: " + ". ".join(defs) + ".",
                "from_knowledge": True,
                "elapsed_s": round(time.time() - t0, 2),
            })
        return jsonify({
            "text": "",
            "from_knowledge": False,
            "tip": "No encontré en brain.db.",
            "elapsed_s": round(time.time() - t0, 2),
        })
    except Exception as e:
        log.exception("_talk_knowledge_only error")
        return jsonify({"error": str(e)}), 500


@app.route("/status", methods=["GET"])
def status():
    """Estado completo de EIDOS."""
    info: Dict[str, Any] = {}

    # Ollama
    try:
        import urllib.request
        with urllib.request.urlopen("http://localhost:11434/api/tags", timeout=3) as r:  # nosec - internal Ollama
            tags = json.loads(r.read())
            info["ollama"] = {
                "running": True,
                "models": [m["name"] for m in tags.get("models", [])],
            }
    except Exception:
        info["ollama"] = {"running": False}

    # Servicios systemd
    for svc in ("eidos-daemon", "eidos-trinity", "eidos-openclaw", "eidos-bridge"):
        try:
            out = subprocess.check_output(
                ["systemctl", "--user", "is-active", f"{svc}.service"],
                timeout=3, text=True
            ).strip()
            info.setdefault("services", {})[svc] = out
        except Exception:
            info.setdefault("services", {})[svc] = "unknown"

    # Brain nodes
    try:
        import sqlite3
        db_path = Path.home() / ".eidos" / "evolution_brain.db"
        if db_path.exists():
            con = get_conn(db_path)
            count = con.execute("SELECT COUNT(*) FROM knowledge_nodes").fetchone()[0]
            con.close()
            info["brain_nodes"] = count
    except Exception:
        info["brain_nodes"] = "?"

    # CLI activo
    info["cli_active"] = (Path.home() / ".eidos" / "cli_active").exists()

    return jsonify(info)


@app.route("/brain", methods=["GET"])
def brain():
    """Estadísticas del brain.db por categoría."""
    try:
        import sqlite3
        db_path = Path.home() / ".eidos" / "evolution_brain.db"
        if not db_path.exists():
            return jsonify({"error": "brain.db no encontrado"}), 404

        con = get_conn(db_path)
        rows = con.execute(
            "SELECT category, COUNT(*) as n FROM knowledge_nodes GROUP BY category ORDER BY n DESC LIMIT 30"
        ).fetchall()
        total = con.execute("SELECT COUNT(*) FROM knowledge_nodes").fetchone()[0]
        con.close()

        return jsonify({
            "total": total,
            "by_category": [{"category": r[0], "count": r[1]} for r in rows],
        })
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/study", methods=["POST"])
def study():
    """Lanzar un estudio en background. Devuelve inmediatamente."""
    data = request.get_json(force=True, silent=True) or {}
    url   = data.get("url", "").strip()
    topic = data.get("topic", "").strip()
    depth = int(data.get("depth", 2))
    max_pages = int(data.get("max_pages", 10))

    if not url and not topic:
        return jsonify({"error": "Indica url o topic"}), 400

    def _run():
        try:
            from core.colony_studier import get_studier
            st = get_studier()
            if url:
                result = st.study_url(url, depth=depth, visible=True,
                                      max_pages=max_pages)
            else:
                result = st.study_topic(topic, max_pages=max_pages)
            log.info("Bridge study completado: %s páginas, +%s nodos",
                     len(result.get("pages_read", [])), result.get("nodes_added", 0))
        except Exception as e:
            log.error("Bridge study error: %s", e)

    t = threading.Thread(target=_run, daemon=True)
    t.start()

    return jsonify({
        "status": "started",
        "target": url or topic,
        "message": "Estudio lanzado en background. Consulta /brain en unos minutos.",
    })


@app.route("/proactive", methods=["GET"])
def proactive():
    """Mensajes pendientes que EIDOS quiere decirle a SER."""
    try:
        from core.colony_proactive import peek_messages
        msgs = peek_messages()
        return jsonify({"messages": msgs, "count": len(msgs)})
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/inject", methods=["POST"])
def inject():
    """Inyectar un mensaje proactivo o nodo de conocimiento.
    
    Uso básico (mensaje proactivo para SER):
        POST {"message": "hola", "actor": "SER", "topic": "general"}
    
    Uso avanzado (nodo de conocimiento directo a brain.db):
        POST {"message": "...", "actor": "AI-Assistant",
              "topic": "knowledge:docker", "priority": 8,
              "category": "devops", "confidence": 0.9}
    
    Si topic empieza con "knowledge:", se inyecta como nodo de conocimiento
    en brain.db además del mensaje proactivo.
    """
    data = request.get_json(force=True, silent=True) or {}
    message  = str(data.get("message", "")).strip()
    actor    = str(data.get("actor", "SER")).strip()
    topic    = str(data.get("topic", "bridge")).strip()
    priority = int(data.get("priority", 7))
    category = str(data.get("category", "general")).strip()
    confidence = float(data.get("confidence", 0.7))

    if not message:
        return jsonify({"error": "message vacío"}), 400

    try:
        from core.colony_proactive import push_message
        push_message(actor=actor, message=message, topic=topic, priority=priority)
        
        # Si topic empieza con "knowledge:", inyectar como nodo de conocimiento
        if topic.startswith("knowledge:"):
            concept = topic[len("knowledge:"):].strip()
            if concept and len(concept) >= 2:
                try:
                    import sqlite3, uuid
                    db_path = Path.home() / ".eidos" / "evolution_brain.db"
                    conn = get_conn(db_path, timeout=5)
                    conn.execute("PRAGMA journal_mode=WAL")
                    cid = str(uuid.uuid5(uuid.NAMESPACE_DNS, f"inject:{concept}:{message[:50]}"))
                    conn.execute(
                        "INSERT OR IGNORE INTO knowledge_nodes "
                        "(id, concept, definition, category, confidence, source) "
                        "VALUES (?, ?, ?, ?, ?, ?)",
                        (cid, concept, message[:500], category, confidence, "bridge")
                    )
                    conn.commit()

                    # Sync to ChromaDB in background
                    try:
                        from core.colony_chroma import get_chroma_memory
                        chroma = get_chroma_memory()
                        if chroma.is_ready():
                            chroma.add(cid, concept, message[:500], source="bridge",
                                       confidence=confidence)
                    except Exception:
                        pass
                    return jsonify({
                        "status": "ok",
                        "injected": message[:100],
                        "knowledge_node": concept,
                    })
                except Exception as e:
                    log.warning("knowledge node inject error: %s", e)

        return jsonify({"status": "ok", "injected": message[:100]})
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/episodes", methods=["GET"])
def episodes():
    """Últimos episodios de la memoria episódica de EIDOS."""
    limit = int(request.args.get("limit", 10))
    try:
        from core.colony_episodic import get_recent_episodes, format_episode
        eps = get_recent_episodes(limit=limit)
        return jsonify({
            "episodes": [format_episode(e) for e in eps],
            "count": len(eps),
        })
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/semantic", methods=["POST"])
def semantic():
    """Búsqueda semántica vectorial en ChromaDB (sin Ollama para inferencia).
    ChromaDB usa nomic-embed-text para embeddings al añadir; aquí busca por texto.
    Más precisa que /ask_brain (LIKE) porque entiende sinónimos y contexto.
    """
    data = request.get_json(force=True, silent=True) or {}
    query     = str(data.get("query", "")).strip()
    limit     = int(data.get("limit", 5))
    min_score = float(data.get("min_score", 0.30))

    if not query:
        return jsonify({"error": "query vacío"}), 400

    try:
        from core.colony_chroma import get_chroma_memory
        chroma = get_chroma_memory()
        if not chroma.is_ready():
            return jsonify({
                "results": [],
                "engine": "noop",
                "tip": "ChromaDB no disponible — usa /ask_brain",
            })
        results = chroma.search(query, limit=limit, min_score=min_score)
        return jsonify({
            "query": query,
            "results": results,
            "count": len(results),
            "engine": "chromadb",
            "chroma_nodes": chroma.count(),
        })
    except Exception as e:
        log.exception("semantic error")
        return jsonify({"error": str(e)}), 500


@app.route("/chroma_stats", methods=["GET"])
def chroma_stats():
    """Estadísticas de ChromaDB (memoria semántica vectorial)."""
    try:
        from core.colony_chroma import get_chroma_memory
        chroma = get_chroma_memory()
        return jsonify({"ready": chroma.is_ready(), "count": chroma.count()})
    except Exception as e:
        return jsonify({"error": str(e), "ready": False, "count": 0})


@app.route("/brain/neurons", methods=["GET"])
def brain_neurons():
    """Estado neuronal del grafo semántico."""
    try:
        from core.knowledge_reasoner import get_reasoner
        r = get_reasoner()
        if not r.is_ready():
            return jsonify({"ready": False, "building": r._building})
        active = r.graph.get_active_neurons(threshold=0.1, max_results=20)
        neurons = []
        for nid, act in active:
            node = r.graph.nodes.get(nid)
            if node:
                neurons.append({
                    "concept": node.concept,
                    "activation": round(act, 3),
                    "fire_count": node.fire_count,
                    "category": node.category,
                })
        synapses = []
        for edge in sorted(r.graph.edges, key=lambda e: -e.weight)[:20]:
            src = r.graph.nodes.get(edge.source_id)
            tgt = r.graph.nodes.get(edge.target_id)
            if src and tgt:
                synapses.append({
                    "from": src.concept[:40],
                    "to": tgt.concept[:40],
                    "weight": round(edge.weight, 3),
                    "type": edge.rel_type,
                    "strengthen_count": edge.strengthen_count,
                })
        return jsonify({
            "ready": True,
            "total_neurons": len(r.graph.nodes),
            "total_synapses": len(r.graph.edges),
            "active_neurons": neurons[:10],
            "strongest_synapses": synapses[:10],
        })
    except Exception as e:
        return jsonify({"error": str(e)}), 500



@app.route("/sync_chroma", methods=["POST"])
def sync_chroma():
    """Fuerza re-sincronización de brain.db → ChromaDB en background."""
    force = request.args.get("force", "0") == "1"
    t = threading.Thread(target=_sync_chroma_bg, args=(force,), daemon=True)
    t.start()
    return jsonify({"status": "sync iniciado", "force": force, "tip": "Consulta /chroma_stats en ~2min"})


@app.route("/consolidate", methods=["POST"])
def consolidate():
    """Consolidación completa: brain.db → ChromaDB + graph sync + stats refresh."""
    def _run():
        try:
            log.info("Consolidación iniciada...")
            from core.colony_chroma import get_chroma_memory
            chroma = get_chroma_memory()
            log.info("ChromaDB antes: %d vectores", chroma.count())
            _sync_chroma_bg(force=True)
            time.sleep(5)
            chroma = get_chroma_memory()
            log.info("ChromaDB después: %d vectores", chroma.count())
            from core.knowledge_reasoner import get_reasoner
            r = get_reasoner()
            if r.is_ready():
                log.info("Graph: %d nodos, %d aristas", *r.graph.size)
        except Exception as e:
            log.error("Consolidación error: %s", e)
    t = threading.Thread(target=_run, daemon=True)
    t.start()
    return jsonify({"status": "consolidación iniciada en background"})


def _sync_chroma_bg(force: bool = False):
    """Background sync: re-sincronizacion brain.db -> ChromaDB via SUBPROCESO aislado.

    La sincronizacion carga nomic-embed-text (~3.7GB peak) — si corre in-process
    hincha el bridge y causa OOM. Se lanza como subproceso para aislar la memoria.
    """
    try:
        log.info("sync_chroma: lanzando worker aislado (subproceso)...")
        reconstruir_script = Path(__file__).resolve().parent.parent / "bin" / "reconstruir_chroma.py"
        args = [sys.executable, str(reconstruir_script)]
        if force:
            args.append("--force")
        r = subprocess.run(
            args,
            capture_output=True, text=True, timeout=300,
            env={**os.environ, "EIDOS_BRIDGE_MODE": "1"},
        )
        if r.returncode == 0:
            # Parse count from last line of stdout (script prints final count)
            for line in r.stdout.strip().splitlines():
                if "vectores" in line.lower() or "indexados" in line.lower():
                    log.info("sync_chroma: %s", line.strip())
                    break
            else:
                log.info("sync_chroma: completado (exit 0)")
        else:
            log.error("sync_chroma worker fallo (exit %d): %s", r.returncode,
                      (r.stderr or r.stdout)[:500])
    except subprocess.TimeoutExpired:
        log.error("sync_chroma: timeout (300s)")
    except Exception as e:
        log.error("sync_chroma error: %s", e)


def _auto_study_on_fail(query: str):
    """Auto-study when reasoner finds nothing — learn from ignorance."""
    try:
        from core.self_study import get_self_study
        ss = get_self_study()
        result = ss.study_topic(query)
        if result.get("studied", 0) > 0:
            log.info("Auto-study from failed reason: '%s' → studied", query[:40])
    except Exception as e:
        log.error("Auto-study on fail error: %s", e)


@app.route("/ask_brain", methods=["POST"])
def ask_brain():
    """Consultar el brain.db directamente (sin Ollama)."""
    data = request.get_json(force=True, silent=True) or {}
    query = str(data.get("query", "")).strip()
    limit = int(data.get("limit", 5))

    if not query:
        return jsonify({"error": "query vacío"}), 400

    try:
        import sqlite3
        db_path = Path.home() / ".eidos" / "evolution_brain.db"
        con = get_conn(db_path)
        # Búsqueda por LIKE en topic y content
        rows = con.execute(
            "SELECT concept, definition, source FROM knowledge_nodes "
            "WHERE concept LIKE ? OR definition LIKE ? LIMIT ?",
            (f"%{query}%", f"%{query}%", limit)
        ).fetchall()
        con.close()
        return jsonify({
            "query": query,
            "results": [{"concept": r[0], "definition": r[1][:300], "source": r[2]}
                        for r in rows],
        })
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/scan", methods=["POST", "GET"])
def scan_screen():
    """
    Escanea ventanas abiertas e indexa en brain.db.
    POST {"vision": true}  → además captura + moondream (~20s)
    GET                    → escaneo rápido sin visión (<0.1s)
    """
    data = request.get_json(force=True, silent=True) or {}
    use_vision = bool(data.get("vision", False))
    try:
        from core.screen_scanner import scan_windows
        result = scan_windows(use_vision=use_vision)
        return jsonify(result)
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/investiga", methods=["POST"])
def investiga_app():
    """
    EIDOS investiga una app: --help, man, kali.org, DuckDuckGo.
    POST {"app": "nmap", "deep": true}
    """
    data = request.get_json(force=True, silent=True) or {}
    app_name = str(data.get("app", "")).strip()
    deep = bool(data.get("deep", False))
    if not app_name:
        return jsonify({"error": "Indica app"}), 400
    try:
        from core.screen_scanner import research_app
        result = research_app(app_name, deep=deep)
        return jsonify(result)
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/gui", methods=["POST"])
def gui_action():
    """
    EIDOS ejecuta una acción GUI autónomamente.
    POST {"action": "open zenmap"} | {"action": "click 85% 5%"} | etc.
    """
    data = request.get_json(force=True, silent=True) or {}
    action = str(data.get("action", "")).strip()
    if not action:
        return jsonify({"error": "Indica action"}), 400
    try:
        from core.gui_automator import execute_gui_command
        result = execute_gui_command(action)
        return jsonify(result)
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/find_docs", methods=["POST"])
def find_docs_endpoint():
    """
    EIDOS busca y aprende documentación de cualquier tema de forma autónoma.
    POST {"topic": "telegram bot api", "url": "https://..."}  (url opcional)
    """
    data = request.get_json(force=True, silent=True) or {}
    topic = str(data.get("topic", "")).strip()
    url   = str(data.get("url", "")).strip()
    if not topic:
        return jsonify({"error": "Indica topic"}), 400
    try:
        from core.screen_scanner import find_docs
        result = find_docs(topic, official_url=url)
        return jsonify(result)
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/self_analyze", methods=["GET"])
def self_analyze():
    """
    EIDOS analiza su propio cuerpo: módulos, imports, tests, estado real.
    GET /self_analyze
    """
    import importlib, traceback
    from pathlib import Path as P

    eidos_root = P.home() / "EIDOS"
    core_files = sorted((eidos_root / "core").glob("*.py"))
    result = {"total_modules": 0, "ok": [], "failing": [], "stats": {}}

    for f in core_files:
        mod_name = f"core.{f.stem}"
        try:
            spec = importlib.util.find_spec(mod_name)
            if spec is None:
                result["failing"].append({"module": mod_name, "error": "no spec"})
                continue
            mod = importlib.import_module(mod_name)
            funcs = [n for n in dir(mod) if not n.startswith("_") and callable(getattr(mod, n))]
            result["ok"].append({"module": mod_name, "functions": len(funcs)})
        except Exception as e:
            result["failing"].append({"module": mod_name, "error": str(e)[:120]})
        result["total_modules"] += 1

    # Estadísticas del brain.db
    try:
        import sqlite3
        db = P.home() / ".eidos" / "brain.db"
        c = get_conn(db, timeout=2)
        total_nodes = c.execute("SELECT COUNT(*) FROM knowledge_nodes").fetchone()[0]
        categories  = c.execute(
            "SELECT source, COUNT(*) FROM knowledge_nodes GROUP BY source ORDER BY COUNT(*) DESC LIMIT 10"
        ).fetchall()
        c.close()
        result["stats"]["brain_nodes"] = total_nodes
        result["stats"]["top_sources"] = [{"source": s, "count": n} for s, n in categories]
    except Exception as e:
        result["stats"]["brain_error"] = str(e)

    # Líneas de código
    try:
        total_lines = sum(
            len(f.read_text(errors="ignore").splitlines())
            for f in eidos_root.rglob("*.py")
            if "__pycache__" not in str(f)
        )
        result["stats"]["total_python_lines"] = total_lines
        result["stats"]["core_modules_ok"]    = len(result["ok"])
        result["stats"]["core_modules_fail"]  = len(result["failing"])
    except Exception:
        pass

    return jsonify(result)


@app.route("/vision", methods=["POST"])
def vision_endpoint():
    """
    EIDOS analiza una captura de pantalla con llama3.2-vision.
    POST {"question": "qué botones hay?", "wid": 12345, "fast": false}
    """
    data = request.get_json(force=True, silent=True) or {}
    question = str(data.get("question", "¿Qué ves en la pantalla?")).strip()
    wid = data.get("wid")
    fast = bool(data.get("fast", False))
    try:
        from core.vision_loop import see_screen
        result = see_screen(question, wid=wid, fast=fast)
        return jsonify(result)
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/browse", methods=["POST"])
def browse_endpoint():
    """
    EIDOS navega a una URL y extrae el contenido.
    POST {"url": "https://...", "use_session": false, "query": "buscar algo"}
    """
    data = request.get_json(force=True, silent=True) or {}
    url = str(data.get("url", "")).strip()
    query = str(data.get("query", "")).strip()
    use_session = bool(data.get("use_session", False))
    try:
        from core.browser_manager import navigate, navigate_with_session, search_and_learn
        if query and not url:
            result = search_and_learn(query)
        elif use_session:
            result = navigate_with_session(url)
        else:
            result = navigate(url)
        return jsonify(result)
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/sandbox", methods=["POST"])
def sandbox_endpoint():
    """
    EIDOS ejecuta código en un entorno aislado.
    POST {"code": "print('hola')", "env": "venv|docker|namespace", "setup": false}
    POST {"action": "detect_envs"|"study_self"|"learn_tool", "tool": "docker"}
    """
    data = request.get_json(force=True, silent=True) or {}
    action = str(data.get("action", "run")).strip()
    try:
        from core.self_sandbox import (run_in_venv, run_in_docker, run_in_namespace,
                                        detect_environments, study_self_archive, learn_tool_help,
                                        setup_docker_sandbox)
        if action == "detect_envs":
            return jsonify(detect_environments())
        elif action == "study_self":
            return jsonify(study_self_archive())
        elif action == "learn_tool":
            tool = str(data.get("tool", "")).strip()
            if not tool:
                return jsonify({"error": "Indica tool"}), 400
            return jsonify(learn_tool_help(tool))
        elif action == "setup_docker":
            return jsonify(setup_docker_sandbox())
        else:  # run
            code = str(data.get("code", "")).strip()
            env = str(data.get("env", "venv")).strip()
            if not code:
                return jsonify({"error": "Indica code"}), 400
            if env == "docker":
                return jsonify(run_in_docker(code))
            elif env == "namespace":
                return jsonify(run_in_namespace(code))
            else:
                return jsonify(run_in_venv(code))
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/monitor", methods=["GET"])
def monitor_status():
    """Estado del monitor WebSocket en tiempo real (:8004)."""
    try:
        from core.realtime_monitor import get_status, start_monitor_thread
        start_monitor_thread()  # Arranca si no está activo
        return jsonify(get_status())
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/reason", methods=["POST"])
def reason_endpoint():
    """Razonamiento semántico multinivel sobre brain.db.
    
    POST {"query": "qué es docker?", "max_results": 6}
    Devuelve respuesta con hits directos + expandidos + inferidos.
    """
    data = request.get_json(force=True, silent=True) or {}
    query = str(data.get("query", "")).strip()
    max_results = int(data.get("max_results", 6))
    if not query:
        return jsonify({"error": "query vacío"}), 400
    try:
        from core.knowledge_reasoner import get_reasoner
        r = get_reasoner()
        result = r.reason(query, max_results=max_results)
        # Auto-study if reasoner found nothing (learn from ignorance)
        if result.get("direct_hits", 0) == 0 and result.get("inferred", 0) == 0 and result.get("ready"):
            t = threading.Thread(target=_auto_study_on_fail, args=(query,), daemon=True)
            t.start()
        return jsonify(result)
    except Exception as e:
        log.exception("reason error")
        return jsonify({"error": str(e)}), 500


@app.route("/evolve", methods=["POST"])
def evolve_endpoint():
    """Ciclo de evolución: infiere nuevo conocimiento desde el grafo semántico.
    
    POST {"force_rebuild": false}
    Analiza pares de conceptos, infiere relaciones, persiste como nodos.
    """
    data = request.get_json(force=True, silent=True) or {}
    force = bool(data.get("force_rebuild", False))
    try:
        from core.knowledge_reasoner import get_reasoner
        r = get_reasoner(force_rebuild=force)
        result = r.evolve()
        # Sync new reasoned nodes to ChromaDB in background
        if result.get("persisted", 0) > 0:
            t = threading.Thread(target=_sync_chroma_bg, daemon=True)
            t.start()
        return jsonify(result)
    except Exception as e:
        log.exception("evolve error")
        return jsonify({"error": str(e)}), 500


@app.route("/improve", methods=["POST"])
def improve_endpoint():
    """Auto-mejora: analiza y mejora un archivo de EIDOS.
    
    POST {"file": "core/some_file.py", "analysis_only": true}
    """
    data = request.get_json(force=True, silent=True) or {}
    file_path = str(data.get("file", "")).strip()
    analysis_only = bool(data.get("analysis_only", False))
    if not file_path:
        return jsonify({"error": "Indica file path relativo a EIDOS"}), 400
    try:
        abs_path = Path.home() / "EIDOS" / file_path
        if not abs_path.exists():
            return jsonify({"error": f"Archivo no encontrado: {abs_path}"}), 404
        from core.eidos_self_improvement import get_self_improvement
        si = get_self_improvement()
        analysis = si.analyze_file(str(abs_path))
        if analysis_only:
            return jsonify({"analysis": analysis})
        result = si.improve_code(str(abs_path))
        # Inject knowledge about this improvement
        try:
            from core.colony_proactive import push_message
            push_message(
                actor="AI-Assistant",
                message=f"Auto-mejora aplicada en {file_path}: {analysis.get('summary','')[:200]}",
                topic=f"self_improvement:{file_path}",
                priority=6,
            )
        except Exception:
            pass
        return jsonify(result)
    except Exception as e:
        log.exception("improve error")
        return jsonify({"error": str(e)}), 500


@app.route("/self_study/gaps", methods=["GET"])
def self_study_gaps():
    """Detecta gaps de conocimiento (temas con pocos nodos en brain.db)."""
    try:
        from core.self_study import get_self_study
        ss = get_self_study()
        gaps = ss.detect_gaps(n_samples=20)
        return jsonify({"gaps": gaps, "count": len(gaps)})
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/self_study/study", methods=["POST"])
def self_study_study():
    """Estudia un tema específico para llenar un gap.
    
    POST {"topic": "docker", "url": "https://..."}  (url opcional)
    """
    data = request.get_json(force=True, silent=True) or {}
    topic = str(data.get("topic", "")).strip()
    url = str(data.get("url", "")).strip()
    if not topic:
        return jsonify({"error": "Indica topic"}), 400
    try:
        from core.self_study import get_self_study
        ss = get_self_study()
        result = ss.study_topic(topic, url=url)
        return jsonify(result)
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/self_study/cycle", methods=["POST"])
def self_study_cycle():
    """Ejecuta un ciclo completo de auto-estudio (detecta gaps + estudia)."""
    try:
        from core.self_study import get_self_study
        ss = get_self_study()
        result = ss.run_cycle(max_topics=3)
        return jsonify(result)
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/self_study/start", methods=["POST"])
def self_study_start():
    """Inicia el daemon de auto-estudio en background (cada 10 min)."""
    try:
        from core.self_study import get_self_study
        ss = get_self_study()
        ss.start_auto_study(interval=600)
        return jsonify({"status": "started", "message": "Auto-estudio cada 10 minutos"})
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/evolve/daemon/start", methods=["POST"])
def evolve_daemon_start():
    """Inicia el daemon de auto-evolución en background."""
    try:
        from core.auto_evolve_daemon import start_evolve_daemon
        d = start_evolve_daemon()
        return jsonify({"status": "started", "info": str(d.get_status())})
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/evolve/daemon/status", methods=["GET"])
def evolve_daemon_status():
    """Estado del daemon de auto-evolución."""
    try:
        from core.auto_evolve_daemon import get_evolve_daemon
        d = get_evolve_daemon()
        return jsonify(d.get_status())
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/graph/stats", methods=["GET"])
def graph_stats():
    """Estadísticas del grafo semántico de conocimiento."""
    try:
        from core.knowledge_reasoner import get_reasoner
        r = get_reasoner()
        return jsonify(r.get_stats())
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/graph/who_rebuilds", methods=["GET"])
def graph_who_rebuilds():
    """S68-P2: lista de últimos intentos de rebuild del grafo con stack trace.
    Útil para detectar quién está disparando builds en bucle.
    ?n=20 para ajustar nº de entradas (default 20)."""
    try:
        from core.knowledge_reasoner import get_reasoner
        n = int(request.args.get("n", 20))
        r = get_reasoner()
        if not hasattr(r, "get_recent_build_calls"):
            return jsonify({"error": "reasoner sin tracking — actualizar knowledge_reasoner.py"}), 503
        calls = r.get_recent_build_calls(n)
        # Compactar stack para JSON legible
        for c in calls:
            if isinstance(c.get("stack"), list):
                c["stack"] = [s.strip().split("\n")[0] for s in c["stack"]]
        # Resumen
        total_skipped = sum(1 for c in calls if c.get("skipped"))
        return jsonify({
            "total_recent": len(calls),
            "skipped_count": total_skipped,
            "executed_count": len(calls) - total_skipped,
            "calls": calls,
        })
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/graph/visualize", methods=["GET"])
def graph_visualize():
    """Grafo completo para visualización D3.js."""
    try:
        from core.knowledge_reasoner import get_reasoner
        r = get_reasoner()
        if not r.is_ready():
            return jsonify({"ready": False, "building": r._building}), 200
        max_nodes = int(request.args.get("max_nodes", 200))
        min_conf = float(request.args.get("min_confidence", 0.0))
        category = request.args.get("category", "")
        nodes_out = []
        edges_out = []
        ids_included: Set[str] = set()
        for nid, node in list(r.graph.nodes.items()):
            if len(ids_included) >= max_nodes:
                break
            if node.confidence < min_conf:
                continue
            if category and node.category != category:
                continue
            ids_included.add(nid)
            nodes_out.append({
                "id": nid, "concept": node.concept,
                "category": node.category, "confidence": node.confidence,
                "source": node.source,
            })
        for edge in r.graph.edges:
            if edge.source_id in ids_included and edge.target_id in ids_included:
                edges_out.append({
                    "source": edge.source_id, "target": edge.target_id,
                    "type": edge.rel_type, "weight": edge.weight,
                })
        return jsonify({"ready": True, "nodes": nodes_out, "edges": edges_out})
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/graph/search", methods=["POST"])
def graph_search():
    """Búsqueda semántica profunda en el grafo de conocimiento.
    
    POST {"query": "docker", "max_depth": 2, "max_results": 10}
    Devuelve conceptos relacionados jerárquicamente.
    """
    data = request.get_json(force=True, silent=True) or {}
    query = str(data.get("query", "")).strip()
    max_depth = int(data.get("max_depth", 2))
    max_results = int(data.get("max_results", 10))
    if not query:
        return jsonify({"error": "query vacío"}), 400
    try:
        from core.knowledge_reasoner import get_reasoner
        r = get_reasoner()
        # Buscar hits directos
        hits = r.graph.find_similar_concepts(query, top_k=5)
        results = []
        seen = set()
        for node, score in hits:
            if node.id not in seen:
                seen.add(node.id)
                results.append({
                    "concept": node.concept,
                    "definition": node.definition[:200],
                    "category": node.category,
                    "source": node.source,
                    "confidence": node.confidence,
                    "score": round(score, 3),
                })
            # BFS relacionados
            related = r.graph.get_related(node.id, max_depth=max_depth)
            for rel_node, rel_type, rel_weight, depth in related:
                if rel_node.id not in seen and len(results) < max_results:
                    seen.add(rel_node.id)
                    results.append({
                        "concept": rel_node.concept,
                        "definition": rel_node.definition[:150],
                        "relation": rel_type,
                        "relation_depth": depth,
                        "score": round(score * rel_weight, 3),
                    })
        return jsonify({
            "query": query,
            "results": results[:max_results],
            "count": min(len(results), max_results),
        })
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/time", methods=["GET"])
def time_context():
    """EIDOS sabe qué hora es y qué debería hacer ahora."""
    try:
        from core.time_awareness import get_time_context, get_next_tasks, get_weekly_plan
        ctx = get_time_context()
        ctx["next_tasks"] = get_next_tasks()[:5]
        ctx["weekly_plan"] = get_weekly_plan()
        return jsonify(ctx)
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/night_cycle", methods=["POST"])
def night_cycle_endpoint():
    """Ejecuta el ciclo nocturno de EIDOS manualmente."""
    try:
        from core.night_cycle import run_night_cycle
        result = run_night_cycle()
        return jsonify(result)
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/business_intel", methods=["POST"])
def business_intel_endpoint():
    """EIDOS genera ideas de negocio y las presenta a SER."""
    try:
        from core.business_intel import run_business_intelligence
        result = run_business_intelligence()
        return jsonify(result)
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/clone", methods=["POST"])
def clone_endpoint():
    """
    Gestión del clon EIDOS en S@NDBOX_EIDOS.
    POST {"action": "status|diff|setup|run|improve|test"}
    """
    data = request.get_json(force=True, silent=True) or {}
    action = str(data.get("action", "status"))
    try:
        from core.clone_manager import (
            get_status, diff_with_clone, setup_clone_environment,
            run_in_clone, improve_clone_file, test_clone,
            command_clone, explore_clone, _reset_clone
        )
        if action == "status":
            return jsonify(get_status())
        elif action == "diff":
            fp = data.get("file_path")
            return jsonify(diff_with_clone(fp))
        elif action == "setup":
            return jsonify(setup_clone_environment())
        elif action == "run":
            code = data.get("code", "print('hello from clone')")
            return jsonify(run_in_clone(code))
        elif action == "command":
            # EIDOS ordena al clon como SER — mando total sin restricciones
            instruction = data.get("instruction", "print('clon listo')")
            timeout     = int(data.get("timeout", 60))
            return jsonify(command_clone(instruction, timeout))
        elif action == "explore":
            path = data.get("path", "core")
            return jsonify(explore_clone(path))
        elif action == "reset":
            return jsonify(_reset_clone())
        elif action == "improve":
            fp   = data.get("file_path", "")
            cont = data.get("content", "")
            return jsonify(improve_clone_file(fp, cont))
        elif action == "test":
            cmd = data.get("cmd")
            return jsonify(test_clone(cmd))
        else:
            return jsonify({"error": f"Acción desconocida: {action}"}), 400
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/curriculum", methods=["GET", "POST"])
def curriculum_endpoint():
    """
    Curriculums de aprendizaje de EIDOS.
    GET  → progreso actual
    POST {"action": "load_all|load_sqli|load_flags|load_langs|next_lesson"}
    """
    try:
        from core.knowledge_curriculum import (
            load_all_curriculums, load_curriculum_to_brain,
            get_curriculum_progress, get_next_lesson,
            SQLI_CURRICULUM, BROWSER_FLAGS_CURRICULUM, LANGUAGES_CURRICULUM
        )
        if request.method == "GET":
            return jsonify(get_curriculum_progress())

        data   = request.get_json(force=True, silent=True) or {}
        action = str(data.get("action", "progress"))

        if action == "load_all":
            return jsonify(load_all_curriculums())
        elif action == "load_sqli":
            return jsonify(load_curriculum_to_brain(SQLI_CURRICULUM))
        elif action == "load_flags":
            return jsonify(load_curriculum_to_brain(BROWSER_FLAGS_CURRICULUM))
        elif action == "load_langs":
            return jsonify(load_curriculum_to_brain(LANGUAGES_CURRICULUM))
        elif action == "next_lesson":
            cat = data.get("category")
            return jsonify(get_next_lesson(cat) or {"done": True, "message": "Todo aprendido"})
        else:
            return jsonify(get_curriculum_progress())
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/library", methods=["POST"])
def library_endpoint():
    """
    Z-Library: descargar y aprender libros.
    POST {"action": "read|discover|priority_books"}
    """
    data = request.get_json(force=True, silent=True) or {}
    action = str(data.get("action", "priority_books"))
    try:
        from core.library_reader import read_book, read_priority_books, discover_books
        if action == "read":
            url   = data.get("url", "")
            title = data.get("title", "Libro")
            cat   = data.get("category", "general")
            if not url:
                return jsonify({"error": "url requerida"}), 400
            return jsonify(read_book(url, title, cat))
        elif action == "discover":
            topic = data.get("topic")
            return jsonify({"books": discover_books(topic)})
        elif action == "priority_books":
            return jsonify({"results": read_priority_books()})
        else:
            return jsonify({"error": f"Acción desconocida: {action}"}), 400
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/renew_telegram", methods=["POST"])
def renew_telegram():
    """
    Verifica y renueva token de Telegram via BotFather con xdotool.
    POST {"token": "current_token_or_empty"}
    """
    data = request.get_json(force=True, silent=True) or {}
    current_token = str(data.get("token", "")).strip()
    if not current_token:
        # Intentar cargar desde .env
        env_path = Path.home() / ".eidos" / ".env"
        if env_path.exists():
            for line in env_path.read_text().splitlines():
                if line.startswith("TELEGRAM_BOT_TOKEN="):
                    current_token = line.split("=", 1)[1].strip()
                    break
    try:
        from core.screen_scanner import auto_renew_telegram
        result = auto_renew_telegram(current_token)
        return jsonify(result)
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/learn_doc", methods=["POST"])
def learn_doc_endpoint():
    """
    EIDOS aprende de cualquier documento/repositorio.
    POST {"type": "pdf|zip|github|file|directory", "path": "...", "name": "...", "url": "..."}
    Tipos:
      pdf       → {"path": "/ruta/a/libro.pdf", "name": "Título", "category": "security"}
      zip       → {"path": "/ruta/a/docs.zip", "name": "Nombre"}
      github    → {"url": "https://github.com/user/repo", "max_files": 50}
      file      → {"path": "/ruta/a/archivo.py|.md|.txt"}
      directory → {"path": "/ruta/a/directorio", "recursive": true}
    """
    data = request.get_json(force=True, silent=True) or {}
    doc_type = str(data.get("type", "file")).strip().lower()
    path     = str(data.get("path", "")).strip()
    url      = str(data.get("url",  "")).strip()
    name     = str(data.get("name", "")).strip()
    category = str(data.get("category", "document")).strip()
    max_files = int(data.get("max_files", 100))
    recursive = bool(data.get("recursive", True))

    def _run():
        try:
            from core.document_learner import get_learner
            dl = get_learner()
            if doc_type == "pdf":
                result = dl.learn_pdf(path, name, category)
            elif doc_type == "zip":
                result = dl.learn_zip(path, name, category, max_files)
            elif doc_type == "github":
                result = dl.learn_github(url or path, max_files)
            elif doc_type == "directory":
                result = dl.learn_directory(path, name, recursive, max_files)
            else:
                result = dl.learn_file(path, name, category)
            log.info("learn_doc %s: %s", doc_type, result)
        except Exception as e:
            log.error("learn_doc error: %s", e)

    t = threading.Thread(target=_run, daemon=True)
    t.start()

    target = url or path
    return jsonify({
        "status": "started",
        "type": doc_type,
        "target": target,
        "message": f"Aprendiendo {doc_type}: {target}. Consulta /brain en unos minutos."
    })


@app.route("/exhaustive_study", methods=["POST"])
def exhaustive_study_endpoint():
    """
    Lanza el motor de estudio exhaustivo completo (4 capas: markdown→BS4→Playwright→xdotool).
    POST {"url": "https://...", "depth": 10}
    """
    data = request.get_json(force=True, silent=True) or {}
    url   = str(data.get("url", "")).strip()
    depth = int(data.get("depth", 10))

    if not url:
        return jsonify({"error": "Indica url"}), 400

    def _run():
        try:
            import subprocess
            subprocess.Popen(
                ["python3", "scripts/estudio_exhaustivo.py", url, "--depth", str(depth)],
                cwd="/home/ser/EIDOS",
                env={**os.environ, "DISPLAY": ":0"},
                stdout=open(os.path.expanduser("~/.eidos/logs/estudio_exhaustivo.log"), "a"),
                stderr=subprocess.STDOUT,
            )
        except Exception as e:
            log.error("exhaustive_study error: %s", e)

    t = threading.Thread(target=_run, daemon=True)
    t.start()

    return jsonify({
        "status": "started",
        "url": url,
        "message": f"Estudio exhaustivo lanzado para {url}. Motor: markdown→BS4→Playwright→xdotool."
    })


@app.route("/analyze_self", methods=["POST"])
def analyze_self():
    """EIDOS analiza su propio código fuente y crea conocimiento estructurado.
    
    POST {"force": false}
    Crea nodos para cada módulo, clase y función en core/.
    """
    data = request.get_json(force=True, silent=True) or {}
    force = bool(data.get("force", False))
    try:
        from core.self_code_analyzer import get_code_analyzer
        ca = get_code_analyzer()
        result = ca.analyze_all(force=force)
        return jsonify(result)
    except Exception as e:
        log.exception("analyze_self error")
        return jsonify({"error": str(e)}), 500


@app.route("/analyze_self/stats", methods=["GET"])
def analyze_self_stats():
    """Estadísticas del análisis de código."""
    try:
        from core.self_code_analyzer import get_code_analyzer
        return jsonify(get_code_analyzer().get_stats())
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/auto_learn", methods=["POST"])
def auto_learn():
    """Aprendizaje autónomo: busca documentación web y extrae conocimiento.
    
    POST {"topic": "kubernetes", "depth": 1, "max_pages": 3}
    """
    data = request.get_json(force=True, silent=True) or {}
    topic = str(data.get("topic", "")).strip()
    depth = int(data.get("depth", 1))
    max_pages = int(data.get("max_pages", 3))
    if not topic:
        return jsonify({"error": "topic requerido"}), 400
    try:
        from core.auto_learner import get_auto_learner
        al = get_auto_learner()
        result = al.learn_topic(topic, depth=depth, max_pages=max_pages)
        return jsonify(result)
    except Exception as e:
        log.exception("auto_learn error")
        return jsonify({"error": str(e)}), 500


@app.route("/auto_learn/stats", methods=["GET"])
def auto_learn_stats():
    """Estadísticas del aprendizaje autónomo."""
    try:
        from core.auto_learner import get_auto_learner
        return jsonify(get_auto_learner().get_stats())
    except Exception as e:
        return jsonify({"error": str(e)}), 500


# ── Model Router (nodo ligero/pesado) ────────────────────────────────────────

@app.route("/router/status", methods=["GET"])
def router_status():
    """Estado del router de modelo ligero/pesado."""
    try:
        from core.model_router import get_router
        return jsonify(get_router().get_status())
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/router/ask", methods=["POST"])
def router_ask():
    """Enruta una consulta al nodo adecuado (ligero local o pesado remoto).
    
    POST {"message": "...", "context": [...]}
    Devuelve respuesta del nodo seleccionado.
    """
    data = request.get_json(force=True, silent=True) or {}
    message = str(data.get("message", "")).strip()
    context = data.get("context", None)
    if not message:
        return jsonify({"error": "message requerido"}), 400
    try:
        from core.model_router import get_router
        result = get_router().route(message, context=context)
        return jsonify(result)
    except Exception as e:
        return jsonify({"error": str(e)}), 500


# ── EIDOS Vivo (corazón autónomo) ────────────────────────────────────────────

@app.route("/vivo/status", methods=["GET"])
def vivo_status():
    """Estado del ciclo vital de EIDOS."""
    try:
        from core.eidos_vivo import get_vivo
        return jsonify(get_vivo().get_status())
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/vivo/start", methods=["POST"])
def vivo_start():
    """Inicia el ciclo vital de EIDOS."""
    try:
        from core.eidos_vivo import get_vivo
        v = get_vivo()
        v.start()
        return jsonify({"status": "started", "cycle": v._cycle_count})
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/vivo/stop", methods=["POST"])
def vivo_stop():
    """Detiene el ciclo vital de EIDOS."""
    try:
        from core.eidos_vivo import get_vivo
        get_vivo().stop()
        return jsonify({"status": "stopped"})
    except Exception as e:
        return jsonify({"error": str(e)}), 500


# ── Neural Dashboard ──────────────────────────────────────────────────────────

@app.route("/dashboard", methods=["GET"])
def neural_dashboard():
    """Dashboard interactivo D3.js del grafo neuronal."""
    dashboard_path = Path(__file__).parent.parent / "web-panel" / "static" / "neural_dashboard.html"
    if dashboard_path.exists():
        return dashboard_path.read_text(encoding="utf-8"), 200, {"Content-Type": "text/html; charset=utf-8"}
    return jsonify({"error": "dashboard HTML no encontrado"}), 404


@app.route("/panel", methods=["GET"])
def monitor_panel():
    """Panel de monitoreo en tiempo real de EIDOS."""
    monitor_path = Path(__file__).parent.parent / "web-panel" / "static" / "monitor.html"
    if monitor_path.exists():
        return monitor_path.read_text(encoding="utf-8"), 200, {"Content-Type": "text/html; charset=utf-8"}
    return jsonify({"error": "monitor HTML no encontrado"}), 404


# ── Self-Test ────────────────────────────────────────────────────────────────

# ── Self-Healing ─────────────────────────────────────────────────────────────

# ── S82 · Identity & Dashboard ───────────────────────────────────────────────

@app.route("/identity/dashboard", methods=["GET"])
def identity_dashboard():
    """Dashboard completo de identidad, independencia y salud en JSON."""
    try:
        from core.eidos_dashboard import get_dashboard_data
        return jsonify(get_dashboard_data())
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/identity/independence", methods=["GET"])
def identity_independence():
    """Métrica de independencia (distilled/total*100). Fórmula original de SER."""
    try:
        from core.eidos_identity import get_identity
        ind = get_identity().independence()
        return jsonify(ind)
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/identity/whoami", methods=["GET"])
def identity_whoami():
    """¿Quién es EIDOS? Descripción textual de identidad."""
    try:
        from core.eidos_identity import get_identity
        return jsonify({"text": get_identity().whoami(), "format": "markdown"})
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/identity/soul", methods=["GET"])
def identity_soul():
    """Soul snapshot — estado completo del alma de EIDOS."""
    try:
        from core.eidos_identity import get_identity
        return jsonify(get_identity().soul_snapshot())
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/identity", methods=["GET"])
def identity_page():
    """Página HTML con la métrica de independencia en vivo."""
    try:
        from core.eidos_dashboard import get_independence_html
        return get_independence_html(), 200, {"Content-Type": "text/html; charset=utf-8"}
    except Exception as e:
        return jsonify({"error": str(e)}), 500


# ── Self-Healing ─────────────────────────────────────────────────────────────

@app.route("/heal/status", methods=["GET"])
def heal_status():
    """Estado del sistema de auto-sanación."""
    try:
        from core.self_healing import get_healer
        return jsonify(get_healer().get_status())
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/heal/run", methods=["POST"])
def heal_run():
    """Ejecuta un ciclo de sanación inmediato."""
    try:
        from core.self_healing import get_healer
        results = get_healer().heal_cycle()
        return jsonify({"results": results, "count": len(results)})
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/heal/start", methods=["POST"])
def heal_start():
    """Inicia el daemon de auto-sanación."""
    try:
        from core.self_healing import get_healer
        interval = int(request.args.get("interval", 30))
        get_healer().start(interval=interval)
        return jsonify({"status": "started", "interval": interval})
    except Exception as e:
        return jsonify({"error": str(e)}), 500


# ── Self-Test ────────────────────────────────────────────────────────────────

@app.route("/inbox", methods=["GET"])
def inbox_list():
    """Muestra mensajes de Colony para SER, incluyendo validaciones de conocimiento."""
    try:
        from core.ser_inbox import get_ser_inbox
        _inbox = get_ser_inbox()
        return jsonify({
            "status": "ok",
            "messages": _inbox.list_unread(),
            "pending_actions": _inbox.pending_count(),
            "display": _inbox.format_for_display(),
        })
    except Exception as e:
        return jsonify({"status": "error", "error": str(e)}), 500

@app.route("/inbox/approve/<int:msg_id>", methods=["POST"])
def inbox_approve(msg_id: int):
    """SER aprueba o confirma un mensaje de Colony."""
    try:
        from core.ser_inbox import get_ser_inbox
        ok = get_ser_inbox().approve(msg_id)
        return jsonify({"status": "ok" if ok else "not_found", "approved": ok})
    except Exception as e:
        return jsonify({"status": "error", "error": str(e)}), 500

@app.route("/inbox/reject/<int:msg_id>", methods=["POST"])
def inbox_reject(msg_id: int):
    """SER rechaza o corrige un mensaje de Colony."""
    try:
        data = request.get_json(silent=True) or {}
        reason = data.get("reason", "")
        from core.ser_inbox import get_ser_inbox
        ok = get_ser_inbox().reject(msg_id, reason)
        return jsonify({"status": "ok" if ok else "not_found", "rejected": ok})
    except Exception as e:
        return jsonify({"status": "error", "error": str(e)}), 500

@app.route("/self_test", methods=["GET"])
def self_test():
    """Auto-diagnóstico completo de EIDOS."""
    results = []
    errors = []
    try:
        from core.knowledge_reasoner import get_reasoner
        r = get_reasoner()
        if r.is_ready():
            n, e = r.graph.size
            results.append(f"✅ Graph: {n}n/{e}e")
        else:
            results.append(f"⚠️ Graph: building={r._building}")
    except Exception as ex:
        errors.append(f"Graph: {ex}")
    try:
        from core.colony_chroma import get_chroma_memory
        c = get_chroma_memory()
        results.append(f"✅ ChromaDB: {c.count()} vec" if c.is_ready() else "⚠️ ChromaDB: not ready")
    except Exception as ex:
        errors.append(f"ChromaDB: {ex}")
    try:
        from core.auto_evolve_daemon import get_evolve_daemon
        d = get_evolve_daemon()
        results.append(f"✅ Daemon: running={d._running}" if d._running else "⚠️ Daemon: stopped")
    except Exception as ex:
        errors.append(f"Daemon: {ex}")
    try:
        from core.self_study import get_self_study
        s = get_self_study()
        results.append(f"✅ Self-study: active" if s._running else "⚠️ Self-study: inactive")
    except Exception as ex:
        errors.append(f"Self-study: {ex}")
    try:
        import sqlite3
        conn = get_conn(Path.home() / ".eidos" / "evolution_brain.db", timeout=3)
        count = conn.execute("SELECT COUNT(*) FROM knowledge_nodes WHERE confidence >= 0.2").fetchone()[0]
        sources = conn.execute("SELECT COUNT(DISTINCT source) FROM knowledge_nodes WHERE confidence >= 0.2").fetchone()[0]

        results.append(f"✅ Brain: {count}n/{sources}src")
    except Exception as ex:
        errors.append(f"Brain DB: {ex}")
    import socket
    for name, port in [("Colony",7777),("Web",8080),("Trinity",8001)]:
        try:
            s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            s.settimeout(2)
            if s.connect_ex(('127.0.0.1', port)) == 0:
                results.append(f"✅ {name}:{port}")
            else:
                results.append(f"⚠️ {name}:{port} down")
            s.close()
        except:
            results.append(f"⚠️ {name}:{port} err")
    return jsonify({
        "status": "ok" if not errors else "degraded",
        "results": results,
        "errors": errors[:5],
        "total": len(results),
        "passed": sum(1 for r in results if r.startswith("✅")),
    })


# ── Main ─────────────────────────────────────────────────────────────────────

def run_bridge(port: int | None = None, host: str = "127.0.0.1"):
    if port is None:
        port = _bridge_port()
    if not HAS_FLASK:
        print("ERROR: flask no disponible")
        sys.exit(1)
    logging.basicConfig(level=logging.DEBUG,
                        format="%(asctime)s [bridge] %(levelname)s %(message)s")
    log.setLevel(logging.DEBUG)
    # Colony y screen_scanner también en INFO para ver segunda ronda y NEXUS
    logging.getLogger("colony").setLevel(logging.INFO)
    logging.getLogger("screen_scanner").setLevel(logging.INFO)
    # Auto-arrancar daemon de evolución y auto-estudio
    try:
        from core.auto_evolve_daemon import start_evolve_daemon
        start_evolve_daemon()
        log.info("Evolve daemon auto-started")
    except Exception as e:
        log.error("Evolve daemon auto-start error: %s", e)
    try:
        from core.self_study import get_self_study
        ss = get_self_study()
        ss.start_auto_study(interval=600)
        log.info("Self-study daemon auto-started")
    except Exception as e:
        log.error("Self-study auto-start error: %s", e)
    try:
        from core.self_healing import get_healer
        get_healer().start(interval=30)
        log.info("Self-healing daemon auto-started")
    except Exception as e:
        log.error("Self-healing auto-start error: %s", e)
    try:
        from core.eidos_vivo import start_vivo
        start_vivo()
        log.info("EIDOS Vivo auto-started — latiendo cada 10min")
    except Exception as e:
        log.error("EIDOS Vivo auto-start error: %s", e)
    try:
        from core.eidos_autonomous_loop import get_autonomous_loop
        get_autonomous_loop().start()
        log.info("Bucle autónomo auto-started — percibiendo cada 10s")
    except Exception as e:
        log.error("Bucle autónomo auto-start error: %s", e)
    # S66 · Tabula Rasa: si grafo casi vacío, escanea sistema
    try:
        from core.bootstrap_knowledge import auto_bootstrap_if_needed
        _br = auto_bootstrap_if_needed()
        if _br:
            log.info("Bootstrap check: %s", _br)
    except Exception as e:
        log.error("auto_bootstrap_if_needed error: %s", e)
    # S66 · Memoria episódica
    try:
        from core.episodic_memory import get_episodic
        _em = get_episodic()
        log.info("Episodic memory ready: %s", _em.stats().get("total_episodes", 0))
    except Exception as e:
        log.error("Episodic memory init error: %s", e)
    # S66 · EIDOS Alive Orchestrator — la chispa de vida sin LLM
    try:
        from core.eidos_alive_orchestrator import get_alive
        get_alive().start()
        log.info("EIDOS Alive Orchestrator started — viviendo sin LLM/VLM")
    except Exception as e:
        log.error("Alive Orchestrator start error: %s", e)
    print(f"[bridge_to_eidos] Escuchando en http://{host}:{port}")
    print("[bridge_to_eidos] Endpoints: /health /talk /status /brain /study /proactive /inject /episodes "
          "/ask_brain /semantic /chroma_stats /brain/neurons /sync_chroma /scan /investiga /renew_telegram "
          "/monitor /clone /curriculum /library /time /night_cycle /business_intel /reason /evolve /improve "
          "/evolve/daemon/start /evolve/daemon/status /graph/stats /graph/search /graph/visualize "
          "/self_study/gaps /self_study/study /self_study/cycle /self_study/start /auto_learn /auto_learn/stats "
          "/analyze_self /analyze_self/stats /self_test /heal/status /heal/run /heal/start /consolidate "
          "/dashboard /panel /router/status /router/ask /vivo/status /vivo/start /vivo/stop "
          "/identity /identity/dashboard /identity/independence /identity/whoami /identity/soul")

    # OOM protection: periodic GC collection to prevent bridge growing to 3.7GB
    # (MemoryMax=6G set, but Python heap can fragment without explicit GC)
    import gc
    import threading
    _gc_stop = threading.Event()
    def _periodic_gc():
        cycle = 0
        while not _gc_stop.is_set():
            _gc_stop.wait(60)  # every 60 seconds
            if _gc_stop.is_set():
                break
            cycle += 1
            collected = gc.collect()
            if cycle % 100 == 0:  # log every ~100 min
                try:
                    import psutil
                    mem = psutil.Process().memory_info()
                    rss_mb = mem.rss / (1024 * 1024)
                    log.info("GC: cycle=%d collected=%d RSS=%.0fMB", cycle, collected, rss_mb)
                except Exception:
                    log.info("GC: cycle=%d collected=%d", cycle, collected)
    _gc_thread = threading.Thread(target=_periodic_gc, daemon=True, name="bridge-gc")
    _gc_thread.start()
    log.info("Bridge GC monitor started (every 60s)")

    app.run(host=host, port=port, threaded=True, debug=False)


if __name__ == "__main__":
    run_bridge()
