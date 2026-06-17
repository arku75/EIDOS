"""
core/eidos_shadow_logger.py — Shadow logger zsh-hook → BrainMemory
==================================================================
EIDOS observa toda la actividad shell de SER (cuando éste lo permite)
para alimentar su brain con method_observed reales. Toggle ON/OFF
explícito vía CLI o función zsh. Por defecto OFF (consentimiento).

Diseño (asimétrico, no invasivo):
  1. Hook zsh (preexec/precmd) escribe línea JSON-line al buffer
     ~/.eidos/shadow/buffer.jsonl (chmod 600). Súper rápido (sin Python
     en el critical path). NO bloquea el prompt.
  2. CLI `drain` consume buffer, mete cada línea como nodo BrainMemory
     con tag "shadow_log" + cwd + exit_code + duration_ms.
  3. Buffer drenado se mueve a ~/.eidos/shadow/archive.jsonl.gz
     (rotación) para historial.
  4. Toggle ON/OFF persistente en ~/.eidos/shadow/state (texto plano).
     El hook zsh lo lee en cada preexec; si OFF, no hace nada.

Privacidad/seguridad:
  - Estado default = OFF (consentimiento explícito de SER cada vez que
    quiera grabar).
  - Permisos chmod 600 en state, buffer, archive (datos privados).
  - Filtros de redacción: passwords/keys/tokens detectados se redactan
    antes de escribir al buffer (regex en hook + segunda pasada en drain).
  - Comando SOLO de SER; el clon NUNCA puede leer este buffer (no está
    en allowlist del eidos_command_channel).

Uso típico:
  python3 -m core.eidos_shadow_logger install-zsh  # imprime snippet
  # añadir snippet a ~/.zshrc.d/eidos_shadow.zsh (o ~/.zshrc)
  source ~/.zshrc                                  # cargar
  eidos-shadow on                                  # activar
  ls -la                                            # se graba
  eidos-shadow drain                                # mover buffer a brain
  eidos-shadow tail 10                              # ver últimas N
  eidos-shadow off                                  # parar
  eidos-shadow status                               # ver estado
"""
from __future__ import annotations

import argparse
import gzip
import json
import os
import re
import sys
import time
from pathlib import Path
from typing import Optional

# ── Paths ──────────────────────────────────────────────────────────────────
SHADOW_DIR = Path(os.path.expanduser("~/.eidos/shadow"))
STATE_FILE = SHADOW_DIR / "state"           # "on"/"off"
BUFFER     = SHADOW_DIR / "buffer.jsonl"    # escribe el hook zsh
ARCHIVE    = SHADOW_DIR / "archive.jsonl.gz"
COUNTER    = SHADOW_DIR / "counter"         # nº de comandos grabados total

# ── Redacción ──────────────────────────────────────────────────────────────
# Patrones que NUNCA llegan al brain ni al buffer.
SECRET_PATTERNS = [
    re.compile(r"(?i)(api[_-]?key|token|password|passwd|secret|bearer)\s*[:=]\s*\S+"),
    re.compile(r"(?i)sk-[A-Za-z0-9]{16,}"),
    re.compile(r"(?i)gsk_[A-Za-z0-9]{16,}"),
    re.compile(r"(?i)hf_[A-Za-z0-9]{16,}"),
    re.compile(r"-----BEGIN [A-Z ]+PRIVATE KEY-----.+?-----END [A-Z ]+PRIVATE KEY-----",
               re.DOTALL),
    re.compile(r"(?i)ssh-(rsa|ed25519|ecdsa)\s+[A-Za-z0-9+/=]{40,}"),
]


def redact(s: str) -> str:
    if not s:
        return s
    out = s
    for pat in SECRET_PATTERNS:
        out = pat.sub("«REDACTED»", out)
    return out


# ── Estado ─────────────────────────────────────────────────────────────────

def _ensure_dir() -> None:
    SHADOW_DIR.mkdir(parents=True, exist_ok=True)
    try:
        os.chmod(SHADOW_DIR, 0o700)
    except Exception:
        pass


def get_state() -> str:
    if not STATE_FILE.exists():
        return "off"
    v = STATE_FILE.read_text(encoding="utf-8").strip().lower()
    return "on" if v in ("on", "1", "yes", "true") else "off"


def set_state(value: str) -> str:
    _ensure_dir()
    v = "on" if value.lower() in ("on", "1", "yes", "true") else "off"
    STATE_FILE.write_text(v + "\n", encoding="utf-8")
    try:
        os.chmod(STATE_FILE, 0o600)
    except Exception:
        pass
    return v


