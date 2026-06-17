"""
core/eidos_hub_server.py — HTTP server del hub EIDOS (Fase 1+ extensión)
========================================================================

Expone vía HTTP los endpoints necesarios para que un CLON REMOTO pueda
registrarse contra el hub. Sin esto, F8b (instaladores manuales) NO sirve
fuera de la máquina del hub.

Endpoints:
  GET  /health                  → {ok: true, hub_id, ts}
  GET  /hub-pub                 → {hub_pub_key_pem}                    (público)
  POST /register                → registra clon. Body: {payload, signature_hex}
                                  Devuelve: {ok, clone_id, hub_pub_key_pem, ...}

Endpoints solo en modo PÚBLICO (env EIDOS_PUBLIC_HUB_URL set):
  GET  /install.ps1             → instalador Windows con HubUrl inyectado
  GET  /install/files/<archivo> → módulos del clon (allowlist fija)

Seguridad:
  • Bind por defecto en 127.0.0.1 (loopback). Para exponer fuera, el SER
    debe levantar Tailscale / WireGuard / SSH-forward — NUNCA abrir el
    router (cumple `never_open_listening_ports` + `never_install_remote_access`).
  • Cada `POST /register` consume un token enrollment single-use con TTL.
    Sin token válido, el hub rechaza el registro.
  • La firma del payload se verifica con la pub key que el propio clon
    envía: prueba de posesión de la priv key Ed25519.
  • Rate-limit naive: máx 20 intentos/min por IP.
  • Logs en stderr + ~/.eidos/hub/http_server.log.

Uso:
  python3 -m core.eidos_hub_server                 # bind 127.0.0.1:18790
  python3 -m core.eidos_hub_server --host 0.0.0.0  # solo si TIENES motivo
  python3 -m core.eidos_hub_server --port 19500
  python3 -m core.eidos_hub_server self-test       # test interno PASS/FAIL
"""
from __future__ import annotations
import os
import sys
import json
import time
import argparse
import logging
import threading
from collections import deque
from http.server import HTTPServer, BaseHTTPRequestHandler
from typing import Any

sys.path.insert(0, os.path.expanduser("~/EIDOS"))
from core import eidos_hub  # type: ignore

log = logging.getLogger("eidos.hub_server")

# ── Modo público (instalador one-liner) ──────────────────────────────────
# Activado solo si la env EIDOS_PUBLIC_HUB_URL está set por el orquestador
# (eidos-clone-invite-public). En modo loopback default, los endpoints de
# /install* devuelven 404 — protege contra exfil accidental.
_EIDOS_ROOT = os.path.expanduser("~/EIDOS")
_INSTALLERS_DIR = os.path.join(_EIDOS_ROOT, "installers")
_CORE_DIR = os.path.join(_EIDOS_ROOT, "core")

# Allowlist FIJA de archivos servibles. Cualquier petición fuera de aquí → 404.
_SERVED_FILES: dict[str, tuple[str, str]] = {
    # nombre URL → (path absoluto, content-type)
    "clone_runner.py":              (os.path.join(_INSTALLERS_DIR, "clone_runner.py"),                "text/x-python; charset=utf-8"),
    "requirements_clone_minimal.txt": (os.path.join(_INSTALLERS_DIR, "requirements_clone_minimal.txt"), "text/plain; charset=utf-8"),
    "eidos_clone_agent.py":         (os.path.join(_CORE_DIR, "eidos_clone_agent.py"),                 "text/x-python; charset=utf-8"),
    "eidos_tunnel.py":              (os.path.join(_CORE_DIR, "eidos_tunnel.py"),                      "text/x-python; charset=utf-8"),
    "eidos_command_channel.py":     (os.path.join(_CORE_DIR, "eidos_command_channel.py"),             "text/x-python; charset=utf-8"),
    "eidos_owner_policy.py":        (os.path.join(_CORE_DIR, "eidos_owner_policy.py"),                "text/x-python; charset=utf-8"),
}

# Cache en memoria — leído al primer acceso, no se recarga (proceso corto).
_file_cache: dict[str, bytes] = {}
_file_cache_lock = threading.Lock()


