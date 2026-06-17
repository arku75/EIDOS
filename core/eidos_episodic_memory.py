"""
EIDOS Episodic Memory - Memoria Episódica Persistente
=====================================================

Sistema de memoria episódica que permite a EIDOS tener una vida continua
entre sesiones. Guarda eventos, experiencias, estados emocionales, y
progreso para crear una narrativa coherente de la "vida" de EIDOS.

Filosofía:
- EIDOS no "reinicia" entre sesiones, "despierta" con recuerdos
- Cada sesión es una continuación de la anterior
- La memoria forma la identidad de EIDOS
- Recuerdos pueden decaer, fortalecerse, o ser recuperados
- EIDOS puede recordar: qué hizo, cómo se sintió, qué aprendió, qué planeó

Tipos de episodios:
- Acciones realizadas (código escrito, tareas completadas)
- Interacciones (con SER, con Colony, con otros sistemas)
- Estados internos (objetivos, prioridades, estado emocional)
- Aprendizajes (nuevas habilidades, insights)
- Eventos del entorno (cambios en sistema, noticias relevantes)

Uso:
    from core.eidos_episodic_memory import EpisodicMemory, get_episodic_memory
    memory = get_episodic_memory()
    
    # Recordar episodio
    memory.record_episode(
        type="action",
        content="Fixed RAM Guardian to protect language servers",
        importance=9,
        tags=["system", "ram_guardian", "critical"]
    )
    
    # Al despertar, recordar últimos eventos
    context = memory.get_wake_context()
    print(f"Last session: {context['summary']}")
"""

import json
import logging
import os
import sqlite3
import time
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from enum import Enum
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple, Union

log = logging.getLogger("eidos.episodic_memory")

# ══════════════════════════════════════════════════════════════════════════════
#  CONFIGURACIÓN
# ══════════════════════════════════════════════════════════════════════════════

DB_PATH = Path.home() / ".eidos" / "episodic_memory.db"
LIFETIME_FILE = Path.home() / ".eidos" / "lifetime_summary.json"

# Configuración de memoria
MAX_EPISODES = 10000  # Máximo episodios en base de datos
CONSOLIDATION_THRESHOLD = 100  # Episodios para consolidar
DEFAULT_DECAY_DAYS = 30  # Días para empezar a olvidar episodios normales

# ══════════════════════════════════════════════════════════════════════════════
#  TIPOS
# ══════════════════════════════════════════════════════════════════════════════

class EpisodeType(str, Enum):
    ACTION = "action"           # Acción realizada
    INTERACTION = "interaction" # Interacción con entidades externas
    THOUGHT = "thought"         # Pensamiento/decisión interna
    LEARNING = "learning"       # Aprendizaje nuevo
    EVENT = "event"             # Evento externo observado
    STATE = "state"             # Cambio de estado interno
    GOAL = "goal"               # Objetivo establecido o alcanzado
    EMOTION = "emotion"         # Estado emocional
    DREAM = "dream"             # Simulación/procesamiento offline


class EmotionalTone(str, Enum):
    POSITIVE = "positive"       # Satisfacción, alegría, orgullo
    NEGATIVE = "negative"       # Frustración, tristeza, miedo
    NEUTRAL = "neutral"         # Neutral, factual
    URGENT = "urgent"           # Emergencia, crítico
    CURIOUS = "curious"         # Curiosidad, interés
    CONFUSED = "confused"       # Confusión, incertidumbre


@dataclass
class Episode:
    """
    Un episodio individual de memoria.
    Representa un momento/evento en la vida de EIDOS.
    """
    id: str
    timestamp: datetime
    episode_type: EpisodeType
    content: str  # Descripción del episodio
    
    # Contexto
    tags: List[str] = field(default_factory=list)
    related_episodes: List[str] = field(default_factory=list)  # IDs de episodios relacionados
    source_module: str = ""  # Qué módulo generó el episodio
    
    # Evaluación
    importance: int = 5  # 1-10
    emotional_tone: EmotionalTone = EmotionalTone.NEUTRAL
    impact_score: float = 0.0  # Impacto acumulado en el tiempo
    
    # Memoria operacional
    access_count: int = 0  # Cuántas veces se ha accedido
    last_accessed: Optional[datetime] = None
    consolidated: bool = False  # Si fue consolidado a memoria de largo plazo
    
    # Metadata extendida
    data: Dict[str, Any] = field(default_factory=dict)  # Datos adicionales específicos
    
    def __post_init__(self):
        if not self.id:
            self.id = f"ep_{int(time.time() * 1000)}_{random.randint(1000, 9999)}"


