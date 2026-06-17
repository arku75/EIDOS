"""
core/screen_capture.py — Captura de pantalla bajo demanda (S76 Fase 2)

Proporciona ScreenCapture con backends scrot y ffmpeg.
Captura fullscreen o región de interés, con preprocesado para OCR.

API:
    cap = ScreenCapture()
    frame = cap.capture()                        # Fullscreen
    frame = cap.capture(region=(100,100,500,400))  # ROI
    proc = cap.preprocess(frame, resize=(800,600))  # Para OCR

Backends (por orden de preferencia):
  1. scrot - rápido, nativo X11, captura ventana o ROI
  2. ffmpeg - streaming, útil para secuencias
  3. PIL ImageGrab - fallback multiplataforma (lento)

Dependencias: scrot (recomendado), ffmpeg, PIL/Pillow, numpy
"""

from __future__ import annotations

import logging
import os
import subprocess
import tempfile
import time
from functools import lru_cache
from pathlib import Path
from typing import Any, Dict, Optional, Tuple

import numpy as np

log = logging.getLogger("eidos.screen_capture")

# Resolución típica (se detecta en runtime)
_DEFAULT_SIZE = (1920, 1080)

# Formatos soportados
_SUPPORTED_FORMATS = {"png", "jpg", "jpeg"}


