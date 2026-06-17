#!/usr/bin/env python3
"""
core/eidos_pipeline.py — Deep Research Pipeline (S127)
========================================================
Encadena tareas multi-paso autónomas: buscar → clonar → compilar → analizar → aprender.
Usa los módulos existentes (research_now, study_queue, sandbox, knowledge_graph, BOM).
NO es un mega-orquestador. Es el pegamento que faltaba (~170 líneas).

Uso:
    python3 -m core.eidos_pipeline "investiga nginx, clónalo y compílalo"
    python3 -m core.eidos_pipeline "estudia el directorio /home/ser/MIS PROGRAMAS/blender"
    eidos pipeline "crea un canal de telegram sobre seguridad"
"""
from __future__ import annotations

import json
import logging
import os
import subprocess
import time
import urllib.parse
from pathlib import Path
from typing import Any, Dict, List, Optional

log = logging.getLogger("eidos.pipeline")

SANDBOX_DIR = Path.home() / ".eidos" / "sandbox"
SANDBOX_DIR.mkdir(parents=True, exist_ok=True)

# ── Fase 1: BUSCAR ──────────────────────────────────────────────────
def _phase_search(topic: str) -> Dict[str, Any]:
    """Investiga el tema usando research_now + APIs."""
    log.info("🔍 Pipeline FASE 1: buscando '%s'", topic)
    try:
        from core.eidos_active_research import research_now
        r = research_now(topic, timeout=20, persist=True, prefer_remote=True)
        definition = r.get("definition", "") if isinstance(r, dict) else str(r)[:500]
        channel = r.get("channel", "unknown") if isinstance(r, dict) else "unknown"
        return {"ok": True, "definition": definition[:1000], "channel": channel}
    except Exception as e:
        log.warning("research_now falló para '%s': %s", topic, e)
        # Fallback: GitHub API
        try:
            import urllib.request, json as j
            q = urllib.parse.quote(topic[:50])
            req = urllib.request.Request(f"https://api.github.com/search/repositories?q={q}&per_page=3",
                                         headers={"User-Agent": "EIDOS-Pipeline"})
            with urllib.request.urlopen(req, timeout=10) as resp:
                data = j.load(resp)
                items = data.get("items", [])
                if items:
                    r = items[0]
                    return {"ok": True, "definition": f"{r['full_name']}: {r.get('description','')}\n⭐ {r['stargazers_count']}\n{r['html_url']}", "channel": "github_api"}
        except Exception:
            pass
        return {"ok": False, "definition": str(e)[:200], "channel": "error"}

# ── Fase 2: CLONAR / DESCARGAR ─────────────────────────────────────
def _phase_clone(topic: str, search_result: Dict) -> Dict[str, Any]:
    """Clona el repositorio encontrado en la fase de búsqueda."""
    log.info("📦 Pipeline FASE 2: clonando para '%s'", topic)
    # Extraer URL de GitHub del resultado de búsqueda
    text = search_result.get("definition", "")
    import re
    urls = re.findall(r'https://github\.com/[\w.-]+/[\w.-]+', text)
    if not urls:
        # Intentar construir URL desde el topic
        slug = topic.lower().replace(" ", "-")[:30]
        urls = [f"https://github.com/search?q={urllib.parse.quote(topic)}"]

    repo_url = urls[0]
    if "/search?" in repo_url:
        return {"ok": False, "error": "no se encontró repo clonable", "url": repo_url}

    repo_name = repo_url.rstrip("/").split("/")[-1]
    target = SANDBOX_DIR / repo_name

    try:
        if target.exists():
            log.info("Repo ya existe en %s, actualizando...", target)
            subprocess.run(["git", "-C", str(target), "pull"], capture_output=True, timeout=30)
        else:
            subprocess.run(["git", "clone", "--depth", "1", repo_url, str(target)],
                         capture_output=True, timeout=60)
        return {"ok": True, "cloned_to": str(target), "url": repo_url}
    except Exception as e:
        log.warning("Clone falló: %s", e)
        return {"ok": False, "error": str(e)[:200], "url": repo_url}

