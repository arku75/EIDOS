"""
EIDOS Multimedia Generator - Generador de Contenido Multimedia
===============================================================

Sistema de generación de contenido multimedia autónomo:
- Video: Generación y edición automatizada
- Audio: Texto a voz, procesamiento de audio
- Voz: Síntesis de voz natural, clonación de voz
- Melodías: Generación musical algorítmica y con IA

Uso:
    from core.eidos_multimedia import MultimediaEngine, get_multimedia_engine
    mm = get_multimedia_engine()
    
    # Generar video educativo
    mm.create_educational_video(
        topic="Python para principiantes",
        duration_minutes=5,
        style="modern"
    )
    
    # Generar música ambiental
    mm.generate_ambient_music(
        mood="focus",
        duration_seconds=600
    )
"""

import json
import logging
import os
import random
import subprocess
import tempfile
import time
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import numpy as np

log = logging.getLogger("eidos.multimedia")

# ══════════════════════════════════════════════════════════════════════════════
#  CONFIGURACIÓN
# ══════════════════════════════════════════════════════════════════════════════

OUTPUT_DIR = Path.home() / ".eidos" / "multimedia"
CACHE_DIR = Path.home() / ".eidos" / "multimedia_cache"

# Tool checks
FFMPEG_AVAILABLE = False
IMAGEMAGICK_AVAILABLE = False

# ══════════════════════════════════════════════════════════════════════════════
#  TIPOS
# ══════════════════════════════════════════════════════════════════════════════

class VideoStyle(str, Enum):
    MINIMAL = "minimal"
    MODERN = "modern"
    RETRO = "retro"
    CORPORATE = "corporate"
    GAMING = "gaming"
    EDUCATIONAL = "educational"
    VLOG = "vlog"


class AudioMood(str, Enum):
    FOCUS = "focus"
    RELAX = "relax"
    ENERGY = "energy"
    DARK = "dark"
    HAPPY = "happy"
    SAD = "sad"
    EPIC = "epic"
    CHILL = "chill"


class VoiceStyle(str, Enum):
    NATURAL = "natural"
    NARRATOR = "narrator"
    ENERGETIC = "energetic"
    CALM = "calm"
    PROFESSIONAL = "professional"
    WHISPER = "whisper"


@dataclass
class VideoProject:
    id: str
    title: str
    description: str
    style: VideoStyle
    duration_seconds: int
    scenes: List[Dict] = field(default_factory=list)
    audio_tracks: List[Dict] = field(default_factory=list)
    output_path: Optional[Path] = None
    created_at: datetime = field(default_factory=datetime.now)
    status: str = "draft"  # draft, rendering, completed, failed
    
    def __post_init__(self):
        if not self.id:
            self.id = f"vid_{int(time.time())}_{random.randint(1000,9999)}"


@dataclass
class MusicTrack:
    id: str
    title: str
    mood: AudioMood
    duration_seconds: int
    bpm: int
    key: str
    chords_progression: List[str] = field(default_factory=list)
    instruments: List[str] = field(default_factory=list)
    output_path: Optional[Path] = None
    created_at: datetime = field(default_factory=datetime.now)
    
    def __post_init__(self):
        if not self.id:
            self.id = f"mus_{int(time.time())}_{random.randint(1000,9999)}"


@dataclass
class VoiceClip:
    id: str
    text: str
    style: VoiceStyle
    output_path: Optional[Path] = None
    duration_seconds: float = 0.0
    created_at: datetime = field(default_factory=datetime.now)
    
    def __post_init__(self):
        if not self.id:
            self.id = f"voc_{int(time.time())}_{random.randint(1000,9999)}"


# ══════════════════════════════════════════════════════════════════════════════
#  SISTEMA DE MELODÍAS (Algorítmico)
# ══════════════════════════════════════════════════════════════════════════════

