"""
core/eidos_shell_term.py — Terminal VISIBLE controlada por EIDOS [S122]
======================================================================
SER quiere ver a EIDOS trabajar en una shell: que abra una terminal real y
ejecute lo que le pida, viéndolo en vivo. Implementado con tmux + Konsole:

  - tmux mantiene una sesión persistente "eidos" (el cerebro escribe ahí).
  - Konsole se engancha a esa sesión → SER ve los comandos ejecutarse en vivo.
  - EIDOS envía comandos con `tmux send-keys` y LEE el resultado con capture-pane.

Seguridad: cada comando pasa por `is_safe()` (bloquea patrones destructivos).
NO sustituye el [SHELL:] silencioso del bridge — esto es la versión VISIBLE.
"""
from __future__ import annotations

import os
import re
import subprocess
import time
import logging
from typing import Tuple

log = logging.getLogger("eidos.shell_term")

TMUX_SESSION = "eidos"
_DISPLAY = os.environ.get("DISPLAY", ":0")

# Patrones destructivos (defensa en profundidad; el bridge también valida).
_DANGEROUS = [
    "rm -rf /", "rm -rf /*", "rm -rf ~", ":(){", "mkfs", "dd if=", "dd of=/dev",
    "> /dev/sda", "fdisk", "format ", "mkswap", "shutdown", "reboot", "init 0",
    "init 6", "chmod -r 000", "chown -r", "> /etc/", "wipefs", "parted",
]


def is_safe(cmd: str) -> Tuple[bool, str]:
    """¿El comando es seguro para ejecutar en la terminal visible?"""
    c = (cmd or "").lower().strip()
    if not c:
        return False, "comando vacío"
    for p in _DANGEROUS:
        if p in c:
            return False, f"bloqueado por seguridad (patrón '{p}')"
    return True, ""


def _tmux(*args, capture: bool = True) -> subprocess.CompletedProcess:
    return subprocess.run(["tmux", *args], capture_output=capture, text=True, timeout=15)


def session_exists() -> bool:
    try:
        r = _tmux("has-session", "-t", TMUX_SESSION)
        return r.returncode == 0
    except Exception:
        return False


def ensure_session() -> bool:
    """Crea la sesión tmux 'eidos' (detached) si no existe."""
    if session_exists():
        return True
    try:
        _tmux("new-session", "-d", "-s", TMUX_SESSION, "-x", "200", "-y", "50")
        time.sleep(0.3)
        return session_exists()
    except Exception as e:  # noqa: BLE001
        log.warning("no pude crear sesión tmux: %s", e)
        return False


def _konsole_attached() -> bool:
    """¿Hay una Konsole enganchada a la sesión (visible para SER)?"""
    try:
        r = subprocess.run(["pgrep", "-af", "konsole.*tmux attach.*" + TMUX_SESSION],
                           capture_output=True, text=True, timeout=5)
        return bool(r.stdout.strip())
    except Exception:
        return False


def open_terminal() -> bool:
    """Abre una Konsole VISIBLE enganchada a la sesión tmux. Idempotente."""
    if not ensure_session():
        return False
    if _konsole_attached():
        return True
    try:
        env = {**os.environ, "DISPLAY": _DISPLAY}
        subprocess.Popen(
            ["konsole", "-p", "tabtitle=EIDOS — terminal", "-e",
             "tmux", "attach", "-t", TMUX_SESSION],
            env=env, start_new_session=True,
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        )
        time.sleep(1.5)
        return True
    except Exception as e:  # noqa: BLE001
        log.warning("no pude abrir konsole: %s", e)
        return False


def run(cmd: str, timeout: float = 20.0, open_window: bool = True) -> dict:
    """Ejecuta `cmd` en la terminal VISIBLE y devuelve su salida.

    Returns: {ok, cmd, output, error}
    """
    cmd = (cmd or "").strip()
    res = {"ok": False, "cmd": cmd, "output": "", "error": ""}
    ok, reason = is_safe(cmd)
    if not ok:
        res["error"] = reason
        return res
    if open_window:
        open_terminal()
    elif not ensure_session():
        res["error"] = "no hay sesión tmux"
        return res

    # Sentinelas de INICIO y FIN para delimitar la salida exacta de ESTE comando,
    # a prueba del prompt multilínea de zsh.
    tag = int(time.time() * 1000)
    s_start, s_end = f"__EIDOS_S_{tag}__", f"__EIDOS_E_{tag}__"
    try:
        _tmux("send-keys", "-t", TMUX_SESSION,
              f"echo {s_start}; {cmd}; echo {s_end}", "Enter")
        deadline = time.time() + timeout
        pane = ""
        while time.time() < deadline:
            time.sleep(0.4)
            cap = _tmux("capture-pane", "-p", "-S", "-200", "-t", TMUX_SESSION)
            pane = cap.stdout if cap.returncode == 0 else ""
            # El FIN aparece como línea propia (el echo ejecutado), no dentro del comando enviado
            if re.search(rf"^{re.escape(s_end)}\s*$", pane, re.M):
                break
        output = _extract_output(pane, s_start, s_end)
        res.update(ok=True, output=output[:4000])
        return res
    except Exception as e:  # noqa: BLE001
        res["error"] = str(e)
        return res


def _extract_output(pane: str, s_start: str, s_end: str) -> str:
    """Saca la salida entre el sentinela de inicio y el de fin (líneas propias)."""
    lines = pane.splitlines()
    start_idx = end_idx = None
    for i, ln in enumerate(lines):
        ls = ln.strip()
        # línea propia del echo ejecutado (no la del comando enviado, que lleva 'echo ...')
        if ls == s_start and start_idx is None:
            start_idx = i + 1
        elif ls == s_end and start_idx is not None:
            end_idx = i
            break
    if start_idx is None or end_idx is None:
        return ""
    return "\n".join(lines[start_idx:end_idx]).strip()


def status() -> dict:
    return {
        "session": session_exists(),
        "visible": _konsole_attached(),
        "tmux_session": TMUX_SESSION,
    }


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    print("status:", status())
    print("open_terminal:", open_terminal())
    r = run("echo hola desde EIDOS && whoami && pwd")
    print("run ok:", r["ok"])
    print("output:\n", r["output"])
    print("status final:", status())
