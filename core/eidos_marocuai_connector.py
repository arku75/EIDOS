#!/usr/bin/env python3
"""core/eidos_marocuai_connector.py — Puente EIDOS↔Marocuai (o cualquier IA externa via HTTP/SSH)"""
import json, logging, urllib.request, os
log = logging.getLogger("eidos.marocuai")

MAROCUAI_URL = os.environ.get("MAROCUAI_URL", "http://localhost:8004/talk")

def ask_marocuai(message: str, timeout: int = 30) -> dict:
    """Envía mensaje a Marocuai (o IA externa) via HTTP POST."""
    try:
        req = urllib.request.Request(MAROCUAI_URL, 
            data=json.dumps({"message": message}).encode(),
            headers={"Content-Type": "application/json"}, method="POST")
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return json.loads(r.read().decode())
    except Exception as e:
        log.warning("Marocuai unreachable: %s", e)
        return {"error": str(e)}

def bridge_debate(topic: str, turns: int = 3) -> list:
    """Debate multi-turno: EIDOS↔Marocuai sobre un tema. Ambos persisten."""
    dialogue = []
    msg = f"[EIDOS]: ¿Qué sabes sobre {topic}? Iniciemos un debate."
    for i in range(turns):
        resp = ask_marocuai(msg)
        if resp.get("error"):
            dialogue.append({"turn": i, "from": "marocuai", "text": f"[ERROR] {resp['error']}"})
            break
        reply = resp.get("text", str(resp)[:500])
        dialogue.append({"turn": i, "from": "marocuai", "text": reply})
        msg = f"[EIDOS]: Interesante. Sobre tu punto de '{reply[:100]}', yo opino que... (responde EIDOS ahora)"
        try:
            from core.knowledge_reasoner import get_reasoner
            eidos_resp = get_reasoner().reason(topic)
            if eidos_resp and eidos_resp.get("answer"):
                dialogue.append({"turn": i, "from": "eidos", "text": eidos_resp["answer"][:500]})
                msg = f"[EIDOS respondió]: {eidos_resp['answer'][:300]}. ¿Qué opinas?"
        except: pass
    return dialogue

if __name__ == "__main__":
    import sys
    if len(sys.argv) > 1:
        r = ask_marocuai(" ".join(sys.argv[1:]))
        print(json.dumps(r, indent=2, ensure_ascii=False))
    else:
        print(f"Marocuai URL: {MAROCUAI_URL}")
        print("Uso: python3 -m core.eidos_marocuai_connector 'mensaje'")
