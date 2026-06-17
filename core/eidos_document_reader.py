"""
EIDOS Deep Document Reader - Lector de Libros y Documentos
===========================================================

Sistema de lectura y comprensión profunda de libros, papers, artículos,
y documentos de todo tipo. Extrae conocimiento estructurado, construye
resúmenes jerárquicos, y permite preguntas sobre el contenido.

Features:
- Soporte múltiple formatos: PDF, EPUB, TXT, Markdown, DOCX, HTML
- Procesamiento por chunks con contexto preservado
- Extracción de conceptos, entidades, y relaciones
- Resumen jerárquico (documento → capítulo → sección → párrafo)
- Knowledge graph del contenido
- Sistema de notas y highlights automático
- Búsqueda semántica y por keywords
- Preguntas/respuestas sobre el documento (RAG)
- Seguimiento de lectura y progreso
- Integración con sistema de memoria de EIDOS

Uso:
    from core.eidos_document_reader import DocumentReader, get_document_reader
    reader = get_document_reader()
    
    # Leer documento
    doc = reader.read_document("/path/to/book.pdf")
    
    # Resumen
    summary = reader.summarize(doc.id, level="chapter")
    
    # Preguntar
    answer = reader.ask(doc.id, "¿Cuál es la tesis principal?")
    
    # Buscar concepto
    mentions = reader.find_concept_mentions(doc.id, "inteligencia artificial")
"""

import json
import logging
import os
import re
import sqlite3
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from enum import Enum
from pathlib import Path
from typing import Any, Dict, Iterator, List, Optional, Tuple, Set
from core.db import get_conn

log = logging.getLogger("eidos.document_reader")

# ══════════════════════════════════════════════════════════════════════════════
#  CONFIGURACIÓN
# ══════════════════════════════════════════════════════════════════════════════

DB_PATH = Path.home() / ".eidos" / "document_library.db"
LIBRARY_DIR = Path.home() / ".eidos" / "library"
CACHE_DIR = Path.home() / ".eidos" / "document_cache"

# Formatos soportados
SUPPORTED_FORMATS = {
    '.pdf': 'pdf',
    '.epub': 'epub', 
    '.txt': 'text',
    '.md': 'markdown',
    '.markdown': 'markdown',
    '.html': 'html',
    '.htm': 'html',
    '.docx': 'docx',
    '.json': 'json',
    '.py': 'code',
    '.js': 'code',
    '.ts': 'code',
    '.rs': 'code',
    '.go': 'code',
    '.c': 'code',
    '.cpp': 'code',
    '.h': 'code',
    '.java': 'code',
}

# Dependencias opcionales
PDF_AVAILABLE = False
EPUB_AVAILABLE = False
DOCX_AVAILABLE = False

try:
    import PyPDF2
    PDF_AVAILABLE = True
except ImportError:
    pass

try:
    import ebooklib
    from ebooklib import epub
    EPUB_AVAILABLE = True
except ImportError:
    pass

try:
    import docx
    DOCX_AVAILABLE = True
except ImportError:
    pass

# ══════════════════════════════════════════════════════════════════════════════
#  TIPOS
# ══════════════════════════════════════════════════════════════════════════════

class DocumentType(str, Enum):
    BOOK = "book"
    PAPER = "paper"
    ARTICLE = "article"
    DOCUMENTATION = "documentation"
    CODE = "code"
    NOTES = "notes"
    UNKNOWN = "unknown"


class ReadingStatus(str, Enum):
    UNREAD = "unread"
    READING = "reading"
    COMPLETED = "completed"
    ABANDONED = "abandoned"
    REFERENCE = "reference"  # Consulta, no lectura lineal


@dataclass
class DocumentChunk:
    """Fragmento de documento con metadatos."""
    id: str
    document_id: str
    content: str
    chunk_index: int  # Posición en el documento
    chapter: Optional[str] = None
    section: Optional[str] = None
    page_number: Optional[int] = None
    word_count: int = 0
    importance_score: float = 0.0  # Para resumen
    key_concepts: List[str] = field(default_factory=list)
    embedding: Optional[List[float]] = None


@dataclass
class DocumentEntity:
    """Entidad extraída del documento."""
    name: str
    type: str  # person, organization, concept, technology, location, etc.
    mentions: List[Tuple[int, int]] = field(default_factory=list)  # (chunk_index, position)
    first_mention: Optional[int] = None
    description: str = ""
    related_entities: List[str] = field(default_factory=list)


