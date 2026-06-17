#!/usr/bin/env python3
"""
EIDOS Vision-Learner Bridge
============================
Conecta la pipeline de visión con la pipeline de aprendizaje.

Lo que EIDOS VE → lo que EIDOS APRENDE.

Flujo:
  1. Captura visual (pantalla, video, imagen)
  2. Análisis (OCR, CLIP, change detection)
  3. Extracción de conocimiento (código, diagramas, texto técnico)
  4. Almacenamiento en knowledge_db + brain_memory

Modos:
  - watch_and_learn(): Monitoreo continuo de pantalla → aprende en tiempo real
  - learn_from_image(): Imagen estática → extrae conocimiento
  - learn_from_video(): Video local → multimodal learning (delega a multimodal_learner)
  - learn_from_screen_session(): Graba N segundos de pantalla → aprende todo
"""
from __future__ import annotations

import json
import logging
import threading
import time
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import List, Dict, Optional, Any

logger = logging.getLogger("eidos.vision_learner")

# ══════════════════════════════════════════════════════════════════════════════
# Lazy imports con flags
# ══════════════════════════════════════════════════════════════════════════════

HAS_VISION = False
HAS_CLIP = False
HAS_PERCEPTION = False
HAS_KNOWLEDGE_DB = False
HAS_BRAIN_MEMORY = False
HAS_MULTIMODAL = False
HAS_CONTINUOUS = False
HAS_CPP_VISION = False

try:
    from .vision_lightweight import get_lightweight_vision, FrameAnalysis
    HAS_VISION = True
except Exception as e:
    logger.warning("vision_lightweight not available: %s", e)

try:
    from .clip_vision import get_clip_vision, ImageAnalysis
    HAS_CLIP = True
except Exception as e:
    logger.warning("clip_vision not available: %s", e)

try:
    from .perception import take_screenshot, ocr_screenshot, analyze_screen
    HAS_PERCEPTION = True
except Exception as e:
    logger.warning("perception not available: %s", e)

try:
    from .knowledge_db import KnowledgeDB
    HAS_KNOWLEDGE_DB = True
except Exception as e:
    logger.warning("knowledge_db not available: %s", e)

try:
    from .brain_memory import get_brain_memory
    HAS_BRAIN_MEMORY = True
except Exception as e:
    logger.warning("brain_memory not available: %s", e)

try:
    from .multimodal_learner import get_multimodal_learner
    HAS_MULTIMODAL = True
except Exception as e:
    logger.warning("multimodal_learner not available: %s", e)

try:
    from .continuous_learner import get_continuous_learner
    HAS_CONTINUOUS = True
except Exception as e:
    logger.warning("continuous_learner not available: %s", e)

try:
    from .vision_ctypes import CPP_VISION_AVAILABLE, get_vision_engine
    HAS_CPP_VISION = CPP_VISION_AVAILABLE
except Exception as e:
    logger.warning("vision_ctypes (C++ 60fps) not available: %s", e)


# ══════════════════════════════════════════════════════════════════════════════
# Data Types
# ══════════════════════════════════════════════════════════════════════════════

@dataclass
class VisualKnowledge:
    """Conocimiento extraído de una fuente visual."""
    source: str                          # "screen", "image", "video"
    source_path: str = ""
    timestamp: str = ""
    text_content: List[str] = field(default_factory=list)
    code_snippets: List[Dict[str, str]] = field(default_factory=list)
    diagrams_detected: int = 0
    semantic_categories: List[str] = field(default_factory=list)
    libraries_found: List[str] = field(default_factory=list)
    technical_score: float = 0.0         # 0-1, qué tan técnico es el contenido
    raw_ocr: str = ""
    clip_analysis: Optional[Dict] = None

    def to_dict(self) -> Dict[str, Any]:
        return {
            "source": self.source,
            "source_path": self.source_path,
            "timestamp": self.timestamp,
            "text_content": self.text_content,
            "code_snippets": self.code_snippets,
            "diagrams_detected": self.diagrams_detected,
            "semantic_categories": self.semantic_categories,
            "libraries_found": self.libraries_found,
            "technical_score": self.technical_score,
        }


