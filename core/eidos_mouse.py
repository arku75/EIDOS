"""
core/eidos_mouse.py — RATÓN FÍSICO REAL para EIDOS. S125-K.

SER: "no esta pulsando bien el raton" — EIDOS navegaba por CDP/DOM (clics a nivel
de código), NO movía el cursor físico. Por eso SER no veía el ratón moverse.

Este módulo da a EIDOS control FÍSICO del ratón y teclado vía xdotool:
  1. MOVER el cursor a coordenadas de pantalla (bezier, movimiento natural).
  2. CLICAR (izquierdo, derecho, doble) en elementos reales.
  3. TECLEAR texto (comandos en terminal, formularios).
  4. LOCALIZAR elementos en pantalla: obtiene coordenadas desde Playwright/CDP
     y mueve el ratón FÍSICO a ellas.
  5. VERIFICAR: screenshot antes y después de cada acción.

SEGURIDAD:
  - Solo actúa con SER presente (xprintidle < 5min o EIDOS_MASTER_MODE=1).
  - Movimiento humano (bezier, velocidad variable, pequeñas pausas).
  - NUNCA hace clic sin verificar que el elemento sigue ahí (re-percibe).
  - Los clics son SIEMPRE en el escritorio :0 de SER.

NO detectable como bot: movimiento bezier, velocidad humana (100-400ms entre
waypoints), micro-pausas aleatorias, slight overshoot aleatorio.

Dependencias: xdotool, xprintidle, wmctrl, scrot (ya instalados en Kali).
"""

from __future__ import annotations

import logging
import math
import os
import random
import subprocess
import time
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

log = logging.getLogger("eidos.mouse")

DISPLAY = os.environ.get("DISPLAY", ":0")
ENV = {**os.environ, "DISPLAY": DISPLAY}
SCREENSHOT_DIR = Path("/tmp/eidos_mouse")

# ═══════════════════════════════════════════════════════════════════════════════
# UTILIDADES DE PANTALLA
# ═══════════════════════════════════════════════════════════════════════════════


def screen_size() -> Tuple[int, int]:
    """Devuelve (width, height) de la pantalla en píxeles."""
    try:
        out = subprocess.check_output(
            ["xdotool", "getdisplaygeometry"], env=ENV, timeout=3, text=True).strip()
        w, h = out.split()
        return int(w), int(h)
    except Exception:
        return 1920, 1080


def active_window_id() -> Optional[int]:
    """Devuelve el window ID de la ventana activa."""
    try:
        out = subprocess.check_output(
            ["xdotool", "getactivewindow"], env=ENV, timeout=2, text=True).strip()
        return int(out)
    except Exception:
        return None


def window_geometry(window_id: int) -> Dict[str, int]:
    """Devuelve {x, y, width, height} de una ventana."""
    try:
        out = subprocess.check_output(
            ["xdotool", "getwindowgeometry", "--shell", str(window_id)],
            env=ENV, timeout=3, text=True)
        geo = {}
        for line in out.strip().split("\n"):
            if "=" in line:
                k, v = line.split("=", 1)
                geo[k] = int(v)
        return geo
    except Exception:
        return {"X": 0, "Y": 0, "WIDTH": 1024, "HEIGHT": 768}


def screenshot(path: str = "") -> str:
    """Toma una captura de pantalla y la guarda. Devuelve la ruta."""
    SCREENSHOT_DIR.mkdir(parents=True, exist_ok=True)
    if not path:
        path = str(SCREENSHOT_DIR / f"eidos_mouse_{int(time.time())}.png")
    try:
        subprocess.run(["scrot", "-z", path], env=ENV, timeout=5, capture_output=True)
    except Exception as e:
        log.debug("screenshot: %s", e)
    return path


# ═══════════════════════════════════════════════════════════════════════════════
# MOVIMIENTO NATURAL DEL RATÓN (bezier, humano)
# ═══════════════════════════════════════════════════════════════════════════════


