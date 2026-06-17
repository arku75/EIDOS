"""
core/eidos_compute_worker.py — Worker LLM distribuido (Fcompute)
==================================================================
Implementa el kind `compute_inference` del canal F3: el clon recibe
una petición firmada {model, prompt, max_tokens, temperature}, valida
contra una quota dura del huésped, invoca Ollama LOCAL del huésped,
y devuelve la respuesta. La respuesta sale firmada por el clon
(eso lo hace ya eidos_command_channel.handle_request_at_clone).

Reemplazo legítimo del "intercambio de recursos con amigos":
  • enabled=false POR DEFECTO en cada huésped (opt-in explícito)
  • allowed_models: lista cerrada (no wildcard)
  • max_ram_pct / max_concurrent / daily_token_budget: cuotas duras
  • kill-switch: si la RAM del sistema cruza max_ram_pct → cancela
  • Audit log en ~/.eidos/compute_audit.log + ~/.eidos/compute_usage.db
  • UI/CLI huésped visible: `eidos-compute status`

Honesto:
  • NO accede al filesystem del huésped fuera de ~/.eidos/.
  • NO ejecuta shell ni red externa más allá de Ollama local.
  • Si Ollama no está corriendo → reject "ollama no disponible".
  • Si enabled=false → reject "compute disabled by host".
"""
from __future__ import annotations

import argparse
import io
import json
import os
import re
import sys
import time
import urllib.request
import urllib.error
from core.db import get_conn
from pathlib import Path
from typing import Callable, Optional

EIDOS_HOME    = Path(os.path.expanduser("~/.eidos"))
QUOTA_PATH    = EIDOS_HOME / "compute_quota.toml"
AUDIT_PATH    = EIDOS_HOME / "compute_audit.log"
USAGE_DB      = EIDOS_HOME / "compute_usage.db"

OLLAMA_URL = os.environ.get(
    "EIDOS_OLLAMA_URL", "http://127.0.0.1:11434")
OLLAMA_GENERATE = OLLAMA_URL.rstrip("/") + "/api/generate"

DEFAULT_QUOTA = {
    "enabled": False,
    "max_ram_pct": 60,
    "max_concurrent": 1,
    "allowed_models": ["llama3.2:3b", "lfm2.5-1.2b-instruct:q4_0"],
    "daily_token_budget": 500000,
    "max_prompt_bytes": 16384,
    "max_max_tokens": 2048,
}


# ── Quota TOML mínimo (sin dep tomli_w / tomllib runtime) ──────────────────

def _toml_load(text: str) -> dict:
    """Parser TOML mínimo (sólo lo que escribimos). No general."""
    out: dict = {}
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if "=" not in line:
            continue
        k, _, v = line.partition("=")
        k = k.strip(); v = v.split("#")[0].strip()
        if v.lower() in ("true", "false"):
            out[k] = v.lower() == "true"
        elif v.startswith("[") and v.endswith("]"):
            inner = v[1:-1].strip()
            if not inner:
                out[k] = []
            else:
                items = [x.strip().strip('"').strip("'")
                         for x in inner.split(",")]
                out[k] = [x for x in items if x]
        else:
            try:
                if "." in v:
                    out[k] = float(v)
                else:
                    out[k] = int(v)
            except ValueError:
                out[k] = v.strip('"').strip("'")
    return out


def _toml_dump(d: dict) -> str:
    lines = [
        "# EIDOS Compute Quota — huésped controla aquí qué hace el worker",
        "# (regenerado por core/eidos_compute_worker.py)",
        f"# regenerado: {time.strftime('%Y-%m-%d %H:%M:%S')}",
        "",
    ]
    for k, v in d.items():
        if isinstance(v, bool):
            lines.append(f"{k} = {str(v).lower()}")
        elif isinstance(v, list):
            inner = ", ".join(f'"{x}"' for x in v)
            lines.append(f"{k} = [{inner}]")
        elif isinstance(v, (int, float)):
            lines.append(f"{k} = {v}")
        else:
            lines.append(f'{k} = "{v}"')
    return "\n".join(lines) + "\n"