@dataclass
class LearnSession:
    """Sesión de aprendizaje visual."""
    session_id: str
    started_at: str
    ended_at: str = ""
    frames_analyzed: int = 0
    knowledge_items: int = 0
    total_text_extracted: int = 0
    total_code_found: int = 0
    libraries_discovered: List[str] = field(default_factory=list)
    status: str = "running"  # running, completed, error


# ══════════════════════════════════════════════════════════════════════════════
# Patterns para detección de código y librerías
# ══════════════════════════════════════════════════════════════════════════════

import re

CODE_INDICATORS = [
    r'def\s+\w+\s*\(',           # Python function
    r'class\s+\w+[\(:]',          # Python/Java class
    r'import\s+\w+',              # Python import
    r'from\s+\w+\s+import',       # Python from import
    r'function\s+\w+\s*\(',       # JavaScript function
    r'const\s+\w+\s*=',           # JavaScript const
    r'fn\s+\w+\s*\(',             # Rust function
    r'func\s+\w+\s*\(',           # Go function
    r'pub\s+(fn|struct|enum)',     # Rust pub
    r'#include\s*[<"]',           # C/C++ include
    r'package\s+\w+',             # Go/Java package
    r'\w+\s*=\s*\{',             # Dict/object literal
    r'if\s+\w+.*:$',             # Python if
    r'for\s+\w+\s+in\s+',        # Python for
    r'try\s*:',                   # Python try
    r'except\s+\w+',             # Python except
    r'async\s+(def|fn|function)', # Async
    r'await\s+\w+',              # Await
    r'return\s+\w+',             # Return
    r'self\.\w+',                # Python self
]

LIBRARY_PATTERNS = {
    "python": [
        r'import\s+([\w.]+)',
        r'from\s+([\w.]+)\s+import',
    ],
    "javascript": [
        r'require\s*\(\s*[\'"]([^"\']+)',
        r'from\s+[\'"]([^"\']+)',
        r'import\s+.*from\s+[\'"]([^"\']+)',
    ],
    "rust": [
        r'use\s+([\w:]+)',
        r'extern\s+crate\s+(\w+)',
    ],
    "go": [
        r'import\s+"([^"]+)"',
        r'"([^"]+/[^"]+)"',
    ],
}

# Librerías conocidas (para matching rápido)
KNOWN_LIBRARIES = {
    "torch", "tensorflow", "keras", "numpy", "pandas", "sklearn",
    "flask", "django", "fastapi", "requests", "aiohttp", "httpx",
    "opencv", "cv2", "pillow", "pil", "matplotlib", "seaborn",
    "react", "vue", "angular", "express", "nextjs", "svelte",
    "tokio", "serde", "actix", "rocket", "axum", "clap",
    "gin", "echo", "fiber", "gorm", "cobra",
    "chromadb", "langchain", "openai", "anthropic", "ollama",
    "selenium", "playwright", "beautifulsoup", "scrapy",
    "pytest", "unittest", "jest", "mocha",
    "docker", "kubernetes", "terraform", "ansible",
    "nmap", "scapy", "metasploit", "burp",
    "sqlalchemy", "peewee", "prisma", "mongoose",
    "redis", "celery", "rabbitmq", "kafka",
}


# ══════════════════════════════════════════════════════════════════════════════
# Bridge principal
# ══════════════════════════════════════════════════════════════════════════════

