"""
core/eidos_observer.py — EIDOS Observer: visión + audio en tiempo real.

EIDOS puede ver TODO lo que pasa en la pantalla, incluyendo:
  - Captura continua de pantalla (2-5 FPS configurable)
  - Seguimiento de mouse: posición, clics, scroll en tiempo real
  - Observación de teclado: teclas pulsadas (para aprender patrones)
  - Análisis visual de frames con llama3.2-vision (Mac) o OCR (Kali)
  - Transcripción de audio en tiempo real con faster-whisper
  - Análisis de vídeos fotograma a fotograma

El observer corre en background y publica eventos al brain y al canal
de Colony para que los agentes respondan a lo que está pasando.

Uso:
    from core.eidos_observer import get_observer, start_observer
    obs = start_observer(fps=2, audio=True, mouse=True, keyboard=True)
    obs.stop()

Privacidad: todo procesado localmente, nada va a internet.
"""
from __future__ import annotations

import base64
import json
import logging
import os
import queue
import sqlite3
import subprocess
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional
from core.db import get_conn

log = logging.getLogger("eidos.observer")

OLLAMA_URL  = os.environ.get("OLLAMA_URL", "http://localhost:11435")
BRAIN_DB    = Path.home() / ".eidos" / "evolution_brain.db"
OBSERVE_DIR = Path.home() / ".eidos" / "observer"
OBSERVE_DIR.mkdir(parents=True, exist_ok=True)


# ── Eventos que emite el observer ──────────────────────────────────────────────

@dataclass
class ObserverEvent:
    kind:      str          # screen | mouse_click | mouse_move | key | audio
    data:      dict
    timestamp: float = field(default_factory=time.time)

    def __str__(self) -> str:
        d = {k: str(v)[:60] for k, v in self.data.items()}
        return f"[{self.kind}@{time.strftime('%H:%M:%S', time.localtime(self.timestamp))}] {d}"


# ── Observer principal ─────────────────────────────────────────────────────────

