"""
core/eidos_service_onboard.py — EIDOS se registra SOLO en servicios [S122-I]
============================================================================
SER: "no lo haré yo sino EIDOS, así aprenderá cómo registrarse en otros lados,
coger la API key y añadírsela él mismo; si no sabe, que investigue. EIDOS tiene
que ser capaz de TODO."

Capacidad reutilizable: EIDOS crea su cuenta en un servicio web, obtiene una
API key, y la guarda en ~/.eidos/secrets.env — sin intervención de SER.

Patrón general (aplicado a n8n, extensible a otros):
  1. Detectar si el servicio necesita registro (setup).
  2. Registrar owner/cuenta con las credenciales de EIDOS (secrets.env:
     EIDOS_LOGIN_EMAIL / EIDOS_LOGIN_PASSWORD).
  3. Obtener una API key vía la API del servicio.
  4. Guardar la key en secrets.env (NUNCA en logs).
  5. Verificar que la key funciona.

Credenciales: SIEMPRE de secrets.env. La API key se guarda ahí. NUNCA se loguea
ningún secreto (solo se reporta 'configurada/ok', nunca el valor).
"""

from __future__ import annotations

import json
import logging
import os
import re
import urllib.request
from pathlib import Path
from typing import Dict, Optional, Tuple

log = logging.getLogger("eidos.onboard")

SECRETS = Path.home() / ".eidos" / "secrets.env"
_BROWSER_UA = "Mozilla/5.0 (X11; Linux x86_64) EIDOS/1.0"


# ── Utilidades de secrets ───────────────────────────────────────────────────

def _read_secret(key: str) -> str:
    if not SECRETS.exists():
        return ""
    for line in SECRETS.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line.startswith(key + "="):
            return line.split("=", 1)[1].strip().strip('"').strip("'")
    return ""


def _write_secret(key: str, value: str) -> None:
    """Guarda/actualiza una clave en secrets.env (chmod 600). Sin loguear el valor."""
    lines = []
    found = False
    if SECRETS.exists():
        for line in SECRETS.read_text(encoding="utf-8").splitlines():
            if line.strip().startswith(key + "="):
                lines.append(f"{key}={value}")
                found = True
            else:
                lines.append(line)
    if not found:
        lines.append(f"{key}={value}")
    SECRETS.write_text("\n".join(lines) + "\n", encoding="utf-8")
    os.chmod(SECRETS, 0o600)
    log.info("secret %s guardado en secrets.env (valor oculto)", key)


# ── HTTP helper con cookies (para flujos de login web) ──────────────────────

def _http(url: str, method: str = "GET", data: Optional[dict] = None,
          headers: Optional[dict] = None, cookie: str = "",
          timeout: int = 15) -> Tuple[int, dict, str]:
    """Petición HTTP. Devuelve (status, json_body, set_cookie)."""
    h = {"Content-Type": "application/json", "User-Agent": _BROWSER_UA}
    if cookie:
        h["Cookie"] = cookie
    if headers:
        h.update(headers)
    body = json.dumps(data).encode() if data is not None else None
    req = urllib.request.Request(url, data=body, headers=h, method=method)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            raw = r.read().decode("utf-8", errors="ignore")
            set_cookie = r.headers.get("Set-Cookie", "")
            try:
                jb = json.loads(raw) if raw else {}
            except json.JSONDecodeError:
                jb = {"_raw": raw}
            return r.status, jb, set_cookie
    except urllib.error.HTTPError as e:
        raw = e.read().decode("utf-8", errors="ignore")
        try:
            jb = json.loads(raw) if raw else {}
        except json.JSONDecodeError:
            jb = {"_raw": raw}
        return e.code, jb, ""
    except Exception as e:  # noqa: BLE001
        return 0, {"error": str(e)}, ""


def _gen_password() -> str:
    """Genera una contraseña válida (n8n: 8+, mayúscula, minúscula, número)."""
    import secrets as _s
    import string
    alpha = string.ascii_letters + string.digits
    while True:
        p = "Eidos" + "".join(_s.choice(alpha) for _ in range(10))
        if re.search(r"[A-Z]", p) and re.search(r"[a-z]", p) and re.search(r"[0-9]", p):
            return p


# ── n8n: registro + API key ─────────────────────────────────────────────────