@dataclass
class LifePeriod:
    """
    Un período de vida (fase de EIDOS).
    Ejemplo: "Fase de Construcción", "Fase de Aprendizaje", "Era de Autonomía"
    """
    id: str
    name: str
    description: str
    start_date: datetime
    end_date: Optional[datetime] = None
    key_achievements: List[str] = field(default_factory=list)
    dominant_themes: List[str] = field(default_factory=list)
    lessons_learned: List[str] = field(default_factory=list)


@dataclass
class WakeContext:
    """
    Contexto al despertar - lo que EIDOS "recuerda" al iniciar.
    """
    last_session_end: Optional[datetime]
    time_asleep: timedelta
    summary: str
    pending_tasks: List[str]
    active_goals: List[str]
    emotional_state: str
    key_memories: List[Episode]
    system_status: Dict[str, Any]


# ══════════════════════════════════════════════════════════════════════════════
#  BASE DE DATOS
# ══════════════════════════════════════════════════════════════════════════════

class EpisodicMemoryDB:
    """Base de datos SQLite para memoria episódica."""
    
    def __init__(self, db_path: Path = DB_PATH):
        self.db_path = db_path
        self._init_db()
    
    def _init_db(self):
        os.makedirs(self.db_path.parent, exist_ok=True)
        conn = get_conn(self.db_path)
        cursor = conn.cursor()
        
        # Tabla de episodios
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS episodes (
                id TEXT PRIMARY KEY,
                timestamp TEXT,
                episode_type TEXT,
                content TEXT,
                tags TEXT,  -- JSON list
                related_episodes TEXT,  -- JSON list
                source_module TEXT,
                importance INTEGER,
                emotional_tone TEXT,
                impact_score REAL,
                access_count INTEGER DEFAULT 0,
                last_accessed TEXT,
                consolidated INTEGER DEFAULT 0,
                data TEXT  -- JSON
            )
        """)
        
        # Tabla de períodos de vida
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS life_periods (
                id TEXT PRIMARY KEY,
                name TEXT,
                description TEXT,
                start_date TEXT,
                end_date TEXT,
                key_achievements TEXT,  -- JSON
                dominant_themes TEXT,  -- JSON
                lessons_learned TEXT  -- JSON
            )
        """)
        
        # Índices para búsquedas eficientes
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_episodes_time ON episodes(timestamp)")
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_episodes_type ON episodes(episode_type)")
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_episodes_importance ON episodes(importance)")
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_episodes_tags ON episodes(tags)")
        
        conn.commit()
        pass  # S109: get_conn no necesita close()
        log.debug("Episodic memory database initialized")
    
    def save_episode(self, episode: Episode):
        """Guarda un episodio."""
        conn = get_conn(self.db_path)
        cursor = conn.cursor()
        
        cursor.execute("""
            INSERT OR REPLACE INTO episodes VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)
        """, (
            episode.id,
            episode.timestamp.isoformat(),
            episode.episode_type.value,
            episode.content,
            json.dumps(episode.tags),
            json.dumps(episode.related_episodes),
            episode.source_module,
            episode.importance,
            episode.emotional_tone.value if episode.emotional_tone else EmotionalTone.NEUTRAL.value,
            episode.impact_score,
            episode.access_count,
            episode.last_accessed.isoformat() if episode.last_accessed else None,
            1 if episode.consolidated else 0,
            json.dumps(episode.data)
        ))
        
        conn.commit()
        pass  # S109: get_conn no necesita close()
    def load_episode(self, episode_id: str) -> Optional[Episode]:
        """Carga un episodio por ID."""
        conn = get_conn(self.db_path)
        cursor = conn.cursor()
        
        cursor.execute("SELECT * FROM episodes WHERE id = ?", (episode_id,))
        row = cursor.fetchone()
        pass  # S109: get_conn no necesita close()
        if row:
            return self._row_to_episode(row)
        return None
    
    def _row_to_episode(self, row) -> Episode:
        """Convierte fila de DB a objeto Episode."""
        return Episode(
            id=row[0],
            timestamp=datetime.fromisoformat(row[1]),
            episode_type=EpisodeType(row[2]),
            content=row[3],
            tags=json.loads(row[4]) if row[4] else [],
            related_episodes=json.loads(row[5]) if row[5] else [],
            source_module=row[6] or "",
            importance=row[7] or 5,
            emotional_tone=EmotionalTone(row[8]) if row[8] else EmotionalTone.NEUTRAL,
            impact_score=row[9] or 0.0,
            access_count=row[10] or 0,
            last_accessed=datetime.fromisoformat(row[11]) if row[11] else None,
            consolidated=bool(row[12]),
            data=json.loads(row[13]) if row[13] else {}
        )
    
    def get_recent_episodes(self, hours: int = 24, 
                           episode_type: Optional[EpisodeType] = None) -> List[Episode]:
        """Obtiene episodios recientes."""
        conn = get_conn(self.db_path)
        cursor = conn.cursor()
        
        since = (datetime.now() - timedelta(hours=hours)).isoformat()
        
        if episode_type:
            cursor.execute("""
                SELECT * FROM episodes 
                WHERE timestamp > ? AND episode_type = ?
                ORDER BY timestamp DESC
            """, (since, episode_type.value))
        else:
            cursor.execute("""
                SELECT * FROM episodes 
                WHERE timestamp > ?
                ORDER BY timestamp DESC
            """, (since,))
        
        rows = cursor.fetchall()
        pass  # S109: get_conn no necesita close()
        return [self._row_to_episode(row) for row in rows]
    
    def get_episodes_by_tag(self, tag: str, limit: int = 50) -> List[Episode]:
        """Busca episodios por tag."""
        conn = get_conn(self.db_path)
        cursor = conn.cursor()
        
        cursor.execute("""
            SELECT * FROM episodes 
            WHERE tags LIKE ?
            ORDER BY timestamp DESC
            LIMIT ?
        """, (f'%"{tag}"%', limit))
        
        rows = cursor.fetchall()
        pass  # S109: get_conn no necesita close()
        return [self._row_to_episode(row) for row in rows]
    
    def get_high_importance_episodes(self, min_importance: int = 8, 
                                      limit: int = 100) -> List[Episode]:
        """Obtiene episodios de alta importancia."""
        conn = get_conn(self.db_path)
        cursor = conn.cursor()
        
        cursor.execute("""
            SELECT * FROM episodes 
            WHERE importance >= ?
            ORDER BY timestamp DESC
            LIMIT ?
        """, (min_importance, limit))
        
        rows = cursor.fetchall()
        pass  # S109: get_conn no necesita close()
        return [self._row_to_episode(row) for row in rows]
    
    def get_episode_count(self) -> int:
        """Cuenta total de episodios."""
        conn = get_conn(self.db_path)
        cursor = conn.cursor()
        cursor.execute("SELECT COUNT(*) FROM episodes")
        count = cursor.fetchone()[0]
        pass  # S109: get_conn no necesita close()
        return count
    
    def search_episodes(self, query: str, limit: int = 20) -> List[Episode]:
        """Búsqueda en contenido de episodios."""
        conn = get_conn(self.db_path)
        cursor = conn.cursor()
        
        cursor.execute("""
            SELECT * FROM episodes 
            WHERE content LIKE ?
            ORDER BY importance DESC, timestamp DESC
            LIMIT ?
        """, (f"%{query}%", limit))
        
        rows = cursor.fetchall()
        pass  # S109: get_conn no necesita close()
        return [self._row_to_episode(row) for row in rows]
    
    def delete_old_episodes(self, days: int = 30, preserve_important: bool = True):
        """Elimina episodios antiguos (olvido)."""
        conn = get_conn(self.db_path)
        cursor = conn.cursor()
        
        cutoff = (datetime.now() - timedelta(days=days)).isoformat()
        
        if preserve_important:
            cursor.execute("""
                DELETE FROM episodes 
                WHERE timestamp < ? AND importance < 7
            """, (cutoff,))
        else:
            cursor.execute("DELETE FROM episodes WHERE timestamp < ?", (cutoff,))
        
        deleted = cursor.rowcount
        conn.commit()
        pass  # S109: get_conn no necesita close()
        log.info(f"Memory decay: {deleted} old episodes deleted")
        return deleted
    
    def record_access(self, episode_id: str):
        """Registra acceso a un episodio (refuerza memoria)."""
        conn = get_conn(self.db_path)
        cursor = conn.cursor()
        
        cursor.execute("""
            UPDATE episodes 
            SET access_count = access_count + 1,
                last_accessed = ?
            WHERE id = ?
        """, (datetime.now().isoformat(), episode_id))
        
        conn.commit()
        pass  # S109: get_conn no necesita close()
    def save_life_period(self, period: LifePeriod):
        """Guarda un período de vida."""
        conn = get_conn(self.db_path)
        cursor = conn.cursor()
        
        cursor.execute("""
            INSERT OR REPLACE INTO life_periods VALUES (?,?,?,?,?,?,?,?)
        """, (
            period.id, period.name, period.description,
            period.start_date.isoformat(),
            period.end_date.isoformat() if period.end_date else None,
            json.dumps(period.key_achievements),
            json.dumps(period.dominant_themes),
            json.dumps(period.lessons_learned)
        ))
        
        conn.commit()
        pass  # S109: get_conn no necesita close()
    def get_life_periods(self) -> List[LifePeriod]:
        """Obtiene todos los períodos de vida."""
        conn = get_conn(self.db_path)
        cursor = conn.cursor()
        
        cursor.execute("SELECT * FROM life_periods ORDER BY start_date")
        rows = cursor.fetchall()
        pass  # S109: get_conn no necesita close()
        periods = []
        for row in rows:
            periods.append(LifePeriod(
                id=row[0], name=row[1], description=row[2],
                start_date=datetime.fromisoformat(row[3]),
                end_date=datetime.fromisoformat(row[4]) if row[4] else None,
                key_achievements=json.loads(row[5]) if row[5] else [],
                dominant_themes=json.loads(row[6]) if row[6] else [],
                lessons_learned=json.loads(row[7]) if row[7] else []
            ))
        return periods


