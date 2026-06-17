"""
EIDOS Input Safety — Freno de emergencia + Modo cesión (S116).

La capa de seguridad que protege a SER: EIDOS NUNCA puede dejarle el ratón/
teclado bloqueado. Tras el incidente del grab de puntero (1 jun 2026), esto es
INNEGOCIABLE y debe consultarse ANTES de cualquier control de input.

Dos protecciones:
1. FRENO DE EMERGENCIA (kill switch): un archivo-bandera ~/.eidos/INPUT_FREEZE.
   Si existe, NINGÚN control de input de EIDOS se ejecuta. SER lo activa con
   `eidos-freeze` (o un atajo de teclado), incluso con el ratón bloqueado.
2. MODO CESIÓN: si SER ha tocado su ratón/teclado en los últimos N segundos,
   EIDOS CEDE y no toca el input. Detecta la actividad real de SER vía la
   extensión XScreenSaver del servidor X (libXss, sin dependencias externas).

REGLA DE ORO: todo módulo que controle ratón/teclado/pantalla DEBE llamar a
`can_control_input()` y abortar si devuelve False. Sin excepciones.
"""

from __future__ import annotations

import ctypes
import logging
import os
from pathlib import Path
from typing import Tuple

log = logging.getLogger("eidos.input_safety")

FREEZE_FLAG = Path.home() / ".eidos" / "INPUT_FREEZE"
# Si SER tocó algo hace menos de esto, está activo → EIDOS cede
USER_ACTIVE_THRESHOLD_MS = 3000


# ── Detección de actividad de SER vía XScreenSaver (libXss) ───────────────────
class _XScreenSaverInfo(ctypes.Structure):
    _fields_ = [
        ("window", ctypes.c_ulong),
        ("state", ctypes.c_int),
        ("kind", ctypes.c_int),
        ("since", ctypes.c_ulong),
        ("idle", ctypes.c_ulong),
        ("event_mask", ctypes.c_ulong),
    ]


_xlib = None
_xss = None
_xss_ok = False


def _init_xss():
    global _xlib, _xss, _xss_ok
    if _xss is not None:
        return _xss_ok
    try:
        _xlib = ctypes.CDLL("libX11.so.6")
        _xss = ctypes.CDLL("libXss.so.1")
        _xlib.XOpenDisplay.restype = ctypes.c_void_p
        _xlib.XDefaultRootWindow.argtypes = [ctypes.c_void_p]
        _xlib.XDefaultRootWindow.restype = ctypes.c_ulong
        _xss.XScreenSaverAllocInfo.restype = ctypes.POINTER(_XScreenSaverInfo)
        _xss.XScreenSaverQueryInfo.argtypes = [
            ctypes.c_void_p, ctypes.c_ulong, ctypes.POINTER(_XScreenSaverInfo)
        ]
        _xss_ok = True
    except Exception as e:
        log.warning("XScreenSaver no disponible (%s) — modo cesión usará fallback", e)
        _xss_ok = False
    return _xss_ok


def get_idle_ms() -> int:
    """Milisegundos desde la última actividad de SER (ratón/teclado). -1 si falla."""
    if not _init_xss():
        return -1
    try:
        dpy = _xlib.XOpenDisplay(os.environ.get("DISPLAY", ":0").encode())
        if not dpy:
            return -1
        root = _xlib.XDefaultRootWindow(dpy)
        info = _xss.XScreenSaverAllocInfo()
        _xss.XScreenSaverQueryInfo(dpy, root, info)
        idle = int(info.contents.idle)
        _xlib.XFree(info)
        _xlib.XCloseDisplay(dpy)
        return idle
    except Exception as e:
        log.debug("get_idle_ms error: %s", e)
        return -1


# ── Freno de emergencia (kill switch) ─────────────────────────────────────────
def is_frozen() -> bool:
    """¿Está activo el freno de emergencia? (SER bloqueó todo control de input)"""
    return FREEZE_FLAG.exists()


def freeze() -> str:
    """Activa el freno: EIDOS no podrá tocar input hasta que SER lo libere."""
    FREEZE_FLAG.parent.mkdir(parents=True, exist_ok=True)
    FREEZE_FLAG.write_text("FROZEN by SER")
    log.warning("INPUT FREEZE ACTIVADO — EIDOS no controlará input")
    return "🛑 Freno activado. EIDOS no tocará tu ratón/teclado hasta que lo liberes."


def unfreeze() -> str:
    """Libera el freno."""
    try:
        FREEZE_FLAG.unlink()
    except FileNotFoundError:
        pass
    log.info("INPUT FREEZE liberado")
    return "✅ Freno liberado. EIDOS puede volver a actuar (respetando el modo cesión)."


def user_is_active() -> bool:
    """¿SER está usando su PC ahora mismo? (tocó algo hace poco)"""
    idle = get_idle_ms()
    if idle < 0:
        # Si no podemos medir, asumir que SER PODRÍA estar activo (seguro)
        return True
    return idle < USER_ACTIVE_THRESHOLD_MS


def can_control_input() -> Tuple[bool, str]:
    """
    LA función que TODO control de input debe llamar antes de actuar.
    Devuelve (permitido, razón). Si False → NO tocar ratón/teclado.
    """
    if is_frozen():
        return False, "freno de emergencia activo"
    if user_is_active():
        idle = get_idle_ms()
        return False, f"SER está usando su PC (cesión; idle={idle}ms)"
    return True, "ok"


# CLI: `python3 -m core.eidos_input_safety freeze|unfreeze|status`
if __name__ == "__main__":
    import sys
    cmd = sys.argv[1] if len(sys.argv) > 1 else "status"
    if cmd == "freeze":
        print(freeze())
    elif cmd == "unfreeze":
        print(unfreeze())
    else:
        ok, reason = can_control_input()
        print(f"frozen={is_frozen()} idle_ms={get_idle_ms()} "
              f"user_active={user_is_active()}")
        print(f"can_control_input={ok} ({reason})")
