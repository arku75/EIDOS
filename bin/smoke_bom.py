#!/usr/bin/env python3
"""
bin/smoke_bom.py — E2E del BOM (core/causal_loop) en SECO (no toca ratón). S124.

Prueba cada etapa: priors → frenos → recall(grafo) → afford → verify → learn →
step → run → hook eidos_vivo. Imprime PASS/FAIL por etapa.
"""
import os
import sys
sys.path.insert(0, os.path.expanduser("~/EIDOS"))
import logging
logging.basicConfig(level=logging.WARNING)

from core.causal_loop import (
    SafetyGuard, seed_ui_priors, _recall_known, _affordances,
    _verify, _learn, step, run,
)
from core.db import get_conn
from pathlib import Path

BRAIN = Path.home() / ".eidos" / "evolution_brain.db"
P, F = 0, 0


def check(name, cond, detail=""):
    global P, F
    ok = bool(cond)
    P += ok
    F += (not ok)
    print(f"  {'✅' if ok else '❌'} {name}" + (f" — {detail}" if detail else ""))
    return ok


print("═" * 60)
print("E2E BOM (core/causal_loop) — DRY-RUN, no toca el ratón")
print("═" * 60)

# 1 — Mapa: priors UI sembrados
seed_ui_priors()
n_priors = get_conn(BRAIN, timeout=10).execute(
    "SELECT COUNT(*) FROM knowledge_nodes WHERE source='seed_ui_prior'").fetchone()[0]
check("Mapa: priors UI en el grafo", n_priors >= 8, f"{n_priors} priors")

# 1b — CUERPO: propiocepción (sabe dónde vive y dónde está su mano)
from core.body import i_am_here, body_signature
_body = i_am_here()
check("Cuerpo: se conoce (casa + usuario)", bool(_body.get("home")) and bool(_body.get("user")),
      f"{_body.get('home')} · {_body.get('user')}")
check("Cuerpo: propiocepción → firma de estado", isinstance(body_signature(), str) and len(body_signature()) > 0,
      body_signature()[:32])
# CORRECCIÓN crítica: la VENTANA va al estado, la MANO no (no fragmenta el aprendizaje)
import core.causal_loop as _cl, inspect as _insp
_src = _insp.getsource(_cl._perceive)
check("Cuerpo: estado usa ventana, NO la mano (no fragmenta)",
      "window_context" in _src and "body_signature" not in _src)
# memoria motora QUERYABLE (no string) — lo que pedía la crítica
from core.body import remember_motor, recall_motor
remember_motor("E2E_TARGET_XZ", 450, 300, success=True, confidence=0.9)
check("Cuerpo: memoria motora QUERYABLE", recall_motor("E2E_TARGET_XZ") == (450, 300),
      f"recall→{recall_motor('E2E_TARGET_XZ')}")

# 1c — AUTOCONCEPTO OPERATIVO (no declarativo): sabe de qué es su cuerpo y si funciona
from core.body import self_model, hand_ok, what_can_i_do
_sm = self_model()
check("Autoconcepto: su mano ES xdotool", _sm["organs"]["mano"]["es"].startswith("xdotool"))
check("Autoconcepto: sabe SI su mano funciona", _sm["organs"]["mano"]["funciona"] == hand_ok(),
      f"mano funciona={hand_ok()}")
check("Autoconcepto: se sabe capaz o lisiado", ("Puedo" in what_can_i_do()) or ("Me falta" in what_can_i_do()),
      what_can_i_do()[:45])

# 2 — Frenos (SafetyGuard)
danger_blocked = all(not SafetyGuard.is_safe(x, "gui_click")[0]
                     for x in ["Eliminar cuenta", "Enviar mensaje", "Pagar ahora", "rm -rf /"])
safe_allowed = all(SafetyGuard.is_safe(x, "gui_click")[0]
                   for x in ["Settings", "Buscar", "Kali Docs"])
check("Frenos: bloquea irreversibles", danger_blocked)
check("Frenos: permite acciones normales", safe_allowed)
check("Frenos: shell directo bloqueado", not SafetyGuard.is_safe("x", "execute_shell")[0])

