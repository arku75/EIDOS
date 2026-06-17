"""
core/eidos_fleet_dashboard.py — Fleet Dashboard local del HUB (F4)
====================================================================
UI web mínima en stdlib (http.server) para que SER vea desde Kali
todos los clones registrados, su estado, audit, y pueda invocar
comandos firmados de la allowlist F3 (ping, system_info, list_dir,
read_file).

Sólo para LOCALHOST por defecto (bind 127.0.0.1:8765). NO exponer
a la red sin auth — esta UI tiene acceso al hub y firma comandos
con la priv del hub. NUNCA bindear 0.0.0.0 sin gateway adelante.

Diseño honesto:
  • Stdlib pura (http.server, html.escape, urllib). 0 deps nuevas.
  • Las páginas leen DIRECTO de eidos_hub.list_clones / status /
    eidos_tunnel.status / audit logs.
  • Comandos firmados llaman a eidos_command_channel.build_request →
    handle_request_at_clone localmente (en F4 todavía sin túnel real
    al clon remoto; la pieza de transporte vendrá en F4-live / F5).
  • La UI es read-only por defecto. Las acciones (issue-token,
    revoke, run_cmd) están detrás de POST con CSRF mínimo (nonce).

Uso:
  python3 -m core.eidos_fleet_dashboard --host 127.0.0.1 --port 8770
  Abrir http://127.0.0.1:8770 en el browser local de Kali.
  (8765 lo usa vscode_api_server; 8770 libre verificado)

  python3 -m core.eidos_fleet_dashboard self-test
"""
from __future__ import annotations

import argparse
import http.server
import io
import json
import os
import secrets
import socketserver
import sys
import threading
import time
import urllib.parse
from html import escape as H
from pathlib import Path
from typing import Optional

# ── Paths e imports lazy ───────────────────────────────────────────────────

HUB_DIR    = Path(os.path.expanduser("~/.eidos/hub"))
CLONE_DIR  = Path(os.path.expanduser("~/.eidos/clone"))
DASH_STATE = HUB_DIR / "dashboard_state.json"  # CSRF nonces, etc.


def _lazy_imports():
    """Importa los módulos del clon/hub de forma perezosa para que el
    módulo se pueda importar sin dependencias circulares."""
    sys.path.insert(0, "/home/ser/EIDOS")
    from core import eidos_hub             # noqa
    from core import eidos_tunnel          # noqa
    from core import eidos_command_channel # noqa
    return eidos_hub, eidos_tunnel, eidos_command_channel


# ── CSRF / sesión ──────────────────────────────────────────────────────────

_csrf_lock = threading.Lock()
_csrf_tokens: dict[str, float] = {}   # token → ts_emitido
CSRF_TTL = 600  # 10 min


def _new_csrf() -> str:
    tok = secrets.token_urlsafe(24)
    with _csrf_lock:
        _csrf_tokens[tok] = time.time()
        # Limpiar viejos
        now = time.time()
        for k in list(_csrf_tokens):
            if now - _csrf_tokens[k] > CSRF_TTL:
                _csrf_tokens.pop(k, None)
    return tok


def _consume_csrf(tok: str) -> bool:
    with _csrf_lock:
        ts = _csrf_tokens.pop(tok, None)
    if ts is None:
        return False
    return (time.time() - ts) <= CSRF_TTL


# ── HTML templates ─────────────────────────────────────────────────────────

