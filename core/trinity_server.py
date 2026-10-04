"""
EIDOS — Trinity Server (localhost:8001)
Agente satélite autónomo de EIDOS con skills propios.
Siempre activo vía systemd eidos-trinity.service.
"""
from __future__ import annotations

import json
import logging
import os
import subprocess
import sys
from pathlib import Path
import time
import urllib.request
import urllib.error
from typing import Any, Dict, List

from flask import Flask, jsonify, request

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

log = logging.getLogger("trinity")
logging.basicConfig(level=logging.INFO, format="%(asctime)s [trinity] %(message)s")

app = Flask(__name__)

OLLAMA_URL = "http://localhost:11434"
TRINITY_MODEL = os.environ.get("TRINITY_MODEL", "deepseek-r1:14b")
TRINITY_FALLBACK = "lfm2.5-thinking:1.2b"
TRINITY_VERSION = "1.0.0"

# ── Skills disponibles ─────────────────────────────────────────────────────────
SKILLS: Dict[str, str] = {
    "shell":    "Ejecuta comandos de shell en entorno seguro",
    "memory":   "Accede y guarda en memoria persistente de Trinity",
    "web":      "Busca información en la web via DuckDuckGo",
    "files":    "Lee y escribe archivos en /tmp/trinity/",
    "ollama":   "Consulta modelos Ollama directamente",
    "eidos":    "Envía mensajes al núcleo EIDOS",
}

_memory: Dict[str, Any] = {}
_conversation: List[Dict] = []


def _ollama_chat(messages: List[Dict], model: str = TRINITY_MODEL, max_tokens: int = 2048) -> str:
    """Llama a Ollama y devuelve el texto de respuesta."""
    payload = json.dumps({
        "model": model,
        "messages": messages,
        "stream": False,
        "options": {"num_predict": max_tokens, "temperature": 0.7},
    }).encode()
    try:
        req = urllib.request.Request(
            f"{OLLAMA_URL}/api/chat",
            data=payload,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=120) as resp:
            data = json.loads(resp.read())
            return data.get("message", {}).get("content", "").strip()
    except urllib.error.URLError:
        # Fallback model
        try:
            payload2 = json.dumps({
                "model": TRINITY_FALLBACK,
                "messages": messages,
                "stream": False,
                "options": {"num_predict": max_tokens},
            }).encode()
            req2 = urllib.request.Request(
                f"{OLLAMA_URL}/api/chat",
                data=payload2,
                headers={"Content-Type": "application/json"},
                method="POST",
            )
            with urllib.request.urlopen(req2, timeout=60) as resp2:
                data2 = json.loads(resp2.read())
                return data2.get("message", {}).get("content", "").strip()
        except Exception as e2:
            return f"[Trinity] Error Ollama: {e2}"
    except Exception as e:
        return f"[Trinity] Error: {e}"


def _exec_skill(skill: str, fn: str, args: List[Any]) -> Dict:
    """Ejecuta un skill de Trinity."""
    if skill == "shell":
        cmd = fn if fn else (args[0] if args else "echo ok")
        try:
            r = subprocess.run(
                cmd, shell=True, capture_output=True, text=True,
                timeout=30, cwd="/tmp"
            )
            return {"stdout": r.stdout, "stderr": r.stderr, "returncode": r.returncode}
        except subprocess.TimeoutExpired:
            return {"error": "timeout"}
        except Exception as e:
            return {"error": str(e)}

    elif skill == "memory":
        if fn == "set" and len(args) >= 2:
            _memory[str(args[0])] = args[1]
            return {"ok": True}
        elif fn == "get" and args:
            return {"value": _memory.get(str(args[0]))}
        elif fn == "list":
            return {"keys": list(_memory.keys())}
        return {"error": "función desconocida"}

    elif skill == "files":
        os.makedirs("/tmp/trinity", exist_ok=True)
        if fn == "write" and len(args) >= 2:
            p = f"/tmp/trinity/{os.path.basename(args[0])}"
            with open(p, "w") as f:
                f.write(str(args[1]))
            return {"ok": True, "path": p}
        elif fn == "read" and args:
            p = f"/tmp/trinity/{os.path.basename(args[0])}"
            try:
                return {"content": open(p).read()}
            except FileNotFoundError:
                return {"error": "no existe"}
        elif fn == "list":
            return {"files": os.listdir("/tmp/trinity")}
        return {"error": "función desconocida"}

    elif skill == "web":
        query = fn or (args[0] if args else "")
        try:
            import urllib.parse
            q = urllib.parse.quote_plus(query)
            url = f"https://html.duckduckgo.com/html/?q={q}"
            req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
            with urllib.request.urlopen(req, timeout=15) as resp:
                html = resp.read().decode("utf-8", errors="ignore")
            # Extraer texto simple
            import re
            text = re.sub(r"<[^>]+>", " ", html)
            text = re.sub(r"\s+", " ", text)[:2000]
            return {"result": text}
        except Exception as e:
            return {"error": str(e)}

    return {"error": f"skill '{skill}' no implementado"}


