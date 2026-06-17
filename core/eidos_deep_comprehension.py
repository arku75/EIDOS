"""
core/eidos_deep_comprehension.py — Comprensión PROFUNDA de CUALQUIER contenido [S122-I]
======================================================================================
SER quiere que EIDOS comprenda CUALQUIER cosa, no solo libros:
  - Libros y documentos (PDF, EPUB, TXT, MD, HTML, DOC)
  - Código fuente (Python, JS, Bash, C, Go, Rust, etc.)
  - Texto plano (artículos, notas, logs)
  - Video (transcripción vía whisper/yt-dlp — placeholder)
  - Audio/música/sonidos (análisis vía ffmpeg/whisper — placeholder)
  - Cualquier archivo que EIDOS pueda leer

Pipeline genérico (independiente del tipo):
  1. DETECTAR tipo de contenido (extensión + análisis textual)
  2. EXTRAER texto (doc_learner para docs, cat para código, ffmpeg para audio/video)
  3. SECCIONAR según tipo (capítulos, funciones, headings, timestamps)
  4. RESUMIR cada sección (cascada LLM con contexto encadenado)
  5. EXTRAER conceptos → persistir al GRAFO
  6. AUTOTEST de comprensión

Reemplaza y extiende a eidos_book_comprehension.py (que ahora es un alias).
"""

from __future__ import annotations

import json
import logging
import os
import re
import subprocess
import time
from pathlib import Path
from typing import Dict, List, Optional, Tuple

log = logging.getLogger("eidos.deep_comprehension")

# ── Constantes ─────────────────────────────────────────────────────────────
BRAIN_DB = Path.home() / ".eidos" / "evolution_brain.db"

# Extensiones por tipo de contenido (para detección rápida)
CONTENT_TYPE_MAP = {
    # Documentos / libros
    ".pdf": "document", ".epub": "document", ".mobi": "document",
    ".doc": "document", ".docx": "document", ".odt": "document",
    ".rtf": "document", ".djvu": "document",
    # Texto
    ".txt": "text", ".md": "text", ".rst": "text", ".org": "text",
    ".log": "text", ".csv": "text", ".json": "text", ".xml": "text",
    ".yaml": "text", ".yml": "text", ".toml": "text", ".ini": "text",
    ".cfg": "text", ".conf": "text",
    # Código
    ".py": "code", ".js": "code", ".ts": "code", ".jsx": "code",
    ".tsx": "code", ".sh": "code", ".bash": "code", ".zsh": "code",
    ".c": "code", ".cpp": "code", ".cc": "code", ".h": "code",
    ".hpp": "code", ".go": "code", ".rs": "code", ".java": "code",
    ".kt": "code", ".swift": "code", ".rb": "code", ".php": "code",
    ".lua": "code", ".r": "code", ".sql": "code", ".pl": "code",
    ".hs": "code", ".scala": "code", ".clj": "code", ".ex": "code",
    ".exs": "code", ".elm": "code", ".vue": "code", ".svelte": "code",
    ".html": "code", ".css": "code", ".scss": "code", ".less": "code",
    # Video
    ".mp4": "video", ".mkv": "video", ".webm": "video", ".avi": "video",
    ".mov": "video", ".flv": "video", ".wmv": "video",
    # Audio
    ".mp3": "audio", ".wav": "audio", ".flac": "audio", ".ogg": "audio",
    ".opus": "audio", ".m4a": "audio", ".aac": "audio", ".wma": "audio",
    # Imagen (placeholder — necesitaría OCR/VLM)
    ".png": "image", ".jpg": "image", ".jpeg": "image", ".gif": "image",
    ".webp": "image", ".bmp": "image", ".svg": "image", ".tiff": "image",
}


def _slug(text: str, n: int = 30) -> str:
    s = re.sub(r"[^a-z0-9]+", "_", (text or "content").lower()).strip("_")
    return (s or "content")[:n]


def _ask_llm(prompt: str, timeout: int = 90) -> str:
    """LLM cloud-first SIN umbral de longitud ni fallback al Mac.

    [S122-I bugfix] No usar ask_llm() de eidos_learn aquí: rechaza respuestas
    <20 chars (rompía _score_answer→"0.8" y respuestas de 1 palabra) y cae a
    Ollama local/Mac apagado (:11435) con timeout largo → la comprensión nunca
    terminaba. Aquí: Groq → DeepSeek directos (rápidos, aceptan respuestas
    cortas). Último recurso: Ollama local (NO el Mac apagado).
    """
    # 1. Groq (rápido, ~1s)
    try:
        from core.eidos_learn import _call_groq
        ans = _call_groq(prompt, timeout=min(timeout, 20))
        if ans and ans.strip():
            return ans.strip()
    except Exception as e:
        log.debug("_call_groq falló: %s", e)
    # 2. DeepSeek (potente, ~2s)
    try:
        from core.eidos_learn import _call_deepseek
        ans = _call_deepseek(prompt, timeout=min(timeout, 30))
        if ans and ans.strip():
            return ans.strip()
    except Exception as e:
        log.debug("_call_deepseek falló: %s", e)
    # 3. Ollama LOCAL (:11434, no el Mac) — último recurso
    try:
        from core.eidos_learn import _call_ollama, OLLAMA_MODEL, OLLAMA_URL
        ans = _call_ollama(prompt, model=OLLAMA_MODEL,
                           timeout=min(timeout, 60), url=OLLAMA_URL)
        if ans and ans.strip():
            return ans.strip()
    except Exception as e:
        log.debug("_call_ollama falló: %s", e)
    return ""


