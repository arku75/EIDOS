"""
core/eidos_command_channel.py — Canal de comandos hub→clon (Fase 3)
=====================================================================
Mensajes firmados Ed25519 que el hub envía al clon a través del túnel
reverso ya establecido (Fase 2). Allowlist estricta, audit log a
ambos lados, timeouts. Respeta la Constitución de EIDOS.

Protocolo:
  Request  = {kind, args, nonce, ts, clone_id} firmado por HUB
  Response = {ok, data|error, ts, nonce_echo} firmado por CLONE
  Transporte (Fase 2): HTTP loopback en el clon → reverse-tunnel
  → puerto asignado en el hub. Pero este módulo es transport-agnostic:
  funciones puras `build_request/handle_request_at_clone/verify_response`.

Allowlist (Fase 3 minimal — extensible):
  • ping             — heartbeat manual
  • system_info      — uname/disk/ram/cpu
  • list_dir         — listar archivos (path, max_entries)
  • read_file        — leer archivo (path, max_bytes ≤ 64KB)
  • run_shell        — ejecutar comando (con timeout, allowlist binarios)
                       SOLO si OPS_SHELL_ALLOWED=True (default False)

Audit:
  ~/.eidos/hub/audit.log (lado hub)
  ~/.eidos/clone/audit.log (lado clon)
  Cada entrada: timestamp, clone_id, cmd, args_summary, result_status.
"""
from __future__ import annotations

import os
import sys
import json
import time
import shlex
import secrets
import platform
import subprocess
import logging
from pathlib import Path
from typing import Optional, Any

log = logging.getLogger("eidos.cmd_channel")

HUB_DIR   = Path(os.path.expanduser("~/.eidos/hub"))
CLONE_DIR = Path(os.path.expanduser("~/.eidos/clone"))
AUDIT_HUB   = HUB_DIR / "audit.log"
AUDIT_CLONE = CLONE_DIR / "audit.log"

OPS_SHELL_ALLOWED = os.environ.get("EIDOS_CLONE_SHELL", "0") == "1"
MAX_FILE_BYTES = 64 * 1024
SHELL_TIMEOUT = 15
# Binarios shell permitidos (NO incluye sudo, rm -rf, etc.)
SHELL_ALLOWLIST = {
    "ls", "pwd", "whoami", "uname", "uptime", "df", "free",
    "ip", "hostname", "date", "cat", "head", "tail", "wc",
    "grep", "find", "ps", "echo",
}


# ── Audit (rotación simple por tamaño) ────────────────────────────────────

def _audit(side: str, entry: dict) -> None:
    path = AUDIT_HUB if side == "hub" else AUDIT_CLONE
    path.parent.mkdir(parents=True, exist_ok=True)
    entry = {"ts": time.time(), **entry}
    try:
        # Rotación simple: si >2MB, renombrar a .old (max 2 ficheros)
        if path.exists() and path.stat().st_size > 2 * 1024 * 1024:
            old = path.with_suffix(".log.old")
            if old.exists():
                old.unlink()
            path.rename(old)
        with open(path, "a") as f:
            f.write(json.dumps(entry, default=str) + "\n")
        os.chmod(path, 0o600)
    except Exception as e:  # noqa: BLE001
        log.warning("audit write fail (%s): %s", side, e)


# ── Hub side: construir request firmado ────────────────────────────────────

def build_request(clone_id: str, kind: str, args: dict) -> tuple[dict, str]:
    """Construye {payload, signature_hex} firmado por la PRIVADA del hub."""
    try:
        from core.eidos_hub import sign_message
    except ImportError:
        sys.path.insert(0, os.path.dirname(
            os.path.dirname(os.path.abspath(__file__))))
        from core.eidos_hub import sign_message
    payload = {
        "kind": kind,
        "clone_id": clone_id,
        "args": args or {},
        "nonce": secrets.token_hex(16),
        "ts": time.time(),
    }
    canon = json.dumps(payload, sort_keys=True,
                       separators=(",", ":")).encode()
    sig = sign_message(canon).hex()
    _audit("hub", {"event": "send", "clone_id": clone_id, "kind": kind,
                   "args_keys": list((args or {}).keys()),
                   "nonce": payload["nonce"]})
    return payload, sig


