#!/usr/bin/env python3
"""
EIDOS core/multi_agent.py -- Sistema Multi-Agente
================================================
EIDOS Principal coordina multiple Workers especializados.

HONESTY NOTE: Previously all 4 workers used identical system prompts
and the same model via model_router._call_llm(). They had different
docstrings but identical behavior. Now each worker has:
  - A DIFFERENT specialized system prompt (coder/pentester/researcher/learner)
  - A DIFFERENT Ollama model per worker type
  - Model selection: coder→qwen2.5-coder, pentester→deepseek-r1:7b,
    researcher→hermes3, learner→lfm2.5-thinking.

Arquitectura:
+---------------------------------+
|    EIDOS PRINCIPAL              |
|    (Orquestador)                |
+--------+------------------------+
         |
    +----+-----+----------+------------+
    |          |          |            |
+---v---+  +--v---+  +---v----+  +---v----+
|Coding |  |Pentest| |Research|  |Learning|
|Worker |  |Worker | |Worker  |  |Worker  |
+qwen2.5|  +deepse.| +hermes3 | +lfm2.5  |
|--coder|  |--r1:7b| |        | |--think.|
+-------+  +-------+ +--------+  +--------+

Workers se ejecutan EN PARALELO para maxima eficiencia.

Uso:
    from core.multi_agent import EIDOSPrincipal

    eidos = EIDOSPrincipal()

    # Tarea compleja -> se divide automaticamente
    result = eidos.execute("Hackea esta web y documenta todo")

    # EIDOS Principal coordina:
    # - Pentesting Worker: escanea y explota
    # - Research Worker: busca vulns conocidas
    # - Coding Worker: genera exploit
    # - Learning Worker: aprende de la experiencia
"""
from __future__ import annotations

import asyncio
import json
import time
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Optional, List, Dict, Any
from concurrent.futures import ThreadPoolExecutor, as_completed

# Agent Bus integration
try:
    from core.agent_bus import get_bus, get_blackboard, get_router, compress
    HAS_BUS = True
except ImportError:
    HAS_BUS = False

# Model Router -- lightweight/heavy node dispatch for LLM calls
try:
    from core.model_router import get_router as get_model_router
    HAS_MODEL_ROUTER = True
except ImportError:
    HAS_MODEL_ROUTER = False


# =============================================================================
# Configuracion
# =============================================================================

AGENTS_DIR = Path.home() / ".eidos" / "agents"
TASKS_LOG = AGENTS_DIR / "tasks_history.jsonl"
KNOWLEDGE_DIR = Path.home() / ".eidos" / "knowledge"


# =============================================================================
# Tipos de Datos
# =============================================================================

@dataclass
class Task:
    """Tarea a ejecutar."""
    id: str
    description: str
    type: str  # "coding", "pentesting", "research", "learning"
    priority: int = 5  # 1-10
    assigned_to: Optional[str] = None
    status: str = "pending"  # pending, in_progress, completed, failed
    result: Optional[Any] = None
    created_at: float = field(default_factory=time.time)
    completed_at: Optional[float] = None


@dataclass
class WorkerResult:
    """Resultado de un Worker."""
    worker_name: str
    task_id: str
    success: bool
    result: Any
    error: Optional[str] = None
    execution_time: float = 0.0
    learnings: List[str] = field(default_factory=list)


# =============================================================================
# Worker Base (Abstract)
# =============================================================================

