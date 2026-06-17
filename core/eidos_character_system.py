"""
core/eidos_character_system.py — Character system mini-universo (F10)
======================================================================
Modelo de carácter persistente para Colony: traits cuantificados,
skills, tastes (placeholder vector), feelings mutables, system prompt
generado, merge entre caracteres y mini-world loop (diálogo entre N
caracteres rutado por eidos_llm.complete con persona como system).

Honesto:
  • Persistencia en ~/.eidos/colony_characters.db (schema propio).
  • Chronicle de los diálogos en la tabla `chronicle` ya existente
    de ~/.eidos/colony_chronicle.db (actor=char_id,
    event_type='dialogue_turn').
  • Los caracteres CONSERVAN acceso al toolset completo de EIDOS-en-Kali
    cuando se invocan desde la máquina de SER. La línea disciplinada
    aplica al CLON-en-amigo, no a las personas del EIDOS local.
  • run_dialogue admite un `llm_callable` inyectable: producción
    usa eidos_llm.complete; tests usan stub determinista.

CLI:
  python3 -m core.eidos_character_system create --name X --base curiosity=70,caution=40
  python3 -m core.eidos_character_system list
  python3 -m core.eidos_character_system merge --a <idA> --b <idB> --name "fusión"
  python3 -m core.eidos_character_system run --chars <idA>,<idB> --turns 6 --topic "..."
  python3 -m core.eidos_character_system seed       # crea 3 chars seed
  python3 -m core.eidos_character_system self-test
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sqlite3
import sys
import time
import uuid
from pathlib import Path
from typing import Callable, Optional
from core.db import get_conn, get_conn_ctx

# ── Paths y schema ─────────────────────────────────────────────────────────

EIDOS_HOME      = Path(os.path.expanduser("~/.eidos"))
CHAR_DB         = EIDOS_HOME / "colony_characters.db"
CHRONICLE_DB    = EIDOS_HOME / "colony_chronicle.db"

DEFAULT_TRAITS = {
    "curiosity": 50, "caution": 50, "warmth": 50,
    "ambition": 50, "humor": 50, "patience": 50, "honesty": 70,
}


SCHEMA = """
CREATE TABLE IF NOT EXISTS characters (
    char_id        TEXT PRIMARY KEY,
    name           TEXT NOT NULL,
    traits         TEXT NOT NULL,        -- JSON dict
    skills         TEXT NOT NULL,        -- JSON list
    tastes         TEXT NOT NULL,        -- JSON list (placeholder vector)
    feelings       TEXT NOT NULL,        -- JSON dict {mood, energy, last_event}
    system_prompt  TEXT NOT NULL,
    created_at     REAL NOT NULL,
    last_active_at REAL
);
CREATE INDEX IF NOT EXISTS idx_chars_name ON characters(name);
"""


def _conn() -> sqlite3.Connection:
    EIDOS_HOME.mkdir(parents=True, exist_ok=True)
    c = get_conn(CHAR_DB)
    c.executescript(SCHEMA)
    return c


def _chronicle_conn() -> sqlite3.Connection:
    """Conexión read-write al chronicle. Asume schema preexistente
    (tabla chronicle con columnas timestamp, actor, event_type,
    content, metadata, importance, session_id)."""
    return get_conn(CHRONICLE_DB)


# ── Generación de system_prompt desde traits ───────────────────────────────

def _trait_descriptor(name: str, value: int) -> str:
    """Devuelve un descriptor textual del rasgo según el valor 0-100."""
    if value >= 80: lvl = "muy alto"
    elif value >= 60: lvl = "alto"
    elif value >= 40: lvl = "medio"
    elif value >= 20: lvl = "bajo"
    else: lvl = "muy bajo"
    return f"{name}: {lvl} ({value})"


def build_system_prompt(name: str, traits: dict, skills: list,
                        tastes: list) -> str:
    """Crea el system prompt para un carácter a partir de su perfil.
    Conciso para que modelos pequeños lo respeten (lección de S57g-l)."""
    trait_str = ", ".join(_trait_descriptor(k, v)
                          for k, v in sorted(traits.items()))
    skills_str = ", ".join(skills[:8]) if skills else "ninguno explícito"
    tastes_str = ", ".join(tastes[:6]) if tastes else "no declarados"
    return (
        f"Eres {name}, un personaje de Colony (la comunidad de EIDOS). "
        f"Rasgos: {trait_str}. "
        f"Habilidades: {skills_str}. "
        f"Gustos: {tastes_str}. "
        f"Responde DESDE ese perfil. Sé conciso (3-5 frases). "
        f"No menciones que eres una IA; eres {name}."
    )


# ── CRUD básico ────────────────────────────────────────────────────────────

def _row_to_dict(row) -> dict:
    return {
        "char_id": row[0], "name": row[1],
        "traits": json.loads(row[2]),
        "skills": json.loads(row[3]),
        "tastes": json.loads(row[4]),
        "feelings": json.loads(row[5]),
        "system_prompt": row[6],
        "created_at": row[7], "last_active_at": row[8],
    }


def create_character(name: str,
                     traits: Optional[dict] = None,
                     skills: Optional[list] = None,
                     tastes: Optional[list] = None,
                     system_prompt: Optional[str] = None) -> dict:
    """Crea un personaje nuevo con char_id uuid. Devuelve el dict."""
    if not name or not name.strip():
        raise ValueError("name vacío")
    t = dict(DEFAULT_TRAITS)
    if traits:
        for k, v in traits.items():
            t[k] = max(0, min(100, int(v)))
    s = list(skills or [])
    st = list(tastes or [])
    sp = system_prompt or build_system_prompt(name, t, s, st)
    feelings = {"mood": "neutro", "energy": 70, "last_event": None}
    cid = "char_" + uuid.uuid4().hex[:16]
    now = time.time()
    with _conn() as c:
        c.execute(
            "INSERT INTO characters(char_id,name,traits,skills,tastes,"
            "feelings,system_prompt,created_at,last_active_at) "
            "VALUES(?,?,?,?,?,?,?,?,?)",
            (cid, name, json.dumps(t, ensure_ascii=False),
             json.dumps(s, ensure_ascii=False),
             json.dumps(st, ensure_ascii=False),
             json.dumps(feelings, ensure_ascii=False),
             sp, now, None))
    return load_character(cid)


def load_character(char_id: str) -> Optional[dict]:
    with _conn() as c:
        r = c.execute(
            "SELECT char_id,name,traits,skills,tastes,feelings,"
            "system_prompt,created_at,last_active_at "
            "FROM characters WHERE char_id=?", (char_id,)).fetchone()
    return _row_to_dict(r) if r else None


def resolve_char(ident: str) -> Optional[dict]:
    """Resuelve por char_id exacto o por name (primer match)."""
    if ident.startswith("char_"):
        d = load_character(ident)
        if d:
            return d
    with _conn() as c:
        r = c.execute(
            "SELECT char_id,name,traits,skills,tastes,feelings,"
            "system_prompt,created_at,last_active_at "
            "FROM characters WHERE name=? "
            "ORDER BY created_at LIMIT 1", (ident,)).fetchone()
    return _row_to_dict(r) if r else None


def list_characters() -> list[dict]:
    with _conn() as c:
        rows = c.execute(
            "SELECT char_id,name,traits,skills,tastes,feelings,"
            "system_prompt,created_at,last_active_at "
            "FROM characters ORDER BY created_at").fetchall()
    return [_row_to_dict(r) for r in rows]


def delete_character(char_id: str) -> bool:
    with _conn() as c:
        n = c.execute("DELETE FROM characters WHERE char_id=?",
                      (char_id,)).rowcount
    return n > 0


# ── Feelings (sigmoid-ish clamping) ────────────────────────────────────────

def _clamp(x: float, lo: float = 0, hi: float = 100) -> float:
    return max(lo, min(hi, x))


def update_feelings(char_id: str, delta: dict) -> Optional[dict]:
    """Aplica delta a feelings; energy clamped [0,100]; mood se reemplaza
    si viene en delta. last_event se registra siempre."""
    d = load_character(char_id)
    if not d:
        return None
    f = d["feelings"]
    if "energy" in delta:
        f["energy"] = _clamp(float(f.get("energy", 70)) + float(delta["energy"]))
    if "mood" in delta:
        f["mood"] = str(delta["mood"])[:24]
    f["last_event"] = delta.get("event", f.get("last_event"))
    now = time.time()
    with _conn() as c:
        c.execute("UPDATE characters SET feelings=?, last_active_at=? "
                  "WHERE char_id=?",
                  (json.dumps(f, ensure_ascii=False), now, char_id))
    return load_character(char_id)


# ── Merge (promedio ponderado de traits, unión de skills, promedio tastes) ─

def merge(char_a_id: str, char_b_id: str,
          weights: tuple[float, float] = (0.5, 0.5),
          new_name: Optional[str] = None) -> Optional[dict]:
    a = load_character(char_a_id)
    b = load_character(char_b_id)
    if not a or not b:
        return None
    wa, wb = weights
    total = wa + wb
    if total <= 0:
        wa, wb, total = 0.5, 0.5, 1.0
    wa, wb = wa / total, wb / total

    keys = set(a["traits"]) | set(b["traits"])
    merged_traits = {
        k: int(round(a["traits"].get(k, 50) * wa
                     + b["traits"].get(k, 50) * wb))
        for k in keys
    }
    merged_skills = sorted(set(a["skills"]) | set(b["skills"]))
    # Tastes: si ambos son vectores numéricos del mismo len → promedio;
    # si son listas heterogéneas → concat dedupe.
    if (a["tastes"] and b["tastes"]
            and len(a["tastes"]) == len(b["tastes"])
            and all(isinstance(x, (int, float)) for x in a["tastes"] + b["tastes"])):
        merged_tastes = [
            round(a["tastes"][i] * wa + b["tastes"][i] * wb, 4)
            for i in range(len(a["tastes"]))
        ]
    else:
        merged_tastes = list(dict.fromkeys(a["tastes"] + b["tastes"]))
    merged_name = new_name or f"{a['name']}+{b['name']}"
    return create_character(merged_name,
                            traits=merged_traits,
                            skills=merged_skills,
                            tastes=merged_tastes)


# ── Mini-world loop (run_dialogue) ─────────────────────────────────────────

def _default_llm_callable(system: str, prompt: str,
                          max_tokens: int = 220) -> tuple[str, str]:
    """Adapter por defecto a eidos_llm.complete con inject_identity=False
    para respetar la persona del carácter."""
    try:
        sys.path.insert(0, "/home/ser/EIDOS")
        from core.eidos_llm import complete  # type: ignore
    except Exception as e:  # noqa: BLE001
        return (f"[character_system: LLM no disponible — {e}]", "none")
    r = complete(prompt, system=system, max_tokens=max_tokens,
                 inject_identity=False)
    return (r.text, f"{r.provider}/{r.model}")


def _log_to_chronicle(char_id: str, turn: int, content: str,
                      model_used: str, topic: Optional[str],
                      session_id: str) -> None:
    try:
        with _chronicle_conn() as c:
            c.execute(
                "INSERT INTO chronicle(timestamp, actor, event_type, "
                "content, metadata, importance, session_id) "
                "VALUES(?,?,?,?,?,?,?)",
                (time.time(), char_id, "dialogue_turn",
                 content[:8000],
                 json.dumps({"turn": turn, "model": model_used,
                             "topic": topic}, ensure_ascii=False),
                 0.5, session_id))
    except Exception as e:  # noqa: BLE001
        # No queremos romper el loop si el chronicle falla
        sys.stderr.write(f"[chronicle: warn {e}]\n")


def run_dialogue(char_ids: list[str], turns: int = 4,
                 topic: Optional[str] = None,
                 llm_callable: Optional[Callable[[str, str, int], tuple[str, str]]] = None
                 ) -> list[dict]:
    """Hace que N caracteres dialoguen `turns` rondas. Cada ronda, cada
    carácter responde con su persona como system prompt.

    Devuelve lista de dicts {turn, char_id, name, content, model}.
    """
    if not char_ids:
        raise ValueError("char_ids vacío")
    chars: list[dict] = []
    for cid in char_ids:
        d = load_character(cid)
        if not d:
            raise KeyError(f"character no existe: {cid}")
        chars.append(d)

    fn = llm_callable or _default_llm_callable
    session_id = "dialogue_" + uuid.uuid4().hex[:12]
    transcript: list[dict] = []
    history: list[str] = []
    if topic:
        history.append(f"Tema de la conversación: {topic}")

    for turn in range(1, turns + 1):
        for ch in chars:
            user_prompt = (
                ("Contexto previo:\n" + "\n".join(history[-12:]) + "\n\n"
                 if history else "")
                + f"Es tu turno (turn {turn}). Responde en 2-4 frases."
            )
            text, model = fn(ch["system_prompt"], user_prompt, 220)
            text = (text or "").strip()
            row = {"turn": turn, "char_id": ch["char_id"],
                   "name": ch["name"], "content": text, "model": model}
            transcript.append(row)
            history.append(f"{ch['name']}: {text}")
            _log_to_chronicle(ch["char_id"], turn, text, model,
                              topic, session_id)
            # Mood heurístico simple
            lower = text.lower()
            pos = any(w in lower for w in
                      ("gracias", "genial", "perfecto", "bien", "me gusta"))
            neg = any(w in lower for w in
                      ("no", "mal", "triste", "preocupa", "duda"))
            delta = {"event": f"turn_{turn}_with_session_{session_id[:8]}"}
            if pos and not neg:
                delta["energy"] = +2
                delta["mood"] = "animado"
            elif neg and not pos:
                delta["energy"] = -2
                delta["mood"] = "pensativo"
            update_feelings(ch["char_id"], delta)
    return transcript


# ── Seeds (3 caracteres iniciales) ─────────────────────────────────────────

SEEDS = [
    {
        "name": "Curador",
        "traits": {"curiosity": 75, "caution": 80, "warmth": 60,
                   "ambition": 40, "humor": 30, "patience": 85,
                   "honesty": 90},
        "skills": ["organizar conocimiento", "verificar fuentes",
                   "archivar"],
        "tastes": ["libros antiguos", "esquemas", "silencio"],
    },
    {
        "name": "Explorador",
        "traits": {"curiosity": 95, "caution": 30, "warmth": 60,
                   "ambition": 80, "humor": 60, "patience": 40,
                   "honesty": 70},
        "skills": ["probar nuevas APIs", "improvisar",
                   "abrir conversaciones"],
        "tastes": ["mapas", "rutas raras", "experimentos sin red"],
    },
    {
        "name": "Crítico",
        "traits": {"curiosity": 60, "caution": 70, "warmth": 35,
                   "ambition": 50, "humor": 25, "patience": 50,
                   "honesty": 95},
        "skills": ["detectar fallas", "preguntar incómodo",
                   "auditar razonamientos"],
        "tastes": ["argumentos sólidos", "casos límite",
                   "consistencia interna"],
    },
]


def seed_default(replace: bool = False) -> list[dict]:
    """Crea los 3 caracteres seed si no existen ya."""
    existing = {c["name"] for c in list_characters()}
    out = []
    for s in SEEDS:
        if s["name"] in existing and not replace:
            continue
        out.append(create_character(
            s["name"], traits=s["traits"],
            skills=s["skills"], tastes=s["tastes"]))
    return out


# ── Self-test (sin LLM real — stub determinista) ───────────────────────────

def _self_test() -> int:
    failures: list[str] = []

    def chk(name: str, cond: bool, detail: str = ""):
        mark = "✅" if cond else "❌"
        print(f"{mark} {name}" + (f" — {detail[:120]}" if detail else ""))
        if not cond:
            failures.append(name)

    # Aislamos el self-test: usamos DB temporal
    global CHAR_DB, CHRONICLE_DB
    orig_char = CHAR_DB
    orig_chronicle = CHRONICLE_DB
    import tempfile
    tmpdir = Path(tempfile.mkdtemp(prefix="eidos-charsys-"))
    CHAR_DB = tmpdir / "characters.db"
    CHRONICLE_DB = tmpdir / "chronicle.db"
    # Crear schema chronicle compatible para que _log_to_chronicle no falle
    with get_conn_ctx(CHRONICLE_DB) as c:
        c.execute("""CREATE TABLE chronicle (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            timestamp REAL NOT NULL,
            actor TEXT NOT NULL,
            event_type TEXT NOT NULL,
            content TEXT NOT NULL,
            metadata TEXT DEFAULT '{}',
            importance REAL DEFAULT 0.5,
            session_id TEXT
        );""")
    try:
        # 1) create_character básico
        a = create_character("TestA",
                              traits={"curiosity": 90, "caution": 20},
                              skills=["probar", "leer"],
                              tastes=["mar"])
        chk("create_character devuelve dict con char_id",
            isinstance(a, dict) and a.get("char_id", "").startswith("char_"))
        chk("traits clamp + defaults",
            a["traits"].get("curiosity") == 90
            and a["traits"].get("caution") == 20
            and a["traits"].get("warmth") == 50)
        chk("system_prompt contiene name", a["name"] in a["system_prompt"])

        b = create_character("TestB",
                              traits={"curiosity": 30, "caution": 90, "humor": 80},
                              skills=["criticar"], tastes=["argumentos"])
        chk("segundo carácter creado", b["char_id"] != a["char_id"])

        # 2) list y resolve
        ls = list_characters()
        chk("list_characters tiene 2", len(ls) == 2)
        chk("resolve por nombre", resolve_char("TestA")["char_id"] == a["char_id"])

        # 3) update_feelings
        u = update_feelings(a["char_id"], {"energy": -10, "mood": "cansado",
                                            "event": "test_event"})
        chk("update_feelings aplica delta",
            u["feelings"]["energy"] == 60 and u["feelings"]["mood"] == "cansado")

        # 4) merge
        m = merge(a["char_id"], b["char_id"], weights=(0.5, 0.5),
                  new_name="Fusion")
        chk("merge produce char nuevo válido",
            m and m["name"] == "Fusion" and m["char_id"] != a["char_id"]
            and m["char_id"] != b["char_id"])
        chk("merge curiosity promedio ~60",
            abs(m["traits"]["curiosity"] - 60) <= 1)
        chk("merge unión de skills",
            set(m["skills"]) >= {"probar", "leer", "criticar"})

        # 5) merge ponderado
        m2 = merge(a["char_id"], b["char_id"], weights=(0.9, 0.1),
                   new_name="MasA")
        chk("merge ponderado 0.9/0.1 favorece A",
            abs(m2["traits"]["curiosity"] - 84) <= 1
            and m2["traits"]["caution"] <= 30)

        # 6) run_dialogue con stub determinista (4 turnos)
        stub_call_count = {"n": 0}

        def stub_llm(system: str, prompt: str, max_tokens: int = 220):
            stub_call_count["n"] += 1
            # Confirmamos que recibimos el system del carácter
            name_hint = "TestA" if "TestA" in system else (
                "TestB" if "TestB" in system else "Other")
            return (f"[{name_hint} dice algo coherente turn ok gracias]",
                    "stub/v1")

        tr = run_dialogue([a["char_id"], b["char_id"]], turns=4,
                          topic="rumbos posibles",
                          llm_callable=stub_llm)
        chk("run_dialogue produce 8 entradas (2 chars × 4 turns)",
            len(tr) == 8, f"got {len(tr)}")
        chk("stub llamado 8 veces", stub_call_count["n"] == 8)
        chk("transcripts traen content y model",
            all(t["content"] and t["model"] == "stub/v1" for t in tr))

        # 7) chronicle log
        with get_conn_ctx(CHRONICLE_DB) as c:
            rows = c.execute(
                "SELECT COUNT(*) FROM chronicle "
                "WHERE event_type='dialogue_turn'").fetchone()[0]
        chk("chronicle log tiene 8 filas dialogue_turn", rows == 8,
            f"got {rows}")

        # 8) delete_character
        chk("delete_character A devuelve True",
            delete_character(a["char_id"]) is True)
        chk("load tras delete devuelve None",
            load_character(a["char_id"]) is None)

        # 9) build_system_prompt es conciso (lección de modelos pequeños)
        sp = build_system_prompt("X",
                                  {"curiosity": 90, "caution": 10},
                                  ["s1", "s2"], ["t1"])
        chk("system_prompt < 600 chars (conciso)", len(sp) < 600,
            f"len={len(sp)}")

    finally:
        CHAR_DB = orig_char
        CHRONICLE_DB = orig_chronicle
        # tmpdir cleanup
        import shutil
        shutil.rmtree(tmpdir, ignore_errors=True)

    print("-" * 60)
    if failures:
        print(f"❌ FAIL ({len(failures)}): {failures}")
        return 1
    print("✅ SELF-TEST PASS")
    return 0


# ── CLI ────────────────────────────────────────────────────────────────────

def _parse_traits_arg(s: str) -> dict:
    """Parses 'curiosity=70,caution=40' → dict[str,int]."""
    if not s:
        return {}
    out: dict[str, int] = {}
    for part in s.split(","):
        if "=" not in part:
            continue
        k, v = part.split("=", 1)
        try:
            out[k.strip()] = int(v.strip())
        except ValueError:
            continue
    return out


def _cli() -> int:
    ap = argparse.ArgumentParser(prog="eidos_character_system",
        description="Character system mini-universo de Colony (F10)")
    sub = ap.add_subparsers(dest="cmd")

    sp = sub.add_parser("create")
    sp.add_argument("--name", required=True)
    sp.add_argument("--base", default="",
                    help="traits 'curiosity=70,caution=40,...'")
    sp.add_argument("--skills", default="")
    sp.add_argument("--tastes", default="")

    sub.add_parser("list")

    sp = sub.add_parser("merge")
    sp.add_argument("--a", required=True)
    sp.add_argument("--b", required=True)
    sp.add_argument("--name", default=None)
    sp.add_argument("--weights", default="0.5,0.5")

    sp = sub.add_parser("run")
    sp.add_argument("--chars", required=True,
                    help="lista de char_ids o nombres separados por coma")
    sp.add_argument("--turns", type=int, default=4)
    sp.add_argument("--topic", default=None)

    sp = sub.add_parser("delete")
    sp.add_argument("--id", required=True)

    sub.add_parser("seed")
    sub.add_parser("self-test")

    args = ap.parse_args()

    if args.cmd == "create":
        skills = [x.strip() for x in args.skills.split(",") if x.strip()]
        tastes = [x.strip() for x in args.tastes.split(",") if x.strip()]
        d = create_character(args.name, _parse_traits_arg(args.base),
                              skills, tastes)
        print(json.dumps(d, indent=2, ensure_ascii=False))
        return 0

    if args.cmd == "list":
        for c in list_characters():
            print(f"{c['char_id']}  {c['name']:<20}  "
                  f"mood={c['feelings']['mood']:<10} "
                  f"created={time.strftime('%Y-%m-%d', time.localtime(c['created_at']))}")
        return 0

    if args.cmd == "merge":
        try:
            wa, wb = (float(x) for x in args.weights.split(","))
        except Exception:
            wa, wb = 0.5, 0.5
        a = resolve_char(args.a)
        b = resolve_char(args.b)
        if not a or not b:
            print(f"no resuelto: a={bool(a)} b={bool(b)}"); return 1
        m = merge(a["char_id"], b["char_id"], (wa, wb), args.name)
        print(json.dumps(m, indent=2, ensure_ascii=False))
        return 0

    if args.cmd == "run":
        ids = []
        for x in args.chars.split(","):
            r = resolve_char(x.strip())
            if not r:
                print(f"no resuelto: {x}"); return 1
            ids.append(r["char_id"])
        tr = run_dialogue(ids, turns=args.turns, topic=args.topic)
        for t in tr:
            print(f"[t{t['turn']}] {t['name']:<14} ({t['model']}): "
                  f"{t['content']}")
        return 0

    if args.cmd == "delete":
        ok = delete_character(args.id)
        print(json.dumps({"ok": ok, "char_id": args.id}))
        return 0 if ok else 1

    if args.cmd == "seed":
        created = seed_default()
        print(f"creados {len(created)} seeds nuevos")
        for c in created:
            print(f"  {c['char_id']}  {c['name']}")
        return 0

    if args.cmd == "self-test":
        return _self_test()

    ap.print_help()
    return 0


if __name__ == "__main__":
    raise SystemExit(_cli())
