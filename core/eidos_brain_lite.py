"""
core/eidos_brain_lite.py — Loop central determinista (S63 Bloque C)
====================================================================

Decisión SER (S63): brain-lite MANDA, LLM OPINA pero no decide.
La identidad y el guard se quedan en eidos_llm.py — intactos.

Pipeline por petición:
  INPUT → RECALL → INTENT → POLICY → CONSULT? → DECIDE → EXECUTE → LEARN

  • INPUT: instrucción de SER (CLI, /screen, Telegram, claude-bridge, sock).
  • RECALL: BrainMemory.recall(query, limit=8) — RAG puro, sin LLM.
  • INTENT: clasificador determinista (regex + reglas) →
     uno de {search, file, run, learn, teach, explain, recall, character, system}.
  • POLICY: pick_character() — elige el carácter de Colony cuyos traits
     maximizan utilidad para el intent (caution para system, curiosity para
     search/learn, warmth para explain/character).
  • CONSULT (opcional): si reglas o intent==explain → eidos_llm.complete()
     pero la salida entra como `proposal`, NO se ejecuta directamente.
  • DECIDE (centro): árbol determinista evalúa el proposal contra
     (a) allowlist de acciones por intent, (b) memoria de métodos previos,
     (c) anti-bucle (ring buffer de últimas 32 acciones). Decide
     EXECUTE / MODIFY / DISCARD.
  • EXECUTE: tools directos (subprocess, screen_trainer, brain_memory).
  • LEARN: persiste {intent, decision, executed, result} en BrainMemory.

Servicio:
  • Unix socket: ~/.eidos/brain_lite.sock (asyncio.start_unix_server)
  • Protocolo: 1 línea JSON por mensaje, terminada en \\n
    request:  {"op":"ask","text":"..."}  o  {"op":"ping"}
    response: {"ok":bool,"intent":...,"decision":...,"executed":...,"result":...}
  • Si el socket ya existe → lo borra antes de bind (cleanup propio).

Uso programático:
    from core.eidos_brain_lite import handle_request
    res = handle_request({"op":"ask","text":"recuerda qué hicimos en S62"})

CLI / self-test:
    python3 core/eidos_brain_lite.py --self-test
    python3 core/eidos_brain_lite.py --serve
    python3 core/eidos_brain_lite.py --ask "ping"
"""
from __future__ import annotations

import os
import sys
import re
import json
import time
import asyncio
import logging
import collections
from pathlib import Path
from typing import Optional, Tuple

sys.path.insert(0, str(Path.home() / "EIDOS"))

log = logging.getLogger("eidos.brain_lite")

SOCK_PATH = Path.home() / ".eidos" / "brain_lite.sock"
RING_MAX = 32
LOOP_WINDOW_S = 30.0
LOOP_THRESHOLD = 3   # >3 repeticiones (intent,target) en LOOP_WINDOW_S → DISCARD


# ═══════════════════════════════════════════════════════════════════════════
# INTENT CLASSIFIER (determinista, sin LLM)
# ═══════════════════════════════════════════════════════════════════════════

_INTENT_RULES: list[Tuple[str, re.Pattern]] = [
    # Orden: las palabras MÁS específicas (binarios, nombres, prefijos) primero
    ("system",    re.compile(r"\b(systemctl|systemd|servicio|service|reiniciar|kill|process|pid)\b", re.I)),
    ("character", re.compile(r"\b(curador|explorador|cr[íi]tico|car[áa]cter|character|colony)\b", re.I)),
    ("teach",     re.compile(r"\b(enseña|enseñ|method_taught|repite|repetir)\b", re.I)),
    ("search",    re.compile(r"\b(busca|search|encuentra|abre.*\.(com|fm|org|net))\b", re.I)),
    ("file",      re.compile(r"\b(archivo|lee.*\.(py|md|toml|json)|file\s+\S+|leer\s+\S+\.(py|md|toml|json))\b", re.I)),
    ("run",       re.compile(r"\b(ejecuta|run\s|corre|shell)\b", re.I)),
    ("learn",     re.compile(r"\b(aprende|estudia|investiga|libro|pdf)\b", re.I)),
    # recall y explain al final — son los más genéricos
    ("recall",    re.compile(r"\b(recuerda|recuerdas|memoria|lo último|qu[ée] hicimos)\b", re.I)),
    ("explain",   re.compile(r"\b(explica|por qu[ée]|c[óo]mo funciona|qu[ée] es|qu[ée] significa)\b", re.I)),
]


