import os
import io
import json
import base64
import urllib.request
import subprocess
from datetime import datetime
from PIL import Image

OLLAMA_URL = "http://localhost:11434"
DEFAULT_VISION_MODEL = "moondream:latest"
SCREENSHOT_DIR = os.path.expanduser("~/.eidos/screenshots")
os.makedirs(SCREENSHOT_DIR, exist_ok=True)

def take_screenshot() -> str:
    """Captura la pantalla actual usando scrot y devuelve la ruta del archivo."""
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    path = os.path.join(SCREENSHOT_DIR, f"screen_{timestamp}.png")
    try:
        # Usamos scrot para capturar la pantalla completa
        subprocess.run(["scrot", "-z", path], check=True)
        return path
    except Exception as e:
        print(f"[ERROR CAPTURA] {e}")
        return ""

def _encode_image(image_path: str) -> str:
    """Lee y codifica la imagen a Base64 sin sobrecargar la memoria."""
    with Image.open(image_path) as img:
        img = img.convert("RGB")
        max_size = (1280, 720) 
        img.thumbnail(max_size, Image.Resampling.LANCZOS)
        
        buffered = io.BytesIO()
        img.save(buffered, format="JPEG", quality=85)
        return base64.b64encode(buffered.getvalue()).decode('utf-8')

def analyze_screen(image_path: str, prompt: str = "Describe brevemente la interfaz gráfica, qué aplicación está abierta y qué elementos interactivos principales ves.", model: str = DEFAULT_VISION_MODEL) -> str:
    """Envía una captura al modelo de visión (Moondream/Llama3.2-Vision) con Smart Cache."""
    if not os.path.exists(image_path):
        return f"[ERROR] Imagen no encontrada: {image_path}"

    # -- Smart Cache: Revisar cache primero --
    try:
        from core.smart_cache import get_cached_vision, cache_vision_result
        cached = get_cached_vision(image_path, prompt)
        if cached is not None:
            print(f"💾 [Cache HIT] Vision cached para {image_path}")
            return cached
    except ImportError:
        pass  # Sin cache, continuar normalmente

    try:
        b64_image = _encode_image(image_path)

        payload = {
            "model": model,
            "messages": [
                {
                    "role": "user",
                    "content": prompt,
                    "images": [b64_image]
                }
            ],
            "stream": False,
            "options": {"num_ctx": 4096, "num_predict": 512}
        }

        req = urllib.request.Request(
            f"{OLLAMA_URL}/api/chat",
            data=json.dumps(payload).encode(),
            headers={"Content-Type": "application/json"}
        )

        with urllib.request.urlopen(req, timeout=120) as resp:
            data = json.load(resp)
            result = data.get("message", {}).get("content", "[Visión sin respuesta]")

            # -- Smart Cache: Guardar resultado --
            try:
                cache_vision_result(image_path, prompt, result)
            except Exception:
                pass  # error no crítico, continuar
            return result

    except Exception as e:
        return f"[ERROR VISION VLM: {model}] {e}"

# ── Stub classes/functions for perception.py compatibility ──────────────────
# These symbols were expected by perception.py but not implemented in the
# original gui_observer. They delegate to screen_scanner where possible.

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional


@dataclass
class DOMElement:
    """Stub DOM element compatible with perception.py imports."""
    text: str = ""
    x: int = 0
    y: int = 0
    w: int = 0
    h: int = 0
    role: str = ""
    tag: str = ""
    attributes: Dict[str, str] = field(default_factory=dict)


@dataclass
class ScreenState:
    """Stub screen state with elements list."""
    elements: List[DOMElement] = field(default_factory=list)
    browser_url: str = ""
    active_window_title: str = ""
    screenshot_path: str = ""


class ResourceMonitor:
    """Stub resource monitor."""
    def __init__(self):
        self.cpu_pct: float = 0.0
        self.ram_mb: float = 0.0

    def snapshot(self) -> Dict[str, float]:
        return {"cpu": self.cpu_pct, "ram": self.ram_mb}


_observer_instance: Optional[object] = None


def get_observer():
    """Stub: returns a simple observer object."""
    global _observer_instance
    if _observer_instance is None:
        _observer_instance = type('_Observer', (), {
            'current_state': None,
            'resources': ResourceMonitor(),
        })()
    return _observer_instance


def get_resources() -> ResourceMonitor:
    """Stub: returns a ResourceMonitor instance."""
    return ResourceMonitor()


def get_screen_state(use_vlm: bool = False, use_dom: bool = True) -> ScreenState:
    """Get current screen state via screen_scanner fallback.

    Returns a ScreenState populated from screen_scanner.get_screen_context()
    and the active window list. Does NOT use VLM by default.
    """
    state = ScreenState()
    try:
        from core.screen_scanner import get_screen_context, get_open_windows
        ctx = get_screen_context()
        state.active_window_title = ctx.get("active_window", "")
        # Convert screen_scanner elements to DOMElement
        for el in ctx.get("elements", []):
            state.elements.append(DOMElement(
                text=el.get("text", ""),
                x=el.get("x", 0), y=el.get("y", 0),
                role="ocr",
            ))
        # Detect if any window looks like a browser
        windows = get_open_windows()
        for w in windows:
            name = w.get("name", "").lower()
            if any(kw in name for kw in ("firefox", "chrome", "chromium", "brave", "edge", "mozilla")):
                # Extract possible URL/title from browser window title
                for sep in (" — ", " - "):
                    if sep in w.get("name", ""):
                        state.browser_url = w["name"].split(sep)[0].strip()
                        break
                break
    except Exception:
        pass
    return state


def scroll_analyze_full(url: str = "", max_sections: int = 10) -> Dict[str, Any]:
    """Stub: scroll analysis not implemented in this module.
    Use eidos_browser or colony_studier for full page analysis."""
    return {"ok": False, "error": "scroll_analyze_full: use eidos_browser or colony_studier"}


def format_page_analysis(analysis: Dict[str, Any]) -> str:
    """Stub: format a page analysis result as text."""
    if not analysis or not analysis.get("ok"):
        return f"[Analysis unavailable: {analysis.get('error', 'unknown')}]" if analysis else "[No analysis]"
    sections = analysis.get("sections", [])
    lines = []
    for s in sections[:5]:
        lines.append(f"## {s.get('title', '?')}\n{s.get('summary', '')[:200]}")
    return "\n\n".join(lines)


if __name__ == "__main__":
    import sys
    if len(sys.argv) > 1 and sys.argv[1] == "--auto":
        print("📸 EIDOS capturando pantalla...")
        path = take_screenshot()
        if path:
            print(f"👁️ Analizando captura: {path}")
            print(analyze_screen(path))
    elif len(sys.argv) > 1:
        img_path = sys.argv[1]
        print(f"Analizando {img_path} con {DEFAULT_VISION_MODEL}...")
        print(analyze_screen(img_path))
    else:
        print("Uso: python3 core/gui_observer.py --auto  (o ruta a imagen)")
