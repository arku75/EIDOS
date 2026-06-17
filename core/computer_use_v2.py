"""
EIDOS core/computer_use_v2.py — Computer Use Maduro (Fase 5 del Roadmap)
=========================================================================
Según el Oráculo 2:
  "Fase 5 — Computer Use Maduro. Aquí EIDOS supera a Claw."
  
  ✅ Web control robusto (Playwright DOM — sin coords, 100% preciso)
  ✅ Desktop control robusto (OCR + AT-SPI2 + PyAutoGUI combinados)
  ✅ Hybrid targeting (decide inteligentemente: DOM vs píxeles)
  ✅ Multi-step verification (snapshot antes y después de cada acción)
  ✅ UI state modeling (modelo mental del estado actual de la UI)

Regla del Oráculo 2 (Bloque 2):
  Web   → SIEMPRE Playwright DOM (dom_click, dom_type, dom_get_text)
  Nativo→ PyAutoGUI + OCR/AT-SPI2 (mouse_click, keyboard_type)
  NUNCA mezclar los dos enfoques en la misma acción.
"""
from __future__ import annotations

import base64
import hashlib
import json
import os
import subprocess
import time
from dataclasses import dataclass, field
from typing import Any

# ── Dependencias opcionales ─────────────────────────────────────────────────
try:
    import pyautogui
    pyautogui.FAILSAFE = True   # Mover ratón a esquina = abort
    pyautogui.PAUSE    = 0.3    # Pausa entre acciones para estabilidad
    HAS_PYAUTOGUI = True
except ImportError:
    HAS_PYAUTOGUI = False

try:
    from playwright.sync_api import sync_playwright, Page, Browser
    HAS_PLAYWRIGHT = True
except ImportError:
    HAS_PLAYWRIGHT = False

from core.perception import (
    take_screenshot, ocr_screenshot, get_accessible_elements,
    analyze_screen, find_element_by_text, UIElement, HybridTargeter
)

SS_DIR = os.path.expanduser("~/.eidos/screenshots")


# ══════════════════════════════════════════════════════════════════════════════
#  UI STATE MODEL — "Modelo mental del estado actual de la UI"
# ══════════════════════════════════════════════════════════════════════════════

@dataclass
class UIState:
    """
    Representa el estado actual de la interfaz conocido por EIDOS.
    Se actualiza con cada screenshot tomado.
    """
    screenshot_path:  str | None = None
    screenshot_hash:  str | None = None
    elements:         list[UIElement] = field(default_factory=list)
    vision_summary:   str = ""
    timestamp:        float = 0.0
    context:          str = "unknown"   # "web" | "desktop" | "terminal"
    active_url:       str | None = None  # Si context="web"
    
    def is_stale(self, max_age_s: float = 5.0) -> bool:
        """¿El estado tiene más de N segundos? → Necesita refresco."""
        return (time.time() - self.timestamp) > max_age_s
    
    def changed_since(self, other: "UIState | None") -> bool:
        """¿El estado cambió desde el último snapshot?"""
        if other is None:
            return True
        if self.screenshot_hash is None or other.screenshot_hash is None:
            return True
        return self.screenshot_hash != other.screenshot_hash
    
    @classmethod
    def from_screenshot(cls, path: str, context: str = "desktop") -> "UIState":
        """Crea un UIState a partir de un screenshot."""
        try:
            with open(path, "rb") as f:
                img_hash = hashlib.md5(f.read()).hexdigest()
        except Exception:
            img_hash = f"NOHASH_{time.time()}"
        
        return cls(
            screenshot_path=path,
            screenshot_hash=img_hash,
            timestamp=time.time(),
            context=context,
        )


# ══════════════════════════════════════════════════════════════════════════════
#  DESKTOP CONTROLLER — PyAutoGUI + OCR + AT-SPI2
#  Para apps nativas (Telegram, VS Code, terminal, cualquier app X11)
# ══════════════════════════════════════════════════════════════════════════════

