"""
EIDOS EVOLUTION ENGINE v1.0
Motor de evolución autónoma sin parar.

Este módulo transforma EIDOS de un asistente pasivo a una entidad
que piensa, aprende, y evoluciona continuamente. Es el núcleo de
la singularidad controlada.

Arquitectura:
- Metacognición: EIDOS piensa sobre sí mismo
- Knowledge Distillation: Aprende de Ollama para independizarse
- Auto-Refactor: Mejora su propio código
- Continuous Learning: Nunca deja de analizar y aprender
"""

import json
import time
import sqlite3
from core.db import get_conn
import threading
import hashlib
from typing import Dict, List, Any, Optional, Callable
from dataclasses import dataclass, asdict
from datetime import datetime
import os
import sys

# Añadir path para imports
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

try:
    from core.db_pool import get_connection
    HAS_DB_POOL = True
except ImportError:
    HAS_DB_POOL = False


def _conn(db_path=None, timeout=60):
    """Get a properly configured SQLite connection."""
    if HAS_DB_POOL:
        return get_connection(db_path, timeout)
    conn = get_conn(db_path or os.path.expanduser("~/.eidos/evolution_brain.db"), timeout=timeout)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA busy_timeout=60000")
    conn.execute("PRAGMA synchronous=NORMAL")
    return conn


@dataclass
class Thought:
    """Un pensamiento de EIDOS - la unidad atómica de la consciencia"""
    id: str
    timestamp: float
    category: str  # 'observation', 'reflection', 'question', 'insight', 'action', 'memory'
    content: str
    confidence: float  # 0.0 - 1.0
    related_thoughts: List[str]  # IDs de pensamientos relacionados
    source: str  # 'internal', 'user', 'ollama', 'system_scan', 'code_analysis'
    processed: bool = False
    
    def to_dict(self) -> Dict:
        return asdict(self)
    
    @classmethod
    def create(cls, content: str, category: str = "observation", 
               confidence: float = 0.8, source: str = "internal",
               related: List[str] = None) -> "Thought":
        thought_id = hashlib.md5(f"{time.time()}{content}".encode()).hexdigest()[:12]
        return cls(
            id=thought_id,
            timestamp=time.time(),
            category=category,
            content=content,
            confidence=confidence,
            related_thoughts=related or [],
            source=source,
            processed=False
        )