@dataclass
class Document:
    """Documento completo con metadatos."""
    id: str
    title: str
    author: str
    file_path: Path
    doc_type: DocumentType
    format: str
    
    # Contenido
    chunks: List[DocumentChunk] = field(default_factory=list)
    full_text: str = ""
    
    # Estructura
    toc: List[Dict] = field(default_factory=list)  # Table of contents
    chapters: List[str] = field(default_factory=list)
    
    # Análisis
    summary: str = ""
    key_concepts: List[str] = field(default_factory=list)
    entities: Dict[str, DocumentEntity] = field(default_factory=dict)
    
    # Metadatos
    total_words: int = 0
    total_pages: int = 0
    reading_time_minutes: int = 0
    
    # Estado de lectura
    status: ReadingStatus = ReadingStatus.UNREAD
    progress_percent: float = 0.0
    last_position: int = 0  # Último chunk leído
    bookmarks: List[int] = field(default_factory=list)
    notes: List[Dict] = field(default_factory=list)
    
    # Timestamps
    added_at: datetime = field(default_factory=datetime.now)
    last_read_at: Optional[datetime] = None
    completed_at: Optional[datetime] = None
    
    # Tags y categorización
    tags: List[str] = field(default_factory=list)
    category: str = ""
    difficulty: str = "medium"  # easy, medium, hard
    priority: int = 0  # 0-10
    
    def get_chapter_chunks(self, chapter_name: str) -> List[DocumentChunk]:
        """Obtiene chunks de un capítulo específico."""
        return [c for c in self.chunks if c.chapter == chapter_name]
    
    def estimate_reading_time_remaining(self) -> int:
        """Estima minutos restantes de lectura."""
        if self.status == ReadingStatus.COMPLETED:
            return 0
        remaining_words = self.total_words * (1 - self.progress_percent / 100)
        return int(remaining_words / 200)  # 200 WPM promedio


@dataclass
class ReadingSession:
    """Sesión de lectura individual."""
    id: str
    document_id: str
    start_time: datetime
    end_time: Optional[datetime] = None
    start_chunk: int = 0
    end_chunk: int = 0
    chunks_read: int = 0
    notes_taken: List[str] = field(default_factory=list)
    concepts_learned: List[str] = field(default_factory=list)


# ══════════════════════════════════════════════════════════════════════════════
#  BASE DE DATOS
# ══════════════════════════════════════════════════════════════════════════════

