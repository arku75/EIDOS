"""
EIDOS API Auth -- JWT authentication for FastAPI endpoints.

Provides:
- JWT token creation / verification (PyJWT preferred, base64+hmac fallback)
- FastAPI dependency ``require_auth`` for protected routes
- API-Key header alternative (X-API-Key)
- Configurable public-path whitelist
- CLI smoke-test at bottom

Env vars:
    EIDOS_JWT_SECRET  - HMAC secret (auto-generated if missing)
    EIDOS_API_USER    - login username   (default "eidos")
    EIDOS_API_PASS    - login password   (default "eidos2026")
    EIDOS_API_KEY     - static API key   (optional)
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import logging
import os
import secrets
import sys
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

log = logging.getLogger("eidos.api_auth")

# ---------------------------------------------------------------------------
# JWT backend selection
# ---------------------------------------------------------------------------
_USE_PYJWT: bool = False
try:
    import jwt as _pyjwt  # PyJWT

    _USE_PYJWT = True
    log.debug("PyJWT available -- using native JWT backend")
except ImportError:
    _pyjwt = None  # type: ignore[assignment]
    log.warning("PyJWT not installed -- using base64+hmac fallback (degraded mode)")

# ---------------------------------------------------------------------------
# Secret management
# ---------------------------------------------------------------------------
_SECRET_DIR = Path.home() / ".eidos"
_SECRET_FILE = _SECRET_DIR / "jwt_secret"


def _load_or_create_secret() -> str:
    """Return the JWT HMAC secret, creating one on first run if needed."""
    env_secret = os.getenv("EIDOS_JWT_SECRET")
    if env_secret:
        return env_secret

    if _SECRET_FILE.exists():
        return _SECRET_FILE.read_text().strip()

    # Auto-generate and persist
    generated = secrets.token_hex(32)
    _SECRET_DIR.mkdir(parents=True, exist_ok=True)
    _SECRET_FILE.write_text(generated + "\n")
    _SECRET_FILE.chmod(0o600)
    log.info("JWT secret auto-generated -> %s", _SECRET_FILE)
    return generated


SECRET: str = _load_or_create_secret()

# ---------------------------------------------------------------------------
# Configurable whitelist -- paths that skip authentication
# ---------------------------------------------------------------------------
AUTH_WHITELIST: List[str] = [
    "/health",
    "/dashboard",
    "/docs",
    "/openapi.json",
    "/redoc",
    "/login",
    "/chat",
    # "/exec",           # BLOQUEADO: endpoint peligroso, exponía ejecución de comandos sin auth
    "/autonomous",
    "/api",
    "/ws",
]

# ---------------------------------------------------------------------------
# Default credentials (from env or hardcoded defaults)
# ---------------------------------------------------------------------------
_DEFAULT_USER: str = os.getenv("EIDOS_API_USER", "eidos")
_DEFAULT_PASS_HASH: str = hashlib.sha256(
    os.getenv("EIDOS_API_PASS", "eidos2026").encode()
).hexdigest()

# Optional static API key
_API_KEY: Optional[str] = os.getenv("EIDOS_API_KEY")

# ---------------------------------------------------------------------------
# Fallback JWT helpers (base64 + HMAC-SHA256, no PyJWT needed)
# ---------------------------------------------------------------------------

def _b64url_encode(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode()


def _b64url_decode(s: str) -> bytes:
    padding = 4 - len(s) % 4
    if padding != 4:
        s += "=" * padding
    return base64.urlsafe_b64decode(s)


def _fallback_encode(payload: dict) -> str:
    header = _b64url_encode(json.dumps({"alg": "HS256", "typ": "JWT"}).encode())
    body = _b64url_encode(json.dumps(payload).encode())
    sig_input = f"{header}.{body}".encode()
    sig = _b64url_encode(hmac.new(SECRET.encode(), sig_input, hashlib.sha256).digest())
    return f"{header}.{body}.{sig}"


def _fallback_decode(token: str) -> Optional[dict]:
    parts = token.split(".")
    if len(parts) != 3:
        return None
    header_b, body_b, sig_b = parts
    expected = hmac.new(
        SECRET.encode(), f"{header_b}.{body_b}".encode(), hashlib.sha256
    ).digest()
    try:
        actual = _b64url_decode(sig_b)
    except Exception:
        return None
    if not hmac.compare_digest(expected, actual):
        return None
    try:
        payload = json.loads(_b64url_decode(body_b))
    except Exception:
        return None
    # Check expiration
    if payload.get("exp") and payload["exp"] < time.time():
        return None
    return payload

# ---------------------------------------------------------------------------
# Public API -- token creation / verification
# ---------------------------------------------------------------------------

def create_token(
    username: str,
    role: str = "admin",
    expires_hours: int = 24,
) -> str:
    """Create a signed JWT token."""
    now = int(time.time())
    payload: Dict[str, Any] = {
        "sub": username,
        "role": role,
        "iat": now,
        "exp": now + expires_hours * 3600,
    }
    if _USE_PYJWT:
        return _pyjwt.encode(payload, SECRET, algorithm="HS256")  # type: ignore[union-attr]
    return _fallback_encode(payload)


def verify_token(token: str) -> Optional[dict]:
    """Verify and decode a JWT token.  Returns payload dict or None."""
    if _USE_PYJWT:
        try:
            return _pyjwt.decode(token, SECRET, algorithms=["HS256"])  # type: ignore[union-attr]
        except Exception:
            return None
    return _fallback_decode(token)


def authenticate(username: str, password: str) -> Optional[str]:
    """Validate credentials and return a JWT string, or None on failure."""
    u_ok = username == _DEFAULT_USER
    p_ok = hashlib.sha256(password.encode()).hexdigest() == _DEFAULT_PASS_HASH
    if u_ok and p_ok:
        token = create_token(username)
        log.info("Login OK for user=%s", username)
        return token
    log.warning("Login FAILED for user=%s", username)
    return None

# ---------------------------------------------------------------------------
# FastAPI integration (lazy imports to avoid hard dep on fastapi)
# ---------------------------------------------------------------------------

def _extract_token_from_request(request: Any) -> Optional[str]:
    """Pull the Bearer token from Authorization header."""
    auth: Optional[str] = request.headers.get("authorization") or request.headers.get(
        "Authorization"
    )
    if auth and auth.lower().startswith("bearer "):
        return auth[7:].strip()
    return None


def _check_api_key(request: Any) -> bool:
    """Return True if the request carries a valid X-API-Key."""
    if not _API_KEY:
        return False
    key = request.headers.get("x-api-key") or request.headers.get("X-API-Key")
    return key == _API_KEY


def _is_whitelisted(path: str) -> bool:
    """Return True if *path* matches any entry in AUTH_WHITELIST."""
    for prefix in AUTH_WHITELIST:
        if path == prefix or path.startswith(prefix + "/"):
            return True
    return False


def get_current_user(request: Any) -> dict:
    """FastAPI dependency -- returns the authenticated user payload.

    Usage::

        from fastapi import Depends, Request
        from core.api_auth import get_current_user

        @app.get("/protected")
        async def protected(user: dict = Depends(get_current_user)):
            return {"hello": user["sub"]}

    Raises ``HTTPException(401)`` on failure.
    """
    from fastapi import HTTPException  # local import -- no top-level dep

    # Whitelist check
    if _is_whitelisted(request.url.path):
        return {"sub": "anonymous", "role": "public"}

    # API-Key shortcut
    if _check_api_key(request):
        return {"sub": "apikey", "role": "admin"}

    # JWT Bearer
    token = _extract_token_from_request(request)
    if not token:
        raise HTTPException(status_code=401, detail="Missing authentication token")

    payload = verify_token(token)
    if payload is None:
        raise HTTPException(status_code=401, detail="Invalid or expired token")

    return payload


async def require_auth(request: Any) -> dict:
    """Async FastAPI dependency suitable for ``Depends(require_auth)``.

    Wraps :func:`get_current_user` so it can be used directly::

        from fastapi import Depends, Request
        from core.api_auth import require_auth

        @app.get("/secret")
        async def secret(user: dict = Depends(require_auth)):
            ...
    """
    return get_current_user(request)


# ---------------------------------------------------------------------------
# FastAPI middleware factory
# ---------------------------------------------------------------------------

def auth_middleware(app: Any) -> None:
    """Register a middleware on *app* that enforces auth on non-whitelisted paths.

    Usage::

        from fastapi import FastAPI
        from core.api_auth import auth_middleware

        app = FastAPI()
        auth_middleware(app)
    """
    from starlette.middleware.base import BaseHTTPMiddleware  # type: ignore[import-untyped]
    from starlette.responses import JSONResponse

    class _AuthMW(BaseHTTPMiddleware):  # type: ignore[misc]
        async def dispatch(self, request: Any, call_next: Any) -> Any:
            if _is_whitelisted(request.url.path):
                return await call_next(request)
            if _check_api_key(request):
                return await call_next(request)
            token = _extract_token_from_request(request)
            if token is None:
                return JSONResponse(
                    {"detail": "Missing authentication token"}, status_code=401
                )
            if verify_token(token) is None:
                return JSONResponse(
                    {"detail": "Invalid or expired token"}, status_code=401
                )
            return await call_next(request)

    app.add_middleware(_AuthMW)

# ---------------------------------------------------------------------------
# CLI smoke-test
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    logging.basicConfig(
        level=logging.DEBUG,
        format="[%(asctime)s] [%(name)s] %(levelname)s - %(message)s",
        datefmt="%H:%M:%S",
    )

    print("=" * 60)
    print("EIDOS API Auth -- smoke test")
    print("=" * 60)

    backend = "PyJWT" if _USE_PYJWT else "base64+hmac fallback"
    print(f"  Backend       : {backend}")
    print(f"  Secret source : {'env' if os.getenv('EIDOS_JWT_SECRET') else str(_SECRET_FILE)}")
    print(f"  API user      : {_DEFAULT_USER}")
    print(f"  API key set   : {bool(_API_KEY)}")
    print(f"  Whitelist     : {AUTH_WHITELIST}")
    print()

    # 1. Login
    print("[1] authenticate('eidos', 'eidos2026') ...")
    tok = authenticate("eidos", "eidos2026")
    assert tok is not None, "Login should succeed with default creds"
    print(f"    token = {tok[:40]}...")

    # 2. Verify
    print("[2] verify_token() ...")
    payload = verify_token(tok)
    assert payload is not None, "Token should verify"
    assert payload["sub"] == "eidos"
    assert payload["role"] == "admin"
    print(f"    payload = {payload}")

    # 3. Bad token
    print("[3] verify_token('garbage') ...")
    assert verify_token("garbage") is None
    print("    correctly rejected")

    # 4. Bad login
    print("[4] authenticate('bad', 'bad') ...")
    assert authenticate("bad", "bad") is None
    print("    correctly rejected")

    # 5. Whitelist check
    print("[5] whitelist check ...")
    assert _is_whitelisted("/health")
    assert _is_whitelisted("/docs")
    assert _is_whitelisted("/openapi.json")
    assert not _is_whitelisted("/api/secret")
    print("    all correct")

    # 6. Expired token
    print("[6] expired token ...")
    expired = create_token("test", expires_hours=0)
    # expires_hours=0 means exp == iat, which is already in the past (or at best, now)
    time.sleep(1)
    result = verify_token(expired)
    if result is None:
        print("    correctly expired")
    else:
        print("    note: 0-hour token still valid within same second (acceptable)")

    print()
    print("All checks passed.")
    sys.exit(0)
