"""
EIDOS Screen Scanner — Visión de pantalla + investigación de apps
Escanea ventanas abiertas (0.01s, sin Ollama), aprende de ellas,
investiga apps con man/--help/kali.org, indexa en brain.db.

Colony responde "qué tienes abierto" en <1s vía knowledge_first.
Moondream analiza visualmente solo cuando hay algo nuevo/desconocido.
"""
from __future__ import annotations

import base64
import io
import json
import logging
import os
from core.db import get_conn
import subprocess
import time
import urllib.request
from pathlib import Path
from typing import Dict, List, Optional, Tuple

log = logging.getLogger("screen_scanner")

DB_PATH = Path.home() / ".eidos" / "evolution_brain.db"
SCREENSHOT_DIR = Path.home() / ".eidos" / "screenshots"
SCREENSHOT_DIR.mkdir(exist_ok=True, parents=True)

# Apps de sistema que no interesan aprender
_SYSTEM_SKIP = {
    "plasmashell", "kwin", "xwl-", "dbus", "polkit",
    "baloo", "kaccess", "kded", "kwallet",
}


# ── Ventanas ──────────────────────────────────────────────────────────────────

def get_open_windows() -> List[Dict]:
    """
    Lista ventanas abiertas con wmctrl. ~0.01s, sin Ollama.
    Filtra ventanas de sistema (plasmashell, escritorio, etc.)
    """
    try:
        out = subprocess.check_output(["wmctrl", "-lp"], text=True, timeout=3)
    except Exception as e:
        log.debug("wmctrl falló: %s", e)
        return []

    windows = []
    for line in out.strip().splitlines():
        parts = line.split(None, 4)
        if len(parts) < 5:
            continue
        win_id, desktop, pid, _host, name = parts
        name = name.strip()
        # Filtrar sistema
        if int(desktop) < 0:
            continue
        if any(s in name.lower() for s in _SYSTEM_SKIP):
            continue
        if "escritorio @" in name or "desktop @" in name:
            continue
        windows.append({"id": win_id, "desktop": int(desktop), "pid": pid, "name": name})
    return windows


def extract_app_name(window_title: str) -> str:
    """'archivo.py — Visual Studio Code' → 'Visual Studio Code'"""
    for sep in [" — ", " - ", " | "]:
        if sep in window_title:
            return window_title.split(sep)[-1].strip()
    return window_title.strip().split()[0] if window_title.strip() else window_title


# ── Brain.db ──────────────────────────────────────────────────────────────────

def _index(concept: str, definition: str, source: str = "screen_scanner"):
    """Upsert en brain.db."""
    try:
        con = get_conn(DB_PATH, timeout=5)
        exists = con.execute(
            "SELECT id FROM knowledge_nodes WHERE concept=?", (concept,)
        ).fetchone()
        if exists:
            con.execute(
                "UPDATE knowledge_nodes SET definition=?, updated_at=datetime('now'), "
                "confidence=MIN(COALESCE(confidence,0.7)+0.03, 1.0) WHERE concept=?",
                (definition, concept),
            )
        else:
            con.execute(
                "INSERT INTO knowledge_nodes "
                "(concept,definition,confidence,category,source,created_at,updated_at) "
                "VALUES (?,?,0.85,'system',?,datetime('now'),datetime('now'))",
                (concept, definition, source),
            )
        con.commit()
        con.close()
    except Exception as e:
        log.debug("_index error: %s", e)


def get_known_info(keyword: str) -> Optional[str]:
    """Busca en brain.db lo que EIDOS sabe sobre una app/concepto. Sin Ollama."""
    try:
        con = get_conn(DB_PATH, timeout=5)
        rows = con.execute(
            "SELECT concept, definition FROM knowledge_nodes "
            "WHERE concept LIKE ? ORDER BY confidence DESC LIMIT 3",
            (f"%{keyword.lower()[:30]}%",),
        ).fetchall()
        con.close()
        if rows:
            return "\n".join(f"• {r[0]}: {r[1][:200]}" for r in rows)
    except Exception:
        pass
    return None


# ── Visión ────────────────────────────────────────────────────────────────────

def take_screenshot(path: Optional[str] = None) -> str:
    """Captura pantalla completa con scrot. Devuelve ruta o ''."""
    if path is None:
        path = str(SCREENSHOT_DIR / f"screen_{int(time.time())}.png")
    try:
        subprocess.run(["scrot", "-z", path], check=True, timeout=6, capture_output=True)
        return path
    except Exception as e:
        log.warning("scrot: %s", e)
        return ""


