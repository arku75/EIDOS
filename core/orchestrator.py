#!/usr/bin/env python3
"""
EIDOS core/orchestrator.py — Meta-Orquestación Fénix (Fase 7+)
================================================================
FUSIÓN SOCIO1 + SOCIO2
Versión mejorada y adaptada para EIDOS con:
- Memoria a largo plazo (ChromaDB)
- Planner con recuerdo histórico (Qwen2.5)
- Executor guiado por el motor nativo DeterministicKernel
- Visión integrada
- Logging profesional

Arquitectura:
  Usuario → Planner (memoria) → Plan → Executor (DeterministicKernel) → ResultCollector → Memoria
"""

from __future__ import annotations
import json
import time
import logging
import urllib.request
import re
from dataclasses import dataclass, field, asdict
from typing import Any, Dict, List, Optional, Tuple

# Dependencias externas (ChromaDB)
try:
    import chromadb
    from chromadb.utils import embedding_functions
    HAS_CHROMADB = True
except ImportError:
    HAS_CHROMADB = False

# -----------------------------------------------------------------------------
# CONFIGURACIÓN CENTRALIZADA
# -----------------------------------------------------------------------------
class Config:
    OLLAMA_URL = "http://localhost:11434"
    FAST_MODEL = "lfm2.5-thinking:1.2b"          # Planner
    TOOL_MODEL = "deepseek-r1:14b"  # Executor (tool calling - via Kernel)
    VISION_MODEL = "moondream2"           # Visión
    CHROMA_DIR = "/home/ser/EIDOS/memoria_fenix"
    LOG_LEVEL = logging.INFO
    MAX_PLAN_SUBTASKS = 6
    MAX_ORCHESTRATOR_ITERATIONS = 20
    ENABLE_AUTO_EVOLUTION = True

# -----------------------------------------------------------------------------
# MEMORIA A LARGO PLAZO (ChromaDB)
# -----------------------------------------------------------------------------
class MemoriaLargoPlazo:
    """Almacena experiencias (objetivos, planes, resultados) para aprendizaje continuo."""
    
    def __init__(self, persist_dir: str = Config.CHROMA_DIR):
        self.logger = logging.getLogger("MemoriaFenix")
        self.client = None
        self.collection = None
        if not HAS_CHROMADB:
            self.logger.warning("ChromaDB no instalado. La memoria será /dev/null")
            return
        try:
            self.client = chromadb.PersistentClient(path=persist_dir)
            self.embedding_fn = embedding_functions.SentenceTransformerEmbeddingFunction(
                model_name="all-MiniLM-L6-v2"
            )
            self.collection = self.client.get_or_create_collection(
                name="experiencias_fenix",
                embedding_function=self.embedding_fn
            )
            self.logger.info(f"Memoria conectada en {persist_dir}")
        except Exception as e:
            self.logger.error(f"Error inicializando ChromaDB: {e}. La memoria será /dev/null")
            self.client = None
    
    def guardar_experiencia(self, texto: str, metadata: Dict[str, Any] = None):
        if not self.client: return
        doc_id = f"exp_{time.time_ns()}"
        try:
            self.collection.add(
                documents=[texto],
                metadatas=[metadata or {}],
                ids=[doc_id]
            )
            self.logger.debug(f"Experiencia guardada: {doc_id}")
        except Exception as e:
            self.logger.error(f"Fallo al guardar experiencia: {e}")
    
    def recuperar_similares(self, consulta: str, n: int = 3) -> List[str]:
        if not self.client: return []
        try:
            resultados = self.collection.query(query_texts=[consulta], n_results=n)
            return resultados['documents'][0] if resultados['documents'] else []  # pyre-ignore[arg-type]
        except Exception as e:
            self.logger.error(f"Fallo al recuperar experiencias: {e}")
            return []

# -----------------------------------------------------------------------------
# MODELOS DE DATOS
# -----------------------------------------------------------------------------
@dataclass
class SubTask:
    id: str
    description: str
    tools_hint: List[str] = field(default_factory=list)
    depends_on: List[str] = field(default_factory=list)
    result: Optional[str] = None
    status: str = "PENDING"  # PENDING, RUNNING, DONE, FAILED
    error_count: int = 0

