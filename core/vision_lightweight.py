#!/usr/bin/env python3
"""
EIDOS Vision Lightweight - Sistema de Visión Optimizado para Bajo Consumo
===========================================================================

Estrategia para visión eficiente:
1. Frame sampling inteligente (no todos los frames)
2. Detección de cambios (solo procesar cuando cambia la pantalla)
3. OCR selectivo (solo regiones con texto)
4. Cache de resultados
5. Procesamiento asíncrono

Consume MUCHO MENOS que procesar todos los frames, pero ve TODO lo importante.
"""

import cv2
import numpy as np
from PIL import Image
from pathlib import Path
from typing import List, Dict, Optional, Callable
from dataclasses import dataclass
from datetime import datetime
import time
import logging
import hashlib

logger = logging.getLogger(__name__)

# Imports opcionales
try:
    import pytesseract
    OCR_AVAILABLE = True
except ImportError:
    OCR_AVAILABLE = False
    logger.warning("pytesseract no disponible - OCR deshabilitado")

try:
    import mss
    SCREEN_CAPTURE_AVAILABLE = True
except ImportError:
    SCREEN_CAPTURE_AVAILABLE = False
    logger.warning("mss no disponible - captura de pantalla deshabilitada")

# CLIP Vision for semantic understanding
try:
    from .clip_vision import get_clip_vision
    CLIP_AVAILABLE = True
except ImportError:
    CLIP_AVAILABLE = False
    logger.warning("CLIP no disponible - análisis semántico deshabilitado")


@dataclass
class FrameAnalysis:
    """Resultado del análisis de un frame"""
    timestamp: float
    has_changed: bool
    text_detected: Optional[str] = None
    code_detected: List[str] = None
    regions_of_interest: List[Dict] = None
    frame_hash: str = ""
    # NEW: CLIP semantic analysis
    semantic_categories: List[Dict] = None  # [{"category": "code_screenshot", "confidence": 0.95}, ...]
    primary_content_type: Optional[str] = None  # "code", "diagram", "ui", "terminal", etc.

    def __post_init__(self):
        if self.code_detected is None:
            self.code_detected = []
        if self.regions_of_interest is None:
            self.regions_of_interest = []
        if self.semantic_categories is None:
            self.semantic_categories = []


