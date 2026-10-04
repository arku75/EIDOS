"""
core/vseidos_learner.py — VSEIDOS: instalar → aprender → soltar [S123-C]
========================================================================
Visión de SER (2026-06-10): "si EIDOS descarga una extensión de lenguaje
o lo que sea, la aprende, y como ya la sabe, la borra — y podrá ver,
utilizar y todo lo que quiera dentro" de su propio VS Code.

Ciclo de vida de una extensión en el editor de EIDOS:

  1. install(ext_id)   — la instala en el perfil VSEIDOS (SU editor,
                         no toca el VS Code de SER)
  2. learn(ext_id)     — la LEE de verdad: package.json (qué hace, sus
                         comandos, sus settings, sus lenguajes) + README
                         → nodos al grafo. Dueño: colony_coder — la
                         neurona de desarrollo crece con cada extensión
                         aprendida (core/character_neuron.py).
  3. release(ext_id)   — la desinstala: ya la sabe, no necesita tenerla.

  learn_cycle(ext_id)  — el ciclo completo 1→2→3 (keep=True la conserva).

Aprendizaje DETERMINISTA (sin LLM): el manifiesto de una extensión es
información estructurada — EIDOS la lee, no la imagina.
"""

from __future__ import annotations

import hashlib
import json
import logging
import re
import subprocess
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

from core.db import get_conn_ctx
from core.paths import EIDOS_HOME, REPO_ROOT

log = logging.getLogger("eidos.vseidos")

VSEIDOS_BIN = REPO_ROOT / "VSEIDOS" / "bin" / "vseidos"
EXT_DIR = REPO_ROOT / "VSEIDOS" / "extensions"
BRAIN_DB = EIDOS_HOME / "evolution_brain.db"

# Las extensiones aprendidas alimentan la neurona del desarrollador
KNOWLEDGE_OWNER = "colony_coder"


def _run_code(*args: str, timeout: int = 180) -> subprocess.CompletedProcess:
    """Ejecuta el VS Code de EIDOS (perfil VSEIDOS) en modo CLI."""
    return subprocess.run(
        [str(VSEIDOS_BIN), *args],
        capture_output=True, text=True, timeout=timeout,
    )


def list_installed() -> List[str]:
    """Extensiones instaladas en el editor de EIDOS."""
    try:
        res = _run_code("--list-extensions", timeout=60)
        return [l.strip() for l in res.stdout.splitlines() if l.strip()]
    except Exception as e:
        log.debug("list_installed: %s", e)
        return []


def install(ext_id: str) -> Dict[str, Any]:
    """Instala una extensión en VSEIDOS (el editor de EIDOS)."""
    try:
        res = _run_code("--install-extension", ext_id)
        ok = "successfully installed" in (res.stdout + res.stderr).lower() \
            or "already installed" in (res.stdout + res.stderr).lower()
        return {"ok": ok, "ext": ext_id,
                "detail": (res.stdout + res.stderr).strip()[-200:]}
    except Exception as e:
        return {"ok": False, "ext": ext_id, "detail": str(e)}


def release(ext_id: str) -> Dict[str, Any]:
    """Desinstala una extensión: EIDOS ya la sabe, la suelta."""
    try:
        res = _run_code("--uninstall-extension", ext_id)
        ok = "successfully uninstalled" in (res.stdout + res.stderr).lower()
        return {"ok": ok, "ext": ext_id,
                "detail": (res.stdout + res.stderr).strip()[-200:]}
    except Exception as e:
        return {"ok": False, "ext": ext_id, "detail": str(e)}


# ── Aprendizaje ───────────────────────────────────────────────────────────────

def _find_ext_dir(ext_id: str) -> Optional[Path]:
    """Carpeta instalada de la extensión (ej. ms-python.python-2026.4.0)."""
    low = ext_id.lower()
    candidates = sorted(
        (d for d in EXT_DIR.iterdir()
         if d.is_dir() and d.name.lower().startswith(low + "-")),
        key=lambda d: d.name, reverse=True,
    )
    return candidates[0] if candidates else None


def _readme_essence(ext_path: Path, max_lines: int = 14) -> str:
    """Las primeras líneas con sustancia del README (sin badges/html)."""
    for name in ("README.md", "readme.md", "README.txt"):
        f = ext_path / name
        if f.exists():
            try:
                lines = []
                for raw in f.read_text(errors="replace").splitlines():
                    line = raw.strip()
                    if not line or line.startswith(("[![", "![", "<", "|", "---")):
                        continue
                    line = re.sub(r"[#*`]", "", line).strip()
                    if len(line) > 25:
                        lines.append(line)
                    if len(lines) >= max_lines:
                        break
                return " ".join(lines)[:1200]
            except Exception:
                pass
    return ""