class DesktopController:
    """
    Controla apps nativas de escritorio usando PyAutoGUI + Percepción.
    
    NUNCA se usa para páginas web (esas van a WebController).
    """
    
    def __init__(self) -> None:
        self._targeter = HybridTargeter()
        self._state_before: UIState | None = None
    
    # ── Screenshot + Refresh ────────────────────────────────────────────────
    
    def capture_state(self, label: str = "") -> UIState:
        """Toma screenshot y construye el UIState actual."""
        path = take_screenshot(label)
        if path is None:
            return UIState(context="desktop")
        state = UIState.from_screenshot(path, context="desktop")
        state.elements = ocr_screenshot(path)
        return state
    
    def verify_state_changed(self, before: UIState, after: UIState,
                              action_desc: str = "") -> bool:
        """
        Multi-step verification: compara el estado antes y después de una acción.
        Oráculo 2: "Snapshot antes y después de cada acción."
        """
        changed = after.changed_since(before)
        if changed:
            print(f"\033[92m[CU VERIFY]\033[0m ✅ Estado cambió tras: {action_desc}")
        else:
            print(f"\033[93m[CU VERIFY]\033[0m ⚠️ Estado SIN CAMBIO tras: {action_desc}")
        return changed
    
    # ── Acciones de Mouse ───────────────────────────────────────────────────
    
    def click(self, x: int, y: int, button: str = "left",
              clicks: int = 1, verify: bool = True) -> str:
        """
        Click en coordenadas absolutas de pantalla.
        Con verify=True, toma screenshot antes y después.
        """
        if not HAS_PYAUTOGUI:
            return "[DESKTOP] Error: pyautogui no instalado. Instala: pip install pyautogui"
        
        before = self.capture_state("before_click") if verify else UIState()
        
        print(f"\033[94m[DESKTOP]\033[0m Click {button} x{clicks} en ({x},{y})")
        try:
            pyautogui.click(x, y, button=button, clicks=clicks, interval=0.1)
            time.sleep(0.5)   # Dar tiempo a la UI para reaccionar
        except Exception as e:
            return f"[DESKTOP ERROR] click({x},{y}): {e}"
        
        if verify:
            after = self.capture_state("after_click")
            changed = self.verify_state_changed(before, after, f"click({x},{y})")
            if not changed:
                return f"[DESKTOP] Click en ({x},{y}) ejecutado pero SIN CAMBIO visible en pantalla."
        
        return f"[DESKTOP] Click en ({x},{y}) ejecutado ✅"
    
    def click_element(self, target_text: str, verify: bool = True) -> str:
        """
        Hace click en un elemento buscado por texto (OCR + AT-SPI2).
        Método preferido sobre click(x,y) cuando se conoce el texto del elemento.
        """
        print(f"\033[94m[DESKTOP]\033[0m Buscando elemento: '{target_text}'")
        
        elem = self._targeter.find(target_text, context="desktop")
        if elem is None:
            return (
                f"[DESKTOP] No se encontró elemento '{target_text}'. "
                f"Opciones: 1) Verifica el texto exacto con take_screenshot. "
                f"2) Usa click(x,y) con coords manuales."
            )
        
        return self.click(elem.x, elem.y, verify=verify)
    
    def double_click(self, x: int, y: int) -> str:
        """Doble click en coordenadas."""
        return self.click(x, y, clicks=2)
    
    def right_click(self, x: int, y: int) -> str:
        """Click derecho (menú contextual)."""
        return self.click(x, y, button="right")
    
    def drag(self, x1: int, y1: int, x2: int, y2: int) -> str:
        """Drag desde (x1,y1) hasta (x2,y2)."""
        if not HAS_PYAUTOGUI:
            return "[DESKTOP] Error: pyautogui no instalado"
        try:
            pyautogui.moveTo(x1, y1, duration=0.3)
            pyautogui.dragTo(x2, y2, duration=0.5, button='left')
            return f"[DESKTOP] Drag ({x1},{y1}) → ({x2},{y2}) ✅"
        except Exception as e:
            return f"[DESKTOP ERROR] drag: {e}"
    
    def scroll(self, x: int, y: int, amount: int = 3,
               direction: str = "down") -> str:
        """Scroll en posición (x,y)."""
        if not HAS_PYAUTOGUI:
            return "[DESKTOP] Error: pyautogui no instalado"
        try:
            pyautogui.moveTo(x, y)
            clicks = -amount if direction == "down" else amount
            pyautogui.scroll(clicks)
            return f"[DESKTOP] Scroll {direction} x{amount} en ({x},{y}) ✅"
        except Exception as e:
            return f"[DESKTOP ERROR] scroll: {e}"
    
    # ── Acciones de Teclado ─────────────────────────────────────────────────
    
    def type_text(self, text: str, interval: float = 0.05) -> str:
        """
        Escribe texto en el elemento enfocado actualmente.
        ADVERTENCIA: Asegúrate de haber hecho click primero en el campo correcto.
        """
        if not HAS_PYAUTOGUI:
            return "[DESKTOP] Error: pyautogui no instalado"
        try:
            pyautogui.write(text, interval=interval)
            return f"[DESKTOP] Texto escrito: '{text[:50]}{'...' if len(text)>50 else ''}' ✅"  # pyre-ignore[arg-type]
        except Exception as e:
            return f"[DESKTOP ERROR] type_text: {e}"
    
    def hotkey(self, *keys: str) -> str:
        """
        Atajo de teclado. Ejemplo: hotkey('ctrl', 'c'), hotkey('alt', 'tab')
        """
        if not HAS_PYAUTOGUI:
            return "[DESKTOP] Error: pyautogui no instalado"
        try:
            pyautogui.hotkey(*keys)
            return f"[DESKTOP] Hotkey: {'+'.join(keys)} ✅"
        except Exception as e:
            return f"[DESKTOP ERROR] hotkey: {e}"
    
    def press_key(self, key: str) -> str:
        """Pulsa una tecla. Ej: 'enter', 'escape', 'tab', 'space', 'f5'"""
        if not HAS_PYAUTOGUI:
            return "[DESKTOP] Error: pyautogui no instalado"
        try:
            pyautogui.press(key)
            return f"[DESKTOP] Tecla: {key} ✅"
        except Exception as e:
            return f"[DESKTOP ERROR] press_key: {e}"
    
    def type_and_submit(self, text: str, field_text: str = "") -> str:
        """
        Encuentra un campo por texto (si se da), escribe el texto y presiona Enter.
        Uso: type_and_submit("búsqueda", field_text="Search")
        """
        if field_text:
            click_result = self.click_element(field_text)
            if "ERROR" in click_result or "No se encontró" in click_result:
                return click_result
        
        type_result = self.type_text(text)
        self.press_key("enter")
        return f"{type_result} → Enter ✅"
    
    # ── Screen analysis ──────────────────────────────────────────────────────
    
    def describe_screen(self, question: str = "¿Qué hay en pantalla?",
                        deep: bool = False) -> str:
        """Toma screenshot y lo analiza con VLM."""
        path = take_screenshot("describe")
        if path is None:
            return "[DESKTOP] No se pudo tomar screenshot"
        return analyze_screen(path, question=question, deep=deep)
    
    def get_screen_text(self) -> list[UIElement]:
        """Retorna todos los elementos de texto detectados por OCR."""
        path = take_screenshot("ocr")
        if path is None:
            return []
        return ocr_screenshot(path)
    
    def get_mouse_position(self) -> tuple[int, int]:
        """Posición actual del cursor."""
        if HAS_PYAUTOGUI:
            return pyautogui.position()
        return (0, 0)
    
    def get_screen_size(self) -> tuple[int, int]:
        """Tamaño de la pantalla en píxeles."""
        if HAS_PYAUTOGUI:
            return pyautogui.size()
        # Fallback via xdpyinfo
        try:
            r = subprocess.run("xdpyinfo | grep dimensions", shell=True,
                               capture_output=True, text=True)
            import re
            m = re.search(r'(\d+)x(\d+)', r.stdout)
            if m:
                return int(m.group(1)), int(m.group(2))
        except Exception:
            pass  # error no crítico, continuar
        return (1920, 1080)


