#!/usr/bin/env python3
"""
EIDOS Continuous Learner - Sistema de Aprendizaje Continuo Multimodal
======================================================================

EIDOS aprende 24/7 de múltiples fuentes:
- 🎥 Videos (YouTube, tutoriales, conferencias)
- 📚 Libros (PDFs, ePubs, documentación)
- 🌐 Web (artículos, blogs, documentación)
- 🎧 Audio (podcasts, conferencias)
- 💾 Tu código (archivos locales)

NUNCA PARA DE APRENDER - Evolución continua
"""

import os
import sys
import json
import time
import logging
import threading
import subprocess
from pathlib import Path
from datetime import datetime
from typing import Dict, List, Optional, Any
from dataclasses import dataclass, asdict
from queue import Queue, PriorityQueue
from enum import Enum

# EIDOS Knowledge Integration (Rust o Python automático)
try:
    from .knowledge_integration import get_knowledge_db, is_using_rust, observe_file_with_fallback
    KNOWLEDGE_DB_AVAILABLE = True
except ImportError:
    KNOWLEDGE_DB_AVAILABLE = False
    logger.warning("⚠️  Knowledge DB integration not available")

# BTW Command Handler (real-time status updates)
try:
    from .btw_command_handler import get_btw_handler
    BTW_AVAILABLE = True
except ImportError:
    BTW_AVAILABLE = False

# Process Manager (background processes)
try:
    from .process_manager import get_process_manager, ProcessPriority
    PROCESS_MANAGER_AVAILABLE = True
except ImportError:
    PROCESS_MANAGER_AVAILABLE = False

# Detailed Logger (Claude-style logging)
try:
    from .detailed_logger import get_detailed_logger, ChangeType
    DETAILED_LOGGER_AVAILABLE = True
except ImportError:
    DETAILED_LOGGER_AVAILABLE = False

# Eidos Browser (for interactive web learning)
try:
    from .eidos_browser import EidosBrowser
    BROWSER_AVAILABLE = True
except ImportError:
    BROWSER_AVAILABLE = False

# Web Hunter (for deep web research)
try:
    from .web_hunter import WebHunter
    WEB_HUNTER_AVAILABLE = True
except ImportError:
    WEB_HUNTER_AVAILABLE = False

# Opinion Seeker (for expert opinions)
try:
    from .opinion_seeker import OpinionSeeker
    OPINION_SEEKER_AVAILABLE = True
except ImportError:
    OPINION_SEEKER_AVAILABLE = False

# Self Improvement
try:
    from .self_improvement import get_self_improvement
    SELF_IMPROVEMENT_AVAILABLE = True
except ImportError:
    SELF_IMPROVEMENT_AVAILABLE = False

# Multimodal Learner (Video learning with Whisper + Vision + CLIP)
try:
    from .multimodal_learner import get_multimodal_learner
    MULTIMODAL_LEARNER_AVAILABLE = True
except ImportError:
    MULTIMODAL_LEARNER_AVAILABLE = False

# Math Art Generator
try:
    from .math_art import get_math_art_generator, generate_math_art
    MATH_ART_AVAILABLE = True
except ImportError:
    MATH_ART_AVAILABLE = False

# Fooocus Integration (Stable Diffusion)
try:
    from .fooocus_integration import get_fooocus, generate_image
    FOOOCUS_AVAILABLE = True
except ImportError:
    FOOOCUS_AVAILABLE = False

# Video Generation
try:
    from core.vision_60fps import generate_video_60fps
    VIDEO_60FPS_AVAILABLE = True
except ImportError:
    VIDEO_60FPS_AVAILABLE = False


# Configuración
EIDOS_HOME = Path.home() / ".eidos"
LEARNING_QUEUE_FILE = EIDOS_HOME / "learning_queue.json"
LEARNED_CONTENT_DB = EIDOS_HOME / "learned_content.json"
LEARNING_LOG = EIDOS_HOME / "logs" / "continuous_learning.log"

# Crear directorios
EIDOS_HOME.mkdir(exist_ok=True)
(EIDOS_HOME / "logs").mkdir(exist_ok=True)
(EIDOS_HOME / "downloads").mkdir(exist_ok=True)
(EIDOS_HOME / "transcripts").mkdir(exist_ok=True)
(EIDOS_HOME / "extracted_knowledge").mkdir(exist_ok=True)

# Logging con rotación (max 5MB, 3 backups)
from logging.handlers import RotatingFileHandler
rotating_handler = RotatingFileHandler(LEARNING_LOG, maxBytes=5*1024*1024, backupCount=3)
rotating_handler.setFormatter(logging.Formatter('[%(asctime)s] [Continuous Learner] %(levelname)s - %(message)s'))
logging.basicConfig(
    level=logging.INFO,
    handlers=[
        rotating_handler,
        logging.StreamHandler()
    ]
)
logger = logging.getLogger(__name__)