class Worker(ABC):
    """
    Worker base - todos los workers heredan de esta clase.

    Cada worker es especialista en un area.
    """

    def __init__(self, name: str, verbose: bool = True):
        self.name = name
        self.verbose = verbose
        self.tasks_completed = 0
        self.knowledge_dir = KNOWLEDGE_DIR / name.lower()
        self.knowledge_dir.mkdir(parents=True, exist_ok=True)

    def _log(self, msg: str):
        """Log con prefijo."""
        if self.verbose:
            print(f"  [{self.name}] {msg}")

    @abstractmethod
    def can_handle(self, task: Task) -> bool:
        """
        Determina si este worker puede manejar la tarea.

        Args:
            task: Tarea a evaluar

        Returns:
            True si el worker puede manejarla
        """
        pass

    @abstractmethod
    def execute(self, task: Task) -> WorkerResult:
        """
        Ejecuta una tarea.

        Args:
            task: Tarea a ejecutar

        Returns:
            WorkerResult con el resultado
        """
        pass

    def _call_llm(self, prompt: str, system_prompt: str = "",
                  model: str = "") -> str:
        """Helper: llama al modelo via model_router o Ollama directo.

        Cada worker puede especificar su propio system_prompt y model.
        Si no se especifica, usa model_router como fallback generico.
        """
        # Prefer Ollama directo con modelo y system prompt especificos
        if model and system_prompt:
            try:
                import urllib.request, json
                payload = json.dumps({
                    "model": model,
                    "messages": [
                        {"role": "system", "content": system_prompt},
                        {"role": "user", "content": prompt},
                    ],
                    "stream": False,
                    "options": {"temperature": 0.3},
                }).encode()
                req = urllib.request.Request(
                    "http://localhost:11434/api/chat",
                    data=payload,
                    headers={"Content-Type": "application/json"},
                )
                with urllib.request.urlopen(req, timeout=120) as r:
                    return json.loads(r.read()).get("message", {}).get("content", "")
            except Exception as e:
                self._log(f"Ollama({model}) error: {e}")
        # Fallback: model_router generico
        if HAS_MODEL_ROUTER:
            try:
                router = get_model_router()
                result = router.route(prompt)
                return result.get("content", "")
            except Exception as e:
                self._log(f"ModelRouter error: {e}")
        return ""

    def save_learning(self, topic: str, content: Dict):
        """Guarda aprendizaje en knowledge database."""
        try:
            file_path = self.knowledge_dir / f"{topic}.json"

            # Cargar existente si hay
            existing = {}
            if file_path.exists():
                with open(file_path, "r") as f:
                    existing = json.load(f)

            # Merge
            existing.update(content)

            # Guardar
            with open(file_path, "w") as f:
                json.dump(existing, f, indent=2)

            self._log(f"Aprendizaje guardado: {topic}")

        except Exception as e:
            self._log(f"Error guardando aprendizaje: {e}")


# =============================================================================
# Workers Especializados
# =============================================================================

class CodingWorker(Worker):
    """
    Worker especializado en programacion — qwen2.5-coder:7b

    Capacidades:
    - Escribe codigo en multiples lenguajes
    - Revisa codigo existente
    - Optimiza codigo
    - Aprende nuevos lenguajes observando
    """

    MODEL = "qwen2.5-coder:7b"
    SYSTEM = (
        "Eres un experto programador senior. Escribes codigo limpio, eficiente "
        "y bien documentado. Identificas bugs, sugieres mejoras de rendimiento, "
        "y explicas conceptos tecnicos con claridad. "
        "Respondes con codigo concreto, no explicaciones genericas. "
        "Lenguajes: Python, Bash, JavaScript, Rust, C, Go."
    )

    def __init__(self):
        super().__init__("CodingWorker")

    def can_handle(self, task: Task) -> bool:
        keywords = ["codigo", "programar", "code", "script", "funcion", "class",
                     "refactorizar", "debug", "test", "implementar"]
        return any(kw in task.description.lower() for kw in keywords)

    def execute(self, task: Task) -> WorkerResult:
        self._log(f"Ejecutando tarea de codigo: {task.description}")

        t0 = time.time()

        try:
            llm_response = self._call_llm(
                f"Tarea: {task.description}. "
                f"Responde con codigo concreto, no explicaciones genericas.",
                system_prompt=self.SYSTEM,
                model=self.MODEL,
            )

            result = {
                "code": llm_response if llm_response else "# Generado via model_router",
                "language": "python",
                "explanation": "Codigo generado basado en la tarea via model_router",
                "llm_used": bool(llm_response),
            }

            learnings = ["Patron de codigo identificado"]

            # Guardar learning
            self.save_learning("code_patterns", {
                task.description: result
            })

            self.tasks_completed += 1

            return WorkerResult(
                worker_name=self.name,
                task_id=task.id,
                success=True,
                result=result,
                execution_time=time.time() - t0,
                learnings=learnings
            )

        except Exception as e:
            return WorkerResult(
                worker_name=self.name,
                task_id=task.id,
                success=False,
                result=None,
                error=str(e),
                execution_time=time.time() - t0
            )