def _save_nodes(ext_id: str, facts: List[Dict[str, str]]) -> int:
    """Guarda lo aprendido en el grafo, con dueño (neurona de coder)."""
    saved = 0
    with get_conn_ctx(BRAIN_DB, cache=False) as conn:
        for fact in facts:
            concept = fact["concept"][:200]
            definition = fact["definition"][:1200]
            if len(definition) < 15:
                continue
            node_id = hashlib.md5(
                f"vseidos:{ext_id}:{concept}".encode()).hexdigest()[:16]
            cur = conn.execute(
                """INSERT OR IGNORE INTO knowledge_nodes
                   (id, concept, definition, category, source, confidence,
                    created_at, character, quality_score)
                   VALUES (?,?,?,?,?,?,?,?,?)""",
                (node_id, concept, definition, "vseidos_extension",
                 f"vseidos:ext:{ext_id}", 0.7, time.time(),
                 KNOWLEDGE_OWNER, 0.6),
            )
            saved += cur.rowcount or 0
        conn.commit()
    return saved


def learn(ext_id: str) -> Dict[str, Any]:
    """EIDOS LEE la extensión instalada y la aprende al grafo.

    Determinista: manifiesto (package.json) + esencia del README.
    """
    ext_path = _find_ext_dir(ext_id)
    if not ext_path:
        return {"ok": False, "ext": ext_id,
                "detail": "no está instalada (instala primero)"}

    facts: List[Dict[str, str]] = []
    try:
        pkg = json.loads((ext_path / "package.json").read_text(errors="replace"))
    except Exception as e:
        return {"ok": False, "ext": ext_id, "detail": f"package.json ilegible: {e}"}

    name = pkg.get("displayName") or pkg.get("name") or ext_id
    desc = (pkg.get("description") or "").strip()
    contributes = pkg.get("contributes", {}) or {}

    # 1. Qué ES la extensión
    essence = _readme_essence(ext_path)
    facts.append({
        "concept": f"[VSCode ext] {name}",
        "definition": (f"{desc}. {essence}" if essence else desc) or name,
    })

    # 2. Sus comandos (lo que EIDOS puede INVOCAR en su editor)
    commands = contributes.get("commands", []) or []
    if commands:
        cmd_lines = "; ".join(
            f"{c.get('command')}: {c.get('title', '')}"
            for c in commands[:25] if c.get("command"))
        facts.append({
            "concept": f"[VSCode ext] {name} — comandos",
            "definition": f"Comandos que aporta {name} en VSEIDOS: {cmd_lines}",
        })

    # 3. Sus lenguajes/gramáticas (qué le enseña a entender)
    languages = contributes.get("languages", []) or []
    if languages:
        lang_lines = ", ".join(
            f"{l.get('id')} ({','.join(l.get('extensions', [])[:4])})"
            for l in languages[:15] if l.get("id"))
        facts.append({
            "concept": f"[VSCode ext] {name} — lenguajes",
            "definition": f"{name} enseña al editor a entender: {lang_lines}",
        })

    # 4. Sus settings clave (cómo se configura)
    config = contributes.get("configuration", {})
    props = {}
    if isinstance(config, dict):
        props = config.get("properties", {}) or {}
    elif isinstance(config, list):
        for c in config:
            props.update((c or {}).get("properties", {}))
    if props:
        prop_lines = "; ".join(
            f"{k}: {(v or {}).get('description', '')[:80]}"
            for k, v in list(props.items())[:15])
        facts.append({
            "concept": f"[VSCode ext] {name} — configuración",
            "definition": f"Settings de {name}: {prop_lines}",
        })

    saved = _save_nodes(ext_id, facts)
    log.info("VSEIDOS aprendió %s: %d hechos (%d nuevos al grafo)",
             ext_id, len(facts), saved)
    return {"ok": True, "ext": ext_id, "facts": len(facts),
            "new_nodes": saved, "owner": KNOWLEDGE_OWNER}


def learn_cycle(ext_id: str, keep: bool = False) -> Dict[str, Any]:
    """El ciclo completo de SER: instalar → aprender → soltar.

    keep=True la conserva instalada (lenguajes que EIDOS usará a diario).
    """
    result: Dict[str, Any] = {"ext": ext_id, "steps": {}}
    already = ext_id.lower() in (e.lower() for e in list_installed())

    if not already:
        result["steps"]["install"] = install(ext_id)
        if not result["steps"]["install"]["ok"]:
            result["ok"] = False
            return result

    result["steps"]["learn"] = learn(ext_id)

    if not keep and not already:
        # Ya la sabe: la suelta (si ya estaba instalada antes, se respeta)
        result["steps"]["release"] = release(ext_id)

    result["ok"] = result["steps"]["learn"]["ok"]
    return result


def learned_extensions() -> List[Dict[str, Any]]:
    """Qué extensiones tiene EIDOS aprendidas en el grafo."""
    out: List[Dict[str, Any]] = []
    try:
        with get_conn_ctx(BRAIN_DB, cache=False) as conn:
            rows = conn.execute(
                """SELECT source, COUNT(*) FROM knowledge_nodes
                   WHERE source LIKE 'vseidos:ext:%'
                   GROUP BY source ORDER BY 2 DESC"""
            ).fetchall()
        for source, n in rows:
            out.append({"ext": source.replace("vseidos:ext:", ""), "nodos": n})
    except Exception as e:
        log.debug("learned_extensions: %s", e)
    return out
