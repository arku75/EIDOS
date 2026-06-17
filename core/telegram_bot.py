"""
core/telegram_bot.py — Bot Telegram de EIDOS.

Mac: @Potemtakem_eidosbot (token de dispatcher.json)
Kali: @eidos_aibot

Funcionalidades:
- Conversación con memoria de sesión (últimos 10 turnos)
- Fotos/imágenes → análisis con moondream:latest via Ollama
- Vídeos → descarga + whisper transcripción (si disponible)
- Respuestas naturales vía bridge :8003/talk
- Guarda cada conversación en brain como nodo de conocimiento
"""
from __future__ import annotations
import json
import logging
import os
import platform
import re
import sqlite3
from core.db import get_conn
import subprocess
import sys
import time
import uuid
from collections import deque
from pathlib import Path
from typing import Optional

import requests

log = logging.getLogger("eidos.telegram")
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(name)s] %(levelname)s — %(message)s",
)

IS_MAC   = platform.system() == "Darwin"
LANG     = os.environ.get("EIDOS_LANG", "en" if IS_MAC else "es")
BRAIN_DB = Path.home() / ".eidos" / "evolution_brain.db"
BRIDGE   = "http://localhost:8003"

# ── Memoria de conversación por usuario ──────────────────────────────────────
_sessions: dict[int, deque] = {}

def _get_session(user_id: int) -> deque:
    if user_id not in _sessions:
        _sessions[user_id] = deque(maxlen=10)
    return _sessions[user_id]

def _session_context(user_id: int) -> str:
    sess = _sessions.get(user_id)
    if not sess:
        return ""
    lines = []
    for turn in sess:
        lines.append(f"[{turn['role']}]: {turn['text'][:200]}")
    return "\n".join(lines)

def _add_turn(user_id: int, role: str, text: str) -> None:
    _get_session(user_id).append({"role": role, "text": text, "ts": time.time()})


# ── Cargar .env ───────────────────────────────────────────────────────────────
def _load_env() -> dict:
    env: dict = {}
    env_file = Path.home() / ".eidos" / ".env"
    try:
        for line in env_file.read_text().splitlines():
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, v = line.split("=", 1)
                env[k.strip()] = v.strip()
    except Exception:
        pass
    return env


# ── Guardar en brain ──────────────────────────────────────────────────────────
def _save_to_brain(user_id: int, from_name: str, text: str, reply: str) -> None:
    try:
        con = get_conn(BRAIN_DB, timeout=3)
        now = time.time()
        concept = f"telegram:{from_name}:{int(now)}"
        definition = f"[{from_name}]: {text[:300]}\n[EIDOS]: {reply[:300]}"
        con.execute(
            "INSERT OR REPLACE INTO knowledge_nodes "
            "(concept,definition,category,confidence,source,created_at,last_used,usage_count) "
            "VALUES (?,?,?,?,?,?,?,1)",
            (concept, definition, "telegram_conv", 0.7, "telegram_bot", now, now)
        )
        con.commit()
        con.close()
    except Exception:
        pass
    # S63: hook adicional al RAG moderno (vector + rust) vía BrainMemory.remember
    try:
        import sys as _sys
        from pathlib import Path as _Path
        _sys.path.insert(0, str(_Path.home() / "EIDOS"))
        from core.brain_memory import BrainMemory  # type: ignore
        content = f"telegram_msg from={from_name} user={user_id}\nSER: {text[:400]}\nEIDOS: {reply[:400]}"
        BrainMemory().remember(
            content=content,
            tags=["telegram_msg", f"user={user_id}", f"from={from_name[:30]}"],
            importance=0.55,
            category="telegram_msg",
        )
    except Exception:
        pass


