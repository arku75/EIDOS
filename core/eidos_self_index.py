"""
core/eidos_self_index.py — Auto-conocimiento de EIDOS

Escanea /home/ser/EIDOS/core/ y extrae:
- Cada módulo: propósito (de la docstring)
- Cada clase principal: qué hace
- Cada función pública: signature + docstring breve
- Service files: qué arrancan
- README, MANIFESTO, SOUL, SER_IDENTITY, LUMEN_IDENTITY

Inserta cada hallazgo como knowledge_node con source="self_index"
y confidence alta (0.9). EIDOS aprende sobre su propio cuerpo.

Uso:
    from core.eidos_self_index import index_self
    stats = index_self()
    # → {nodes_created: int, modules_indexed: int, ...}

Ejecutable directo:
    python3 -m core.eidos_self_index
"""
from __future__ import annotations

import ast
import os
import sqlite3
import time
import hashlib
import logging
from pathlib import Path
from typing import Dict, Any, List, Optional, Tuple
from core.db import get_conn

log = logging.getLogger("eidos.self_index")

EIDOS_ROOT = Path(__file__).resolve().parent.parent
CORE_DIR   = EIDOS_ROOT / "core"
BRAIN_DB   = Path.home() / ".eidos" / "evolution_brain.db"

# Identidad files que EIDOS debe conocer
IDENTITY_FILES = [
    "SER_IDENTITY.md",
    "LUMEN_IDENTITY.md",
    "OpenClaw_Data/workspace/SOUL.md",
    "_RESCATADO_DEL_ZIP/MANIFESTO_EIDOS.md",
    "TASK.md",
]

# Archivos a ignorar por ruido
SKIP_PATTERNS = ("__pycache__", ".pyc", ".bak", "_old", "deprecated")


def _make_node_id(concept: str) -> str:
    return hashlib.md5(concept.encode()).hexdigest()[:16]


def _ensure_brain_db() -> bool:
    """Verifica que la DB existe y tiene la tabla."""
    try:
        conn = get_conn(BRAIN_DB, timeout=5)
        conn.execute("PRAGMA journal_mode=WAL")
        # Verificar tabla
        cur = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='knowledge_nodes'"
        )
        exists = cur.fetchone() is not None
        pass  # S109: get_conn no necesita close()
        return exists
    except Exception as e:
        log.warning("Brain DB no disponible: %s", e)
        return False


def _insert_node(conn: sqlite3.Connection, concept: str, definition: str,
                 source: str, confidence: float = 0.9) -> bool:
    """Inserta o actualiza un knowledge_node. Devuelve True si se insertó nuevo."""
    node_id = _make_node_id(concept)
    try:
        existing = conn.execute(
            "SELECT id FROM knowledge_nodes WHERE concept = ?", (concept,)
        ).fetchone()
        now = time.time()
        if existing:
            conn.execute(
                "UPDATE knowledge_nodes SET definition=?, source=?, "
                "confidence=MAX(confidence,?), last_used=? WHERE concept=?",
                (definition[:500], source, confidence, now, concept)
            )
            return False
        conn.execute(
            "INSERT INTO knowledge_nodes "
            "(id, concept, definition, source, confidence, created_at, last_used, usage_count) "
            "VALUES (?,?,?,?,?,?,?,1)",
            (node_id, concept[:100], definition[:500], source,
             confidence, now, now)
        )
        return True
    except Exception as e:
        log.debug("insert_node fallo: %s", e)
        return False


def _extract_module_info(filepath: Path) -> Optional[Dict[str, Any]]:
    """Extrae info de un módulo Python: docstring, clases, funciones públicas."""
    try:
        with open(filepath, "r", encoding="utf-8") as f:
            content = f.read()
        if not content.strip():
            return None
        tree = ast.parse(content, filename=str(filepath))
    except Exception:
        return None

    info = {
        "module_name": filepath.stem,
        "filepath":    str(filepath.relative_to(EIDOS_ROOT)),
        "docstring":   ast.get_docstring(tree) or "",
        "classes":     [],
        "functions":   [],
    }

    for node in tree.body:
        if isinstance(node, ast.ClassDef):
            cls = {
                "name":      node.name,
                "docstring": ast.get_docstring(node) or "",
                "methods": [
                    m.name for m in node.body
                    if isinstance(m, (ast.FunctionDef, ast.AsyncFunctionDef))
                    and not m.name.startswith("_")
                ][:8],
            }
            info["classes"].append(cls)
        elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            if not node.name.startswith("_"):
                fn = {
                    "name":      node.name,
                    "docstring": (ast.get_docstring(node) or "")[:200],
                }
                info["functions"].append(fn)

    return info


