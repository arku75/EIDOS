"""
EIDOS core/wake_word.py — Wake Word + Voice Listener
======================================================
Sistema de escucha continua que detecta la palabra clave "EIDOS" (o variantes)
y activa el reconocimiento de voz completo para procesar comandos hablados.

Pipeline:
  1. Micrófono → stream de audio (16kHz, mono)
  2. Vosk STT → transcripción en tiempo real
  3. Wake word detection → "eidos", "hey eidos", "oye eidos"
  4. Si detecta wake word → graba comando completo (hasta silencio)
  5. Despacha comando a ColonyEngine o kernel
  6. Responde con Piper TTS

Modes:
  - PASSIVE: Solo escucha wake word (bajo consumo CPU)
  - ACTIVE: Escucha y transcribe todo (post-wake word, timeout 10s)
  - CONTINUOUS: Transcripción continua (para dictado)

Uso:
    from core.wake_word import get_wake_listener
    listener = get_wake_listener()
    listener.start()  # Empieza a escuchar
    listener.stop()   # Para
    listener.on_command(callback)  # Registra handler
"""
from __future__ import annotations

import json
import logging
import os
import queue
import threading
import time
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any, Optional, Dict, List, Callable

log = logging.getLogger("eidos.wake_word")

# ══════════════════════════════════════════════════════════════════════════════
#  IMPORTS CONDICIONALES
# ══════════════════════════════════════════════════════════════════════════════

try:
    import vosk
    HAS_VOSK = True
except ImportError:
    HAS_VOSK = False

try:
    import sounddevice as sd
    HAS_SOUNDDEVICE = True
except ImportError:
    HAS_SOUNDDEVICE = False

# Modelos Vosk
MODELS_DIR = Path.home() / ".eidos" / "models"
VOSK_MODEL_ES = MODELS_DIR / "vosk-model-small-es-0.42"
VOSK_MODEL_EN = MODELS_DIR / "vosk-model-small-en-us-0.15"

# ══════════════════════════════════════════════════════════════════════════════
#  CONFIGURACIÓN
# ══════════════════════════════════════════════════════════════════════════════

SAMPLE_RATE = 16000
BLOCK_SIZE = 4000           # ~250ms de audio por bloque
ACTIVE_TIMEOUT = 10.0       # Segundos en modo ACTIVE antes de volver a PASSIVE
SILENCE_THRESHOLD = 2.0     # Segundos de silencio para terminar comando
MIN_COMMAND_LENGTH = 2      # Mínimo de palabras para ser un comando válido

# Wake words (case-insensitive, parcial match)
WAKE_WORDS_ES = ["eidos", "hey eidos", "oye eidos", "hola eidos", "ey eidos"]
WAKE_WORDS_EN = ["eidos", "hey eidos", "hi eidos", "ok eidos"]
ALL_WAKE_WORDS = WAKE_WORDS_ES + WAKE_WORDS_EN


class ListenMode(str, Enum):
    PASSIVE = "passive"       # Solo wake word
    ACTIVE = "active"         # Escucha comando completo
    CONTINUOUS = "continuous"  # Transcripción continua


@dataclass
class VoiceCommand:
    """Comando de voz reconocido."""
    text: str
    language: str = "es"
    confidence: float = 0.0
    wake_word_used: str = ""
    duration_s: float = 0.0
    timestamp: float = field(default_factory=time.time)


# ══════════════════════════════════════════════════════════════════════════════
#  WAKE WORD LISTENER
# ══════════════════════════════════════════════════════════════════════════════