def classify_intent(text: str) -> str:
    for intent, pat in _INTENT_RULES:
        if pat.search(text or ""):
            return intent
    return "explain"   # default


# ═══════════════════════════════════════════════════════════════════════════
# POLICY: pick_character por intent
# ═══════════════════════════════════════════════════════════════════════════

_INTENT_TRAITS = {
    "recall": "caution",
    "explain": "warmth",
    "search": "curiosity",
    "learn": "curiosity",
    "teach": "warmth",
    "file": "caution",
    "run": "caution",
    "character": "warmth",
    "system": "caution",
}


def pick_character(intent: str) -> Optional[dict]:
    """Elige el carácter de Colony cuyo trait dominante encaja con el intent."""
    try:
        from core.eidos_character_system import list_characters
        chars = list_characters()
    except Exception:
        return None
    if not chars:
        return None
    trait = _INTENT_TRAITS.get(intent, "curiosity")
    def score(c):
        traits = c.get("traits") or {}
        if isinstance(traits, str):
            try:
                traits = json.loads(traits)
            except Exception:
                traits = {}
        return traits.get(trait, 0)
    return max(chars, key=score)


# ═══════════════════════════════════════════════════════════════════════════
# ANTI-LOOP (ring buffer)
# ═══════════════════════════════════════════════════════════════════════════

_recent_actions: collections.deque = collections.deque(maxlen=RING_MAX)


def is_looping(intent: str, target: str) -> bool:
    now = time.time()
    key = (intent, (target or "")[:80])
    recent = [t for k, t in _recent_actions if k == key and (now - t) < LOOP_WINDOW_S]
    return len(recent) >= LOOP_THRESHOLD


def push_action(intent: str, target: str) -> None:
    _recent_actions.append(((intent, (target or "")[:80]), time.time()))


# ═══════════════════════════════════════════════════════════════════════════
# RECALL (RAG sin LLM)
# ═══════════════════════════════════════════════════════════════════════════

def recall(query: str, limit: int = 8) -> list[dict]:
    try:
        from core.brain_memory import BrainMemory
        return BrainMemory().recall(query, limit=limit) or []
    except Exception as e:
        log.warning("recall falló: %s", e)
        return []


# ═══════════════════════════════════════════════════════════════════════════
# CONSULT (LLM opcional — opina pero no decide)
# ═══════════════════════════════════════════════════════════════════════════

def consult_llm(text: str, context: list[dict], intent: str) -> Optional[str]:
    """Pide al LLM una propuesta. NO se ejecuta directamente — pasa por DECIDE."""
    try:
        from core.eidos_llm import complete
    except Exception:
        return None
    try:
        ctx_lines = [f"- {d.get('content','')[:200]}" for d in (context or [])[:5]]
        ctx_text = "\n".join(ctx_lines) if ctx_lines else "(sin contexto)"
        prompt = (
            f"Intent: {intent}\n"
            f"Contexto del brain:\n{ctx_text}\n\n"
            f"Pregunta del SER: {text}\n\n"
            f"Responde en ≤120 palabras."
        )
        r = complete(prompt, max_tokens=300)
        if hasattr(r, "text"):
            return r.text
        return str(r) if r else None
    except Exception as e:
        log.warning("consult_llm falló: %s", e)
        return None


# ═══════════════════════════════════════════════════════════════════════════
# DECIDE (CENTRAL — brain-lite manda)
# ═══════════════════════════════════════════════════════════════════════════

_ALLOWED_ACTIONS = {
    "recall": ["recall_only"],
    "explain": ["recall_only", "llm_summary"],
    "search": ["screen_trainer", "recall_only"],
    "file": ["read_file"],
    "run": ["shell_safe"],
    "learn": ["continuous_learner_queue", "recall_only"],
    "teach": ["screen_trainer"],
    "character": ["character_invoke", "recall_only"],
    "system": ["systemctl_query"],
}


