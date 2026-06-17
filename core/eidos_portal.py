"""
core/eidos_portal.py — Portal mode CLI desde Kali → clon remoto (F9)
====================================================================
CLI shell-like que SER abre desde Kali contra un clon registrado. Cada
comando se traduce a un kind del canal F3 EXISTENTE (allowlist actual,
NO se amplía):

    ls <path>     → list_dir
    cat <path>    → read_file (max 64 KB)
    info          → system_info
    ping          → ping
    pwd           → cwd local de la sesión (tracking)
    cd <path>     → cambia cwd local de la sesión (valida con list_dir)
    help          → muestra comandos
    exit / quit / Ctrl-D → cerrar sesión

Honesto:
  • Mientras el transporte vivo (autossh, F5+) no esté terminado, el
    portal ejecuta build_request → handle_request_at_clone → verify
    LOCALMENTE en el mismo PC. Esto valida el flujo cripto/firma E2E
    sin túnel todavía. Cuando F5 esté listo se cambia handle por la
    llamada vía el túnel reverso.
  • NO se amplía la SHELL_ALLOWLIST. NO se ejecuta run_shell. NO se
    expone escritura remota (write_file no está en allowlist).
  • Resuelve por clone_id exacto o friendly_name (primer match en
    registry.db).

Uso:
  python3 -m core.eidos_portal --list
  python3 -m core.eidos_portal <clone_id_or_friendly_name>
  python3 -m core.eidos_portal self-test
"""
from __future__ import annotations

import argparse
import io
import json
import os
import shlex
import sqlite3
import sys
import time
from pathlib import Path
from typing import Callable, Iterable, Optional, TextIO
from core.db import get_conn, get_conn_ctx

HUB_DIR  = Path(os.path.expanduser("~/.eidos/hub"))
REG_DB   = HUB_DIR / "registry.db"

EIDOS_REPO = "/home/ser/EIDOS"
if EIDOS_REPO not in sys.path:
    sys.path.insert(0, EIDOS_REPO)


# ── Resolver clone por friendly_name o clone_id ────────────────────────────

def _list_clones() -> list[dict]:
    if not REG_DB.exists():
        return []
    with get_conn_ctx(REG_DB) as c:
        rows = c.execute(
            "SELECT clone_id, friendly_name, os_info, registered_at, "
            "last_seen, revoked, COALESCE(tunnel_port, 0) "
            "FROM clones ORDER BY registered_at").fetchall()
    return [{"clone_id": r[0], "friendly_name": r[1], "os_info": r[2],
             "registered_at": r[3], "last_seen": r[4],
             "revoked": bool(r[5]), "tunnel_port": r[6] or None}
            for r in rows]


def resolve(target: str) -> Optional[dict]:
    rows = _list_clones()
    # Match por clone_id exacto
    for r in rows:
        if r["clone_id"] == target:
            return r
    # Match por friendly_name exacto
    for r in rows:
        if r["friendly_name"] == target:
            return r
    # Match parcial al final de clone_id (uuid corto)
    for r in rows:
        if r["clone_id"].endswith(target):
            return r
    return None


# ── Wrapping del canal F3 (build → exec → verify) ──────────────────────────

def _default_exec(clone_id: str, kind: str, args: dict) -> dict:
    """Ejecuta el comando en el PC local (asume que este PC tiene la
    identidad del clon cargada — caso típico cuando SER prueba el
    portal en su propio Kali contra su propio clon). En F5+ esto será
    reemplazado por una llamada sobre el túnel reverso al clon real."""
    from core import eidos_command_channel as cc  # type: ignore
    req, sig = cc.build_request(clone_id, kind, args)
    resp, rsig = cc.handle_request_at_clone(req, sig)
    verified = cc.verify_response(clone_id, resp, rsig)
    return {"req": req, "resp": resp, "verified": verified}


# Tipo del callable que se puede inyectar en tests
ExecFn = Callable[[str, str, dict], dict]


# ── Sesión interactiva ─────────────────────────────────────────────────────

