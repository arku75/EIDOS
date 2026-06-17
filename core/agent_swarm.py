#!/usr/bin/env python3
"""
core/agent_swarm.py -- Agent Swarm Controller
==============================================
Spawn dinamico de agentes especializados para tareas complejas.
Envoltorio sobre core.multi_agent (EIDOSPrincipal + Workers).

Uso:
    from core.agent_swarm import get_swarm_controller, SwarmTaskType

    controller = get_swarm_controller()
    task_id = controller.spawn("Research state of the art LLMs", SwarmTaskType.RESEARCH)
    status = controller.get_swarm_status(task_id)
"""
from __future__ import annotations

import enum
import time
import uuid
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional


class SwarmTaskType(enum.Enum):
    """Tipos de tareas que el swarm puede manejar."""
    RESEARCH = "research"
    CODE_REVIEW = "code_review"
    DEBUGGING = "debugging"
    ARCHITECTURE = "architecture"
    ANALYSIS = "analysis"
    CREATIVE = "creative"
    TESTING = "testing"
    DOCUMENTATION = "documentation"


@dataclass
class SwarmTask:
    """Tarea gestionada por el swarm controller."""
    task_id: str
    description: str
    task_type: SwarmTaskType
    status: str = "pending"  # pending, running, completed, failed
    complexity: float = 0.0
    agents_total: int = 0
    agents_completed: int = 0
    agents_failed: int = 0
    agents_working: int = 0
    result: Optional[Dict[str, Any]] = None
    created_at: float = field(default_factory=time.time)
    started_at: Optional[float] = None
    completed_at: Optional[float] = None
    elapsed: float = 0.0


class SwarmController:
    """
    Controlador del Agent Swarm.

    Gestiona el ciclo de vida de multiples tareas usando EIDOSPrincipal
    como backend de ejecucion multi-worker con ThreadPoolExecutor.
    """

    MAX_AGENTS_LIMIT = 16

    def __init__(self):
        self._tasks: Dict[str, SwarmTask] = {}
        self._total_spawned = 0
        self._total_tasks_created = 0
        self._eidos = None  # lazy init

    def _get_eidos(self):
        """Lazy init de EIDOSPrincipal."""
        if self._eidos is None:
            from core.multi_agent import get_eidos_principal
            self._eidos = get_eidos_principal()
        return self._eidos

    @staticmethod
    def _estimate_complexity(description: str) -> float:
        """Estima complejidad 0-1 de una tarea."""
        desc_lower = description.lower()
        score = 0.3  # base

        word_count = len(description.split())
        if word_count > 30:
            score += 0.1
        if word_count > 80:
            score += 0.1
        if word_count > 150:
            score += 0.1

        complex_kw = ["multi", "complejo", "arquitectura", "sistema", "exhaustivo",
                       "multiple files", "deep", "full", "complete"]
        for kw in complex_kw:
            if kw in desc_lower:
                score += 0.1
                break

        return min(score, 1.0)

    def spawn(self, description: str, task_type: SwarmTaskType,
              max_agents: Optional[int] = None) -> str:
        """Lanza un nuevo swarm para una tarea.

        Args:
            description: Descripcion de la tarea.
            task_type: Tipo de tarea (SwarmTaskType).
            max_agents: Maximo de agentes a usar (opcional, usa complejidad si no).

        Returns:
            task_id de seguimiento.
        """
        task_id = f"swarm_{uuid.uuid4().hex[:8]}"
        complexity = self._estimate_complexity(description)

        if max_agents is None:
            # Derivar numero de agentes de la complejidad
            max_agents = max(1, min(int(complexity * self.MAX_AGENTS_LIMIT),
                                    self.MAX_AGENTS_LIMIT))

        task = SwarmTask(
            task_id=task_id,
            description=description,
            task_type=task_type,
            complexity=complexity,
            agents_total=max_agents,
            agents_working=max_agents,
        )

        self._tasks[task_id] = task
        self._total_spawned += max_agents
        self._total_tasks_created += 1

        # Ejecutar via EIDOSPrincipal (usa workers en paralelo)
        task.status = "running"
        task.started_at = time.time()

        try:
            eidos = self._get_eidos()
            result = eidos.execute(description)

            task.result = result
            task.status = "completed" if result.get("success") else "failed"
            task.agents_completed = result.get("workers_used", max_agents)
            task.agents_failed = max_agents - task.agents_completed
            task.agents_working = 0
            task.completed_at = time.time()
            task.elapsed = task.completed_at - (task.started_at or task.created_at)

        except Exception as e:
            task.status = "failed"
            task.result = {"error": str(e)}
            task.agents_working = 0
            task.completed_at = time.time()
            task.elapsed = task.completed_at - (task.started_at or task.created_at)

        return task_id

    def get_swarm_status(self, task_id: str) -> Optional[Dict[str, Any]]:
        """Obtiene estado detallado de un swarm."""
        task = self._tasks.get(task_id)
        if not task:
            return None

        elapsed = task.elapsed
        if task.status == "running" and task.started_at:
            elapsed = time.time() - task.started_at

        return {
            "task_id": task.task_id,
            "description": task.description,
            "task_type": task.task_type.value,
            "status": task.status,
            "complexity": task.complexity,
            "agents_total": task.agents_total,
            "agents_completed": task.agents_completed,
            "agents_failed": task.agents_failed,
            "agents_working": task.agents_working,
            "elapsed": elapsed,
            "result": task.result,
        }

    def get_statistics(self) -> Dict[str, Any]:
        """Obtiene estadisticas globales del controller."""
        active_agents = sum(
            t.agents_working for t in self._tasks.values()
            if t.status == "running"
        )
        active_tasks = sum(
            1 for t in self._tasks.values() if t.status == "running"
        )
        completed_tasks = sum(
            1 for t in self._tasks.values() if t.status in ("completed", "failed")
        )

        # Stats from EIDOSPrincipal
        try:
            eidos_stats = self._get_eidos().get_stats()
            worker_stats = eidos_stats.get("workers", {})
            total_worker_tasks = sum(
                w.get("tasks_completed", 0) for w in worker_stats.values()
            )
        except Exception:
            total_worker_tasks = 0

        return {
            "max_agents_limit": self.MAX_AGENTS_LIMIT,
            "active_agents": active_agents,
            "active_tasks": active_tasks,
            "completed_tasks": completed_tasks,
            "total_spawned": self._total_spawned,
            "total_tasks_created": self._total_tasks_created,
            "worker_tasks_completed": total_worker_tasks,
        }

    def get_active_swarms(self) -> List[Dict[str, Any]]:
        """Lista todos los swarms activos (running o pending)."""
        active = []
        for task in self._tasks.values():
            if task.status in ("running", "pending"):
                elapsed = task.elapsed
                if task.status == "running" and task.started_at:
                    elapsed = time.time() - task.started_at
                active.append({
                    "task_id": task.task_id,
                    "description": task.description,
                    "status": task.status,
                    "elapsed": elapsed,
                })
        return sorted(active, key=lambda t: t.get("elapsed", 0), reverse=True)


# =============================================================================
# Singleton
# =============================================================================

_controller: Optional[SwarmController] = None


def get_swarm_controller() -> SwarmController:
    """Obtiene la instancia singleton del SwarmController."""
    global _controller
    if _controller is None:
        _controller = SwarmController()
    return _controller
