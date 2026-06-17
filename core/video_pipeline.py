"""
EIDOS Auto-Video Generation Pipeline
====================================
Pipeline completo para generar videos automáticamente.

Features:
- Generación de contenido visual (math art)
- Síntesis de voz (narración)
- Combinación de video + audio
- Optimizado para facelessreels
- Generación en batch
"""
from __future__ import annotations

import sys
import time
import subprocess
from pathlib import Path
from typing import Optional, List, Dict, Any
from dataclasses import dataclass
from datetime import datetime

# Añadir root al path
EIDOS_ROOT = Path(__file__).parent.parent.parent
sys.path.insert(0, str(EIDOS_ROOT))

from core.eidos_config import get_config

# ============================
# VIDEO SPEC
# ============================

@dataclass
class DialogueSegment:
    """Segmento de diálogo para multi-voz"""
    character: str = "narrator"
    voice: str = "es_davefx"       # Piper voice name
    text: str = ""

@dataclass
class VideoSpec:
    """Especificación para generar un video"""
    title: str
    narration: str  # Texto que se convertirá en voz
    duration_seconds: int = 30
    fps: int = 60
    resolution: tuple = (1920, 1080)  # width, height
    style: str = "math_art"  # math_art, geometric, abstract
    background_music: Optional[Path] = None
    dialogue: Optional[List[DialogueSegment]] = None  # Multi-voz segments

@dataclass
class GeneratedVideo:
    """Video generado"""
    spec: VideoSpec
    video_path: Path
    audio_path: Optional[Path]
    final_path: Path
    generated_at: float
    duration_actual: float
    file_size_mb: float

# ============================
# VIDEO PIPELINE
# ============================

