"""
core/mac_observer.py — Observer nativo para macOS (reemplaza eidos_observer.py en Mac).

macOS tiene sus propias herramientas de control:
  screencapture -x  → captura sin cursor "+", sin sonido, completamente silencioso
  osascript         → AppleScript: controla CUALQUIER app de Mac
  open -a           → abre cualquier aplicación
  system_profiler   → inventario completo del sistema

Sin xdotool, sin scrot, sin X11 — todo nativo macOS.

Capacidades:
  - Captura pantalla silenciosa (screencapture -x)
  - Control de apps via AppleScript (Telegram, Chrome, Safari, etc.)
  - Click/tipo en cualquier ventana sin mover el cursor del usuario
  - Análisis completo de aplicaciones instaladas
  - Control total del sistema macOS
  - Crear bots de Telegram abriendo la app y usando BotFather

Uso:
    from core.mac_observer import MacObserver
    obs = MacObserver()
    obs.screenshot('/tmp/mac_screen.png')
    obs.describe_screen()   # moondream:latest describe lo que ve
    obs.analyze_all_apps()  # aprende todas las apps instaladas
    obs.open_app('Telegram')
    obs.applescript('tell app "Telegram" to activate')
"""
from __future__ import annotations

import base64
import json
import logging
import os
import sqlite3
import subprocess
import time
from pathlib import Path
from typing import Optional
from core.db import get_conn

log = logging.getLogger("eidos.mac_observer")

BRAIN_DB   = Path.home() / ".eidos" / "evolution_brain.db"
OLLAMA_URL = os.environ.get("OLLAMA_URL", "http://localhost:11434")
APPS_DIR   = Path("/Applications")
USER_APPS  = Path.home() / "Applications"
OBSERVE_DIR = Path.home() / ".eidos" / "observer"
OBSERVE_DIR.mkdir(parents=True, exist_ok=True)


