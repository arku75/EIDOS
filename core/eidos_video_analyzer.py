"""
EIDOS Deep Video Analyzer - Analizador de Video Profundo
=========================================================

Sistema de análisis profundo de videos de YouTube y archivos locales.
Extrae conocimiento, genera resúmenes, identifica conceptos clave,
y construye una base de conocimiento searchable.

Features:
- Descarga y transcripción de videos de YouTube
- Procesamiento de videos locales (mp4, mkv, avi, etc.)
- Extracción de frames clave con análisis visual
- Transcripción de audio a texto (whisper)
- Segmentación por temas/topics
- Resumen inteligente con timestamps
- Extracción de conceptos y entidades
- Knowledge graph de videos analizados
- Búsqueda semántica en contenido

Uso:
    from core.eidos_video_analyzer import VideoAnalyzer, get_video_analyzer
    analyzer = get_video_analyzer()
    
    # Analizar video de YouTube
    result = analyzer.analyze_youtube("https://youtube.com/watch?v=...")
    
    # Analizar archivo local
    result = analyzer.analyze_local("/path/to/video.mp4")
    
    # Buscar en conocimiento acumulado
    matches = analyzer.search("machine learning transformers")
"""

import json
import logging
import os
import re
import sqlite3
import subprocess
import tempfile
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple
from urllib.parse import parse_qs, urlparse

import numpy as np
from core.db import get_conn

log = logging.getLogger("eidos.video_analyzer")

# ══════════════════════════════════════════════════════════════════════════════
#  CONFIGURACIÓN
# ══════════════════════════════════════════════════════════════════════════════

DB_PATH = Path.home() / ".eidos" / "video_knowledge.db"
CACHE_DIR = Path.home() / ".eidos" / "video_cache"
TRANSCRIPTS_DIR = Path.home() / ".eidos" / "transcripts"
FRAMES_DIR = Path.home() / ".eidos" / "video_frames"

# Verificar dependencias opcionales
YT_DLP_AVAILABLE = False
WHISPER_AVAILABLE = False
FFMPEG_AVAILABLE = False

try:
    import yt_dlp
    YT_DLP_AVAILABLE = True
except ImportError:
    pass

try:
    import whisper
    WHISPER_AVAILABLE = True
except ImportError:
    pass

try:
    subprocess.run(["ffmpeg", "-version"], capture_output=True, check=True)
    FFMPEG_AVAILABLE = True
except:
    pass

# ══════════════════════════════════════════════════════════════════════════════
#  TIPOS
# ══════════════════════════════════════════════════════════════════════════════

@dataclass
class VideoSegment:
    """Segmento de video con transcripción y análisis."""
    start_time: float  # segundos
    end_time: float
    text: str
    key_topics: List[str] = field(default_factory=list)
    importance_score: float = 0.0  # 0-1
    frame_descriptions: List[str] = field(default_factory=list)


@dataclass
class VideoAnalysis:
    """Resultado completo de análisis de video."""
    id: str
    source_url: Optional[str]
    local_path: Optional[Path]
    title: str
    description: str
    duration_seconds: float
    author: str
    upload_date: Optional[str]
    
    # Contenido extraído
    transcript: str = ""
    segments: List[VideoSegment] = field(default_factory=list)
    key_frames: List[Path] = field(default_factory=list)
    
    # Análisis
    summary: str = ""
    key_concepts: List[str] = field(default_factory=list)
    entities: Dict[str, List[str]] = field(default_factory=dict)  # person, org, tech, etc.
    topics: List[str] = field(default_factory=list)
    
    # Metadata
    analyzed_at: datetime = field(default_factory=datetime.now)
    processing_time_seconds: float = 0.0
    
    def get_segment_at_time(self, seconds: float) -> Optional[VideoSegment]:
        """Obtiene el segmento que cubre un timestamp específico."""
        for seg in self.segments:
            if seg.start_time <= seconds < seg.end_time:
                return seg
        return None