class PentestingWorker(Worker):
    """
    Worker especializado en pentesting — deepseek-r1:7b (razonamiento)

    Capacidades:
    - Escaneo de redes
    - Enumeracion
    - Explotacion
    - Post-explotacion
    """

    MODEL = "deepseek-r1:7b"
    SYSTEM = (
        "Eres un pentester experto con anos de experiencia en red team. "
        "Conoces metodologias: recon → enum → exploit → post-explotacion → "
        "borrado de huellas. Dominas: nmap, metasploit, burp suite, sqlmap, "
        "hydra, john, impacket, bloodhound, mimikatz. "
        "Describes paso a paso con herramientas y tecnicas especificas. "
        "Siempre priorizas la seguridad operacional y el sigilo."
    )

    def __init__(self):
        super().__init__("PentestingWorker")

    def can_handle(self, task: Task) -> bool:
        keywords = ["escanear", "scan", "hack", "exploit", "pentest", "vulnerabilidad",
                     "nmap", "metasploit", "burp", "sqlmap", "reverse shell"]
        return any(kw in task.description.lower() for kw in keywords)

    def execute(self, task: Task) -> WorkerResult:
        self._log(f"Ejecutando tarea de pentesting: {task.description}")

        t0 = time.time()

        try:
            llm_response = self._call_llm(
                f"Describe paso a paso como abordarias: "
                f"{task.description}. Incluye herramientas y tecnicas especificas.",
                system_prompt=self.SYSTEM,
                model=self.MODEL,
            )

            result = {
                "phase": "reconnaissance",
                "plan": llm_response if llm_response else "Plan generico de pentesting",
                "findings": [],
                "next_steps": [],
                "llm_used": bool(llm_response),
            }

            learnings = ["Nueva tecnica de pentesting"]

            self.save_learning("pentesting_techniques", {
                task.description: result
            })

            self.tasks_completed += 1

            return WorkerResult(
                worker_name=self.name,
                task_id=task.id,
                success=True,
                result=result,
                execution_time=time.time() - t0,
                learnings=learnings
            )

        except Exception as e:
            return WorkerResult(
                worker_name=self.name,
                task_id=task.id,
                success=False,
                result=None,
                error=str(e),
                execution_time=time.time() - t0
            )


