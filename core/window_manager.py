"""
EIDOS Window Focus Manager
===========================
Captura y analiza ventanas específicas (no toda la pantalla).

Features:
- Captura window-specific (no screenshot completo)
- Análisis de DOM-like (estructura de ventana)
- Funciona con ventanas minimizadas
- OCR para extraer texto
- Optimizado para RAM baja
"""
from __future__ import annotations

import sys
import time
import subprocess
from pathlib import Path
from typing import Optional, List, Dict, Any, Tuple
from dataclasses import dataclass
from datetime import datetime

# Añadir root al path
EIDOS_ROOT = Path(__file__).parent.parent.parent
sys.path.insert(0, str(EIDOS_ROOT))

from core.eidos_config import get_config

# ============================
# WINDOW INFO
# ============================

@dataclass
class WindowInfo:
    """Información de una ventana"""
    window_id: str
    title: str
    app_name: str
    pid: int
    geometry: Tuple[int, int, int, int]  # x, y, width, height
    is_active: bool
    is_minimized: bool
    workspace: int

@dataclass
class WindowCapture:
    """Captura de ventana con metadata"""
    window: WindowInfo
    screenshot_path: Optional[Path]
    timestamp: float
    text_content: List[str]  # Texto extraído
    elements: List[Dict[str, Any]]  # Elementos detectados

# ============================
# WINDOW MANAGER
# ============================

class WindowFocusManager:
    """
    Gestiona capturas y análisis de ventanas específicas.

    Usa herramientas nativas de Linux:
    - xdotool (para listar ventanas)
    - xwininfo (para info de ventanas)
    - import (ImageMagick) para capturas
    - tesseract (OCR para extraer texto)
    """

    def __init__(self):
        self.config = get_config()
        self.screenshots_dir = Path(self.config.paths.home) / "window_captures"
        self.screenshots_dir.mkdir(exist_ok=True)

        # Verificar herramientas
        self._check_tools()

    def _check_tools(self):
        """Verifica que las herramientas necesarias están instaladas"""
        self.tools_available = {
            "xdotool": self._command_exists("xdotool"),
            "xwininfo": self._command_exists("xwininfo"),
            "import": self._command_exists("import"),
            "tesseract": self._command_exists("tesseract"),
        }

        for tool, available in self.tools_available.items():
            if available:
                print(f"[WINDOW] ✅ {tool} disponible")
            else:
                print(f"[WINDOW] ⚠️  {tool} no encontrado")

    def _command_exists(self, command: str) -> bool:
        """Verifica si un comando existe"""
        try:
            subprocess.run(
                ["which", command],
                capture_output=True,
                check=True
            )
            return True
        except Exception:
            return False

    def list_windows(self, app_filter: Optional[str] = None) -> List[WindowInfo]:
        """
        Lista todas las ventanas abiertas.

        Args:
            app_filter: Filtrar por nombre de app (ej: "code", "firefox")

        Returns:
            Lista de WindowInfo
        """
        if not self.tools_available["xdotool"]:
            print("[WINDOW] ⚠️  xdotool no disponible")
            return []

        try:
            # Obtener todas las ventanas
            result = subprocess.run(
                ["xdotool", "search", "--onlyvisible", "--name", ".*"],
                capture_output=True,
                text=True
            )

            window_ids = result.stdout.strip().split('\n')
            windows = []

            for wid in window_ids:
                if not wid:
                    continue

                info = self._get_window_info(wid)
                if info:
                    # Aplicar filtro si existe
                    if app_filter:
                        if app_filter.lower() not in info.app_name.lower():
                            continue
                    windows.append(info)

            return windows

        except Exception as e:
            print(f"[WINDOW] ❌ Error listando ventanas: {e}")
            return []

    def _get_window_info(self, window_id: str) -> Optional[WindowInfo]:
        """Obtiene información detallada de una ventana"""
        try:
            # Obtener título
            result = subprocess.run(
                ["xdotool", "getwindowname", window_id],
                capture_output=True,
                text=True
            )
            title = result.stdout.strip()

            # Obtener PID
            result = subprocess.run(
                ["xdotool", "getwindowpid", window_id],
                capture_output=True,
                text=True
            )
            pid_str = result.stdout.strip()
            if not pid_str:
                return None  # Ventana sin PID (ventana especial del sistema)
            pid = int(pid_str)

            # Obtener geometría
            result = subprocess.run(
                ["xdotool", "getwindowgeometry", window_id],
                capture_output=True,
                text=True
            )

            # Parse geometría (formato: "Position: X,Y (screen: 0)\n  Geometry: WxH")
            lines = result.stdout.strip().split('\n')
            position_line = [l for l in lines if "Position:" in l][0]
            geometry_line = [l for l in lines if "Geometry:" in l][0]

            pos_parts = position_line.split("Position:")[1].split("(")[0].strip().split(',')
            x, y = int(pos_parts[0]), int(pos_parts[1])

            geo_parts = geometry_line.split("Geometry:")[1].strip().split('x')
            width, height = int(geo_parts[0]), int(geo_parts[1])

            # Verificar si está activa
            result = subprocess.run(
                ["xdotool", "getactivewindow"],
                capture_output=True,
                text=True
            )
            active_id = result.stdout.strip()
            is_active = (active_id == window_id)

            # Obtener nombre de app (desde /proc/PID/comm)
            try:
                with open(f"/proc/{pid}/comm", 'r') as f:
                    app_name = f.read().strip()
            except Exception:
                app_name = "unknown"

            return WindowInfo(
                window_id=window_id,
                title=title,
                app_name=app_name,
                pid=pid,
                geometry=(x, y, width, height),
                is_active=is_active,
                is_minimized=False,  # TODO: detectar si minimizada
                workspace=0  # TODO: obtener workspace
            )

        except Exception as e:
            print(f"[WINDOW] ⚠️  Error obteniendo info de {window_id}: {e}")
            return None

    def capture_window(
        self,
        window_id: str,
        extract_text: bool = True
    ) -> Optional[WindowCapture]:
        """
        Captura una ventana específica.

        Args:
            window_id: ID de la ventana a capturar
            extract_text: Si True, extrae texto con OCR

        Returns:
            WindowCapture con screenshot y texto extraído
        """
        if not self.tools_available["import"]:
            print("[WINDOW] ⚠️  ImageMagick (import) no disponible")
            return None

        try:
            # Obtener info de ventana
            window_info = self._get_window_info(window_id)
            if not window_info:
                return None

            # Generar path para screenshot
            timestamp = int(time.time())
            screenshot_name = f"window_{window_id}_{timestamp}.png"
            screenshot_path = self.screenshots_dir / screenshot_name

            # Capturar ventana con ImageMagick
            subprocess.run(
                ["import", "-window", window_id, str(screenshot_path)],
                check=True,
                capture_output=True
            )

            print(f"[WINDOW] 📸 Capturada: {window_info.title}")

            # Extraer texto si se solicita
            text_content = []
            if extract_text and self.tools_available["tesseract"]:
                text_content = self._extract_text(screenshot_path)

            return WindowCapture(
                window=window_info,
                screenshot_path=screenshot_path,
                timestamp=time.time(),
                text_content=text_content,
                elements=[]  # TODO: detectar elementos UI
            )

        except Exception as e:
            print(f"[WINDOW] ❌ Error capturando ventana: {e}")
            return None

    def _extract_text(self, image_path: Path) -> List[str]:
        """Extrae texto de imagen usando Tesseract OCR"""
        try:
            result = subprocess.run(
                ["tesseract", str(image_path), "stdout", "-l", "spa+eng"],
                capture_output=True,
                text=True
            )

            # Dividir en líneas y limpiar
            lines = result.stdout.split('\n')
            lines = [l.strip() for l in lines if l.strip()]

            print(f"[WINDOW] 📝 Texto extraído: {len(lines)} líneas")
            return lines

        except Exception as e:
            print(f"[WINDOW] ⚠️  Error en OCR: {e}")
            return []

    def focus_window(self, window_id: str) -> bool:
        """Enfoca (trae al frente) una ventana"""
        if not self.tools_available["xdotool"]:
            return False

        try:
            subprocess.run(
                ["xdotool", "windowactivate", window_id],
                check=True
            )
            print(f"[WINDOW] 👁️  Ventana {window_id} enfocada")
            return True
        except Exception:
            return False

    def monitor_window(
        self,
        window_id: str,
        interval_seconds: int = 5,
        duration_minutes: int = 10
    ) -> List[WindowCapture]:
        """
        Monitorea una ventana capturándola periódicamente.

        Args:
            window_id: Ventana a monitorear
            interval_seconds: Cada cuántos segundos capturar
            duration_minutes: Por cuántos minutos monitorear

        Returns:
            Lista de capturas realizadas
        """
        print(f"[WINDOW] 👀 Monitoreando ventana {window_id}")
        print(f"[WINDOW]    Intervalo: {interval_seconds}s")
        print(f"[WINDOW]    Duración: {duration_minutes}min")

        captures = []
        end_time = time.time() + (duration_minutes * 60)

        try:
            while time.time() < end_time:
                capture = self.capture_window(window_id, extract_text=True)
                if capture:
                    captures.append(capture)

                time.sleep(interval_seconds)

        except KeyboardInterrupt:
            print(f"\n[WINDOW] ⏸️  Monitoreo interrumpido")

        print(f"[WINDOW] ✅ Monitoreo completado: {len(captures)} capturas")
        return captures