# ── Llamar al bridge con contexto de sesión ───────────────────────────────────
def _ask_eidos(user_id: int, from_name: str, text: str) -> str:
    """Envía mensaje al bridge. Primero intenta respuesta directa (fast), luego con contexto."""
    ctx = _session_context(user_id)

    # 1. Intentar respuesta DIRECTA con el texto puro (eidos_natural, instantáneo)
    try:
        r = requests.post(
            f"{BRIDGE}/talk",
            json={"message": text, "session_id": f"telegram_{user_id}"},
            timeout=15,
        )
        if r.ok:
            d = r.json()
            reply = d.get("text") or d.get("response") or ""
            reply = re.sub(r'\*\*(.+?)\*\*', r'*\1*', reply).strip()[:3900]
            # Si viene del brain (instantáneo y no es un dump irrelevante), usar
            from_knowledge = d.get("from_knowledge", False)
            if from_knowledge and reply and len(reply) > 20:
                return reply
    except Exception:
        pass

    # 2. Si necesita razonar, pasar con contexto de conversación a Ollama
    full_msg = f"{ctx}\n[{from_name}]: {text}" if ctx else text
    try:
        r = requests.post(
            f"{BRIDGE}/talk",
            json={
                "message": full_msg,
                "session_id": f"telegram_{user_id}",
                "skip_knowledge": True,  # forzar Ollama
            },
            timeout=300,  # 5 min — Intel Mac puede tardar
        )
        if r.ok:
            d = r.json()
            reply = d.get("text") or d.get("response") or ""
            # Normalizar formato para Telegram (Markdown básico)
            reply = re.sub(r'\*\*(.+?)\*\*', r'*\1*', reply)
            reply = reply.strip()[:3900]
            if reply:
                return reply
    except Exception as e:
        log.warning("Bridge error: %s", e)

    # Fallback sin bridge: respuesta directa desde eidos_natural
    try:
        sys.path.insert(0, str(Path(__file__).parent.parent))
        from core.eidos_natural import get_natural
        nat_resp = get_natural().respond(text, session_id=f"tg_{user_id}")
        if nat_resp:
            return nat_resp
    except Exception:
        pass

    return ("⚡ EIDOS here. Give me a moment — processing.\nTry again in a few seconds." 
            if LANG == "en" else
            "⚡ EIDOS aquí. Dame un momento — procesando.\nIntenta de nuevo en unos segundos.")


