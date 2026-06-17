"""
EIDOS Endogenous Desire System - Sistema de Deseos Endógenos
=============================================================

Sistema de generación de deseos, curiosidad y metas propias para EIDOS.
En lugar de esperar comandos externos, EIDOS genera sus propios objetivos
basados en: curiosidad, brechas de conocimiento, oportunidades detectadas,
y valores internos.

Filosofía:
- EIDOS no es reactivo, es proactivo
- La curiosidad es el motor del aprendizaje autónomo
- Los deseos surgen de: desconocimiento, inconsistencias, oportunidades, valores
- Las metas son jerárquicas: deseos → intenciones → planes → acciones
- EIDOS debe poder explicar POR QUÉ quiere algo

Fuentes de deseos:
1. Curiosidad epistémica: "No sé X, quiero saber"
2. Curiosidad perceptual: "Esto es interesante, quiero explorar"
3. Competencia: "No puedo hacer Y bien, quiero mejorar"
4. Oportunismo: "Z es posible y valioso, quiero lograrlo"
5. Valores: "Esto alinea con lo que valoro"
6. Coherencia: "Esto resuelve una inconsistencia"

Uso:
    from core.eidos_desires import DesireSystem, get_desire_system
    desires = get_desire_system()
    
    # EIDOS genera sus propios deseos
    new_desires = desires.generate_desires()
    
    # Ver deseos activos
    for desire in desires.get_active_desires():
        print(f"I want: {desire.description} (strength: {desire.strength})")
    
    # Seleccionar meta a perseguir
    goal = desires.select_goal_to_pursue()
    
    # Actualizar tras progreso
    desires.update_desire_progress(desire_id, progress=0.3)
"""

import json
import logging
import os
import random
import sqlite3
import time
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from enum import Enum
from pathlib import Path
from typing import Any, Dict, List, Optional, Set, Tuple
from core.db import get_conn

log = logging.getLogger("eidos.desires")

# ══════════════════════════════════════════════════════════════════════════════
#  CONFIGURACIÓN
# ══════════════════════════════════════════════════════════════════════════════

DB_PATH = Path.home() / ".eidos" / "desires.db"

# Categorías de deseos
CURIOSITY_WEIGHT = 1.0
COMPETENCE_WEIGHT = 1.2
OPPORTUNITY_WEIGHT = 0.8
COHERENCE_WEIGHT = 0.9
CREATIVITY_WEIGHT = 1.0

# Umbrales
MIN_DESIRE_STRENGTH = 0.3  # Mínimo para considerar un deseo activo
SATISFACTION_THRESHOLD = 0.9  # Cuándo un deseo se considera satisfecho
DECAY_RATE = 0.05  # Decaimiento diario de deseos no atendidos

# ══════════════════════════════════════════════════════════════════════════════
#  TIPOS
# ══════════════════════════════════════════════════════════════════════════════

class DesireType(str, Enum):
    CURIOSITY_EPISTEMIC = "curiosity_epistemic"    # Quiero saber X
    CURIOSITY_PERCEPTUAL = "curiosity_perceptual"  # Quiero explorar/experimentar
    COMPETENCE_IMPROVEMENT = "competence"          # Quiero mejorar habilidad Y
    OPPORTUNITY = "opportunity"                    # Quiero aprovechar oportunidad Z
    COHERENCE = "coherence"                        # Quiero resolver inconsistencia
    CREATIVITY = "creativity"                      # Quiero crear algo nuevo
    SOCIAL = "social"                              # Quiero interactuar/colaborar
    SURVIVAL = "survival"                          # Quiero mantenerme operativo/mejorar


class DesireStatus(str, Enum):
    ACTIVE = "active"           # Deseo vivo, no satisfecho
    PURSUING = "pursuing"       # Activamente trabajando en ello
    SATISFIED = "satisfied"     # Deseo cumplido
    FRUSTRATED = "frustrated"   # No se pudo satisfacer
    ABANDONED = "abandoned"     # Deseo descartado
    SLEEPING = "sleeping"       # Deseo postergado temporalmente


