"""
core/eidos_shell_logger.py — Logger zsh VISIBLE (FA)
======================================================
Logger opcional y VISIBLE (NO shadow) de la actividad shell de SER en
su Kali. SER lo activa conscientemente con `eidos-log on`, ve un
indicador en el prompt mientras está activo, lo apaga con
`eidos-log off`.

Diseño honesto (línea disciplinada v2026-05-21):
  • LOCAL ONLY: la base SQLite vive en ~/.eidos/ser_shell_log.db (chmod 600).
  • NUNCA se envía por el canal F3. NUNCA se sincroniza a otros clones.
  • Indicador VISIBLE en el prompt mientras está activo ("● REC").
  • Activación por flag file ~/.eidos/.log_enabled — si no existe → no loggea.
  • Queries vía CLI SOLO SELECT (rechaza INSERT/UPDATE/DELETE/PRAGMA/ATTACH).
  • Sirve para alimentar BrainMemory.recall cuando SER lo invoca explícitamente
    (no automático).

Componentes:
  • SQLite schema shell_log(ts, cwd, cmd, exit_code, duration_ms).
  • Hook zsh en scripts/zsh-logger-hook.zsh (se installa en ~/.eidos/).
  • CLI `eidos-log` con: install, uninstall, on, off, status,
    query <SQL>, purge --before <ts|iso>, log <cmd> (lo llama el hook),
    feed-brain --since <ts>.

Uso típico:
  python3 -m core.eidos_shell_logger install        # sólo la primera vez
  eidos-log on
  ... (haz cosas en zsh, el prompt mostrará "● REC")
  eidos-log status
  eidos-log query "SELECT cmd FROM shell_log ORDER BY ts DESC LIMIT 10"
  eidos-log off
"""
from __future__ import annotations

import argparse
import getpass
import io
import json
import os
import re
import shutil
import socket
import sqlite3
import sys
import tempfile
import textwrap
import time
from pathlib import Path
from typing import Optional
from core.db import get_conn, get_conn_ctx

# ── Paths ──────────────────────────────────────────────────────────────────

EIDOS_HOME    = Path(os.path.expanduser("~/.eidos"))
LOG_DB        = EIDOS_HOME / "ser_shell_log.db"
ENABLED_FLAG  = EIDOS_HOME / ".log_enabled"
HOOK_FILE     = EIDOS_HOME / "zsh_logger.zsh"
ZSHRC_PATH    = Path(os.path.expanduser("~/.zshrc"))

ZSHRC_MARKER_BEGIN = "# >>> eidos-log hook >>>"
ZSHRC_MARKER_END   = "# <<< eidos-log hook <<<"


# ── Schema ────────────────────────────────────────────────────────────────

SCHEMA = """
CREATE TABLE IF NOT EXISTS shell_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ts REAL NOT NULL,
    cwd TEXT NOT NULL,
    cmd TEXT NOT NULL,
    exit_code INTEGER,
    duration_ms INTEGER,
    host TEXT,
    user TEXT
);
CREATE INDEX IF NOT EXISTS idx_log_ts ON shell_log(ts);
"""


def _conn() -> sqlite3.Connection:
    EIDOS_HOME.mkdir(parents=True, exist_ok=True)
    c = get_conn(LOG_DB)
    c.executescript(SCHEMA)
    try:
        os.chmod(LOG_DB, 0o600)
    except Exception:
        pass
    return c


# ── Toggle on/off ──────────────────────────────────────────────────────────

def is_enabled() -> bool:
    return ENABLED_FLAG.exists()


def turn_on() -> None:
    EIDOS_HOME.mkdir(parents=True, exist_ok=True)
    ENABLED_FLAG.write_text(
        time.strftime("%Y-%m-%dT%H:%M:%S"), encoding="utf-8")
    try:
        os.chmod(ENABLED_FLAG, 0o600)
    except Exception:
        pass


def turn_off() -> None:
    try:
        ENABLED_FLAG.unlink()
    except FileNotFoundError:
        pass


# ── Append (llamado por el hook zsh) ───────────────────────────────────────

CMD_MAX_LEN = 4000  # protege la DB de pegotes accidentales