class LightweightVision:
    """
    Sistema de visión optimizado para bajo consumo

    Técnicas de optimización:
    - Change detection: Solo procesa frames que cambiaron
    - Adaptive sampling: Más frames cuando hay actividad, menos en idle
    - ROI detection: Solo analiza regiones con contenido interesante
    - Text caching: No reanaliza texto que ya detectó
    """

    def __init__(self, enable_clip: bool = True):
        """
        Inicializar vision system

        Args:
            enable_clip: Habilitar análisis semántico con CLIP (más lento pero más inteligente)
        """
        logger.info("🎨 Inicializando EIDOS Lightweight Vision...")

        # Estado
        self.last_frame_hash = None
        self.last_text_cache = {}
        self.frames_without_change = 0

        # CLIP Vision (lazy loaded)
        self.clip_vision = None
        self.enable_clip = enable_clip and CLIP_AVAILABLE
        if self.enable_clip:
            logger.info("   ✅ CLIP semantic analysis enabled")
        else:
            logger.info("   ⚠️  CLIP disabled (faster but less intelligent)")

        # Configuración adaptativa
        self.min_sample_interval = 0.5   # Mínimo: 2 fps cuando hay cambios
        self.max_sample_interval = 5.0   # Máximo: 0.2 fps cuando no hay cambios
        self.current_interval = 1.0      # Actual

        # Estadísticas
        self.stats = {
            'frames_captured': 0,
            'frames_skipped': 0,
            'frames_processed': 0,
            'text_regions_found': 0,
            'cache_hits': 0
        }

        logger.info("✅ Lightweight Vision inicializado")

    def compute_frame_hash(self, frame: np.ndarray, quick: bool = True) -> str:
        """
        Calcula hash del frame para detectar cambios

        quick=True: Usa solo algunos píxeles (más rápido)
        quick=False: Hash completo (más preciso)
        """
        if quick:
            # Muestrear solo 1% de los píxeles (mucho más rápido)
            h, w = frame.shape[:2]
            sample = frame[::10, ::10]  # Cada 10 píxeles
        else:
            sample = frame

        # Reducir a escala de grises y baja resolución
        gray = cv2.cvtColor(sample, cv2.COLOR_BGR2GRAY)
        tiny = cv2.resize(gray, (32, 32))  # Muy pequeño

        # Hash
        return hashlib.md5(tiny.tobytes()).hexdigest()

    def has_frame_changed(self, frame: np.ndarray, threshold: float = 0.1) -> bool:
        """
        Detecta si el frame cambió significativamente

        threshold: Porcentaje de cambio necesario (0-1)
        """
        current_hash = self.compute_frame_hash(frame, quick=True)

        if self.last_frame_hash is None:
            self.last_frame_hash = current_hash
            return True

        changed = current_hash != self.last_frame_hash

        if changed:
            self.last_frame_hash = current_hash
            self.frames_without_change = 0
        else:
            self.frames_without_change += 1

        return changed

    def adapt_sampling_rate(self):
        """
        Ajusta la tasa de muestreo según actividad

        Más actividad → muestrear más rápido
        Sin actividad → muestrear más lento (ahorrar CPU)
        """
        if self.frames_without_change == 0:
            # Actividad detectada, aumentar frecuencia
            self.current_interval = self.min_sample_interval
        elif self.frames_without_change < 5:
            # Actividad reciente
            self.current_interval = 1.0
        elif self.frames_without_change < 20:
            # Poco cambio
            self.current_interval = 2.0
        else:
            # Sin cambios, modo idle
            self.current_interval = self.max_sample_interval

        return self.current_interval

    def find_text_regions(self, frame: np.ndarray) -> List[Dict]:
        """
        Encuentra regiones que probablemente contengan texto

        Usa detección de bordes y morfología (muy rápido, sin ML)
        """
        # Convertir a escala de grises
        gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)

        # Detección de bordes
        edges = cv2.Canny(gray, 50, 150)

        # Morfología para conectar letras
        kernel = cv2.getStructuringElement(cv2.MORPH_RECT, (5, 3))
        dilated = cv2.dilate(edges, kernel, iterations=2)

        # Encontrar contornos
        contours, _ = cv2.findContours(dilated, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)

        # Filtrar por tamaño y aspect ratio (texto típico)
        text_regions = []
        for contour in contours:
            x, y, w, h = cv2.boundingRect(contour)

            # Filtros heurísticos para texto
            aspect_ratio = w / h if h > 0 else 0
            area = w * h

            # Texto típico: ancho > alto, área mínima
            if aspect_ratio > 1.5 and area > 200 and area < frame.shape[0] * frame.shape[1] * 0.5:
                text_regions.append({
                    'x': x,
                    'y': y,
                    'width': w,
                    'height': h,
                    'area': area
                })

        return text_regions

    def extract_text_from_region(self, frame: np.ndarray, region: Dict) -> Optional[str]:
        """
        Extrae texto de una región específica (OCR)

        Solo procesa la región, no todo el frame
        """
        if not OCR_AVAILABLE:
            return None

        try:
            # Extraer ROI
            x, y, w, h = region['x'], region['y'], region['width'], region['height']
            roi = frame[y:y+h, x:x+w]

            # Preprocesar para mejor OCR
            gray = cv2.cvtColor(roi, cv2.COLOR_BGR2GRAY)

            # Threshold adaptativo
            thresh = cv2.adaptiveThreshold(
                gray, 255,
                cv2.ADAPTIVE_THRESH_GAUSSIAN_C,
                cv2.THRESH_BINARY,
                11, 2
            )

            # OCR
            text = pytesseract.image_to_string(thresh, config='--psm 6')

            return text.strip() if text else None

        except Exception as e:
            logger.debug(f"Error en OCR: {e}")
            return None

    def detect_code_patterns(self, text: str) -> bool:
        """
        Detecta si el texto contiene código

        Patterns comunes de código
        """
        code_indicators = [
            'import ', 'from ', 'def ', 'class ', 'function ',
            'const ', 'let ', 'var ', 'fn ', 'pub ', 'use ',
            '#!/', '/*', '//', '<!--',
            '{', '}', '(', ')', ';', '=', '=>', '->'
        ]

        # Contar indicadores
        indicators_found = sum(1 for ind in code_indicators if ind in text)

        # Si hay 3+ indicadores, probablemente es código
        return indicators_found >= 3

    def analyze_frame(self, frame: np.ndarray, force: bool = False) -> FrameAnalysis:
        """
        Analiza un frame de manera eficiente

        force=True: Analizar aunque no haya cambios
        """
        self.stats['frames_captured'] += 1

        timestamp = time.time()

        # Detectar cambios
        has_changed = self.has_frame_changed(frame)

        if not has_changed and not force:
            self.stats['frames_skipped'] += 1
            return FrameAnalysis(
                timestamp=timestamp,
                has_changed=False
            )

        # Frame cambió, procesar
        self.stats['frames_processed'] += 1

        # Encontrar regiones con texto
        text_regions = self.find_text_regions(frame)
        self.stats['text_regions_found'] += len(text_regions)

        # Extraer texto de las 5 regiones más grandes
        text_regions.sort(key=lambda r: r['area'], reverse=True)
        all_text = []
        code_snippets = []

        for region in text_regions[:5]:  # Solo top 5
            text = self.extract_text_from_region(frame, region)
            if text:
                all_text.append(text)

                # Detectar si es código
                if self.detect_code_patterns(text):
                    code_snippets.append(text)

        # CLIP semantic analysis (if enabled)
        semantic_categories = []
        primary_content_type = None

        if self.enable_clip and has_changed:
            # Lazy load CLIP
            if self.clip_vision is None:
                try:
                    self.clip_vision = get_clip_vision()
                    logger.info("🎨 CLIP Vision loaded on-demand")
                except Exception as e:
                    logger.warning(f"Failed to load CLIP: {e}")
                    self.enable_clip = False

            if self.clip_vision:
                try:
                    # Convert numpy to PIL Image
                    pil_image = Image.fromarray(cv2.cvtColor(frame, cv2.COLOR_BGR2RGB))

                    # Save temporary file for CLIP
                    import tempfile
                    with tempfile.NamedTemporaryFile(suffix='.png', delete=False) as tmp:
                        pil_image.save(tmp.name)
                        tmp_path = tmp.name

                    # Analyze with CLIP
                    clip_result = self.clip_vision.analyze_image(tmp_path, top_k=3)

                    # Extract top categories
                    semantic_categories = [
                        {
                            "category": cat.category,
                            "confidence": round(cat.confidence, 3)
                        }
                        for cat in clip_result.all_categories
                    ]

                    # Primary content type is the top category
                    if semantic_categories:
                        primary_content_type = semantic_categories[0]["category"]

                    # Cleanup
                    import os
                    try:
                        os.unlink(tmp_path)
                    except Exception:
                        pass  # error no crítico, continuar
                    self.stats['clip_analyses'] = self.stats.get('clip_analyses', 0) + 1

                except Exception as e:
                    logger.debug(f"CLIP analysis failed: {e}")

        return FrameAnalysis(
            timestamp=timestamp,
            has_changed=True,
            text_detected="\n".join(all_text) if all_text else None,
            code_detected=code_snippets,
            regions_of_interest=text_regions,
            frame_hash=self.compute_frame_hash(frame, quick=False),
            semantic_categories=semantic_categories,
            primary_content_type=primary_content_type
        )

    def watch_screen(self, duration: float = 60.0,
                    callback: Optional[Callable] = None) -> List[FrameAnalysis]:
        """
        Observa la pantalla de manera eficiente

        duration: Segundos a observar
        callback: Función a llamar con cada análisis
        """
        if not SCREEN_CAPTURE_AVAILABLE:
            logger.error("mss no instalado - no se puede capturar pantalla")
            return []

        logger.info(f"👁️  Observando pantalla por {duration}s...")
        logger.info("   Presiona Ctrl+C para detener antes")

        results = []
        start_time = time.time()

        try:
            with mss.mss() as sct:
                monitor = sct.monitors[1]  # Monitor principal

                while time.time() - start_time < duration:
                    # Capturar
                    screenshot = sct.grab(monitor)
                    img = Image.frombytes("RGB", screenshot.size, screenshot.bgra, "raw", "BGRX")
                    frame = np.array(img)
                    frame = cv2.cvtColor(frame, cv2.COLOR_RGB2BGR)

                    # Analizar
                    analysis = self.analyze_frame(frame)
                    results.append(analysis)

                    # Callback
                    if callback:
                        callback(analysis)

                    # Log si detectó algo interesante
                    if analysis.has_changed and analysis.code_detected:
                        logger.info(f"   📝 Código detectado: {len(analysis.code_detected)} snippets")

                    # Adaptar frecuencia
                    interval = self.adapt_sampling_rate()
                    time.sleep(interval)

        except KeyboardInterrupt:
            logger.info("⏹️  Observación detenida por usuario")

        # Estadísticas finales
        elapsed = time.time() - start_time
        logger.info(f"\n📊 Estadísticas de observación:")
        logger.info(f"   Duración: {elapsed:.1f}s")
        logger.info(f"   Frames capturados: {self.stats['frames_captured']}")
        logger.info(f"   Frames procesados: {self.stats['frames_processed']}")
        logger.info(f"   Frames saltados: {self.stats['frames_skipped']}")
        logger.info(f"   Ahorro: {self.stats['frames_skipped']/self.stats['frames_captured']*100:.1f}%")
        logger.info(f"   Regiones de texto: {self.stats['text_regions_found']}")

        return results

    def process_video_efficient(self, video_path: Path,
                               target_fps: float = 1.0) -> List[FrameAnalysis]:
        """
        Procesa video de manera eficiente

        target_fps: FPS objetivo (1 = 1 frame por segundo)
        """
        logger.info(f"🎬 Procesando video: {video_path}")

        cap = cv2.VideoCapture(str(video_path))
        original_fps = cap.get(cv2.CAP_PROP_FPS)
        total_frames = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
        duration = total_frames / original_fps

        logger.info(f"   FPS original: {original_fps:.1f}")
        logger.info(f"   Total frames: {total_frames}")
        logger.info(f"   Duración: {duration:.1f}s")

        # Calcular frame skip
        frame_skip = int(original_fps / target_fps)
        logger.info(f"   Procesando 1 de cada {frame_skip} frames")

        results = []
        frame_count = 0

        while True:
            ret, frame = cap.read()
            if not ret:
                break

            # Solo procesar cada N frames
            if frame_count % frame_skip == 0:
                analysis = self.analyze_frame(frame, force=False)
                analysis.timestamp = frame_count / original_fps
                results.append(analysis)

                if frame_count % (frame_skip * 10) == 0:
                    progress = (frame_count / total_frames) * 100
                    logger.info(f"   Progreso: {progress:.1f}% ({frame_count}/{total_frames})")

            frame_count += 1

        cap.release()

        logger.info(f"✅ Video procesado: {len(results)} frames analizados")
        logger.info(f"   Frames con cambios: {sum(1 for r in results if r.has_changed)}")
        logger.info(f"   Código detectado en: {sum(1 for r in results if r.code_detected)} frames")

        return results


# Singleton global
_vision_instance = None

def get_lightweight_vision(enable_clip: bool = True) -> LightweightVision:
    """
    Get global lightweight vision instance

    Args:
        enable_clip: Enable CLIP semantic analysis (slower but smarter)
                    Set to False for maximum speed
    """
    global _vision_instance
    if _vision_instance is None:
        _vision_instance = LightweightVision(enable_clip=enable_clip)
    return _vision_instance
