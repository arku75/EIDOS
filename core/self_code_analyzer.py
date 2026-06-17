"""
core/self_code_analyzer.py — EIDOS escanea y aprende de su propio código fuente.

Crea nodos de conocimiento para:
  - Cada módulo Python (archivo .py)
  - Cada clase y función dentro del módulo
  - Relaciones de importación entre módulos
  - Dependencias externas

Uso:
    from core.self_code_analyzer import get_code_analyzer
    ca = get_code_analyzer()
    result = ca.analyze_all()  # Escanea todo EIDOS
"""
from __future__ import annotations

import ast
from core.db import get_conn
import logging
import os
import sqlite3
import threading
import time
import uuid
from collections import defaultdict
from pathlib import Path
from typing import Any, Dict, List, Optional, Set, Tuple

log = logging.getLogger("eidos.code_analyzer")

EIDOS_DIR = Path.home() / "EIDOS"
BRAIN_DB = Path.home() / ".eidos" / "evolution_brain.db"


class SelfCodeAnalyzer:
    """Analiza el código fuente de EIDOS y crea conocimiento estructurado."""

    def __init__(self):
        self._lock = threading.Lock()
        self._analyzing = False
        self._stats: Dict[str, Any] = {
            "modules_analyzed": 0,
            "classes_found": 0,
            "functions_found": 0,
            "nodes_injected": 0,
            "edges_created": 0,
            "errors": [],
            "last_analysis": 0,
        }

    def analyze_all(self, force: bool = False) -> Dict[str, Any]:
        """Analiza todos los archivos .py en EIDOS/core/ y crea nodos de conocimiento."""
        if self._analyzing:
            return {"status": "busy", "error": "Ya hay un análisis en curso"}
        self._analyzing = True
        try:
            return self._run_analysis(force)
        finally:
            self._analyzing = False

    def _run_analysis(self, force: bool) -> Dict[str, Any]:
        t0 = time.time()
        modules = 0
        classes = 0
        functions = 0
        injected = 0
        errors = []

        core_dir = EIDOS_DIR / "core"
        if not core_dir.exists():
            return {"status": "error", "error": f"No existe {core_dir}"}

        # Mapa de módulos: nombre -> {classes, functions, imports, docstring}
        module_map: Dict[str, Dict[str, Any]] = {}

        for fpath in sorted(core_dir.glob("*.py")):
            try:
                info = self._analyze_file(fpath)
                if info:
                    module_map[info["name"]] = info
                    modules += 1
                    classes += len(info["classes"])
                    functions += len(info["functions"])
            except Exception as e:
                errors.append(f"{fpath.name}: {str(e)[:60]}")
                log.error("Code analysis error: %s: %s", fpath.name, e)

        # Inyectar nodos de conocimiento para cada módulo, clase, función
        conn = None
        try:
            conn = get_conn(BRAIN_DB, timeout=10)
            conn.execute("PRAGMA journal_mode=WAL")

            for mod_name, info in module_map.items():
                # Nodo del módulo
                mod_id = self._inject_module(conn, info, force)
                if mod_id:
                    injected += 1

                # Nodos de clases
                for cls_info in info["classes"]:
                    cls_id = self._inject_class(conn, mod_name, cls_info, force)
                    if cls_id:
                        injected += 1
                # Relación: clase pertenece a módulo
                try:
                    conn.execute(
                        "INSERT OR IGNORE INTO knowledge_edges "
                        "(from_node, to_node, relation_type, strength) VALUES (?, ?, ?, ?)",
                        (cls_id, mod_id, "part_of", 1.0)
                    )
                except Exception:
                    pass

                # Nodos de funciones
                for fn_info in info["functions"]:
                    fn_id = self._inject_function(conn, mod_name, fn_info, force)
                    if fn_id:
                        injected += 1
                        # Relación: función pertenece a módulo
                        try:
                            conn.execute(
                                "INSERT OR IGNORE INTO knowledge_edges "
                                "(from_node, to_node, relation_type, strength) VALUES (?, ?, ?, ?)",
                                (fn_id, mod_id, "part_of", 1.0)
                            )
                        except Exception:
                            pass

            # Relaciones de importación
            edge_count = 0
            for mod_name, info in module_map.items():
                for imp in info["imports"]:
                    if imp in module_map:
                        src_id = self._get_module_id(conn, mod_name)
                        tgt_id = self._get_module_id(conn, imp)
                        if src_id and tgt_id:
                            try:
                                conn.execute(
                                    "INSERT OR IGNORE INTO knowledge_edges "
                                    "(from_node, to_node, relation_type, strength) VALUES (?, ?, ?, ?)",
                                    (src_id, tgt_id, "imports", 0.7)
                                )
                                edge_count += 1
                            except Exception:
                                pass

            conn.commit()
        except Exception as e:
            errors.append(f"DB error: {str(e)[:80]}")
            log.error("Code analysis DB error: %s", e)
        finally:
            if conn:
                conn.close()

        elapsed = round(time.time() - t0, 1)
        with self._lock:
            self._stats["modules_analyzed"] = modules
            self._stats["classes_found"] = classes
            self._stats["functions_found"] = functions
            self._stats["nodes_injected"] = injected
            self._stats["edges_created"] = edge_count
            self._stats["errors"] = errors[-10:]
            self._stats["last_analysis"] = time.time()

        return {
            "status": "ok",
            "modules": modules,
            "classes": classes,
            "functions": functions,
            "nodes_injected": injected,
            "import_edges": edge_count,
            "errors": len(errors),
            "elapsed_s": elapsed,
        }

    def _analyze_file(self, fpath: Path) -> Optional[Dict[str, Any]]:
        """Analiza un archivo Python extrayendo AST."""
        source = fpath.read_text("utf-8", errors="replace")
        try:
            tree = ast.parse(source)
        except SyntaxError:
            return None

        mod_name = fpath.stem
        classes = []
        functions = []
        imports = []

        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    imports.append(alias.name.split(".")[0])
            elif isinstance(node, ast.ImportFrom):
                if node.module:
                    imports.append(node.module.split(".")[0])
            elif isinstance(node, ast.ClassDef):
                doc = ast.get_docstring(node) or ""
                methods = [n.name for n in node.body if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))]
                classes.append({
                    "name": node.name,
                    "docstring": doc[:300],
                    "methods": methods[:20],
                    "lineno": node.lineno,
                })
            elif isinstance(node, ast.FunctionDef):
                doc = ast.get_docstring(node) or ""
                functions.append({
                    "name": node.name,
                    "docstring": doc[:300],
                    "lineno": node.lineno,
                    "args": [arg.arg for arg in node.args.args[:10]],
                })

        docstring = ast.get_docstring(tree) or ""
        return {
            "name": mod_name,
            "path": str(fpath.relative_to(EIDOS_DIR)),
            "docstring": docstring[:500],
            "classes": classes,
            "functions": functions,
            "imports": list(set(imports)),
            "size": len(source),
        }

    def _inject_module(self, conn, info: Dict[str, Any], force: bool) -> Optional[str]:
        mid = str(uuid.uuid5(uuid.NAMESPACE_DNS, f"eidos_module:{info['name']}"))
        concept = f"Módulo {info['name']}"
        definition = f"Módulo de EIDOS: {info['path']}. {info['docstring']}"
        if len(definition) < 20:
            definition = f"Módulo {info['name']} en {info['path']}. Contiene {len(info['classes'])} clases, {len(info['functions'])} funciones."
        try:
            conn.execute(
                "INSERT OR IGNORE INTO knowledge_nodes "
                "(id, concept, definition, category, confidence, source) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                (mid, concept[:200], definition[:500],
                 "eidos_module", 0.8, "code_analyzer")
            )
            return mid
        except Exception:
            return None

    def _inject_class(self, conn, mod_name: str, cls_info: Dict[str, Any], force: bool) -> Optional[str]:
        cid = str(uuid.uuid5(uuid.NAMESPACE_DNS, f"eidos_class:{mod_name}:{cls_info['name']}"))
        concept = f"{cls_info['name']} ({mod_name})"
        methods_str = ", ".join(cls_info["methods"][:8])
        definition = f"Clase en {mod_name}.py:{cls_info['lineno']}. {cls_info['docstring']}"
        if len(definition) < 20:
            definition = f"Clase {cls_info['name']} en módulo {mod_name}. Métodos: {methods_str}"
        try:
            conn.execute(
                "INSERT OR IGNORE INTO knowledge_nodes "
                "(id, concept, definition, category, confidence, source) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                (cid, concept[:200], definition[:500],
                 "eidos_class", 0.7, "code_analyzer")
            )
            return cid
        except Exception:
            return None

    def _inject_function(self, conn, mod_name: str, fn_info: Dict[str, Any], force: bool) -> Optional[str]:
        fid = str(uuid.uuid5(uuid.NAMESPACE_DNS, f"eidos_func:{mod_name}:{fn_info['name']}"))
        concept = f"{fn_info['name']}() ({mod_name})"
        args_str = ", ".join(fn_info["args"][:6])
        definition = f"Función en {mod_name}.py:{fn_info['lineno']}. {fn_info['docstring']}"
        if len(definition) < 20:
            definition = f"Función {fn_info['name']}({args_str}) en módulo {mod_name}."
        try:
            conn.execute(
                "INSERT OR IGNORE INTO knowledge_nodes "
                "(id, concept, definition, category, confidence, source) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                (fid, concept[:200], definition[:500],
                 "eidos_function", 0.6, "code_analyzer")
            )
            return fid
        except Exception:
            return None

    def _get_module_id(self, conn, mod_name: str) -> Optional[str]:
        try:
            row = conn.execute(
                "SELECT id FROM knowledge_nodes WHERE concept = ? AND source = 'code_analyzer'",
                (f"Módulo {mod_name}",)
            ).fetchone()
            return row[0] if row else None
        except Exception:
            return None

    def get_stats(self) -> Dict[str, Any]:
        with self._lock:
            return dict(self._stats)


# ── Singleton ──
_instance = None
_lock = threading.Lock()


def get_code_analyzer() -> SelfCodeAnalyzer:
    global _instance
    if _instance is None:
        with _lock:
            if _instance is None:
                _instance = SelfCodeAnalyzer()
    return _instance