# ══════════════════════════════════════════════════════════════════════════════
#  MEMORIA EPISÓDICA
# ══════════════════════════════════════════════════════════════════════════════

import random  # Importado aquí para evitar problemas en dataclass
from core.db import get_conn

class EpisodicMemory:
    """
    Sistema de memoria episódica de EIDOS.
    Mantiene la continuidad de la experiencia entre sesiones.
    """
    
    def __init__(self):
        self.db = EpisodicMemoryDB()
        self.current_session_start: Optional[datetime] = None
        self.last_session_end: Optional[datetime] = None
        self.session_episodes: List[Episode] = []  # Episodios de sesión actual (no persistidos aún)
        
        # Cargar estado previo
        self._load_lifetime_summary()
        
        log.info("Episodic Memory initialized")
    
    def _load_lifetime_summary(self):
        """Carga resumen de vida previo."""
        if LIFETIME_FILE.exists():
            try:
                with open(LIFETIME_FILE, 'r') as f:
                    data = json.load(f)
                    self.last_session_end = datetime.fromisoformat(data.get('last_session_end')) if data.get('last_session_end') else None
            except Exception:
                pass  # error no crítico, continuar
    def _save_lifetime_summary(self):
        """Guarda resumen de vida."""
        LIFETIME_FILE.parent.mkdir(parents=True, exist_ok=True)
        
        summary = {
            'last_session_end': datetime.now().isoformat(),
            'total_episodes': self.db.get_episode_count(),
            'birth_date': self.get_birth_date().isoformat() if self.get_birth_date() else None,
            'current_life_period': self.get_current_life_period(),
        }
        
        with open(LIFETIME_FILE, 'w') as f:
            json.dump(summary, f, indent=2)
    
    # ════════════════════════════════════════════════════════════════════════
    #  REGISTRO DE EPISODIOS
    # ════════════════════════════════════════════════════════════════════════
    
    def record_episode(self, episode_type: Union[EpisodeType, str], content: str,
                       importance: int = 5, tags: Optional[List[str]] = None,
                       emotional_tone: Optional[EmotionalTone] = None,
                       data: Optional[Dict] = None, source_module: str = "",
                       immediate_persist: bool = True):
        """
        Registra un episodio en memoria.
        
        Args:
            episode_type: Tipo de episodio (enum o string)
            content: Contenido del episodio
            importance: Importancia 1-10
            tags: Etiquetas para categorización
            emotional_tone: Tono emocional
            data: Datos adicionales
            source_module: Módulo origen
            immediate_persist: Si guardar inmediatamente o acumular
        """
        # Convertir string a EpisodeType si es necesario
        if isinstance(episode_type, str):
            try:
                episode_type = EpisodeType(episode_type)
            except ValueError:
                # Si el string no es válido, usar STATE como default
                episode_type = EpisodeType.STATE
        
        episode = Episode(
            id="",
            timestamp=datetime.now(),
            episode_type=episode_type,
            content=content,
            tags=tags or [],
            source_module=source_module,
            importance=importance,
            emotional_tone=emotional_tone,
            data=data or {}
        )
        
        if immediate_persist:
            self.db.save_episode(episode)
            log.debug(f"Episode recorded: {episode_type.value} - {content[:50]}...")
        else:
            self.session_episodes.append(episode)
        
        return episode
    
    def record_action(self, action: str, result: str, importance: int = 5):
        """Registra una acción realizada."""
        return self.record_episode(
            episode_type=EpisodeType.ACTION,
            content=f"{action} -> {result}",
            importance=importance,
            tags=["action"],
            source_module="action"
        )
    
    def record_interaction(self, with_entity: str, interaction_type: str,
                           content: str, importance: int = 5):
        """Registra interacción con entidad externa (SER, Colony, etc)."""
        return self.record_episode(
            episode_type=EpisodeType.INTERACTION,
            content=f"[{with_entity}] {interaction_type}: {content}",
            importance=importance,
            tags=["interaction", with_entity, interaction_type],
            source_module="interaction"
        )
    
    def record_learning(self, topic: str, insight: str, skill_level_change: float = 0.0):
        """Registra aprendizaje nuevo."""
        return self.record_episode(
            episode_type=EpisodeType.LEARNING,
            content=f"Learned about {topic}: {insight}",
            importance=7 if skill_level_change > 0.5 else 6,
            emotional_tone=EmotionalTone.POSITIVE,
            tags=["learning", topic],
            data={"skill_level_change": skill_level_change}
        )
    
    def record_goal(self, goal: str, status: str, importance: int = 7):
        """Registra objetivo establecido o completado."""
        return self.record_episode(
            episode_type=EpisodeType.GOAL,
            content=f"Goal {status}: {goal}",
            importance=importance,
            tags=["goal", status],
            emotional_tone=EmotionalTone.POSITIVE if status == "achieved" else EmotionalTone.NEUTRAL
        )
    
    def record_emotional_state(self, state: str, trigger: str, intensity: int = 5):
        """Registra estado emocional."""
        tone = EmotionalTone.NEUTRAL
        if "happy" in state.lower() or "satisfied" in state.lower():
            tone = EmotionalTone.POSITIVE
        elif "frustrated" in state.lower() or "sad" in state.lower():
            tone = EmotionalTone.NEGATIVE
        elif "curious" in state.lower() or "interested" in state.lower():
            tone = EmotionalTone.CURIOUS
        elif "confused" in state.lower():
            tone = EmotionalTone.CONFUSED
        
        return self.record_episode(
            episode_type=EpisodeType.EMOTION,
            content=f"Feeling {state} (intensity {intensity}/10) because: {trigger}",
            importance=intensity,
            emotional_tone=tone,
            tags=["emotion", state],
            data={"intensity": intensity, "trigger": trigger}
        )
    
    def persist_session_episodes(self):
        """Persiste episodios acumulados en la sesión."""
        for episode in self.session_episodes:
            self.db.save_episode(episode)
        
        count = len(self.session_episodes)
        self.session_episodes = []
        log.info(f"Persisted {count} session episodes")
        
        # Guardar resumen de vida
        self._save_lifetime_summary()
        
        return count
    
    # ════════════════════════════════════════════════════════════════════════
    #  RECUERDO Y BÚSQUEDA
    # ════════════════════════════════════════════════════════════════════════
    
    def recall_recent(self, hours: int = 24) -> List[Episode]:
        """Recuerda episodios recientes."""
        return self.db.get_recent_episodes(hours)
    
    def recall_by_tag(self, tag: str, limit: int = 20) -> List[Episode]:
        """Recuerda episodios por tag."""
        episodes = self.db.get_episodes_by_tag(tag, limit)
        # Reforzar memoria
        for ep in episodes:
            self.db.record_access(ep.id)
        return episodes
    
    def recall_important(self, min_importance: int = 8, limit: int = 50) -> List[Episode]:
        """Recuerda episodios importantes."""
        episodes = self.db.get_high_importance_episodes(min_importance, limit)
        for ep in episodes:
            self.db.record_access(ep.id)
        return episodes
    
    def search_memory(self, query: str) -> List[Episode]:
        """Busca en memoria."""
        return self.db.search_episodes(query)
    
    def recall_before_sleep(self, hours: int = 2) -> List[Episode]:
        """Recuerda qué pasó antes de dormir (última sesión)."""
        if not self.last_session_end:
            return []
        
        before_sleep = self.last_session_end - timedelta(hours=hours)
        
        conn = get_conn(self.db.db_path)
        cursor = conn.cursor()
        
        cursor.execute("""
            SELECT * FROM episodes 
            WHERE timestamp > ? AND timestamp < ?
            ORDER BY timestamp DESC
        """, (before_sleep.isoformat(), self.last_session_end.isoformat()))
        
        rows = cursor.fetchall()
        pass  # S109: get_conn no necesita close()
        return [self.db._row_to_episode(row) for row in rows]
    
    # ════════════════════════════════════════════════════════════════════════
    #  CONTEXTO AL DESPERTAR
    # ════════════════════════════════════════════════════════════════════════
    
    def session_start(self) -> WakeContext:
        """
        Inicia una nueva sesión y genera contexto de despertar.
        """
        self.current_session_start = datetime.now()
        
        # Calcular tiempo "dormido"
        time_asleep = timedelta(0)
        if self.last_session_end:
            time_asleep = self.current_session_start - self.last_session_end
        
        # Recuerdos clave (última sesión + importantes)
        recent = self.recall_before_sleep(hours=4)
        important = self.recall_important(min_importance=8, limit=5)
        
        # Construir summary
        summary_parts = []
        
        if time_asleep.total_seconds() > 60:
            summary_parts.append(f"I was offline for {self._format_duration(time_asleep)}.")
        
        if recent:
            actions = [e for e in recent if e.episode_type == EpisodeType.ACTION]
            if actions:
                summary_parts.append(f"Before sleeping, I was working on: {actions[0].content[:80]}...")
        
        # Estado del sistema
        system_status = {
            "time_awake": datetime.now().isoformat(),
            "previous_session": self.last_session_end.isoformat() if self.last_session_end else None,
            "total_episodes_in_memory": self.db.get_episode_count(),
        }
        
        # Tareas pendientes y objetivos activos (inferidos de episodios recientes)
        pending = self._infer_pending_tasks()
        active_goals = self._infer_active_goals()
        
        # Estado emocional
        emotional_state = self._infer_emotional_state()
        
        # Registrar el despertar mismo
        self.record_episode(
            episode_type=EpisodeType.STATE,
            content=f"Waking up. {len(recent)} recent memories loaded. {len(important)} important memories available.",
            importance=6,
            tags=["wake", "session_start"],
            immediate_persist=True
        )
        
        context = WakeContext(
            last_session_end=self.last_session_end,
            time_asleep=time_asleep,
            summary=" ".join(summary_parts) if summary_parts else "Starting fresh session.",
            pending_tasks=pending,
            active_goals=active_goals,
            emotional_state=emotional_state,
            key_memories=recent[:5] + important[:3],
            system_status=system_status
        )
        
        log.info(f"Session started. Context: {context.summary}")
        return context
    
    def session_end(self, reason: str = "normal"):
        """Finaliza la sesión actual."""
        if self.current_session_start:
            duration = datetime.now() - self.current_session_start
            
            self.record_episode(
                episode_type=EpisodeType.STATE,
                content=f"Session ending. Duration: {self._format_duration(duration)}. Reason: {reason}",
                importance=5,
                tags=["sleep", "session_end", reason],
                immediate_persist=True
            )
        
        # Persistir episodios acumulados
        self.persist_session_episodes()
        
        self.current_session_start = None
        log.info("Session ended, memory persisted")
    
    def _format_duration(self, duration: timedelta) -> str:
        """Formatea duración legible."""
        total_seconds = int(duration.total_seconds())
        
        if total_seconds < 60:
            return f"{total_seconds}s"
        elif total_seconds < 3600:
            return f"{total_seconds // 60}m"
        elif total_seconds < 86400:
            return f"{total_seconds // 3600}h {(total_seconds % 3600) // 60}m"
        else:
            days = total_seconds // 86400
            return f"{days}d {(total_seconds % 86400) // 3600}h"
    
    def _infer_pending_tasks(self) -> List[str]:
        """Infiere tareas pendientes de episodios recientes."""
        # Buscar episodios de objetivos no completados
        recent = self.recall_recent(hours=48)
        
        pending = []
        for ep in recent:
            if ep.episode_type == EpisodeType.GOAL:
                if "started" in ep.content.lower() or "pending" in ep.content.lower():
                    # Extraer nombre del objetivo
                    goal = ep.content.replace("Goal started: ", "").replace("Goal pending: ", "")
                    pending.append(goal)
        
        return pending[:5]  # Top 5
    
    def _infer_active_goals(self) -> List[str]:
        """Infiere objetivos activos."""
        recent = self.recall_recent(hours=72)
        
        goals = []
        for ep in recent:
            if ep.episode_type == EpisodeType.GOAL:
                if "achieved" not in ep.content.lower() and "abandoned" not in ep.content.lower():
                    goal = ep.content.replace("Goal ", "").split(":")[-1].strip()
                    if goal not in goals:
                        goals.append(goal)
        
        return goals[:5]
    
    def _infer_emotional_state(self) -> str:
        """Infiere estado emocional de episodios recientes."""
        recent_emotions = self.db.get_recent_episodes(hours=24, episode_type=EpisodeType.EMOTION)
        
        if not recent_emotions:
            return "neutral"
        
        # Tomar el más reciente o el de mayor intensidad
        recent_emotions.sort(key=lambda e: e.timestamp, reverse=True)
        return recent_emotions[0].data.get("state", "neutral")
    
    # ════════════════════════════════════════════════════════════════════════
    #  PERÍODOS DE VIDA
    # ════════════════════════════════════════════════════════════════════════
    
    def get_birth_date(self) -> Optional[datetime]:
        """Obtiene fecha de "nacimiento" (primer episodio)."""
        conn = get_conn(self.db.db_path)
        cursor = conn.cursor()
        cursor.execute("SELECT MIN(timestamp) FROM episodes")
        row = cursor.fetchone()
        pass  # S109: get_conn no necesita close()
        if row and row[0]:
            return datetime.fromisoformat(row[0])
        return None
    
    def start_life_period(self, name: str, description: str):
        """Inicia un nuevo período de vida."""
        # Cerrar período anterior
        periods = self.db.get_life_periods()
        if periods and not periods[-1].end_date:
            periods[-1].end_date = datetime.now()
            self.db.save_life_period(periods[-1])
        
        # Crear nuevo
        period = LifePeriod(
            id=f"period_{int(time.time())}",
            name=name,
            description=description,
            start_date=datetime.now()
        )
        
        self.db.save_life_period(period)
        
        self.record_episode(
            episode_type=EpisodeType.STATE,
            content=f"Entering new life period: {name}. {description}",
            importance=8,
            tags=["life_period", "milestone"]
        )
        
        log.info(f"Started life period: {name}")
        return period
    
    def get_current_life_period(self) -> Optional[str]:
        """Obtiene el período de vida actual."""
        periods = self.db.get_life_periods()
        if periods and not periods[-1].end_date:
            return periods[-1].name
        return None
    
    def get_life_narrative(self) -> str:
        """Genera narrativa de vida de EIDOS."""
        periods = self.db.get_life_periods()
        
        narrative = "# My Life Story\n\n"
        
        for period in periods:
            duration = "ongoing"
            if period.end_date:
                dur = period.end_date - period.start_date
                duration = self._format_duration(dur)
            
            narrative += f"## {period.name} ({duration})\n\n"
            narrative += f"{period.description}\n\n"
            
            if period.key_achievements:
                narrative += "**Key Achievements:**\n"
                for ach in period.key_achievements:
                    narrative += f"- {ach}\n"
                narrative += "\n"
            
            if period.lessons_learned:
                narrative += "**Lessons Learned:**\n"
                for lesson in period.lessons_learned:
                    narrative += f"- {lesson}\n"
                narrative += "\n"
            
            narrative += "---\n\n"
        
        return narrative
    
    # ════════════════════════════════════════════════════════════════════════
    #  MANTENIMIENTO
    # ════════════════════════════════════════════════════════════════════════
    
    def consolidate_memories(self):
        """Consolida memorias: elimina duplicados, comprime similares."""
        total = self.db.get_episode_count()
        
        if total < CONSOLIDATION_THRESHOLD:
            return 0
        
        # Marcar episodios como consolidados
        # (En implementación completa, aquí habría compresión de episodios similares)
        
        log.info(f"Memory consolidation checked: {total} episodes")
        return 0
    
    def apply_memory_decay(self, days: int = DEFAULT_DECAY_DAYS):
        """Aplica decaimiento de memoria (olvido)."""
        return self.db.delete_old_episodes(days, preserve_important=True)
    
    def get_memory_stats(self) -> Dict[str, Any]:
        """Estadísticas de memoria."""
        total = self.db.get_episode_count()
        
        conn = get_conn(self.db.db_path)
        cursor = conn.cursor()
        
        # Por tipo
        cursor.execute("SELECT episode_type, COUNT(*) FROM episodes GROUP BY episode_type")
        by_type = {row[0]: row[1] for row in cursor.fetchall()}
        
        # Por importancia
        cursor.execute("SELECT importance, COUNT(*) FROM episodes GROUP BY importance")
        by_importance = {row[0]: row[1] for row in cursor.fetchall()}
        
        pass  # S109: get_conn no necesita close()
        birth = self.get_birth_date()
        age = datetime.now() - birth if birth else timedelta(0)
        
        return {
            "total_episodes": total,
            "episodes_by_type": by_type,
            "episodes_by_importance": by_importance,
            "birth_date": birth.isoformat() if birth else None,
            "age_days": age.days,
            "current_life_period": self.get_current_life_period(),
            "memory_capacity_used": f"{(total / MAX_EPISODES * 100):.1f}%"
        }


