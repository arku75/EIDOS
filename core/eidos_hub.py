"""
core/eidos_hub.py — Hub criptográfico de EIDOS (Fase 1 del clon/portal)
========================================================================
Identidad del hub (Ed25519) + registro de clones + tokens de enrollment.

Diseño honesto (mínimo seguro, no overengineered):
  • Hub tiene 1 keypair Ed25519 (master). Su clave PRIVADA NUNCA sale.
  • Cada clon (en otro PC) genera SU keypair localmente. Su privada
    tampoco sale. Solo intercambian PÚBLICAS.
  • Enrollment: SER ejecuta `issue_token()` en su Kali → token de un
    solo uso, válido 15 min → lo pega manualmente en el clon al
    instalarse. Sin token válido + firma → NO se acepta registro.
  • Registro: el clon manda {pub_key, friendly_name, os_info, token}
    firmado con su privada. Hub verifica token + firma → enrola →
    devuelve hub_pub_key + clone_id firmado por el hub.
  • Después: TODAS las comunicaciones llevan firma del emisor. Hub
    verifica la firma del clon usando la pub_key registrada del clon.
    Clon verifica firmas del hub usando hub_pub_key (que recibió al
    enrolar). Asimetría natural: el clon NUNCA inicia conexión al
    hub fuera de lo que el hub le pide (lo gobierna Fase 2).

Compatible 100% con la Constitución de seguridad de EIDOS (no toca
constitution.toml; no lee SSH keys; no exfiltra). Es identidad propia
del hub para su rol de control de clones — paralelo a la operación
normal de EIDOS.

Persistencia:
  ~/.eidos/hub/hub_ed25519.priv  (chmod 600, NUNCA sale)
  ~/.eidos/hub/hub_ed25519.pub   (chmod 644, share con clones al enrolar)
  ~/.eidos/hub/registry.db       (SQLite: clones registrados)
  ~/.eidos/hub/enroll_tokens.db  (tokens single-use, TTL 15 min)

CLI: `python3 core/eidos_hub.py init|status|issue-token|list-clones|revoke`
"""
from __future__ import annotations

import os
import sys
import json
import time
import sqlite3
import secrets
import logging
from pathlib import Path
from typing import Optional

from cryptography.hazmat.primitives.asymmetric.ed25519 import (
    Ed25519PrivateKey, Ed25519PublicKey)
from cryptography.hazmat.primitives import serialization
from cryptography.exceptions import InvalidSignature
from core.db import get_conn, get_conn_ctx

log = logging.getLogger("eidos.hub")

HUB_DIR = Path(os.path.expanduser("~/.eidos/hub"))
HUB_DIR.mkdir(parents=True, exist_ok=True)
os.chmod(HUB_DIR, 0o700)

PRIV_PATH = HUB_DIR / "hub_ed25519.priv"
PUB_PATH  = HUB_DIR / "hub_ed25519.pub"
REG_DB    = HUB_DIR / "registry.db"
TOK_DB    = HUB_DIR / "enroll_tokens.db"

TOKEN_TTL_SECONDS = 15 * 60   # 15 min enrollment window


# ── Schema persistencia ────────────────────────────────────────────────────

def _init_db() -> None:
    """Crea schema si no existe (idempotente, no destructivo)."""
    with get_conn_ctx(REG_DB) as c:
        c.execute("""CREATE TABLE IF NOT EXISTS clones (
            clone_id      TEXT PRIMARY KEY,
            pub_key_pem   TEXT NOT NULL,
            friendly_name TEXT NOT NULL,
            os_info       TEXT,
            registered_at REAL NOT NULL,
            last_seen     REAL,
            revoked       INTEGER NOT NULL DEFAULT 0,
            metadata      TEXT
        )""")
        c.commit()
    os.chmod(REG_DB, 0o600)
    with get_conn_ctx(TOK_DB) as c:
        c.execute("""CREATE TABLE IF NOT EXISTS tokens (
            token     TEXT PRIMARY KEY,
            issued_at REAL NOT NULL,
            expires_at REAL NOT NULL,
            used      INTEGER NOT NULL DEFAULT 0,
            label     TEXT
        )""")
        c.commit()
    os.chmod(TOK_DB, 0o600)


# ── Identidad del hub ──────────────────────────────────────────────────────

def hub_initialized() -> bool:
    return PRIV_PATH.exists() and PUB_PATH.exists()


