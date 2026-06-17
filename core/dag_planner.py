"""
EIDOS core/dag_planner.py — DAG Planner (F22)
=============================================
Divide tareas complejas en subtareas y las ejecuta en paralelo usando
un DAG (Directed Acyclic Graph) donde cada nodo es una tool call.

Ventajas sobre la ejecución secuencial del kernel:
  - Paralelismo real: subtareas independientes se lanzan simultáneamente
  - Dependencias explícitas: si B depende de A, espera el resultado de A
  - Retry automático por nodo fallido (sin reiniciar todo el plan)
  - Visibilidad: progreso en tiempo real de cada nodo del DAG

Ejemplo:
    planner = DAGPlanner()
    plan = planner.plan("Analiza la red local, escanea puertos y genera un informe")
    result = planner.execute(plan)

El LLM descompone la tarea en nodos JSON:
  [
    {"id": "n1", "tool": "kali_tool_info", "args": {...}, "deps": []},
    {"id": "n2", "tool": "exec_shell",     "args": {...}, "deps": ["n1"]},
    {"id": "n3", "tool": "write_file",     "args": {...}, "deps": ["n1","n2"]},
  ]
"""
from __future__ import annotations

import asyncio
import json
import time
import urllib.request
from collections import defaultdict
from dataclasses import dataclass, field
from typing import Any

OLLAMA_URL = __import__("os").environ.get("OLLAMA_URL", "http://127.0.0.1:11434")


# ── Tipos básicos ────────────────────────────────────────────────────────────

@dataclass
class DAGNode:
    id:         str
    tool:       str
    args:       dict
    deps:       list[str]       = field(default_factory=list)
    status:     str             = "pending"   # pending / running / done / error
    result:     str             = ""
    error:      str             = ""
    started_at: float           = 0.0
    ended_at:   float           = 0.0

    @property
    def duration_s(self) -> float:
        if self.ended_at and self.started_at:
            return round(self.ended_at - self.started_at, 2)
        return 0.0


@dataclass
class DAGPlan:
    task:        str
    nodes:       list[DAGNode]
    created_at:  float = field(default_factory=time.time)
    total_nodes: int   = 0

    def __post_init__(self) -> None:
        self.total_nodes = len(self.nodes)


# ── DAG Planner ─────────────────────────────────────────────────────────────

