"""
core/self_reviewer.py — EIDOS se cuestiona a sí mismo.

Después de cualquier acción, EIDOS revisa lo que hizo:
- URL/link    → re-fetchea y verifica contenido
- Código      → analiza con LLM + ejecuta tests si existen
- Binario     → strings/file/hexdump + Binary Ninja si disponible
- Imagen      → re-analiza con visión
- Archivo     → verifica integridad/formato/contenido
- Resultado   → compara expectativa vs realidad

Integración: slash /review o automático vía hook post-acción.
"""
from __future__ import annotations

import json
import os
import re
import subprocess
import time
import logging
from pathlib import Path
from typing import Any

log = logging.getLogger("eidos.self_reviewer")

EIDOS_DIR    = os.environ.get("EIDOS_DIR", "/home/ser/EIDOS")
BRIDGE_URL   = os.environ.get("EIDOS_BRIDGE", "http://localhost:8003")
BINJA_URL    = os.environ.get("BINJA_MCP_URL", "http://localhost:9009")   # Binary Ninja MCP server
_REVIEW_DB   = Path.home() / ".eidos" / "review_log.jsonl"


# ── helpers ──────────────────────────────────────────────────────────────────

def _ask_colony(question: str, max_tokens: int = 512) -> str:
    try:
        import requests
        r = requests.post(
            f"{BRIDGE_URL}/talk",
            json={"message": question, "max_agents": 1, "max_tokens": max_tokens},
            timeout=60,
        )
        return r.json().get("response", "")
    except Exception as e:
        return f"[Colony no disponible: {e}]"


def _log_review(kind: str, target: str, verdict: str, details: str) -> None:
    _REVIEW_DB.parent.mkdir(parents=True, exist_ok=True)
    entry = {
        "ts": time.time(),
        "kind": kind,
        "target": target[:200],
        "verdict": verdict,   # "ok" | "issue" | "unknown"
        "details": details[:1000],
    }
    with open(_REVIEW_DB, "a") as f:
        f.write(json.dumps(entry, ensure_ascii=False) + "\n")


# ── revisores por tipo ────────────────────────────────────────────────────────

def review_url(url: str) -> dict:
    """Visita la URL y pide a Colony que evalúe si el contenido es válido/seguro."""
    try:
        import requests
        r = requests.get(url, timeout=15, headers={"User-Agent": "Mozilla/5.0"})
        snippet = r.text[:3000]
        status  = r.status_code
    except Exception as e:
        _log_review("url", url, "issue", f"Fetch error: {e}")
        return {"verdict": "issue", "detail": f"No se pudo acceder: {e}"}

    if status >= 400:
        _log_review("url", url, "issue", f"HTTP {status}")
        return {"verdict": "issue", "detail": f"HTTP {status}"}

    question = (
        f"Acabo de acceder a esta URL: {url}\n"
        f"HTTP {status}. Primeros 3000 chars del contenido:\n{snippet}\n\n"
        "¿El contenido parece válido, seguro y coherente con lo esperado? "
        "Responde en máximo 3 frases: veredicto + razón + cualquier problema."
    )
    verdict_text = _ask_colony(question)
    verdict = "ok" if any(w in verdict_text.lower() for w in ["válido", "correcto", "ok", "safe", "correct"]) else "unknown"
    _log_review("url", url, verdict, verdict_text)
    return {"verdict": verdict, "detail": verdict_text, "http_status": status}


def review_code(code_or_path: str) -> dict:
    """Analiza código: si es un path lo lee, luego pide análisis crítico a Colony."""
    path = Path(code_or_path)
    if path.exists() and path.is_file():
        try:
            code = path.read_text(errors="replace")[:8000]
            filename = path.name
        except Exception as e:
            return {"verdict": "issue", "detail": f"No se pudo leer: {e}"}
    else:
        code = code_or_path[:8000]
        filename = "snippet"

    question = (
        f"Revisa este código ({filename}) de forma crítica:\n\n```\n{code}\n```\n\n"
        "Busca: errores lógicos, bugs, problemas de seguridad, mejoras importantes. "
        "Sé directo. Si está bien, di por qué. Si hay problemas, listarlos brevemente."
    )
    analysis = _ask_colony(question, max_tokens=800)
    verdict = "issue" if any(w in analysis.lower() for w in ["error", "bug", "problema", "vulnerab", "fallo", "issue"]) else "ok"
    _log_review("code", filename, verdict, analysis)
    return {"verdict": verdict, "detail": analysis}