CSS = """
body{font-family:ui-monospace,Menlo,Consolas,monospace;background:#0d1117;color:#c9d1d9;margin:0;padding:0}
header{background:#161b22;padding:16px 24px;border-bottom:1px solid #30363d;display:flex;align-items:center;gap:16px}
header h1{font-size:18px;margin:0;color:#58a6ff}
header nav a{color:#8b949e;text-decoration:none;margin-right:18px;font-size:13px}
header nav a:hover{color:#58a6ff}
main{padding:24px;max-width:1280px;margin:0 auto}
h2{color:#58a6ff;border-bottom:1px solid #30363d;padding-bottom:6px;font-size:16px}
table{border-collapse:collapse;width:100%;margin:12px 0;font-size:13px}
th,td{text-align:left;padding:8px 10px;border-bottom:1px solid #21262d;vertical-align:top}
th{background:#161b22;color:#8b949e;font-weight:500;font-size:11px;text-transform:uppercase;letter-spacing:0.5px}
tr:hover{background:#161b22}
.pill{display:inline-block;padding:2px 8px;border-radius:10px;font-size:11px;font-weight:600}
.pill.ok{background:#1a4d2e;color:#56d364}
.pill.warn{background:#5a3d00;color:#e3b341}
.pill.err{background:#5a1f1f;color:#ff7b72}
.pill.gray{background:#21262d;color:#8b949e}
.mono{font-family:ui-monospace,Menlo,Consolas,monospace;font-size:12px}
a.btn{display:inline-block;background:#238636;color:#fff;padding:6px 12px;border-radius:6px;text-decoration:none;font-size:12px;margin-right:6px}
a.btn:hover{background:#2ea043}
a.btn.danger{background:#da3633}
a.btn.danger:hover{background:#f85149}
a.btn.ghost{background:transparent;border:1px solid #30363d;color:#c9d1d9}
pre{background:#0a0f15;padding:12px;border-radius:6px;overflow-x:auto;font-size:12px;border:1px solid #21262d}
form{display:inline-block}
input[type=text],select,textarea{background:#0d1117;border:1px solid #30363d;color:#c9d1d9;padding:6px 10px;border-radius:4px;font-family:inherit;font-size:13px}
button{background:#238636;color:#fff;border:none;padding:6px 14px;border-radius:6px;font-size:12px;cursor:pointer}
button:hover{background:#2ea043}
.row{display:flex;gap:16px;margin:12px 0}
.card{background:#161b22;border:1px solid #30363d;border-radius:8px;padding:14px;flex:1}
.kv{display:grid;grid-template-columns:auto 1fr;gap:6px 14px;font-size:13px}
.kv dt{color:#8b949e}
.kv dd{margin:0;color:#c9d1d9;font-family:ui-monospace,monospace}
footer{text-align:center;color:#6e7681;font-size:11px;padding:24px}
"""


def _layout(title: str, body: str) -> str:
    return f"""<!doctype html><html><head>
<meta charset="utf-8"><title>{H(title)} · EIDOS Fleet</title>
<style>{CSS}</style></head>
<body>
<header>
  <h1>🧠 EIDOS · Fleet Dashboard</h1>
  <nav>
    <a href="/">Clones</a>
    <a href="/tokens">Tokens</a>
    <a href="/audit">Audit</a>
    <a href="/hub">Hub Status</a>
  </nav>
  <span style="margin-left:auto;color:#6e7681;font-size:11px;">localhost only · F4</span>
</header>
<main>{body}</main>
<footer>EIDOS Fleet · solo localhost · {time.strftime("%Y-%m-%d %H:%M:%S")}</footer>
</body></html>"""


def _status_pill(c: dict) -> str:
    if c.get("revoked"):
        return '<span class="pill err">REVOKED</span>'
    ls = c.get("last_seen")
    if ls is None:
        return '<span class="pill gray">never seen</span>'
    age = time.time() - float(ls)
    if age < 120:
        return '<span class="pill ok">online</span>'
    if age < 3600:
        return f'<span class="pill warn">{int(age/60)}m ago</span>'
    return f'<span class="pill err">{int(age/3600)}h ago</span>'


def _fmt_ts(ts) -> str:
    if not ts:
        return "—"
    try:
        return time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(float(ts)))
    except Exception:
        return str(ts)


# ── Pages ──────────────────────────────────────────────────────────────────