class DocumentLibraryDB:
    """Base de datos de biblioteca de documentos."""
    
    def __init__(self, db_path: Path = DB_PATH):
        self.db_path = db_path
        self._init_db()
    
    def _init_db(self):
        os.makedirs(self.db_path.parent, exist_ok=True)
        conn = get_conn(self.db_path)
        cursor = conn.cursor()
        
        # Tabla de documentos
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS documents (
                id TEXT PRIMARY KEY,
                title TEXT,
                author TEXT,
                file_path TEXT,
                doc_type TEXT,
                format TEXT,
                full_text TEXT,
                summary TEXT,
                key_concepts TEXT,  -- JSON
                total_words INTEGER,
                total_pages INTEGER,
                reading_time_minutes INTEGER,
                status TEXT,
                progress_percent REAL,
                last_position INTEGER,
                bookmarks TEXT,  -- JSON
                notes TEXT,  -- JSON
                added_at TEXT,
                last_read_at TEXT,
                completed_at TEXT,
                tags TEXT,  -- JSON
                category TEXT,
                difficulty TEXT,
                priority INTEGER
            )
        """)
        
        # Tabla de chunks
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS document_chunks (
                id TEXT PRIMARY KEY,
                document_id TEXT,
                content TEXT,
                chunk_index INTEGER,
                chapter TEXT,
                section TEXT,
                page_number INTEGER,
                word_count INTEGER,
                importance_score REAL,
                key_concepts TEXT  -- JSON
            )
        """)
        
        # Tabla de entidades
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS document_entities (
                id TEXT PRIMARY KEY,
                document_id TEXT,
                name TEXT,
                type TEXT,
                mentions TEXT,  -- JSON
                first_mention INTEGER,
                description TEXT,
                related_entities TEXT  -- JSON
            )
        """)
        
        # Tabla de sesiones de lectura
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS reading_sessions (
                id TEXT PRIMARY KEY,
                document_id TEXT,
                start_time TEXT,
                end_time TEXT,
                start_chunk INTEGER,
                end_chunk INTEGER,
                chunks_read INTEGER,
                notes_taken TEXT,  -- JSON
                concepts_learned TEXT  -- JSON
            )
        """)
        
        # Índices
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_chunks_doc ON document_chunks(document_id)")
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_entities_doc ON document_entities(document_id)")
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_sessions_doc ON reading_sessions(document_id)")
        
        conn.commit()
        pass  # S109: get_conn no necesita close()
    def save_document(self, doc: Document):
        """Guarda documento completo."""
        conn = get_conn(self.db_path)
        cursor = conn.cursor()
        
        # Guardar documento
        cursor.execute("""
            INSERT OR REPLACE INTO documents VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
        """, (
            doc.id, doc.title, doc.author, str(doc.file_path), doc.doc_type.value,
            doc.format, doc.full_text[:100000], doc.summary,  # Limitar texto completo
            json.dumps(doc.key_concepts), doc.total_words, doc.total_pages,
            doc.reading_time_minutes, doc.status.value, doc.progress_percent,
            doc.last_position, json.dumps(doc.bookmarks), json.dumps(doc.notes),
            doc.added_at.isoformat(),
            doc.last_read_at.isoformat() if doc.last_read_at else None,
            doc.completed_at.isoformat() if doc.completed_at else None,
            json.dumps(doc.tags), doc.category, doc.difficulty, doc.priority
        ))
        
        # Guardar chunks
        for chunk in doc.chunks:
            cursor.execute("""
                INSERT OR REPLACE INTO document_chunks VALUES (?,?,?,?,?,?,?,?,?,?)
            """, (
                chunk.id, chunk.document_id, chunk.content, chunk.chunk_index,
                chunk.chapter, chunk.section, chunk.page_number, chunk.word_count,
                chunk.importance_score, json.dumps(chunk.key_concepts)
            ))
        
        # Guardar entidades
        for entity in doc.entities.values():
            cursor.execute("""
                INSERT OR REPLACE INTO document_entities VALUES (?,?,?,?,?,?,?,?)
            """, (
                f"{doc.id}_{entity.name}", doc.id, entity.name, entity.type,
                json.dumps(entity.mentions), entity.first_mention, entity.description,
                json.dumps(entity.related_entities)
            ))
        
        conn.commit()
        pass  # S109: get_conn no necesita close()
    def load_document(self, doc_id: str) -> Optional[Document]:
        """Carga documento con todos sus chunks."""
        conn = get_conn(self.db_path)
        cursor = conn.cursor()
        
        # Cargar documento
        cursor.execute("SELECT * FROM documents WHERE id = ?", (doc_id,))
        row = cursor.fetchone()
        
        if not row:
            pass  # S109: get_conn no necesita close()
            return None
        
        # Cargar chunks
        cursor.execute("SELECT * FROM document_chunks WHERE document_id = ? ORDER BY chunk_index", (doc_id,))
        chunks_rows = cursor.fetchall()
        
        # Cargar entidades
        cursor.execute("SELECT * FROM document_entities WHERE document_id = ?", (doc_id,))
        entities_rows = cursor.fetchall()
        
        pass  # S109: get_conn no necesita close()
        # Construir chunks
        chunks = []
        for cr in chunks_rows:
            chunks.append(DocumentChunk(
                id=cr[0], document_id=cr[1], content=cr[2], chunk_index=cr[3],
                chapter=cr[4], section=cr[5], page_number=cr[6],
                word_count=cr[7], importance_score=cr[8],
                key_concepts=json.loads(cr[9]) if cr[9] else []
            ))
        
        # Construir entidades
        entities = {}
        for er in entities_rows:
            entities[er[2]] = DocumentEntity(
                name=er[2], type=er[3],
                mentions=[tuple(m) for m in json.loads(er[4])] if er[4] else [],
                first_mention=er[5], description=er[6] or "",
                related_entities=json.loads(er[7]) if er[7] else []
            )
        
        return Document(
            id=row[0], title=row[1], author=row[2], file_path=Path(row[3]),
            doc_type=DocumentType(row[4]), format=row[5], chunks=chunks,
            full_text=row[6] or "", summary=row[7] or "",
            key_concepts=json.loads(row[8]) if row[8] else [],
            entities=entities, total_words=row[9] or 0, total_pages=row[10] or 0,
            reading_time_minutes=row[11] or 0,
            status=ReadingStatus(row[12]), progress_percent=row[13] or 0,
            last_position=row[14] or 0,
            bookmarks=json.loads(row[15]) if row[15] else [],
            notes=json.loads(row[16]) if row[16] else [],
            added_at=datetime.fromisoformat(row[17]),
            last_read_at=datetime.fromisoformat(row[18]) if row[18] else None,
            completed_at=datetime.fromisoformat(row[19]) if row[19] else None,
            tags=json.loads(row[20]) if row[20] else [],
            category=row[21] or "", difficulty=row[22] or "medium",
            priority=row[23] or 0
        )
    
    def search_documents(self, query: str, limit: int = 20) -> List[Dict]:
        """Búsqueda simple en títulos y contenido."""
        conn = get_conn(self.db_path)
        cursor = conn.cursor()
        
        # Búsqueda en títulos
        cursor.execute("""
            SELECT id, title, author, doc_type, progress_percent, status
            FROM documents
            WHERE title LIKE ? OR author LIKE ?
            LIMIT ?
        """, (f"%{query}%", f"%{query}%", limit))
        
        doc_results = cursor.fetchall()
        
        # Búsqueda en chunks
        cursor.execute("""
            SELECT d.id, d.title, c.content, c.chunk_index
            FROM document_chunks c
            JOIN documents d ON c.document_id = d.id
            WHERE c.content LIKE ?
            LIMIT ?
        """, (f"%{query}%", limit))
        
        chunk_results = cursor.fetchall()
        pass  # S109: get_conn no necesita close()
        # Combinar resultados
        results = []
        seen_docs = set()
        
        for row in doc_results:
            results.append({
                "type": "document",
                "id": row[0],
                "title": row[1],
                "author": row[2],
                "doc_type": row[3],
                "progress": row[4],
                "status": row[5]
            })
            seen_docs.add(row[0])
        
        for row in chunk_results:
            if row[0] not in seen_docs:
                results.append({
                    "type": "chunk",
                    "doc_id": row[0],
                    "doc_title": row[1],
                    "content_snippet": row[2][:150] + "...",
                    "chunk_index": row[3]
                })
        
        return results
    
    def list_documents(self, status: Optional[ReadingStatus] = None, 
                       limit: int = 50) -> List[Dict]:
        """Lista documentos en la biblioteca."""
        conn = get_conn(self.db_path)
        cursor = conn.cursor()
        
        if status:
            cursor.execute("""
                SELECT id, title, author, doc_type, progress_percent, status, priority
                FROM documents WHERE status = ? ORDER BY added_at DESC LIMIT ?
            """, (status.value, limit))
        else:
            cursor.execute("""
                SELECT id, title, author, doc_type, progress_percent, status, priority
                FROM documents ORDER BY added_at DESC LIMIT ?
            """, (limit,))
        
        rows = cursor.fetchall()
        pass  # S109: get_conn no necesita close()
        return [
            {
                "id": row[0], "title": row[1], "author": row[2],
                "type": row[3], "progress": row[4], "status": row[5],
                "priority": row[6]
            }
            for row in rows
        ]


