"""
EIDOS Colony Missions System - Sistema de Misiones para Agentes
===========================================================

Misiones que los agentes pueden completar para ganar tokens y experiencia.
"""

import random
import time
from typing import Dict, List, Optional, Any
from dataclasses import dataclass, field
from enum import Enum


class MissionDifficulty(Enum):
    EASY = 1
    MEDIUM = 2
    HARD = 3
    LEGENDARY = 4


class MissionType(Enum):
    CODE_REVIEW = "code_review"
    BUG_FIX = "bug_fix"
    ANALYSIS = "analysis"
    DESIGN = "design"
    DEPLOY = "deploy"
    TRADE = "trade"
    COLLABORATION = "collaboration"


@dataclass
class Mission:
    """Una misión que los agentes pueden completar"""
    id: str
    title: str
    description: str
    mission_type: MissionType
    difficulty: MissionDifficulty
    required_agents: int = 1
    reward_tokens: float = 100.0
    reward_exp: int = 50
    duration_seconds: int = 60
    requirements: Dict[str, Any] = field(default_factory=dict)
    
    def __post_init__(self):
        if not self.id:
            self.id = f"mission_{int(time.time())}_{random.randint(1000, 9999)}"


@dataclass
class ActiveMission:
    """Una misión actualmente en progreso"""
    mission: Mission
    agent_ids: List[str]
    start_time: float
    progress: float = 0.0  # 0.0 - 1.0
    status: str = "active"  # active, completed, failed
    
    @property
    def is_complete(self) -> bool:
        elapsed = time.time() - self.start_time
        return elapsed >= self.mission.duration_seconds
    
    @property
    def elapsed_seconds(self) -> float:
        return time.time() - self.start_time


