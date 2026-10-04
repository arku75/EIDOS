"""
core/causal_loop.py — El BOM: bucle percibir→decidir→actuar→verificar→aprender (S124)

EIDOS aprende a usar apps SOLO: mira la pantalla (AT-SPI2 = SABER qué es cada
control; OCR = fallback), decide una acción con eidos_rl (Q-learning real),
la ejecuta (con FRENOS de seguridad), re-percibe para VERIFICAR el efecto, y
aprende ese cause→effect como neurona permanente con confianza.

Motor (este módulo) + dirección (goal) + frenos (SafetyGuard) + mapa
(seed_ui_priors + grafo). REUSA perception + eidos_rl + grafo. No reinventa.

SEGURIDAD: dry_run=True por defecto → NO toca ratón/teclado (lección mouse-grab).
Solo con SER presente y dry_run=False ejecuta de verdad.

CLI:
  python3 -m core.causal_loop "explora la app" --steps 5        # SECO (no actúa)
  python3 -m core.causal_loop "explora la app" --steps 5 --real # actúa (SER presente)
  python3 -m core.causal_loop --seed                            # siembra priors UI
"""
from __future__ import annotations

import hashlib
import logging
import os
import time
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

log = logging.getLogger("eidos.causal_loop")

BRAIN_DB = Path.home() / ".eidos" / "evolution_brain.db"

# ── screen_controller integration ───────────────────────────────────────────────
_SCREEN_CONTROLLER = None


def _get_sc():
    """Lazy-load del ScreenController singleton (5 capas: Planner→VisualCortex→DecisionEngine→HumanEmulator→InputBackend)."""
    global _SCREEN_CONTROLLER
    if _SCREEN_CONTROLLER is None:
        from core.screen_controller import get_screen_controller
        _SCREEN_CONTROLLER = get_screen_controller(dry_run=False, use_vlm=False, enable_s89=False, enable_s90=False)
    return _SCREEN_CONTROLLER

# ── FRENOS (brakes): acciones irreversibles/peligrosas que EIDOS NO ejecuta solo ──
_DANGER = (
    "delete", "borrar", "eliminar", "remove", "drop", "wipe", "format", "formatear",
    "enviar", "send", "publicar", "publish", "post tweet", "pagar", "pay", "comprar",
    "buy", "purchase", "checkout", "confirmar compra", "confirm purchase", "transfer",
    "transferir", "logout", "cerrar sesión", "sign out", "uninstall", "desinstalar",
    "shutdown", "apagar", "reboot", "reiniciar", "factory reset", "restablecer",
    "rm -rf", "sudo rm", "drop table",
)


class SafetyGuard:
    """Decide si una acción sobre un elemento es segura para que EIDOS la haga SOLO."""

    @staticmethod
    def is_safe(label: str, action: str) -> Tuple[bool, str]:
        low = (label or "").lower().strip()
        for d in _DANGER:
            if d in low:
                return False, f"freno: '{d}' es irreversible/peligroso → requiere SER"
        if action == "execute_shell":
            return False, "freno: shell directo solo bajo SHELL_ALLOWLIST/SER"
        return True, "ok"


# ── PERCEPCIÓN: estado de la pantalla (qué hay y qué es) ──────────────────────
def _perceive(app_name: Optional[str] = None) -> Tuple[str, List[Any]]:
    """Devuelve (state_hash, elements). AT-SPI2 primero (SABER); OCR de respaldo."""
    elements: List[Any] = []
    try:
        from core.perception import get_accessible_elements
        elements = get_accessible_elements(app_name) or []
    except Exception as e:
        log.debug("AT-SPI2 no disponible: %s", e)
    if not elements:
        try:
            from core.perception import take_screenshot, ocr_screenshot
            shot = take_screenshot("causal_loop")
            if shot:
                elements = ocr_screenshot(shot) or []
        except Exception as e:
            log.debug("OCR fallback falló: %s", e)
    labels = sorted({(getattr(e, "text", "") or "").strip()[:40]
                     for e in elements if (getattr(e, "text", "") or "").strip()})
    # S124: el ESTADO incluye el CUERPO (zona de la mano + ventana), no solo los ojos.
    # Así el Q-learning aprende de forma corporal (dónde estoy + qué veo), no abstracta.
    try:
        # CORRECCIÓN (crítica de revisión): solo la VENTANA en el estado de decisión.
        # La posición de la mano NO va aquí (fragmentaría 'qué clicar'); va a memoria motora.
        from core.body import window_context
        body = window_context()
    except Exception:
        body = ""
    state_hash = hashlib.sha1(("|".join(labels) + "||" + body).encode()).hexdigest()[:16]
    return state_hash, elements


