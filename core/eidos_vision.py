"""
EIDOS Vision — Los Ojos de EIDOS
Captura pantalla, OCR, detección de UI, comprensión visual del entorno

Capacidades:
- Ver la pantalla en tiempo real (screenshots)
- Leer texto en pantalla (OCR)
- Detectar elementos de UI (botones, campos, ventanas)
- Analizar el estado del sistema visualmente
- Memoria visual (qué vio, cuándo, dónde)

Autonomía: EIDOS decide qué mirar, cuándo, y qué aprender de ello.
"""
import subprocess
import time
import threading
from pathlib import Path
from dataclasses import dataclass, field
from typing import List, Dict, Optional, Tuple, Any
from datetime import datetime
import json
import base64
from PIL import Image
import io


@dataclass
class VisualElement:
    """Elemento detectado en pantalla"""
    element_type: str  # "button", "text", "input", "window", "icon", "image"
    text: str
    bbox: Tuple[int, int, int, int]  # (x, y, width, height)
    confidence: float
    metadata: Dict[str, Any] = field(default_factory=dict)


@dataclass
class ScreenState:
    """Estado actual de la pantalla"""
    timestamp: float
    screenshot_path: Optional[str]
    text_content: str
    elements: List[VisualElement]
    active_window: Optional[str]
    resolution: Tuple[int, int]
    summary: str  # Descripción en lenguaje natural