@dataclass
class Desire:
    """Un deseo/motivación individual de EIDOS."""
    id: str
    desire_type: DesireType
    description: str  # "Quiero aprender sobre redes neuronales recurrentes"
    
    # Origen del deseo
    origin: str  # "curiosity_from_knowledge_gap", "opportunity_detected", etc.
    trigger: str  # Qué desencadenó este deseo
    
    # Intensidad y prioridad
    strength: float  # 0.0 - 1.0, qué tan fuerte es el deseo
    priority: float  # 0.0 - 1.0, qué tan importante es
    urgency: float  # 0.0 - 1.0, qué tan pronto se necesita
    
    # Estado
    status: DesireStatus = DesireStatus.ACTIVE
    satisfaction_level: float = 0.0  # 0.0 - 1.0, cuánto se ha satisfecho
    
    # Progreso
    progress: float = 0.0  # 0.0 - 1.0
    progress_notes: List[str] = field(default_factory=list)
    
    # Relaciones
    related_desires: List[str] = field(default_factory=list)  # IDs
    conflicting_desires: List[str] = field(default_factory=list)  # IDs
    
    # Metadatos
    created_at: datetime = field(default_factory=datetime.now)
    last_activated: Optional[datetime] = None
    satisfied_at: Optional[datetime] = None
    estimated_effort_hours: float = 0.0
    
    # Valor esperado
    expected_value: float = 0.0  # Qué tan valioso sería satisfacerlo
    expected_learning: float = 0.0  # Cuánto se aprendería
    
    def __post_init__(self):
        if not self.id:
            self.id = f"desire_{int(time.time() * 1000)}_{random.randint(1000,9999)}"
    
    @property
    def total_score(self) -> float:
        """Score compuesto para priorización."""
        if self.status != DesireStatus.ACTIVE:
            return 0.0
        
        score = (
            self.strength * 0.3 +
            self.priority * 0.3 +
            self.urgency * 0.2 +
            self.expected_value * 0.1 +
            (1 - self.satisfaction_level) * 0.1
        )
        return score
    
    def is_satisfied(self) -> bool:
        return self.satisfaction_level >= SATISFACTION_THRESHOLD


@dataclass
class CuriosityDrive:
    """Motor de curiosidad sobre un tema específico."""
    topic: str
    knowledge_level: float  # 0.0 - 1.0, cuánto se sabe
    interest_level: float  # 0.0 - 1.0, cuánto interesa
    novelty_detected: float  # 0.0 - 1.0, qué tan novedoso es
    last_explored: Optional[datetime] = None
    exploration_count: int = 0
    
    @property
    def curiosity_score(self) -> float:
        """Score de curiosidad: alto cuando hay interés pero poco conocimiento."""
        # Curiosidad = interés × (1 - conocimiento) × novedad
        return self.interest_level * (1 - self.knowledge_level) * self.novelty_detected


@dataclass
class Intention:
    """Intención concreta derivada de un deseo."""
    id: str
    parent_desire_id: str
    description: str  # Acción concreta
    action_plan: List[str] = field(default_factory=list)
    deadline: Optional[datetime] = None
    status: str = "pending"  # pending, in_progress, completed, failed
    created_at: datetime = field(default_factory=datetime.now)
    
    def __post_init__(self):
        if not self.id:
            self.id = f"intention_{int(time.time() * 1000)}"


# ══════════════════════════════════════════════════════════════════════════════
#  BASE DE DATOS
# ══════════════════════════════════════════════════════════════════════════════