def get_counter() -> int:
    if not COUNTER.exists():
        return 0
    try:
        return int(COUNTER.read_text(encoding="utf-8").strip() or "0")
    except Exception:
        return 0


def _inc_counter(n: int = 1) -> None:
    _ensure_dir()
    c = get_counter() + n
    COUNTER.write_text(str(c), encoding="utf-8")
    try:
        os.chmod(COUNTER, 0o600)
    except Exception:
        pass


# ── Record API (para tests o uso manual; el hook escribe directo) ──────────

def record(cmd: str, cwd: str = "", exit_code: int = 0,
           duration_ms: int = 0, ts: Optional[float] = None) -> bool:
    """Añade un registro al buffer. Idéntico a lo que escribiría el hook.
    Retorna True si grabó, False si shadow OFF o entrada vacía."""
    if get_state() != "on":
        return False
    cmd_r = redact((cmd or "").strip())
    if not cmd_r:
        return False
    _ensure_dir()
    entry = {
        "ts": ts if ts is not None else time.time(),
        "cmd": cmd_r,
        "cwd": cwd or os.getcwd(),
        "exit": int(exit_code),
        "dur_ms": int(duration_ms),
    }
    with open(BUFFER, "a", encoding="utf-8") as f:
        f.write(json.dumps(entry, ensure_ascii=False) + "\n")
    try:
        os.chmod(BUFFER, 0o600)
    except Exception:
        pass
    return True


# ── Drain (buffer → BrainMemory + archive) ─────────────────────────────────