HELP_TEXT = """
Comandos del portal:
  ls [path]      → lista contenido de directorio (default: cwd)
  cat <path>     → muestra archivo (max 64KB)
  info           → system_info del clon
  ping           → ping firmado
  pwd            → muestra el cwd local de la sesión
  cd <path>      → cambia el cwd local (validado con list_dir)
  help           → este mensaje
  exit / quit    → cerrar sesión (también Ctrl-D)

Nota: el portal usa SOLO la allowlist actual del canal F3.
No hay run_shell, no hay write_file, no hay get_cookies.
"""


def _format_list_dir(data) -> str:
    """data es un dict con clave 'entries' o una lista directa."""
    entries = data.get("entries") if isinstance(data, dict) else data
    if not entries:
        return "(directorio vacío o no accesible)"
    lines = []
    for e in entries:
        if isinstance(e, dict):
            name = e.get("name", "?")
            kind = e.get("type", "f")
            sz = e.get("size", "")
            lines.append(f"  {kind} {name}{' ('+str(sz)+')' if sz else ''}")
        else:
            lines.append(f"  {e}")
    return "\n".join(lines)


def _format_system_info(data) -> str:
    if not isinstance(data, dict):
        return str(data)
    return "\n".join(f"  {k}: {v}" for k, v in data.items())


def _resolve_path(cwd: str, p: str) -> str:
    """Resuelve un path relativo contra el cwd de la sesión."""
    if not p or p == ".":
        return cwd
    if p.startswith("/"):
        return os.path.normpath(p)
    return os.path.normpath(os.path.join(cwd, p))


def session_loop(clone: dict,
                 inp: TextIO, out: TextIO,
                 exec_fn: Optional[ExecFn] = None,
                 max_commands: int = 0,
                 initial_cwd: str = "/") -> dict:
    """Sesión interactiva. Devuelve dict con n_commands y last_status.
    max_commands>0 cierra automáticamente tras N comandos (usado por tests)."""
    cid = clone["clone_id"]
    fname = clone.get("friendly_name") or cid[:12]
    host = (clone.get("os_info") or "?").split()[0][:24] if clone.get("os_info") else "?"
    cwd = initial_cwd
    exec_fn = exec_fn or _default_exec

    out.write(f"EIDOS portal — clon: {fname} ({cid[:18]}…) os={host}\n")
    out.write("Comandos: ls cat info ping pwd cd help exit · "
              "allowlist F3 actual (sin run_shell)\n\n")

    n = 0
    last_status = "ok"
    while True:
        prompt = f"[clon:{fname}@{host}:{cwd}]$ "
        out.write(prompt); out.flush()
        try:
            line = inp.readline()
        except KeyboardInterrupt:
            out.write("\n(interrumpido)\n")
            break
        if line == "":   # EOF / Ctrl-D
            out.write("\n(EOF)\n")
            break
        cmdline = line.strip()
        if not cmdline:
            continue
        n += 1

        try:
            parts = shlex.split(cmdline)
        except ValueError as e:
            out.write(f"  (parse error: {e})\n")
            continue
        cmd = parts[0].lower()
        rest = parts[1:]

        if cmd in {"exit", "quit"}:
            out.write("(saliendo)\n")
            break
        if cmd == "help":
            out.write(HELP_TEXT)
            continue
        if cmd == "pwd":
            out.write(f"  {cwd}\n")
            continue

        if cmd == "cd":
            target = rest[0] if rest else "/"
            new_cwd = _resolve_path(cwd, target)
            # Validar con list_dir
            res = exec_fn(cid, "list_dir", {"path": new_cwd, "max_entries": 1})
            ok = res["resp"].get("ok") and res["verified"]
            if ok:
                cwd = new_cwd
                out.write(f"  → {cwd}\n")
                last_status = "ok"
            else:
                last_status = "err"
                out.write(f"  (no se pudo entrar en {new_cwd}: "
                          f"{res['resp'].get('error') or 'verify fallo'})\n")
            continue

        if cmd == "ls":
            target = _resolve_path(cwd, rest[0] if rest else ".")
            res = exec_fn(cid, "list_dir",
                          {"path": target, "max_entries": 200})
            if res["resp"].get("ok") and res["verified"]:
                out.write(_format_list_dir(res["resp"].get("data")) + "\n")
                last_status = "ok"
            else:
                last_status = "err"
                out.write(f"  (error: {res['resp'].get('error')} | "
                          f"verify={res['verified']})\n")
            continue

        if cmd == "cat":
            if not rest:
                out.write("  cat <path>\n"); continue
            target = _resolve_path(cwd, rest[0])
            res = exec_fn(cid, "read_file", {"path": target})
            if res["resp"].get("ok") and res["verified"]:
                data = res["resp"].get("data") or {}
                content = data.get("content", "") if isinstance(data, dict) else str(data)
                out.write(content)
                if not content.endswith("\n"):
                    out.write("\n")
                last_status = "ok"
            else:
                last_status = "err"
                out.write(f"  (error: {res['resp'].get('error')} | "
                          f"verify={res['verified']})\n")
            continue

        if cmd == "info":
            res = exec_fn(cid, "system_info", {})
            if res["resp"].get("ok") and res["verified"]:
                out.write(_format_system_info(res["resp"].get("data")) + "\n")
                last_status = "ok"
            else:
                last_status = "err"
                out.write(f"  (error: {res['resp'].get('error')})\n")
            continue

        if cmd == "ping":
            res = exec_fn(cid, "ping", {})
            if res["resp"].get("ok") and res["verified"]:
                rt = res["resp"].get("data", {}) if isinstance(res["resp"].get("data"), dict) else {}
                out.write(f"  pong (verified=True) {rt}\n")
                last_status = "ok"
            else:
                last_status = "err"
                out.write(f"  (ping error: {res['resp'].get('error')})\n")
            continue

        # Comandos prohibidos explicitos (ayudar al usuario)
        if cmd in {"run", "sh", "bash", "rm", "mv", "cp",
                   "wget", "curl", "nc", "ssh",
                   "get_cookies", "get_file", "write"}:
            out.write(f"  (comando '{cmd}' NO permitido en portal — "
                      "línea disciplinada / allowlist F3)\n")
            last_status = "blocked"
            continue

        out.write(f"  (desconocido: '{cmd}'; escribe 'help')\n")
        last_status = "unknown"

        if max_commands and n >= max_commands:
            out.write("(max_commands alcanzado)\n")
            break

    return {"commands_run": n, "last_status": last_status, "final_cwd": cwd}


