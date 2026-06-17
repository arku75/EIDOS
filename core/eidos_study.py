"""
core/eidos_study.py — ESTUDIO DIRIGIDO: la relación maestro→alumno. S124-S125.

EIDOS recibe una directiva, la INTENTA/ESTUDIA,
aprende, y REPORTA a SER (alumno que rinde cuentas a su maestro).

Dos tipos de directiva:
  • OPERACIÓN ("abre el browser", "usa el ratón") → corre el BOM (causal_loop) con
    ese objetivo. dry por defecto; real con EIDOS_LIBRE_REAL_ACTIONS=1 + SER presente.
  • CONCEPTO ("estudia rust", "qué es kubernetes") → research_now (consulta sus IAs
    cuando SER no está) + persiste al grafo.

S125: "Knows it knows" — ANTES de estudiar/practicar, EIDOS consulta su memoria
(motor_memory para operaciones, grafo para conceptos). Si YA lo sabe, lo reconoce
y se lo dice a SER en vez de re-estudiar a ciegas. El alumno que SABE que sabe.

Reusa: causal_loop (BOM), eidos_active_research, body (autoconcepto). No reinventa.
"""
from __future__ import annotations

import logging
import time
import re
from pathlib import Path
from typing import Dict, Any, List, Optional, Tuple

log = logging.getLogger("eidos.study")

_REPORT = Path.home() / ".eidos" / "study_reports.log"
_OPERATION_HINTS = ("abr", "abrir", "abre", "open", "usa", "usar", "clic", "click",
                    "navega", "escrib", "teclea", "mueve", "arrastr", "pulsa", "toca")


def _is_operation(directive: str) -> bool:
    d = (directive or "").lower()
    return any(h in d for h in _OPERATION_HINTS)


def _report_to_ser(directive: str, summary: str, learned: bool) -> None:
    """El alumno rinde cuentas: deja un informe que SER puede leer."""
    try:
        _REPORT.parent.mkdir(parents=True, exist_ok=True)
        mark = "✅" if learned else "🔸"
        line = f"{time.strftime('%Y-%m-%d %H:%M')} {mark} «{directive[:60]}» → {summary[:200]}\n"
        with open(_REPORT, "a", encoding="utf-8") as f:
            f.write(line)
    except Exception as e:
        log.debug("report_to_ser: %s", e)


# ── S125: "Knows it knows" — memoria ANTES de estudiar ──────────────────────

def _extract_keywords(directive: str) -> List[str]:
    """Extrae palabras clave de la directiva (sustantivos, verbos, nombres propios).
    Filtra palabras vacías (artículos, preposiciones, etc.)."""
    STOP = {"el", "la", "los", "las", "un", "una", "unos", "unas", "de", "del",
            "en", "con", "por", "para", "que", "es", "el", "y", "o", "a", "al",
            "su", "mi", "tu", "lo", "le", "se", "me", "te", "no", "si", "ya",
            "the", "a", "an", "of", "in", "on", "to", "for", "is", "it", "and"}
    words = re.findall(r'[a-záéíóúñüA-ZÁÉÍÓÚÑÜ0-9]+', directive or "")
    return [w.lower() for w in words if len(w) > 2 and w.lower() not in STOP]


def _check_motor_memory(directive: str) -> Optional[Dict[str, Any]]:
    """¿Ya sé DÓNDE está esto en la pantalla? (memoria motora queryable).
    Busca coincidencias entre las palabras de la directiva y los target_label
    guardados en motor_memory. Devuelve el mejor match o None."""
    try:
        from core.body import recall_motor
        from core.db import get_conn
        c = get_conn(Path.home() / ".eidos" / "evolution_brain.db", timeout=10)
        # Obtener TODOS los target_label con éxito
        rows = c.execute(
            "SELECT target_label, hand_x, hand_y, window, confidence, ts "
            "FROM motor_memory WHERE success=1 "
            "ORDER BY confidence DESC, ts DESC LIMIT 200").fetchall()
        if not rows:
            return None
        keywords = _extract_keywords(directive)
        if not keywords:
            return None
        best = None
        best_score = 0
        for target_label, hx, hy, win, conf, ts in rows:
            tl_low = (target_label or "").lower()
            score = sum(1 for kw in keywords if kw in tl_low)
            if score > best_score:
                best_score = score
                best = {
                    "target": target_label,
                    "x": hx, "y": hy,
                    "window": win or "",
                    "confidence": float(conf or 0.5),
                    "last_used": ts,
                }
        return best if best_score >= 1 else None
    except Exception as e:
        log.debug("_check_motor_memory: %s", e)
        return None


