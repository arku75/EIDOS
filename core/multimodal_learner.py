#!/usr/bin/env python3
"""
EIDOS Multimodal Learner
========================

Aprende de videos combinando múltiples modalidades:
- Audio (Whisper transcription)
- Visual (Vision lightweight + CLIP)
- Texto (transcripts, subtítulos)

Sincronización temporal para máxima extracción de conocimiento.
"""
from __future__ import annotations

import json
import logging
import subprocess
import tempfile
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import List, Dict, Any, Optional
import os
import time

logger = logging.getLogger(__name__)


# ══════════════════════════════════════════════════════════════════════════════
# Data Types
# ══════════════════════════════════════════════════════════════════════════════

@dataclass
class TimelineEvent:
    """Evento en la timeline del video"""
    timestamp: float  # Segundos desde inicio
    event_type: str   # 'audio', 'visual', 'code', 'diagram'
    content: str
    confidence: float = 1.0
    metadata: Dict[str, Any] = field(default_factory=dict)


@dataclass
class MultimodalKnowledge:
    """Conocimiento extraído de todas las modalidades"""
    video_url: str
    title: str
    duration: float  # segundos

    # Modalidades
    transcript: str = ""  # Full transcript (Whisper)
    visual_events: List[TimelineEvent] = field(default_factory=list)
    audio_events: List[TimelineEvent] = field(default_factory=list)

    # Conocimiento extraído
    concepts: List[str] = field(default_factory=list)
    code_snippets: List[Dict] = field(default_factory=list)
    diagrams: List[Dict] = field(default_factory=list)
    libraries_mentioned: List[str] = field(default_factory=list)
    best_practices: List[str] = field(default_factory=list)

    # Metadata
    extracted_at: str = field(default_factory=lambda: datetime.now().isoformat())


# ══════════════════════════════════════════════════════════════════════════════
# Multimodal Learner
# ══════════════════════════════════════════════════════════════════════════════

