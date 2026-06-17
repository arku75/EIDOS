"""
core/master_protocol.py — MODO MAESTRO: EIDOS pregunta a SER en tiempo real. S125.

La relación maestro→alumno que SER pidió: cuando EIDOS está aprendiendo CON SER
presente y se topa con algo que no sabe, NO investiga a ciegas — le PREGUNTA a SER,
ESPERA su respuesta, y la guarda como conocimiento de máxima confianza (el maestro
es la verdad). Si SER no contesta en `timeout` segundos (no está / ocupado), el
llamador cae al modo autónomo (sus IAs). Así un solo flujo cubre los dos modos:
    SER presente  → te pregunta a TI (confianza 0.95)
    SER ausente   → cae solo a research/IAs (confianza menor)

Canal de aviso: ser_inbox (eidos inbox). SER responde:  eidos teach <id> "respuesta"
NO toca ratón/teclado. Solo DB + inbox. Reusa core.db, ser_inbox. No reinventa.
"""
from __future__ import annotations

import logging
import os
import shutil
import subprocess
import time
import uuid
from pathlib import Path
from typing import Any, Dict, List, Optional

log = logging.getLogger("eidos.master")

BRAIN_DB = Path.home() / ".eidos" / "evolution_brain.db"


def _conn():
    from core.db import get_conn
    c = get_conn(BRAIN_DB, timeout=20)
    c.execute("""CREATE TABLE IF NOT EXISTS ser_teach(
        id TEXT PRIMARY KEY, ts REAL, question TEXT, context TEXT,
        answer TEXT, answered INTEGER DEFAULT 0, answered_at REAL)""")
    return c


# ── ¿Está SER delante? (heurística de presencia, no decide sola) ──────────────
def ser_present() -> Optional[bool]:
    """¿SER está al teclado? Usa el tiempo de inactividad de X (xprintidle): <5min = presente.
    Devuelve None si no se puede saber (entonces manda EIDOS_MASTER_MODE)."""
    if shutil.which("xprintidle"):
        try:
            env = {**os.environ, "DISPLAY": os.environ.get("DISPLAY", ":0")}
            idle_ms = int(subprocess.run(["xprintidle"], capture_output=True,
                                         text=True, timeout=3, env=env).stdout.strip())
            return idle_ms < 300_000  # <5 min sin tocar nada = presente
        except Exception:
            return None
    return None


def should_ask_ser() -> bool:
    """¿Debo preguntar a SER en vez de a mis IAs? Sí si MASTER_MODE=1 y (presencia
    desconocida o confirmada). Si xprintidle dice que SER lleva >5min fuera, NO molesto."""
    if os.environ.get("EIDOS_MASTER_MODE") != "1":
        return False
    present = ser_present()
    return present is None or present is True


# ── PREGUNTAR a SER y ESPERAR (el corazón del modo maestro) ───────────────────
def ask_ser(question: str, context: str = "", timeout: float = 60.0,
            poll: float = 2.0) -> Optional[str]:
    """EIDOS le pregunta a SER y espera su respuesta hasta `timeout` segundos.
    Devuelve la respuesta (str) o None si SER no contestó a tiempo (→ cae a IAs)."""
    question = (question or "").strip()
    if not question:
        return None
    qid = "ask_" + uuid.uuid4().hex[:10]
    try:
        c = _conn()
        c.execute("INSERT INTO ser_teach(id,ts,question,context,answered) VALUES(?,?,?,?,0)",
                  (qid, time.time(), question[:500], context[:500]))
        c.commit()
    except Exception as e:
        log.debug("ask_ser insert: %s", e)
        return None
    # avisar a SER por su buzón
    try:
        from core.ser_inbox import get_ser_inbox
        get_ser_inbox().add("proposal_needs_approval",
                            f"EIDOS pregunta: {question[:80]}",
                            f"Responde con:  eidos teach {qid} \"tu respuesta\"  "
                            f"(o ignóralo y aprenderé solo). Contexto: {context[:120]}",
                            action_needed=True)
    except Exception as e:
        log.debug("ask_ser inbox: %s", e)
    log.info("🙋 pregunté a SER (%s): %s", qid, question[:60])
    # esperar la respuesta de SER
    deadline = time.time() + timeout
    while time.time() < deadline:
        ans = _get_answer(qid)
        if ans is not None:
            # el aprendizaje lo hace answer() (acto del maestro), aquí solo recojo
            log.info("🎓 SER me enseñó: %s", ans[:60])
            return ans
        time.sleep(poll)
    log.info("⏳ SER no contestó en %.0fs → aprenderé por mi cuenta", timeout)
    return None


