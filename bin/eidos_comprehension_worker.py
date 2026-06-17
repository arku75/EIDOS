#!/usr/bin/env python3
"""
bin/eidos_comprehension_worker.py — Worker de comprensión en SUBPROCESO [S122-I]
================================================================================
La comprensión profunda (transcripción whisper + ~20 llamadas LLM para resumir
secciones, extraer conceptos y autoevaluarse) es CARA y LENTA. Si corre dentro
del bridge lo bloquea y lo hincha a 3G (misma lección que el deep research).

Aquí corre como proceso aparte que MUERE al terminar → libera toda la memoria.
El bridge lo invoca y lee el JSON de la última línea (prefijo ___RESULT___).
Los conceptos se persisten al grafo aunque el bridge deje de esperar.

Uso: python3 eidos_comprehension_worker.py "<ruta|url|tema>" [--no-graph] [--no-test]
"""
import sys
import os
import json

sys.path.insert(0, os.path.expanduser("~/EIDOS"))

# [S122-I] Límite de memoria del worker: si la comprensión de un archivo enorme
# (whisper + LLM + texto) se descontrola, MUERE este worker en vez de disparar
# un OOM del sistema (que el kernel resolvería matando eidos-vivo u otro servicio).
try:
    import resource
    _MEM_CAP = int(os.environ.get("EIDOS_COMPREHENSION_MEM_MB", "2560")) * 1024 * 1024
    _soft, _hard = resource.getrlimit(resource.RLIMIT_AS)
    _new_hard = _hard if _hard != resource.RLIM_INFINITY else _MEM_CAP
    resource.setrlimit(resource.RLIMIT_AS, (_MEM_CAP, _new_hard))
except Exception:
    pass  # si no se puede limitar, seguir igual


def main() -> int:
    if len(sys.argv) < 2:
        print("___RESULT___" + json.dumps({"ok": False, "error": "sin fuente"}))
        return 1
    source = sys.argv[1]
    extract = "--no-graph" not in sys.argv
    evaluate = "--no-test" not in sys.argv
    try:
        from core.eidos_deep_comprehension import comprehend
        r = comprehend(source, extract_concepts_to_graph=extract,
                       self_evaluate=evaluate)
    except Exception as e:  # noqa: BLE001
        r = {"ok": False, "error": str(e), "source": source}
    # Última línea = resultado JSON (el bridge la parsea).
    print("___RESULT___" + json.dumps(r, ensure_ascii=False, default=str))
    return 0


if __name__ == "__main__":
    sys.exit(main())