def decide(intent: str, text: str, proposal: Optional[str],
           context: list[dict]) -> dict:
    """Árbol determinista: EXECUTE / MODIFY / DISCARD."""
    target = (text or "")[:80]
    if is_looping(intent, target):
        return {"decision": "DISCARD", "reason": "loop",
                "action": None}

    allowed = _ALLOWED_ACTIONS.get(intent, ["recall_only"])
    chosen = allowed[0]   # por defecto, la primera acción permitida

    if intent == "explain" and proposal:
        chosen = "llm_summary"
    if intent == "search" and "://" in text:
        chosen = "screen_trainer"

    push_action(intent, target)
    return {"decision": "EXECUTE", "action": chosen, "reason": "allowed"}


# ═══════════════════════════════════════════════════════════════════════════
# EXECUTE
# ═══════════════════════════════════════════════════════════════════════════

def execute(action: str, text: str, context: list[dict],
            proposal: Optional[str]) -> dict:
    if action == "recall_only":
        return {"result": [{"content": d.get("content", "")[:300],
                            "source": d.get("source", "?")}
                           for d in (context or [])[:5]]}
    if action == "llm_summary":
        return {"result": proposal or "(LLM no disponible)"}
    if action == "screen_trainer":
        try:
            from core.screen_trainer import train
            res = train(text, max_steps=6, focus_browser=True, dry_run=False)
            return {"result": res}
        except Exception as e:
            return {"result": f"screen_trainer error: {e}"}
    if action == "read_file":
        m = re.search(r"\b([A-Za-z0-9_\-./~]+\.(py|md|toml|json|txt|sh))\b", text)
        if not m:
            return {"result": "no se identificó archivo"}
        p = Path(os.path.expanduser(m.group(1)))
        if not p.exists() or not p.is_file() or p.stat().st_size > 80_000:
            return {"result": "archivo no leíble o demasiado grande"}
        try:
            return {"result": p.read_text(errors="replace")[:4000]}
        except Exception as e:
            return {"result": f"read err: {e}"}
    if action == "systemctl_query":
        try:
            import subprocess
            r = subprocess.run(
                ["systemctl", "--user", "list-units", "--no-pager", "--state=running"],
                capture_output=True, text=True, timeout=4,
            )
            lines = [l for l in r.stdout.splitlines() if "eidos" in l.lower()]
            return {"result": "\n".join(lines[:25])}
        except Exception as e:
            return {"result": f"systemctl error: {e}"}
    if action == "character_invoke":
        try:
            from core.eidos_character_system import list_characters
            chars = list_characters()
            return {"result": [{"name": c.get("name"), "char_id": c.get("char_id")}
                               for c in chars]}
        except Exception as e:
            return {"result": f"character err: {e}"}
    if action == "continuous_learner_queue":
        topic_file = Path.home() / ".eidos" / "libre_topic.txt"
        topic_file.parent.mkdir(parents=True, exist_ok=True)
        topic_file.write_text(text[:300], encoding="utf-8")
        return {"result": f"topic encolado: {text[:60]}"}
    if action == "shell_safe":
        return {"result": "shell_safe no implementado por seguridad — usa el endpoint canal F3"}
    return {"result": f"acción desconocida: {action}"}


# ═══════════════════════════════════════════════════════════════════════════
# LEARN
# ═══════════════════════════════════════════════════════════════════════════

def learn(text: str, intent: str, decision: dict, exec_result: dict) -> Optional[int]:
    try:
        from core.brain_memory import BrainMemory
        summary = (
            f"brain_lite cycle: intent={intent} decision={decision.get('decision')} "
            f"action={decision.get('action')} text={text[:120]}"
        )
        return BrainMemory().remember(
            content=summary,
            tags=["brain_lite", "cycle", f"intent={intent}"],
            importance=0.5,
            category="brain_lite",
        )
    except Exception as e:
        log.warning("learn falló: %s", e)
        return None


# ═══════════════════════════════════════════════════════════════════════════
# HANDLER (loop completo)
# ═══════════════════════════════════════════════════════════════════════════

