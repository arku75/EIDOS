"""
EIDOS Voice System - Sistema de Voz con Piper + fallbacks
==========================================================
Sistema de texto-a-voz multi-engine:

  1. Piper TTS (principal) — ultra-rapido en CPU, voces naturales
  2. pyttsx3 (fallback)   — offline, sin dependencias pesadas

Features:
- Voces naturales en espanol e ingles
- Tiempo real en CPU (~100ms/frase con Piper)
- Cache de audio generado
- Multiples modelos de voz
"""
from __future__ import annotations

import os
import sys
import time
import hashlib
from pathlib import Path
from typing import Optional, Dict, Any
from dataclasses import dataclass

# Añadir root al path
EIDOS_ROOT = Path(__file__).parent.parent.parent
sys.path.insert(0, str(EIDOS_ROOT))

from core.eidos_config import get_config

# ============================
# VOICE CONFIGURATION
# ============================

PIPER_VOICES_DIR = Path.home() / ".eidos" / "piper_voices"

@dataclass
class VoiceSettings:
    """Configuracion de voz"""
    speaker: str = "v2/es_speaker_6"  # Voz espanola masculina
    language: str = "es"
    use_small_model: bool = True  # Modelo pequeno para RAM
    temperature: float = 0.7  # Creatividad (0-1)
    silence_duration: float = 0.25  # Pausa entre frases

# ============================
# PIPER VOICE ENGINE (PRIMARY)
# ============================