# 3 — Razonar: recall del grafo interpreta lo conocido
known = _recall_known(["buscar", "configuración", "menú", "xyzqwerty_inexistente"])
check("Razonar: recall encuentra lo conocido", len(known) >= 1, f"{list(known.keys())}")
check("Razonar: no inventa lo desconocido", "xyzqwerty_inexistente" not in known)


# 4 — Afford: candidatos seguros + sesgo por conocimiento
class _El:
    def __init__(self, text, x=10, y=10):
        self.text, self.x, self.y = text, x, y
        self.x1 = self.y1 = self.x2 = self.y2 = 0


els = [_El("Settings"), _El("Eliminar todo"), _El("Buscar"), _El("Ana López")]
affs = _affordances(els, "buscar algo")
keys = [a for a, _, _ in affs]
check("Afford: genera candidatos", len(affs) >= 1, f"{len(affs)} acciones")
check("Afford: excluye el peligroso", not any("eliminar" in k for k in keys))
check("Afford: prioriza el del objetivo", keys and "buscar" in keys[0])

# 4b — Curiosidad: SIN goal prioriza lo DESCONOCIDO (para APRENDERLO, no solo lo conocido)
keys_c = [a for a, _, _ in _affordances([_El("Buscar"), _El("WidgetRaroXyz")], "")]
check("Curiosidad: prioriza lo DESCONOCIDO para aprenderlo",
      keys_c and "widgetraroxyz" in keys_c[0], f"{keys_c}")

# 5 — Verify: recompensas correctas en los 3 casos
r_same, _ = _verify("a", {"X"}, "a", {"X"}, "g")
r_goal, _ = _verify("a", {"X"}, "b", {"X", "Configuración"}, "configuración")
r_chg, _ = _verify("a", {"X"}, "b", {"X", "Y"}, "zzz")
check("Verify: sin efecto → negativo", r_same < 0, f"{r_same}")
check("Verify: aparece objetivo → alto", r_goal >= 3.0, f"{r_goal}")
check("Verify: cambió → positivo medio", 0 < r_chg < 3.0, f"{r_chg}")

# 6 — Learn: persiste neurona NUEVA, REFUERZA al reconfirmar, NO persiste si reward≤0
import time as _t
ustate = f"e2e_{int(_t.time())}"
concept = f"skill: en pantalla {ustate}, click 'Settings' → abrió config"


def _count():
    return get_conn(BRAIN, timeout=10).execute(
        "SELECT COUNT(*) FROM knowledge_nodes WHERE source='skill_learned'").fetchone()[0]


def _conf():
    r = get_conn(BRAIN, timeout=10).execute(
        "SELECT confidence FROM knowledge_nodes WHERE concept=?", (concept,)).fetchone()
    return float(r[0]) if r else 0.0


before = _count()
_learn(ustate, "click:settings", 3.0, "e2e_next", "Settings", "abrió config")
after, conf1 = _count(), _conf()
check("Learn: neurona NUEVA persistida", after > before, f"{before}→{after}")
_learn(ustate, "click:settings", 3.0, "e2e_next", "Settings", "abrió config")  # reconfirmar
after_r, conf2 = _count(), _conf()
check("Learn: reconfirmar NO duplica", after_r == after, f"{after}→{after_r}")
check("Learn: reconfirmar REFUERZA confianza", conf2 > conf1, f"{conf1:.2f}→{conf2:.2f}")
_learn(ustate + "_neg", "click:x", -0.3, "n", "X", "nada")
check("Learn: NO persiste si reward ≤ 0", _count() == after_r, f"{after_r}→{_count()}")

# 7 — step(): ciclo completo en seco sobre la pantalla real
r = step(goal="explora la app activa", dry_run=True)
check("step(): ciclo completo gira", r.get("ok") is True, f"acción={r.get('action','?')[:30]}")