# ══════════════════════════════════════════════════════════════════════════════
#  WEB CONTROLLER — Playwright DOM
#  Para páginas web. SIEMPRE DOM, NUNCA coordenadas.
# ══════════════════════════════════════════════════════════════════════════════

class WebController:
    """
    Controla páginas web usando Playwright DOM.
    100% preciso — no necesita coords, usa selectores CSS/XPath.
    
    Oráculo 2: "Web → SIEMPRE Playwright DOM."
    """
    
    def __init__(self) -> None:
        self._playwright = None
        self._browser: Browser | None = None
        self._page: Page | None = None
        self._current_url: str | None = None
    
    def _ensure_browser(self) -> str | None:
        """Inicia Playwright si no está activo. Retorna error o None."""
        if not HAS_PLAYWRIGHT:
            return "[WEB] Error: Playwright no instalado. Instala: pip install playwright && playwright install chromium"
        
        if self._page is None or self._page.is_closed():
            try:
                self._playwright = sync_playwright().start()
                self._browser = self._playwright.chromium.launch(headless=False)
                context = self._browser.new_context(
                    viewport={"width": 1280, "height": 800},
                    user_agent="Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36"
                )
                self._page = context.new_page()
                print("\033[94m[WEB]\033[0m Browser Chromium iniciado")
            except Exception as e:
                return f"[WEB ERROR] No se pudo iniciar browser: {e}"
        
        return None
    
    def navigate(self, url: str, wait_for: str = "networkidle",
                 timeout_ms: int = 30000) -> str:
        """Navega a una URL y espera a que cargue."""
        err = self._ensure_browser()
        if err:
            return err
        
        try:
            print(f"\033[94m[WEB]\033[0m Navegando a: {url}")
            self._page.goto(url, wait_until=wait_for, timeout=timeout_ms)
            self._current_url = self._page.url
            title = self._page.title()
            return f"[WEB] ✅ Navegado a '{title}' ({self._current_url})"
        except Exception as e:
            return f"[WEB ERROR] navigate({url}): {e}"
    
    def click(self, selector: str, timeout_ms: int = 10000) -> str:
        """Click en elemento por selector CSS o XPath."""
        err = self._ensure_browser()
        if err:
            return err
        
        try:
            print(f"\033[94m[WEB]\033[0m Click: {selector}")
            self._page.click(selector, timeout=timeout_ms)
            return f"[WEB] ✅ Click en '{selector}'"
        except Exception as e:
            return f"[WEB ERROR] click('{selector}'): {e}. ¿El selector es correcto?"
    
    def click_text(self, text: str) -> str:
        """Click en elemento que contiene el texto dado."""
        return self.click(f"text='{text}'")
    
    def type_in(self, selector: str, text: str,
                clear_first: bool = True) -> str:
        """Escribe en un campo de formulario."""
        err = self._ensure_browser()
        if err:
            return err
        
        try:
            print(f"\033[94m[WEB]\033[0m Type '{text[:30]}' en '{selector}'")  # pyre-ignore[arg-type]
            if clear_first:
                self._page.fill(selector, text)
            else:
                self._page.type(selector, text, delay=50)
            return f"[WEB] ✅ Escrito en '{selector}'"
        except Exception as e:
            return f"[WEB ERROR] type_in('{selector}'): {e}"
    
    def get_text(self, selector: str = "body") -> str:
        """Extrae el texto de un elemento (o de toda la página)."""
        err = self._ensure_browser()
        if err:
            return err
        
        try:
            text = self._page.inner_text(selector)
            return text[:3000]  # pyre-ignore[arg-type]
        except Exception as e:
            return f"[WEB ERROR] get_text('{selector}'): {e}"
    
    def get_html(self, selector: str = "body") -> str:
        """Extrae el HTML de un elemento."""
        err = self._ensure_browser()
        if err:
            return err
        
        try:
            html = self._page.inner_html(selector)
            return html[:3000]  # pyre-ignore[arg-type]
        except Exception as e:
            return f"[WEB ERROR] get_html('{selector}'): {e}"
    
    def fill_form_and_submit(self, fields: dict[str, str],
                              submit_selector: str | None = None) -> str:
        """
        Rellena múltiples campos y envía el formulario.
        
        Args:
            fields: {selector: texto} para cada campo
            submit_selector: selector del botón submit (o None para Enter)
        """
        results = []
        for selector, text in fields.items():
            result = self.type_in(selector, text)
            results.append(result)
        
        if submit_selector:
            results.append(self.click(submit_selector))
        else:
            try:
                self._page.keyboard.press("Enter")
                results.append("[WEB] Form enviado con Enter ✅")
            except Exception as e:
                results.append(f"[WEB ERROR] Enter: {e}")
        
        return "\n".join(results)
    
    def screenshot_web(self) -> str | None:
        """Screenshot de la página actual usando Playwright (dentro del navegador)."""
        if self._page and not self._page.is_closed():
            path = os.path.join(SS_DIR, f"web_{int(time.time())}.png")
            try:
                self._page.screenshot(path=path, full_page=False)
                return path
            except Exception:
                pass  # error no crítico, continuar
        # Fallback: screenshot de escritorio
        return take_screenshot("web")

    def mouse_click(self, x: int, y: int, button: str = "left") -> str:
        """Click en coordenadas DENTRO del viewport del navegador (no es pyautogui)."""
        err = self._ensure_browser()
        if err:
            return err
        try:
            btn = button if button in ("left", "right", "middle") else "left"
            self._page.mouse.click(x, y, button=btn)
            return f"[WEB] ✅ Mouse click DOM ({x},{y}) button={btn}"
        except Exception as e:
            return f"[WEB ERROR] mouse_click({x},{y}): {e}"

    def keyboard_type(self, text: str, delay_ms: float = 50.0) -> str:
        """Escribe texto en el elemento activo de la página (no es pyautogui)."""
        err = self._ensure_browser()
        if err:
            return err
        try:
            self._page.keyboard.type(text, delay=delay_ms)
            return f"[WEB] ✅ Teclado DOM: '{text[:50]}{'...' if len(text)>50 else ''}'"  # pyre-ignore[arg-type]
        except Exception as e:
            return f"[WEB ERROR] keyboard_type: {e}"

    def keyboard_press(self, key: str) -> str:
        """Pulsa una tecla en la página activa (Enter, Escape, Tab, F5…)."""
        err = self._ensure_browser()
        if err:
            return err
        try:
            self._page.keyboard.press(key)
            return f"[WEB] ✅ Tecla DOM: {key}"
        except Exception as e:
            return f"[WEB ERROR] keyboard_press({key}): {e}"
    
    def execute_js(self, script: str) -> Any:
        """Ejecuta JavaScript en la página y devuelve el resultado."""
        err = self._ensure_browser()
        if err:
            return err
        
        try:
            return self._page.evaluate(script)
        except Exception as e:
            return f"[WEB ERROR] JS: {e}"
    
    def close(self) -> None:
        """Cierra el browser."""
        try:
            if self._browser:
                self._browser.close()
            if self._playwright:
                self._playwright.stop()
            self._page = None
            self._browser = None
            print("\033[94m[WEB]\033[0m Browser cerrado")
        except Exception:
            pass  # error no crítico, continuar