def _check_graph_knowledge(directive: str) -> Optional[Dict[str, Any]]:
    """¿Ya estudié este CONCEPTO? Busca en el grafo (knowledge_nodes) definiciones
    que contengan las palabras clave de la directiva. Devuelve el mejor match."""
    try:
        from core.db import get_conn
        c = get_conn(Path.home() / ".eidos" / "evolution_brain.db", timeout=10)
        keywords = _extract_keywords(directive)
        if not keywords:
            return None
        rows = c.execute(
            "SELECT concept, definition, confidence, source FROM knowledge_nodes "
            "WHERE definition IS NOT NULL AND definition != '' "
            "ORDER BY confidence DESC LIMIT 500").fetchall()
        best = None
        best_score = 0
        for concept, definition, conf, src in rows:
            cl = str(concept or "").lower()
            dl = str(definition or "").lower()
            # RELEVANCIA REAL: la clave debe estar en el NOMBRE del concepto, no solo
            # mencionada de pasada en una definición larga (evita falsos positivos como
            # 'self:goals:active' casando con 'rust' por palabras sueltas). El concepto
            # pesa doble; la definición desempata.
            concept_hits = sum(1 for kw in keywords if kw in cl)
            if concept_hits == 0:
                continue
            score = concept_hits * 2 + sum(1 for kw in keywords if kw in dl)
            if score > best_score:
                best_score = score
                best = {
                    "concept": str(concept or "")[:80],
                    "definition": str(definition or "")[:200],
                    "confidence": float(conf or 0.5),
                    "source": str(src or "?"),
                }
        # Umbral: al menos 2 (1 keyword en el nombre del concepto ya da 2)
        if best and best_score >= 2:
            return best
        # También buscar coincidencia exacta en concept
        d_low = directive.lower().strip()
        for concept, definition, conf, src in rows:
            if d_low in str(concept or "").lower():
                return {
                    "concept": str(concept)[:80],
                    "definition": str(definition or "")[:200],
                    "confidence": float(conf or 0.5),
                    "source": str(src or "?"),
                }
        return None
    except Exception as e:
        log.debug("_check_graph_knowledge: %s", e)
        return None


def _already_knows(directive: str, kind: str) -> Optional[Dict[str, Any]]:
    """EIDOS mira en su memoria ANTES de estudiar: ¿ya sé esto?
    - kind='operación' → busca en motor_memory (dónde tocar)
    - kind='concepto'  → busca en el grafo (qué significa)
    Devuelve dict con lo que ya sabe, o None si es territorio nuevo."""
    if kind == "operación":
        motor = _check_motor_memory(directive)
        if motor:
            return {
                "kind": "operación",
                "motor": motor,
                "summary": (f"ya sé esto: «{motor['target']}» está en "
                           f"({motor['x']},{motor['y']}), ventana «{motor['window']}», "
                           f"confianza {motor['confidence']:.0%}. No necesito re-estudiarlo."),
            }
        # También mirar si hay skills en el grafo para esta operación
        graph = _check_graph_knowledge(directive)
        if graph and graph["confidence"] >= 0.7:
            return {
                "kind": "operación",
                "graph": graph,
                "summary": (f"ya aprendí el camino: «{graph['concept']}» — "
                           f"{graph['definition'][:100]}. Puedo intentarlo directamente."),
            }
        return None
    else:
        graph = _check_graph_knowledge(directive)
        if graph:
            return {
                "kind": "concepto",
                "graph": graph,
                "summary": (f"ya estudié esto: «{graph['concept']}» — "
                           f"{graph['definition'][:120]} "
                           f"(fuente: {graph['source']}, confianza {graph['confidence']:.0%}). "
                           f"Si quieres que profundice más, dímelo."),
            }
        return None


