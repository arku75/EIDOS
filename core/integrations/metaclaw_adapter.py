"""
core/integrations/metaclaw_adapter.py — Adapter conceptual MetaClaw → EIDOS
==========================================================================

NO importa MetaClaw directamente (es un repo async pesado con scheduler propio).
Toma SOLO la idea: en lugar de hacer un merge simple por promedio ponderado,
seleccionar traits por **score de fitness** del par (a, b) — los traits del
"ganador" en cada dimensión pesan más.

Original MetaClaw SkillEvolver: mantiene `failures` y `evolved skills` y
selecciona via tournament. Nosotros aplicamos el mismo principio al merge de
caracteres de Colony.

Algoritmo `merge_evolved`:
  • Para cada trait, calcula fitness_a = a[trait] * confidence_a, fitness_b idem.
  • Si |fitness_a - fitness_b| > THRESHOLD → el trait del ganador pesa 70/30.
  • Si están parejos → promedio 50/50 (igual que merge original).
  • Skills: unión (igual que original) + ranking por co-aparición en tastes.
  • Tastes: dedupe preservando orden del más exitoso.

Compatible con la firma de eidos_character_system.merge() — devuelve un dict
de carácter creado vía create_character (persistido en colony_characters.db).

Confidence se infiere del `last_active_at`: más reciente = más confidence.
Heurística simple, sin necesidad de telemetría externa.

Uso:
    from core.integrations.metaclaw_adapter import merge_evolved
    new_char = merge_evolved(a_id, b_id, new_name="Sintético")

Self-test:
    python3 core/integrations/metaclaw_adapter.py --self-test
"""
from __future__ import annotations

import sys
import time
import json
import logging
from pathlib import Path
from typing import Optional

sys.path.insert(0, str(Path.home() / "EIDOS"))

log = logging.getLogger("eidos.metaclaw_adapter")

FITNESS_THRESHOLD = 15.0     # diferencia mínima de fitness para "ganador claro"
WINNER_WEIGHT = 0.70


def _confidence(char: dict) -> float:
    """0.5..1.0 según last_active_at (más reciente = más confidence)."""
    last = char.get("last_active_at") or char.get("created_at") or time.time()
    age_h = max(0, (time.time() - last) / 3600.0)
    return max(0.5, 1.0 - min(0.5, age_h / 168.0))   # decae 7 días → 0.5


def _evolved_traits(a: dict, b: dict) -> dict:
    ca, cb = _confidence(a), _confidence(b)
    keys = set(a.get("traits", {}).keys()) | set(b.get("traits", {}).keys())
    out: dict = {}
    for k in keys:
        va = a.get("traits", {}).get(k, 50)
        vb = b.get("traits", {}).get(k, 50)
        fa, fb = va * ca, vb * cb
        if abs(fa - fb) >= FITNESS_THRESHOLD:
            wa = WINNER_WEIGHT if fa > fb else (1 - WINNER_WEIGHT)
        else:
            wa = 0.5
        out[k] = int(round(va * wa + vb * (1 - wa)))
    return out


def _evolved_skills(a: dict, b: dict) -> list:
    # Unión + ranking: skills que aparecen en ambos van primero.
    sa = set(a.get("skills") or [])
    sb = set(b.get("skills") or [])
    both = sorted(sa & sb)
    only_a = sorted(sa - sb)
    only_b = sorted(sb - sa)
    return both + only_a + only_b


def _evolved_tastes(a: dict, b: dict) -> list:
    ca, cb = _confidence(a), _confidence(b)
    primary = a.get("tastes", []) if ca >= cb else b.get("tastes", [])
    secondary = b.get("tastes", []) if ca >= cb else a.get("tastes", [])
    # dedupe preservando orden del primary
    seen, out = set(), []
    for x in primary + secondary:
        key = str(x)
        if key not in seen:
            seen.add(key)
            out.append(x)
    return out


def merge_evolved(char_a_id: str, char_b_id: str,
                  new_name: Optional[str] = None) -> Optional[dict]:
    """Merge inspirado en MetaClaw SkillEvolver: traits por fitness, no
    promedio simple. Skills unión rankeada. Tastes desde el más activo."""
    try:
        from core.eidos_character_system import load_character, create_character
    except Exception as e:
        log.warning("character_system no importable: %s", e)
        return None

    a = load_character(char_a_id)
    b = load_character(char_b_id)
    if not a or not b:
        return None

    new_traits = _evolved_traits(a, b)
    new_skills = _evolved_skills(a, b)
    new_tastes = _evolved_tastes(a, b)
    name = new_name or f"{a['name']}×{b['name']}-evo"
    return create_character(name,
                            traits=new_traits,
                            skills=new_skills,
                            tastes=new_tastes)


def _self_test() -> dict:
    """Antes/después: crea 2 caracteres temporales, hace merge clásico y
    merge_evolved, compara traits. PASS si los dos producen caracteres
    válidos y merge_evolved se sesga al de mayor confidence."""
    try:
        from core.eidos_character_system import (
            create_character, merge as classic_merge, delete_character,
        )
    except Exception as e:
        return {"self_test": "FAIL", "reason": f"import error: {e}"}

    checks = []
    A = create_character("test-evo-A",
                         traits={"curiosity": 90, "caution": 20, "warmth": 50},
                         skills=["python", "linux"],
                         tastes=["red_team", "rust"])
    B = create_character("test-evo-B",
                         traits={"curiosity": 30, "caution": 90, "warmth": 50},
                         skills=["go", "linux"],
                         tastes=["finance", "compliance"])
    try:
        cls = classic_merge(A["char_id"], B["char_id"], new_name="test-evo-C")
        evo = merge_evolved(A["char_id"], B["char_id"], new_name="test-evo-D")
        checks.append(("classic produce dict", isinstance(cls, dict)))
        checks.append(("evolved produce dict", isinstance(evo, dict)))
        if cls and evo:
            checks.append(("classic curiosity = promedio simple (60)",
                           55 <= cls["traits"]["curiosity"] <= 65))
            checks.append(("evolved curiosity ≠ promedio simple (sesgo a A)",
                           evo["traits"]["curiosity"] != cls["traits"]["curiosity"]))
            checks.append(("evolved skills incluye intersección 'linux'",
                           "linux" in evo["skills"]))
    finally:
        for ch in (A, B):
            try:
                if ch:
                    delete_character(ch["char_id"])
            except Exception:
                pass
        if 'cls' in locals() and cls:
            try:
                delete_character(cls["char_id"])
            except Exception:
                pass
        if 'evo' in locals() and evo:
            try:
                delete_character(evo["char_id"])
            except Exception:
                pass

    passed = sum(1 for _, ok in checks if ok)
    return {
        "self_test": "PASS" if passed == len(checks) and checks else "FAIL",
        "passed": passed,
        "total": len(checks),
        "checks": [{"check": c, "ok": ok} for c, ok in checks],
    }


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("--self-test", action="store_true")
    args = ap.parse_args()
    if args.self_test:
        print(json.dumps(_self_test(), ensure_ascii=False, indent=2))
        sys.exit(0)
    ap.print_help()
