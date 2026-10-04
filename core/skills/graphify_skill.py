#!/usr/bin/env python3
"""
EIDOS core/skills/graphify_skill.py — Graphify Integration
===========================================================
Skill de integración con Graphify (open source) para EIDOS.

Graphify analiza código de 25+ lenguajes, documentos, papers e imágenes
y construye un knowledge graph consultable localmente.

Todo se mantiene local en la PC del usuario - sin telemetría, sin nube.
El código se procesa localmente via tree-sitter AST (sin LLM).
Solo docs/imágenes usan LLM para extracción semántica.

Uso:
    /graphify [path] [--mode deep]    # Construir grafo
    /graphify query "..."             # Consultar grafo
    /graphify path "A" "B"            # Camino más corto
    /graphify explain "node"          # Explicar nodo
    /graphify stats                   # Estadísticas

Requiere:
    - venv-graphify/ configurado en EIDOS
    - graphify instalado: pip install graphifyy
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Optional, Dict, Any, List

from core.paths import EIDOS_HOME, REPO_ROOT, USER_HOME

# ═══════════════════════════════════════════════════════════════════════════════
# Configuración
# ═══════════════════════════════════════════════════════════════════════════════

GRAPHIFY_VENV = Path(os.environ.get("EIDOS_GRAPHIFY_VENV", str(USER_HOME / "MIS PROGRAMAS" / "graphify" / "venv"))).expanduser()
GRAPHIFY_BIN = GRAPHIFY_VENV / "bin" / "graphify"
PYTHON_BIN = GRAPHIFY_VENV / "bin" / "python"
EIDOS_GRAPH_DIR = EIDOS_HOME / "graphify"

# ═══════════════════════════════════════════════════════════════════════════════
# Graphify Skill
# ═══════════════════════════════════════════════════════════════════════════════

class GraphifySkill:
    """
    Skill de integración con Graphify para EIDOS.
    Mantiene todo local - sin telemetría, sin nube.
    """
    
    def __init__(self):
        self.enabled = self._check_installation()
        self.graph_dir = EIDOS_GRAPH_DIR
        self.graph_dir.mkdir(parents=True, exist_ok=True)
        
    def _check_installation(self) -> bool:
        """Verifica que graphify está instalado en el venv."""
        return GRAPHIFY_BIN.exists() and PYTHON_BIN.exists()
    
    def _run_graphify(self, *args, cwd: Optional[Path] = None, capture=True) -> tuple:
        """
        Ejecuta graphify desde el venv aislado.
        
        Args:
            args: Argumentos para graphify
            cwd: Directorio de trabajo
            capture: Si capturar output
            
        Returns:
            (stdout, stderr, returncode)
        """
        if not self.enabled:
            return "", "Graphify no instalado. Ejecuta: /graphify install", 1
        
        cmd = [str(GRAPHIFY_BIN)] + list(args)
        env = os.environ.copy()
        env["PATH"] = str(GRAPHIFY_VENV / "bin") + ":" + env.get("PATH", "")
        env["GRAPHIFY_NO_TELEMETRY"] = "1"  # Desactivar telemetría
        env["GRAPHIFY_OFFLINE"] = "1"       # Modo offline preferente
        
        try:
            result = subprocess.run(
                cmd,
                cwd=cwd or self.graph_dir,
                capture_output=capture,
                text=True,
                env=env,
                timeout=300  # 5 min timeout
            )
            return result.stdout, result.stderr, result.returncode
        except subprocess.TimeoutExpired:
            return "", "Timeout - operación tomó más de 5 minutos", 1
        except Exception as e:
            return "", f"Error ejecutando graphify: {e}", 1
    
    def install(self) -> Dict[str, Any]:
        """
        Instala/verifica la instalación de graphify.
        
        Returns:
            Estado de la instalación
        """
        if self.enabled:
            stdout, _, code = self._run_graphify("--help")
            return {
                "success": code == 0,
                "status": "instalado",
                "path": str(GRAPHIFY_BIN),
                "version": "0.6.7",  # Versión del source
                "privacy": "100% local - sin telemetría",
                "note": "Código procesado localmente via tree-sitter AST"
            }
        
        return {
            "success": False,
            "status": "no instalado",
            "error": f"Graphify no encontrado en {GRAPHIFY_VENV}",
            "fix": "El venv existe pero graphify no está instalado"
        }
    
    def build_graph(self, path: str = ".", mode: str = "standard", 
                   update: bool = False) -> Dict[str, Any]:
        """
        Construye el knowledge graph para un directorio.
        
        Args:
            path: Ruta a analizar (default: EIDOS)
            mode: 'standard' o 'deep' (más agresivo)
            update: Solo actualizar archivos cambiados
            
        Returns:
            Resultado de la operación
        """
        if not self.enabled:
            return {"success": False, "error": "Graphify no instalado"}
        
        target_path = Path(path).resolve()
        if not target_path.exists():
            return {"success": False, "error": f"Ruta no existe: {path}"}
        
        # Construir argumentos
        args = [str(target_path)]
        if mode == "deep":
            args.append("--mode")
            args.append("deep")
        if update:
            args.append("--update")
        
        # Ejecutar graphify (nota: el comando principal no es necesario,
        # graphify sin subcomando procesa el directorio)
        stdout, stderr, code = self._run_graphify(*args, cwd=target_path)
        
        if code == 0:
            # Buscar archivos generados
            out_dir = target_path / "graphify-out"
            files = []
            if out_dir.exists():
                files = [f.name for f in out_dir.iterdir() if f.is_file()]
            
            return {
                "success": True,
                "path": str(target_path),
                "output_dir": str(out_dir) if out_dir.exists() else None,
                "files_generated": files,
                "privacy_note": "Todo procesado localmente",
                "stdout": stdout[-2000:] if stdout else ""  # Last 2000 chars
            }
        else:
            return {
                "success": False,
                "error": stderr or stdout,
                "code": code
            }
    
    def query(self, question: str, graph_path: Optional[str] = None,
              budget: int = 2000, dfs: bool = False) -> Dict[str, Any]:
        """
        Consulta el knowledge graph con una pregunta natural.
        
        Args:
            question: Pregunta en lenguaje natural
            graph_path: Ruta a graph.json (default: graphify-out/graph.json)
            budget: Límite de tokens de salida
            dfs: Usar depth-first search
            
        Returns:
            Resultado de la consulta
        """
        if not self.enabled:
            return {"success": False, "error": "Graphify no instalado"}
        
        args = ["query", question, "--budget", str(budget)]
        
        if graph_path:
            args.extend(["--graph", graph_path])
        else:
            default_graph = Path.cwd() / "graphify-out" / "graph.json"
            if default_graph.exists():
                args.extend(["--graph", str(default_graph)])
        
        if dfs:
            args.append("--dfs")
        
        stdout, stderr, code = self._run_graphify(*args)
        
        return {
            "success": code == 0,
            "question": question,
            "answer": stdout if code == 0 else stderr,
            "mode": "dfs" if dfs else "bfs"
        }
    
    def path(self, node_a: str, node_b: str, 
             graph_path: Optional[str] = None) -> Dict[str, Any]:
        """
        Encuentra el camino más corto entre dos nodos.
        
        Args:
            node_a: Nodo origen
            node_b: Nodo destino
            graph_path: Ruta a graph.json
            
        Returns:
            Camino encontrado
        """
        if not self.enabled:
            return {"success": False, "error": "Graphify no instalado"}
        
        args = ["path", node_a, node_b]
        
        if graph_path:
            args.extend(["--graph", graph_path])
        
        stdout, stderr, code = self._run_graphify(*args)
        
        return {
            "success": code == 0,
            "from": node_a,
            "to": node_b,
            "path": stdout if code == 0 else stderr
        }
    
    def explain(self, node: str, graph_path: Optional[str] = None) -> Dict[str, Any]:
        """
        Explica un nodo y sus vecinos en lenguaje natural.
        
        Args:
            node: Nodo a explicar
            graph_path: Ruta a graph.json
            
        Returns:
            Explicación del nodo
        """
        if not self.enabled:
            return {"success": False, "error": "Graphify no instalado"}
        
        args = ["explain", node]
        
        if graph_path:
            args.extend(["--graph", graph_path])
        
        stdout, stderr, code = self._run_graphify(*args)
        
        return {
            "success": code == 0,
            "node": node,
            "explanation": stdout if code == 0 else stderr
        }
    
    def stats(self, graph_path: Optional[str] = None) -> Dict[str, Any]:
        """
        Obtiene estadísticas del grafo.
        
        Args:
            graph_path: Ruta a graph.json
            
        Returns:
            Estadísticas
        """
        # Buscar graph.json por defecto
        if not graph_path:
            candidates = [
                Path.cwd() / "graphify-out" / "graph.json",
                REPO_ROOT / "graphify-out" / "graph.json",
            ]
            for c in candidates:
                if c.exists():
                    graph_path = str(c)
                    break
        
        if not graph_path or not Path(graph_path).exists():
            return {
                "success": False,
                "error": "No se encontró graph.json. Ejecuta /graphify primero.",
                "hint": "Usa /graphify . para construir el grafo"
            }
        
        try:
            with open(graph_path) as f:
                data = json.load(f)
            
            nodes = len(data.get("nodes", []))
            edges = len(data.get("links", []))
            
            return {
                "success": True,
                "graph_path": graph_path,
                "nodes": nodes,
                "edges": edges,
                "density": edges / (nodes * (nodes - 1)) if nodes > 1 else 0,
                "privacy": "100% local"
            }
        except Exception as e:
            return {"success": False, "error": str(e)}
    
    def update(self, path: str = ".", force: bool = False) -> Dict[str, Any]:
        """
        Actualiza el grafo con archivos cambiados (sin LLM, solo código).
        
        Args:
            path: Ruta a actualizar
            force: Forzar actualización incluso si hay menos nodos
            
        Returns:
            Resultado
        """
        if not self.enabled:
            return {"success": False, "error": "Graphify no instalado"}
        
        target_path = Path(path).resolve()
        args = ["update", str(target_path)]
        
        if force:
            args.append("--force")
        
        stdout, stderr, code = self._run_graphify(*args)
        
        return {
            "success": code == 0,
            "path": str(target_path),
            "output": stdout if code == 0 else stderr,
            "note": "Código re-procesado localmente via AST"
        }


# ═══════════════════════════════════════════════════════════════════════════════
# Singleton
# ═══════════════════════════════════════════════════════════════════════════════

_graphify_skill: Optional[GraphifySkill] = None

def get_graphify_skill() -> GraphifySkill:
    """Obtiene la instancia singleton del skill."""
    global _graphify_skill
    if _graphify_skill is None:
        _graphify_skill = GraphifySkill()
    return _graphify_skill


# ═══════════════════════════════════════════════════════════════════════════════
# Comandos para slash_commands.py
# ═══════════════════════════════════════════════════════════════════════════════

def cmd_graphify_install() -> str:
    """Instala/verifica graphify."""
    skill = get_graphify_skill()
    result = skill.install()
    
    if result["success"]:
        return f"""✅ Graphify instalado localmente