def _parse_json_list(ans: str) -> List[Dict]:
    """Parsea una lista JSON de objetos de forma ROBUSTA, tolerando:
      - markdown ```json ... ```
      - JSON truncado (max_tokens cortó el array a mitad → sin `]` final)
      - texto extra antes/después

    [S122-I] Las respuestas del LLM (Groq max_tokens=300) suelen truncarse en
    listas largas → json.loads fallaba y devolvía []. Aquí extraemos cada
    objeto {...} balanceado individualmente, así sobreviven los completos.
    """
    if not ans:
        return []
    # Quitar fences markdown
    ans = re.sub(r"```(?:json)?\s*", "", ans).replace("```", "").strip()

    # 1. Intento directo (lista completa y válida)
    try:
        d = json.loads(ans)
        if isinstance(d, list):
            return [x for x in d if isinstance(x, dict)]
    except json.JSONDecodeError:
        pass

    # 2. Extraer objetos {...} balanceados uno a uno (sobrevive truncamiento)
    objs: List[Dict] = []
    depth = 0
    start = -1
    in_str = False
    esc = False
    for i, ch in enumerate(ans):
        if esc:
            esc = False
            continue
        if ch == "\\":
            esc = True
            continue
        if ch == '"':
            in_str = not in_str
            continue
        if in_str:
            continue
        if ch == "{":
            if depth == 0:
                start = i
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0 and start >= 0:
                frag = ans[start:i + 1]
                try:
                    o = json.loads(frag)
                    if isinstance(o, dict):
                        objs.append(o)
                except json.JSONDecodeError:
                    pass
                start = -1
    return objs


# ── 1. Detección de tipo de contenido ──────────────────────────────────────

def detect_content_type(source: str) -> str:
    """Detecta el tipo de contenido por extensión y/o análisis del texto.

    Returns: 'document', 'code', 'text', 'video', 'audio', 'image', 'unknown'
    """
    source_path = Path(source)
    ext = source_path.suffix.lower()

    # 1. Por extensión
    if ext in CONTENT_TYPE_MAP:
        return CONTENT_TYPE_MAP[ext]

    # 2. Si no hay extensión o es desconocida, intentar leer el archivo y analizar
    if source_path.exists() and source_path.is_file():
        try:
            with open(source_path, errors="ignore") as f:
                sample = f.read(2000)
            return _analyze_text_type(sample)
        except Exception:
            pass

    # 3. ¿Es una URL o tema abstracto?
    if source.startswith(("http://", "https://")):
        return "url"

    return "unknown"


def _analyze_text_type(text: str) -> str:
    """Analiza un texto para determinar si es código, documento, o texto plano."""
    lines = text.split("\n")
    if not lines:
        return "text"

    # Heurísticas de código
    code_indicators = 0
    doc_indicators = 0

    for line in lines[:50]:
        stripped = line.strip()
        if not stripped:
            continue
        # Código: llaves, punto y coma, imports, def, function, class, var, let, const
        if re.search(r'^\s*(import |from |def |class |function |var |let |const |#include|package |require\()', stripped):
            code_indicators += 2
        if re.search(r'[{};]\s*$', stripped) and not stripped.startswith("#"):
            code_indicators += 1
        if re.search(r'^\s*(//|#|--|/\*)', stripped):
            code_indicators += 1
        # Documento: párrafos largos, puntuación de prosa
        if len(stripped) > 80 and re.search(r'[.,;:!?»"«]', stripped):
            doc_indicators += 1
        if re.search(r'(capítulo|chapter|sección|section|índice|index|prólogo|preface)', stripped, re.I):
            doc_indicators += 3

    if code_indicators > doc_indicators + 3:
        return "code"
    elif doc_indicators > code_indicators:
        return "document"
    return "text"


# ── 2. Extracción de texto ─────────────────────────────────────────────────

def _extract_text(path: str, content_type: str = "") -> str:
    """Extrae texto de cualquier tipo de archivo.

    Para documentos usa doc_learner. Para código/texto lee directamente.
    Para video/audio usa ffmpeg+whisper (si disponible).
    """
    path = Path(path).expanduser()
    if not path.exists():
        return ""

    if content_type in ("video", "audio"):
        return _extract_media_text(path, content_type)

    if content_type == "image":
        return _extract_image_description(path)

    if content_type in ("document",):
        try:
            from core.doc_learner import DocLearner
            return DocLearner()._extract_text(str(path))
        except Exception as e:
            log.warning("doc_learner falló para %s: %s, leyendo como texto", path, e)

    # Código, texto, o fallback: leer directamente
    try:
        return path.read_text()
    except UnicodeDecodeError:
        try:
            return path.read_bytes().decode("latin-1")
        except Exception:
            return f"[No se pudo leer el archivo: {path}]"


def _which(tool: str) -> bool:
    """¿Está el binario `tool` en PATH?"""
    from shutil import which
    return which(tool) is not None


