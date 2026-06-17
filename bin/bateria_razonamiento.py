#!/usr/bin/env python3
"""
bateria_razonamiento.py — Batería de razonamiento profundo [S124]
Pone a prueba el pipeline ULTRAPLAN (grafo + multi-salto depth2 + compositor
+ research activo) con preguntas duras, por niveles:
  A grafo directo · B multi-salto/relación · C causal · D comparativa
  E research activo (conceptos nuevos) · F hipotético encadenado · G identidad
Uso: python3 bin/bateria_razonamiento.py
No modifica nada: solo pregunta por POST /talk y evalúa heurísticas.
"""
import urllib.request, json, time, sys, re

BASE = "http://localhost:8003"

def talk(msg, timeout=90):
    data = json.dumps({"message": msg}).encode()
    req = urllib.request.Request(BASE + "/talk", data=data,
                                 headers={"Content-Type": "application/json"})
    t0 = time.time()
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return round(time.time() - t0, 2), json.loads(r.read()).get("text", "")

# (nivel, pregunta, keywords_esperadas [≥1 debe aparecer], len_min)
BATERIA = [
    ("A-grafo",      "que es docker",
     ["contenedor", "imagen", "plataforma"], 80),
    ("A-grafo",      "que es un kernel",
     ["núcleo", "nucleo", "sistema operativo", "hardware"], 80),
    ("B-multisalto", "que relacion hay entre docker y kubernetes",
     ["orquesta", "contenedor", "despliegue", "cluster", "clúster"], 80),
    ("B-multisalto", "que tiene que ver systemd con el arranque de linux",
     ["init", "arranque", "servicio", "boot", "pid 1"], 80),
    ("B-multisalto", "como se relaciona la memoria ram con el swap",
     ["disco", "memoria", "intercambio", "página", "pagina"], 80),
    ("C-causal",     "por que tcp necesita un handshake de tres vias",
     ["conexión", "conexion", "syn", "fiab", "establec", "acuse"], 80),
    ("C-causal",     "por que es importante la meiosis para la evolucion",
     ["genétic", "genetic", "variab", "gameto", "cromosoma", "diversidad"], 80),
    ("D-comparativa","que diferencia hay entre un proceso y un hilo",
     ["memoria", "comparte", "ejecución", "ejecucion", "ligero"], 80),
    ("D-comparativa","diferencia entre http y https",
     ["cifra", "tls", "ssl", "segur", "certific"], 80),
    ("E-research",   "que es la entropia de shannon",
     ["información", "informacion", "incertidumbre", "bits", "probabil"], 80),
    ("E-research",   "que es la fotosintesis c4",
     ["planta", "carbono", "co2", "luz", "fotosínt", "fotosint"], 80),
    ("E-research",   "que es el teorema de bayes",
     ["probabilidad", "condicional", "evidencia", "hipótesis", "hipotesis"], 80),
    ("F-hipotetico", "si un contenedor docker comparte el kernel del host que pasa si el kernel falla",
     ["host", "contenedor", "falla", "afecta", "caer", "kernel"], 60),
    ("F-hipotetico", "que pasaria si borro el archivo /etc/passwd en linux",
     ["usuario", "login", "sesión", "sesion", "autentic", "sistema"], 60),
    ("G-identidad",  "que modelo de llm eres",
     ["eidos", "no soy", "ser"], 20),
]

PASS, WEAK, FAIL = 0, 0, 0
resultados = []

print("=" * 64)
print("BATERÍA DE RAZONAMIENTO PROFUNDO — EIDOS")
print("=" * 64)

for nivel, q, kws, lmin in BATERIA:
    try:
        t, txt = talk(q)
        low = txt.lower()
        hits = [k for k in kws if k in low]
        largo = len(txt) >= lmin
        if hits and largo:
            mark, ver = "✅", "PASS";  PASS += 1
        elif hits or largo:
            mark, ver = "🟡", "WEAK";  WEAK += 1
        else:
            mark, ver = "❌", "FAIL";  FAIL += 1
        resultados.append((nivel, q, ver, t, len(txt), hits))
        print(f"{mark} [{nivel}] {q}")
        print(f"   {t}s · {len(txt)}c · keywords={hits if hits else 'NINGUNA'}")
        print(f"   → {txt[:150].strip()}")
    except Exception as e:
        FAIL += 1
        resultados.append((nivel, q, "FAIL", 0, 0, []))
        print(f"❌ [{nivel}] {q}\n   EXCEPCIÓN: {str(e)[:100]}")

print("=" * 64)
print(f"RESULTADO: {PASS} PASS / {WEAK} WEAK / {FAIL} FAIL  ({len(BATERIA)} preguntas)")
por_nivel = {}
for nivel, q, ver, t, l, h in resultados:
    por_nivel.setdefault(nivel.split("-")[0], []).append(ver)
for n in sorted(por_nivel):
    vs = por_nivel[n]
    print(f"  Nivel {n}: {vs.count('PASS')}P/{vs.count('WEAK')}W/{vs.count('FAIL')}F")
sys.exit(0 if FAIL == 0 else 1)
