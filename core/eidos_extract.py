"""
core/eidos_extract.py — Extractor estructurado de conocimiento con LFM2.5-350m [S121]
====================================================================================
Problema que resuelve: cuando un LLM (Groq/DeepSeek/local/Mac) responde a una
pregunta, EIDOS aprende de esa respuesta con `logic.learn_from_text()`, que usa
regex. Eso captura hechos sueltos pero NO produce un par concepto→definición
limpio para el grafo de conocimiento.

Este módulo usa el modelo LiquidAI **lfm2.5-350m** (ultra-rápido, especializado en
"data extraction and tool use") para destilar de un texto libre UN par estructurado
{concept, definition} listo para insertar como knowledge_node — y lo pasa por el
PORTERO DE CALIDAD (eidos_quality_gate) antes de tocar la DB.

Diseño (respeta CLAUDE.md):
  - Módulo NUEVO, no infla core/ existente.
  - Reutiliza _call_ollama (cascada) y el portero `gate` — no reinventa nada.
  - El modelo 350m es OPCIONAL: si no está instalado o falla, devuelve None y
    el flujo de aprendizaje sigue como antes (regex). Nunca rompe el aprendizaje.
  - Solo INSERT OR IGNORE: nunca pisa nodos existentes mejores.

Uso:
    from core.eidos_extract import extract_and_learn
    n = extract_and_learn(answer_text, source="extract:groq")  # nodos insertados
"""
from __future__ import annotations

import json
import logging
import os
import re
import time
from pathlib import Path
from typing import Optional, Tuple

log = logging.getLogger("eidos.extract")

BRAIN_DB = Path.home() / ".eidos" / "evolution_brain.db"

# Modelo Extract de LiquidAI. Configurable por SER vía env.
EXTRACT_MODEL = os.environ.get("EIDOS_EXTRACT_MODEL", "LiquidAI/lfm2.5-350m:latest")

# Prompt de extracción: pedimos JSON estricto {concept, definition}.
_EXTRACT_SYSTEM = (
    "Eres un extractor de conocimiento. Te doy un texto y devuelves SOLO un JSON "
    "con el concepto principal y su definición concisa. Formato EXACTO, sin nada más:\n"
    '{"concept": "<término principal en minúsculas>", '
    '"definition": "<definición clara de 1-2 frases>"}\n'
    "Si el texto no define nada concreto, devuelve {\"concept\": \"\", \"definition\": \"\"}."
)

# Disponibilidad del modelo cacheada (no sondear Ollama en cada llamada).
_model_cache = {"ts": 0.0, "available": False}


def _extract_model_available(cache_secs: int = 300) -> bool:
    """¿Está el modelo 350m instalado en Ollama? Cacheado 5 min."""
    now = time.time()
    if now - _model_cache["ts"] < cache_secs:
        return _model_cache["available"]
    available = False
    try:
        import urllib.request
        with urllib.request.urlopen("http://localhost:11434/api/tags", timeout=4) as r:
            data = json.loads(r.read())
        names = {m.get("name", "") for m in data.get("models", [])}
        base = EXTRACT_MODEL.split(":")[0]
        available = any(n == EXTRACT_MODEL or n.split(":")[0] == base for n in names)
    except Exception as e:  # noqa: BLE001
        log.debug("no se pudo verificar modelo extract: %s", e)
        available = False
    _model_cache.update({"ts": now, "available": available})
    return available


def _parse_json_pair(raw: str) -> Optional[Tuple[str, str]]:
    """Extrae {concept, definition} de la salida del modelo (tolerante a ruido)."""
    if not raw:
        return None
    # Buscar el primer bloque {...}
    m = re.search(r"\{.*?\}", raw, re.DOTALL)
    if not m:
        return None
    try:
        obj = json.loads(m.group(0))
    except Exception:
        return None
    concept = str(obj.get("concept", "")).strip().lower()
    definition = str(obj.get("definition", "")).strip()
    if not concept or not definition:
        return None
    return concept[:200], definition[:500]