def transcribe_audio(audio_path: str, lang: Optional[str] = None) -> str:
    """Transcribe un archivo de audio a texto. Auto-detecta backend de whisper.

    Backends en orden de preferencia:
      1. faster-whisper (Python, eficiente CTranslate2)
      2. openai-whisper (Python)
      3. whisper CLI
      4. whisper.cpp (whisper-cli / main)

    Returns: texto transcrito, o "" si no hay backend.
    """
    audio_path = str(audio_path)
    # [S122-I] Modelo whisper configurable. Default "small" en Kali (buen
    # equilibrio calidad/RAM ~500MB). El Mac (más RAM) puede usar medium/large-v3
    # con EIDOS_WHISPER_MODEL. NO rotamos modelos: cargar varios = más RAM (peor).
    _wmodel = os.environ.get("EIDOS_WHISPER_MODEL", "small")

    # 1. faster-whisper (preferido: rápido y ligero)
    try:
        from faster_whisper import WhisperModel  # type: ignore
        model = WhisperModel(_wmodel, device="cpu", compute_type="int8")
        segments, _info = model.transcribe(audio_path, language=lang)
        text = " ".join(seg.text for seg in segments).strip()
        if text:
            log.info("transcribe_audio: faster-whisper OK (%d chars)", len(text))
            return text
    except ImportError:
        pass
    except Exception as e:
        log.debug("faster-whisper falló: %s", e)

    # 2. openai-whisper (Python)
    try:
        import whisper  # type: ignore
        model = whisper.load_model(_wmodel)
        result = model.transcribe(audio_path, language=lang)
        text = (result.get("text") or "").strip()
        if text:
            log.info("transcribe_audio: openai-whisper OK (%d chars)", len(text))
            return text
    except ImportError:
        pass
    except Exception as e:
        log.debug("openai-whisper falló: %s", e)

    # 3. whisper CLI
    if _which("whisper"):
        try:
            import tempfile
            with tempfile.TemporaryDirectory() as td:
                subprocess.run(
                    ["whisper", audio_path, "--model", _wmodel, "--output_dir", td,
                     "--output_format", "txt"] + (["--language", lang] if lang else []),
                    capture_output=True, timeout=600)
                for f in Path(td).glob("*.txt"):
                    text = f.read_text().strip()
                    if text:
                        log.info("transcribe_audio: whisper CLI OK")
                        return text
        except Exception as e:
            log.debug("whisper CLI falló: %s", e)

    # 4. whisper.cpp
    for binname in ("whisper-cli", "main"):
        if _which(binname):
            try:
                r = subprocess.run([binname, "-f", audio_path, "-nt"],
                                   capture_output=True, text=True, timeout=600)
                if r.stdout.strip():
                    log.info("transcribe_audio: whisper.cpp OK")
                    return r.stdout.strip()
            except Exception as e:
                log.debug("whisper.cpp falló: %s", e)

    return ""


def _extract_media_text(path: Path, media_type: str) -> str:
    """Extrae texto de video/audio: ffmpeg extrae audio → whisper transcribe."""
    if not _which("ffmpeg"):
        return (f"[{media_type.upper()}: {path.name}]\n"
                f"Para transcribir {media_type}s necesito ffmpeg: sudo apt install ffmpeg")

    # 1. Extraer audio a WAV 16kHz mono (formato óptimo para whisper)
    import tempfile
    try:
        wav_fd, wav_path = tempfile.mkstemp(suffix=".wav")
        os.close(wav_fd)
        r = subprocess.run(
            ["ffmpeg", "-y", "-i", str(path), "-ar", "16000", "-ac", "1",
             "-vn", "-f", "wav", wav_path],
            capture_output=True, timeout=300)
        if r.returncode != 0:
            return (f"[{media_type.upper()}: {path.name}]\n"
                    f"ffmpeg no pudo extraer el audio: {r.stderr.decode(errors='ignore')[:200]}")

        # 2. Transcribir
        text = transcribe_audio(wav_path)
        try:
            os.unlink(wav_path)
        except OSError:
            pass

        if text:
            return f"[{media_type.upper()} transcrito: {path.name}]\n\n{text}"
        return (f"[{media_type.upper()}: {path.name}]\n"
                f"Extraje el audio con ffmpeg, pero no hay backend de transcripción. "
                f"Instala uno: pip install faster-whisper (recomendado) o openai-whisper.")
    except subprocess.TimeoutExpired:
        return f"[{media_type.upper()}: {path.name}]\nTimeout procesando el archivo (>5min)."
    except Exception as e:
        return f"[{media_type.upper()}: {path.name}]\nError: {e}"


