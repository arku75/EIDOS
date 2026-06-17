"""
EIDOS Screen Layout Parser — convierte lista plana de OCR elements
(text + bounding boxes) en estructura jerárquica de UI: ventanas,
menubars, toolbars, content, statusbars, dialogs.

Sin ML. Solo heurísticas espaciales sobre coordenadas y dimensiones.
"""
from __future__ import annotations

import logging
import re
import subprocess
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple

log = logging.getLogger("eidos.layout")


@dataclass
class UIElement:
    """Elemento UI con tipo inferido."""
    text: str
    x: int
    y: int
    width: int
    height: int
    type: str = "text"           # text/button/title/menu/input/icon
    parent: Optional[str] = None  # window title o "dialog:..." o "menubar"
    confidence: float = 0.5

    @property
    def center(self) -> Tuple[int, int]:
        return (self.x + self.width // 2, self.y + self.height // 2)

    @property
    def bbox(self) -> Tuple[int, int, int, int]:
        return (self.x, self.y, self.x + self.width, self.y + self.height)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "text": self.text, "x": self.x, "y": self.y,
            "width": self.width, "height": self.height,
            "type": self.type, "parent": self.parent,
            "confidence": self.confidence,
            "center_x": self.center[0], "center_y": self.center[1],
        }


def _normalize_element(e: Dict[str, Any]) -> UIElement:
    """Convierte un dict OCR genérico en UIElement, tolerante a diferentes claves."""
    text = str(e.get("text", "")).strip()
    x = int(e.get("x", e.get("left", 0)))
    y = int(e.get("y", e.get("top", 0)))
    w = int(e.get("width", e.get("w", 50)))
    h = int(e.get("height", e.get("h", 20)))
    type_ = e.get("type", "text")
    conf = float(e.get("confidence", e.get("conf", 0.5)))
    return UIElement(text=text, x=x, y=y, width=w, height=h,
                     type=type_, confidence=conf)


def _get_screen_resolution() -> Tuple[int, int]:
    """Resolución actual de la pantalla principal."""
    try:
        out = subprocess.check_output(
            ["xrandr", "--current"], text=True, timeout=2,
            stderr=subprocess.DEVNULL,
        )
        for line in out.splitlines():
            m = re.search(r"\b(\d{3,5})x(\d{3,5})\b.*\*", line)
            if m:
                return int(m.group(1)), int(m.group(2))
    except Exception:
        pass
    return 1920, 1080


def _get_window_geometries() -> List[Dict[str, Any]]:
    """Lista ventanas con geometría real desde wmctrl -lG."""
    wins = []
    try:
        out = subprocess.check_output(
            ["wmctrl", "-lG"], text=True, timeout=2,
            stderr=subprocess.DEVNULL,
        )
        for line in out.splitlines():
            parts = line.split(None, 7)
            if len(parts) < 8:
                continue
            wid, desktop, x, y, w, h, host, title = parts
            try:
                wins.append({
                    "id": wid, "x": int(x), "y": int(y),
                    "width": int(w), "height": int(h),
                    "title": title.strip(),
                })
            except ValueError:
                continue
    except Exception as e:
        log.debug("wmctrl -lG falló: %s", e)
    return wins


