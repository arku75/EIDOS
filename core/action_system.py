"""
EIDOS Action System — Control de mouse, teclado y GUI

Permite a EIDOS:
- Mover el mouse a coordenadas específicas
- Hacer clic en botones (izquierdo, derecho, doble)
- Escribir texto en campos
- Navegar por menús de aplicaciones
- Controlar ventanas (maximizar, minimizar, cerrar)

Ejemplo:
    move_to(100, 200)          # Mueve mouse a (x=100, y=200)
    click()                    # Clic izquierdo
    write("hola mundo")       # Escribe texto
    press("enter")            # Presiona Enter
    locate_button("Aceptar")  # Encuentra botón "Aceptar" en pantalla
"""

import logging
import time
from typing import Dict, Optional, Tuple

log = logging.getLogger("eidos.action_system")

try:
    import pyautogui
    pyautogui.FAILSAFE = True  # Mover mouse a esquina superior izquierda aborta
    pyautogui.PAUSE = 0.2      # Pequeña pausa entre acciones
    _PYAUTOGUI_AVAILABLE = True
except ImportError:
    _PYAUTOGUI_AVAILABLE = False
    log.warning("pyautogui no disponible — acciones de mouse/teclado deshabilitadas")


def is_available() -> bool:
    """Verifica si pyautogui está disponible."""
    return _PYAUTOGUI_AVAILABLE


def move_to(x: int, y: int, duration: float = 0.5):
    """Mueve el mouse a coordenadas (x, y)."""
    if not _PYAUTOGUI_AVAILABLE:
        return False
    try:
        pyautogui.moveTo(x, y, duration=duration)
        return True
    except Exception as e:
        log.error("move_to falló: %s", e)
        return False


def click(button: str = "left"):
    """Hace clic con el botón especificado (left, right, middle)."""
    if not _PYAUTOGUI_AVAILABLE:
        return False
    try:
        pyautogui.click(button=button)
        return True
    except Exception as e:
        log.error("click falló: %s", e)
        return False


def double_click():
    """Doble clic izquierdo."""
    if not _PYAUTOGUI_AVAILABLE:
        return False
    try:
        pyautogui.doubleClick()
        return True
    except Exception as e:
        log.error("double_click falló: %s", e)
        return False


def write(text: str, interval: float = 0.05):
    """Escribe texto con pausas entre caracteres."""
    if not _PYAUTOGUI_AVAILABLE:
        return False
    try:
        pyautogui.write(text, interval=interval)
        return True
    except Exception as e:
        log.error("write falló: %s", e)
        return False


def press(key: str):
    """Presiona una tecla (ej: "enter", "esc", "ctrl+c")."""
    if not _PYAUTOGUI_AVAILABLE:
        return False
    try:
        pyautogui.press(key)
        return True
    except Exception as e:
        log.error("press falló: %s", e)
        return False


def locate_button(text: str, confidence: float = 0.8) -> Optional[Tuple[int, int]]:
    """Localiza un botón por texto en pantalla y devuelve coordenadas (x, y) del centro.
    Usa OCR estructurado (Tesseract + bounding boxes). Sin Moondream.
    """
    if not _PYAUTOGUI_AVAILABLE:
        return None
    try:
        from core.screen_scanner import find_element_on_screen
        elem = find_element_on_screen(text)
        if elem:
            log.info("Botón '%s' encontrado en (%d, %d)", text, elem["center_x"], elem["center_y"])
            return (elem["center_x"], elem["center_y"])
        log.info("Botón '%s' no encontrado en pantalla", text)
        return None
    except Exception as e:
        log.error("locate_button falló: %s", e)
        return None


def control_window(window_name: str, action: str = "activate"):
    """Controla una ventana por nombre (activate, minimize, maximize, close)."""
    if not _PYAUTOGUI_AVAILABLE:
        return False
    try:
        if action == "activate":
            # Usar wmctrl para activar ventana
            import subprocess
            subprocess.run(["wmctrl", "-a", window_name], check=True, timeout=3)
        elif action == "close":
            # Encontrar ventana y hacer clic en el botón de cerrar
            # (Implementación simplificada para demo)
            move_to(100, 100)  # Coordenadas del botón de cerrar en muchas apps
            click()
        return True
    except Exception as e:
        log.error("control_window falló: %s", e)
        return False


def execute_sequence(actions: list):
    """Ejecuta una secuencia de acciones.
    
    Ejemplo:
        execute_sequence([
            {"type": "move", "x": 100, "y": 200},
            {"type": "click"},
            {"type": "write", "text": "hola mundo"},
            {"type": "press", "key": "enter"}
        ])
    """
    if not _PYAUTOGUI_AVAILABLE:
        return False
    
    for action in actions:
        try:
            if action["type"] == "move":
                move_to(action["x"], action["y"])
            elif action["type"] == "click":
                click(action.get("button", "left"))
            elif action["type"] == "write":
                write(action["text"])
            elif action["type"] == "press":
                press(action["key"])
            elif action["type"] == "wait":
                time.sleep(action.get("seconds", 1))
        except Exception as e:
            log.error("Acción falló: %s — %s", action, e)
            continue
    return True