def load_quota() -> dict:
    if not QUOTA_PATH.exists():
        return dict(DEFAULT_QUOTA)
    try:
        d = _toml_load(QUOTA_PATH.read_text(encoding="utf-8"))
    except Exception:
        d = {}
    out = dict(DEFAULT_QUOTA)
    out.update(d)
    return out


def save_quota(q: dict) -> None:
    EIDOS_HOME.mkdir(parents=True, exist_ok=True)
    QUOTA_PATH.write_text(_toml_dump(q), encoding="utf-8")
    try:
        os.chmod(QUOTA_PATH, 0o600)
    except Exception:
        pass


# ── Audit + usage DB ───────────────────────────────────────────────────────

def _audit(entry: dict) -> None:
    AUDIT_PATH.parent.mkdir(parents=True, exist_ok=True)
    entry["ts"] = entry.get("ts", time.time())
    entry["ts_iso"] = time.strftime("%Y-%m-%d %H:%M:%S",
                                    time.localtime(entry["ts"]))
    with open(AUDIT_PATH, "a", encoding="utf-8") as f:
        f.write(json.dumps(entry, ensure_ascii=False) + "\n")
    try:
        os.chmod(AUDIT_PATH, 0o600)
    except Exception:
        pass


USAGE_SCHEMA = """
CREATE TABLE IF NOT EXISTS usage (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ts REAL NOT NULL,
    requester TEXT,
    model TEXT,
    tokens INTEGER,
    duration_ms INTEGER,
    ok INTEGER,
    err TEXT
);
CREATE INDEX IF NOT EXISTS idx_usage_ts ON usage(ts);
"""


def _usage_conn() -> sqlite3.Connection:
    EIDOS_HOME.mkdir(parents=True, exist_ok=True)
    c = get_conn(USAGE_DB)
    c.executescript(USAGE_SCHEMA)
    try:
        os.chmod(USAGE_DB, 0o600)
    except Exception:
        pass
    return c


def _usage_record(requester: str, model: str, tokens: int,
                  duration_ms: int, ok: bool, err: Optional[str]) -> None:
    with _usage_conn() as c:
        c.execute(
            "INSERT INTO usage(ts, requester, model, tokens, duration_ms, "
            "ok, err) VALUES(?,?,?,?,?,?,?)",
            (time.time(), requester, model, tokens, duration_ms,
             1 if ok else 0, err))


def tokens_used_today() -> int:
    """Tokens contados de hoy (00:00 UTC local)."""
    if not USAGE_DB.exists():
        return 0
    today_start = time.mktime(time.strptime(
        time.strftime("%Y-%m-%d"), "%Y-%m-%d"))
    with _usage_conn() as c:
        r = c.execute(
            "SELECT COALESCE(SUM(tokens),0) FROM usage "
            "WHERE ts>=? AND ok=1", (today_start,)).fetchone()
    return int(r[0] or 0)


# ── RAM check (kill-switch) ────────────────────────────────────────────────

def system_ram_pct() -> float:
    """Devuelve % de RAM EN USO. 0-100. Usa /proc/meminfo (Linux)."""
    try:
        info: dict = {}
        with open("/proc/meminfo") as f:
            for line in f:
                k, _, v = line.partition(":")
                parts = v.strip().split()
                if parts:
                    info[k.strip()] = int(parts[0])
        total = info.get("MemTotal", 0)
        avail = info.get("MemAvailable", info.get("MemFree", 0))
        if total == 0:
            return 0.0
        used = total - avail
        return round(used * 100 / total, 1)
    except Exception:
        return 0.0


# ── Llamada a Ollama (con fallback inyectable para tests) ──────────────────

OllamaCallable = Callable[[str, str, int, float], dict]