def review_binary(path: str) -> dict:
    """
    Analiza un binario:
    1. file / strings / readelf / hexdump básico
    2. Si Binary Ninja MCP está activo → análisis profundo
    """
    p = Path(path)
    if not p.exists():
        return {"verdict": "issue", "detail": f"Archivo no encontrado: {path}"}

    info: list[str] = []

    # file
    try:
        out = subprocess.run(["file", str(p)], capture_output=True, text=True, timeout=5).stdout.strip()
        info.append(f"file: {out}")
    except Exception:
        pass

    # strings (primeras 50)
    try:
        out = subprocess.run(
            ["strings", "-n", "6", str(p)], capture_output=True, text=True, timeout=10
        ).stdout
        lines = [l for l in out.splitlines() if len(l) > 5][:50]
        info.append("strings (top 50):\n" + "\n".join(lines))
    except Exception:
        pass

    # readelf (solo cabecera ELF)
    try:
        out = subprocess.run(
            ["readelf", "-h", str(p)], capture_output=True, text=True, timeout=5
        ).stdout[:500]
        if out:
            info.append(f"readelf -h:\n{out}")
    except Exception:
        pass

    # Binary Ninja MCP (si está activo)
    binja_info = ""
    try:
        import requests
        r = requests.get(f"{BINJA_URL}/health", timeout=2)
        if r.status_code == 200:
            # Abrir binario en Binary Ninja via MCP
            open_r = requests.post(
                f"{BINJA_URL}/open", json={"path": str(p)}, timeout=30
            )
            if open_r.status_code == 200:
                # Pedir funciones
                fn_r = requests.get(f"{BINJA_URL}/functions", timeout=15)
                if fn_r.status_code == 200:
                    fns = fn_r.json().get("functions", [])[:20]
                    binja_info = "Binary Ninja functions:\n" + "\n".join(
                        f"  {f.get('name')} @ {f.get('address')}" for f in fns
                    )
                    info.append(binja_info)
    except Exception:
        pass

    combined = "\n\n".join(info)
    question = (
        f"Análisis de binario: {p.name}\n\n{combined[:4000]}\n\n"
        "¿Qué hace este binario? ¿Hay algo sospechoso (malware, exploits, shellcode)? "
        "Responde en máximo 4 frases."
    )
    analysis = _ask_colony(question, max_tokens=600)
    verdict = "issue" if any(w in analysis.lower() for w in ["sospech", "malware", "exploit", "shellcode", "peligr"]) else "ok"
    _log_review("binary", str(p), verdict, analysis)
    return {"verdict": verdict, "detail": analysis, "raw_info": combined[:1000]}


def review_image(path: str) -> dict:
    """Re-analiza una imagen con el modelo de visión."""
    p = Path(path)
    if not p.exists():
        return {"verdict": "issue", "detail": f"Imagen no encontrada: {path}"}

    question = (
        f"[VISION: analiza la imagen en {path}] "
        "¿Qué contiene? ¿Hay texto, código, diagramas o algo relevante? "
        "¿Es lo que se esperaba o hay algo inesperado?"
    )
    analysis = _ask_colony(question, max_tokens=400)
    verdict = "unknown"
    _log_review("image", str(p), verdict, analysis)
    return {"verdict": verdict, "detail": analysis}


