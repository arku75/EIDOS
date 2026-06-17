#!/usr/bin/env python3
"""
EIDOS Vision C++ Bridge — ctypes wrapper for libeidos_vision.so
================================================================
Provides Python access to the C++ 60fps camera capture and OCR engine.

Usage:
    from core.vision_ctypes import get_vision_engine, CPP_VISION_AVAILABLE

    if CPP_VISION_AVAILABLE:
        engine = get_vision_engine()
        text = engine.ocr_file("/path/to/image.png")
"""
from __future__ import annotations

import ctypes
import logging
import numpy as np
from pathlib import Path
from typing import Optional, Tuple

logger = logging.getLogger("eidos.vision_ctypes")

# ══════════════════════════════════════════════════════════════════════════════
# Load shared library
# ══════════════════════════════════════════════════════════════════════════════

CPP_VISION_AVAILABLE = False
_lib = None

_LIB_PATHS = [
    Path(__file__).parent.parent.parent / "cpp-core" / "build" / "libeidos_vision.so",
    Path.home() / "EIDOS" / "cpp-core" / "build" / "libeidos_vision.so",
    Path("/usr/local/lib/libeidos_vision.so"),
]

for _path in _LIB_PATHS:
    if _path.exists():
        try:
            _lib = ctypes.CDLL(str(_path))
            CPP_VISION_AVAILABLE = True
            logger.info("C++ vision loaded from %s", _path)
            break
        except OSError as e:
            logger.warning("Failed to load %s: %s", _path, e)

if _lib is not None:
    # Vision API
    _lib.vision_create.restype = ctypes.c_void_p
    _lib.vision_create.argtypes = []

    _lib.vision_start.restype = ctypes.c_int
    _lib.vision_start.argtypes = [ctypes.c_void_p, ctypes.c_int]

    _lib.vision_has_frame.restype = ctypes.c_int
    _lib.vision_has_frame.argtypes = [ctypes.c_void_p]

    _lib.vision_get_frame.restype = ctypes.c_int
    _lib.vision_get_frame.argtypes = [
        ctypes.c_void_p,
        ctypes.POINTER(ctypes.c_ubyte),
        ctypes.POINTER(ctypes.c_int),
        ctypes.POINTER(ctypes.c_int),
    ]

    _lib.vision_stop.restype = None
    _lib.vision_stop.argtypes = [ctypes.c_void_p]

    _lib.vision_destroy.restype = None
    _lib.vision_destroy.argtypes = [ctypes.c_void_p]

    # OCR API
    _lib.ocr_create.restype = ctypes.c_void_p
    _lib.ocr_create.argtypes = []

    _lib.ocr_recognize_file.restype = ctypes.c_char_p
    _lib.ocr_recognize_file.argtypes = [ctypes.c_void_p, ctypes.c_char_p]

    _lib.ocr_recognize_buffer.restype = ctypes.c_char_p
    _lib.ocr_recognize_buffer.argtypes = [
        ctypes.c_void_p,
        ctypes.POINTER(ctypes.c_ubyte),
        ctypes.c_int,
        ctypes.c_int,
    ]

    _lib.ocr_free_string.restype = None
    _lib.ocr_free_string.argtypes = [ctypes.c_char_p]

    _lib.ocr_destroy.restype = None
    _lib.ocr_destroy.argtypes = [ctypes.c_void_p]


# ══════════════════════════════════════════════════════════════════════════════
# High-level Python API
# ══════════════════════════════════════════════════════════════════════════════

# Max frame buffer: 1920x1080 BGR
_MAX_FRAME_BYTES = 1920 * 1080 * 3


class VisionEngine:
    """Python wrapper for C++ Vision60FPS + OCR engine."""

    def __init__(self):
        if not CPP_VISION_AVAILABLE:
            raise RuntimeError("libeidos_vision.so not available")
        self._vision = None
        self._ocr = None
        self._frame_buf = (ctypes.c_ubyte * _MAX_FRAME_BYTES)()

    # ── Camera ─────────────────────────────────────────────────────────────

    def init_camera(self, camera_id: int = 0) -> bool:
        """Initialize and start camera capture at 60fps."""
        if self._vision is not None:
            self.release_camera()
        self._vision = _lib.vision_create()
        result = _lib.vision_start(self._vision, camera_id)
        if result != 0:
            _lib.vision_destroy(self._vision)
            self._vision = None
            return False
        logger.info("Camera %d started (60fps)", camera_id)
        return True

    def capture_frame(self) -> Optional[np.ndarray]:
        """Capture a single frame. Returns numpy BGR array or None."""
        if self._vision is None:
            return None
        if not _lib.vision_has_frame(self._vision):
            return None

        width = ctypes.c_int(0)
        height = ctypes.c_int(0)
        size = _lib.vision_get_frame(
            self._vision, self._frame_buf,
            ctypes.byref(width), ctypes.byref(height)
        )
        if size <= 0:
            return None

        w, h = width.value, height.value
        frame = np.frombuffer(self._frame_buf, dtype=np.uint8, count=w * h * 3)
        return frame.reshape((h, w, 3)).copy()

    def release_camera(self):
        """Stop camera and release resources."""
        if self._vision is not None:
            _lib.vision_stop(self._vision)
            _lib.vision_destroy(self._vision)
            self._vision = None
            logger.info("Camera released")

    # ── OCR ────────────────────────────────────────────────────────────────

    def ocr_file(self, image_path: str) -> str:
        """Run OCR on an image file. Returns extracted text."""
        if self._ocr is None:
            self._ocr = _lib.ocr_create()
        raw = _lib.ocr_recognize_file(self._ocr, image_path.encode("utf-8"))
        if raw is None:
            return ""
        text = raw.decode("utf-8", errors="replace")
        _lib.ocr_free_string(raw)
        return text

    def ocr_frame(self, frame: np.ndarray) -> str:
        """Run OCR on a numpy BGR frame. Returns extracted text."""
        if self._ocr is None:
            self._ocr = _lib.ocr_create()
        if frame is None or frame.size == 0:
            return ""
        h, w = frame.shape[:2]
        buf = frame.ctypes.data_as(ctypes.POINTER(ctypes.c_ubyte))
        raw = _lib.ocr_recognize_buffer(self._ocr, buf, w, h)
        if raw is None:
            return ""
        text = raw.decode("utf-8", errors="replace")
        _lib.ocr_free_string(raw)
        return text

    # ── Lifecycle ──────────────────────────────────────────────────────────

    def __del__(self):
        self.release_camera()
        if self._ocr is not None:
            _lib.ocr_destroy(self._ocr)
            self._ocr = None


# ══════════════════════════════════════════════════════════════════════════════
# Singleton
# ══════════════════════════════════════════════════════════════════════════════

_engine: Optional[VisionEngine] = None


def get_vision_engine() -> Optional[VisionEngine]:
    """Get singleton VisionEngine, or None if C++ lib not available."""
    global _engine
    if not CPP_VISION_AVAILABLE:
        return None
    if _engine is None:
        _engine = VisionEngine()
    return _engine