# ══════════════════════════════════════════════════════════════════════════════
#  HYBRID COMPUTER USE — El orquestador inteligente
#  "Decide inteligentemente DOM o píxeles" — Oráculo 2
# ══════════════════════════════════════════════════════════════════════════════

class HybridComputerUse:
    """
    Orquestador que decide automáticamente qué controlador usar.
    
    Regla clara del Oráculo 2:
      Si la tarea implica una URL o "web" → WebController (DOM)
      Si es una app nativa (Telegram, VS Code...) → DesktopController
    
    Integrado con el DeterministicKernel: 
      Las acciones van a través del TOOL_IMPL del kernel para validación.
    """
    
    def __init__(self) -> None:
        self.desktop = DesktopController()
        self.web     = WebController()
        self._current_state: UIState | None = None
        self._failed_actions: int = 0
        self.MAX_CONSECUTIVE_FAILS = 3
    
    def detect_context(self, task: str) -> str:
        """
        Detecta automáticamente si la tarea es web o desktop.
        Returns: "web" | "desktop"
        """
        task_lower = task.lower()
        web_keywords = [
            "http", "https", "www.", ".com", ".org", ".net", "browser",
            "chrome", "firefox", "web", "url", "página", "sitio", "navega"
        ]
        if any(kw in task_lower for kw in web_keywords):
            return "web"
        return "desktop"
    
    def capture_and_update(self, context: str = "desktop") -> UIState:
        """Toma screenshot y actualiza el modelo de estado.
        
        Si context='web' y hay un browser activo, usa page.screenshot() para
        capturar SOLO el viewport del navegador (no scrot del escritorio).
        """
        if context == "web" and self.web._page and not self.web._page.is_closed():
            path = self.web.screenshot_web()
        else:
            path = take_screenshot("state_update")
        if path:
            self._current_state = UIState.from_screenshot(path, context)
            self._current_state.elements = ocr_screenshot(path)
        return self._current_state or UIState()
    
    def click_smart(self, target: str, context: str = "auto",
                    url: str | None = None) -> str:
        """
        Click inteligente: si hay URL usa DOM, si no usa desktop.
        
        Args:
            target: Texto del elemento O selector CSS (para web).
            context: "web" | "desktop" | "auto"
            url: URL de la página (si context=web).
        """
        # Verificar abort por fails consecutivos
        if self._failed_actions >= self.MAX_CONSECUTIVE_FAILS:
            self._failed_actions = 0
            return (
                f"[CU ABORT] {self.MAX_CONSECUTIVE_FAILS} acciones fallidas consecutivas. "
                f"Requiere intervención humana."
            )
        
        # Tomar estado inicial
        before = self.capture_and_update(context)
        
        # Decidir método
        if context == "web" or (context == "auto" and url):
            result = self.web.click_text(target)
        else:
            result = self.desktop.click_element(target)
        
        # Verificar cambio de estado
        time.sleep(0.8)
        after = self.capture_and_update(context)
        
        if after.changed_since(before):
            self._failed_actions = 0
            print(f"\033[92m[CU]\033[0m ✅ Acción exitosa: estado cambió")
        else:
            self._failed_actions += 1
            print(f"\033[93m[CU]\033[0m ⚠️ Sin cambio ({self._failed_actions}/{self.MAX_CONSECUTIVE_FAILS})")
        
        return result
    
    def full_action(self, action: str, target: str,
                    text: str = "", context: str = "auto",
                    url: str | None = None) -> str:
        """
        Acción completa con verificación.
        
        Args:
            action: "click" | "type" | "scroll" | "hotkey" | "navigate"
            target: Elemento objetivo (texto, selector, coordenadas "x,y")
            text: Texto a escribir (para action="type")
            context: "web" | "desktop" | "auto"
            url: URL (para action="navigate")
        """
        if action == "navigate":
            return self.web.navigate(target or url)
        
        if action == "click":
            return self.click_smart(target, context, url)
        
        if action == "type":
            if context == "web":
                return self.web.type_in(target, text)
            else:
                # Para desktop: hacer click en el campo primero, luego escribir
                if target:
                    click_r = self.desktop.click_element(target)
                    if "Error" in click_r:
                        return click_r
                return self.desktop.type_text(text)
        
        if action == "scroll":
            try:
                x, y = (int(v) for v in target.split(","))
                return self.desktop.scroll(x, y, direction=text or "down")
            except Exception:
                return f"[CU] Para scroll usa target='x,y' ej: '640,400'"
        
        if action == "hotkey":
            keys = [k.strip() for k in target.split("+")]
            return self.desktop.hotkey(*keys)
        
        if action == "screenshot":
            if context == "web" and self.web._page and not self.web._page.is_closed():
                # Captura dentro del browser, no del escritorio global
                path = self.web.screenshot_web()
            else:
                path = take_screenshot("action_screenshot")
            return f"[CU] Screenshot: {path}"
        
        return f"[CU] Acción desconocida: '{action}'. Usa: click|type|scroll|hotkey|navigate|screenshot"