# ── Hub side: verificar respuesta del clon ────────────────────────────────

def verify_response(clone_id: str, response_payload: dict,
                    response_sig_hex: str) -> bool:
    try:
        from core.eidos_hub import verify_clone_sig, touch_clone
    except ImportError:
        sys.path.insert(0, os.path.dirname(
            os.path.dirname(os.path.abspath(__file__))))
        from core.eidos_hub import verify_clone_sig, touch_clone
    canon = json.dumps(response_payload, sort_keys=True,
                       separators=(",", ":")).encode()
    ok = verify_clone_sig(clone_id, canon, bytes.fromhex(response_sig_hex))
    if ok:
        touch_clone(clone_id)
    _audit("hub", {"event": "recv", "clone_id": clone_id, "ok": ok,
                   "nonce_echo": response_payload.get("nonce_echo")})
    return ok


# ── Clone side: handler de request entrante ───────────────────────────────

def handle_request_at_clone(request_payload: dict,
                            request_sig_hex: str) -> tuple[dict, str]:
    """Verifica firma del hub → ejecuta comando whitelisteado →
    devuelve respuesta firmada por la PRIVADA del clon."""
    try:
        from core.eidos_clone_agent import (load_identity, sign_payload,
            load_priv as clone_priv)
    except ImportError:
        sys.path.insert(0, os.path.dirname(
            os.path.dirname(os.path.abspath(__file__))))
        from core.eidos_clone_agent import (load_identity, sign_payload,
            load_priv as clone_priv)
    ident = load_identity()
    if not ident:
        return _err_response("clon no registrado", request_payload), \
               _sign_err()

    # Verificar firma del hub
    from cryptography.hazmat.primitives import serialization
    from cryptography.exceptions import InvalidSignature
    try:
        hub_pub = serialization.load_pem_public_key(
            ident["hub_pub_key_pem"].encode())
        canon = json.dumps(request_payload, sort_keys=True,
                           separators=(",", ":")).encode()
        hub_pub.verify(bytes.fromhex(request_sig_hex), canon)  # type: ignore[attr-defined]
    except (InvalidSignature, Exception) as e:  # noqa: BLE001
        _audit("clone", {"event": "reject_sig", "reason": str(e)[:80]})
        return _err_response(f"firma hub inválida: {e}", request_payload), \
               _sign_err()

    # Verificar clone_id coincide con nosotros
    if request_payload.get("clone_id") != ident["clone_id"]:
        _audit("clone", {"event": "reject_wrong_id",
                         "in_payload": request_payload.get("clone_id")})
        return _err_response("clone_id no coincide", request_payload), \
               _sign_err()

    # Anti-replay simple: nonce nuevo + ts no muy viejo (60s window)
    if abs(time.time() - request_payload.get("ts", 0)) > 60:
        _audit("clone", {"event": "reject_stale", "ts": request_payload.get("ts")})
        return _err_response("ts demasiado viejo o futuro",
                              request_payload), _sign_err()

    # Ejecutar comando whitelisteado
    kind = request_payload.get("kind")
    args = request_payload.get("args", {}) or {}
    _audit("clone", {"event": "exec", "kind": kind,
                     "args_keys": list(args.keys())})
    try:
        data = _execute(kind, args)
        ok = True; err = None
    except Exception as e:  # noqa: BLE001
        data = None
        ok = False; err = str(e)
        _audit("clone", {"event": "exec_error", "kind": kind,
                         "error": str(e)[:200]})

    resp = {
        "ok": ok,
        "data": data,
        "error": err,
        "ts": time.time(),
        "nonce_echo": request_payload.get("nonce"),
    }
    canon_resp = json.dumps(resp, sort_keys=True,
                            separators=(",", ":")).encode()
    sig = clone_priv().sign(canon_resp).hex()
    return resp, sig


def _err_response(msg: str, req: dict) -> dict:
    return {"ok": False, "data": None, "error": msg, "ts": time.time(),
            "nonce_echo": req.get("nonce")}


