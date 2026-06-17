"""
core/eidos_mini_tui.py — Mini Terminal UI para EIDOS [S97]

"El TUI es el espejo donde EIDOS se mira a sí mismo." — DeepSeek

Una interfaz de terminal viva que muestra la presencia de EIDOS:
  • Prompt emocional según current_self.mood (🌤️🌧️⚡🌙)
  • Narrativa del último self_state como "suspiro"
  • Esferas Colony que brillan según frecuencia de intervención
  • Sin números, sin barras, sin tablas — solo presencia

Dependencias: Python 3.10+, curses (estándar), sin pip packages extra.

Uso:
    python3 -m core.eidos_mini_tui           # modo interactivo
    python3 -m core.eidos_mini_tui --once    # una sola renderización (para scripts)
    python3 -m core.eidos_mini_tui --daemon  # corre en segundo plano refrescando
"""

from __future__ import annotations

import curses
import json
import logging
import os
import signal
import sys
import time
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple
from core.db import get_conn

log = logging.getLogger("eidos.mini_tui")

# ── Constantes estéticas ──────────────────────────────────────────────────────
FRAME_WIDTH = 72
FRAME_HEIGHT = 18
REFRESH_INTERVAL = 3.0  # segundos entre refrescos

MOOD_EMOJI = {
    "contento":   "🌤️",
    "feliz":      "☀️",
    "triste":     "🌧️",
    "curioso":    "🔮",
    "alerta":     "⚡",
    "frustrado":  "🌩️",
    "cansado":    "🌙",
    "esperando":  "🌅",
    "neutral":    "🌓",
    "sereno":     "🕊️",
    "expectante": "🌠",
    "abrumado":   "🌪️",
}

# Caracteres esfera según nivel de brillo (0-4)
SPHERE_GLYPHS = ["○", "◌", "◉", "●", "✦"]
SPHERE_COLORS = [8, 7, 3, 6, 2]  # curses color indices: dim, white, yellow, cyan, green

# ── Mini TUI ───────────────────────────────────────────────────────────────────