# ── Fase 3: COMPILAR / INSTALAR ─────────────────────────────────────
def _phase_build(topic: str, clone_result: Dict) -> Dict[str, Any]:
    """Compila/instala el repo clonado."""
    target = clone_result.get("cloned_to", "")
    if not target or not Path(target).exists():
        return {"ok": False, "error": "no hay directorio para compilar"}

    log.info("🔨 Pipeline FASE 3: compilando '%s' en %s", topic, target)
    path = Path(target)

    # Detectar sistema de build
    build_commands = []
    if (path / "Makefile").exists():
        build_commands = ["make -j$(nproc) 2>&1 | tail -20"]
    elif (path / "CMakeLists.txt").exists():
        build_commands = ["mkdir -p build && cd build && cmake .. && make -j$(nproc) 2>&1 | tail -20"]
    elif (path / "go.mod").exists():
        build_commands = ["go build -o /tmp/eidos_build_output . 2>&1 | tail -10"]
    elif (path / "Cargo.toml").exists():
        build_commands = ["cargo build --release 2>&1 | tail -10"]
    elif (path / "setup.py").exists() or (path / "pyproject.toml").exists():
        build_commands = ["pip install -e . 2>&1 | tail -10"]
    elif (path / "package.json").exists():
        build_commands = ["npm install 2>&1 | tail -10"]

    if not build_commands:
        return {"ok": True, "summary": "no se detectó sistema de build (repo de documentación?)", "built": False}

    results = []
    for cmd in build_commands:
        try:
            r = subprocess.run(cmd, shell=True, cwd=str(path), capture_output=True, text=True, timeout=120)
            output = (r.stdout + r.stderr)[:500]
            results.append({"cmd": cmd[:60], "ok": r.returncode == 0, "output": output})
        except Exception as e:
            results.append({"cmd": cmd[:60], "ok": False, "output": str(e)[:200]})

    return {"ok": any(r["ok"] for r in results), "build_results": results, "built": True}

# ── Fase 4: ANALIZAR / EXPLORAR ────────────────────────────────────
def _phase_analyze(topic: str, clone_result: Dict, build_result: Dict) -> Dict[str, Any]:
    """Analiza la estructura del proyecto y extrae conocimiento."""
    target = clone_result.get("cloned_to", "")
    if not target or not Path(target).exists():
        return {"ok": False, "error": "no hay directorio que analizar"}

    log.info("📊 Pipeline FASE 4: analizando '%s'", topic)
    path = Path(target)

    analysis = {
        "total_files": 0,
        "languages": {},
        "readme_preview": "",
        "key_files": [],
    }

    try:
        # Contar archivos por extensión
        for f in path.rglob("*"):
            if f.is_file() and ".git" not in str(f):
                analysis["total_files"] += 1
                ext = f.suffix or "no_ext"
                analysis["languages"][ext] = analysis["languages"].get(ext, 0) + 1
    except Exception:
        pass

    # Leer README
    for readme_name in ["README.md", "README.rst", "README", "readme.md"]:
        readme_path = path / readme_name
        if readme_path.exists():
            try:
                analysis["readme_preview"] = readme_path.read_text(encoding="utf-8", errors="ignore")[:1500]
            except Exception:
                pass
            break

    # Archivos clave
    key_patterns = ["main.", "app.", "server.", "index.", "config.", "setup.", "Dockerfile", "Makefile"]
    for kf in key_patterns:
        matches = list(path.rglob(kf + "*"))
        for m in matches[:3]:
            if ".git" not in str(m):
                analysis["key_files"].append(str(m.relative_to(path)))

    # Persistir al grafo
    try:
        from core.knowledge_graph import KnowledgeGraph
        kg = KnowledgeGraph(verbose=False)
        concept = f"pipeline:analysis:{topic[:40]}"
        kg.add_node(concept, "concept", {"desc": json.dumps(analysis, ensure_ascii=False)[:1000]})
    except Exception:
        pass

    return {"ok": True, "analysis": analysis}