@dataclass
class Plan:
    goal: str
    subtasks: List[SubTask] = field(default_factory=list)
    created_at: float = field(default_factory=time.time)
    metadata: Dict = field(default_factory=dict)
    
    def next_runnable(self) -> Optional[SubTask]:
        done_ids = {t.id for t in self.subtasks if t.status == "DONE"}
        for task in self.subtasks:
            if task.status == "PENDING":
                if all(dep in done_ids for dep in task.depends_on):
                    return task
        return None
    
    def is_complete(self) -> bool:
        return all(t.status in ("DONE", "FAILED") for t in self.subtasks)
    
    def summary(self) -> str:
        done = sum(1 for t in self.subtasks if t.status == "DONE")
        failed = sum(1 for t in self.subtasks if t.status == "FAILED")
        return f"{done} completadas, {failed} fallidas, {len(self.subtasks)-done-failed} pendientes"

# -----------------------------------------------------------------------------
# PLANNER AGENT (con memoria SOCIO1)
# -----------------------------------------------------------------------------
class PlannerAgent:
    """
    Descompone un objetivo en subtareas, apoyándose en experiencias pasadas recuperadas vectorialmente.
    """
    
    SYSTEM_PROMPT = """Eres el planificador maestro de EIDOS. Debes descomponer un objetivo en subtareas.

Reglas INQUEBRANTABLES:
- Máximo {max_subtasks} subtareas.
- Cada subtarea debe ser muy descriptiva (qué hacer exactamente).
- Especifica las tools prioritarias en "tools_hint".
- Define dependencias ("depends_on") usando los IDs para tareas que requieren el resultado anterior.
- Responde EXCLUSIVAMENTE con un código JSON válido basado en el *Objetivo de Misión* del usuario (NUNCA copies los ejemplos), sin markdown fuera del bloque, con este exacto formato:
{{
  "subtasks": [
    {{"id": "1", "description": "EJEMPLO: Accion 1", "tools": ["exec_shell"], "depends_on": []}},
    {{"id": "2", "description": "EJEMPLO: Accion 2", "tools": ["exec_shell"], "depends_on": ["1"]}}
  ]
}}

Contexto útil de experiencias pasadas similares (para aprender de los errores y aciertos):
{experiencias}
"""
    
    def __init__(self, memoria: MemoriaLargoPlazo):
        self.memoria = memoria
        self.logger = logging.getLogger("Fenix.Planner")
    
    def plan(self, goal: str, context: str = "") -> Plan:
        experiencias = self.memoria.recuperar_similares(goal, n=2)
        experiencias_text = "\n".join(f"- {exp[:300]}" for exp in experiencias) if experiencias else "Ninguna previa."  # pyre-ignore[arg-type]
        
        system = self.SYSTEM_PROMPT.format(
            max_subtasks=Config.MAX_PLAN_SUBTASKS,
            experiencias=experiencias_text
        )
        user = f"Objetivo de Misión: {goal}\nContexto: {context}"
        
        payload = json.dumps({
            "model": Config.FAST_MODEL,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user}
            ],
            "stream": False,
            "options": {"temperature": 0.1, "num_predict": 1000}
        }).encode()
        
        plan = Plan(goal=goal)
        try:
            req = urllib.request.Request(
                f"{Config.OLLAMA_URL}/api/chat",
                data=payload,
                headers={"Content-Type": "application/json"}
            )
            with urllib.request.urlopen(req, timeout=90) as resp:
                content = json.load(resp).get("message", {}).get("content", "")
            
            json_match = re.search(r'\{.*\}', content, re.DOTALL)
            if json_match:
                parsed = json.loads(json_match.group())
                for raw in parsed.get("subtasks", []):
                    plan.subtasks.append(SubTask(
                        id=str(raw["id"]),
                        description=raw["description"],
                        tools_hint=raw.get("tools", []),
                        depends_on=[str(d) for d in raw.get("depends_on", [])]
                    ))
            else:
                self.logger.warning("No JSON from LLM. Raw content: " + content[:200])  # pyre-ignore[arg-type]
                raise ValueError("No se encontró JSON en la respuesta")
                
            self.logger.info(f"Fénix Plan generado con {len(plan.subtasks)} subtareas.")
        except Exception as e:
            self.logger.error(f"Fallo en Planner ({e}). Fallback a plan de bloque único.")
            plan.subtasks.append(SubTask(id="1", description=goal, tools_hint=["exec_shell"]))
            
        plan.metadata["experiencias_usadas"] = experiencias
        return plan