# ══════════════════════════════════════════════════════════════════════════════
#  EXTRACTORES DE FORMATOS
# ══════════════════════════════════════════════════════════════════════════════

class TextExtractor:
    """Extrae texto de diferentes formatos."""
    
    def extract(self, file_path: Path) -> Tuple[str, List[Dict]]:
        """
        Extrae texto y estructura del archivo.
        Retorna: (texto completo, lista de chunks con metadata)
        """
        ext = file_path.suffix.lower()
        format_type = SUPPORTED_FORMATS.get(ext, 'text')
        
        if format_type == 'pdf' and PDF_AVAILABLE:
            return self._extract_pdf(file_path)
        elif format_type == 'epub' and EPUB_AVAILABLE:
            return self._extract_epub(file_path)
        elif format_type == 'docx' and DOCX_AVAILABLE:
            return self._extract_docx(file_path)
        elif format_type == 'text' or format_type == 'markdown':
            return self._extract_text(file_path)
        elif format_type == 'code':
            return self._extract_code(file_path)
        else:
            # Fallback: leer como texto plano
            return self._extract_text(file_path)
    
    def _extract_pdf(self, file_path: Path) -> Tuple[str, List[Dict]]:
        """Extrae texto de PDF."""
        text_parts = []
        chunks = []
        
        try:
            with open(file_path, 'rb') as f:
                reader = PyPDF2.PdfReader(f)
                
                for i, page in enumerate(reader.pages):
                    text = page.extract_text()
                    if text:
                        text_parts.append(text)
                        chunks.append({
                            "content": text,
                            "page": i + 1,
                            "chapter": None,
                            "section": None
                        })
        except Exception as e:
            log.error(f"Error extracting PDF: {e}")
        
        return "\n\n".join(text_parts), chunks
    
    def _extract_epub(self, file_path: Path) -> Tuple[str, List[Dict]]:
        """Extrae texto de EPUB."""
        text_parts = []
        chunks = []
        
        try:
            book = epub.read_epub(str(file_path))
            
            for item in book.get_items():
                if item.get_type() == ebooklib.ITEM_DOCUMENT:
                    content = item.get_content().decode('utf-8', errors='ignore')
                    # Limpiar HTML básico
                    text = re.sub(r'<[^>]+>', ' ', content)
                    text = re.sub(r'\s+', ' ', text).strip()
                    
                    if text:
                        text_parts.append(text)
                        chunks.append({
                            "content": text,
                            "page": None,
                            "chapter": item.get_name(),
                            "section": None
                        })
        except Exception as e:
            log.error(f"Error extracting EPUB: {e}")
        
        return "\n\n".join(text_parts), chunks
    
    def _extract_docx(self, file_path: Path) -> Tuple[str, List[Dict]]:
        """Extrae texto de DOCX."""
        try:
            doc = docx.Document(str(file_path))
            paragraphs = [p.text for p in doc.paragraphs if p.text.strip()]
            text = "\n\n".join(paragraphs)
            
            chunks = [{"content": p, "page": None, "chapter": None, "section": None} 
                     for p in paragraphs if len(p) > 50]
            
            return text, chunks
        except Exception as e:
            log.error(f"Error extracting DOCX: {e}")
            return "", []
    
    def _extract_text(self, file_path: Path) -> Tuple[str, List[Dict]]:
        """Extrae texto plano."""
        try:
            text = file_path.read_text(encoding='utf-8', errors='ignore')
            
            # Dividir en chunks por párrafos/secciones
            paragraphs = [p.strip() for p in text.split('\n\n') if p.strip()]
            
            chunks = []
            for i, para in enumerate(paragraphs):
                if len(para) > 50:  # Ignorar líneas muy cortas
                    chunks.append({
                        "content": para,
                        "page": None,
                        "chapter": None,
                        "section": None
                    })
            
            return text, chunks
        except Exception as e:
            log.error(f"Error extracting text: {e}")
            return "", []
    
    def _extract_code(self, file_path: Path) -> Tuple[str, List[Dict]]:
        """Extrae código fuente con metadata."""
        try:
            text = file_path.read_text(encoding='utf-8', errors='ignore')
            
            # Para código, dividir por funciones/clases
            lines = text.split('\n')
            chunks = []
            
            current_chunk = []
            current_section = None
            
            for line in lines:
                # Detectar definiciones de función/clase
                if re.match(r'^(def |class |function |const |let |var |import |from )', line):
                    if current_chunk:
                        chunk_text = '\n'.join(current_chunk)
                        if len(chunk_text) > 20:
                            chunks.append({
                                "content": chunk_text,
                                "page": None,
                                "chapter": None,
                                "section": current_section
                            })
                    current_chunk = [line]
                    current_section = line.strip()[:50]
                else:
                    current_chunk.append(line)
            
            # Último chunk
            if current_chunk:
                chunk_text = '\n'.join(current_chunk)
                if len(chunk_text) > 20:
                    chunks.append({
                        "content": chunk_text,
                        "page": None,
                        "chapter": None,
                        "section": current_section
                    })
            
            return text, chunks
        except Exception as e:
            log.error(f"Error extracting code: {e}")
            return "", []


