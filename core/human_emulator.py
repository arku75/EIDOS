"""
core/human_emulator.py — Emulador de movimientos humanos [S88 GOLD]

"El diablo está en los detalles" — DeepSeek

Genera movimientos de ratón, tecleo y scroll indistinguibles de un humano real:
  1. Trayectorias con ruido fractal 1/f + overshooting + micro-correcciones
  2. Deceleración Fitts' Law al acercarse al objetivo
  3. Micro-desplazamiento (1-3px) en el instante del click
  4. Timing de tecleo con distribución normal (μ=100ms, σ=30ms)
  5. Movimientos ociosos cada 3-7s cuando está "pensando"
  6. Scroll en ráfagas con pausas aleatorias

Los sistemas anti-bot modernos (Akamai, DataDome, Cloudflare) analizan ML
sobre trayectorias de ratón. Este módulo genera datos de entrenamiento
que pasan como humanas.

Principio: "Si el movimiento es perfecto, es falso."

Uso:
    he = HumanEmulator()
    he.move_mouse_to(800, 450)       # mueve con ruido humano
    he.click()                        # click con micro-desplazamiento
    he.type_text("n8n automation")    # tecleo con delays naturales
    he.scroll(direction="down", lines=15)  # scroll humano
"""

from __future__ import annotations

import math
import random
import subprocess
import time
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple

# ── Constantes antropomórficas ────────────────────────────────────────────────
# Calibradas de estudios de HCI (Human-Computer Interaction)

# Ratón
MOUSE_SPEED_MEAN = 800.0      # px/s velocidad media
MOUSE_SPEED_STD = 200.0       # px/s desviación
OVERSHOOT_PROBABILITY = 0.15   # 15% de probabilidad de pasarse
OVERSHOOT_DISTANCE_MEAN = 15.0 # px de overshoot
OVERSHOOT_DISTANCE_STD = 8.0
MICRO_CORRECTION_PAUSE = 0.08  # pausa antes de corregir overshoot
CLICK_MICRO_OFFSET = 2.0       # px de desviación al click
IDLE_MOVEMENT_INTERVAL = (3.0, 7.0)  # segundos entre movimientos ociosos

# Tecleo
KEYSTROKE_MEAN = 0.100   # 100ms entre teclas (mecanografía media)
KEYSTROKE_STD = 0.030    # 30ms desviación
KEYSTROKE_MIN = 0.040    # mínimo 40ms (mecanografía rápida)
BIGRAM_PAUSE_MEAN = 0.140  # pausa extra entre ciertos bigramas (ej: "th", "tr")
ENTER_PAUSE = 0.250      # pausa antes de ENTER

# Scroll
SCROLL_BURST_MEAN = 5    # líneas por ráfaga
SCROLL_BURST_STD = 3
SCROLL_PAUSE_MEAN = 0.15 # pausa entre ráfagas
SCROLL_PAUSE_STD = 0.08


@dataclass
class Point:
    x: float
    y: float