def _extract_image_description(path: Path) -> str:
    """Describe una imagen combinando VLM (moondream) + OCR (tesseract)."""
    parts = []
    # 1. Descripción visual con VLM moondream (vía perception.analyze_screen)
    try:
        from core.perception import analyze_screen
        desc = analyze_screen(
            str(path),
            question="Describe detalladamente el contenido de esta imagen.",
            deep=False)  # [S122-I] sin max_tokens (SER): sistema vivo
        if desc and not desc.startswith("[VISION ERROR]"):
            parts.append(f"Descripción visual: {desc}")
    except Exception as e:
        log.debug("analyze_screen falló: %s", e)
    # 2. Texto incrustado vía OCR
    try:
        from core.perception import ocr_screenshot
        elements = ocr_screenshot(str(path))
        ocr_text = " ".join(getattr(el, "text", "") for el in (elements or []))
        if ocr_text.strip():
            parts.append(f"Texto detectado (OCR): {ocr_text[:1500]}")
    except Exception as e:
        log.debug("ocr_screenshot falló: %s", e)

    if parts:
        return f"[IMAGEN: {path.name}]\n" + "\n\n".join(parts)
    return (f"[IMAGEN: {path.name}]\n"
            f"Para analizar imágenes necesito moondream (VLM, vía Ollama) "
            f"y/o tesseract (OCR). Verifica que Ollama esté activo.")


# ── 3. Seccionado según tipo de contenido ──────────────────────────────────

def _detect_sections(text: str, content_type: str = "text",
                     source_path: str = "") -> List[Dict]:
    """Divide el contenido en secciones lógicas según su tipo.

    Args:
        text: Texto completo del contenido.
        content_type: Tipo detectado ('document', 'code', 'text', etc.).
        source_path: Ruta del archivo original (para metadatos).

    Returns:
        [{title, length, text}, ...]
    """
    if not text or len(text.strip()) < 200:
        return [{"title": "Contenido", "length": len(text), "text": text}]

    if content_type == "code":
        sections = _section_code(text, source_path)
    elif content_type == "document":
        sections = _section_document(text)
    else:
        sections = _section_generic(text)

    # [S122-I] Guard central: NUNCA devolver vacío si hay contenido.
    # (un seccionado con umbrales estrictos podía filtrar todo en archivos
    #  cortos → comprehend se quedaría sin nada que resumir).
    if not sections:
        sections = [{"title": "Contenido", "length": len(text), "text": text}]
    return sections


def _section_document(text: str) -> List[Dict]:
    """Secciona un documento/libro por capítulos/secciones."""
    chapters = []
    lines = text.split("\n")
    current_start = 0
    current_title = "Introducción"

    ch_patterns = [
        re.compile(r"^(?:Capítulo|Chapter|CAPÍTULO|CHAPTER)\s+[\dIVX]+", re.I),
        re.compile(r"^#+\s+(?:Capítulo|Chapter)?\s*[\dIVX]+", re.I),
        re.compile(r"^(?:PARTE|Parte|PART)\s+[\dIVX]+", re.I),
        re.compile(r"^(?:Sección|Section|SECCIÓN|SECTION)\s+[\dIVX]+", re.I),
        re.compile(r"^#{1,3}\s+(.+)$"),  # Markdown headings
    ]

    for i, line in enumerate(lines):
        stripped = line.strip()
        if not stripped:
            continue

        is_start = False
        for pat in ch_patterns:
            m = pat.match(stripped)
            if m:
                is_start = True
                # Para headings markdown, usar el texto del heading como título
                if m.lastindex and m.lastindex >= 1:
                    current_title = m.group(1)[:100]
                break

        if not is_start and len(stripped) < 80 and stripped.isupper():
            if i + 1 < len(lines) and lines[i + 1].strip():
                is_start = True

        if is_start and i > current_start + 5:
            ch_text = "\n".join(lines[current_start:i]).strip()
            if len(ch_text) > 200:
                chapters.append({
                    "title": current_title,
                    "length": len(ch_text),
                    "text": ch_text,
                })
            current_title = stripped[:100]
            current_start = i

    # Último capítulo
    if current_start < len(lines):
        ch_text = "\n".join(lines[current_start:]).strip()
        if len(ch_text) > 200:
            chapters.append({
                "title": current_title,
                "length": len(ch_text),
                "text": ch_text,
            })

    if not chapters:
        return _section_generic(text)
    return chapters


