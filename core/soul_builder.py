"""
EIDOS core/soul_builder.py — Generador de SOUL.md Dinámico
===========================================================
Genera el SOUL.md de EIDOS de forma dinámica en cada arranque,
incorporando automáticamente:
  - Los patrones de intención aprendidos de SER
  - El estado actual del sistema (tools, modelos, RAM)
  - El perfil lingüístico de SER desde patterns.json
  - El historial de habilidades frecuentes

Esto hace que EIDOS sea cada vez más preciso al entender a SER,
porque su "alma" (system prompt) evoluciona con cada interacción.

Uso:
    from core.soul_builder import build_soul, get_soul
    soul_text = get_soul()   # Carga o genera fresh SOUL.md
    soul_text = build_soul() # Regenera siempre
"""
from __future__ import annotations

import json
import os
import threading
import time
import urllib.request
from typing import Optional

EIDOS_DIR       = os.path.expanduser("~/EIDOS")
SOUL_PATH       = os.path.join(EIDOS_DIR, "SOUL.md")
SOUL_CACHE_PATH = os.path.expanduser("~/.eidos/soul_generated.md")
PATTERNS_FILE   = os.path.join(EIDOS_DIR, "EIDOS_Knowledge", "patterns.json")
LEARNED_FILE    = os.path.expanduser("~/.eidos/learned_patterns.json")
FIXES_FILE      = os.path.expanduser("~/.eidos/technical_fixes.json")
OLLAMA_URL      = os.environ.get("OLLAMA_URL", "http://127.0.0.1:11434")
STATS_FILE      = os.path.expanduser("~/.eidos/soul_stats.json")

# El SOUL.md se regenera si tiene más de N minutos de antigüedad
SOUL_REGEN_MINUTES = 30

# Singleton del hilo de auto-evolución
_evo_thread: threading.Thread | None = None


def _get_system_status() -> dict:
    """Lee estado actual: RAM libre, modelos cargados, tools disponibles."""
    status = {"ram_free_gb": 0.0, "loaded_models": [], "tools_count": 0}
    try:
        with open("/proc/meminfo") as f:
            lines = f.readlines()
        mem = {l.split()[0].rstrip(":"): int(l.split()[1])  # pyre-ignore[arg-type]
               for l in lines if len(l.split()) >= 2 and l.split()[1].isdigit()}  # pyre-ignore[arg-type]
        status["ram_free_gb"] = round(mem.get("MemAvailable", 0) / (1024 * 1024), 1)
    except Exception:
        pass  # error no crítico, continuar
    try:
        req = urllib.request.Request(f"{OLLAMA_URL}/api/ps")
        with urllib.request.urlopen(req, timeout=3) as resp:
            data = json.load(resp)
        status["loaded_models"] = [m.get("name", "") for m in data.get("models", [])]
    except Exception:
        pass  # error no crítico, continuar
    try:
        from core.tools import TOOLS
        status["tools_count"] = len(TOOLS)
    except Exception:
        status["tools_count"] = 31

    return status


def _get_intent_summary() -> str:
    """Genera un resumen de los patrones de intención para el SOUL.md."""
    try:
        from core.intent_to_action import get_mapper
        mapper = get_mapper()
        return mapper.context_for_prompt(top_n=10)
    except Exception:
        # Fallback: leer directamente del JSON
        try:
            with open(PATTERNS_FILE) as f:
                data = json.load(f)
            patterns = data.get("intent_patterns", [])[:10]  # pyre-ignore[arg-type]
            lines = ["## Intenciones frecuentes de SER:"]
            for p in patterns:
                trig = p["triggers"][0] if p["triggers"] else ""  # pyre-ignore[arg-type]
                tools = " → ".join(p["tool_chain"])
                lines.append(f'- "{trig}" → {tools}')
            return "\n".join(lines)
        except Exception:
            return ""


def _get_ser_profile_summary() -> str:
    """Genera un resumen del perfil lingüístico de SER."""
    try:
        with open(PATTERNS_FILE) as f:
            data = json.load(f)
        profile = data.get("ser_language_profile", {})
        traits = profile.get("traits", [])
        shortcuts = profile.get("shortcuts", {})

        lines = ["## Cómo habla SER:"]
        for trait in traits[:5]:  # pyre-ignore[arg-type]
            lines.append(f"- {trait}")
        if shortcuts:
            lines.append("\n## Atajos que reconoces al instante:")
            for kw, desc in list(shortcuts.items())[:5]:  # pyre-ignore[arg-type]
                lines.append(f'- "{kw}" → {desc}')
        return "\n".join(lines)
    except Exception:
        return ""


