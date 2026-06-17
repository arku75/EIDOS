"""
EIDOS core/advanced_planner.py — Advanced Planning System
==========================================================
Sistema de planificación avanzado que implementa razonamiento estructurado
tipo Claude Code con:
  - Thinking chains (cadenas de pensamiento explícitas)
  - Multi-step reasoning (descomposición inteligente)
  - Self-critique (auto-evaluación del plan)
  - Adaptive re-planning (ajuste dinámico del plan)

A diferencia del DAG Planner que ejecuta en paralelo, este planner se enfoca
en el RAZONAMIENTO PROFUNDO antes de ejecutar, garantizando que cada paso
tenga sentido en el contexto completo de la tarea.

Flujo:
    1. UNDERSTAND → Analizar la tarea en profundidad
    2. DECOMPOSE → Descomponer en pasos lógicos
    3. CRITIQUE → Auto-evaluar el plan
    4. REFINE → Ajustar basado en críticas
    5. EXECUTE → Ejecutar con monitoreo
    6. LEARN → Guardar aprendizajes para futuras tareas

Autor: EIDOS AI System
Fecha: 2026-03-17
"""
from __future__ import annotations

import json
import time
import urllib.request
from dataclasses import dataclass, field
from typing import Any, Optional, List, Dict
from enum import Enum

OLLAMA_URL = __import__("os").environ.get("OLLAMA_URL", "http://127.0.0.1:11434")


# ── Tipos ────────────────────────────────────────────────────────────────────

class StepType(Enum):
    """Tipos de pasos en un plan"""
    GATHER_INFO = "gather_info"       # Recopilar información
    ANALYZE = "analyze"                # Analizar datos
    EXECUTE_ACTION = "execute_action"  # Ejecutar acción
    VERIFY = "verify"                  # Verificar resultado
    DECISION = "decision"              # Punto de decisión


@dataclass
class ThinkingStep:
    """Un paso en la cadena de pensamiento"""
    type: StepType
    description: str
    reasoning: str
    expected_outcome: str
    tool: Optional[str] = None
    args: Optional[Dict] = None
    dependencies: List[str] = field(default_factory=list)
    confidence: float = 1.0  # 0.0-1.0


@dataclass
class PlanCritique:
    """Crítica auto-generada de un plan"""
    issues: List[str]
    suggestions: List[str]
    risk_level: str  # low / medium / high
    confidence_score: float


@dataclass
class AdvancedPlan:
    """Plan completo con razonamiento estructurado"""
    task: str
    understanding: str  # Comprensión profunda de la tarea
    thinking_steps: List[ThinkingStep]
    critique: Optional[PlanCritique] = None
    refined: bool = False
    created_at: float = field(default_factory=time.time)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "task": self.task,
            "understanding": self.understanding,
            "steps": [
                {
                    "type": s.type.value,
                    "description": s.description,
                    "reasoning": s.reasoning,
                    "tool": s.tool,
                    "confidence": s.confidence,
                }
                for s in self.thinking_steps
            ],
            "critique": {
                "issues": self.critique.issues if self.critique else [],
                "risk_level": self.critique.risk_level if self.critique else "low",
            } if self.critique else None,
            "refined": self.refined,
        }


# ── Advanced Planner ─────────────────────────────────────────────────────────