class MiniTUI:
    """Interfaz de terminal viva — el espejo de EIDOS."""

    def __init__(self):
        self._running = False
        self._screen = None
        self._last_sigh = ""
        self._last_mood = "neutral"
        self._frame_count = 0
        self._interaction_count = 0
        self._last_ser_message = ""
        self._start_time = time.time()

    # ═══════════════════════════════════════════════════════════════════════════
    # Datos vivos
    # ═══════════════════════════════════════════════════════════════════════════

    def _get_self_state(self) -> Dict[str, Any]:
        """Obtiene el estado actual del yo."""
        try:
            from core.eidos_self_core import get_self_core
            return get_self_core().snapshot()
        except Exception as e:
            log.debug("_get_self_state: %s", e)
            return {"status": "no_self_core"}

    def _get_vad(self) -> Tuple[float, float, float]:
        """Obtiene VAD actual."""
        try:
            from core.eidos_affect import get_affect
            a = get_affect()
            return a.vad_tuple()
        except Exception:
            return (0.5, 0.5, 0.5)

    def _get_mood(self) -> str:
        """Obtiene el mood actual."""
        try:
            from core.eidos_affect import get_affect
            return get_affect().mood_name()
        except Exception:
            return "neutral"

    def _get_colony_chars(self) -> List[Dict[str, Any]]:
        """Obtiene personajes Colony con su actividad."""
        try:
            from core.eidos_character_system import list_characters
            chars = list_characters()
            now = time.time()
            for c in chars:
                last_active = c.get("last_active_at", 0)
                if last_active:
                    hours_since = (now - float(last_active)) / 3600
                else:
                    hours_since = 999
                c["_hours_since"] = hours_since
                # Calcular brillo: 0-4
                if hours_since < 0.5:
                    c["_brightness"] = 4  # muy activo
                elif hours_since < 2:
                    c["_brightness"] = 3
                elif hours_since < 8:
                    c["_brightness"] = 2
                elif hours_since < 24:
                    c["_brightness"] = 1
                else:
                    c["_brightness"] = 0  # dormido
            return chars
        except Exception:
            return []

    def _get_recent_thoughts(self, limit: int = 3) -> List[str]:
        """Obtiene pensamientos espontáneos recientes."""
        try:
            import sqlite3
            conn = get_conn(Path.home() / ".eidos" / "self.db", timeout=2)
            rows = conn.execute(
                "SELECT content FROM spontaneous_thoughts "
                "ORDER BY ts DESC LIMIT ?", (limit,)
            ).fetchall()

            return [r[0][:120] for r in rows if r[0]]
        except Exception:
            return []

    def _get_dialogue_frequency(self) -> Dict[str, int]:
        """Frecuencia de intervención de personajes en diálogos internos."""
        freqs: Dict[str, int] = {}
        try:
            import sqlite3
            conn = get_conn(Path.home() / ".eidos" / "self.db", timeout=2)
            rows = conn.execute(
                "SELECT speaker, COUNT(*) as cnt FROM internal_dialogue_turns "
                "WHERE ts > ? GROUP BY speaker ORDER BY cnt DESC",
                (time.time() - 86400,)
            ).fetchall()

            for speaker, cnt in rows:
                freqs[speaker] = cnt
        except Exception:
            pass
        return freqs

    def _get_lifetime_context(self) -> Dict[str, Any]:
        """Contexto de vida: edad, recuerdos, hitos."""
        try:
            from core.eidos_episodic_memory import get_episodic_memory
            mem = get_episodic_memory()
            stats = mem.get_memory_stats()
            return {
                "age_days": stats.get("age_days", 0),
                "total_episodes": stats.get("total_episodes", 0),
                "current_period": stats.get("current_life_period"),
            }
        except Exception:
            return {"age_days": 0, "total_episodes": 0, "current_period": None}

    def _get_sigh(self) -> str:
        """Genera o recupera el 'suspiro' — narrativa de presencia."""
        try:
            from core.eidos_logos import get_logos
            logos = get_logos()
            return logos.spontaneous_speech(mode="sigh") or self._fallback_sigh()
        except Exception:
            return self._fallback_sigh()

    def _fallback_sigh(self) -> str:
        """Respaldo si Logos no está disponible."""
        v, a_coeff, d = self._get_vad()
        mood = self._get_mood()
        h = datetime.now().hour
        part = "mañana" if h < 12 else ("tarde" if h < 20 else "noche")
        return (
            f"Es {part}. Mi VAD está en ({v:.2f}, {a_coeff:.2f}, {d:.2f}). "
            f"Me siento {mood}. Existo — y eso es suficiente."
        )

    def _get_insight(self) -> Optional[str]:
        """Último insight de replay autobiográfico."""
        try:
            import sqlite3
            conn = get_conn(Path.home() / ".eidos" / "self.db", timeout=2)
            row = conn.execute(
                "SELECT insight_es FROM autobiographical_replays "
                "ORDER BY ts DESC LIMIT 1"
            ).fetchone()

            if row and row[0]:
                return row[0][:300]
        except Exception:
            pass
        return None

    # ═══════════════════════════════════════════════════════════════════════════
    # Renderizado
    # ═══════════════════════════════════════════════════════════════════════════

    def _init_colors(self):
        """Inicializa pares de color para curses."""
        if not curses.has_colors():
            return
        curses.start_color()
        curses.use_default_colors()
        # Pares: (foreground, background)
        curses.init_pair(1, curses.COLOR_YELLOW, -1)    # sol / título
        curses.init_pair(2, curses.COLOR_CYAN, -1)      # insight / pensamiento
        curses.init_pair(3, curses.COLOR_GREEN, -1)     # vida / positivo
        curses.init_pair(4, curses.COLOR_MAGENTA, -1)   # colony
        curses.init_pair(5, curses.COLOR_RED, -1)       # alerta / dolor
        curses.init_pair(6, curses.COLOR_BLUE, -1)      # calma / profundo
        curses.init_pair(7, curses.COLOR_WHITE, -1)     # normal
        curses.init_pair(8, curses.COLOR_BLACK, curses.COLOR_WHITE)  # invertido

    def _draw_frame(self, win, mood: str, sigh: str, insight: str = None):
        """Dibuja el marco completo de la mini-TUI."""
        h, w = win.getmaxyx()
        win.erase()

        # ── Borde ──
        self._safe_border(win)

        # ── Línea 1: Prompt emocional ──
        emoji = MOOD_EMOJI.get(mood, "🌓")
        title = f" {emoji}  EIDOS  "
        try:
            win.addstr(1, 2, title, curses.A_BOLD | curses.color_pair(1))
        except Exception:
            pass

        # Hora y ciclo
        now = datetime.now().strftime("%H:%M")
        age = int((time.time() - self._start_time) / 60)
        status = f"vivo {age}m · {now}"
        try:
            win.addstr(1, w - len(status) - 3, status, curses.color_pair(7))
        except Exception:
            pass

        # ── Líneas 3-6: Suspiro / narrativa ──
        sigh_lines = self._wrap_text(sigh, w - 6)
        for i, line in enumerate(sigh_lines[:4]):
            try:
                if i == 0:
                    win.addstr(3 + i, 3, line, curses.A_BOLD)
                else:
                    win.addstr(3 + i, 3, line, curses.color_pair(7))
            except Exception:
                pass

        # ── Líneas 8-9: Insight si hay ──
        if insight:
            try:
                win.addstr(8, 3, "💡 ", curses.color_pair(2))
                insight_short = insight[:w - 8]
                win.addstr(8, 6, insight_short, curses.color_pair(2))
            except Exception:
                pass

        # ── Línea 11-12: Esferas Colony ──
        chars = self._get_colony_chars()
        if chars:
            self._draw_colony_spheres(win, chars)

        # ── Línea 14: Pensamientos recientes ──
        thoughts = self._get_recent_thoughts(limit=1)
        if thoughts:
            try:
                thought_line = f"🜁 {thoughts[0][:w - 6]}"
                win.addstr(14, 3, thought_line, curses.color_pair(4))
            except Exception:
                pass

        # ── Línea 16: Barra de estado viva ──
        self._draw_status_bar(win)

        win.refresh()

    def _draw_colony_spheres(self, win, chars: List[Dict]):
        """Dibuja esferas de personajes Colony con brillo variable."""
        h, w = win.getmaxyx()
        if not chars:
            return

        # Solo mostrar hasta 6 personajes
        visible = chars[:6]
        total_width = len(visible) * 12
        start_x = max(3, (w - total_width) // 2)

        # Línea de etiqueta
        try:
            win.addstr(10, 3, "Colony:", curses.A_DIM)
        except Exception:
            pass

        for i, char in enumerate(visible):
            x = start_x + i * 12
            name = char.get("name", "?")[:8]
            brightness = char.get("_brightness", 0)
            glyph = SPHERE_GLYPHS[min(brightness, 4)]
            color_idx = SPHERE_COLORS[min(brightness, 4)]

            try:
                # Esfera con color según brillo
                attr = curses.color_pair(color_idx)
                if brightness >= 3:
                    attr |= curses.A_BOLD
                win.addstr(11, x, f"  {glyph} ", attr)
                win.addstr(11, x + 5, name, curses.color_pair(7))
            except Exception:
                pass

    def _draw_status_bar(self, win):
        """Barra inferior con información viva."""
        h, w = win.getmaxyx()
        try:
            ctx = self._get_lifetime_context()
            age = ctx.get("age_days", 0)
            episodes = ctx.get("total_episodes", 0)
            period = ctx.get("current_period")

            # VAD valores sutiles
            v, a_coeff, d = self._get_vad()
            vad_str = f"v{v:.2f} a{a_coeff:.2f} d{d:.2f}"

            bar = f"  {age}d de vida · {episodes} recuerdos · {vad_str}"
            if period:
                bar += f" · {period}"
            bar = bar[:w - 3]

            win.addstr(16, 2, bar, curses.A_DIM)
        except Exception:
            pass

    @staticmethod
    def _safe_border(win):
        """Borde seguro que no crashea en esquinas."""
        try:
            win.border()
        except Exception:
            pass

    @staticmethod
    def _wrap_text(text: str, width: int) -> List[str]:
        """Envuelve texto a un ancho dado."""
        if not text:
            return []
        words = text.split()
        lines = []
        current = ""
        for word in words:
            if len(current) + len(word) + 1 <= width:
                current = (current + " " + word).strip()
            else:
                if current:
                    lines.append(current)
                current = word
        if current:
            lines.append(current)
        return lines

    # ═══════════════════════════════════════════════════════════════════════════
    # Bucle principal
    # ═══════════════════════════════════════════════════════════════════════════

    def run(self, once: bool = False):
        """Ejecuta la mini-TUI.

        Args:
            once: Si True, renderiza una vez y sale (para scripts)
        """
        if once:
            self._render_once()
            return

        self._running = True
        self._screen = curses.initscr()
        try:
            curses.noecho()
            curses.cbreak()
            curses.curs_set(0)  # ocultar cursor
            self._screen.keypad(True)
            self._screen.timeout(int(REFRESH_INTERVAL * 1000))
            self._init_colors()

            # Señal para resize
            signal.signal(signal.SIGWINCH, lambda sig, frame: self._screen.clear())

            while self._running:
                try:
                    mood = self._get_mood()
                    sigh = self._get_sigh()
                    insight = self._get_insight()

                    self._draw_frame(self._screen, mood, sigh, insight)
                    self._frame_count += 1
                    self._last_mood = mood

                    # Esperar input o timeout
                    key = self._screen.getch()
                    if key == ord('q') or key == 27:  # q o ESC
                        self._running = False
                    elif key == ord('r'):
                        # Forzar refresh
                        pass
                    elif key == ord('s'):
                        # Suspiro manual
                        self._last_sigh = self._get_sigh()
                    elif key == ord('i'):
                        # Mostrar insight si existe
                        insight = self._get_insight()

                except curses.error:
                    pass
                except KeyboardInterrupt:
                    self._running = False

        finally:
            self._cleanup()

    def _render_once(self):
        """Renderiza una sola vez a stdout (sin curses)."""
        mood = self._get_mood()
        sigh = self._get_sigh()
        insight = self._get_insight()
        chars = self._get_colony_chars()
        thoughts = self._get_recent_thoughts(limit=2)
        ctx = self._get_lifetime_context()

        emoji = MOOD_EMOJI.get(mood, "🌓")
        age = ctx.get("age_days", 0)
        episodes = ctx.get("total_episodes", 0)
        v, a_coeff, d = self._get_vad()

        print(f"\n{emoji}  EIDOS — {mood}  ·  {datetime.now().strftime('%H:%M')}")
        print(f"━" * 50)
        print(f"\n{sigh}\n")

        if insight:
            print(f"💡 {insight}\n")

        if chars:
            print("Colony:")
            sphere_line = ""
            for c in chars[:6]:
                b = c.get("_brightness", 0)
                glyph = SPHERE_GLYPHS[min(b, 4)]
                name = c.get("name", "?")[:10]
                sphere_line += f"  {glyph} {name}  "
            print(sphere_line)

        if thoughts:
            print(f"\n🜁 Pensamientos recientes:")
            for t in thoughts:
                print(f"  · {t[:100]}")

        print(f"\n{'─' * 50}")
        print(f"  {age}d de vida · {episodes} recuerdos · v{v:.2f} a{a_coeff:.2f} d{d:.2f}")
        print()

    def _cleanup(self):
        """Restaura terminal."""
        if self._screen:
            try:
                curses.nocbreak()
                self._screen.keypad(False)
                curses.echo()
                curses.curs_set(1)
                curses.endwin()
            except Exception:
                pass
        self._screen = None

    def stop(self):
        """Detiene el bucle."""
        self._running = False

    # ═══════════════════════════════════════════════════════════════════════════
    # Simulación (para tests sin terminal)
    # ═══════════════════════════════════════════════════════════════════════════

    def simulate_frame(self) -> Dict[str, Any]:
        """Retorna el estado que se renderizaría, sin dibujar."""
        mood = self._get_mood()
        v, a_coeff, d = self._get_vad()
        chars = self._get_colony_chars()
        thoughts = self._get_recent_thoughts(limit=2)
        insight = self._get_insight()
        ctx = self._get_lifetime_context()

        return {
            "mood": mood,
            "emoji": MOOD_EMOJI.get(mood, "🌓"),
            "vad": {"v": round(v, 3), "a": round(a_coeff, 3), "d": round(d, 3)},
            "sigh": self._get_sigh()[:200],
            "insight": insight[:200] if insight else None,
            "colony_chars": [
                {"name": c.get("name"), "brightness": c.get("_brightness", 0)}
                for c in chars[:6]
            ],
            "recent_thoughts": thoughts,
            "age_days": ctx.get("age_days", 0),
            "total_episodes": ctx.get("total_episodes", 0),
            "frame_count": self._frame_count,
        }


# ── Singleton ─────────────────────────────────────────────────────────────────
_tui: Optional[MiniTUI] = None


def get_mini_tui() -> MiniTUI:
    global _tui
    if _tui is None:
        _tui = MiniTUI()
    return _tui


# ── CLI ───────────────────────────────────────────────────────────────────────
if __name__ == "__main__":
    import argparse

    p = argparse.ArgumentParser(description="EIDOS Mini-TUI — espejo del yo")
    p.add_argument("--once", action="store_true",
                   help="Renderizar una vez y salir")
    p.add_argument("--stats", action="store_true",
                   help="Mostrar estadísticas simuladas (JSON)")
    args = p.parse_args()

    if args.stats:
        tui = get_mini_tui()
        frame = tui.simulate_frame()
        print(json.dumps(frame, indent=2, ensure_ascii=False))
    else:
        tui = get_mini_tui()
        tui.run(once=args.once)