class WakeWordListener:
    """
    Escucha continua con detección de wake word + STT.

    Requiere: vosk, sounddevice
    Modelos: vosk-model-small-es-0.42 (español), vosk-model-small-en-us-0.15 (inglés)
    """

    def __init__(self, language: str = "es"):
        self._language = language
        self._mode = ListenMode.PASSIVE
        self._running = False
        self._lock = threading.Lock()
        self._audio_queue: queue.Queue = queue.Queue()
        self._listen_thread: Optional[threading.Thread] = None
        self._process_thread: Optional[threading.Thread] = None
        self._command_handlers: List[Callable[[VoiceCommand], None]] = []
        self._wake_handlers: List[Callable[[str], None]] = []

        # Stats
        self._wake_count = 0
        self._command_count = 0
        self._total_listen_time = 0.0
        self._start_time: Optional[float] = None
        self._last_command: Optional[VoiceCommand] = None

        # Vosk model
        self._model: Optional[Any] = None
        self._recognizer: Optional[Any] = None

        # Active mode state
        self._active_start: float = 0.0
        self._active_text: str = ""
        self._last_speech_time: float = 0.0

        log.info("[WakeWord] Init — vosk=%s sounddevice=%s lang=%s",
                 HAS_VOSK, HAS_SOUNDDEVICE, language)

    def _load_model(self) -> bool:
        """Carga el modelo Vosk."""
        if not HAS_VOSK:
            print("[WakeWord] Vosk no instalado: pip install vosk")
            return False

        model_path = VOSK_MODEL_ES if self._language == "es" else VOSK_MODEL_EN
        if not model_path.exists():
            print(f"[WakeWord] Modelo no encontrado: {model_path}")
            return False

        try:
            vosk.SetLogLevel(-1)  # Silenciar logs de Vosk
            self._model = vosk.Model(str(model_path))
            self._recognizer = vosk.KaldiRecognizer(self._model, SAMPLE_RATE)
            print(f"[WakeWord] Modelo cargado: {model_path.name}")
            return True
        except Exception as e:
            print(f"[WakeWord] Error cargando modelo: {e}")
            return False

    # ── START / STOP ─────────────────────────────────────────────────────────

    def start(self) -> str:
        """Inicia la escucha continua."""
        if self._running:
            return "Ya escuchando"

        if not HAS_SOUNDDEVICE:
            return "sounddevice no instalado: pip install sounddevice"

        if not self._model:
            if not self._load_model():
                return "Error cargando modelo Vosk"

        self._running = True
        self._mode = ListenMode.PASSIVE
        self._start_time = time.time()

        # Thread de captura de audio
        self._listen_thread = threading.Thread(
            target=self._audio_capture_loop, daemon=True, name="wake-audio"
        )
        self._listen_thread.start()

        # Thread de procesamiento STT
        self._process_thread = threading.Thread(
            target=self._process_loop, daemon=True, name="wake-stt"
        )
        self._process_thread.start()

        msg = f"Escuchando... (wake word: 'EIDOS', idioma: {self._language})"
        print(f"[WakeWord] {msg}")
        return msg

    def stop(self) -> str:
        """Detiene la escucha."""
        self._running = False
        if self._start_time:
            self._total_listen_time += time.time() - self._start_time
            self._start_time = None
        self._mode = ListenMode.PASSIVE
        return "Escucha detenida"

    # ── AUDIO CAPTURE ────────────────────────────────────────────────────────

    def _audio_capture_loop(self) -> None:
        """Captura audio del micrófono."""
        def audio_callback(indata, frames, time_info, status):
            if self._running:
                self._audio_queue.put(bytes(indata))

        try:
            with sd.RawInputStream(
                samplerate=SAMPLE_RATE,
                blocksize=BLOCK_SIZE,
                dtype="int16",
                channels=1,
                callback=audio_callback,
            ):
                while self._running:
                    time.sleep(0.1)
        except Exception as e:
            log.error("[WakeWord] Audio capture error: %s", e)
            print(f"[WakeWord] Error captura audio: {e}")
            self._running = False

    # ── STT PROCESSING ───────────────────────────────────────────────────────

    def _process_loop(self) -> None:
        """Procesa audio y detecta wake word / comandos."""
        while self._running:
            try:
                data = self._audio_queue.get(timeout=0.5)
            except queue.Empty:
                # Check active mode timeout
                if self._mode == ListenMode.ACTIVE:
                    elapsed = time.time() - self._active_start
                    silence = time.time() - self._last_speech_time
                    if elapsed > ACTIVE_TIMEOUT or silence > SILENCE_THRESHOLD:
                        self._finalize_command()
                continue

            if not self._recognizer:
                continue

            if self._recognizer.AcceptWaveform(data):
                result = json.loads(self._recognizer.Result())
                text = result.get("text", "").strip().lower()
                if text:
                    self._handle_text(text, final=True)
            else:
                partial = json.loads(self._recognizer.PartialResult())
                text = partial.get("partial", "").strip().lower()
                if text:
                    self._handle_text(text, final=False)

    def _handle_text(self, text: str, final: bool) -> None:
        """Procesa texto reconocido."""
        if self._mode == ListenMode.PASSIVE:
            # Buscar wake word
            wake_word = self._detect_wake_word(text)
            if wake_word:
                self._wake_count += 1
                self._mode = ListenMode.ACTIVE
                self._active_start = time.time()
                self._active_text = ""
                self._last_speech_time = time.time()

                # Extraer comando después del wake word
                idx = text.find(wake_word) + len(wake_word)
                remainder = text[idx:].strip()
                if remainder:
                    self._active_text = remainder

                print(f"\n[WakeWord] Detectado: '{wake_word}' — escuchando comando...")

                # Notificar handlers de wake
                for handler in self._wake_handlers:
                    try:
                        handler(wake_word)
                    except Exception:
                        pass  # error no crítico, continuar
        elif self._mode == ListenMode.ACTIVE:
            self._last_speech_time = time.time()
            if final:
                if self._active_text:
                    self._active_text += " " + text
                else:
                    self._active_text = text

        elif self._mode == ListenMode.CONTINUOUS:
            if final and text:
                cmd = VoiceCommand(
                    text=text, language=self._language, confidence=0.8
                )
                self._dispatch_command(cmd)

    def _detect_wake_word(self, text: str) -> Optional[str]:
        """Detecta si el texto contiene un wake word."""
        text_lower = text.lower()
        for ww in ALL_WAKE_WORDS:
            if ww in text_lower:
                return ww
        return None

    def _finalize_command(self) -> None:
        """Finaliza el modo ACTIVE y despacha el comando."""
        self._mode = ListenMode.PASSIVE
        text = self._active_text.strip()

        if not text or len(text.split()) < MIN_COMMAND_LENGTH:
            print("[WakeWord] Comando muy corto, ignorado")
            return

        duration = time.time() - self._active_start
        cmd = VoiceCommand(
            text=text,
            language=self._language,
            confidence=0.7,
            wake_word_used="eidos",
            duration_s=round(duration, 1),
        )

        print(f"[WakeWord] Comando: '{text}' ({duration:.1f}s)")
        self._dispatch_command(cmd)

    def _dispatch_command(self, cmd: VoiceCommand) -> None:
        """Despacha un comando a los handlers registrados."""
        self._command_count += 1
        self._last_command = cmd

        for handler in self._command_handlers:
            try:
                handler(cmd)
            except Exception as e:
                log.warning("[WakeWord] Handler error: %s", e)

        # Si no hay handlers, intentar ColonyEngine
        if not self._command_handlers:
            self._default_handler(cmd)

    def _default_handler(self, cmd: VoiceCommand) -> None:
        """Handler por defecto: envía a ColonyEngine + responde con TTS."""
        try:
            from core.colony_query_engine import get_colony_engine
            engine = get_colony_engine()
            result = engine.query(cmd.text, requester="voice")

            print(f"[WakeWord] Colony response: {result.response[:200]}")

            # Intentar TTS
            try:
                from core.voice_system import get_voice_system
                voice = get_voice_system()
                if voice.is_available():
                    voice.speak(result.response[:500])
            except Exception:
                pass  # Sin voz, solo print

        except Exception as e:
            print(f"[WakeWord] Error processing: {e}")

    # ── HANDLERS ─────────────────────────────────────────────────────────────

    def on_command(self, handler: Callable[[VoiceCommand], None]) -> None:
        """Registra handler para comandos de voz."""
        self._command_handlers.append(handler)

    def on_wake(self, handler: Callable[[str], None]) -> None:
        """Registra handler para detección de wake word."""
        self._wake_handlers.append(handler)

    # ── MODOS ────────────────────────────────────────────────────────────────

    def set_mode(self, mode: str) -> str:
        """Cambia el modo de escucha."""
        try:
            self._mode = ListenMode(mode)
            return f"Modo cambiado a {mode}"
        except ValueError:
            return f"Modo inválido: {mode}. Usar: passive, active, continuous"

    def set_language(self, lang: str) -> str:
        """Cambia idioma y recarga modelo."""
        if lang not in ("es", "en"):
            return "Idioma no soportado. Usar: es, en"
        self._language = lang
        was_running = self._running
        if was_running:
            self.stop()
        self._model = None
        self._recognizer = None
        if was_running:
            return self.start()
        return f"Idioma cambiado a {lang}"

    # ── STATUS ───────────────────────────────────────────────────────────────

    def get_status(self) -> Dict:
        """Estado del listener."""
        uptime = 0.0
        if self._start_time:
            uptime = time.time() - self._start_time

        return {
            "running": self._running,
            "mode": self._mode.value,
            "language": self._language,
            "vosk_available": HAS_VOSK,
            "sounddevice_available": HAS_SOUNDDEVICE,
            "model_loaded": self._model is not None,
            "model_es_exists": VOSK_MODEL_ES.exists(),
            "model_en_exists": VOSK_MODEL_EN.exists(),
            "wake_count": self._wake_count,
            "command_count": self._command_count,
            "uptime_s": round(uptime, 1),
            "total_listen_time_s": round(self._total_listen_time + uptime, 1),
            "last_command": {
                "text": self._last_command.text,
                "time": self._last_command.timestamp,
            } if self._last_command else None,
        }

    def is_available(self) -> bool:
        """¿Puede funcionar el listener?"""
        return HAS_VOSK and HAS_SOUNDDEVICE and (VOSK_MODEL_ES.exists() or VOSK_MODEL_EN.exists())