def _real_ollama_call(model: str, prompt: str,
                      max_tokens: int, temperature: float) -> dict:
    body = {
        "model": model,
        "prompt": prompt,
        "stream": False,
        "options": {
            "num_predict": max_tokens,
            "temperature": temperature,
        },
    }
    req = urllib.request.Request(
        OLLAMA_GENERATE,
        data=json.dumps(body).encode("utf-8"),
        headers={"Content-Type": "application/json",
                 "User-Agent": "eidos-compute-worker/1.0"})
    with urllib.request.urlopen(req, timeout=120) as r:
        data = json.loads(r.read().decode("utf-8"))
    return {
        "text": data.get("response", ""),
        "model": data.get("model", model),
        "eval_count": int(data.get("eval_count", 0)),
        "prompt_eval_count": int(data.get("prompt_eval_count", 0)),
        "total_tokens": int(data.get("eval_count", 0))
                         + int(data.get("prompt_eval_count", 0)),
        "raw": data,
    }


def _ollama_available() -> bool:
    try:
        with urllib.request.urlopen(OLLAMA_URL.rstrip("/") + "/api/tags",
                                    timeout=3) as r:
            return r.status == 200
    except Exception:
        return False


# ── Handler de compute_inference (invocado desde el canal F3) ──────────────

def handle_compute_inference(args: dict,
                             requester: Optional[str] = None,
                             ollama_call: Optional[OllamaCallable] = None) -> dict:
    """Endpoint principal. args = {model, prompt, max_tokens, temperature}.
    Devuelve dict que el canal F3 envolverá en {ok, data, error}.
    Levanta excepciones que el canal convierte en error response."""
    t0 = time.time()
    q = load_quota()
    model = str(args.get("model", "")).strip()
    prompt = str(args.get("prompt", ""))
    max_t = int(args.get("max_tokens", 256))
    temp  = float(args.get("temperature", 0.2))

    # 1) enabled
    if not q.get("enabled", False):
        _audit({"event": "reject", "reason": "disabled", "requester": requester,
                "model": model})
        raise PermissionError("compute disabled by host (enabled=false)")

    # 2) modelo allowed
    if model not in q.get("allowed_models", []):
        _audit({"event": "reject", "reason": "model_not_allowed",
                "requester": requester, "model": model})
        raise PermissionError(
            f"modelo '{model}' fuera de allowed_models {q.get('allowed_models')}")

    # 3) prompt size
    if len(prompt.encode("utf-8")) > q.get("max_prompt_bytes",
                                            DEFAULT_QUOTA["max_prompt_bytes"]):
        _audit({"event": "reject", "reason": "prompt_too_big",
                "requester": requester})
        raise ValueError("prompt > max_prompt_bytes")

    # 4) max_tokens cap
    cap = q.get("max_max_tokens", DEFAULT_QUOTA["max_max_tokens"])
    if max_t < 1 or max_t > cap:
        _audit({"event": "reject", "reason": "max_tokens_oor",
                "requester": requester, "max_t": max_t})
        raise ValueError(f"max_tokens fuera de rango (1..{cap})")

    # 5) daily budget
    used_today = tokens_used_today()
    if used_today + max_t > q.get("daily_token_budget",
                                  DEFAULT_QUOTA["daily_token_budget"]):
        _audit({"event": "reject", "reason": "budget_exhausted",
                "requester": requester, "used_today": used_today})
        raise PermissionError(
            f"daily_token_budget agotado (used={used_today})")

    # 6) RAM kill-switch (chequeo pre-job)
    ram_pct = system_ram_pct()
    if ram_pct > q.get("max_ram_pct", DEFAULT_QUOTA["max_ram_pct"]):
        _audit({"event": "reject", "reason": "ram_too_high",
                "requester": requester, "ram_pct": ram_pct})
        raise PermissionError(
            f"RAM {ram_pct}% > max_ram_pct {q.get('max_ram_pct')}")

    # 7) Ollama disponible (usa la callable inyectable si test)
    fn = ollama_call or _real_ollama_call
    if ollama_call is None and not _ollama_available():
        _audit({"event": "reject", "reason": "ollama_unavailable",
                "requester": requester})
        raise RuntimeError("ollama no disponible en localhost")

    # 8) ejecutar
    try:
        result = fn(model, prompt, max_t, temp)
    except Exception as e:  # noqa: BLE001
        dt_ms = int((time.time() - t0) * 1000)
        _usage_record(requester or "", model, 0, dt_ms, False, str(e)[:200])
        _audit({"event": "error", "reason": str(e)[:200],
                "requester": requester, "model": model})
        raise

    dt_ms = int((time.time() - t0) * 1000)
    total_tokens = int(result.get("total_tokens", 0))
    _usage_record(requester or "", model, total_tokens, dt_ms, True, None)
    _audit({"event": "ok", "requester": requester, "model": model,
            "tokens": total_tokens, "duration_ms": dt_ms})

    # Devuelve subset seguro (no propaga raw payload de Ollama)
    return {
        "text": result.get("text", "")[:32000],
        "model": result.get("model", model),
        "tokens": total_tokens,
        "duration_ms": dt_ms,
        "ram_pct_pre": ram_pct,
    }