def page_clones() -> str:
    hub, tun, _ = _lazy_imports()
    csrf = _new_csrf()
    rows = hub.list_clones(include_revoked=True)
    if not rows:
        body = "<h2>Clones</h2><p>(ninguno registrado todavía)</p>"
    else:
        head = ("<tr><th>STATUS</th><th>CLONE_ID</th><th>FRIENDLY</th>"
                "<th>OS</th><th>PORT</th><th>REGISTERED</th>"
                "<th>LAST SEEN</th><th>ACTIONS</th></tr>")
        trs = []
        for c in rows:
            cid = c["clone_id"]
            port = tun.get_tunnel_port(cid) or "—"
            short_id = cid.replace("clone_", "")[:12]
            actions = (
                f'<a class="btn ghost" href="/clone/{cid}">detail</a>'
                + (f'<form method="post" action="/clone/{cid}/revoke" '
                   f'style="display:inline">'
                   f'<input type="hidden" name="csrf" value="{csrf}">'
                   f'<button class="" style="background:#da3633" '
                   f'onclick="return confirm(\'Revocar {short_id}?\')">'
                   f'revoke</button></form>' if not c.get("revoked") else "")
            )
            trs.append(
                f"<tr><td>{_status_pill(c)}</td>"
                f"<td class='mono'><a href='/clone/{cid}' "
                f"style='color:#58a6ff'>{H(short_id)}</a></td>"
                f"<td>{H(c.get('friendly_name') or '—')}</td>"
                f"<td>{H(c.get('os_info') or '—')}</td>"
                f"<td class='mono'>{port}</td>"
                f"<td>{_fmt_ts(c.get('registered_at'))}</td>"
                f"<td>{_fmt_ts(c.get('last_seen'))}</td>"
                f"<td>{actions}</td></tr>"
            )
        body = ("<h2>Clones registrados</h2>"
                "<table>" + head + "".join(trs) + "</table>"
                "<p><a class='btn' href='/tokens'>+ emitir nuevo token enrollment</a></p>")
    return _layout("Clones", body)


def page_tokens() -> str:
    hub, _, _ = _lazy_imports()
    csrf = _new_csrf()
    body = f"""
<h2>Tokens de enrollment</h2>
<p>Genera un token <b>single-use TTL 15 min</b> para que SER instale
un nuevo clon en cualquier PC. El token caduca al usarse o tras 15
minutos. NO se guarda; cópialo en el momento.</p>

<form method="post" action="/tokens/issue">
  <input type="hidden" name="csrf" value="{csrf}">
  <label>label (opcional):
    <input type="text" name="label" placeholder="win-juan-portatil"
           style="width:280px"></label>
  <button type="submit">Emitir token</button>
</form>

<h2 style="margin-top:32px;">Instrucciones rápidas</h2>
<pre>1) Click "Emitir token" arriba — copia el token mostrado.
2) En el otro PC: copia los 4 módulos del clon
   (eidos_clone_agent.py, eidos_tunnel.py,
    eidos_command_channel.py, eidos_owner_policy.py)
3) pip install --user cryptography
4) python3 -m core.eidos_clone_agent register-local &lt;TOKEN&gt; "nombre-pc"
5) Vuelve aquí — debería aparecer el clon en /
</pre>
"""
    return _layout("Tokens", body)


def page_audit() -> str:
    body = "<h2>Audit log del hub</h2>"
    audit_paths = [
        ("hub send/recv (command channel)", HUB_DIR / "audit.log"),
        ("hub registry events", HUB_DIR / "registry_audit.log"),
        ("owner_policy toggles", Path(os.path.expanduser(
            "~/.eidos/owner_policy_audit.log"))),
        ("clone side (este PC si es clon)", CLONE_DIR / "audit.log"),
    ]
    for label, p in audit_paths:
        body += f"<h3 style='margin-top:24px;color:#8b949e;font-size:13px;'>{H(label)}</h3>"
        if not p.exists():
            body += f"<p class='mono' style='color:#6e7681'>{H(str(p))} (no existe)</p>"
            continue
        try:
            lines = p.read_text(encoding="utf-8").splitlines()[-50:]
            body += f"<p class='mono' style='color:#6e7681'>{H(str(p))} (últimas {len(lines)} líneas)</p>"
            body += "<pre>" + H("\n".join(lines)) + "</pre>"
        except Exception as e:
            body += f"<p class='mono' style='color:#ff7b72'>error: {H(str(e))}</p>"
    return _layout("Audit", body)