# ══════════════════════════════════════════════════════════════════════════════
#  SINGLETON
# ══════════════════════════════════════════════════════════════════════════════

_wake_listener: Optional[WakeWordListener] = None


def get_wake_listener(language: str = "es") -> WakeWordListener:
    global _wake_listener
    if _wake_listener is None:
        _wake_listener = WakeWordListener(language=language)
    return _wake_listener


# ── CLI ──────────────────────────────────────────────────────────────────────
if __name__ == "__main__":
    import sys

    print("=" * 60)
    print("  EIDOS Wake Word Listener")
    print("=" * 60)

    listener = get_wake_listener()
    status = listener.get_status()
    print(f"\n  Vosk: {'OK' if status['vosk_available'] else 'NO'}")
    print(f"  SoundDevice: {'OK' if status['sounddevice_available'] else 'NO'}")
    print(f"  Model ES: {'OK' if status['model_es_exists'] else 'NO'}")
    print(f"  Model EN: {'OK' if status['model_en_exists'] else 'NO'}")
    print(f"  Available: {listener.is_available()}")

    if listener.is_available() and "--listen" in sys.argv:
        print("\n  Iniciando escucha... (Ctrl+C para parar)")
        print("  Di 'EIDOS' o 'Hey EIDOS' para activar")
        msg = listener.start()
        print(f"  {msg}")
        try:
            while True:
                time.sleep(0.5)
        except KeyboardInterrupt:
            listener.stop()
            print("\n  Detenido.")
    else:
        print("\n  Usa --listen para iniciar escucha real")
        print("  Wake Word Listener funcional")