def _get_answer(qid: str) -> Optional[str]:
    try:
        c = _conn()
        r = c.execute("SELECT answer FROM ser_teach WHERE id=? AND answered=1",
                      (qid,)).fetchone()
        return r[0] if r and r[0] else None
    except Exception:
        return None


def _learn_from_ser(question: str, answer: str) -> None:
    """Lo que enseña el MAESTRO entra con confianza máxima (0.95): es la verdad para EIDOS."""
    try:
        c = _conn()
        concept = f"SER me enseñó: {question[:80]}"
        nid = "taught_" + uuid.uuid4().hex[:12]
        c.execute(
            "INSERT INTO knowledge_nodes "
            "(id, concept, definition, category, confidence, source, created_at) "
            "VALUES (?,?,?,?,?,?,?)",
            (nid, concept, answer[:1000], "taught", 0.95, "ser_taught",
             time.strftime("%Y-%m-%d %H:%M:%S")))
        c.commit()
        log.info("🧠 neurona del maestro guardada (conf 0.95): %s", concept[:50])
    except Exception as e:
        log.debug("_learn_from_ser: %s", e)


# ── lo que usa SER desde el CLI ───────────────────────────────────────────────
def pending() -> List[Dict[str, Any]]:
    """Preguntas de EIDOS que esperan respuesta de SER."""
    try:
        c = _conn()
        rows = c.execute("SELECT id, ts, question, context FROM ser_teach "
                         "WHERE answered=0 ORDER BY ts DESC").fetchall()
        return [{"id": r[0], "ts": r[1], "question": r[2], "context": r[3]} for r in rows]
    except Exception:
        return []


def answer(qid: str, text: str) -> bool:
    """SER responde a una pregunta de EIDOS (eidos teach <id> "..."). Guarda + enseña."""
    text = (text or "").strip()
    if not text:
        return False
    try:
        c = _conn()
        row = c.execute("SELECT question FROM ser_teach WHERE id=?", (qid,)).fetchone()
        if not row:
            return False
        c.execute("UPDATE ser_teach SET answer=?, answered=1, answered_at=? WHERE id=?",
                  (text[:1000], time.time(), qid))
        c.commit()
        # el maestro enseñó → guardar SIEMPRE como neurona de máxima confianza,
        # conteste EIDOS estuviera o no esperando todavía (respuesta asíncrona).
        _learn_from_ser(row[0], text)
        return True
    except Exception as e:
        log.debug("answer: %s", e)
        return False


if __name__ == "__main__":
    import sys, json
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    cmd = sys.argv[1] if len(sys.argv) > 1 else "pending"
    if cmd == "pending":
        print(json.dumps(pending(), ensure_ascii=False, indent=2))
    elif cmd == "answer" and len(sys.argv) >= 4:
        ok = answer(sys.argv[2], " ".join(sys.argv[3:]))
        print("✅ enseñado" if ok else "❌ no encontré esa pregunta")
    elif cmd == "ask" and len(sys.argv) >= 3:
        # prueba: pregunta y espera poco (no bloquear al probar)
        a = ask_ser(" ".join(sys.argv[2:]), timeout=float(os.environ.get("ASK_TIMEOUT", "6")))
        print("respuesta:", a if a else "(SER no contestó → caería a IAs)")
    else:
        print("uso: python3 -m core.master_protocol [pending | answer <id> \"texto\" | ask \"pregunta\"]")
