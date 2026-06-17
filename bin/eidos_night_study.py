#!/usr/bin/env python3
"""
bin/eidos_night_study.py — Estudio AUTÓNOMO continuo de EIDOS [S122-I]
=====================================================================
SER: "dale libros/pdfs/web para que estudie TODO sobre Kali Linux, todas mis
apps (de Kali y no-Kali), que sepa usar o investigar lo que sea. Que NO PARE."

Worker persistente: EIDOS estudia sin parar y persiste al grafo:
  1. Self-curriculum (kali_linux_core, shell, security, etc.)
  2. CADA herramienta/app instalada en el sistema (qué es, para qué sirve, uso)
  3. PDFs/libros en ~/Descargas, ~/Documentos (comprensión profunda)
Corre en bucle hasta que lo paren. Loggea progreso en ~/.eidos/logs/night_study.log
"""
import sys
import os
import time
import json
import subprocess
import logging
from pathlib import Path

sys.path.insert(0, os.path.expanduser("~/EIDOS"))

LOG = Path.home() / ".eidos" / "logs" / "night_study.log"
LOG.parent.mkdir(parents=True, exist_ok=True)
PROGRESS = Path.home() / ".eidos" / "night_study_progress.json"
logging.basicConfig(
    level=logging.INFO, format="%(asctime)s %(message)s",
    handlers=[logging.FileHandler(LOG), logging.StreamHandler()])
log = logging.getLogger("night_study")


def _already_known(concept: str) -> bool:
    """¿EIDOS ya tiene este concepto con definición decente en el grafo?"""
    try:
        from core.db import get_conn
        c = get_conn(Path.home() / ".eidos" / "evolution_brain.db", timeout=20)
        row = c.execute(
            "SELECT LENGTH(COALESCE(definition,'')) FROM knowledge_nodes "
            "WHERE concept = ? OR concept = ?",
            (concept, concept.lower())).fetchone()
        return bool(row and row[0] and row[0] > 120)
    except Exception:
        return False


def _list_apps() -> list:
    """Apps/herramientas instaladas: binarios en PATH + .desktop apps."""
    tools = set()
    # Binarios en /usr/bin (herramientas Kali y generales)
    try:
        for d in ("/usr/bin", "/usr/sbin", "/usr/local/bin"):
            if os.path.isdir(d):
                for f in os.listdir(d):
                    if (os.path.isfile(os.path.join(d, f)) and len(f) > 2
                            and not f[0].isdigit() and "-" not in f[:2]):
                        tools.add(f)
    except Exception:
        pass
    return sorted(tools)


def study_tool(name: str) -> bool:
    """EIDOS investiga una herramienta/app (qué es, para qué sirve)."""
    if _already_known(name):
        return False
    try:
        from core.eidos_active_research import research_now
        r = research_now(name, timeout=15.0, persist=True, prefer_remote=False)
        if r.get("learned"):
            log.info("📚 aprendió: %s (%s)", name, r.get("channel", "?"))
            return True
    except Exception as e:
        log.debug("study_tool %s: %s", name, e)
    return False


def study_curriculum():
    """Self-curriculum de Kali/seguridad/etc."""
    try:
        from core.eidos_self_curriculum import study_all
        log.info("🎓 Curriculum: iniciando estudio completo...")
        result = study_all(max_per_topic=0)
        log.info("🎓 Curriculum completado: %s conceptos",
                 result.get("total_learned", "?") if isinstance(result, dict) else "?")
    except Exception as e:
        log.error("curriculum falló: %s", e)


def study_docs():
    """Comprensión profunda de PDFs/libros en Descargas/Documentos."""
    try:
        from core.eidos_deep_comprehension import comprehend
        import glob
        seen = set()
        prog = json.loads(PROGRESS.read_text()) if PROGRESS.exists() else {}
        done_docs = set(prog.get("docs_done", []))
        for d in ("~/Descargas", "~/Documentos", "~/Downloads", "~/Documents"):
            dd = os.path.expanduser(d)
            if not os.path.isdir(dd):
                continue
            for f in glob.glob(os.path.join(dd, "**", "*"), recursive=True):
                if (os.path.splitext(f)[1].lower() in (".pdf", ".epub", ".txt", ".md")
                        and f not in done_docs and os.path.getsize(f) < 30_000_000):
                    log.info("📖 comprendiendo doc: %s", os.path.basename(f))
                    try:
                        r = comprehend(f, extract_concepts_to_graph=True, self_evaluate=False)
                        if r.get("ok"):
                            log.info("   → %d conceptos de %s",
                                     r.get("concepts", {}).get("total_persisted", 0),
                                     os.path.basename(f))
                        done_docs.add(f)
                        prog["docs_done"] = list(done_docs)
                        PROGRESS.write_text(json.dumps(prog))
                    except Exception as e:
                        log.debug("doc %s: %s", f, e)
                    time.sleep(2)
    except Exception as e:
        log.error("study_docs falló: %s", e)


def main():
    log.info("=" * 60)
    log.info("🌙 EIDOS ESTUDIO NOCTURNO iniciado — no para hasta que lo paren")
    log.info("=" * 60)

    # 1. Curriculum primero (Kali core, security, etc.)
    study_curriculum()

    # 2. Docs/PDFs
    study_docs()

    # 3. Bucle continuo: estudiar apps/herramientas del sistema
    apps = _list_apps()
    log.info("🔧 %d herramientas/apps detectadas para estudiar", len(apps))
    prog = json.loads(PROGRESS.read_text()) if PROGRESS.exists() else {}
    studied = set(prog.get("tools_done", []))
    learned_count = prog.get("learned_count", 0)

    idx = 0
    for name in apps:
        if name in studied:
            continue
        if study_tool(name):
            learned_count += 1
        studied.add(name)
        idx += 1
        if idx % 10 == 0:
            prog["tools_done"] = list(studied)
            prog["learned_count"] = learned_count
            prog["last"] = name
            PROGRESS.write_text(json.dumps(prog))
            log.info("progreso: %d apps procesadas, %d aprendidas", idx, learned_count)
        time.sleep(3)  # no saturar APIs/CPU

    log.info("✅ Estudio de apps completado: %d aprendidas de %d", learned_count, len(apps))
    # Repetir curriculum + docs periódicamente (sigue aprendiendo lo que falle)
    while True:
        time.sleep(1800)  # cada 30 min
        log.info("🔁 re-pasada: docs nuevos + curriculum")
        study_docs()


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        log.info("estudio detenido")