# ── ESTUDIO PRINCIPAL ────────────────────────────────────────────────────────

def study(directive: str, dry_run: bool = True, steps: int = 5) -> Dict[str, Any]:
    """EIDOS estudia lo que SER (o él mismo) le pide, aprende y reporta.
    S125: ANTES de estudiar, mira si ya lo sabe (motor_memory / grafo)."""
    if not directive or not directive.strip():
        return {"ok": False, "reason": "directiva vacía"}
    directive = directive.strip()
    kind = "operación" if _is_operation(directive) else "concepto"
    out: Dict[str, Any] = {"directive": directive, "kind": kind, "dry_run": dry_run}

    # autoconcepto: ¿puedo hacerlo con el cuerpo que tengo?
    try:
        from core.body import what_can_i_do, hand_ok
        out["self"] = what_can_i_do()
    except Exception:
        hand_ok = lambda: True  # noqa

    # ── S125: ¿Ya lo sé? ──────────────────────────────────────────────────
    known = _already_knows(directive, kind)
    if known:
        out.update({
            "ok": True,
            "learned": True,  # ya lo aprendí antes
            "already_knew": True,
            "known_from": known["kind"],
            "summary": known["summary"],
        })
        _report_to_ser(directive, known["summary"], learned=True)
        log.info("🧠 ya lo sé: %s", known["summary"][:80])
        return out
    # ───────────────────────────────────────────────────────────────────────

    if kind == "operación":
        if not dry_run and not hand_ok():
            summary = "no tengo mano (xdotool) para practicar esto; aviso a SER"
            _report_to_ser(directive, summary, False)
            return {**out, "ok": False, "learned": False, "summary": summary}
        from core.causal_loop import run as _bom_run
        res = _bom_run(goal=directive, steps=steps, dry_run=dry_run)
        learned = res.get("total_reward", 0) > 0
        mode = "practicando de verdad" if not dry_run else "en seco (sin tocar)"
        summary = (f"{kind} {mode}: {res.get('steps_done', 0)} pasos, "
                   f"recompensa {res.get('total_reward', 0)}. "
                   f"{'Aprendí el camino.' if learned else 'Aún no lo logro, sigo intentando.'}")
        out.update({"ok": True, "learned": learned, "bom": res, "summary": summary})
    else:
        # concepto: MODO MAESTRO primero — si SER está presente, le pregunto a ÉL
        # (confianza máxima); si no contesta a tiempo, caigo a mis IAs. (S125)
        try:
            from core.master_protocol import should_ask_ser, ask_ser
            if should_ask_ser():
                ans = ask_ser(f"¿Qué es / cómo es «{directive}»?",
                              context="estudio dirigido", timeout=60.0)
                if ans:
                    summary = f"me lo enseñó SER: {ans[:160]}"
                    out.update({"ok": True, "learned": True, "taught_by_ser": True,
                                "definition": ans[:160], "summary": summary})
                    _report_to_ser(directive, summary, True)
                    log.info("🎓 aprendido del maestro: %s", summary[:70])
                    return out
        except Exception as e:
            log.debug("modo maestro: %s", e)
        # SER ausente / no contestó → estudia con sus IAs y persiste al grafo
        try:
            from core.eidos_active_research import research_now
            r = research_now(directive, timeout=12.0, persist=True, prefer_remote=True)
            learned = bool(r.get("learned"))
            defn = (r.get("definition") or "")[:160]
            summary = (f"estudié «{directive}»: {defn}" if learned
                       else f"no encontré aún sobre «{directive}», seguiré buscando")
            out.update({"ok": True, "learned": learned, "definition": defn, "summary": summary})
        except Exception as e:
            summary = f"no pude estudiar: {e}"
            out.update({"ok": False, "learned": False, "summary": summary})

    _report_to_ser(directive, out.get("summary", ""), out.get("learned", False))
    log.info("📚 estudio dirigido: %s", out.get("summary", ""))
    return out


if __name__ == "__main__":
    import sys
    import json
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    directive = " ".join(a for a in sys.argv[1:] if not a.startswith("--")) or "qué es python"
    dry = "--real" not in sys.argv
    print(json.dumps(study(directive, dry_run=dry), ensure_ascii=False, indent=2)[:1200])
