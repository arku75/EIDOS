"""
EIDOS core/ui_parser.py — OmniParser-Inspired UI Element Detection
===================================================================
Detecta elementos de UI (botones, inputs, links, iconos) en screenshots
usando OpenCV + OCR + VLM local (moondream/llama3.2-vision).

Inspirado en OmniParser de Microsoft pero 100% local:
  - Fase 1: OpenCV → detecta regiones rectangulares (botones, inputs, frames)
  - Fase 2: OCR (Tesseract) → extrae texto de cada región
  - Fase 3: Clasificación heurística → asigna tipo (button, input, link, label, icon)
  - Fase 4: VLM (opcional) → moondream describe elementos ambiguos

Uso:
    from core.ui_parser import UIParser

    parser = UIParser()
    elements = parser.parse_screen()                    # screenshot automático
    elements = parser.parse_image("/tmp/screenshot.png") # imagen específica

    for el in elements:
        print(f"[{el.type}] '{el.text}' at ({el.cx},{el.cy}) {el.w}x{el.h}")

    # Click en un botón por texto
    btn = parser.find_element("Accept", element_type="button")
    if btn:
        import pyautogui
        pyautogui.click(btn.cx, btn.cy)
"""
from __future__ import annotations

import os
import io
import json
import base64
import time
import urllib.request
from pathlib import Path
from dataclasses import dataclass, field
from typing import Optional

import logging
log = logging.getLogger("eidos.ui_parser")

# ═══════════════════════════════════════════════════════════════════════════════
#  IMPORTS (graceful fallback)
# ═══════════════════════════════════════════════════════════════════════════════

try:
    import cv2
    import numpy as np
    HAS_CV2 = True
except ImportError:
    HAS_CV2 = False

try:
    from PIL import Image
    HAS_PIL = True
except ImportError:
    HAS_PIL = False

try:
    import pytesseract
    HAS_OCR = True
except ImportError:
    HAS_OCR = False

try:
    import mss
    HAS_MSS = True
except ImportError:
    HAS_MSS = False

OLLAMA_URL = "http://localhost:11434"
VISION_FAST = "moondream:latest"

# ═══════════════════════════════════════════════════════════════════════════════
#  DATA STRUCTURES
# ═══════════════════════════════════════════════════════════════════════════════

@dataclass
class UIElement:
    """Elemento de UI detectado en pantalla."""
    type: str           # button, input, link, label, icon, checkbox, dropdown, tab, menu, unknown
    text: str           # texto visible del elemento
    x: int              # esquina superior izquierda X
    y: int              # esquina superior izquierda Y
    w: int              # ancho
    h: int              # alto
    confidence: float   # 0.0 - 1.0
    source: str = ""    # "cv2", "ocr", "vlm", "atspi"
    ref: str = ""       # referencia tipo "e1", "e2" para interacción

    @property
    def cx(self) -> int:
        """Centro X."""
        return self.x + self.w // 2

    @property
    def cy(self) -> int:
        """Centro Y."""
        return self.y + self.h // 2

    @property
    def x2(self) -> int:
        return self.x + self.w

    @property
    def y2(self) -> int:
        return self.y + self.h

    @property
    def area(self) -> int:
        return self.w * self.h

    def to_dict(self) -> dict:
        return {
            "type": self.type, "text": self.text,
            "x": self.x, "y": self.y, "w": self.w, "h": self.h,
            "cx": self.cx, "cy": self.cy,
            "confidence": round(self.confidence, 2),
            "source": self.source, "ref": self.ref,
        }


# ═══════════════════════════════════════════════════════════════════════════════
#  UI PARSER
# ═══════════════════════════════════════════════════════════════════════════════