class VisionLearnerBridge:
    """Puente entre lo que EIDOS ve y lo que aprende."""

    def __init__(self):
        self._vision = None
        self._clip = None
        self._knowledge_db = None
        self._brain_memory = None
        self._cpp_vision = None
        self._watching = False
        self._watch_thread: Optional[threading.Thread] = None
        self._sessions: List[LearnSession] = []
        self._knowledge_log = Path.home() / ".eidos" / "vision_learning.jsonl"
        self._knowledge_log.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()

        # Stats
        self.stats = {
            "total_frames": 0,
            "total_knowledge": 0,
            "total_code": 0,
            "total_text": 0,
            "libraries_found": set(),
            "sessions": 0,
        }

        logger.info(f"VisionLearnerBridge init — vision={HAS_VISION} clip={HAS_CLIP} "
                     f"perception={HAS_PERCEPTION} knowledge={HAS_KNOWLEDGE_DB} "
                     f"memory={HAS_BRAIN_MEMORY} cpp_vision={HAS_CPP_VISION}")

    # ─── Lazy loaders ─────────────────────────────────────────────────────

    def _get_vision(self):
        if self._vision is None and HAS_VISION:
            self._vision = get_lightweight_vision(enable_clip=HAS_CLIP)
        return self._vision

    def _get_clip(self):
        if self._clip is None and HAS_CLIP:
            self._clip = get_clip_vision()
        return self._clip

    def _get_knowledge_db(self):
        if self._knowledge_db is None and HAS_KNOWLEDGE_DB:
            self._knowledge_db = KnowledgeDB()
        return self._knowledge_db

    def _get_brain_memory(self):
        if self._brain_memory is None and HAS_BRAIN_MEMORY:
            self._brain_memory = get_brain_memory()
        return self._brain_memory

    def _get_cpp_vision(self):
        if self._cpp_vision is None and HAS_CPP_VISION:
            self._cpp_vision = get_vision_engine()
        return self._cpp_vision

    # ─── Análisis de texto extraído ───────────────────────────────────────

    def _detect_code(self, text: str) -> List[Dict[str, str]]:
        """Detecta fragmentos de código en texto OCR."""
        if not text or len(text) < 10:
            return []

        snippets = []
        lines = text.split('\n')
        code_block = []
        in_code = False

        for line in lines:
            is_code_line = any(re.search(p, line) for p in CODE_INDICATORS)
            # También: indentación consistente, operadores, brackets
            has_structure = bool(re.match(r'^(\s{2,}|\t)', line)) and len(line.strip()) > 3

            if is_code_line or (in_code and has_structure):
                code_block.append(line)
                in_code = True
            else:
                if code_block and len(code_block) >= 2:
                    snippet = '\n'.join(code_block)
                    lang = self._detect_language(snippet)
                    snippets.append({"code": snippet, "language": lang})
                code_block = []
                in_code = False

        # Último bloque
        if code_block and len(code_block) >= 2:
            snippet = '\n'.join(code_block)
            lang = self._detect_language(snippet)
            snippets.append({"code": snippet, "language": lang})

        return snippets

    def _detect_language(self, code: str) -> str:
        """Detecta el lenguaje de un snippet de código."""
        if 'def ' in code or 'import ' in code or 'self.' in code:
            return "python"
        if 'function ' in code or 'const ' in code or '=>' in code:
            return "javascript"
        if 'fn ' in code or 'let mut' in code or '::' in code:
            return "rust"
        if 'func ' in code or 'package ' in code or ':=' in code:
            return "go"
        if '#include' in code or '->(' in code:
            return "cpp"
        return "unknown"

    def _extract_libraries(self, text: str) -> List[str]:
        """Extrae nombres de librerías del texto."""
        found = set()

        for lang, patterns in LIBRARY_PATTERNS.items():
            for pattern in patterns:
                matches = re.findall(pattern, text, re.MULTILINE)
                for m in matches:
                    lib = m.split('.')[0].split('/')[0].strip()
                    if lib and len(lib) > 1 and lib.lower() not in {'os', 'sys', 'io', 're'}:
                        found.add(lib.lower())

        # Match directo con librerías conocidas
        text_lower = text.lower()
        for lib in KNOWN_LIBRARIES:
            if lib in text_lower:
                found.add(lib)

        return sorted(found)

    def _compute_technical_score(self, text: str, code_count: int,
                                  libs_count: int) -> float:
        """Calcula qué tan técnico es el contenido (0-1)."""
        score = 0.0
        if not text:
            return 0.0

        # Código encontrado
        score += min(code_count * 0.15, 0.45)

        # Librerías
        score += min(libs_count * 0.1, 0.3)

        # Indicadores de código en el texto
        code_matches = sum(1 for p in CODE_INDICATORS if re.search(p, text))
        score += min(code_matches * 0.03, 0.15)

        # Palabras técnicas
        tech_words = {'api', 'server', 'database', 'function', 'class', 'module',
                      'error', 'debug', 'deploy', 'config', 'endpoint', 'query',
                      'thread', 'async', 'cache', 'memory', 'cpu', 'gpu',
                      'container', 'docker', 'kubernetes', 'network', 'protocol'}
        text_lower = text.lower()
        tech_count = sum(1 for w in tech_words if w in text_lower)
        score += min(tech_count * 0.02, 0.1)

        return min(score, 1.0)

    # ─── Core: analizar una imagen/frame y extraer conocimiento ───────────

    def analyze_and_learn(self, image_path: Optional[str] = None,
                          frame=None, source: str = "screen") -> VisualKnowledge:
        """Analiza una imagen o frame y extrae conocimiento.

        Args:
            image_path: Ruta a imagen en disco
            frame: numpy array (frame de video o screenshot)
            source: "screen", "image", o "video"

        Returns:
            VisualKnowledge con todo lo extraído
        """
        vk = VisualKnowledge(
            source=source,
            source_path=image_path or "",
            timestamp=datetime.now().isoformat(),
        )

        # 1. OCR — extraer texto
        ocr_text = ""
        if image_path and HAS_PERCEPTION:
            elements = ocr_screenshot(image_path)
            ocr_text = ' '.join(e.text for e in elements if e.text)
            vk.raw_ocr = ocr_text

        if frame is not None and HAS_VISION:
            vision = self._get_vision()
            if vision:
                analysis = vision.analyze_frame(frame, force=True)
                if analysis.text_detected:
                    ocr_text = analysis.text_detected
                    vk.raw_ocr = ocr_text
                if analysis.code_detected:
                    for code in analysis.code_detected:
                        vk.code_snippets.append({
                            "code": code, "language": self._detect_language(code)
                        })
                if analysis.semantic_categories:
                    vk.semantic_categories = [
                        c.get("category", c) if isinstance(c, dict) else str(c)
                        for c in analysis.semantic_categories
                    ]

        # 2. CLIP — clasificación semántica (si hay imagen en disco)
        if image_path and HAS_CLIP:
            clip = self._get_clip()
            if clip:
                try:
                    img_analysis = clip.analyze_image(image_path)
                    vk.clip_analysis = {
                        "primary": img_analysis.primary_category.category
                            if img_analysis.primary_category else "unknown",
                        "confidence": img_analysis.confidence_score,
                        "technical": img_analysis.technical_content,
                    }
                    if img_analysis.primary_category:
                        vk.semantic_categories.append(
                            img_analysis.primary_category.category
                        )
                except Exception as e:
                    logger.debug(f"CLIP analysis failed: {e}")

        # 3. Extraer código del OCR
        if ocr_text:
            code_from_ocr = self._detect_code(ocr_text)
            vk.code_snippets.extend(code_from_ocr)
            vk.text_content = [line.strip() for line in ocr_text.split('\n')
                               if line.strip() and len(line.strip()) > 5]

        # 4. Extraer librerías
        all_text = ocr_text + ' '.join(s.get("code", "") for s in vk.code_snippets)
        vk.libraries_found = self._extract_libraries(all_text)

        # 5. Score técnico
        vk.technical_score = self._compute_technical_score(
            all_text, len(vk.code_snippets), len(vk.libraries_found)
        )

        # 6. Contar diagramas (desde CLIP categories)
        diagram_cats = {"architecture_diagram", "flowchart", "sequence_diagram",
                        "er_diagram", "network_diagram", "deployment_diagram"}
        vk.diagrams_detected = sum(
            1 for c in vk.semantic_categories if c.lower().replace(" ", "_") in diagram_cats
        )

        # 7. Almacenar si es contenido técnico relevante
        if vk.technical_score >= 0.2 or vk.code_snippets or vk.libraries_found:
            self._store_knowledge(vk)

        return vk

    # ─── Almacenamiento ──────────────────────────────────────────────────

    def _store_knowledge(self, vk: VisualKnowledge) -> None:
        """Almacena el conocimiento extraído en knowledge_db y brain_memory."""

        # 1. Log en JSONL
        with self._lock:
            try:
                with open(self._knowledge_log, 'a') as f:
                    f.write(json.dumps(vk.to_dict(), ensure_ascii=False) + '\n')
            except Exception as e:
                logger.error(f"Error writing knowledge log: {e}")

        # 2. Knowledge DB — observar código extraído
        kb = self._get_knowledge_db()
        if kb and vk.code_snippets:
            for snippet in vk.code_snippets:
                try:
                    # Crear archivo temporal virtual para que KB lo analice
                    lang = snippet.get("language", "python")
                    ext_map = {"python": ".py", "javascript": ".js", "rust": ".rs",
                               "go": ".go", "cpp": ".cpp", "unknown": ".txt"}
                    ext = ext_map.get(lang, ".txt")
                    tmp_path = Path(f"/tmp/eidos_vision_code{ext}")
                    tmp_path.write_text(snippet["code"])
                    kb.observe_file(str(tmp_path))
                    tmp_path.unlink(missing_ok=True)
                except Exception as e:
                    logger.debug(f"KB observe failed: {e}")

        # 3. Brain Memory — recordar lo aprendido
        bm = self._get_brain_memory()
        if bm:
            summary_parts = []
            if vk.code_snippets:
                langs = set(s.get("language", "?") for s in vk.code_snippets)
                summary_parts.append(f"{len(vk.code_snippets)} code snippets ({', '.join(langs)})")
            if vk.libraries_found:
                summary_parts.append(f"libs: {', '.join(vk.libraries_found[:10])}")
            if vk.semantic_categories:
                summary_parts.append(f"type: {', '.join(vk.semantic_categories[:3])}")

            if summary_parts:
                summary = f"[VisionLearn] {vk.source}: {'; '.join(summary_parts)}"
                try:
                    bm.remember(summary, tags=["vision_learning", vk.source])
                except Exception as e:
                    logger.debug(f"BrainMemory store failed: {e}")

        # 4. Stats
        self.stats["total_knowledge"] += 1
        self.stats["total_code"] += len(vk.code_snippets)
        self.stats["total_text"] += len(vk.text_content)
        self.stats["libraries_found"].update(vk.libraries_found)

    # ─── Modos de operación ──────────────────────────────────────────────

    def learn_from_image(self, image_path: str) -> VisualKnowledge:
        """Aprende de una imagen estática."""
        return self.analyze_and_learn(image_path=image_path, source="image")

    def learn_from_screen(self) -> VisualKnowledge:
        """Captura pantalla actual y aprende de ella."""
        # Usar C++ VisionEngine si está disponible (60fps)
        if HAS_CPP_VISION:
            return self._learn_from_screen_cpp()
        
        # Fallback a Python puro
        if not HAS_PERCEPTION:
            logger.warning("Perception not available for screen capture")
            return VisualKnowledge(source="screen")

        screenshot_path = take_screenshot("vision_learn")
        if not screenshot_path:
            return VisualKnowledge(source="screen")

        return self.analyze_and_learn(image_path=screenshot_path, source="screen")

    def _learn_from_screen_cpp(self) -> VisualKnowledge:
        """Captura pantalla usando C++ VisionEngine (60fps)."""
        cpp = self._get_cpp_vision()
        if not cpp:
            return VisualKnowledge(source="screen")
        
        try:
            # Capturar frame usando C++
            frame = cpp.capture_frame()
            if frame is None:
                logger.warning("C++ capture returned None")
                return VisualKnowledge(source="screen")
            
            # Convertir frame numpy a imagen temporal
            import tempfile
            from PIL import Image
            
            with tempfile.NamedTemporaryFile(suffix='.png', delete=False) as f:
                img = Image.fromarray(frame)
                img.save(f.name)
                return self.analyze_and_learn(image_path=f.name, source="screen_cpp")
        except Exception as e:
            logger.error(f"C++ capture failed: {e}")
            return VisualKnowledge(source="screen")

    def learn_from_video(self, video_path: str,
                         title: str = "") -> Optional[Dict[str, Any]]:
        """Aprende de un video local o URL (delega a multimodal_learner)."""
        if not HAS_MULTIMODAL:
            logger.warning("MultimodalLearner not available")
            return None

        ml = get_multimodal_learner()
        knowledge = ml.learn_from_video(video_path, title=title)

        # Almacenar en brain_memory
        bm = self._get_brain_memory()
        if bm and knowledge:
            summary = (f"[VideoLearn] {knowledge.title or video_path}: "
                       f"{len(knowledge.concepts)} concepts, "
                       f"{len(knowledge.code_snippets)} code, "
                       f"{len(knowledge.libraries_mentioned)} libs")
            try:
                bm.remember(summary, tags=["video_learning"])
            except Exception:
                pass  # error no crítico, continuar
        return knowledge.__dict__ if knowledge else None

    # ─── Watch and Learn (monitoreo continuo) ────────────────────────────

    def watch_and_learn(self, duration: float = 300.0,
                         interval: float = 5.0,
                         min_technical_score: float = 0.15,
                         callback=None) -> LearnSession:
        """Monitorea la pantalla y aprende en tiempo real.

        Args:
            duration: Duración en segundos (default 5 min)
            interval: Intervalo entre capturas (default 5s)
            min_technical_score: Mínimo score técnico para guardar (default 0.15)
            callback: Función callback(VisualKnowledge) llamada por cada análisis

        Returns:
            LearnSession con resumen
        """
        session = LearnSession(
            session_id=f"vl_{int(time.time())}",
            started_at=datetime.now().isoformat(),
        )
        self._sessions.append(session)
        self.stats["sessions"] += 1

        if not HAS_VISION:
            logger.error("LightweightVision not available for watch_and_learn")
            session.status = "error"
            session.ended_at = datetime.now().isoformat()
            return session

        vision = self._get_vision()
        if not vision:
            session.status = "error"
            session.ended_at = datetime.now().isoformat()
            return session

        logger.info(f"[WatchAndLearn] Starting {duration}s session, interval={interval}s")

        start_time = time.time()
        last_frame_hash = ""

        try:
            while time.time() - start_time < duration:
                if not self._watching and self._watch_thread is not None:
                    break  # Stopped externally

                # Capturar pantalla
                if HAS_PERCEPTION:
                    ss_path = take_screenshot(f"wl_{session.session_id}")
                    if ss_path:
                        vk = self.analyze_and_learn(
                            image_path=ss_path, source="screen"
                        )
                        session.frames_analyzed += 1
                        self.stats["total_frames"] += 1

                        if vk.technical_score >= min_technical_score:
                            session.knowledge_items += 1
                            session.total_text_extracted += len(vk.text_content)
                            session.total_code_found += len(vk.code_snippets)
                            for lib in vk.libraries_found:
                                if lib not in session.libraries_discovered:
                                    session.libraries_discovered.append(lib)

                            if callback:
                                callback(vk)

                        # Limpiar screenshot
                        try:
                            Path(ss_path).unlink(missing_ok=True)
                        except Exception:
                            pass  # error no crítico, continuar
                time.sleep(interval)

        except Exception as e:
            logger.error(f"[WatchAndLearn] Error: {e}")
            session.status = "error"
        else:
            session.status = "completed"

        session.ended_at = datetime.now().isoformat()
        logger.info(f"[WatchAndLearn] Session done: {session.frames_analyzed} frames, "
                     f"{session.knowledge_items} items, "
                     f"{session.total_code_found} code snippets")

        return session

    def start_watching(self, duration: float = 300.0,
                        interval: float = 5.0, **kwargs) -> str:
        """Inicia watch_and_learn en background thread."""
        if self._watching:
            return "Already watching"

        self._watching = True

        def _run():
            try:
                self.watch_and_learn(duration=duration, interval=interval, **kwargs)
            finally:
                self._watching = False

        self._watch_thread = threading.Thread(
            target=_run, daemon=True, name="eidos-vision-learner"
        )
        self._watch_thread.start()
        return f"Started watching for {duration}s (interval {interval}s)"

    def stop_watching(self) -> str:
        """Detiene el monitoreo."""
        if not self._watching:
            return "Not watching"
        self._watching = False
        return "Stopped watching"

    @property
    def is_watching(self) -> bool:
        return self._watching

    # ─── Status y stats ──────────────────────────────────────────────────

    def get_status(self) -> Dict[str, Any]:
        """Retorna estado actual."""
        return {
            "watching": self._watching,
            "modules": {
                "vision": HAS_VISION,
                "clip": HAS_CLIP,
                "perception": HAS_PERCEPTION,
                "knowledge_db": HAS_KNOWLEDGE_DB,
                "brain_memory": HAS_BRAIN_MEMORY,
                "multimodal": HAS_MULTIMODAL,
            },
            "stats": {
                **{k: v if not isinstance(v, set) else sorted(v)
                   for k, v in self.stats.items()},
            },
            "sessions": len(self._sessions),
            "last_session": (self._sessions[-1].__dict__
                             if self._sessions else None),
        }

    def get_learning_history(self, limit: int = 20) -> List[Dict]:
        """Lee el log de aprendizaje visual."""
        entries = []
        if self._knowledge_log.exists():
            try:
                with open(self._knowledge_log) as f:
                    for line in f:
                        line = line.strip()
                        if line:
                            entries.append(json.loads(line))
            except Exception:
                pass  # error no crítico, continuar
        return entries[-limit:]


