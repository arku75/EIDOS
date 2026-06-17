#!/usr/bin/env python3
"""
smoke_colony.py — Smoke-test E2E de EIDOS [S72]
Verifica que cada capacidad responde OK. Detecta qué pulir al 100%.
Uso: python3 bin/smoke_colony.py
"""
import urllib.request, json, time, sys

BASE = "http://localhost:8003"

def _post(path, body, timeout=25):
    data = json.dumps(body).encode()
    req = urllib.request.Request(BASE + path, data=data,
                                 headers={"Content-Type": "application/json"})
    t0 = time.time()
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return round(time.time() - t0, 2), json.loads(r.read())

def _get(path, timeout=10):
    with urllib.request.urlopen(BASE + path, timeout=timeout) as r:
        return json.loads(r.read())

PASS, FAIL = 0, 0
def check(name, cond, detail=""):
    global PASS, FAIL
    mark = "✅" if cond else "❌"
    if cond: PASS += 1
    else: FAIL += 1
    print(f"  {mark} {name}: {detail[:120]}")

print("="*60)
print("SMOKE-TEST EIDOS — capacidades")
print("="*60)

# 1. Health
try:
    h = _get("/health")
    check("health", h.get("status") == "ok", str(h))
except Exception as e:
    check("health", False, str(e))

# 2. Grafo ready
try:
    g = _get("/graph/stats")
    check("grafo ready", g.get("ready") and g.get("graph_nodes", 0) > 1000,
          f"nodos={g.get('graph_nodes')} aristas={g.get('graph_edges')}")
except Exception as e:
    check("grafo", False, str(e))

# 3. Identidad (eidos_natural)
try:
    t, d = _post("/talk", {"message": "que eres"})
    txt = d.get("text", "")
    check("identidad", "EIDOS" in txt and t < 5, f"{t}s {txt[:60]}")
except Exception as e:
    check("identidad", False, str(e))

# 4. Conocimiento del grafo (concepto conocido)
try:
    t, d = _post("/talk", {"message": "que es kali linux"})
    txt = d.get("text", "")
    check("grafo-respuesta", "kali" in txt.lower() and t < 6, f"{t}s {txt[:60]}")
except Exception as e:
    check("grafo-respuesta", False, str(e))

# 5. Research ACTIVO (concepto nuevo no-tech)
try:
    nuevo = f"que es el fenomeno {int(time.time())%9999} meiosis celular"
    t, d = _post("/talk", {"message": "que es la meiosis"})
    txt = d.get("text", "")
    ok = ("meiosis" in txt.lower() or "investigu" in txt.lower() or "célula" in txt.lower())
    check("research-activo", ok, f"{t}s {txt[:70]}")
except Exception as e:
    check("research-activo", False, str(e))

# 6. Compositor (conectores)
try:
    t, d = _post("/talk", {"message": "explicame docker"})
    txt = d.get("text", "")
    check("compositor", len(txt) > 50, f"{t}s len={len(txt)}")
except Exception as e:
    check("compositor", False, str(e))

# 7. Reason endpoint
try:
    t, d = _post("/reason", {"query": "kali linux"})
    check("reason", bool(d.get("answer")), str(d.get("answer",""))[:60])
except Exception as e:
    check("reason", False, str(e))

# 8. Status servicios
try:
    s = _get("/status")
    check("status", isinstance(s, dict), str(list(s.keys()))[:80])
except Exception as e:
    check("status", False, str(e))

print("="*60)
print(f"RESULTADO: {PASS} PASS / {FAIL} FAIL")
print("="*60)
sys.exit(0 if FAIL == 0 else 1)