def init_hub(force: bool = False) -> dict:
    """Genera el keypair Ed25519 del hub. Idempotente salvo `force`.
    NO sobrescribe sin force (proteger identidad existente)."""
    _init_db()
    if hub_initialized() and not force:
        return {"status": "already_initialized", "pub_path": str(PUB_PATH)}
    priv = Ed25519PrivateKey.generate()
    pub  = priv.public_key()
    PRIV_PATH.write_bytes(priv.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.PKCS8,
        encryption_algorithm=serialization.NoEncryption()))
    PUB_PATH.write_bytes(pub.public_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PublicFormat.SubjectPublicKeyInfo))
    os.chmod(PRIV_PATH, 0o600)
    os.chmod(PUB_PATH, 0o644)
    return {"status": "created", "priv": str(PRIV_PATH), "pub": str(PUB_PATH)}


def load_priv() -> Ed25519PrivateKey:
    return serialization.load_pem_private_key(
        PRIV_PATH.read_bytes(), password=None)  # type: ignore[return-value]


def load_pub() -> Ed25519PublicKey:
    return serialization.load_pem_public_key(
        PUB_PATH.read_bytes())  # type: ignore[return-value]


def hub_pub_pem() -> str:
    return PUB_PATH.read_text()


def sign_message(payload: bytes) -> bytes:
    return load_priv().sign(payload)


def verify_clone_sig(clone_id: str, payload: bytes, sig: bytes) -> bool:
    """Verifica una firma de un clon registrado usando SU pub_key."""
    info = get_clone(clone_id)
    if not info or info.get("revoked"):
        return False
    try:
        pub = serialization.load_pem_public_key(info["pub_key_pem"].encode())
        pub.verify(sig, payload)  # type: ignore[attr-defined]
        return True
    except (InvalidSignature, Exception):  # noqa: BLE001
        return False


# ── Tokens de enrollment ───────────────────────────────────────────────────

def issue_token(label: str = "") -> dict:
    """Emite token de un solo uso (TTL 15 min). SER lo pega en el clon."""
    _init_db()
    if not hub_initialized():
        raise RuntimeError("hub no inicializado — corre init_hub() primero")
    tok = secrets.token_urlsafe(32)
    now = time.time()
    exp = now + TOKEN_TTL_SECONDS
    with get_conn_ctx(TOK_DB) as c:
        c.execute("INSERT INTO tokens VALUES (?,?,?,0,?)",
                  (tok, now, exp, label))
        c.commit()
    return {"token": tok, "expires_in_sec": TOKEN_TTL_SECONDS,
            "label": label}


def consume_token(token: str) -> bool:
    """Valida y marca como usado. Single-use + TTL."""
    _init_db()
    with get_conn_ctx(TOK_DB) as c:
        row = c.execute(
            "SELECT used, expires_at FROM tokens WHERE token=?",
            (token,)).fetchone()
        if not row:
            return False
        used, exp = row
        if used or time.time() > exp:
            return False
        c.execute("UPDATE tokens SET used=1 WHERE token=?", (token,))
        c.commit()
    return True


# ── Registro de clones ─────────────────────────────────────────────────────

def register_clone(register_payload: dict, signature_hex: str) -> dict:
    """Procesa una solicitud de registro de un clon.
    register_payload: {
        clone_pub_key_pem: str,
        friendly_name:     str,
        os_info:           str,   # "Linux Debian", "macOS 14", "Win 11"...
        enroll_token:      str,
        nonce:             str    # anti-replay
    }
    El payload va firmado con la PRIVADA del propio clon (auto-prueba
    de posesión). signature_hex = hex(sign(canonical_json(payload))).
    Devuelve clone_id + hub_pub_pem (lo que el clon necesita guardar).
    """
    _init_db()
    if not hub_initialized():
        raise RuntimeError("hub no inicializado")
    required = {"clone_pub_key_pem", "friendly_name", "os_info",
                "enroll_token", "nonce"}
    if not required.issubset(register_payload):
        return {"ok": False, "error": "campos requeridos faltantes"}
    if not consume_token(register_payload["enroll_token"]):
        return {"ok": False, "error": "token invalido o expirado"}
    # Self-prueba de posesión: verificar firma del payload con su pub
    try:
        clone_pub = serialization.load_pem_public_key(
            register_payload["clone_pub_key_pem"].encode())
        canon = json.dumps(register_payload, sort_keys=True,
                           separators=(",", ":")).encode()
        clone_pub.verify(bytes.fromhex(signature_hex), canon)  # type: ignore[attr-defined]
    except (InvalidSignature, Exception) as e:  # noqa: BLE001
        return {"ok": False, "error": f"firma inválida: {e}"}
    # Persistir
    clone_id = "clone_" + secrets.token_hex(8)
    now = time.time()
    with get_conn_ctx(REG_DB) as c:
        c.execute("""INSERT INTO clones
            (clone_id, pub_key_pem, friendly_name, os_info,
             registered_at, last_seen, revoked, metadata)
            VALUES (?,?,?,?,?,NULL,0,?)""",
            (clone_id, register_payload["clone_pub_key_pem"],
             register_payload["friendly_name"],
             register_payload["os_info"], now, "{}"))
        c.commit()
    log.info("clon registrado: %s (%s, %s)", clone_id,
             register_payload["friendly_name"],
             register_payload["os_info"])
    return {"ok": True, "clone_id": clone_id,
            "hub_pub_key_pem": hub_pub_pem(),
            "registered_at": now}