class PiperVoiceEngine:
    """
    Motor de voz usando Piper TTS.

    Ultra-rapido en CPU (~100ms/frase), calidad buena,
    multiples voces/idiomas via modelos ONNX.
    """

    # Available voice models (name -> filename without .onnx)
    VOICES = {
        "es_davefx": "es_ES-davefx-medium",
        "en_lessac": "en_US-lessac-medium",
    }

    def __init__(self, language: str = "es"):
        self.piper_available = False
        self.language = language
        self._voices: dict = {}

        self.cache_dir = Path.home() / ".eidos" / "voice_cache"
        self.cache_dir.mkdir(parents=True, exist_ok=True)

        self._load_piper()

    def _load_piper(self):
        """Load Piper and available voice models."""
        try:
            from piper import PiperVoice
            self._PiperVoice = PiperVoice

            # Load available models
            for name, filename in self.VOICES.items():
                model_path = PIPER_VOICES_DIR / f"{filename}.onnx"
                if model_path.exists():
                    try:
                        self._voices[name] = PiperVoice.load(str(model_path))
                        print(f"[VOICE] Piper voice loaded: {name}")
                    except Exception as e:
                        print(f"[VOICE] Piper voice {name} error: {e}")

            if self._voices:
                self.piper_available = True
                print(f"[VOICE] Piper TTS ready ({len(self._voices)} voices)")
            else:
                print("[VOICE] Piper installed but no voice models found in ~/.eidos/piper_voices/")

        except ImportError:
            print("[VOICE] Piper not installed")

    def _get_voice(self, language: str = None):
        """Get the best voice for the language."""
        lang = language or self.language
        # Try language-specific voice first
        for name, voice in self._voices.items():
            if name.startswith(lang[:2]):
                return voice
        # Fallback to any voice
        if self._voices:
            return next(iter(self._voices.values()))
        return None

    def speak(self, text: str, save_path: Optional[Path] = None,
              language: str = None) -> Optional[Path]:
        """Generate speech from text.

        Args:
            text: Text to speak
            save_path: Where to save (auto-generated if None)
            language: "es" or "en" (auto-detect if None)

        Returns:
            Path to WAV file, or None on failure
        """
        if not self.piper_available:
            return None

        voice = self._get_voice(language)
        if not voice:
            return None

        # Cache check
        text_hash = hashlib.md5(text.encode()).hexdigest()[:12]
        if save_path is None:
            save_path = self.cache_dir / f"piper_{text_hash}.wav"

        if save_path.exists():
            return save_path

        try:
            import wave
            audio_bytes = b""
            sr = 22050
            for chunk in voice.synthesize(text):
                audio_bytes += chunk.audio_int16_bytes
                sr = chunk.sample_rate
            with wave.open(str(save_path), "wb") as wf:
                wf.setnchannels(1)
                wf.setsampwidth(2)
                wf.setframerate(sr)
                wf.writeframes(audio_bytes)
            return save_path

        except Exception as e:
            print(f"[VOICE] Piper synthesis error: {e}")
            return None

    def speak_and_play(self, text: str, language: str = None) -> Optional[Path]:
        """Generate and immediately play audio."""
        path = self.speak(text, language=language)
        if path:
            self.play_audio(path)
        return path

    @staticmethod
    def play_audio(path: Path):
        """Play audio file using system player."""
        import subprocess
        try:
            for player in ["aplay", "paplay", "ffplay -nodisp -autoexit"]:
                parts = player.split()
                try:
                    subprocess.run(
                        [*parts, str(path)],
                        capture_output=True, timeout=30,
                    )
                    return
                except FileNotFoundError:
                    continue
        except Exception:
            pass  # error no crítico, continuar
    def list_voices(self) -> list[str]:
        return list(self._voices.keys())

    def speak_as(self, text: str, voice_name: str,
                 save_path: Optional[Path] = None) -> Optional[Path]:
        """Generate speech using a specific voice by name.

        Args:
            text: Text to speak
            voice_name: Key from VOICES dict (e.g. "es_davefx", "en_lessac")
            save_path: Where to save (auto-generated if None)

        Returns:
            Path to WAV file, or None on failure
        """
        if not self.piper_available:
            return None

        voice = self._voices.get(voice_name)
        if not voice:
            # Fallback to language-based selection
            return self.speak(text, save_path=save_path, language=voice_name[:2])

        text_hash = hashlib.md5(f"{voice_name}:{text}".encode()).hexdigest()[:12]
        if save_path is None:
            save_path = self.cache_dir / f"piper_{voice_name}_{text_hash}.wav"

        if save_path.exists():
            return save_path

        try:
            import wave
            audio_bytes = b""
            sr = 22050
            for chunk in voice.synthesize(text):
                audio_bytes += chunk.audio_int16_bytes
                sr = chunk.sample_rate
            with wave.open(str(save_path), "wb") as wf:
                wf.setnchannels(1)
                wf.setsampwidth(2)
                wf.setframerate(sr)
                wf.writeframes(audio_bytes)
            return save_path
        except Exception as e:
            print(f"[VOICE] Piper speak_as error ({voice_name}): {e}")
            return None

    def speak_dialogue(self, segments: list[dict],
                       output_path: Optional[Path] = None,
                       pause_ms: int = 400) -> Optional[Path]:
        """Generate multi-voice dialogue from segments.

        Each segment: {"character": "narrator", "voice": "es_davefx", "text": "..."}
        Concatenates all segments into a single WAV with pauses between.

        Args:
            segments: List of dicts with character/voice/text
            output_path: Where to save final concatenated audio
            pause_ms: Milliseconds of silence between segments

        Returns:
            Path to concatenated WAV file
        """
        if not self.piper_available:
            return None

        if not segments:
            return None

        import wave
        import struct

        audio_parts = []
        sample_rate = 22050

        for seg in segments:
            voice_name = seg.get("voice", "es_davefx")
            text = seg.get("text", "")
            if not text:
                continue

            voice = self._voices.get(voice_name)
            if not voice:
                # Fallback
                voice = next(iter(self._voices.values()), None)
            if not voice:
                continue

            try:
                seg_bytes = b""
                for chunk in voice.synthesize(text):
                    seg_bytes += chunk.audio_int16_bytes
                    sample_rate = chunk.sample_rate
                audio_parts.append(seg_bytes)

                # Add silence between segments
                silence_samples = int(sample_rate * pause_ms / 1000)
                silence = struct.pack(f"<{silence_samples}h", *([0] * silence_samples))
                audio_parts.append(silence)
            except Exception as e:
                print(f"[VOICE] Segment error ({voice_name}): {e}")
                continue

        if not audio_parts:
            return None

        # Concatenate all audio
        all_audio = b"".join(audio_parts)

        if output_path is None:
            ts = int(time.time())
            output_path = self.cache_dir / f"dialogue_{ts}.wav"

        try:
            with wave.open(str(output_path), "wb") as wf:
                wf.setnchannels(1)
                wf.setsampwidth(2)
                wf.setframerate(sample_rate)
                wf.writeframes(all_audio)
            return output_path
        except Exception as e:
            print(f"[VOICE] Dialogue write error: {e}")
            return None


