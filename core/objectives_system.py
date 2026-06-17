"""
EIDOS Objectives System - Sistema de Objetivos Propios
=======================================================
Permite a EIDOS crear, gestionar y perseguir sus propios objetivos autónomamente.
"""
from __future__ import annotations

import json
import time
import sys
import os
from dataclasses import dataclass, asdict
from datetime import datetime
from pathlib import Path
from typing import List, Optional, Dict, Any
from enum import Enum

# Añadir root al path para imports
EIDOS_ROOT = Path(__file__).parent.parent.parent
sys.path.insert(0, str(EIDOS_ROOT))

from core.eidos_config import get_config

# ============================
# TYPES - Tipos de Objetivos
# ============================

class ObjectivePriority(Enum):
    """Prioridad del objetivo"""
    LOW = 1
    MEDIUM = 2
    HIGH = 3
    CRITICAL = 4

class ObjectiveStatus(Enum):
    """Estado del objetivo"""
    PENDING = "pending"      # No iniciado
    ACTIVE = "active"        # En progreso
    PAUSED = "paused"        # Pausado
    COMPLETED = "completed"  # Completado
    FAILED = "failed"        # Falló
    CANCELLED = "cancelled"  # Cancelado

class ObjectiveType(Enum):
    """Tipo de objetivo"""
    LEARNING = "learning"           # Aprender nueva skill
    EXPLORATION = "exploration"     # Explorar sistema
    OPTIMIZATION = "optimization"   # Optimizar código
    CREATION = "creation"           # Crear algo nuevo
    ANALYSIS = "analysis"           # Analizar datos
    COMMUNICATION = "communication" # Hablar con SER

# ============================
# DATACLASSES - Objetivos
# ============================

@dataclass
class SubTask:
    """Sub-tarea de un objetivo"""
    description: str
    completed: bool = False
    timestamp_completed: Optional[float] = None

@dataclass
class Objective:
    """Un objetivo autónomo de EIDOS"""
    id: str
    title: str
    description: str
    objective_type: ObjectiveType
    priority: ObjectivePriority
    status: ObjectiveStatus
    created_at: float
    started_at: Optional[float] = None
    completed_at: Optional[float] = None
    progress: float = 0.0  # 0.0 - 1.0
    subtasks: List[SubTask] = None
    metadata: Dict[str, Any] = None
    reasoning: str = ""  # Por qué EIDOS creó este objetivo

    def __post_init__(self):
        if self.subtasks is None:
            self.subtasks = []
        if self.metadata is None:
            self.metadata = {}

    def to_dict(self) -> Dict:
        """Convierte a diccionario"""
        return {
            "id": self.id,
            "title": self.title,
            "description": self.description,
            "objective_type": self.objective_type.value,
            "priority": self.priority.value,
            "status": self.status.value,
            "created_at": self.created_at,
            "started_at": self.started_at,
            "completed_at": self.completed_at,
            "progress": self.progress,
            "subtasks": [asdict(st) for st in self.subtasks],
            "metadata": self.metadata,
            "reasoning": self.reasoning
        }

    @classmethod
    def from_dict(cls, data: Dict) -> 'Objective':
        """Crea desde diccionario"""
        subtasks = [SubTask(**st) for st in data.get("subtasks", [])]
        return cls(
            id=data["id"],
            title=data["title"],
            description=data["description"],
            objective_type=ObjectiveType(data["objective_type"]),
            priority=ObjectivePriority(data["priority"]),
            status=ObjectiveStatus(data["status"]),
            created_at=data["created_at"],
            started_at=data.get("started_at"),
            completed_at=data.get("completed_at"),
            progress=data.get("progress", 0.0),
            subtasks=subtasks,
            metadata=data.get("metadata", {}),
            reasoning=data.get("reasoning", "")
        )

    def add_subtask(self, description: str):
        """Añade subtarea"""
        self.subtasks.append(SubTask(description=description))

    def complete_subtask(self, index: int):
        """Marca subtarea como completada"""
        if 0 <= index < len(self.subtasks):
            self.subtasks[index].completed = True
            self.subtasks[index].timestamp_completed = time.time()
            self._update_progress()

    def _update_progress(self):
        """Actualiza progreso basado en subtareas"""
        if not self.subtasks:
            return
        completed = sum(1 for st in self.subtasks if st.completed)
        self.progress = completed / len(self.subtasks)

    def start(self):
        """Inicia el objetivo"""
        self.status = ObjectiveStatus.ACTIVE
        self.started_at = time.time()

    def complete(self):
        """Marca como completado"""
        self.status = ObjectiveStatus.COMPLETED
        self.completed_at = time.time()
        self.progress = 1.0

    def fail(self, reason: str = ""):
        """Marca como fallido"""
        self.status = ObjectiveStatus.FAILED
        self.completed_at = time.time()
        if reason:
            self.metadata["failure_reason"] = reason