def log_command(cwd: str, cmd: str,
                exit_code: Optional[int] = None,
                duration_ms: Optional[int] = None,
                host: Optional[str] = None,
                user: Optional[str] = None) -> None:
    """Inserta una entrada en la DB. Si is_enabled()==False → no-op."""
    if not is_enabled():
        return
    cmd = (cmd or "").strip()
    if not cmd:
        return
    if len(cmd) > CMD_MAX_LEN:
        cmd = cmd[:CMD_MAX_LEN] + " …[truncated]"
    with _conn() as c:
        c.execute(
            "INSERT INTO shell_log(ts, cwd, cmd, exit_code, duration_ms,"
            " host, user) VALUES(?,?,?,?,?,?,?)",
            (time.time(), cwd, cmd,
             exit_code, duration_ms,
             host or socket.gethostname(),
             user or getpass.getuser()))


# ── Status ─────────────────────────────────────────────────────────────────

def status() -> dict:
    enabled = is_enabled()
    db_size = LOG_DB.stat().st_size if LOG_DB.exists() else 0
    n = 0
    last_ts = None
    if LOG_DB.exists():
        with _conn() as c:
            n = c.execute("SELECT COUNT(*) FROM shell_log").fetchone()[0]
            r = c.execute(
                "SELECT ts, cmd FROM shell_log ORDER BY ts DESC LIMIT 1"
            ).fetchone()
            last_ts = r[0] if r else None
    return {
        "enabled": enabled,
        "db_path": str(LOG_DB),
        "db_size_bytes": db_size,
        "entries": n,
        "last_entry_ts": last_ts,
        "last_entry_iso": (time.strftime("%Y-%m-%d %H:%M:%S",
                                         time.localtime(last_ts))
                           if last_ts else None),
        "zsh_hook_installed": HOOK_FILE.exists(),
        "zshrc_line_present": _zshrc_has_marker(),
    }


# ── Query seguro (solo SELECT) ─────────────────────────────────────────────

_FORBIDDEN_TOKENS = (
    "insert", "update", "delete", "drop", "alter", "create",
    "attach", "detach", "pragma", "vacuum", "replace", "reindex",
)


def _is_safe_select(sql: str) -> bool:
    s = sql.strip().rstrip(";").lower()
    if not s.startswith("select") and not s.startswith("with "):
        return False
    # Rechazar tokens peligrosos como palabras
    for tok in _FORBIDDEN_TOKENS:
        if re.search(rf"\b{tok}\b", s):
            return False
    if ";" in s:  # nada de múltiples statements
        return False
    return True


def query(sql: str) -> tuple[bool, str, list]:
    """Ejecuta un SELECT contra la DB. Devuelve (ok, error_msg, rows)."""
    if not _is_safe_select(sql):
        return False, "solo se permiten SELECT (sin INSERT/UPDATE/etc, sin ;)", []
    if not LOG_DB.exists():
        return True, "", []
    try:
        with _conn() as c:
            rows = c.execute(sql).fetchall()
        return True, "", rows
    except sqlite3.Error as e:
        return False, str(e), []


# ── Purge ──────────────────────────────────────────────────────────────────

def _parse_ts(s: str) -> Optional[float]:
    """Acepta epoch float o ISO 'YYYY-MM-DD[ HH:MM[:SS]]'."""
    try:
        return float(s)
    except ValueError:
        pass
    fmts = ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M", "%Y-%m-%d")
    for f in fmts:
        try:
            return time.mktime(time.strptime(s, f))
        except ValueError:
            continue
    return None


def purge(before_ts: float) -> int:
    """Borra entradas con ts < before_ts. Devuelve nº filas borradas."""
    if not LOG_DB.exists():
        return 0
    with _conn() as c:
        n = c.execute("DELETE FROM shell_log WHERE ts < ?",
                      (before_ts,)).rowcount
        c.commit()
    # VACUUM debe correr fuera de transacción
    c2 = get_conn(LOG_DB, isolation_level=None)
    try:
        c2.execute("VACUUM")
    finally:
        c2.close()
    return n


# ── Feed BrainMemory (sólo cuando SER lo invoca explícitamente) ────────────

