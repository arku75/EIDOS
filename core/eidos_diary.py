"""
core/eidos_diary.py — Diario de conversaciones SER ↔ EIDOS (Idea #5)
======================================================================
Registro estructurado en ~/.eidos/conversations/ por fecha (JSONL).
Busca por keyword, resume por día, exporta a markdown.

Cada entrada:
  {ts, ts_iso, role, content, model?, provider?, elapsed_s?, kind?,
   tags?: [...], session_id?}

Sin LLM externo para el diary mismo — usa eidos_llm.complete()
opcionalmente para resúmenes diarios (--summarize).

NOTA: no automático. SER decide cuándo activar el logging hook
poniendo `EIDOS_DIARY=1` en el env. NUNCA loggea passwords ni
secretos (filtro de tokens conocidos).

Uso:
  python3 -m core.eidos_diary add --role ser --content "hola eidos"
  python3 -m core.eidos_diary add --role eidos --content "hola SER"
  python3 -m core.eidos_diary list --days 7
  python3 -m core.eidos_diary search "túnel"
  python3 -m core.eidos_diary summarize --date 2026-05-22
  python3 -m core.eidos_diary stats
  python3 -m core.eidos_diary self-test
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
import time
from pathlib import Path
from typing import Optional

DIARY_DIR = Path(os.path.expanduser("~/.eidos/conversations"))
SECRET_PATTERNS = (
    re.compile(r"sk-[a-zA-Z0-9_-]{16,}"),
    re.compile(r"ghp_[a-zA-Z0-9]{20,}"),
    re.compile(r"gsk_[a-zA-Z0-9]{20,}"),
    re.compile(r"hf_[a-zA-Z0-9]{20,}"),
    re.compile(r"Bearer\s+[a-zA-Z0-9._-]{20,}"),
    re.compile(r"[0-9]{9,10}:AAE[a-zA-Z0-9_-]{30,}"),
    re.compile(r"--password[ =]+[\"']?[^\s\"']+[\"']?"),
)


def _scrub(text: str) -> str:
    """Reemplaza patrones sensibles por [REDACTED]."""
    if not text:
        return text
    for pat in SECRET_PATTERNS:
        text = pat.sub("[REDACTED]", text)
    return text


def _today_file(now: Optional[float] = None) -> Path:
    DIARY_DIR.mkdir(parents=True, exist_ok=True)
    date = time.strftime("%Y-%m-%d", time.localtime(now or time.time()))
    return DIARY_DIR / f"diary_{date}.jsonl"


def add(role: str, content: str,
        model: Optional[str] = None,
        provider: Optional[str] = None,
        elapsed_s: Optional[float] = None,
        kind: Optional[str] = None,
        tags: Optional[list] = None,
        session_id: Optional[str] = None) -> dict:
    """Añade una entrada al diary del día actual."""
    entry = {
        "ts": time.time(),
        "ts_iso": time.strftime("%Y-%m-%d %H:%M:%S"),
        "role": role.lower(),
        "content": _scrub(content)[:32000],
    }
    if model: entry["model"] = model
    if provider: entry["provider"] = provider
    if elapsed_s is not None: entry["elapsed_s"] = round(elapsed_s, 2)
    if kind: entry["kind"] = kind
    if tags: entry["tags"] = tags
    if session_id: entry["session_id"] = session_id
    f = _today_file()
    with open(f, "a", encoding="utf-8") as fp:
        fp.write(json.dumps(entry, ensure_ascii=False) + "\n")
    try:
        os.chmod(f, 0o600)
    except Exception:
        pass
    return entry


def _iter_files(days: int = 30) -> list[Path]:
    """Devuelve archivos diary_*.jsonl de los últimos N días."""
    if not DIARY_DIR.exists():
        return []
    cutoff = time.time() - days * 86400
    files = []
    for f in DIARY_DIR.glob("diary_*.jsonl"):
        try:
            date_str = f.stem.replace("diary_", "")
            ts = time.mktime(time.strptime(date_str, "%Y-%m-%d"))
            if ts >= cutoff:
                files.append(f)
        except Exception:
            continue
    return sorted(files)


def list_entries(days: int = 7, role: Optional[str] = None,
                 limit: int = 100) -> list[dict]:
    """Lista las últimas N entradas en los últimos N días."""
    out = []
    for f in _iter_files(days):
        for line in f.read_text(encoding="utf-8").splitlines():
            try:
                e = json.loads(line)
                if role and e.get("role") != role.lower():
                    continue
                out.append(e)
            except Exception:
                continue
    out.sort(key=lambda x: x.get("ts", 0), reverse=True)
    return out[:limit]


def search(query: str, days: int = 30, limit: int = 50) -> list[dict]:
    """Búsqueda case-insensitive de keyword en content."""
    qlow = query.lower()
    out = []
    for f in _iter_files(days):
        for line in f.read_text(encoding="utf-8").splitlines():
            try:
                e = json.loads(line)
                if qlow in (e.get("content") or "").lower():
                    out.append(e)
            except Exception:
                continue
    out.sort(key=lambda x: x.get("ts", 0), reverse=True)
    return out[:limit]


def stats(days: int = 30) -> dict:
    """Estadísticas del diary."""
    n_total = 0
    by_role: dict[str, int] = {}
    by_provider: dict[str, int] = {}
    by_date: dict[str, int] = {}
    for f in _iter_files(days):
        date = f.stem.replace("diary_", "")
        for line in f.read_text(encoding="utf-8").splitlines():
            try:
                e = json.loads(line)
                n_total += 1
                r = e.get("role", "?")
                by_role[r] = by_role.get(r, 0) + 1
                p = e.get("provider")
                if p:
                    by_provider[p] = by_provider.get(p, 0) + 1
                by_date[date] = by_date.get(date, 0) + 1
            except Exception:
                continue
    return {
        "total_entries": n_total,
        "days_with_entries": len(by_date),
        "by_role": by_role,
        "by_provider": by_provider,
        "by_date": dict(sorted(by_date.items())[-7:]),  # last 7 days
        "diary_dir": str(DIARY_DIR),
    }


def summarize_day(date_str: str, max_tokens: int = 300) -> dict:
    """Resumen vía eidos_llm.complete del día dado."""
    f = DIARY_DIR / f"diary_{date_str}.jsonl"
    if not f.exists():
        return {"ok": False, "error": f"no diary for {date_str}"}
    entries = []
    for line in f.read_text(encoding="utf-8").splitlines():
        try:
            entries.append(json.loads(line))
        except Exception:
            continue
    if not entries:
        return {"ok": False, "error": "empty diary"}
    # Construir contexto
    ctx_lines = []
    for e in entries[-50:]:
        role = e.get("role", "?")
        content = (e.get("content") or "")[:240]
        ctx_lines.append(f"[{role}] {content}")
    ctx = "\n".join(ctx_lines)[:8000]
    prompt = (
        f"Resume en 4-6 frases lo que pasó el {date_str} entre SER y EIDOS. "
        f"Identifica temas, decisiones, y aprendizajes. Diálogo:\n\n{ctx}")
    try:
        sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
        from core.eidos_llm import complete  # type: ignore
        r = complete(prompt, max_tokens=max_tokens, inject_identity=True)
        return {"ok": r.ok, "summary": r.text,
                "provider": r.provider, "model": r.model,
                "n_entries": len(entries), "date": date_str}
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "error": str(e), "n_entries": len(entries)}


# ── Self-test ──────────────────────────────────────────────────────────────

def _self_test() -> int:
    failures: list[str] = []

    def chk(name, cond, detail=""):
        m = "✅" if cond else "❌"
        print(f"{m} {name}" + (f" — {detail[:100]}" if detail else ""))
        if not cond:
            failures.append(name)

    # Aislar
    global DIARY_DIR
    orig = DIARY_DIR
    import tempfile, shutil
    tmpdir = Path(tempfile.mkdtemp(prefix="eidos-diary-"))
    DIARY_DIR = tmpdir / "conversations"

    try:
        # 1) Add entries
        e1 = add("ser", "Hola eidos, ¿cómo estás?", tags=["greeting"])
        chk("add devuelve dict con ts", "ts" in e1)
        chk("add escribe role lowercase", e1["role"] == "ser")
        add("eidos", "Hola SER, todo bien. Brain con 13266 nodos.",
            model="llama-3.1-8b-instant", provider="groq",
            elapsed_s=1.2, kind="response")
        add("ser", "Genial, ¿qué aprendiste hoy?")

        # 2) Scrubbing secretos
        e_scrub = add("ser", "Mi key es sk-abc123def456ghi789jkl012mno y otro gsk_yyyyyyyyyyyyyyyyyyyyy")
        chk("scrubbing sk- key",
            "[REDACTED]" in e_scrub["content"] and "sk-abc123" not in e_scrub["content"])
        chk("scrubbing gsk_ key",
            "[REDACTED]" in e_scrub["content"] and "gsk_yyyy" not in e_scrub["content"])

        # 3) List
        ls = list_entries(days=1)
        chk("list devuelve 4 entries", len(ls) == 4)

        # 4) Filter por role
        ser_only = list_entries(days=1, role="ser")
        chk("filter role=ser devuelve 3", len(ser_only) == 3)

        # 5) Search
        hits = search("eidos")
        chk("search 'eidos' devuelve hits", len(hits) >= 1)
        nohits = search("xyznotexisty")
        chk("search no-hits = 0", len(nohits) == 0)

        # 6) Stats
        st = stats()
        chk("stats total_entries=4", st["total_entries"] == 4)
        chk("stats by_role correcto",
            st["by_role"].get("ser", 0) == 3 and st["by_role"].get("eidos", 0) == 1)
        chk("stats by_provider tiene groq",
            st["by_provider"].get("groq", 0) == 1)

        # 7) Chmod 600
        f = _today_file()
        chk("chmod 600 en archivo diary",
            oct(f.stat().st_mode)[-3:] == "600",
            f"got {oct(f.stat().st_mode)[-3:]}")

        # 8) Content max length 32000
        long_content = "x" * 40000
        e_long = add("ser", long_content)
        chk("content truncado a 32000",
            len(e_long["content"]) == 32000)

    finally:
        DIARY_DIR = orig
        shutil.rmtree(tmpdir, ignore_errors=True)

    print("-" * 60)
    if failures:
        print(f"❌ FAIL ({len(failures)}): {failures}")
        return 1
    print("✅ SELF-TEST PASS")
    return 0


# ── CLI ────────────────────────────────────────────────────────────────────

def _cli() -> int:
    ap = argparse.ArgumentParser(prog="eidos_diary",
        description="Diario SER↔EIDOS (~/.eidos/conversations/)")
    sub = ap.add_subparsers(dest="cmd")

    sp = sub.add_parser("add")
    sp.add_argument("--role", required=True, choices=["ser", "eidos", "system"])
    sp.add_argument("--content", required=True)
    sp.add_argument("--model", default=None)
    sp.add_argument("--provider", default=None)
    sp.add_argument("--kind", default=None)
    sp.add_argument("--tags", default=None, help="coma-separados")

    sp = sub.add_parser("list")
    sp.add_argument("--days", type=int, default=7)
    sp.add_argument("--role", default=None)
    sp.add_argument("--limit", type=int, default=20)

    sp = sub.add_parser("search")
    sp.add_argument("query")
    sp.add_argument("--days", type=int, default=30)

    sp = sub.add_parser("summarize")
    sp.add_argument("--date", required=True, help="YYYY-MM-DD")

    sub.add_parser("stats")
    sub.add_parser("self-test")

    args = ap.parse_args()

    if args.cmd == "add":
        tags = [t.strip() for t in (args.tags or "").split(",") if t.strip()]
        r = add(args.role, args.content, args.model, args.provider,
                kind=args.kind, tags=tags or None)
        print(json.dumps(r, indent=2, ensure_ascii=False))
        return 0
    if args.cmd == "list":
        for e in list_entries(args.days, args.role, args.limit):
            print(f"[{e['ts_iso']}] {e['role']:<6} "
                  f"({e.get('provider','-')})  {(e['content'] or '')[:100]}")
        return 0
    if args.cmd == "search":
        for e in search(args.query, args.days):
            print(f"[{e['ts_iso']}] {e['role']}: {(e['content'] or '')[:140]}")
        return 0
    if args.cmd == "summarize":
        r = summarize_day(args.date)
        print(json.dumps(r, indent=2, ensure_ascii=False))
        return 0 if r.get("ok") else 1
    if args.cmd == "stats":
        print(json.dumps(stats(), indent=2, ensure_ascii=False))
        return 0
    if args.cmd == "self-test":
        return _self_test()
    ap.print_help()
    return 0


if __name__ == "__main__":
    raise SystemExit(_cli())