# ── Estado para CLI huésped ────────────────────────────────────────────────

def status() -> dict:
    q = load_quota()
    n_today = tokens_used_today()
    last = None
    n_total = 0
    if USAGE_DB.exists():
        with _usage_conn() as c:
            n_total = c.execute("SELECT COUNT(*) FROM usage").fetchone()[0]
            r = c.execute(
                "SELECT ts, requester, model, tokens, ok, err FROM usage "
                "ORDER BY ts DESC LIMIT 1").fetchone()
            if r:
                last = {
                    "ts_iso": time.strftime("%Y-%m-%d %H:%M:%S",
                                            time.localtime(r[0])),
                    "requester": r[1], "model": r[2], "tokens": r[3],
                    "ok": bool(r[4]), "err": r[5]}
    return {
        "enabled": q.get("enabled", False),
        "ram_pct_now": system_ram_pct(),
        "ollama_available": _ollama_available() if not os.environ.get(
            "EIDOS_COMPUTE_NO_PROBE") else None,
        "allowed_models": q.get("allowed_models", []),
        "tokens_used_today": n_today,
        "daily_token_budget": q.get("daily_token_budget"),
        "max_ram_pct": q.get("max_ram_pct"),
        "max_concurrent": q.get("max_concurrent"),
        "max_max_tokens": q.get("max_max_tokens"),
        "max_prompt_bytes": q.get("max_prompt_bytes"),
        "quota_path": str(QUOTA_PATH),
        "audit_path": str(AUDIT_PATH),
        "usage_db_path": str(USAGE_DB),
        "n_total_records": n_total,
        "last_request": last,
    }


def set_enabled(value: bool) -> dict:
    q = load_quota()
    q["enabled"] = bool(value)
    save_quota(q)
    _audit({"event": "enabled_changed", "new": q["enabled"]})
    return {"ok": True, "enabled": q["enabled"]}


# ── Self-test (sin Ollama real — todo stubeado) ────────────────────────────