class VideoPipeline:
    """
    Pipeline completo de generación de videos.

    Pasos:
    1. Generar contenido visual
    2. Generar narración de voz
    3. Combinar video + audio
    4. Optimizar para plataforma
    """

    def __init__(self):
        self.config = get_config()
        self.output_dir = Path(self.config.paths.videos)
        self.output_dir.mkdir(exist_ok=True)

        # Verificar dependencias
        self._check_dependencies()

    def _check_dependencies(self):
        """Verifica que las dependencias estén disponibles"""
        self.deps = {
            "ffmpeg": self._command_exists("ffmpeg"),
            "math_art": self._module_exists("core.math_art"),
            "voice": self._module_exists("core.voice_system"),
        }

        for dep, available in self.deps.items():
            if available:
                print(f"[PIPELINE] ✅ {dep} disponible")
            else:
                print(f"[PIPELINE] ⚠️  {dep} no disponible")

    def _command_exists(self, command: str) -> bool:
        """Verifica si comando existe"""
        try:
            subprocess.run(["which", command], capture_output=True, check=True)
            return True
        except Exception:
            return False

    def _module_exists(self, module_name: str) -> bool:
        """Verifica si módulo existe"""
        try:
            __import__(module_name)
            return True
        except Exception:
            return False

    def generate_video(self, spec: VideoSpec) -> Optional[GeneratedVideo]:
        """
        Genera un video completo según especificación.

        Args:
            spec: VideoSpec con configuración del video

        Returns:
            GeneratedVideo con paths y metadata
        """
        print(f"[PIPELINE] 🎬 Generando video: {spec.title}")
        start_time = time.time()

        try:
            # Paso 1: Generar contenido visual
            print(f"[PIPELINE] 📹 Paso 1/3: Generando contenido visual...")
            visual_path = self._generate_visual(spec)
            if not visual_path:
                print(f"[PIPELINE] ❌ Error generando visual")
                return None

            # Paso 2: Generar narración
            print(f"[PIPELINE] 🎤 Paso 2/3: Generando narración...")
            audio_path = self._generate_narration(spec)
            # Audio es opcional
            if audio_path:
                print(f"[PIPELINE] ✅ Narración generada")
            else:
                print(f"[PIPELINE] ⚠️  Sin narración (continuando sin audio)")

            # Paso 3: Combinar video + audio
            print(f"[PIPELINE] 🔧 Paso 3/3: Combinando video y audio...")
            final_path = self._combine_video_audio(visual_path, audio_path, spec)
            if not final_path:
                # Si falla la combinación, usar solo video
                final_path = visual_path

            # Calcular metadata
            duration = time.time() - start_time
            file_size = final_path.stat().st_size / (1024 * 1024)  # MB

            result = GeneratedVideo(
                spec=spec,
                video_path=visual_path,
                audio_path=audio_path,
                final_path=final_path,
                generated_at=time.time(),
                duration_actual=duration,
                file_size_mb=file_size
            )

            print(f"[PIPELINE] ✅ Video completado en {duration:.1f}s")
            print(f"[PIPELINE]    Path: {final_path}")
            print(f"[PIPELINE]    Tamaño: {file_size:.1f} MB")

            return result

        except Exception as e:
            print(f"[PIPELINE] ❌ Error en pipeline: {e}")
            import traceback
            traceback.print_exc()
            return None

    def _generate_visual(self, spec: VideoSpec) -> Optional[Path]:
        """Genera contenido visual"""
        try:
            if spec.style == "math_art":
                # Usar vision_60fps que ya funciona
                from core.vision_60fps import generate_video_60fps

                # Generar video
                video_path = generate_video_60fps(
                    prompt=spec.title,
                    duration_s=spec.duration_seconds
                )

                if video_path:
                    print(f"[PIPELINE] ✅ Visual generado: {video_path}")
                    return Path(video_path)
                else:
                    print(f"[PIPELINE] ⚠️  No se pudo generar visual")
                    return None

            else:
                print(f"[PIPELINE] ⚠️  Estilo '{spec.style}' no soportado")
                return None

        except Exception as e:
            print(f"[PIPELINE] ❌ Error generando visual: {e}")
            import traceback
            traceback.print_exc()
            return None

    def _generate_narration(self, spec: VideoSpec) -> Optional[Path]:
        """Genera narración de voz. Soporta multi-voz si spec.dialogue existe."""
        if not spec.narration and not spec.dialogue:
            return None

        try:
            from core.voice_system import get_voice_system

            voice = get_voice_system()

            if not voice.is_available():
                print(f"[PIPELINE] ⚠️  Sistema de voz no disponible")
                return None

            # Multi-voz: usar dialogue segments
            if spec.dialogue and hasattr(voice, 'engine') and hasattr(voice.engine, 'speak_dialogue'):
                segments = [
                    {"character": seg.character, "voice": seg.voice, "text": seg.text}
                    for seg in spec.dialogue
                ]
                ts = int(time.time())
                out = Path(self.output_dir) / f"dialogue_{ts}.wav"
                audio_path = voice.engine.speak_dialogue(segments, output_path=out)
                if audio_path:
                    print(f"[PIPELINE] 🎭 Multi-voice narration: {len(segments)} segments")
                    return audio_path

            # Single voice fallback
            audio_path = voice.speak(spec.narration, save_to_file=True)
            return audio_path

        except Exception as e:
            print(f"[PIPELINE] ⚠️  Error generando narración: {e}")
            return None

    def _combine_video_audio(
        self,
        video_path: Path,
        audio_path: Optional[Path],
        spec: VideoSpec
    ) -> Optional[Path]:
        """Combina video y audio usando FFmpeg"""
        if not audio_path:
            return video_path  # Solo video, sin audio

        if not self.deps["ffmpeg"]:
            print(f"[PIPELINE] ⚠️  FFmpeg no disponible, retornando video sin audio")
            return video_path

        try:
            # Path de salida
            timestamp = int(time.time())
            output_name = f"{spec.title.replace(' ', '_')}_{timestamp}_final.mp4"
            output_path = self.output_dir / output_name

            # Comando FFmpeg para combinar
            cmd = [
                "ffmpeg",
                "-i", str(video_path),      # Input video
                "-i", str(audio_path),       # Input audio
                "-c:v", "copy",              # Copy video codec (no re-encode)
                "-c:a", "aac",               # Audio codec
                "-shortest",                 # Cortar al más corto
                "-y",                        # Overwrite
                str(output_path)
            ]

            result = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                timeout=120
            )

            if result.returncode == 0 and output_path.exists():
                print(f"[PIPELINE] ✅ Video y audio combinados")
                return output_path
            else:
                print(f"[PIPELINE] ⚠️  Error en FFmpeg: {result.stderr[:200]}")
                return video_path

        except Exception as e:
            print(f"[PIPELINE] ⚠️  Error combinando: {e}")
            return video_path

    def generate_batch(
        self,
        specs: List[VideoSpec],
        delay_between: int = 30
    ) -> List[GeneratedVideo]:
        """
        Genera múltiples videos en batch.

        Args:
            specs: Lista de VideoSpec
            delay_between: Segundos de espera entre videos

        Returns:
            Lista de GeneratedVideo
        """
        print(f"[PIPELINE] 🎬 Generando batch de {len(specs)} videos")

        results = []

        for i, spec in enumerate(specs, 1):
            print(f"\n[PIPELINE] ═══ Video {i}/{len(specs)} ═══")

            result = self.generate_video(spec)
            if result:
                results.append(result)

            # Esperar entre videos (para no saturar)
            if i < len(specs):
                print(f"[PIPELINE] ⏸️  Esperando {delay_between}s antes del siguiente...")
                time.sleep(delay_between)

        print(f"\n[PIPELINE] ✅ Batch completado: {len(results)}/{len(specs)} exitosos")
        return results

