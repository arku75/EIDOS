"""
EIDOS core/knowledge_evolver.py — Auto-Evolución de Conocimiento (Fase 31)
===========================================================================
EIDOS mejora su propio conocimiento leyendo conversaciones exitosas,
extrayendo patrones y actualizando su SOUL.md + patterns.json automáticamente.

Ciclo de evolución (se ejecuta en background cada N horas):
  1. Leer conversaciones recientes del log (daemon.log, ~/.eidos/history.jsonl)
  2. Extraer: qué preguntas hizo SER, qué respondió EIDOS, qué resultó exitoso
  3. Identificar nuevas habilidades o patrones no cubiertos en patterns.json
  4. Actualizar EIDOS_Knowledge/patterns.json con nuevos intents aprendidos
  5. Generar un resumen de evolución en EIDOS_Knowledge/evolution_log.md
  6. Opcionalmente enriquecer SOUL.md con nuevas directrices
"""
from __future__ import annotations

import json
import os
import re
import time
import urllib.request
from dataclasses import dataclass, field
from typing import Any

EIDOS_DIR      = os.path.expanduser("~/EIDOS")
KNOWLEDGE_DIR  = os.path.join(EIDOS_DIR, "EIDOS_Knowledge")
PATTERNS_FILE  = os.path.join(KNOWLEDGE_DIR, "patterns.json")
SOUL_FILE      = os.path.join(EIDOS_DIR, "SOUL.md")
EVOLUTION_LOG  = os.path.join(KNOWLEDGE_DIR, "evolution_log.md")
HISTORY_FILE   = os.path.expanduser("~/.eidos/history.jsonl")
LEARNED_FILE   = os.path.expanduser("~/.eidos/learned_patterns.json")
OLLAMA_URL     = os.environ.get("OLLAMA_URL", "http://127.0.0.1:11434")

# Modelo ligero para análisis (no bloquea Hermes3)
EVOLVER_MODEL  = "lfm2.5-thinking:1.2b"

os.makedirs(KNOWLEDGE_DIR, exist_ok=True)
os.makedirs(os.path.dirname(HISTORY_FILE), exist_ok=True)


# ── Estructuras ──────────────────────────────────────────────────────────────

@dataclass
class EvolutionEvent:
    timestamp:    str
    trigger:      str        # Qué lo causó ("daily", "session_end", "manual")
    new_patterns: int        # Cuántos patrones nuevos aprendidos
    updated:      int        # Cuántos actualizados
    soul_updated: bool       # Si SOUL.md fue modificado
    summary:      str        # Resumen en lenguaje natural


@dataclass
class ConversationEntry:
    role:      str   # "user" | "assistant" | "tool"
    content:   str
    timestamp: float = field(default_factory=time.time)
    success:   bool = True   # Si la acción resultó exitosa


# ── Registro de conversaciones ────────────────────────────────────────────────

def record_exchange(user_msg: str, assistant_reply: str,
                    tools_used: list[str] | None = None,
                    success: bool = True) -> None:
    """
    Registra un intercambio SER→EIDOS en el historial JSONL.
    Se llama al final de cada ciclo del kernel.
    """
    entry = {
        "ts":       time.time(),
        "date":     time.strftime("%Y-%m-%dT%H:%M:%S"),
        "user":     user_msg[:500],          # pyre-ignore[arg-type]
        "reply":    assistant_reply[:800],   # pyre-ignore[arg-type]
        "tools":    tools_used or [],
        "success":  success,
        "tokens":   len(assistant_reply.split()),
    }
    try:
        with open(HISTORY_FILE, "a") as f:
            f.write(json.dumps(entry, ensure_ascii=False) + "\n")
    except Exception as e:
        print(f"[EVOLVER] Error registrando: {e}")


def load_recent_history(n: int = 50) -> list[dict]:
    """Carga las últimas N entradas del historial JSONL."""
    try:
        with open(HISTORY_FILE) as f:
            lines = f.readlines()
        entries = []
        for line in lines[-n:]:           # pyre-ignore[arg-type]
            try:
                entries.append(json.loads(line.strip()))
            except Exception:
                continue
        return entries
    except Exception:
        return []


# ── LLM: extracción de patrones ───────────────────────────────────────────────

def _ask_llm(prompt: str, model: str = EVOLVER_MODEL, max_tokens: int = 400) -> str:
    """Llama al LLM local para análisis. Falla silenciosamente."""
    try:
        payload = json.dumps({
            "model":    model,
            "stream":   False,
            "messages": [{"role": "user", "content": prompt}],
            "options":  {"num_predict": max_tokens, "temperature": 0.2},
        }).encode()
        req = urllib.request.Request(
            f"{OLLAMA_URL}/api/chat",
            data=payload,
            headers={"Content-Type": "application/json"},
        )
        with urllib.request.urlopen(req, timeout=45) as resp:
            return json.load(resp).get("message", {}).get("content", "").strip()
    except Exception as e:
        return f"[ERROR] {e}"


