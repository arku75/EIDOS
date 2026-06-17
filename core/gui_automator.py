"""
core/gui_automator.py — Autonomía real de GUI para EIDOS

EIDOS abre apps, busca ventanas por WID, hace click y lee resultados
SIN que Claude lo haga desde el terminal. Colony usa [GUI: acción] tags.

Regla de oro: SIEMPRE verificar ventana activa antes de interactuar.
"""
from core.db import get_conn
import subprocess
import time
import logging
import os
import re
from pathlib import Path
from typing import Optional, Dict, Any, Tuple

log = logging.getLogger("gui_automator")
DISPLAY = os.environ.get("DISPLAY", ":0")


# ── Conocimiento del entorno de pantalla ──────────────────────────────────────

class ScreenContext:
    """
    EIDOS conoce su propia pantalla: dimensiones, panel, DPI, escala.
    Se detecta al importar y se guarda en brain para uso futuro.
    """
    def __init__(self):
        self.width: int = 1920
        self.height: int = 1080
        self.panel_top: int = 0      # px que ocupa el panel superior (KDE)
        self.panel_bottom: int = 0   # px que ocupa el panel inferior
        self.usable_h: int = 1080    # altura útil sin paneles
        self.scale: float = 1.0      # factor de escala HiDPI
        self.detected: bool = False
        self._detect()

    def _detect(self):
        """Detecta resolución real con xrandr y panel KDE con _NET_WM_STRUT."""
        try:
            import re as _re

            # 1. Resolución total via xrandr
            out, _ = _run_raw("xrandr --current")
            m = _re.search(r'current (\d+) x (\d+)', out)
            if m:
                self.width, self.height = int(m.group(1)), int(m.group(2))

            # 2. Panel KDE: buscar ventanas con struts (_NET_WM_STRUT_PARTIAL)
            # Cada strut reserva espacio en: left, right, top, bottom
            out2, _ = _run_raw("wmctrl -l")
            for line in out2.splitlines():
                wid_hex = line.split()[0] if line.split() else ""
                if not wid_hex:
                    continue
                try:
                    strut, _ = _run_raw(
                        f"xprop -id {wid_hex} _NET_WM_STRUT_PARTIAL 2>/dev/null"
                    )
                    if "_NET_WM_STRUT_PARTIAL" in strut:
                        nums = list(map(int, _re.findall(r'\d+', strut)))
                        if len(nums) >= 4:
                            # [left, right, top, bottom, ...]
                            top_strut = nums[2]
                            bot_strut = nums[3]
                            if top_strut > 0:
                                self.panel_top = max(self.panel_top, top_strut)
                            if bot_strut > 0:
                                self.panel_bottom = max(self.panel_bottom, bot_strut)
                except Exception:
                    pass

            # 3. Si struts no funcionaron, detectar panel mirando ventanas maximizadas
            # Formato wmctrl -l -G: WID  DESKTOP  X  Y  W  H  HOSTNAME  TITLE
            if self.panel_top == 0:
                out3, _ = _run_raw("wmctrl -l -G")
                for line in out3.splitlines():
                    parts = line.split()
                    if len(parts) >= 7:
                        try:
                            wy = int(parts[3])
                            ww = int(parts[4])   # W = ancho
                            wh = int(parts[5])   # H = alto
                            # Ventana que ocupa casi toda la pantalla pero no empieza en y=0
                            if ww >= self.width * 0.8 and wh >= self.height * 0.7 and 0 < wy < 80:
                                self.panel_top = wy
                                log.info("panel_top detectado desde ventana maximizada: %dpx", wy)
                                break
                        except ValueError:
                            pass

            # 4. Fallback: maximizar ventana activa temporalmente para medir panel
            if self.panel_top == 0:
                try:
                    import time as _t
                    # Obtener ventana activa actual
                    active_out, _ = _run_raw("xdotool getactivewindow")
                    wid_active = active_out.strip()
                    if wid_active:
                        # Guardar estado actual (geometría)
                        geo_out, _ = _run_raw(f"wmctrl -l -G")
                        geo_before = {}
                        for gl in geo_out.splitlines():
                            gp = gl.split()
                            if len(gp) >= 6 and int(gp[0], 16) == int(wid_active):
                                geo_before = {"x": gp[2], "y": gp[3], "w": gp[4], "h": gp[5]}
                                break
                        wid_hex_active = hex(int(wid_active))
                        # Maximizar para medir
                        _run_raw(f"wmctrl -ir {wid_hex_active} -b add,maximized_vert,maximized_horz")
                        _t.sleep(0.5)
                        out5, _ = _run_raw("wmctrl -l -G")
                        for l2 in out5.splitlines():
                            gp2 = l2.split()
                            if len(gp2) >= 6:
                                try:
                                    if int(gp2[0], 16) == int(wid_active):
                                        yt = int(gp2[3])
                                        wt = int(gp2[4])
                                        if wt >= self.width * 0.8 and 0 <= yt < 80:
                                            self.panel_top = yt
                                            log.info("panel_top detectado vía ventana activa: %dpx", yt)
                                        break
                                except ValueError:
                                    pass
                        # Restaurar al estado original si teníamos geometría
                        if geo_before:
                            _run_raw(f"wmctrl -ir {wid_hex_active} -b remove,maximized_vert,maximized_horz")
                            _t.sleep(0.3)
                            _run_raw(f"wmctrl -ir {wid_hex_active} -e 0,{geo_before['x']},{geo_before['y']},{geo_before['w']},{geo_before['h']}")
                        else:
                            _run_raw(f"wmctrl -ir {wid_hex_active} -b remove,maximized_vert,maximized_horz")
                except Exception:
                    pass

            self.usable_h = self.height - self.panel_top - self.panel_bottom
            self.detected = True
            log.info("pantalla: %dx%d | panel_top=%dpx | usable=%dx%d",
                     self.width, self.height, self.panel_top,
                     self.width, self.usable_h)
            self._save_to_brain()
        except Exception as e:
            log.debug("detect screen: %s", e)

    def _save_to_brain(self):
        """Guarda las dimensiones en evolution_brain.db para que Colony las conozca."""
        try:
            import sqlite3, uuid
            db = Path.home() / ".eidos" / "evolution_brain.db"
            c = get_conn(db, timeout=3)
            info = (f"Pantalla de EIDOS detectada:\n"
                    f"Resolución total: {self.width}x{self.height}px\n"
                    f"Panel superior (KDE): {self.panel_top}px\n"
                    f"Área útil para ventanas: {self.width}x{self.usable_h}px\n"
                    f"DISPLAY: {DISPLAY}\n"
                    f"Escala HiDPI: {self.scale}x\n"
                    f"Uso: las ventanas maximizadas empiezan en y={self.panel_top} "
                    f"y miden {self.width}x{self.usable_h}px")
            existing = c.execute(
                "SELECT id FROM knowledge_nodes WHERE concept='gui:screen:dimensions'"
            ).fetchone()
            if existing:
                c.execute("UPDATE knowledge_nodes SET definition=? WHERE concept=?",
                          (info, 'gui:screen:dimensions'))
            else:
                c.execute(
                    "INSERT INTO knowledge_nodes (id,concept,definition,category,confidence,source) "
                    "VALUES (?,?,?,?,?,?)",
                    (str(uuid.uuid4()), 'gui:screen:dimensions', info,
                     'gui', 0.99, 'gui_automator')
                )
            c.commit(); c.close()
        except Exception as e:
            log.debug("save screen to brain: %s", e)

    def abs_xy(self, win_x: int, win_y: int, win_w: int, win_h: int,
               x_pct: float, y_pct: float):
        """Convierte posición porcentual dentro de una ventana a coordenadas absolutas."""
        return (win_x + int(win_w * x_pct / 100),
                win_y + int(win_h * y_pct / 100))

    def summary(self) -> str:
        return (f"Pantalla {self.width}x{self.height}px | "
                f"Panel top: {self.panel_top}px | "
                f"Área útil: {self.width}x{self.usable_h}px")