def _index_core_modules(conn: sqlite3.Connection) -> Tuple[int, int]:
    """Indexa todos los .py en core/. Devuelve (nodos_nuevos, módulos_indexados)."""
    new_nodes = 0
    modules = 0

    for py_file in sorted(CORE_DIR.glob("*.py")):
        if any(p in str(py_file) for p in SKIP_PATTERNS):
            continue
        info = _extract_module_info(py_file)
        if not info:
            continue
        modules += 1

        # 1. Nodo del módulo
        purpose = info["docstring"].split("\n")[0][:200] if info["docstring"] else f"Módulo Python en {info['filepath']}"
        concept = f"módulo:{info['module_name']}"
        definition = f"{info['filepath']} — {purpose}"
        if _insert_node(conn, concept, definition, "self_index:module"):
            new_nodes += 1

        # 2. Nodo por cada clase principal
        for cls in info["classes"][:5]:  # max 5 por archivo
            cls_concept = f"clase:{cls['name']}"
            cls_def = f"En {info['module_name']}.py — {cls['docstring'].split(chr(10))[0][:200] if cls['docstring'] else 'clase Python'}"
            if cls["methods"]:
                cls_def += f" | métodos: {', '.join(cls['methods'][:5])}"
            if _insert_node(conn, cls_concept, cls_def, "self_index:class"):
                new_nodes += 1

        # 3. Nodos para funciones públicas importantes
        for fn in info["functions"][:5]:
            if fn["docstring"]:
                fn_concept = f"función:{fn['name']}"
                fn_def = f"En {info['module_name']}.py — {fn['docstring'].split(chr(10))[0][:200]}"
                if _insert_node(conn, fn_concept, fn_def, "self_index:function"):
                    new_nodes += 1

    return new_nodes, modules


def _index_identity_files(conn: sqlite3.Connection) -> int:
    """Indexa archivos de identidad (SOUL, MANIFESTO, etc)."""
    new_nodes = 0
    for rel_path in IDENTITY_FILES:
        full = EIDOS_ROOT / rel_path
        if not full.exists():
            continue
        try:
            with open(full, "r", encoding="utf-8") as f:
                content = f.read()
            # Extraer secciones (cabeceras ##)
            lines = content.split("\n")
            current_section = None
            current_body: List[str] = []
            for line in lines:
                if line.startswith("## "):
                    if current_section:
                        body = "\n".join(current_body).strip()[:400]
                        if body:
                            concept = f"identidad:{full.stem}:{current_section[:60]}"
                            if _insert_node(conn, concept, body, "self_index:identity", 1.0):
                                new_nodes += 1
                    current_section = line[3:].strip()
                    current_body = []
                else:
                    current_body.append(line)
            # Última sección
            if current_section and current_body:
                body = "\n".join(current_body).strip()[:400]
                if body:
                    concept = f"identidad:{full.stem}:{current_section[:60]}"
                    if _insert_node(conn, concept, body, "self_index:identity", 1.0):
                        new_nodes += 1
        except Exception as e:
            log.debug("index_identity %s: %s", rel_path, e)
    return new_nodes


