"""
core/study_queue.py — MODO AUTÓNOMO: cola de estudio para cuando SER no está. S125.

SER deja una lista de cosas que estudiar ("estudia rust", "aprende docker", "abre el
browser"). EIDOS la trabaja SOLO: consulta sus IAs (research_now → Wikipedia/DDG/
GitHub/man; y si no encuentra, pregunta directo a Groq/DeepSeek = "habla con una IA"),
persiste al grafo, y deja un INFORME EXTENSO en ~/.eidos/study_report.md que SER lee
al volver. Las operaciones ("abre el browser") corren el BOM (en seco salvo --real).

Reusa: eidos_study.study (operación/concepto + "knows it knows"), eidos_learn (IAs
directas), research_now. No reinventa. NO toca ratón salvo --real con SER presente.

CLI:
    python3 -m core.study_queue add "estudia rust" [--priority 1]
    python3 -m core.study_queue list
    python3 -m core.study_queue run [--real] [--max 20]
    python3 -m core.study_queue report
"""
from __future__ import annotations

import logging
import time
import uuid
from pathlib import Path
from typing import Any, Dict, List

log = logging.getLogger("eidos.study_queue")

BRAIN_DB = Path.home() / ".eidos" / "evolution_brain.db"
_REPORT = Path.home() / ".eidos" / "study_report.md"


def _conn():
    from core.db import get_conn
    c = get_conn(BRAIN_DB, timeout=20)
    c.execute("""CREATE TABLE IF NOT EXISTS study_queue(
        id TEXT PRIMARY KEY, ts REAL, directive TEXT, priority INTEGER DEFAULT 5,
        status TEXT DEFAULT 'pending', kind TEXT, learned INTEGER DEFAULT 0,
        summary TEXT, source TEXT, done_at REAL)""")
    return c


def enqueue(directive: str, priority: int = 5) -> str:
    """SER añade una directiva a la cola. priority: 1=urgente .. 9=cuando puedas."""
    directive = (directive or "").strip()
    if not directive:
        return ""
    qid = "sq_" + uuid.uuid4().hex[:10]
    c = _conn()
    c.execute("INSERT INTO study_queue(id,ts,directive,priority,status) VALUES(?,?,?,?,'pending')",
              (qid, time.time(), directive[:300], int(priority)))
    c.commit()
    log.info("📥 en cola (%s, prio %d): %s", qid, priority, directive[:60])
    return qid


def list_items(status: str = "") -> List[Dict[str, Any]]:
    c = _conn()
    if status:
        rows = c.execute("SELECT id,directive,priority,status,kind,learned,summary,source "
                         "FROM study_queue WHERE status=? ORDER BY priority ASC, ts ASC",
                         (status,)).fetchall()
    else:
        rows = c.execute("SELECT id,directive,priority,status,kind,learned,summary,source "
                         "FROM study_queue ORDER BY priority ASC, ts ASC").fetchall()
    cols = ["id", "directive", "priority", "status", "kind", "learned", "summary", "source"]
    return [dict(zip(cols, r)) for r in rows]


def _ask_own_ias(directive: str) -> Dict[str, Any]:
    """Último recurso para un CONCEPTO que research_now no resolvió: HABLAR con sus
    propias IAs (Groq/DeepSeek). Esto es 'consultar a las IAs que tiene' literal."""
    prompt = (f"Explica de forma clara y útil, en español: {directive}. "
              f"Da una definición y por qué importa.")
    for fn_name in ("ask_llm", "_call_deepseek", "_call_groq"):
        try:
            from core import eidos_learn
            fn = getattr(eidos_learn, fn_name, None)
            if not fn:
                continue
            txt = fn(prompt)
            if isinstance(txt, (tuple, list)):   # ask_llm devuelve (texto, meta)
                txt = txt[0] if txt else ""
            if txt and len(str(txt).strip()) >= 40:
                defn = str(txt).strip()
                # persistir como nodo aprendido de su IA
                try:
                    from core.eidos_active_research import _persist
                    _persist(directive[:60], defn[:1000], f"ia:{fn_name}", 0.7)
                except Exception:
                    pass
                return {"learned": True, "definition": defn, "source": f"ia:{fn_name}"}
        except Exception as e:
            log.debug("_ask_own_ias %s: %s", fn_name, e)
    return {"learned": False, "definition": "", "source": ""}


import re as _re
_WEB_HINTS = ("http://", "https://", "labex", "en la web", "en internet", "en la red",
              "navega a", "busca en internet", "tutorial de", "curso de", "página de",
              "documentación de", "docs de")


def _is_web(directive: str) -> bool:
    """¿Esta directiva pide aprender de la WEB de verdad (no solo del grafo/IA)?"""
    d = (directive or "").lower()
    return any(h in d for h in _WEB_HINTS)


def _extract_url(directive: str):
    m = _re.search(r'https?://\S+', directive or "")
    return m.group(0).rstrip(').,') if m else None


_REGISTER_HINTS = ("regístrate", "registrate", "regístrame", "crea cuenta", "crea una cuenta",
                   "crear cuenta", "darse de alta", "date de alta", "alta en", "sign up",
                   "sign-up", "signup", "haz cuenta", "abre cuenta")


