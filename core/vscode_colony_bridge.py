"""
core/vscode_colony_bridge.py — Colony controla VSCode bidireccional

Colony puede enviar comandos a VSEIDOS en tiempo real:
  - Abrir archivos
  - Ejecutar comandos en terminal
  - Mostrar mensajes
  - Instalar extensiones
  - Crear/editar archivos

La extensión VSEIDOS hace polling cada 2s a /api/commands/pending
y ejecuta los comandos que Colony encola aquí.

Uso directo:
    from core.vscode_colony_bridge import get_vscode_bridge
    vb = get_vscode_bridge()
    vb.open_file(str(REPO_ROOT / "core" / "colony_community.py"))
    vb.run_command("python3 -m pytest tests/", cwd=str(REPO_ROOT))
    vb.show_message("EIDOS ha completado el análisis")

Uso desde Colony (via IPC broadcast):
    vb.show_colony_insight("Detecté un patrón en tu código Python...")
"""
from __future__ import annotations

import logging
import threading
import urllib.request
import json
from typing import Optional

from core.paths import REPO_ROOT

log = logging.getLogger("eidos.vscode_bridge")

VSCODE_API  = "http://localhost:8765"
_instance: Optional["VscodeColonyBridge"] = None
_lock = threading.Lock()


def get_vscode_bridge() -> "VscodeColonyBridge":
    global _instance
    if _instance is None:
        with _lock:
            if _instance is None:
                _instance = VscodeColonyBridge()
    return _instance


class VscodeColonyBridge:
    """Puente Colony → VSEIDOS para control bidireccional del PC."""

    def __init__(self, api_url: str = VSCODE_API):
        self.api_url = api_url
        self._available: Optional[bool] = None

    def is_available(self) -> bool:
        try:
            req = urllib.request.Request(f"{self.api_url}/api/status", method="GET")
            with urllib.request.urlopen(req, timeout=2):
                self._available = True
                return True
        except Exception:
            self._available = False
            return False

    def _send(self, cmd_type: str, payload: dict) -> bool:
        """Encola un comando para que VSEIDOS lo ejecute."""
        try:
            data = json.dumps({"type": cmd_type, "payload": payload}).encode()
            req = urllib.request.Request(
                f"{self.api_url}/api/commands",
                data=data,
                method="POST",
                headers={"Content-Type": "application/json"},
            )
            with urllib.request.urlopen(req, timeout=3) as r:
                result = json.loads(r.read())
                return result.get("success", False)
        except Exception as e:
            log.debug("vscode_bridge._send error: %s", e)
            return False

    def open_file(self, path: str) -> bool:
        """Abre un archivo en VSCode."""
        return self._send("open_file", {"path": path})

    def run_command(self, command: str, cwd: str = "") -> bool:
        """Ejecuta un comando en el terminal de VSCode."""
        return self._send("run_terminal", {"command": command, "cwd": cwd})

    def show_message(self, text: str, level: str = "info") -> bool:
        """Muestra un mensaje en VSCode. level: info|warning|error"""
        return self._send("show_message", {"text": text, "level": level})

    def install_extension(self, extension_id: str) -> bool:
        """Instala una extensión de VSCode."""
        return self._send("install_extension", {"id": extension_id})

    def create_file(self, path: str, content: str) -> bool:
        """Crea o sobreescribe un archivo y lo abre en VSCode."""
        return self._send("create_file", {"path": path, "content": content})

    def focus_file(self, path: str) -> bool:
        """Pone el foco en un archivo ya abierto."""
        return self._send("focus_file", {"path": path})

    def show_colony_insight(self, insight: str) -> bool:
        """Muestra un insight de Colony como mensaje info en VSCode."""
        return self.show_message(f"Colony: {insight}", "info")

    def run_tests(self, cwd: str = str(REPO_ROOT)) -> bool:
        """Ejecuta los tests del proyecto en el terminal de VSCode."""
        return self.run_command("python3 -m pytest tests/ -x --tb=short", cwd=cwd)

    def analyze_current_file(self) -> bool:
        """Dispara análisis del archivo activo por Colony."""
        return self._send("show_message", {
            "text": "Analizando archivo activo con Colony...",
            "level": "info",
        })

    def get_active_file(self) -> Optional[dict]:
        """Obtiene información del archivo activo en VSCode."""
        try:
            req = urllib.request.Request(
                f"{self.api_url}/api/active-file",
                method="GET",
            )
            with urllib.request.urlopen(req, timeout=3) as r:
                return json.loads(r.read())
        except Exception:
            return None

    def ask_colony_about_file(self, question: str = "") -> Optional[str]:
        """
        Pide a Colony que analice el archivo activo en VSCode.
        Devuelve la respuesta o None si no está disponible.
        """
        active = self.get_active_file()
        if not active or not active.get("content"):
            return None
        try:
            from core.colony_community import get_colony_community
            colony = get_colony_community()
            if not colony._session_active:
                colony.start_session()
            prompt = (
                f"Analiza este archivo {active.get('language','?')} "
                f"({active.get('filePath','')}). "
                f"{question or 'Identifica mejoras o problemas.'}\n\n"
                f"```{active.get('language','')}\n{active.get('content','')[:2000]}\n```"
            )
            return colony.deliberate(prompt, max_agents=2, max_tokens=500)
        except Exception as e:
            log.debug("ask_colony_about_file error: %s", e)
            return None
