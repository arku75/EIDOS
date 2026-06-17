"""
EIDOS core/perception.py — Percepción Multicapa (Fase 1 del Roadmap)
=====================================================================
Según el Oráculo 2:
  1. Screenshot capture estable           ✅ (ya existe en tools.py)
  2. OCR layer (Tesseract)               ✅ → mapa {texto: bbox(x1,y1,x2,y2)}
  3. AT-SPI2 integration                 ✅ → árbol de widgets con coords
  4. Vision grounding (moondream2)       ✅ → upgrade de moondream:latest
  5. Escalation model (llama3.2-vision)  ✅ → solo bajo condición explícita

Integración F27 (2026-03-01):
  0. GUIObserver (NUEVO — método 0, MÁS PRIORITARIO):
     - AT-SPI2 + Playwright DOM + VLM moondream2 + ResourceMonitor
     - Scroll progresivo 10% por sección
     - Si disponible, HybridTargeter lo usa como fuente primaria

El Oráculo 2 validó: OCR Pre-paso + AT-SPI2 son el método correcto (no Grid Overlay).
"""
from __future__ import annotations

import base64
import json
import os
import subprocess
import time
import urllib.request
from dataclasses import dataclass
from typing import Any

OLLAMA_URL   = "http://localhost:11434"
# [S122-I] VLM por defecto = LiquidAI LFM2-VL-450M (mismo ecosistema que EIDOS,
# ~11x más rápido que moondream en CPU, mejor calidad). Configurable por env.
# Fallback a moondream si LFM2-VL no está. Ver TASK.md S122-I.
VISION_FAST  = os.environ.get("EIDOS_VLM_MODEL", "lfm2-vl:latest")
VISION_DEEP  = os.environ.get("EIDOS_VLM_DEEP", "lfm2-vl:latest")
SS_DIR       = os.path.expanduser("~/.eidos/screenshots")
os.makedirs(SS_DIR, exist_ok=True)

# ── GUI Observer (F27) — Método 0 de percepción ──────────────────────────────
HAS_GUI_OBS = False
try:
    from core.gui_observer import (
        get_screen_state, get_observer, get_resources,
        ScreenState, DOMElement, ResourceMonitor,
        scroll_analyze_full, format_page_analysis,
    )
    HAS_GUI_OBS = True
except ImportError:
    pass


# ══════════════════════════════════════════════════════════════════════════════
#  BOUNDING BOX — Estructura de datos para elementos UI detectados
# ══════════════════════════════════════════════════════════════════════════════

@dataclass
class UIElement:
    """
    Representa un elemento de UI detectado por cualquier método de percepción.
    """
    text:     str           # Texto del elemento
    x:        int           # Coordenada X del centro
    y:        int           # Coordenada Y del centro
    x1:       int           # Left
    y1:       int           # Top
    x2:       int           # Right
    y2:       int           # Bottom
    source:   str = "ocr"   # "ocr" | "atspi" | "dom" | "vision"
    role:     str = ""      # Para AT-SPI2: "button", "entry", "label", etc.
    
    @property
    def center(self) -> tuple[int, int]:
        return (self.x, self.y)
    
    def __repr__(self) -> str:
        return f"UIElement({self.text!r}, center=({self.x},{self.y}), source={self.source})"


# ══════════════════════════════════════════════════════════════════════════════
#  FASE 1.1 — Screenshot captura estable
# ══════════════════════════════════════════════════════════════════════════════

def take_screenshot(label: str = "") -> str | None:
    """
    Toma screenshot estable. Prueba scrot → gnome-screenshot → pyautogui.
    Returns: path al archivo PNG, o None si falla.
    """
    ts   = int(time.time())
    name = f"screen_{label+'_' if label else ''}{ts}.png"
    path = os.path.join(SS_DIR, name)
    
    for cmd in [
        f"scrot '{path}'",
        f"gnome-screenshot -f '{path}'",
    ]:
        r = subprocess.run(cmd, shell=True, capture_output=True)
        if r.returncode == 0 and os.path.exists(path):
            print(f"\033[94m[PERCEPTION]\033[0m Screenshot: {name}")
            return path
    
    # Fallback: pyautogui
    try:
        import pyautogui
        img = pyautogui.screenshot()
        img.save(path)
        if os.path.exists(path):
            return path
    except Exception:
        pass  # error no crítico, continuar
    print("\033[91m[PERCEPTION] ERROR: No se pudo tomar screenshot.\033[0m")
    return None