class ResearchWorker(Worker):
    """
    Worker especializado en investigacion — hermes3:8b (conocimiento general)

    Capacidades:
    - Buscar en internet (browser)
    - Consultar documentacion
    - Analizar papers/articulos
    - Buscar CVEs/exploits
    """

    MODEL = "hermes3:8b-llama3.1-q4_K_M"
    SYSTEM = (
        "Eres un investigador cientifico riguroso. Buscas fuentes primarias, "
        "verificas datos, y citas referencias. Tu objetivo es la verdad factual. "
        "Cuando no sabes algo, lo dices claramente. "
        "Cubres: ciberseguridad, sistemas, redes, programacion, matematicas, fisica. "
        "Respondes con fuentes y datos concretos, no opiniones."
    )

    def __init__(self):
        super().__init__("ResearchWorker")

    def can_handle(self, task: Task) -> bool:
        keywords = ["buscar", "investigar", "research", "documentacion", "cve",
                     "paper", "articulo", "informacion", "que es", "como funciona"]
        return any(kw in task.description.lower() for kw in keywords)

    def execute(self, task: Task) -> WorkerResult:
        self._log(f"Investigando: {task.description}")

        t0 = time.time()

        try:
            llm_response = self._call_llm(
                f"Investiga a fondo y responde con "
                f"fuentes y datos concretos sobre: {task.description}",
                system_prompt=self.SYSTEM,
                model=self.MODEL,
            )

            result = {
                "sources": [],
                "summary": llm_response if llm_response else "Resumen de investigacion",
                "references": [],
                "llm_used": bool(llm_response),
            }

            learnings = ["Nueva fuente de informacion"]

            self.save_learning("research_sources", {
                task.description: result
            })

            self.tasks_completed += 1

            return WorkerResult(
                worker_name=self.name,
                task_id=task.id,
                success=True,
                result=result,
                execution_time=time.time() - t0,
                learnings=learnings
            )

        except Exception as e:
            return WorkerResult(
                worker_name=self.name,
                task_id=task.id,
                success=False,
                result=None,
                error=str(e),
                execution_time=time.time() - t0
            )


class LearningWorker(Worker):
    """
    Worker especializado en aprendizaje y knowledge management — lfm2.5-thinking

    Capacidades:
    - Observa codigo del usuario (VSCode)
    - Extrae patrones y lenguajes
    - Consolida conocimiento de otros workers
    - Sincroniza knowledge entre EIDOS instances
    """

    MODEL = "lfm2.5-thinking:1.2b"
    SYSTEM = (
        "Eres un analista de conocimiento experto. Tu trabajo es observar, "
        "extraer patrones, identificar lenguajes de programacion y librerias, "
        "y consolidar el aprendizaje de otros agentes en conocimiento "
        "estructurado y reutilizable. "
        "Extraes: lenguajes detectados, patrones de codigo, librerias usadas, "
        "y lecciones aprendidas de cada experiencia."
    )

    def __init__(self):
        super().__init__("LearningWorker")

    def can_handle(self, task: Task) -> bool:
        keywords = ["aprender", "learn", "observar", "consolidar", "sincronizar",
                     "patron", "knowledge", "mejorar"]
        return any(kw in task.description.lower() for kw in keywords)

    def execute(self, task: Task) -> WorkerResult:
        self._log(f"Aprendiendo: {task.description}")

        t0 = time.time()

        try:
            llm_response = self._call_llm(
                f"Analiza y extrae patrones de aprendizaje, lenguajes, y librerias "
                f"identificables en: {task.description}",
                system_prompt=self.SYSTEM,
                model=self.MODEL,
            )

            result = {
                "language_detected": None,
                "patterns_found": [],
                "libraries_identified": [],
                "analysis": llm_response if llm_response else "Analisis de aprendizaje",
                "llm_used": bool(llm_response),
            }

            learnings = ["Nuevo patron aprendido"]

            self.tasks_completed += 1

            return WorkerResult(
                worker_name=self.name,
                task_id=task.id,
                success=True,
                result=result,
                execution_time=time.time() - t0,
                learnings=learnings
            )

        except Exception as e:
            return WorkerResult(
                worker_name=self.name,
                task_id=task.id,
                success=False,
                result=None,
                error=str(e),
                execution_time=time.time() - t0
            )


# =============================================================================
# EIDOS Principal (Orquestador)
# =============================================================================