@dataclass
class VideoKnowledgeEntry:
    """Entrada en la base de conocimiento de videos."""
    id: str
    analysis_id: str
    content_type: str  # 'transcript', 'summary', 'concept', 'entity'
    content: str
    source_timestamp: Optional[float]
    embedding: Optional[List[float]] = None  # Para búsqueda semántica
    created_at: datetime = field(default_factory=datetime.now)


# ══════════════════════════════════════════════════════════════════════════════
#  BASE DE DATOS
# ══════════════════════════════════════════════════════════════════════════════

class VideoKnowledgeDB:
    """Base de datos SQLite para conocimiento de videos."""
    
    def __init__(self, db_path: Path = DB_PATH):
        self.db_path = db_path
        self._init_db()
    
    def _init_db(self):
        os.makedirs(self.db_path.parent, exist_ok=True)
        conn = get_conn(self.db_path)
        cursor = conn.cursor()
        
        # Tabla de análisis completados
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS video_analyses (
                id TEXT PRIMARY KEY,
                source_url TEXT,
                local_path TEXT,
                title TEXT,
                description TEXT,
                duration_seconds REAL,
                author TEXT,
                upload_date TEXT,
                transcript TEXT,
                segments TEXT,  -- JSON
                summary TEXT,
                key_concepts TEXT,  -- JSON list
                entities TEXT,  -- JSON dict
                topics TEXT,  -- JSON list
                analyzed_at TEXT,
                processing_time_seconds REAL
            )
        """)
        
        # Tabla de conocimiento extraído (para búsqueda)
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS video_knowledge (
                id TEXT PRIMARY KEY,
                analysis_id TEXT,
                content_type TEXT,
                content TEXT,
                source_timestamp REAL,
                created_at TEXT
            )
        """)
        
        # Índices
        cursor.execute("""
            CREATE INDEX IF NOT EXISTS idx_knowledge_analysis 
            ON video_knowledge(analysis_id)
        """)
        cursor.execute("""
            CREATE INDEX IF NOT EXISTS idx_knowledge_content 
            ON video_knowledge(content)
        """)
        
        conn.commit()
        pass  # S109: get_conn no necesita close()
    def save_analysis(self, analysis: VideoAnalysis):
        """Guarda un análisis completo."""
        conn = get_conn(self.db_path)
        cursor = conn.cursor()
        
        cursor.execute("""
            INSERT OR REPLACE INTO video_analyses VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
        """, (
            analysis.id,
            analysis.source_url,
            str(analysis.local_path) if analysis.local_path else None,
            analysis.title,
            analysis.description,
            analysis.duration_seconds,
            analysis.author,
            analysis.upload_date,
            analysis.transcript,
            json.dumps([{
                "start": s.start_time,
                "end": s.end_time,
                "text": s.text,
                "topics": s.key_topics,
                "importance": s.importance_score
            } for s in analysis.segments]),
            analysis.summary,
            json.dumps(analysis.key_concepts),
            json.dumps(analysis.entities),
            json.dumps(analysis.topics),
            analysis.analyzed_at.isoformat(),
            analysis.processing_time_seconds
        ))
        
        conn.commit()
        pass  # S109: get_conn no necesita close()
        # Indexar conocimiento
        self._index_knowledge(analysis)
    
    def _index_knowledge(self, analysis: VideoAnalysis):
        """Indexa el contenido para búsqueda."""
        conn = get_conn(self.db_path)
        cursor = conn.cursor()
        
        entries = []
        
        # Indexar resumen
        if analysis.summary:
            entries.append((
                f"{analysis.id}_summary",
                analysis.id,
                "summary",
                analysis.summary,
                None
            ))
        
        # Indexar conceptos clave
        for concept in analysis.key_concepts:
            entries.append((
                f"{analysis.id}_concept_{len(entries)}",
                analysis.id,
                "concept",
                concept,
                None
            ))
        
        # Indexar segmentos de transcripción
        for seg in analysis.segments:
            if seg.text and len(seg.text) > 20:  # Ignorar segmentos muy cortos
                entries.append((
                    f"{analysis.id}_seg_{seg.start_time}",
                    analysis.id,
                    "transcript",
                    seg.text,
                    seg.start_time
                ))
        
        # Guardar entradas
        cursor.executemany("""
            INSERT OR REPLACE INTO video_knowledge 
            (id, analysis_id, content_type, content, source_timestamp, created_at)
            VALUES (?, ?, ?, ?, ?, ?)
        """, [
            (e[0], e[1], e[2], e[3], e[4], datetime.now().isoformat())
            for e in entries
        ])
        
        conn.commit()
        pass  # S109: get_conn no necesita close()
    def search_content(self, query: str, limit: int = 20) -> List[Dict]:
        """Búsqueda simple por contenido."""
        conn = get_conn(self.db_path)
        cursor = conn.cursor()
        
        # Búsqueda con LIKE (básica, para producción usar FTS o embeddings)
        cursor.execute("""
            SELECT k.*, a.title, a.source_url, a.local_path
            FROM video_knowledge k
            JOIN video_analyses a ON k.analysis_id = a.id
            WHERE k.content LIKE ?
            ORDER BY k.created_at DESC
            LIMIT ?
        """, (f"%{query}%", limit))
        
        rows = cursor.fetchall()
        pass  # S109: get_conn no necesita close()
        results = []
        for row in rows:
            results.append({
                "id": row[0],
                "analysis_id": row[1],
                "content_type": row[2],
                "content": row[3][:200] + "..." if len(row[3]) > 200 else row[3],
                "timestamp": row[4],
                "video_title": row[6],
                "video_url": row[7],
                "video_path": row[8]
            })
        
        return results
    
    def get_analysis(self, analysis_id: str) -> Optional[VideoAnalysis]:
        """Carga un análisis por ID."""
        conn = get_conn(self.db_path)
        cursor = conn.cursor()
        
        cursor.execute("SELECT * FROM video_analyses WHERE id = ?", (analysis_id,))
        row = cursor.fetchone()
        pass  # S109: get_conn no necesita close()
        if not row:
            return None
        
        segments_data = json.loads(row[9]) if row[9] else []
        segments = [
            VideoSegment(
                start_time=s["start"],
                end_time=s["end"],
                text=s["text"],
                key_topics=s.get("topics", []),
                importance_score=s.get("importance", 0)
            )
            for s in segments_data
        ]
        
        return VideoAnalysis(
            id=row[0],
            source_url=row[1],
            local_path=Path(row[2]) if row[2] else None,
            title=row[3],
            description=row[4],
            duration_seconds=row[5],
            author=row[6],
            upload_date=row[7],
            transcript=row[8] or "",
            segments=segments,
            summary=row[10] or "",
            key_concepts=json.loads(row[11]) if row[11] else [],
            entities=json.loads(row[12]) if row[12] else {},
            topics=json.loads(row[13]) if row[13] else [],
            analyzed_at=datetime.fromisoformat(row[14]),
            processing_time_seconds=row[15] or 0
        )
    
    def list_analyses(self, limit: int = 50) -> List[Dict]:
        """Lista análisis recientes."""
        conn = get_conn(self.db_path)
        cursor = conn.cursor()
        
        cursor.execute("""
            SELECT id, title, author, duration_seconds, analyzed_at
            FROM video_analyses
            ORDER BY analyzed_at DESC
            LIMIT ?
        """, (limit,))
        
        rows = cursor.fetchall()
        pass  # S109: get_conn no necesita close()
        return [
            {
                "id": row[0],
                "title": row[1],
                "author": row[2],
                "duration": row[3],
                "analyzed_at": row[4]
            }
            for row in rows
        ]