class SourceType(Enum):
    """Tipos de fuentes de aprendizaje"""
    VIDEO_YOUTUBE = "video_youtube"
    VIDEO_LOCAL = "video_local"
    BOOK_PDF = "book_pdf"
    BOOK_EPUB = "book_epub"
    WEB_ARTICLE = "web_article"
    WEB_DOCUMENTATION = "web_documentation"
    AUDIO_PODCAST = "audio_podcast"
    CODE_REPOSITORY = "code_repository"
    PAPER_ACADEMIC = "paper_academic"


class Priority(Enum):
    """Prioridad de aprendizaje"""
    CRITICAL = 1   # Aprender AHORA (ej: bug crítico en proyecto actual)
    HIGH = 2       # Aprender pronto (ej: nueva tecnología que usarás mañana)
    MEDIUM = 3     # Aprender cuando puedas (ej: tutorial interesante)
    LOW = 4        # Aprender en background (ej: tema general)


@dataclass
class LearningTask:
    """Tarea de aprendizaje"""
    id: str
    source_type: SourceType
    url: Optional[str]
    local_path: Optional[Path]
    title: str
    description: str
    priority: Priority
    tags: List[str]
    added_at: str
    started_at: Optional[str] = None
    completed_at: Optional[str] = None
    status: str = "pending"  # pending, processing, completed, failed
    extracted_knowledge: Optional[Dict] = None
    error: Optional[str] = None

    def __lt__(self, other):
        """Para comparación en PriorityQueue"""
        if self.priority.value != other.priority.value:
            return self.priority.value < other.priority.value
        return self.added_at < other.added_at

    def __le__(self, other):
        return self.priority.value <= other.priority.value

    def __gt__(self, other):
        return self.priority.value > other.priority.value

    def __ge__(self, other):
        return self.priority.value >= other.priority.value

    def to_dict(self):
        """Convierte a diccionario para JSON"""
        data = asdict(self)
        data['source_type'] = self.source_type.value
        data['priority'] = self.priority.value
        if self.local_path:
            data['local_path'] = str(self.local_path)
        return data

    @classmethod
    def from_dict(cls, data: Dict):
        """Crea desde diccionario"""
        data['source_type'] = SourceType(data['source_type'])
        data['priority'] = Priority(data['priority'])
        if data.get('local_path'):
            data['local_path'] = Path(data['local_path'])
        return cls(**data)