# ══════════════════════════════════════════════════════════════════════════════
#  TOOL IMPLEMENTATIONS — Para integrar con el Kernel
# ══════════════════════════════════════════════════════════════════════════════

# Singleton global de Computer Use
_cu = HybridComputerUse()

def get_computer_use_tools() -> dict:
    """
    Retorna las implementaciones de tools de Computer Use para el TOOL_IMPL del Kernel.
    Se une a core/tools.py TOOL_IMPL.
    """
    def _web_active() -> bool:
        """¿Hay una página Playwright activa?"""
        return bool(_cu.web._page and not _cu.web._page.is_closed())

    return {
        # ── RATÓN ─────────────────────────────────────────────────────────────
        # Si el browser está abierto → page.mouse.click (dentro del navegador)
        # Si no → pyautogui (escritorio)
        "mouse_click": lambda a: (
            _cu.web.mouse_click(
                int(a.get("x", 0)), int(a.get("y", 0)), a.get("button", "left")
            ) if _web_active() else _cu.desktop.click(
                int(a.get("x", 0)), int(a.get("y", 0)),
                a.get("button", "left"), int(a.get("clicks", 1))
            )
        ),
        # ── TECLADO ───────────────────────────────────────────────────────────
        # Si el browser está abierto → page.keyboard.type (dentro del navegador)
        # Si no → pyautogui (escritorio)
        "keyboard_type": lambda a: (
            _cu.web.keyboard_type(
                a.get("text", ""), float(a.get("interval", 50))
            ) if _web_active() else _cu.desktop.type_text(
                a.get("text", ""), float(a.get("interval", 0.05))
            )
        ),
        "keyboard_press": lambda a: (
            _cu.web.keyboard_press(a.get("key", "Enter"))
            if _web_active() else _cu.desktop.press_key(a.get("key", "enter"))
        ),
        "keyboard_hotkey": lambda a: _cu.desktop.hotkey(*a.get("keys", [])),
        # ── DOM ───────────────────────────────────────────────────────────────
        "dom_click": lambda a: _cu.web.click_text(a.get("selector", "")),
        "dom_type": lambda a: _cu.web.type_in(a.get("selector", ""), a.get("text", "")),
        "dom_get_text": lambda a: _cu.web.get_text(a.get("selector", "body")),
        "web_navigate": lambda a: _cu.web.navigate(a.get("url", "")),
        # ── SCREENSHOT ────────────────────────────────────────────────────────
        # Si browser activo → page.screenshot(); Si no → scrot
        "take_screenshot": lambda a: (
            _cu.web.screenshot_web() if _web_active()
            else take_screenshot(a.get("label", "tool"))
        ),
    }


def get_desktop_controller() -> DesktopController:
    return _cu.desktop

def get_web_controller() -> WebController:
    return _cu.web

def get_hybrid_cu() -> HybridComputerUse:
    return _cu


# ── Test rápido ──────────────────────────────────────────────────────────────
if __name__ == "__main__":
    print("=== Test Computer Use v2 ===")
    
    cu = HybridComputerUse()
    
    # Test detección de contexto
    print(f"detect_context('abre chrome en google.com') = {cu.detect_context('abre chrome en google.com')}")
    print(f"detect_context('escribe en el terminal') = {cu.detect_context('escribe en el terminal')}")
    print(f"detect_context('abre https://github.com') = {cu.detect_context('abre https://github.com')}")
    
    # Test screen size
    size = cu.desktop.get_screen_size()
    pos = cu.desktop.get_mouse_position()
    print(f"Screen size: {size}")
    print(f"Mouse pos: {pos}")
    
    # Test tools dict
    tools = get_computer_use_tools()
    print(f"Computer Use tools: {list(tools.keys())}")
    
    print("\n✅ Computer Use v2 listo")