def get_clone(clone_id: str) -> Optional[dict]:
    _init_db()
    with get_conn_ctx(REG_DB) as c:
        r = c.execute("""SELECT clone_id, pub_key_pem, friendly_name,
            os_info, registered_at, last_seen, revoked, metadata
            FROM clones WHERE clone_id=?""", (clone_id,)).fetchone()
    if not r:
        return None
    return {"clone_id": r[0], "pub_key_pem": r[1], "friendly_name": r[2],
            "os_info": r[3], "registered_at": r[4], "last_seen": r[5],
            "revoked": bool(r[6]), "metadata": r[7]}


def list_clones(include_revoked: bool = False) -> list[dict]:
    _init_db()
    with get_conn_ctx(REG_DB) as c:
        q = ("SELECT clone_id, friendly_name, os_info, registered_at, "
             "last_seen, revoked FROM clones")
        if not include_revoked:
            q += " WHERE revoked=0"
        q += " ORDER BY registered_at DESC"
        rows = c.execute(q).fetchall()
    return [{"clone_id": r[0], "friendly_name": r[1], "os_info": r[2],
             "registered_at": r[3], "last_seen": r[4],
             "revoked": bool(r[5])} for r in rows]


def revoke_clone(clone_id: str) -> bool:
    _init_db()
    with get_conn_ctx(REG_DB) as c:
        cur = c.execute("UPDATE clones SET revoked=1 WHERE clone_id=?",
                        (clone_id,))
        c.commit()
        return cur.rowcount > 0


def touch_clone(clone_id: str) -> None:
    _init_db()
    with get_conn_ctx(REG_DB) as c:
        c.execute("UPDATE clones SET last_seen=? WHERE clone_id=?",
                  (time.time(), clone_id))
        c.commit()


def status() -> dict:
    return {
        "initialized": hub_initialized(),
        "hub_dir": str(HUB_DIR),
        "pub_path": str(PUB_PATH) if PUB_PATH.exists() else None,
        "clones": len(list_clones()),
        "clones_revoked": len(
            [c for c in list_clones(include_revoked=True) if c["revoked"]]),
    }


# ── CLI ────────────────────────────────────────────────────────────────────

def _main() -> int:
    if len(sys.argv) < 2:
        print("Uso: python3 core/eidos_hub.py "
              "init|status|issue-token [label]|list-clones|revoke <id>")
        return 0
    cmd = sys.argv[1]
    if cmd == "init":
        r = init_hub(force="--force" in sys.argv)
        print(json.dumps(r, indent=2))
    elif cmd == "status":
        print(json.dumps(status(), indent=2))
    elif cmd == "issue-token":
        label = sys.argv[2] if len(sys.argv) > 2 else ""
        r = issue_token(label)
        print(json.dumps(r, indent=2))
        print("\n>>> PEGA ESTE TOKEN EN EL CLON AL INSTALARSE (15 min TTL) <<<")
    elif cmd == "list-clones":
        rows = list_clones(include_revoked="--all" in sys.argv)
        print(json.dumps(rows, indent=2, default=str))
    elif cmd == "revoke":
        if len(sys.argv) < 3:
            print("revoke requiere <clone_id>"); return 1
        ok = revoke_clone(sys.argv[2])
        print(json.dumps({"revoked": ok, "clone_id": sys.argv[2]}))
    else:
        print(f"comando desconocido: {cmd}")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(_main())