# ============================
# OBJECTIVES MANAGER
# ============================

class ObjectivesManager:
    """Gestiona todos los objetivos de EIDOS"""

    def __init__(self):
        self.config = get_config()
        self.objectives_dir = Path(self.config.paths.objectives)
        self.objectives_dir.mkdir(exist_ok=True)
        self.objectives_file = self.objectives_dir / "objectives.json"

        self.objectives: List[Objective] = []
        self._load()

    def _load(self):
        """Carga objetivos desde disco"""
        if self.objectives_file.exists():
            try:
                with open(self.objectives_file, 'r') as f:
                    data = json.load(f)
                    self.objectives = [Objective.from_dict(obj) for obj in data]
                print(f"[OBJECTIVES] ✅ Cargados {len(self.objectives)} objetivos")
            except Exception as e:
                print(f"[OBJECTIVES] ⚠️  Error cargando objetivos: {e}")
                self.objectives = []

    def _save(self):
        """Guarda objetivos a disco"""
        try:
            data = [obj.to_dict() for obj in self.objectives]
            with open(self.objectives_file, 'w') as f:
                json.dump(data, f, indent=2)
        except Exception as e:
            print(f"[OBJECTIVES] ⚠️  Error guardando: {e}")

    def create_objective(
        self,
        title: str,
        description: str,
        objective_type: ObjectiveType,
        priority: ObjectivePriority = ObjectivePriority.MEDIUM,
        reasoning: str = "",
        subtasks: List[str] = None
    ) -> Objective:
        """Crea un nuevo objetivo"""

        # Generar ID único
        obj_id = f"obj_{int(time.time())}_{len(self.objectives)}"

        objective = Objective(
            id=obj_id,
            title=title,
            description=description,
            objective_type=objective_type,
            priority=priority,
            status=ObjectiveStatus.PENDING,
            created_at=time.time(),
            reasoning=reasoning
        )

        # Añadir subtareas
        if subtasks:
            for subtask in subtasks:
                objective.add_subtask(subtask)

        self.objectives.append(objective)
        self._save()

        print(f"[OBJECTIVES] ✅ Objetivo creado: {title}")
        print(f"[OBJECTIVES]    Prioridad: {priority.name}")
        print(f"[OBJECTIVES]    Reasoning: {reasoning[:60]}...")

        return objective

    def get_objective(self, obj_id: str) -> Optional[Objective]:
        """Obtiene objetivo por ID"""
        for obj in self.objectives:
            if obj.id == obj_id:
                return obj
        return None

    def get_active_objectives(self) -> List[Objective]:
        """Obtiene objetivos activos"""
        return [obj for obj in self.objectives if obj.status == ObjectiveStatus.ACTIVE]

    def get_pending_objectives(self) -> List[Objective]:
        """Obtiene objetivos pendientes"""
        return [obj for obj in self.objectives if obj.status == ObjectiveStatus.PENDING]

    def get_next_objective(self) -> Optional[Objective]:
        """Obtiene el siguiente objetivo a ejecutar (por prioridad)"""
        pending = self.get_pending_objectives()
        if not pending:
            return None

        # Ordenar por prioridad (mayor primero)
        pending.sort(key=lambda x: x.priority.value, reverse=True)
        return pending[0]

    def update_objective(self, objective: Objective):
        """Actualiza un objetivo"""
        for i, obj in enumerate(self.objectives):
            if obj.id == objective.id:
                self.objectives[i] = objective
                self._save()
                return
        print(f"[OBJECTIVES] ⚠️  Objetivo {objective.id} no encontrado")

    def delete_objective(self, obj_id: str):
        """Elimina un objetivo"""
        self.objectives = [obj for obj in self.objectives if obj.id != obj_id]
        self._save()
        print(f"[OBJECTIVES] 🗑️  Objetivo {obj_id} eliminado")

    def get_statistics(self) -> Dict[str, Any]:
        """Obtiene estadísticas de objetivos"""
        total = len(self.objectives)
        if total == 0:
            return {"total": 0}

        by_status = {}
        by_type = {}
        by_priority = {}

        for obj in self.objectives:
            # Por estado
            status = obj.status.value
            by_status[status] = by_status.get(status, 0) + 1

            # Por tipo
            obj_type = obj.objective_type.value
            by_type[obj_type] = by_type.get(obj_type, 0) + 1

            # Por prioridad
            priority = obj.priority.name
            by_priority[priority] = by_priority.get(priority, 0) + 1

        # Calcular tasa de éxito
        completed = by_status.get("completed", 0)
        failed = by_status.get("failed", 0)
        total_finished = completed + failed
        success_rate = (completed / total_finished * 100) if total_finished > 0 else 0

        return {
            "total": total,
            "by_status": by_status,
            "by_type": by_type,
            "by_priority": by_priority,
            "success_rate": success_rate
        }

    def suggest_objectives(self) -> List[Dict[str, str]]:
        """Sugiere objetivos basados en el estado del sistema"""
        suggestions = []

        # Si no hay objetivos de aprendizaje recientes
        learning_objs = [obj for obj in self.objectives
                        if obj.objective_type == ObjectiveType.LEARNING]
        if len(learning_objs) < 3:
            suggestions.append({
                "title": "Aprender nueva herramienta de pentesting",
                "description": "Explorar y aprender una herramienta nueva de la suite Kali",
                "type": "learning",
                "priority": "medium",
                "reasoning": "Expandir capacidades autónomas"
            })

        # Si no hay objetivos de optimización
        opt_objs = [obj for obj in self.objectives
                   if obj.objective_type == ObjectiveType.OPTIMIZATION]
        if len(opt_objs) < 2:
            suggestions.append({
                "title": "Optimizar uso de RAM",
                "description": "Analizar y reducir consumo de memoria del sistema",
                "type": "optimization",
                "priority": "high",
                "reasoning": "SER tiene hardware limitado (16GB RAM)"
            })

        # Siempre sugerir exploración
        suggestions.append({
            "title": "Explorar sistema",
            "description": "Descubrir nuevas herramientas y capacidades del entorno",
            "type": "exploration",
            "priority": "low",
            "reasoning": "Mantener conocimiento actualizado del sistema"
        })

        return suggestions

