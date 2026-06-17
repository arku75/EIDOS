import sys
import os

EIDOS_DIR = os.path.expanduser("~/EIDOS")
if EIDOS_DIR not in sys.path:
    sys.path.insert(0, EIDOS_DIR)

from core.intent_classifier import IntentClassifier

c = IntentClassifier()

tests = [
    "hola quien eres",
    "ls -la /tmp",
    "haz un analisis profundo de este proyecto y redacta un plan",
    "mira la pantalla y dime que ventana esta abierta"
]

for t in tests:
    res = c.classify(t)
    print(f"[{res}] <- {t}")
