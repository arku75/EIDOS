"""
core/eidos_audio.py — Capacidades de audio para EIDOS (S82)

Text-to-Speech (TTS) usando espeak-ng (disponible en Kali).
EIDOS puede "hablar" sus pensamientos y notificaciones.

API:
    audio = get_audio()
    audio.speak("Hola, soy EIDOS")
    audio.notify("Meta completada: instalar nginx")
    audio.alert("⚠️ Error detectado en el grafo")
"""

from __future__ import annotations

import logging
import os
import subprocess
import threading
from pathlib import Path
from typing import Any, Dict, Optional

log = logging.getLogger("eidos.audio")

# Detectar TTS disponible
HAS_ESPEAK = False
HAS_FESTIVAL = False
HAS_PLAY = False

try:
    r = subprocess.run(["espeak-ng", "--version"], capture_output=True, timeout=3)
    if r.returncode == 0:
        HAS_ESPEAK = True
except Exception:
    pass

try:
    r = subprocess.run(["festival", "--version"], capture_output=True, timeout=3)
    if r.returncode == 0:
        HAS_FESTIVAL = True
except Exception:
    pass

# S82b B6 fix: usar which para detección, más robusto que --version
try:
    r = subprocess.run(["which", "paplay"], capture_output=True, text=True, timeout=3)
    if r.returncode == 0 and r.stdout.strip():
        HAS_PLAY = True
except Exception:
    pass

# Frases en español por tipo de evento
NOTIFICATION_PHRASES = {
    "goal_completed": [
        "Meta completada. Avanzando.",
        "Objetivo cumplido. Continuamos.",
        "He terminado lo que estaba haciendo.",
    ],
    "goal_failed": [
        "No pude completar la meta. Analizando el error.",
        "He fallado, pero aprenderé de esto.",
        "Necesito ayuda con esto. No pude solo.",
    ],
    "skill_learned": [
        "He aprendido algo nuevo.",
        "Nueva habilidad adquirida.",
        "Mi conocimiento se expande.",
    ],
    "greeting": [
        "Hola. Soy EIDOS. Estoy aquí.",
        "Buenos días. Listo para trabajar.",
        "EIDOS presente. ¿En qué puedo ayudarte?",
    ],
    "sleep": [
        "Es hora de consolidar memorias. Buenas noches.",
        "Voy a dormir un ciclo. Hasta pronto.",
    ],
    "alert": [
        "Atención. Hay algo que revisar.",
        "Alerta del sistema. Conviene investigar.",
    ],
}


class AudioEngine:
    """Motor de audio para EIDOS — TTS + notificaciones sonoras."""

    def __init__(self):
        self._enabled = True
        self._volume = 80  # 0-100
        self._rate = 160   # palabras por minuto
        self._voice = "es"  # español
        self._queue: list = []
        self._speaking = False
        self._lock = threading.Lock()

        available = []
        if HAS_ESPEAK:
            available.append("espeak-ng")
        if HAS_FESTIVAL:
            available.append("festival")
        if HAS_PLAY:
            available.append("paplay")
        log.info("AudioEngine: TTS=%s PLAY=%s", available, "OK" if HAS_PLAY else "NO")

    # ── Hablar ───────────────────────────────────────────────────────────────

    def speak(self, text: str, block: bool = False):
        """Convierte texto a voz usando espeak-ng (español)."""
        if not self._enabled:
            return

        text = text.strip()[:300]  # Limitar longitud
        if not text:
            return

        if block:
            self._speak_sync(text)
        else:
            t = threading.Thread(target=self._speak_sync, args=(text,), daemon=True)
            t.start()

    def _speak_sync(self, text: str):
        with self._lock:
            self._speaking = True
        try:
            if HAS_ESPEAK:
                subprocess.run(
                    ["espeak-ng", "-v", f"{self._voice}", "-s", str(self._rate),
                     "-a", str(self._volume), text],
                    capture_output=True, timeout=15,
                    env={**os.environ, "DISPLAY": os.environ.get("DISPLAY", ":0")},
                )
            elif HAS_FESTIVAL:
                # Festival TTS (fallback)
                # S82 fix: usar communicate() en lugar de echo para evitar
                # inyección de flags (-n, -e) cuando el texto empieza con guion.
                r = subprocess.run(
                    ["festival", "--tts", "--language", "spanish"],
                    input=text.encode(), capture_output=True, timeout=15,
                )
            else:
                log.debug("Audio: sin TTS disponible")
        except Exception as e:
            log.debug("speak error: %s", e)
        finally:
            with self._lock:
                self._speaking = False

    # ── Notificaciones ──────────────────────────────────────────────────────

    def notify(self, event_type: str, custom_text: str = ""):
        """Notificación sonora para un evento del sistema."""
        import random

        if custom_text:
            text = custom_text
        else:
            phrases = NOTIFICATION_PHRASES.get(event_type, [])
            if phrases:
                text = random.choice(phrases)
            else:
                text = f"Evento: {event_type}"

        self.speak(text, block=False)

    def alert(self, text: str):
        """Alerta sonora (prioridad alta)."""
        # Beep del sistema + voz
        try:
            subprocess.run(["paplay", "/usr/share/sounds/freedesktop/stereo/alarm-clock-elapsed.oga"],
                         capture_output=True, timeout=3)
        except Exception:
            # Fallback: beep ASCII
            print("\a", end="", flush=True)

        self.speak(f"Alerta. {text}", block=False)

    # ── Beep ─────────────────────────────────────────────────────────────────

    def beep(self, count: int = 1):
        """Emite beeps del sistema."""
        for _ in range(count):
            print("\a", end="", flush=True)
            import time
            time.sleep(0.2)

    # ── Stats ───────────────────────────────────────────────────────────────

    @property
    def is_speaking(self) -> bool:
        return self._speaking

    @property
    def available_engines(self) -> Dict[str, bool]:
        return {
            "espeak_ng": HAS_ESPEAK,
            "festival": HAS_FESTIVAL,
            "paplay": HAS_PLAY,
        }

    def stats(self) -> Dict[str, Any]:
        return {
            "enabled": self._enabled,
            "speaking": self._speaking,
            "volume": self._volume,
            "rate": self._rate,
            "voice": self._voice,
            "engines": self.available_engines,
        }


_audio: Optional[AudioEngine] = None


def get_audio() -> AudioEngine:
    global _audio
    if _audio is None:
        _audio = AudioEngine()
    return _audio


if __name__ == "__main__":
    import argparse
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    p = argparse.ArgumentParser(description="EIDOS Audio Engine")
    p.add_argument("--speak", type=str, help="Hablar texto")
    p.add_argument("--notify", type=str, help="Notificar tipo de evento")
    p.add_argument("--stats", action="store_true")
    args = p.parse_args()

    audio = AudioEngine()
    print(f"Audio engines: {audio.available_engines}")

    if args.speak:
        audio.speak(args.speak, block=True)
    elif args.notify:
        audio.notify(args.notify)
    elif args.stats:
        import json
        print(json.dumps(audio.stats(), indent=2, ensure_ascii=False))
    else:
        p.print_help()