# ══════════════════════════════════════════════════════════════════════════════
#  PROCESADORES
# ══════════════════════════════════════════════════════════════════════════════

class YouTubeProcessor:
    """Procesa videos de YouTube."""
    
    def __init__(self):
        self.cache_dir = CACHE_DIR / "youtube"
        self.cache_dir.mkdir(parents=True, exist_ok=True)
    
    def extract_video_id(self, url: str) -> Optional[str]:
        """Extrae el ID de video de una URL de YouTube."""
        patterns = [
            r'(?:youtube\.com\/watch\?v=|youtu\.be\/|youtube\.com\/embed\/)([a-zA-Z0-9_-]{11})',
            r'youtube\.com\/shorts\/([a-zA-Z0-9_-]{11})',
        ]
        
        for pattern in patterns:
            match = re.search(pattern, url)
            if match:
                return match.group(1)
        
        # Fallback a parseo de URL
        parsed = urlparse(url)
        if parsed.hostname in ['youtube.com', 'www.youtube.com', 'youtu.be']:
            if parsed.hostname == 'youtu.be':
                return parsed.path[1:]
            query = parse_qs(parsed.query)
            return query.get('v', [None])[0]
        
        return None
    
    def download_video_info(self, url: str) -> Optional[Dict]:
        """Obtiene información del video sin descargar."""
        if not YT_DLP_AVAILABLE:
            log.error("yt-dlp not available")
            return None
        
        try:
            ydl_opts = {
                'quiet': True,
                'no_warnings': True,
                'skip_download': True,
            }
            
            with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                info = ydl.extract_info(url, download=False)
                return {
                    "id": info.get("id"),
                    "title": info.get("title", "Unknown"),
                    "description": info.get("description", ""),
                    "duration": info.get("duration", 0),
                    "uploader": info.get("uploader", "Unknown"),
                    "upload_date": info.get("upload_date"),
                    "thumbnail": info.get("thumbnail"),
                    "tags": info.get("tags", []),
                    "categories": info.get("categories", []),
                }
        except Exception as e:
            log.error(f"Error fetching YouTube info: {e}")
            return None
    
    def download_audio(self, url: str, video_id: str) -> Optional[Path]:
        """Descarga solo el audio del video."""
        if not YT_DLP_AVAILABLE:
            return None
        
        output_path = self.cache_dir / f"{video_id}.mp3"
        
        if output_path.exists():
            log.info(f"Using cached audio: {output_path}")
            return output_path
        
        try:
            ydl_opts = {
                'format': 'bestaudio/best',
                'outtmpl': str(self.cache_dir / '%(id)s.%(ext)s'),
                'postprocessors': [{
                    'key': 'FFmpegExtractAudio',
                    'preferredcodec': 'mp3',
                    'preferredquality': '192',
                }],
                'quiet': True,
                'no_warnings': True,
            }
            
            with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                ydl.download([url])
            
            # yt-dlp puede guardar con extensión diferente
            for ext in ['mp3', 'm4a', 'webm']:
                candidate = self.cache_dir / f"{video_id}.{ext}"
                if candidate.exists():
                    return candidate
            
            return output_path if output_path.exists() else None
            
        except Exception as e:
            log.error(f"Error downloading audio: {e}")
            return None