class EidosObserver:
    """
    Observer de tiempo real: ve pantalla, mouse, teclado, audio.
    Todo corre en threads separados para no bloquear a EIDOS.
    """

    def __init__(self, fps: float = 2.0, audio: bool = False,
                 mouse: bool = True, keyboard: bool = False,
                 vision_model: str = "moondream:latest"):
        self.fps           = max(0.5, min(fps, 10.0))   # 0.5–10 FPS
        self.audio         = audio
        self.mouse_track   = mouse
        self.keyboard_obs  = keyboard
        self.vision_model  = vision_model
        self._running      = False
        self._threads: list[threading.Thread] = []
        self._event_q: queue.Queue = queue.Queue(maxsize=200)
        self._last_screen_text: str = ""
        self._last_window: str = ""
        self._mouse_pos: tuple = (0, 0)
        self._click_count: int = 0
        self._keys_typed: list = []
        self._audio_transcript: str = ""

    # ── API pública ──────────────────────────────────────────────────────────

    def start(self) -> "EidosObserver":
        if self._running:
            return self
        self._running = True
        log.info("🔭 EidosObserver iniciado (fps=%.1f audio=%s mouse=%s key=%s)",
                 self.fps, self.audio, self.mouse_track, self.keyboard_obs)

        # Thread de captura de pantalla
        self._spawn(self._screen_loop, "obs-screen")

        # Thread de seguimiento de mouse
        if self.mouse_track:
            self._spawn(self._mouse_loop, "obs-mouse")

        # Thread de observación de teclado
        if self.keyboard_obs:
            self._spawn(self._keyboard_loop, "obs-keyboard")

        # Thread de audio en tiempo real
        if self.audio:
            self._spawn(self._audio_loop, "obs-audio")

        # Thread procesador de eventos → brain
        self._spawn(self._event_processor, "obs-processor")

        return self

    def stop(self) -> None:
        self._running = False
        for t in self._threads:
            t.join(timeout=3)
        log.info("🔭 EidosObserver detenido")

    def get_summary(self) -> dict:
        return {
            "running":          self._running,
            "fps":              self.fps,
            "last_window":      self._last_window,
            "screen_text":      self._last_screen_text[:200],
            "mouse_pos":        self._mouse_pos,
            "clicks":           self._click_count,
            "audio_transcript": self._audio_transcript[:200],
        }

    def describe_screen(self) -> str:
        """Pide al LLM que describa lo que hay en pantalla ahora mismo."""
        img_path = OBSERVE_DIR / "current_screen.png"
        if not img_path.exists():
            return "[sin captura disponible]"
        return self._analyze_frame(str(img_path))

    def watch_video_frames(self, video_path: str, fps: float = 1.0,
                           max_frames: int = 30) -> list[str]:
        """Analiza un vídeo extrayendo frames y describiendo cada uno."""
        out_dir = OBSERVE_DIR / "video_frames"
        out_dir.mkdir(exist_ok=True)
        # Extraer frames con ffmpeg
        try:
            subprocess.run([
                "ffmpeg", "-i", video_path,
                "-vf", f"fps={fps}",
                "-frames:v", str(max_frames),
                str(out_dir / "frame_%04d.png"),
                "-y", "-loglevel", "quiet"
            ], timeout=120, check=True)
        except Exception as e:
            return [f"Error extrayendo frames: {e}"]

        frames = sorted(out_dir.glob("frame_*.png"))[:max_frames]
        descriptions = []
        for frame in frames:
            desc = self._analyze_frame(str(frame))
            descriptions.append(desc)
            log.info("Frame %s: %s", frame.name, desc[:60])
        return descriptions

    # ── Loops internos ───────────────────────────────────────────────────────

    def _screen_loop(self) -> None:
        interval = 1.0 / self.fps
        try:
            import mss
            use_mss = True
        except ImportError:
            use_mss = False

        while self._running:
            try:
                out = OBSERVE_DIR / "current_screen.png"
                if use_mss:
                    import mss
                    with mss.mss() as sct:
                        mon = sct.monitors[0]
                        img = sct.grab(mon)
                        import mss.tools
                        mss.tools.to_png(img.rgb, img.size, output=str(out))
                else:
                    subprocess.run(["scrot", "-z", str(out)],
                                   capture_output=True, timeout=5)

                # OCR rápido para detectar cambios
                txt = self._ocr_fast(str(out))
                if txt != self._last_screen_text:
                    self._last_screen_text = txt
                    win = self._get_active_window()
                    self._last_window = win
                    self._event_q.put_nowait(ObserverEvent(
                        kind="screen",
                        data={"window": win, "text": txt[:300],
                              "frame": str(out)}
                    ))
            except Exception as e:
                log.debug("screen_loop: %s", e)
            time.sleep(interval)

    def _mouse_loop(self) -> None:
        """Sigue la posición del mouse y detecta clics vía X11."""
        try:
            from Xlib import display as XDisplay, X
            d = XDisplay.Display()
            root = d.screen().root
            prev_pos = (0, 0)
            prev_buttons = (0, 0, 0)

            while self._running:
                try:
                    ptr = root.query_pointer()
                    pos = (ptr.root_x, ptr.root_y)
                    buttons = (ptr.mask & X.Button1Mask,
                               ptr.mask & X.Button2Mask,
                               ptr.mask & X.Button3Mask)

                    self._mouse_pos = pos

                    # Movimiento significativo (>10px)
                    dx = abs(pos[0] - prev_pos[0])
                    dy = abs(pos[1] - prev_pos[1])
                    if dx + dy > 10:
                        self._event_q.put_nowait(ObserverEvent(
                            kind="mouse_move",
                            data={"x": pos[0], "y": pos[1],
                                  "dx": dx, "dy": dy}
                        ))
                        prev_pos = pos

                    # Clic detectado
                    if buttons != prev_buttons:
                        if buttons[0] and not prev_buttons[0]:
                            self._click_count += 1
                            self._event_q.put_nowait(ObserverEvent(
                                kind="mouse_click",
                                data={"x": pos[0], "y": pos[1],
                                      "button": "left",
                                      "window": self._last_window}
                            ))
                        prev_buttons = buttons

                    time.sleep(0.05)  # 20Hz mouse tracking

                except Exception as e:
                    log.debug("mouse_loop inner: %s", e)
                    time.sleep(0.1)

        except ImportError:
            log.warning("python-xlib no disponible, mouse tracking desactivado")
        except Exception as e:
            log.warning("mouse_loop: %s", e)

    def _keyboard_loop(self) -> None:
        """Observa teclas pulsadas vía xinput (no hace keylogger — solo detecta patrones)."""
        try:
            # xinput lista los dispositivos de teclado
            r = subprocess.run(["xinput", "list", "--id-only"],
                               capture_output=True, text=True, timeout=5)
            if r.returncode != 0:
                return
            ids = r.stdout.strip().split()
            if not ids:
                return

            # Solo primero teclado
            kbd_id = ids[0]
            proc = subprocess.Popen(
                ["xinput", "test", kbd_id],
                stdout=subprocess.PIPE, text=True
            )
            while self._running and proc.poll() is None:
                line = proc.stdout.readline().strip()
                if "key press" in line or "key release" in line:
                    parts = line.split()
                    key_code = parts[-1] if parts else "?"
                    if "press" in line:
                        self._keys_typed.append(key_code)
                        if len(self._keys_typed) > 50:
                            self._keys_typed.pop(0)
                        self._event_q.put_nowait(ObserverEvent(
                            kind="key",
                            data={"keycode": key_code, "action": "press"}
                        ))
            proc.terminate()
        except Exception as e:
            log.debug("keyboard_loop: %s", e)

    def _audio_loop(self) -> None:
        """Transcribe audio del sistema en tiempo real con faster-whisper."""
        try:
            import pyaudio
            from faster_whisper import WhisperModel
        except ImportError as e:
            log.warning("audio_loop: %s no disponible", e)
            return

        try:
            model = WhisperModel("tiny", device="cpu", compute_type="int8")
            audio_if = pyaudio.PyAudio()
            CHUNK     = 16000 * 5   # 5 segundos de audio
            RATE      = 16000
            CHANNELS  = 1
            FORMAT    = pyaudio.paInt16
            import tempfile, wave, struct

            stream = audio_if.open(
                format=FORMAT, channels=CHANNELS, rate=RATE,
                input=True, frames_per_buffer=1024
            )
            log.info("🎤 Audio observer activo (tiny model)")

            while self._running:
                try:
                    # Grabar 5s
                    frames = []
                    for _ in range(0, RATE * 5 // 1024):
                        if not self._running:
                            break
                        frames.append(stream.read(1024, exception_on_overflow=False))

                    # Guardar en WAV temporal
                    with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as tf:
                        tmp_wav = tf.name
                    wf = wave.open(tmp_wav, "wb")
                    wf.setnchannels(CHANNELS)
                    wf.setsampwidth(audio_if.get_sample_size(FORMAT))
                    wf.setframerate(RATE)
                    wf.writeframes(b"".join(frames))
                    wf.close()

                    # Transcribir
                    segments, _ = model.transcribe(tmp_wav, language="es")
                    text = " ".join(s.text.strip() for s in segments).strip()
                    Path(tmp_wav).unlink(missing_ok=True)

                    if text and len(text) > 5:
                        self._audio_transcript = text
                        log.info("🎤 Audio: %s", text[:80])
                        self._event_q.put_nowait(ObserverEvent(
                            kind="audio",
                            data={"text": text, "lang": "es"}
                        ))
                        # Guardar en brain
                        self._save_to_brain(
                            f"Audio {time.strftime('%H:%M:%S')}",
                            text, "audio_transcript"
                        )

                except Exception as e:
                    log.debug("audio_loop inner: %s", e)

            stream.stop_stream()
            stream.close()
            audio_if.terminate()

        except Exception as e:
            log.warning("audio_loop init: %s", e)

    def _event_processor(self) -> None:
        """Procesa eventos y los guarda en brain cuando son significativos."""
        screen_event_count = 0
        while self._running or not self._event_q.empty():
            try:
                ev = self._event_q.get(timeout=1)
                if ev.kind == "screen":
                    screen_event_count += 1
                    # Cada 10 cambios de pantalla, analizar visualmente si hay vision model
                    if screen_event_count % 10 == 0:
                        frame = ev.data.get("frame", "")
                        if frame and Path(frame).exists():
                            desc = self._analyze_frame(frame)
                            if desc and len(desc) > 20:
                                self._save_to_brain(
                                    f"Pantalla {time.strftime('%H:%M')}",
                                    f"Ventana: {ev.data.get('window','?')}\n{desc}",
                                    "screen_observation"
                                )
                elif ev.kind == "mouse_click":
                    log.debug("Click: %s", ev)
                elif ev.kind == "key":
                    pass  # solo tracking interno
            except queue.Empty:
                continue
            except Exception as e:
                log.debug("event_processor: %s", e)

    # ── Helpers ──────────────────────────────────────────────────────────────

    def _analyze_frame(self, img_path: str) -> str:
        """Envía un frame al modelo de visión y pide descripción."""
        try:
            with open(img_path, "rb") as f:
                img_b64 = base64.b64encode(f.read()).decode()

            import urllib.request
            payload = json.dumps({
                "model": self.vision_model,
                "prompt": (
                    "Describe brevemente qué hay en esta pantalla. "
                    "¿Qué aplicación está abierta? ¿Qué hace el usuario? "
                    "¿Hay texto importante visible? Responde en español, máximo 3 frases."
                ),
                "images": [img_b64],
                "stream": False,
                "options": {"num_predict": 150, "temperature": 0.3},
            }).encode()
            req = urllib.request.Request(
                f"{OLLAMA_URL}/api/generate", data=payload,
                headers={"Content-Type": "application/json"}
            )
            with urllib.request.urlopen(req, timeout=30) as r:
                data = json.loads(r.read())
            return data.get("response", "")
        except Exception as e:
            # Fallback: usar OCR si no hay vision model
            return self._ocr_fast(img_path)

    @staticmethod
    def _ocr_fast(img_path: str) -> str:
        """OCR rápido con tesseract."""
        try:
            r = subprocess.run(
                ["tesseract", img_path, "stdout", "-l", "eng+spa",
                 "--psm", "3"],
                capture_output=True, text=True, timeout=10
            )
            return r.stdout.strip()[:500]
        except Exception:
            return ""

    @staticmethod
    def _get_active_window() -> str:
        try:
            r = subprocess.run(["xdotool", "getactivewindow", "getwindowname"],
                               capture_output=True, text=True, timeout=3)
            return r.stdout.strip()[:80]
        except Exception:
            return "?"

    @staticmethod
    def _save_to_brain(concept: str, definition: str, category: str) -> None:
        try:
            conn = get_conn(BRAIN_DB, timeout=3)
            ex = conn.execute("SELECT 1 FROM knowledge_nodes WHERE concept=?",
                              (concept,)).fetchone()
            if not ex:
                conn.execute(
                    "INSERT INTO knowledge_nodes (concept,definition,category,"
                    "confidence,source,created_at) VALUES (?,?,?,?,?,?)",
                    (concept, definition[:1000], category, 0.7,
                     "observer", time.time())
                )
                conn.commit()
            pass  # S109: get_conn no necesita close()
        except Exception:
            pass

    def _spawn(self, target, name: str) -> None:
        t = threading.Thread(target=target, daemon=True, name=name)
        t.start()
        self._threads.append(t)
        log.debug("Thread %s iniciado", name)


# ── Singleton ─────────────────────────────────────────────────────────────────

_observer: Optional[EidosObserver] = None


def get_observer() -> EidosObserver:
    global _observer
    if _observer is None:
        _observer = EidosObserver()
    return _observer


def start_observer(fps: float = 2.0, audio: bool = False,
                   mouse: bool = True, keyboard: bool = False) -> EidosObserver:
    """Inicia el observer con los parámetros dados."""
    global _observer
    _observer = EidosObserver(fps=fps, audio=audio, mouse=mouse,
                               keyboard=keyboard)
    return _observer.start()
