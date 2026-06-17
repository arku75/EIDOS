"""
core/colony_wake_word.py — Wake word bidireccional EIDOS ↔ SER

Flujo:
    1. Escucha micro con Whisper (modo silencio-activo)
    2. Si detecta "EIDOS" o "colonia" → activa sesión de voz
    3. Transcribe la petición completa → delibera con Colony
    4. Responde con Piper TTS

Usar:
    eidos wake-word          → activa el loop de escucha
    eidos wake-word stop     → detiene
    from core.colony_wake_word import get_wake_word_daemon
    daemon = get_wake_word_daemon()
    daemon.start()
"""
from __future__ import annotations

import logging
import os
import queue
import subprocess
import tempfile
import threading
import time
from pathlib import Path
from typing import Optional

log = logging.getLogger("eidos.wake_word")

WHISPER_MODEL   = os.environ.get("EIDOS_WHISPER_MODEL", "base")
WHISPER_DEVICE  = "cpu"
PIPER_BIN       = Path("/home/ser/EIDOS/voice/piper/piper/piper")
PIPER_MODEL     = Path("/home/ser/.eidos/piper_voices/es_ES-davefx-medium.onnx")
PIPER_LD        = "/home/ser/EIDOS/voice/piper:/home/ser/EIDOS/voice/piper/piper"
WAKE_WORDS      = {"eidos", "colonia", "colony", "oye eidos", "hey eidos"}
SAMPLE_RATE     = 16000
AUDIO_DEVICE    = os.environ.get("EIDOS_AUDIO_DEVICE", "pulse")

# VAD (Voice Activity Detection) con InputStream continuo — evita parpadeo del micro
CHUNK_FRAMES    = 512          # frames por callback (~32ms a 16kHz)
SILENCE_RMS     = 120          # RMS por debajo = silencio
PRE_ROLL_CHUNKS = 16           # chunks previos al habla que se incluyen (~0.5s)
SILENCE_CHUNKS  = 48           # chunks de silencio para cerrar utterance (~1.5s)
MIN_SPEECH_FRAMES = 8000       # ~0.5s mínimo para intentar transcripción

_instance: Optional["WakeWordDaemon"] = None
_lock = threading.Lock()


def get_wake_word_daemon() -> "WakeWordDaemon":
    global _instance
    if _instance is None:
        with _lock:
            if _instance is None:
                _instance = WakeWordDaemon()
    return _instance