# ══════════════════════════════════════════════════════════════════════════════
# Singleton
# ══════════════════════════════════════════════════════════════════════════════

_bridge: Optional[VisionLearnerBridge] = None

def get_vision_learner_bridge() -> VisionLearnerBridge:
    global _bridge
    if _bridge is None:
        _bridge = VisionLearnerBridge()
    return _bridge


# ══════════════════════════════════════════════════════════════════════════════
# CLI
# ══════════════════════════════════════════════════════════════════════════════

if __name__ == "__main__":
    import sys
    logging.basicConfig(level=logging.INFO, format="%(message)s")

    bridge = get_vision_learner_bridge()
    status = bridge.get_status()
    print(f"VisionLearnerBridge — modules: {status['modules']}")

    if len(sys.argv) > 1:
        cmd = sys.argv[1]
        if cmd == "screen":
            vk = bridge.learn_from_screen()
            print(f"  Text: {len(vk.text_content)} lines")
            print(f"  Code: {len(vk.code_snippets)} snippets")
            print(f"  Libs: {vk.libraries_found}")
            print(f"  Score: {vk.technical_score:.2f}")
        elif cmd == "image" and len(sys.argv) > 2:
            vk = bridge.learn_from_image(sys.argv[2])
            print(f"  Text: {len(vk.text_content)} lines")
            print(f"  Code: {len(vk.code_snippets)} snippets")
            print(f"  Libs: {vk.libraries_found}")
            print(f"  Score: {vk.technical_score:.2f}")
        elif cmd == "watch":
            dur = float(sys.argv[2]) if len(sys.argv) > 2 else 60
            session = bridge.watch_and_learn(duration=dur)
            print(f"  Frames: {session.frames_analyzed}")
            print(f"  Knowledge: {session.knowledge_items}")
            print(f"  Code: {session.total_code_found}")
            print(f"  Libs: {session.libraries_discovered}")
        elif cmd == "status":
            for k, v in status.items():
                print(f"  {k}: {v}")
    else:
        print("Usage: python vision_learner_bridge.py [screen|image <path>|watch <seconds>|status]")