def _section_code(text: str, source_path: str = "") -> List[Dict]:
    """Secciona código fuente por funciones/clases/métodos/bloques.

    Enfoque robusto e independiente del lenguaje: cada DEFINICIÓN de alto nivel
    (def/class/function/func/fn/...) inicia una sección que va desde esa línea
    hasta la siguiente definición. NO depende de llaves {} (rompía en Python,
    que usa indentación) ni de tracking de profundidad. Funciona igual para
    Python, JS, C, Go, Rust, Java, Shell, SQL, etc. [S122-I bugfix]
    """
    sections = []
    lines = text.split("\n")

    # Patrones de inicio de bloque/definición en varios lenguajes.
    # Capturamos el nivel de indentación para no fragmentar en exceso
    # (preferir definiciones de nivel superior o de clase, no closures profundos).
    block_starters = [
        # Python
        re.compile(r"^(\s*)(async def |def |class )"),
        # JS/TS
        re.compile(r"^(\s*)(export\s+)?(default\s+)?(async\s+)?(function\b|class\b|const\s+\w+\s*=\s*(async\s*)?\()"),
        # Go
        re.compile(r"^(\s*)func "),
        # Rust
        re.compile(r"^(\s*)(pub\s+)?(async\s+)?fn "),
        # Java/Kotlin/Scala/C#
        re.compile(r"^(\s*)(public\s+|private\s+|protected\s+|internal\s+)?(static\s+)?(abstract\s+)?(class |interface |enum |fun |object |void |trait )"),
        # C/C++ función (tipo retorno + nombre + paréntesis)
        re.compile(r"^(\s*)[A-Za-z_][\w\s\*&:<>,]*\s+[A-Za-z_]\w*\s*\([^;]*\)\s*\{?\s*$"),
        # Shell
        re.compile(r"^(\s*)(function\s+\w+|\w+\s*\(\)\s*\{?)"),
        # SQL
        re.compile(r"^(\s*)CREATE\s+(OR\s+REPLACE\s+)?(TABLE|INDEX|VIEW|FUNCTION|PROCEDURE|TRIGGER)", re.I),
        # Ruby/PHP
        re.compile(r"^(\s*)(def |function |class |module )"),
        # Comentarios de sección (banners)
        re.compile(r"^(\s*)(#{2,}|/{2,}|-{2,}|;{2,})\s*[=#\-*]{2,}"),
        re.compile(r"^(\s*)#+\s*(region|section|PART|SECTION|SECCIÓN)", re.I),
    ]

    def _match_starter(stripped_line: str) -> bool:
        """¿Esta línea inicia una definición? Comprobamos contra la línea
        sin indentación inicial pero conservando el patrón."""
        for pat in block_starters:
            if pat.match(stripped_line):
                return True
        return False

    # Encontrar todos los índices donde empieza una definición de nivel "bajo"
    # (indentación <= 4 espacios → top-level o método de clase, no closures hondos)
    starter_indices = []
    for i, line in enumerate(lines):
        if not line.strip():
            continue
        indent = len(line) - len(line.lstrip())
        if indent <= 4 and _match_starter(line):
            starter_indices.append((i, line.strip()[:100]))

    # Si no hay definiciones detectadas, caer a seccionado genérico
    if not starter_indices:
        return _section_generic(text)

    # Construir secciones: header (antes de la 1ª def) + cada def hasta la siguiente
    boundaries = [idx for idx, _ in starter_indices]
    titles = [title for _, title in starter_indices]

    # Header (imports/constantes antes de la primera definición)
    if boundaries[0] > 0:
        header_text = "\n".join(lines[:boundaries[0]]).strip()
        if len(header_text) > 80:
            sections.append({
                "title": "Header / imports / constantes",
                "length": len(header_text),
                "text": header_text,
            })

    # Agrupar definiciones consecutivas hasta ~600 chars por sección:
    # así no se pierden funciones cortas (que individualmente serían diminutas)
    # ni se disparan cientos de llamadas LLM en archivos enormes. [S122-I]
    _MIN_SECTION = 600
    _MAX_SECTIONS = 40
    buf_text = ""
    buf_title = ""
    for k in range(len(boundaries)):
        start = boundaries[k]
        end = boundaries[k + 1] if k + 1 < len(boundaries) else len(lines)
        chunk = "\n".join(lines[start:end]).strip()
        if not chunk:
            continue
        if not buf_text:
            buf_title = titles[k]
        buf_text = (buf_text + "\n\n" + chunk) if buf_text else chunk
        # Cerrar sección cuando alcanza tamaño mínimo
        if len(buf_text) >= _MIN_SECTION:
            sections.append({
                "title": buf_title,
                "length": len(buf_text),
                "text": buf_text,
            })
            buf_text = ""
            buf_title = ""
    # Resto acumulado
    if buf_text:
        sections.append({
            "title": buf_title or "Resto del código",
            "length": len(buf_text),
            "text": buf_text,
        })

    # Limitar nº de secciones (fusionar las últimas si hay demasiadas)
    if len(sections) > _MAX_SECTIONS:
        head = sections[:_MAX_SECTIONS - 1]
        tail_text = "\n\n".join(s["text"] for s in sections[_MAX_SECTIONS - 1:])
        head.append({"title": "Resto del código",
                     "length": len(tail_text), "text": tail_text})
        sections = head

    if not sections:
        return _section_generic(text)
    return sections


def _section_generic(text: str, chunk_size: int = 5000) -> List[Dict]:
    """Secciona texto genérico por tamaño o párrafos grandes."""
    sections = []

    # Intentar detectar headings o separadores de sección
    lines = text.split("\n")
    section_starts = [0]
    section_titles = ["Inicio"]

    for i, line in enumerate(lines):
        stripped = line.strip()
        # Headings o separadores
        if re.match(r"^#{1,3}\s+\S", stripped):
            section_starts.append(i)
            section_titles.append(stripped[:100])
        elif re.match(r"^[A-Z][A-Z\s]{10,}$", stripped):
            section_starts.append(i)
            section_titles.append(stripped[:100])
        elif stripped.startswith("---") or stripped.startswith("==="):
            section_starts.append(i)
            section_titles.append(f"Sección {len(section_starts)}")

    section_starts.append(len(lines))
    section_titles.append("Fin")

    # Construir secciones
    for j in range(len(section_starts) - 1):
        start = section_starts[j]
        end = section_starts[j + 1]
        sec_text = "\n".join(lines[start:end]).strip()
        if len(sec_text) > 200:
            sections.append({
                "title": section_titles[j] if j < len(section_titles) else f"Sección {j + 1}",
                "length": len(sec_text),
                "text": sec_text,
            })

    # Si no se detectaron secciones, dividir por tamaño
    if not sections:
        for k in range(0, len(text), chunk_size):
            chunk = text[k:k + chunk_size]
            if len(chunk.strip()) > 200:
                sections.append({
                    "title": f"Parte {len(sections) + 1}",
                    "length": len(chunk),
                    "text": chunk,
                })

    return sections