# ══════════════════════════════════════════════════════════════════════════════
#  FASE 1.2 — OCR Layer (Tesseract)
#  El Oráculo 2: "OCR Pre-paso: Tesseract → {texto: bbox}"
# ══════════════════════════════════════════════════════════════════════════════

# S124: backend OCR seleccionable. Tesseract por defecto (no rompe nada);
# EIDOS_OCR=rapid usa RapidOCR (modelos PaddleOCR vía ONNX, mejor calidad en
# layouts/tablas/PDFs). Mismo formato de salida UIElement. Singleton perezoso.
_RAPID_OCR = None


def _ocr_rapid(screenshot_path: str) -> list[UIElement]:
    """OCR con RapidOCR (modelos PaddleOCR exportados a ONNX). Mejor calidad que
    Tesseract sin arrastrar PaddlePaddle. Devuelve el MISMO formato UIElement."""
    global _RAPID_OCR
    if _RAPID_OCR is None:
        from rapidocr_onnxruntime import RapidOCR
        _RAPID_OCR = RapidOCR()
    result, _elapsed = _RAPID_OCR(screenshot_path)
    elements: list[UIElement] = []
    for box, text, conf in (result or []):
        text = (text or "").strip()
        try:
            if not text or float(conf) < 0.5:
                continue
        except (TypeError, ValueError):
            if not text:
                continue
        xs = [p[0] for p in box]
        ys = [p[1] for p in box]
        x1, y1, x2, y2 = int(min(xs)), int(min(ys)), int(max(xs)), int(max(ys))
        elements.append(UIElement(
            text=text, x=(x1 + x2) // 2, y=(y1 + y2) // 2,
            x1=x1, y1=y1, x2=x2, y2=y2, source="ocr"))
    print(f"\033[94m[PERCEPTION]\033[0m OCR(rapid): {len(elements)} elementos detectados")
    return elements


def ocr_screenshot(screenshot_path: str) -> list[UIElement]:
    """
    Aplica OCR al screenshot y devuelve elementos con texto y coordenadas.
    Backend por env EIDOS_OCR: 'tesseract' (default) | 'rapid' (RapidOCR/ONNX).
    Requiere: sudo apt install tesseract-ocr python3-pytesseract

    Returns: Lista de UIElement con text, x, y, bbox.
    """
    if os.environ.get("EIDOS_OCR", "tesseract").lower() in ("rapid", "rapidocr", "paddle"):
        try:
            return _ocr_rapid(screenshot_path)
        except Exception as e:
            print(f"\033[93m[PERCEPTION]\033[0m RapidOCR falló ({e}), fallback a Tesseract")
    try:
        import pytesseract
        from PIL import Image
    except ImportError:
        print("\033[93m[PERCEPTION] pytesseract no instalado. Instala: pip install pytesseract pillow\033[0m")
        return []
    
    try:
        img  = Image.open(screenshot_path)
        # S125-FIX: Reducir 4K a 1920x1080 para OCR rápido (~15s vs timeout)
        w, h = img.size
        if w > 1920:
            img = img.resize((1920, int(h * 1920 / w)), Image.LANCZOS)
        data = pytesseract.image_to_data(img, output_type=pytesseract.Output.DICT,
                                          lang="spa+eng", timeout=60)
    except Exception as e:
        print(f"\033[91m[PERCEPTION] OCR error: {e}\033[0m")
        return []
    
    elements = []
    n = len(data["text"])
    
    for i in range(n):
        text = data["text"][i].strip()
        conf = int(data["conf"][i])
        
        if not text or conf < 50:   # Ignorar texto vacío o de baja confianza
            continue
        
        x1 = data["left"][i]
        y1 = data["top"][i]
        w  = data["width"][i]
        h  = data["height"][i]
        x2 = x1 + w
        y2 = y1 + h
        cx = x1 + w // 2
        cy = y1 + h // 2
        
        elements.append(UIElement(
            text=text, x=cx, y=cy,
            x1=x1, y1=y1, x2=x2, y2=y2,
            source="ocr"
        ))
    
    print(f"\033[94m[PERCEPTION]\033[0m OCR: {len(elements)} elementos detectados")
    return elements