def page_hub_status() -> str:
    hub, tun, _ = _lazy_imports()
    s = hub.status()
    ts = tun.status() if hasattr(tun, "status") else {}
    body = "<h2>Hub status</h2>"
    body += "<div class='row'>"
    body += "<div class='card'><h3 style='margin-top:0;color:#8b949e'>Hub identity</h3><dl class='kv'>"
    for k, v in s.items():
        body += f"<dt>{H(k)}</dt><dd>{H(str(v))}</dd>"
    body += "</dl></div>"
    body += "<div class='card'><h3 style='margin-top:0;color:#8b949e'>Tunnel module</h3><dl class='kv'>"
    for k, v in ts.items():
        body += f"<dt>{H(k)}</dt><dd>{H(str(v))}</dd>"
    body += "</dl></div></div>"
    return _layout("Hub", body)


def page_clone_detail(clone_id: str) -> str:
    hub, tun, cc = _lazy_imports()
    c = hub.get_clone(clone_id)
    csrf = _new_csrf()
    if not c:
        return _layout("Clone " + clone_id,
                       "<h2>Clone no encontrado</h2>"
                       "<p><a class='btn ghost' href='/'>← volver</a></p>")
    port = tun.get_tunnel_port(clone_id)
    body = f"<h2>Detail · {H(c.get('friendly_name') or clone_id)} {_status_pill(c)}</h2>"
    body += "<div class='card'><dl class='kv'>"
    for k in ("clone_id", "friendly_name", "os_info", "registered_at",
              "last_seen", "revoked"):
        v = c.get(k)
        body += f"<dt>{H(k)}</dt><dd>{H(_fmt_ts(v) if 'at' in k or 'seen' in k else str(v))}</dd>"
    body += f"<dt>tunnel_port</dt><dd>{H(str(port or '—'))}</dd>"
    body += "</dl></div>"

    # Acciones (comando firmado local — F4 no usa todavía el transporte)
    body += "<h3 style='margin-top:24px;color:#8b949e;font-size:13px'>Run signed command (local exec)</h3>"
    body += "<p style='color:#6e7681;font-size:12px'>F4 ejecuta los comandos firmados LOCALMENTE (en este PC) "
    body += "porque el transporte vivo (autossh) será F5. Sirve para validar el flujo cripto end-to-end. "
    body += "Si este PC NO es el clon de identidad cargada, la respuesta dirá 'clon no registrado'.</p>"

    body += f"""
<form method="post" action="/clone/{clone_id}/cmd">
  <input type="hidden" name="csrf" value="{csrf}">
  <label>kind:
    <select name="kind">
      <option value="ping">ping</option>
      <option value="system_info">system_info</option>
      <option value="list_dir">list_dir</option>
      <option value="read_file">read_file</option>
    </select>
  </label>
  <label>params (JSON):
    <input type="text" name="args" value='{{}}'
           placeholder='{{"path":"/tmp"}}' style="width:280px"></label>
  <button type="submit">Run</button>
</form>
"""

    if not c.get("revoked"):
        body += f"""
<form method="post" action="/clone/{clone_id}/revoke" style="margin-top:24px">
  <input type="hidden" name="csrf" value="{csrf}">
  <button class="" style="background:#da3633"
          onclick="return confirm('Revocar clon {H(clone_id[:12])}?')">
    Revocar clon
  </button>
</form>
"""
    body += "<p style='margin-top:24px'><a class='btn ghost' href='/'>← volver</a></p>"
    return _layout("Clone " + clone_id, body)


