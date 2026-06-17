"""
core/eidos_tunnel.py — Túnel persistente saliente clon→hub (Fase 2)
======================================================================
Cada clon establece UN túnel reverse-SSH saliente al hub. La asimetría
sale gratis: el clon NUNCA escucha entradas externas; expone un puerto
LOCAL al clon que el hub puede usar A TRAVÉS del túnel ya establecido.

Tecnología: autossh (battle-tested ya en EIDOS para Kali↔Mac). Auto-
reconnect, ServerAliveInterval, key-auth (sin contraseñas).

Puertos asignados a clones: rango 21000-21999 (libre, no colisiona con
los túneles EIDOS existentes -L 11435/7778 ni -R 11436/2222/7779).
El hub asigna el puerto al registrar (extiende registry).

Claves SSH del clon: SEPARADAS de las Ed25519 de identidad — propósitos
distintos:
  • Ed25519 identidad → firmas a nivel aplicación
  • SSH ed25519 → autenticación de transporte SSH
~/.eidos/clone/ssh_id_ed25519 (privada, chmod 600)

Install hub-side (manual, NO automático — seguridad):
  cat clone_ssh.pub >> ~/.ssh/authorized_keys con restricciones:
    command="echo 'tunnel only'",no-pty,no-X11-forwarding,no-agent-forwarding,
    permitlisten="21XXX",no-port-forwarding (excepto el reverse asignado)

NOTA: este módulo construye el comando, gestiona keys, asigna puertos
y firma heartbeats. El test live de red requiere el setup manual de
authorized_keys del hub — está documentado pero NO automatizado por
seguridad (es la decisión consciente de SER). Self-test es dry-run.
"""
from __future__ import annotations

import os
import sys
import json
import time
import shlex
import sqlite3
import subprocess
import logging
from pathlib import Path
from typing import Optional
from core.db import get_conn, get_conn_ctx

log = logging.getLogger("eidos.tunnel")

CLONE_DIR = Path(os.path.expanduser("~/.eidos/clone"))
CLONE_DIR.mkdir(parents=True, exist_ok=True)
SSH_PRIV = CLONE_DIR / "ssh_id_ed25519"
SSH_PUB  = CLONE_DIR / "ssh_id_ed25519.pub"

PORT_RANGE_START = 21000
PORT_RANGE_END   = 21999


# ── SSH keypair del clon (separado de identidad) ───────────────────────────

def ssh_key_initialized() -> bool:
    return SSH_PRIV.exists() and SSH_PUB.exists()


def init_clone_ssh_key(force: bool = False) -> dict:
    """Genera ed25519 SSH keypair para el túnel. Idempotente."""
    if ssh_key_initialized() and not force:
        return {"status": "already_initialized", "pub_path": str(SSH_PUB)}
    if SSH_PRIV.exists():
        SSH_PRIV.unlink()
    if SSH_PUB.exists():
        SSH_PUB.unlink()
    r = subprocess.run(
        ["ssh-keygen", "-t", "ed25519", "-f", str(SSH_PRIV),
         "-N", "", "-C", "eidos-clone-tunnel", "-q"],
        capture_output=True, text=True, timeout=15)
    if r.returncode != 0:
        return {"status": "error", "stderr": r.stderr}
    os.chmod(SSH_PRIV, 0o600)
    os.chmod(SSH_PUB, 0o644)
    return {"status": "created", "priv": str(SSH_PRIV), "pub": str(SSH_PUB),
            "pub_content": SSH_PUB.read_text().strip()}


def ssh_pub_content() -> str:
    return SSH_PUB.read_text().strip() if SSH_PUB.exists() else ""


# ── Asignación de puerto en el hub ─────────────────────────────────────────

def _hub_db_path() -> Path:
    return Path(os.path.expanduser("~/.eidos/hub/registry.db"))


def _ensure_tunnel_schema() -> None:
    """Añade columnas de túnel al registry si faltan (idempotente)."""
    db = _hub_db_path()
    if not db.exists():
        raise RuntimeError("hub registry no existe — corre eidos_hub init")
    with get_conn_ctx(db) as c:
        cols = {r[1] for r in c.execute("PRAGMA table_info(clones)")}
        if "tunnel_port" not in cols:
            c.execute("ALTER TABLE clones ADD COLUMN tunnel_port INTEGER")
        if "ssh_pub" not in cols:
            c.execute("ALTER TABLE clones ADD COLUMN ssh_pub TEXT")
        c.commit()


def assign_tunnel_port(clone_id: str, ssh_pub: str) -> Optional[int]:
    """Hub asigna un puerto libre del rango y guarda SSH pub del clon."""
    _ensure_tunnel_schema()
    db = _hub_db_path()
    with get_conn_ctx(db) as c:
        used = {r[0] for r in c.execute(
            "SELECT tunnel_port FROM clones WHERE tunnel_port IS NOT NULL").fetchall()}
        for p in range(PORT_RANGE_START, PORT_RANGE_END + 1):
            if p not in used:
                c.execute("UPDATE clones SET tunnel_port=?, ssh_pub=? "
                          "WHERE clone_id=?", (p, ssh_pub, clone_id))
                c.commit()
                if c.total_changes:
                    return p
        return None  # rango agotado