def analyze_with_vision(image_path: str, prompt: str,
                        model: str = "moondream:latest") -> str:
    """
    Analiza imagen con moondream (1.7GB, rápido) o llama3.2-vision como fallback.
    Devuelve descripción en texto o '' si falla.
    """
    try:
        from PIL import Image
        with Image.open(image_path) as img:
            img = img.convert("RGB")
            img.thumbnail((1280, 720), Image.Resampling.LANCZOS)
            buf = io.BytesIO()
            img.save(buf, format="JPEG", quality=80)
            b64 = base64.b64encode(buf.getvalue()).decode()
    except Exception as e:
        log.debug("encode img: %s", e)
        return ""

    for mdl in [model, "moondream:latest"]:
        try:
            payload = json.dumps({
                "model": mdl,
                "messages": [{"role": "user", "content": prompt, "images": [b64]}],
                "stream": False,
                "options": {"num_predict": 300, "temperature": 0.2},
            }).encode()
            req = urllib.request.Request(
                "http://localhost:11434/api/chat",
                data=payload,
                headers={"Content-Type": "application/json"},
            )
            with urllib.request.urlopen(req, timeout=90) as r:
                result = json.load(r).get("message", {}).get("content", "").strip()
                if result:
                    return result
        except Exception as e:
            log.debug("vision %s: %s", mdl, e)

    return ""


# ── Escaneo de pantalla ───────────────────────────────────────────────────────

def scan_windows(use_vision: bool = False) -> Dict:
    """
    Escanea ventanas abiertas y las indexa en brain.db.
    use_vision=False → wmctrl instantáneo (<0.1s).
    use_vision=True  → además scrot+moondream para descripción visual (~10-30s).
    """
    windows = get_open_windows()
    window_names = [w["name"] for w in windows]

    # Siempre indexar la lista de ventanas (actualización en vivo)
    summary = ", ".join(window_names[:12]) if window_names else "ninguna detectada"
    _index(
        "pantalla:ventanas_activas",
        f"Ventanas abiertas ({time.strftime('%H:%M:%S')} {time.strftime('%Y-%m-%d')}):\n"
        + "\n".join(f"  • {n}" for n in window_names)
        + f"\nTotal: {len(windows)}",
        "screen_scanner:live",
    )

    indexed = 1
    vision_desc = None

    if use_vision and windows:
        ss = take_screenshot()
        if ss:
            vision_desc = analyze_with_vision(
                ss,
                "Describe briefly each visible application window: name, purpose, "
                "and what the user appears to be doing. Be specific.",
            )
            if vision_desc:
                _index(
                    "pantalla:vision_actual",
                    f"Descripción visual ({time.strftime('%H:%M')}):\n{vision_desc}",
                    "screen_scanner:vision",
                )
                indexed += 1
            try:
                os.remove(ss)
            except Exception:
                pass

    return {
        "windows": window_names,
        "indexed": indexed,
        "vision": vision_desc,
        "count": len(windows),
        "message": f"{len(windows)} ventanas, {indexed} nodos actualizados",
    }


# ── Investigación de apps ─────────────────────────────────────────────────────

