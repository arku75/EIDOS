"""
core/eidos_builder.py — EIDOS construye y PERFECCIONA código solo [S122 Fase 4]
==============================================================================
SER quiere que EIDOS, en modo libre/constructor, CREE algo y lo PERFECCIONE en
sandbox sin romper el PC: genera código → lo ejecuta → si falla, lee el error y
lo corrige → repite hasta que funcione. Para cualquier cosa que SER pida.

Usa la cascada LLM (eidos_learn.ask_llm) para generar/corregir y eidos_fileops
para escribir/ejecutar en el workspace (con cortafuegos anti-destrucción).
"""
from __future__ import annotations

import re
import time
import logging
from pathlib import Path
from typing import Dict

log = logging.getLogger("eidos.builder")


def _slug(text: str, n: int = 24) -> str:
    s = re.sub(r"[^a-z0-9]+", "_", (text or "build").lower()).strip("_")
    return (s or "build")[:n]


def _strip_code_fences(text: str) -> str:
    """Quita ```python ... ``` y deja solo el código."""
    if not text:
        return ""
    m = re.search(r"```(?:python|py|bash|sh)?\s*\n(.*?)```", text, re.DOTALL)
    if m:
        return m.group(1).strip()
    return text.strip()


def _gen_code(goal: str, lang: str) -> str:
    from core.eidos_learn import ask_llm
    prompt = (
        f"Escribe un programa COMPLETO en {lang} que cumpla este objetivo:\n«{goal}»\n\n"
        f"Requisitos: código autocontenido y ejecutable, sin dependencias raras "
        f"(usa solo stdlib si puedes). Devuelve SOLO el código, sin explicaciones, "
        f"dentro de un bloque ```{lang}."
    )
    ans, _ = ask_llm(prompt, timeout=60)
    return _strip_code_fences(ans)


def _fix_code(goal: str, code: str, error: str, lang: str) -> str:
    from core.eidos_learn import ask_llm
    prompt = (
        f"Este programa en {lang} (objetivo: «{goal}») da un ERROR al ejecutarse.\n\n"
        f"=== CÓDIGO ===\n{code[:3000]}\n\n=== ERROR ===\n{error[:1500]}\n\n"
        f"Corrige el código para que funcione. Devuelve SOLO el código corregido "
        f"completo, dentro de un bloque ```{lang}, sin explicaciones."
    )
    ans, _ = ask_llm(prompt, timeout=60)
    fixed = _strip_code_fences(ans)
    return fixed or code


def build(goal: str, lang: str = "python", max_iters: int = 3,
          timeout: int = 30) -> Dict:
    """Construye y perfecciona en sandbox: genera → ejecuta → corrige → repite.

    Returns: {ok, goal, file, code, output, error, iters, history}
    """
    from core.eidos_fileops import write_file, run_script, WORKSPACE
    t0 = time.time()
    goal = (goal or "").strip()
    ext = "py" if lang == "python" else "sh"
    fname = f"build_{_slug(goal)}_{int(time.time())}.{ext}"
    history = []

    code = _gen_code(goal, lang)
    if not code:
        return {"ok": False, "goal": goal, "error": "el LLM no generó código",
                "iters": 0, "history": history}

    last = {}
    for i in range(max_iters):
        wr = write_file(fname, code)
        if not wr.get("ok"):
            return {"ok": False, "goal": goal, "error": wr.get("error"),
                    "iters": i, "history": history}
        res = run_script(fname, lang=lang, timeout=timeout)
        history.append({"iter": i + 1, "ok": res.get("ok"),
                        "err": (res.get("stderr") or res.get("error") or "")[:200]})
        last = res
        if res.get("ok"):
            log.info("builder: «%s» OK en %d intento(s)", goal[:40], i + 1)
            return {"ok": True, "goal": goal, "file": str(WORKSPACE / fname),
                    "code": code, "output": res.get("stdout", ""),
                    "iters": i + 1, "history": history,
                    "elapsed_s": round(time.time() - t0, 1)}
        # corregir y reintentar
        err = res.get("stderr") or res.get("error") or "fallo desconocido"
        log.info("builder: intento %d falló (%s) — corrigiendo", i + 1, err[:60])
        code = _fix_code(goal, code, err, lang)

    return {"ok": False, "goal": goal, "file": str(WORKSPACE / fname),
            "code": code, "error": last.get("stderr") or last.get("error"),
            "iters": max_iters, "history": history,
            "elapsed_s": round(time.time() - t0, 1)}