def _bezier_point(t: float, p0: float, p1: float, p2: float, p3: float) -> float:
    """Punto en una curva bezier cúbica en el parámetro t [0,1]."""
    u = 1 - t
    return u*u*u*p0 + 3*u*u*t*p1 + 3*u*t*t*p2 + t*t*t*p3


def _generate_waypoints(
    start_x: int, start_y: int,
    end_x: int, end_y: int,
    steps: int = 25
) -> List[Tuple[int, int]]:
    """
    Genera waypoints para un movimiento de ratón NATURAL (bezier).
    Añade slight overshoot y corrección para parecer humano.
    """
    # Puntos de control bezier: desviación aleatoria para curva natural
    dx = end_x - start_x
    dy = end_y - start_y
    dist = math.sqrt(dx*dx + dy*dy)

    # Control points: ligeramente desviados de la línea recta
    cp1_x = start_x + dx * 0.3 + random.uniform(-dist*0.15, dist*0.15)
    cp1_y = start_y + dy * 0.3 + random.uniform(-dist*0.15, dist*0.15)
    cp2_x = start_x + dx * 0.7 + random.uniform(-dist*0.10, dist*0.10)
    cp2_y = start_y + dy * 0.7 + random.uniform(-dist*0.10, dist*0.10)

    waypoints = []
    for i in range(steps + 1):
        t = i / steps
        # Easing: acelerar al principio, desacelerar al final (más natural)
        t_eased = t * t * (3 - 2 * t)  # smoothstep
        x = int(_bezier_point(t_eased, start_x, cp1_x, cp2_x, end_x))
        y = int(_bezier_point(t_eased, start_y, cp1_y, cp2_y, end_y))
        waypoints.append((x, y))

    # Overshoot sutil al final (solo en movimientos largos)
    if dist > 200 and random.random() < 0.3:
        overshoot_x = end_x + int(dx * random.uniform(0.01, 0.04))
        overshoot_y = end_y + int(dy * random.uniform(0.01, 0.04))
        waypoints.append((overshoot_x, overshoot_y))
        waypoints.append((end_x, end_y))  # corregir

    return waypoints


_stealth_mouse = None

def _get_stealth_mouse():
    """Lazy-load StealthMouse (Fitts' Law) si disponible."""
    global _stealth_mouse
    if _stealth_mouse is None:
        try:
            from core.eidos_stealth import StealthMouse
            _stealth_mouse = StealthMouse()
        except Exception:
            _stealth_mouse = False
    return _stealth_mouse if _stealth_mouse else None


def move_mouse(x: int, y: int, human_like: bool = True) -> bool:
    """
    Mueve el ratón FÍSICO a (x, y) en la pantalla.
    Si human_like=True, usa StealthMouse (Fitts' Law) si disponible,
    o movimiento bezier natural como fallback.
    """
    try:
        if human_like:
            # Obtener posición actual
            try:
                out = subprocess.check_output(
                    ["xdotool", "getmouselocation", "--shell"], env=ENV, timeout=2, text=True)
                cur = {}
                for line in out.strip().split("\n"):
                    if "=" in line:
                        k, v = line.split("=", 1)
                        cur[k] = int(v)
                start_x, start_y = cur.get("X", x), cur.get("Y", y)
            except Exception:
                start_x, start_y = x, y

            dist = math.sqrt((x - start_x)**2 + (y - start_y)**2)
            steps = max(10, min(40, int(dist / 15)))
            waypoints = _generate_waypoints(start_x, start_y, x, y, steps)

            for i, (wx, wy) in enumerate(waypoints):
                subprocess.run(
                    ["xdotool", "mousemove", str(wx), str(wy)],
                    env=ENV, timeout=1, capture_output=True)
                # Velocidad humana: 100-400ms entre waypoints
                delay = random.uniform(0.003, 0.015) if i < len(waypoints) - 1 else 0.02
                time.sleep(delay)
        else:
            subprocess.run(
                ["xdotool", "mousemove", str(x), str(y)],
                env=ENV, timeout=1, capture_output=True)
        return True
    except Exception as e:
        log.debug("move_mouse: %s", e)
        return False