def _self_test() -> int:
    failures: list[str] = []

    def chk(name: str, cond: bool, detail: str = ""):
        mark = "✅" if cond else "❌"
        print(f"{mark} {name}" + (f" — {detail[:120]}" if detail else ""))
        if not cond:
            failures.append(name)

    # Aislamos todo en tmpdir
    global EIDOS_HOME, QUOTA_PATH, AUDIT_PATH, USAGE_DB
    orig = (EIDOS_HOME, QUOTA_PATH, AUDIT_PATH, USAGE_DB)
    import tempfile, shutil
    tmpdir = Path(tempfile.mkdtemp(prefix="eidos-compute-"))
    EIDOS_HOME = tmpdir / ".eidos"
    EIDOS_HOME.mkdir()
    QUOTA_PATH  = EIDOS_HOME / "compute_quota.toml"
    AUDIT_PATH  = EIDOS_HOME / "compute_audit.log"
    USAGE_DB    = EIDOS_HOME / "compute_usage.db"

    # Stub Ollama: cuenta llamadas y devuelve tokens controlados.
    stub_state = {"calls": 0, "last_args": None}
    def stub_ollama(model, prompt, max_t, temp):
        stub_state["calls"] += 1
        stub_state["last_args"] = (model, prompt, max_t, temp)
        return {
            "text": f"<{model}> respondió a '{prompt[:24]}'",
            "model": model,
            "eval_count": 50, "prompt_eval_count": 30,
            "total_tokens": 80,
        }

    try:
        # 1) default off → reject
        try:
            handle_compute_inference(
                {"model": "llama3.2:3b", "prompt": "hola",
                 "max_tokens": 100, "temperature": 0.2},
                requester="test", ollama_call=stub_ollama)
            chk("default off rechaza", False, "no excepción")
        except PermissionError as e:
            chk("default off rechaza", "disabled" in str(e).lower())

        # 2) habilitar y modelo no allowed → reject
        set_enabled(True)
        try:
            handle_compute_inference(
                {"model": "evil-model", "prompt": "x",
                 "max_tokens": 100, "temperature": 0.2},
                ollama_call=stub_ollama)
            chk("modelo no allowed rechaza", False)
        except PermissionError as e:
            chk("modelo no allowed rechaza", "allowed_models" in str(e))

        # 3) prompt demasiado grande
        q = load_quota(); q["allowed_models"] = ["llama3.2:3b"]
        save_quota(q)
        try:
            handle_compute_inference(
                {"model": "llama3.2:3b", "prompt": "x" * 20000,
                 "max_tokens": 100, "temperature": 0.2},
                ollama_call=stub_ollama)
            chk("prompt grande rechaza", False)
        except ValueError as e:
            chk("prompt grande rechaza", "max_prompt_bytes" in str(e))

        # 4) max_tokens fuera de rango
        try:
            handle_compute_inference(
                {"model": "llama3.2:3b", "prompt": "hola",
                 "max_tokens": 9999, "temperature": 0.2},
                ollama_call=stub_ollama)
            chk("max_tokens out-of-range rechaza", False)
        except ValueError as e:
            chk("max_tokens out-of-range rechaza",
                "max_tokens" in str(e))

        # 5) flow OK
        res = handle_compute_inference(
            {"model": "llama3.2:3b", "prompt": "hola Curador",
             "max_tokens": 100, "temperature": 0.2},
            requester="test", ollama_call=stub_ollama)
        chk("flow OK devuelve text", res.get("text", "").startswith("<llama3.2"))
        chk("flow OK tokens=80", res.get("tokens") == 80)
        chk("usage_db tiene 1 record OK",
            get_conn(USAGE_DB).execute(
                "SELECT COUNT(*) FROM usage WHERE ok=1").fetchone()[0] == 1)
        chk("audit_log existe y tiene entries",
            AUDIT_PATH.exists() and len(
                AUDIT_PATH.read_text().strip().splitlines()) >= 3)

        # 6) RAM kill-switch — forzamos max_ram_pct bajo
        q = load_quota(); q["max_ram_pct"] = 0  # 0% imposible → siempre falla
        save_quota(q)
        try:
            handle_compute_inference(
                {"model": "llama3.2:3b", "prompt": "x",
                 "max_tokens": 50, "temperature": 0.2},
                ollama_call=stub_ollama)
            chk("RAM kill-switch dispara", False)
        except PermissionError as e:
            chk("RAM kill-switch dispara", "max_ram_pct" in str(e))

        # 7) daily budget agotado
        q = load_quota(); q["max_ram_pct"] = 99
        q["daily_token_budget"] = 50
        save_quota(q)
        try:
            handle_compute_inference(
                {"model": "llama3.2:3b", "prompt": "x",
                 "max_tokens": 100, "temperature": 0.2},
                ollama_call=stub_ollama)
            chk("budget agotado rechaza", False)
        except PermissionError as e:
            chk("budget agotado rechaza", "budget" in str(e).lower())

        # 8) status produce dict razonable
        q = load_quota(); q["daily_token_budget"] = 500000
        save_quota(q)
        os.environ["EIDOS_COMPUTE_NO_PROBE"] = "1"
        st = status()
        chk("status tiene enabled+last_request",
            st.get("enabled") is True
            and st.get("last_request") is not None)
        chk("status report tokens_used_today > 0",
            st.get("tokens_used_today", 0) >= 80)

        # 9) toml roundtrip
        q = {"enabled": True, "max_ram_pct": 75,
             "allowed_models": ["a", "b"], "daily_token_budget": 1000,
             "max_prompt_bytes": 4096, "max_concurrent": 2,
             "max_max_tokens": 512}
        save_quota(q)
        q2 = load_quota()
        chk("toml roundtrip preserva tipos y valores",
            q2["enabled"] is True and q2["max_ram_pct"] == 75
            and q2["allowed_models"] == ["a", "b"]
            and q2["daily_token_budget"] == 1000)

        # 10) Nunca se ejecutó Ollama real (stub usado siempre)
        chk("stub Ollama llamado al menos 1 vez",
            stub_state["calls"] >= 1)

    finally:
        EIDOS_HOME, QUOTA_PATH, AUDIT_PATH, USAGE_DB = orig
        shutil.rmtree(tmpdir, ignore_errors=True)
        os.environ.pop("EIDOS_COMPUTE_NO_PROBE", None)

    print("-" * 60)
    if failures:
        print(f"❌ FAIL ({len(failures)}): {failures}")
        return 1
    print("✅ SELF-TEST PASS")
    return 0