class MultimodalLearner:
    """
    Aprende de videos combinando audio, visual y texto.

    Flujo:
    1. Download video
    2. Extract audio → Whisper transcription
    3. Extract key frames → Vision analysis
    4. Sync timeline (audio events + visual events)
    5. Extract knowledge from all modalities
    6. Store in Knowledge DB
    """

    def __init__(self, verbose: bool = True):
        self.verbose = verbose
        self.temp_dir = Path(tempfile.gettempdir()) / "eidos_multimodal"
        self.temp_dir.mkdir(exist_ok=True)

        # Import lazy (solo cuando se necesita)
        self.whisper_model = None
        self.vision_system = None
        self.clip_model = None

    def _log(self, msg: str):
        """Log con prefijo"""
        if self.verbose:
            print(f"🎬 [Multimodal] {msg}")
        logger.info(msg)

    def _get_whisper(self):
        """Load Whisper model (lazy)"""
        if self.whisper_model is None:
            try:
                from faster_whisper import WhisperModel
                self._log("Cargando Whisper model 'tiny'...")
                self.whisper_model = WhisperModel(
                    "tiny",
                    device="cpu",
                    compute_type="int8",
                    num_workers=1
                )
                self._log("✅ Whisper cargado")
            except ImportError:
                self._log("❌ faster-whisper no disponible")
                return None
        return self.whisper_model

    def _get_vision(self):
        """Load Vision system (lazy)"""
        if self.vision_system is None:
            try:
                from .vision_lightweight import get_lightweight_vision
                self.vision_system = get_lightweight_vision()
                self._log("✅ Vision system cargado")
            except Exception as e:
                self._log(f"❌ Vision system error: {e}")
                return None
        return self.vision_system

    def _get_clip(self):
        """Load CLIP model (lazy)"""
        if self.clip_model is None:
            try:
                # Intentar importar CLIP (puede no estar instalado aún)
                from .clip_vision import CLIPVision
                self.clip_model = CLIPVision()
                self._log("✅ CLIP model cargado")
            except Exception:
                self._log("⚠️  CLIP no disponible (install: pip install transformers torch)")
                return None
        return self.clip_model

    def learn_from_video(self, video_url: str, title: str = "") -> MultimodalKnowledge:
        """
        Aprende de un video usando todas las modalidades.

        Args:
            video_url: URL de YouTube o ruta local
            title: Título del video (opcional)

        Returns:
            MultimodalKnowledge con todo el conocimiento extraído
        """
        self._log(f"Aprendiendo de: {video_url}")

        knowledge = MultimodalKnowledge(
            video_url=video_url,
            title=title or "Unknown video"
        )

        # Step 1: Download video (solo audio si es necesario)
        self._log("📥 Step 1: Downloading video...")
        video_path, audio_path = self._download_video(video_url)

        if not video_path or not video_path.exists():
            self._log("❌ Failed to download video")
            return knowledge

        # Step 2: Transcribe audio with Whisper
        self._log("🎤 Step 2: Transcribing audio with Whisper...")
        transcript, audio_events = self._transcribe_audio(audio_path or video_path)
        knowledge.transcript = transcript
        knowledge.audio_events = audio_events

        # Step 3: Analyze visual frames
        self._log("👁️  Step 3: Analyzing visual frames...")
        visual_events = self._analyze_video_frames(video_path)
        knowledge.visual_events = visual_events

        # Step 4: Sync timeline
        self._log("⏱️  Step 4: Syncing timeline...")
        self._sync_timeline(knowledge)

        # Step 5: Extract knowledge
        self._log("🧠 Step 5: Extracting knowledge...")
        self._extract_knowledge(knowledge)

        # Step 6: Cleanup
        self._cleanup_temp_files(video_path, audio_path)

        self._log(f"✅ Aprendizaje completado!")
        self._log(f"   Concepts: {len(knowledge.concepts)}")
        self._log(f"   Code snippets: {len(knowledge.code_snippets)}")
        self._log(f"   Diagrams: {len(knowledge.diagrams)}")
        self._log(f"   Libraries: {len(knowledge.libraries_mentioned)}")

        return knowledge

    def _download_video(self, url: str) -> tuple[Optional[Path], Optional[Path]]:
        """
        Download video usando yt-dlp.

        Returns:
            (video_path, audio_path) - Puede ser el mismo archivo
        """
        try:
            output_template = str(self.temp_dir / "video_%(id)s.%(ext)s")

            # Intentar download completo primero
            cmd = [
                "yt-dlp",
                "-f", "best[height<=720]",  # Max 720p para ahorrar espacio
                "-o", output_template,
                url
            ]

            result = subprocess.run(cmd, capture_output=True, text=True, timeout=300)

            if result.returncode == 0:
                # Encontrar archivo descargado
                for file in self.temp_dir.glob("video_*"):
                    if file.suffix in ['.mp4', '.webm', '.mkv']:
                        self._log(f"✅ Video descargado: {file.name}")
                        return file, file

            # Fallback: solo audio
            self._log("⚠️  Download completo falló, intentando solo audio...")
            cmd[2] = "bestaudio"
            result = subprocess.run(cmd, capture_output=True, text=True, timeout=300)

            if result.returncode == 0:
                for file in self.temp_dir.glob("video_*"):
                    if file.suffix in ['.m4a', '.webm', '.opus']:
                        self._log(f"✅ Audio descargado: {file.name}")
                        return None, file

            return None, None

        except Exception as e:
            self._log(f"❌ Error downloading: {e}")
            return None, None

    def _transcribe_audio(self, audio_path: Path) -> tuple[str, List[TimelineEvent]]:
        """Transcribe audio con Whisper y extrae eventos"""
        whisper = self._get_whisper()
        if not whisper or not audio_path.exists():
            return "", []

        try:
            segments, info = whisper.transcribe(
                str(audio_path),
                beam_size=1,
                vad_filter=True,
                language=None
            )

            transcript_parts = []
            events = []

            for segment in segments:
                transcript_parts.append(segment.text)

                # Crear evento por cada segmento
                events.append(TimelineEvent(
                    timestamp=segment.start,
                    event_type='audio',
                    content=segment.text,
                    confidence=1.0,  # Whisper no da confidence scores
                    metadata={
                        'start': segment.start,
                        'end': segment.end,
                        'language': info.language
                    }
                ))

            transcript = " ".join(transcript_parts)
            self._log(f"   Transcript: {len(transcript)} chars, {len(events)} segments")

            return transcript, events

        except Exception as e:
            self._log(f"❌ Transcription error: {e}")
            return "", []

    def _analyze_video_frames(self, video_path: Optional[Path]) -> List[TimelineEvent]:
        """Analiza frames clave del video"""
        if not video_path or not video_path.exists():
            return []

        vision = self._get_vision()
        if not vision:
            return []

        events = []

        try:
            # Extraer frames clave (1 frame cada 5 segundos)
            import cv2

            cap = cv2.VideoCapture(str(video_path))
            fps = cap.get(cv2.CAP_PROP_FPS)
            total_frames = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
            duration = total_frames / fps if fps > 0 else 0

            self._log(f"   Video: {duration:.1f}s, {fps:.1f} FPS")

            frame_interval = int(fps * 5)  # 1 frame cada 5 segundos
            frame_count = 0

            while True:
                ret, frame = cap.read()
                if not ret:
                    break

                if frame_count % frame_interval == 0:
                    timestamp = frame_count / fps

                    # Analizar frame con Vision + CLIP
                    analysis_result = self._analyze_single_frame(frame, timestamp, vision)

                    if analysis_result:
                        events.append(analysis_result)

                frame_count += 1

            cap.release()
            self._log(f"   Analyzed {frame_count} frames, {len(events)} events")

        except Exception as e:
            self._log(f"❌ Frame analysis error: {e}")

        return events

    def _analyze_single_frame(self, frame, timestamp: float, vision) -> Optional[TimelineEvent]:
        """
        Analiza un frame individual con Vision + CLIP + OCR.

        Detecta:
        - Código en pantalla (OCR)
        - Diagramas (CLIP)
        - Tipo de contenido (CLIP semantic categories)
        """
        try:
            import cv2
            import tempfile

            # Guardar frame temporalmente
            with tempfile.NamedTemporaryFile(suffix='.png', delete=False) as tmp:
                tmp_path = tmp.name
                cv2.imwrite(tmp_path, frame)

            # Análisis CLIP
            clip = self._get_clip()
            event_type = "visual"
            content = ""
            confidence = 0.5
            metadata = {"timestamp": timestamp}

            if clip:
                clip_result = clip.analyze_image(tmp_path, top_k=3)
                top_category = clip_result.top_categories[0][0] if clip_result.top_categories else "unknown"
                top_confidence = clip_result.top_categories[0][1] if clip_result.top_categories else 0.5

                # Determinar tipo de evento basado en categoría CLIP
                if "code" in top_category or "terminal" in top_category:
                    event_type = "code"
                    content = f"Code/terminal detected ({top_category})"

                    # Intentar OCR para extraer código
                    try:
                        import pytesseract
                        from PIL import Image
                        img_pil = Image.open(tmp_path)
                        text = pytesseract.image_to_string(img_pil)

                        if text and len(text.strip()) > 10:
                            content += f"\n\nExtracted text:\n{text[:500]}"
                    except Exception:
                        pass  # error no crítico, continuar
                elif "diagram" in top_category or "flowchart" in top_category:
                    event_type = "diagram"
                    content = f"Diagram detected ({top_category})"

                else:
                    event_type = "visual"
                    content = f"Visual content: {top_category}"

                confidence = top_confidence
                metadata["clip_categories"] = [
                    {"category": cat, "confidence": conf}
                    for cat, conf in clip_result.top_categories[:3]
                ]

            # Limpiar archivo temporal
            try:
                os.remove(tmp_path)
            except Exception:
                pass  # error no crítico, continuar
            # Solo crear evento si es relevante (confianza > 0.3)
            if confidence > 0.3:
                return TimelineEvent(
                    timestamp=timestamp,
                    event_type=event_type,
                    content=content,
                    confidence=confidence,
                    metadata=metadata
                )

            return None

        except Exception as e:
            self._log(f"Error analyzing frame at {timestamp:.1f}s: {e}")
            return None

    def _sync_timeline(self, knowledge: MultimodalKnowledge):
        """Sincroniza eventos de audio y visual en una timeline unificada"""
        # Combinar y ordenar todos los eventos por timestamp
        all_events = knowledge.audio_events + knowledge.visual_events
        all_events.sort(key=lambda e: e.timestamp)

        self._log(f"   Timeline: {len(all_events)} eventos sincronizados")

    def _extract_knowledge(self, knowledge: MultimodalKnowledge):
        """
        Extrae conocimiento de alto nivel de todos los eventos.

        Analiza:
        - Transcript para concepts, libraries, best practices
        - Visual events para code snippets, diagrams
        - Correlaciones entre audio y visual
        """
        # Analizar transcript
        if knowledge.transcript:
            # Extraer conceptos (palabras clave técnicas)
            import re

            # Patterns comunes en tutoriales de programación
            patterns = {
                'libraries': r'(?:import|use|require|from)\s+([a-zA-Z_][a-zA-Z0-9_\.]*)',
                'concepts': r'\b(async|await|promise|callback|closure|decorator|middleware|API|REST|GraphQL|database|SQL|NoSQL)\b',
            }

            for pattern_name, pattern in patterns.items():
                matches = re.findall(pattern, knowledge.transcript, re.IGNORECASE)
                if pattern_name == 'libraries':
                    knowledge.libraries_mentioned.extend(matches)
                elif pattern_name == 'concepts':
                    knowledge.concepts.extend(matches)

            # Deduplicar
            knowledge.concepts = list(set(knowledge.concepts))
            knowledge.libraries_mentioned = list(set(knowledge.libraries_mentioned))

        # TODO: Análisis más profundo con CLIP para diagrams
        # TODO: Correlacionar audio + visual para mejor comprensión

    def _cleanup_temp_files(self, *files):
        """Limpia archivos temporales"""
        for file in files:
            if file and file.exists():
                try:
                    file.unlink()
                    self._log(f"🧹 Cleaned: {file.name}")
                except Exception:
                    pass  # error no crítico, continuar
# ══════════════════════════════════════════════════════════════════════════════
# Singleton
# ══════════════════════════════════════════════════════════════════════════════

_multimodal_learner = None

def get_multimodal_learner() -> MultimodalLearner:
    """Get singleton MultimodalLearner instance"""
    global _multimodal_learner
    if _multimodal_learner is None:
        _multimodal_learner = MultimodalLearner()
    return _multimodal_learner


# ══════════════════════════════════════════════════════════════════════════════
# CLI Testing
# ══════════════════════════════════════════════════════════════════════════════

if __name__ == "__main__":
    import sys

    if len(sys.argv) < 2:
        print("Usage: python multimodal_learner.py <youtube_url>")
        sys.exit(1)

    url = sys.argv[1]

    learner = get_multimodal_learner()
    knowledge = learner.learn_from_video(url, "Test Video")

    print("\n" + "="*70)
    print("KNOWLEDGE EXTRACTED")
    print("="*70)
    print(f"Transcript length: {len(knowledge.transcript)} chars")
    print(f"Concepts: {knowledge.concepts}")
    print(f"Libraries: {knowledge.libraries_mentioned}")
    print(f"Audio events: {len(knowledge.audio_events)}")
    print(f"Visual events: {len(knowledge.visual_events)}")