def onboard_n8n(base: str = "http://localhost:5678") -> Dict:
    """EIDOS se registra en n8n y obtiene una API key, guardándola en secrets.

    Returns: {ok, steps, api_key_saved, message}
    """
    steps = []
    email = _read_secret("EIDOS_LOGIN_EMAIL")
    password = _read_secret("EIDOS_LOGIN_PASSWORD")
    if not email:
        return {"ok": False, "message": "Sin EIDOS_LOGIN_EMAIL en secrets.env"}
    # n8n exige password fuerte; si la de EIDOS no cumple, genera una y la guarda
    if not (len(password) >= 8 and re.search(r"[A-Z]", password)
            and re.search(r"[a-z]", password) and re.search(r"[0-9]", password)):
        password = _gen_password()
        _write_secret("EIDOS_LOGIN_PASSWORD", password)
        steps.append("password regenerada (no cumplía requisitos n8n) y guardada")

    # 1. ¿Necesita setup?
    st, settings, _ = _http(f"{base}/rest/settings")
    needs_setup = settings.get("data", {}).get("userManagement", {}).get(
        "showSetupOnFirstLoad", False)
    steps.append(f"settings: needs_setup={needs_setup}")

    cookie = ""
    if needs_setup:
        # 2. Crear owner
        st, body, set_cookie = _http(
            f"{base}/rest/owner/setup", method="POST",
            data={"email": email, "firstName": "EIDOS", "lastName": "AI",
                  "password": password})
        if st in (200, 201):
            cookie = set_cookie.split(";")[0] if set_cookie else ""
            steps.append("owner creado ✓")
        else:
            return {"ok": False, "steps": steps,
                    "message": f"setup owner falló (HTTP {st}): {str(body)[:200]}"}
    else:
        # Ya hay owner → login para obtener cookie
        st, body, set_cookie = _http(
            f"{base}/rest/login", method="POST",
            data={"emailOrLdapLoginId": email, "password": password})
        if st == 200:
            cookie = set_cookie.split(";")[0] if set_cookie else ""
            steps.append("login ✓ (owner ya existía)")
        else:
            return {"ok": False, "steps": steps,
                    "message": f"login falló (HTTP {st}); ¿credenciales distintas?"}

    if not cookie:
        return {"ok": False, "steps": steps, "message": "sin cookie de sesión"}

    # 3. Crear API key. n8n v2.x EXIGE: scopes (array de los válidos) + expiresAt
    #    (null = no expira). La key cruda viene en 'rawApiKey' (JWT). [S122-I,
    #    descubierto investigando los 400 que iba devolviendo n8n].
    st_sc, sc_body, _ = _http(f"{base}/rest/api-keys/scopes", cookie=cookie)
    scopes = sc_body.get("data", sc_body) if isinstance(sc_body, dict) else sc_body
    if not isinstance(scopes, list) or not scopes:
        scopes = ["workflow:list", "workflow:read", "workflow:create",
                  "workflow:update", "execution:list", "execution:read"]
    # Borrar keys "eidos*" previas (la rawApiKey solo se da al crear; un label
    # duplicado da 500 por UNIQUE). Así el registro es idempotente.
    st_l, lst, _ = _http(f"{base}/rest/api-keys", cookie=cookie)
    # n8n devuelve {data:{items:[...]}} o {data:[...]} según versión
    _data = lst.get("data", []) if isinstance(lst, dict) else []
    existing = _data.get("items", []) if isinstance(_data, dict) else _data
    _cleaned = 0
    for k in existing:
        if isinstance(k, dict) and str(k.get("label", "")).startswith("eidos"):
            _http(f"{base}/rest/api-keys/{k.get('id')}", method="DELETE", cookie=cookie)
            _cleaned += 1
    if _cleaned:
        steps.append(f"limpiadas {_cleaned} keys 'eidos*' previas")
    st, body, _ = _http(
        f"{base}/rest/api-keys", method="POST", cookie=cookie,
        data={"label": "eidos", "scopes": scopes, "expiresAt": None})
    api_key = ""
    if st in (200, 201):
        d = body.get("data", body)
        api_key = d.get("rawApiKey") or d.get("apiKey") or d.get("key", "")
        steps.append(f"API key creada ✓ ({len(scopes)} scopes)")
    else:
        return {"ok": False, "steps": steps,
                "message": f"crear API key falló (HTTP {st}): {str(body)[:150]}"}

    if not api_key:
        return {"ok": False, "steps": steps, "message": "API key vacía en la respuesta"}

    # 4. Guardar en secrets
    _write_secret("N8N_API_KEY", api_key)
    steps.append("N8N_API_KEY guardada en secrets.env ✓")

    # 5. Verificar
    st, body, _ = _http(f"{base}/api/v1/workflows", headers={"X-N8N-API-KEY": api_key})
    verified = st == 200
    steps.append(f"verificación API key: {'OK' if verified else 'HTTP '+str(st)}")

    return {"ok": verified, "steps": steps, "api_key_saved": True,
            "message": "EIDOS se registró en n8n y configuró su API key" if verified
                       else "registrado pero la verificación de la key falló"}


# ── CLI ──────────────────────────────────────────────────────────────────────
if __name__ == "__main__":
    import sys
    logging.basicConfig(level=logging.INFO)
    svc = sys.argv[1] if len(sys.argv) > 1 else "n8n"
    if svc == "n8n":
        r = onboard_n8n()
        print(json.dumps({k: v for k, v in r.items()}, indent=2, ensure_ascii=False))
    else:
        print(f"Servicio '{svc}' aún no soportado. Disponible: n8n")