# ── Self-test (no llama al canal F3 real — exec_fn mockeado) ───────────────

def _self_test() -> int:
    failures: list[str] = []

    def chk(name: str, cond: bool, detail: str = ""):
        mark = "✅" if cond else "❌"
        print(f"{mark} {name}" + (f" — {detail[:120]}" if detail else ""))
        if not cond:
            failures.append(name)

    # Mock clone (no toca registry.db)
    fake_clone = {
        "clone_id": "clone_self_test_0000",
        "friendly_name": "selftest",
        "os_info": "Linux Test 6.0",
        "registered_at": time.time(),
        "last_seen": time.time(),
        "revoked": False,
        "tunnel_port": None,
    }

    # Exec stub determinista. Cada kind devuelve algo válido.
    call_log: list[tuple[str, dict]] = []

    def stub_exec(cid: str, kind: str, args: dict) -> dict:
        call_log.append((kind, args))
        if kind == "ping":
            return {"req": {}, "resp": {"ok": True, "data": {"echo": "pong"}},
                    "verified": True}
        if kind == "system_info":
            return {"req": {}, "resp": {"ok": True,
                    "data": {"os": "Linux", "py": "3.13", "host": "kali"}},
                    "verified": True}
        if kind == "list_dir":
            path = args.get("path", "")
            if path.startswith("/nope"):
                return {"req": {}, "resp": {"ok": False,
                        "error": "ENOENT"}, "verified": True}
            return {"req": {}, "resp": {"ok": True, "data":
                    {"entries": [
                        {"name": "etc", "type": "d", "size": ""},
                        {"name": "home", "type": "d", "size": ""},
                        {"name": "README.md", "type": "f", "size": 1234}]}},
                    "verified": True}
        if kind == "read_file":
            return {"req": {}, "resp": {"ok": True,
                    "data": {"content": "hola desde el clon\n"}},
                    "verified": True}
        return {"req": {}, "resp": {"ok": False, "error": "kind no permitido"},
                "verified": False}

    # 1) Comandos básicos
    scripted = "\n".join([
        "ping",
        "info",
        "ls",
        "ls /home",
        "cat /tmp/x.txt",
        "pwd",
        "cd /etc",
        "pwd",
        "cd /nope/no/existe",
        "pwd",                       # no debe haber cambiado
        "run rm -rf /",              # prohibido
        "wget http://x",             # prohibido
        "get_cookies firefox",       # prohibido
        "noexisteesto",
        "exit",
    ]) + "\n"
    inp = io.StringIO(scripted)
    out = io.StringIO()
    res = session_loop(fake_clone, inp, out, exec_fn=stub_exec)
    output = out.getvalue()

    chk("session corrió comandos", res["commands_run"] >= 12)
    chk("ping → pong verified", "pong" in output and "verified=True" in output)
    chk("info muestra os/host", "os: Linux" in output and "host: kali" in output)
    chk("ls muestra entries", "etc" in output and "README.md" in output)
    chk("cat muestra content", "hola desde el clon" in output)
    chk("pwd inicial '/'", "[clon:selftest" in output and "/]$" in output)
    chk("cd /etc cambia cwd", ":/etc]$" in output)
    chk("cd /nope falla y conserva cwd", "no se pudo entrar" in output)
    chk("'run rm -rf' BLOQUEADO", "NO permitido" in output and "'run'" in output)
    chk("'wget' BLOQUEADO", "NO permitido" in output and "'wget'" in output)
    chk("'get_cookies' BLOQUEADO",
        "NO permitido" in output and "'get_cookies'" in output)
    chk("comando desconocido reporta",
        "desconocido: 'noexisteesto'" in output)
    chk("kinds usados solo de allowlist",
        {k for k, _ in call_log} <= {"ping", "system_info",
                                     "list_dir", "read_file"},
        f"used={ {k for k, _ in call_log} }")
    chk("NO usa kind 'run_shell'",
        "run_shell" not in {k for k, _ in call_log})
    chk("NO usa kind 'write_file' ni 'get_cookies' ni 'get_file'",
        not {"write_file", "get_cookies", "get_file"} & {k for k,_ in call_log})

    # 2) EOF cierra limpio
    inp = io.StringIO("ping\n")  # sin exit, sin newline final extra
    out = io.StringIO()
    res = session_loop(fake_clone, inp, out, exec_fn=stub_exec)
    chk("EOF cierra sesión limpia", "(EOF)" in out.getvalue())

    # 3) Resolver: por ahora solo verificamos que la función no crashea
    #    cuando registry.db no contiene el target (no usamos registry real).
    res = resolve("clon-que-no-existe-xyz123")
    chk("resolve devuelve None si no existe", res is None)

    print("-" * 60)
    if failures:
        print(f"❌ FAIL ({len(failures)}): {failures}")
        return 1
    print("✅ SELF-TEST PASS")
    return 0