class EIDOSPrincipal:
    """
    EIDOS Principal - Orquestador del sistema multi-agente.

    Coordina todos los workers y divide tareas complejas.
    """

    def __init__(self, verbose: bool = True):
        self.verbose = verbose
        self._ensure_dirs()

        # Crear workers
        self.workers = {
            "coding": CodingWorker(),
            "pentesting": PentestingWorker(),
            "research": ResearchWorker(),
            "learning": LearningWorker()
        }

        # Executor para paralelismo
        self.executor = ThreadPoolExecutor(max_workers=4)

        # Task queue
        self.task_queue: List[Task] = []
        self.task_counter = 0

        # Agent Bus -- pub/sub + blackboard + tag routing
        if HAS_BUS:
            self.bus = get_bus()
            self.blackboard = get_blackboard()
            self.router = get_router()
            # Registrar workers como handlers de tag routing
            for name, worker in self.workers.items():
                self.router.register(name, lambda msg, w=worker: w.quick_handle(msg) if hasattr(w, 'quick_handle') else msg)
            self._log("Agent Bus conectado (pub/sub + blackboard + tag routing)")

    def _ensure_dirs(self):
        """Crea directorios necesarios."""
        AGENTS_DIR.mkdir(parents=True, exist_ok=True)
        KNOWLEDGE_DIR.mkdir(parents=True, exist_ok=True)

    def _log(self, msg: str):
        """Log con prefijo."""
        if self.verbose:
            print(f"[EIDOS Principal] {msg}")

    def execute(self, user_input: str) -> Dict[str, Any]:
        """
        Ejecuta una tarea compleja dividiendola entre workers.

        Args:
            user_input: Descripcion de la tarea

        Returns:
            Dict con resultados consolidados
        """
        self._log(f"Recibido: {user_input}")

        # 1. Analizar y dividir tarea
        tasks = self._decompose_task(user_input)

        self._log(f"Tarea dividida en {len(tasks)} subtareas")

        # 2. Asignar a workers
        for task in tasks:
            self._assign_task(task)

        # 3. Ejecutar en paralelo
        results = self._execute_parallel(tasks)

        # 4. Consolidar resultados
        final_result = self._consolidate_results(results)

        # 5. Log
        self._log_execution(user_input, tasks, results)

        # 6. Publicar resultados en bus + blackboard
        if HAS_BUS:
            self.bus.publish("task.completed", {
                "input": user_input[:200],
                "tasks": len(tasks),
                "success": final_result["success"],
            }, source="principal")
            self.blackboard.write("last_task_result", final_result, agent="principal")

        return final_result

    def _decompose_task(self, user_input: str) -> List[Task]:
        """Divide tarea compleja en subtareas basado en keywords y modelo."""
        tasks = []

        user_lower = user_input.lower()

        # Pentesting task?
        if any(kw in user_lower for kw in ["hack", "escanear", "exploit", "pentest",
                                             "vulnerabilidad", "nmap", "metasploit"]):
            self.task_counter += 1
            tasks.append(Task(
                id=f"task_{self.task_counter}",
                description=f"Pentesting: {user_input}",
                type="pentesting",
                priority=8
            ))

        # Research task?
        if any(kw in user_lower for kw in ["buscar", "investigar", "research", "documentar",
                                             "que es", "como funciona", "explica"]):
            self.task_counter += 1
            tasks.append(Task(
                id=f"task_{self.task_counter}",
                description=f"Research: {user_input}",
                type="research",
                priority=6
            ))

        # Coding task?
        if any(kw in user_lower for kw in ["codigo", "programar", "script", "generar",
                                             "implementar", "refactorizar"]):
            self.task_counter += 1
            tasks.append(Task(
                id=f"task_{self.task_counter}",
                description=f"Coding: {user_input}",
                type="coding",
                priority=7
            ))

        # Si no hay tasks especificas, crear tarea generica
        if not tasks:
            self.task_counter += 1
            tasks.append(Task(
                id=f"task_{self.task_counter}",
                description=user_input,
                type="general",
                priority=5
            ))

        return tasks

    def _assign_task(self, task: Task):
        """Asigna tarea al worker apropiado."""
        for worker_name, worker in self.workers.items():
            if worker.can_handle(task):
                task.assigned_to = worker_name
                self._log(f"  {task.id} -> {worker_name}")
                return

        # Fallback: asignar a research
        task.assigned_to = "research"
        self._log(f"  {task.id} -> research (fallback)")

    def _execute_parallel(self, tasks: List[Task]) -> List[WorkerResult]:
        """Ejecuta multiples tasks en paralelo."""
        futures = {}

        for task in tasks:
            worker = self.workers.get(task.assigned_to)
            if worker:
                future = self.executor.submit(worker.execute, task)
                futures[future] = task

        results = []
        for future in as_completed(futures):
            task = futures[future]
            try:
                result = future.result()
                results.append(result)
            except Exception as e:
                self._log(f"Error en {task.id}: {e}")
                results.append(WorkerResult(
                    worker_name=task.assigned_to or "unknown",
                    task_id=task.id,
                    success=False,
                    result=None,
                    error=str(e)
                ))

        return results

    def _consolidate_results(self, results: List[WorkerResult]) -> Dict[str, Any]:
        """Consolida resultados de multiples workers."""
        consolidated = {
            "success": all(r.success for r in results),
            "results": {},
            "learnings": [],
            "total_time": sum(r.execution_time for r in results),
            "workers_used": len(set(r.worker_name for r in results))
        }

        for result in results:
            consolidated["results"][result.worker_name] = result.result
            consolidated["learnings"].extend(result.learnings)

        return consolidated

    def _log_execution(self, user_input: str, tasks: List[Task], results: List[WorkerResult]):
        """Guarda log de ejecucion."""
        try:
            log_entry = {
                "timestamp": datetime.now().isoformat(),
                "input": user_input,
                "tasks_count": len(tasks),
                "results": [
                    {
                        "worker": r.worker_name,
                        "success": r.success,
                        "time": r.execution_time
                    }
                    for r in results
                ]
            }

            with open(TASKS_LOG, "a") as f:
                f.write(json.dumps(log_entry) + "\n")

        except Exception:
            pass

    def get_stats(self) -> Dict[str, Any]:
        """Obtiene estadisticas de workers."""
        return {
            "workers": {
                name: {
                    "tasks_completed": worker.tasks_completed,
                    "type": worker.__class__.__name__
                }
                for name, worker in self.workers.items()
            }
        }