def _classify_element(elem: UIElement, window: Dict[str, Any],
                      screen_h: int) -> str:
    """Asigna tipo (button/menu/title/statusbar/content) por heurísticas."""
    text = elem.text
    if not text:
        return "icon"

    # Relativos a la ventana
    win_top = window.get("y", 0)
    win_bottom = win_top + window.get("height", 1080)
    win_height = max(1, window.get("height", 1080))
    rel_y = (elem.y - win_top) / win_height  # 0=top, 1=bottom

    # Heurísticas por posición + forma
    # 1) Title bar: muy arriba en la ventana, texto que coincide con título
    if rel_y < 0.05 and elem.height <= 30:
        if text.lower() in window.get("title", "").lower() or \
           window.get("title", "").lower().endswith(text.lower()):
            return "title"

    # 2) Menubar: top 5-12% altura, texto corto típico de menú
    menu_words = {"file", "edit", "view", "tools", "help", "window",
                  "archivo", "editar", "ver", "herramientas", "ayuda",
                  "ventana", "datei", "bearbeiten"}
    if 0.02 <= rel_y <= 0.10 and len(text) <= 18 and \
       text.lower().rstrip(":").rstrip("&") in menu_words:
        return "menu"

    # 3) Status bar: parte inferior, texto único largo o info
    if rel_y >= 0.93:
        return "statusbar"

    # 4) Botón: texto corto (2-30 chars), altura típica botón (15-50)
    if 2 <= len(text) <= 30 and 12 <= elem.height <= 60:
        # Palabras típicas de botón
        button_words = {"aceptar", "cancelar", "cerrar", "guardar", "ok",
                        "yes", "no", "sí", "siguiente", "anterior", "next",
                        "back", "enviar", "send", "abrir", "open", "salir",
                        "exit", "apply", "aplicar", "delete", "eliminar",
                        "borrar", "save", "close"}
        if text.lower().strip(".:!?") in button_words:
            return "button"
        # Botón heurístico: texto que termina con paréntesis (atajo de teclado)
        if re.search(r"\([A-Z]\)$", text):
            return "button"

    # 5) Input: texto corto + ancho grande
    if elem.width > 200 and elem.height < 40 and len(text) < 100:
        # Posible campo de texto con placeholder
        if any(p in text.lower() for p in ["buscar", "search", "...", "type"]):
            return "input"

    return "text"


def _classify_window(window: Dict[str, Any], screen_w: int,
                     screen_h: int) -> str:
    """Distingue dialog vs ventana normal."""
    w = window.get("width", 0)
    h = window.get("height", 0)
    if w < screen_w * 0.5 and h < screen_h * 0.5:
        title = window.get("title", "").lower()
        if any(k in title for k in ["confirm", "error", "warning",
                                     "advertencia", "alerta",
                                     "save as", "guardar como"]):
            return "dialog"
        if w < screen_w * 0.35 and h < screen_h * 0.35:
            return "dialog"
    return "window"


def _element_in_window(elem: UIElement, win: Dict[str, Any]) -> bool:
    """¿El elemento cae dentro de la ventana?"""
    cx, cy = elem.center
    return (win["x"] <= cx <= win["x"] + win["width"] and
            win["y"] <= cy <= win["y"] + win["height"])


def parse_layout(elements: List[Dict[str, Any]],
                 windows: Optional[List[Dict[str, Any]]] = None,
                 screen_size: Optional[Tuple[int, int]] = None) -> Dict[str, Any]:
    """Construye estructura jerárquica de la pantalla.

    Args:
        elements: lista de OCR elements (text+bbox), tal como devuelve
                  screen_scanner.extract_screen_elements.
        windows:  lista de ventanas con geometría (si None, se obtiene de wmctrl).
        screen_size: (w, h) opcional; si None, se detecta con xrandr.

    Returns:
        dict con `windows`, `dialogs`, `screen_size`, `total_elements`.
    """
    if windows is None:
        windows = _get_window_geometries()
    if screen_size is None:
        screen_size = _get_screen_resolution()
    screen_w, screen_h = screen_size

    ui_elements = [_normalize_element(e) for e in elements if e]

    parsed_wins: List[Dict[str, Any]] = []
    parsed_dialogs: List[Dict[str, Any]] = []

    used_ids = set()
    for win in windows:
        win_type = _classify_window(win, screen_w, screen_h)
        children_by_type: Dict[str, List[Dict[str, Any]]] = {}
        for i, elem in enumerate(ui_elements):
            if i in used_ids:
                continue
            if not _element_in_window(elem, win):
                continue
            etype = _classify_element(elem, win, screen_h)
            elem.type = etype
            elem.parent = win["title"]
            children_by_type.setdefault(etype, []).append(elem.to_dict())
            used_ids.add(i)

        node = {
            "title": win["title"],
            "id": win["id"],
            "bbox": [win["x"], win["y"], win["width"], win["height"]],
            "menubar": children_by_type.get("menu", []),
            "buttons": children_by_type.get("button", []),
            "titles": children_by_type.get("title", []),
            "inputs": children_by_type.get("input", []),
            "statusbar": children_by_type.get("statusbar", []),
            "content": children_by_type.get("text", []),
            "icons": children_by_type.get("icon", []),
        }
        if win_type == "dialog":
            parsed_dialogs.append(node)
        else:
            parsed_wins.append(node)

    # Elementos sueltos (no caen en ninguna ventana detectada)
    orphans = []
    for i, elem in enumerate(ui_elements):
        if i not in used_ids:
            orphans.append(elem.to_dict())

    return {
        "screen_size": [screen_w, screen_h],
        "total_elements": len(ui_elements),
        "windows": parsed_wins,
        "dialogs": parsed_dialogs,
        "orphans": orphans,
    }