def extract_pair(text: str, subject_hint: str = "",
                 timeout: int = 40) -> Optional[Tuple[str, str]]:
    """Destila de `text` un par (concept, definition) usando el modelo 350m.

    `subject_hint` ancla el concepto al sujeto de la pregunta (p.ej. "nmap"),
    porque un modelo de 350M tiende a coger sub-frases si no se le guía.

    Devuelve None si el modelo no está, falla, o no hay nada que extraer.
    """
    if not text or len(text) < 30:
        return None
    if not _extract_model_available():
        return None
    try:
        from core.eidos_learn import _call_ollama  # reutiliza la cascada
        hint = ""
        if subject_hint:
            hint = (f'\nEl concepto principal del texto es "{subject_hint}". '
                    f'Usa exactamente "{subject_hint}" como "concept".')
        # Construimos el prompt como un único mensaje (system+user) para 350m.
        prompt = f"{_EXTRACT_SYSTEM}{hint}\n\nTexto:\n{text[:1200]}"
        raw = _call_ollama(prompt, model=EXTRACT_MODEL, timeout=timeout,
                           temperature=0.1)
        pair = _parse_json_pair(raw)
        # Si dimos hint pero el modelo se desvió, forzamos el sujeto como concepto.
        if pair and subject_hint:
            _, definition = pair
            return subject_hint.strip().lower()[:200], definition
        return pair
    except Exception as e:  # noqa: BLE001
        log.debug("extract_pair falló: %s", e)
        return None


def extract_and_learn(text: str, source: str = "extract:llm",
                      subject_hint: str = "",
                      confidence: float = 0.7, timeout: int = 40) -> int:
    """Extrae un par concepto→definición de `text`, lo pasa por el portero de
    calidad y lo inserta en el grafo si lo admite. Devuelve nº de nodos insertados.

    NO rompe nada si el modelo no está: simplemente devuelve 0.
    """
    pair = extract_pair(text, subject_hint=subject_hint, timeout=timeout)
    if not pair:
        return 0
    concept, definition = pair

    # Portero de calidad (reutiliza _compute_quality vía el gate).
    try:
        from core.eidos_quality_gate import gate
        verdict = gate.evaluate(concept, definition, source, "general", confidence)
    except Exception as e:  # noqa: BLE001
        log.debug("extract_and_learn: portero no disponible (%s)", e)
        return 0
    if not verdict.admit:
        log.debug("extract_and_learn: portero filtró '%s': %s", concept[:40], verdict.reason)
        return 0

    # Insertar (OR IGNORE: nunca pisa un nodo existente).
    try:
        from core.db import get_conn
        conn = get_conn(BRAIN_DB, timeout=10)
        conn.execute("PRAGMA journal_mode=WAL")
        cur = conn.execute(
            "INSERT OR IGNORE INTO knowledge_nodes "
            "(id, concept, definition, category, confidence, source, quality_score) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            (
                f"extract_{int(time.time())}_{abs(hash(concept)) % 100000}",
                concept, definition, "general", confidence, source,
                verdict.quality_score,
            ),
        )
        conn.commit()
        inserted = cur.rowcount if cur.rowcount and cur.rowcount > 0 else 0
        if inserted:
            log.info("extract_and_learn: nodo nuevo '%s' (q=%.2f, %s)",
                     concept[:40], verdict.quality_score, source)
        return inserted
    except Exception as e:  # noqa: BLE001
        log.debug("extract_and_learn: insert falló: %s", e)
        return 0


if __name__ == "__main__":
    import sys as _sys
    _sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    logging.basicConfig(level=logging.INFO)
    print(f"=== Extractor 350m (modelo={EXTRACT_MODEL}) ===")
    print("Modelo disponible:", _extract_model_available())
    demo = ("Nmap es una herramienta de escaneo de redes y auditoría de seguridad "
            "que sirve para descubrir hosts, puertos abiertos y servicios en una red.")
    print("Par (sin hint):", extract_pair(demo))
    print("Par (hint=nmap):", extract_pair(demo, subject_hint="nmap"))