def research_app(app_name: str, deep: bool = False) -> Dict:
    """
    Investiga una aplicación del sistema y aprende todo sobre ella:
    - which (¿instalada?)
    - --help / -h
    - man page (resumen NAME+DESCRIPTION)
    - kali.org/tools URL
    - DuckDuckGo si deep=True
    Indexa en brain.db. Devuelve resumen.
    """
    result: Dict = {
        "app": app_name, "installed": False,
        "nodes_added": 0, "summary": "", "kali_url": "",
    }
    app_lower = app_name.lower().strip()
    parts: List[str] = []

    # 1. which — ¿instalada?
    which = subprocess.run(
        ["which", app_lower], capture_output=True, text=True
    ).stdout.strip()
    if which:
        result["installed"] = True
        parts.append(f"Instalada en: {which}")

    # 2. --help
    for flag in ["--help", "-h", "-help"]:
        try:
            proc = subprocess.run(
                [app_lower, flag],
                capture_output=True, text=True, timeout=6
            )
            txt = (proc.stdout or proc.stderr).strip()
            if txt and len(txt) > 30:
                parts.append(f"Uso ({flag}):\n{txt[:800]}")
                break
        except Exception:
            pass

    # 3. Man page — secciones NAME + DESCRIPTION
    try:
        man = subprocess.run(
            ["man", "-P", "cat", app_lower],
            capture_output=True, text=True, timeout=6
        )
        if man.stdout:
            # Extraer las primeras 600 chars útiles
            txt = man.stdout[:1200].replace("\x08", "")  # quitar backspaces
            parts.append(f"Manual:\n{txt[:600]}")
    except Exception:
        pass

    # 4. dpkg / rpm para descripción del paquete
    try:
        dpkg = subprocess.run(
            ["dpkg", "-s", app_lower],
            capture_output=True, text=True, timeout=4
        )
        for line in dpkg.stdout.splitlines():
            if line.startswith("Description:"):
                parts.append(f"Paquete: {line[12:].strip()}")
                break
    except Exception:
        pass

    # 5. Kali tools URL (indexar aunque no hagamos fetch)
    kali_url = f"https://www.kali.org/tools/{app_lower}/"
    result["kali_url"] = kali_url

    # Indexar nodo principal
    if parts:
        definition = (
            f"Aplicación: {app_name}\n"
            + "\n---\n".join(parts)
            + f"\nDocs Kali: {kali_url}"
        )
        _index(f"app:{app_lower}", definition, f"research:{app_name}")
        result["nodes_added"] += 1
        result["summary"] = parts[0][:200]

    # Indexar comando de ejecución
    run_info = (
        f"Para ejecutar {app_name} desde EIDOS:\n"
        f"  [SHELL: {app_lower} --help]      ← ver opciones\n"
        f"  [SHELL: {app_lower}]              ← lanzar\n"
        f"Docs: {kali_url}\n"
        f"Instalada: {'sí' in which if which else 'no'}"
    )
    _index(f"cmd:{app_lower}", run_info, "screen_scanner")
    result["nodes_added"] += 1

    # Deep research — DuckDuckGo + kali.org (solo si se pide)
    if deep:
        try:
            from core.colony_studier import get_studier
            studier = get_studier()
            # Intentar estudiar kali.org/tools página
            web_text = studier._fetch_url(kali_url)
            if web_text and len(web_text) > 100:
                _index(f"app:{app_lower}:kali_docs", web_text[:1500], f"kali:{app_lower}")
                result["nodes_added"] += 1
        except Exception as e:
            log.debug("kali fetch: %s", e)

        try:
            # DuckDuckGo API
            import urllib.parse
            query = urllib.parse.quote(f"{app_name} kali linux how to use")
            req = urllib.request.Request(
                f"https://api.duckduckgo.com/?q={query}&format=json&no_html=1&skip_disambig=1",
                headers={"User-Agent": "EIDOS/1.0"},
            )
            with urllib.request.urlopen(req, timeout=8) as r:
                data = json.loads(r.read())
                abstract = data.get("AbstractText", "")
                if abstract and len(abstract) > 50:
                    _index(
                        f"app:{app_lower}:ddg",
                        f"DuckDuckGo sobre {app_name}:\n{abstract[:800]}",
                        "duckduckgo",
                    )
                    result["nodes_added"] += 1
        except Exception as e:
            log.debug("ddg: %s", e)

    log.info("research_app(%s): %d nodos, instalada=%s", app_name, result["nodes_added"], result["installed"])
    return result


# ── Autonomous doc finder ─────────────────────────────────────────────────────