class ScreenCapture:
    """Captura de pantalla bajo demanda con múltiples backends.

    Uso:
        cap = ScreenCapture()
        frame = cap.capture()                   # Fullscreen RGB
        frame = cap.capture(region=(0,0,800,600))  # ROI
        gray = cap.preprocess(frame, grayscale=True)
    """

    def __init__(self, backend: str = "auto", display: str = ":0"):
        self.backend = self._detect_backend(backend)
        self.display = display
        self._screen_size: Optional[Tuple[int, int]] = None
        log.info("ScreenCapture: backend=%s display=%s", self.backend, display)

    def _detect_backend(self, preferred: str) -> str:
        """Detecta el mejor backend disponible."""
        if preferred != "auto" and preferred in ("scrot", "ffmpeg", "pil"):
            return preferred
        # Preferencia: scrot > ffmpeg > PIL
        if self._has_command("scrot"):
            return "scrot"
        if self._has_command("ffmpeg"):
            return "ffmpeg"
        return "pil"

    @staticmethod
    def _has_command(cmd: str) -> bool:
        """Verifica si un comando está disponible en $PATH."""
        try:
            subprocess.run(["which", cmd], capture_output=True, timeout=2, check=True)
            return True
        except (subprocess.CalledProcessError, FileNotFoundError):
            return False

    # ── API principal ─────────────────────────────────────────────────────────

    def capture(self, region: Optional[Tuple[int, int, int, int]] = None,
                window_id: Optional[int] = None) -> np.ndarray:
        """Captura pantalla y retorna array numpy RGB.

        Args:
            region: (x, y, w, h) o None para fullscreen.
            window_id: X11 window ID para capturar ventana específica.

        Returns:
            np.ndarray shape (H, W, 3) dtype uint8 en formato RGB.
        """
        import PIL.Image

        if self.backend == "scrot":
            img = self._capture_scrot(region, window_id)
        elif self.backend == "ffmpeg":
            img = self._capture_ffmpeg(region)
        else:
            img = self._capture_pil(region)

        if img is None:
            # Fallback último recurso
            log.error("Todos los backends fallaron")
            return np.zeros((*self._detect_screen_size(), 3), dtype=np.uint8)

        return np.array(img.convert("RGB"))

    def preprocess(self, img: np.ndarray, *,
                   grayscale: bool = True,
                   resize: Optional[Tuple[int, int]] = None,
                   threshold: bool = True) -> np.ndarray:
        """Preprocesa imagen para OCR/reconocimiento.

        Args:
            img: Array numpy RGB.
            grayscale: Convertir a escala de grises.
            resize: Redimensionar a (W, H).
            threshold: Aplicar threshold adaptativo (mejora OCR).

        Returns:
            np.ndarray procesado.
        """
        import cv2

        if grayscale and len(img.shape) == 3:
            img = cv2.cvtColor(img, cv2.COLOR_RGB2GRAY)

        if resize:
            img = cv2.resize(img, resize, interpolation=cv2.INTER_CUBIC)

        if threshold and len(img.shape) == 2:
            # CLAHE para mejorar contraste
            clahe = cv2.createCLAHE(clipLimit=2.0, tileGridSize=(8, 8))
            img = clahe.apply(img)

        return img

    # ── Backend: scrot ────────────────────────────────────────────────────────

    def _capture_scrot(self, region: Optional[Tuple[int, int, int, int]] = None,
                       window_id: Optional[int] = None) -> Any:
        """Captura usando scrot (rápido, nativo X11)."""
        import PIL.Image

        with tempfile.NamedTemporaryFile(suffix=".png", delete=False) as tmp:
            path = tmp.name

        try:
            if window_id:
                cmd = ["scrot", "-z", path, "--window", str(window_id)]
            elif region:
                x, y, w, h = region
                cmd = ["scrot", "-z", path, "-a", f"{x},{y},{w},{h}"]
            else:
                cmd = ["scrot", "-z", path]

            env = {**os.environ, "DISPLAY": self.display}
            proc = subprocess.run(cmd, env=env, capture_output=True,
                                timeout=5, text=True)
            if proc.returncode != 0:
                log.debug("scrot falló: %s", proc.stderr[:200])
                return None

            img = PIL.Image.open(path)
            img.load()
            return img

        except subprocess.TimeoutExpired:
            log.warning("scrot timeout (5s)")
            return None
        except Exception as e:
            log.debug("scrot error: %s", e)
            return None
        finally:
            try:
                os.unlink(path)
            except OSError:
                pass

    # ── Backend: ffmpeg ───────────────────────────────────────────────────────

    def _capture_ffmpeg(self, region: Optional[Tuple[int, int, int, int]] = None) -> Any:
        """Captura usando ffmpeg x11grab."""
        import PIL.Image

        w, h = self._detect_screen_size()
        if region:
            x, y, rw, rh = region
            video_size = f"{rw}x{rh}"
            offset = f"{x},{y}"
        else:
            video_size = f"{w}x{h}"
            offset = "0,0"

        cmd = [
            "ffmpeg", "-y",
            "-f", "x11grab",
            "-video_size", video_size,
            "-i", f"{self.display}+{offset}",
            "-vframes", "1",
            "-f", "image2pipe",
            "-pix_fmt", "rgb24",
            "-vcodec", "rawvideo",
            "-"
        ]

        try:
            proc = subprocess.run(cmd, capture_output=True, timeout=5)
            if proc.returncode != 0 or not proc.stdout:
                return None
            rw = region[2] if region else w
            rh = region[3] if region else h
            return PIL.Image.frombytes("RGB", (rw, rh), proc.stdout)
        except Exception as e:
            log.debug("ffmpeg error: %s", e)
            return None

    # ── Backend: PIL (fallback) ───────────────────────────────────────────────

    def _capture_pil(self, region: Optional[Tuple[int, int, int, int]] = None) -> Any:
        """Captura usando PIL ImageGrab (lento, multiplataforma)."""
        try:
            from PIL import ImageGrab
            if region:
                return ImageGrab.grab(bbox=region)
            return ImageGrab.grab()
        except Exception as e:
            log.debug("PIL ImageGrab error: %s", e)
            return None

    # ── Utilidades ────────────────────────────────────────────────────────────

    def _detect_screen_size(self) -> Tuple[int, int]:
        """Detecta resolución de pantalla."""
        if self._screen_size:
            return self._screen_size
        try:
            proc = subprocess.run(
                ["xrandr"], capture_output=True, text=True, timeout=3
            )
            for line in proc.stdout.split("\n"):
                if " connected" in line and "primary" in line:
                    import re
                    m = re.search(r"(\d+)x(\d+)\+", line)
                    if m:
                        self._screen_size = (int(m.group(1)), int(m.group(2)))
                        return self._screen_size
        except Exception:
            pass
        self._screen_size = _DEFAULT_SIZE
        return _DEFAULT_SIZE

    def get_active_window_id(self) -> Optional[int]:
        """Obtiene el ID de la ventana activa."""
        try:
            proc = subprocess.run(
                ["xdotool", "getactivewindow"],
                capture_output=True, text=True, timeout=2
            )
            return int(proc.stdout.strip())
        except Exception:
            return None

    def get_window_geometry(self, wid: int) -> Optional[Tuple[int, int, int, int]]:
        """Obtiene geometría de ventana: (x, y, w, h)."""
        try:
            import re
            proc = subprocess.run(
                ["xdotool", "getwindowgeometry", "--shell", str(wid)],
                capture_output=True, text=True, timeout=2
            )
            x = y = w = h = 0
            for line in proc.stdout.split("\n"):
                if "X=" in line:
                    x = int(line.split("=")[1])
                elif "Y=" in line:
                    y = int(line.split("=")[1])
                elif "WIDTH=" in line:
                    w = int(line.split("=")[1])
                elif "HEIGHT=" in line:
                    h = int(line.split("=")[1])
            return (x, y, w, h)
        except Exception:
            return None


# ── Singleton ──────────────────────────────────────────────────────────────────

_screen_capture: Optional[ScreenCapture] = None


def get_capture() -> ScreenCapture:
    """Retorna instancia singleton de ScreenCapture."""
    global _screen_capture
    if _screen_capture is None:
        _screen_capture = ScreenCapture()
    return _screen_capture


# ── CLI ────────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    import argparse
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")

    parser = argparse.ArgumentParser(description="Screen Capture utility")
    parser.add_argument("--test", action="store_true", help="Capturar y guardar test.png")
    parser.add_argument("--region", type=str, help="Región: x,y,w,h")
    parser.add_argument("--window", action="store_true", help="Ventana activa")
    args = parser.parse_args()

    cap = ScreenCapture()

    if args.test:
        region = None
        if args.region:
            parts = [int(p) for p in args.region.split(",")]
            region = tuple(parts) if len(parts) == 4 else None
        wid = cap.get_active_window_id() if args.window else None
        frame = cap.capture(region=region, window_id=wid)
        from PIL import Image
        Image.fromarray(frame).save("/tmp/eidos_capture_test.png")
        print(f"Captura: {frame.shape} → /tmp/eidos_capture_test.png")