def _is_register(directive: str) -> bool:
    d = (directive or "").lower()
    return any(h in d for h in _REGISTER_HINTS)


def _platform_from(directive: str) -> str:
    """Saca el sitio: URL > dominio (algo.algo) > palabra tras 'en' > labex.io por defecto."""
    url = _extract_url(directive)
    if url:
        return url
    m = _re.search(r'\b([a-z0-9][a-z0-9-]*\.[a-z]{2,}(?:\.[a-z]{2,})?)\b', (directive or "").lower())
    if m:
        return m.group(1)
    m = _re.search(r'\ben\s+([a-z0-9.\-]+)', (directive or "").lower())
    return m.group(1) if m else "labex.io"


def _do_register(directive: str) -> Dict[str, Any]:
    """EIDOS se da de alta SOLO en el sitio que SER pidió (su correo + navegador)."""
    try:
        from core.eidos_register import register
    except Exception as e:
        return {"learned": False, "summary": f"no pude cargar el registrador: {e}", "source": "register"}
    plat = _platform_from(directive)
    r = register(plat, visible=False)
    return {"learned": bool(r.get("ok")),
            "summary": r.get("summary") or r.get("reason", ""), "source": f"register:{plat}"}


def _study_web(directive: str, visible: bool = False) -> Dict[str, Any]:
    """EIDOS VIVO en el navegador: abre la web de verdad (headless = desatendido y seguro;
    visible = Firefox real de SER), lee páginas y mete nodos a su grafo. Reusa colony_studier.

    S127: Si colony_studier falla o no esta disponible, usa eidos_web_actor.act_on_page()
    como fallback para webs que requieren login/interaccion."""
    # ── Primary: colony_studier ────────────────────────────────────────────
    studier_available = False
    try:
        from core.colony_studier import get_studier
        studier = get_studier()
        studier_available = True
    except Exception:
        studier = None

    url = _extract_url(directive)
    if studier_available:
        try:
            if url:
                studier.study_url(url, depth=2, visible=visible)
                summary = f"navegué {'(visible)' if visible else '(headless)'} {url} (prof. 2) y aprendí a mi grafo."
            else:
                r = studier.study_topic(directive, max_pages=5) or {}
                pages = len(r.get("pages_read", []))
                nodes = r.get("nodes_added", 0)
                summary = f"estudié en la web «{directive}»: {pages} páginas, +{nodes} nodos al grafo."
            return {"learned": True, "summary": summary, "source": "web:navegador"}
        except Exception as e:
            log.debug("colony_studier fallo: %s — probando eidos_web_actor", e)

    # ── Fallback: eidos_web_actor (S127) ───────────────────────────────────
    try:
        from core.eidos_web_actor import act_on_page
        # Intentar obtener una pagina de Playwright (headless por defecto)
        page = None
        target_url = url or f"https://duckduckgo.com/?q={__import__('urllib.parse').quote(directive)}"

        try:
            from core.eidos_browser import PLAYWRIGHT_AVAILABLE
            if PLAYWRIGHT_AVAILABLE:
                from playwright.sync_api import sync_playwright
                pw = sync_playwright().start()
                browser = pw.chromium.launch(headless=True)
                page = browser.new_page()
                page.goto(target_url, timeout=15000)
        except Exception:
            pass

        if page is not None:
            actor_result = act_on_page(page, directive, visible=False, max_steps=5)
            # Cerrar browser
            try:
                page.context.browser.close()
            except Exception:
                pass

            if actor_result.get("ok"):
                execution = actor_result.get("execution", {})
                plan = actor_result.get("plan", {})
                learned = actor_result.get("learned", [])
                summary = (f"WebActor: {plan.get('goal', '?')} via {plan.get('method', '?')} — "
                          f"{len(execution.get('steps_done', []))}/{len(execution.get('steps_done', [])) + len(execution.get('steps_failed', []))} steps, "
                          f"+{len(learned)} skills")
                return {"learned": True, "summary": summary,
                        "source": "web:actor",
                        "actor_result": {"plan": plan, "learned": learned}}
            elif actor_result.get("needs_ser"):
                return {"learned": False,
                        "summary": f"WebActor: necesita a SER — {actor_result.get('plan', {}).get('reasoning', '')[:200]}",
                        "source": "web:actor:needs_ser"}
            else:
                return {"learned": False,
                        "summary": f"WebActor: no pudo completar — "
                                   f"{actor_result.get('execution', {}).get('final_state', '?')}",
                        "source": "web:actor"}
        else:
            return {"learned": False, "summary": "no pude abrir navegador (Playwright no disponible)",
                    "source": "web"}
    except Exception as e:
        return {"learned": False, "summary": f"fallo navegando (studier + actor): {e}", "source": "web"}