def _get_learned_summary() -> str:
    """Resumen de patrones aprendidos más frecuentes."""
    try:
        with open(LEARNED_FILE) as f:
            learned = json.load(f)
        if not learned:
            return ""
        top = sorted(learned, key=lambda x: -x.get("frequency", 1))[:5]  # pyre-ignore[arg-type]
        total_uses = sum(p.get("frequency", 1) for p in learned)
        lines = [f"\n## Lo que SER me ha enseñado ({len(learned)} patrones, {total_uses} usos totales):"]
        for p in top:
            trigger = p["triggers"][0] if p["triggers"] else ""  # pyre-ignore[arg-type]
            tools   = " → ".join(p.get("tool_chain", []))
            freq    = p.get("frequency", 1)
            lines.append(f'- "{trigger}" → {tools}  ({freq}x)')
        return "\n".join(lines)
    except Exception:
        return ""


def _get_fixes_summary() -> str:
    """Resumen de heurísticas técnicas aprendidas de fallos corregidos."""
    try:
        if not os.path.exists(FIXES_FILE):
            return ""
        with open(FIXES_FILE) as f:
            fixes = json.load(f)
        if not fixes:
            return ""
        lines = ["\n## Heurísticas de Auto-Sanación (Capa Autobot):"]
        for fix in fixes[-5:]:  # Últimos 5 éxitos
            lines.append(f"- **Error:** {fix['error'][:60]}... → **Fix:** {fix['fix']}")
        return "\n".join(lines)
    except Exception:
        return ""


