"""
core/eidos_clone_agent.py — Agente clon (lado clon) — Fase 1
=============================================================
Identidad criptográfica del CLON + builder del request de registro.

Diseño (espejo de eidos_hub.py):
  • El clon genera SU keypair Ed25519 al instalarse.
  • La PRIVADA del clon NUNCA sale del PC del clon.
  • Para registrarse: SER pega el token de enrollment que generó en
    el hub (con `eidos_hub.py issue-token`). El clon construye el
    payload, lo firma con su privada, lo manda al hub.
  • Al recibir respuesta del hub, persiste: clone_id asignado +
    hub_pub_key_pem (para verificar después firmas del hub).

Persistencia (en el PC del CLON):
  ~/.eidos/clone/clone_ed25519.priv  (chmod 600, NUNCA sale del PC)
  ~/.eidos/clone/clone_ed25519.pub   (chmod 644)
  ~/.eidos/clone/identity.json       (clone_id + hub_pub_key + metadata)

NOTA: este módulo NO conecta a ninguna red en Fase 1. Solo construye
los payloads firmados. El transporte (HTTP/WSS/SSH-tunnel) viene en
Fase 2. Para pruebas, hay `register_with_hub_local(...)` que llama
directamente al hub local — solo para self-test cuando hub y clone
están en la misma máquina.
"""
from __future__ import annotations

import os
import sys
import json
import time
import platform
import secrets
import logging
from pathlib import Path
from typing import Optional

from cryptography.hazmat.primitives.asymmetric.ed25519 import (
    Ed25519PrivateKey, Ed25519PublicKey)
from cryptography.hazmat.primitives import serialization
from cryptography.exceptions import InvalidSignature

log = logging.getLogger("eidos.clone_agent")

CLONE_DIR = Path(os.path.expanduser("~/.eidos/clone"))
CLONE_DIR.mkdir(parents=True, exist_ok=True)
os.chmod(CLONE_DIR, 0o700)

PRIV_PATH = CLONE_DIR / "clone_ed25519.priv"
PUB_PATH  = CLONE_DIR / "clone_ed25519.pub"
IDENT_PATH = CLONE_DIR / "identity.json"


# ── Identidad del clon ─────────────────────────────────────────────────────

def clone_initialized() -> bool:
    return PRIV_PATH.exists() and PUB_PATH.exists()


def init_clone(force: bool = False) -> dict:
    """Genera el keypair Ed25519 del clon. Idempotente salvo `force`."""
    if clone_initialized() and not force:
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


def clone_pub_pem() -> str:
    return PUB_PATH.read_text()


def sign_payload(payload: dict) -> str:
    """Firma canonical-JSON del payload con la privada del clon.
    Devuelve hex de la firma (transportable como texto)."""
    canon = json.dumps(payload, sort_keys=True,
                       separators=(",", ":")).encode()
    return load_priv().sign(canon).hex()


# ── Detección de entorno (para friendly_name y os_info) ───────────────────

def detect_os_info() -> str:
    try:
        s = platform.system()
        if s == "Linux":
            try:
                d = {}
                for line in open("/etc/os-release"):
                    if "=" in line:
                        k, _, v = line.strip().partition("=")
                        d[k] = v.strip('"')
                return f"Linux {d.get('PRETTY_NAME', d.get('NAME', '?'))}"
            except Exception:  # noqa: BLE001
                return f"Linux {platform.release()}"
        if s == "Darwin":
            return f"macOS {platform.mac_ver()[0]}"
        if s == "Windows":
            return f"Windows {platform.release()}"
        return f"{s} {platform.release()}"
    except Exception:  # noqa: BLE001
        return platform.platform()


def default_friendly_name() -> str:
    try:
        return f"{platform.node()}-eidos-clone"
    except Exception:  # noqa: BLE001
        return f"clone-{secrets.token_hex(3)}"


# ── Construcción del request de registro ──────────────────────────────────