def feed_brain(since_ts: float, limit: int = 500) -> dict:
    """Carga entradas desde `since_ts` y las inyecta en BrainMemory
    como method_observed. SOLO se ejecuta cuando SER llama explícitamente
    `eidos-log feed-brain --since X`. No es automático.

    Implementación honesta: si brain_memory no está disponible, devuelve
    ok=False con error. No es bloqueador para el resto del módulo.
    """
    if not LOG_DB.exists():
        return {"ok": True, "fed": 0, "note": "no DB"}
    with _conn() as c:
        rows = c.execute(
            "SELECT ts, cwd, cmd, exit_code FROM shell_log "
            "WHERE ts >= ? ORDER BY ts LIMIT ?", (since_ts, limit)
        ).fetchall()
    if not rows:
        return {"ok": True, "fed": 0}
    try:
        sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
        from core.eidos_brain import BrainMemory  # type: ignore
        bm = BrainMemory()
        n_fed = 0
        for ts, cwd, cmd, ec in rows:
            try:
                bm.add_lesson(
                    kind="method_observed",
                    content=f"SER ejecutó en {cwd}: {cmd}"
                            + (f" (exit={ec})" if ec is not None else ""),
                    metadata={"ts": ts, "source": "shell_logger_FA"})
                n_fed += 1
            except Exception:  # noqa: BLE001
                continue
        return {"ok": True, "fed": n_fed, "total_rows": len(rows)}
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "fed": 0, "error": str(e)}


# ── Hook zsh ───────────────────────────────────────────────────────────────

# preexec/precmd zsh hooks. Compatibles con zsh estándar (Kali). El
# indicador "● REC" se inyecta en el prompt cuando el flag está activo.
ZSH_HOOK = textwrap.dedent("""\
    # ┌──────────────────────────────────────────────────────────────┐
    # │ EIDOS shell-logger (FA) — VISIBLE, opt-in, local-only         │
    # └──────────────────────────────────────────────────────────────┘
    # Activación por flag file: ~/.eidos/.log_enabled
    #   $ eidos-log on   # crea el flag
    #   $ eidos-log off  # borra el flag
    # NUNCA se loggea si el flag no existe.

    EIDOS_LOG_DIR="${HOME}/.eidos"
    EIDOS_LOG_FLAG="${EIDOS_LOG_DIR}/.log_enabled"

    # Inyecta indicador "● REC" en el prompt si el flag existe.
    # Conserva el PS1 original como _EIDOS_PS1_ORIG en la primera carga.
    if [ -z "${_EIDOS_PS1_ORIG+x}" ]; then
      _EIDOS_PS1_ORIG="$PS1"
    fi
    _eidos_set_prompt() {
      if [ -f "$EIDOS_LOG_FLAG" ]; then
        PS1="%F{red}● REC%f $_EIDOS_PS1_ORIG"
      else
        PS1="$_EIDOS_PS1_ORIG"
      fi
    }
    autoload -Uz add-zsh-hook
    add-zsh-hook precmd _eidos_set_prompt

    # preexec captura el comando justo antes de ejecutarlo.
    _EIDOS_LOG_LAST=""
    _EIDOS_LOG_T0=0
    _eidos_log_preexec() {
      _EIDOS_LOG_LAST="$1"
      _EIDOS_LOG_T0=$EPOCHREALTIME
    }
    # precmd corre tras la ejecución; sabemos exit code = $? del último
    _eidos_log_precmd() {
      local ec=$?
      _eidos_set_prompt
      if [ ! -f "$EIDOS_LOG_FLAG" ]; then return 0; fi
      if [ -z "$_EIDOS_LOG_LAST" ]; then return 0; fi
      local dt_ms=0
      if [ -n "$_EIDOS_LOG_T0" ] && [ "$_EIDOS_LOG_T0" != "0" ]; then
        dt_ms=$(( ( $(printf '%.0f' $((${EPOCHREALTIME%.*} * 1000)) )
                  - $(printf '%.0f' $((${_EIDOS_LOG_T0%.*} * 1000)) ) ) ))
      fi
      # Llama al CLI en background para no bloquear el prompt.
      ( eidos-log log --cwd "$PWD" --cmd "$_EIDOS_LOG_LAST" \\
          --exit "$ec" --dur "$dt_ms" >/dev/null 2>&1 & )
      _EIDOS_LOG_LAST=""
      _EIDOS_LOG_T0=0
    }
    zmodload zsh/datetime 2>/dev/null
    add-zsh-hook preexec _eidos_log_preexec
    add-zsh-hook precmd  _eidos_log_precmd
""")