def find_docs(topic: str, official_url: str = "") -> Dict:
    """
    EIDOS busca documentación de cualquier tema de forma autónoma:
    1. Si se da official_url → fetch directo
    2. DuckDuckGo Instant Answer API → abstract + URL oficial
    3. Indexa todo en brain.db
    Llamable desde Colony sin intervención de SER.
    """
    result: Dict = {"topic": topic, "nodes_added": 0, "urls_found": [], "summary": ""}
    parts: List[str] = []

    # 1. URL oficial directa si se conoce
    if official_url:
        try:
            from core.colony_studier import get_studier
            txt = get_studier()._fetch_url(official_url)
            if txt and len(txt) > 100:
                chunk = txt[:2000]
                _index(f"docs:{topic.lower()}:official", chunk, f"official:{official_url}")
                result["nodes_added"] += 1
                result["urls_found"].append(official_url)
                parts.append(f"Docs oficiales de {topic}: {chunk[:300]}")
        except Exception as e:
            log.debug("find_docs url fetch: %s", e)

    # 2. DuckDuckGo → abstract + URL
    try:
        import urllib.parse as _up
        q = _up.quote(f"{topic} official documentation")
        req = urllib.request.Request(
            f"https://api.duckduckgo.com/?q={q}&format=json&no_html=1&skip_disambig=1",
            headers={"User-Agent": "EIDOS/1.0"},
        )
        with urllib.request.urlopen(req, timeout=8) as r:
            data = json.loads(r.read())
        abstract = data.get("AbstractText", "")
        abstract_url = data.get("AbstractURL", "")
        official_site = data.get("OfficialWebsite", "")
        if abstract and len(abstract) > 30:
            _index(f"docs:{topic.lower()}:ddg", f"Documentación {topic}:\n{abstract[:800]}", "duckduckgo")
            result["nodes_added"] += 1
            parts.append(abstract[:200])
        # Intentar fetch de la URL oficial encontrada
        for url in filter(None, [official_site, abstract_url]):
            if url and url not in result["urls_found"]:
                try:
                    from core.colony_studier import get_studier
                    txt = get_studier()._fetch_url(url)
                    if txt and len(txt) > 100:
                        _index(f"docs:{topic.lower()}:web", txt[:2000], f"web:{url}")
                        result["nodes_added"] += 1
                        result["urls_found"].append(url)
                        break
                except Exception:
                    pass
    except Exception as e:
        log.debug("find_docs ddg: %s", e)

    result["summary"] = parts[0][:200] if parts else f"No se encontraron docs para {topic}"
    log.info("find_docs(%s): %d nodos, urls=%s", topic, result["nodes_added"], result["urls_found"])
    return result


# ── Telegram token renewal ────────────────────────────────────────────────────

def check_telegram_token(token: str) -> bool:
    """Verifica si un token de Telegram Bot API es válido."""
    try:
        req = urllib.request.Request(
            f"https://api.telegram.org/bot{token}/getMe",
            headers={"User-Agent": "EIDOS/1.0"},
        )
        with urllib.request.urlopen(req, timeout=5) as r:
            data = json.loads(r.read())
            return data.get("ok", False)
    except Exception:
        return False


def save_telegram_token(token: str):
    """Guarda el token nuevo en ~/.eidos/.env"""
    env_path = Path.home() / ".eidos" / ".env"
    lines = []
    found = False
    if env_path.exists():
        for line in env_path.read_text().splitlines():
            if line.startswith("TELEGRAM_BOT_TOKEN="):
                lines.append(f"TELEGRAM_BOT_TOKEN={token}")
                found = True
            else:
                lines.append(line)
    if not found:
        lines.append(f"TELEGRAM_BOT_TOKEN={token}")
    env_path.write_text("\n".join(lines) + "\n")
    log.info("Token Telegram guardado en %s", env_path)


def request_telegram_token_via_botfather() -> str:
    """
    Abre Telegram desktop y navega a @BotFather para obtener un nuevo token.
    Devuelve el token si lo extrae, '' si no pudo.
    Requiere que Telegram desktop esté instalado y la sesión iniciada.
    """
    import re

    # 1. Abrir Telegram desktop
    try:
        subprocess.Popen(["telegram-desktop"], start_new_session=True)
        time.sleep(4)
    except Exception as e:
        log.warning("No pude abrir Telegram: %s", e)
        return ""

    # 2. Buscar ventana de Telegram
    time.sleep(2)
    wins = get_open_windows()
    tg_win = next((w for w in wins if "telegram" in w["name"].lower()), None)
    if not tg_win:
        log.warning("Ventana Telegram no encontrada")
        return ""

    win_id = tg_win["id"]

    # 3. Activar ventana y abrir chat con @BotFather
    try:
        subprocess.run(["xdotool", "windowactivate", "--sync", win_id], timeout=3)
        time.sleep(1)
        # Ctrl+K para búsqueda de chats en Telegram
        subprocess.run(["xdotool", "key", "--window", win_id, "ctrl+k"], timeout=3)
        time.sleep(0.8)
        subprocess.run(["xdotool", "type", "--window", win_id, "--clearmodifiers", "@BotFather"], timeout=3)
        time.sleep(1.5)
        subprocess.run(["xdotool", "key", "--window", win_id, "Return"], timeout=3)
        time.sleep(2)

        # Enviar /mybots para ver los bots
        subprocess.run(["xdotool", "type", "--window", win_id, "--clearmodifiers", "/mybots"], timeout=3)
        subprocess.run(["xdotool", "key", "--window", win_id, "Return"], timeout=3)
        time.sleep(3)

        # Captura para ver la respuesta
        ss = take_screenshot()
        if ss:
            text = analyze_with_vision(
                ss,
                "Read all text visible in the Telegram chat window. "
                "List any bot API tokens (format: numbers:letters). "
                "If you see bot names, list them too.",
            )
            os.remove(ss)

            # Buscar token en el texto extraído
            token_match = re.search(r"\d{8,12}:[A-Za-z0-9_-]{35}", text or "")
            if token_match:
                token = token_match.group()
                log.info("Token extraído via visión: %s...", token[:15])
                save_telegram_token(token)
                return token

    except Exception as e:
        log.warning("xdotool Telegram: %s", e)

    return ""