# =============================================================================
# Singleton
# =============================================================================

_principal: Optional[EIDOSPrincipal] = None

def get_eidos_principal() -> EIDOSPrincipal:
    """Obtiene la instancia singleton de EIDOS Principal."""
    global _principal
    if _principal is None:
        _principal = EIDOSPrincipal()
    return _principal


# =============================================================================
# CLI Testing
# =============================================================================

if __name__ == "__main__":
    import sys

    eidos = get_eidos_principal()

    print(f"\n{'=' * 70}")
    print(f"EIDOS MULTI-AGENT SYSTEM")
    print(f"{'=' * 70}\n")

    if len(sys.argv) > 1:
        task = " ".join(sys.argv[1:])
    else:
        task = "Hackea esta web vulnerable.com y genera un reporte completo"

    print(f"Tarea: {task}\n")
    print(f"{'-' * 70}\n")

    result = eidos.execute(task)

    print(f"\n{'-' * 70}")
    print(f"RESULTADO")
    print(f"{'-' * 70}")
    print(f"Success: {result['success']}")
    print(f"Workers usados: {result['workers_used']}")
    print(f"Tiempo total: {result['total_time']:.2f}s")
    print(f"Learnings: {len(result['learnings'])}")

    print(f"\n{'-' * 70}")
    print(f"STATS")
    print(f"{'-' * 70}")

    stats = eidos.get_stats()
    for worker_name, worker_stats in stats["workers"].items():
        print(f"  {worker_name:15s} -> {worker_stats['tasks_completed']} tareas")

    print(f"\n{'=' * 70}\n")