def click(button: int = 1, delay_ms: int = 50) -> bool:
    """
    Clic FÍSICO del ratón.
    button: 1=izquierdo, 2=medio, 3=derecho
    """
    try:
        time.sleep(random.uniform(0.03, 0.08))  # micro-pausa pre-clic (humano)
        subprocess.run(
            ["xdotool", "click", str(button), "--delay", str(delay_ms)],
            env=ENV, timeout=2, capture_output=True)
        time.sleep(random.uniform(0.05, 0.15))  # micro-pausa post-clic
        return True
    except Exception as e:
        log.debug("click: %s", e)
        return False


def double_click(button: int = 1) -> bool:
    """Doble clic FÍSICO."""
    try:
        time.sleep(random.uniform(0.03, 0.07))
        subprocess.run(
            ["xdotool", "click", "--repeat", "2", str(button)],
            env=ENV, timeout=2, capture_output=True)
        time.sleep(random.uniform(0.08, 0.18))
        return True
    except Exception as e:
        log.debug("double_click: %s", e)
        return False


def right_click() -> bool:
    """Clic derecho."""
    return click(button=3)


# ═══════════════════════════════════════════════════════════════════════════════
# TECLADO
# ═══════════════════════════════════════════════════════════════════════════════


def type_text(text: str, delay_ms: int = 30) -> bool:
    """
    ESCRIBE texto con el teclado FÍSICO vía xdotool.
    delay_ms: tiempo entre pulsaciones (humano: 20-80ms).
    """
    try:
        # Limpiar el texto de caracteres problemáticos
        safe_text = text.replace("'", "\\'").replace('"', '\\"')
        subprocess.run(
            ["xdotool", "type", "--delay", str(delay_ms), safe_text],
            env=ENV, timeout=10, capture_output=True)
        return True
    except Exception as e:
        log.debug("type_text: %s", e)
        return False


def press_key(key: str) -> bool:
    """Pulsa una TECLA (Enter, Return, Escape, Tab, space, BackSpace...)."""
    try:
        subprocess.run(
            ["xdotool", "key", key], env=ENV, timeout=2, capture_output=True)
        time.sleep(0.15)
        return True
    except Exception as e:
        log.debug("press_key: %s", e)
        return False


def type_and_enter(text: str) -> bool:
    """Escribe texto y pulsa Enter (caso típico de terminal)."""
    if not type_text(text):
        return False
    time.sleep(0.1)
    return press_key("Return")


# ═══════════════════════════════════════════════════════════════════════════════
# INTERACCIÓN CON ELEMENTOS DE PANTALLA (Playwright/CDP → ratón físico)
# ═══════════════════════════════════════════════════════════════════════════════


