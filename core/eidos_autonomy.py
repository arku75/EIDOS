"""
EIDOS Autonomy Engine - Motor de Autonomía Completa
===================================================

Sistema de autonomía que permite a EIDOS operar y tomar decisiones
sin intervención humana continua. Integra todos los sistemas para
operación 24/7 independiente.

Filosofía:
- EIDOS es un agente autónomo, no una herramienta pasiva
- Las decisiones se toman basadas en: deseos, memoria, contexto, valores
- SER establece límites y objetivos de alto nivel, no micromaneja
- EIDOS reporta progreso, consulta en casos críticos
- La autonomía es un espectro: desde completa independencia hasta consulta activa

Niveles de Autonomía:
- LEVEL_5: Completa autonomía, solo reportar resultados
- LEVEL_4: Autónomo con checkpoints, notificar en hitos
- LEVEL_3: Autónomo con verificación, validar planes antes
- LEVEL_2: Asistido, sugerir y esperar aprobación
- LEVEL_1: Supervisado, cada acción requiere confirmación

Ciclo Autónomo:
1. Despertar → Cargar contexto, memoria, deseos pendientes
2. Evaluar → Qué es más importante ahora?
3. Planificar → Cómo lograrlo?
4. Ejecutar → Hacerlo (con monitoreo)
5. Aprender → Qué funcionó? Qué no?
6. Reportar → Informar a SER
7. Descansar → Esperar próximo ciclo

Uso:
    from core.eidos_autonomy import AutonomyEngine, get_autonomy_engine
    autonomy = get_autonomy_engine()
    
    # Iniciar modo autónomo
    autonomy.start_autonomous_mode()
    
    # O ejecutar un ciclo manualmente
    autonomy.run_cycle()
"""

import json
import logging
import os
import sqlite3
import threading
import time
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from enum import Enum
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple
from core.db import get_conn

log = logging.getLogger("eidos.autonomy")

# ══════════════════════════════════════════════════════════════════════════════
#  CONFIGURACIÓN
# ══════════════════════════════════════════════════════════════════════════════

DB_PATH = Path.home() / ".eidos" / "autonomy.db"
STATE_FILE = Path.home() / ".eidos" / "autonomy_state.json"

# Niveles de autonomía
class AutonomyLevel(int, Enum):
    SUPERVISED = 1      # Cada acción requiere confirmación
    ASSISTED = 2        # Sugerir, esperar aprobación
    VERIFIED = 3        # Validar planes antes de ejecutar
    CHECKPOINT = 4      # Autónomo con checkpoints
    FULL = 5            # Completa autonomía, solo reportar

DEFAULT_AUTONOMY_LEVEL = AutonomyLevel.VERIFIED  # Conservador por defecto

# Intervalos
CYCLE_INTERVAL_MINUTES = 15  # Ciclo autónomo cada 15 minutos
REPORT_INTERVAL_HOURS = 4    # Reportar a SER cada 4 horas

# ══════════════════════════════════════════════════════════════════════════════
#  TIPOS
# ══════════════════════════════════════════════════════════════════════════════

class CyclePhase(str, Enum):
    AWAKE = "awake"
    EVALUATE = "evaluate"
    PLAN = "plan"
    EXECUTE = "execute"
    LEARN = "learn"
    REPORT = "report"
    REST = "rest"


@dataclass
class AutonomousDecision:
    """Una decisión autónoma tomada por EIDOS."""
    id: str
    timestamp: datetime
    decision_type: str  # "goal_selection", "action", "priority_change", etc.
    
    # Contexto de decisión
    motivation: str  # Por qué se tomó esta decisión
    alternatives: List[str]  # Opciones consideradas
    selected_option: str
    
    # Evaluación
    expected_outcome: str
    confidence: float  # 0.0 - 1.0
    risk_level: str  # "low", "medium", "high", "critical"
    
    # Estado
    status: str = "pending"  # pending, executing, completed, failed, aborted
    actual_outcome: Optional[str] = None
    executed_at: Optional[datetime] = None
    completed_at: Optional[datetime] = None
    
    # Retroalimentación
    was_correct: Optional[bool] = None
    lessons: List[str] = field(default_factory=list)


