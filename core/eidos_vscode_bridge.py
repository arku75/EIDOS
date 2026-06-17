"""
core/eidos_vscode_bridge.py — VSCode Bridge (S82)

Integración con Visual Studio Code. Permite a EIDOS interactuar con
el IDE: abrir archivos, leer selección, ejecutar comandos.

Basado en eidos_vscode_bridge.py de SER (NO TOCAR).

API:
    bridge = get_vscode_bridge()
    bridge.open_file("core/eidos_vivo.py", line=695)
    bridge.get_selection() → texto seleccionado en el editor
"""

from __future__ import annotations

import json
import logging
import os
import subprocess
from pathlib import Path
from typing import Any, Dict, List, Optional

log = logging.getLogger("eidos.vscode")

EIDOS_ROOT = Path.home() / "EIDOS"


class VSCodeBridge:
    """Puente bidireccional entre EIDOS y VSCode."""

    def __init__(self):
        self._available = self._detect()
        log.info("VSCodeBridge: %s", "disponible" if self._available else "no detectado")

    def _detect(self) -> bool:
        """Detecta si VSCode está instalado y corriendo."""
        try:
            # Verificar CLI
            r = subprocess.run(["code", "--version"], capture_output=True, timeout=5)
            if r.returncode != 0:
                return False
            # Verificar si hay ventana abierta
            r2 = subprocess.run(
                ["xdotool", "search", "--name", "Visual Studio Code"],
                capture_output=True, timeout=3)
            return r2.returncode == 0 and bool(r2.stdout.strip())
        except Exception:
            return False

    @property
    def is_available(self) -> bool:
        return self._available

    # ── Acciones ────────────────────────────────────────────────────────────

    def open_file(self, file_path: str, line: int = 0) -> bool:
        """Abre un archivo en VSCode, opcionalmente en una línea específica."""
        if not self._available:
            return False
        try:
            full_path = EIDOS_ROOT / file_path
            if not full_path.exists():
                log.debug("VSCode: archivo no existe: %s", full_path)
                return False
            goto = f":{line}" if line > 0 else ""
            subprocess.Popen(
                ["code", "--goto", f"{full_path}{goto}"],
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                start_new_session=True)
            log.info("VSCode: abierto %s%s", file_path, goto)
            return True
        except Exception as e:
            log.debug("VSCode open_file: %s", e)
            return False

    def open_terminal(self, command: str = "") -> bool:
        """Abre o enfoca el terminal integrado de VSCode."""
        if not self._available:
            return False
        try:
            # S82 fix: buscar window ID sin shell=True
            r = subprocess.run(
                ["xdotool", "search", "--name", "Visual Studio Code"],
                capture_output=True, text=True, timeout=3)
            win_ids = (r.stdout or "").strip().split("\n")
            if win_ids and win_ids[0]:
                subprocess.run(
                    ["xdotool", "windowactivate", "--sync", win_ids[0]],
                    capture_output=True, timeout=3)
            # Ctrl+` para abrir terminal
            subprocess.run(["xdotool", "key", "ctrl+grave"], timeout=3)
            if command:
                import time
                time.sleep(0.3)
                subprocess.run(["xdotool", "type", command], timeout=3)
            return True
        except Exception as e:
            log.debug("VSCode terminal: %s", e)
            return False

    def run_command(self, command_id: str) -> bool:
        """Ejecuta un comando de VSCode via Command Palette."""
        if not self._available:
            return False
        try:
            # Ctrl+Shift+P para Command Palette
            subprocess.run(["xdotool", "key", "ctrl+shift+p"], timeout=3)
            import time
            time.sleep(0.3)
            subprocess.run(["xdotool", "type", command_id], timeout=3)
            time.sleep(0.2)
            subprocess.run(["xdotool", "key", "Return"], timeout=3)
            return True
        except Exception as e:
            log.debug("VSCode command: %s", e)
            return False

    def get_selection(self) -> Optional[str]:
        """Obtiene el texto seleccionado en el editor (vía xclip)."""
        try:
            r = subprocess.run(
                ["xclip", "-o", "-selection", "primary"],
                capture_output=True, text=True, timeout=3)
            if r.returncode == 0 and r.stdout.strip():
                return r.stdout.strip()[:1000]
            return None
        except Exception:
            return None

    # ── Notificaciones ─────────────────────────────────────────────────────

    def notify(self, message: str, level: str = "info"):
        """Muestra una notificación en VSCode."""
        if not self._available:
            return
        # Usar el CLI de VSCode para notificaciones (si está disponible)
        try:
            # Alternativa: escribir a un archivo que una extensión lea
            notify_file = Path.home() / ".eidos" / "vscode_notify.txt"
            notify_file.parent.mkdir(parents=True, exist_ok=True)
            notify_file.write_text(f"[{level.upper()}] {message}")
        except Exception:
            pass

    # ── Stats ──────────────────────────────────────────────────────────────

    def stats(self) -> Dict[str, Any]:
        return {
            "available": self._available,
            "vscode_root": str(EIDOS_ROOT),
        }


_vscode_bridge: Optional[VSCodeBridge] = None


def get_vscode_bridge() -> VSCodeBridge:
    global _vscode_bridge
    if _vscode_bridge is None:
        _vscode_bridge = VSCodeBridge()
    return _vscode_bridge


if __name__ == "__main__":
    import argparse
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    p = argparse.ArgumentParser(description="EIDOS VSCode Bridge")
    p.add_argument("--open", type=str, help="Abrir archivo")
    p.add_argument("--line", type=int, default=0)
    p.add_argument("--terminal", action="store_true")
    p.add_argument("--selection", action="store_true")
    args = p.parse_args()

    bridge = VSCodeBridge()
    print(f"VSCode disponible: {bridge.is_available}")

    if args.open:
        ok = bridge.open_file(args.open, args.line)
        print(f"Abierto: {ok}")
    elif args.terminal:
        bridge.open_terminal()
    elif args.selection:
        sel = bridge.get_selection()
        print(f"Selección: {sel[:200] if sel else 'nada'}")