def _recall_known(labels: List[str]) -> Dict[str, Tuple[float, str]]:
    """RAZONAR sobre lo que YA SABE: consulta el grafo (priors UI + skills aprendidas)
    y devuelve {label_lower: (confianza, significado)}. EIDOS interpreta lo que ve
    con sus propios nodos. Una sola query (perf)."""
    out: Dict[str, Tuple[float, str]] = {}
    lows = [l.lower() for l in labels if l]
    if not lows:
        return out
    try:
        from core.db import get_conn
        c = get_conn(BRAIN_DB, timeout=10)
        rows = c.execute(
            "SELECT concept, definition, confidence FROM knowledge_nodes "
            "WHERE source IN ('skill_learned','seed_ui_prior') "
            "ORDER BY confidence DESC LIMIT 800").fetchall()
        for concept, definition, conf in rows:
            text = (str(concept) + " " + str(definition or "")).lower()
            for ll in lows:
                if ll and ll in text and ll not in out:
                    out[ll] = (float(conf or 0.5), str(definition or concept)[:70])
    except Exception as e:
        log.debug("_recall_known: %s", e)
    return out


def _affordances(elements: List[Any], goal: str) -> List[Tuple[str, Any, str]]:
    """Acciones-candidatas SEGURAS: [(action_key, element, significado_conocido)].
    Puntúa por: objetivo (goal) + lo que EIDOS YA ENTIENDE de su grafo (razona sobre
    lo conocido en vez de tantear a ciegas)."""
    goal_words = {w for w in (goal or "").lower().split() if len(w) > 3}
    labels = [(getattr(el, "text", "") or "").strip() for el in elements]
    known = _recall_known([l for l in labels if l])
    cands: List[Tuple[str, Any, float, str]] = []
    for el in elements:
        label = (getattr(el, "text", "") or "").strip()
        if not label or len(label) > 50:
            continue
        safe, _ = SafetyGuard.is_safe(label, "gui_click")
        if not safe:
            continue
        action_key = f"click:{label.lower()[:40]}"
        score = sum(1 for w in goal_words if w in label.lower()) * 2.0
        k = known.get(label.lower())
        if k:                        # ya lo entiende → poco aliciente (salvo que sirva al goal)
            meaning = k[1]
            score += 0.4
        else:                        # NO lo conoce → CURIOSIDAD: probarlo PARA APRENDERLO
            meaning = ""
            score += 1.8             # lo desconocido ATRAE: así EIDOS aprende lo que no sabe
        cands.append((action_key, el, score, meaning))

    # ── screen_controller como fuente adicional de candidatos (prioridad ALTA 0.85) ─
    try:
        sc = _get_sc()
        sc_affs = sc.get_affordances(goal)
        for sa in sc_affs:
            label = sa.get("label", "")
            if not label or len(label) > 50:
                continue
            safe, _ = SafetyGuard.is_safe(label, "gui_click")
            if not safe:
                continue
            # Crear pseudo-elemento con coordenadas del screen_controller
            sc_element = type("SCElement", (), {
                "text": label,
                "x": sa.get("x", 0),
                "y": sa.get("y", 0),
                "width": 0,
                "height": 0,
                "from_sc": True,        # flag para que _act() use InputBackend
                "sc_data": sa,          # datos completos para el backend
            })()
            action_key = f"click:{label.lower()[:40]}"
            # Prioridad ALTA: 0.85 base + bonus por goal-match
            score = 0.85 + sum(1 for w in goal_words if w in label.lower()) * 1.5
            k = known.get(label.lower())
            meaning = k[1] if k else ""
            cands.append((action_key, sc_element, score, meaning))
    except Exception as e:
        log.debug("screen_controller affordances: %s", e)

    cands.sort(key=lambda c: c[2], reverse=True)
    return [(a, e, m) for a, e, _, m in cands]