def get_tunnel_port(clone_id: str) -> Optional[int]:
    _ensure_tunnel_schema()
    db = _hub_db_path()
    with get_conn_ctx(db) as c:
        row = c.execute("SELECT tunnel_port FROM clones WHERE clone_id=?",
                        (clone_id,)).fetchone()
    return row[0] if row else None


# ── Construcción del comando autossh ───────────────────────────────────────

def build_autossh_cmd(hub_host: str, hub_ssh_user: str, hub_ssh_port: int,
                      tunnel_port: int, local_service_port: int) -> list[str]:
    """Devuelve la línea de comando exacta de autossh que el clon
    ejecuta para mantener el túnel reverso vivo. Sin contraseñas, key
    auth, reconexión automática. -R <tunnel_port>:localhost:<local_service_port>
    expone el puerto local del clon (donde corre su agente de comandos)
    como <tunnel_port> en el hub."""
    return [
        "autossh", "-M", "0", "-N",                       # sin monitor port
        "-o", "ServerAliveInterval=15",
        "-o", "ServerAliveCountMax=3",
        "-o", "ExitOnForwardFailure=no",                  # no morir si reverse falla
        "-o", "StrictHostKeyChecking=accept-new",
        "-o", "ConnectTimeout=10",
        "-o", "TCPKeepAlive=yes",
        "-i", str(SSH_PRIV),
        "-R", f"{tunnel_port}:localhost:{local_service_port}",
        "-p", str(hub_ssh_port),
        f"{hub_ssh_user}@{hub_host}",
    ]


def cmd_to_shell_str(cmd: list[str]) -> str:
    return " ".join(shlex.quote(x) for x in cmd)


# ── Heartbeat firmado (Ed25519 identidad, no SSH) ──────────────────────────

def make_heartbeat_payload(clone_id: str) -> dict:
    return {
        "kind": "heartbeat",
        "clone_id": clone_id,
        "ts": time.time(),
        "uptime_s": int(time.time() - _proc_start_ts()),
    }


def _proc_start_ts() -> float:
    """Aproximación de cuándo arrancó el proceso del clone agent."""
    try:
        return os.path.getmtime("/proc/self/cmdline")
    except Exception:  # noqa: BLE001
        return time.time()


def sign_heartbeat(payload: dict) -> str:
    """Firma con la identidad Ed25519 del clon (no la SSH)."""
    try:
        from core.eidos_clone_agent import sign_payload
    except ImportError:
        sys.path.insert(0, os.path.dirname(
            os.path.dirname(os.path.abspath(__file__))))
        from core.eidos_clone_agent import sign_payload
    return sign_payload(payload)


def verify_heartbeat_at_hub(payload: dict, sig_hex: str) -> bool:
    """Verifica que un heartbeat venga del clon registrado."""
    try:
        from core.eidos_hub import verify_clone_sig, touch_clone
    except ImportError:
        sys.path.insert(0, os.path.dirname(
            os.path.dirname(os.path.abspath(__file__))))
        from core.eidos_hub import verify_clone_sig, touch_clone
    canon = json.dumps(payload, sort_keys=True,
                       separators=(",", ":")).encode()
    if verify_clone_sig(payload.get("clone_id", ""), canon,
                        bytes.fromhex(sig_hex)):
        touch_clone(payload["clone_id"])
        return True
    return False


# ── Status ─────────────────────────────────────────────────────────────────

def status() -> dict:
    return {
        "ssh_key_initialized": ssh_key_initialized(),
        "ssh_pub_path": str(SSH_PUB) if SSH_PUB.exists() else None,
        "port_range": f"{PORT_RANGE_START}-{PORT_RANGE_END}",
    }


# ── CLI ────────────────────────────────────────────────────────────────────

def _main() -> int:
    if len(sys.argv) < 2:
        print("Uso: python3 core/eidos_tunnel.py "
              "init-ssh|status|build-cmd <hub_host> <hub_user> <hub_ssh_port> "
              "<tunnel_port> <local_svc_port>|assign-port <clone_id>")
        return 0
    cmd = sys.argv[1]
    if cmd == "init-ssh":
        print(json.dumps(init_clone_ssh_key(force="--force" in sys.argv),
                         indent=2))
    elif cmd == "status":
        print(json.dumps(status(), indent=2))
    elif cmd == "build-cmd":
        if len(sys.argv) < 7:
            print("build-cmd <hub_host> <hub_user> <hub_ssh_port> "
                  "<tunnel_port> <local_svc_port>")
            return 1
        c = build_autossh_cmd(sys.argv[2], sys.argv[3], int(sys.argv[4]),
                              int(sys.argv[5]), int(sys.argv[6]))
        print(cmd_to_shell_str(c))
    elif cmd == "assign-port":
        if len(sys.argv) < 3:
            print("assign-port <clone_id>"); return 1
        p = assign_tunnel_port(sys.argv[2], ssh_pub_content())
        print(json.dumps({"clone_id": sys.argv[2], "tunnel_port": p}))
    else:
        print(f"comando desconocido: {cmd}")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(_main())