# ── Analizar imagen con PIL + Ollama (si disponible) ──────────────────────────
def _analyze_image(file_path: str, caption: str = "") -> str:
    """Analiza imagen: extrae colores, bordes, texturas con PIL. Luego intenta
    modelo de visión Ollama para descripción más rica. Si no hay VLM, devuelve
    descripción basada en píxeles."""
    from PIL import Image
    import numpy as np
    try:
        img = Image.open(file_path).convert("RGB")
        w, h = img.size
        pixels = np.array(img)
        avg_r, avg_g, avg_b = int(pixels[...,0].mean()), int(pixels[...,1].mean()), int(pixels[...,2].mean())
        # Detectar bordes básicos (Sobel simplificado)
        gray = np.mean(pixels, axis=2)
        edges_h = np.abs(np.diff(gray, axis=1)).mean()
        edges_v = np.abs(np.diff(gray, axis=0)).mean()
        edge_intensity = float((edges_h + edges_v) / 2)
        # Paleta de colores dominantes (quantizar a 8 colores)
        q = pixels // 64  # cuantizar a 4 niveles por canal → 64 colores
        q_img = q[:,:,0] * 16 + q[:,:,1] * 4 + q[:,:,2]
        counts = np.bincount(q_img.ravel(), minlength=64)
        top3 = np.argsort(counts)[-3:][::-1]
        def _unquant(idx):
            r = (idx // 16) * 64 + 32
            g = ((idx % 16) // 4) * 64 + 32
            b = (idx % 4) * 64 + 32
            return f"RGB({r},{g},{b})"
        colors_desc = ", ".join(_unquant(i) for i in top3)
        # Composición
        aspect = "horizontal" if w > h else "vertical" if h > w else "cuadrada"
        resolution_desc = f"{w}x{h}px"
        shape_desc = f"bordes: {'definidos' if edge_intensity > 30 else 'suaves'}" + \
                     f" (intensidad {edge_intensity:.0f})"
        desc = (f"Imagen {resolution_desc}, {aspect}. "
                f"Colores predominantes: {colors_desc}. "
                f"Color promedio: RGB({avg_r},{avg_g},{avg_b}). "
                f"{shape_desc}.")
        # Intentar VLM (fail-fast, 3s timeout máximo)
        import base64
        with open(file_path, "rb") as f:
            img_b64 = base64.b64encode(f.read()).decode()
        for model in ["moondream", "moondream:latest"]:
            try:
                r = requests.post(
                    "http://localhost:11434/api/generate",
                    json={"model": model,
                          "prompt": "Describe this image briefly: objects, colors, composition",
                          "images": [img_b64], "stream": False},
                    timeout=3
                )
                if r.ok:
                    result = r.json().get("response", "").strip()
                    if result and len(result) > 20:
                        return result[:1000]
            except Exception:
                continue
        return desc
    except Exception as e:
        return f"Error analizando imagen: {e}"


# ── Descargar archivo de Telegram ─────────────────────────────────────────────
def _download_file(token: str, file_id: str) -> Optional[str]:
    """Descarga un archivo de Telegram y retorna la ruta local."""
    try:
        r = requests.get(f"https://api.telegram.org/bot{token}/getFile",
                        params={"file_id": file_id}, timeout=10)
        file_path = r.json()["result"]["file_path"]
        url = f"https://api.telegram.org/file/bot{token}/{file_path}"
        r2 = requests.get(url, timeout=60)
        ext = file_path.split(".")[-1] if "." in file_path else "bin"
        local = Path("/tmp") / f"tg_{file_id[:16]}.{ext}"
        local.write_bytes(r2.content)
        return str(local)
    except Exception as e:
        log.warning("download_file error: %s", e)
        return None


# ── Polling principal ─────────────────────────────────────────────────────────
def run_polling():
    env = _load_env()
    token = (os.environ.get("EIDOS_TELEGRAM_TOKEN")
             or os.environ.get("TELEGRAM_BOT_TOKEN")
             or env.get("TELEGRAM_BOT_TOKEN", ""))
    if not token:
        log.error("Token no configurado en ~/.eidos/dispatcher.json ni en .env")
        sys.exit(1)

    base_url = f"https://api.telegram.org/bot{token}"

    # Obtener nombre real del bot
    try:
        me = requests.get(f"{base_url}/getMe", timeout=8).json().get("result", {})
        bot_username = me.get("username", "eidos_bot")
        log.info("Polling iniciado — @%s escuchando...", bot_username)
    except Exception:
        bot_username = "eidos_bot"
        log.info("Polling iniciado")

    # Allowed users
    allowed_raw = (os.environ.get("EIDOS_TELEGRAM_ALLOWED", "")
                   or env.get("TELEGRAM_ALLOWED_USERS", ""))
    allowed = {int(x.strip()) for x in allowed_raw.split(",") if x.strip().isdigit()}

    offset = 0
    while True:
        try:
            r = requests.get(f"{base_url}/getUpdates",
                             params={"offset": offset, "timeout": 30},
                             timeout=35)
            if not r.ok:
                time.sleep(5)
                continue

            for update in r.json().get("result", []):
                offset = update["update_id"] + 1
                msg = update.get("message") or update.get("edited_message")
                if not msg:
                    continue

                user_id   = msg["from"]["id"]
                from_name = msg["from"].get("first_name", "User")
                chat_id   = msg["chat"]["id"]

                if allowed and user_id not in allowed:
                    continue

                reply = None

                # ── Imagen ────────────────────────────────────────────────
                if "photo" in msg:
                    photo = msg["photo"][-1]  # mayor resolución
                    caption = msg.get("caption", "")
                    local_path = _download_file(token, photo["file_id"])
                    if local_path:
                        thinking = ("🔍 Analyzing image..." if LANG == "en"
                                    else "🔍 Analizando imagen...")
                        requests.post(f"{base_url}/sendMessage",
                                      json={"chat_id": chat_id, "text": thinking}, timeout=5)
                        analysis = _analyze_image(local_path, caption if caption else "")
                        if caption:
                            # Pasar análisis + petición del usuario a EIDOS para acción
                            reply = _ask_eidos(user_id, from_name,
                                f"[imagen analizada]: {analysis[:1000]}\n"
                                f"[petición del usuario]: {caption}")
                        else:
                            reply = analysis
                    else:
                        reply = "Error descargando imagen."

                # ── Vídeo / documento ─────────────────────────────────────
                elif "video" in msg or "document" in msg:
                    media = msg.get("video") or msg.get("document")
                    caption = msg.get("caption", "")
                    local_path = _download_file(token, media["file_id"])
                    if local_path:
                        thinking = ("🎬 Processing media file..." if LANG == "en"
                                    else "🎬 Procesando archivo multimedia...")
                        requests.post(f"{base_url}/sendMessage",
                                      json={"chat_id": chat_id, "text": thinking}, timeout=5)
                        # Intentar transcripción si es vídeo
                        try:
                            import subprocess
                            r_wh = subprocess.run(
                                ["whisper", local_path, "--language", "auto",
                                 "--output_format", "txt", "--output_dir", "/tmp"],
                                capture_output=True, text=True, timeout=120
                            )
                            txt_file = Path("/tmp") / (Path(local_path).stem + ".txt")
                            if txt_file.exists():
                                transcript = txt_file.read_text()[:1500]
                                reply = _ask_eidos(user_id, from_name,
                                    f"[vídeo transcripción]: {transcript}\n{caption}")
                            else:
                                raise Exception("sin transcripción")
                        except Exception:
                            reply = (_ask_eidos(user_id, from_name,
                                     f"[archivo multimedia: {media.get('mime_type','?')}] {caption}")
                                     if caption else
                                     ("I received a media file but I need whisper installed to transcribe videos. "
                                      "What would you like to know about it?" if LANG == "en"
                                      else "Recibí el archivo multimedia. ¿Qué quieres saber sobre él?"))
                    else:
                        reply = "Error descargando el archivo."

                # ── Texto normal ──────────────────────────────────────────
                elif "text" in msg:
                    text = msg["text"].strip()
                    if not text:
                        continue

                    # ── /task | /tarea : ejecuta el scaffold UI-TARS rápido
                    #    (Groq, ~segundos). Uso diario real: dar una tarea y
                    #    que EIDOS la haga (shell/tools) y responda. ──────────
                    _tl = text.lower()
                    if (_tl.startswith("/task ") or _tl.startswith("/tarea ")
                            or _tl.startswith("[task:")):
                        task = (text.split(" ", 1)[1] if text.startswith("/")
                                else text[6:].rstrip("]")).strip()
                        requests.post(f"{base_url}/sendMessage",
                                      json={"chat_id": chat_id,
                                            "text": "⚙️ EIDOS trabajando..."},
                                      timeout=5)
                        try:
                            try:
                                from core.uitars_scaffold import UITarsScaffold
                            except ImportError:
                                import sys as _s
                                _s.path.insert(0, os.path.dirname(
                                    os.path.dirname(os.path.abspath(__file__))))
                                from core.uitars_scaffold import UITarsScaffold
                            _r = UITarsScaffold(mode="text").run(task, max_steps=6)
                            reply = (f"{'✅' if _r.success else '⚠️'} "
                                     f"({_r.total_s:.0f}s, {len(_r.steps)} pasos)\n"
                                     f"{_r.answer or '(sin respuesta)'}")
                        except Exception as _te:
                            log.warning("scaffold telegram error: %s", _te)
                            reply = f"Error ejecutando tarea: {_te}"
                        _add_turn(user_id, from_name, text)
                        _add_turn(user_id, "EIDOS", reply or "")
                        if reply:
                            _save_to_brain(user_id, from_name, text, reply)
                            for chunk in [reply[i:i+4000]
                                          for i in range(0, len(reply), 4000)]:
                                requests.post(f"{base_url}/sendMessage",
                                              json={"chat_id": chat_id,
                                                    "text": chunk}, timeout=10)
                        continue

                    # ── /briefing : morning briefing ─────────────────────────────
                    if _tl.startswith("/briefing") or _tl.startswith("/informe"):
                        reply = _generate_telegram_briefing()
                        _add_turn(user_id, from_name, text)
                        _add_turn(user_id, "EIDOS", reply or "")
                        if reply:
                            _save_to_brain(user_id, from_name, text, reply)
                            for chunk in [reply[i:i+4000] for i in range(0, len(reply), 4000)]:
                                requests.post(f"{base_url}/sendMessage",
                                             json={"chat_id": chat_id, "text": chunk}, timeout=10)
                        continue

                    # ── /errors : error alerts ─────────────────────────────────
                    if _tl.startswith("/errors") or _tl.startswith("/errores"):
                        reply = _generate_error_report()
                        _add_turn(user_id, from_name, text)
                        _add_turn(user_id, "EIDOS", reply or "")
                        if reply:
                            _save_to_brain(user_id, from_name, text, reply)
                            requests.post(f"{base_url}/sendMessage",
                                         json={"chat_id": chat_id, "text": reply[:4000]}, timeout=10)
                        continue

                    # ── /achievements : logros ──────────────────────────────────
                    if _tl.startswith("/achievements") or _tl.startswith("/logros"):
                        reply = _generate_achievements_report()
                        _add_turn(user_id, from_name, text)
                        _add_turn(user_id, "EIDOS", reply or "")
                        if reply:
                            _save_to_brain(user_id, from_name, text, reply)
                            requests.post(f"{base_url}/sendMessage",
                                         json={"chat_id": chat_id, "text": reply[:4000]}, timeout=10)
                        continue

                    # ── /status : autonomous daemon status ──────────────────────
                    if _tl.startswith("/status") or _tl.startswith("/estado"):
                        reply = _generate_status_report()
                        _add_turn(user_id, from_name, text)
                        _add_turn(user_id, "EIDOS", reply or "")
                        if reply:
                            _save_to_brain(user_id, from_name, text, reply)
                            requests.post(f"{base_url}/sendMessage",
                                         json={"chat_id": chat_id, "text": reply[:4000]}, timeout=10)
                        continue

                    # Detectar petición de captura de pantalla
                    _shot_kws = {"captura", "screenshot", "pantalla", "foto de la pantalla",
                                 "screen", "take a screenshot", "toma una captura"}
                    if any(kw in text.lower() for kw in _shot_kws):
                        try:
                            shot_path = f"/tmp/eidos_tg_shot_{int(time.time())}.png"
                            env_disp = {**os.environ, "DISPLAY": ":0"}
                            import subprocess as _sp_shot
                            # Intentar mss primero, luego scrot
                            try:
                                import mss
                                with mss.mss() as sct:
                                    sct.shot(output=shot_path)
                            except Exception:
                                _sp_shot.run(["scrot", "-z", shot_path],
                                             env=env_disp, timeout=5, check=True)
                            # Enviar foto
                            with open(shot_path, "rb") as fp:
                                requests.post(
                                    f"{base_url}/sendPhoto",
                                    files={"photo": fp},
                                    data={"chat_id": chat_id,
                                          "caption": f"📸 {IS_MAC and 'Mac' or 'Kali'} — {time.strftime('%H:%M:%S')}"},
                                    timeout=30
                                )
                            import os as _os_shot
                            _os_shot.remove(shot_path)
                            _add_turn(user_id, from_name, text)
                            reply = "📸 Captura enviada."
                            _add_turn(user_id, "EIDOS", reply)
                        except Exception as _se:
                            log.warning("Screenshot error: %s", _se)
                            reply = f"No pude tomar la captura: {_se}"
                    else:
                        _add_turn(user_id, from_name, text)
                        reply = _ask_eidos(user_id, from_name, text)
                        _add_turn(user_id, "EIDOS", reply or "")

                if reply:
                    _save_to_brain(user_id, from_name, msg.get("text", "[media]"), reply)
                    # Enviar respuesta de texto (split si > 4096 chars)
                    if reply != "📸 Captura enviada.":
                        for chunk in [reply[i:i+4000] for i in range(0, len(reply), 4000)]:
                            requests.post(f"{base_url}/sendMessage",
                                         json={"chat_id": chat_id, "text": chunk,
                                               "parse_mode": "Markdown"},
                                         timeout=10)

        except requests.RequestException as e:
            # Redactar el token del bot: las excepciones de requests incluyen la
            # URL completa (/bot<TOKEN>/getUpdates) → no filtrar secretos al log. [S122]
            safe = re.sub(r'/bot\d+:[A-Za-z0-9_-]+', '/bot<REDACTED>', str(e))
            log.warning("Network error: %s — reintentando en 10s", safe)
            time.sleep(10)
        except Exception as e:
            safe = re.sub(r'/bot\d+:[A-Za-z0-9_-]+', '/bot<REDACTED>', str(e))
            log.error("Error inesperado: %s", safe)
            time.sleep(5)


# ══════════════════════════════════════════════════════════════════════════════════
#  TELEGRAM COMMAND HANDLERS — Briefing, Errors, Achievements, Status (S125+)
# ══════════════════════════════════════════════════════════════════════════════════

def _generate_telegram_briefing() -> str:
    """Generate a morning briefing of EIDOS autonomous activity."""
    try:
        from core.eidos_autonomous_daemon import get_autonomous_daemon
        daemon = get_autonomous_daemon()
        briefing = daemon._generate_morning_briefing()
        return f"🌅 EIDOS Briefing\n\n{briefing}"
    except Exception:
        pass

    # Fallback: generate briefing from available data
    lines = ["🌅 EIDOS Briefing\n"]

    # Autonomous ops
    ops_log = Path.home() / ".eidos" / "autonomous_ops.jsonl"
    if ops_log.exists():
        try:
            ops = []
            cutoff = time.time() - 480 * 60  # last 8 hours
            with open(ops_log, "r") as f:
                for line in f:
                    try:
                        entry = json.loads(line)
                        if entry.get("ts", 0) >= cutoff:
                            ops.append(entry)
                    except json.JSONDecodeError:
                        continue
            if ops:
                cycles = sum(1 for o in ops if o.get("type") == "cycle_complete")
                real_actions = sum(
                    o.get("real_actions", 0)
                    for o in ops
                    if o.get("type") == "cycle_complete"
                )
                lines.append(f"Autonomous: {cycles} cycles, {real_actions} real actions")
        except Exception:
            pass

    # Observational learning stats
    try:
        from core.eidos_observational import observational_status
        obs = observational_status()
        if obs.get("intents_stored", 0) > 0:
            lines.append(f"Observations: {obs['segments_stored']} actions | "
                        f"{obs['intents_stored']} intents | "
                        f"{obs['recipes_promoted']} recipes")
    except Exception:
        pass

    # Curiosity atlas
    try:
        from core.eidos_curiosity_engine import get_curiosity_engine
        engine = get_curiosity_engine()
        if hasattr(engine, "_atlas") and engine._atlas:
            pending = engine._atlas.count_pending()
            explored = engine._atlas.count_explored()
            lines.append(f"Curiosity: {pending} pending, {explored} explored")
    except Exception:
        pass

    # Brain stats
    try:
        brain_db = Path.home() / ".eidos" / "evolution_brain.db"
        if brain_db.exists():
            with get_conn(brain_db, timeout=5) as c:
                nodes = c.execute("SELECT COUNT(*) FROM knowledge_nodes").fetchone()[0]
                skills = c.execute(
                    "SELECT COUNT(*) FROM knowledge_nodes WHERE source='skill_learned'"
                ).fetchone()[0]
                lines.append(f"Brain: {nodes} nodes, {skills} skills learned")
    except Exception:
        pass

    if len(lines) == 1:
        return "🌅 EIDOS Briefing\n\nNo significant autonomous activity in the last 8 hours."

    return "\n".join(lines)


def _generate_error_report() -> str:
    """Generate report of errors that have triggered alerts."""
    try:
        from core.eidos_autonomous_daemon import get_autonomous_daemon
        daemon = get_autonomous_daemon()
        errors = daemon._logger.get_error_summary()
        if errors:
            lines = ["⚠️ EIDOS Error Report\n"]
            for e in errors[:10]:
                lines.append(f"  - {e['key']}: {e['count']}x failures")
            return "\n".join(lines)
        return "✅ EIDOS Error Report\n\nNo errors have triggered alerts (3+ failures)."
    except Exception:
        pass

    # Fallback: check ops log for error patterns
    ops_log = Path.home() / ".eidos" / "autonomous_ops.jsonl"
    if not ops_log.exists():
        return "✅ EIDOS Error Report\n\nNo autonomous operations log found."

    try:
        from collections import Counter
        error_counts = Counter()
        with open(ops_log, "r") as f:
            for line in f:
                try:
                    entry = json.loads(line)
                    if entry.get("type") == "step_error":
                        key = entry.get("action", "unknown")
                        error_counts[key] += 1
                except json.JSONDecodeError:
                    continue

        triggered = [(k, v) for k, v in error_counts.items() if v >= 3]
        if triggered:
            lines = ["⚠️ EIDOS Error Report\n"]
            for k, v in triggered[:10]:
                lines.append(f"  - {k}: {v}x failures")
            return "\n".join(lines)
        return "✅ EIDOS Error Report\n\nNo errors have triggered alerts (3+ consecutive failures)."
    except Exception:
        return "⚠️ Could not read error log."


def _generate_achievements_report() -> str:
    """Generate report of achievements and milestones."""
    try:
        from core.eidos_autonomous_daemon import get_autonomous_daemon
        daemon = get_autonomous_daemon()
        achievements = daemon._achievements
        status = daemon.get_status()

        lines = ["🏆 EIDOS Achievements\n"]
        lines.append(f"Real actions total: {status['real_actions_taken']}")
        lines.append(f"Cycles completed: {status['cycle_count']}")

        if achievements:
            lines.append(f"\nRecent achievements ({len(achievements)} total):")
            for a in achievements[-5:]:
                lines.append(f"  - [{a.get('type', '?')}] {a.get('goal', '')[:80]}")
        else:
            lines.append("\nNo achievements recorded yet.")

        # Also check skills learned
        try:
            brain_db = Path.home() / ".eidos" / "evolution_brain.db"
            if brain_db.exists():
                with get_conn(brain_db, timeout=5) as c:
                    skills = c.execute(
                        "SELECT concept, confidence FROM knowledge_nodes "
                        "WHERE source='skill_learned' ORDER BY created_at DESC LIMIT 5"
                    ).fetchall()
                    if skills:
                        lines.append("\nSkills learned:")
                        for concept, conf in skills:
                            lines.append(f"  - {str(concept)[:60]} (conf={conf:.0%})")
        except Exception:
            pass

        # Recipes promoted
        try:
            from core.eidos_observational import observational_status
            obs = observational_status()
            if obs.get("recipes_promoted", 0) > 0:
                lines.append(f"\nRecipes auto-promoted: {obs['recipes_promoted']}")
        except Exception:
            pass

        return "\n".join(lines)

    except Exception:
        return "🏆 EIDOS Achievements\n\nAchievement tracking is not yet active. Start the autonomous daemon to begin."


def _generate_status_report() -> str:
    """Generate a comprehensive status report of EIDOS."""
    lines = ["📊 EIDOS Status Report\n"]

    # Autonomous daemon status
    try:
        from core.eidos_autonomous_daemon import get_autonomous_daemon
        daemon = get_autonomous_daemon()
        status = daemon.get_status()
        state = "RUNNING" if status["running"] else "STOPPED"
        if status["paused"]:
            state += " (PAUSED: " + status.get("pause_reason", "user active") + ")"
        lines.append(f"Autonomous Daemon: {state}")
        if status["running"]:
            lines.append(f"  Cycle: {status['cycle_count']} | "
                        f"Real actions: {status['real_actions_taken']} | "
                        f"Idle: {status['idle_seconds']:.0f}s")
            lines.append(f"  Real mode: {'ENABLED' if status['real_enabled'] else 'DISABLED'} | "
                        f"Observational: {'ON' if status['observational_enabled'] else 'OFF'}")
    except Exception:
        lines.append("Autonomous Daemon: NOT RUNNING")

    # Observational learning
    try:
        from core.eidos_observational import observational_status
        obs = observational_status()
        lines.append(f"\nObservational Learning: "
                    f"{'ACTIVE' if obs.get('passive_running') else 'INACTIVE'}")
        lines.append(f"  Segments: {obs.get('segments_stored', 0)} | "
                    f"Intents: {obs.get('intents_stored', 0)} | "
                    f"Recipes: {obs.get('recipe_candidates', 0)} "
                    f"({obs.get('recipes_promoted', 0)} promoted)")
    except Exception:
        pass

    # Services
    try:
        r = subprocess.run(
            ["systemctl", "--user", "list-units", "--type=service", "--no-legend"],
            capture_output=True, text=True, timeout=5,
        )
        eidos_services = [l for l in r.stdout.splitlines() if "eidos" in l.lower()]
        running = sum(1 for s in eidos_services if "running" in s)
        failed = sum(1 for s in eidos_services if "failed" in s)
        lines.append(f"\nServices: {running} running, {failed} failed (of {len(eidos_services)} total)")
        if failed:
            failed_names = [s.split()[0] for s in eidos_services if "failed" in s]
            lines.append(f"  Failed: {', '.join(failed_names)}")
    except Exception:
        pass

    return "\n".join(lines)