class UIParser:
    """
    Detecta elementos de UI en screenshots.

    Arquitectura 4 fases:
      1. OpenCV contour detection → regiones rectangulares candidatas
      2. OCR por región → extrae texto visible
      3. Heurística → clasifica tipo de elemento
      4. VLM (opcional) → describe elementos sin texto o ambiguos
    """

    # Dimensiones mínimas/máximas para considerar un rectángulo como UI element
    MIN_W = 20
    MIN_H = 12
    MAX_W_RATIO = 0.95   # max % del ancho de pantalla
    MAX_H_RATIO = 0.80   # max % del alto de pantalla

    # Aspect ratios típicos de elementos UI
    BUTTON_ASPECT_MIN = 1.5    # botones son más anchos que altos
    BUTTON_ASPECT_MAX = 12.0
    INPUT_ASPECT_MIN = 2.5     # inputs son muy anchos
    INPUT_ASPECT_MAX = 30.0
    ICON_ASPECT_MIN = 0.7      # iconos son cuadrados
    ICON_ASPECT_MAX = 1.4
    ICON_MAX_SIZE = 64         # iconos son pequeños

    def __init__(self, vlm_model: str = VISION_FAST, use_vlm: bool = False):
        self.vlm_model = vlm_model
        self.use_vlm = use_vlm
        self._ref_counter = 0
        self._last_elements: list[UIElement] = []
        log.info(f"🖼️ [UIParser] Init (CV2={HAS_CV2}, OCR={HAS_OCR}, VLM={'ON' if use_vlm else 'OFF'})")

    def _next_ref(self) -> str:
        self._ref_counter += 1
        return f"u{self._ref_counter}"

    # ─────────────────────────────────────────────────────────────────────────
    #  PHASE 0: Screenshot capture
    # ─────────────────────────────────────────────────────────────────────────

    def _capture_screen(self) -> Optional[np.ndarray]:
        """Captura pantalla y devuelve como numpy array BGR."""
        if not HAS_MSS or not HAS_CV2:
            return None
        with mss.mss() as sct:
            monitor = sct.monitors[1]  # monitor principal
            shot = sct.grab(monitor)
            img = np.array(shot)
            # mss devuelve BGRA, convertir a BGR
            return cv2.cvtColor(img, cv2.COLOR_BGRA2BGR)

    def _load_image(self, path: str) -> Optional[np.ndarray]:
        """Carga imagen desde archivo."""
        if not HAS_CV2:
            return None
        if not os.path.exists(path):
            return None
        return cv2.imread(path)

    # ─────────────────────────────────────────────────────────────────────────
    #  PHASE 1: OpenCV contour detection
    # ─────────────────────────────────────────────────────────────────────────

    def _detect_rectangles(self, img: np.ndarray) -> list[tuple]:
        """
        Detecta regiones rectangulares candidatas a ser UI elements.

        Returns: list of (x, y, w, h) tuples
        """
        h_img, w_img = img.shape[:2]
        max_w = int(w_img * self.MAX_W_RATIO)
        max_h = int(h_img * self.MAX_H_RATIO)

        # Convertir a gris
        gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)

        # Multi-strategy edge detection para capturar botones de distintos estilos
        candidates = set()

        # Strategy 1: Canny edges → detecta bordes definidos (botones con borde)
        edges = cv2.Canny(gray, 50, 150)
        kernel = cv2.getStructuringElement(cv2.MORPH_RECT, (3, 3))
        edges = cv2.dilate(edges, kernel, iterations=1)
        contours, _ = cv2.findContours(edges, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)

        for cnt in contours:
            x, y, w, h = cv2.boundingRect(cnt)
            if w >= self.MIN_W and h >= self.MIN_H and w <= max_w and h <= max_h:
                # Verificar que es razonablemente rectangular
                area = cv2.contourArea(cnt)
                rect_area = w * h
                if rect_area > 0 and area / rect_area > 0.4:
                    candidates.add((x, y, w, h))

        # Strategy 2: Adaptive threshold → detecta elementos con fondo diferente
        thresh = cv2.adaptiveThreshold(gray, 255, cv2.ADAPTIVE_THRESH_GAUSSIAN_C,
                                        cv2.THRESH_BINARY_INV, 11, 2)
        thresh = cv2.dilate(thresh, kernel, iterations=2)
        contours2, _ = cv2.findContours(thresh, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)

        for cnt in contours2:
            x, y, w, h = cv2.boundingRect(cnt)
            if w >= self.MIN_W and h >= self.MIN_H and w <= max_w and h <= max_h:
                peri = cv2.arcLength(cnt, True)
                approx = cv2.approxPolyDP(cnt, 0.04 * peri, True)
                if len(approx) >= 4:  # rectangular-ish
                    candidates.add((x, y, w, h))

        # Strategy 3: Color segmentation → detecta botones con color de fondo sólido
        hsv = cv2.cvtColor(img, cv2.COLOR_BGR2HSV)
        # Buscar regiones saturadas (botones coloreados)
        sat = hsv[:, :, 1]
        _, sat_mask = cv2.threshold(sat, 50, 255, cv2.THRESH_BINARY)
        sat_mask = cv2.morphologyEx(sat_mask, cv2.MORPH_CLOSE, kernel, iterations=3)
        contours3, _ = cv2.findContours(sat_mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)

        for cnt in contours3:
            x, y, w, h = cv2.boundingRect(cnt)
            if w >= self.MIN_W and h >= self.MIN_H and w <= max_w and h <= max_h:
                area = cv2.contourArea(cnt)
                rect_area = w * h
                if rect_area > 0 and area / rect_area > 0.5:
                    candidates.add((x, y, w, h))

        # Dedup: merge overlapping rectangles
        return self._merge_overlapping(list(candidates), w_img, h_img)

    def _merge_overlapping(self, rects: list[tuple], img_w: int, img_h: int) -> list[tuple]:
        """Merge rectangles que se solapan >50%."""
        if not rects:
            return []

        # Sort by area (largest first)
        rects = sorted(rects, key=lambda r: r[2] * r[3], reverse=True)
        merged = []
        used = [False] * len(rects)

        for i in range(len(rects)):
            if used[i]:
                continue
            x1, y1, w1, h1 = rects[i]

            for j in range(i + 1, len(rects)):
                if used[j]:
                    continue
                x2, y2, w2, h2 = rects[j]

                # Calcular overlap
                ox = max(0, min(x1 + w1, x2 + w2) - max(x1, x2))
                oy = max(0, min(y1 + h1, y2 + h2) - max(y1, y2))
                overlap = ox * oy
                smaller_area = min(w1 * h1, w2 * h2)

                if smaller_area > 0 and overlap / smaller_area > 0.5:
                    used[j] = True

            merged.append((x1, y1, w1, h1))
            used[i] = True

        return merged[:200]  # Limitar a 200 candidatos

    # ─────────────────────────────────────────────────────────────────────────
    #  PHASE 2: OCR text extraction per region
    # ─────────────────────────────────────────────────────────────────────────

    def _extract_text(self, img: np.ndarray, x: int, y: int, w: int, h: int) -> str:
        """Extrae texto de una región de la imagen con OCR."""
        if not HAS_OCR or not HAS_PIL:
            return ""

        # Recortar región con padding
        pad = 2
        y1 = max(0, y - pad)
        y2 = min(img.shape[0], y + h + pad)
        x1 = max(0, x - pad)
        x2 = min(img.shape[1], x + w + pad)
        roi = img[y1:y2, x1:x2]

        if roi.size == 0:
            return ""

        # Preprocesar para mejor OCR
        gray = cv2.cvtColor(roi, cv2.COLOR_BGR2GRAY)
        # Escalar si es muy pequeño
        if h < 30:
            scale = 30.0 / h
            gray = cv2.resize(gray, None, fx=scale, fy=scale, interpolation=cv2.INTER_CUBIC)

        # Binarizar
        _, binary = cv2.threshold(gray, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)

        try:
            pil_img = Image.fromarray(binary)
            text = pytesseract.image_to_string(pil_img, config="--psm 7 -c tessedit_char_whitelist=ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789 .-_:/@")
            return text.strip()
        except Exception:
            return ""

    def _batch_ocr(self, img: np.ndarray, rects: list[tuple]) -> dict[tuple, str]:
        """OCR batch para todas las regiones."""
        results = {}
        for rect in rects:
            x, y, w, h = rect
            text = self._extract_text(img, x, y, w, h)
            results[rect] = text
        return results

    # ─────────────────────────────────────────────────────────────────────────
    #  PHASE 3: Heuristic classification
    # ─────────────────────────────────────────────────────────────────────────

    def _classify_element(self, img: np.ndarray, x: int, y: int, w: int, h: int,
                          text: str, img_w: int, img_h: int) -> tuple[str, float]:
        """
        Clasifica un rectángulo detectado en un tipo de UI element.

        Returns: (type, confidence)
        """
        aspect = w / max(h, 1)
        area = w * h
        rel_w = w / max(img_w, 1)
        rel_h = h / max(img_h, 1)

        # Extraer features de color de la región
        roi = img[y:y+h, x:x+w]
        if roi.size == 0:
            return "unknown", 0.3

        mean_color = roi.mean(axis=(0, 1))  # BGR mean
        color_std = roi.std(axis=(0, 1))     # varianza de color
        is_uniform = color_std.mean() < 30   # fondo uniforme = probablemente botón/input

        # Detectar borde
        gray_roi = cv2.cvtColor(roi, cv2.COLOR_BGR2GRAY)
        edges_roi = cv2.Canny(gray_roi, 50, 150)
        border_pixels = np.sum(edges_roi > 0)
        perimeter = 2 * (w + h)
        has_border = border_pixels > perimeter * 0.3 if perimeter > 0 else False

        has_text = len(text) > 0
        text_lower = text.lower() if text else ""

        # ── Classification rules ──

        # Checkbox/Radio: pequeño, cuadrado
        if w < 30 and h < 30 and 0.7 < aspect < 1.4:
            return "checkbox", 0.7

        # Icon: pequeño, cuadrado, sin texto
        if (w <= self.ICON_MAX_SIZE and h <= self.ICON_MAX_SIZE
                and self.ICON_ASPECT_MIN <= aspect <= self.ICON_ASPECT_MAX
                and not has_text):
            return "icon", 0.6

        # Button: rectangular horizontal, fondo uniforme, texto corto
        if (self.BUTTON_ASPECT_MIN <= aspect <= self.BUTTON_ASPECT_MAX
                and 15 <= h <= 60 and is_uniform and has_text and len(text) < 30):
            # Button keywords boost confidence
            btn_words = {"ok", "cancel", "submit", "save", "delete", "accept",
                         "close", "next", "back", "apply", "send", "login",
                         "sign", "aceptar", "cancelar", "guardar", "enviar",
                         "cerrar", "siguiente", "continuar", "confirm"}
            conf = 0.85 if any(w in text_lower for w in btn_words) else 0.7
            return "button", conf

        # Input field: wide rectangle, light background, has border, little/no text
        if (self.INPUT_ASPECT_MIN <= aspect and 20 <= h <= 50
                and has_border and mean_color.mean() > 180):
            return "input", 0.75

        # Dropdown/Select: like input but with a small arrow region
        if (self.INPUT_ASPECT_MIN <= aspect and 20 <= h <= 45
                and has_border and rel_w < 0.5):
            return "dropdown", 0.55

        # Tab: horizontal, at top of screen area, uniform color
        if (1.5 <= aspect <= 6.0 and 25 <= h <= 45
                and y < img_h * 0.15 and is_uniform and has_text):
            return "tab", 0.6

        # Menu item: wide, short, text
        if (aspect > 3.0 and 18 <= h <= 35 and has_text and rel_w > 0.1):
            return "menu_item", 0.55

        # Link: text with specific patterns
        if has_text and len(text) < 50:
            if any(p in text_lower for p in ["http", "www", ".com", ".org"]):
                return "link", 0.8
            # Colored text on neutral background (links are usually blue/colored)
            b, g, r = mean_color
            if b > 150 and r < 100 and g < 100:  # blue-ish text region
                return "link", 0.6

        # Label: has text, not interactive-looking
        if has_text and not is_uniform and not has_border:
            return "label", 0.5

        # Large container/panel: too big to be a single element
        if rel_w > 0.6 and rel_h > 0.3:
            return "panel", 0.4

        # Text with border = could be a button
        if has_text and has_border and 15 <= h <= 50:
            return "button", 0.55

        return "unknown", 0.3

    # ─────────────────────────────────────────────────────────────────────────
    #  PHASE 4: VLM classification (optional, for ambiguous elements)
    # ─────────────────────────────────────────────────────────────────────────

    def _vlm_classify(self, img: np.ndarray, elements: list[UIElement]) -> list[UIElement]:
        """
        Usa moondream para clasificar elementos ambiguos (confidence < 0.5).
        Solo se llama si use_vlm=True y hay elementos ambiguos.
        """
        ambiguous = [e for e in elements if e.confidence < 0.5 and e.type in ("unknown", "icon")]
        if not ambiguous:
            return elements

        # Limitar VLM calls (son lentos)
        ambiguous = ambiguous[:5]

        for el in ambiguous:
            try:
                roi = img[el.y:el.y2, el.x:el.x2]
                if roi.size == 0:
                    continue

                # Encode region
                _, buffer = cv2.imencode('.jpg', roi, [cv2.IMWRITE_JPEG_QUALITY, 80])
                img_b64 = base64.b64encode(buffer).decode()

                payload = json.dumps({
                    "model": self.vlm_model,
                    "prompt": "What type of UI element is this? Answer with ONE word: button, input, link, icon, checkbox, dropdown, tab, menu, label, or image.",
                    "images": [img_b64],
                    "stream": False,
                    "options": {"num_predict": 10}
                }).encode()

                req = urllib.request.Request(
                    f"{OLLAMA_URL}/api/generate",
                    data=payload,
                    headers={"Content-Type": "application/json"}
                )

                with urllib.request.urlopen(req, timeout=30) as resp:
                    data = json.load(resp)
                    answer = data.get("response", "").strip().lower()

                    valid_types = {"button", "input", "link", "icon", "checkbox",
                                   "dropdown", "tab", "menu", "label", "image"}
                    # Extract first valid type from answer
                    for vt in valid_types:
                        if vt in answer:
                            el.type = vt
                            el.confidence = 0.65
                            el.source = "vlm"
                            break

            except Exception as e:
                log.debug(f"VLM classify failed for {el.ref}: {e}")
                continue

        return elements

    # ─────────────────────────────────────────────────────────────────────────
    #  PHASE 5: Full screen VLM parse (fallback when CV2 finds too few)
    # ─────────────────────────────────────────────────────────────────────────

    def _vlm_full_parse(self, img: np.ndarray) -> list[UIElement]:
        """
        Envía la pantalla completa al VLM pidiendo que liste UI elements.
        Fallback cuando OpenCV no detecta suficientes elementos.
        """
        try:
            # Resize for VLM
            h, w = img.shape[:2]
            scale = min(1280 / w, 720 / h, 1.0)
            if scale < 1.0:
                resized = cv2.resize(img, (int(w * scale), int(h * scale)))
            else:
                resized = img
                scale = 1.0

            _, buffer = cv2.imencode('.jpg', resized, [cv2.IMWRITE_JPEG_QUALITY, 85])
            img_b64 = base64.b64encode(buffer).decode()

            prompt = (
                "List ALL interactive UI elements visible in this screenshot. "
                "For each element, provide: type (button/input/link/menu/tab/icon/checkbox/dropdown), "
                "the visible text or label, and approximate position as percentage from top-left "
                "(e.g., 'button \"Save\" at 80%,90%'). "
                "List one element per line. Be thorough."
            )

            payload = json.dumps({
                "model": self.vlm_model,
                "prompt": prompt,
                "images": [img_b64],
                "stream": False,
                "options": {"num_predict": 500}
            }).encode()

            req = urllib.request.Request(
                f"{OLLAMA_URL}/api/generate",
                data=payload,
                headers={"Content-Type": "application/json"}
            )

            with urllib.request.urlopen(req, timeout=60) as resp:
                data = json.load(resp)
                text = data.get("response", "")

            return self._parse_vlm_response(text, w, h, scale)

        except Exception as e:
            log.warning(f"VLM full parse failed: {e}")
            return []

    def _parse_vlm_response(self, text: str, img_w: int, img_h: int, scale: float) -> list[UIElement]:
        """Parsea la respuesta del VLM en UIElements."""
        elements = []
        import re

        for line in text.strip().split('\n'):
            line = line.strip()
            if not line or line.startswith('#'):
                continue

            # Intentar extraer tipo
            el_type = "unknown"
            for t in ["button", "input", "link", "menu", "tab", "icon", "checkbox", "dropdown"]:
                if t in line.lower():
                    el_type = t
                    break

            # Intentar extraer texto entre comillas
            quoted = re.findall(r'"([^"]+)"', line)
            el_text = quoted[0] if quoted else ""

            # Intentar extraer porcentajes de posición
            pcts = re.findall(r'(\d{1,3})%', line)
            if len(pcts) >= 2:
                pct_x = int(pcts[0]) / 100.0
                pct_y = int(pcts[1]) / 100.0
                # Convertir porcentaje a pixels (ajustar por scale)
                cx = int(pct_x * img_w)
                cy = int(pct_y * img_h)
                # Tamaño estimado
                est_w = 80
                est_h = 30

                elements.append(UIElement(
                    type=el_type, text=el_text,
                    x=max(0, cx - est_w // 2), y=max(0, cy - est_h // 2),
                    w=est_w, h=est_h,
                    confidence=0.5, source="vlm",
                    ref=self._next_ref(),
                ))

        return elements

    # ─────────────────────────────────────────────────────────────────────────
    #  PUBLIC API
    # ─────────────────────────────────────────────────────────────────────────

    def parse_image(self, path: str, min_confidence: float = 0.4) -> list[UIElement]:
        """
        Parsea una imagen y devuelve UI elements detectados.

        Args:
            path: Ruta a la imagen PNG/JPG
            min_confidence: Confianza mínima para incluir un elemento

        Returns:
            Lista de UIElement ordenados por posición (top-left → bottom-right)
        """
        img = self._load_image(path)
        if img is None:
            log.error(f"No se pudo cargar: {path}")
            return []
        return self._parse(img, min_confidence)

    def parse_screen(self, min_confidence: float = 0.4) -> list[UIElement]:
        """
        Captura la pantalla actual y parsea UI elements.

        Returns:
            Lista de UIElement ordenados por posición
        """
        img = self._capture_screen()
        if img is None:
            log.error("No se pudo capturar pantalla")
            return []
        return self._parse(img, min_confidence)

    def _parse(self, img: np.ndarray, min_confidence: float) -> list[UIElement]:
        """Pipeline completo de parsing."""
        self._ref_counter = 0
        start = time.time()
        h_img, w_img = img.shape[:2]

        # Phase 1: OpenCV rectangle detection
        rects = self._detect_rectangles(img)
        log.info(f"Phase 1 (CV2): {len(rects)} candidate rectangles")

        # Phase 2: OCR text extraction
        texts = self._batch_ocr(img, rects)
        log.info(f"Phase 2 (OCR): {sum(1 for t in texts.values() if t)} regions with text")

        # Phase 3: Heuristic classification
        elements = []
        for rect in rects:
            x, y, w, h = rect
            text = texts.get(rect, "")
            el_type, conf = self._classify_element(img, x, y, w, h, text, w_img, h_img)
            elements.append(UIElement(
                type=el_type, text=text,
                x=x, y=y, w=w, h=h,
                confidence=conf, source="cv2",
                ref=self._next_ref(),
            ))

        # Phase 4: VLM classification for ambiguous elements
        if self.use_vlm:
            elements = self._vlm_classify(img, elements)

        # Fallback: if very few elements found, try full VLM parse
        if len(elements) < 3 and self.use_vlm:
            vlm_elements = self._vlm_full_parse(img)
            elements.extend(vlm_elements)

        # Filter by confidence
        elements = [e for e in elements if e.confidence >= min_confidence]

        # Filter out panels (too large to be useful as click targets)
        elements = [e for e in elements if e.type != "panel"]

        # Sort by position: top-to-bottom, left-to-right
        elements.sort(key=lambda e: (e.y // 30, e.x))

        # Re-assign refs
        for i, el in enumerate(elements, 1):
            el.ref = f"u{i}"

        self._last_elements = elements
        elapsed = round(time.time() - start, 2)
        log.info(f"UIParser: {len(elements)} elements in {elapsed}s")

        return elements

    def find_element(self, text: str, element_type: str = None) -> Optional[UIElement]:
        """
        Busca un elemento por texto (y opcionalmente tipo).

        Args:
            text: Texto a buscar (case-insensitive, partial match)
            element_type: Filtrar por tipo (button, input, etc.)

        Returns:
            UIElement más relevante o None
        """
        text_lower = text.lower()
        candidates = []

        for el in self._last_elements:
            if element_type and el.type != element_type:
                continue
            if text_lower in el.text.lower():
                candidates.append(el)

        if not candidates:
            return None

        # Preferir match exacto, luego mayor confidence
        exact = [c for c in candidates if c.text.lower() == text_lower]
        if exact:
            return max(exact, key=lambda e: e.confidence)
        return max(candidates, key=lambda e: e.confidence)

    def find_by_ref(self, ref: str) -> Optional[UIElement]:
        """Busca elemento por referencia (u1, u2, etc.)."""
        for el in self._last_elements:
            if el.ref == ref:
                return el
        return None

    def get_interactive(self) -> list[UIElement]:
        """Devuelve solo elementos interactivos (botones, inputs, links, etc.)."""
        interactive_types = {"button", "input", "link", "checkbox", "dropdown", "tab", "menu_item"}
        return [e for e in self._last_elements if e.type in interactive_types]

    def to_text(self, elements: list[UIElement] = None) -> str:
        """
        Genera representación textual de los elementos (para pasar al LLM).
        Formato compacto tipo accessibility tree.
        """
        if elements is None:
            elements = self._last_elements

        if not elements:
            return "[No UI elements detected]"

        lines = []
        for el in elements:
            text_part = f' "{el.text}"' if el.text else ""
            lines.append(f"[{el.ref}] {el.type}{text_part} @({el.cx},{el.cy}) {el.w}x{el.h}")

        return "\n".join(lines)

    def annotate_image(self, img_path: str, output_path: str = None,
                       elements: list[UIElement] = None) -> str:
        """
        Dibuja bounding boxes y labels sobre la imagen.
        Útil para debug y para que el LLM vea los elementos marcados.

        Returns: ruta a la imagen anotada
        """
        if not HAS_CV2:
            return ""

        img = self._load_image(img_path)
        if img is None:
            return ""

        if elements is None:
            elements = self._last_elements

        if output_path is None:
            output_path = img_path.replace(".png", "_annotated.png").replace(".jpg", "_annotated.jpg")

        # Colores por tipo
        colors = {
            "button":    (0, 200, 0),     # green
            "input":     (200, 100, 0),   # blue-ish
            "link":      (200, 0, 0),     # blue
            "icon":      (0, 200, 200),   # yellow
            "checkbox":  (200, 0, 200),   # magenta
            "dropdown":  (0, 150, 200),   # orange
            "tab":       (150, 150, 0),   # teal
            "menu_item": (100, 200, 100), # light green
            "label":     (150, 150, 150), # gray
            "unknown":   (100, 100, 100), # dark gray
        }

        for el in elements:
            color = colors.get(el.type, (100, 100, 100))
            # Bounding box
            cv2.rectangle(img, (el.x, el.y), (el.x2, el.y2), color, 2)
            # Label
            label = f"{el.ref}:{el.type}"
            font = cv2.FONT_HERSHEY_SIMPLEX
            (tw, th), _ = cv2.getTextSize(label, font, 0.4, 1)
            cv2.rectangle(img, (el.x, el.y - th - 4), (el.x + tw + 4, el.y), color, -1)
            cv2.putText(img, label, (el.x + 2, el.y - 2), font, 0.4, (255, 255, 255), 1)

        cv2.imwrite(output_path, img)
        return output_path

    @property
    def stats(self) -> dict:
        from collections import Counter
        types = Counter(e.type for e in self._last_elements)
        return {
            "total_elements": len(self._last_elements),
            "by_type": dict(types),
            "interactive": len(self.get_interactive()),
            "has_cv2": HAS_CV2,
            "has_ocr": HAS_OCR,
            "vlm_enabled": self.use_vlm,
        }


# ═══════════════════════════════════════════════════════════════════════════════
#  SINGLETON
# ═══════════════════════════════════════════════════════════════════════════════

_parser: Optional[UIParser] = None

def get_ui_parser(use_vlm: bool = False) -> UIParser:
    global _parser
    if _parser is None:
        _parser = UIParser(use_vlm=use_vlm)
    return _parser


# ═══════════════════════════════════════════════════════════════════════════════
#  CLI TEST
# ═══════════════════════════════════════════════════════════════════════════════

if __name__ == "__main__":
    import sys

    parser = UIParser(use_vlm="--vlm" in sys.argv)

    if len(sys.argv) > 1 and not sys.argv[1].startswith("--"):
        # Parse specific image
        elements = parser.parse_image(sys.argv[1])
    else:
        # Parse current screen
        print("Capturing screen...")
        elements = parser.parse_screen()

    print(f"\n{'='*60}")
    print(f"  UI Elements Detected: {len(elements)}")
    print(f"  Interactive: {len(parser.get_interactive())}")
    print(f"{'='*60}\n")

    print(parser.to_text(elements))

    print(f"\nStats: {parser.stats}")

    # Annotate if image provided
    if len(sys.argv) > 1 and not sys.argv[1].startswith("--"):
        out = parser.annotate_image(sys.argv[1])
        if out:
            print(f"\nAnnotated image: {out}")