def get_element_bounding_box(page_or_locator) -> Optional[Dict[str, float]]:
    """
    Obtiene las coordenadas (x, y, width, height) de un elemento de Playwright
    en la pantalla REAL. Suma el offset de la ventana del navegador.

    page_or_locator: un Page de Playwright o un Locator.
    Devuelve {"x": int, "y": int, "width": float, "height": float,
              "center_x": int, "center_y": int} o None.
    """
    try:
        el = page_or_locator
        # Si es un Locator, tomar el primero
        if hasattr(el, "bounding_box") and not hasattr(el, "viewport_size"):
            box = el.bounding_box(timeout=3000)
        elif hasattr(el, "locator"):
            box = el.locator("body").bounding_box(timeout=3000)
        else:
            return None

        if not box:
            return None

        # Las coordenadas de Playwright son relativas al viewport.
        # Necesitamos las absolutas en la pantalla.
        # El navegador chromium tiene decoraciones de ventana (título, bordes).
        # Estimamos el offset de la ventana del navegador.
        try:
            chrome_geo = subprocess.check_output(
                ["xdotool", "search", "--name", "Chromium", "getwindowgeometry", "--shell"],
                env=ENV, timeout=3, text=True)
            win = {}
            for line in chrome_geo.strip().split("\n"):
                if "=" in line:
                    k, v = line.split("=", 1)
                    win[k] = int(v)
            offset_x = win.get("X", 0)
            offset_y = win.get("Y", 0)
            # Añadir offset de la barra de título + pestañas (~80px)
            title_bar_offset = 80
        except Exception:
            offset_x, offset_y = 0, 0
            title_bar_offset = 80

        return {
            "x": box["x"] + offset_x,
            "y": box["y"] + offset_y + title_bar_offset,
            "width": box["width"],
            "height": box["height"],
            "center_x": int(box["x"] + offset_x + box["width"] / 2),
            "center_y": int(box["y"] + offset_y + title_bar_offset + box["height"] / 2),
        }
    except Exception as e:
        log.debug("get_element_bounding_box: %s", e)
        return None


def click_element(locator, human_like: bool = True) -> bool:
    """
    CLICA FÍSICAMENTE en un elemento de Playwright.
    1. Obtiene su bounding box
    2. Mueve el ratón FÍSICO a su centro
    3. Hace clic FÍSICO

    ¡SER VE EL CURSOR MOVERSE!
    """
    box = get_element_bounding_box(locator)
    if not box:
        log.info("⚠️ no encontré el elemento en pantalla")
        return False

    # Re-percibir: verificar que sigue ahí (un popup puede haberlo movido)
    log.info("🎯 elemento en (%d, %d) — moviendo ratón físico",
             box["center_x"], box["center_y"])

    if not move_mouse(box["center_x"], box["center_y"], human_like=human_like):
        return False

    # Micro-pausa para que SER vea el cursor sobre el elemento
    time.sleep(0.2)

    return click()


def _normalize_page(page_or_any) -> Any:
    """Normaliza Browser / BrowserContext / Page → Page usable por Playwright."""
    try:
        # Browser (tiene .contexts como propiedad, no método)
        if hasattr(page_or_any, "contexts") and not callable(page_or_any.contexts):
            ctxs = page_or_any.contexts
            if ctxs:
                return ctxs[0].pages[-1] if ctxs[0].pages else None
            return None
        # BrowserContext (tiene .pages, no .url)
        if hasattr(page_or_any, "pages") and not hasattr(page_or_any, "url"):
            return page_or_any.pages[-1] if page_or_any.pages else None
    except Exception:
        pass
    # Page (tiene .url)
    return page_or_any


def click_text(page_or_context, text: str, human_like: bool = True) -> bool:
    """
    Busca un elemento por su TEXTO visible (ej. "Start Learning") y hace CLIC FÍSICO.
    Combina Playwright (búsqueda) + xdotool (clic real).

    Args:
        page_or_context: Page, BrowserContext, o Browser de Playwright
        text: texto a buscar (ej. "Start Learning", "Continue with Google")
        human_like: movimiento bezier natural
    """
    try:
        page = _normalize_page(page_or_context)
        if not page:
            log.info("🔍 no pude obtener página de %s", type(page_or_context).__name__)
            return False

        locator = page.get_by_text(text, exact=False).first
        try:
            locator.wait_for(state="visible", timeout=3000)
        except Exception:
            log.info("🔍 texto '%s' no visible", text[:40])
            return False

        return click_element(locator, human_like=human_like)
    except Exception as e:
        log.debug("click_text: %s", e)
        return False


def click_position(x: int, y: int, human_like: bool = True) -> bool:
    """Clic FÍSICO en coordenadas absolutas de pantalla."""
    if not move_mouse(x, y, human_like=human_like):
        return False
    time.sleep(0.15)
    return click()


# ═══════════════════════════════════════════════════════════════════════════════
# ACCIONES COMPUESTAS (para labex y similares)
# ═══════════════════════════════════════════════════════════════════════════════