class EidosVision:
    """
    Sistema de visión de EIDOS
    
    Le permite a EIDOS:
    - Ver su entorno (tu PC)
    - Leer lo que hay en pantalla
    - Entender la interfaz
    - Recordar lo que vio
    
    Autonomía:
    - Decide qué zonas de la pantalla observar
    - Prioriza información relevante
    - Ignora lo irrelevante (spam visual)
    - Aprende patrones visuales
    """
    
    def __init__(self, capture_interval: float = 2.0):
        """
        Args:
            capture_interval: Segundos entre capturas cuando está observando
        """
        self.capture_interval = capture_interval
        self.vision_active = False
        self.vision_thread: Optional[threading.Thread] = None
        
        # Paths
        self.vision_dir = Path.home() / ".eidos" / "vision"
        self.vision_dir.mkdir(parents=True, exist_ok=True)
        
        self.screenshots_dir = self.vision_dir / "screenshots"
        self.screenshots_dir.mkdir(exist_ok=True)
        
        self.memory_file = self.vision_dir / "visual_memory.json"
        
        # Estado actual
        self.current_state: Optional[ScreenState] = None
        self.visual_memory: List[Dict] = []
        
        # Herramientas disponibles
        self.has_mss = self._check_mss()
        self.has_pil = self._check_pil()
        self.has_tesseract = self._check_tesseract()
        self.has_scrot = self._check_scrot()
        
        # Estadísticas
        self.captures_count = 0
        self.ocr_count = 0
        
        print(f"👁️  [EIDOS Vision] Inicializado")
        print(f"   Directorio: {self.vision_dir}")
        print(f"   MSS: {'✅' if self.has_mss else '❌'}")
        print(f"   PIL: {'✅' if self.has_pil else '❌'}")
        print(f"   Tesseract: {'✅' if self.has_tesseract else '❌'}")
        print(f"   Scrot: {'✅' if self.has_scrot else '❌'}")
    
    def _check_mss(self) -> bool:
        """Verifica si mss está disponible"""
        try:
            import mss
            return True
        except ImportError:
            return False
    
    def _check_pil(self) -> bool:
        """Verifica si PIL/Pillow está disponible"""
        try:
            from PIL import Image
            return True
        except ImportError:
            return False
    
    def _check_tesseract(self) -> bool:
        """Verifica si tesseract está instalado"""
        try:
            result = subprocess.run(
                ["tesseract", "--version"],
                capture_output=True,
                timeout=2
            )
            return result.returncode == 0
        except Exception:
            return False
    
    def _check_scrot(self) -> bool:
        """Verifica si scrot está instalado"""
        try:
            result = subprocess.run(
                ["which", "scrot"],
                capture_output=True,
                timeout=2
            )
            return result.returncode == 0
        except Exception:
            return False
    
    def capture_screen(self, filename: Optional[str] = None) -> Optional[str]:
        """
        Captura la pantalla completa
        
        Returns:
            Path al screenshot o None si falló
        """
        if filename is None:
            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S_%f")
            filename = f"screenshot_{timestamp}.png"
        
        filepath = self.screenshots_dir / filename
        
        import platform as _plat
        try:
            # Intentar mss primero (más rápido)
            if self.has_mss:
                try:
                    import mss
                    with mss.mss() as sct:
                        sct.shot(output=str(filepath))
                    self.captures_count += 1
                    return str(filepath)
                except Exception:
                    pass  # mss falló (ej. sin display en SSH), probar siguiente

            # macOS: screencapture nativo (funciona sin display adjunto)
            if _plat.system() == "Darwin":
                r = subprocess.run(
                    ["screencapture", "-x", str(filepath)],
                    timeout=5, capture_output=True
                )
                if r.returncode == 0 and filepath.exists():
                    self.captures_count += 1
                    return str(filepath)

            # Linux: scrot (-z = silencioso, sin cursor "+" visible)
            if self.has_scrot:
                subprocess.run(
                    ["scrot", "-z", str(filepath)],
                    timeout=5, check=True
                )
                self.captures_count += 1
                return str(filepath)

            # Fallback Linux: gnome-screenshot (no disponible en macOS)
            if _plat.system() != "Darwin":
                subprocess.run(
                    ["gnome-screenshot", "-f", str(filepath)],
                    timeout=5, check=True
                )
                self.captures_count += 1
                return str(filepath)

            return None  # macOS sin sesión GUI (SSH) → no se puede capturar

        except Exception as e:
            print(f"   ⚠️  Error capturando pantalla: {e}")
            return None
    
    def ocr_screen(self, image_path: str) -> str:
        """
        Extrae texto de una imagen usando OCR
        
        Args:
            image_path: Path a la imagen
            
        Returns:
            Texto detectado
        """
        if not self.has_tesseract:
            return ""
        
        try:
            result = subprocess.run(
                ["tesseract", image_path, "stdout", "-l", "spa+eng"],
                capture_output=True,
                text=True,
                timeout=10
            )
            
            self.ocr_count += 1
            return result.stdout.strip()
            
        except Exception as e:
            print(f"   ⚠️  Error en OCR: {e}")
            return ""
    
    def get_active_window(self) -> Optional[str]:
        """
        Obtiene el nombre de la ventana activa
        """
        try:
            # Intentar con xdotool
            result = subprocess.run(
                ["xdotool", "getactivewindow", "getwindowname"],
                capture_output=True,
                text=True,
                timeout=2
            )
            if result.returncode == 0:
                return result.stdout.strip()
        except Exception:
            pass  # error no crítico, continuar
        try:
            # Fallback a xprop
            result = subprocess.run(
                ["xprop", "-id", "$(xprop -root 32x '\\t$0' _NET_ACTIVE_WINDOW | cut -f 2)"],
                capture_output=True,
                text=True,
                timeout=2,
                shell=True
            )
            if result.returncode == 0:
                for line in result.stdout.split('\n'):
                    if 'WM_NAME' in line:
                        return line.split('=')[-1].strip().strip('"')
        except Exception:
            pass  # error no crítico, continuar
        return None
    
    def get_screen_resolution(self) -> Tuple[int, int]:
        """
        Obtiene la resolución de la pantalla
        """
        try:
            result = subprocess.run(
                ["xdpyinfo"],
                capture_output=True,
                text=True,
                timeout=2
            )
            for line in result.stdout.split('\n'):
                if 'dimensions:' in line:
                    parts = line.split()
                    res = parts[1].split('x')
                    return (int(res[0]), int(res[1]))
        except Exception:
            pass  # error no crítico, continuar
        # Fallback
        return (1920, 1080)
    
    def analyze_screen(self, screenshot_path: str) -> ScreenState:
        """
        Analiza una captura de pantalla completa
        
        Returns:
            ScreenState con toda la información
        """
        timestamp = time.time()
        
        # OCR
        text_content = self.ocr_screen(screenshot_path)
        
        # Ventana activa
        active_window = self.get_active_window()
        
        # Resolución
        resolution = self.get_screen_resolution()
        
        # Detectar elementos (básico por ahora)
        elements = self._detect_basic_elements(text_content)
        
        # Generar resumen
        summary = self._generate_summary(text_content, active_window, elements)
        
        state = ScreenState(
            timestamp=timestamp,
            screenshot_path=screenshot_path,
            text_content=text_content,
            elements=elements,
            active_window=active_window,
            resolution=resolution,
            summary=summary
        )
        
        return state
    
    def _detect_basic_elements(self, text_content: str) -> List[VisualElement]:
        """
        Detección básica de elementos basada en el texto
        """
        elements = []
        
        # Detectar posibles botones (líneas cortas en mayúsculas)
        lines = text_content.split('\n')
        y_pos = 0
        for line in lines:
            line = line.strip()
            if 3 < len(line) < 30 and line.isupper():
                elements.append(VisualElement(
                    element_type="button",
                    text=line,
                    bbox=(0, y_pos, 200, 30),
                    confidence=0.6
                ))
            y_pos += 20
        
        return elements
    
    def _generate_summary(self, text: str, window: Optional[str], elements: List[VisualElement]) -> str:
        """
        Genera un resumen en lenguaje natural de lo que hay en pantalla
        """
        parts = []
        
        if window:
            parts.append(f"Ventana activa: {window}")
        
        text_preview = text[:200].replace('\n', ' ')
        if text_preview:
            parts.append(f"Texto visible: {text_preview}...")
        
        if elements:
            parts.append(f"Elementos detectados: {len(elements)}")
        
        return " | ".join(parts) if parts else "Pantalla sin texto legible"
    
    def look(self, save_memory: bool = True) -> Optional[ScreenState]:
        """
        Mira la pantalla una vez
        
        Args:
            save_memory: Guardar en memoria visual
            
        Returns:
            Estado de pantalla o None
        """
        screenshot = self.capture_screen()
        if not screenshot:
            return None
        
        state = self.analyze_screen(screenshot)
        self.current_state = state
        
        if save_memory:
            self._save_to_memory(state)
        
        return state
    
    def _save_to_memory(self, state: ScreenState):
        """Guarda el estado visual en memoria"""
        memory_entry = {
            'timestamp': state.timestamp,
            'datetime': datetime.fromtimestamp(state.timestamp).isoformat(),
            'screenshot': state.screenshot_path,
            'window': state.active_window,
            'text_preview': state.text_content[:500],
            'summary': state.summary,
            'element_count': len(state.elements)
        }
        
        self.visual_memory.append(memory_entry)
        
        # Limitar memoria a últimas 1000 entradas
        if len(self.visual_memory) > 1000:
            old = self.visual_memory.pop(0)
            # Opcional: borrar screenshot antiguo
            if old.get('screenshot') and Path(old['screenshot']).exists():
                try:
                    Path(old['screenshot']).unlink()
                except Exception:
                    pass  # error no crítico, continuar
        # Guardar a archivo
        try:
            with open(self.memory_file, 'w') as f:
                json.dump(self.visual_memory[-100:], f, indent=2)
        except Exception:
            pass  # error no crítico, continuar
    def start_watching(self):
        """
        Inicia observación continua de la pantalla
        EIDOS ve todo el tiempo
        """
        if self.vision_active:
            print("⚠️  [EIDOS Vision] Ya está observando")
            return
        
        def vision_loop():
            print(f"👁️  [EIDOS Vision] Observando cada {self.capture_interval}s")
            while self.vision_active:
                try:
                    self.look(save_memory=True)
                    time.sleep(self.capture_interval)
                except Exception as e:
                    print(f"   ⚠️  Error en loop de visión: {e}")
                    time.sleep(1)
        
        self.vision_active = True
        self.vision_thread = threading.Thread(target=vision_loop, daemon=True)
        self.vision_thread.start()
    
    def stop_watching(self):
        """Detiene la observación"""
        self.vision_active = False
        if self.vision_thread:
            self.vision_thread.join(timeout=5)
        print("🛑 [EIDOS Vision] Observación detenida")
    
    def get_current_view(self) -> Optional[str]:
        """
        Obtiene descripción de lo que EIDOS está viendo ahora
        """
        if self.current_state:
            return self.current_state.summary
        return "No hay información visual reciente"
    
    def search_in_memory(self, query: str) -> List[Dict]:
        """
        Busca en la memoria visual
        
        Args:
            query: Texto a buscar
            
        Returns:
            Lista de entradas coincidentes
        """
        results = []
        query_lower = query.lower()
        
        for entry in reversed(self.visual_memory):
            if (query_lower in entry.get('text_preview', '').lower() or
                query_lower in entry.get('window', '').lower() or
                query_lower in entry.get('summary', '').lower()):
                results.append(entry)
        
        return results[:10]  # Top 10
    
    def get_stats(self) -> Dict:
        """Estadísticas del sistema de visión"""
        return {
            'captures': self.captures_count,
            'ocr_extractions': self.ocr_count,
            'memory_entries': len(self.visual_memory),
            'watching': self.vision_active,
            'tools': {
                'mss': self.has_mss,
                'pil': self.has_pil,
                'tesseract': self.has_tesseract,
                'scrot': self.has_scrot
            },
            'current_view': self.get_current_view()
        }