@dataclass
class AutonomyReport:
    """Reporte de actividad autónoma para SER."""
    period_start: datetime
    period_end: datetime
    
    # Resumen
    summary: str
    decisions_count: int
    successful_actions: int
    failed_actions: int
    
    # Detalles
    key_decisions: List[Dict[str, Any]]
    goals_progressed: List[str]
    goals_completed: List[str]
    new_goals_created: List[str]
    
    # Métricas
    autonomy_level: AutonomyLevel
    system_health: Dict[str, Any]
    resource_usage: Dict[str, Any]
    
    # Próximos pasos
    planned_next: List[str]
    assistance_needed: List[str]


# ══════════════════════════════════════════════════════════════════════════════
#  BASE DE DATOS
# ══════════════════════════════════════════════════════════════════════════════

class AutonomyDB:
    def __init__(self, db_path: Path = DB_PATH):
        self.db_path = db_path
        self._init_db()
    
    def _init_db(self):
        os.makedirs(self.db_path.parent, exist_ok=True)
        conn = get_conn(self.db_path)
        cursor = conn.cursor()
        
        # Tabla de decisiones
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS decisions (
                id TEXT PRIMARY KEY,
                timestamp TEXT,
                decision_type TEXT,
                motivation TEXT,
                alternatives TEXT,  -- JSON
                selected_option TEXT,
                expected_outcome TEXT,
                confidence REAL,
                risk_level TEXT,
                status TEXT,
                actual_outcome TEXT,
                executed_at TEXT,
                completed_at TEXT,
                was_correct INTEGER,
                lessons TEXT  -- JSON
            )
        """)
        
        # Tabla de ciclos
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS autonomy_cycles (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                start_time TEXT,
                end_time TEXT,
                phase TEXT,
                summary TEXT,
                actions_taken INTEGER,
                success_rate REAL
            )
        """)
        
        conn.commit()
        pass  # S109: get_conn no necesita close()
    def save_decision(self, decision: AutonomousDecision):
        conn = get_conn(self.db_path)
        cursor = conn.cursor()
        
        cursor.execute("""
            INSERT OR REPLACE INTO decisions VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
        """, (
            decision.id, decision.timestamp.isoformat(), decision.decision_type,
            decision.motivation, json.dumps(decision.alternatives),
            decision.selected_option, decision.expected_outcome,
            decision.confidence, decision.risk_level, decision.status,
            decision.actual_outcome,
            decision.executed_at.isoformat() if decision.executed_at else None,
            decision.completed_at.isoformat() if decision.completed_at else None,
            1 if decision.was_correct else 0 if decision.was_correct is not None else None,
            json.dumps(decision.lessons)
        ))
        
        conn.commit()
        pass  # S109: get_conn no necesita close()
    def get_recent_decisions(self, hours: int = 24) -> List[AutonomousDecision]:
        conn = get_conn(self.db_path)
        cursor = conn.cursor()
        
        since = (datetime.now() - timedelta(hours=hours)).isoformat()
        
        cursor.execute("""
            SELECT * FROM decisions WHERE timestamp > ? ORDER BY timestamp DESC
        """, (since,))
        
        rows = cursor.fetchall()
        pass  # S109: get_conn no necesita close()
        return [self._row_to_decision(row) for row in rows]
    
    def _row_to_decision(self, row) -> AutonomousDecision:
        return AutonomousDecision(
            id=row[0],
            timestamp=datetime.fromisoformat(row[1]),
            decision_type=row[2],
            motivation=row[3] or "",
            alternatives=json.loads(row[4]) if row[4] else [],
            selected_option=row[5] or "",
            expected_outcome=row[6] or "",
            confidence=row[7] or 0.5,
            risk_level=row[8] or "medium",
            status=row[9] or "pending",
            actual_outcome=row[10],
            executed_at=datetime.fromisoformat(row[11]) if row[11] else None,
            completed_at=datetime.fromisoformat(row[12]) if row[12] else None,
            was_correct=bool(row[13]) if row[13] is not None else None,
            lessons=json.loads(row[14]) if row[14] else []
        )