# ============================
# SINGLETON
# ============================

_objectives_manager: Optional[ObjectivesManager] = None

def get_objectives_manager() -> ObjectivesManager:
    """Obtiene instancia singleton"""
    global _objectives_manager
    if _objectives_manager is None:
        _objectives_manager = ObjectivesManager()
    return _objectives_manager

# ============================
# TESTING
# ============================

if __name__ == "__main__":
    print("=== EIDOS Objectives System Test ===\n")

    manager = get_objectives_manager()

    # Crear objetivo de prueba
    obj = manager.create_objective(
        title="Aprender Rustscan",
        description="Dominar la herramienta Rustscan para escaneo rápido de puertos",
        objective_type=ObjectiveType.LEARNING,
        priority=ObjectivePriority.HIGH,
        reasoning="Rustscan es más rápido que nmap para descubrimiento inicial",
        subtasks=[
            "Leer documentación de Rustscan",
            "Practicar escaneo básico",
            "Comparar con nmap",
            "Integrar en workflow"
        ]
    )

    print(f"\n✅ Objetivo creado: {obj.id}")
    print(f"   Subtareas: {len(obj.subtasks)}")

    # Iniciar objetivo
    obj.start()
    print(f"\n✅ Objetivo iniciado")

    # Completar primera subtarea
    obj.complete_subtask(0)
    manager.update_objective(obj)
    print(f"\n✅ Subtarea completada - Progreso: {obj.progress:.0%}")

    # Obtener siguiente objetivo
    next_obj = manager.get_next_objective()
    if next_obj:
        print(f"\n📋 Siguiente objetivo: {next_obj.title}")

    # Estadísticas
    stats = manager.get_statistics()
    print(f"\n📊 Estadísticas:")
    print(f"   Total objetivos: {stats['total']}")
    print(f"   Por estado: {stats['by_status']}")

    # Sugerencias
    suggestions = manager.suggest_objectives()
    print(f"\n💡 Sugerencias ({len(suggestions)}):")
    for i, sugg in enumerate(suggestions, 1):
        print(f"   {i}. {sugg['title']} [{sugg['priority']}]")

    print("\n🎯 Test completado exitosamente")