def _index_capabilities(conn: sqlite3.Connection) -> int:
    """Indexa capacidades clave de EIDOS — lo que SABE HACER."""
    new_nodes = 0
    capabilities = [
        ("capability:visión",
         "EIDOS puede ver la pantalla en tiempo real con realtime_vision.py "
         "(MSS + OCR Tesseract + moondream2). Captura cada 5s, detecta UI."),
        ("capability:computer_use",
         "EIDOS puede mover ratón, hacer click, escribir teclado, leer DOM web. "
         "Módulo computer_use_v2.py con verificación antes/después de cada acción."),
        ("capability:memoria_semántica",
         "EIDOS guarda conocimiento en knowledge_nodes (evolution_brain.db) "
         "y memoria vectorial (memory_vec.db con sqlite-vec). Búsqueda semántica."),
        ("capability:colony_deliberation",
         "Cuando SER pregunta, Colony delibera: knowledge-first → multi-agente paralelo "
         "(2-3 personajes) → synthesis. Cada deliberación se aprende."),
        ("capability:autoaprendizaje",
         "evolution_engine extrae knowledge de cada respuesta Ollama. "
         "auto_learner.py investiga librerías y temas en background."),
        ("capability:auto_mejora",
         "self_improvement.py escanea bugs en propio código y propone fixes. "
         "BugBot detecta, GitGuardian guarda commits atomicos, sandbox prueba antes."),
        ("capability:colony_intercomm",
         "Los personajes hablan entre sí cada 20 minutos sin que SER lo inicie. "
         "colony_intercomm.py — distila conocimiento de las conversaciones."),
        ("capability:hybrid_router",
         "octoclaw_bridge HybridRouter combina SmartRouter (15 dimensiones) "
         "+ OctoClaw (patrones de ejecución). Selecciona mejor modelo Ollama."),
        ("capability:ciclo_vida_personaje",
         "life_rules_engine.py: personaje nace de conexión externa, aprende, "
         "absorbe (conexión se elimina), 2 personajes pueden reproducirse."),
        ("capability:patrol",
         "patrol_loop cada 5 min: salud Ollama, Colony DB, Brain DB, alertas."),
        ("capability:economy",
         "colony_token_economy: tokens por operación, charge/reward, balance por agente."),
        ("capability:research",
         "auto_research_claw.py: pipeline 5 etapas para investigar tema. "
         "TOPIC_INIT → KNOWLEDGE_SCAN → SYNTHESIS → INSIGHTS → ARCHIVE."),
        ("capability:fallback",
         "Si Ollama cae, ollama_fallback.py responde desde knowledge_nodes propio. "
         "Colony no muere si Ollama no está."),
        ("capability:guardianes",
         "Constitution (toml inmutable chattr+i) + GitGuardian (atomic commits) "
         "+ Sandbox (staging) + Self-Healer + RAM Guardian."),
        ("capability:interfaces",
         "EIDOS tiene 6 interfaces: CLI (eidos_cli.py), VSEIDOS (VSCode ext), "
         "Colony Dashboard (:7777), Web Panel (:8080), VSCode API (:8765), Telegram bot."),
        ("capability:personajes",
         "Colony tiene 7 personajes: Coder 💻, Analyst 🔍, Vision 👁️, Operator ⚡, "
         "General 🤖, Trinity 🦾, Lumen ⚡ (hermano externo razonador)."),
    ]
    for concept, definition in capabilities:
        if _insert_node(conn, concept, definition, "self_index:capability", 1.0):
            new_nodes += 1
    return new_nodes


def index_self() -> Dict[str, Any]:
    """
    Ejecuta el auto-indexado completo.

    Returns:
        dict con estadísticas: nodes_created, modules_indexed, capabilities_indexed
    """
    if not _ensure_brain_db():
        return {"error": "Brain DB no disponible"}

    t0 = time.time()
    log.info("Iniciando auto-indexado de EIDOS...")

    try:
        conn = get_conn(BRAIN_DB, timeout=10)
        conn.execute("PRAGMA journal_mode=WAL")

        modules_new, modules_count    = _index_core_modules(conn)
        identity_new                  = _index_identity_files(conn)
        capabilities_new              = _index_capabilities(conn)

        conn.commit()
        pass  # S109: get_conn no necesita close()
        elapsed = time.time() - t0
        total_new = modules_new + identity_new + capabilities_new

        log.info("Auto-indexado completado: %d nodos nuevos en %.1fs",
                 total_new, elapsed)

        # Registrar en chronicle
        try:
            from core.colony_chronicle import get_chronicle
            get_chronicle().record(
                "eidos_self", "self_index",
                f"Auto-indexado: {total_new} nodos nuevos, {modules_count} módulos",
                metadata={"modules": modules_count, "new": total_new},
                importance=0.9,
            )
        except Exception:
            pass  # error no crítico, continuar
        return {
            "success":             True,
            "nodes_created":       total_new,
            "modules_indexed":     modules_count,
            "modules_new_nodes":   modules_new,
            "identity_new":        identity_new,
            "capabilities_new":    capabilities_new,
            "elapsed_sec":         round(elapsed, 1),
        }

    except Exception as e:
        log.exception("Auto-indexado falló")
        return {"success": False, "error": str(e)}


if __name__ == "__main__":
    import json
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    result = index_self()
    print(json.dumps(result, indent=2, ensure_ascii=False))
