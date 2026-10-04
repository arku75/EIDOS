"""
core/eidos_procedural.py — Memoria procedimental (recetas) [S125]

Cierra el GAP entre saber QUÉ es algo (declarativo, grafo de conocimiento)
y saber CÓMO hacerlo (procedimental, secuencias de acciones).

Estructura de una receta:
  {
    name: "Escanear puertos con nmap",
    trigger: ["escanear puertos", "port scan", "nmap"],
    steps: [
      {action: "terminal", cmd: "nmap -sV -p 1-1000 {target}", description: "..."},
      ...
    ],
    preconditions: ["nmap instalado", "IP objetivo conocida"],
    postconditions: ["puertos identificados", "servicios detectados"],
    success_count: 3, fail_count: 0,
    last_used: "2026-06-14T10:00:00",
    learned_from: "autonomous" | "ser_demo" | "inference",
    tags: ["kali", "reconocimiento", "red"],
  }

API:
  - record_recipe(name, steps, ...) — graba una nueva receta
  - recall_recipes(trigger_words) — busca recetas por palabras clave
  - execute_recipe(name, context) — ejecuta una receta paso a paso
  - learn_from_action(action, result) — aprende una receta de una acción exitosa
  - discover_procedural_gaps() — encuentra conceptos sin receta
  - get_all_recipes() — lista todas las recetas
"""

from __future__ import annotations

import json
import logging
import sqlite3
import time
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import sys as _sys
from pathlib import Path as _Path
_EIDOS_ROOT = _Path(__file__).resolve().parent.parent
if str(_EIDOS_ROOT) not in _sys.path:
    _sys.path.insert(0, str(_EIDOS_ROOT))
from core.db import get_conn

log = logging.getLogger("eidos.procedural")

DB_PATH = Path.home() / ".eidos" / "procedural_memory.db"


def _ensure_db() -> None:
    """Crea/actualiza la base de datos de memoria procedimental."""
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    with get_conn(DB_PATH, timeout=5) as c:
        c.executescript("""
            CREATE TABLE IF NOT EXISTS recipes (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL UNIQUE,
                description TEXT,
                steps_json TEXT NOT NULL,          -- JSON array of step objects
                preconditions_json TEXT DEFAULT '[]',
                postconditions_json TEXT DEFAULT '[]',
                trigger_words_json TEXT DEFAULT '[]',
                tags_json TEXT DEFAULT '[]',
                success_count INTEGER DEFAULT 0,
                fail_count INTEGER DEFAULT 0,
                last_used TEXT,
                learned_from TEXT DEFAULT 'autonomous',
                created_at TEXT DEFAULT (datetime('now')),
                updated_at TEXT DEFAULT (datetime('now'))
            );
            CREATE TABLE IF NOT EXISTS procedural_actions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                recipe_id INTEGER,
                step_index INTEGER,
                action TEXT,             -- "terminal", "click", "type", "browse", "filesystem"
                target TEXT,
                command TEXT,
                output TEXT,
                success INTEGER DEFAULT 0,
                duration_ms INTEGER,
                ts TEXT DEFAULT (datetime('now')),
                FOREIGN KEY (recipe_id) REFERENCES recipes(id)
            );
            CREATE TABLE IF NOT EXISTS procedural_gaps (
                concept TEXT PRIMARY KEY,
                gap_type TEXT,           -- "no_recipe", "recipe_failing", "never_tried"
                detected_at TEXT DEFAULT (datetime('now')),
                resolved INTEGER DEFAULT 0
            );
            CREATE INDEX IF NOT EXISTS idx_recipes_name ON recipes(name);
            CREATE INDEX IF NOT EXISTS idx_recipes_tags ON recipes(tags_json);
            CREATE INDEX IF NOT EXISTS idx_actions_recipe ON procedural_actions(recipe_id);
        """)


_ensure_db()


# ── CRUD de recetas ──────────────────────────────────────────────────────────


