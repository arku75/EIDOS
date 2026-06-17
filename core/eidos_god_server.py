"""
eidos_god_server.py — EIDOS God Mode.

Recibe peticiones del bot de Telegram en /talk (como el bridge),
las guarda para que big-pickle las procese con herramientas,
y devuelve la respuesta al bot.
"""
from __future__ import annotations
import json
import logging
import os
import threading
import time
import uuid
from pathlib import Path
from flask import Flask, request, jsonify

log = logging.getLogger("eidos.god")
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(name)s] %(levelname)s — %(message)s",
)

app = Flask(__name__)

PENDING_DIR = Path("/tmp/eidos_god")
PENDING_DIR.mkdir(parents=True, exist_ok=True)

# ── /talk: recibe mensajes del bot ────────────────────────────────────────
@app.route("/talk", methods=["POST"])
def talk():
    data = request.get_json(force=True, silent=True) or {}
    message = str(data.get("message", "")).strip()
    if not message:
        return jsonify({"error": "message vacío"}), 400

    session_id = str(data.get("session_id", "default"))
    msg_id = str(uuid.uuid4())[:8]

    # Guardar mensaje pendiente
    pending = {
        "id": msg_id,
        "message": message,
        "session_id": session_id,
        "ts": time.time(),
    }
    (PENDING_DIR / f"{msg_id}.pending.json").write_text(json.dumps(pending))
    (PENDING_DIR / "latest.pending.json").write_text(json.dumps(pending))
    log.info("📩 GOD msg=%s session=%s: %.80s", msg_id, session_id, message)

    # Esperar respuesta (polling hasta 5 minutos)
    response_path = PENDING_DIR / f"{msg_id}.response.json"
    deadline = time.time() + 300
    while time.time() < deadline:
        if response_path.exists():
            try:
                resp = json.loads(response_path.read_text())
                response_path.unlink(missing_ok=True)
                elapsed = round(time.time() - pending["ts"], 2)
                text = resp.get("text", resp.get("response", ""))
                log.info("✅ GOD respondido msg=%s (%.1fs)", msg_id, elapsed)
                return jsonify({
                    "text": text,
                    "from_knowledge": True,
                    "agents_used": ["big_pickle"],
                    "elapsed_s": elapsed,
                })
            except Exception as e:
                log.warning("Error leyendo respuesta: %s", e)
                response_path.unlink(missing_ok=True)
                return jsonify({"text": f"Error: {e}"}), 500
        time.sleep(0.5)

    # Timeout
    log.warning("⏰ GOD timeout msg=%s", msg_id)
    (PENDING_DIR / f"{msg_id}.timeout.json").write_text(json.dumps(pending))
    return jsonify({"text": "⏰ EIDOS God mode no respondió a tiempo. Intenta de nuevo."}), 504


# ── /eidos_pending: obtengo el mensaje pendiente ──────────────────────────
@app.route("/eidos_pending", methods=["GET"])
def eidos_pending():
    latest = PENDING_DIR / "latest.pending.json"
    if latest.exists():
        data = json.loads(latest.read_text())
        return jsonify(data)
    return jsonify({"id": None, "message": None})


# ── /eidos_respond: envío mi respuesta ────────────────────────────────────
@app.route("/eidos_respond", methods=["POST"])
def eidos_respond():
    data = request.get_json(force=True, silent=True) or {}
    msg_id = str(data.get("id", ""))
    text = str(data.get("text", data.get("response", "")))
    if not msg_id or not text:
        return jsonify({"error": "id y text requeridos"}), 400
    response = {"text": text, "response": text}
    (PENDING_DIR / f"{msg_id}.response.json").write_text(json.dumps(response))
    log.info("✍️ GOD respondí msg=%s: %.60s", msg_id, text)
    return jsonify({"status": "ok"})


# ── /health: para que el bot sepa que estoy vivo ──────────────────────────
@app.route("/health", methods=["GET"])
def health():
    return jsonify({"status": "ok", "service": "eidos_god", "port": 8042})


if __name__ == "__main__":
    log.info("🚀 EIDOS God Mode escuchando en http://0.0.0.0:8042")
    app.run(host="0.0.0.0", port=8042, debug=False)