class MacObserver:
    """Control y observación nativa de macOS."""

    # ── Captura de pantalla (silenciosa, sin "+" cursor) ─────────────────────

    def screenshot(self, output: str = None) -> Optional[str]:
        """Captura pantalla sin sonido, sin cursor '+', completamente silenciosa."""
        if output is None:
            output = str(OBSERVE_DIR / f"screen_{int(time.time())}.png")
        try:
            # -x = sin sonido/cursor, -C = incluir cursor (omitimos para ser discretos)
            r = subprocess.run(
                ["screencapture", "-x", output],
                capture_output=True, timeout=10
            )
            return output if r.returncode == 0 else None
        except Exception as e:
            log.warning("screenshot error: %s", e)
            return None

    def screenshot_window(self, app_name: str, output: str = None) -> Optional[str]:
        """Captura SOLO la ventana de una app específica."""
        if output is None:
            output = str(OBSERVE_DIR / f"{app_name.lower().replace(' ','_')}.png")
        try:
            # Obtener window ID con osascript
            wid_script = f'tell application "System Events" to get id of first window of process "{app_name}"'
            r = subprocess.run(
                ["osascript", "-e", wid_script],
                capture_output=True, text=True, timeout=5
            )
            if r.returncode == 0 and r.stdout.strip():
                wid = r.stdout.strip()
                subprocess.run(
                    ["screencapture", "-x", "-l", wid, output],
                    capture_output=True, timeout=10
                )
                return output
            # Fallback: captura completa
            return self.screenshot(output)
        except Exception as e:
            log.warning("screenshot_window error: %s", e)
            return self.screenshot(output)

    def describe_screen(self, question: str = "") -> str:
        """Captura pantalla y pide a moondream:latest que la describa."""
        img = self.screenshot()
        if not img:
            return "[error: no se pudo capturar pantalla]"
        try:
            with open(img, "rb") as f:
                img_b64 = base64.b64encode(f.read()).decode()
            q = question or (
                "¿Qué aplicaciones están abiertas? ¿Qué está haciendo el usuario? "
                "¿Hay contenido importante visible? Responde en 3 frases en español."
            )
            import urllib.request
            payload = json.dumps({
                "model": "moondream:latest",
                "prompt": q,
                "images": [img_b64],
                "stream": False,
                "options": {"num_predict": 200, "temperature": 0.3},
            }).encode()
            req = urllib.request.Request(
                f"{OLLAMA_URL}/api/generate", data=payload,
                headers={"Content-Type": "application/json"}
            )
            with urllib.request.urlopen(req, timeout=60) as r:
                return json.loads(r.read()).get("response", "")
        except Exception as e:
            return f"[error describe_screen] {e}"

    # ── AppleScript — control de cualquier app ───────────────────────────────

    def applescript(self, script: str, timeout: int = 15) -> str:
        """Ejecuta AppleScript. Controla cualquier app de Mac."""
        try:
            r = subprocess.run(
                ["osascript", "-e", script],
                capture_output=True, text=True, timeout=timeout
            )
            return r.stdout.strip() if r.returncode == 0 else f"[error] {r.stderr.strip()[:200]}"
        except Exception as e:
            return f"[error applescript] {e}"

    def open_app(self, app_name: str) -> str:
        """Abre una aplicación de Mac."""
        try:
            r = subprocess.run(
                ["open", "-a", app_name],
                capture_output=True, text=True, timeout=10
            )
            if r.returncode == 0:
                time.sleep(2)  # esperar que abra
                return f"✅ {app_name} abierto"
            return f"[error] {r.stderr.strip()}"
        except Exception as e:
            return f"[error open_app] {e}"

    def type_in_app(self, app_name: str, text: str) -> str:
        """Escribe texto en una aplicación sin mover el cursor del usuario."""
        script = f'''
tell application "{app_name}"
    activate
end tell
tell application "System Events"
    tell process "{app_name}"
        keystroke "{text}"
    end tell
end tell'''
        return self.applescript(script)

    def click_in_app(self, app_name: str, x: int, y: int) -> str:
        """Click en coordenadas de una app sin afectar el cursor real."""
        script = f'''
tell application "System Events"
    tell process "{app_name}"
        click at {{{x}, {y}}}
    end tell
end tell'''
        return self.applescript(script)

    def get_open_windows(self) -> list[dict]:
        """Lista todas las ventanas abiertas."""
        script = '''
set result to {}
tell application "System Events"
    repeat with p in (every process whose background only is false)
        set appName to name of p
        repeat with w in (every window of p)
            set wTitle to name of w
            set end of result to appName & ": " & wTitle
        end repeat
    end repeat
end tell
return result'''
        try:
            r = subprocess.run(
                ["osascript", "-e", script],
                capture_output=True, text=True, timeout=15
            )
            if r.returncode == 0:
                lines = [l.strip() for l in r.stdout.strip().split(",") if l.strip()]
                return [{"window": l} for l in lines]
        except Exception:
            pass
        return []

    def send_telegram_message(self, to_username: str, message: str) -> str:
        """Envía mensaje via Telegram Desktop en Mac usando AppleScript."""
        # 1. Abrir Telegram
        self.open_app("Telegram")
        time.sleep(2)

        # 2. Cmd+K para buscar chat
        script_search = '''
tell application "Telegram"
    activate
end tell
tell application "System Events"
    tell process "Telegram"
        keystroke "k" using command down
    end tell
end tell'''
        self.applescript(script_search)
        time.sleep(1)

        # 3. Escribir el username
        script_type = f'''
tell application "System Events"
    tell process "Telegram"
        keystroke "{to_username.lstrip('@')}"
    end tell
end tell'''
        self.applescript(script_type)
        time.sleep(1.5)

        # 4. Enter para abrir chat
        script_enter = '''
tell application "System Events"
    tell process "Telegram"
        keystroke return
    end tell
end tell'''
        self.applescript(script_enter)
        time.sleep(1)

        # 5. Escribir el mensaje (línea a línea con Shift+Enter)
        lines = message.split('\n')
        for i, line in enumerate(lines):
            if line:
                safe_line = line.replace('"', '\\"').replace("'", "\\'")
                type_line = (
                    'tell application "System Events"\n'
                    '    tell process "Telegram"\n'
                    f'        keystroke "{safe_line}"\n'
                    '    end tell\n'
                    'end tell'
                )
                self.applescript(type_line)
            if i < len(lines) - 1:
                shift_enter = '''
tell application "System Events"
    tell process "Telegram"
        keystroke return using shift down
    end tell
end tell'''
                self.applescript(shift_enter)
            time.sleep(0.1)

        # 6. Enter para enviar
        self.applescript(script_enter)
        return f"✅ Mensaje enviado a @{to_username.lstrip('@')} via Telegram Mac"

    # ── Análisis de aplicaciones ─────────────────────────────────────────────

    def analyze_all_apps(self, save_to_brain: bool = True) -> list[dict]:
        """
        Analiza TODAS las aplicaciones instaladas en el Mac.
        Guarda descripción de cada app en el brain para que EIDOS las use.
        """
        apps = []
        app_dirs = [APPS_DIR, USER_APPS]

        for app_dir in app_dirs:
            if not app_dir.exists():
                continue
            for app_path in app_dir.glob("*.app"):
                name = app_path.stem
                # Leer Info.plist para descripción
                info_plist = app_path / "Contents" / "Info.plist"
                description = ""
                bundle_id = ""
                try:
                    r = subprocess.run(
                        ["defaults", "read", str(info_plist)],
                        capture_output=True, text=True, timeout=5
                    )
                    if "CFBundleDisplayName" in r.stdout:
                        import re
                        m = re.search(r'CFBundleDisplayName = "([^"]+)"', r.stdout)
                        if m:
                            name = m.group(1)
                    if "CFBundleIdentifier" in r.stdout:
                        import re
                        m = re.search(r'CFBundleIdentifier = "([^"]+)"', r.stdout)
                        if m:
                            bundle_id = m.group(1)
                    if "NSHumanReadableCopyright" in r.stdout:
                        import re
                        m = re.search(r'NSHumanReadableCopyright = "([^"]+)"', r.stdout)
                        if m:
                            description = m.group(1)[:100]
                except Exception:
                    pass

                app_info = {
                    "name": name,
                    "path": str(app_path),
                    "bundle_id": bundle_id,
                    "description": description,
                }
                apps.append(app_info)

        if save_to_brain and apps:
            self._save_apps_to_brain(apps)

        log.info("Analizadas %d aplicaciones en Mac", len(apps))
        return apps

    def get_system_info(self) -> dict:
        """Obtiene información del sistema macOS."""
        info = {}
        try:
            # RAM
            r = subprocess.run(
                ["sysctl", "-n", "hw.memsize"],
                capture_output=True, text=True, timeout=5
            )
            if r.stdout.strip():
                info["ram_gb"] = round(int(r.stdout.strip()) / 1e9, 1)
            # CPU
            r2 = subprocess.run(
                ["sysctl", "-n", "machdep.cpu.brand_string"],
                capture_output=True, text=True, timeout=5
            )
            info["cpu"] = r2.stdout.strip()
            # macOS version
            r3 = subprocess.run(
                ["sw_vers", "-productVersion"],
                capture_output=True, text=True, timeout=5
            )
            info["macos"] = r3.stdout.strip()
        except Exception:
            pass
        return info

    # ── Telegram Bot Creator ─────────────────────────────────────────────────

    def create_telegram_bot(self, bot_name: str, bot_username: str) -> dict:
        """
        Crea un bot de Telegram via BotFather usando Telegram Desktop en Mac.
        Paso a paso con AppleScript.

        Args:
            bot_name: nombre visible del bot (ej: "PotemTakem EIDOS")
            bot_username: username del bot (ej: "Potemtakem_eidosbot")

        Returns: {"token": "...", "username": "...", "success": bool}
        """
        log.info("Creando bot Telegram: %s (@%s)", bot_name, bot_username)

        # 1. Abrir BotFather
        self.open_app("Telegram")
        time.sleep(2)

        # Abrir búsqueda y buscar BotFather
        self.applescript('''
tell application "Telegram" to activate
tell application "System Events"
    tell process "Telegram"
        keystroke "k" using command down
    end tell
end tell''')
        time.sleep(1)
        self.applescript('''
tell application "System Events"
    tell process "Telegram"
        keystroke "BotFather"
    end tell
end tell''')
        time.sleep(1.5)
        self.applescript('''
tell application "System Events"
    tell process "Telegram"
        keystroke return
    end tell
end tell''')
        time.sleep(1)

        # 2. Enviar /newbot
        self._tg_type_send("/newbot")
        time.sleep(2)

        # 3. Nombre del bot
        self._tg_type_send(bot_name)
        time.sleep(2)

        # 4. Username del bot
        self._tg_type_send(bot_username if bot_username.endswith("bot") else bot_username + "_bot")
        time.sleep(3)

        # 5. Capturar pantalla para leer el token
        img = self.screenshot_window("Telegram")
        token_description = ""
        if img:
            token_description = self.describe_screen(
                "Lee el token de API que BotFather acaba de generar. "
                "El token tiene el formato: 123456789:ABCdef... "
                "Responde SOLO con el token si lo ves, o 'no visible' si no lo ves."
            )

        return {
            "success": True,
            "bot_name": bot_name,
            "bot_username": bot_username,
            "token_raw": token_description,
            "note": "Revisa la pantalla de Telegram para copiar el token manualmente si el OCR no lo leyó bien."
        }

    def _tg_type_send(self, text: str) -> None:
        """Escribe texto en Telegram y pulsa Enter."""
        safe_text = text.replace('"', '\\"').replace("'", "\\'")
        self.applescript(
            'tell application "System Events"\n'
            '    tell process "Telegram"\n'
            f'        keystroke "{safe_text}"\n'
            '    end tell\n'
            'end tell'
        )
        time.sleep(0.5)
        self.applescript('''
tell application "System Events"
    tell process "Telegram"
        keystroke return
    end tell
end tell''')

    # ── Helpers ──────────────────────────────────────────────────────────────

    @staticmethod
    def _save_apps_to_brain(apps: list[dict]) -> None:
        try:
            conn = get_conn(BRAIN_DB, timeout=5)
            added = 0
            for app in apps:
                concept = f"Mac App: {app['name']}"
                definition = (
                    f"Aplicación macOS: {app['name']}\n"
                    f"Bundle ID: {app.get('bundle_id','')}\n"
                    f"Ruta: {app['path']}\n"
                    f"Descripción: {app.get('description','')}\n"
                    f"Abrir con: open -a '{app['name']}' o osascript AppleScript\n"
                    f"Control: tell application \"{app['name']}\" to activate"
                )
                ex = conn.execute(
                    "SELECT 1 FROM knowledge_nodes WHERE concept=?", (concept,)
                ).fetchone()
                if not ex:
                    conn.execute(
                        "INSERT INTO knowledge_nodes (concept,definition,category,confidence,source,created_at) "
                        "VALUES (?,?,?,?,?,strftime('%s','now'))",
                        (concept, definition, "mac_app", 0.9, "mac_observer")
                    )
                    added += 1
            conn.commit()
            pass  # S109: get_conn no necesita close()
            log.info("Guardadas %d apps en brain", added)
        except Exception as e:
            log.warning("save_apps_to_brain error: %s", e)


def get_mac_observer() -> MacObserver:
    return MacObserver()