# 8 — run(): varios pasos sin romper
res = run(goal="explora", steps=3, dry_run=True)
check("run(): completa N pasos", res.get("steps_done", 0) >= 1, f"{res.get('steps_done')} pasos")

# 8b — ESTUDIO DIRIGIDO (maestro→alumno): la esencia que pidió SER
from core.eidos_study import _is_operation, study
check("Estudio: clasifica operación vs concepto",
      _is_operation("abre el browser") and not _is_operation("estudia rust"))
_st = study("usa el menú", dry_run=True, steps=1)
check("Estudio: dirigido funciona y REPORTA a SER", _st.get("ok") and bool(_st.get("summary")),
      _st.get("summary", "")[:40])

# 8c — KNOWS IT KNOWS (S125): EIDOS reconoce lo ya aprendido, no re-estudia
from core.eidos_study import _already_knows, _check_motor_memory, _check_graph_knowledge, _extract_keywords
# Extracción de palabras clave
kw = _extract_keywords("abre el navegador Firefox ya!")
check("Knows: extrae keywords de directiva", "firefox" in kw and "navegador" in kw and "abre" in kw,
      str(kw))
check("Knows: filtra palabras vacías (el, ya)", "el" not in kw and "ya" not in kw)
# Memoria motora: encuentra lo ya tocado
motor = _check_motor_memory("E2E_TARGET_XZ")
check("Knows: motor_memory encuentra target conocido", motor is not None and motor.get("x") == 450,
      str(motor)[:60] if motor else "None")
motor_unk = _check_motor_memory("zyxwvutsrq_inexistente_999")
check("Knows: motor_memory None para desconocido", motor_unk is None)
# Grafo: encuentra concepto ya estudiado
graph = _check_graph_knowledge("python")
check("Knows: grafo encuentra concepto conocido", graph is not None and bool(graph.get("definition")),
      f"concept={graph.get('concept','?')[:30]}" if graph else "None")
graph_unk = _check_graph_knowledge("xyznonexistente999")
check("Knows: grafo None para desconocido", graph_unk is None)
# already_knows: integración para operación
from core.body import remember_motor
remember_motor("menú de buscar", 320, 180, success=True, confidence=0.85)
ak_op2 = _already_knows("buscar en el menú", "operación")
check("Knows: already_knows detecta operación ya aprendida",
      ak_op2 is not None and "ya sé esto" in ak_op2.get("summary", ""),
      ak_op2.get("summary","?")[:60] if ak_op2 else "None")
# already_knows: integración para concepto
ak_con = _already_knows("python", "concepto")
check("Knows: already_knows detecta concepto ya estudiado",
      ak_con is not None and "ya estudié esto" in ak_con.get("summary", ""),
      ak_con.get("summary","?")[:60] if ak_con else "None")
# already_knows: None para lo verdaderamente nuevo
ak_new = _already_knows("xzqxzqxzq_fnoqwnfqow_999zzz", "concepto")
check("Knows: already_knows None para lo nuevo", ak_new is None)
# study() completo: reconoce y no re-estudia
st_known = study("abre el menú de buscar", dry_run=True, steps=1)
check("Knows: study() reconoce lo ya sabido (already_knew=True)",
      st_known.get("already_knew") is True and st_known.get("learned") is True,
      st_known.get("summary","?")[:60])
check("Knows: study() no re-estudia (sin BOM, sin research)",
      "bom" not in st_known and "definition" not in st_known)

# 9 — Hook en eidos_vivo (gated, off por defecto)
import importlib
src = (Path.home() / "EIDOS" / "core" / "eidos_libre.py").read_text()
check("Hook: EIDOS_BOM presente en eidos_libre", "EIDOS_BOM" in src and "causal_loop" in src)
check("Hook: gated (off por defecto)", 'EIDOS_BOM") == "1"' in src)
check("Hook: dry atado a EIDOS_LIBRE_REAL_ACTIONS", "self._real_actions" in src)

print("═" * 60)
print(f"  RESULTADO: {P} PASS / {F} FAIL")
print("═" * 60)
sys.exit(1 if F else 0)
