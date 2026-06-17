import os
import time
import base64
from pathlib import Path
from io import BytesIO
import pyautogui
import mss
from PIL import Image

# Configuración de seguridad
pyautogui.FAILSAFE = True  # Moviendo el ratón a una esquina se aborta
pyautogui.PAUSE = 0.5      # Pausa entre acciones para dar tiempo al OS

SCREENSHOT_DIR = Path("/tmp/eidos_vision")
os.makedirs(SCREENSHOT_DIR, exist_ok=True)

def take_screenshot(save_name: str = "current_screen.png") -> str:
    """Toma una captura de pantalla y devuelve la ruta absoluta."""
    filepath = str(SCREENSHOT_DIR / save_name)
    with mss.mss() as sct:
        sct.shot(mon=-1, output=filepath)  # mon=-1 captura todos los monitores
    return filepath

def get_image_base64(filepath: str) -> str:
    """Lee una imagen y la devuelve en Base64 para pasarla al LLM (Llama 3.2 Vision)."""
    if not os.path.exists(filepath):
        return ""
    with open(filepath, "rb") as image_file:
        return base64.b64encode(image_file.read()).decode('utf-8')

# ── Herramientas de Interacción (Computer Use) ──

def move_mouse(x: int, y: int) -> str:
    """Mueve el ratón a coordenadas X,Y."""
    try:
        pyautogui.moveTo(x, y, duration=0.3)
        return f"Ratón movido a ({x}, {y})"
    except Exception as e:
        return f"Error moviendo ratón: {e}"

def click_mouse(x: int = None, y: int = None, button: str = "left", clicks: int = 1) -> str:
    """Hace clic con el ratón."""
    try:
        if x is not None and y is not None:
            pyautogui.click(x=x, y=y, button=button, clicks=clicks)
            return f"Clic {button}x{clicks} en ({x}, {y})"
        else:
            pyautogui.click(button=button, clicks=clicks)
            return f"Clic {button}x{clicks} en posición actual"
    except Exception as e:
        return f"Error haciendo clic: {e}"

def type_text(text: str, press_enter: bool = False) -> str:
    """Escribe texto simulando pulsaciones de teclado."""
    try:
        pyautogui.write(text, interval=0.02)
        if press_enter:
            pyautogui.press('enter')
        return f"Texto escrito: '{text}'" + (" (Enter pulsado)" if press_enter else "")
    except Exception as e:
        return f"Error escribiendo: {e}"

def press_key(key: str) -> str:
    """Pulsa una tecla especial (ej: 'enter', 'esc', 'ctrl', 'alt', 'tab', 'win')."""
    try:
        pyautogui.press(key)
        return f"Tecla '{key}' pulsada"
    except Exception as e:
        return f"Error pulsando tecla: {e}"

def hotkey(*keys) -> str:
    """Pulsa un atajo de teclado (ej: 'ctrl', 'c')."""
    try:
        pyautogui.hotkey(*keys)
        return f"Atajo {keys} pulsado"
    except Exception as e:
        return f"Error en atajo: {e}"

def scroll(amount: int) -> str:
    """Hace scroll. Positivo = arriba, Negativo = abajo."""
    try:
        pyautogui.scroll(amount)
        return f"Scroll {'arriba' if amount > 0 else 'abajo'} ({amount})"
    except Exception as e:
        return f"Error en scroll: {e}"

if __name__ == "__main__":
    # Pequeño test
    print("Captura tomada en:", take_screenshot("test.png"))