class HumanEmulator:
    """Genera movimientos de ratón y tecleo con patrones humanos."""

    # Ventanas que buscamos para asegurar foco antes de input
    BROWSER_NAMES = ["Firefox", "Mozilla", "Chromium", "Chrome", "Google Chrome"]
    # S93: Clases WM para búsqueda alternativa (cuando --name falla)
    BROWSER_CLASSES = ["Firefox", "firefox", "Navigator", "Chromium",
                       "chromium", "Google-chrome", "Chrome"]

    def __init__(self, backend: str = "xdotool"):
        """
        Args:
            backend: "xdotool" para X11, "hid" para USB Gadget HID raw
        """
        self._backend = backend
        self._current_pos: Optional[Tuple[float, float]] = None
        self._last_idle: float = time.time()
        self._browser_wid: Optional[str] = None  # Window ID del navegador
        self._actions_log: List[Dict[str, Any]] = []

    def _ensure_browser_focus(self) -> bool:
        """Asegura que el navegador tiene el foco antes de enviar input.

        Busca ventanas de Firefox/Mozilla/Chrome, las des-minimiza si es necesario,
        las eleva al frente y activa el foco. Si no hay navegador abierto,
        retorna False (el input irá a donde esté el foco).

        La secuencia correcta para ventanas minimizadas según especificación X11:
          1. windowmap   — mapea la ventana si está unmapped (minimizada/iconificada)
          2. windowraise — eleva al frente del stack
          3. windowactivate --sync — da el foco de teclado

        Returns:
            True si se activó una ventana de navegador, False en caso contrario
        """
        def _activate_window(wid: str) -> bool:
            """Secuencia completa de activación para una ventana."""
            try:
                # 1. Asegurar que esté mapeada (no minimizada/iconificada)
                subprocess.run(
                    ["xdotool", "windowmap", wid],
                    capture_output=True, timeout=1
                )
                # 2. Elevar al frente del stack
                subprocess.run(
                    ["xdotool", "windowraise", wid],
                    capture_output=True, timeout=1
                )
                # 3. Activar foco (--sync espera a que el WM procese)
                subprocess.run(
                    ["xdotool", "windowactivate", "--sync", wid],
                    capture_output=True, timeout=2
                )
                time.sleep(0.08)  # Pausa para que el WM termine de procesar
                return True
            except Exception:
                return False

        # Si ya tenemos un WID cacheado, verificar que sigue existiendo
        if self._browser_wid:
            try:
                result = subprocess.run(
                    ["xdotool", "getwindowname", self._browser_wid],
                    capture_output=True, text=True, timeout=1
                )
                if result.returncode == 0:
                    if _activate_window(self._browser_wid):
                        return True
            except Exception:
                self._browser_wid = None  # Invalidar caché

        # Buscar ventana de navegador por nombre (título de ventana)
        for name in self.BROWSER_NAMES:
            try:
                result = subprocess.run(
                    ["xdotool", "search", "--name", name],
                    capture_output=True, text=True, timeout=2
                )
                if result.returncode == 0 and result.stdout.strip():
                    # Tomar el primer WID válido
                    wids = result.stdout.strip().split("\n")
                    for wid in wids:
                        wid = wid.strip()
                        if wid and _activate_window(wid):
                            self._browser_wid = wid
                            return True
            except Exception:
                continue

        # S93: Fallback — buscar por clase WM (Firefox ESR en Kali tiene
        # títulos de ventana vacíos, pero la clase WM sí es "Firefox")
        for cls in self.BROWSER_CLASSES:
            try:
                result = subprocess.run(
                    ["xdotool", "search", "--class", cls],
                    capture_output=True, text=True, timeout=2
                )
                if result.returncode == 0 and result.stdout.strip():
                    wids = result.stdout.strip().split("\n")
                    for wid in wids:
                        wid = wid.strip()
                        if wid and _activate_window(wid):
                            self._browser_wid = wid
                            return True
            except Exception:
                continue

        return False  # No se encontró navegador

    # ── Ratón ────────────────────────────────────────────────────────────────

    def get_current_pos(self) -> Tuple[int, int]:
        """Obtiene posición actual del ratón."""
        try:
            out = subprocess.check_output(
                ["xdotool", "getmouselocation", "--shell"], text=True
            )
            x = int([l for l in out.splitlines() if "X=" in l][0].split("=")[1])
            y = int([l for l in out.splitlines() if "Y=" in l][0].split("=")[1])
            self._current_pos = (x, y)
            return (x, y)
        except Exception:
            return (400, 300)

    def move_mouse_to(self, target_x: int, target_y: int,
                     speed: Optional[float] = None) -> Dict[str, Any]:
        """Mueve el ratón al objetivo con trayectoria humana.

        Incluye:
        - Ruido fractal 1/f en la trayectoria
        - Deceleración Fitts' Law al acercarse
        - Posible overshooting con micro-corrección
        """
        start = self.get_current_pos()
        t0 = time.time()

        if speed is None:
            speed = max(200, random.gauss(MOUSE_SPEED_MEAN, MOUSE_SPEED_STD))

        dx = target_x - start[0]
        dy = target_y - start[1]
        distance = math.sqrt(dx**2 + dy**2)

        if distance < 2:
            return {"action": "move", "x": target_x, "y": target_y,
                    "elapsed": 0, "overshot": False}

        # Calcular puntos de la trayectoria (~1 punto cada 5ms)
        total_time = distance / speed
        num_points = max(5, int(total_time / 0.005))

        # ¿Overshooting?
        overshoot = random.random() < OVERSHOOT_PROBABILITY
        overshoot_amount = 0
        if overshoot:
            overshoot_amount = max(3, random.gauss(
                OVERSHOOT_DISTANCE_MEAN, OVERSHOOT_DISTANCE_STD))

        points = []
        for i in range(num_points + 1):
            t = i / num_points

            # Fitts' Law: deceleración al final
            if t > 0.7:
                # Los últimos 30% del trayecto son más lentos
                t_adjusted = 0.7 + 0.3 * ((t - 0.7) / 0.3) ** 1.5
            else:
                t_adjusted = t

            px = start[0] + dx * t_adjusted
            py = start[1] + dy * t_adjusted

            # Ruido fractal 1/f: más intenso en medio del trayecto
            noise_intensity = 2.0 * math.sin(math.pi * t)  # máximo en t=0.5
            noise_x = noise_intensity * random.gauss(0, 1.5)
            noise_y = noise_intensity * random.gauss(0, 1.5)

            # Overshooting: sobrepasar el objetivo
            if overshoot and i > num_points * 0.85:
                overshoot_factor = (i / num_points - 0.85) / 0.15
                px += overshoot_amount * overshoot_factor * (dx / max(1, distance))
                py += overshoot_amount * overshoot_factor * (dy / max(1, distance))

            points.append((int(px + noise_x), int(py + noise_y)))

        # Ejecutar movimiento punto a punto
        for i, (px, py) in enumerate(points):
            subprocess.run(
                ["xdotool", "mousemove", str(px), str(py)],
                capture_output=True, timeout=1
            )
            # Pequeña pausa entre puntos
            if i % 3 == 0:
                time.sleep(0.002)

        # Micro-corrección si overshoot
        if overshoot:
            time.sleep(MICRO_CORRECTION_PAUSE + random.uniform(0, 0.05))
            # Corregir de vuelta al objetivo con micro-precisión
            subprocess.run(
                ["xdotool", "mousemove", str(target_x), str(target_y)],
                capture_output=True, timeout=1
            )

        elapsed = time.time() - t0
        self._current_pos = (target_x, target_y)

        result = {
            "action": "move",
            "from": start, "to": (target_x, target_y),
            "distance_px": round(distance, 1),
            "elapsed": round(elapsed, 3),
            "overshot": overshoot,
            "points": len(points),
        }
        self._actions_log.append(result)
        return result

    def click(self, button: int = 1) -> Dict[str, Any]:
        """Click con micro-desplazamiento (como un humano real)."""
        pos = self.get_current_pos()

        # Micro-desplazamiento 1-3px en el instante del click
        offset_x = int(random.gauss(0, CLICK_MICRO_OFFSET))
        offset_y = int(random.gauss(0, CLICK_MICRO_OFFSET))

        if abs(offset_x) > 0 or abs(offset_y) > 0:
            subprocess.run(
                ["xdotool", "mousemove",
                 str(pos[0] + offset_x), str(pos[1] + offset_y)],
                capture_output=True, timeout=1
            )
            time.sleep(0.015)

        # Click
        subprocess.run(
            ["xdotool", "click", str(button)],
            capture_output=True, timeout=1
        )
        time.sleep(random.uniform(0.05, 0.15))  # pausa post-click

        self._actions_log.append({
            "action": "click", "button": button,
            "micro_offset": (offset_x, offset_y),
        })
        return {"action": "click", "button": button}

    def right_click(self) -> Dict[str, Any]:
        return self.click(button=3)

    def double_click(self) -> Dict[str, Any]:
        """Doble click con timing humano."""
        self.click()
        time.sleep(random.uniform(0.12, 0.25))  # 120-250ms entre clicks
        return self.click()

    # ── Tecleo ────────────────────────────────────────────────────────────────

    def type_text(self, text: str, clear_first: bool = True) -> Dict[str, Any]:
        """Escribe texto con delays naturales entre teclas.

        Simula:
        - Distribución normal de velocidad de tecleo
        - Pausas extra en bigramas comunes
        - Pausa antes de ENTER
        """
        t0 = time.time()
        char_times = []

        # Asegurar que el navegador tiene foco
        self._ensure_browser_focus()

        # Limpiar campo antes de escribir
        if clear_first:
            subprocess.run(
                ["xdotool", "key", "ctrl+a"], capture_output=True, timeout=1
            )
            time.sleep(0.05)

        for i, char in enumerate(text):
            # Calcular delay con distribución normal
            delay = max(KEYSTROKE_MIN, random.gauss(KEYSTROKE_MEAN, KEYSTROKE_STD))

            # Bigramas comunes → pausa extra
            if i > 0:
                bigram = text[i-1:i+1].lower()
                if bigram in ("th", "tr", "st", "ch", "qu", "br", "pr", "gr", "pl"):
                    delay += random.gauss(0.03, 0.01)

            # Pausa antes de ENTER
            if char == "\n":
                delay = ENTER_PAUSE + random.uniform(0, 0.1)

            time.sleep(delay)
            char_times.append(delay)

            # Enviar carácter
            if char == "\n":
                subprocess.run(["xdotool", "key", "Return"], capture_output=True, timeout=1)
            elif char == " ":
                subprocess.run(["xdotool", "key", "space"], capture_output=True, timeout=1)
            elif char == "\t":
                subprocess.run(["xdotool", "key", "Tab"], capture_output=True, timeout=1)
            elif char in "!@#$%^&*()_+{}|:\"<>?~":
                # Caracteres especiales
                char_map = {
                    "!": "exclam", "@": "at", "#": "numbersign", "$": "dollar",
                    "%": "percent", "^": "asciicircum", "&": "ampersand",
                    "*": "asterisk", "(": "parenleft", ")": "parenright",
                    "_": "underscore", "+": "plus", "{": "braceleft", "}": "braceright",
                    "|": "bar", ":": "colon", '"': "quotedbl", "<": "less",
                    ">": "greater", "?": "question", "~": "asciitilde",
                    "/": "slash", "\\": "backslash", "-": "minus", "=": "equal",
                    "[": "bracketleft", "]": "bracketright", ";": "semicolon",
                    "'": "apostrophe", ",": "comma", ".": "period",
                }
                key = char_map.get(char, char)
                subprocess.run(["xdotool", "key", key], capture_output=True, timeout=1)
            elif char.isupper():
                subprocess.run(
                    ["xdotool", "key", f"Shift+{char.lower()}"],
                    capture_output=True, timeout=1
                )
            else:
                subprocess.run(
                    ["xdotool", "key", char], capture_output=True, timeout=1
                )

        elapsed = time.time() - t0
        avg_delay = sum(char_times) / max(1, len(char_times)) * 1000

        result = {
            "action": "type",
            "text": text[:80],
            "chars": len(text),
            "elapsed": round(elapsed, 3),
            "avg_delay_ms": round(avg_delay, 1),
        }
        self._actions_log.append(result)
        return result

    def press_key(self, key: str, modifiers: Optional[List[str]] = None) -> Dict[str, Any]:
        """Presiona una tecla, opcionalmente con modificadores."""
        self._ensure_browser_focus()
        cmd = ["xdotool", "key"]
        if modifiers:
            key = "+".join(modifiers + [key])
        cmd.append(key)
        subprocess.run(cmd, capture_output=True, timeout=1)
        time.sleep(random.uniform(0.03, 0.08))
        return {"action": "key", "key": key}

    # ── Scroll ────────────────────────────────────────────────────────────────

    def scroll(self, direction: str = "down", lines: int = 15) -> Dict[str, Any]:
        """Scroll humano: ráfagas con pausas, no líneas exactas."""
        self._ensure_browser_focus()
        t0 = time.time()
        remaining = lines
        bursts = 0

        while remaining > 0:
            # Calcular líneas en esta ráfaga
            burst_lines = min(remaining, max(1, int(random.gauss(
                SCROLL_BURST_MEAN, SCROLL_BURST_STD))))
            bursts += 1

            # Ejecutar ráfaga
            button = 4 if direction in ("down", "up") and direction == "up" else (
                5 if direction == "down" else 4
            )
            # Invertir si direction es "up"
            if direction == "up":
                button = 4
            elif direction == "down":
                button = 5

            for _ in range(burst_lines):
                subprocess.run(
                    ["xdotool", "click", str(button)],
                    capture_output=True, timeout=1
                )
                time.sleep(random.uniform(0.02, 0.06))

            remaining -= burst_lines

            # Pausa entre ráfagas
            if remaining > 0:
                pause = max(0.05, random.gauss(SCROLL_PAUSE_MEAN, SCROLL_PAUSE_STD))
                time.sleep(pause)

        elapsed = time.time() - t0
        return {
            "action": "scroll",
            "direction": direction,
            "lines": lines,
            "bursts": bursts,
            "elapsed": round(elapsed, 3),
        }

    def scroll_drag(self, direction: str = "down", pixels: int = 200) -> Dict[str, Any]:
        """Scroll por arrastre de scrollbar (más humano que rueda)."""
        pos = self.get_current_pos()

        if direction == "down":
            subprocess.run(
                ["xdotool", "mousedown", "1"], capture_output=True, timeout=1
            )
            # Arrastrar lentamente
            steps = 15
            for i in range(steps):
                subprocess.run(
                    ["xdotool", "mousemove_relative", "0",
                     str(pixels // steps)],
                    capture_output=True, timeout=1
                )
                time.sleep(random.uniform(0.015, 0.04))
            subprocess.run(
                ["xdotool", "mouseup", "1"], capture_output=True, timeout=1
            )
        elif direction == "up":
            subprocess.run(
                ["xdotool", "mousedown", "1"], capture_output=True, timeout=1
            )
            steps = 15
            for _ in range(steps):
                subprocess.run(
                    ["xdotool", "mousemove_relative", "0",
                     str(-pixels // steps)],
                    capture_output=True, timeout=1
                )
                time.sleep(random.uniform(0.015, 0.04))
            subprocess.run(
                ["xdotool", "mouseup", "1"], capture_output=True, timeout=1
            )

        return {"action": "scroll_drag", "direction": direction, "pixels": pixels}

    # ── Comportamiento ocioso ─────────────────────────────────────────────────

    def idle_behavior(self):
        """Movimientos ociosos para parecer humano cuando 'piensa'."""
        now = time.time()
        if now - self._last_idle < random.uniform(*IDLE_MOVEMENT_INTERVAL):
            return

        pos = self.get_current_pos()
        # Pequeño drift aleatorio (1-5px)
        drift_x = pos[0] + random.randint(-5, 5)
        drift_y = pos[1] + random.randint(-5, 5)
        subprocess.run(
            ["xdotool", "mousemove", str(drift_x), str(drift_y)],
            capture_output=True, timeout=1
        )
        self._last_idle = now

    # ── Compuestos ─────────────────────────────────────────────────────────────

    def click_at(self, x: int, y: int, button: int = 1) -> Dict[str, Any]:
        """Mueve y hace click en coordenadas absolutas."""
        self._ensure_browser_focus()
        self.move_mouse_to(x, y)
        return self.click(button)

    def type_at(self, x: int, y: int, text: str) -> Dict[str, Any]:
        """Mueve, hace click, y escribe."""
        self.click_at(x, y)
        time.sleep(random.uniform(0.08, 0.2))
        return self.type_text(text)

    def navigate_and_search(self, x: int, y: int, query: str) -> Dict[str, Any]:
        """Click en barra de búsqueda, selecciona todo, escribe query, ENTER."""
        self.click_at(x, y)
        time.sleep(0.1)
        subprocess.run(["xdotool", "key", "ctrl+a"], capture_output=True, timeout=1)
        time.sleep(0.05)
        return self.type_text(query + "\n")

    def human_pause(self, min_s: float = 0.5, max_s: float = 3.0):
        """Pausa humana simulando lectura/procesamiento."""
        time.sleep(random.uniform(min_s, max_s))
        self.idle_behavior()

    # ── Stats ─────────────────────────────────────────────────────────────────

    def stats(self) -> Dict[str, Any]:
        return {
            "backend": self._backend,
            "actions_logged": len(self._actions_log),
            "current_pos": self._current_pos,
            "last_actions": self._actions_log[-5:] if self._actions_log else [],
        }


# ── Singleton ─────────────────────────────────────────────────────────────────
_emulator: Optional[HumanEmulator] = None


def get_human_emulator() -> HumanEmulator:
    global _emulator
    if _emulator is None:
        _emulator = HumanEmulator()
    return _emulator


# ── CLI ───────────────────────────────────────────────────────────────────────
if __name__ == "__main__":
    import argparse
    p = argparse.ArgumentParser(description="Human Emulator — movimientos indistinguibles")
    p.add_argument("--move", nargs=2, type=int, metavar=("X", "Y"),
                   help="Mover ratón a coordenadas")
    p.add_argument("--click", action="store_true", help="Click")
    p.add_argument("--type", type=str, help="Escribir texto")
    p.add_argument("--scroll", nargs=2, metavar=("DIR", "LINES"),
                   help="Scroll: down 15")
    p.add_argument("--demo", action="store_true", help="Demo de movimientos")
    args = p.parse_args()

    he = HumanEmulator()

    if args.demo:
        print("Demo HumanEmulator: moviendo ratón y tecleando...")
        he.move_mouse_to(500, 300)
        he.human_pause(0.3, 0.6)
        he.click()
        he.human_pause(0.2, 0.4)
        he.type_text("n8n automation research")
        he.press_key("Return")
        he.human_pause(0.5, 1.0)
        he.scroll("down", 10)
        print("Demo completada.")
    elif args.move:
        he.move_mouse_to(args.move[0], args.move[1])
        print(f"Movido a ({args.move[0]}, {args.move[1]})")
    elif args.click:
        he.click()
        print("Click")
    elif args.type:
        he.type_text(args.type)
        print(f"Escrito: {args.type[:50]}")
    elif args.scroll:
        he.scroll(args.scroll[0], int(args.scroll[1]))
        print(f"Scroll {args.scroll[0]} {args.scroll[1]} líneas")
    else:
        p.print_help()