def _run_raw(cmd: str, timeout: int = 10) -> Tuple[str, str]:
    env = {**os.environ, "DISPLAY": DISPLAY}
    r = subprocess.run(cmd, shell=True, capture_output=True, text=True,
                       timeout=timeout, env=env)
    return r.stdout.strip(), r.stderr.strip()


# Instancia global — EIDOS conoce su pantalla desde el primer import
_SCREEN = ScreenContext()


def screen_info() -> Dict[str, Any]:
    """Devuelve el conocimiento de pantalla de EIDOS."""
    return {
        "width": _SCREEN.width,
        "height": _SCREEN.height,
        "panel_top": _SCREEN.panel_top,
        "usable_height": _SCREEN.usable_h,
        "display": DISPLAY,
        "summary": _SCREEN.summary(),
    }


# ── Utilidades de bajo nivel ──────────────────────────────────────────────────

def _run(cmd: str, timeout: int = 10) -> Tuple[str, str]:
    """Ejecuta un comando de sistema, devuelve (stdout, stderr)."""
    return _run_raw(cmd, timeout)


def _xdo(cmd: str) -> str:
    out, _ = _run(f"xdotool {cmd}")
    return out


def _wmctrl(cmd: str) -> str:
    out, _ = _run(f"wmctrl {cmd}")
    return out


