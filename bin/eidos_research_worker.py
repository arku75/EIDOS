#!/usr/bin/env python3
"""
bin/eidos_research_worker.py — Worker de investigación en SUBPROCESO [S122]
==========================================================================
Aísla la memoria: la investigación profunda (Playwright visible + crawl + LLM)
corría DENTRO del bridge y lo hinchaba a ~3G. Aquí corre como proceso aparte
que MUERE al terminar → libera toda esa memoria. El bridge solo lo invoca y
lee el JSON de la última línea (prefijo ___RESULT___).

Uso: python3 eidos_research_worker.py "<tema>" [--visible]
"""
import sys
import os
import json

sys.path.insert(0, os.path.expanduser("~/EIDOS"))


def main() -> int:
    if len(sys.argv) < 2:
        print("___RESULT___" + json.dumps({"error": "sin tema"}))
        return 1
    topic = sys.argv[1]
    visible = "--visible" in sys.argv
    try:
        from core.eidos_deep_research import investigate_deep
        r = investigate_deep(topic, visible=visible)
    except Exception as e:  # noqa: BLE001
        r = {"error": str(e), "topic": topic, "summary": "", "pages_read": 0}
    # Última línea = resultado JSON (el bridge la parsea).
    print("___RESULT___" + json.dumps(r, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