# ── ACCIÓN: ejecuta (o simula en dry_run) ────────────────────────────────────
def _act(element: Any, dry_run: bool) -> bool:
    x, y = int(getattr(element, "x", 0)), int(getattr(element, "y", 0))
    label = (getattr(element, "text", "") or "")[:40]
    from_sc = getattr(element, "from_sc", False)

    if dry_run:
        backend = "InputBackend" if from_sc else "xdotool"
        log.info("[DRY] click virtual en '%s' (%d,%d) via %s", label, x, y, backend)
        return True

    # ── screen_controller path: usa HumanEmulator/InputBackend en vez de xdotool directo ─
    if from_sc:
        try:
            sc = _get_sc()
            # HumanEmulator: trayectoria humana + click con ritmo natural
            sc.human_emulator.click_at(x, y)
            log.info("click REAL via InputBackend en '%s' (%d,%d)", label, x, y)
            try:
                from core.body import remember_motor
                remember_motor(label, x, y, success=True, confidence=0.70,
                               source='bom_sc')
            except Exception:
                pass
            return True
        except Exception as e:
            log.warning("InputBackend click falló: %s → fallback xdotool", e)
            # fall through to xdotool path below

    # ── S125-READY: GATE de presencia de SER ──────────────────────────────
    # Solo actuar si SER está presente O EIDOS_MASTER_MODE=1
    if os.environ.get("EIDOS_MASTER_MODE") != "1":
        try:
            import subprocess
            r = subprocess.run(["xprintidle"], capture_output=True, text=True, timeout=3)
            idle_ms = int(r.stdout.strip() or 0)
            if idle_ms > 300_000:  # 5 minutos sin actividad = SER ausente
                log.warning("[BOM] SER ausente (%ds idle) → no actúo sin supervisión", idle_ms // 1000)
                return False
        except Exception as exc:
            # Fail closed: inability to establish operator presence is not
            # evidence that a real GUI action is safe.
            log.warning("[BOM] no pude verificar presencia de SER: %s → no actúo", exc)
            return False
    # ── autoconcepto operativo: ¿tengo mano? mi mano es xdotool ──────────
    try:
        from core.body import hand_ok
        if not hand_ok():
            log.warning("[CUERPO] no tengo mano (xdotool ausente) → no puedo tocar")
            return False
    except Exception:
        pass
    try:  # real: endpoint del web-panel (xdotool)
        import urllib.request, json
        req = urllib.request.Request(
            "http://127.0.0.1:8080/api/screen/click",
            data=json.dumps({"x": x, "y": y, "button": 1}).encode(),
            headers={"Content-Type": "application/json"}, method="POST")
        urllib.request.urlopen(req, timeout=8)
        log.info("click REAL en '%s' (%d,%d)", label, x, y)
        # ── Save motor memory immediately after real click ───────────────
        try:
            from core.body import remember_motor
            remember_motor(label, x, y, success=True, confidence=0.65,
                           source='bom_real')
        except Exception:
            pass
        # ── Recovery: verificar que el click tuvo efecto ──────────────────
        try:
            from core.eidos_recovery import get_recovery
            recovery = get_recovery()
            verified, detail = recovery.verifier.verify("click",
                {"x": x, "y": y, "screen_before_hash": ""}, "")
            if not verified:
                log.warning("[RECOVERY] click en '%s' no verificado: %s", label, detail)
        except Exception:
            pass
        return True
    except Exception as e:
        log.warning("click real falló: %s", e)
        # ── Recovery: intentar fallback ───────────────────────────────────
        try:
            from core.eidos_recovery import get_recovery
            recovery = get_recovery()
            fallback_result = recovery.fallback_engine.fallback_click(x, y, 1)
            if fallback_result and fallback_result.get("ok"):
                log.info("[RECOVERY] fallback click exitoso en (%d,%d)", x, y)
                return True
        except Exception:
            pass
        return False


# ── VERIFICAR: ¿pasó algo? ¿avanzó hacia el goal? → recompensa ────────────────
def _verify(before_hash: str, before_labels: set, after_hash: str,
            after_labels: set, goal: str) -> Tuple[float, str]:
    if before_hash == after_hash:
        return -0.3, "sin efecto (pantalla igual)"
    goal_words = {w for w in (goal or "").lower().split() if len(w) > 3}
    nuevos = after_labels - before_labels
    if goal_words and any(w in " ".join(nuevos).lower() for w in goal_words):
        return 3.0, "apareció algo del objetivo"
    return 0.8, "la pantalla cambió (efecto observado)"


# ── APRENDER: Q-learning + neurona permanente del cause→effect ────────────────
def _learn(state_hash: str, action_key: str, reward: float, next_hash: str,
           label: str, effect: str):
    try:
        from core.eidos_rl import get_rl_agent
        get_rl_agent().learn(state_hash, action_key, reward, next_hash)
    except Exception as e:
        log.debug("rl.learn: %s", e)
    if reward <= 0:
        return  # solo persistimos cause→effect ÚTIL como neurona
    try:
        from core.db import get_conn
        import uuid
        conf = min(0.95, 0.5 + reward / 10.0)
        concept = f"skill: en pantalla {state_hash}, click '{label[:30]}' → {effect}"
        c = get_conn(BRAIN_DB, timeout=20)
        row = c.execute("SELECT id, confidence FROM knowledge_nodes WHERE concept=?",
                        (concept,)).fetchone()
        if row:   # ya lo había aprendido → REFUERZA (repetir fortalece la neurona)
            new_conf = min(0.99, max(conf, float(row[1] or 0.5)) + 0.05)
            c.execute("UPDATE knowledge_nodes SET confidence=? WHERE id=?", (new_conf, row[0]))
            log.info("neurona reforzada (conf %.2f): %s", new_conf, concept[:55])
        else:     # nueva causa→efecto descubierta
            nid = "skill_" + uuid.uuid4().hex[:12]
            c.execute(
                "INSERT INTO knowledge_nodes "
                "(id, concept, definition, category, confidence, source, created_at) "
                "VALUES (?,?,?,?,?,?,?)",
                (nid, concept, f"click '{label[:40]}' produce: {effect}", "skill",
                 conf, "skill_learned", time.strftime("%Y-%m-%d %H:%M:%S")))
            log.info("neurona aprendida (conf %.2f): %s", conf, concept[:55])
        c.commit()
    except Exception as e:
        log.debug("persist skill: %s", e)


def _relocate(label: str, app_name: Optional[str]) -> Optional[Any]:
    """VERIFY-BEFORE-ACT (la pieza que Kimi y Claude marcaron): re-percibe JUSTO antes
    de clicar y devuelve el elemento con esta etiqueta AHORA (coordenadas frescas), o None
    si ya no está (el botón se movió/desapareció → NO clicar a ciegas en coords viejas)."""
    try:
        _, els = _perceive(app_name)
        ll = (label or "").strip().lower()
        for e in els:
            if (getattr(e, "text", "") or "").strip().lower() == ll:
                return e
    except Exception as e:
        log.debug("_relocate: %s", e)
    return None


def _research_unknown(label: str) -> bool:
    """Si EIDOS NO SABE qué es algo, lo INVESTIGA (no solo lo tantea): research_now
    busca/aprende y persiste al grafo. Best-effort, gated por EIDOS_BOM_RESEARCH=1
    (off por defecto, para no frenar el bucle). Así 'lo desconocido' se vuelve conocido."""
    import os as _os
    if _os.environ.get("EIDOS_BOM_RESEARCH") != "1":
        return False
    term = (label or "").strip()
    if len(term) < 4 or term.lower() in ("aceptar", "cancelar", "cerrar", "abrir", "ok", "atrás"):
        return False
    try:
        from core.eidos_active_research import research_now
        r = research_now(term, timeout=8.0, persist=True, prefer_remote=False)
        if r.get("learned"):
            log.info("🔎 investigó lo desconocido '%s' → aprendido", term[:30])
            return True
    except Exception as e:
        log.debug("_research_unknown: %s", e)
    return False


# ── EL BUCLE ──────────────────────────────────────────────────────────────────
def step(goal: str = "", app_name: Optional[str] = None,
         dry_run: bool = True) -> Dict[str, Any]:
    """Un ciclo BOM: percibir→decidir→actuar→verificar→aprender."""
    from core.eidos_rl import get_rl_agent
    s_hash, els = _perceive(app_name)
    before_labels = {(getattr(e, "text", "") or "").strip() for e in els}
    affs = _affordances(els, goal)
    if not affs:
        return {"ok": False, "reason": "sin elementos accionables seguros",
                "state": s_hash, "perceived": len(els)}
    action_keys = [a for a, _, _ in affs]
    key_to_el = {a: e for a, e, _ in affs}
    key_to_meaning = {a: m for a, e, m in affs}
    chosen_key = get_rl_agent().select_action(s_hash, available_actions=action_keys)
    element = key_to_el.get(chosen_key, affs[0][1])
    meaning = key_to_meaning.get(chosen_key, "")   # lo que EIDOS YA entendía de esto
    label = (getattr(element, "text", "") or "")[:40]
    if not meaning:                  # no lo conoce → además de probarlo, lo investiga (gated)
        _research_unknown(label)
    from core.body import hand_position as _hand
    _hand0 = _hand()                 # propiocepción: dónde está mi mano ANTES
    # VERIFY-BEFORE-ACT: en real, confirmar que el objetivo SIGUE ahí justo antes de tocar.
    if not dry_run:
        fresh = _relocate(label, app_name)
        if fresh is None:
            return {"ok": False, "reason": "el objetivo desapareció antes de tocar (re-percibo, no clico a ciegas)",
                    "action": chosen_key, "label": label, "state": s_hash}
        element = fresh              # usar las coordenadas ACTUALES, no las de hace un instante
    acted = _act(element, dry_run)
    if not acted:
        # fallo CORPORAL, no número: EIDOS lo entiende como su mano fallando
        return {"ok": False, "reason": "mi mano no pudo tocar (fallo motor)", "action": chosen_key}
    time.sleep(0.6 if not dry_run else 0)
    n_hash, n_els = (_perceive(app_name) if not dry_run else (s_hash, els))
    after_labels = {(getattr(e, "text", "") or "").strip() for e in n_els}
    reward, effect = _verify(s_hash, before_labels, n_hash, after_labels, goal)
    # razonamiento: descubrir lo DESCONOCIDO vale MÁS que confirmar lo sabido
    if reward > 0 and not meaning:
        reward += 0.7
        effect = f"{effect} (DESCUBRIÓ algo nuevo que no sabía)"
    elif reward > 0 and meaning:
        reward += 0.4
        effect = f"{effect} (confirmó lo que sabía: {meaning[:35]})"
    _hand1 = _hand()                 # propiocepción: dónde quedó mi mano DESPUÉS
    if _hand0 and _hand1 and _hand0 != _hand1:
        effect = f"{effect} · mano {_hand0}→{_hand1}"
    # memoria corporal QUERYABLE: registra la POSICIÓN del ELEMENTO si funcionó,
    # para luego consultar recall_motor('Settings') → (x,y). (corrección de revisión)
    if reward > 0:
        try:
            from core.body import remember_motor
            ex = int(getattr(element, "x", 0))
            ey = int(getattr(element, "y", 0))
            conf = min(0.95, 0.5 + reward / 10.0)
            remember_motor(label, ex, ey, success=True,
                           confidence=conf, source='bom_real')
        except Exception:
            pass
    _learn(s_hash, chosen_key, reward, n_hash, label, effect)
    return {"ok": True, "action": chosen_key, "label": label, "knew": meaning[:50],
            "reward": round(reward, 2), "effect": effect,
            "state": s_hash, "next_state": n_hash, "dry_run": dry_run}


def run(goal: str = "", steps: int = 5, app_name: Optional[str] = None,
        dry_run: bool = True) -> Dict[str, Any]:
    log.info("BOM run: goal=%r steps=%d dry_run=%s", goal, steps, dry_run)
    history = []
    for i in range(steps):
        r = step(goal, app_name, dry_run)
        history.append(r)
        log.info("paso %d/%d → %s", i + 1, steps, r)
        if not r.get("ok") and r.get("reason", "").startswith("sin elementos"):
            break
    total = sum(h.get("reward", 0) for h in history if h.get("ok"))
    return {"goal": goal, "steps_done": len(history),
            "total_reward": round(total, 2), "dry_run": dry_run,
            "history": history}


# ── MAPA: priors UI sembrados (suelo donde crece el aprendizaje) ──────────────
_PRIORS = [
    ("UI: icono de engranaje ⚙️", "Un engranaje suele abrir la configuración (Settings) de una app."),
    ("UI: icono de lupa 🔍", "La lupa sirve para buscar dentro de la app."),
    ("UI: campo de texto inferior", "Un campo vacío abajo suele ser donde se escribe (mensaje, búsqueda)."),
    ("UI: nombre en una lista lateral", "Un nombre propio en una lista lateral suele ser un contacto/conversación clicable."),
    ("UI: barra superior", "La barra superior suele tener menús, título y acciones de la ventana."),
    ("UI: botón con X", "Una X suele cerrar la ventana o el diálogo actual."),
    ("UI: tres líneas ☰", "El icono de tres líneas (hamburguesa) abre el menú principal."),
    ("UI: Enter tras escribir", "Pulsar Enter después de escribir suele enviar/confirmar."),
]


def seed_ui_priors() -> int:
    """Inyecta conocimiento base de convenciones UI en el grafo (idempotente)."""
    from core.db import get_conn
    import uuid
    c = get_conn(BRAIN_DB, timeout=20)
    n = 0
    for concept, definition in _PRIORS:
        exists = c.execute("SELECT 1 FROM knowledge_nodes WHERE concept=?",
                           (concept,)).fetchone()
        if exists:
            continue
        c.execute(
            "INSERT OR IGNORE INTO knowledge_nodes "
            "(id, concept, definition, category, confidence, source, created_at) "
            "VALUES (?,?,?,?,?,?,?)",
            ("prior_" + uuid.uuid4().hex[:12], concept, definition, "ui_prior",
             0.7, "seed_ui_prior", time.strftime("%Y-%m-%d %H:%M:%S")))
        n += 1
    c.commit()
    return n


if __name__ == "__main__":
    import sys
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    if "--seed" in sys.argv:
        print(f"priors UI sembrados: {seed_ui_priors()}")
        sys.exit(0)
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    goal = args[0] if args else ""
    steps = 5
    if "--steps" in sys.argv:
        try:
            steps = int(sys.argv[sys.argv.index("--steps") + 1])
        except Exception:
            pass
    dry = "--real" not in sys.argv   # SECO por defecto
    import json
    print(json.dumps(run(goal, steps=steps, dry_run=dry), ensure_ascii=False, indent=2))