# ── Fase 5: APRENDER / PERSISTIR ────────────────────────────────────
def _phase_learn(topic: str, all_results: List[Dict]) -> Dict[str, Any]:
    """Sintetiza todo lo aprendido y persiste al grafo con alta confianza."""
    log.info("🧠 Pipeline FASE 5: aprendiendo y persistiendo '%s'", topic)

    summary_parts = []
    for i, r in enumerate(all_results):
        if r.get("ok"):
            phase_names = ["búsqueda", "clonado", "compilación", "análisis", "aprendizaje"]
            summary_parts.append(f"Fase {i+1} ({phase_names[i] if i < len(phase_names) else '?'}): OK")

    summary = " | ".join(summary_parts) if summary_parts else "sin resultados"

    try:
        from core.knowledge_graph import KnowledgeGraph
        kg = KnowledgeGraph(verbose=False)
        concept = f"pipeline:learned:{topic[:50]}"
        kg.add_node(concept, "concept", {
            "desc": f"Pipeline completo ejecutado para '{topic}'. {summary}",
            "phases": len([r for r in all_results if r.get("ok")]),
            "total_phases": len(all_results),
        })
        kg.add_edge(concept, topic[:50], "related_to")
    except Exception:
        pass

    return {"ok": True, "summary": summary, "phases_completed": len([r for r in all_results if r.get("ok")])}

# ── PIPELINE PRINCIPAL ──────────────────────────────────────────────
def deep_research(topic: str, dry_run: bool = False) -> Dict[str, Any]:
    """
    Pipeline completo de investigación profunda autónoma.
    
    Fases: buscar → clonar → compilar → analizar → aprender
    
    Args:
        topic: Tema a investigar (ej: "nginx", "rust", "evilginx")
        dry_run: Si True, solo busca y analiza (no clona ni compila)
    
    Returns:
        Dict con resultados de cada fase y resumen
    """
    started = time.time()
    results = []

    # Fase 1: Buscar (siempre)
    r1 = _phase_search(topic)
    results.append({"phase": "search", **r1})

    if dry_run:
        # Solo búsqueda + análisis del resultado
        r5 = _phase_learn(topic, results)
        results.append({"phase": "learn", **r5})
        return {"topic": topic, "dry_run": True, "phases": results,
                "elapsed_s": round(time.time() - started, 1)}

    # Fase 2: Clonar
    if r1.get("ok"):
        r2 = _phase_clone(topic, r1)
    else:
        r2 = {"ok": False, "error": "fase 1 falló, no se puede clonar"}
    results.append({"phase": "clone", **r2})

    # Fase 3: Compilar
    if r2.get("ok"):
        r3 = _phase_build(topic, r2)
    else:
        r3 = {"ok": False, "error": "fase 2 falló, no se puede compilar"}
    results.append({"phase": "build", **r3})

    # Fase 4: Analizar
    if r2.get("ok"):
        r4 = _phase_analyze(topic, r2, r3)
    else:
        r4 = {"ok": False, "error": "no hay repo que analizar"}
    results.append({"phase": "analyze", **r4})

    # Fase 6: Sintetizar (conclusiones propias)
    r6 = _phase_synthesize(topic, results)
    results.append({"phase": "synthesize", **r6})

    # Fase 5: Aprender
    r5 = _phase_learn(topic, results)
    results.append({"phase": "learn", **r5})

    return {"topic": topic, "dry_run": False, "phases": results,
            "elapsed_s": round(time.time() - started, 1)}