# ============================
# BARK VOICE ENGINE (LEGACY)
# ============================

class BarkVoiceEngine:
    """
    Motor de voz usando Bark.

    Bark es un modelo de TTS (text-to-speech) transformer que genera
    audio ultra-realista con expresiones naturales.
    """

    def __init__(self, use_small_model: bool = True):
        self.config = get_config()
        self.use_small_model = use_small_model
        self.bark_available = False
        self.model = None

        # Directorio de caché
        self.cache_dir = Path(self.config.paths.home) / "voice_cache"
        self.cache_dir.mkdir(exist_ok=True)

        # Intentar cargar Bark
        self._load_bark()

    def _load_bark(self):
        """Carga Bark si está disponible"""
        try:
            # Intentar importar
            from bark import SAMPLE_RATE, generate_audio, preload_models

            self.SAMPLE_RATE = SAMPLE_RATE
            self.generate_audio = generate_audio
            self.preload_models = preload_models

            print("[VOICE] ✅ Bark encontrado")
            self.bark_available = True

            # Precargar modelos en background
            if self.use_small_model:
                os.environ["SUNO_USE_SMALL_MODELS"] = "1"
                print("[VOICE] 📦 Usando modelo pequeño (optimizado para RAM)")

            # Preload en background para no bloquear
            print("[VOICE] ⏳ Precargando modelos de voz...")
            try:
                self.preload_models()
                print("[VOICE] ✅ Modelos cargados")
            except Exception as e:
                print(f"[VOICE] ⚠️  Error precargando modelos: {e}")

        except ImportError:
            print("[VOICE] ⚠️  Bark no está instalado")
            print("[VOICE] 💡 Para instalar: pip install git+https://github.com/suno-ai/bark.git")
            self.bark_available = False

    def speak(
        self,
        text: str,
        speaker: Optional[str] = None,
        temperature: float = 0.7,
        save_path: Optional[Path] = None
    ) -> Optional[Path]:
        """
        Genera audio desde texto.

        Args:
            text: Texto a convertir en voz
            speaker: ID del speaker (ej: "v2/es_speaker_6")
            temperature: Creatividad (0-1)
            save_path: Dónde guardar el audio

        Returns:
            Path al archivo de audio generado, o None si falla
        """
        if not self.bark_available:
            print(f"[VOICE] ⚠️  Bark no disponible. Texto: {text[:50]}...")
            return None

        # Usar speaker por defecto si no se especifica
        if speaker is None:
            speaker = self.config.voice.speaker

        # Generar hash del texto para caché
        text_hash = hashlib.md5(text.encode()).hexdigest()

        # Determinar path de salida
        if save_path is None:
            save_path = self.cache_dir / f"voice_{text_hash}.wav"

        # Si ya existe en caché, retornar
        if save_path.exists():
            print(f"[VOICE] 📦 Usando caché: {save_path.name}")
            return save_path

        try:
            print(f"[VOICE] 🎤 Generando voz: {text[:50]}...")

            # Preparar prompt con speaker
            prompt = f"[{speaker}] {text}"

            # Generar audio
            audio_array = self.generate_audio(
                prompt,
                history_prompt=speaker,
                text_temp=temperature
            )

            # Guardar audio
            self._save_audio(audio_array, save_path)

            print(f"[VOICE] ✅ Audio generado: {save_path.name}")
            return save_path

        except Exception as e:
            print(f"[VOICE] ❌ Error generando voz: {e}")
            return None

    def _save_audio(self, audio_array, path: Path):
        """Guarda array de audio a archivo WAV"""
        try:
            from scipy.io.wavfile import write as write_wav
            write_wav(str(path), self.SAMPLE_RATE, audio_array)
        except ImportError:
            # Si scipy no está disponible, intentar con pydub
            try:
                import numpy as np
                from pydub import AudioSegment

                # Convertir a int16
                audio_int16 = (audio_array * 32767).astype(np.int16)

                # Crear AudioSegment
                audio_segment = AudioSegment(
                    audio_int16.tobytes(),
                    frame_rate=self.SAMPLE_RATE,
                    sample_width=2,
                    channels=1
                )

                # Exportar
                audio_segment.export(str(path), format="wav")
            except Exception:
                raise Exception("Necesitas scipy o pydub para guardar audio")

    def speak_list(
        self,
        texts: list[str],
        speaker: Optional[str] = None,
        pause_between: float = 0.5
    ) -> list[Path]:
        """
        Genera múltiples audios con pausas entre ellos.

        Args:
            texts: Lista de textos
            speaker: Speaker a usar
            pause_between: Segundos de pausa entre textos

        Returns:
            Lista de paths a archivos de audio
        """
        audio_files = []

        for i, text in enumerate(texts):
            print(f"[VOICE] 🎤 Generando {i+1}/{len(texts)}...")

            audio_path = self.speak(text, speaker=speaker)
            if audio_path:
                audio_files.append(audio_path)

            # Pausa entre generaciones (para no saturar RAM)
            if i < len(texts) - 1:
                time.sleep(pause_between)

        return audio_files

    def clear_cache(self):
        """Limpia el caché de audio"""
        try:
            import shutil
            shutil.rmtree(self.cache_dir)
            self.cache_dir.mkdir()
            print("[VOICE] 🗑️  Caché de voz limpiado")
        except Exception as e:
            print(f"[VOICE] ⚠️  Error limpiando caché: {e}")