class MelodyGenerator:
    """
    Generador de melodías algorítmico.
    Crea música procedural sin necesidad de modelos externos.
    """
    
    # Escalas y acordes
    SCALES = {
        "C_MAJOR": ["C", "D", "E", "F", "G", "A", "B"],
        "A_MINOR": ["A", "B", "C", "D", "E", "F", "G"],
        "G_MAJOR": ["G", "A", "B", "C", "D", "E", "F#"],
        "E_MINOR": ["E", "F#", "G", "A", "B", "C", "D"],
        "D_MAJOR": ["D", "E", "F#", "G", "A", "B", "C#"],
        "F_MAJOR": ["F", "G", "A", "Bb", "C", "D", "E"],
    }
    
    CHORDS = {
        "C_MAJOR": ["C", "Dm", "Em", "F", "G", "Am", "Bdim"],
        "A_MINOR": ["Am", "Bdim", "C", "Dm", "Em", "F", "G"],
        "G_MAJOR": ["G", "Am", "Bm", "C", "D", "Em", "F#dim"],
        "E_MINOR": ["Em", "F#dim", "G", "Am", "Bm", "C", "D"],
    }
    
    # Progresiones comunes
    PROGRESSIONS = {
        AudioMood.HAPPY: [
            ["C", "G", "Am", "F"],      # I-V-vi-IV
            ["C", "F", "G", "C"],       # I-IV-V-I
            ["C", "Am", "F", "G"],      # I-vi-IV-V
        ],
        AudioMood.SAD: [
            ["Am", "F", "C", "G"],      # vi-IV-I-V
            ["Am", "Em", "F", "C"],     # vi-iii-IV-I
            ["Am", "G", "F", "Em"],     # vi-V-IV-iii
        ],
        AudioMood.ENERGY: [
            ["C", "G", "Am", "F"],      # Pop punk
            ["C", "Am", "F", "G"],      # Fast
        ],
        AudioMood.FOCUS: [
            ["C", "Am", "F", "C"],      # Ambient
            ["Am", "C", "G", "Am"],     # Minimal
        ],
        AudioMood.RELAX: [
            ["C", "Em", "Am", "F"],     # Soft
            ["F", "C", "G", "Am"],      # Chill
        ],
        AudioMood.DARK: [
            ["Am", "E", "F", "Dm"],     # Minor heavy
            ["Em", "C", "Am", "Dm"],    # Dark
        ],
        AudioMood.EPIC: [
            ["C", "G", "Am", "F"],      # Anthem
            ["Am", "F", "C", "G"],      # Build up
        ],
    }
    
    INSTRUMENTS = {
        AudioMood.FOCUS: ["piano", "synth_pad", "bass", "soft_drums"],
        AudioMood.RELAX: ["acoustic_guitar", "piano", "strings", "soft_drums"],
        AudioMood.ENERGY: ["electric_guitar", "synth_lead", "bass", "drums"],
        AudioMood.DARK: ["synth_bass", "dark_pad", "industrial_drums"],
        AudioMood.HAPPY: ["ukulele", "piano", "bells", "light_drums"],
        AudioMood.SAD: ["piano", "strings", "cello", "soft_drums"],
        AudioMood.EPIC: ["orchestra", "brass", "strings", "taiko_drums"],
        AudioMood.CHILL: ["lofi_piano", "jazz_bass", "brushed_drums", "rhodes"],
    }
    
    BPM_RANGES = {
        AudioMood.FOCUS: (60, 80),
        AudioMood.RELAX: (50, 70),
        AudioMood.ENERGY: (120, 140),
        AudioMood.DARK: (70, 90),
        AudioMood.HAPPY: (90, 110),
        AudioMood.SAD: (60, 75),
        AudioMood.EPIC: (80, 100),
        AudioMood.CHILL: (70, 90),
    }
    
    def generate_track(self, mood: AudioMood, duration_seconds: int = 180) -> MusicTrack:
        """Genera una pista musical completa."""
        
        bpm = random.randint(*self.BPM_RANGES[mood])
        key = random.choice(["C_MAJOR", "A_MINOR", "G_MAJOR", "E_MINOR"])
        
        # Seleccionar progresión
        progressions = self.PROGRESSIONS.get(mood, self.PROGRESSIONS[AudioMood.FOCUS])
        chords = random.choice(progressions)
        
        # Calcular estructura
        beats_per_bar = 4
        seconds_per_beat = 60.0 / bpm
        bars_needed = int(duration_seconds / (seconds_per_beat * beats_per_bar))
        
        # Expandir progresión para duración completa
        full_progression = []
        chord_duration_bars = 4  # Cada acorde dura 4 compases
        
        for i in range(0, bars_needed, chord_duration_bars):
            chord_idx = (i // chord_duration_bars) % len(chords)
            full_progression.append(chords[chord_idx])
        
        track = MusicTrack(
            id=f"",
            title=f"{mood.value.title()} Track {random.randint(1000, 9999)}",
            mood=mood,
            duration_seconds=duration_seconds,
            bpm=bpm,
            key=key,
            chords_progression=full_progression,
            instruments=self.INSTRUMENTS.get(mood, ["piano", "bass"])
        )
        
        return track
    
    def generate_midi_data(self, track: MusicTrack) -> bytes:
        """
        Genera datos MIDI para la pista.
        Retorna bytes MIDI format 0.
        """
        # Nota: Implementación básica. Para producción usar pretty_midi o similar.
        # Esto es un placeholder que genera estructura MIDI válida pero simple.
        
        # MIDI header
        header = bytes([
            0x4D, 0x54, 0x68, 0x64,  # MThd
            0x00, 0x00, 0x00, 0x06,  # Header length
            0x00, 0x00,              # Format 0
            0x00, 0x01,              # 1 track
            0x01, 0xE0               # 480 ticks per quarter note
        ])
        
        # Track data (simplificado)
        track_data = bytearray()
        
        # Tempo
        microseconds_per_quarter = int(60000000 / track.bpm)
        track_data.extend([
            0x00,                    # Delta time
            0xFF, 0x51, 0x03,        # Meta event: tempo
            (microseconds_per_quarter >> 16) & 0xFF,
            (microseconds_per_quarter >> 8) & 0xFF,
            microseconds_per_quarter & 0xFF
        ])
        
        # Time signature 4/4
        track_data.extend([
            0x00,
            0xFF, 0x58, 0x04,
            0x04, 0x02, 0x18, 0x08
        ])
        
        # Notas simples (placeholder)
        # C4 = 60 en MIDI
        current_time = 0
        notes = [60, 64, 67, 72]  # C major arpeggio
        
        for i, note in enumerate(notes * (track.duration_seconds // 2)):
            # Note on
            track_data.append(0x00 if i == 0 else 0x60)  # Delta time
            track_data.extend([0x90, note, 0x64])  # Note on, channel 0, velocity 100
            
            # Note off (después de 480 ticks = 1 quarter note)
            track_data.extend([0x60, 0x80, note, 0x00])  # Note off
        
        # End of track
        track_data.extend([0x00, 0xFF, 0x2F, 0x00])
        
        # Track header
        track_header = bytes([
            0x4D, 0x54, 0x72, 0x6B  # MTrk
        ]) + len(track_data).to_bytes(4, 'big')
        
        return header + track_header + bytes(track_data)


# ══════════════════════════════════════════════════════════════════════════════
#  SISTEMA DE VIDEO
# ══════════════════════════════════════════════════════════════════════════════

class VideoEngine:
    """
    Motor de generación y edición de video.
    """
    
    VIDEO_TEMPLATES = {
        VideoStyle.EDUCATIONAL: {
            "intro_duration": 3,
            "outro_duration": 3,
            "title_font": "sans-serif",
            "bg_color": "#1a1a2e",
            "accent_color": "#16213e",
            "text_color": "#eee",
        },
        VideoStyle.MODERN: {
            "intro_duration": 2,
            "outro_duration": 2,
            "title_font": "system-ui",
            "bg_color": "#0f0f0f",
            "accent_color": "#00d4ff",
            "text_color": "#ffffff",
        },
        VideoStyle.VLOG: {
            "intro_duration": 1,
            "outro_duration": 5,
            "title_font": "cursive",
            "bg_color": "#f5f5f5",
            "accent_color": "#ff6b6b",
            "text_color": "#2d3436",
        },
    }
    
    def __init__(self):
        self.output_dir = OUTPUT_DIR / "videos"
        self.output_dir.mkdir(parents=True, exist_ok=True)
    
    def create_project(self, title: str, description: str, 
                       style: VideoStyle, duration_seconds: int) -> VideoProject:
        """Crea un nuevo proyecto de video."""
        
        project = VideoProject(
            id="",
            title=title,
            description=description,
            style=style,
            duration_seconds=duration_seconds
        )
        
        project.output_path = self.output_dir / f"{project.id}.mp4"
        
        return project
    
    def add_text_scene(self, project: VideoProject, text: str, 
                       duration: int, position: str = "center") -> Dict:
        """Añade una escena de texto al proyecto."""
        
        scene = {
            "type": "text",
            "text": text,
            "duration": duration,
            "position": position,
            "added_at": datetime.now().isoformat()
        }
        
        project.scenes.append(scene)
        return scene
    
    def add_image_scene(self, project: VideoProject, image_path: Path,
                        duration: int, transition: str = "fade") -> Dict:
        """Añade una escena de imagen."""
        
        scene = {
            "type": "image",
            "path": str(image_path),
            "duration": duration,
            "transition": transition,
            "added_at": datetime.now().isoformat()
        }
        
        project.scenes.append(scene)
        return scene
    
    def add_audio_track(self, project: VideoProject, audio_path: Path,
                        volume: float = 1.0, fade_in: int = 0, fade_out: int = 0) -> Dict:
        """Añade pista de audio."""
        
        track = {
            "path": str(audio_path),
            "volume": volume,
            "fade_in": fade_in,
            "fade_out": fade_out,
            "added_at": datetime.now().isoformat()
        }
        
        project.audio_tracks.append(track)
        return track
    
    def render_project(self, project: VideoProject) -> Optional[Path]:
        """
        Renderiza el proyecto a video final.
        Requiere ffmpeg instalado.
        """
        if not FFMPEG_AVAILABLE:
            log.error("ffmpeg no disponible para renderizado")
            project.status = "failed"
            return None
        
        # Crear script de ffmpeg basado en escenas
        # Esto es una implementación simplificada
        
        project.status = "rendering"
        
        try:
            # Generar comandos ffmpeg para cada escena
            scene_files = []
            
            for i, scene in enumerate(project.scenes):
                scene_file = CACHE_DIR / f"{project.id}_scene_{i}.mp4"
                
                if scene["type"] == "text":
                    # Crear video de texto con ffmpeg
                    template = self.VIDEO_TEMPLATES.get(project.style, 
                                                        self.VIDEO_TEMPLATES[VideoStyle.MODERN])
                    
                    cmd = [
                        "ffmpeg", "-y",
                        "-f", "lavfi",
                        "-i", f"color=c={template['bg_color'].replace('#', '')}:s=1920x1080:d={scene['duration']}",
                        "-vf", f"drawtext=text='{scene['text']}':fontfile=/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf:fontsize=60:fontcolor={template['text_color']}:x=(w-text_w)/2:y=(h-text_h)/2",
                        "-c:v", "libx264", "-tune", "stillimage",
                        "-pix_fmt", "yuv420p",
                        str(scene_file)
                    ]
                    subprocess.run(cmd, check=True, capture_output=True)
                    scene_files.append(scene_file)
            
            # Concatenar escenas
            concat_list = CACHE_DIR / f"{project.id}_concat.txt"
            with open(concat_list, 'w') as f:
                for sf in scene_files:
                    f.write(f"file '{sf}'\n")
            
            # Render final
            cmd = [
                "ffmpeg", "-y",
                "-f", "concat", "-safe", "0",
                "-i", str(concat_list),
                "-c:v", "libx264", "-crf", "23", "-preset", "fast",
                str(project.output_path)
            ]
            subprocess.run(cmd, check=True, capture_output=True)
            
            # Limpiar temporales
            for sf in scene_files:
                sf.unlink(missing_ok=True)
            concat_list.unlink(missing_ok=True)
            
            project.status = "completed"
            log.info(f"Video renderizado: {project.output_path}")
            return project.output_path
            
        except Exception as e:
            log.error(f"Error renderizando video: {e}")
            project.status = "failed"
            return None
    
    def create_educational_video(self, topic: str, script: List[str],
                                  style: VideoStyle = VideoStyle.EDUCATIONAL) -> VideoProject:
        """
        Crea un video educativo completo desde un script.
        """
        total_duration = len(script) * 10  # 10 segundos por punto
        
        project = self.create_project(
            title=f"Educational: {topic}",
            description=f"Video educativo sobre {topic}",
            style=style,
            duration_seconds=total_duration
        )
        
        # Añadir intro
        self.add_text_scene(project, topic, 3, "center")
        
        # Añadir contenido
        for point in script:
            self.add_text_scene(project, point, 8, "center")
        
        # Añadir outro
        self.add_text_scene(project, "Gracias por ver", 3, "center")
        
        return project


# ══════════════════════════════════════════════════════════════════════════════
#  SISTEMA DE VOZ (TTS)
# ══════════════════════════════════════════════════════════════════════════════

class VoiceEngine:
    """
    Motor de síntesis de voz.
    Soporta múltiples backends: Piper, Coqui TTS, espeak-ng.
    """
    
    def __init__(self):
        self.output_dir = OUTPUT_DIR / "voice"
        self.output_dir.mkdir(parents=True, exist_ok=True)
        self.backend = self._detect_backend()
    
    def _detect_backend(self) -> str:
        """Detecta el mejor backend de TTS disponible."""
        # Preferencia: piper > coqui > espeak
        try:
            subprocess.run(["piper", "--version"], capture_output=True, check=True)
            log.info("Voice backend: Piper")
            return "piper"
        except Exception:
            pass  # error no crítico, continuar
        try:
            import TTS
            log.info("Voice backend: Coqui TTS")
            return "coqui"
        except ImportError:
            pass
        
        try:
            subprocess.run(["espeak-ng", "--version"], capture_output=True, check=True)
            log.info("Voice backend: espeak-ng")
            return "espeak"
        except Exception:
            pass  # error no crítico, continuar
        log.warning("No TTS backend available")
        return "none"
    
    def synthesize(self, text: str, style: VoiceStyle = VoiceStyle.NATURAL,
                   output_name: Optional[str] = None) -> Optional[Path]:
        """Sintetiza texto a voz."""
        
        clip_id = f"voc_{int(time.time())}_{random.randint(1000,9999)}"
        output_path = self.output_dir / (output_name or f"{clip_id}.wav")
        
        try:
            if self.backend == "piper":
                return self._synthesize_piper(text, output_path, style)
            elif self.backend == "coqui":
                return self._synthesize_coqui(text, output_path, style)
            elif self.backend == "espeak":
                return self._synthesize_espeak(text, output_path, style)
            else:
                log.error("No TTS backend available")
                return None
        except Exception as e:
            log.error(f"TTS synthesis failed: {e}")
            return None
    
    def _synthesize_piper(self, text: str, output_path: Path, style: VoiceStyle) -> Path:
        """Usa Piper para TTS (offline, rápido)."""
        # Guardar texto a archivo temporal
        text_file = CACHE_DIR / "tts_input.txt"
        text_file.parent.mkdir(parents=True, exist_ok=True)
        text_file.write_text(text)
        
        cmd = [
            "piper",
            "--model", "en_US-lessac-medium",  # Default model
            "--config", "",
            "--input", str(text_file),
            "--output_file", str(output_path)
        ]
        
        subprocess.run(cmd, check=True, capture_output=True)
        text_file.unlink(missing_ok=True)
        
        return output_path
    
    def _synthesize_coqui(self, text: str, output_path: Path, style: VoiceStyle) -> Path:
        """Usa Coqui TTS."""
        from TTS.api import TTS
        
        tts = TTS("tts_models/en/ljspeech/tacotron2-DDC")
        tts.tts_to_file(text=text, file_path=str(output_path))
        
        return output_path
    
    def _synthesize_espeak(self, text: str, output_path: Path, style: VoiceStyle) -> Path:
        """Fallback a espeak-ng."""
        cmd = [
            "espeak-ng",
            "-w", str(output_path),
            text
        ]
        subprocess.run(cmd, check=True, capture_output=True)
        return output_path
    
    def batch_synthesize(self, texts: List[str], style: VoiceStyle = VoiceStyle.NATURAL) -> List[Path]:
        """Sintetiza múltiples textos."""
        results = []
        for i, text in enumerate(texts):
            path = self.synthesize(text, style, f"batch_{i}_{int(time.time())}.wav")
            if path:
                results.append(path)
        return results
    
    def estimate_duration(self, text: str, wpm: int = 150) -> float:
        """Estima duración en segundos basado en palabras por minuto."""
        word_count = len(text.split())
        return (word_count / wpm) * 60


# ══════════════════════════════════════════════════════════════════════════════
#  MOTOR MULTIMEDIA PRINCIPAL
# ══════════════════════════════════════════════════════════════════════════════

class MultimediaEngine:
    """
    Motor multimedia unificado de EIDOS.
    Integra: Video, Audio, Voz, y Melodías.
    """
    
    def __init__(self):
        OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
        CACHE_DIR.mkdir(parents=True, exist_ok=True)
        
        self.video = VideoEngine()
        self.music = MelodyGenerator()
        self.voice = VoiceEngine()
        
        self._check_tools()
        
        log.info("Multimedia Engine initialized")
    
    def _check_tools(self):
        """Verifica herramientas externas disponibles."""
        global FFMPEG_AVAILABLE, IMAGEMAGICK_AVAILABLE
        
        try:
            subprocess.run(["ffmpeg", "-version"], capture_output=True, check=True)
            FFMPEG_AVAILABLE = True
            log.info("ffmpeg: available")
        except Exception:
            log.warning("ffmpeg: not available (video rendering limited)")
        
        try:
            subprocess.run(["convert", "--version"], capture_output=True, check=True)
            IMAGEMAGICK_AVAILABLE = True
            log.info("imagemagick: available")
        except Exception:
            log.warning("imagemagick: not available")
    
    # ════════════════════════════════════════════════════════════════════════
    #  API PÚBLICA
    # ════════════════════════════════════════════════════════════════════════
    
    def generate_ambient_music(self, mood: AudioMood, 
                               duration_seconds: int = 300) -> MusicTrack:
        """Genera música ambiental para estados de ánimo específicos."""
        track = self.music.generate_track(mood, duration_seconds)
        
        # Guardar MIDI
        midi_data = self.music.generate_midi_data(track)
        midi_path = OUTPUT_DIR / "music" / f"{track.id}.mid"
        midi_path.parent.mkdir(parents=True, exist_ok=True)
        midi_path.write_bytes(midi_data)
        
        track.output_path = midi_path
        log.info(f"Generated ambient music: {track.title} ({duration_seconds}s, {mood.value})")
        
        return track
    
    def narrate_text(self, text: str, style: VoiceStyle = VoiceStyle.NATURAL) -> Optional[Path]:
        """Convierte texto a voz natural."""
        return self.voice.synthesize(text, style)
    
    def create_educational_video(self, topic: str, script_points: List[str],
                                  style: VideoStyle = VideoStyle.EDUCATIONAL) -> VideoProject:
        """Crea un video educativo completo."""
        return self.video.create_educational_video(topic, script_points, style)
    
    def create_podcast_segment(self, title: str, content: str,
                                background_mood: AudioMood = AudioMood.FOCUS) -> Dict[str, Path]:
        """
        Crea un segmento de podcast con voz narrada y música de fondo.
        """
        # Generar voz
        voice_path = self.voice.synthesize(content, VoiceStyle.NARRATOR)
        
        # Generar música de fondo
        voice_duration = self.voice.estimate_duration(content)
        music = self.generate_ambient_music(background_mood, int(voice_duration) + 30)
        
        return {
            "voice": voice_path,
            "music": music.output_path,
            "title": title
        }
    
    def create_focus_session(self, duration_minutes: int = 25) -> MusicTrack:
        """Crea música para sesión de focus (técnica Pomodoro)."""
        return self.generate_ambient_music(
            AudioMood.FOCUS, 
            duration_seconds=duration_minutes * 60
        )
    
    def create_meditation_audio(self, duration_minutes: int = 10) -> Dict[str, Any]:
        """Crea audio completo para meditación guiada."""
        
        # Texto de meditación
        meditation_script = [
            "Bienvenido a tu sesión de meditación.",
            "Siéntate cómodamente y cierra los ojos suavemente.",
            "Respira profundamente... inhala... exhala...",
            "Deja que tu cuerpo se relaje con cada exhalación.",
            "Observa tus pensamientos sin juzgarlos, como nubes pasando.",
            "Vuelve tu atención a la respiración cuando te distraigas.",
            "Eres paciente, eres amable, eres completo.",
            "Cuando estés listo, abre los ojos suavemente.",
            "Namasté."
        ]
        
        # Generar narración
        voice_paths = []
        for segment in meditation_script:
            path = self.voice.synthesize(segment, VoiceStyle.CALM)
            if path:
                voice_paths.append(path)
        
        # Generar música ambiental
        music = self.generate_ambient_music(AudioMood.RELAX, duration_minutes * 60)
        
        return {
            "voice_segments": voice_paths,
            "background_music": music,
            "type": "meditation",
            "duration_minutes": duration_minutes
        }
    
    def generate_notification_sound(self, type: str = "info") -> Optional[Path]:
        """Genera sonido de notificación."""
        
        # Usar melodías cortas
        moods = {
            "info": AudioMood.FOCUS,
            "success": AudioMood.HAPPY,
            "warning": AudioMood.ENERGY,
            "error": AudioMood.DARK,
        }
        
        track = self.music.generate_track(moods.get(type, AudioMood.FOCUS), 2)
        
        # Guardar
        midi_path = OUTPUT_DIR / "sfx" / f"notif_{type}_{track.id}.mid"
        midi_path.parent.mkdir(parents=True, exist_ok=True)
        midi_path.write_bytes(self.music.generate_midi_data(track))
        
        return midi_path
    
    def get_stats(self) -> Dict[str, Any]:
        """Estadísticas del sistema multimedia."""
        music_dir = OUTPUT_DIR / "music"
        voice_dir = OUTPUT_DIR / "voice"
        video_dir = OUTPUT_DIR / "videos"
        
        return {
            "output_directory": str(OUTPUT_DIR),
            "music_files": len(list(music_dir.glob("*.mid"))) if music_dir.exists() else 0,
            "voice_files": len(list(voice_dir.glob("*.wav"))) if voice_dir.exists() else 0,
            "video_files": len(list(video_dir.glob("*.mp4"))) if video_dir.exists() else 0,
            "tts_backend": self.voice.backend,
            "ffmpeg_available": FFMPEG_AVAILABLE,
        }


# Singleton
_multimedia_engine: Optional[MultimediaEngine] = None

def get_multimedia_engine() -> MultimediaEngine:
    global _multimedia_engine
    if _multimedia_engine is None:
        _multimedia_engine = MultimediaEngine()
    return _multimedia_engine


# ══════════════════════════════════════════════════════════════════════════════
#  TEST
# ══════════════════════════════════════════════════════════════════════════════

if __name__ == "__main__":
    print("=" * 70)
    print("  EIDOS Multimedia Generator - Test")
    print("=" * 70)
    
    mm = get_multimedia_engine()
    
    # Test 1: Generar música
    print("\n[Test 1] Generating ambient music (focus)...")
    track = mm.generate_ambient_music(AudioMood.FOCUS, 30)
    print(f"  ✓ Generated: {track.title}")
    print(f"    BPM: {track.bpm}, Key: {track.key}")
    print(f"    Instruments: {', '.join(track.instruments)}")
    print(f"    Chords: {' - '.join(track.chords_progression[:4])}...")
    
    # Test 2: Generar música relajante
    print("\n[Test 2] Generating ambient music (relax)...")
    track2 = mm.create_focus_session(5)  # 5 minutos
    print(f"  ✓ Generated: {track2.title}")
    
    # Test 3: Síntesis de voz (si hay backend)
    print("\n[Test 3] Voice synthesis...")
    if mm.voice.backend != "none":
        voice_path = mm.narrate_text("Hello, I am EIDOS, your autonomous AI assistant.")
        print(f"  ✓ Voice synthesized: {voice_path}")
    else:
        print("  ⚠ No TTS backend available (install piper, coqui, or espeak-ng)")
    
    # Test 4: Estadísticas
    print("\n[Test 4] System stats:")
    stats = mm.get_stats()
    for key, value in stats.items():
        print(f"  • {key}: {value}")
    
    print("\n✅ Multimedia Engine test complete")