def find_element_by_text(elements: list[UIElement], query: str,
                          fuzzy: bool = True) -> UIElement | None:
    """
    Busca un elemento por texto. Con fuzzy=True acepta coincidencias parciales.
    
    Ejemplo:
        elems = ocr_screenshot(path)
        btn = find_element_by_text(elems, "Enviar")
        if btn:
            mouse_click(*btn.center)
    """
    query_lower = query.lower()
    
    # Búsqueda exacta primero
    for elem in elements:
        if elem.text.lower() == query_lower:
            return elem
    
    # Búsqueda parcial (fuzzy)
    if fuzzy:
        for elem in elements:
            if query_lower in elem.text.lower() or elem.text.lower() in query_lower:
                return elem
    
    return None


# ══════════════════════════════════════════════════════════════════════════════
#  FASE 1.3 — AT-SPI2 Integration
#  El Oráculo 2: "AT-SPI2: API accesibilidad X11 para apps GTK/Qt"
#  Sin VLM. Con coords exactas. Para apps que exponen accesibilidad.
# ══════════════════════════════════════════════════════════════════════════════

def get_accessible_elements(app_name: str | None = None) -> list[UIElement]:
    """
    Usa AT-SPI2 para obtener el árbol de accesibilidad de apps GTK/Qt.
    Devuelve elementos con texto y coordenadas reales.
    
    Requiere: sudo apt install python3-pyatspi at-spi2-core
    
    Args:
        app_name: Filtrar por nombre de aplicación (ej: "firefox", "gedit")
    """
    try:
        import pyatspi
    except ImportError:
        print("\033[93m[PERCEPTION] pyatspi no instalado. Instala: sudo apt install python3-pyatspi at-spi2-core\033[0m")
        return _fallback_atspi_via_shell(app_name)
    
    elements = []
    
    try:
        desktop = pyatspi.Registry.getDesktop(0)
        
        def recurse(acc, depth: int = 0) -> None:
            if depth > 20:  # Límite de profundidad para evitar bucles
                return
            try:
                name  = acc.name or ""
                role  = acc.getRoleName() or ""
                
                # Filtrar por app si se especifica
                if app_name and depth == 1:
                    if app_name.lower() not in name.lower():
                        return
                
                # Obtener bounding box
                try:
                    ext = acc.queryComponent().getExtents(pyatspi.DESKTOP_COORDS)
                    x1, y1, w, h = ext.x, ext.y, ext.width, ext.height
                    if w > 0 and h > 0 and name:
                        elements.append(UIElement(
                            text=name,
                            x=x1 + w // 2, y=y1 + h // 2,
                            x1=x1, y1=y1, x2=x1+w, y2=y1+h,
                            source="atspi", role=role
                        ))
                except Exception:
                    pass  # error no crítico, continuar
                for i in range(acc.childCount):
                    recurse(acc.getChildAtIndex(i), depth + 1)
            except Exception:
                pass  # error no crítico, continuar
        recurse(desktop)
        print(f"\033[94m[PERCEPTION]\033[0m AT-SPI2: {len(elements)} elementos accesibles")
    
    except Exception as e:
        print(f"\033[93m[PERCEPTION] AT-SPI2 error: {e}. Usando fallback.\033[0m")
        return _fallback_atspi_via_shell(app_name)
    
    return elements