class ContinuousLearner:
    """
    Sistema de aprendizaje continuo de EIDOS

    Funciona 24/7 en background procesando fuentes de conocimiento
    """

    def __init__(self):
        self.learning_queue = PriorityQueue()
        self.learned_content = {}
        self.is_running = False
        self.worker_thread = None

        # Initialize integrated components
        if PROCESS_MANAGER_AVAILABLE:
            self.process_manager = get_process_manager(max_concurrent=5)
            logger.info("✅ Process Manager integrated")
        else:
            self.process_manager = None

        if DETAILED_LOGGER_AVAILABLE:
            self.detailed_logger = get_detailed_logger("continuous_learner")
            logger.info("✅ Detailed Logger integrated")
        else:
            self.detailed_logger = None

        # Cargar estado persistente
        self._load_queue()
        self._load_learned_content()

        logger.info("🎓 EIDOS Continuous Learner iniciado")

    def add_youtube_video(self, url: str, title: str = "", priority: Priority = Priority.MEDIUM, tags: List[str] = None):
        """Agregar video de YouTube para aprender"""
        task = LearningTask(
            id=self._generate_id(),
            source_type=SourceType.VIDEO_YOUTUBE,
            url=url,
            local_path=None,
            title=title or f"YouTube Video {url}",
            description=f"Aprender de video: {url}",
            priority=priority,
            tags=tags or [],
            added_at=datetime.now().isoformat()
        )

        self._add_task(task)
        logger.info(f"📹 Video agregado a cola: {title}")

        return task.id

    def add_pdf_book(self, path: str, title: str = "", priority: Priority = Priority.MEDIUM, tags: List[str] = None):
        """Agregar libro PDF para aprender"""
        task = LearningTask(
            id=self._generate_id(),
            source_type=SourceType.BOOK_PDF,
            url=None,
            local_path=Path(path),
            title=title or Path(path).name,
            description=f"Aprender de libro: {Path(path).name}",
            priority=priority,
            tags=tags or [],
            added_at=datetime.now().isoformat()
        )

        self._add_task(task)
        logger.info(f"📚 Libro PDF agregado: {title}")

        return task.id

    def add_web_article(self, url: str, title: str = "", priority: Priority = Priority.LOW, tags: List[str] = None):
        """Agregar artículo web para aprender"""
        task = LearningTask(
            id=self._generate_id(),
            source_type=SourceType.WEB_ARTICLE,
            url=url,
            local_path=None,
            title=title or url,
            description=f"Aprender de artículo: {url}",
            priority=priority,
            tags=tags or [],
            added_at=datetime.now().isoformat()
        )

        self._add_task(task)
        logger.info(f"🌐 Artículo web agregado: {title}")

        return task.id

    def add_github_repo(self, url: str, title: str = "", priority: Priority = Priority.HIGH, tags: List[str] = None):
        """Agregar repositorio GitHub para aprender"""
        task = LearningTask(
            id=self._generate_id(),
            source_type=SourceType.CODE_REPOSITORY,
            url=url,
            local_path=None,
            title=title or url.split('/')[-1],
            description=f"Aprender de repositorio: {url}",
            priority=priority,
            tags=tags or ['code', 'repository'],
            added_at=datetime.now().isoformat()
        )

        self._add_task(task)
        logger.info(f"💻 Repositorio agregado: {title}")

        return task.id

    def start_learning(self):
        """Inicia el aprendizaje continuo en background"""
        if self.is_running:
            logger.warning("⚠️ Ya está ejecutándose")
            return

        self.is_running = True
        self.worker_thread = threading.Thread(target=self._learning_loop, daemon=True)
        self.worker_thread.start()

        logger.info("🚀 Aprendizaje continuo iniciado - EIDOS nunca parará de aprender")

    def stop_learning(self):
        """Detiene el aprendizaje (temporal)"""
        self.is_running = False
        if self.worker_thread:
            self.worker_thread.join(timeout=5)

        logger.info("⏸️ Aprendizaje pausado temporalmente")

    def _learning_loop(self):
        """Loop principal de aprendizaje 24/7"""
        logger.info("🔄 Loop de aprendizaje continuo iniciado")

        while self.is_running:
            try:
                # Obtener siguiente tarea (prioridad)
                if not self.learning_queue.empty():
                    priority, task = self.learning_queue.get()

                    logger.info(f"📖 Procesando: {task.title} (prioridad: {task.priority.name})")

                    # Procesar según tipo
                    self._process_task(task)

                    # Guardar estado
                    self._save_learned_content()
                else:
                    # Sin tareas, esperar
                    time.sleep(10)

            except Exception as e:
                logger.error(f"❌ Error en learning loop: {e}")
                import traceback
                logger.error(traceback.format_exc())
                time.sleep(5)

    def _process_task(self, task: LearningTask):
        """Procesa una tarea de aprendizaje"""
        task.status = "processing"
        task.started_at = datetime.now().isoformat()
        start_time = time.time()

        # Detailed log: Learning started
        if self.detailed_logger:
            self.detailed_logger.log_learning_start(
                source=task.url or str(task.local_path),
                source_type=task.source_type.value
            )

        # Update BTW context (real-time status)
        if BTW_AVAILABLE:
            btw = get_btw_handler()
            btw.update_context(
                task=f"Aprendiendo de {task.source_type.value}",
                description=task.title,
                thoughts=f"Procesando {task.source_type.value}: {task.description}"
            )

        try:
            if task.source_type == SourceType.VIDEO_YOUTUBE:
                knowledge = self._learn_from_youtube(task)
            elif task.source_type == SourceType.BOOK_PDF:
                knowledge = self._learn_from_pdf(task)
            elif task.source_type == SourceType.WEB_ARTICLE:
                knowledge = self._learn_from_web(task)
            elif task.source_type == SourceType.CODE_REPOSITORY:
                knowledge = self._learn_from_github(task)
            else:
                logger.warning(f"⚠️ Tipo de fuente no implementado aún: {task.source_type}")
                knowledge = None

            if knowledge:
                task.extracted_knowledge = knowledge
                task.status = "completed"
                task.completed_at = datetime.now().isoformat()

                # Guardar conocimiento en memoria
                self.learned_content[task.id] = task.to_dict()

                # Guardar en Knowledge DB (Rust o Python)
                self._store_in_knowledge_db(task, knowledge)

                # Detailed log: Learning completed
                if self.detailed_logger:
                    duration = time.time() - start_time
                    self.detailed_logger.log_learning_complete(
                        source=task.url or str(task.local_path),
                        knowledge_extracted=knowledge,
                        duration=duration
                    )

                logger.info(f"✅ Completado: {task.title}")
            else:
                task.status = "failed"
                task.error = "No se pudo extraer conocimiento"
                logger.error(f"❌ Falló: {task.title}")

                # Detailed log: Error
                if self.detailed_logger:
                    self.detailed_logger.log(
                        description=f"Failed to learn from: {task.title}",
                        change_type=ChangeType.ERROR,
                        before=f"Status: processing",
                        after=f"Status: failed - No knowledge extracted"
                    )

        except Exception as e:
            task.status = "failed"
            task.error = str(e)
            logger.error(f"❌ Error procesando {task.title}: {e}")
            import traceback
            logger.error(traceback.format_exc())

    def _learn_from_youtube(self, task: LearningTask) -> Optional[Dict]:
        """
        Aprende de un video de YouTube

        Proceso:
        1. Descargar video con yt-dlp
        2. Extraer audio
        3. Transcribir con whisper
        4. Analizar transcript para extraer conocimiento
        5. Guardar conocimiento estructurado
        """
        logger.info(f"🎥 Descargando video: {task.url}")

        try:
            # Verificar yt-dlp instalado
            if not self._check_command("yt-dlp"):
                logger.error("yt-dlp no instalado. Instalar: pip install yt-dlp")
                return None

            # Descargar metadata primero
            video_info = self._get_youtube_metadata(task.url)

            # Descargar subtítulos si existen (más rápido que transcribir)
            transcript = self._download_youtube_subtitles(task.url)

            if not transcript:
                # No hay subtítulos, descargar audio y transcribir
                logger.info("No hay subtítulos, descargando audio...")
                audio_file = self._download_youtube_audio(task.url)

                if audio_file:
                    transcript = self._transcribe_audio(audio_file)

            if transcript:
                # Extraer conocimiento del transcript
                knowledge = self._extract_knowledge_from_text(
                    transcript,
                    source=f"YouTube: {task.url}",
                    metadata=video_info
                )

                # Guardar transcript
                transcript_file = EIDOS_HOME / "transcripts" / f"{task.id}.txt"
                transcript_file.write_text(transcript)

                logger.info(f"✅ Video procesado: {len(transcript)} caracteres de transcript")

                return knowledge
            else:
                logger.error("No se pudo obtener transcript")
                return None

        except Exception as e:
            logger.error(f"Error procesando YouTube: {e}")
            return None

    def _learn_from_pdf(self, task: LearningTask) -> Optional[Dict]:
        """
        Aprende de un libro PDF

        Proceso:
        1. Extraer texto con PyPDF2/pdfplumber
        2. Dividir en chunks
        3. Analizar cada chunk
        4. Extraer conceptos, código, diagramas
        5. Crear índice de conocimiento
        """
        logger.info(f"📚 Procesando PDF: {task.local_path}")

        try:
            # Verificar que existe
            if not task.local_path.exists():
                logger.error(f"Archivo no existe: {task.local_path}")
                return None

            # Extraer texto
            text = self._extract_text_from_pdf(task.local_path)

            if text:
                # Extraer conocimiento
                knowledge = self._extract_knowledge_from_text(
                    text,
                    source=f"PDF: {task.local_path.name}",
                    metadata={'path': str(task.local_path)}
                )

                logger.info(f"✅ PDF procesado: {len(text)} caracteres")

                return knowledge
            else:
                logger.error("No se pudo extraer texto del PDF")
                return None

        except Exception as e:
            logger.error(f"Error procesando PDF: {e}")
            return None

    def _learn_from_web(self, task: LearningTask) -> Optional[Dict]:
        """
        Aprende de artículo web

        Proceso:
        1. Usar EidosBrowser si disponible (mejor extracción)
        2. Fallback a requests + BeautifulSoup
        3. Extraer contenido principal (sin ads, navbar, etc)
        4. Convertir a markdown
        5. Analizar contenido
        6. Extraer código si hay
        """
        logger.info(f"🌐 Descargando artículo: {task.url}")

        # Try EIDOS Browser first (better extraction for interactive sites)
        if BROWSER_AVAILABLE:
            try:
                logger.debug("   Using EIDOS Browser for enhanced extraction...")
                browser = EidosBrowser()
                content_data = browser.extract_content(task.url)

                if content_data:
                    knowledge = self._extract_knowledge_from_text(
                        content_data.get('text', ''),
                        source=f"Web (Browser): {task.url}",
                        metadata={
                            'url': task.url,
                            'code_snippets': content_data.get('code_blocks', []),
                            'links': content_data.get('links', [])[:20]
                        }
                    )

                    logger.info(f"✅ Artículo procesado (Browser): {len(content_data.get('text', ''))} caracteres")
                    return knowledge

            except Exception as e:
                logger.debug(f"EIDOS Browser failed, falling back to requests: {e}")

        # Fallback to traditional method
        try:
            import requests
            from bs4 import BeautifulSoup

            # Descargar
            response = requests.get(task.url, timeout=30)
            response.raise_for_status()

            # Parsear
            soup = BeautifulSoup(response.content, 'html.parser')

            # Extraer contenido principal
            # Intentar encontrar el artículo
            article = soup.find('article') or soup.find('main') or soup.find('body')

            if article:
                # Remover scripts, styles, ads
                for tag in article.find_all(['script', 'style', 'nav', 'footer', 'header']):
                    tag.decompose()

                text = article.get_text(separator='\n', strip=True)

                # Extraer código si hay
                code_blocks = article.find_all(['code', 'pre'])
                code_snippets = [block.get_text() for block in code_blocks]

                # Extraer conocimiento
                knowledge = self._extract_knowledge_from_text(
                    text,
                    source=f"Web: {task.url}",
                    metadata={
                        'url': task.url,
                        'code_snippets': code_snippets
                    }
                )

                logger.info(f"✅ Artículo procesado: {len(text)} caracteres, {len(code_snippets)} bloques de código")

                return knowledge
            else:
                logger.error("No se pudo encontrar contenido principal")
                return None

        except Exception as e:
            logger.error(f"Error procesando web: {e}")
            return None

    def _learn_from_github(self, task: LearningTask) -> Optional[Dict]:
        """
        Aprende de repositorio GitHub

        Proceso:
        1. Clonar repo
        2. Analizar estructura
        3. Procesar archivos de código
        4. Leer README, docs
        5. Extraer patrones
        """
        logger.info(f"💻 Clonando repositorio: {task.url}")

        try:
            # Directorio temporal para clonar
            clone_dir = EIDOS_HOME / "downloads" / f"repo_{task.id}"
            clone_dir.mkdir(parents=True, exist_ok=True)

            # Clonar (shallow clone para rapidez)
            subprocess.run(
                ['git', 'clone', '--depth', '1', task.url, str(clone_dir)],
                check=True,
                capture_output=True
            )

            # Analizar repositorio
            knowledge = self._analyze_repository(clone_dir)

            logger.info(f"✅ Repositorio analizado")

            return knowledge

        except Exception as e:
            logger.error(f"Error procesando GitHub: {e}")
            return None

    def learn_and_persist(self, text: str, source: str = "continuous_learner",
                           topic: str = "", metadata: Dict = None) -> int:
        """Extrae conocimiento y lo persiste en el motor lógico.

        S119 #242: Integración bidireccional con autonomous_research_loop.
        Combina extracción enriquecida (conceptos, tecnologías) +
        persistencia en el motor lógico (hechos atómicos).

        Returns: número de hechos/conceptos nuevos aprendidos.
        """
        learned = 0
        try:
            # 1. Extracción enriquecida (conceptos, tecnologías, etc.)
            enriched = self._extract_knowledge_from_text(
                text, source=source, metadata=metadata
            )
            # 2. Persistir hechos atómicos vía motor lógico
            try:
                from core.eidos_logic import get_logic_reasoner, TruthValue
                logic = get_logic_reasoner()
                if len(logic._concept_index) < 100:
                    logic.load_from_graph(max_nodes=5000)
                    logic.load_edges(max_edges=5000)
                    logic.load_seed_facts()
                    logic._load_learned_facts()
                # Hechos del texto
                learned += logic.learn_from_text(
                    text, subject_hint=topic, confidence=0.60
                )
                # Inyectar conceptos extraídos
                subj = topic.lower() if topic else source.lower()
                for concept in enriched.get("concepts", [])[:5]:
                    c_name = concept if isinstance(concept, str) else concept.get("name", str(concept))
                    if len(c_name) > 3:
                        fact_key = f"{subj} relacionado_con {c_name.lower()}"
                        if fact_key not in logic.facts:
                            logic.facts[fact_key] = TruthValue(0.55, 0.75)
                            learned += 1
                # Inyectar tecnologías
                for tech in enriched.get("technologies", [])[:5]:
                    t_name = tech if isinstance(tech, str) else tech.get("name", str(tech))
                    if len(t_name) > 2:
                        fact_key = f"{subj} usa_tecnologia {t_name.lower()}"
                        if fact_key not in logic.facts:
                            logic.facts[fact_key] = TruthValue(0.55, 0.75)
                            learned += 1
            except ImportError:
                log.debug("continuous_learner: motor lógico no disponible")
            except Exception as e:
                log.debug("continuous_learner: persistencia lógica falló: %s", e)

            if learned:
                log.info("continuous_learner #242: %d items persistidos de '%s'",
                         learned, source)
            return learned
        except Exception as e:
            log.warning("learn_and_persist error: %s", e)
            return 0

    def _extract_knowledge_from_text(self, text: str, source: str, metadata: Dict = None) -> Dict:
        """
        Extrae conocimiento estructurado de texto

        Identifica:
        - Conceptos clave
        - Tecnologías mencionadas
        - Código y comandos
        - Pasos y procedimientos
        - Enlaces y referencias
        """
        knowledge = {
            'source': source,
            'metadata': metadata or {},
            'extracted_at': datetime.now().isoformat(),
            'text_length': len(text),
            'concepts': [],
            'technologies': [],
            'code_snippets': [],
            'commands': [],
            'links': [],
            'summary': ''
        }

        # Extracción HÍBRIDA: regex barato+exacto para URLs/comandos +
        # LLM (vía eidos_llm) para conceptos/tecnologías/método/resumen.
        # Captura PATRONES OBSERVABLES de CÓMO trabaja la IA/carácter
        # que generó el texto — clave para que EIDOS aprenda de cada
        # conexión (idea #1 de SER). Fallback a regex si LLM no responde.
        import re, json as _json

        # 1) regex (rápido y determinista): URLs + comandos + code blocks
        urls = re.findall(r'https?://[^\s<>"{}|\\^`\[\]]+', text)
        knowledge['links'] = list(set(urls))[:20]
        knowledge['commands'] = re.findall(
            r'^[$#>]\s*(.+)$', text, re.MULTILINE)[:50]
        knowledge['code_snippets'] = re.findall(
            r'```[\w]*\n(.*?)```', text, re.DOTALL)[:10]

        # 2) LLM: lo rico (conceptos, tecnologías, método observable, resumen)
        knowledge['method_observed'] = ""   # NUEVO: patrón de trabajo
        text_sample = text[:3500]           # acotar prompt
        try:
            try:
                from core.eidos_llm import complete as _llm
            except ImportError:
                import sys as _s, os as _o
                _s.path.insert(0, _o.path.dirname(
                    _o.path.dirname(_o.path.abspath(__file__))))
                from core.eidos_llm import complete as _llm
            prompt = (
                "Analiza este texto y extrae conocimiento estructurado en "
                "JSON ESTRICTO con estas claves exactas:\n"
                '{"concepts": [hasta 8 ideas clave, cortas],\n'
                ' "technologies": [tecnologías/herramientas mencionadas],\n'
                ' "method_observed": "1-2 frases describiendo CÓMO se '
                'abordó el problema (pasos, enfoque, estilo) — patrón '
                'observable de trabajo",\n'
                ' "summary": "resumen 2-3 frases del texto"}\n\n'
                f"FUENTE: {source}\nTEXTO:\n{text_sample}\n\n"
                "Devuelve SOLO el JSON, nada más.")
            r = _llm(prompt, max_tokens=500, inject_identity=False)
            if r and r.ok and r.text:
                t = r.text.strip()
                m = re.search(r"\{.*\}", t, re.DOTALL)
                if m:
                    try:
                        d = _json.loads(m.group(0))
                        knowledge['concepts'] = list(d.get('concepts', []))[:8]
                        knowledge['technologies'] = list(
                            d.get('technologies', []))[:15]
                        knowledge['method_observed'] = str(
                            d.get('method_observed', ''))[:400]
                        knowledge['summary'] = str(d.get('summary', ''))[:600]
                    except _json.JSONDecodeError:
                        knowledge['summary'] = t[:600]
                else:
                    knowledge['summary'] = t[:600]
            else:
                raise RuntimeError("llm sin respuesta")
        except Exception as _e:  # noqa: BLE001
            log.debug("extract_knowledge LLM falló (%s) → fallback regex", _e)
            # Fallback defensivo: regex básico legacy — NO romper si LLM cae.
            tech_re = {
                'Python': r'\b(python|pip|django|flask|fastapi|pandas)\b',
                'Rust': r'\b(rust|cargo|tokio|serde|actix)\b',
                'JavaScript': r'\b(javascript|node|npm|react|vue)\b',
                'Docker': r'\b(docker|dockerfile|container|compose)\b',
                'Kubernetes': r'\b(kubernetes|k8s|kubectl|helm)\b',
            }
            for t, p in tech_re.items():
                if re.search(p, text, re.IGNORECASE):
                    knowledge['technologies'].append(t)
            knowledge['summary'] = text[:500].strip() + "..."

        return knowledge

    def _check_command(self, command: str) -> bool:
        """Verifica si un comando está disponible"""
        try:
            subprocess.run(['which', command], check=True, capture_output=True)
            return True
        except Exception:
            return False

    def _get_youtube_metadata(self, url: str) -> Dict:
        """Obtiene metadata de video YouTube"""
        try:
            result = subprocess.run(
                ['yt-dlp', '--dump-json', '--no-download', url],
                capture_output=True,
                text=True,
                check=True
            )
            return json.loads(result.stdout)
        except Exception:
            return {}

    def _download_youtube_subtitles(self, url: str) -> Optional[str]:
        """Descarga subtítulos de YouTube si existen"""
        try:
            subtitle_file = EIDOS_HOME / "transcripts" / f"temp_subs_{int(time.time())}.txt"

            subprocess.run(
                ['yt-dlp', '--write-auto-subs', '--sub-lang', 'en,es', '--skip-download',
                 '--convert-subs', 'srt', '-o', str(subtitle_file), url],
                capture_output=True,
                check=True
            )

            # Buscar archivo de subtítulos generado
            srt_files = list(subtitle_file.parent.glob(f"{subtitle_file.stem}*.srt"))

            if srt_files:
                # Leer y convertir SRT a texto plano
                return self._srt_to_text(srt_files[0])

            return None

        except Exception as e:
            logger.debug(f"No se pudieron descargar subtítulos: {e}")
            return None

    def _download_youtube_audio(self, url: str) -> Optional[Path]:
        """Descarga audio de YouTube"""
        try:
            audio_file = EIDOS_HOME / "downloads" / f"audio_{int(time.time())}.mp3"

            subprocess.run(
                ['yt-dlp', '-x', '--audio-format', 'mp3', '-o', str(audio_file), url],
                check=True,
                capture_output=True
            )

            return audio_file if audio_file.exists() else None

        except Exception as e:
            logger.error(f"Error descargando audio: {e}")
            return None

    def _transcribe_audio(self, audio_file: Path) -> Optional[str]:
        """
        Transcribe audio usando Whisper (faster-whisper optimizado)

        Optimizaciones para bajo consumo:
        - Modelo 'tiny' o 'base' (pequeño, rápido)
        - compute_type='int8' para CPU (menor memoria)
        - beam_size=1 para velocidad máxima
        - Procesa en chunks si el audio es muy largo
        """
        try:
            from faster_whisper import WhisperModel

            logger.info(f"🎤 Transcribiendo audio: {audio_file.name}")
            logger.info(f"   Tamaño: {audio_file.stat().st_size / 1024 / 1024:.1f} MB")

            # Usar modelo 'tiny' para máxima velocidad y bajo consumo
            # 'tiny' = 39M params, ~100MB RAM, 10x más rápido que 'base'
            # Cambiar a 'base' si necesitas más precisión
            model_size = "tiny"

            logger.info(f"   Cargando modelo Whisper '{model_size}'...")

            # Configuración optimizada para bajo consumo:
            # - device="cpu" (no GPU necesaria)
            # - compute_type="int8" (cuantización, 4x menos memoria)
            # - num_workers=1 (menos threads = menos CPU)
            model = WhisperModel(
                model_size,
                device="cpu",
                compute_type="int8",
                num_workers=1
            )

            # Transcribir con configuración rápida
            # - beam_size=1 (greedy, más rápido)
            # - vad_filter=True (Voice Activity Detection, ignora silencios)
            # - language=None (auto-detect, o especificar 'en', 'es', etc.)
            segments, info = model.transcribe(
                str(audio_file),
                beam_size=1,          # Greedy decoding (más rápido)
                vad_filter=True,      # Filtrar silencios
                vad_parameters=dict(min_silence_duration_ms=500),
                language=None,        # Auto-detect
                task="transcribe"
            )

            logger.info(f"   Idioma detectado: {info.language} (confianza: {info.language_probability:.1%})")
            logger.info(f"   Transcribiendo...")

            # Recolectar segmentos
            transcript_parts = []
            segment_count = 0

            for segment in segments:
                transcript_parts.append(segment.text)
                segment_count += 1

                # Log cada 10 segmentos para no spamear
                if segment_count % 10 == 0:
                    logger.info(f"      Procesados {segment_count} segmentos...")

            transcript = " ".join(transcript_parts)

            logger.info(f"✅ Transcripción completada!")
            logger.info(f"   Total segmentos: {segment_count}")
            logger.info(f"   Caracteres: {len(transcript)}")
            logger.info(f"   Palabras estimadas: {len(transcript.split())}")

            return transcript

        except ImportError as e:
            logger.error("❌ faster-whisper no instalado!")
            logger.error("   Instalar: pip install --user --break-system-packages faster-whisper")
            return None
        except Exception as e:
            logger.error(f"❌ Error transcribiendo audio: {e}")
            import traceback
            logger.error(traceback.format_exc())
            return None

    def _srt_to_text(self, srt_file: Path) -> str:
        """Convierte SRT a texto plano"""
        import re

        content = srt_file.read_text()

        # Remover números de línea y timestamps
        text = re.sub(r'\d+\n\d{2}:\d{2}:\d{2},\d{3} --> \d{2}:\d{2}:\d{2},\d{3}\n', '', content)

        # Remover líneas vacías extras
        text = re.sub(r'\n\n+', '\n', text)

        return text.strip()

    def _extract_text_from_pdf(self, pdf_path: Path) -> Optional[str]:
        """Extrae texto de PDF"""
        try:
            import PyPDF2

            text = []
            with open(pdf_path, 'rb') as f:
                reader = PyPDF2.PdfReader(f)
                for page in reader.pages:
                    text.append(page.extract_text())

            return '\n'.join(text)

        except ImportError:
            logger.error("PyPDF2 no instalado. Instalar: pip install PyPDF2")
            return None
        except Exception as e:
            logger.error(f"Error extrayendo PDF: {e}")
            return None

    def _analyze_repository(self, repo_path: Path) -> Dict:
        """Analiza repositorio clonado"""
        knowledge = {
            'type': 'repository',
            'path': str(repo_path),
            'files': [],
            'languages': set(),
            'readme': None
        }

        # Leer README
        for readme_name in ['README.md', 'README.txt', 'README']:
            readme_path = repo_path / readme_name
            if readme_path.exists():
                knowledge['readme'] = readme_path.read_text()
                break

        # Analizar archivos
        for file_path in repo_path.rglob('*'):
            if file_path.is_file() and not file_path.name.startswith('.'):
                # Detectar lenguaje por extensión
                ext = file_path.suffix
                lang_map = {
                    '.py': 'python',
                    '.rs': 'rust',
                    '.js': 'javascript',
                    '.ts': 'typescript',
                    '.go': 'go',
                }

                if ext in lang_map:
                    knowledge['languages'].add(lang_map[ext])
                    knowledge['files'].append(str(file_path))

        knowledge['languages'] = list(knowledge['languages'])

        return knowledge

    def _store_in_knowledge_db(self, task: LearningTask, knowledge: Dict):
        """
        Almacena conocimiento en Knowledge DB (Rust o Python).

        Esto permite consultas rápidas y búsqueda semántica.
        """
        if not KNOWLEDGE_DB_AVAILABLE:
            return

        try:
            kb = get_knowledge_db(verbose=False)
            if kb is None:
                return

            # Extraer libraries/tecnologías del knowledge
            technologies = knowledge.get('technologies', [])

            # Si hay archivos de código, observarlos
            files = knowledge.get('files', [])
            for file_path in files[:10]:  # Max 10 archivos para no saturar
                try:
                    observe_file_with_fallback(file_path)
                except Exception as e:
                    logger.debug(f"No se pudo observar {file_path}: {e}")

            # Loggear que se usó Rust
            if is_using_rust():
                logger.debug(f"  → Stored in Rust KB (5x faster)")
            else:
                logger.debug(f"  → Stored in Python KB")

        except Exception as e:
            logger.error(f"Error storing in Knowledge DB: {e}")

    def _add_task(self, task: LearningTask):
        """Agrega tarea a la cola con prioridad"""
        self.learning_queue.put((task.priority.value, task))
        self._save_queue()

    def _generate_id(self) -> str:
        """Genera ID único para tarea"""
        import hashlib
        return hashlib.md5(f"{datetime.now().isoformat()}".encode()).hexdigest()[:12]

    def _save_queue(self):
        """Guarda cola de aprendizaje a disco"""
        try:
            import json
            items = []
            while not self.learning_queue.empty():
                priority, task = self.learning_queue.get()
                task_dict = asdict(task)
                # SourceType enum is not JSON-serializable → convert to str
                if 'source_type' in task_dict and hasattr(task_dict['source_type'], 'value'):
                    task_dict['source_type'] = task_dict['source_type'].value
                items.append({"priority": priority, "task": task_dict})
            with open(LEARNING_QUEUE_FILE, "w") as f:
                json.dump(items, f)
            for item in items:
                # Restore SourceType enum from string value when re-queuing
                td = item["task"]
                if 'source_type' in td and isinstance(td['source_type'], str):
                    td['source_type'] = SourceType(td['source_type'])
                self.learning_queue.put((item["priority"], LearningTask(**td)))
        except Exception as e:
            logger.error(f"Error guardando cola: {e}")

    def _load_queue(self):
        """Carga cola de aprendizaje desde disco"""
        try:
            import json
            if LEARNING_QUEUE_FILE.exists():
                with open(LEARNING_QUEUE_FILE) as f:
                    items = json.load(f)
                for item in items:
                    td = item["task"]
                    # Restore SourceType enum from string value
                    if 'source_type' in td and isinstance(td['source_type'], str):
                        td['source_type'] = SourceType(td['source_type'])
                    self.learning_queue.put((item["priority"], LearningTask(**td)))
                logger.info(f"Cola cargada: {len(items)} items")
        except Exception as e:
            logger.warning(f"Error cargando cola: {e}")

    def _save_learned_content(self):
        """Guarda contenido aprendido"""
        with open(LEARNED_CONTENT_DB, 'w') as f:
            json.dump(self.learned_content, f, indent=2)

    def _load_learned_content(self):
        """Carga contenido aprendido"""
        if LEARNED_CONTENT_DB.exists():
            with open(LEARNED_CONTENT_DB) as f:
                self.learned_content = json.load(f)

    def get_stats(self) -> Dict:
        """Estadísticas de aprendizaje"""
        total = len(self.learned_content)
        completed = sum(1 for task in self.learned_content.values() if task['status'] == 'completed')
        failed = sum(1 for task in self.learned_content.values() if task['status'] == 'failed')

        return {
            'total_tasks': total,
            'completed': completed,
            'failed': failed,
            'success_rate': (completed / total * 100) if total > 0 else 0,
            'queue_size': self.learning_queue.qsize(),
            'is_running': self.is_running
        }


# Singleton global
_learner_instance = None


def get_continuous_learner() -> ContinuousLearner:
    """Obtiene instancia singleton del learner"""
    global _learner_instance
    if _learner_instance is None:
        _learner_instance = ContinuousLearner()
    return _learner_instance


if __name__ == '__main__':
    # Test
    learner = get_continuous_learner()

    # Agregar algunas tareas de prueba
    learner.add_youtube_video(
        "https://www.youtube.com/watch?v=dQw4w9WgXcQ",
        title="Rust Programming Tutorial",
        priority=Priority.HIGH,
        tags=['rust', 'programming']
    )

    learner.add_web_article(
        "https://blog.rust-lang.org/",
        title="Rust Blog",
        priority=Priority.MEDIUM,
        tags=['rust', 'news']
    )

    # Iniciar aprendizaje
    learner.start_learning()

    print("✅ Continuous Learner iniciado")
    print(f"📊 Stats: {learner.get_stats()}")

    # Mantener vivo
    try:
        while True:
            time.sleep(60)
            print(f"📊 Stats: {learner.get_stats()}")
    except KeyboardInterrupt:
        learner.stop_learning()
        print("\n👋 Detenido")