class LocalVideoProcessor:
    """Procesa archivos de video locales."""
    
    def __init__(self):
        self.cache_dir = CACHE_DIR / "local"
        self.cache_dir.mkdir(parents=True, exist_ok=True)
    
    def extract_audio(self, video_path: Path) -> Optional[Path]:
        """Extrae audio de un video local usando ffmpeg."""
        if not FFMPEG_AVAILABLE:
            log.error("ffmpeg not available for audio extraction")
            return None
        
        video_hash = str(hash(str(video_path)))[:12]
        audio_path = self.cache_dir / f"{video_hash}.mp3"
        
        if audio_path.exists():
            return audio_path
        
        try:
            cmd = [
                "ffmpeg", "-i", str(video_path),
                "-vn",  # No video
                "-acodec", "libmp3lame",
                "-q:a", "2",  # Calidad
                "-y",  # Overwrite
                str(audio_path)
            ]
            subprocess.run(cmd, check=True, capture_output=True, timeout=300)
            return audio_path
        except Exception as e:
            log.error(f"Error extracting audio: {e}")
            return None
    
    def get_video_info(self, video_path: Path) -> Dict:
        """Obtiene información del video usando ffprobe."""
        info = {
            "path": str(video_path),
            "title": video_path.stem,
            "duration": 0,
            "width": 0,
            "height": 0,
        }
        
        if not FFMPEG_AVAILABLE:
            return info
        
        try:
            cmd = [
                "ffprobe", "-v", "quiet", "-print_format", "json",
                "-show_format", "-show_streams", str(video_path)
            ]
            result = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
            data = json.loads(result.stdout)
            
            if 'format' in data:
                info["duration"] = float(data['format'].get('duration', 0))
            
            for stream in data.get('streams', []):
                if stream.get('codec_type') == 'video':
                    info["width"] = stream.get('width', 0)
                    info["height"] = stream.get('height', 0)
                    break
            
        except Exception as e:
            log.warning(f"Error getting video info: {e}")
        
        return info