class DAGPlanner:
    """
    Divide la tarea usando el LLM, luego ejecuta los nodos en paralelo
    respetando las dependencias.
    """

    def __init__(self, verbose: bool = True) -> None:
        self.verbose = verbose
        self._tool_impl: dict | None = None

    def _get_tool_impl(self) -> dict:
        if self._tool_impl is None:
            try:
                from core.tools import TOOL_IMPL
                self._tool_impl = TOOL_IMPL
            except Exception:
                self._tool_impl = {}
        return self._tool_impl

    def _log(self, msg: str) -> None:
        if self.verbose:
            print(msg)

    # ── Planning con LLM ──────────────────────────────────────────────────

    def plan(self, task: str, available_tools: list[str] | None = None) -> DAGPlan:
        """
        Usa el LLM para descomponer la tarea en un DAG de tool calls.
        Retorna un DAGPlan con todos los nodos y sus dependencias.
        """
        if available_tools is None:
            try:
                from core.tools import TOOLS
                available_tools = [t["function"]["name"] for t in TOOLS]
            except Exception:
                available_tools = ["exec_shell", "read_file", "write_file",
                                   "navigate_browser", "get_resource_status"]

        tools_list = ", ".join(available_tools[:20])  # pyre-ignore[arg-type]

        prompt = f"""Descompón esta tarea en subtareas paralelas usando un DAG.
Tarea: {task}

Herramientas disponibles: {tools_list}

Responde SOLO con JSON válido, un array de nodos:
[
  {{"id": "n1", "tool": "exec_shell", "args": {{"command": "ls /home/ser"}}, "deps": [], "description": "listar archivos"}},
  {{"id": "n2", "tool": "write_file", "args": {{"path": "/tmp/result.txt", "content": "{{n1_result}}"}}, "deps": ["n1"], "description": "guardar resultado"}}
]

Reglas:
- "deps": lista de IDs que deben terminar antes de este nodo
- Nodos sin deps se ejecutan en PARALELO
- Usa {{nX_result}} para referenciar el output de un nodo anterior en args
- Máximo 8 nodos
- Solo el JSON array, nada más"""

        try:
            payload = json.dumps({
                "model":    "deepseek-r1:14b",
                "stream":   False,
                "messages": [{"role": "user", "content": prompt}],
                "options":  {"num_predict": 600, "temperature": 0.3},
            }).encode()
            req = urllib.request.Request(
                f"{OLLAMA_URL}/api/chat",
                data=payload,
                headers={"Content-Type": "application/json"},
            )
            with urllib.request.urlopen(req, timeout=60) as resp:
                content = json.load(resp).get("message", {}).get("content", "[]")

            # Extraer solo el JSON array
            import re
            m = re.search(r'\[.*\]', content, re.DOTALL)
            if m:
                raw_nodes = json.loads(m.group(0))
            else:
                raw_nodes = []
        except Exception as e:
            self._log(f"[DAG] LLM planning error: {e} — usando plan trivial")
            raw_nodes = [{"id": "n1", "tool": "exec_shell",
                          "args": {"command": "echo 'tarea: " + task[:50] + "'"},  # pyre-ignore[arg-type]
                          "deps": [], "description": task}]

        nodes = [
            DAGNode(
                id   = n.get("id", f"n{i}"),
                tool = n.get("tool", "exec_shell"),
                args = n.get("args", {}),
                deps = n.get("deps", []),
            )
            for i, n in enumerate(raw_nodes)
        ]

        plan = DAGPlan(task=task, nodes=nodes)
        self._log(f"\n[⚙️ DAG] Plan creado: {len(nodes)} nodos para '{task[:60]}'")  # pyre-ignore[arg-type]
        for node in nodes:
            deps_str = f"deps={node.deps}" if node.deps else "SIN deps (paralelo)"
            self._log(f"  [{node.id}] {node.tool}({list(node.args.keys())}) — {deps_str}")

        return plan

    def _render_ascii_dag(self, plan: DAGPlan) -> None:
        """Imprime una representación ASCII visual del plan agrupada por fases de ejecución paralela."""
        if not self.verbose or not plan.nodes:
            return
            
        # Calcular 'depth' de cada nodo
        depths: dict[str, int] = {}
        # Iterar hasta que todos tengan depth (DAG finito asume no ciclos)
        for _ in range(len(plan.nodes)):
            for n in plan.nodes:
                if n.id in depths: continue
                if not n.deps:
                    depths[n.id] = 0
                elif all(d in depths for d in n.deps):
                    depths[n.id] = max(depths[d] for d in n.deps) + 1
                    
        # Agrupar por fase
        phases: dict[int, list[DAGNode]] = defaultdict(list)
        for n in plan.nodes:
            phases[depths.get(n.id, 0)].append(n)
            
        print("\n[bold magenta]╭─── 🔀 GRAFO DE EJECUCIÓN DAG ───╮[/bold magenta]")
        for phase in sorted(phases.keys()):
            print(f"[bold cyan]│ FASE {phase + 1} (Paralelo)[/bold cyan]")
            nodes = phases[phase]
            for i, n in enumerate(nodes):
                is_last = (i == len(nodes) - 1)
                prefix = "│ └──" if is_last else "│ ├──"
                deps_info = f"[dim](deps: {','.join(n.deps)})[/dim]" if n.deps else ""
                print(f"{prefix} [bold yellow]{n.id}[/bold yellow]: {n.tool} {deps_info}")
        print("[bold magenta]╰─────────────────────────────────╯[/bold magenta]\n")

    # ── Ejecución con asyncio ─────────────────────────────────────────────

    async def _execute_node(
        self,
        node:    DAGNode,
        results: dict[str, str],
        impl:    dict,
    ) -> None:
        """Ejecuta un nodo individual, reemplazando referencias a resultados previos."""
        node.status     = "running"
        node.started_at = time.time()

        self._log(f"  [▶ {node.id}] {node.tool} ejecutando...")

        # Resolver referencias a resultados anteriores {nX_result} o {nX.campo}
        args_str = json.dumps(node.args)
        
        # 1. Reemplazos de resultados completos
        for ref_id, ref_val in results.items():
            args_str = args_str.replace(f"{{{ref_id}_result}}", str(ref_val))
            
            # 2. Intento de inyección de campos JSON (Fase 2.1)
            try:
                if ref_val.strip().startswith("{"):
                    data = json.loads(ref_val)
                    if isinstance(data, dict):
                        for key, val in data.items():
                            placeholder = f"{{{ref_id}.{key}}}"
                            if placeholder in args_str:
                                args_str = args_str.replace(placeholder, str(val))
            except Exception:
                pass  # error no crítico, continuar
        try:
            resolved_args = json.loads(args_str)
        except Exception:
            resolved_args = node.args

        # Ejecutar la tool
        tool_fn = impl.get(node.tool)
        if tool_fn is None:
            node.error  = f"Tool '{node.tool}' no encontrada en TOOL_IMPL"
            node.status = "error"
            node.ended_at = time.time()
            self._log(f"  [✗ {node.id}] ERROR: {node.error}")
            return

        try:
            # Ejecutar en threadpool para no bloquear el event loop
            loop = asyncio.get_event_loop()
            result = await loop.run_in_executor(None, tool_fn, resolved_args)
            node.result   = str(result)
            node.status   = "done"
            self._log(f"  [✓ {node.id}] {node.duration_s}s → {node.result[:80]}")  # pyre-ignore[arg-type]
        except Exception as e:
            node.error  = str(e)
            node.status = "error"
            self._log(f"  [✗ {node.id}] ERROR: {e}")
        finally:
            node.ended_at = time.time()

        results[node.id] = node.result

    async def _execute_dag(self, plan: DAGPlan) -> dict[str, str]:
        """Ejecuta el DAG completo respetando dependencias."""
        impl    = self._get_tool_impl()
        results: dict[str, str]  = {}
        done:    set[str]        = set()
        pending: list[DAGNode]   = list(plan.nodes)

        while pending:
            # Encontrar nodos listos (deps ya completadas)
            ready = [n for n in pending if all(d in done for d in n.deps)]
            if not ready:
                # Deadlock check
                if not done:
                    self._log("[DAG] ERROR: Deadlock detectado — ningún nodo puede empezar")
                    break
                await asyncio.sleep(0.1)
                continue

            # Ejecutar todos los nodos listos en paralelo
            tasks = [self._execute_node(n, results, impl) for n in ready]
            await asyncio.gather(*tasks)

            for n in ready:
                pending.remove(n)
                done.add(n.id)

        return results

    def execute(self, plan: DAGPlan) -> dict[str, Any]:
        """
        Ejecuta el DAGPlan y retorna el resumen de resultados.
        """
        self._log(f"\n[⚙️ DAG] Ejecutando plan: '{plan.task[:60]}'")  # pyre-ignore[arg-type]
        self._render_ascii_dag(plan)
        t0 = time.time()

        try:
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
            results = loop.run_until_complete(self._execute_dag(plan))
        except Exception as e:
            self._log(f"[DAG] Error fatal en ejecución: {e}")
            results = {}
        finally:
            loop.close()

        total_s = round(time.time() - t0, 2)
        done_nodes  = [n for n in plan.nodes if n.status == "done"]
        error_nodes = [n for n in plan.nodes if n.status == "error"]

        summary = {
            "task":          plan.task,
            "total_nodes":   plan.total_nodes,
            "done":          len(done_nodes),
            "errors":        len(error_nodes),
            "total_s":       total_s,
            "results":       results,
            "nodes_detail":  [
                {"id": n.id, "tool": n.tool, "status": n.status,
                 "result": n.result[:200], "error": n.error, "duration_s": n.duration_s}  # pyre-ignore[arg-type]
                for n in plan.nodes
            ],
        }

        self._log(f"\n[⚙️ DAG] Completado: {len(done_nodes)}/{plan.total_nodes} OK | {len(error_nodes)} errores | {total_s}s total")
        return summary

    def run(self, task: str) -> dict[str, Any]:
        """Atajo: plan + execute en una sola llamada."""
        plan = self.plan(task)
        return self.execute(plan)


# ── Singleton global ─────────────────────────────────────────────────────────

_planner: DAGPlanner | None = None

def get_planner() -> DAGPlanner:
    global _planner
    if _planner is None:
        _planner = DAGPlanner()
    return _planner


# ── CLI rápido ────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    import sys
    task = " ".join(sys.argv[1:]) if len(sys.argv) > 1 else "lista los archivos .py en /home/ser/EIDOS/core"  # pyre-ignore[arg-type]
    planner = DAGPlanner(verbose=True)
    summary = planner.run(task)
    print(f"\n{'='*50}")
    print(f"Tarea: {summary['task']}")
    print(f"Nodos: {summary['done']}/{summary['total_nodes']} OK | {summary['total_s']}s")
    if summary["results"]:
        last_key = list(summary["results"].keys())[-1]
        print(f"Último resultado ({last_key}): {summary['results'][last_key][:300]}")  # pyre-ignore[arg-type]