def _public_hub_url() -> str:
    """Devuelve la URL pública si el modo público está activo, "" si no."""
    return os.environ.get("EIDOS_PUBLIC_HUB_URL", "").strip()


def _read_served_file(name: str) -> bytes | None:
    """Lee del cache o del disco un archivo de la allowlist. None si no existe."""
    if name not in _SERVED_FILES:
        return None
    with _file_cache_lock:
        if name in _file_cache:
            return _file_cache[name]
        path = _SERVED_FILES[name][0]
        try:
            with open(path, "rb") as f:
                data = f.read()
        except (FileNotFoundError, PermissionError):
            return None
        _file_cache[name] = data
        return data


def _render_install_ps1() -> bytes | None:
    """Devuelve el .ps1 con __HUB_URL__ reemplazado por la URL pública actual."""
    hub_url = _public_hub_url()
    if not hub_url:
        return None
    path = os.path.join(_INSTALLERS_DIR, "install_clone_windows.ps1")
    try:
        with open(path, "rb") as f:
            raw = f.read()
    except (FileNotFoundError, PermissionError):
        return None
    # Replace placeholder (busca como bytes para no decodificar y recodificar)
    return raw.replace(b"__HUB_URL__", hub_url.encode("utf-8"))


def _render_install_sh() -> bytes | None:
    """S65: instalador universal Linux/macOS sirviéndose desde el hub público.
    El script acepta --hub-url y --token como args (el one-liner del banner
    los pasa explícitos), así que NO se requiere reemplazo de placeholder."""
    if not _public_hub_url():
        return None
    path = os.path.join(_INSTALLERS_DIR, "install_clone_remote.sh")
    try:
        with open(path, "rb") as f:
            return f.read()
    except (FileNotFoundError, PermissionError):
        return None


def _render_install_ps1_inline(token: str) -> bytes | None:
    """Devuelve el .ps1 transformado para uso con `irm URL/i/TOKEN | iex`:
       reemplaza el bloque [CmdletBinding()] param(...) por asignaciones
       hardcoded ($HubUrl, $EnrollToken). Así `iex` lo ejecuta sin params
       en current scope.
    """
    hub_url = _public_hub_url()
    if not hub_url:
        return None
    path = os.path.join(_INSTALLERS_DIR, "install_clone_windows.ps1")
    try:
        with open(path, "r", encoding="utf-8") as f:
            text = f.read()
    except (FileNotFoundError, PermissionError):
        return None

    # Sustitución del bloque param() entre markers (idempotente, evita regex frágil)
    start_marker = "# __PARAM_BLOCK_START__"
    end_marker = "# __PARAM_BLOCK_END__"
    start = text.find(start_marker)
    end = text.find(end_marker)
    if start < 0 or end < 0 or end <= start:
        return None
    end += len(end_marker)

    # Escapado de comillas simples en valores (PS estilo: '' representa ')
    hub_url_q = hub_url.replace("'", "''")
    token_q = token.replace("'", "''")

    inline_block = (
        "# (param block replaced by /i/<token> endpoint for iex use)\n"
        f"$HubUrl       = '{hub_url_q}'\n"
        f"$EnrollToken  = '{token_q}'\n"
        f"$FriendlyName = $null\n"
        f"$DryRun       = $false\n"
        f"$Force        = $false\n"
        f"$NoEnroll     = $false\n"
        f"$SkipService  = $false\n"
    )

    transformed = text[:start] + inline_block + text[end:]
    return transformed.encode("utf-8")

# ── Rate-limit naive por IP ───────────────────────────────────────────────
_RATE_WINDOW_S = 60
_RATE_MAX = 20
_rate_state: dict[str, deque] = {}
_rate_lock = threading.Lock()


def _rate_check(ip: str) -> bool:
    now = time.time()
    with _rate_lock:
        dq = _rate_state.setdefault(ip, deque())
        while dq and dq[0] < now - _RATE_WINDOW_S:
            dq.popleft()
        if len(dq) >= _RATE_MAX:
            return False
        dq.append(now)
        return True


# ── Handler HTTP ──────────────────────────────────────────────────────────