# -----------------------------------------------------------------------------
# EXECUTOR AGENT (Hibridado SOCIO2 DeterministicKernel)
# -----------------------------------------------------------------------------
class ExecutorAgent:
    """
    Delega de forma robusta la ejecución de la subtarea al DeterministicKernel de EIDOS,
    que maneja sus propios iteradores (ReAct), fallos de shell y protección visual.
    """
    def __init__(self):
        # Inicializamos en lazy o importamos el robusto núcleo local
        from core.kernel import DeterministicKernel
        self._kernel = DeterministicKernel()
        self.logger = logging.getLogger("Fenix.Executor")
    
    def execute(self, subtask: SubTask, contexto: Dict[str, str] = None) -> str:
        subtask.status = "RUNNING"
        self.logger.info(f"→ Inicia Subtarea [{subtask.id}]: {subtask.description[:80]}")  # pyre-ignore[arg-type]
        
        # Le inyectamos memoria local de orquestación al Kernel
        history = []
        if contexto:
            contexto_str = "\n".join(f"[Resultado previo subtarea {k}]: {v[:300]}" for k, v in contexto.items())  # pyre-ignore[arg-type]
            history.append({
                "role": "system",
                "content": f"El planificador descompuso la tarea mayor. Aquí tienes resultados PREVIOS relevantes. Úsalos si tu tarea depende de ellos:\n{contexto_str}"
            })
            
        try:
            # DeterministicKernel maneja iteraciones nativas ReAct con Hermes3
            # y tiene validaciones de bloque ciego.
            resultado = self._kernel.run(
                task=subtask.description,
                history=history if history else None,
                max_iterations=6  # Cada subtarea puede iterar hasta 6 veces
            )
            subtask.result = resultado
            subtask.status = "DONE"
            self.logger.info(f"✓ Subtarea [{subtask.id}] completada")
            return resultado
        except Exception as e:
            error_msg = f"[Fenix Exec Error]: {str(e)}"
            self.logger.error(error_msg)
            subtask.result = error_msg
            subtask.status = "FAILED"
            subtask.error_count += 1
            return error_msg

# -----------------------------------------------------------------------------
# VISION AGENT (Mantenido para el orchestrator pre-check)
# -----------------------------------------------------------------------------
class VisionAgent:
    """Wrapper para invocación rápida del módulo visual entre subtareas si es requerido"""
    def __init__(self):
        self.logger = logging.getLogger("Fenix.Vision")
        
    def describe(self, pregunta: str = "¿Qué ves en pantalla?") -> str:
        try:
            from core.optimizer import VisionPipelineOptimizer
            opt = VisionPipelineOptimizer()
            res = opt.analyze(pregunta, force_deep=False)
            return res
        except Exception as e:
            self.logger.error(f"Error de VisionAgent: {e}")
            return f"Error visual: {e}"

