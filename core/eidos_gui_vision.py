"""
core/eidos_gui_vision.py — Visión de GUI para apps con login [S122-I Nº1]
=========================================================================
SER quiere que EIDOS use CUALQUIER app GUI (n8n, Telegram, Maltego...):
verla por VISIÓN, entenderla, e iniciar sesión con sus credenciales.

Esta capa envuelve core/perception.py añadiendo lo que faltaba para que sea
PRÁCTICO en la pantalla 4K de SER:

  1. REDUCCIÓN de imagen antes de OCR/VLM (4K tesseract tarda >50s → reducido
     ~5-10s) — el cuello de botella real medido en S122-I.
  2. REESCALADO de coordenadas: tras OCR en la imagen reducida, las coords se
     multiplican de vuelta a la resolución REAL para que un clic caiga exacto.
  3. Helpers: see() (leer pantalla), find(texto), describe() (VLM general).

SEGURIDAD: este módulo SOLO LEE la pantalla (screenshot+OCR+VLM). NO toca el
ratón/teclado. La interacción (clic/escritura) la hace eidos_control con SER
presente (riesgo de grab — feedback_raton_grab). Login: credenciales SIEMPRE
desde ~/.eidos/secrets.env, NUNCA en logs.
"""

from __future__ import annotations

import logging
import os
import time
from dataclasses import dataclass
from pathlib import Path
from typing import List, Optional

log = logging.getLogger("eidos.gui_vision")

# Ancho al que se reduce el screenshot antes de OCR/VLM (equilibrio velocidad/legibilidad)
OCR_WIDTH = int(os.environ.get("EIDOS_GUI_OCR_WIDTH", "1600"))
VLM_WIDTH = int(os.environ.get("EIDOS_GUI_VLM_WIDTH", "1024"))


@dataclass
class GuiElement:
    """Elemento de UI detectado, con coordenadas en la pantalla REAL (sin escalar)."""
    text: str
    x: int          # centro X en pantalla real
    y: int          # centro Y en pantalla real
    x1: int
    y1: int
    x2: int
    y2: int
    conf: float = 0.0

    @property
    def center(self) -> tuple[int, int]:
        return (self.x, self.y)


def _reduce_image(src_path: str, target_w: int) -> tuple[str, float]:
    """Reduce la imagen a `target_w` de ancho. Devuelve (ruta_reducida, factor).

    factor = ancho_real / ancho_reducido → multiplicar coords reducidas × factor
    para volver a la pantalla real.
    """
    from PIL import Image
    img = Image.open(src_path)
    w, h = img.size
    if w <= target_w:
        return src_path, 1.0
    nw = target_w
    nh = int(h * nw / w)
    small = img.resize((nw, nh), Image.LANCZOS)
    out = str(Path(src_path).with_suffix("")) + f"_w{target_w}.png"
    small.save(out)
    return out, w / nw


def capture(label: str = "gui") -> Optional[str]:
    """Toma un screenshot de la pantalla. Devuelve la ruta o None."""
    try:
        from core.perception import take_screenshot
        return take_screenshot(label)
    except Exception as e:
        log.warning("capture falló: %s", e)
        return None


def see(label: str = "gui", min_conf: int = 50) -> List[GuiElement]:
    """Lee la pantalla por OCR (rápido) y devuelve elementos con coords REALES.

    Reduce la imagen primero (4K → OCR_WIDTH) y reescala las coords de vuelta.
    """
    shot = capture(label)
    if not shot:
        return []
    return see_path(shot, min_conf=min_conf)


def _preprocess_for_ocr(img):
    """Realza contraste para texto de bajo contraste (temas oscuros tipo n8n).

    [S122-I] NO invertir globalmente (rompe pantallas con un card pequeño sobre
    fondo oscuro → dejaba todo blanco y OCR vacío). En su lugar: escala de grises
    + autocontrast agresivo, que sube el contraste del texto tenue SIN destruir
    la imagen. tesseract LSTM ya lee texto claro/oscuro si hay contraste.
    """
    from PIL import ImageOps
    g = img.convert("L")
    g = ImageOps.autocontrast(g, cutoff=1)
    return g