# ── HTTP handler ───────────────────────────────────────────────────────────

class FleetHandler(http.server.BaseHTTPRequestHandler):
    server_version = "EidosFleet/0.4"

    # Sólo escuchamos localhost en bind; aun así verificamos remote_addr.
    def _check_local(self) -> bool:
        host = self.client_address[0]
        return host in ("127.0.0.1", "::1", "localhost")

    def log_message(self, format, *args):  # silencio por defecto
        if os.environ.get("EIDOS_FLEET_VERBOSE"):
            super().log_message(format, *args)

    def _respond(self, code: int, body: str,
                 content_type: str = "text/html; charset=utf-8") -> None:
        data = body.encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(data)))
        # security headers (paranoia razonable para localhost)
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("X-Frame-Options", "DENY")
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(data)

    def _respond_json(self, code: int, obj) -> None:
        self._respond(code, json.dumps(obj, indent=2, default=str),
                      "application/json; charset=utf-8")

    def _redirect(self, location: str) -> None:
        self.send_response(303)
        self.send_header("Location", location)
        self.end_headers()

    def do_GET(self):
        if not self._check_local():
            return self._respond(403, "forbidden (localhost-only)")
        url = urllib.parse.urlparse(self.path)
        p = url.path

        try:
            if p == "/" or p == "/clones":
                return self._respond(200, page_clones())
            if p == "/tokens":
                return self._respond(200, page_tokens())
            if p == "/audit":
                return self._respond(200, page_audit())
            if p == "/hub":
                return self._respond(200, page_hub_status())
            if p.startswith("/clone/") and "/" not in p[7:]:
                cid = p[7:]
                return self._respond(200, page_clone_detail(cid))
            if p == "/api/clones":
                hub, _, _ = _lazy_imports()
                return self._respond_json(200,
                    hub.list_clones(include_revoked=True))
            if p == "/api/hub":
                hub, tun, _ = _lazy_imports()
                return self._respond_json(200,
                    {"hub": hub.status(),
                     "tunnel": tun.status() if hasattr(tun,'status') else {}})
            return self._respond(404, _layout("404",
                f"<h2>404</h2><p>no existe: {H(p)}</p>"))
        except Exception as e:  # noqa: BLE001
            return self._respond(500,
                _layout("Error",
                    f"<h2>500</h2><pre>{H(str(e))}</pre>"))

    def do_POST(self):
        if not self._check_local():
            return self._respond(403, "forbidden (localhost-only)")
        url = urllib.parse.urlparse(self.path)
        p = url.path
        length = int(self.headers.get("Content-Length", "0"))
        raw = self.rfile.read(length) if length else b""
        params = urllib.parse.parse_qs(raw.decode("utf-8")) if raw else {}
        csrf = (params.get("csrf") or [""])[0]
        if not _consume_csrf(csrf):
            return self._respond(403, _layout("CSRF",
                "<h2>CSRF inválido o expirado</h2>"))

        try:
            hub, tun, cc = _lazy_imports()
            if p == "/tokens/issue":
                label = (params.get("label") or [""])[0][:80]
                r = hub.issue_token(label)
                body = ("<h2>Token emitido</h2>"
                        f"<p>label: <b>{H(label or '(sin label)')}</b><br>"
                        f"TTL: {r.get('expires_in_sec')}s · single-use</p>"
                        f"<pre style='user-select:all;font-size:16px'>"
                        f"{H(r['token'])}</pre>"
                        "<p style='color:#e3b341'>⚠️ Copia este token AHORA. "
                        "No se vuelve a mostrar.</p>"
                        "<p><a class='btn ghost' href='/tokens'>← tokens</a>"
                        " <a class='btn ghost' href='/'>clones</a></p>")
                return self._respond(200, _layout("Token", body))

            if p.startswith("/clone/") and p.endswith("/revoke"):
                cid = p[len("/clone/"):-len("/revoke")]
                ok = hub.revoke_clone(cid)
                return self._redirect("/" if ok else f"/clone/{cid}")

            if p.startswith("/clone/") and p.endswith("/cmd"):
                cid = p[len("/clone/"):-len("/cmd")]
                kind = (params.get("kind") or ["ping"])[0]
                args_raw = (params.get("args") or ["{}"])[0]
                try:
                    args = json.loads(args_raw) if args_raw.strip() else {}
                except Exception as e:
                    return self._respond(400, _layout("Bad JSON",
                        f"<h2>JSON inválido en args</h2><pre>{H(str(e))}</pre>"))
                req, sig = cc.build_request(cid, kind, args)
                resp, rsig = cc.handle_request_at_clone(req, sig)
                ver = cc.verify_response(cid, resp, rsig)
                body = (f"<h2>Resultado · {H(kind)} → {H(cid[:16])}</h2>"
                        f"<div class='card'><dl class='kv'>"
                        f"<dt>ok</dt><dd>{H(str(resp.get('ok')))}</dd>"
                        f"<dt>error</dt><dd>{H(str(resp.get('error')))}</dd>"
                        f"<dt>hub→verify</dt><dd>{H(str(ver))}</dd>"
                        f"</dl></div>"
                        f"<h3 style='color:#8b949e;font-size:13px'>data</h3>"
                        f"<pre>{H(json.dumps(resp.get('data'), indent=2, default=str)[:8000])}</pre>"
                        f"<p><a class='btn ghost' href='/clone/{cid}'>← volver</a></p>")
                return self._respond(200, _layout("Cmd result", body))

            return self._respond(404, _layout("404",
                f"<h2>404 POST</h2><p>{H(p)}</p>"))
        except Exception as e:  # noqa: BLE001
            return self._respond(500, _layout("Error",
                f"<h2>500 POST</h2><pre>{H(str(e))}</pre>"))