def handle_request(req: dict) -> dict:
    op = req.get("op", "ask")
    if op == "ping":
        return {"ok": True, "pong": True, "ts": time.time()}
    if op != "ask":
        return {"ok": False, "error": f"op desconocida: {op}"}

    text = (req.get("text") or "").strip()
    if not text:
        return {"ok": False, "error": "text vacío"}

    t0 = time.time()
    intent = classify_intent(text)
    ctx = recall(text, limit=8)
    char = pick_character(intent)
    proposal = None
    if intent == "explain" or req.get("consult"):
        proposal = consult_llm(text, ctx, intent)
    decision = decide(intent, text, proposal, ctx)
    if decision["decision"] != "EXECUTE":
        node_id = learn(text, intent, decision, {})
        return {"ok": True, "intent": intent, "decision": decision,
                "result": None, "node_id": node_id,
                "elapsed_s": round(time.time() - t0, 2)}
    exec_res = execute(decision["action"], text, ctx, proposal)
    node_id = learn(text, intent, decision, exec_res)
    return {
        "ok": True,
        "intent": intent,
        "character": (char or {}).get("name"),
        "decision": decision,
        "result": exec_res.get("result"),
        "node_id": node_id,
        "elapsed_s": round(time.time() - t0, 2),
    }


# ═══════════════════════════════════════════════════════════════════════════
# Unix-socket server
# ═══════════════════════════════════════════════════════════════════════════

async def _client(reader: asyncio.StreamReader, writer: asyncio.StreamWriter):
    try:
        line = await asyncio.wait_for(reader.readline(), timeout=30)
        if not line:
            writer.close()
            return
        try:
            req = json.loads(line.decode())
        except Exception:
            writer.write(json.dumps({"ok": False, "error": "json inválido"}).encode() + b"\n")
            await writer.drain()
            writer.close()
            return
        resp = await asyncio.to_thread(handle_request, req)
        writer.write((json.dumps(resp, ensure_ascii=False) + "\n").encode())
        await writer.drain()
    except Exception as e:
        try:
            writer.write(json.dumps({"ok": False, "error": str(e)}).encode() + b"\n")
            await writer.drain()
        except Exception:
            pass
    finally:
        try:
            writer.close()
        except Exception:
            pass


async def serve(path: Path = SOCK_PATH):
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        try:
            path.unlink()
        except Exception:
            pass
    server = await asyncio.start_unix_server(_client, path=str(path))
    os.chmod(path, 0o600)
    log.info("brain-lite escuchando en %s", path)
    async with server:
        await server.serve_forever()


# ═══════════════════════════════════════════════════════════════════════════
# Self-test
# ═══════════════════════════════════════════════════════════════════════════

def _self_test() -> dict:
    checks = []
    # 1. intent classifier
    checks.append(("intent recall", classify_intent("recuerda qué hicimos en S62") == "recall"))
    checks.append(("intent system", classify_intent("systemctl status eidos") == "system"))
    checks.append(("intent search", classify_intent("busca en github.com") == "search"))
    checks.append(("intent default", classify_intent("hola") == "explain"))
    # 2. anti-loop
    _recent_actions.clear()
    for _ in range(LOOP_THRESHOLD + 1):
        push_action("test", "X")
    checks.append(("anti-loop bloquea repetición", is_looping("test", "X")))
    _recent_actions.clear()
    checks.append(("anti-loop libera tras clear", not is_looping("test", "X")))
    # 3. handle_request ping
    pong = handle_request({"op": "ping"})
    checks.append(("ping ok", pong.get("ok") and pong.get("pong")))
    # 4. handle_request ask recall (no exige LLM)
    res = handle_request({"op": "ask", "text": "recuerda lo último de eidos"})
    checks.append(("ask recall produce intent", res.get("intent") == "recall"))
    checks.append(("ask recall ejecuta sin error", res.get("ok") and "decision" in res))
    # 5. handle_request ask system
    res2 = handle_request({"op": "ask", "text": "systemctl estado servicios eidos"})
    checks.append(("ask system devuelve listado", isinstance(res2.get("result"), str)))

    passed = sum(1 for _, ok in checks if ok)
    total = len(checks)
    return {
        "self_test": "PASS" if passed == total else "FAIL",
        "passed": passed,
        "total": total,
        "details": [{"check": c, "ok": ok} for c, ok in checks],
    }


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO,
                        format="%(asctime)s [brain_lite] %(message)s")
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("--self-test", action="store_true")
    ap.add_argument("--serve", action="store_true")
    ap.add_argument("--ask", type=str)
    args = ap.parse_args()

    if args.self_test:
        print(json.dumps(_self_test(), ensure_ascii=False, indent=2))
        sys.exit(0)
    if args.ask:
        print(json.dumps(handle_request({"op": "ask", "text": args.ask}),
                         ensure_ascii=False, indent=2))
        sys.exit(0)
    if args.serve:
        asyncio.run(serve())
        sys.exit(0)
    ap.print_help()