def build_and_test(topic: str, max_retries: int = 3) -> Dict:
    """Builder mode (Fase 4): encadena Maker → Builder → Sandbox → persistir al grafo.

    1. Maker: crea scaffold del proyecto (archivos iniciales)
    2. Builder: genera el código con LLM
    3. Sandbox: ejecuta el código
    4. Si falla: lee el error, corrige, reintenta (máx max_retries veces)
    5. Si pasa: persiste al grafo como builder:success:topic

    Args:
        topic: descripción de lo que hay que construir (ej: "script que ordena archivos")
        max_retries: máximo de reintentos totales (default 3)

    Returns:
        {ok, topic, project_dir, code, output, iters, history, grafo_persisted, elapsed_s}
    """
    t0 = time.time()
    result = {
        "ok": False,
        "topic": topic,
        "project_dir": "",
        "code": "",
        "output": "",
        "iters": 0,
        "history": [],
        "grafo_persisted": False,
        "elapsed_s": 0,
        "error": "",
    }

    # ── Fase 1: Maker — crear scaffold ──────────────────────────────────────
    try:
        from core.eidos_maker import get_maker
        maker = get_maker()
        maker_result = maker.create_project(topic, project_type="script")
        result["project_dir"] = maker_result.get("project_dir", "")
        log.info("build_and_test: scaffold creado → %s", maker_result.get("project_name", ""))
    except Exception as e:
        log.warning("build_and_test: Maker falló (%s), usando scaffold filesystem", e)
        try:
            from core.eidos_filesystem import create_project_scaffold
            fs_result = create_project_scaffold(_slug(topic), project_type="python")
            result["project_dir"] = fs_result.get("project_dir", "")
        except Exception as e2:
            result["error"] = f"scaffold falló: {e} / {e2}"
            result["elapsed_s"] = round(time.time() - t0, 1)
            return result

    # ── Fase 2+3+4: Builder → Sandbox → reintentos ──────────────────────────
    build_result = build(topic, lang="python", max_iters=max_retries, timeout=30)
    result["code"] = build_result.get("code", "")
    result["iters"] = build_result.get("iters", 0)
    result["history"] = build_result.get("history", [])

    if build_result.get("ok"):
        result["ok"] = True
        result["output"] = build_result.get("output", "")
    else:
        result["error"] = build_result.get("error", "build falló tras reintentos")
        result["elapsed_s"] = round(time.time() - t0, 1)
        # Aunque falle, intentamos persistir el intento al grafo (aprendizaje del fallo)
        _persist_to_grafo(topic, result)
        return result

    # ── Fase 5: persistir al grafo ──────────────────────────────────────────
    result["grafo_persisted"] = _persist_to_grafo(topic, result)
    result["elapsed_s"] = round(time.time() - t0, 1)
    log.info("build_and_test: «%s» OK en %d iters (%.1fs) grafo=%s",
             topic[:40], result["iters"], result["elapsed_s"], result["grafo_persisted"])
    return result


def _persist_to_grafo(topic: str, result: Dict) -> bool:
    """Persiste el resultado de build_and_test como nodo en el grafo neuronal."""
    try:
        from core.db import get_conn
        import uuid
        c = get_conn(Path.home() / ".eidos" / "evolution_brain.db", timeout=10)
        concept = f"builder:success:{topic}" if result.get("ok") else f"builder:attempt:{topic}"
        definition = (
            f"build_and_test {'OK' if result.get('ok') else 'FAIL'}: "
            f"iters={result.get('iters', 0)}, "
            f"output={str(result.get('output', ''))[:100]}, "
            f"error={str(result.get('error', ''))[:100]}"
        )
        confidence = 0.85 if result.get("ok") else 0.3

        # Upsert: reforzar si ya existe
        row = c.execute(
            "SELECT id, confidence FROM knowledge_nodes WHERE concept=?",
            (concept,)
        ).fetchone()
        if row:
            new_conf = min(0.99, max(confidence, float(row[1] or 0.5)) + 0.05)
            c.execute("UPDATE knowledge_nodes SET confidence=?, definition=? WHERE id=?",
                      (new_conf, definition, row[0]))
        else:
            nid = "builder_" + uuid.uuid4().hex[:12]
            c.execute(
                "INSERT INTO knowledge_nodes "
                "(id, concept, definition, category, confidence, source, created_at) "
                "VALUES (?,?,?,?,?,?,?)",
                (nid, concept, definition, "builder",
                 confidence, "builder_mode_fase4",
                 time.strftime("%Y-%m-%d %H:%M:%S")))
        c.commit()
        log.info("grafo: persistido '%s' (conf=%.2f)", concept[:60], confidence)
        return True
    except Exception as e:
        log.debug("_persist_to_grafo: %s", e)
        return False


if __name__ == "__main__":
    import sys, os
    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    if len(sys.argv) > 1 and sys.argv[1] == "build_and_test":
        topic = sys.argv[2] if len(sys.argv) > 2 else "script que calcula los primeros 10 primos"
        r = build_and_test(topic)
        import json
        print(json.dumps(r, indent=2, ensure_ascii=False))
    else:
        goal = sys.argv[1] if len(sys.argv) > 1 else "calcula y muestra los primeros 10 números primos"
        r = build(goal)
        print(f"\n=== build «{goal}» ===")
        print(f"ok={r['ok']} iters={r['iters']} file={r.get('file','')}")
        print("salida:\n", r.get("output", r.get("error", ""))[:500])