def _fallback_atspi_via_shell(app_name: str | None = None) -> list[UIElement]:
    """
    Fallback: usa xdotool para detectar ventanas y sus posiciones básicas.
    Menos preciso que pyatspi pero no requiere librerías extra.
    """
    try:
        cmd = "xdotool search --onlyvisible --name . getactivewindow getwindowgeometry"
        r = subprocess.run(cmd, shell=True, capture_output=True, text=True, timeout=5)
        if r.returncode != 0:
            return []
        
        # Parse básico de xdotool
        elements = []
        lines = r.stdout.strip().split("\n")
        for line in lines:
            if "Position:" in line or "Geometry:" in line:
                pass  # Parse básico, retorna vacío si no hay info clara
        
        return elements
    except Exception:
        return []


# ══════════════════════════════════════════════════════════════════════════════
#  FASE 1.4 — Vision Grounding (moondream2 / llama3.2-vision)
#  El Oráculo 2: "moondream2 para loop frecuente, llama3.2 bajo condición"
# ══════════════════════════════════════════════════════════════════════════════

def analyze_screen(
    screenshot_path: str,
    question: str = "¿Qué elementos de UI ves? Describe brevemente.",
    deep: bool = False,
    max_tokens: int = 0
) -> str:
    """
    Analiza un screenshot con VLM.

    Args:
        screenshot_path: Ruta al PNG.
        question: Pregunta sobre el contenido.
        deep: Si True, usa llama3.2-vision (lento ~25s). False → moondream2 (~8s).
        max_tokens: 0 = SIN límite (SER: sistema neuronal vivo). >0 = límite opcional.

    Returns:
        Descripción textual del contenido visual.
    """
    model = VISION_DEEP if deep else VISION_FAST

    try:
        with open(screenshot_path, "rb") as f:
            img_b64 = base64.b64encode(f.read()).decode()
    except Exception as e:
        return f"[VISION ERROR] No se pudo leer {screenshot_path}: {e}"

    # [S122-I] SIN num_predict por defecto (SER): EIDOS es un sistema neuronal
    # vivo, no se le limita la respuesta. Solo limitar si max_tokens > 0.
    _opts = {"num_predict": max_tokens} if max_tokens and max_tokens > 0 else {}
    # [S122-I] Usar /api/chat (no /api/generate): los VLM modernos (LFM2-VL)
    # necesitan el template de chat para procesar la imagen — con /generate
    # devolvían respuesta vacía.
    payload = json.dumps({
        "model":   model,
        "messages": [{"role": "user", "content": question, "images": [img_b64]}],
        "stream":  False,
        "options": _opts
    }).encode()

    req = urllib.request.Request(
        f"{OLLAMA_URL}/api/chat",
        data=payload,
        headers={"Content-Type": "application/json"}
    )

    try:
        start = time.time()
        with urllib.request.urlopen(req, timeout=180) as resp:
            data    = json.load(resp)
            result  = data.get("message", {}).get("content", "") or "?"
            elapsed = round(time.time() - start, 1)
            print(f"\033[94m[PERCEPTION]\033[0m Vision({model.split(':')[0]}): {elapsed}s → {result[:100]}")  # pyre-ignore[arg-type]
            return result
    except Exception as e:
        return f"[VISION ERROR] {e}"


# ══════════════════════════════════════════════════════════════════════════════
#  HYBRID TARGETING — El método inteligente del Oráculo 2
#  Decide automáticamente qué método de percepción usar
# ══════════════════════════════════════════════════════════════════════════════

