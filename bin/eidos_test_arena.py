#!/usr/bin/env python3
"""
EIDOS Test Arena — entorno de prueba AISLADO para que SER hable con EIDOS
mientras Claude monitorea todo (S113).

- Carga el código NUEVO (NLG, SemanticRouter, pipeline arreglado).
- Flags: USE_NEURAL_RESPONDER=1, EIDOS_NLG=1.
- Puerto 8009 (no toca el bridge productivo :8003 ni el web panel :8080).
- Registra CADA interacción en ~/.eidos/test_arena.log con detalle completo:
  timestamp, mensaje, ruta semántica, agente, low_confidence, latencia, respuesta.

SER abre http://localhost:8009 en el navegador y habla. Claude lee el log.
"""
import os, sys, time, json, logging
from pathlib import Path

os.environ["USE_NEURAL_RESPONDER"] = "1"
os.environ["EIDOS_NLG"] = "1"
os.environ["EIDOS_BRIDGE_MODE"] = "1"
sys.path.insert(0, str(Path.home() / "EIDOS"))

LOG_PATH = Path.home() / ".eidos" / "test_arena.log"
logging.basicConfig(level=logging.WARNING)

from flask import Flask, request, jsonify, Response
from core.colony_community import ColonyCommunity
from core.knowledge_reasoner import get_reasoner
from core.semantic_router import get_semantic_router

def log_event(kind, data):
    """Registra un evento estructurado en el log de la arena."""
    entry = {"ts": time.strftime("%H:%M:%S"), "kind": kind, **data}
    with open(LOG_PATH, "a") as f:
        f.write(json.dumps(entry, ensure_ascii=False) + "\n")

print("Arena: construyendo grafo neuronal...", flush=True)
_r = get_reasoner(); _r.build_graph()
_router = get_semantic_router()
_colony = ColonyCommunity()
log_event("startup", {"graph_nodes": _r.graph.size[0], "graph_edges": _r.graph.size[1]})
print(f"Arena LISTA: {_r.graph.size[0]} nodos. Abre http://localhost:8009", flush=True)

app = Flask(__name__)

PAGE = """<!doctype html><html><head><meta charset=utf-8><title>EIDOS Arena</title>
<style>
body{font-family:system-ui;max-width:760px;margin:0 auto;background:#0d1117;color:#c9d1d9;padding:16px}
#chat{height:70vh;overflow-y:auto;border:1px solid #30363d;border-radius:8px;padding:12px;margin-bottom:10px}
.msg{margin:8px 0;padding:10px 14px;border-radius:10px;line-height:1.45}
.ser{background:#1f6feb33;text-align:right}
.eidos{background:#23863633}
.meta{font-size:11px;color:#8b949e;margin-top:4px}
#inp{width:78%;padding:12px;background:#161b22;color:#c9d1d9;border:1px solid #30363d;border-radius:8px}
button{padding:12px 20px;background:#238636;color:#fff;border:0;border-radius:8px;cursor:pointer}
h2{color:#58a6ff}
</style></head><body>
<h2>🧠 EIDOS — Arena de prueba</h2>
<div id=chat></div>
<input id=inp placeholder="Escribe tu pregunta y pulsa Enter..." autofocus>
<button onclick=send()>Enviar</button>
<script>
const chat=document.getElementById('chat'),inp=document.getElementById('inp');
function add(t,cls,meta){const d=document.createElement('div');d.className='msg '+cls;
 d.textContent=t;if(meta){const m=document.createElement('div');m.className='meta';m.textContent=meta;d.appendChild(m);}
 chat.appendChild(d);chat.scrollTop=chat.scrollHeight;}
async function send(){const q=inp.value.trim();if(!q)return;add(q,'ser');inp.value='';
 add('pensando...','eidos','');const t0=Date.now();
 try{const r=await fetch('/chat',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({message:q})});
  const d=await r.json();chat.lastChild.remove();
  add(d.response,'eidos','['+d.elapsed+'s · '+d.agent+' · ruta:'+d.route+(d.low_conf?' · NO-SÉ':'')+']');
 }catch(e){chat.lastChild.remove();add('ERROR: '+e,'eidos','');}}
inp.addEventListener('keydown',e=>{if(e.key==='Enter')send();});
</script></body></html>"""

@app.route("/")
def home():
    return Response(PAGE, mimetype="text/html")

@app.route("/chat", methods=["POST"])
def chat():
    msg = (request.json or {}).get("message", "").strip()
    if not msg:
        return jsonify({"response": "(vacío)", "elapsed": 0, "agent": "-", "route": "-", "low_conf": False})
    t0 = time.time()
    route, conf, method = _router.route(msg)
    try:
        resp = _colony.deliberate(msg, max_agents=1, session_id="arena")
        dt = round(time.time() - t0, 2)
        agent = (resp.get("agents_consulted") or ["?"])[0]
        text = resp.get("response", "(sin respuesta)")
        metrics = resp.get("individual_responses", {}).get("neural_metrics", {})
        low = metrics.get("low_confidence", False)
        log_event("chat", {
            "ser": msg, "route": route, "route_method": method, "agent": agent,
            "low_conf": low, "elapsed": dt, "eidos": text,
            "top_conf": metrics.get("top_confidence"),
        })
        return jsonify({"response": text, "elapsed": dt, "agent": agent,
                        "route": route, "low_conf": low})
    except Exception as e:
        dt = round(time.time() - t0, 2)
        import traceback
        tb = traceback.format_exc()
        log_event("error", {"ser": msg, "error": str(e), "traceback": tb, "elapsed": dt})
        return jsonify({"response": f"ERROR interno: {e}", "elapsed": dt,
                        "agent": "error", "route": route, "low_conf": True})

if __name__ == "__main__":
    app.run(host="127.0.0.1", port=8009, threaded=True)