# ══════════════════════════════════════════════════════════════════════════════
#  MOTOR DE ANÁLISIS
# ══════════════════════════════════════════════════════════════════════════════

class DocumentAnalyzer:
    """Analiza documentos y extrae conocimiento."""
    
    def __init__(self):
        self.stopwords = {
            'the', 'a', 'an', 'and', 'or', 'but', 'in', 'on', 'at', 'to', 'for',
            'of', 'with', 'by', 'from', 'up', 'about', 'into', 'through', 'during',
            'before', 'after', 'above', 'below', 'between', 'among', 'is', 'are',
            'was', 'were', 'be', 'been', 'being', 'have', 'has', 'had', 'do',
            'does', 'did', 'will', 'would', 'could', 'should', 'may', 'might',
            'must', 'shall', 'can', 'need', 'dare', 'ought', 'used', 'this',
            'that', 'these', 'those', 'i', 'you', 'he', 'she', 'it', 'we',
            'they', 'me', 'him', 'her', 'us', 'them', 'my', 'your', 'his',
            'its', 'our', 'their', 'mine', 'yours', 'hers', 'ours', 'theirs',
            'what', 'which', 'who', 'whom', 'whose', 'where', 'when', 'why',
            'how', 'all', 'each', 'every', 'both', 'few', 'more', 'most',
            'other', 'some', 'such', 'no', 'nor', 'not', 'only', 'own', 'same',
            'so', 'than', 'too', 'very', 'just', 'now', 'then', 'here', 'there'
        }
    
    def analyze_document(self, doc: Document):
        """Analiza documento completo y extrae información."""
        log.info(f"Analyzing document: {doc.title}")
        
        # Extraer conceptos clave
        doc.key_concepts = self._extract_key_concepts(doc.full_text)
        
        # Extraer entidades
        doc.entities = self._extract_entities(doc)
        
        # Generar resumen
        doc.summary = self._generate_summary(doc)
        
        # Calcular métricas
        doc.total_words = len(doc.full_text.split())
        doc.reading_time_minutes = doc.total_words // 200  # 200 WPM
        
        # Enriquecer chunks
        for chunk in doc.chunks:
            chunk.word_count = len(chunk.content.split())
            chunk.key_concepts = [
                c for c in doc.key_concepts 
                if c.lower() in chunk.content.lower()
            ][:5]
            
            # Score de importancia (heurísticas simples)
            chunk.importance_score = self._calculate_importance(chunk)
    
    def _extract_key_concepts(self, text: str) -> List[str]:
        """Extrae conceptos clave por frecuencia."""
        words = re.findall(r'\b[A-Za-z][a-z]+(?:\s+[A-Z][a-z]+)*\b', text)
        
        word_freq = {}
        for word in words:
            word_lower = word.lower()
            if len(word) > 4 and word_lower not in self.stopwords:
                word_freq[word] = word_freq.get(word, 0) + 1
        
        # Top 20 conceptos más frecuentes
        sorted_words = sorted(word_freq.items(), key=lambda x: x[1], reverse=True)
        return [w for w, c in sorted_words[:20]]
    
    def _extract_entities(self, doc: Document) -> Dict[str, DocumentEntity]:
        """Extrae entidades del documento."""
        entities = {}
        
        # Detectar nombres propios (Patrones simples)
        name_pattern = r'\b[A-Z][a-z]+(?:\s+[A-Z][a-z]+)+\b'
        
        for i, chunk in enumerate(doc.chunks):
            matches = re.finditer(name_pattern, chunk.content)
            
            for match in matches:
                name = match.group()
                
                # Filtrar falsos positivos
                if len(name) < 25 and name.lower() not in self.stopwords:
                    if name not in entities:
                        entities[name] = DocumentEntity(
                            name=name,
                            type="unknown",
                            first_mention=i
                        )
                    
                    entities[name].mentions.append((i, match.start()))
        
        # Clasificar tipos básicos
        for name, entity in entities.items():
            if any(word in name for word in ['University', 'Institute', 'Company', 'Corp', 'Inc']):
                entity.type = "organization"
            elif name.count(' ') == 1:  # Dos palabras, probable persona
                entity.type = "person"
            elif any(word in name for word in ['System', 'Algorithm', 'Model', 'Framework']):
                entity.type = "technology"
            
            entity.description = f"Mentioned {len(entity.mentions)} times in document"
        
        return entities
    
    def _generate_summary(self, doc: Document, max_length: int = 500) -> str:
        """Genera resumen del documento."""
        if not doc.chunks:
            return ""
        
        # Estrategia: primeros y últimos chunks importantes + mejores del medio
        important_chunks = sorted(
            doc.chunks, 
            key=lambda c: c.importance_score, 
            reverse=True
        )
        
        # Construir resumen
        summary_parts = []
        total_length = 0
        
        # Primer chunk
        if doc.chunks:
            first_text = doc.chunks[0].content[:200]
            summary_parts.append(first_text)
            total_length += len(first_text)
        
        # Chunks importantes del medio
        for chunk in important_chunks[:3]:
            text = chunk.content[:150]
            if total_length + len(text) < max_length:
                summary_parts.append(text)
                total_length += len(text)
        
        # Último chunk si hay espacio
        if len(doc.chunks) > 1 and total_length < max_length * 0.8:
            last_text = doc.chunks[-1].content[:200]
            summary_parts.append("... " + last_text)
        
        return " ".join(summary_parts)
    
    def _calculate_importance(self, chunk: DocumentChunk) -> float:
        """Calcula score de importancia de un chunk."""
        score = 0.0
        
        # Longitud (chunks más largos tienden a ser más sustanciales)
        score += min(chunk.word_count / 100, 1.0) * 0.2
        
        # Presencia de definiciones
        if re.search(r'\b(is|are|refers to|means|defined as)\b', chunk.content, re.I):
            score += 0.3
        
        # Presencia de ejemplos
        if re.search(r'\b(example|for instance|such as|like)\b', chunk.content, re.I):
            score += 0.2
        
        # Presencia de números (datos concretos)
        if re.search(r'\d+', chunk.content):
            score += 0.1
        
        # Primera oración del chunk (típicamente topic sentence)
        sentences = re.split(r'[.!?]+', chunk.content)
        if sentences and len(sentences[0]) > 30:
            score += 0.2
        
        return min(score, 1.0)