# ── 4. Pipeline de comprensión (común a todos los tipos) ───────────────────

def _summarize_section(title: str, text: str, content_type: str = "",
                       prev_summary: str = "") -> str:
    """Resume una sección usando LLM, adaptando el prompt al tipo de contenido."""
    type_hints = {
        "code": (f"Esto es una sección de CÓDIGO FUENTE. Resume qué HACE este código, "
                 f"su propósito y lógica principal (no describas la sintaxis)."),
        "document": (f"Esto es un capítulo/sección de un documento. "
                     f"Resume las ideas principales en 3-5 frases."),
        "text": "Resume este texto en 3-5 frases, capturando las ideas clave.",
        "video": "Esto es la transcripción de un video. Resume los puntos principales.",
        "audio": "Esto es la transcripción de un audio. Resume los puntos principales.",
    }
    hint = type_hints.get(content_type, type_hints["text"])

    prompt = (
        f"{hint}\n"
        f"Título: «{title}».\n"
        f"Contexto previo: {prev_summary or 'N/A'}\n\n"
        f"=== CONTENIDO ===\n{text[:6000]}\n\n"
        f"Devuelve SOLO el resumen en 3-5 frases, en español. Sin introducciones."
    )
    summary = _ask_llm(prompt)
    return summary or f"[No se pudo resumir «{title}»]"


def _extract_concepts(text: str, max_concepts: int = 10,
                      source: str = "") -> List[Dict]:
    """Extrae conceptos clave usando LLM → persiste al grafo."""
    prompt = (
        f"Extrae los {max_concepts} conceptos o ideas clave más importantes "
        f"del siguiente contenido. Para cada concepto: nombre (1-5 palabras), "
        f"definición breve (1 frase), categoría (seguridad/programación/sistema/red/IA/etc).\n\n"
        f"=== CONTENIDO ===\n{text[:5000]}\n\n"
        f'Devuelve SOLO JSON COMPACTO (una línea, sin saltos ni indentación): '
        f'[{{"concept":"...","definition":"...","category":"..."}}]'
    )
    ans = _ask_llm(prompt)
    concepts = _parse_json_list(ans)
    if not concepts:
        return []

    # Persistir al grafo
    persisted = 0
    for c in concepts[:max_concepts]:
        try:
            from core.db import get_conn
            conn = get_conn(BRAIN_DB, timeout=30)
            concept_name = c.get("concept", "")
            definition = c.get("definition", "")
            category = c.get("category", "general")
            conn.execute("""
                INSERT INTO knowledge_nodes (concept, definition, category, confidence,
                                             source, quality_score, verified)
                VALUES (?, ?, ?, 0.7, ?, 0.45, 0)
                ON CONFLICT(concept) DO UPDATE SET
                    definition = CASE WHEN LENGTH(COALESCE(definition, '')) < LENGTH(?)
                                      THEN ? ELSE definition END,
                    quality_score = CASE WHEN quality_score < 0.45
                                         THEN 0.45 ELSE quality_score END,
                    last_used = CURRENT_TIMESTAMP
            """, (concept_name, definition, category, source,
                  definition, definition))
            c["persisted"] = True
            persisted += 1
        except Exception as e:
            log.debug("persist concept «%s»: %s", c.get("concept", "?"), e)
            c["persisted"] = False

    log.info("_extract_concepts: %d extraídos, %d al grafo", len(concepts), persisted)
    return concepts[:max_concepts]


def _generate_questions(text: str, n: int = 5, content_type: str = "") -> List[Dict]:
    """Genera preguntas de autotest adaptadas al tipo de contenido."""
    type_context = {
        "code": "sobre qué hace este código y su lógica",
        "document": "de comprensión lectora sobre el contenido del documento",
        "text": "de comprensión sobre las ideas principales del texto",
        "video": "sobre el contenido y puntos clave de la transcripción",
        "audio": "sobre el contenido y puntos clave de la transcripción",
    }
    tc = type_context.get(content_type, "de comprensión sobre el contenido")

    prompt = (
        f"Genera {n} preguntas {tc}. Para cada pregunta, incluye la respuesta correcta.\n\n"
        f"=== CONTENIDO ===\n{text[:5000]}\n\n"
        f'Devuelve SOLO JSON COMPACTO (una línea): [{{"question":"...","answer":"..."}}]'
    )
    ans = _ask_llm(prompt)
    questions = _parse_json_list(ans)
    return questions[:n]


def _answer_question(question: str, text: str) -> str:
    """Responde una pregunta basándose en el contenido."""
    prompt = (
        f"Basándote SOLO en el siguiente contenido, responde a esta pregunta "
        f"de forma breve y precisa (1-3 frases).\n\n"
        f"=== CONTENIDO ===\n{text[:4000]}\n\n"
        f"Pregunta: {question}\n\nRespuesta:"
    )
    return _ask_llm(prompt, timeout=30) or "[No pude responder]"