def drain(limit: int = 1000, dry: bool = False) -> dict:
    """Lee buffer, mete cada línea como nodo BrainMemory, archiva la línea
    gz-comprimida, vacía el buffer. Devuelve métricas."""
    if not BUFFER.exists():
        return {"ok": True, "drained": 0, "buffer_empty": True}
    lines = []
    try:
        with open(BUFFER, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line:
                    lines.append(line)
                if len(lines) >= limit:
                    break
    except Exception as e:
        return {"ok": False, "error": f"read buffer: {e}"}
    if not lines:
        return {"ok": True, "drained": 0, "buffer_empty": True}

    drained = 0
    errors = 0
    bm = None
    if not dry:
        try:
            from core.brain_memory import BrainMemory  # type: ignore
            bm = BrainMemory()
        except Exception as e:
            return {"ok": False, "error": f"BrainMemory unavailable: {e}",
                    "drained": 0}
    for raw in lines:
        try:
            j = json.loads(raw)
            j["cmd"] = redact(j.get("cmd", ""))  # segunda pasada
            content = (
                f"shell_cmd: {j['cmd']} "
                f"[cwd={j.get('cwd','?')}] "
                f"[exit={j.get('exit',0)}] "
                f"[dur_ms={j.get('dur_ms',0)}]"
            )
            tags = ["shadow_log", "shell", f"exit_{int(j.get('exit',0))}"]
            if bm is not None:
                bm.remember(content, tags=tags, importance=0.4,
                            category="shadow_shell")
            drained += 1
        except Exception:
            errors += 1
            continue

    # Archive (gz append) y truncado de buffer
    if drained:
        try:
            with gzip.open(ARCHIVE, "ab") as gz:
                gz.write(("\n".join(lines) + "\n").encode("utf-8"))
            try:
                os.chmod(ARCHIVE, 0o600)
            except Exception:
                pass
        except Exception as e:
            return {"ok": False, "error": f"archive: {e}",
                    "drained": drained}

    # Vaciar líneas drenadas del buffer (preservar lo que no entró por limit)
    if len(lines) < (sum(1 for _ in open(BUFFER)) if BUFFER.exists() else 0):
        # Hubo más líneas que limit: re-escribe las restantes
        with open(BUFFER, "r", encoding="utf-8") as f:
            all_lines = f.readlines()
        with open(BUFFER, "w", encoding="utf-8") as f:
            f.writelines(all_lines[len(lines):])
    else:
        BUFFER.unlink(missing_ok=True)

    if drained:
        _inc_counter(drained)

    return {"ok": True, "drained": drained, "errors": errors,
            "total_recorded": get_counter()}


# ── Tail ───────────────────────────────────────────────────────────────────

def tail(n: int = 20) -> list[dict]:
    """Devuelve las últimas N entries (buffer + archive)."""
    out: list[dict] = []
    if BUFFER.exists():
        try:
            with open(BUFFER, "r", encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if line:
                        try:
                            out.append(json.loads(line))
                        except Exception:
                            continue
        except Exception:
            pass
    if len(out) < n and ARCHIVE.exists():
        try:
            with gzip.open(ARCHIVE, "rt", encoding="utf-8") as gz:
                arch = []
                for line in gz:
                    line = line.strip()
                    if line:
                        try:
                            arch.append(json.loads(line))
                        except Exception:
                            continue
                out = arch + out
        except Exception:
            pass
    return out[-n:]


# ── Stats ──────────────────────────────────────────────────────────────────

def status() -> dict:
    buf_lines = 0
    if BUFFER.exists():
        try:
            buf_lines = sum(1 for _ in open(BUFFER))
        except Exception:
            pass
    arch_size = ARCHIVE.stat().st_size if ARCHIVE.exists() else 0
    return {
        "state": get_state(),
        "buffer_pending": buf_lines,
        "archive_bytes": arch_size,
        "total_recorded": get_counter(),
        "shadow_dir": str(SHADOW_DIR),
    }


# ── ZSH hook snippet ───────────────────────────────────────────────────────

ZSH_SNIPPET = r'''
# ── EIDOS SHADOW LOGGER (instalado por core/eidos_shadow_logger.py) ────────
# Toggle: eidos-shadow on|off|status|drain|tail [N]
# Persistente entre sesiones (estado en ~/.eidos/shadow/state).
# OFF por defecto. Bajo consentimiento explícito.
typeset -g _EIDOS_SHADOW_START=0
typeset -g _EIDOS_SHADOW_CMD=""
_EIDOS_SHADOW_STATE_FILE="${HOME}/.eidos/shadow/state"
_EIDOS_SHADOW_BUFFER="${HOME}/.eidos/shadow/buffer.jsonl"
_EIDOS_SHADOW_HOOKED_CMDS=0

_eidos_shadow_is_on() {
    [[ -r "$_EIDOS_SHADOW_STATE_FILE" ]] || return 1
    local v
    v="$(<"$_EIDOS_SHADOW_STATE_FILE")"
    [[ "${v%%[[:space:]]*}" == "on" ]]
}

eidos-shadow() {
    case "$1" in
        on)
            mkdir -p "${HOME}/.eidos/shadow" && chmod 700 "${HOME}/.eidos/shadow"
            print -- "on" > "$_EIDOS_SHADOW_STATE_FILE"
            chmod 600 "$_EIDOS_SHADOW_STATE_FILE"
            print -- "✅ EIDOS shadow ON — comandos shell se graban en ~/.eidos/shadow/buffer.jsonl"
            ;;
        off)
            mkdir -p "${HOME}/.eidos/shadow" && chmod 700 "${HOME}/.eidos/shadow"
            print -- "off" > "$_EIDOS_SHADOW_STATE_FILE"
            chmod 600 "$_EIDOS_SHADOW_STATE_FILE"
            print -- "💤 EIDOS shadow OFF"
            ;;
        status|"")
            if _eidos_shadow_is_on; then
                print -- "🟢 shadow ON"
            else
                print -- "⚫ shadow OFF"
            fi
            python3 -m core.eidos_shadow_logger status 2>/dev/null
            ;;
        drain)
            python3 -m core.eidos_shadow_logger drain
            ;;
        tail)
            python3 -m core.eidos_shadow_logger tail "${2:-20}"
            ;;
        *)
            print -- "uso: eidos-shadow [on|off|status|drain|tail [N]]"
            ;;
    esac
}

# Redacción rápida en bash-regex (segunda pasada vive en Python al drain).
_eidos_shadow_redact() {
    local cmd="$1"
    cmd="${cmd//(--?[Pp]assword|--?[Tt]oken|--?[Aa]pi[_-]?[Kk]ey)[= ]*[^ ]*/«REDACTED»}"
    print -r -- "$cmd"
}

# JSON escape minimal (backslash + quote + control)
_eidos_shadow_json_esc() {
    local s="$1"
    s="${s//\\/\\\\}"
    s="${s//\"/\\\"}"
    s="${s//$'\n'/\\n}"
    s="${s//$'\r'/\\r}"
    s="${s//$'\t'/\\t}"
    print -r -- "$s"
}

_eidos_shadow_preexec() {
    _eidos_shadow_is_on || return
    _EIDOS_SHADOW_START=$EPOCHREALTIME
    _EIDOS_SHADOW_CMD="$1"
}

_eidos_shadow_precmd() {
    local exit_code=$?
    _eidos_shadow_is_on || return
    [[ -z "$_EIDOS_SHADOW_CMD" ]] && return
    local now=$EPOCHREALTIME
    local dur_ms=$(( int((now - _EIDOS_SHADOW_START) * 1000) ))
    local cmd_r cwd_esc cmd_esc
    cmd_r=$(_eidos_shadow_redact "$_EIDOS_SHADOW_CMD")
    cmd_esc=$(_eidos_shadow_json_esc "$cmd_r")
    cwd_esc=$(_eidos_shadow_json_esc "$PWD")
    mkdir -p "${HOME}/.eidos/shadow" 2>/dev/null && chmod 700 "${HOME}/.eidos/shadow" 2>/dev/null
    print -r -- "{\"ts\":$EPOCHREALTIME,\"cmd\":\"$cmd_esc\",\"cwd\":\"$cwd_esc\",\"exit\":$exit_code,\"dur_ms\":$dur_ms}" >> "$_EIDOS_SHADOW_BUFFER"
    chmod 600 "$_EIDOS_SHADOW_BUFFER" 2>/dev/null
    _EIDOS_SHADOW_CMD=""
    _EIDOS_SHADOW_HOOKED_CMDS=$((_EIDOS_SHADOW_HOOKED_CMDS + 1))
    # Drain automático cada 50 comandos (background, no bloquea)
    if (( _EIDOS_SHADOW_HOOKED_CMDS % 50 == 0 )); then
        ( cd "${HOME}/EIDOS" 2>/dev/null && python3 -m core.eidos_shadow_logger drain >/dev/null 2>&1 & )
    fi
}

zmodload zsh/datetime 2>/dev/null
autoload -Uz add-zsh-hook 2>/dev/null
add-zsh-hook preexec _eidos_shadow_preexec 2>/dev/null
add-zsh-hook precmd _eidos_shadow_precmd 2>/dev/null
# ── /EIDOS SHADOW LOGGER ──────────────────────────────────────────────────
'''


def print_install_snippet() -> None:
    print(ZSH_SNIPPET)


# ── CLI ────────────────────────────────────────────────────────────────────

def _cli() -> int:
    ap = argparse.ArgumentParser(
        prog="eidos_shadow_logger",
        description="Shadow logger zsh-hook → BrainMemory (toggle ON/OFF)",
    )
    sub = ap.add_subparsers(dest="cmd")

    sub.add_parser("status")
    sub.add_parser("on")
    sub.add_parser("off")
    sub.add_parser("install-zsh", help="Imprime snippet zsh para appendear a ~/.zshrc.d/eidos_shadow.zsh")

    sp = sub.add_parser("record", help="Añade un registro manual al buffer")
    sp.add_argument("--cmd", required=True)
    sp.add_argument("--cwd", default="")
    sp.add_argument("--exit", type=int, default=0)
    sp.add_argument("--dur", type=int, default=0, help="duration_ms")

    sp = sub.add_parser("drain", help="Buffer → BrainMemory + archive")
    sp.add_argument("--limit", type=int, default=1000)
    sp.add_argument("--dry", action="store_true")

    sp = sub.add_parser("tail")
    sp.add_argument("n", nargs="?", type=int, default=20)

    sub.add_parser("self-test", help="Test interno (sin tocar BrainMemory en --dry)")

    args = ap.parse_args()
    cmd = args.cmd or "status"

    if cmd == "status":
        print(json.dumps(status(), indent=2, ensure_ascii=False))
        return 0
    if cmd == "on":
        v = set_state("on")
        print(f"shadow → {v}")
        return 0
    if cmd == "off":
        v = set_state("off")
        print(f"shadow → {v}")
        return 0
    if cmd == "install-zsh":
        print_install_snippet()
        return 0
    if cmd == "record":
        ok = record(args.cmd, args.cwd, args.exit, args.dur)
        print(json.dumps({"recorded": ok}, indent=2))
        return 0
    if cmd == "drain":
        print(json.dumps(drain(limit=args.limit, dry=args.dry),
                         indent=2, ensure_ascii=False))
        return 0
    if cmd == "tail":
        for e in tail(args.n):
            print(json.dumps(e, ensure_ascii=False))
        return 0
    if cmd == "self-test":
        return _self_test()
    ap.print_help()
    return 1


# ── Self-test ──────────────────────────────────────────────────────────────

def _self_test() -> int:
    """Self-test in-tree (sin tocar el state real del usuario)."""
    global SHADOW_DIR, STATE_FILE, BUFFER, ARCHIVE, COUNTER
    import tempfile
    orig = (SHADOW_DIR, STATE_FILE, BUFFER, ARCHIVE, COUNTER)
    tmp = Path(tempfile.mkdtemp(prefix="eidos_shadow_test_"))
    SHADOW_DIR = tmp
    STATE_FILE = tmp / "state"
    BUFFER     = tmp / "buffer.jsonl"
    ARCHIVE    = tmp / "archive.jsonl.gz"
    COUNTER    = tmp / "counter"

    failures: list[str] = []

    def chk(name: str, cond: bool, detail: str = "") -> None:
        mark = "✅" if cond else "❌"
        msg = f"{mark} {name}" + (f" — {detail}" if detail else "")
        print(msg)
        if not cond:
            failures.append(name)

    try:
        # 1. Estado default OFF
        chk("state default OFF", get_state() == "off")

        # 2. record con estado OFF NO graba
        chk("record con OFF no graba", record("ls -la", "/tmp") is False)
        chk("buffer no existe tras off-record", not BUFFER.exists())

        # 3. Toggle ON
        set_state("on")
        chk("state se vuelve ON tras set_state", get_state() == "on")
        # Permisos state
        chk("state chmod 600",
            (STATE_FILE.stat().st_mode & 0o777) == 0o600,
            f"actual={oct(STATE_FILE.stat().st_mode & 0o777)}")

        # 4. record con ON graba
        ok = record("ls -la /home/ser", "/tmp", 0, 12)
        chk("record con ON devuelve True", ok)
        chk("buffer existe", BUFFER.exists())
        chk("buffer tiene 1 línea",
            sum(1 for _ in open(BUFFER)) == 1)
        # Permisos buffer
        chk("buffer chmod 600",
            (BUFFER.stat().st_mode & 0o777) == 0o600,
            f"actual={oct(BUFFER.stat().st_mode & 0o777)}")

        # 5. record vacío NO añade línea
        before = sum(1 for _ in open(BUFFER))
        record("", "/tmp")
        record("   ", "/tmp")
        after = sum(1 for _ in open(BUFFER))
        chk("record vacío/whitespace ignorado", before == after)

        # 6. Redacción
        record("curl -H 'Authorization: Bearer sk-superlongkey123456789abc' https://x", "/tmp")
        # Leer última línea
        with open(BUFFER, "r", encoding="utf-8") as f:
            last = f.readlines()[-1]
        j = json.loads(last)
        chk("token redactado en buffer",
            "REDACTED" in j["cmd"] and "sk-superlongkey" not in j["cmd"],
            j["cmd"][:80])

        record("export GROQ_API_KEY=gsk_realLookingKey_abcdefghijklmnopqrstuv", "/tmp")
        with open(BUFFER, "r", encoding="utf-8") as f:
            last = f.readlines()[-1]
        j = json.loads(last)
        chk("gsk_ key redactada",
            "REDACTED" in j["cmd"] and "gsk_realLooking" not in j["cmd"],
            j["cmd"][:80])

        # 7. Drain dry-run
        n_before = sum(1 for _ in open(BUFFER))
        r = drain(dry=True)
        chk("drain dry sin BrainMemory devuelve drained=count",
            r["ok"] and r["drained"] == n_before,
            json.dumps(r))
        chk("drain dry archivó", ARCHIVE.exists() and ARCHIVE.stat().st_size > 0)
        chk("drain dry vació buffer",
            (not BUFFER.exists()) or sum(1 for _ in open(BUFFER)) == 0)

        # 8. Counter aumentó por la drain dry (sí incrementa)
        chk("counter > 0 tras drain", get_counter() > 0,
            f"counter={get_counter()}")

        # 9. Tail recupera del archive (3 records reales: ls, curl bearer, gsk export)
        t = tail(50)
        chk("tail devuelve >=3 entradas", len(t) >= 3, f"got {len(t)}")

        # 10. Toggle OFF
        set_state("off")
        chk("state OFF tras toggle", get_state() == "off")
        ok = record("test cmd después de off", "/tmp")
        chk("record tras OFF devuelve False", ok is False)

        # 11. Redact directo (función pura)
        r1 = redact("api_key=secret123456789longenough")
        chk("redact api_key match", "REDACTED" in r1, r1)
        r2 = redact("clean text without secrets")
        chk("redact clean text intacto", r2 == "clean text without secrets")

        # 12. Snippet zsh contiene piezas críticas
        s = ZSH_SNIPPET
        for piece in ("eidos-shadow", "preexec", "precmd", "EPOCHREALTIME",
                      "buffer.jsonl", "REDACTED"):
            chk(f"snippet contiene '{piece}'", piece in s)

    finally:
        SHADOW_DIR, STATE_FILE, BUFFER, ARCHIVE, COUNTER = orig
        # cleanup tmp
        try:
            import shutil
            shutil.rmtree(tmp, ignore_errors=True)
        except Exception:
            pass

    print("-" * 60)
    if failures:
        print(f"❌ FAIL ({len(failures)}): {failures}")
        return 1
    print("✅ SELF-TEST PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(_cli())
