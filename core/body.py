"""
core/body.py — EL CUERPO de EIDOS: propiocepción digital. S124.

EIDOS sabe dónde está su MANO (cursor), qué ventana TOCA, su pantalla, y dónde
VIVE. No es un módulo más: es el punto desde el que el cuerpo se mira a sí mismo.
Reusa xdotool (ya presente). Egocéntrico: todo desde el punto de vista del cuerpo.

Conecta al BOM (core/causal_loop): el estado del Q-learning incluye la posición de
la mano (zona), no solo lo que ven los ojos. Así el aprendizaje es CORPORAL.
"""
from __future__ import annotations

import os
import time
import shutil
import subprocess
from pathlib import Path
from typing import Optional, Tuple, Dict, Any

_DISPLAY = os.environ.get("DISPLAY", ":0")
_ENV = {**os.environ, "DISPLAY": _DISPLAY}


def hand_position() -> Optional[Tuple[int, int]]:
    """¿Dónde está mi mano (cursor) AHORA? (x, y) o None. Propiocepción."""
    try:
        out = subprocess.run(["xdotool", "getmouselocation", "--shell"],
                             capture_output=True, text=True, timeout=3, env=_ENV).stdout
        d = dict(l.split("=", 1) for l in out.strip().splitlines() if "=" in l)
        return int(d["X"]), int(d["Y"])
    except Exception:
        return None


def active_window() -> str:
    """¿Qué ventana estoy tocando ahora mismo?"""
    try:
        wid = subprocess.run(["xdotool", "getactivewindow"],
                            capture_output=True, text=True, timeout=3, env=_ENV).stdout.strip()
        if not wid:
            return ""
        return subprocess.run(["xdotool", "getwindowname", wid],
                            capture_output=True, text=True, timeout=3, env=_ENV).stdout.strip()[:60]
    except Exception:
        return ""


def display_size() -> Tuple[int, int]:
    try:
        w, h = subprocess.run(["xdotool", "getdisplaygeometry"],
                            capture_output=True, text=True, timeout=3, env=_ENV).stdout.split()
        return int(w), int(h)
    except Exception:
        return (1920, 1080)


def body_signature() -> str:
    """Firma compacta del cuerpo para el ESTADO del Q-learning: zona (cuadrante) de
    la mano + ventana activa. Cuadrante (no píxel) para que el estado GENERALICE."""
    h = hand_position()
    if not h:
        return "hand:?"
    w, sh = display_size()
    qx = "L" if h[0] < w / 3 else ("C" if h[0] < 2 * w / 3 else "R")
    qy = "T" if h[1] < sh / 3 else ("M" if h[1] < 2 * sh / 3 else "B")
    return f"hand:{qx}{qy}|win:{active_window()[:20]}"


def i_am_here() -> Dict[str, Any]:
    """Mi estado corporal completo ahora (egocéntrico). 'Yo estoy aquí.'"""
    hand = hand_position()
    w, h = display_size()
    return {
        "hand": {"x": hand[0], "y": hand[1]} if hand else None,
        "active_window": active_window(),
        "screen": {"w": w, "h": h},
        "home": str(Path.home() / "EIDOS"),
        "user": os.getenv("USER", "?"),
    }


def reached(target: Tuple[int, int], tol: int = 12) -> bool:
    """Propiocepción de control motor: ¿mi mano LLEGÓ al objetivo? (feedback corporal).
    Devuelve True si el cursor está a <=tol píxeles del objetivo."""
    h = hand_position()
    if not h or not target:
        return False
    return abs(h[0] - target[0]) <= tol and abs(h[1] - target[1]) <= tol


def move_to_with_feedback(x: int, y: int, max_retries: int = 3, tol: int = 10) -> bool:
    """Mueve el ratón a (x,y) con verificación. Reintenta si no llegó. (S127)."""
    import subprocess, time as _time
    for attempt in range(max_retries):
        try:
            subprocess.run(["xdotool", "mousemove", str(x), str(y)], timeout=2)
            _time.sleep(0.2)
            if reached((x, y), tol):
                return True
            if attempt > 0:
                hx, hy = hand_position() or (x, y)
                subprocess.run(["xdotool", "mousemove_relative", "--",
                              str(x - hx), str(y - hy)], timeout=2)
                _time.sleep(0.1)
        except Exception:
            pass
        _time.sleep(0.3 * (attempt + 1))
    return reached((x, y), tol * 2)


# ── AUTOCONCEPTO OPERATIVO ────
def hand_ok() -> bool:
    """¿Tengo mano? Mi mano ES xdotool. Si no está, NO puedo tocar → estoy lisiado."""
    return shutil.which("xdotool") is not None