def _study_one(directive: str, dry_run: bool) -> Dict[str, Any]:
    """Estudia una directiva: REGISTRO > WEB (navegador) > operación (BOM) > concepto (grafo/IAs)."""
    # -1. ¿REGISTRARSE? ("regístrate en labex.io") → EIDOS se da de alta SOLO con su correo.
    #     Va ANTES del web porque "regístrate en labex.io" también casa con web.
    if _is_register(directive):
        r = _do_register(directive)
        return {"kind": "registro", "learned": r["learned"], "summary": r["summary"], "source": r["source"]}
    # 0. ¿estudio WEB? (URL o "en la web/labex/internet/docs") → EIDOS abre el navegador SOLO
    if _is_web(directive):
        w = _study_web(directive)
        return {"kind": "web", "learned": w["learned"], "summary": w["summary"], "source": w["source"]}
    from core.eidos_study import study
    res = study(directive, dry_run=dry_run)
    kind = res.get("kind", "concepto")
    learned = bool(res.get("learned"))
    summary = res.get("summary", "")
    source = "study"
    # SER lo pidió claro: cuando él no está, EIDOS CONSULTA A SUS IAS. Para todo
    # CONCEPTO pregunto a mis IAs (Groq/DeepSeek) — dan mejor respuesta que el ruido de
    # whatis. La neurona que enseñó el MAESTRO (ser_taught 0.95) sigue intacta igual. (S125)
    if kind == "concepto":
        ia = _ask_own_ias(directive)
        if ia["learned"]:
            learned = True
            summary = f"consulté mis IAs: {ia['definition'][:200]}"
            source = ia["source"]
    return {"kind": kind, "learned": learned, "summary": summary, "source": source}


def run_pending(max_items: int = 20, dry_run: bool = True) -> Dict[str, Any]:
    """Trabaja la cola por prioridad. Operaciones en seco salvo dry_run=False (SER presente)."""
    items = list_items("pending")[:max_items]
    done, failed = 0, 0
    for it in items:
        log.info("📚 estudiando: %s", it["directive"][:60])
        try:
            r = _study_one(it["directive"], dry_run)
            c = _conn()
            c.execute("UPDATE study_queue SET status=?, kind=?, learned=?, summary=?, "
                      "source=?, done_at=? WHERE id=?",
                      ("done" if r["learned"] else "failed", r["kind"],
                       1 if r["learned"] else 0, r["summary"][:500], r["source"],
                       time.time(), it["id"]))
            c.commit()
            done += 1 if r["learned"] else 0
            failed += 0 if r["learned"] else 1
        except Exception as e:
            log.warning("fallo estudiando %s: %s", it["id"], e)
            failed += 1
    report_path = write_report()
    return {"processed": len(items), "learned": done, "failed": failed,
            "report": str(report_path)}


def write_report(path: Path = _REPORT) -> Path:
    """Informe EXTENSO en markdown de todo lo de la cola, para que SER lo lea al volver."""
    items = list_items()
    pend = [i for i in items if i["status"] == "pending"]
    done = [i for i in items if i["status"] == "done"]
    fail = [i for i in items if i["status"] == "failed"]
    lines = [
        "# Informe de estudio autónomo de EIDOS",
        f"_Generado {time.strftime('%Y-%m-%d %H:%M')} — mientras SER no estaba._",
        "",
        f"**Resumen:** {len(done)} aprendidas · {len(fail)} sin resolver · {len(pend)} en cola.",
        "",
    ]
    if done:
        lines.append("## ✅ Lo que aprendí")
        for i in done:
            lines += [f"### «{i['directive']}»  _(via {i['source'] or '?'}, {i['kind']})_",
                      f"{i['summary'] or '(sin detalle)'}", ""]
    if fail:
        lines.append("## 🔸 No pude resolver solo (necesito que me enseñes, SER)")
        for i in fail:
            lines += [f"- **«{i['directive']}»** — {i['summary'] or 'no encontré nada claro'}", ]
        lines.append("")
    if pend:
        lines.append("## ⏳ Todavía en cola")
        for i in pend:
            lines.append(f"- «{i['directive']}» (prioridad {i['priority']})")
        lines.append("")
    lines.append("---\n_Para enseñarme algo de la lista de arriba: `eidos teach` o háblame directamente._")
    try:
        _REPORT.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("\n".join(lines), encoding="utf-8")
    except Exception as e:
        log.debug("write_report: %s", e)
    return path


if __name__ == "__main__":
    import sys, json
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    args = sys.argv[1:]
    cmd = args[0] if args else "list"
    if cmd == "add" and len(args) >= 2:
        prio = 5
        if "--priority" in args:
            try: prio = int(args[args.index("--priority") + 1])
            except Exception: pass
        directive = " ".join(a for a in args[1:] if not a.startswith("--") and not a.isdigit())
        print("añadido:", enqueue(directive, prio))
    elif cmd == "list":
        print(json.dumps(list_items(), ensure_ascii=False, indent=2))
    elif cmd == "run":
        mx = 20
        if "--max" in args:
            try: mx = int(args[args.index("--max") + 1])
            except Exception: pass
        dry = "--real" not in args
        print(json.dumps(run_pending(max_items=mx, dry_run=dry), ensure_ascii=False, indent=2))
    elif cmd == "report":
        print("informe en:", write_report())
    else:
        print('uso: add "..." [--priority N] | list | run [--real] [--max N] | report')