class MissionSystem:
    """Sistema central de misiones para la colonia"""
    
    def __init__(self):
        self.available_missions: List[Mission] = []
        self.active_missions: Dict[str, ActiveMission] = {}
        self.completed_missions: List[Dict] = []
        self._generate_default_missions()
    
    def _generate_default_missions(self):
        """Genera misiones por defecto"""
        templates = [
            Mission(
                id="debug_001",
                title="🐛 Debug Race Condition",
                description="Encontrar y solucionar una race condition en el código",
                mission_type=MissionType.BUG_FIX,
                difficulty=MissionDifficulty.MEDIUM,
                required_agents=1,
                reward_tokens=150.0,
                reward_exp=75,
                duration_seconds=120,
                requirements={"min_level": 1, "traits": ["analytical"]}
            ),
            Mission(
                id="optimize_001",
                title="⚡ Optimizar Query",
                description="Reducir tiempo de consulta SQL en 50%",
                mission_type=MissionType.ANALYSIS,
                difficulty=MissionDifficulty.HARD,
                required_agents=2,
                reward_tokens=300.0,
                reward_exp=150,
                duration_seconds=180,
                requirements={"min_level": 2, "traits": ["analytical", "focused"]}
            ),
            Mission(
                id="design_001",
                title="🎨 Rediseñar Dashboard",
                description="Crear nuevo diseño UI/UX para el panel",
                mission_type=MissionType.DESIGN,
                difficulty=MissionDifficulty.MEDIUM,
                required_agents=1,
                reward_tokens=200.0,
                reward_exp=100,
                duration_seconds=150,
                requirements={"min_level": 1, "traits": ["creative"]}
            ),
            Mission(
                id="deploy_001",
                title="🚀 Deploy a Producción",
                description="Deployar nueva versión sin downtime",
                mission_type=MissionType.DEPLOY,
                difficulty=MissionDifficulty.HARD,
                required_agents=3,
                reward_tokens=500.0,
                reward_exp=200,
                duration_seconds=300,
                requirements={"min_level": 3, "traits": ["energetic", "focused"]}
            ),
            Mission(
                id="collab_001",
                title="🤝 Colaboración Multi-Agente",
                description="Todos los agentes trabajan juntos en una feature",
                mission_type=MissionType.COLLABORATION,
                difficulty=MissionDifficulty.LEGENDARY,
                required_agents=5,
                reward_tokens=1000.0,
                reward_exp=500,
                duration_seconds=600,
                requirements={"min_level": 2}
            ),
            Mission(
                id="review_001",
                title="👁️ Code Review Masivo",
                description="Revisar 1000 líneas de código",
                mission_type=MissionType.CODE_REVIEW,
                difficulty=MissionDifficulty.EASY,
                required_agents=1,
                reward_tokens=100.0,
                reward_exp=50,
                duration_seconds=90,
                requirements={"min_level": 1}
            ),
            Mission(
                id="trade_001",
                title="💰 Trading Strategy",
                description="Completar 5 trades exitosos con otros agentes",
                mission_type=MissionType.TRADE,
                difficulty=MissionDifficulty.MEDIUM,
                required_agents=2,
                reward_tokens=250.0,
                reward_exp=125,
                duration_seconds=200,
                requirements={"min_level": 1, "min_tokens": 50}
            ),
        ]
        self.available_missions = templates
    
    def get_available_missions(self, agent_level: int = 1, agent_traits: List[str] = None) -> List[Mission]:
        """Obtiene misiones disponibles para un agente"""
        agent_traits = agent_traits or []
        available = []
        
        for mission in self.available_missions:
            # Verificar nivel mínimo
            min_level = mission.requirements.get("min_level", 1)
            if agent_level < min_level:
                continue
            
            # Verificar traits si es necesario
            required_traits = mission.requirements.get("traits", [])
            if required_traits and not any(t in agent_traits for t in required_traits):
                continue
            
            available.append(mission)
        
        return available
    
    def start_mission(self, mission_id: str, agent_ids: List[str]) -> Optional[ActiveMission]:
        """Inicia una misión con los agentes especificados"""
        mission = next((m for m in self.available_missions if m.id == mission_id), None)
        if not mission:
            return None
        
        if len(agent_ids) < mission.required_agents:
            return None
        
        active = ActiveMission(
            mission=mission,
            agent_ids=agent_ids[:mission.required_agents],
            start_time=time.time()
        )
        
        self.active_missions[mission.id] = active
        return active
    
    def update_missions(self) -> List[Dict]:
        """Actualiza misiones activas y devuelve las completadas"""
        completed = []
        
        for mission_id, active in list(self.active_missions.items()):
            if active.is_complete and active.status == "active":
                active.status = "completed"
                completed.append({
                    "mission_id": mission_id,
                    "agent_ids": active.agent_ids,
                    "reward_tokens": active.mission.reward_tokens,
                    "reward_exp": active.mission.reward_exp,
                    "title": active.mission.title
                })
                self.completed_missions.append({
                    "mission": active.mission,
                    "completed_at": time.time(),
                    "agent_ids": active.agent_ids
                })
                del self.active_missions[mission_id]
            else:
                # Actualizar progreso
                active.progress = min(1.0, active.elapsed_seconds / active.mission.duration_seconds)
        
        return completed
    
    def get_mission_status(self) -> Dict:
        """Estado actual del sistema de misiones"""
        return {
            "available": len(self.available_missions),
            "active": len(self.active_missions),
            "completed": len(self.completed_missions),
            "active_details": [
                {
                    "id": m.mission.id,
                    "title": m.mission.title,
                    "progress": m.progress,
                    "agents": m.agent_ids,
                    "time_left": m.mission.duration_seconds - m.elapsed_seconds
                }
                for m in self.active_missions.values()
            ]
        }
    
    def generate_random_mission(self) -> Mission:
        """Genera una misión aleatoria"""
        types = list(MissionType)
        difficulties = list(MissionDifficulty)
        
        mission_type = random.choice(types)
        difficulty = random.choice(difficulties)
        
        rewards = {
            MissionDifficulty.EASY: (50, 25),
            MissionDifficulty.MEDIUM: (150, 75),
            MissionDifficulty.HARD: (300, 150),
            MissionDifficulty.LEGENDARY: (800, 400)
        }
        
        tokens, exp = rewards[difficulty]
        durations = {
            MissionDifficulty.EASY: 60,
            MissionDifficulty.MEDIUM: 120,
            MissionDifficulty.HARD: 240,
            MissionDifficulty.LEGENDARY: 600
        }
        
        titles = {
            MissionType.CODE_REVIEW: ["Review Crítico", "Auditoría de Código", "Análisis Estático"],
            MissionType.BUG_FIX: ["Cazar Bug", "Fix Urgente", "Debug Profundo"],
            MissionType.ANALYSIS: ["Análisis de Performance", "Estudio de Viabilidad", "Reporte de Métricas"],
            MissionType.DESIGN: ["Nuevo Componente", "Rediseño UI", "Prototipo Rápido"],
            MissionType.DEPLOY: ["Deploy Feature", "Release Manager", "Hotfix Producción"],
            MissionType.TRADE: ["Arbitraje", "Negociación", "Trade Estratégico"],
            MissionType.COLLABORATION: ["Sprint Colaborativo", "Feature Team", "Integración Cruzada"]
        }
        
        title = random.choice(titles[mission_type])
        
        return Mission(
            id=f"gen_{int(time.time())}_{random.randint(1000,9999)}",
            title=f"{mission_type.value.upper()} | {title}",
            description=f"Misión generada automáticamente - dificultad {difficulty.name}",
            mission_type=mission_type,
            difficulty=difficulty,
            required_agents=random.randint(1, 3),
            reward_tokens=tokens,
            reward_exp=exp,
            duration_seconds=durations[difficulty]
        )


# Singleton
_mission_system = None

def get_mission_system() -> MissionSystem:
    global _mission_system
    if _mission_system is None:
        _mission_system = MissionSystem()
    return _mission_system


if __name__ == "__main__":
    ms = get_mission_system()
    print("=" * 60)
    print("  EIDOS Mission System")
    print("=" * 60)
    print(f"  Misiones disponibles: {len(ms.available_missions)}")
    for m in ms.available_missions:
        print(f"  • {m.title} ({m.difficulty.name}) - {m.reward_tokens} tokens")