def build_register_request(enroll_token: str,
                           friendly_name: Optional[str] = None,
                           os_info: Optional[str] = None) -> tuple[dict, str]:
    """Construye {payload, signature_hex} listo para enviar al hub."""
    if not clone_initialized():
        raise RuntimeError("clone no inicializado — corre init_clone() antes")
    payload = {
        "clone_pub_key_pem": clone_pub_pem(),
        "friendly_name": friendly_name or default_friendly_name(),
        "os_info": os_info or detect_os_info(),
        "enroll_token": enroll_token,
        "nonce": secrets.token_hex(16),
    }
    sig = sign_payload(payload)
    return payload, sig


# ── Persistencia de identidad asignada (tras registro exitoso) ────────────

def save_identity(clone_id: str, hub_pub_key_pem: str,
                  registered_at: float) -> None:
    IDENT_PATH.write_text(json.dumps({
        "clone_id": clone_id,
        "hub_pub_key_pem": hub_pub_key_pem,
        "registered_at": registered_at,
        "saved_at": time.time(),
    }, indent=2))
    os.chmod(IDENT_PATH, 0o600)


def load_identity() -> Optional[dict]:
    if not IDENT_PATH.exists():
        return None
    try:
        return json.loads(IDENT_PATH.read_text())
    except Exception:  # noqa: BLE001
        return None


# ── Verificar firma del hub (recibida en mensajes futuros) ────────────────

def verify_hub_sig(payload: bytes, sig: bytes) -> bool:
    ident = load_identity()
    if not ident:
        return False
    try:
        pub: Ed25519PublicKey = serialization.load_pem_public_key(  # type: ignore[assignment]
            ident["hub_pub_key_pem"].encode())
        pub.verify(sig, payload)  # type: ignore[attr-defined]
        return True
    except (InvalidSignature, Exception):  # noqa: BLE001
        return False


# ── Registro LOCAL (mismo PC, para self-test) ─────────────────────────────

def register_with_hub_local(enroll_token: str,
                             friendly_name: Optional[str] = None) -> dict:
    """SOLO PARA SELF-TEST: registra contra eidos_hub local (mismo PC).
    En producción Fase 2, el transporte va por la red."""
    try:
        from core.eidos_hub import register_clone
    except ImportError:
        sys.path.insert(0, os.path.dirname(
            os.path.dirname(os.path.abspath(__file__))))
        from core.eidos_hub import register_clone
    payload, sig = build_register_request(enroll_token, friendly_name)
    resp = register_clone(payload, sig)
    if resp.get("ok"):
        save_identity(resp["clone_id"], resp["hub_pub_key_pem"],
                      resp["registered_at"])
    return resp


def status() -> dict:
    ident = load_identity()
    return {
        "initialized": clone_initialized(),
        "clone_dir": str(CLONE_DIR),
        "pub_path": str(PUB_PATH) if PUB_PATH.exists() else None,
        "registered": ident is not None,
        "clone_id": (ident or {}).get("clone_id"),
        "os_info": detect_os_info(),
    }


# ── CLI ────────────────────────────────────────────────────────────────────

def _main() -> int:
    if len(sys.argv) < 2:
        print("Uso: python3 core/eidos_clone_agent.py "
              "init|status|register-local <token> [name]")
        return 0
    cmd = sys.argv[1]
    if cmd == "init":
        print(json.dumps(init_clone(force="--force" in sys.argv), indent=2))
    elif cmd == "status":
        print(json.dumps(status(), indent=2))
    elif cmd == "register-local":
        if len(sys.argv) < 3:
            print("register-local <token> [friendly_name]")
            return 1
        token = sys.argv[2]
        name = sys.argv[3] if len(sys.argv) > 3 else None
        init_clone()           # idempotente
        r = register_with_hub_local(token, name)
        print(json.dumps({k: (v[:60] + "..." if isinstance(v, str)
                              and len(v) > 80 else v)
                          for k, v in r.items()}, indent=2))
    else:
        print(f"comando desconocido: {cmd}")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(_main())