# -----------------------------------------------------------------------------
# MASTER ORCHESTRATOR FÉNIX
# -----------------------------------------------------------------------------
class MasterOrchestrator:
    """
    Simbiosis Total:
    - Planeamiento e historia a cargo de la arquitectura SOCIO1 (Fénix)
    - Ejecución a cargo de la arquitectura SOCIO2 (Kernel)
    """
    
    def __init__(self):
        logging.basicConfig(
            level=Config.LOG_LEVEL,
            format='%(asctime)s | %(name)s | %(message)s',
            handlers=[logging.StreamHandler()]
        )
        self.logger = logging.getLogger("Fenix.Orchestrator")
        
        self.memoria = MemoriaLargoPlazo()
        self.planner = PlannerAgent(self.memoria)
        self.executor = ExecutorAgent()
        self.vision = VisionAgent()
        
    def run(self, goal: str, context: str = "", use_vision: bool = False) -> str:
        self.logger.info(f"=== INICIANDO MASTER ORCHESTRATOR FENIX ===")
        self.logger.info(f"🎯 Meta principal: {goal}")
        t0 = time.time()
        
        plan = self.planner.plan(goal, context)
        if not plan.subtasks:
            return "[ORCHESTRATOR FÉNIX] Fallo crítico: No se generó flujo de ejecución."
            
        results: Dict[str, str] = {}
        iteration = 0
        
        while not plan.is_complete() and iteration < Config.MAX_ORCHESTRATOR_ITERATIONS:
            iteration += 1
            subtask = plan.next_runnable()
            if not subtask:
                self.logger.warning("Fallo lógico: El plan no está completo pero no hay tareas ejecutables. (Dependencia rota o circular)")
                break
                
            # Cross-Verificación Visual opcional entre saltos del plan
            if use_vision and any(t in subtask.tools_hint for t in ["mouse_click", "keyboard_type", "dom_click"]):
                self.logger.info(f"Ojo de Fénix observando antes de {subtask.id}...")
                vision_state = self.vision.describe(f"Verificar estado de UI antes de interactuar.")
                self.logger.info(f"👁️: {vision_state[:100]}...")  # pyre-ignore[arg-type]
                
            resultado = self.executor.execute(subtask, contexto=results)
            results[subtask.id] = resultado
            
            # Simple retry para tareas fallidas
            if subtask.status == "FAILED" and subtask.error_count < 2:
                self.logger.warning(f"Reintentando subtarea {subtask.id}...")
                time.sleep(2)
                resultado = self.executor.execute(subtask, contexto=results)
                results[subtask.id] = resultado
                
            # Si sigue fallando, invocar mutación genética (Self-Skill Creator)
            if subtask.status == "FAILED":
                self.logger.error(f"Fallo irrecuperable en {subtask.id}. Invocando Meta-Evolución...")
                self._trigger_evolution(subtask.description)
                
        # Construcción visual final y volcado en ChromaDB
        final_answer = self._build_response(goal, plan, results)
        
        self.memoria.guardar_experiencia(
            texto=f"META: {goal}\nPLAN RESULTANTE: {plan.summary()}\nLOG FINAL: {final_answer[:800]}",  # pyre-ignore[arg-type]
            metadata={
                "tipo": "fenix_orchestration",
                "duracion_segundos": time.time() - t0,
                "exito": plan.is_complete() and all(t.status=="DONE" for t in plan.subtasks)
            }
        )
        
        self.logger.info(f"=== FÉNIX CICLO COMPLETADO en {time.time()-t0:.1f}s ===")
        return final_answer
        
    def _trigger_evolution(self, missing_capability: str):
        self.logger.info(f"🧬 Iniciando mutación para suplir: {missing_capability}")
        try:
            from core.skill_registry import SkillRegistry
            registry = SkillRegistry()
            name_slug = "".join(c if c.isalnum() else "_" for c in missing_capability.split()[0].lower()[:10]) + f"_{int(time.time()%1000)}"  # pyre-ignore[arg-type]
            nombre_archivo = registry.create_skill(
                skill_name=name_slug,
                description=missing_capability,
                save_dir="/home/ser/EIDOS/skills/plugins"
            )
            self.logger.info(f"🧬 Fénix ha sintetizado un nuevo skill: {nombre_archivo}")
        except Exception as e:
            self.logger.error(f"Fallo en Meta-Evolución: {e}")
    
    def _build_response(self, goal: str, plan: Plan, results: Dict[str, str]) -> str:
        lines = [f"🔥 FÉNIX RESULT: {goal}", f"📊 {plan.summary()}", ""]
        for task in plan.subtasks:
            icon = "✅" if task.status == "DONE" else "❌" if task.status == "FAILED" else "⏳"
            lines.append(f"{icon} [{task.id}] {task.description}")
            if task.result:
                res = task.result[:500] + "..." if len(task.result) > 500 else task.result  # pyre-ignore[arg-type]
                lines.append(f"   ↳ {res.strip()}")
        return "\n".join(lines)

master_orchestrator = MasterOrchestrator()