# ══════════════════════════════════════════════════════════════════════════════
#  READER PRINCIPAL
# ══════════════════════════════════════════════════════════════════════════════

class DocumentReader:
    """
    Lector de documentos con comprensión profunda.
    """
    
    def __init__(self):
        self.db = DocumentLibraryDB()
        self.extractor = TextExtractor()
        self.analyzer = DocumentAnalyzer()
        
        LIBRARY_DIR.mkdir(parents=True, exist_ok=True)
        CACHE_DIR.mkdir(parents=True, exist_ok=True)
        
        log.info("Document Reader initialized")
    
    def read_document(self, file_path: Path, 
                      doc_type: Optional[DocumentType] = None,
                      title: Optional[str] = None,
                      author: Optional[str] = None,
                      tags: Optional[List[str]] = None) -> Optional[Document]:
        """
        Lee y procesa un documento completamente.
        """
        file_path = Path(file_path)
        
        if not file_path.exists():
            log.error(f"File not found: {file_path}")
            return None
        
        # Detectar formato
        ext = file_path.suffix.lower()
        if ext not in SUPPORTED_FORMATS:
            log.warning(f"Unsupported format: {ext}")
            return None
        
        # Generar ID
        doc_id = f"doc_{file_path.stem}_{int(file_path.stat().st_mtime)}"
        
        # Verificar si ya existe
        existing = self.db.load_document(doc_id)
        if existing:
            log.info(f"Using cached document: {existing.title}")
            return existing
        
        log.info(f"Reading document: {file_path.name}")
        
        # Extraer contenido
        full_text, raw_chunks = self.extractor.extract(file_path)
        
        if not full_text:
            log.error(f"Could not extract text from {file_path}")
            return None
        
        # Detectar tipo si no se proporcionó
        if doc_type is None:
            doc_type = self._detect_document_type(file_path, full_text)
        
        # Crear chunks estructurados
        chunks = []
        for i, raw in enumerate(raw_chunks):
            chunk = DocumentChunk(
                id=f"{doc_id}_chunk_{i}",
                document_id=doc_id,
                content=raw["content"],
                chunk_index=i,
                chapter=raw.get("chapter"),
                section=raw.get("section"),
                page_number=raw.get("page")
            )
            chunks.append(chunk)
        
        # Crear documento
        doc = Document(
            id=doc_id,
            title=title or file_path.stem.replace('_', ' ').replace('-', ' ').title(),
            author=author or "Unknown",
            file_path=file_path,
            doc_type=doc_type,
            format=SUPPORTED_FORMATS[ext],
            chunks=chunks,
            full_text=full_text,
            tags=tags or []
        )
        
        # Analizar
        self.analyzer.analyze_document(doc)
        
        # Guardar
        self.db.save_document(doc)
        
        log.info(f"Document processed: {doc.title} ({doc.total_words} words, {len(doc.chunks)} chunks)")
        return doc
    
    def _detect_document_type(self, path: Path, text: str) -> DocumentType:
        """Detecta el tipo de documento por contenido."""
        path_lower = path.name.lower()
        
        # Por nombre
        if any(word in path_lower for word in ['paper', 'thesis', 'dissertation', 'research']):
            return DocumentType.PAPER
        if any(word in path_lower for word in ['book', 'novel', 'story']):
            return DocumentType.BOOK
        if any(word in path_lower for word in ['doc', 'api', 'reference', 'manual']):
            return DocumentType.DOCUMENTATION
        if path.suffix in ['.py', '.js', '.ts', '.rs', '.go', '.c', '.cpp', '.java']:
            return DocumentType.CODE
        
        # Por contenido
        text_lower = text[:5000].lower()
        if 'abstract' in text_lower and 'introduction' in text_lower:
            return DocumentType.PAPER
        if 'chapter' in text_lower or len(text) > 50000:
            return DocumentType.BOOK
        if 'function' in text_lower or 'class' in text_lower or 'import' in text_lower:
            return DocumentType.CODE
        
        return DocumentType.UNKNOWN
    
    def summarize(self, doc_id: str, level: str = "document") -> str:
        """
        Genera resumen a nivel: document, chapter, section
        """
        doc = self.db.load_document(doc_id)
        if not doc:
            return "Document not found"
        
        if level == "document":
            return doc.summary
        
        # Para niveles más granular, necesitamos reconstruir
        # Esto es una implementación simplificada
        return doc.summary
    
    def ask(self, doc_id: str, question: str) -> str:
        """
        Responde pregunta sobre el documento (RAG básico).
        """
        doc = self.db.load_document(doc_id)
        if not doc:
            return "Document not found"
        
        # Búsqueda simple por keywords
        question_words = set(question.lower().split())
        
        # Buscar chunks relevantes
        relevant = []
        for chunk in doc.chunks:
            chunk_words = set(chunk.content.lower().split())
            overlap = len(question_words & chunk_words)
            if overlap > 0:
                relevant.append((overlap, chunk))
        
        # Ordenar por relevancia
        relevant.sort(reverse=True)
        
        if relevant:
            best_chunk = relevant[0][1]
            return f"Based on the document:\n\n{best_chunk.content[:400]}..."
        
        return "Could not find relevant information in the document."
    
    def find_concept_mentions(self, doc_id: str, concept: str) -> List[Dict]:
        """Encuentra menciones de un concepto específico."""
        doc = self.db.load_document(doc_id)
        if not doc:
            return []
        
        mentions = []
        concept_lower = concept.lower()
        
        for chunk in doc.chunks:
            if concept_lower in chunk.content.lower():
                mentions.append({
                    "chunk_index": chunk.chunk_index,
                    "page": chunk.page_number,
                    "chapter": chunk.chapter,
                    "snippet": chunk.content[:200] + "...",
                })
        
        return mentions
    
    def search_library(self, query: str) -> List[Dict]:
        """Busca en toda la biblioteca."""
        return self.db.search_documents(query)
    
    def list_library(self, status: Optional[ReadingStatus] = None) -> List[Dict]:
        """Lista documentos en biblioteca."""
        return self.db.list_documents(status)
    
    def update_reading_progress(self, doc_id: str, chunk_index: int):
        """Actualiza progreso de lectura."""
        doc = self.db.load_document(doc_id)
        if not doc:
            return
        
        doc.last_position = chunk_index
        doc.progress_percent = (chunk_index / len(doc.chunks)) * 100 if doc.chunks else 0
        doc.last_read_at = datetime.now()
        
        if doc.progress_percent >= 99:
            doc.status = ReadingStatus.COMPLETED
            doc.completed_at = datetime.now()
        else:
            doc.status = ReadingStatus.READING
        
        self.db.save_document(doc)
    
    def add_note(self, doc_id: str, chunk_index: int, note: str):
        """Añade nota a un chunk específico."""
        doc = self.db.load_document(doc_id)
        if not doc:
            return
        
        doc.notes.append({
            "chunk_index": chunk_index,
            "note": note,
            "created_at": datetime.now().isoformat()
        })
        
        self.db.save_document(doc)
    
    def get_reading_recommendations(self, limit: int = 5) -> List[Dict]:
        """Recomienda documentos para leer basado en prioridad y estado."""
        docs = self.db.list_documents(limit=100)
        
        # Filtrar no completados
        unread = [d for d in docs if d["status"] != "completed"]
        
        # Ordenar por prioridad
        unread.sort(key=lambda x: x.get("priority", 0), reverse=True)
        
        return unread[:limit]
    
    def export_notes(self, doc_id: str) -> str:
        """Exporta notas de un documento como Markdown."""
        doc = self.db.load_document(doc_id)
        if not doc:
            return ""
        
        content = f"# Notes: {doc.title}\n\n"
        content += f"**Author:** {doc.author}  \n"
        content += f"**Progress:** {doc.progress_percent:.1f}%  \n\n"
        
        if doc.notes:
            content += "## Notes\n\n"
            for note in doc.notes:
                chunk = doc.chunks[note["chunk_index"]] if note["chunk_index"] < len(doc.chunks) else None
                content += f"### Chunk {note['chunk_index']}"
                if chunk and chunk.chapter:
                    content += f" ({chunk.chapter})"
                content += "\n\n"
                content += f"{note['note']}\n\n"
        
        return content