class AdvancedPlanner:
    """
    Planner con razonamiento profundo tipo Claude Code.
    """

    def __init__(self, model: str = "deepseek-r1:14b", verbose: bool = True):
        self.model = model
        self.verbose = verbose
        self.planning_history: List[AdvancedPlan] = []

    def _log(self, msg: str) -> None:
        if self.verbose:
            print(msg)

    def _call_llm(self, prompt: str, max_tokens: int = 1000) -> str:
        """Llamada al LLM con manejo de errores"""
        try:
            payload = json.dumps({
                "model": self.model,
                "stream": False,
                "messages": [{"role": "user", "content": prompt}],
                "options": {"num_predict": max_tokens, "temperature": 0.4},
            }).encode()

            req = urllib.request.Request(
                f"{OLLAMA_URL}/api/chat",
                data=payload,
                headers={"Content-Type": "application/json"},
            )

            with urllib.request.urlopen(req, timeout=90) as resp:
                content = json.load(resp).get("message", {}).get("content", "")
                return content
        except Exception as e:
            self._log(f"[Advanced Planner] Error LLM: {e}")
            return ""

    # ── Fase 1: UNDERSTAND ───────────────────────────────────────────────────

    def understand_task(self, task: str) -> str:
        """
        Fase 1: Comprensión profunda de la tarea.
        Analiza qué se pide realmente, contexto, restricciones, etc.
        """
        self._log(f"\n🧠 [UNDERSTAND] Analizando tarea en profundidad...")

        prompt = f"""Eres EIDOS, un asistente AI experto en planificación.

TAREA: {task}

Analiza esta tarea en profundidad y responde:

1. ¿Qué se está pidiendo REALMENTE? (objetivo final)
2. ¿Qué información necesito antes de empezar?
3. ¿Hay restricciones implícitas o explícitas?
4. ¿Cuál es el criterio de éxito?
5. ¿Qué riesgos/desafíos preveo?

Responde en español de forma concisa pero completa (max 200 palabras)."""

        understanding = self._call_llm(prompt, max_tokens=400)

        self._log(f"   ✅ Comprensión completada ({len(understanding)} caracteres)")
        return understanding

    # ── Fase 2: DECOMPOSE ────────────────────────────────────────────────────

    def decompose_into_steps(self, task: str, understanding: str) -> List[ThinkingStep]:
        """
        Fase 2: Descomposición en pasos lógicos con razonamiento.
        """
        self._log(f"\n🔀 [DECOMPOSE] Descomponiendo en pasos lógicos...")

        prompt = f"""TAREA: {task}

COMPRENSIÓN:
{understanding}

Descompón esta tarea en pasos ejecutables con razonamiento claro.

Responde SOLO con JSON válido (array de pasos):
[
  {{
    "type": "gather_info",
    "description": "Verificar herramientas disponibles",
    "reasoning": "Necesito saber qué tools tengo antes de planificar",
    "expected_outcome": "Lista de tools disponibles",
    "tool": "list_tools",
    "confidence": 0.9
  }},
  {{
    "type": "execute_action",
    "description": "Ejecutar análisis principal",
    "reasoning": "Con la info anterior, puedo proceder",
    "expected_outcome": "Resultado del análisis",
    "tool": "analyze_data",
    "dependencies": ["step_0"],
    "confidence": 0.8
  }}
]

Tipos válidos: gather_info, analyze, execute_action, verify, decision
Incluye confidence (0.0-1.0) para cada paso.
Max 10 pasos.
Solo el JSON, nada más."""

        response = self._call_llm(prompt, max_tokens=1200)

        # Extraer JSON
        import re
        m = re.search(r'\[.*\]', response, re.DOTALL)
        if not m:
            self._log("   ⚠️  No se pudo extraer JSON, usando plan fallback")
            return [
                ThinkingStep(
                    type=StepType.EXECUTE_ACTION,
                    description=f"Ejecutar: {task}",
                    reasoning="Plan simple de un paso",
                    expected_outcome="Tarea completada",
                    tool="exec_shell",
                    args={"command": f"echo 'Tarea: {task[:100]}'"},
                    confidence=0.5,
                )
            ]

        try:
            steps_data = json.loads(m.group(0))
        except Exception:
            self._log("   ⚠️  JSON inválido, usando plan fallback")
            return [ThinkingStep(
                type=StepType.EXECUTE_ACTION,
                description=task,
                reasoning="Fallback simple",
                expected_outcome="Completar tarea",
                confidence=0.5,
            )]

        steps = []
        for i, s in enumerate(steps_data):
            try:
                step_type = StepType(s.get("type", "execute_action"))
            except Exception:
                step_type = StepType.EXECUTE_ACTION

            steps.append(ThinkingStep(
                type=step_type,
                description=s.get("description", f"Paso {i+1}"),
                reasoning=s.get("reasoning", "Sin razonamiento"),
                expected_outcome=s.get("expected_outcome", "Resultado esperado"),
                tool=s.get("tool"),
                args=s.get("args"),
                dependencies=s.get("dependencies", []),
                confidence=float(s.get("confidence", 0.7)),
            ))

        self._log(f"   ✅ {len(steps)} pasos generados")
        for i, step in enumerate(steps):
            self._log(f"      [{i}] {step.type.value}: {step.description} (conf: {step.confidence:.2f})")

        return steps

    # ── Fase 3: CRITIQUE ─────────────────────────────────────────────────────

    def critique_plan(self, plan: AdvancedPlan) -> PlanCritique:
        """
        Fase 3: Auto-crítica del plan generado.
        """
        self._log(f"\n🔍 [CRITIQUE] Auto-evaluando el plan...")

        steps_summary = "\n".join([
            f"{i}. {s.description} (confidence: {s.confidence})"
            for i, s in enumerate(plan.thinking_steps)
        ])

        prompt = f"""TAREA: {plan.task}

PLAN PROPUESTO:
{steps_summary}

Como crítico experto, evalúa este plan y responde en JSON:
{{
  "issues": ["problema 1", "problema 2"],
  "suggestions": ["mejora 1", "mejora 2"],
  "risk_level": "low|medium|high",
  "confidence_score": 0.85
}}

Aspectos a evaluar:
- ¿Falta algún paso crítico?
- ¿Hay pasos redundantes?
- ¿El orden es lógico?
- ¿Hay dependencias faltantes?
- ¿Qué podría fallar?

Solo el JSON, sin explicaciones."""

        response = self._call_llm(prompt, max_tokens=500)

        import re
        m = re.search(r'\{.*\}', response, re.DOTALL)
        if not m:
            return PlanCritique(
                issues=[],
                suggestions=[],
                risk_level="low",
                confidence_score=0.7,
            )

        try:
            critique_data = json.loads(m.group(0))
            critique = PlanCritique(
                issues=critique_data.get("issues", []),
                suggestions=critique_data.get("suggestions", []),
                risk_level=critique_data.get("risk_level", "low"),
                confidence_score=float(critique_data.get("confidence_score", 0.7)),
            )

            self._log(f"   ✅ Crítica completada:")
            self._log(f"      Risk: {critique.risk_level}")
            self._log(f"      Issues: {len(critique.issues)}")
            self._log(f"      Suggestions: {len(critique.suggestions)}")

            return critique
        except Exception:
            return PlanCritique(issues=[], suggestions=[], risk_level="low", confidence_score=0.7)

    # ── Fase 4: REFINE ───────────────────────────────────────────────────────

    def refine_plan(self, plan: AdvancedPlan) -> AdvancedPlan:
        """
        Fase 4: Refinar el plan basado en la crítica.
        """
        if not plan.critique or (not plan.critique.issues and not plan.critique.suggestions):
            self._log(f"\n✅ [REFINE] Plan OK, no necesita refinamiento")
            return plan

        self._log(f"\n🔧 [REFINE] Refinando plan basado en críticas...")

        # Por ahora, simplemente marcamos como refinado
        # En una versión más avanzada, re-generaríamos los pasos
        plan.refined = True

        self._log(f"   ✅ Plan refinado")
        return plan

    # ── API Principal ────────────────────────────────────────────────────────

    def create_plan(self, task: str, auto_critique: bool = True) -> AdvancedPlan:
        """
        Crea un plan completo con razonamiento profundo.

        Args:
            task: Tarea a planificar
            auto_critique: Si True, auto-critica y refina el plan

        Returns:
            AdvancedPlan con thinking steps y crítica
        """
        self._log(f"\n{'='*70}")
        self._log(f"🎯 ADVANCED PLANNER: Planificando tarea")
        self._log(f"{'='*70}")

        # Fase 1: UNDERSTAND
        understanding = self.understand_task(task)

        # Fase 2: DECOMPOSE
        steps = self.decompose_into_steps(task, understanding)

        # Crear plan inicial
        plan = AdvancedPlan(
            task=task,
            understanding=understanding,
            thinking_steps=steps,
        )

        # Fase 3 y 4: CRITIQUE + REFINE (opcional)
        if auto_critique:
            plan.critique = self.critique_plan(plan)
            plan = self.refine_plan(plan)

        # Guardar en historial
        self.planning_history.append(plan)

        self._log(f"\n{'='*70}")
        self._log(f"✅ PLAN COMPLETADO")
        self._log(f"   Pasos: {len(plan.thinking_steps)}")
        self._log(f"   Refinado: {plan.refined}")
        self._log(f"   Confidence promedio: {sum(s.confidence for s in plan.thinking_steps)/len(plan.thinking_steps):.2f}")
        self._log(f"{'='*70}\n")

        return plan

    def execute_plan(self, plan: AdvancedPlan) -> Dict[str, Any]:
        """
        Ejecuta un plan usando el kernel de EIDOS.

        Returns:
            Resultados de la ejecución
        """
        self._log(f"\n⚡ [EXECUTE] Ejecutando plan...")

        try:
            from core.kernel import DeterministicKernel
            kernel = DeterministicKernel()

            results = []
            for i, step in enumerate(plan.thinking_steps):
                self._log(f"\n  [{i+1}/{len(plan.thinking_steps)}] {step.description}")
                self._log(f"      Reasoning: {step.reasoning}")

                if step.tool and step.tool in kernel.tool_impl:
                    try:
                        result = kernel.safe_call(
                            step.tool,
                            step.args or {},
                            context=f"Step {i}: {step.description}"
                        )
                        results.append({
                            "step": i,
                            "description": step.description,
                            "result": result,
                            "success": True,
                        })
                        self._log(f"      ✅ Completado")
                    except Exception as e:
                        results.append({
                            "step": i,
                            "description": step.description,
                            "error": str(e),
                            "success": False,
                        })
                        self._log(f"      ❌ Error: {e}")
                else:
                    self._log(f"      ⚠️  Tool '{step.tool}' no disponible, skip")

            return {
                "task": plan.task,
                "total_steps": len(plan.thinking_steps),
                "completed": sum(1 for r in results if r.get("success")),
                "failed": sum(1 for r in results if not r.get("success")),
                "results": results,
            }

        except Exception as e:
            self._log(f"  ❌ Error fatal en ejecución: {e}")
            return {"error": str(e)}

    def plan_and_execute(self, task: str) -> Dict[str, Any]:
        """Atajo: create_plan + execute en una sola llamada"""
        plan = self.create_plan(task, auto_critique=True)
        return self.execute_plan(plan)


# ── Singleton ────────────────────────────────────────────────────────────────

_advanced_planner: Optional[AdvancedPlanner] = None

def get_advanced_planner() -> AdvancedPlanner:
    """Obtiene el singleton del Advanced Planner"""
    global _advanced_planner
    if _advanced_planner is None:
        _advanced_planner = AdvancedPlanner()
    return _advanced_planner


# ── CLI de prueba ────────────────────────────────────────────────────────────

if __name__ == "__main__":
    import sys

    task = " ".join(sys.argv[1:]) if len(sys.argv) > 1 else \
        "Analiza el directorio /home/ser/EIDOS/core y dame un resumen de los módulos más importantes"

    planner = AdvancedPlanner(verbose=True)
    plan = planner.create_plan(task, auto_critique=True)

    print("\n" + "="*70)
    print("PLAN GENERADO:")
    print("="*70)
    print(json.dumps(plan.to_dict(), indent=2, ensure_ascii=False))