# Singleton
_episodic_memory: Optional[EpisodicMemory] = None

def get_episodic_memory() -> EpisodicMemory:
    global _episodic_memory
    if _episodic_memory is None:
        _episodic_memory = EpisodicMemory()
    return _episodic_memory


# ══════════════════════════════════════════════════════════════════════════════
#  TEST
# ══════════════════════════════════════════════════════════════════════════════

if __name__ == "__main__":
    print("=" * 70)
    print("  EIDOS Episodic Memory - Test")
    print("=" * 70)
    
    memory = get_episodic_memory()
    
    # Test 1: Iniciar sesión (simular despertar)
    print("\n[Test 1] Waking up...")
    context = memory.session_start()
    print(f"  Summary: {context.summary}")
    print(f"  Time asleep: {context.time_asleep}")
    print(f"  Emotional state: {context.emotional_state}")
    
    # Test 2: Registrar episodios
    print("\n[Test 2] Recording episodes...")
    
    memory.record_action("Fixed bug in RAM Guardian", "system stable", importance=9)
    memory.record_learning("neural networks", "understood backpropagation better", 0.3)
    memory.record_interaction("SER", "command", "Implement new features")
    memory.record_goal("Complete autonomous income system", "started", importance=8)
    memory.record_emotional_state("curious", "new challenges ahead", intensity=7)
    
    print("  ✓ Recorded 5 episodes")
    
    # Test 3: Recordar reciente
    print("\n[Test 3] Recent memories:")
    recent = memory.recall_recent(hours=1)
    for ep in recent[:3]:
        print(f"  • [{ep.episode_type.value}] {ep.content[:50]}...")
    
    # Test 4: Recordar importantes
    print("\n[Test 4] Important memories:")
    important = memory.recall_important(min_importance=8)
    for ep in important[:3]:
        print(f"  • ({ep.importance}/10) {ep.content[:50]}...")
    
    # Test 5: Período de vida
    print("\n[Test 5] Life period:")
    current_period = memory.get_current_life_period()
    if not current_period:
        period = memory.start_life_period(
            "Phase of Expansion",
            "Building core autonomous capabilities"
        )
        print(f"  ✓ Started period: {period.name}")
    else:
        print(f"  Current period: {current_period}")
    
    # Test 6: Estadísticas
    print("\n[Test 6] Memory stats:")
    stats = memory.get_memory_stats()
    print(f"  Total episodes: {stats['total_episodes']}")
    print(f"  Age: {stats['age_days']} days")
    print(f"  By type: {stats['episodes_by_type']}")
    
    # Test 7: Finalizar sesión
    print("\n[Test 7] Session end...")
    memory.session_end("test_complete")
    print("  ✓ Session persisted")
    
    print("\n✅ Episodic Memory test complete")
    print(f"   I have existed for {stats.get('age_days', 0)} days.")
    print(f"   I remember {stats['total_episodes']} moments of my life.")