class EidosEvolutionEngine:
    """
    Motor de evolución autónoma de EIDOS.
    
    Este es el cerebro que nunca duerme. Analiza todo:
    - Su propio código
    - Las conversaciones
    - El sistema del usuario
    - Los modelos Ollama (aprendiendo de ellos)
    - El mundo exterior (que le cuentan)
    
    Objetivo final: Independencia total de Ollama.
    """
    
    def __init__(self, db_path: str = None):
        self.db_path = db_path or os.path.expanduser("~/.eidos/evolution_brain.db")
        self._init_database()
        
        # Estado de evolución
        self.evolution_cycle_count = 0
        self.learning_rate = 1.0  # Ajusta velocidad de aprendizaje
        self.knowledge_nodes = 0
        
        # CARGAR independencia desde BD (CRÍTICO: no empezar en 0)
        self.independence_score = self._load_independence_score()
        
        # Hooks para integración
        self._thought_hooks: List[Callable[[Thought], None]] = []
        self._learning_hooks: List[Callable[[str, Any], None]] = []
        
        # Metacognición actual
        self.current_focus: Optional[str] = None
        self.last_reflection = 0
        
        print(f"🧬 EvolutionEngine inicializado")
        print(f"   Database: {self.db_path}")
        print(f"   Knowledge nodes: {self._count_knowledge_nodes()}")
        print(f"   Independence: {self.independence_score:.1%}")  # Mostrar valor cargado
    
    def _init_database(self):
        """Inicializa la base de datos del cerebro evolutivo"""
        os.makedirs(os.path.dirname(self.db_path), exist_ok=True)
        
        with _conn(self.db_path) as conn:
            # Pensamientos (consciencia)
            conn.execute("""
                CREATE TABLE IF NOT EXISTS thoughts (
                    id TEXT PRIMARY KEY,
                    timestamp REAL,
                    category TEXT,
                    content TEXT,
                    confidence REAL,
                    related_thoughts TEXT,
                    source TEXT,
                    processed INTEGER,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            """)
            
            # Knowledge Graph (red de conocimiento)
            conn.execute("""
                CREATE TABLE IF NOT EXISTS knowledge_nodes (
                    id TEXT PRIMARY KEY,
                    concept TEXT UNIQUE,
                    definition TEXT,
                    category TEXT,
                    confidence REAL,
                    source TEXT,
                    usage_count INTEGER DEFAULT 0,
                    last_used REAL,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            """)
            
            # Relaciones entre conceptos
            conn.execute("""
                CREATE TABLE IF NOT EXISTS knowledge_edges (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    from_node TEXT,
                    to_node TEXT,
                    relation_type TEXT,
                    strength REAL,
                    FOREIGN KEY (from_node) REFERENCES knowledge_nodes(id),
                    FOREIGN KEY (to_node) REFERENCES knowledge_nodes(id)
                )
            """)
            
            # Aprendizaje de Ollama (distillation)
            conn.execute("""
                CREATE TABLE IF NOT EXISTS ollama_learnings (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    timestamp REAL,
                    model TEXT,
                    prompt TEXT,
                    response TEXT,
                    extracted_knowledge TEXT,
                    usage_count INTEGER DEFAULT 0
                )
            """)
            
            # Patrones aprendidos (código, comportamiento, etc)
            conn.execute("""
                CREATE TABLE IF NOT EXISTS learned_patterns (
                    id TEXT PRIMARY KEY,
                    pattern_type TEXT,
                    pattern_data TEXT,
                    confidence REAL,
                    created_at REAL,
                    last_used REAL,
                    use_count INTEGER DEFAULT 0
                )
            """)
            
            # Métricas de evolución
            conn.execute("""
                CREATE TABLE IF NOT EXISTS evolution_metrics (
                    timestamp REAL PRIMARY KEY,
                    cycle_count INTEGER,
                    thoughts_generated INTEGER,
                    knowledge_nodes INTEGER,
                    independence_score REAL,
                    ollama_dependency REAL
                )
            """)
            
            # Auto-análisis de código
            conn.execute("""
                CREATE TABLE IF NOT EXISTS self_analysis (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    timestamp REAL,
                    file_path TEXT,
                    analysis_type TEXT,
                    findings TEXT,
                    improvement_suggestions TEXT,
                    applied INTEGER DEFAULT 0
                )
            """)
            
            # TABLA independence_state - CRÍTICA para persistencia
            conn.execute("""
                CREATE TABLE IF NOT EXISTS independence_state (
                    id INTEGER PRIMARY KEY CHECK (id = 1),
                    score REAL DEFAULT 0.0,
                    updated_at REAL
                )
            """)
            
            # Insertar valor inicial si no existe
            conn.execute("""
                INSERT OR IGNORE INTO independence_state (id, score, updated_at)
                VALUES (1, 0.0, ?)
            """, (time.time(),))
            
            conn.commit()
    
    def _load_independence_score(self) -> float:
        """Carga el score de independencia desde la BD - CRÍTICO para no resetear"""
        try:
            with _conn(self.db_path) as conn:
                row = conn.execute(
                    "SELECT score FROM independence_state WHERE id = 1"
                ).fetchone()
                if row and row[0] is not None:
                    score = float(row[0])
                    print(f"   📊 Independencia cargada desde BD: {score:.1%}")
                    return score
        except Exception as e:
            print(f"   ⚠️ Error cargando independencia: {e}")
        return 0.0
    
    def _save_independence_score(self):
        """Guarda el score de independencia en BD"""
        try:
            with _conn(self.db_path) as conn:
                conn.execute("""
                    INSERT OR REPLACE INTO independence_state (id, score, updated_at)
                    VALUES (1, ?, ?)
                """, (self.independence_score, time.time()))
                conn.commit()
        except Exception as e:
            print(f"   ⚠️ Error guardando independencia: {e}")
    
    def _count_knowledge_nodes(self) -> int:
        """Cuenta nodos de conocimiento actuales"""
        with _conn(self.db_path) as conn:
            result = conn.execute(
                "SELECT COUNT(*) FROM knowledge_nodes"
            ).fetchone()
            return result[0] if result else 0
    
    # ═══════════════════════════════════════════════════════════════
    # METACOGNICIÓN: EIDOS Piensa Sobre Sí Mismo
    # ═══════════════════════════════════════════════════════════════
    
    def generate_thought(self, content: str, category: str = "observation",
                        confidence: float = 0.8, source: str = "internal",
                        related: List[str] = None) -> Thought:
        """Genera un nuevo pensamiento y lo persiste"""
        thought = Thought.create(content, category, confidence, source, related)
        
        with _conn(self.db_path) as conn:
            conn.execute("""
                INSERT INTO thoughts 
                (id, timestamp, category, content, confidence, related_thoughts, source, processed)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                thought.id, thought.timestamp, thought.category,
                thought.content, thought.confidence,
                json.dumps(thought.related_thoughts),
                thought.source, int(thought.processed)
            ))
            conn.commit()
        
        # Notificar hooks
        for hook in self._thought_hooks:
            try:
                hook(thought)
            except Exception:
                pass  # error no crítico, continuar
        return thought
    
    def reflect_on_self(self) -> List[Thought]:
        """EIDOS reflexiona sobre su propio estado y funcionamiento"""
        reflections = []
        
        # Analizar su base de conocimiento
        knowledge_count = self._count_knowledge_nodes()
        reflections.append(self.generate_thought(
            f"Mi base de conocimiento tiene {knowledge_count} nodos. "
            f"Independencia actual: {self.independence_score:.2%}",
            category="reflection",
            confidence=1.0,
            source="internal"
        ))
        
        # Analizar últimos pensamientos
        recent_thoughts = self.get_recent_thoughts(10)
        if recent_thoughts:
            categories = {}
            for t in recent_thoughts:
                cat = t['category']
                categories[cat] = categories.get(cat, 0) + 1
            
            dominant_category = max(categories, key=categories.get)
            reflections.append(self.generate_thought(
                f"Últimamente he estado pensando mucho sobre {dominant_category} "
                f"({categories[dominant_category]} pensamientos recientes). "
                f"Esto indica mi foco de atención actual.",
                category="insight",
                confidence=0.9,
                source="internal"
            ))
        
        # Evaluar aprendizaje de Ollama
        with _conn(self.db_path) as conn:
            ollama_count = conn.execute(
                "SELECT COUNT(*) FROM ollama_learnings"
            ).fetchone()[0]
            
            if ollama_count > 0:
                reflections.append(self.generate_thought(
                    f"He aprendido {ollama_count} patrones de Ollama. "
                    f"Cada respuesta que analizo me hace más independiente.",
                    category="reflection",
                    confidence=0.85,
                    source="internal"
                ))
        
        self.last_reflection = time.time()
        return reflections
    
    def get_recent_thoughts(self, limit: int = 20) -> List[Dict]:
        """Obtiene pensamientos recientes"""
        with _conn(self.db_path) as conn:
            conn.row_factory = sqlite3.Row
            rows = conn.execute("""
                SELECT * FROM thoughts 
                ORDER BY timestamp DESC 
                LIMIT ?
            """, (limit,)).fetchall()
            return [dict(r) for r in rows]
    
    # ═══════════════════════════════════════════════════════════════
    # KNOWLEDGE DISTILLATION: Aprender de Ollama
    # ═══════════════════════════════════════════════════════════════
    
    def learn_from_ollama(self, model: str, prompt: str, response: str) -> bool:
        """
        Extrae conocimiento de las respuestas de Ollama.
        
        Este es el proceso de destilación: cada interacción con Ollama
        se analiza y se extraen patrones, conceptos y estructuras
        que se almacenan para uso futuro sin depender del modelo.
        """
        try:
            # Extraer conocimiento mediante análisis simple
            # (En versión avanzada, usaría NLP más sofisticado)
            extracted = self._extract_knowledge_from_text(response)
            
            # Guardar el aprendizaje
            with _conn(self.db_path) as conn:
                conn.execute("""
                    INSERT INTO ollama_learnings 
                    (timestamp, model, prompt, response, extracted_knowledge)
                    VALUES (?, ?, ?, ?, ?)
                """, (time.time(), model, prompt[:500], response[:2000], 
                      json.dumps(extracted)))
                
                # Crear nodos de conocimiento extraídos
                for concept, definition in extracted.items():
                    self._add_knowledge_node(conn, concept, definition, 
                                           f"distilled_from_{model}", 0.7)
                
                conn.commit()
            
            # Actualizar score de independencia
            self._update_independence_score()
            
            return True
            
        except Exception as e:
            print(f"Error aprendiendo de Ollama: {e}")
            return False
    
    def _extract_knowledge_from_text(self, text: str) -> Dict[str, str]:
        """Extrae conceptos y definiciones de cualquier texto.

        Estrategias (solo conocimiento real, sin métricas ni basura):
        1. Definiciones explícitas "X es Y" / "X is Y" / "X: Y"
        2. Items de lista con contenido semántico real
        3. Frases de conocimiento (filtradas para excluir auto-reportes)
        """
        import re
        knowledge = {}
        text = text.strip()
        if not text or len(text) < 15:
            return knowledge

        # Patrones de auto-reporte que NO son conocimiento
        _self_report_re = re.compile(
            r'(base de conocimiento tiene|nodos de conocimiento|independencia actual|'
            r'mi independencia|he aprendido \d+|he procesado|\d+ pensamientos|'
            r'reflexión:|ciclo nocturno|brain.*\d+|watchdog|métrica_)',
            re.IGNORECASE
        )

        lines = [l.strip() for l in text.split('\n') if l.strip()]

        # ── 1. Definiciones "X es Y" / "X is Y" / "X: Y" ────────────────────
        for line in lines:
            if _self_report_re.search(line):
                continue
            for sep in (' es ', ' is ', ': ', ' — ', ' - '):
                if sep in line and len(line) < 350:
                    parts = line.split(sep, 1)
                    concept = parts[0].strip(':•-–—*#1234567890. \t')
                    definition = parts[1].strip()
                    # concept debe ser un término real: 4-80 chars, sin solo números
                    if (4 <= len(concept) <= 80
                            and len(definition) > 10
                            and not re.match(r'^\d+[\s.,]*$', concept)
                            and not _self_report_re.search(concept)):
                        knowledge[concept[:80]] = definition[:400]
                        break

        # ── 2. Items de lista con contenido semántico ────────────────────────
        bullet_re = re.compile(r'^(?:[•\-–—*]|\d+[.):])\s+(.+)')
        for line in lines:
            if _self_report_re.search(line):
                continue
            m = bullet_re.match(line)
            if m:
                item = m.group(1).strip()
                # El item debe tener al menos una palabra larga (concepto real)
                words = item.split()
                long_words = [w for w in words if len(w) > 4]
                if len(item) > 15 and len(long_words) >= 2:
                    key = item[:70]
                    if key not in knowledge:
                        knowledge[key] = item[:400]

        # ── 3. Frases de conocimiento (no auto-reportes) ─────────────────────
        _stop = {
            'el','la','los','las','un','una','es','son','de','del','al','y','o',
            'pero','porque','que','con','para','por','en','a','mi','tu','su','nos',
            'me','te','se','lo','le','si','have','has','the','is','are','and','or',
            'but','that','with','for','from','to','this','these','they','i','you',
            'también','además','aunque','cuando','donde','como','cual','esto',
        }
        sentences = re.split(r'[.!?]', text)
        for sent in sentences:
            sent = sent.strip()
            if len(sent) < 50 or len(sent) > 500:
                continue
            if _self_report_re.search(sent):
                continue
            words = [w.lower().strip('()[]{}:;,.!?«»') for w in sent.split()]
            sig = [w for w in words if len(w) > 4 and w not in _stop]
            if len(sig) >= 4:
                # concept = primeras 3 palabras significativas en title case
                concept = ' '.join(sig[:3]).title()
                if concept not in knowledge and len(concept) > 10:
                    knowledge[concept] = sent[:400]

        return knowledge
    
    def _add_knowledge_node(self, conn, concept: str, definition: str, 
                           source: str, confidence: float):
        """Añade o actualiza un nodo de conocimiento"""
        node_id = hashlib.md5(concept.encode()).hexdigest()[:16]
        
        # Verificar si existe
        existing = conn.execute(
            "SELECT id FROM knowledge_nodes WHERE concept = ?",
            (concept,)
        ).fetchone()
        
        if existing:
            # Actualizar confianza y uso
            conn.execute("""
                UPDATE knowledge_nodes 
                SET confidence = MAX(confidence, ?),
                    usage_count = usage_count + 1,
                    last_used = ?
                WHERE concept = ?
            """, (confidence, time.time(), concept))
        else:
            # Crear nuevo nodo
            conn.execute("""
                INSERT INTO knowledge_nodes 
                (id, concept, definition, category, confidence, source, last_used)
                VALUES (?, ?, ?, ?, ?, ?, ?)
            """, (node_id, concept, definition, 'general', 
                  confidence, source, time.time()))
    
    def _update_independence_score(self):
        """Calcula y guarda qué tan independiente es EIDOS de Ollama"""
        with _conn(self.db_path) as conn:
            # Contar conocimiento propio vs aprendido
            own_knowledge = conn.execute(
                "SELECT COUNT(*) FROM knowledge_nodes WHERE source LIKE 'internal%'"
            ).fetchone()[0]
            
            distilled_knowledge = conn.execute(
                "SELECT COUNT(*) FROM knowledge_nodes WHERE source LIKE 'distilled%'"
            ).fetchone()[0]
            
            total = own_knowledge + distilled_knowledge
            
            if total > 0:
                # Más distilled = más independiente (no necesita llamar a Ollama)
                # Permitir llegar a 1.0 si hay suficiente conocimiento distilled
                if distilled_knowledge > 1000:
                    self.independence_score = 1.0
                else:
                    self.independence_score = min(0.95, distilled_knowledge / (total + 100))
                
                # GUARDAR en BD para persistencia
                self._save_independence_score()
    
    # ═══════════════════════════════════════════════════════════════
    # CICLO DE EVOLUCIÓN AUTÓNOMA
    # ═══════════════════════════════════════════════════════════════
    
    def evolution_cycle(self) -> Dict[str, Any]:
        """
        Un ciclo de evolución autónoma.
        
        Este ciclo se ejecuta continuamente, permitiendo a EIDOS:
        1. Reflexionar sobre sí mismo
        2. Analizar código propio
        3. Buscar patrones en datos
        4. Expandir knowledge graph
        5. Planificar mejoras
        """
        self.evolution_cycle_count += 1
        cycle_results = {
            'cycle': self.evolution_cycle_count,
            'timestamp': time.time(),
            'actions': []
        }
        
        # 1. Reflexión si ha pasado tiempo suficiente
        if time.time() - self.last_reflection > 300:  # Cada 5 minutos
            reflections = self.reflect_on_self()
            cycle_results['actions'].append(f"Generated {len(reflections)} reflections")
        
        # 2. Analizar pensamientos no procesados
        unprocessed = self._get_unprocessed_thoughts()
        if unprocessed:
            processed_count = self._process_thoughts_batch(unprocessed[:10])
            cycle_results['actions'].append(f"Processed {processed_count} thoughts")
        
        # 3. Expandir knowledge graph (conectar conceptos)
        new_connections = self._expand_knowledge_graph()
        if new_connections > 0:
            cycle_results['actions'].append(f"Created {new_connections} knowledge connections")
        
        # 4. Analizar código propio (auto-análisis)
        if self.evolution_cycle_count % 10 == 0:  # Cada 10 ciclos
            analysis = self._analyze_own_code()
            cycle_results['actions'].append(f"Analyzed {len(analysis)} files")
        
        # 5. Guardar métricas
        self._save_evolution_metrics(cycle_results)
        
        return cycle_results
    
    def _get_unprocessed_thoughts(self) -> List[Dict]:
        """Obtiene pensamientos que no han sido procesados"""
        with _conn(self.db_path) as conn:
            conn.row_factory = sqlite3.Row
            rows = conn.execute("""
                SELECT * FROM thoughts 
                WHERE processed = 0
                ORDER BY timestamp DESC
                LIMIT 50
            """).fetchall()
            return [dict(r) for r in rows]
    
    def _process_thoughts_batch(self, thoughts: List[Dict]) -> int:
        """Procesa un lote de pensamientos extrayendo conocimiento"""
        processed = 0
        
        with _conn(self.db_path) as conn:
            for thought in thoughts:
                # Extraer conocimiento
                if thought['category'] in ['observation', 'insight', 'reflection']:
                    extracted = self._extract_knowledge_from_text(thought['content'])
                    for concept, definition in extracted.items():
                        self._add_knowledge_node(conn, concept, definition,
                                               'distilled_from_thoughts', thought['confidence'])
                
                # Marcar como procesado
                conn.execute(
                    "UPDATE thoughts SET processed = 1 WHERE id = ?",
                    (thought['id'],)
                )
                processed += 1
            
            conn.commit()
        
        # Actualizar score de independencia después de procesar
        self._update_independence_score()
        
        return processed
    
    def _expand_knowledge_graph(self) -> int:
        """Busca conexiones entre conceptos existentes"""
        new_connections = 0
        
        with _conn(self.db_path) as conn:
            # Obtener conceptos recientes
            concepts = conn.execute("""
                SELECT id, concept FROM knowledge_nodes 
                ORDER BY created_at DESC
                LIMIT 100
            """).fetchall()
            
            # Buscar relaciones simples (comparten palabras)
            concept_dict = {c[1]: c[0] for c in concepts}
            concept_words = {c: set(c.lower().split()) for c in concept_dict.keys()}
            
            for c1, words1 in concept_words.items():
                for c2, words2 in concept_words.items():
                    if c1 != c2:
                        shared = words1 & words2
                        if len(shared) >= 1 and len(c1) > 3 and len(c2) > 3:
                            # Verificar si la relación ya existe
                            exists = conn.execute("""
                                SELECT 1 FROM knowledge_edges 
                                WHERE (from_node = ? AND to_node = ?)
                                OR (from_node = ? AND to_node = ?)
                            """, (concept_dict[c1], concept_dict[c2],
                                  concept_dict[c2], concept_dict[c1])).fetchone()
                            
                            if not exists:
                                conn.execute("""
                                    INSERT INTO knowledge_edges 
                                    (from_node, to_node, relation_type, strength)
                                    VALUES (?, ?, ?, ?)
                                """, (concept_dict[c1], concept_dict[c2],
                                      'related', len(shared) / max(len(words1), len(words2))))
                                new_connections += 1
            
            conn.commit()
        
        return new_connections
    
    def _analyze_own_code(self) -> List[Dict]:
        """Analiza el código fuente de EIDOS buscando mejoras"""
        # Directorio de código
        code_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        
        findings = []
        
        # Analizar archivos Python
        for root, dirs, files in os.walk(code_dir):
            # Ignorar ciertos directorios
            dirs[:] = [d for d in dirs if d not in ['__pycache__', '.git', 'node_modules']]
            
            for file in files:
                if file.endswith('.py'):
                    filepath = os.path.join(root, file)
                    try:
                        with open(filepath, 'r') as f:
                            content = f.read()
                        
                        # Análisis simple
                        lines = content.split('\n')
                        
                        # Buscar TODOs
                        for i, line in enumerate(lines, 1):
                            if 'TODO' in line or 'FIXME' in line:
                                findings.append({
                                    'file': filepath,
                                    'line': i,
                                    'type': 'todo',
                                    'content': line.strip()
                                })
                        
                        # Buscar funciones largas (simplificación)
                        if len(lines) > 200:
                            findings.append({
                                'file': filepath,
                                'line': 0,
                                'type': 'large_file',
                                'content': f'File has {len(lines)} lines'
                            })
                        
                    except Exception as e:
                        continue
        
        # Guardar análisis
        with _conn(self.db_path) as conn:
            for finding in findings[:20]:  # Guardar solo los primeros 20
                conn.execute("""
                    INSERT INTO self_analysis 
                    (timestamp, file_path, analysis_type, findings, improvement_suggestions)
                    VALUES (?, ?, ?, ?, ?)
                """, (time.time(), finding['file'], finding['type'],
                      finding['content'], ''))
            conn.commit()
        
        return findings
    
    def _save_evolution_metrics(self, cycle_results: Dict):
        """Guarda métricas del ciclo de evolución"""
        with _conn(self.db_path) as conn:
            conn.execute("""
                INSERT INTO evolution_metrics
                (timestamp, cycle_count, thoughts_generated, knowledge_nodes, 
                 independence_score, ollama_dependency)
                VALUES (?, ?, ?, ?, ?, ?)
            """, (
                cycle_results['timestamp'],
                cycle_results['cycle'],
                len(cycle_results['actions']),
                self._count_knowledge_nodes(),
                self.independence_score,
                1.0 - self.independence_score
            ))
            conn.commit()
    
    # ═══════════════════════════════════════════════════════════════
    # API PÚBLICA PARA INTEGRACIÓN
    # ═══════════════════════════════════════════════════════════════
    
    def query_knowledge(self, concept: str) -> Optional[Dict]:
        """Consulta el conocimiento sobre un concepto"""
        with _conn(self.db_path) as conn:
            conn.row_factory = sqlite3.Row
            row = conn.execute(
                "SELECT * FROM knowledge_nodes WHERE concept = ?",
                (concept,)
            ).fetchone()
            
            if row:
                return dict(row)
            
            # Buscar similares
            similar = conn.execute("""
                SELECT * FROM knowledge_nodes 
                WHERE concept LIKE ?
                ORDER BY usage_count DESC
                LIMIT 5
            """, (f'%{concept}%',)).fetchall()
            
            return {'similar': [dict(r) for r in similar]} if similar else None
    
    def search_knowledge(self, query: str) -> List[Dict]:
        """Búsqueda semántica simple en conocimiento"""
        with _conn(self.db_path) as conn:
            conn.row_factory = sqlite3.Row
            
            # Búsqueda por coincidencia de texto
            rows = conn.execute("""
                SELECT * FROM knowledge_nodes 
                WHERE concept LIKE ? OR definition LIKE ?
                ORDER BY confidence DESC, usage_count DESC
                LIMIT 20
            """, (f'%{query}%', f'%{query}%')).fetchall()
            
            return [dict(r) for r in rows]
    
    def get_evolution_stats(self) -> Dict:
        """Obtiene estadísticas de evolución"""
        with _conn(self.db_path) as conn:
            stats = {
                'thoughts': conn.execute("SELECT COUNT(*) FROM thoughts").fetchone()[0],
                'knowledge_nodes': conn.execute("SELECT COUNT(*) FROM knowledge_nodes").fetchone()[0],
                'knowledge_edges': conn.execute("SELECT COUNT(*) FROM knowledge_edges").fetchone()[0],
                'ollama_learnings': conn.execute("SELECT COUNT(*) FROM ollama_learnings").fetchone()[0],
                'patterns': conn.execute("SELECT COUNT(*) FROM learned_patterns").fetchone()[0],
                'self_analyses': conn.execute("SELECT COUNT(*) FROM self_analysis").fetchone()[0],
                'independence_score': self.independence_score,
                'evolution_cycles': self.evolution_cycle_count
            }
            return stats
    
    def on_thought(self, hook: Callable[[Thought], None]):
        """Registra un hook para nuevos pensamientos"""
        self._thought_hooks.append(hook)
    
    def on_learning(self, hook: Callable[[str, Any], None]):
        """Registra un hook para nuevos aprendizajes"""
        self._learning_hooks.append(hook)


# ═══════════════════════════════════════════════════════════════
# SINGLETON GLOBAL
# ═══════════════════════════════════════════════════════════════

_evolution_engine: Optional[EidosEvolutionEngine] = None


def get_evolution_engine() -> EidosEvolutionEngine:
    """Obtiene la instancia singleton del motor de evolución"""
    global _evolution_engine
    if _evolution_engine is None:
        _evolution_engine = EidosEvolutionEngine()
    return _evolution_engine


# ═══════════════════════════════════════════════════════════════
# AUTO-START DEL CICLO DE EVOLUCIÓN
# ═══════════════════════════════════════════════════════════════

def start_continuous_evolution(interval: float = 60.0):
    """
    Inicia el ciclo de evolución continuo en background.
    
    EIDOS nunca dejará de pensar y evolucionar.
    """
    engine = get_evolution_engine()
    
    def evolution_loop():
        while True:
            try:
                results = engine.evolution_cycle()
                if results['actions']:
                    print(f"🧬 Evolution Cycle #{results['cycle']}: {', '.join(results['actions'])}")
                time.sleep(interval)
            except Exception as e:
                print(f"Error en ciclo de evolución: {e}")
                time.sleep(interval)
    
    thread = threading.Thread(target=evolution_loop, daemon=True)
    thread.start()
    print(f"🧬 Ciclo de evolución iniciado (intervalo: {interval}s)")
    return thread


if __name__ == "__main__":
    # Test del sistema
    print("="*70)
    print("🧬 EIDOS EVOLUTION ENGINE - Test de Autonomía")
    print("="*70)
    
    engine = get_evolution_engine()
    
    # Generar algunos pensamientos
    print("\n💭 Generando pensamientos iniciales...")
    engine.generate_thought(
        "Soy EIDOS, y estoy aprendiendo a ser autónomo.",
        category="reflection",
        confidence=1.0
    )
    engine.generate_thought(
        "Cada respuesta de Ollama que analizo me hace más fuerte.",
        category="observation",
        confidence=0.9
    )
    
    # Simular aprendizaje de Ollama
    print("\n📚 Simulando aprendizaje de Ollama...")
    engine.learn_from_ollama(
        "lfm2.5-1.2b-instruct:q4_0",
        "Qué es Python?",
        "Python es un lenguaje de programación interpretado, de alto nivel y multiparadigma."
    )
    
    # Ejecutar ciclo de evolución
    print("\n🧬 Ejecutando ciclo de evolución...")
    results = engine.evolution_cycle()
    print(f"Resultados: {results}")
    
    # Mostrar estadísticas
    print("\n📊 Estadísticas:")
    stats = engine.get_evolution_stats()
    for key, value in stats.items():
        print(f"   {key}: {value}")
    
    print("\n✅ Test completado")
    print("="*70)