def _score_answer(expected: str, actual: str) -> float:
    """Evalúa si la respuesta es correcta (0.0-1.0)."""
    prompt = (
        f"Evalúa si esta respuesta es correcta comparada con la esperada. "
        f"Devuelve SOLO un número entre 0.0 (incorrecta) y 1.0 (perfecta).\n\n"
        f"Esperada: {expected}\nDada: {actual}\n\nScore:"
    )
    ans = _ask_llm(prompt, timeout=30)
    try:
        return max(0.0, min(1.0, float(ans.strip())))
    except (ValueError, TypeError):
        expected_w = set(expected.lower().split())
        actual_w = set(actual.lower().split())
        if not expected_w:
            return 0.0
        return min(1.0, len(expected_w & actual_w) / len(expected_w))


# ── Extracción desde URL (vídeo con yt-dlp+whisper, o página web) ───────────

_VIDEO_HOSTS = ("youtube.com", "youtu.be", "vimeo.com", "dailymotion.com",
                "twitch.tv", "tiktok.com", "facebook.com/watch")
_MEDIA_URL_EXTS = (".mp4", ".mkv", ".webm", ".avi", ".mov", ".mp3", ".wav",
                   ".flac", ".ogg", ".m4a", ".opus")


def _extract_url_text(url: str) -> Tuple[str, str]:
    """Extrae texto de una URL. Devuelve (texto, content_type).

    - Vídeo/audio (YouTube, etc. o URL con extensión de medios) → yt-dlp baja
      el audio → whisper transcribe.
    - Otra URL → descargar página y extraer texto (BeautifulSoup).
    """
    url_l = url.lower()
    is_media = (any(h in url_l for h in _VIDEO_HOSTS)
                or url_l.split("?")[0].endswith(_MEDIA_URL_EXTS))

    if is_media and _which("yt-dlp") and _which("ffmpeg"):
        import tempfile
        try:
            td = tempfile.mkdtemp()
            out_tmpl = os.path.join(td, "media.%(ext)s")
            # Bajar solo el audio en wav 16k mono (óptimo para whisper)
            r = subprocess.run(
                ["yt-dlp", "-x", "--audio-format", "wav",
                 "--postprocessor-args", "-ar 16000 -ac 1",
                 "-o", out_tmpl, url],
                capture_output=True, text=True, timeout=600)
            wavs = list(Path(td).glob("*.wav"))
            if wavs:
                text = transcribe_audio(str(wavs[0]))
                # Limpieza
                for f in Path(td).glob("*"):
                    try:
                        f.unlink()
                    except OSError:
                        pass
                try:
                    os.rmdir(td)
                except OSError:
                    pass
                if text:
                    return text, "video"
            log.debug("yt-dlp sin audio o sin transcripción: %s", r.stderr[:200])
        except subprocess.TimeoutExpired:
            return "", "video"
        except Exception as e:
            log.debug("_extract_url_text vídeo falló: %s", e)

    # Página web: descargar y extraer texto
    try:
        import urllib.request
        req = urllib.request.Request(
            url, headers={"User-Agent": "Mozilla/5.0 (X11; Linux x86_64) EIDOS/1.0"})
        with urllib.request.urlopen(req, timeout=20) as resp:
            html = resp.read().decode("utf-8", errors="ignore")
        try:
            from bs4 import BeautifulSoup
            soup = BeautifulSoup(html, "html.parser")
            for tag in soup(["script", "style", "nav", "footer", "header"]):
                tag.decompose()
            text = soup.get_text(separator="\n")
            text = "\n".join(ln.strip() for ln in text.splitlines() if ln.strip())
            return text, "document"
        except ImportError:
            # Sin bs4: stripear tags con regex
            text = re.sub(r"<[^>]+>", " ", html)
            text = re.sub(r"\s+", " ", text).strip()
            return text, "document"
    except Exception as e:
        log.debug("_extract_url_text web falló: %s", e)
        return "", "url"


# ── 5. Pipeline principal ──────────────────────────────────────────────────