class AudioTranscriber:
    """Transcribe audio a texto usando Whisper."""
    
    def __init__(self, model_size: str = "base"):
        self.model = None
        self.model_size = model_size
        
        if WHISPER_AVAILABLE:
            try:
                log.info(f"Loading Whisper model: {model_size}")
                self.model = whisper.load_model(model_size)
            except Exception as e:
                log.error(f"Error loading Whisper: {e}")
    
    def transcribe(self, audio_path: Path) -> Optional[Dict]:
        """Transcribe audio y retorna texto con timestamps."""
        if not self.model:
            log.error("Whisper model not available")
            return None
        
        try:
            result = self.model.transcribe(str(audio_path), verbose=False)
            
            # Estructurar segments
            segments = []
            for seg in result.get("segments", []):
                segments.append({
                    "start": seg["start"],
                    "end": seg["end"],
                    "text": seg["text"].strip()
                })
            
            return {
                "text": result["text"],
                "segments": segments,
                "language": result.get("language", "unknown")
            }
        except Exception as e:
            log.error(f"Transcription error: {e}")
            return None


# ══════════════════════════════════════════════════════════════════════════════
#  ANALIZADOR PRINCIPAL
# ══════════════════════════════════════════════════════════════════════════════

class VideoAnalyzer:
    """
    Analizador unificado de videos (YouTube + local).
    """
    
    def __init__(self, whisper_model: str = "base"):
        self.db = VideoKnowledgeDB()
        self.yt_processor = YouTubeProcessor()
        self.local_processor = LocalVideoProcessor()
        self.transcriber = AudioTranscriber(whisper_model)
        
        # Directorios
        TRANSCRIPTS_DIR.mkdir(parents=True, exist_ok=True)
        FRAMES_DIR.mkdir(parents=True, exist_ok=True)
        
        log.info("Video Analyzer initialized")
    
    def analyze_youtube(self, url: str) -> Optional[VideoAnalysis]:
        """
        Analiza completo un video de YouTube.
        """
        start_time = time.time()
        
        log.info(f"Analyzing YouTube: {url}")
        
        # Extraer ID
        video_id = self.yt_processor.extract_video_id(url)
        if not video_id:
            log.error("Could not extract video ID")
            return None
        
        # Verificar si ya existe
        existing = self.db.get_analysis(f"yt_{video_id}")
        if existing:
            log.info(f"Using cached analysis for {video_id}")
            return existing
        
        # Obtener información
        info = self.yt_processor.download_video_info(url)
        if not info:
            return None
        
        # Descargar audio
        audio_path = self.yt_processor.download_audio(url, video_id)
        if not audio_path:
            log.error("Could not download audio")
            return None
        
        # Transcribir
        transcript_data = None
        if self.transcriber.model:
            transcript_data = self.transcriber.transcribe(audio_path)
        
        # Crear análisis
        analysis = VideoAnalysis(
            id=f"yt_{video_id}",
            source_url=url,
            local_path=None,
            title=info["title"],
            description=info["description"],
            duration_seconds=info["duration"],
            author=info["uploader"],
            upload_date=info["upload_date"],
        )
        
        if transcript_data:
            analysis.transcript = transcript_data["text"]
            analysis.segments = [
                VideoSegment(
                    start_time=seg["start"],
                    end_time=seg["end"],
                    text=seg["text"]
                )
                for seg in transcript_data.get("segments", [])
            ]
        
        # Generar resumen y análisis
        self._enrich_analysis(analysis)
        
        # Calcular tiempo de procesamiento
        analysis.processing_time_seconds = time.time() - start_time
        
        # Guardar
        self.db.save_analysis(analysis)
        
        log.info(f"Analysis complete: {analysis.title} ({len(analysis.segments)} segments)")
        return analysis
    
    def analyze_local(self, video_path: Path) -> Optional[VideoAnalysis]:
        """
        Analiza un archivo de video local.
        """
        start_time = time.time()
        
        video_path = Path(video_path)
        if not video_path.exists():
            log.error(f"Video not found: {video_path}")
            return None
        
        log.info(f"Analyzing local video: {video_path}")
        
        # Verificar caché
        file_hash = str(hash(str(video_path.absolute())))[:16]
        analysis_id = f"local_{file_hash}"
        
        existing = self.db.get_analysis(analysis_id)
        if existing:
            log.info(f"Using cached analysis for {video_path.name}")
            return existing
        
        # Obtener info
        info = self.local_processor.get_video_info(video_path)
        
        # Extraer audio
        audio_path = self.local_processor.extract_audio(video_path)
        if not audio_path:
            log.error("Could not extract audio")
            return None
        
        # Transcribir
        transcript_data = None
        if self.transcriber.model:
            transcript_data = self.transcriber.transcribe(audio_path)
        
        # Crear análisis
        analysis = VideoAnalysis(
            id=analysis_id,
            source_url=None,
            local_path=video_path,
            title=info["title"],
            description="",
            duration_seconds=info["duration"],
            author="unknown",
            upload_date=None,
        )
        
        if transcript_data:
            analysis.transcript = transcript_data["text"]
            analysis.segments = [
                VideoSegment(
                    start_time=seg["start"],
                    end_time=seg["end"],
                    text=seg["text"]
                )
                for seg in transcript_data.get("segments", [])
            ]
        
        # Enriquecer análisis
        self._enrich_analysis(analysis)
        
        analysis.processing_time_seconds = time.time() - start_time
        
        # Guardar
        self.db.save_analysis(analysis)
        
        log.info(f"Analysis complete: {analysis.title}")
        return analysis
    
    def _enrich_analysis(self, analysis: VideoAnalysis):
        """
        Enriquece el análisis generando resumen, conceptos, etc.
        En producción usaría LLM, aquí usa heurísticas simples.
        """
        if not analysis.transcript:
            return
        
        # Generar "resumen" simple (primeras y últimas frases importantes)
        sentences = analysis.transcript.split('. ')
        if len(sentences) > 5:
            summary_sentences = sentences[:2] + ["..."] + sentences[-2:]
            analysis.summary = '. '.join(summary_sentences)
        else:
            analysis.summary = analysis.transcript[:500]
        
        # Extraer "conceptos" (palabras frecuentes filtradas)
        words = analysis.transcript.lower().split()
        stopwords = {'the', 'a', 'an', 'and', 'or', 'but', 'in', 'on', 'at', 'to', 'for', 'of', 'with', 'by', 'is', 'are', 'was', 'were', 'be', 'been', 'have', 'has', 'had', 'do', 'does', 'did', 'will', 'would', 'could', 'should', 'may', 'might', 'must', 'can', 'this', 'that', 'these', 'those', 'i', 'you', 'he', 'she', 'it', 'we', 'they', 'me', 'him', 'her', 'us', 'them'}
        
        word_freq = {}
        for word in words:
            word = re.sub(r'[^\w]', '', word)
            if len(word) > 4 and word not in stopwords:
                word_freq[word] = word_freq.get(word, 0) + 1
        
        # Top conceptos
        analysis.key_concepts = [
            word for word, count in 
            sorted(word_freq.items(), key=lambda x: x[1], reverse=True)[:10]
        ]
        
        # Extraer entidades simples (mayúsculas)
        potential_names = re.findall(r'\b[A-Z][a-z]+\s+[A-Z][a-z]+\b', analysis.transcript)
        if potential_names:
            analysis.entities["person"] = list(set(potential_names))[:5]
        
        # Topics basados en el título
        analysis.topics = [
            word for word in analysis.title.lower().split()
            if len(word) > 3 and word not in stopwords
        ][:5]
    
    def search(self, query: str, limit: int = 10) -> List[Dict]:
        """Busca en la base de conocimiento de videos."""
        return self.db.search_content(query, limit)
    
    def get_summary(self, analysis_id: str) -> Optional[str]:
        """Obtiene resumen de un video analizado."""
        analysis = self.db.get_analysis(analysis_id)
        if analysis:
            return analysis.summary
        return None
    
    def get_transcript_at_time(self, analysis_id: str, minutes: int) -> Optional[str]:
        """Obtiene transcripción en un minuto específico."""
        analysis = self.db.get_analysis(analysis_id)
        if not analysis:
            return None
        
        seconds = minutes * 60
        segment = analysis.get_segment_at_time(seconds)
        if segment:
            return segment.text
        return None
    
    def list_analyzed(self, limit: int = 20) -> List[Dict]:
        """Lista videos analizados."""
        return self.db.list_analyses(limit)
    
    def ask_about_video(self, analysis_id: str, question: str) -> str:
        """
        Responde una pregunta sobre el contenido del video.
        En producción usaría RAG con embeddings + LLM.
        """
        analysis = self.db.get_analysis(analysis_id)
        if not analysis:
            return "Video not found in database."
        
        # Búsqueda simple de keywords en la pregunta
        question_words = set(question.lower().split())
        
        # Buscar segmentos relevantes
        relevant_segments = []
        for seg in analysis.segments:
            seg_words = set(seg.text.lower().split())
            overlap = question_words & seg_words
            if overlap:
                relevant_segments.append((len(overlap), seg))
        
        # Ordenar por relevancia
        relevant_segments.sort(reverse=True)
        
        if relevant_segments:
            best_segment = relevant_segments[0][1]
            return f"Relevant content (at {int(best_segment.start_time//60)}:{int(best_segment.start_time%60):02d}): {best_segment.text[:300]}..."
        
        return "Could not find specific information about that in the video."