def _sign_err() -> str:
    """Firma errores con la clave del clon (si existe), para que el
    hub pueda verificar al menos que el rechazo viene del clon real."""
    try:
        from core.eidos_clone_agent import load_priv
        return load_priv().sign(b"error").hex()
    except Exception:  # noqa: BLE001
        return ""


# ── Ejecutores allowlist ──────────────────────────────────────────────────

def _execute(kind: str, args: dict) -> Any:
    if kind == "ping":
        return {"pong": True, "host": platform.node(),
                "os": platform.system(), "ts": time.time()}

    if kind == "system_info":
        try:
            import shutil
            du = shutil.disk_usage(os.path.expanduser("~"))
            try:
                with open("/proc/meminfo") as f:
                    mem_kb = int(next(l for l in f if l.startswith(
                        "MemTotal:")).split()[1])
                ram_gb = round(mem_kb / 1024 / 1024, 2)
            except Exception:  # noqa: BLE001
                ram_gb = None
            return {
                "host": platform.node(),
                "system": platform.system(),
                "release": platform.release(),
                "machine": platform.machine(),
                "cpu_count": os.cpu_count(),
                "ram_gb": ram_gb,
                "disk_home_free_gb": round(du.free / 1024**3, 2),
                "disk_home_total_gb": round(du.total / 1024**3, 2),
                "python": platform.python_version(),
            }
        except Exception as e:  # noqa: BLE001
            raise RuntimeError(f"system_info error: {e}")

    if kind == "list_dir":
        path = os.path.expanduser(args.get("path", "~"))
        max_n = int(args.get("max_entries", 200))
        if not os.path.isdir(path):
            raise FileNotFoundError(f"no es directorio: {path}")
        entries = []
        for name in sorted(os.listdir(path))[:max_n]:
            full = os.path.join(path, name)
            try:
                st = os.stat(full)
                entries.append({
                    "name": name,
                    "is_dir": os.path.isdir(full),
                    "size": st.st_size,
                    "mtime": st.st_mtime,
                })
            except Exception:  # noqa: BLE001
                entries.append({"name": name, "error": True})
        return {"path": path, "entries": entries, "truncated_at": max_n}

    if kind == "read_file":
        path = os.path.expanduser(args.get("path", ""))
        max_b = min(int(args.get("max_bytes", MAX_FILE_BYTES)), MAX_FILE_BYTES)
        if not os.path.isfile(path):
            raise FileNotFoundError(f"no es fichero: {path}")
        with open(path, "rb") as f:
            data = f.read(max_b)
        try:
            text = data.decode("utf-8")
            return {"path": path, "encoding": "utf-8", "content": text,
                    "size_total": os.path.getsize(path),
                    "truncated": os.path.getsize(path) > len(data)}
        except UnicodeDecodeError:
            import base64
            return {"path": path, "encoding": "base64",
                    "content_b64": base64.b64encode(data).decode(),
                    "size_total": os.path.getsize(path),
                    "truncated": os.path.getsize(path) > len(data)}

    if kind == "run_shell":
        if not OPS_SHELL_ALLOWED:
            raise PermissionError("shell deshabilitado en este clon "
                                  "(set EIDOS_CLONE_SHELL=1 para activar)")
        cmd_str = args.get("cmd", "")
        try:
            parts = shlex.split(cmd_str)
        except ValueError as e:
            raise ValueError(f"comando inválido: {e}")
        if not parts:
            raise ValueError("cmd vacío")
        if parts[0] not in SHELL_ALLOWLIST:
            raise PermissionError(
                f"binario '{parts[0]}' fuera de allowlist {sorted(SHELL_ALLOWLIST)}")
        r = subprocess.run(parts, capture_output=True, text=True,
                           timeout=SHELL_TIMEOUT)
        return {"cmd": cmd_str, "returncode": r.returncode,
                "stdout": r.stdout[-8000:], "stderr": r.stderr[-4000:]}

    # Fcompute (S58): worker LLM distribuido sobre Ollama local del huésped.
    # Default enabled=false en cuota; el huésped opta-in explícitamente con
    # `eidos-compute enable`. Backup previo a esta extensión:
    #   core/eidos_command_channel.py.pre-fcompute.bak
    if kind == "compute_inference":
        try:
            from core.eidos_compute_worker import handle_compute_inference
        except ImportError:
            sys.path.insert(0, os.path.dirname(
                os.path.dirname(os.path.abspath(__file__))))
            from core.eidos_compute_worker import handle_compute_inference
        return handle_compute_inference(args, requester="hub")

    # ── F5 (S61): screen_capture — DOBLE-GATEADO ────────────────────────────
    # 1. owner_policy.never_share_screen_to_hub debe estar relajado (false).
    # 2. ~/.eidos/clone/role.txt debe contener literalmente "own_device".
    # Si CUALQUIERA falla → NACK con razón clara, audit en ambos.
    # En clones de terceros (Oleh, etc.) este kind devuelve siempre NACK
    # porque no se marca own_device.
    if kind == "screen_capture":
        try:
            from core.eidos_owner_policy import is_constraint_enforced
        except ImportError:
            sys.path.insert(0, os.path.dirname(
                os.path.dirname(os.path.abspath(__file__))))
            from core.eidos_owner_policy import is_constraint_enforced

        # Gate 1: owner_policy
        if is_constraint_enforced("never_share_screen_to_hub"):
            raise PermissionError(
                "screen_capture bloqueado por owner_policy "
                "(never_share_screen_to_hub=enforced). SER puede relajarlo con: "
                "python3 core/eidos_owner_policy.py set never_share_screen_to_hub "
                "false 'nota auditoria'")

        # Gate 2: rol del clon
        role_path = os.path.expanduser("~/.eidos/clone/role.txt")
        try:
            role = open(role_path).read().strip()
        except FileNotFoundError:
            raise PermissionError(
                "screen_capture bloqueado: rol del clon no marcado. "
                "Para clones OWN_DEVICE crea ~/.eidos/clone/role.txt con texto 'own_device'. "
                "Para terceros este kind siempre rechaza.")
        if role != "own_device":
            raise PermissionError(
                f"screen_capture bloqueado: rol='{role}', se requiere 'own_device'")

        # OK, gates pasados. Captura.
        import subprocess as _sp
        import base64 as _b64
        import tempfile as _tf
        display = os.environ.get("DISPLAY", ":0")
        env = {**os.environ, "DISPLAY": display}
        format_arg = (args.get("format") or "png").lower()
        if format_arg not in ("png", "jpg"):
            format_arg = "png"
        with _tf.NamedTemporaryFile(suffix="." + format_arg, delete=False) as tmp:
            tmp_path = tmp.name
        try:
            r = _sp.run(["scrot", "--silent", tmp_path],
                        capture_output=True, timeout=10, env=env)
            if r.returncode != 0 or not os.path.exists(tmp_path) or os.path.getsize(tmp_path) == 0:
                _sp.run(["import", "-window", "root", "-display", display, tmp_path],
                        capture_output=True, timeout=10, env=env)
            if not os.path.exists(tmp_path) or os.path.getsize(tmp_path) == 0:
                raise RuntimeError(f"captura falló (scrot rc={r.returncode})")
            data = open(tmp_path, "rb").read()
            # Cap 8MB para no saturar el canal
            if len(data) > 8 * 1024 * 1024:
                raise RuntimeError(f"captura demasiado grande ({len(data)} bytes), cap 8MB")
            return {
                "format": format_arg,
                "size_bytes": len(data),
                "ts": time.time(),
                "content_b64": _b64.b64encode(data).decode(),
                "host": platform.node(),
            }
        finally:
            try:
                os.unlink(tmp_path)
            except OSError:
                pass

    raise ValueError(f"comando desconocido: {kind}")


# ── CLI ────────────────────────────────────────────────────────────────────

def _main() -> int:
    print("Módulo de canal de comandos (Fase 3). Importa las funciones:")
    print("  build_request(clone_id, kind, args) → (payload, sig)")
    print("  handle_request_at_clone(payload, sig) → (resp, sig)")
    print("  verify_response(clone_id, resp, sig) → bool")
    print(f"\nAllowlist actual: {sorted(SHELL_ALLOWLIST)}")
    print(f"Shell habilitado: {OPS_SHELL_ALLOWED}")
    return 0


if __name__ == "__main__":
    raise SystemExit(_main())