def comprehend(source: str, extract_concepts_to_graph: bool = True,
               self_evaluate: bool = True, content_type: str = "") -> Dict:
    """Comprende CUALQUIER contenido a fondo.

    Args:
        source: Ruta al archivo, URL, o tema textual.
        extract_concepts_to_graph: Extraer conceptos al grafo de conocimiento.
        self_evaluate: Generar autotest de comprensión.
        content_type: Forzar tipo ('document','code','text','video','audio').
                      Si se omite, se autodetecta.

    Returns:
        {ok, type, source, sections: [{title, length, summary}],
         concepts: [{concept, definition, category, persisted}],
         test: {questions: [{question, expected, actual, score}], average_score},
         elapsed_s}
    """
    t0 = time.time()

    _is_url = source.startswith(("http://", "https://"))

    # 0. ¿Es un tema/texto, no un archivo ni URL?
    source_path = Path(source).expanduser()
    if not source_path.exists() and not _is_url:
        # Probablemente es un tema/concepto → investigar en vez de leer archivo
        return _comprehend_topic(source, extract_concepts_to_graph, self_evaluate)

    # 0b. ¿Es una URL? → vídeo (yt-dlp+whisper) o página web
    if _is_url:
        text, content_type = _extract_url_text(source)
        if not text or len(text.strip()) < 50:
            return {"ok": False, "type": content_type or "url", "source": source,
                    "error": f"No se pudo extraer contenido de la URL ({len(text)} chars)"}
    else:
        # 1. Detectar tipo
        if not content_type:
            content_type = detect_content_type(source)
        # 2. Extraer texto
        text = _extract_text(str(source_path), content_type)
        if not text or len(text.strip()) < 50:
            return {"ok": False, "type": content_type, "source": source,
                    "error": f"No se pudo extraer texto (o es muy corto: {len(text)} chars)"}

    log.info("comprehend: «%s» tipo=%s, %d chars", source, content_type, len(text))

    # 3. Seccionar
    sections = _detect_sections(text, content_type, source)

    # 4. Resumir cada sección
    prev_summary = ""
    for sec in sections:
        sec["summary"] = _summarize_section(
            sec["title"], sec["text"], content_type, prev_summary)
        prev_summary = sec["summary"]
        if len(sections) > 3:
            time.sleep(0.3)  # Respetar rate limits cloud

    # 5. Extraer conceptos
    concepts = []
    if extract_concepts_to_graph:
        combined = " ".join(s.get("summary", "") for s in sections)
        if len(combined) < 500:
            combined = text[:10000]
        concepts = _extract_concepts(combined, max_concepts=15,
                                     source=str(source))

    # 6. Autotest
    test_result = {"questions": [], "average_score": 0.0, "passed": False}
    if self_evaluate:
        study_text = " ".join(s.get("summary", "") for s in sections)
        if len(study_text) < 500:
            study_text = text[:8000]
        questions = _generate_questions(study_text, n=5, content_type=content_type)
        results = []
        total_score = 0.0
        for q in questions:
            actual = _answer_question(q["question"], study_text)
            score = _score_answer(q["answer"], actual)
            results.append({
                "question": q["question"],
                "expected": q["answer"],
                "actual": actual,
                "score": round(score, 2),
            })
            total_score += score
        avg = total_score / len(results) if results else 0.0
        test_result = {
            "questions": results,
            "average_score": round(avg, 2),
            "passed": avg >= 0.5,
        }

    elapsed = time.time() - t0
    log.info("comprehend: «%s» hecho en %.1fs (%d secciones, %d conceptos, test=%.2f)",
             source, elapsed, len(sections), len(concepts), test_result["average_score"])

    return {
        "ok": True,
        "type": content_type,
        "source": source,
        "total_chars": len(text),
        "sections": sections,
        "total_sections": len(sections),
        "concepts": {
            "items": concepts,
            "total_extracted": len(concepts),
            "total_persisted": sum(1 for c in concepts if c.get("persisted")),
        },
        "test": test_result,
        "elapsed_s": round(elapsed, 1),
    }


def _comprehend_topic(topic: str, extract_to_graph: bool = True,
                      self_evaluate: bool = True) -> Dict:
    """Comprende un TEMA (no archivo) investigándolo a fondo."""
    log.info("_comprehend_topic: «%s» — investigando", topic)
    try:
        from core.eidos_deep_research import investigate_deep
        result = investigate_deep(topic, visible=False)
        summary = result.get("summary", "")
        if summary:
            sections = [{"title": topic, "length": len(summary),
                         "text": summary, "summary": summary}]
            concepts = []
            if extract_to_graph:
                concepts = _extract_concepts(summary, max_concepts=10, source=topic)
            return {
                "ok": True, "type": "topic", "source": topic,
                "total_chars": len(summary),
                "sections": sections, "total_sections": 1,
                "concepts": {
                    "items": concepts,
                    "total_extracted": len(concepts),
                    "total_persisted": sum(1 for c in concepts if c.get("persisted")),
                },
                "test": {"questions": [], "average_score": 0.0, "passed": False},
                "elapsed_s": result.get("elapsed", 0),
            }
        return {"ok": False, "type": "topic", "source": topic,
                "error": "No se encontró información sobre el tema"}
    except Exception as e:
        return {"ok": False, "type": "topic", "source": topic,
                "error": f"Error investigando: {e}"}


# ── 6. Alias backward-compatible ───────────────────────────────────────────

def deep_read(path: str, extract_to_graph: bool = True,
              self_evaluate: bool = True) -> Dict:
    """Alias de comprehend() para compatibilidad con eidos_book_comprehension."""
    return comprehend(path, extract_concepts_to_graph=extract_to_graph,
                      self_evaluate=self_evaluate, content_type="")


# ── CLI ────────────────────────────────────────────────────────────────────
if __name__ == "__main__":
    import sys
    logging.basicConfig(level=logging.INFO)

    if len(sys.argv) < 2:
        print("Uso: python3 eidos_deep_comprehension.py <archivo|tema> [--no-graph] [--no-test] [--type=TYPE]")
        print("Tipos: document, code, text, video, audio, auto (default)")
        sys.exit(1)

    src = sys.argv[1]
    ctype = ""
    for a in sys.argv[2:]:
        if a.startswith("--type="):
            ctype = a.split("=", 1)[1]

    result = comprehend(
        src,
        extract_concepts_to_graph="--no-graph" not in sys.argv,
        self_evaluate="--no-test" not in sys.argv,
        content_type=ctype,
    )
    print(json.dumps(result, indent=2, ensure_ascii=False, default=str))