# ============================
# FALLBACK - TTS SIMPLE
# ============================

class SimpleTTS:
    """
    Sistema TTS fallback usando pyttsx3 (offline, sin instalación).
    Se usa si Bark no está disponible.
    """

    def __init__(self):
        self.engine = None
        self._load_pyttsx3()

    def _load_pyttsx3(self):
        """Intenta cargar pyttsx3"""
        try:
            import pyttsx3
            self.engine = pyttsx3.init()

            # Configurar para español
            voices = self.engine.getProperty('voices')
            for voice in voices:
                if 'spanish' in voice.name.lower() or 'es' in voice.languages:
                    self.engine.setProperty('voice', voice.id)
                    break

            # Configurar velocidad
            self.engine.setProperty('rate', 150)

            print("[VOICE] ✅ pyttsx3 (fallback TTS) cargado")

        except ImportError:
            print("[VOICE] ⚠️  pyttsx3 no disponible")
            print("[VOICE] 💡 Para instalar: pip install pyttsx3")

    def speak(self, text: str, save_path: Optional[Path] = None) -> Optional[Path]:
        """Genera voz simple"""
        if self.engine is None:
            print(f"[VOICE] ⚠️  TTS no disponible. Texto: {text[:50]}...")
            return None

        try:
            if save_path:
                self.engine.save_to_file(text, str(save_path))
                self.engine.runAndWait()
                return save_path
            else:
                self.engine.say(text)
                self.engine.runAndWait()
                return None
        except Exception as e:
            print(f"[VOICE] ❌ Error en TTS: {e}")
            return None

# ============================
# UNIFIED VOICE SYSTEM
# ============================