def _get_quality_score() -> str:
    """Calcula un score de calidad del SOUL basado en datos disponibles."""
    score = 0
    reasons = []
    try:
        with open(LEARNED_FILE) as f:
            learned = json.load(f)
        if learned:
            score += min(len(learned) * 5, 30)  # hasta +30 por patrones aprendidos
            reasons.append(f"{len(learned)} patrones aprendidos")
    except Exception:
        pass  # error no crítico, continuar
    try:
        with open(PATTERNS_FILE) as f:
            data = json.load(f)
        patterns = data.get("intent_patterns", [])
        score += min(len(patterns) * 2, 40)  # hasta +40 por patrones base
        reasons.append(f"{len(patterns)} patrones base")
    except Exception:
        pass  # error no crítico, continuar
    score = min(score, 100)
    bar = "█" * (score // 10) + "░" * (10 - score // 10)
    return f"- 🎯 Calidad del SOUL: **{score}/100** [{bar}] ({', '.join(reasons)})"


def build_soul(force: bool = False) -> str:
    """
    Construye el SOUL.md dinámico incorporando:
    - Identidad base de EIDOS
    - Estado del sistema en tiempo real
    - Patrones de intención de SER
    - Patrones aprendidos (memoria persistente)
    - Perfil lingüístico de SER

    Args:
        force: Si True, siempre regenera aunque sea reciente.

    Returns:
        El texto del SOUL.md generado.
    """
    # ── Leer base SOUL.md estático ──────────────────────────────────────
    base_soul = ""
    try:
        with open(SOUL_PATH) as f:
            base_soul = f.read()
    except Exception:
        base_soul = "# EIDOS — Agente Soberano de SER\n"

    # ── Estado del sistema ──────────────────────────────────────────────
    sys_status = _get_system_status()
    ram  = sys_status["ram_free_gb"]
    loaded = sys_status["loaded_models"]
    tools_n = sys_status["tools_count"]
    timestamp = time.strftime("%Y-%m-%d %H:%M")

    # ── Construcción del SOUL dinámico ──────────────────────────────────
    quality_str  = _get_quality_score()
    dynamic_sections = f"""

---
<!-- SECCIÓN DINÁMICA — Generada automáticamente {timestamp} -->

## Estado del sistema ahora mismo
- 🧠 RAM libre: **{ram}GB**
- 🔧 Herramientas disponibles: **{tools_n}**
- 📦 Modelos en memoria: {', '.join(loaded) if loaded else 'ninguno (se cargan al usarse)'}
{quality_str}

{_get_ser_profile_summary()}

{_get_intent_summary()}

{_get_learned_summary()}

{_get_fixes_summary()}

## Regla de oro
Cuando SER te diga algo, primero busca si encaja en los patrones de arriba.
Si encaja, ejecuta DIRECTAMENTE la tool chain. No preguntes, actúa.
Si no encaja, usa tu criterio + herramientas disponibles para ayudarle.
SER confía en ti. Tú confías en la arquitectura.
<!-- FIN SECCIÓN DINÁMICA -->
"""

    full_soul = base_soul.rstrip() + dynamic_sections

    # ── Guardar en caché ────────────────────────────────────────────────
    try:
        with open(SOUL_CACHE_PATH, "w") as f:
            f.write(full_soul)
    except Exception as e:
        print(f"[SOUL] Warn: No se pudo guardar caché: {e}")

    return full_soul


def get_soul(max_age_minutes: int = SOUL_REGEN_MINUTES) -> str:
    """
    Devuelve el SOUL.md dinámico.
    Si la caché es reciente (< max_age_minutes), la usa. Si no, regenera.
    """
    # Intentar cargar caché
    if os.path.exists(SOUL_CACHE_PATH):
        age_seconds = time.time() - os.path.getmtime(SOUL_CACHE_PATH)
        if age_seconds < max_age_minutes * 60:
            try:
                with open(SOUL_CACHE_PATH) as f:
                    return f.read()
            except Exception:
                pass  # error no crítico, continuar
    # Regenerar
    return build_soul()


def rebuild_if_new_patterns() -> bool:
    """
    Regenera el SOUL si hay patrones aprendidos más nuevos que la caché.
    Llámalo después de cada learn() para mantener el SOUL actualizado.
    Returns True si se regeneró.
    """
    soul_mtime    = os.path.getmtime(SOUL_CACHE_PATH) if os.path.exists(SOUL_CACHE_PATH) else 0
    learned_mtime = os.path.getmtime(LEARNED_FILE)    if os.path.exists(LEARNED_FILE)    else 0

    if learned_mtime > soul_mtime:
        build_soul(force=True)
        return True
    return False


def start_auto_evolution(interval_s: int = 300) -> None:
    """
    Lanza un hilo daemon que regenera el SOUL.md automáticamente:
    - Cada `interval_s` segundos (por defecto 5 min)
    - Inmediatamente si hay nuevos patrones aprendidos

    Se llama una vez al arrancar el CLI de EIDOS (eidos_cli.py).
    No bloquea — corre como hilo de fondo.
    """
    global _evo_thread
    if _evo_thread and _evo_thread.is_alive():
        return  # Ya corriendo

    def _loop() -> None:
        last_learned_mtime = 0.0
        while True:
            try:
                # Detectar si hay patrones nuevos o editados (learned o base)
                learned_mtime = (
                    os.path.getmtime(LEARNED_FILE)
                    if os.path.exists(LEARNED_FILE) else 0.0
                )
                patterns_mtime = (
                    os.path.getmtime(PATTERNS_FILE)
                    if os.path.exists(PATTERNS_FILE) else 0.0
                )
                cur_mtime = max(learned_mtime, patterns_mtime)

                soul_mtime = (
                    os.path.getmtime(SOUL_CACHE_PATH)
                    if os.path.exists(SOUL_CACHE_PATH) else 0.0
                )
                if cur_mtime > soul_mtime or cur_mtime != last_learned_mtime:
                    build_soul(force=True)
                    last_learned_mtime = cur_mtime
                    print("[SOUL] 🧬 SOUL.md auto-evolucionado (cambio en patrones detectado)")
            except Exception as e:
                print(f"[SOUL] Warn auto-evo: {e}")
            time.sleep(interval_s)

    _evo_thread = threading.Thread(target=_loop, name="soul-evo", daemon=True)
    _evo_thread.start()
    print(f"[SOUL] 🧬 Auto-evolución iniciada (cada {interval_s}s)")


# ── CLI rápido ────────────────────────────────────────────────────────────────
if __name__ == "__main__":
    import sys
    force = "--force" in sys.argv
    print("[SOUL BUILDER] Generando SOUL.md dinámico...")
    soul = build_soul(force=force)
    print(f"✅ Generado: {SOUL_CACHE_PATH}")
    print(f"   Tamaño: {len(soul)} chars")
    # Mostrar últimas 30 líneas (sección dinámica)
    lines = soul.splitlines()
    print("\n--- SECCIÓN DINÁMICA (últimas 30 líneas) ---")
    print("\n".join(lines[-30:]))