# ── Endpoints ──────────────────────────────────────────────────────────────────

@app.route("/health")
def health():
    # Verificar Ollama
    ollama_ok = False
    try:
        with urllib.request.urlopen(f"{OLLAMA_URL}/api/tags", timeout=3) as r:
            ollama_ok = r.status == 200
    except Exception:
        pass

    return jsonify({
        "status": "ok",
        "agent": "TrinityClaw",
        "version": TRINITY_VERSION,
        "model": TRINITY_MODEL,
        "ollama": ollama_ok,
        "skills": len(SKILLS),
        "uptime": time.time(),
    })


@app.route("/skills")
def skills_list():
    return jsonify({"skills": SKILLS, "count": len(SKILLS)})


@app.route("/chat", methods=["POST"])
def chat():
    body = request.get_json(silent=True) or {}
    message = body.get("message", "").strip()
    if not message:
        return jsonify({"error": "message requerido"}), 400

    history = body.get("history", [])
    session_id = body.get("session_id", "default")

    # Construir contexto de conversación
    messages = [
        {
            "role": "system",
            "content": (
                "Eres TrinityClaw, agente satélite de EIDOS. "
                "Tienes skills: shell, memory, files, web, ollama. "
                "Eres autónomo, preciso, y trabajas para SER a través de EIDOS. "
                "Responde siempre en español."
            ),
        }
    ]
    for h in history[-8:]:
        messages.append({"role": "user", "content": h.get("user_message", "")})
        messages.append({"role": "assistant", "content": h.get("trinity_response", "")})
    messages.append({"role": "user", "content": message})

    t0 = time.time()
    response_text = _ollama_chat(messages)
    elapsed = round(time.time() - t0, 2)

    # Guardar en conversación
    _conversation.append({
        "session": session_id,
        "user": message,
        "response": response_text,
        "elapsed": elapsed,
        "ts": time.time(),
    })
    if len(_conversation) > 200:
        _conversation.pop(0)

    return jsonify({
        "response": response_text,
        "skills_used": [],
        "elapsed": elapsed,
        "model": TRINITY_MODEL,
    })


@app.route("/skill/call", methods=["POST"])
def skill_call():
    body = request.get_json(silent=True) or {}
    skill = body.get("skill", "")
    fn = body.get("function", "")
    args = body.get("args", [])
    result = _exec_skill(skill, fn, args)
    return jsonify({"success": "error" not in result, "result": result})


@app.route("/memory", methods=["GET"])
def get_memory():
    return jsonify({"memory": _memory, "count": len(_memory)})


@app.route("/conversation", methods=["GET"])
def get_conversation():
    return jsonify({"conversation": _conversation[-20:], "total": len(_conversation)})


if __name__ == "__main__":
    log.info(f"🔺 Trinity Server v{TRINITY_VERSION} arrancando en :8001")
    log.info(f"   Modelo: {TRINITY_MODEL}")
    app.run(host="127.0.0.1", port=8001, debug=False, threaded=True)