class HybridTargeter:
    """
    Selecciona automáticamente el mejor método para encontrar un elemento UI.
    El Oráculo 2: "Hybrid targeting: decide inteligentemente DOM o píxeles."

    Prioridad (más a menos preciso y rápido):
      1. DOM (Playwright) — Para páginas web
      2. AT-SPI2          — Para apps GTK/Qt nativas
      3. OCR (Tesseract)  — Para cualquier app con texto visible
      4. Vision (VLM)     — Último recurso
    """
    
    def __init__(self) -> None:
        self._last_screenshot: str | None = None
        self._cached_ocr: list[UIElement] = []
    
    def find(self, target_text: str,
             context: str = "desktop",
             url: str | None = None) -> "UIElement | None":
        """
        Busca un elemento UI usando el mejor método disponible.
        
        Orden de prioridad (más a menos rápido y preciso):
          0. GUIObserver (F27) — AT-SPI2 + Playwright DOM ya cacheados
          1. AT-SPI2 — Para apps GTK/Qt nativas
          2. OCR (Tesseract) — Para cualquier app con texto visible
          3. Vision (VLM) — Último recurso
        
        Args:
            target_text: Texto del elemento a encontrar (ej: "Submit", "Search").
            context: "web" | "desktop" | "auto"
            url: Si context="web", la URL de la página.
        
        Returns:
            UIElement con coordenadas, o None si no se encuentra.
        """
        # ── Método 0: GUIObserver (F27) — elementos ya analizados ────────────
        if HAS_GUI_OBS:
            try:
                obs = get_observer()
                state = obs.current_state or get_screen_state(use_vlm=False, use_dom=True)
                query_lower = target_text.lower()
                for el in state.elements:
                    if el.text and query_lower in el.text.lower():
                        print(f"\033[92m[TARGET]\033[0m GUIObserver → '{target_text}' en ({el.x},{el.y})")
                        return UIElement(
                            text=el.text, x=el.x, y=el.y,
                            x1=el.x, y1=el.y, x2=el.x + el.w, y2=el.y + el.h,
                            source="gui_observer", role=el.role
                        )
            except Exception:
                pass  # error no crítico, continuar
        # ── Método 1: AT-SPI2 (para apps nativas) ────────────────────────────
        if context in ("desktop", "auto"):
            atspi_elements = get_accessible_elements()
            elem = find_element_by_text(atspi_elements, target_text)
            if elem:
                print(f"\033[92m[TARGET]\033[0m AT-SPI2 → '{target_text}' en ({elem.x},{elem.y})")
                return elem
        
        # ── Método 2: OCR (screenshot + Tesseract) ───────────────────────────
        screenshot = take_screenshot(f"target_{target_text[:10]}")  # pyre-ignore[arg-type]
        if screenshot:
            self._last_screenshot = screenshot
            ocr_elements = ocr_screenshot(screenshot)
            self._cached_ocr = ocr_elements
            
            elem = find_element_by_text(ocr_elements, target_text)
            if elem:
                print(f"\033[92m[TARGET]\033[0m OCR → '{target_text}' en ({elem.x},{elem.y})")
                return elem
        
        # ── Método 3: Vision VLM (último recurso) ────────────────────────────
        if screenshot:
            print(f"\033[93m[TARGET]\033[0m OCR falló, escalando a VLM...\033[0m")
            description = analyze_screen(
                screenshot,
                question=f"Where exactly (pixel coordinates) is the element labeled '{target_text}'? "
                         f"Reply ONLY with: x=<number>, y=<number>",
                deep=False
            )  # [S122-I] sin max_tokens (SER): sistema vivo
            import re
            m = re.search(r'x=(\d+).*?y=(\d+)', description, re.IGNORECASE)
            if m:
                x, y = int(m.group(1)), int(m.group(2))
                print(f"\033[92m[TARGET]\033[0m VLM → '{target_text}' en ({x},{y})")
                return UIElement(text=target_text, x=x, y=y, x1=x-5, y1=y-5, x2=x+5, y2=y+5, source="vision")
        
        print(f"\033[91m[TARGET]\033[0m No se encontró: '{target_text}'\033[0m")
        return None



# Singleton global
targeter = HybridTargeter()


# ── Test rápido ──────────────────────────────────────────────────────────────
if __name__ == "__main__":
    print("=== Test Perception Layer ===")
    
    # Test screenshot
    path = take_screenshot("test")
    if path:
        print(f"Screenshot: {path}")
        
        # Test OCR
        elements = ocr_screenshot(path)
        print(f"OCR elements: {len(elements)}")
        if elements:
            print(f"  Primeros 3: {elements[:3]}")  # pyre-ignore[arg-type]
        
        # Test Vision
        desc = analyze_screen(path, "¿Qué ves en esta pantalla?")
        print(f"Vision: {desc[:150]}")  # pyre-ignore[arg-type]
    else:
        print("No se pudo tomar screenshot (entorno headless?)")
    
    print("\n✅ Perception Layer lista")