def extract_new_patterns(history: list[dict]) -> list[dict]:
    """
    Analiza el historial con el LLM y extrae nuevos patrones de intención
    que no están ya cubiertos en patterns.json.
    """
    if not history:
        return []

    # Cargar patterns actuales para comparar
    try:
        with open(PATTERNS_FILE) as f:
            existing = json.load(f)
        existing_ids  = {p["id"] for p in existing.get("intent_patterns", [])}
        existing_cats = {p["category"] for p in existing.get("intent_patterns", [])}
    except Exception:
        existing_ids  = set()
        existing_cats = set()

    # Resumir historial para el LLM
    hist_text = "\n".join(
        f"- USER: {e['user'][:100]}"
        f"  → TOOLS: {e.get('tools', [])}"
        f"  → OK: {e.get('success', True)}"
        for e in history[-20:]   # pyre-ignore[arg-type]
    )

    prompt = f"""Analiza estas conversaciones recientes entre SER y EIDOS:

{hist_text}

Categorías ya cubiertas: {list(existing_cats)[:10]}

¿Qué nuevas INTENCIONES o PATRONES de SER detectas que NO estén ya cubiertos?
Responde con JSON array de máximo 3 patrones nuevos. Formato exacto:
[
  {{
    "id": "ip_new_001",
    "category": "categoria",
    "triggers": ["frase1", "frase2"],
    "tool_chain": ["tool1", "tool2"],
    "description": "qué quiere SER",
    "priority": 2
  }}
]
Si no hay patrones nuevos relevantes, responde: []"""

    raw = _ask_llm(prompt, max_tokens=500)

    # Extraer JSON del response
    try:
        match = re.search(r'\[.*\]', raw, re.DOTALL)
        if not match:
            return []
        new_patterns = json.loads(match.group(0))
        # Filtrar patrones con IDs que ya existen
        return [p for p in new_patterns if p.get("id") not in existing_ids]
    except Exception as e:
        print(f"[EVOLVER] Error parseando patrones del LLM: {e}")
        return []


# ── Actualización de SOUL.md ─────────────────────────────────────────────────

def generate_soul_update(history: list[dict]) -> str | None:
    """
    Analiza si hay algo en el historial reciente que justifique
    actualizar directrices en SOUL.md.
    Solo actúa si hay patrones claros (n > 5 conversaciones).
    """
    if len(history) < 5:
        return None

    # Extraer las correcciones de SER (mensajes de corrección)
    corrections = [
        e["user"] for e in history
        if any(w in e["user"].lower() for w in
               ["no hagas", "para", "no te dije", "mal", "error tuyo",
                "recuerda", "siempre", "nunca"])
    ]
    if len(corrections) < 2:
        return None

    corrections_text = "\n".join(f"- {c[:120]}" for c in corrections[:5])  # pyre-ignore[arg-type]

    prompt = f"""SER ha corregido a EIDOS con estas instrucciones:

{corrections_text}

Genera 1-2 líneas (máximo) para añadir al SOUL.md de EIDOS que reflejen
esta corrección como una regla permanente de comportamiento.
Ejemplo: "- NUNCA inicies acciones sin confirmar primero con SER cuando hay ambigüedad."
Responde SOLO con las líneas, sin explicación."""

    suggestion = _ask_llm(prompt, max_tokens=100)
    if suggestion and len(suggestion) > 10 and "[ERROR]" not in suggestion:
        return suggestion
    return None


def apply_soul_update(new_rules: str) -> bool:
    """Añade nuevas reglas al final de SOUL.md sin sobreescribir nada."""
    try:
        with open(SOUL_FILE, "a") as f:
            f.write(f"\n\n## Reglas Auto-Aprendidas ({time.strftime('%Y-%m-%d')})\n")
            f.write(new_rules + "\n")
        return True
    except Exception as e:
        print(f"[EVOLVER] Error actualizando SOUL.md: {e}")
        return False


# ── Aplicar patrones nuevos ──────────────────────────────────────────────────