def _zshrc_line(hook_path: Path) -> str:
    return (f"{ZSHRC_MARKER_BEGIN}\n"
            f"[ -f \"{hook_path}\" ] && source \"{hook_path}\"\n"
            f"{ZSHRC_MARKER_END}\n")


def _zshrc_has_marker() -> bool:
    if not ZSHRC_PATH.exists():
        return False
    txt = ZSHRC_PATH.read_text(encoding="utf-8", errors="ignore")
    return ZSHRC_MARKER_BEGIN in txt


def install_hook(zshrc: Optional[Path] = None,
                 hook_dest: Optional[Path] = None) -> dict:
    """Instala el hook y añade la línea source al .zshrc (idempotente)."""
    z = zshrc or ZSHRC_PATH
    h = hook_dest or HOOK_FILE
    EIDOS_HOME.mkdir(parents=True, exist_ok=True)
    h.write_text(ZSH_HOOK, encoding="utf-8")
    os.chmod(h, 0o644)
    if not z.exists():
        z.write_text("", encoding="utf-8")
    txt = z.read_text(encoding="utf-8")
    if ZSHRC_MARKER_BEGIN not in txt:
        new = txt.rstrip() + "\n\n" + _zshrc_line(h)
        z.write_text(new, encoding="utf-8")
    return {"ok": True, "hook": str(h), "zshrc": str(z),
            "zshrc_modified": ZSHRC_MARKER_BEGIN not in txt}


def uninstall_hook(zshrc: Optional[Path] = None,
                   hook_dest: Optional[Path] = None) -> dict:
    """Quita la línea del .zshrc (idempotente) y borra el hook."""
    z = zshrc or ZSHRC_PATH
    h = hook_dest or HOOK_FILE
    removed = False
    if z.exists():
        txt = z.read_text(encoding="utf-8")
        # Captura la línea en blanco que el install añade antes del marcador
        pattern = re.compile(
            r"\n*" + re.escape(ZSHRC_MARKER_BEGIN) + r".*?"
            + re.escape(ZSHRC_MARKER_END) + r"\n?",
            re.DOTALL)
        new = pattern.sub("", txt)
        if new != txt:
            # Asegurar exactamente 1 newline al final (norma POSIX)
            new = new.rstrip("\n") + "\n"
            z.write_text(new, encoding="utf-8")
            removed = True
    if h.exists():
        h.unlink()
    return {"ok": True, "zshrc_clean": not _zshrc_has_marker(),
            "hook_removed": not h.exists(), "zshrc_diff_applied": removed}


# ── Self-test ──────────────────────────────────────────────────────────────