class VoiceSystem:
    """
    Sistema de voz unificado. Prioridad: Piper > Bark > pyttsx3.
    """

    def __init__(self):
        self.config = get_config()
        self.engine = None

        # 1. Intentar Piper primero (rapido, CPU-friendly)
        piper = PiperVoiceEngine(
            language=getattr(self.config.voice, 'language', 'es')
        )
        if piper.piper_available:
            self.engine = piper
            self.engine_type = "piper"
            print("[VOICE] Usando Piper TTS (fast, natural)")
            return

        # 2. Intentar Bark (lento pero ultra-realista)
        if self.config.voice.enabled:
            bark = BarkVoiceEngine(use_small_model=self.config.voice.use_small)
            if bark.bark_available:
                self.engine = bark
                self.engine_type = "bark"
                print("[VOICE] Usando Bark (ultra-realistic, slow)")
                return

        # 3. Fallback a pyttsx3
        simple = SimpleTTS()
        if simple.engine:
            self.engine = simple
            self.engine_type = "pyttsx3"
            print("[VOICE] Usando pyttsx3 (fallback)")
            return

        # Sin TTS disponible
        self.engine_type = "none"
        print("[VOICE] Sistema de voz no disponible")

    def speak(self, text: str, save_to_file: bool = False,
              language: str = None, play: bool = False) -> Optional[Path]:
        """
        Habla el texto dado.

        Args:
            text: Texto a hablar
            save_to_file: Si True, guarda a archivo
            language: "es" o "en" (auto-detect si None)
            play: Si True, reproduce el audio inmediatamente

        Returns:
            Path al archivo si save_to_file=True, None si solo reproduce
        """
        if self.engine is None:
            print(f"[VOICE] Sin engine. Texto: {text[:50]}...")
            return None

        # Piper path (primary)
        if self.engine_type == "piper":
            if play:
                return self.engine.speak_and_play(text, language=language)
            elif save_to_file:
                timestamp = int(time.time())
                filename = f"speech_{timestamp}.wav"
                save_path = Path(self.config.voice.output_dir) / filename
                Path(self.config.voice.output_dir).mkdir(exist_ok=True)
                return self.engine.speak(text, save_path=save_path, language=language)
            else:
                return self.engine.speak(text, language=language)

        # Bark/pyttsx3 path (legacy)
        if save_to_file:
            timestamp = int(time.time())
            filename = f"speech_{timestamp}.wav"
            save_path = Path(self.config.voice.output_dir) / filename
            Path(self.config.voice.output_dir).mkdir(exist_ok=True)
            return self.engine.speak(text, save_path=save_path)
        else:
            return self.engine.speak(text)

    def is_available(self) -> bool:
        """Verifica si hay TTS disponible"""
        return self.engine is not None

# ============================
# SINGLETON
# ============================

_voice_system: Optional[VoiceSystem] = None

def get_voice_system() -> VoiceSystem:
    """Obtiene instancia singleton del sistema de voz"""
    global _voice_system
    if _voice_system is None:
        _voice_system = VoiceSystem()
    return _voice_system

# ============================
# TESTING
# ============================

if __name__ == "__main__":
    print("=== EIDOS Voice System Test ===\n")

    voice = get_voice_system()

    print(f"\nTipo de engine: {voice.engine_type}")
    print(f"Disponible: {voice.is_available()}")

    if voice.is_available():
        print("\n🎤 Test de voz...")

        # Test simple
        test_text = "Hola, soy EIDOS. Sistema de inteligencia artificial autónomo."

        print(f"Texto: {test_text}")

        # Guardar a archivo
        audio_file = voice.speak(test_text, save_to_file=True)

        if audio_file:
            print(f"✅ Audio guardado: {audio_file}")
        else:
            print("⚠️  No se pudo generar audio")
    else:
        print("\n⚠️  Sistema de voz no disponible")
        print("💡 Instala Bark o pyttsx3 para usar TTS")

    print("\n🎯 Test completado")