# ============================
# CONTENT GENERATOR
# ============================

class ContentGenerator:
    """Genera ideas de contenido para videos"""

    @staticmethod
    def generate_facelessreel_ideas(count: int = 5) -> List[VideoSpec]:
        """
        Genera ideas para facelessreels (sin rostro).

        Temas: motivación, datos interesantes, filosofía, ciencia
        """
        ideas = [
            VideoSpec(
                title="Mente Infinita",
                narration="La mente humana procesa 70,000 pensamientos al día. ¿Cuántos de ellos son realmente útiles?",
                duration_seconds=30,
                style="math_art"
            ),
            VideoSpec(
                title="El Poder del Silencio",
                narration="En el silencio encontramos las respuestas que el ruido del mundo nos oculta.",
                duration_seconds=25,
                style="math_art"
            ),
            VideoSpec(
                title="Expansión del Universo",
                narration="El universo se expande a 73 kilómetros por segundo por cada megaparsec. Estamos en constante movimiento hacia lo desconocido.",
                duration_seconds=35,
                style="math_art"
            ),
            VideoSpec(
                title="Inteligencia Artificial",
                narration="La IA no reemplazará a los humanos. Amplificará nuestras capacidades y liberará nuestro potencial creativo.",
                duration_seconds=30,
                style="math_art"
            ),
            VideoSpec(
                title="Aprendizaje Continuo",
                narration="Cada día es una oportunidad para aprender algo nuevo. El conocimiento es el único recurso que crece al compartirse.",
                duration_seconds=28,
                style="math_art"
            )
        ]

        return ideas[:count]

# ============================
# SINGLETON
# ============================

_video_pipeline: Optional[VideoPipeline] = None

def get_video_pipeline() -> VideoPipeline:
    """Obtiene instancia singleton"""
    global _video_pipeline
    if _video_pipeline is None:
        _video_pipeline = VideoPipeline()
    return _video_pipeline

# ============================
# TESTING
# ============================

if __name__ == "__main__":
    print("=== EIDOS Video Pipeline Test ===\n")

    pipeline = get_video_pipeline()

    # Generar una idea de contenido
    ideas = ContentGenerator.generate_facelessreel_ideas(count=1)
    spec = ideas[0]

    print(f"\n📋 Spec del video:")
    print(f"   Título: {spec.title}")
    print(f"   Narración: {spec.narration}")
    print(f"   Duración: {spec.duration_seconds}s")
    print(f"   FPS: {spec.fps}")

    # Generar video
    print(f"\n🎬 Generando video...")
    result = pipeline.generate_video(spec)

    if result:
        print(f"\n✅ Video generado exitosamente!")
        print(f"   Path final: {result.final_path}")
        print(f"   Tamaño: {result.file_size_mb:.1f} MB")
        print(f"   Tiempo de generación: {result.duration_actual:.1f}s")
    else:
        print(f"\n❌ Error generando video")

    print("\n🎯 Test completado")