# ── Servidor ───────────────────────────────────────────────────────────────

class _ThreadedServer(socketserver.ThreadingMixIn, http.server.HTTPServer):
    daemon_threads = True
    allow_reuse_address = True


def serve(host: str = "127.0.0.1", port: int = 8765) -> None:
    if host not in ("127.0.0.1", "::1", "localhost"):
        print(f"⚠️  bind {host} NO es localhost. "
              "EIDOS Fleet sólo debe escuchar localhost (auth ausente).")
    srv = _ThreadedServer((host, port), FleetHandler)
    print(f"🧠 EIDOS Fleet Dashboard → http://{host}:{port}")
    print("   (Ctrl+C para parar)")
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        print("\n(parando)")
    finally:
        srv.server_close()


# ── Self-test ──────────────────────────────────────────────────────────────

def _self_test() -> int:
    """Smoke test del dashboard sin abrir socket externo:
    arranca el servidor en localhost en un puerto libre, hace GETs/POST,
    verifica que devuelve HTML coherente y JSON válido."""
    import urllib.request

    # Encuentra un puerto libre
    import socket as _sk
    s = _sk.socket(); s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]; s.close()

    srv = _ThreadedServer(("127.0.0.1", port), FleetHandler)
    t = threading.Thread(target=srv.serve_forever, daemon=True)
    t.start()
    time.sleep(0.4)

    failures: list[str] = []

    def chk(name: str, cond: bool, detail: str = ""):
        mark = "✅" if cond else "❌"
        print(f"{mark} {name}" + (f" — {detail[:120]}" if detail else ""))
        if not cond:
            failures.append(name)

    base = f"http://127.0.0.1:{port}"

    try:
        # GET /
        with urllib.request.urlopen(f"{base}/") as r:
            html = r.read().decode()
            chk("/ devuelve 200 con header EIDOS",
                r.status == 200 and "EIDOS · Fleet Dashboard" in html)
            chk("/ contiene tabla Clones",
                ("Clones" in html or "ninguno registrado" in html))

        with urllib.request.urlopen(f"{base}/tokens") as r:
            html = r.read().decode()
            chk("/tokens contiene form issue",
                'action="/tokens/issue"' in html)
            # Extraer un csrf para POST
            import re
            m = re.search(r'name="csrf" value="([^"]+)"', html)
            chk("csrf encontrado en /tokens", bool(m))
            csrf = m.group(1) if m else ""

        with urllib.request.urlopen(f"{base}/audit") as r:
            html = r.read().decode()
            chk("/audit devuelve 200", r.status == 200 and "Audit" in html)

        with urllib.request.urlopen(f"{base}/hub") as r:
            html = r.read().decode()
            chk("/hub devuelve 200 con Hub identity",
                r.status == 200 and "Hub identity" in html)

        # API JSON
        with urllib.request.urlopen(f"{base}/api/clones") as r:
            data = json.loads(r.read().decode())
            chk("/api/clones devuelve lista JSON", isinstance(data, list))

        with urllib.request.urlopen(f"{base}/api/hub") as r:
            data = json.loads(r.read().decode())
            chk("/api/hub devuelve dict con hub+tunnel",
                "hub" in data and "tunnel" in data)

        # POST sin csrf → 403
        try:
            req = urllib.request.Request(f"{base}/tokens/issue",
                data=urllib.parse.urlencode({"label":"x"}).encode(),
                method="POST")
            r = urllib.request.urlopen(req)
            chk("POST sin csrf rechazado", False, f"status={r.status}")
        except urllib.error.HTTPError as e:
            chk("POST sin csrf rechazado (403)", e.code == 403,
                f"got {e.code}")

        # POST con csrf válido (emite token; verificamos contenido)
        if csrf:
            req = urllib.request.Request(f"{base}/tokens/issue",
                data=urllib.parse.urlencode(
                    {"csrf": csrf, "label": "fleet-self-test"}).encode(),
                method="POST")
            with urllib.request.urlopen(req) as r:
                html = r.read().decode()
                chk("POST issue con csrf devuelve token",
                    r.status == 200 and "Token emitido" in html
                    and "TTL" in html)

        # 404 sanity
        try:
            urllib.request.urlopen(f"{base}/no-existe-esta-ruta")
            chk("404 raro", False)
        except urllib.error.HTTPError as e:
            chk("404 en path inexistente", e.code == 404)

        # GET sólo desde localhost (ya garantizado por bind, pero el handler
        # tiene check de remote_addr). Confirmamos respondiendo a la propia
        # request (mismo client_address 127.0.0.1).

    finally:
        srv.shutdown()
        srv.server_close()

    print("-" * 60)
    if failures:
        print(f"❌ FAIL ({len(failures)}): {failures}")
        return 1
    print("✅ SELF-TEST PASS")
    return 0


# ── CLI ────────────────────────────────────────────────────────────────────

def _cli() -> int:
    ap = argparse.ArgumentParser(prog="eidos_fleet_dashboard",
        description="Fleet Dashboard local del hub EIDOS (F4)")
    sub = ap.add_subparsers(dest="cmd")
    sp = sub.add_parser("serve")
    sp.add_argument("--host", default="127.0.0.1")
    sp.add_argument("--port", type=int, default=8770)
    sub.add_parser("self-test")
    # Atajos sin subcmd: --host/--port → serve
    ap.add_argument("--host", default=None, help="(alias de serve --host)")
    ap.add_argument("--port", type=int, default=None,
                    help="(alias de serve --port)")
    args = ap.parse_args()

    if args.cmd == "self-test":
        return _self_test()
    host = (args.host if isinstance(args.host, str) else None) or "127.0.0.1"
    port = args.port if args.port else 8770
    serve(host, port)
    return 0


if __name__ == "__main__":
    raise SystemExit(_cli())