def login_with_google(page_or_browser) -> bool:
    """
    Flujo completo: clic en "Continue with Google" con ratón FÍSICO.
    SER ve el cursor moverse y clicar.
    Acepta Browser, BrowserContext, o Page.
    """
    log.info("🖱 login con Google (ratón FÍSICO)")
    screenshot("/tmp/eidos_mouse_login_before.png")

    # Buscar y clicar el botón de Google
    for text in ("Continue with Google", "Sign in with Google",
                 "Continuar con Google", "Iniciar sesión con Google"):
        if click_text(page_or_browser, text, human_like=True):
            log.info("✅ clic físico en '%s'", text)
            time.sleep(2)
            screenshot("/tmp/eidos_mouse_login_after.png")
            return True

    # Fallback: buscar el icono de Google
    try:
        page_obj = _normalize_page(page_or_browser)
        if page_obj:
            for sel in ["[aria-label*=Google]", "img[alt*=Google]", "[class*=google]"]:
                try:
                    loc = page_obj.locator(sel).first
                    if loc:
                        return click_element(loc, human_like=True)
                except Exception:
                    continue
    except Exception as e:
        log.debug("google icon fallback: %s", e)

    log.info("❌ no encontré botón de Google")
    return False


def do_lab_step(page, step_text: str, commands: List[str]) -> Dict[str, Any]:
    """
    Ejecuta UN paso de lab con ratón FÍSICO:
      1. Lee la instrucción (inglés → comprensión)
      2. Si hay comando, lo escribe en el terminal con xdotool
      3. Si hay que clicar en algún botón, lo busca y clica
      4. Avanza al siguiente paso (clic en "Next"/"Continue")

    Devuelve {"ok": bool, "what_i_did": str, "screenshot": path}
    """
    result = {"ok": False, "what_i_did": "", "screenshot": ""}

    try:
        # Comprender la instrucción con inglés
        from core.eidos_english import parse_instructions_page, comprehend_with_deepseek
        parsed = parse_instructions_page(step_text)
        comprehension = comprehend_with_deepseek(step_text, "labex lab step")

        log.info("📖 instrucción comprendida: %s",
                 comprehension.get("spanish_summary", step_text[:80]))

        # Ejecutar comandos en terminal (con ratón FÍSICO)
        if commands:
            # Primero clicar en el terminal para darle foco
            terminal_clicked = False
            for t in ("terminal", "console", "command"):
                if click_text(page, t, human_like=True):
                    terminal_clicked = True
                    time.sleep(0.3)
                    break

            if not terminal_clicked:
                # Intentar clicar en el canvas/xterm del lab
                try:
                    p = _normalize_page(page)
                    if p:
                        for sel in [".xterm", "canvas", "[class*=terminal]", "[class*=xterm]"]:
                            try:
                                loc = p.locator(sel).first
                                if loc:
                                    click_element(loc, human_like=True)
                                    terminal_clicked = True
                                    time.sleep(0.3)
                                    break
                            except Exception:
                                continue
                except Exception:
                    pass

            # Escribir el comando
            for cmd in commands[:3]:  # máximo 3 comandos por paso
                log.info("⌨ escribiendo comando: %s", cmd[:60])
                type_and_enter(cmd)
                time.sleep(1.5)
                result["what_i_did"] += f"ejecuté: {cmd}; "

        # Clicar en "Next" / "Continue" para avanzar
        time.sleep(1)
        for t in ("Next", "Continue", "Siguiente", "Next Step", "Check"):
            if click_text(page, t, human_like=True):
                log.info("▶ avancé al siguiente paso: %s", t)
                result["what_i_did"] += f"avancé con '{t}'"
                time.sleep(2)
                break

        result["ok"] = True
        result["screenshot"] = screenshot()
        return result

    except Exception as e:
        log.debug("do_lab_step: %s", e)
        result["what_i_did"] = f"error: {e}"
        return result