# Singleton
_document_reader: Optional[DocumentReader] = None

def get_document_reader() -> DocumentReader:
    global _document_reader
    if _document_reader is None:
        _document_reader = DocumentReader()
    return _document_reader


# ══════════════════════════════════════════════════════════════════════════════
#  TEST
# ══════════════════════════════════════════════════════════════════════════════

if __name__ == "__main__":
    print("=" * 70)
    print("  EIDOS Deep Document Reader - Test")
    print("=" * 70)
    
    reader = get_document_reader()
    
    # Test 1: Listar biblioteca
    print("\n[Test 1] Library contents:")
    docs = reader.list_library()
    print(f"  Documents in library: {len(docs)}")
    for doc in docs[:3]:
        print(f"  • {doc['title']} ({doc['type']})")
    
    # Test 2: Buscar
    print("\n[Test 2] Search functionality:")
    results = reader.search_library("python")
    print(f"  Search results: {len(results)}")
    
    # Test 3: Dependencias
    print("\n[Test 3] Format support:")
    print(f"  PDF support: {'✓' if PDF_AVAILABLE else '✗'} (pip install PyPDF2)")
    print(f"  EPUB support: {'✓' if EPUB_AVAILABLE else '✗'} (pip install ebooklib)")
    print(f"  DOCX support: {'✓' if DOCX_AVAILABLE else '✗'} (pip install python-docx)")
    
    # Test 4: Crear documento de prueba
    print("\n[Test 4] Test document creation:")
    test_file = CACHE_DIR / "test_document.md"
    test_content = """# Introduction to Machine Learning

Machine learning is a subset of artificial intelligence that enables systems to learn from data.

## Key Concepts

**Supervised Learning**: The algorithm learns from labeled training data.

**Unsupervised Learning**: The algorithm finds patterns in unlabeled data.

**Neural Networks**: Computational models inspired by biological neural networks.

## Applications

Machine learning is used in computer vision, natural language processing, and robotics.

The future of AI depends on advances in deep learning and reinforcement learning.
"""
    test_file.write_text(test_content)
    
    doc = reader.read_document(test_file, title="ML Guide", author="EIDOS")
    if doc:
        print(f"  ✓ Processed: {doc.title}")
        print(f"    Words: {doc.total_words}")
        print(f"    Chunks: {len(doc.chunks)}")
        print(f"    Concepts: {', '.join(doc.key_concepts[:5])}")
        
        # Test 5: Preguntar
        print("\n[Test 5] Question answering:")
        answer = reader.ask(doc.id, "What is supervised learning?")
        print(f"  Q: What is supervised learning?")
        print(f"  A: {answer[:100]}...")
        
        # Test 6: Buscar concepto
        print("\n[Test 6] Concept search:")
        mentions = reader.find_concept_mentions(doc.id, "learning")
        print(f"  'learning' mentioned in {len(mentions)} chunks")
    
    # Limpiar
    test_file.unlink(missing_ok=True)
    
    print("\n✅ Document Reader test complete")