def record_recipe(
    name: str,
    steps: List[Dict[str, str]],
    description: str = "",
    trigger_words: Optional[List[str]] = None,
    preconditions: Optional[List[str]] = None,
    postconditions: Optional[List[str]] = None,
    tags: Optional[List[str]] = None,
    learned_from: str = "autonomous",
) -> Dict[str, Any]:
    """Graba una nueva receta procedimental.

    Args:
        name: Nombre descriptivo ("Escanear puertos con nmap")
        steps: Lista de pasos [{action, cmd/target, description}, ...]
        trigger_words: Palabras que activan esta receta
        preconditions: Lo que debe cumplirse antes de ejecutar
        postconditions: Lo que se espera obtener después
        tags: Etiquetas para categorizar
        learned_from: Origen del aprendizaje

    Returns: {ok, recipe_id, name, is_new}
    """
    result = {"ok": False, "recipe_id": 0, "name": name, "is_new": False}

    try:
        with get_conn(DB_PATH, timeout=5) as c:
            # Verificar si ya existe
            existing = c.execute(
                "SELECT id FROM recipes WHERE name = ?", (name,)
            ).fetchone()

            steps_json = json.dumps(steps, ensure_ascii=False)
            precond_json = json.dumps(preconditions or [], ensure_ascii=False)
            postcond_json = json.dumps(postconditions or [], ensure_ascii=False)
            triggers_json = json.dumps(trigger_words or [], ensure_ascii=False)
            tags_json = json.dumps(tags or [], ensure_ascii=False)

            if existing:
                # Actualizar receta existente
                c.execute(
                    """UPDATE recipes SET steps_json=?, description=?,
                       trigger_words_json=?, preconditions_json=?,
                       postconditions_json=?, tags_json=?,
                       updated_at=datetime('now')
                       WHERE id=?""",
                    (steps_json, description, triggers_json, precond_json,
                     postcond_json, tags_json, existing[0]),
                )
                result["recipe_id"] = existing[0]
                result["is_new"] = False
            else:
                cur = c.execute(
                    """INSERT INTO recipes
                       (name, description, steps_json, preconditions_json,
                        postconditions_json, trigger_words_json, tags_json,
                        learned_from)
                       VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                    (name, description, steps_json, precond_json,
                     postcond_json, triggers_json, tags_json, learned_from),
                )
                result["recipe_id"] = cur.lastrowid
                result["is_new"] = True

            result["ok"] = True
            log.info("Receta '%s' %s (id=%s)",
                     name,
                     "actualizada" if not result["is_new"] else "creada",
                     result["recipe_id"])

    except Exception as e:
        result["error"] = str(e)
        log.error("Error grabando receta '%s': %s", name, e)

    return result


def recall_recipes(trigger_text: str, max_results: int = 5) -> List[Dict[str, Any]]:
    """Busca recetas relevantes por palabras clave en el texto trigger.

    Args:
        trigger_text: Texto de búsqueda ("cómo escanear puertos")
        max_results: Máximo de resultados

    Returns: Lista de recetas [{id, name, description, steps, confidence, ...}]
    """
    results = []
    try:
        words = set(trigger_text.lower().split())
        with get_conn(DB_PATH, timeout=5) as c:
            rows = c.execute(
                "SELECT id, name, description, steps_json, trigger_words_json, "
                "success_count, fail_count, last_used, learned_from, tags_json "
                "FROM recipes ORDER BY success_count DESC, updated_at DESC"
            ).fetchall()

            for row in rows:
                triggers = json.loads(row[4])
                # Matching: cuántas palabras del trigger aparecen en el nombre o triggers
                name_words = set(row[1].lower().split())
                trigger_set = set(t.lower() for t in triggers)
                all_words = name_words | trigger_set
                matches = words & all_words
                if matches:
                    confidence = len(matches) / max(len(words), 1)
                    steps = json.loads(row[3])
                    results.append({
                        "id": row[0],
                        "name": row[1],
                        "description": row[2],
                        "steps": steps,
                        "confidence": round(confidence, 2),
                        "success_count": row[5],
                        "fail_count": row[6],
                        "last_used": row[7],
                        "learned_from": row[8],
                        "tags": json.loads(row[9]),
                    })

        results.sort(key=lambda r: (r["confidence"], r["success_count"]), reverse=True)
        return results[:max_results]

    except Exception as e:
        log.error("Error buscando recetas: %s", e)
        return []


def execute_recipe(name_or_id: Any, context: Optional[Dict] = None,
                   dry_run: bool = True) -> Dict[str, Any]:
    """Ejecuta una receta paso a paso.

    Args:
        name_or_id: Nombre o ID de la receta
        context: Diccionario con variables de contexto ({target: "192.168.1.1"})
        dry_run: Si True, solo simula

    Returns: {ok, recipe_name, steps_executed: [{step, result}], all_succeeded}
    """
    result = {"ok": False, "recipe_name": "", "steps_executed": [],
              "all_succeeded": False, "error": ""}

    try:
        with get_conn(DB_PATH, timeout=5) as c:
            if isinstance(name_or_id, int):
                row = c.execute(
                    "SELECT id, name, steps_json FROM recipes WHERE id=?",
                    (name_or_id,)
                ).fetchone()
            else:
                row = c.execute(
                    "SELECT id, name, steps_json FROM recipes WHERE name=?",
                    (name_or_id,)
                ).fetchone()

            if not row:
                result["error"] = f"Receta no encontrada: {name_or_id}"
                return result

            recipe_id, name, steps_json = row
            steps = json.loads(steps_json)
            result["recipe_name"] = name
            context = context or {}

            all_ok = True
            for i, step in enumerate(steps):
                action = step.get("action", "")
                cmd = step.get("cmd", "")
                target = step.get("target", "")

                # Interpolar variables del contexto
                for k, v in context.items():
                    cmd = cmd.replace(f"{{{k}}}", str(v))
                    target = target.replace(f"{{{k}}}", str(v))

                step_result = {"step": i + 1, "action": action, "ok": False}

                if dry_run:
                    step_result["ok"] = True
                    step_result["output"] = f"[DRY_RUN] {action}: {cmd or target}"
                else:
                    # Ejecución real según el tipo de acción
                    if action == "terminal":
                        from core.eidos_shell_term import run, is_safe
                        safe, why = is_safe(cmd)
                        if not safe:
                            step_result["ok"] = False
                            step_result["error"] = why
                            all_ok = False
                            break
                        r = run(cmd, timeout=30.0, open_window=True)
                        step_result["ok"] = r.get("ok", False)
                        step_result["output"] = r.get("output", "")[:500]
                        step_result["error"] = r.get("error", "")

                    elif action == "click":
                        from core.eidos_mouse import click_text
                        step_result["ok"] = click_text(None, target, human_like=True)
                        step_result["output"] = f"Click en '{target}'"

                    elif action == "type":
                        from core.eidos_mouse import type_text
                        step_result["ok"] = type_text(target or cmd)
                        step_result["output"] = f"Texto '{target or cmd}' tipeado"

                    elif action == "browse":
                        from core.eidos_browser import browse
                        text = browse(target, action="read", headless=False)
                        step_result["ok"] = True
                        step_result["output"] = str(text)[:500]

                    elif action == "filesystem":
                        from core.eidos_filesystem import read_file, ls
                        if cmd == "ls":
                            r = ls(target)
                            step_result["ok"] = r.get("ok", False)
                            step_result["output"] = str(r.get("entries", []))[:500]

                # Grabar acción
                c.execute(
                    """INSERT INTO procedural_actions
                       (recipe_id, step_index, action, target, command, output, success)
                       VALUES (?, ?, ?, ?, ?, ?, ?)""",
                    (recipe_id, i, action, target, cmd,
                     step_result.get("output", "")[:1000],
                     1 if step_result["ok"] else 0),
                )

                result["steps_executed"].append(step_result)
                if not step_result["ok"]:
                    all_ok = False
                    break

            # Actualizar contadores
            if all_ok:
                c.execute(
                    "UPDATE recipes SET success_count = success_count + 1, "
                    "last_used = datetime('now'), updated_at = datetime('now') "
                    "WHERE id = ?", (recipe_id,)
                )
            else:
                c.execute(
                    "UPDATE recipes SET fail_count = fail_count + 1, "
                    "last_used = datetime('now') WHERE id = ?", (recipe_id,)
                )

            result["all_succeeded"] = all_ok
            result["ok"] = True

    except Exception as e:
        result["error"] = str(e)
        log.error("Error ejecutando receta: %s", e)

    return result


# ── Aprendizaje automático de recetas ────────────────────────────────────────


def learn_from_action(action: Dict[str, Any], result: Dict[str, Any],
                      context: Optional[Dict] = None) -> Optional[Dict[str, Any]]:
    """Aprende una receta de una acción exitosa ejecutada por el orchestrator.

    Si una secuencia de acciones tuvo éxito, la graba como receta para reusar.

    Args:
        action: La acción ejecutada {type, reason, ...}
        result: El resultado {ok, output, ...}
        context: Contexto {active_window, visible_text, ...}

    Returns: La receta creada o None si no se pudo aprender
    """
    if not result.get("ok"):
        return None

    action_type = action.get("type", action.get("action", ""))
    if not action_type:
        return None

    # Construir nombre de receta del contexto
    context_str = ""
    if context:
        active = context.get("active_window", "")
        text = context.get("visible_text", "")
        context_str = f"{active} {text}".strip()[:100]

    action_desc = action.get("reason", action_type)
    name = f"{action_type}: {action_desc[:60]}"

    # Crear paso
    steps = [{
        "action": action_type,
        "cmd": action.get("cmd", action.get("shell_cmd", "")),
        "target": action.get("target", action.get("url", "")),
        "description": action_desc,
    }]

    trigger = [action_type, action_desc]
    if context:
        trigger.extend(context.get("visible_text", "").split()[:5])

    return record_recipe(
        name=name,
        steps=steps,
        description=f"Aprendido de acción exitosa: {action_desc}",
        trigger_words=trigger,
        learned_from="autonomous",
        tags=["auto_learned", action_type],
    )


def discover_procedural_gaps() -> List[Dict[str, Any]]:
    """Encuentra conceptos del grafo de conocimiento que NO tienen receta.

    Compara los conceptos en evolution_brain.db con las recetas registradas.
    Los conceptos sin receta son "gaps procedimentales" — EIDOS sabe QUÉ son
    pero no CÓMO usarlos.

    Returns: [{concept, type, priority}, ...]
    """
    gaps = []
    try:
        brain_db = Path.home() / ".eidos" / "evolution_brain.db"
        if not brain_db.exists():
            return gaps

        from core.db import get_conn as _get_conn_proc
        brain = _get_conn_proc(str(brain_db), timeout=10)
        brain.row_factory = __import__('sqlite3').Row

        # Conceptos que son herramientas/acciones (apps, comandos)
        tool_concepts = brain.execute(
            "SELECT concept, category, source FROM knowledge_nodes "
            "WHERE concept LIKE 'app:%' OR concept LIKE 'cmd:%' "
            "OR category IN ('herramienta', 'comando', 'security_tool', 'kali_tool') "
            "LIMIT 200"
        ).fetchall()

        # Recetas existentes
        with get_conn(DB_PATH, timeout=5) as c:
            recipe_names = set(
                r[0] for r in c.execute("SELECT name FROM recipes").fetchall()
            )

        for tc in tool_concepts:
            concept = tc["concept"] if isinstance(tc, dict) else tc[0]
            if concept not in recipe_names:
                # Ver si ya está en gaps
                existing = False
                with get_conn(DB_PATH, timeout=5) as c:
                    ex = c.execute(
                        "SELECT 1 FROM procedural_gaps WHERE concept=? AND resolved=0",
                        (concept,)
                    ).fetchone()
                    existing = bool(ex)

                if not existing:
                    gaps.append({
                        "concept": concept,
                        "type": "no_recipe",
                        "priority": "high" if "tool" in (tc["category"] if isinstance(tc, dict) else "") else "medium",
                    })

        brain.close()
    except Exception as e:
        log.error("Error descubriendo gaps: %s", e)

    return gaps


# ── Utilidades ────────────────────────────────────────────────────────────────


def get_all_recipes(limit: int = 100) -> List[Dict[str, Any]]:
    """Lista todas las recetas registradas."""
    recipes = []
    try:
        with get_conn(DB_PATH, timeout=5) as c:
            rows = c.execute(
                "SELECT id, name, description, steps_json, success_count, "
                "fail_count, last_used, learned_from, tags_json "
                "FROM recipes ORDER BY success_count DESC LIMIT ?",
                (limit,)
            ).fetchall()
            for row in rows:
                recipes.append({
                    "id": row[0],
                    "name": row[1],
                    "description": row[2],
                    "steps_count": len(json.loads(row[3])),
                    "success_count": row[4],
                    "fail_count": row[5],
                    "last_used": row[6],
                    "learned_from": row[7],
                    "tags": json.loads(row[8]),
                })
    except Exception as e:
        log.error("Error listando recetas: %s", e)
    return recipes


def seed_core_recipes() -> List[Dict[str, Any]]:
    """Siembra recetas fundamentales de Kali Linux y uso del PC.

    Estas son las recetas básicas que todo usuario de Kali debe saber.
    EIDOS las aprende como punto de partida para luego descubrir más.
    """
    core_recipes = [
        {
            "name": "Escanear puertos con nmap",
            "description": "Escaneo básico de puertos TCP en un objetivo",
            "steps": [
                {"action": "terminal",
                 "cmd": "nmap -sV -sC -p 1-1000 {target} -oN /tmp/eidos_nmap_scan.txt",
                 "description": "Escanea 1000 puertos TCP con detección de versiones y scripts por defecto"},
                {"action": "filesystem",
                 "cmd": "read",
                 "target": "/tmp/eidos_nmap_scan.txt",
                 "description": "Leer resultados del escaneo"},
            ],
            "trigger_words": ["escanear puertos", "nmap", "port scan", "reconocimiento"],
            "preconditions": ["nmap instalado", "IP objetivo"],
            "postconditions": ["puertos abiertos identificados", "servicios detectados"],
            "tags": ["kali", "reconocimiento", "red", "nmap"],
        },
        {
            "name": "Actualizar sistema Kali",
            "description": "Actualiza todos los paquetes del sistema",
            "steps": [
                {"action": "terminal", "cmd": "sudo apt update",
                 "description": "Actualizar índice de paquetes"},
                {"action": "terminal", "cmd": "sudo apt upgrade -y",
                 "description": "Instalar actualizaciones"},
            ],
            "trigger_words": ["actualizar sistema", "apt update", "upgrade", "parches"],
            "preconditions": ["acceso sudo", "conexión a internet"],
            "postconditions": ["sistema actualizado"],
            "tags": ["kali", "sistema", "mantenimiento"],
        },
        {
            "name": "Buscar en la web con DuckDuckGo",
            "description": "Abre el navegador con una búsqueda",
            "steps": [
                {"action": "browse",
                 "target": "https://duckduckgo.com/?q={query}",
                 "description": "Buscar en DuckDuckGo"},
            ],
            "trigger_words": ["buscar", "search", "google", "duckduckgo", "internet"],
            "preconditions": ["firefox instalado"],
            "postconditions": ["resultados de búsqueda visibles"],
            "tags": ["web", "navegador", "búsqueda"],
        },
        {
            "name": "Ver uso de disco",
            "description": "Muestra el espacio en disco disponible",
            "steps": [
                {"action": "terminal", "cmd": "df -h && echo '---' && du -sh ~/* 2>/dev/null | sort -rh | head -10",
                 "description": "Mostrar espacio en disco y directorios más grandes"},
            ],
            "trigger_words": ["espacio disco", "disk usage", "df", "cuánto espacio"],
            "preconditions": [],
            "postconditions": ["uso de disco conocido"],
            "tags": ["sistema", "archivos", "diagnóstico"],
        },
        {
            "name": "Listar procesos activos",
            "description": "Muestra los procesos que más recursos consumen",
            "steps": [
                {"action": "terminal",
                 "cmd": "ps aux --sort=-%mem | head -15",
                 "description": "Top 15 procesos por uso de memoria"},
            ],
            "trigger_words": ["procesos", "ps", "top", "htop", "qué consume"],
            "preconditions": [],
            "postconditions": ["procesos identificados"],
            "tags": ["sistema", "diagnóstico", "procesos"],
        },
        {
            "name": "Instalar paquete con apt",
            "description": "Instala un paquete de software",
            "steps": [
                {"action": "terminal", "cmd": "sudo apt install -y {package}",
                 "description": "Instalar paquete"},
                {"action": "terminal", "cmd": "which {package} || dpkg -l | grep {package}",
                 "description": "Verificar instalación"},
            ],
            "trigger_words": ["instalar", "install", "apt install", "descargar paquete"],
            "preconditions": ["acceso sudo", "conexión a internet"],
            "postconditions": ["paquete instalado"],
            "tags": ["sistema", "paquetes", "apt"],
        },
        {
            "name": "Ejecutar script Python",
            "description": "Ejecuta un script Python en el sandbox",
            "steps": [
                {"action": "terminal",
                 "cmd": "python3 -u {script_path}",
                 "description": "Ejecutar script Python con output sin buffer"},
            ],
            "trigger_words": ["ejecutar python", "run python", "script py"],
            "preconditions": ["python3 instalado", "script existe"],
            "postconditions": ["script ejecutado"],
            "tags": ["python", "programación", "sandbox"],
        },
        {
            "name": "Compilar y ejecutar programa en C",
            "description": "Compila y ejecuta un programa en C",
            "steps": [
                {"action": "terminal",
                 "cmd": "gcc -Wall -o /tmp/eidos_bin {source_file} && /tmp/eidos_bin",
                 "description": "Compilar con warnings y ejecutar"},
            ],
            "trigger_words": ["compilar c", "gcc", "ejecutar c", "run c"],
            "preconditions": ["gcc instalado", "código fuente disponible"],
            "postconditions": ["programa compilado y ejecutado"],
            "tags": ["c", "programación", "compilador"],
        },
    ]

    results = []
    for recipe in core_recipes:
        r = record_recipe(
            name=recipe["name"],
            steps=recipe["steps"],
            description=recipe["description"],
            trigger_words=recipe["trigger_words"],
            preconditions=recipe["preconditions"],
            postconditions=recipe["postconditions"],
            tags=recipe["tags"],
            learned_from="seed_core",
        )
        results.append(r)

    return results


# ── CLI rápido ────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    import sys
    import json as _json

    if len(sys.argv) < 2:
        print("Uso: python eidos_procedural.py <cmd> [args]")
        print("  seed      — sembrar recetas core")
        print("  list      — listar todas las recetas")
        print("  recall <q>— buscar recetas")
        print("  gaps      — encontrar gaps procedimentales")
        sys.exit(1)

    cmd = sys.argv[1]

    if cmd == "seed":
        results = seed_core_recipes()
        print(_json.dumps(results, indent=2, ensure_ascii=False))

    elif cmd == "list":
        recipes = get_all_recipes()
        print(_json.dumps(recipes, indent=2, ensure_ascii=False))

    elif cmd == "recall":
        q = sys.argv[2] if len(sys.argv) > 2 else "escanear"
        recipes = recall_recipes(q)
        print(_json.dumps(recipes, indent=2, ensure_ascii=False))

    elif cmd == "gaps":
        gaps = discover_procedural_gaps()
        print(f"{len(gaps)} gaps encontrados:")
        for g in gaps[:20]:
            print(f"  - {g['concept']} [{g['priority']}]")

    else:
        print(f"Comando desconocido: {cmd}")