def auto_renew_telegram(current_token: str = "") -> Dict:
    """
    Verifica token actual y, si está caducado, intenta renovar via BotFather.
    Notifica a SER del resultado.
    """
    result = {"valid": False, "renewed": False, "token": current_token, "action": ""}

    # Verificar token actual
    if current_token and check_telegram_token(current_token):
        result["valid"] = True
        result["action"] = "token válido, nada que hacer"
        return result

    result["action"] = "token inválido — intentando renovar via BotFather"

    # Intentar renovar
    new_token = request_telegram_token_via_botfather()
    if new_token and check_telegram_token(new_token):
        result["valid"] = True
        result["renewed"] = True
        result["token"] = new_token
        result["action"] = "token renovado correctamente"
    else:
        result["action"] = "renovación fallida — SER debe proporcionar nuevo token manualmente"

    # Notificar a SER
    try:
        from core.colony_proactive import push_message
        push_message(
            actor="Colony",
            message=f"Telegram: {result['action']}",
            topic="telegram:token",
            priority=8,
        )
    except Exception:
        pass

    return result


# ── API pública ───────────────────────────────────────────────────────────────

_scanner_instance = None

def get_screen_context() -> Dict[str, Any]:
    """Captura la pantalla actual y devuelve contexto estructurado.
    Usada por AliveOrchestrator y autonomous_loop para percepción.
    Retorna dict con keys: windows_open, elements, active_window, visible_text, active_pid.
    """
    import os
    empty = {"windows_open": [], "elements": [], "active_window": "", "visible_text": "", "active_pid": 0}
    if not os.environ.get("DISPLAY") and not os.environ.get("WAYLAND_DISPLAY"):
        return empty
    try:
        windows = get_open_windows()
        active = windows[0] if windows else {"name": "", "pid": 0}
        elements = []
        visible_text = ""
        try:
            from core.perception import take_screenshot, ocr_screenshot
            path = take_screenshot()
            if path:
                elements = ocr_screenshot(path)
                visible_text = " ".join(e.text for e in elements if e.text)
        except Exception:
            pass  # OCR fallback: sin texto pero devolvemos ventanas
        return {
            "windows_open": [w.get("name", "") for w in windows],
            "elements": [{"text": e.text, "x": e.x, "y": e.y} for e in elements],
            "active_window": active.get("name", ""),
            "visible_text": visible_text[:2000],
            "active_pid": active.get("pid", 0),
        }
    except Exception as e:
        import logging
        logging.getLogger("eidos").debug("get_screen_context: %s", e)
    return empty


def get_scanner():
    global _scanner_instance
    if _scanner_instance is None:
        _scanner_instance = _ScreenScanner()
    return _scanner_instance


class _ScreenScanner:
    """Singleton con estado del scanner."""

    def __init__(self):
        self._last_scan: float = 0
        self._last_windows: List[str] = []

    def quick_scan(self) -> Dict:
        """Escaneo rápido sin visión. <0.1s."""
        r = scan_windows(use_vision=False)
        self._last_scan = time.time()
        self._last_windows = r["windows"]
        return r

    def deep_scan(self) -> Dict:
        """Escaneo con visión. ~10-30s."""
        r = scan_windows(use_vision=True)
        self._last_scan = time.time()
        self._last_windows = r["windows"]
        return r

    def windows_summary(self) -> str:
        """Resumen de ventanas para knowledge_first. Sin Ollama."""
        wins = get_open_windows()
        if not wins:
            return "No hay ventanas abiertas detectables."
        names = [w["name"] for w in wins]
        return "Ventanas abiertas:\n" + "\n".join(f"  • {n}" for n in names)