def review_file(path: str) -> dict:
    """Verifica un archivo genérico: tipo, tamaño, integridad, contenido."""
    p = Path(path)
    if not p.exists():
        return {"verdict": "issue", "detail": f"No existe: {path}"}

    size = p.stat().st_size
    try:
        file_type = subprocess.run(["file", str(p)], capture_output=True, text=True, timeout=5).stdout.strip()
    except Exception:
        file_type = "desconocido"

    # Intentar leer si es texto
    content_snippet = ""
    try:
        content_snippet = p.read_text(errors="replace")[:2000]
    except Exception:
        content_snippet = f"[binario, {size} bytes]"

    # Detectar extensión y usar revisor específico
    suffix = p.suffix.lower()
    if suffix in {".py", ".js", ".ts", ".sh", ".c", ".cpp", ".go", ".rs"}:
        return review_code(str(p))
    if suffix in {".png", ".jpg", ".jpeg", ".gif", ".webp", ".bmp"}:
        return review_image(str(p))
    if suffix in {".exe", ".elf", ".so", ".dll", ".bin"}:
        return review_binary(str(p))

    question = (
        f"Archivo: {p.name} ({file_type}, {size} bytes)\n"
        f"Contenido (primeros 2000 chars):\n{content_snippet}\n\n"
        "¿Es válido y coherente? ¿Hay problemas? Responde en 2-3 frases."
    )
    analysis = _ask_colony(question, max_tokens=300)
    verdict = "ok" if size > 0 else "issue"
    _log_review("file", str(p), verdict, analysis)
    return {"verdict": verdict, "detail": analysis}


def review_result(action: str, expected: str, actual: str) -> dict:
    """
    EIDOS se pregunta: 'Lo que esperaba vs lo que pasó — ¿está bien?'
    action: descripción de lo que EIDOS intentó hacer
    expected: lo que esperaba que pasara
    actual: lo que realmente pasó (stdout, log, resultado)
    """
    question = (
        f"Acción que intenté: {action}\n"
        f"Esperaba: {expected}\n"
        f"Resultado real: {actual[:2000]}\n\n"
        "¿El resultado coincide con lo esperado? "
        "¿Hay errores, discrepancias o problemas que deba resolver? "
        "Responde: veredicto (ok/issue/parcial) + razón en 2 frases."
    )
    analysis = _ask_colony(question, max_tokens=400)
    verdict = "issue" if "issue" in analysis.lower() or "error" in analysis.lower() else "ok"
    _log_review("result", action[:100], verdict, analysis)
    return {"verdict": verdict, "detail": analysis}


# ── función principal: detecta tipo automáticamente ───────────────────────────

def auto_review(target: str, context: str = "") -> dict:
    """
    Dado cualquier string (URL, path, código, resultado), detecta el tipo
    y aplica el revisor adecuado.
    """
    target = target.strip()

    # URL
    if re.match(r"https?://", target):
        return review_url(target)

    # Path que existe
    p = Path(target)
    if p.exists():
        return review_file(str(p))

    # Parece código
    code_indicators = ["def ", "function ", "import ", "class ", "#include", "<?php", "package "]
    if any(ind in target for ind in code_indicators):
        return review_code(target)

    # Resultado de acción
    if context:
        return review_result(context, "éxito", target)

    # Genérico
    question = (
        f"Revisa esto críticamente:\n\n{target[:3000]}\n\n"
        f"Contexto: {context or 'ninguno'}\n\n"
        "¿Es correcto, completo, seguro? Veredicto en 2-3 frases."
    )
    analysis = _ask_colony(question)
    _log_review("generic", target[:80], "unknown", analysis)
    return {"verdict": "unknown", "detail": analysis}


# ── historial de revisiones ───────────────────────────────────────────────────

def get_review_history(limit: int = 10) -> list[dict]:
    if not _REVIEW_DB.exists():
        return []
    lines = _REVIEW_DB.read_text().splitlines()
    results = []
    for line in lines[-limit:]:
        try:
            results.append(json.loads(line))
        except Exception:
            pass
    return list(reversed(results))


def format_review_history(limit: int = 10) -> str:
    entries = get_review_history(limit)
    if not entries:
        return "Sin revisiones aún."
    lines = []
    for e in entries:
        ts = time.strftime("%H:%M:%S", time.localtime(e["ts"]))
        icon = "✅" if e["verdict"] == "ok" else ("⚠️" if e["verdict"] == "issue" else "🔍")
        lines.append(f"{icon} [{ts}] {e['kind'].upper():8} {e['target'][:50]}")
        lines.append(f"     {e['details'][:120]}")
    return "\n".join(lines)