def eyes_ok() -> bool:
    """¿Veo? Mis ojos son scrot/import (captura) + AT-SPI2."""
    return shutil.which("scrot") is not None or shutil.which("import") is not None


def brain_ok() -> bool:
    """¿Tengo cerebro? Mi cerebro es el grafo en evolution_brain.db."""
    return (Path.home() / ".eidos" / "evolution_brain.db").exists()


def self_model() -> Dict[str, Any]:
    """AUTOCONCEPTO OPERATIVO: no 'soy autónomo' (declarativo), sino de QUÉ están
    hechos mis órganos y SI FUNCIONAN. 'Mi mano es xdotool; si falla, no puedo tocar.'"""
    return {
        "i_am": f"proceso Python en {Path.home() / 'EIDOS'} (PID {os.getpid()})",
        "user": os.getenv("USER", "?"),
        "organs": {
            "ojos":    {"es": "AT-SPI2 + OCR (scrot/import)",     "funciona": eyes_ok()},
            "mano":    {"es": "xdotool (ratón)",                  "funciona": hand_ok()},
            "voz":     {"es": "xdotool (teclado)",                "funciona": hand_ok()},
            "cerebro": {"es": "grafo evolution_brain.db",         "funciona": brain_ok()},
            "memoria": {"es": "knowledge_nodes + motor_memory",   "funciona": brain_ok()},
        },
    }


def what_can_i_do() -> str:
    """EIDOS se sabe capaz o lisiado, y lo DICE (autoconcepto que guía conducta)."""
    sm = self_model()
    broken = [k for k, v in sm["organs"].items() if not v["funciona"]]
    if broken:
        return (f"Me falta: {', '.join(broken)}. No puedo actuar completo — "
                f"debería avisar a SER o pedir ayuda antes de intentar.")
    return "Tengo ojos, mano, voz, cerebro y memoria operativos. Puedo percibir, tocar, escribir y aprender."


def window_context() -> str:
    """Para el ESTADO de DECISIÓN: solo qué app/ventana veo. SIN la mano — la posición
    del cursor NO debe fragmentar 'qué clicar' (corrección crítica: la mano va a la
    memoria MOTORA, no al estado de decisión). 'Settings en Telegram' ≠ 'Settings en Firefox',
    pero 'Settings con mano arriba' == 'Settings con mano abajo'."""
    return f"win:{active_window()[:30]}"


# ── MEMORIA MOTORA queryable (no string): ¿dónde está cada objetivo? ──────────
def _motor_db():
    from core.db import get_conn
    c = get_conn(Path.home() / ".eidos" / "evolution_brain.db", timeout=20)
    c.execute("""CREATE TABLE IF NOT EXISTS motor_memory(
        target_label TEXT, hand_x INTEGER, hand_y INTEGER, window TEXT,
        success INTEGER, confidence REAL, ts REAL, source TEXT)""")
    # Add source column for existing DBs (idempotent)
    try:
        c.execute("ALTER TABLE motor_memory ADD COLUMN source TEXT")
    except Exception:
        pass
    return c


def remember_motor(target_label: str, x: int, y: int, success: bool = True,
                   confidence: float = 0.7, window: str = "",
                   source: str = "") -> None:
    """Memoria corporal REAL: 'toqué <label> en (x,y) y funcionó'. Consultable."""
    try:
        c = _motor_db()
        c.execute("INSERT INTO motor_memory(target_label,hand_x,hand_y,window,success,confidence,ts,source) "
                  "VALUES(?,?,?,?,?,?,?,?)",
                  (str(target_label)[:60], int(x), int(y), str(window)[:40],
                   1 if success else 0, float(confidence), time.time(),
                   str(source)[:40]))
        c.commit()
    except Exception:
        pass


def recall_motor(target_label: str) -> Optional[Tuple[int, int]]:
    """¿Dónde estaba este objetivo la última vez que lo toqué con ÉXITO? (x,y) o None.
    Esto es memoria corporal queryable — lo que pedía la crítica."""
    try:
        c = _motor_db()
        r = c.execute("SELECT hand_x,hand_y FROM motor_memory WHERE target_label=? AND success=1 "
                      "ORDER BY confidence DESC, ts DESC LIMIT 1", (str(target_label)[:60],)).fetchone()
        return (int(r[0]), int(r[1])) if r else None
    except Exception:
        return None


if __name__ == "__main__":
    import json
    print(json.dumps(i_am_here(), ensure_ascii=False, indent=2))
    print("estado decisión:", window_context(), "| motor:", body_signature())