# ── CLI ────────────────────────────────────────────────────────────────────

def _cli() -> int:
    ap = argparse.ArgumentParser(prog="eidos_portal",
        description="Portal mode CLI desde Kali → clon remoto (F9)")
    ap.add_argument("target", nargs="?",
                    help="clone_id o friendly_name del clon")
    ap.add_argument("--list", action="store_true",
                    help="lista los clones registrados")
    ap.add_argument("--self-test", action="store_true")
    args = ap.parse_args()

    if args.self_test:
        return _self_test()

    if args.list:
        rows = _list_clones()
        if not rows:
            print("(no hay clones registrados)")
            return 0
        for r in rows:
            ls = (time.strftime("%Y-%m-%d %H:%M", time.localtime(r["last_seen"]))
                  if r["last_seen"] else "—")
            print(f"  {r['clone_id'][:24]:<24} "
                  f"{(r['friendly_name'] or '—'):<24} "
                  f"port={r['tunnel_port'] or '—'} "
                  f"last_seen={ls} "
                  f"{'REVOKED' if r['revoked'] else ''}")
        return 0

    if not args.target:
        ap.print_help()
        return 1

    clone = resolve(args.target)
    if not clone:
        print(f"no resuelto: {args.target}")
        print("usa --list para ver los clones registrados")
        return 1
    if clone.get("revoked"):
        print(f"clon revocado: {clone['clone_id']}")
        return 1

    res = session_loop(clone, sys.stdin, sys.stdout)
    print(f"\nportal cerrado. comandos ejecutados: {res['commands_run']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(_cli())