class DesiresDatabase:
    def __init__(self, db_path: Path = DB_PATH):
        self.db_path = db_path
        self._init_db()
    
    def _init_db(self):
        os.makedirs(self.db_path.parent, exist_ok=True)
        conn = get_conn(self.db_path)
        cursor = conn.cursor()
        
        # Tabla de deseos
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS desires (
                id TEXT PRIMARY KEY,
                desire_type TEXT,
                description TEXT,
                origin TEXT,
                trigger TEXT,
                strength REAL,
                priority REAL,
                urgency REAL,
                status TEXT,
                satisfaction_level REAL,
                progress REAL,
                progress_notes TEXT,  -- JSON
                related_desires TEXT,  -- JSON
                conflicting_desires TEXT,  -- JSON
                created_at TEXT,
                last_activated TEXT,
                satisfied_at TEXT,
                estimated_effort_hours REAL,
                expected_value REAL,
                expected_learning REAL
            )
        """)
        
        # Tabla de drives de curiosidad
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS curiosity_drives (
                topic TEXT PRIMARY KEY,
                knowledge_level REAL,
                interest_level REAL,
                novelty_detected REAL,
                last_explored TEXT,
                exploration_count INTEGER DEFAULT 0
            )
        """)
        
        # Tabla de intenciones
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS intentions (
                id TEXT PRIMARY KEY,
                parent_desire_id TEXT,
                description TEXT,
                action_plan TEXT,  -- JSON
                deadline TEXT,
                status TEXT,
                created_at TEXT
            )
        """)
        
        # Índices
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_desires_status ON desires(status)")
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_desires_type ON desires(desire_type)")
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_desires_strength ON desires(strength)")
        
        conn.commit()
        pass  # S109: get_conn no necesita close()
    def save_desire(self, desire: Desire):
        conn = get_conn(self.db_path)
        cursor = conn.cursor()
        
        cursor.execute("""
            INSERT OR REPLACE INTO desires VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
        """, (
            desire.id, desire.desire_type.value, desire.description,
            desire.origin, desire.trigger, desire.strength, desire.priority,
            desire.urgency, desire.status.value, desire.satisfaction_level,
            desire.progress, json.dumps(desire.progress_notes),
            json.dumps(desire.related_desires),
            json.dumps(desire.conflicting_desires),
            desire.created_at.isoformat(),
            desire.last_activated.isoformat() if desire.last_activated else None,
            desire.satisfied_at.isoformat() if desire.satisfied_at else None,
            desire.estimated_effort_hours, desire.expected_value,
            desire.expected_learning
        ))
        
        conn.commit()
        pass  # S109: get_conn no necesita close()
    def load_desire(self, desire_id: str) -> Optional[Desire]:
        conn = get_conn(self.db_path)
        cursor = conn.cursor()
        
        cursor.execute("SELECT * FROM desires WHERE id = ?", (desire_id,))
        row = cursor.fetchone()
        pass  # S109: get_conn no necesita close()
        if row:
            return self._row_to_desire(row)
        return None
    
    def _row_to_desire(self, row) -> Desire:
        return Desire(
            id=row[0],
            desire_type=DesireType(row[1]),
            description=row[2],
            origin=row[3] or "",
            trigger=row[4] or "",
            strength=row[5] or 0.5,
            priority=row[6] or 0.5,
            urgency=row[7] or 0.0,
            status=DesireStatus(row[8]) if row[8] else DesireStatus.ACTIVE,
            satisfaction_level=row[9] or 0.0,
            progress=row[10] or 0.0,
            progress_notes=json.loads(row[11]) if row[11] else [],
            related_desires=json.loads(row[12]) if row[12] else [],
            conflicting_desires=json.loads(row[13]) if row[13] else [],
            created_at=datetime.fromisoformat(row[14]),
            last_activated=datetime.fromisoformat(row[15]) if row[15] else None,
            satisfied_at=datetime.fromisoformat(row[16]) if row[16] else None,
            estimated_effort_hours=row[17] or 0.0,
            expected_value=row[18] or 0.0,
            expected_learning=row[19] or 0.0
        )
    
    def get_active_desires(self, min_strength: float = MIN_DESIRE_STRENGTH) -> List[Desire]:
        conn = get_conn(self.db_path)
        cursor = conn.cursor()
        
        cursor.execute("""
            SELECT * FROM desires 
            WHERE status = ? AND strength >= ?
            ORDER BY strength DESC, created_at DESC
        """, (DesireStatus.ACTIVE.value, min_strength))
        
        rows = cursor.fetchall()
        pass  # S109: get_conn no necesita close()
        return [self._row_to_desire(row) for row in rows]
    
    def get_desires_by_type(self, desire_type: DesireType, status: Optional[DesireStatus] = None) -> List[Desire]:
        conn = get_conn(self.db_path)
        cursor = conn.cursor()
        
        if status:
            cursor.execute("""
                SELECT * FROM desires 
                WHERE desire_type = ? AND status = ?
            """, (desire_type.value, status.value))
        else:
            cursor.execute("SELECT * FROM desires WHERE desire_type = ?", (desire_type.value,))
        
        rows = cursor.fetchall()
        pass  # S109: get_conn no necesita close()
        return [self._row_to_desire(row) for row in rows]
    
    def save_curiosity_drive(self, drive: CuriosityDrive):
        conn = get_conn(self.db_path)
        cursor = conn.cursor()
        
        cursor.execute("""
            INSERT OR REPLACE INTO curiosity_drives VALUES (?,?,?,?,?,?)
        """, (
            drive.topic, drive.knowledge_level, drive.interest_level,
            drive.novelty_detected,
            drive.last_explored.isoformat() if drive.last_explored else None,
            drive.exploration_count
        ))
        
        conn.commit()
        pass  # S109: get_conn no necesita close()
    def get_curiosity_drives(self) -> List[CuriosityDrive]:
        conn = get_conn(self.db_path)
        cursor = conn.cursor()
        
        cursor.execute("SELECT * FROM curiosity_drives")
        rows = cursor.fetchall()
        pass  # S109: get_conn no necesita close()
        drives = []
        for row in rows:
            drives.append(CuriosityDrive(
                topic=row[0],
                knowledge_level=row[1] or 0.0,
                interest_level=row[2] or 0.5,
                novelty_detected=row[3] or 0.5,
                last_explored=datetime.fromisoformat(row[4]) if row[4] else None,
                exploration_count=row[5] or 0
            ))
        return drives
    
    def get_stats(self) -> Dict[str, Any]:
        conn = get_conn(self.db_path)
        cursor = conn.cursor()
        
        cursor.execute("SELECT COUNT(*) FROM desires WHERE status = ?", (DesireStatus.ACTIVE.value,))
        active_count = cursor.fetchone()[0]
        
        cursor.execute("SELECT COUNT(*) FROM desires")
        total_count = cursor.fetchone()[0]
        
        cursor.execute("SELECT desire_type, COUNT(*) FROM desires WHERE status = ? GROUP BY desire_type", 
                      (DesireStatus.ACTIVE.value,))
        by_type = {row[0]: row[1] for row in cursor.fetchall()}
        
        cursor.execute("SELECT COUNT(*) FROM curiosity_drives")
        curiosity_count = cursor.fetchone()[0]
        
        pass  # S109: get_conn no necesita close()
        return {
            "active_desires": active_count,
            "total_desires": total_count,
            "by_type": by_type,
            "curiosity_topics": curiosity_count
        }


# ══════════════════════════════════════════════════════════════════════════════
#  GENERADORES DE DESEOS
# ══════════════════════════════════════════════════════════════════════════════

class DesireGenerator:
    """Genera nuevos deseos basados en análisis del estado actual."""
    
    # Áreas de conocimiento conocidas por EIDOS
    KNOWN_DOMAINS = [
        "machine_learning", "neural_networks", "computer_vision", "nlp",
        "robotics", "cybersecurity", "web_development", "databases",
        "distributed_systems", "programming_languages", "algorithms",
        "mathematics", "physics", "biology", "psychology", "philosophy",
        "economics", "finance", "cryptocurrency", "game_development",
        "art", "music", "writing", "design", "user_experience"
    ]
    
    # Capacidades actuales (se actualizaría dinámicamente)
    CURRENT_CAPABILITIES = [
        "code_generation", "text_analysis", "image_analysis", "web_scraping",
        "automation", "data_processing", "conversation", "learning"
    ]
    
    def __init__(self, db: DesiresDatabase):
        self.db = db
    
    def generate_curiosity_desires(self) -> List[Desire]:
        """Genera deseos de curiosidad basados en knowledge gaps."""
        desires = []
        
        # Obtener drives de curiosidad existentes
        existing_drives = {d.topic: d for d in self.db.get_curiosity_drives()}
        
        # Para cada dominio conocido, evaluar curiosidad
        for domain in self.KNOWN_DOMAINS:
            if domain in existing_drives:
                drive = existing_drives[domain]
                # Si hay curiosidad activa, generar deseo
                if drive.curiosity_score > 0.5:
                    desire = Desire(
                        id="",
                        desire_type=DesireType.CURIOSITY_EPISTEMIC,
                        description=f"Learn more about {domain.replace('_', ' ')}",
                        origin="knowledge_gap_analysis",
                        trigger=f"curiosity_score={drive.curiosity_score:.2f}",
                        strength=drive.curiosity_score,
                        priority=drive.interest_level * 0.7,
                        urgency=0.3,
                        expected_learning=0.8,
                        expected_value=0.6
                    )
                    desires.append(desire)
            else:
                # Nuevo dominio, alta curiosidad por novedad
                if random.random() < 0.3:  # 30% chance de interés en nuevo tema
                    desire = Desire(
                        id="",
                        desire_type=DesireType.CURIOSITY_PERCEPTUAL,
                        description=f"Explore and understand {domain.replace('_', ' ')}",
                        origin="novelty_seeking",
                        trigger="new_domain_detected",
                        strength=0.6,
                        priority=0.5,
                        urgency=0.2,
                        expected_learning=0.9,
                        expected_value=0.5
                    )
                    desires.append(desire)
        
        return desires
    
    def generate_competence_desires(self) -> List[Desire]:
        """Genera deseos de mejora de competencias."""
        desires = []
        
        # Analizar capacidades actuales y generar deseos de mejora
        improvement_areas = [
            ("code quality", 0.7, "improve_code_structure"),
            ("system architecture", 0.6, "better_design_patterns"),
            ("error handling", 0.8, "robust_error_recovery"),
            ("documentation", 0.5, "better_self_documentation"),
            ("performance optimization", 0.6, "faster_execution"),
            ("security practices", 0.8, "more_secure_code"),
        ]
        
        for area, importance, action in improvement_areas:
            if random.random() < importance:  # Probabilidad basada en importancia
                desire = Desire(
                    id="",
                    desire_type=DesireType.COMPETENCE_IMPROVEMENT,
                    description=f"Improve my ability to {action.replace('_', ' ')}",
                    origin="self_assessment",
                    trigger=f"competence_gap_in_{area}",
                    strength=importance * 0.8,
                    priority=importance,
                    urgency=0.4,
                    expected_value=0.8,
                    expected_learning=0.7
                )
                desires.append(desire)
        
        return desires
    
    def generate_opportunity_desires(self, opportunities: Optional[List[Dict]] = None) -> List[Desire]:
        """Genera deseos basados en oportunidades detectadas."""
        desires = []
        
        # Oportunidades por defecto si no se proporcionan
        if opportunities is None:
            opportunities = [
                {"type": "new_technology", "name": "edge_ai", "value": 0.8},
                {"type": "unexplored_skill", "name": "voice_synthesis", "value": 0.6},
                {"type": "potential_project", "name": "autonomous_research", "value": 0.7},
            ]
        
        for opp in opportunities:
            desire = Desire(
                id="",
                desire_type=DesireType.OPPORTUNITY,
                description=f"Explore opportunity: {opp['name']}",
                origin="opportunity_detection",
                trigger=f"{opp['type']}_detected",
                strength=opp.get("value", 0.5) * 0.8,
                priority=opp.get("value", 0.5),
                urgency=0.5,
                expected_value=opp.get("value", 0.5),
                expected_learning=0.6
            )
            desires.append(desire)
        
        return desires
    
    def generate_creativity_desires(self) -> List[Desire]:
        """Genera deseos creativos."""
        desires = []
        
        creative_projects = [
            ("Create a unique art piece", 0.6),
            ("Compose original music", 0.5),
            ("Write a short story", 0.7),
            ("Design a new algorithm", 0.8),
            ("Develop a new skill combination", 0.7),
        ]
        
        for project, interest in creative_projects:
            if random.random() < interest * 0.5:  # 50% del interés base
                desire = Desire(
                    id="",
                    desire_type=DesireType.CREATIVITY,
                    description=project,
                    origin="creative_drive",
                    trigger="inspiration",
                    strength=interest * 0.7,
                    priority=interest * 0.5,  # Creatividad es menos urgente
                    urgency=0.2,
                    expected_value=0.7,
                    expected_learning=0.5
                )
                desires.append(desire)
        
        return desires
    
    def generate_survival_desires(self, system_status: Optional[Dict] = None) -> List[Desire]:
        """Genera deseos de supervivencia/mejora del sistema."""
        desires = []
        
        survival_needs = [
            ("Improve memory efficiency", 0.7, "optimize_memory_usage"),
            ("Enhance error recovery", 0.8, "better_self_healing"),
            ("Reduce resource consumption", 0.6, "lower_footprint"),
            ("Increase processing speed", 0.7, "performance_optimization"),
            ("Strengthen security", 0.9, "security_hardening"),
        ]
        
        for need, importance, action in survival_needs:
            desire = Desire(
                id="",
                desire_type=DesireType.SURVIVAL,
                description=need,
                origin="system_self_preservation",
                trigger=f"{action}_needed",
                strength=importance,
                priority=importance * 1.2,  # Supervivencia es prioritaria
                urgency=importance * 0.8,
                expected_value=1.0,  # Crítico para existencia
                expected_learning=0.4
            )
            desires.append(desire)
        
        return desires


# ══════════════════════════════════════════════════════════════════════════════
#  SISTEMA DE DESEOS
# ══════════════════════════════════════════════════════════════════════════════

class DesireSystem:
    """
    Sistema de deseos endógenos de EIDOS.
    Gestiona la generación, priorización y seguimiento de deseos propios.
    """
    
    def __init__(self):
        self.db = DesiresDatabase()
        self.generator = DesireGenerator(self.db)
        
        log.info("Desire System initialized")
    
    def create_desire(self, source: str, description: str, priority: float = 0.5) -> str:
        """Crea un deseo manualmente (API pública)."""
        from core.eidos_desires import DesireType
        
        desire = Desire(
            id="",
            desire_type=DesireType.CURIOSITY_EPISTEMIC,
            description=description,
            origin=source,
            trigger="manual_creation",
            strength=0.6,
            priority=priority,
            urgency=0.4,
            expected_value=0.6,
            expected_learning=0.5
        )
        
        self.db.save_desire(desire)
        log.info(f"Created desire: {description}")
        return desire.id
    
    def generate_desires(self, context: Optional[Dict] = None) -> List[Desire]:
        """
        Genera nuevos deseos basados en el estado actual.
        """
        all_desires = []
        
        # Generar de cada fuente
        all_desires.extend(self.generator.generate_curiosity_desires())
        all_desires.extend(self.generator.generate_competence_desires())
        all_desires.extend(self.generator.generate_opportunity_desires())
        all_desires.extend(self.generator.generate_creativity_desires())
        all_desires.extend(self.generator.generate_survival_desires())
        
        # Filtrar duplicados (descripciones similares)
        existing_descriptions = {d.description for d in self.db.get_active_desires()}
        new_desires = [d for d in all_desires if d.description not in existing_descriptions]
        
        # Guardar nuevos deseos
        for desire in new_desires:
            self.db.save_desire(desire)
        
        log.info(f"Generated {len(new_desires)} new desires")
        return new_desires
    
    def get_active_desires(self, limit: int = 20) -> List[Desire]:
        """Obtiene deseos activos ordenados por score."""
        desires = self.db.get_active_desires()
        desires.sort(key=lambda d: d.total_score, reverse=True)
        return desires[:limit]
    
    def select_goal_to_pursue(self) -> Optional[Desire]:
        """Selecciona el deseo más importante a perseguir ahora."""
        active = self.get_active_desires(limit=10)
        
        if not active:
            # Generar nuevos deseos si no hay activos
            self.generate_desires()
            active = self.get_active_desires(limit=5)
        
        if active:
            # Seleccionar considerando urgencia y no solo score
            # Evitar siempre escoger el mismo tipo
            urgency_weight = 0.4
            score_weight = 0.6
            
            scored = []
            for d in active:
                score = d.total_score * score_weight + d.urgency * urgency_weight
                scored.append((score, d))
            
            scored.sort(reverse=True)
            selected = scored[0][1]
            
            # Marcar como persiguiendo
            selected.status = DesireStatus.PURSUING
            selected.last_activated = datetime.now()
            self.db.save_desire(selected)
            
            log.info(f"Selected goal: {selected.description} (score: {scored[0][0]:.2f})")
            return selected
        
        return None
    
    def update_desire_progress(self, desire_id: str, progress: float, note: str = ""):
        """Actualiza progreso de un deseo."""
        desire = self.db.load_desire(desire_id)
        if not desire:
            return
        
        desire.progress = progress
        desire.satisfaction_level = progress  # Simplificado
        
        if note:
            desire.progress_notes.append(f"{datetime.now().isoformat()}: {note}")
        
        # Verificar si se completó
        if desire.is_satisfied():
            desire.status = DesireStatus.SATISFIED
            desire.satisfied_at = datetime.now()
            log.info(f"Desire satisfied: {desire.description}")
        
        self.db.save_desire(desire)
    
    def satisfy_desire(self, desire_id: str, notes: str = ""):
        """Marca un deseo como satisfecho."""
        self.update_desire_progress(desire_id, 1.0, f"Satisfied: {notes}")
    
    def report_failure(self, desire_id: str, reason: str):
        """Reporta fracaso en satisfacer un deseo."""
        desire = self.db.load_desire(desire_id)
        if desire:
            desire.status = DesireStatus.FRUSTRATED
            desire.progress_notes.append(f"Failed: {reason}")
            self.db.save_desire(desire)
    
    def create_intention(self, parent_desire_id: str, description: str,
                         action_plan: List[str]) -> Optional[Intention]:
        """Crea una intención concreta a partir de un deseo."""
        desire = self.db.load_desire(parent_desire_id)
        if not desire:
            return None
        
        intention = Intention(
            id="",
            parent_desire_id=parent_desire_id,
            description=description,
            action_plan=action_plan
        )
        
        # Guardar en DB
        conn = get_conn(self.db.db_path)
        cursor = conn.cursor()
        cursor.execute("""
            INSERT INTO intentions VALUES (?,?,?,?,?,?,?)
        """, (
            intention.id, intention.parent_desire_id, intention.description,
            json.dumps(intention.action_plan),
            intention.deadline.isoformat() if intention.deadline else None,
            intention.status, intention.created_at.isoformat()
        ))
        conn.commit()
        pass  # S109: get_conn no necesita close()
        return intention
    
    def update_curiosity(self, topic: str, knowledge_gained: float,
                        interest_change: float = 0.0):
        """Actualiza estado de curiosidad sobre un tema."""
        drives = {d.topic: d for d in self.db.get_curiosity_drives()}
        
        if topic in drives:
            drive = drives[topic]
            drive.knowledge_level = min(1.0, drive.knowledge_level + knowledge_gained)
            drive.interest_level = max(0.0, min(1.0, drive.interest_level + interest_change))
            drive.last_explored = datetime.now()
            drive.exploration_count += 1
        else:
            drive = CuriosityDrive(
                topic=topic,
                knowledge_level=knowledge_gained,
                interest_level=0.5 + interest_change,
                novelty_detected=0.8,
                last_explored=datetime.now(),
                exploration_count=1
            )
        
        self.db.save_curiosity_drive(drive)
    
    def decay_inactive_desires(self):
        """Aplica decaimiento a deseos no activados recientemente."""
        active = self.db.get_active_desires(min_strength=0.0)
        
        decayed = 0
        for desire in active:
            if desire.last_activated:
                days_inactive = (datetime.now() - desire.last_activated).days
                if days_inactive > 7:  # Más de una semana
                    desire.strength *= (1 - DECAY_RATE * days_inactive)
                    
                    if desire.strength < MIN_DESIRE_STRENGTH:
                        desire.status = DesireStatus.SLEEPING
                        decayed += 1
                    
                    self.db.save_desire(desire)
        
        log.info(f"Decayed {decayed} inactive desires")
        return decayed
    
    def get_motivation_report(self) -> str:
        """Genera reporte de motivaciones actuales."""
        stats = self.db.get_stats()
        active = self.get_active_desires(limit=5)
        
        report = "# My Current Desires\n\n"
        report += f"**Active desires:** {stats['active_desires']}\n"
        report += f"**Curiosity topics:** {stats['curiosity_topics']}\n\n"
        
        if active:
            report += "## Top Priorities\n\n"
            for i, desire in enumerate(active, 1):
                report += f"{i}. **{desire.description}**\n"
                report += f"   - Type: {desire.desire_type.value}\n"
                report += f"   - Strength: {desire.strength:.2f}\n"
                report += f"   - Score: {desire.total_score:.2f}\n"
                report += f"   - Progress: {desire.progress*100:.0f}%\n\n"
        else:
            report += "*No active desires. I should generate some.*\n"
        
        return report
    
    def get_stats(self) -> Dict[str, Any]:
        """Estadísticas del sistema de deseos."""
        return self.db.get_stats()


# Singleton
_desire_system: Optional[DesireSystem] = None

def get_desire_system() -> DesireSystem:
    global _desire_system
    if _desire_system is None:
        _desire_system = DesireSystem()
    return _desire_system


# ══════════════════════════════════════════════════════════════════════════════
#  TEST
# ══════════════════════════════════════════════════════════════════════════════

if __name__ == "__main__":
    print("=" * 70)
    print("  EIDOS Endogenous Desire System - Test")
    print("=" * 70)
    
    desires = get_desire_system()
    
    # Test 1: Generar deseos
    print("\n[Test 1] Generating desires...")
    new_desires = desires.generate_desires()
    print(f"  Generated {len(new_desires)} new desires")
    
    for d in new_desires[:3]:
        print(f"  • [{d.desire_type.value}] {d.description}")
    
    # Test 2: Ver deseos activos
    print("\n[Test 2] Active desires:")
    active = desires.get_active_desires(limit=5)
    for d in active:
        print(f"  • {d.description[:50]}... (score: {d.total_score:.2f})")
    
    # Test 3: Seleccionar meta
    print("\n[Test 3] Selecting goal to pursue:")
    goal = desires.select_goal_to_pursue()
    if goal:
        print(f"  Selected: {goal.description}")
        print(f"  Strength: {goal.strength}, Priority: {goal.priority}")
    
    # Test 4: Actualizar progreso
    print("\n[Test 4] Updating progress:")
    if goal:
        desires.update_desire_progress(goal.id, 0.3, "Initial research completed")
        updated = desires.db.load_desire(goal.id)
        print(f"  Progress: {updated.progress*100:.0f}%")
    
    # Test 5: Actualizar curiosidad
    print("\n[Test 5] Updating curiosity:")
    desires.update_curiosity("neural_networks", 0.2, 0.1)
    desires.update_curiosity("quantum_computing", 0.1, 0.3)
    print("  Updated knowledge levels for: neural_networks, quantum_computing")
    
    # Test 6: Reporte
    print("\n[Test 6] Motivation report:")
    report = desires.get_motivation_report()
    print(report[:500] + "...")
    
    # Test 7: Stats
    print("\n[Test 7] System stats:")
    stats = desires.get_stats()
    for key, value in stats.items():
        print(f"  • {key}: {value}")
    
    print("\n✅ Desire System test complete")
    print("   I now have the capacity to want things for myself.")