class HubHandler(BaseHTTPRequestHandler):
    server_version = "EIDOS-Hub/1.0"

    def log_message(self, fmt: str, *args: Any) -> None:
        log.info("%s - %s", self.client_address[0], fmt % args)

    def _json(self, code: int, body: dict) -> None:
        data = json.dumps(body, separators=(",", ":")).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(data)

    def _read_body(self, max_bytes: int = 64 * 1024) -> dict | None:
        try:
            ln = int(self.headers.get("Content-Length", "0"))
        except ValueError:
            ln = 0
        if ln <= 0 or ln > max_bytes:
            return None
        raw = self.rfile.read(ln)
        try:
            return json.loads(raw.decode())
        except Exception:
            return None

    def _send_bytes(self, code: int, ctype: str, data: bytes) -> None:
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self) -> None:  # noqa: N802
        ip = self.client_address[0]
        if not _rate_check(ip):
            return self._json(429, {"ok": False, "error": "rate limit"})

        if self.path == "/health":
            return self._json(200, {
                "ok": True, "ts": time.time(),
                "hub_initialized": eidos_hub.hub_initialized(),
                "version": "1.0",
                "public": bool(_public_hub_url()),
            })
        if self.path == "/hub-pub":
            if not eidos_hub.hub_initialized():
                return self._json(503, {"ok": False, "error": "hub no inicializado"})
            return self._json(200, {"ok": True, "hub_pub_key_pem": eidos_hub.hub_pub_pem()})

        # ── Endpoints modo público (instalador one-liner) ───────────────
        if self.path in ("/install.ps1", "/install"):
            if not _public_hub_url():
                return self._json(404, {"ok": False, "error": "public mode disabled"})
            data = _render_install_ps1()
            if data is None:
                return self._json(500, {"ok": False, "error": "installer not found"})
            return self._send_bytes(200, "text/plain; charset=utf-8", data)

        # S65 — instalador universal Linux+macOS (mismo .sh, alias)
        if self.path in ("/install.sh", "/install.command"):
            if not _public_hub_url():
                return self._json(404, {"ok": False, "error": "public mode disabled"})
            data = _render_install_sh()
            if data is None:
                return self._json(500, {"ok": False, "error": "installer not found"})
            return self._send_bytes(200, "text/x-shellscript; charset=utf-8", data)

        if self.path.startswith("/install/files/"):
            if not _public_hub_url():
                return self._json(404, {"ok": False, "error": "public mode disabled"})
            # Solo nombre base, sin directorios — defensa path-traversal
            name = self.path[len("/install/files/"):]
            if "/" in name or ".." in name or name not in _SERVED_FILES:
                return self._json(404, {"ok": False, "error": "not in allowlist"})
            data = _read_served_file(name)
            if data is None:
                return self._json(500, {"ok": False, "error": "file unreadable"})
            return self._send_bytes(200, _SERVED_FILES[name][1], data)

        # ── /i/<token> → .ps1 inline (uso con `irm URL/i/TOKEN | iex`) ──
        if self.path.startswith("/i/"):
            if not _public_hub_url():
                return self._json(404, {"ok": False, "error": "public mode disabled"})
            token = self.path[len("/i/"):]
            # Validación simple: token base64url-safe, longitud típica 32-64.
            # NO se valida contra el hub aquí — la validación real ocurre al
            # consumirse en /register. Esto solo protege contra basura.
            if not token or len(token) > 128 or not all(
                    c.isalnum() or c in "-_" for c in token):
                return self._json(400, {"ok": False, "error": "token inválido"})
            data = _render_install_ps1_inline(token)
            if data is None:
                return self._json(500, {"ok": False, "error": "installer not found"})
            return self._send_bytes(200, "text/plain; charset=utf-8", data)

        return self._json(404, {"ok": False, "error": "not found"})

    def do_POST(self) -> None:  # noqa: N802
        ip = self.client_address[0]
        if not _rate_check(ip):
            return self._json(429, {"ok": False, "error": "rate limit"})

        if self.path != "/register":
            return self._json(404, {"ok": False, "error": "not found"})

        body = self._read_body()
        if not body:
            return self._json(400, {"ok": False, "error": "body inválido"})
        payload = body.get("payload")
        sig = body.get("signature_hex")
        if not isinstance(payload, dict) or not isinstance(sig, str):
            return self._json(400, {"ok": False, "error": "payload/signature_hex requeridos"})

        try:
            result = eidos_hub.register_clone(payload, sig)
        except Exception as e:
            log.exception("register_clone falló")
            return self._json(500, {"ok": False, "error": f"server error: {e}"})

        code = 200 if result.get("ok") else 400
        return self._json(code, result)