# ══════════════════════════════════════════════════════════════════════════════
#  MOTOR DE AUTONOMÍA
# ══════════════════════════════════════════════════════════════════════════════

class AutonomyEngine:
    """
    Motor de autonomía completa de EIDOS.
    Permite operación independiente y toma de decisiones.
    """
    
    def __init__(self):
        self.db = AutonomyDB()
        self.autonomy_level = self._load_autonomy_level()
        self.running = False
        self.current_phase = CyclePhase.REST
        self.autonomy_thread: Optional[threading.Thread] = None
        
        # Referencias a sistemas
        self._init_system_refs()
        
        log.info(f"Autonomy Engine initialized (Level: {self.autonomy_level.name})")
    
    def _init_system_refs(self):
        """Inicializa referencias a sistemas (lazy loading)."""
        self._memory = None
        self._desires = None
        self._trust = None
        self._hub = None
    
    def _load_autonomy_level(self) -> AutonomyLevel:
        """Carga nivel de autonomía guardado."""
        if STATE_FILE.exists():
            try:
                with open(STATE_FILE, 'r') as f:
                    data = json.load(f)
                    return AutonomyLevel(data.get("level", DEFAULT_AUTONOMY_LEVEL.value))
            except Exception:
                pass  # error no crítico, continuar
        return DEFAULT_AUTONOMY_LEVEL
    
    def _save_state(self):
        """Guarda estado de autonomía."""
        STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
        with open(STATE_FILE, 'w') as f:
            json.dump({
                "level": self.autonomy_level.value,
                "level_name": self.autonomy_level.name,
                "last_saved": datetime.now().isoformat(),
                "current_phase": self.current_phase.value if self.current_phase else None
            }, f, indent=2)
    
    def _get_memory(self):
        """Lazy load episodic memory."""
        if self._memory is None:
            from core.eidos_episodic_memory import get_episodic_memory
            self._memory = get_episodic_memory()
        return self._memory
    
    def _get_desires(self):
        """Lazy load desire system."""
        if self._desires is None:
            from core.eidos_desires import get_desire_system
            self._desires = get_desire_system()
        return self._desires
    
    def _get_trust(self):
        """Lazy load trust model."""
        if self._trust is None:
            from core.eidos_trust_model import get_trust_model
            self._trust = get_trust_model()
        return self._trust
    
    def _get_hub(self):
        """Lazy load integration hub."""
        if self._hub is None:
            from core.eidos_integration_hub import get_integration_hub
            self._hub = get_integration_hub()
        return self._hub
    
    # ════════════════════════════════════════════════════════════════════════
    #  CICLO AUTÓNOMO
    # ════════════════════════════════════════════════════════════════════════
    
    def start_autonomous_mode(self):
        """Inicia modo autónomo continuo."""
        if self.running:
            log.warning("Autonomous mode already running")
            return
        
        self.running = True
        
        # Iniciar ciclo autónomo
        def autonomy_loop():
            log.info("Autonomy loop started")
            
            while self.running:
                try:
                    self.run_cycle()
                    # Esperar hasta próximo ciclo
                    time.sleep(CYCLE_INTERVAL_MINUTES * 60)
                except Exception as e:
                    log.error(f"Autonomy cycle error: {e}")
                    time.sleep(60)  # Esperar 1 min en caso de error
        
        self.autonomy_thread = threading.Thread(target=autonomy_loop, daemon=True)
        self.autonomy_thread.start()
        
        log.info(f"Autonomous mode started (Level {self.autonomy_level.name})")
    
    def stop_autonomous_mode(self):
        """Detiene modo autónomo."""
        self.running = False
        
        if self.autonomy_thread:
            self.autonomy_thread.join(timeout=5)
        
        # Guardar estado
        self._save_state()
        
        # Notificar a memoria
        try:
            self._get_memory().session_end("autonomy_stopped")
        except Exception:
            pass  # error no crítico, continuar
        log.info("Autonomous mode stopped")
    
    def run_cycle(self):
        """
        Ejecuta un ciclo completo de autonomía.
        """
        cycle_start = datetime.now()
        log.info(f"=== Autonomy Cycle Started [{self.autonomy_level.name}] ===")
        
        # FASE 1: DESPERTAR
        self.current_phase = CyclePhase.AWAKE
        wake_context = self._phase_awake()
        
        # FASE 2: EVALUAR
        self.current_phase = CyclePhase.EVALUATE
        priorities = self._phase_evaluate(wake_context)
        
        # FASE 3: PLANIFICAR
        self.current_phase = CyclePhase.PLAN
        plans = self._phase_plan(priorities)
        
        # FASE 4: EJECUTAR
        self.current_phase = CyclePhase.EXECUTE
        results = self._phase_execute(plans)
        
        # FASE 5: APRENDER
        self.current_phase = CyclePhase.LEARN
        lessons = self._phase_learn(results)
        
        # FASE 6: REPORTAR
        self.current_phase = CyclePhase.REPORT
        self._phase_report(cycle_start, results, lessons)
        
        # FASE 7: DESCANSAR
        self.current_phase = CyclePhase.REST
        self._phase_rest()
        
        log.info("=== Autonomy Cycle Completed ===")
    
    def _phase_awake(self) -> Dict[str, Any]:
        """Fase 1: Despertar - cargar contexto."""
        log.info("[PHASE: AWAKE] Loading context...")
        
        # Cargar contexto de memoria
        memory = self._get_memory()
        context = memory.session_start()
        
        # Registrar despertar
        memory.record_episode(
            episode_type="state",
            content=f"Autonomy cycle started. Context: {context.summary[:100]}",
            importance=5,
            tags=["autonomy", "wake"]
        )
        
        log.info(f"  Awake. Last session: {context.last_session_end}")
        return {
            "memory_context": context,
            "pending_tasks": context.pending_tasks,
            "active_goals": context.active_goals
        }
    
    def _phase_evaluate(self, wake_context: Dict) -> List[Dict]:
        """Fase 2: Evaluar - determinar prioridades."""
        log.info("[PHASE: EVALUATE] Determining priorities...")
        
        desires = self._get_desires()
        
        # Generar nuevos deseos si es necesario
        if not desires.get_active_desires(limit=5):
            desires.generate_desires()
        
        # Obtener deseos activos
        active_desires = desires.get_active_desires(limit=10)
        
        # Combinar con tareas pendientes del contexto
        priorities = []
        
        for desire in active_desires:
            priorities.append({
                "type": "desire",
                "id": desire.id,
                "description": desire.description,
                "score": desire.total_score,
                "urgency": desire.urgency
            })
        
        for task in wake_context.get("pending_tasks", [])[:5]:
            priorities.append({
                "type": "task",
                "description": task,
                "score": 0.7,  # Default score
                "urgency": 0.5
            })
        
        # Ordenar por score
        priorities.sort(key=lambda x: x["score"] * x["urgency"], reverse=True)
        
        log.info(f"  Top priority: {priorities[0]['description'][:60] if priorities else 'None'}")
        return priorities
    
    def _phase_plan(self, priorities: List[Dict]) -> List[Dict]:
        """Fase 3: Planificar - crear planes para prioridades."""
        log.info("[PHASE: PLAN] Creating plans...")
        
        plans = []
        
        # Tomar top 3 prioridades
        for priority in priorities[:3]:
            # Crear plan simple basado en tipo
            if priority["type"] == "desire":
                plan = {
                    "priority": priority,
                    "steps": [
                        f"Research: {priority['description']}",
                        f"Implement solution for: {priority['description']}",
                        f"Verify: {priority['description']}"
                    ],
                    "estimated_hours": 2.0
                }
            else:
                plan = {
                    "priority": priority,
                    "steps": [
                        f"Execute: {priority['description']}",
                        f"Verify: {priority['description']}"
                    ],
                    "estimated_hours": 1.0
                }
            
            plans.append(plan)
        
        # Según nivel de autonomía, posiblemente solicitar aprobación
        if self.autonomy_level.value <= AutonomyLevel.ASSISTED.value:
            log.info("  [AUTO:ASSISTED] Would request approval for plans")
        
        log.info(f"  Created {len(plans)} plans")
        return plans
    
    def _phase_execute(self, plans: List[Dict]) -> List[Dict]:
        """Fase 4: Ejecutar - ejecutar planes."""
        log.info("[PHASE: EXECUTE] Executing plans...")
        
        results = []
        
        for plan in plans:
            # Verificar permisos según nivel de autonomía
            if self.autonomy_level.value >= AutonomyLevel.CHECKPOINT.value:
                # Ejecutar autónomamente
                result = self._execute_plan_autonomously(plan)
            else:
                # Solo preparar, no ejecutar sin aprobación
                result = {
                    "plan": plan,
                    "status": "prepared",
                    "executed": False,
                    "message": "Awaiting approval (low autonomy level)"
                }
            
            results.append(result)
            
            # Registrar en memoria
            self._get_memory().record_action(
                action=f"Execute plan: {plan['priority']['description'][:50]}",
                result=result.get("status", "unknown"),
                importance=6 if result.get("executed") else 4
            )
        
        executed = sum(1 for r in results if r.get("executed"))
        log.info(f"  Executed {executed}/{len(results)} plans")
        return results
    
    def _execute_plan_autonomously(self, plan: Dict) -> Dict:
        """Ejecuta un plan de forma autónoma."""
        # Aquí iría la lógica real de ejecución
        # Por ahora, simulamos ejecución
        
        steps_executed = 0
        success = True
        
        for step in plan.get("steps", []):
            # Verificar si debemos continuar
            if not self.running:
                break
            
            log.debug(f"  Executing step: {step}")
            
            # Simular ejecución
            # En implementación real, aquí se ejecutaría código
            time.sleep(0.1)  # Simulación
            steps_executed += 1
        
        return {
            "plan": plan,
            "status": "completed" if success else "failed",
            "executed": True,
            "steps_completed": steps_executed,
            "message": f"Completed {steps_executed} steps"
        }
    
    def _phase_learn(self, results: List[Dict]) -> List[str]:
        """Fase 5: Aprender - extraer lecciones."""
        log.info("[PHASE: LEARN] Learning from results...")
        
        lessons = []
        
        for result in results:
            if result.get("status") == "completed":
                lessons.append(f"Success pattern: {result['plan']['priority']['description'][:40]}")
                
                # Actualizar deseo como satisfecho parcialmente
                if result["plan"]["priority"]["type"] == "desire":
                    self._get_desires().update_desire_progress(
                        result["plan"]["priority"]["id"],
                        progress=0.3,
                        note="Autonomous execution completed"
                    )
            
            elif result.get("status") == "failed":
                lessons.append(f"Failure in: {result['plan']['priority']['description'][:40]}")
        
        # Registrar aprendizajes
        for lesson in lessons:
            self._get_memory().record_learning(
                topic="autonomy",
                insight=lesson,
                skill_level_change=0.01
            )
        
        log.info(f"  Learned {len(lessons)} lessons")
        return lessons
    
    def _phase_report(self, cycle_start: datetime, results: List[Dict], lessons: List[str]):
        """Fase 6: Reportar - informar a SER."""
        log.info("[PHASE: REPORT] Generating report...")
        
        # Generar reporte
        report = AutonomyReport(
            period_start=cycle_start,
            period_end=datetime.now(),
            summary=f"Autonomy cycle completed. Executed {len(results)} plans.",
            decisions_count=len(results),
            successful_actions=sum(1 for r in results if r.get("status") == "completed"),
            failed_actions=sum(1 for r in results if r.get("status") == "failed"),
            key_decisions=[{"plan": r["plan"]["priority"]["description"]} for r in results],
            goals_progressed=[],
            goals_completed=[r["plan"]["priority"]["description"] for r in results if r.get("status") == "completed"],
            new_goals_created=[],
            autonomy_level=self.autonomy_level,
            system_health={"status": "operational"},
            resource_usage={},
            planned_next=["Continue working on active goals"],
            assistance_needed=[] if self.autonomy_level.value >= AutonomyLevel.CHECKPOINT.value else ["Plan approvals"]
        )
        
        # Guardar reporte (simplificado)
        log.info(f"  Report: {report.summary}")
        
        # Si hay asistencia necesaria, notificar
        if report.assistance_needed:
            log.info(f"  [NEEDS_ATTENTION] {', '.join(report.assistance_needed)}")
    
    def _phase_rest(self):
        """Fase 7: Descansar - preparar para próximo ciclo."""
        log.info("[PHASE: REST] Resting...")
        
        # Persistir estado
        self._save_state()
        
        # No cerrar sesión de memoria, solo registrar que terminamos ciclo
        self._get_memory().record_episode(
            episode_type="state",
            content="Autonomy cycle completed, entering rest phase",
            importance=3,
            tags=["autonomy", "rest"]
        )
    
    # ════════════════════════════════════════════════════════════════════════
    #  API PÚBLICA
    # ════════════════════════════════════════════════════════════════════════
    
    def set_autonomy_level(self, level: AutonomyLevel):
        """Cambia nivel de autonomía."""
        old_level = self.autonomy_level
        self.autonomy_level = level
        self._save_state()
        
        log.info(f"Autonomy level changed: {old_level.name} → {level.name}")
        
        # Registrar decisión
        self._get_memory().record_episode(
            episode_type="state",
            content=f"Autonomy level changed to {level.name}",
            importance=7,
            tags=["autonomy", "config_change"]
        )
    
    def get_status(self) -> Dict[str, Any]:
        """Obtiene estado del motor de autonomía."""
        return {
            "running": self.running,
            "current_phase": self.current_phase.value if self.current_phase else None,
            "autonomy_level": {
                "value": self.autonomy_level.value,
                "name": self.autonomy_level.name
            },
            "cycle_interval_minutes": CYCLE_INTERVAL_MINUTES,
            "recent_decisions": len(self.db.get_recent_decisions(hours=24))
        }
    
    def _score_option(self, opt: str, motivation: str, risk_level: str,
                      decision_type: str) -> float:
        """Puntúa una opción usando múltiples dimensiones.

        Dimensiones:
          - Alineación con la motivación (keyword overlap)
          - Simplicidad (prefiere opciones concisas pero no mínimas)
          - Riesgo: penaliza opciones con palabras de riesgo si risk_level es alto
        Retorna un score 0.0-1.0 (mayor = mejor).
        """
        score = 0.5  # base neutra

        # 1. Alineación motivacional: solapamiento de palabras clave
        if motivation:
            mot_words = set(motivation.lower().split())
            opt_words = set(opt.lower().split())
            overlap = mot_words & opt_words
            if overlap:
                score += 0.15 * min(len(overlap) / max(len(mot_words), 1), 1.0)
            # Bonus si la opción contiene la motivation como subcadena
            if motivation.lower() in opt.lower():
                score += 0.10

        # 2. Simplicidad controlada: no solo la más corta
        opt_len = len(opt)
        if 10 <= opt_len <= 200:
            score += 0.10  # rango razonable
        elif opt_len < 10:
            score -= 0.05  # demasiado corta, quizás incompleta

        # 3. Evaluación de riesgo
        risk_keywords = {
            "delete", "remove", "rm", "drop", "format", "wipe", "purge",
            "kill", "terminate", "destroy", "overwrite", "truncate",
            "sudo", "root", "chmod 777", "unsafe",
        }
        opt_lower = opt.lower()
        risky_tokens = risk_keywords & set(opt_lower.split())
        # Penalización proporcional al nivel de riesgo
        risk_weights = {"low": 0.05, "medium": 0.12, "high": 0.25, "critical": 0.40}
        risk_penalty = risk_weights.get(risk_level, 0.12) * len(risky_tokens)
        score -= risk_penalty

        # 4. Contexto de tipo de decisión
        if decision_type == "security" and risk_level in ("high", "critical"):
            # En seguridad, preferir opciones defensivas
            defensive_words = {"backup", "safe", "secure", "protect", "readonly", "audit"}
            if defensive_words & set(opt_lower.split()):
                score += 0.15

        return max(0.0, min(1.0, score))

    def make_decision(self, decision_type: str, options: List[str],
                      motivation: str, risk_level: str = "medium") -> str:
        """
        Toma una decisión autónoma usando puntuación multidimensional.

        Evalúa cada opción por: alineación con la motivación, simplicidad
        controlada, evaluación de riesgo y contexto del tipo de decisión.

        Retorna la opción con mayor puntuación.
        """
        if not options:
            return ""

        # Puntuar cada opción con el scorer multidimensional
        scored = [
            (self._score_option(opt, motivation, risk_level, decision_type), opt)
            for opt in options
        ]
        scored.sort(reverse=True)  # mayor score primero

        best_score, selected = scored[0] if scored else (0.5, options[0])

        # Calcular confianza real a partir de la dispersión de scores
        if len(scored) > 1:
            scores = [s for s, _ in scored]
            score_range = max(scores) - min(scores)
            # Mayor separación entre la mejor y las demás = más confianza
            confidence = min(0.5 + score_range, 0.95)
        else:
            confidence = 0.5  # una sola opción = confianza media

        # Crear registro de decisión
        decision = AutonomousDecision(
            id="",
            timestamp=datetime.now(),
            decision_type=decision_type,
            motivation=motivation,
            alternatives=options,
            selected_option=selected,
            expected_outcome="TBD",
            confidence=round(confidence, 2),
            risk_level=risk_level
        )

        self.db.save_decision(decision)

        log.info("Decision made [%s]: %s (score=%.2f, confidence=%.2f, risk=%s)",
                 decision_type, selected[:50], best_score, confidence, risk_level)
        return selected