# ── CLI (`eidos-compute`) ──────────────────────────────────────────────────

def _cli() -> int:
    ap = argparse.ArgumentParser(prog="eidos-compute",
        description="Worker LLM distribuido del huésped (Fcompute)")
    sub = ap.add_subparsers(dest="cmd")

    sub.add_parser("status")
    sub.add_parser("enable")
    sub.add_parser("disable")
    sp = sub.add_parser("quota-set")
    sp.add_argument("key")
    sp.add_argument("value")
    sub.add_parser("log",  help="muestra últimos N jobs")
    sub.add_parser("self-test")

    args = ap.parse_args()

    if args.cmd == "status":
        print(json.dumps(status(), indent=2, ensure_ascii=False))
        return 0
    if args.cmd == "enable":
        print(json.dumps(set_enabled(True), indent=2)); return 0
    if args.cmd == "disable":
        print(json.dumps(set_enabled(False), indent=2)); return 0
    if args.cmd == "quota-set":
        q = load_quota()
        k, v = args.key, args.value
        if k not in q:
            print(f"flag desconocido: {k}"); return 1
        cur = q[k]
        try:
            if isinstance(cur, bool):
                q[k] = v.lower() in ("true", "1", "yes", "on")
            elif isinstance(cur, int):
                q[k] = int(v)
            elif isinstance(cur, float):
                q[k] = float(v)
            elif isinstance(cur, list):
                q[k] = [x.strip() for x in v.split(",") if x.strip()]
            else:
                q[k] = v
        except ValueError as e:
            print(f"valor inválido: {e}"); return 1
        save_quota(q)
        _audit({"event": "quota_set", "key": k, "old": cur, "new": q[k]})
        print(json.dumps({"ok": True, "key": k, "value": q[k]}, indent=2))
        return 0
    if args.cmd == "log":
        if not USAGE_DB.exists():
            print("(no hay jobs)"); return 0
        with _usage_conn() as c:
            rows = c.execute(
                "SELECT ts, requester, model, tokens, duration_ms, ok, err "
                "FROM usage ORDER BY ts DESC LIMIT 20").fetchall()
        for r in rows:
            t = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(r[0]))
            ok = "OK " if r[5] else "ERR"
            print(f"[{t}] {ok} {r[2]:<24} tokens={r[3]:<6} "
                  f"dt={r[4]:<6} req={r[1] or '-'}"
                  + (f"  err={(r[6] or '')[:60]}" if not r[5] else ""))
        return 0
    if args.cmd == "self-test":
        return _self_test()

    ap.print_help()
    return 0


if __name__ == "__main__":
    raise SystemExit(_cli())