# Singleton
_video_analyzer: Optional[VideoAnalyzer] = None

def get_video_analyzer(whisper_model: str = "base") -> VideoAnalyzer:
    global _video_analyzer
    if _video_analyzer is None:
        _video_analyzer = VideoAnalyzer(whisper_model)
    return _video_analyzer


# ══════════════════════════════════════════════════════════════════════════════
#  TEST
# ══════════════════════════════════════════════════════════════════════════════

if __name__ == "__main__":
    print("=" * 70)
    print("  EIDOS Video Analyzer - Test")
    print("=" * 70)
    
    analyzer = get_video_analyzer()
    
    # Info de disponibilidad
    print("\n[Dependencies]")
    print(f"  yt-dlp: {'✓' if YT_DLP_AVAILABLE else '✗'} (pip install yt-dlp)")
    print(f"  whisper: {'✓' if WHISPER_AVAILABLE else '✗'} (pip install openai-whisper)")
    print(f"  ffmpeg: {'✓' if FFMPEG_AVAILABLE else '✗'} (apt install ffmpeg)")
    
    # Test: Info de estructura
    print("\n[Test 1] Database structure:")
    analyses = analyzer.list_analyzed(5)
    print(f"  Analyzed videos in DB: {len(analyses)}")
    
    # Test: Búsqueda
    print("\n[Test 2] Search functionality:")
    results = analyzer.search("machine learning", limit=3)
    print(f"  Search results: {len(results)}")
    
    # Test: Video ID extraction
    print("\n[Test 3] YouTube URL parsing:")
    test_urls = [
        "https://www.youtube.com/watch?v=dQw4w9WgXcQ",
        "https://youtu.be/dQw4w9WgXcQ",
        "https://youtube.com/embed/dQw4w9WgXcQ",
    ]
    for url in test_urls:
        video_id = analyzer.yt_processor.extract_video_id(url)
        print(f"  {url[:40]}... -> {video_id}")
    
    print("\n✅ Video Analyzer test complete")
    print("\nNote: Full analysis requires yt-dlp, whisper, and ffmpeg installed.")