def find_clickable_target(layout: Dict[str, Any],
                          semantic_hint: str) -> Optional[Dict[str, Any]]:
    """Busca el elemento clickable más probable dado un hint semántico.

    Args:
        layout: salida de parse_layout()
        semantic_hint: ej. "guardar", "cerrar dialog", "aceptar"

    Returns:
        UIElement.to_dict() del mejor match, o None.
    """
    hint = semantic_hint.lower()
    # Sinónimos básicos
    synonyms = {
        "guardar": ["guardar", "save", "salvar"],
        "cerrar": ["cerrar", "close", "x", "salir", "exit"],
        "aceptar": ["aceptar", "ok", "yes", "sí", "confirmar", "apply"],
        "cancelar": ["cancelar", "cancel", "no", "abort"],
        "siguiente": ["siguiente", "next", "continuar", "continue"],
        "anterior": ["anterior", "back", "atrás", "previous"],
        "enviar": ["enviar", "send", "submit"],
        "abrir": ["abrir", "open"],
    }
    needles = [hint]
    for key, syns in synonyms.items():
        if hint in syns:
            needles = syns
            break

    best = None
    best_score = 0.0
    pools = []
    # Prioridad: dialogs primero (suelen requerir respuesta), luego buttons
    for dlg in layout.get("dialogs", []):
        pools.append(("dialog_button", dlg.get("buttons", [])))
        pools.append(("dialog_content", dlg.get("content", [])))
    for win in layout.get("windows", []):
        pools.append(("button", win.get("buttons", [])))
        pools.append(("menu", win.get("menubar", [])))
    pools.append(("orphan", layout.get("orphans", [])))

    for pool_type, items in pools:
        boost = {"dialog_button": 2.0, "dialog_content": 1.5,
                 "button": 1.3, "menu": 1.0, "orphan": 0.7}.get(pool_type, 0.5)
        for item in items:
            text = str(item.get("text", "")).lower().strip(".:!?()")
            if not text:
                continue
            score = 0.0
            for needle in needles:
                if needle == text:
                    score = max(score, 1.0)
                elif needle in text or text in needle:
                    score = max(score, 0.7)
            score *= boost
            if score > best_score:
                best_score = score
                best = dict(item)
                best["match_score"] = score
                best["pool"] = pool_type

    return best


if __name__ == "__main__":
    # Self-test sintético
    fake_elements = [
        {"text": "Archivo", "x": 10, "y": 30, "width": 60, "height": 20},
        {"text": "Editar", "x": 80, "y": 30, "width": 50, "height": 20},
        {"text": "Documento sin título — gedit",
         "x": 0, "y": 0, "width": 800, "height": 25},
        {"text": "Hola mundo", "x": 100, "y": 300, "width": 200, "height": 30},
        {"text": "Aceptar", "x": 350, "y": 600, "width": 80, "height": 30},
        {"text": "Cancelar", "x": 450, "y": 600, "width": 80, "height": 30},
    ]
    fake_windows = [
        {"id": "0x1", "x": 0, "y": 0, "width": 800, "height": 700,
         "title": "Documento sin título — gedit"},
    ]
    layout = parse_layout(fake_elements, windows=fake_windows,
                          screen_size=(1920, 1080))
    print(f"Windows: {len(layout['windows'])}, "
          f"Dialogs: {len(layout['dialogs'])}, "
          f"Orphans: {len(layout['orphans'])}")
    if layout["windows"]:
        w = layout["windows"][0]
        print(f"  Menubar: {[m['text'] for m in w['menubar']]}")
        print(f"  Buttons: {[b['text'] for b in w['buttons']]}")
        print(f"  Titles: {[t['text'] for t in w['titles']]}")
    target = find_clickable_target(layout, "aceptar")
    print(f"Target 'aceptar': {target}")