def see_path(shot: str, min_conf: int = 40) -> List[GuiElement]:
    """Como see() pero sobre un screenshot ya tomado.

    Hace 2 pasadas de OCR (imagen normal + preprocesada para tema oscuro) y
    combina, para leer tanto temas claros como oscuros. min_conf 40 (antes 50)
    para no perder texto de bajo contraste.
    """
    t0 = time.time()
    try:
        import pytesseract
        from PIL import Image
    except ImportError:
        log.warning("pytesseract/PIL no disponibles")
        return []

    small_path, factor = _reduce_image(shot, OCR_WIDTH)
    try:
        base = Image.open(small_path)
    except Exception as e:
        log.warning("no se pudo abrir imagen: %s", e)
        return []

    # 2 variantes: original + preprocesada (tema oscuro). Combina resultados.
    variants = [base]
    try:
        variants.append(_preprocess_for_ocr(base))
    except Exception as e:
        log.debug("preprocess falló: %s", e)

    elements: List[GuiElement] = []
    seen = set()
    for variant in variants:
        try:
            data = pytesseract.image_to_data(
                variant, output_type=pytesseract.Output.DICT, lang="spa+eng")
        except Exception as e:
            log.warning("OCR falló: %s", e)
            continue
        n = len(data["text"])
        for i in range(n):
            text = data["text"][i].strip()
            try:
                conf = int(data["conf"][i])
            except (ValueError, TypeError):
                conf = 0
            if not text or conf < min_conf:
                continue
            # Coords en imagen reducida → escalar a pantalla real
            x1 = int(data["left"][i] * factor)
            y1 = int(data["top"][i] * factor)
            w = int(data["width"][i] * factor)
            h = int(data["height"][i] * factor)
            # Dedup por (texto, posición aproximada)
            key = (text.lower(), x1 // 30, y1 // 30)
            if key in seen:
                continue
            seen.add(key)
            elements.append(GuiElement(
                text=text, x=x1 + w // 2, y=y1 + h // 2,
                x1=x1, y1=y1, x2=x1 + w, y2=y1 + h, conf=float(conf)))

    log.info("see: %d elementos en %.1fs (factor x%.2f)",
             len(elements), time.time() - t0, factor)
    return elements


def find(query: str, elements: Optional[List[GuiElement]] = None,
         label: str = "gui") -> Optional[GuiElement]:
    """Localiza un elemento por texto (exacto → parcial). Coords REALES para clic."""
    if elements is None:
        elements = see(label)
    ql = query.lower().strip()
    # Exacto
    for el in elements:
        if el.text.lower() == ql:
            return el
    # Parcial (la query contenida en el elemento o viceversa)
    for el in elements:
        tl = el.text.lower()
        if ql in tl or (len(tl) > 2 and tl in ql):
            return el
    return None


def find_all(query: str, elements: Optional[List[GuiElement]] = None,
             label: str = "gui") -> List[GuiElement]:
    """Todas las coincidencias de un texto."""
    if elements is None:
        elements = see(label)
    ql = query.lower().strip()
    return [el for el in elements if ql in el.text.lower()]


def describe(label: str = "gui", question: str = "", deep: bool = False) -> str:
    """Descripción general de la pantalla con VLM (moondream). Imagen reducida.

    Lento en CPU (~30-120s en 4K incluso reducido). Para entender layout cuando
    el OCR no basta. Para texto exacto usar see()/find() (más rápido).
    """
    shot = capture(label)
    if not shot:
        return ""
    small_path, _ = _reduce_image(shot, VLM_WIDTH)
    try:
        from core.perception import analyze_screen
        q = question or "Describe esta pantalla: ventanas, app, campos, botones."
        # [S122-I] SIN max_tokens (SER): sistema vivo, sin límite de respuesta.
        return analyze_screen(small_path, question=q, deep=deep)
    except Exception as e:
        log.warning("describe falló: %s", e)
        return ""


def read_text(label: str = "gui") -> str:
    """Todo el texto visible de la pantalla como una cadena (rápido)."""
    els = see(label)
    return " ".join(e.text for e in els)


# ── CLI de prueba (solo lectura, sin tocar ratón) ──────────────────────────
if __name__ == "__main__":
    import sys
    logging.basicConfig(level=logging.INFO)
    mode = sys.argv[1] if len(sys.argv) > 1 else "see"
    if mode == "see":
        for el in see()[:40]:
            print(f"  [{el.x:>5},{el.y:>5}] {el.text}")
    elif mode == "find" and len(sys.argv) > 2:
        el = find(sys.argv[2])
        print(f"  encontrado: {el}" if el else "  no encontrado")
    elif mode == "text":
        print(read_text()[:1000])
    elif mode == "describe":
        print(describe())