class WakeWordDaemon:

    def __init__(self):
        self._running   = False
        self._stop_evt  = threading.Event()
        self._thread: Optional[threading.Thread] = None
        self._listening = False
        self._stats = {
            "activations": 0,
            "queries":     0,
            "started_at":  None,
        }
        self._whisper = None
        self._check_dependencies()

    def _check_dependencies(self) -> None:
        # Verificar faster-whisper (preferido) o openai-whisper
        try:
            from faster_whisper import WhisperModel
            self._whisper = "faster"
            self._fw_model = None  # Se carga lazy en primer uso
            log.info("faster-whisper OK")
        except ImportError:
            try:
                import whisper
                self._whisper = whisper
                self._fw_model = None
                log.info("openai-whisper OK")
            except ImportError:
                self._fw_model = None
                log.warning("Whisper no instalado: pip install faster-whisper")
        # Verificar Piper TTS
        if not PIPER_BIN.exists():
            log.warning("Piper no encontrado en %s", PIPER_BIN)
        # Verificar sounddevice
        try:
            import sounddevice  # noqa
        except ImportError:
            log.warning("sounddevice no instalado: pip install sounddevice")

    def start(self) -> bool:
        if self._running:
            log.info("Wake word daemon ya activo")
            return True
        if not hasattr(self, '_whisper') or not self._whisper:
            log.error("Whisper no disponible — no se puede iniciar wake word")
            return False
        self._stop_evt.clear()
        self._running = True
        self._stats["started_at"] = time.time()
        self._thread = threading.Thread(
            target=self._loop,
            daemon=True,
            name="wake-word-daemon",
        )
        self._thread.start()
        log.info("Wake word daemon iniciado — escuchando: %s", WAKE_WORDS)
        return True

    def stop(self) -> None:
        self._stop_evt.set()
        self._running = False
        log.info("Wake word daemon detenido")

    def is_running(self) -> bool:
        return self._running

    def get_stats(self) -> dict:
        s = dict(self._stats)
        s["running"] = self._running
        s["listening"] = self._listening
        if s.get("started_at"):
            s["uptime_minutes"] = round((time.time() - s["started_at"]) / 60, 1)
        return s

    # ── Loop principal con InputStream continuo ───────────────────────────────
    # El micrófono se abre UNA vez y permanece abierto.
    # KDE muestra el indicador fijo (ON), sin parpadeo.

    def _loop(self) -> None:
        log.info("Wake word loop iniciado (InputStream continuo)")
        try:
            import sounddevice as sd
            import numpy as np
        except ImportError:
            log.error("sounddevice/numpy no disponibles")
            self._running = False
            return

        audio_q: queue.Queue = queue.Queue(maxsize=200)

        def _cb(indata, frames, t, status):
            try:
                audio_q.put_nowait(indata.copy())
            except queue.Full:
                pass

        try:
            stream_kwargs = dict(
                samplerate=SAMPLE_RATE,
                channels=1,
                dtype="int16",
                blocksize=CHUNK_FRAMES,
                callback=_cb,
            )
            if AUDIO_DEVICE:
                stream_kwargs["device"] = AUDIO_DEVICE
            stream = sd.InputStream(**stream_kwargs)
        except Exception as e:
            log.error("No se pudo abrir InputStream: %s", e)
            self._running = False
            return

        pre_roll: list = []
        buffer:   list = []
        in_speech      = False
        silence_count  = 0

        with stream:
            log.info("Micrófono abierto — escuchando wake word %s", WAKE_WORDS)
            while not self._stop_evt.is_set():
                try:
                    chunk = audio_q.get(timeout=0.5)
                except queue.Empty:
                    continue

                rms = float(np.sqrt(np.mean(chunk.astype(np.float32) ** 2)))

                if not in_speech:
                    self._listening = False
                    pre_roll.append(chunk)
                    if len(pre_roll) > PRE_ROLL_CHUNKS:
                        pre_roll.pop(0)
                    if rms > SILENCE_RMS:
                        in_speech = True
                        silence_count = 0
                        buffer = list(pre_roll) + [chunk]
                        self._listening = True
                else:
                    buffer.append(chunk)
                    if rms < SILENCE_RMS:
                        silence_count += 1
                        if silence_count >= SILENCE_CHUNKS:
                            # Fin de utterance → transcribir
                            audio = np.concatenate(buffer).flatten()
                            if len(audio) >= MIN_SPEECH_FRAMES:
                                text = self._transcribe_array(audio)
                                if text:
                                    log.debug("Transcripción: %s", text)
                                    if self._has_wake_word(text):
                                        self._stats["activations"] += 1
                                        self._handle_activation(text)
                            buffer = []
                            pre_roll = []
                            in_speech = False
                            silence_count = 0
                            self._listening = False
                    else:
                        silence_count = 0

        self._listening = False
        log.info("Wake word loop terminado")

    def _transcribe_array(self, audio_np) -> Optional[str]:
        """Transcribe directamente desde numpy array (sin archivo temporal)."""
        try:
            import numpy as np
            import scipy.io.wavfile as wav
            f = tempfile.NamedTemporaryFile(suffix=".wav", delete=False)
            wav.write(f.name, SAMPLE_RATE, audio_np.astype(np.int16))
            text = self._transcribe(f.name)
            try:
                os.unlink(f.name)
            except Exception:
                pass  # error no crítico, continuar
            return text
        except Exception as e:
            log.debug("transcribe_array error: %s", e)
            return None

    def _transcribe(self, audio_path: str) -> Optional[str]:
        """Transcribe audio con faster-whisper o openai-whisper."""
        try:
            if self._whisper == "faster":
                from faster_whisper import WhisperModel
                if self._fw_model is None:
                    self._fw_model = WhisperModel(
                        WHISPER_MODEL,
                        device=WHISPER_DEVICE,
                        compute_type="int8",
                    )
                segs, _ = self._fw_model.transcribe(
                    audio_path, language="es", beam_size=1
                )
                return " ".join(s.text for s in segs).strip().lower()
            else:
                model = self._whisper.load_model(WHISPER_MODEL)
                result = model.transcribe(
                    audio_path, language="es", fp16=False,
                    condition_on_previous_text=False,
                )
                return result.get("text", "").strip().lower()
        except Exception as e:
            log.debug("transcribe error: %s", e)
            return None

    def _has_wake_word(self, text: str) -> bool:
        text_lower = text.lower()
        return any(w in text_lower for w in WAKE_WORDS)

    def _handle_activation(self, trigger_text: str) -> None:
        """
        Wake word detectado. Graba la petición real, delibera, responde.
        """
        log.info("Wake word activado: %s", trigger_text)
        self._speak("Sí, dime.")

        # Grabar la petición (hasta 8 segundos)
        audio_path = self._record_chunk(8)
        if not audio_path:
            self._speak("No te escuché bien. Inténtalo de nuevo.")
            return

        query = self._transcribe(audio_path)
        try:
            os.unlink(audio_path)
        except Exception:
            pass  # error no crítico, continuar
        if not query or len(query) < 3:
            self._speak("No entendí. ¿Puedes repetir?")
            return

        log.info("Petición por voz: %s", query)
        self._stats["queries"] += 1

        # Deliberar con Colony
        response = self._ask_colony(query)
        if response:
            # Responder con TTS (primeros 300 chars para no ser muy largo)
            self._speak(response[:300])
        else:
            self._speak("Dame un momento, estoy procesando.")

    def _ask_colony(self, query: str) -> Optional[str]:
        """Envía query a Colony deliberation."""
        try:
            from core.colony_community import get_colony
            colony = get_colony()
            return colony.deliberate(query, requester="ser_voice")
        except Exception as e:
            log.debug("colony ask error: %s", e)
            return None

    def _speak(self, text: str) -> None:
        """TTS con Piper."""
        if not PIPER_BIN.exists():
            log.debug("TTS: %s (Piper no disponible)", text)
            return
        try:
            model_path = str(PIPER_MODEL) if PIPER_MODEL.exists() else ""
            if not model_path:
                # Buscar cualquier modelo Piper disponible
                piper_dir = Path.home() / ".local" / "share" / "piper"
                models = list(piper_dir.glob("*.onnx")) if piper_dir.exists() else []
                if models:
                    model_path = str(models[0])
                else:
                    log.warning("No hay modelos Piper instalados")
                    return

            import os as _os
            env = _os.environ.copy()
            env["LD_LIBRARY_PATH"] = PIPER_LD + (":" + env.get("LD_LIBRARY_PATH", "") if env.get("LD_LIBRARY_PATH") else "")
            proc = subprocess.Popen(
                [str(PIPER_BIN), "--model", model_path, "--output-raw"],
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL,
                env=env,
            )
            # Reproducir con aplay (ALSA)
            player = subprocess.Popen(
                ["aplay", "-r", "22050", "-f", "S16_LE", "-t", "raw", "-"],
                stdin=proc.stdout,
                stderr=subprocess.DEVNULL,
            )
            proc.stdin.write(text.encode("utf-8"))
            proc.stdin.close()
            proc.wait(timeout=15)
            player.wait(timeout=15)
        except Exception as e:
            log.debug("TTS error: %s", e)