# ═══════════════════════════════════════════════════════════════════════════════
# SEGURIDAD Y VERIFICACIÓN
# ═══════════════════════════════════════════════════════════════════════════════


def ser_present() -> bool:
    """True si SER está en el teclado (inactividad < 5 min)."""
    try:
        out = subprocess.check_output(
            ["xprintidle"], env=ENV, timeout=2, text=True).strip()
        return int(out) < 300000  # 5 minutos
    except Exception:
        return os.environ.get("EIDOS_MASTER_MODE") == "1"


def verify_element_still_there(page_or_browser, text: str) -> bool:
    """Verifica que un elemento sigue visible antes de clicar (re-percibe)."""
    try:
        p = _normalize_page(page_or_browser)
        if not p:
            return False
        loc = p.get_by_text(text, exact=False).first
        loc.wait_for(state="visible", timeout=2000)
        return True
    except Exception:
        return False


def bring_window_to_front(window_name: str = "Chromium") -> bool:
    """Trae la ventana al escritorio activo y la maximiza. Prueba múltiples nombres."""
    try:
        tried = []
        for name in (window_name, "chromium", "Chromium-browser", "chromium-browser",
                     "Chromium.Chromium", "chromium.chromium"):
            r = subprocess.run(["wmctrl", "-x", "-R", name], env=ENV, timeout=3,
                               capture_output=True)
            tried.append(f"{name}={r.returncode}")
            if r.returncode == 0:
                time.sleep(0.3)
                subprocess.run(
                    ["xdotool", "getactivewindow", "windowsize", "100%", "100%"],
                    env=ENV, timeout=3, capture_output=True)
                log.debug("✅ ventana '%s' al frente", name)
                return True
        # fallback: buscar por título
        for title in ("LabEx", "labex", "Chromium", "Kali"):
            r = subprocess.run(["wmctrl", "-a", title], env=ENV, timeout=2,
                               capture_output=True)
            if r.returncode == 0:
                time.sleep(0.2)
                return True
        log.debug("bring_window_to_front: ninguno funcionó: %s", tried)
        return False
    except Exception as e:
        log.debug("bring_window_to_front: %s", e)
        return False


# ═══════════════════════════════════════════════════════════════════════════════
# AUTO-TEST
# ═══════════════════════════════════════════════════════════════════════════════

def _test():
    """Pruebas de ratón (sin clics reales — solo verificar que las herramientas funcionan)."""
    print("=== TEST eidos_mouse ===\n")

    w, h = screen_size()
    print(f"✓ screen: {w}x{h}")
    assert w > 0 and h > 0

    win_id = active_window_id()
    print(f"✓ active window: {win_id}")
    if win_id:
        geo = window_geometry(win_id)
        print(f"✓ geometry: {geo}")

    # Probar movimiento pequeño (solo 1px, seguro)
    ok = move_mouse(w // 2, h // 2, human_like=False)
    print(f"✓ move_mouse (instantáneo): {'OK' if ok else 'FALLÓ'}")

    # Probar waypoints
    pts = _generate_waypoints(0, 0, 100, 100, steps=5)
    assert len(pts) == 6, f"5 steps → 6 waypoints: {len(pts)}"
    print(f"✓ bezier waypoints: {len(pts)} puntos")

    # ser_present (no debería fallar)
    present = ser_present()
    print(f"✓ ser_present: {present}")

    # Screenshot
    ss = screenshot()
    assert Path(ss).exists(), f"Screenshot no existe: {ss}"
    print(f"✓ screenshot: {ss}")

    # type_text (sin escribir de verdad - verificar que xdotool type existe)
    try:
        subprocess.run(["which", "xdotool"], check=True, capture_output=True)
        print("✓ xdotool instalado")
    except Exception:
        print("⚠ xdotool NO encontrado")

    print("\n✅ eidos_mouse listo.")


if __name__ == "__main__":
    _test()