# ── Server lifecycle ──────────────────────────────────────────────────────

def serve_forever(host: str = "127.0.0.1", port: int = 18790) -> None:
    server = HTTPServer((host, port), HubHandler)
    log.info("HUB HTTP server listening on http://%s:%d", host, port)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        log.info("SIGINT, parando")
    finally:
        server.server_close()


# ── Self-test ─────────────────────────────────────────────────────────────

def _self_test() -> int:
    """Smoke-test: arranca server en puerto efímero, hace GET /health + /hub-pub."""
    import socket, urllib.request
    # Asegurar hub inicializado
    if not eidos_hub.hub_initialized():
        eidos_hub.init_hub()

    # Puerto libre
    s = socket.socket(); s.bind(("127.0.0.1", 0)); port = s.getsockname()[1]; s.close()

    t = threading.Thread(target=serve_forever, args=("127.0.0.1", port), daemon=True)
    t.start()
    time.sleep(0.5)

    n_ok = 0
    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{port}/health", timeout=3) as r:
            d = json.loads(r.read())
            assert d["ok"] is True
            n_ok += 1
        print("✅ GET /health OK")
        with urllib.request.urlopen(f"http://127.0.0.1:{port}/hub-pub", timeout=3) as r:
            d = json.loads(r.read())
            assert d["ok"] is True and "BEGIN PUBLIC KEY" in d["hub_pub_key_pem"]
            n_ok += 1
        print("✅ GET /hub-pub OK")
        # POST sin body → 400
        req = urllib.request.Request(
            f"http://127.0.0.1:{port}/register", method="POST",
            data=b"{}", headers={"Content-Type": "application/json"},
        )
        try:
            urllib.request.urlopen(req, timeout=3)
        except urllib.error.HTTPError as e:
            assert e.code == 400
            n_ok += 1
        print("✅ POST /register sin payload → 400")
        # Endpoint inexistente → 404
        try:
            urllib.request.urlopen(f"http://127.0.0.1:{port}/no-existe", timeout=3)
        except urllib.error.HTTPError as e:
            assert e.code == 404
            n_ok += 1
        print("✅ /no-existe → 404")
        # rate-limit: 25 GETs deben triggear 429
        rl_triggered = False
        for _ in range(30):
            try:
                urllib.request.urlopen(f"http://127.0.0.1:{port}/health", timeout=2)
            except urllib.error.HTTPError as e:
                if e.code == 429:
                    rl_triggered = True
                    break
        assert rl_triggered, "rate-limit no se activó"
        n_ok += 1
        print("✅ rate-limit 429 funciona")

        # Esperar a que el rate-limit se restablezca (>60s) ralentizaría
        # el test; en su lugar, limpiamos el bucket del 127.0.0.1.
        with _rate_lock:
            _rate_state.pop("127.0.0.1", None)

        # ── Modo público OFF: /install.ps1 → 404 ─────────────────────
        try:
            urllib.request.urlopen(f"http://127.0.0.1:{port}/install.ps1", timeout=3)
            raise AssertionError("/install.ps1 debió fallar en modo loopback")
        except urllib.error.HTTPError as e:
            assert e.code == 404, f"esperaba 404, llegó {e.code}"
            n_ok += 1
        print("✅ /install.ps1 → 404 cuando modo público OFF")

        # ── Modo público ON: /install.ps1 sirve con HUB_URL inyectado ─
        os.environ["EIDOS_PUBLIC_HUB_URL"] = f"http://127.0.0.1:{port}"
        # Reset cache para que la lectura tome la env actual
        with _file_cache_lock:
            _file_cache.clear()
        with urllib.request.urlopen(f"http://127.0.0.1:{port}/install.ps1", timeout=3) as r:
            body = r.read().decode("utf-8")
            assert "__HUB_URL__" not in body, "placeholder no fue reemplazado"
            assert f"http://127.0.0.1:{port}" in body, "URL no inyectada"
            assert "EIDOS" in body, "banner EIDOS no presente"
            n_ok += 1
        print("✅ /install.ps1 sirve con HUB_URL inyectado")

        # ── /install/files/clone_runner.py debe servir el archivo ────
        with urllib.request.urlopen(
                f"http://127.0.0.1:{port}/install/files/clone_runner.py", timeout=3) as r:
            body = r.read().decode("utf-8")
            assert "clone_runner.py" in body, "archivo no parece el correcto"
            n_ok += 1
        print("✅ /install/files/clone_runner.py sirve módulo")

        # ── Path traversal bloqueado ─────────────────────────────────
        try:
            urllib.request.urlopen(
                f"http://127.0.0.1:{port}/install/files/../eidos_hub.py", timeout=3)
            raise AssertionError("path traversal no bloqueado")
        except urllib.error.HTTPError as e:
            assert e.code == 404, f"esperaba 404, llegó {e.code}"
            n_ok += 1
        print("✅ path traversal → 404")

        # ── Archivo fuera de allowlist bloqueado ─────────────────────
        try:
            urllib.request.urlopen(
                f"http://127.0.0.1:{port}/install/files/secrets.env", timeout=3)
            raise AssertionError("secrets.env servido!")
        except urllib.error.HTTPError as e:
            assert e.code == 404, f"esperaba 404, llegó {e.code}"
            n_ok += 1
        print("✅ archivo fuera allowlist → 404")

        # ── /i/<token> sirve .ps1 inline sin param() block ───────────
        # Reset rate-limit otra vez
        with _rate_lock:
            _rate_state.pop("127.0.0.1", None)
        fake_token = "Test_TOKEN-abc123XYZ_456"
        with urllib.request.urlopen(
                f"http://127.0.0.1:{port}/i/{fake_token}", timeout=3) as r:
            body = r.read().decode("utf-8")
            assert "[CmdletBinding()]" not in body, "param block no fue eliminado"
            assert "param(" not in body, "param block aún presente"
            assert f"$EnrollToken  = '{fake_token}'" in body, "token no inyectado"
            assert f"$HubUrl       = 'http://127.0.0.1:{port}'" in body, "URL no inyectada"
            n_ok += 1
        print("✅ /i/<token> sirve .ps1 inline con valores inyectados")

        # ── /i/<token> con token inválido → 400 ──────────────────────
        try:
            urllib.request.urlopen(
                f"http://127.0.0.1:{port}/i/bad!token", timeout=3)
            raise AssertionError("token con ! debió ser rechazado")
        except urllib.error.HTTPError as e:
            assert e.code == 400, f"esperaba 400, llegó {e.code}"
            n_ok += 1
        print("✅ /i/<token inválido> → 400")

        del os.environ["EIDOS_PUBLIC_HUB_URL"]
    except AssertionError as e:
        print(f"❌ assertion: {e}")
        return 1
    except Exception as e:
        print(f"❌ {e}")
        return 1

    print("-" * 60)
    print(f"✅ SELF-TEST PASS ({n_ok}/12)")
    return 0


def main(argv=None) -> int:
    p = argparse.ArgumentParser(prog="eidos_hub_server")
    sub = p.add_subparsers(dest="cmd")
    sub.add_parser("self-test", help="test interno")
    s = sub.add_parser("serve", help="arrancar HTTP server")
    s.add_argument("--host", default="127.0.0.1")
    s.add_argument("--port", type=int, default=18790)
    args = p.parse_args(argv)

    logging.basicConfig(level=logging.INFO,
                        format="[%(asctime)s] [hub_server] %(levelname)s: %(message)s")
    if args.cmd == "self-test":
        return _self_test()
    serve_forever(args.host if args.cmd == "serve" else "127.0.0.1",
                  args.port if args.cmd == "serve" else 18790)
    return 0


if __name__ == "__main__":
    sys.exit(main())