# ============================
# SINGLETON
# ============================

_window_manager: Optional[WindowFocusManager] = None

def get_window_manager() -> WindowFocusManager:
    """Obtiene instancia singleton"""
    global _window_manager
    if _window_manager is None:
        _window_manager = WindowFocusManager()
    return _window_manager

# ============================
# TESTING
# ============================

if __name__ == "__main__":
    print("=== EIDOS Window Focus Manager Test ===\n")

    manager = get_window_manager()

    # Listar ventanas
    print("\n📋 Ventanas abiertas:")
    windows = manager.list_windows()

    if not windows:
        print("⚠️  No se pudieron listar ventanas (¿falta xdotool?)")
    else:
        for i, win in enumerate(windows[:5], 1):  # Mostrar primeras 5
            active = "🟢" if win.is_active else "⚪"
            print(f"{i}. {active} {win.title[:50]} ({win.app_name})")

        # Capturar primera ventana
        if windows:
            print(f"\n📸 Capturando primera ventana...")
            first_window = windows[0]

            capture = manager.capture_window(first_window.window_id, extract_text=True)

            if capture:
                print(f"✅ Captura exitosa: {capture.screenshot_path}")
                print(f"   Texto extraído: {len(capture.text_content)} líneas")

                if capture.text_content:
                    print(f"   Primeras líneas:")
                    for line in capture.text_content[:3]:
                        print(f"     - {line[:60]}")
            else:
                print("⚠️  No se pudo capturar")

    print("\n🎯 Test completado")