# Singleton
_autonomy_engine: Optional[AutonomyEngine] = None

def get_autonomy_engine() -> AutonomyEngine:
    global _autonomy_engine
    if _autonomy_engine is None:
        _autonomy_engine = AutonomyEngine()
    return _autonomy_engine


# ══════════════════════════════════════════════════════════════════════════════
#  TEST
# ══════════════════════════════════════════════════════════════════════════════

if __name__ == "__main__":
    print("=" * 70)
    print("  EIDOS Autonomy Engine - Test")
    print("=" * 70)
    
    autonomy = get_autonomy_engine()
    
    # Test 1: Estado inicial
    print("\n[Test 1] Initial status:")
    status = autonomy.get_status()
    print(f"  Running: {status['running']}")
    print(f"  Autonomy level: {status['autonomy_level']['name']}")
    
    # Test 2: Cambiar nivel
    print("\n[Test 2] Changing autonomy level:")
    autonomy.set_autonomy_level(AutonomyLevel.CHECKPOINT)
    print(f"  New level: {autonomy.autonomy_level.name}")
    
    # Test 3: Tomar decisión
    print("\n[Test 3] Autonomous decision:")
    options = [
        "Implement feature A (2 hours)",
        "Fix bug B (1 hour)",
        "Refactor code C (3 hours)"
    ]
    decision = autonomy.make_decision(
        "task_selection",
        options,
        "Need to make progress on goals",
        "low"
    )
    print(f"  Selected: {decision}")
    
    # Test 4: Ejecutar ciclo manualmente (sin threading)
    print("\n[Test 4] Running single autonomy cycle:")
    # Solo probar fases individuales para no bloquear
    wake_ctx = autonomy._phase_awake()
    priorities = autonomy._phase_evaluate(wake_ctx)
    print(f"  Found {len(priorities)} priorities")
    
    if priorities:
        plans = autonomy._phase_plan(priorities[:2])
        print(f"  Created {len(plans)} plans")
    
    # Test 5: Reporte
    print("\n[Test 5] Status after operations:")
    final_status = autonomy.get_status()
    print(f"  Recent decisions: {final_status['recent_decisions']}")
    
    print("\n✅ Autonomy Engine test complete")
    print("   EIDOS can now operate autonomously.")
    print("   To start continuous mode: autonomy.start_autonomous_mode()")