def _self_test() -> int:
    failures: list[str] = []

    def chk(name: str, cond: bool, detail: str = ""):
        mark = "✅" if cond else "❌"
        print(f"{mark} {name}" + (f" — {detail[:120]}" if detail else ""))
        if not cond:
            failures.append(name)

    # Aislamos: DB y flag y zshrc temporales
    global LOG_DB, ENABLED_FLAG, HOOK_FILE, EIDOS_HOME, ZSHRC_PATH
    orig = (LOG_DB, ENABLED_FLAG, HOOK_FILE, EIDOS_HOME, ZSHRC_PATH)
    tmpdir = Path(tempfile.mkdtemp(prefix="eidos-log-"))
    EIDOS_HOME = tmpdir / ".eidos"
    EIDOS_HOME.mkdir()
    LOG_DB        = EIDOS_HOME / "ser_shell_log.db"
    ENABLED_FLAG  = EIDOS_HOME / ".log_enabled"
    HOOK_FILE     = EIDOS_HOME / "zsh_logger.zsh"
    ZSHRC_PATH    = tmpdir / ".zshrc"
    ZSHRC_PATH.write_text("# fake zshrc original\nexport FOO=bar\n",
                          encoding="utf-8")

    try:
        # 1) Toggle on/off
        chk("inicial off", not is_enabled())
        turn_on()
        chk("turn_on activa flag", is_enabled())
        turn_off()
        chk("turn_off desactiva flag", not is_enabled())

        # 2) Loggear cuando off → no-op
        log_command("/tmp", "echo hola")
        chk("log_command con flag off no escribe", not LOG_DB.exists()
            or get_conn(LOG_DB).execute(
                "SELECT COUNT(*) FROM shell_log").fetchone()[0] == 0)

        # 3) Activar y loggear 3 comandos
        turn_on()
        log_command("/tmp", "ls -la", 0, 12)
        log_command("/home", "git status", 0, 45)
        log_command("/etc", "cat hosts", 0, 8)
        with get_conn_ctx(LOG_DB) as c:
            n = c.execute("SELECT COUNT(*) FROM shell_log").fetchone()[0]
        chk("3 comandos loggeados con flag on", n == 3, f"got {n}")

        # 4) Status
        st = status()
        chk("status enabled=True", st["enabled"] is True)
        chk("status entries=3", st["entries"] == 3)
        chk("status db_size>0", st["db_size_bytes"] > 0)

        # 5) Query seguros
        ok, err, rows = query("SELECT cmd FROM shell_log ORDER BY ts DESC LIMIT 2")
        chk("SELECT permitido", ok is True and len(rows) == 2 and err == "")
        ok, err, _ = query("DELETE FROM shell_log")
        chk("DELETE rechazado", ok is False and "solo" in err.lower())
        ok, err, _ = query("INSERT INTO shell_log(ts,cwd,cmd) VALUES(1,'/','x')")
        chk("INSERT rechazado", ok is False)
        ok, err, _ = query("SELECT * FROM shell_log; DROP TABLE shell_log")
        chk("multi-statement rechazado", ok is False)
        ok, err, _ = query("PRAGMA table_info(shell_log)")
        chk("PRAGMA rechazado", ok is False)
        ok, err, _ = query("ATTACH DATABASE 'x' AS y")
        chk("ATTACH rechazado", ok is False)

        # 6) Purge
        # añadimos una entrada con ts antiguo
        with get_conn_ctx(LOG_DB) as c:
            c.execute("INSERT INTO shell_log(ts,cwd,cmd) VALUES(?,?,?)",
                      (100.0, "/old", "viejo"))
        deleted = purge(before_ts=200.0)
        chk("purge borra entrada vieja", deleted == 1)
        with get_conn_ctx(LOG_DB) as c:
            n_after = c.execute("SELECT COUNT(*) FROM shell_log").fetchone()[0]
        chk("entries post-purge = 3", n_after == 3)

        # 7) Hook install / uninstall sobre .zshrc temporal
        r = install_hook()
        chk("hook instalado", r["ok"] and HOOK_FILE.exists())
        chk("marker añadido al .zshrc",
            ZSHRC_MARKER_BEGIN in ZSHRC_PATH.read_text())
        # idempotente
        r2 = install_hook()
        chk("install idempotente (no duplica)",
            ZSHRC_PATH.read_text().count(ZSHRC_MARKER_BEGIN) == 1)
        r3 = uninstall_hook()
        chk("uninstall hook borrado", not HOOK_FILE.exists())
        chk("uninstall marker quitado del .zshrc",
            ZSHRC_MARKER_BEGIN not in ZSHRC_PATH.read_text())

        # 8) Truncar comando largo
        log_command("/", "x" * (CMD_MAX_LEN + 200))
        with get_conn_ctx(LOG_DB) as c:
            r = c.execute("SELECT cmd FROM shell_log ORDER BY id DESC LIMIT 1"
                          ).fetchone()
        chk("cmd largo truncado y marcado",
            len(r[0]) <= CMD_MAX_LEN + 20 and "truncated" in r[0])

        # 9) Hook contiene los markers y el indicador VISIBLE
        chk("hook script contiene indicador '● REC'", "● REC" in ZSH_HOOK)
        chk("hook script chequea flag file", ".log_enabled" in ZSH_HOOK)
        chk("hook usa preexec+precmd", "preexec" in ZSH_HOOK and "precmd" in ZSH_HOOK)

    finally:
        LOG_DB, ENABLED_FLAG, HOOK_FILE, EIDOS_HOME, ZSHRC_PATH = orig
        shutil.rmtree(tmpdir, ignore_errors=True)

    print("-" * 60)
    if failures:
        print(f"❌ FAIL ({len(failures)}): {failures}")
        return 1
    print("✅ SELF-TEST PASS")
    return 0