def apply_new_patterns(new_patterns: list[dict]) -> int:
    """
    Añade los nuevos patrones a patterns.json.
    Retorna el número de patrones añadidos.
    """
    if not new_patterns:
        return 0
    try:
        with open(PATTERNS_FILE) as f:
            data = json.load(f)
    except Exception:
        data = {"intent_patterns": [], "ser_language_profile": {}}

    existing_ids = {p["id"] for p in data.get("intent_patterns", [])}
    added = 0
    for p in new_patterns:
        if p.get("id") not in existing_ids:
            p["_auto_learned"] = True
            p["_learned_at"]   = time.strftime("%Y-%m-%dT%H:%M:%S")
            data["intent_patterns"].append(p)
            added += 1

    if added > 0:
        with open(PATTERNS_FILE, "w") as f:
            json.dump(data, f, indent=2, ensure_ascii=False)
        print(f"[EVOLVER] ✅ {added} nuevos patrones añadidos a patterns.json")
    return added


# ── Log de evolución ─────────────────────────────────────────────────────────

def log_evolution(event: EvolutionEvent) -> None:
    """Añade entrada al evolution_log.md."""
    entry = f"""
## {event.timestamp} — {event.trigger}
- **Patrones nuevos**: {event.new_patterns}
- **Actualizados**: {event.updated}
- **SOUL.md actualizado**: {'Sí' if event.soul_updated else 'No'}
- **Resumen**: {event.summary}
"""
    try:
        with open(EVOLUTION_LOG, "a") as f:
            f.write(entry)
    except Exception as e:
        print(f"[EVOLVER] Error en evolution_log: {e}")


# ── Ciclo principal de evolución ─────────────────────────────────────────────

def evolve(trigger: str = "manual", n_history: int = 50) -> EvolutionEvent:
    """
    Ejecuta un ciclo completo de auto-evolución.

    Args:
        trigger: Quién / qué disparó la evolución ("daily", "session_end", "manual")
        n_history: Cuántas entradas de historial analizar

    Returns:
        EvolutionEvent con resumen de lo que cambió.
    """
    print(f"[EVOLVER] 🧬 Ciclo evolución iniciado (trigger={trigger})")
    ts = time.strftime("%Y-%m-%d %H:%M:%S")

    # 1. Cargar historial
    history = load_recent_history(n_history)
    if not history:
        return EvolutionEvent(
            timestamp=ts, trigger=trigger,
            new_patterns=0, updated=0, soul_updated=False,
            summary="Sin historial — nada que analizar"
        )
    print(f"[EVOLVER] Analizando {len(history)} entradas...")

    # 2. Extraer patrones nuevos
    new_patterns = extract_new_patterns(history)
    added = apply_new_patterns(new_patterns)

    # 3. Actualizar learned_patterns (IntentMapper los usa en tiempo real)
    try:
        if new_patterns:
            existing: list = []
            try:
                with open(LEARNED_FILE) as f:
                    existing = json.load(f)
            except Exception:
                pass  # error no crítico, continuar
            existing.extend([p for p in new_patterns if p not in existing])
            with open(LEARNED_FILE, "w") as f:
                json.dump(existing, f, indent=2, ensure_ascii=False)
    except Exception as e:
        print(f"[EVOLVER] Error en learned_patterns: {e}")

    # 4. Sugerir actualización SOUL.md
    soul_updated = False
    soul_rules = generate_soul_update(history)
    if soul_rules:
        soul_updated = apply_soul_update(soul_rules)

    # 5. Crear evento y loguearlo
    summary_parts = []
    if added:
        summary_parts.append(f"{added} patrones nuevos aprendidos")
    if soul_updated:
        summary_parts.append("SOUL.md mejorado")
    if not summary_parts:
        summary_parts.append(f"Analizado {len(history)} conversaciones, sin cambios necesarios")

    event = EvolutionEvent(
        timestamp=ts, trigger=trigger,
        new_patterns=added, updated=0,
        soul_updated=soul_updated,
        summary=" | ".join(summary_parts)
    )
    log_evolution(event)
    print(f"[EVOLVER] ✅ {event.summary}")
    return event


# ── Daemon de evolución periódica ────────────────────────────────────────────

def run_daemon(interval_hours: float = 6.0) -> None:
    """
    Corre en background, ejecutando un ciclo de evolución cada N horas.
    Se lanza desde eidos_daemon.py o como servicio systemd.
    """
    print(f"[EVOLVER] 🧬 Daemon iniciado — ciclo cada {interval_hours}h")
    while True:
        evolve(trigger="daemon")
        time.sleep(int(interval_hours * 3600))


# ── CLI directo ──────────────────────────────────────────────────────────────
if __name__ == "__main__":
    import sys
    if len(sys.argv) > 1 and sys.argv[1] == "daemon":
        run_daemon()
    else:
        event = evolve(trigger="manual")
        print(f"\n[RESULTADO]")
        print(f"  Patrones nuevos: {event.new_patterns}")
        print(f"  SOUL actualizado: {event.soul_updated}")
        print(f"  Resumen: {event.summary}")