# ═══════════════════════════════════════════════════════════════════════════
# Singleton para acceso global
# ═══════════════════════════════════════════════════════════════════════════

eidos_vision = EidosVision()


def get_vision_system() -> EidosVision:
    """Obtiene la instancia singleton del sistema de visión."""
    return eidos_vision


def start_vision():
    """Inicia el sistema de visión de EIDOS"""
    eidos_vision.start_watching()


def stop_vision():
    """Detiene el sistema de visión"""
    eidos_vision.stop_watching()


def look_once() -> Optional[ScreenState]:
    """Mira una vez y retorna el estado"""
    return eidos_vision.look()


def what_do_you_see() -> str:
    """Pregunta a EIDOS qué está viendo"""
    return eidos_vision.get_current_view()


# ═══════════════════════════════════════════════════════════════════════════
# Test
# ═══════════════════════════════════════════════════════════════════════════

if __name__ == "__main__":
    print("=== Test EIDOS Vision ===\n")
    
    # Test 1: Captura simple
    print("Test 1: Captura de pantalla")
    screenshot = eidos_vision.capture_screen()
    if screenshot:
        print(f"  ✅ Screenshot guardado: {screenshot}")
    else:
        print("  ❌ No se pudo capturar")
    
    # Test 2: OCR
    if screenshot and eidos_vision.has_tesseract:
        print("\nTest 2: OCR")
        text = eidos_vision.ocr_screen(screenshot)
        print(f"  Texto detectado ({len(text)} chars):")
        print(f"  {text[:200]}...")
    
    # Test 3: Análisis completo
    if screenshot:
        print("\nTest 3: Análisis de pantalla")
        state = eidos_vision.analyze_screen(screenshot)
        print(f"  Ventana: {state.active_window}")
        print(f"  Resolución: {state.resolution}")
        print(f"  Elementos: {len(state.elements)}")
        print(f"  Resumen: {state.summary[:100]}...")
    
    # Test 4: Stats
    print("\nTest 4: Estadísticas")
    stats = eidos_vision.get_stats()
    print(f"  Capturas: {stats['captures']}")
    print(f"  OCR: {stats['ocr_extractions']}")
    print(f"  Tools: {stats['tools']}")
    
    print("\n✅ EIDOS Vision listo")