# ── DIRECTORIO / APP ────────────────────────────────────────────────
def study_directory(path: str) -> Dict[str, Any]:
    """Estudia un directorio a fondo: estructura, archivos, documentación."""
    p = Path(path).expanduser().resolve()
    if not p.exists():
        return {"ok": False, "error": f"no existe: {path}"}

    result = {"path": str(p), "exists": True, "total_files": 0, "total_dirs": 0,
              "file_types": {}, "readmes": [], "largest_files": []}

    try:
        for item in p.rglob("*"):
            if item.is_file() and ".git" not in str(item):
                result["total_files"] += 1
                ext = item.suffix or "no_ext"
                result["file_types"][ext] = result["file_types"].get(ext, 0) + 1
                if "readme" in item.name.lower():
                    try:
                        preview = item.read_text(encoding="utf-8", errors="ignore")[:300]
                        result["readmes"].append({"file": item.name, "preview": preview})
                    except Exception:
                        pass
                try:
                    size = item.stat().st_size
                    if len(result["largest_files"]) < 10:
                        result["largest_files"].append({"name": item.name, "size": size})
                except Exception:
                    pass
            elif item.is_dir():
                result["total_dirs"] += 1
    except Exception as e:
        result["error"] = str(e)[:200]

    # Persistir
    try:
        from core.knowledge_graph import KnowledgeGraph
        kg = KnowledgeGraph(verbose=False)
        kg.add_node(f"pipeline:dir:{p.name}", "concept", {"desc": json.dumps(result, ensure_ascii=False)[:1000]})
    except Exception:
        pass

    return {"ok": True, **result}

# ── CLI ──────────────────────────────────────────────────────────────

# ── Fase 6: SINTETIZAR (conclusiones propias) ────────────────────
def _phase_synthesize(topic: str, all_results: List[Dict]) -> Dict[str, Any]:
    """Genera conclusiones propias usando deep_comprehension sobre el texto acumulado."""
    log.info("🧠 Pipeline FASE 6: sintetizando conclusiones para '%s'", topic)
    
    # Recopilar todo el texto de fases anteriores
    texts = []
    for r in all_results:
        if r.get("definition"):
            texts.append(str(r["definition"])[:2000])
        if r.get("analysis") and r["analysis"].get("readme_preview"):
            texts.append(r["analysis"]["readme_preview"][:2000])
    
    combined = "\n\n".join(texts)[:8000]
    if len(combined) < 200:
        return {"ok": False, "error": "poco texto para sintetizar"}
    
    try:
        from core.eidos_deep_comprehension import comprehend
        analysis = comprehend(combined, topic=topic)
        summary = analysis.get("summary", "") if isinstance(analysis, dict) else str(analysis)[:500]
        
        # Persistir conclusión al grafo
        try:
            from core.knowledge_graph import KnowledgeGraph
            kg = KnowledgeGraph(verbose=False)
            concept = f"pipeline:conclusion:{topic[:50]}"
            kg.add_node(concept, "concept", {
                "desc": f"CONCLUSIÓN PROPIA sobre '{topic}': {summary}",
                "source": "pipeline_synthesis",
                "confidence": 0.75
            })
        except Exception:
            pass
        
        return {"ok": True, "summary": summary[:500], "synthesized": True}
    except Exception as e:
        # Fallback: síntesis local simple
        words = combined.split()
        unique = len(set(w.lower() for w in words if len(w) > 4))
        return {"ok": True, "summary": f"Análisis de {len(words)} palabras, {unique} conceptos únicos sobre '{topic}'.", "synthesized": False, "error": str(e)[:100]}


if __name__ == "__main__":
    import sys
    logging.basicConfig(level=logging.INFO)

    if len(sys.argv) < 2:
        print("Uso: python3 -m core.eidos_pipeline <tema> [--dry-run]")
        print("      python3 -m core.eidos_pipeline --dir <ruta>")
        sys.exit(1)

    if sys.argv[1] == "--dir":
        result = study_directory(sys.argv[2])
    else:
        topic = " ".join(sys.argv[1:]).replace(" --dry-run", "")
        dry_run = "--dry-run" in " ".join(sys.argv)
        result = deep_research(topic, dry_run=dry_run)

    print(json.dumps(result, indent=2, ensure_ascii=False))
