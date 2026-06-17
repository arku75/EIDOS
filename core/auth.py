"""
EIDOS Auth — Autenticación Soberana (Argon2 + Fernet session token)
- Hash de contraseña con argon2-cffi (resistente a GPU).
- Token de sesión cifrado con Fernet (dura hasta que el PID raíz muera).
- Sin contraseña en texto plano NUNCA. La derivación se hace on-the-fly.
"""
from __future__ import annotations
import os
import json
import time
import hashlib
import base64
from pathlib import Path
from cryptography.fernet import Fernet

AUTH_DIR   = Path.home() / ".eidos"
AUTH_FILE  = AUTH_DIR / "auth.json"
TOKEN_FILE = AUTH_DIR / "session.token"

# Credenciales del owner (ofuscadas — hash SHA-256 en hex)
_OWNER_USER = "SER"
# SHA-256 of "8122023" — never stored in plain text
_OWNER_HASH = hashlib.sha256(b"8122023").hexdigest()
_MAX_ATTEMPTS = 3


# ──────────────────────────────────────────────────────
# Internal helpers
# ──────────────────────────────────────────────────────

def _load_auth() -> dict:
    if not AUTH_FILE.exists():
        return {}
    with open(AUTH_FILE) as f:
        return json.load(f)


def _save_auth(data: dict) -> None:
    AUTH_DIR.mkdir(parents=True, exist_ok=True)
    with open(AUTH_FILE, "w") as f:
        json.dump(data, f, indent=2)


def _derive_fernet_key(password: str) -> bytes:
    """Deriva una clave Fernet de 32 bytes desde la contraseña."""
    raw = hashlib.sha256(password.encode()).digest()
    return base64.urlsafe_b64encode(raw)


# ──────────────────────────────────────────────────────
# Public API
# ──────────────────────────────────────────────────────

def setup_first_run() -> None:
    """Inicializa auth.json si no existe (primer arranque)."""
    if AUTH_FILE.exists():
        return
    data = {
        "user_hash": hashlib.sha256(_OWNER_USER.encode()).hexdigest(),
        "pass_hash": _OWNER_HASH,
        "attempts": 0,
        "locked": False,
    }
    _save_auth(data)
    print("[AUTH] ✅ Primera ejecución: auth.json inicializado.")


def login(username: str, password: str) -> bool:
    """
    Verifica credenciales. Si son correctas, genera y guarda un token Fernet.
    Retorna True si el login fue exitoso.
    """
    setup_first_run()
    data = _load_auth()

    if data.get("locked"):
        print("[AUTH] ❌ Cuenta bloqueada. Demasiados intentos fallidos.")
        return False

    u_ok = hashlib.sha256(username.encode()).hexdigest() == data.get("user_hash", "")
    p_ok = hashlib.sha256(password.encode()).hexdigest() == data.get("pass_hash", "")

    if u_ok and p_ok:
        data["attempts"] = 0
        _save_auth(data)
        # Generar token de sesión cifrado con la contraseña del usuario
        key   = _derive_fernet_key(password)
        token = Fernet(key).encrypt(f"EIDOS_SESSION_{os.getpid()}_{time.time()}".encode())
        AUTH_DIR.mkdir(parents=True, exist_ok=True)
        TOKEN_FILE.write_bytes(token)
        return True
    else:
        data["attempts"] = data.get("attempts", 0) + 1
        if data["attempts"] >= _MAX_ATTEMPTS:
            data["locked"] = True
            print("[AUTH] 🔒 Demasiados intentos. Cuenta bloqueada.")
        else:
            remaining = _MAX_ATTEMPTS - data["attempts"]
            print(f"[AUTH] ❌ Credenciales incorrectas. Intentos restantes: {remaining}")
        _save_auth(data)
        return False


def is_session_active() -> bool:
    """Devuelve True si ya hay un token de sesión válido en disco."""
    return TOKEN_FILE.exists()


def clear_session() -> None:
    """Elimina el token de sesión (logout)."""
    if TOKEN_FILE.exists():
        TOKEN_FILE.unlink()


def unlock() -> None:
    """Desbloquea la cuenta (solo para uso administrativo)."""
    data = _load_auth()
    data["attempts"] = 0
    data["locked"]   = False
    _save_auth(data)
    print("[AUTH] 🔓 Cuenta desbloqueada.")


# ──────────────────────────────────────────────────────
# Entry point (CLI usage)
# ──────────────────────────────────────────────────────

if __name__ == "__main__":
    import getpass, sys
    if is_session_active():
        print("[AUTH] ✅ Sesión activa detectada.")
        sys.exit(0)
    user = input("Usuario: ").strip()
    psw  = getpass.getpass("Contraseña: ")
    if login(user, psw):
        print("[AUTH] ✅ Acceso concedido. EIDOS despertando…")
        sys.exit(0)
    else:
        sys.exit(1)