# ── Estado interno (ventana enfocada actualmente por EIDOS) ───────────────────

_focused_wid: Optional[int] = None
_focused_name: str = ""


# ── API pública ───────────────────────────────────────────────────────────────

def active_window() -> Dict[str, Any]:
    """Devuelve la ventana activa actual con WID y nombre."""
    try:
        wid = int(_xdo("getactivewindow"))
        name = _xdo(f"getwindowname {wid}")
        return {"wid": wid, "name": name, "ok": True}
    except Exception as e:
        return {"wid": None, "name": "", "ok": False, "error": str(e)}


def list_windows() -> list:
    """Lista todas las ventanas visibles con WID y título."""
    out, _ = _run("wmctrl -l")
    windows = []
    for line in out.splitlines():
        parts = line.split(None, 3)
        if len(parts) >= 4:
            try:
                wid = int(parts[0], 16)
                title = parts[3]
                windows.append({"wid": wid, "wid_hex": parts[0], "title": title})
            except ValueError:
                pass
    return windows


def open_app(app_name: str, wait_seconds: int = 8) -> Dict[str, Any]:
    """
    Abre una aplicación y espera a que aparezca su ventana.
    Devuelve {"wid": int, "name": str, "ok": bool}.
    """
    global _focused_wid, _focused_name

    # Ventanas antes de abrir
    before = {w["wid"] for w in list_windows()}

    log.info("gui: abriendo '%s'...", app_name)
    env = {**os.environ, "DISPLAY": DISPLAY}
    subprocess.Popen(app_name.split(), env=env,
                     stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

    # Esperar hasta wait_seconds a que aparezca una nueva ventana
    for i in range(wait_seconds * 2):
        time.sleep(0.5)
        after = list_windows()
        new = [w for w in after if w["wid"] not in before]
        if new:
            w = new[0]
            _focused_wid = w["wid"]
            _focused_name = w["title"]
            log.info("gui: ventana detectada WID=%s '%s' en %.1fs",
                     hex(w["wid"]), w["title"], (i + 1) * 0.5)
            return {"wid": w["wid"], "wid_hex": w["wid_hex"],
                    "name": w["title"], "ok": True}

    return {"wid": None, "name": "", "ok": False,
            "error": f"'{app_name}' no abrió ventana en {wait_seconds}s"}


def focus_window(pattern: str) -> Dict[str, Any]:
    """
    Enfoca la primera ventana cuyo título contenga `pattern`.
    Actualiza el estado interno. Siempre verifica antes de actuar.
    """
    global _focused_wid, _focused_name

    windows = list_windows()
    match = next(
        (w for w in windows if pattern.lower() in w["title"].lower()), None
    )
    if not match:
        return {"ok": False, "error": f"No hay ventana con '{pattern}'",
                "available": [w["title"] for w in windows]}

    wid = match["wid"]
    wid_hex = match["wid_hex"]
    _run(f"wmctrl -ia {wid_hex}")
    time.sleep(0.4)

    # Verificar que el foco llegó
    active = active_window()
    if active["wid"] == wid:
        _focused_wid = wid
        _focused_name = match["title"]
        log.info("gui: foco en WID=%s '%s'", wid_hex, _focused_name)
        return {"wid": wid, "wid_hex": wid_hex, "name": _focused_name, "ok": True}
    else:
        # KDE a veces necesita un segundo intento
        _run(f"xdotool windowraise {wid}")
        _run(f"xdotool windowfocus --sync {wid}")
        time.sleep(0.4)
        active = active_window()
        _focused_wid = wid
        _focused_name = match["title"]
        return {"wid": wid, "name": _focused_name,
                "ok": active["wid"] == wid,
                "note": "foco forzado con xdotool"}


def _verify_focus(wid: int) -> bool:
    """Comprueba que la ventana WID tiene el foco. Intenta re-enfocar si no."""
    active = active_window()
    if active["wid"] == wid:
        return True
    # Re-intentar
    _run(f"wmctrl -ia {hex(wid)}")
    time.sleep(0.3)
    return active_window()["wid"] == wid


def get_geometry(wid: int) -> Dict[str, int]:
    """Devuelve la geometría absoluta de una ventana: x, y, w, h."""
    out = _xdo(f"getwindowgeometry {wid}")
    geo = {"x": 0, "y": 0, "w": 1920, "h": 1080}
    for line in out.splitlines():
        if "Position:" in line:
            pos = line.split("Position:")[1].strip().split()[0].split(",")
            geo["x"], geo["y"] = int(pos[0]), int(pos[1])
        elif "Geometry:" in line:
            size = line.split("Geometry:")[1].strip().split()[0].split("x")
            geo["w"], geo["h"] = int(size[0]), int(size[1])
    return geo


def click(wid: int, x_pct: float, y_pct: float) -> Dict[str, Any]:
    """
    Click en la ventana WID en posición porcentual (0-100).
    Siempre verifica el foco antes. Nunca actúa en ventana equivocada.
    """
    if not _verify_focus(wid):
        return {"ok": False, "error": f"No se pudo enfocar WID {hex(wid)} antes del click"}

    geo = get_geometry(wid)
    abs_x = geo["x"] + int(geo["w"] * x_pct / 100)
    abs_y = geo["y"] + int(geo["h"] * y_pct / 100)

    _xdo(f"mousemove --sync {abs_x} {abs_y}")
    time.sleep(0.15)
    _xdo("click 1")
    time.sleep(0.2)

    log.info("gui: click en WID=%s (%.0f%%,%.0f%%) → pantalla(%d,%d)",
             hex(wid), x_pct, y_pct, abs_x, abs_y)
    return {"ok": True, "abs_x": abs_x, "abs_y": abs_y,
            "wid": wid, "x_pct": x_pct, "y_pct": y_pct}


def right_click(wid: int, x_pct: float, y_pct: float) -> Dict[str, Any]:
    """Click derecho en posición porcentual de la ventana WID."""
    if not _verify_focus(wid):
        return {"ok": False, "error": "foco perdido"}
    geo = get_geometry(wid)
    abs_x = geo["x"] + int(geo["w"] * x_pct / 100)
    abs_y = geo["y"] + int(geo["h"] * y_pct / 100)
    _xdo(f"mousemove --sync {abs_x} {abs_y}")
    time.sleep(0.15)
    _xdo("click 3")
    time.sleep(0.3)
    return {"ok": True, "abs_x": abs_x, "abs_y": abs_y}


def type_text(wid: int, text: str, clear_first: bool = True) -> Dict[str, Any]:
    """Escribe texto en la ventana WID. Verifica foco antes."""
    if not _verify_focus(wid):
        return {"ok": False, "error": "foco perdido antes de escribir"}
    if clear_first:
        _xdo("key ctrl+a")
        time.sleep(0.1)
    _xdo(f"type --clearmodifiers --delay 60 '{text}'")
    log.info("gui: type '%s' en WID=%s", text[:30], hex(wid))
    return {"ok": True, "text": text, "wid": wid}


def send_key(wid: int, key: str) -> Dict[str, Any]:
    """Envía una tecla (Enter, Tab, Escape, ctrl+c, etc.) a la ventana WID."""
    if not _verify_focus(wid):
        return {"ok": False, "error": "foco perdido"}
    _xdo(f"key {key}")
    time.sleep(0.2)
    return {"ok": True, "key": key, "wid": wid}


def screenshot(wid: Optional[int] = None,
               region: Optional[Tuple[int, int, int, int]] = None) -> Dict[str, Any]:
    """
    Toma una captura de pantalla.
    wid=None → pantalla completa. region=(x,y,w,h) → área específica.
    Devuelve {"path": str, "ok": bool}.
    """
    ts = int(time.time())
    path = f"/tmp/eidos_gui_{ts}.png"
    env = {**os.environ, "DISPLAY": DISPLAY}

    if region:
        x, y, w, h = region
        cmd = f"scrot -z {path} -a {x},{y},{w},{h}"
    elif wid:
        cmd = f"scrot -z {path} --window {wid}"
    else:
        cmd = f"scrot -z {path}"

    ret = subprocess.run(cmd, shell=True, env=env,
                         capture_output=True, timeout=5)
    if ret.returncode == 0 and Path(path).exists():
        log.info("gui: screenshot → %s", path)
        return {"ok": True, "path": path}
    else:
        # Fallback: pantalla completa
        subprocess.run(f"scrot -z {path}", shell=True, env=env,
                       capture_output=True, timeout=5)
        return {"ok": Path(path).exists(), "path": path, "note": "fallback completo"}


def maximize(wid: int) -> Dict[str, Any]:
    """Maximiza la ventana WID."""
    _run(f"wmctrl -ir {hex(wid)} -b add,maximized_vert,maximized_horz")
    time.sleep(0.6)
    return {"ok": True, "wid": wid}


def close_window(wid: int) -> Dict[str, Any]:
    """Cierra la ventana WID de forma limpia."""
    _run(f"wmctrl -ic {hex(wid)}")
    time.sleep(0.5)
    return {"ok": True, "wid": wid}


def scroll(wid: int, direction: str = "down", amount: int = 3) -> Dict[str, Any]:
    """Scroll en la ventana WID. direction: up|down."""
    if not _verify_focus(wid):
        return {"ok": False, "error": "foco perdido"}
    btn = "5" if direction == "down" else "4"
    for _ in range(amount):
        _xdo(f"click {btn}")
        time.sleep(0.05)
    return {"ok": True, "direction": direction, "amount": amount}


# ── Parser de comandos [GUI:] para Colony/CLI ─────────────────────────────────

def execute_gui_command(cmd_str: str) -> Dict[str, Any]:
    """
    Parsea y ejecuta un comando [GUI: ...] emitido por Colony.

    Sintaxis soportada:
      [GUI: open zenmap]                    → abre la app
      [GUI: focus zenmap]                   → enfoca ventana con ese título
      [GUI: click 85% 5%]                   → click en % de la ventana activa
      [GUI: type localhost]                  → escribe texto en ventana activa
      [GUI: key Return]                      → envía tecla
      [GUI: screenshot]                      → captura la ventana activa
      [GUI: maximize]                        → maximiza ventana activa
      [GUI: scroll down 3]                   → scroll hacia abajo
      [GUI: close]                           → cierra ventana activa
      [GUI: windows]                         → lista ventanas abiertas
      [GUI: active]                          → informa ventana activa
    """
    global _focused_wid, _focused_name

    cmd_str = cmd_str.strip()
    parts = cmd_str.split(None, 2)
    if not parts:
        return {"ok": False, "error": "comando vacío"}

    action = parts[0].lower()

    # ── open ──────────────────────────────────────────────────────────────────
    if action == "open":
        app = parts[1] if len(parts) > 1 else ""
        if not app:
            return {"ok": False, "error": "open requiere nombre de app"}
        result = open_app(app)
        if result["ok"]:
            _focused_wid = result["wid"]
            _focused_name = result["name"]
        return result

    # ── focus ─────────────────────────────────────────────────────────────────
    elif action == "focus":
        pattern = " ".join(parts[1:]) if len(parts) > 1 else ""
        if not pattern:
            return {"ok": False, "error": "focus requiere patrón de título"}
        return focus_window(pattern)

    # ── click ─────────────────────────────────────────────────────────────────
    elif action == "click":
        if not _focused_wid:
            return {"ok": False, "error": "no hay ventana enfocada — usa [GUI: focus nombre]"}
        try:
            x_pct = float(parts[1].replace("%", "")) if len(parts) > 1 else 50.0
            y_pct = float(parts[2].replace("%", "")) if len(parts) > 2 else 50.0
        except ValueError:
            return {"ok": False, "error": "click requiere x% y% (ej: 85% 5%)"}
        return click(_focused_wid, x_pct, y_pct)

    # ── type ──────────────────────────────────────────────────────────────────
    elif action == "type":
        if not _focused_wid:
            return {"ok": False, "error": "no hay ventana enfocada"}
        text = " ".join(parts[1:]) if len(parts) > 1 else ""
        return type_text(_focused_wid, text)

    # ── key ───────────────────────────────────────────────────────────────────
    elif action == "key":
        if not _focused_wid:
            return {"ok": False, "error": "no hay ventana enfocada"}
        key = parts[1] if len(parts) > 1 else "Return"
        return send_key(_focused_wid, key)

    # ── screenshot ────────────────────────────────────────────────────────────
    elif action == "screenshot":
        return screenshot(wid=_focused_wid)

    # ── maximize ──────────────────────────────────────────────────────────────
    elif action == "maximize":
        if not _focused_wid:
            return {"ok": False, "error": "no hay ventana enfocada"}
        return maximize(_focused_wid)

    # ── scroll ────────────────────────────────────────────────────────────────
    elif action == "scroll":
        if not _focused_wid:
            return {"ok": False, "error": "no hay ventana enfocada"}
        direction = parts[1] if len(parts) > 1 else "down"
        amount = int(parts[2]) if len(parts) > 2 else 3
        return scroll(_focused_wid, direction, amount)

    # ── close ─────────────────────────────────────────────────────────────────
    elif action == "close":
        if not _focused_wid:
            return {"ok": False, "error": "no hay ventana enfocada"}
        r = close_window(_focused_wid)
        _focused_wid = None
        _focused_name = ""
        return r

    # ── windows ───────────────────────────────────────────────────────────────
    elif action == "windows":
        wins = list_windows()
        return {"ok": True, "windows": wins,
                "summary": "\n".join(f"• {w['title']}" for w in wins)}

    # ── active ────────────────────────────────────────────────────────────────
    elif action == "active":
        a = active_window()
        return {**a, "focused_by_eidos": _focused_name}

    # ── screen ────────────────────────────────────────────────────────────────
    elif action == "screen":
        return screen_info()

    else:
        return {"ok": False, "error": f"acción desconocida: '{action}'",
                "valid": ["open", "focus", "click", "type", "key",
                          "screenshot", "maximize", "scroll", "close",
                          "windows", "active"]}


def parse_and_run_gui_tags(text: str) -> list:
    """
    Extrae todos los [GUI: ...] del texto de Colony y los ejecuta en orden.
    Devuelve lista de resultados.
    """
    tags = re.findall(r'\[GUI:\s*([^\]]+)\]', text, re.IGNORECASE)
    results = []
    for tag in tags[:5]:  # máximo 5 acciones por respuesta
        log.info("gui_react: ejecutando [GUI: %s]", tag.strip())
        r = execute_gui_command(tag.strip())
        r["command"] = tag.strip()
        results.append(r)
        if not r.get("ok"):
            log.warning("gui_react: falló [GUI: %s] — %s", tag.strip(), r.get("error"))
            break  # parar si falla para no hacer click en lugar equivocado
        time.sleep(0.3)
    return results