📍 Ruta: {result['path']}
🔒 Privacidad: {result['privacy']}
📝 Nota: {result['note']}

Comandos disponibles:
  /graphify [path]       - Construir grafo
  /graphify query "..."  - Consultar grafo
  /graphify path A B     - Camino entre nodos
  /graphify explain X    - Explicar nodo
  /graphify update       - Actualizar cambios"""
    else:
        return f"❌ {result.get('error', 'Error desconocido')}"

def cmd_graphify_build(path: str = ".", mode: str = "standard", update: bool = False) -> str:
    """Construye el knowledge graph."""
    skill = get_graphify_skill()
    result = skill.build_graph(path, mode, update)
    
    if result["success"]:
        files = "\n  - ".join(result.get("files_generated", []))
        return f"""✅ Grafo construido

📂 Directorio: {result['path']}
📁 Output: {result['output_dir']}
📄 Archivos:
  - {files}

🔒 {result['privacy_note']}"""
    else:
        return f"❌ Error: {result.get('error', 'Desconocido')}"

def cmd_graphify_query(question: str, dfs: bool = False) -> str:
    """Consulta el grafo."""
    skill = get_graphify_skill()
    result = skill.query(question, dfs=dfs)
    
    if result["success"]:
        return f"🔍 {result['question']}\n\n{result['answer']}"
    else:
        return f"❌ Error: {result.get('answer', 'Desconocido')}"

def cmd_graphify_path(node_a: str, node_b: str) -> str:
    """Camino entre nodos."""
    skill = get_graphify_skill()
    result = skill.path(node_a, node_b)
    
    if result["success"]:
        return f"🛤️  Camino: {result['from']} → {result['to']}\n\n{result['path']}"
    else:
        return f"❌ Error: {result.get('path', 'Desconocido')}"

def cmd_graphify_explain(node: str) -> str:
    """Explica un nodo."""
    skill = get_graphify_skill()
    result = skill.explain(node)
    
    if result["success"]:
        return f"📖 {result['node']}\n\n{result['explanation']}"
    else:
        return f"❌ Error: {result.get('explanation', 'Desconocido')}"

def cmd_graphify_stats() -> str:
    """Estadísticas del grafo."""
    skill = get_graphify_skill()
    result = skill.stats()
    
    if result["success"]:
        return f"""📊 Estadísticas del Grafo

📁 Archivo: {result['graph_path']}
🔵 Nodos: {result['nodes']}
🔗 Aristas: {result['edges']}
📈 Densidad: {result['density']:.4f}
🔒 {result['privacy']}"""
    else:
        return f"❌ {result.get('error', 'Desconocido')}"

def cmd_graphify_update(path: str = ".") -> str:
    """Actualiza el grafo."""
    skill = get_graphify_skill()
    result = skill.update(path)
    
    if result["success"]:
        return f"""🔄 Grafo actualizado

📂 {result['path']}
📝 {result['note']}

Output:
{result['output'][:1000]}"""
    else:
        return f"❌ Error: {result.get('output', 'Desconocido')}"


if __name__ == "__main__":
    # Test rápido
    skill = get_graphify_skill()
    print("Graphify Skill Test")
    print("=" * 40)
    print(f"Habilitado: {skill.enabled}")
    print(f"Graph dir: {skill.graph_dir}")
    
    if skill.enabled:
        print("\nInstalación:")
        print(skill.install())