# ── CLI (también accesible como bin/eidos-log) ─────────────────────────────

def _cli() -> int:
    ap = argparse.ArgumentParser(prog="eidos-log",
        description="Logger zsh VISIBLE de SER (FA, local-only opt-in)")
    sub = ap.add_subparsers(dest="cmd")

    sub.add_parser("install", help="instala hook zsh + edita .zshrc (idempotente)")
    sub.add_parser("uninstall", help="quita hook zsh + limpia .zshrc")
    sub.add_parser("on",  help="activa el logger (crea flag)")
    sub.add_parser("off", help="desactiva el logger (borra flag)")
    sub.add_parser("status", help="muestra estado + últimos N")

    sp = sub.add_parser("query", help="ejecuta SELECT contra la DB")
    sp.add_argument("sql")

    sp = sub.add_parser("purge", help="borra entradas anteriores a <ts>")
    sp.add_argument("--before", required=True,
                    help="epoch o ISO 'YYYY-MM-DD[ HH:MM[:SS]]'")
    sp.add_argument("--yes", action="store_true",
                    help="confirma sin preguntar")

    sp = sub.add_parser("log", help="(interno) usado por el hook zsh")
    sp.add_argument("--cwd", required=True)
    sp.add_argument("--cmd", required=True)
    sp.add_argument("--exit", type=int, default=None)
    sp.add_argument("--dur", type=int, default=None)

    sp = sub.add_parser("feed-brain", help="inyecta entradas en BrainMemory")
    sp.add_argument("--since", required=True,
                    help="epoch o ISO. NO se hace automáticamente — SER lo invoca.")

    sub.add_parser("self-test")

    args = ap.parse_args()

    if args.cmd == "install":
        print(json.dumps(install_hook(), indent=2))
        print("Para activar en la sesión actual: source ~/.zshrc")
        return 0
    if args.cmd == "uninstall":
        print(json.dumps(uninstall_hook(), indent=2)); return 0
    if args.cmd == "on":
        turn_on(); print("● REC activado")
        return 0
    if args.cmd == "off":
        turn_off(); print("logger apagado"); return 0
    if args.cmd == "status":
        st = status()
        print(json.dumps(st, indent=2))
        # mostrar últimas 5
        ok, _, rows = query("SELECT datetime(ts,'unixepoch','localtime'), "
                            "exit_code, cmd FROM shell_log "
                            "ORDER BY ts DESC LIMIT 5")
        if ok and rows:
            print("\nÚltimas 5 entradas:")
            for r in rows:
                print(f"  [{r[0]}] (ec={r[1]}) {r[2][:120]}")
        return 0
    if args.cmd == "query":
        ok, err, rows = query(args.sql)
        if not ok:
            print(json.dumps({"ok": False, "error": err}))
            return 1
        for r in rows:
            print(r)
        return 0
    if args.cmd == "purge":
        ts = _parse_ts(args.before)
        if ts is None:
            print(f"no pude parsear --before='{args.before}'"); return 1
        if not args.yes:
            ans = input(f"Borrar entradas anteriores a {args.before}? (yes/no) ")
            if ans.strip().lower() not in ("yes", "y", "si", "s"):
                print("cancelado"); return 0
        n = purge(ts)
        print(json.dumps({"deleted": n}))
        return 0
    if args.cmd == "log":
        log_command(args.cwd, args.cmd, args.exit, args.dur)
        return 0
    if args.cmd == "feed-brain":
        ts = _parse_ts(args.since)
        if ts is None:
            print(f"no pude parsear --since='{args.since}'"); return 1
        print(json.dumps(feed_brain(ts), indent=2)); return 0
    if args.cmd == "self-test":
        return _self_test()

    ap.print_help()
    return 0


if __name__ == "__main__":
    raise SystemExit(_cli())
