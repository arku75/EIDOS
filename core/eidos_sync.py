"""
core/eidos_sync.py — Sync archivos/cookies asimétrico clon→hub (F6)
====================================================================
Extiende el canal F3 con dos kinds nuevos (get_file, get_cookies) SIN
modificar eidos_command_channel.py. El clon verifica owner_policy en
su propio PC antes de ejecutar — consentimiento asimétrico.

Bloqueos:
  • Hard (siempre): privadas Ed25519, ~/.ssh/id_*, /etc/shadow/sudoers,
    identity.json del clon
  • Soft (owner_policy toggleable):
      - get_file: si never_exfiltrate_data=true, sólo SAFE_FILE_PATHS
      - get_cookies: si never_read_browser_data=true, bloqueado
  • Privacy-first: get_cookies por defecto NO devuelve values
    (sólo host+name+expiry). include_values=true exige owner_policy
    relajada.

Hub-side:
  build_extended_request, save_file_response, smoke_get_file_local

Clone-side:
  handle_extended_at_clone (delega a F3 si kind no es extendido)

CLI: python3 -m core.eidos_sync self-test | owner-status
"""
from __future__ import annotations

import argparse
import base64
import hashlib
import json
import os
import platform
import shutil
import sqlite3
import sys
import tempfile
import time
from pathlib import Path
from typing import Optional
from core.db import get_conn

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


def _imports():
    from core import eidos_command_channel as cc
    from core import eidos_clone_agent as cla
    from core import eidos_hub as hub
    from core import eidos_owner_policy as op
    return cc, cla, hub, op


MAX_GET_FILE_BYTES = 4 * 1024 * 1024   # 4 MB
MAX_COOKIES        = 5000
EXTENDED_KINDS = ("get_file", "get_cookies")

SAFE_FILE_PATHS = (
    "/etc/hostname", "/etc/os-release", "/etc/machine-id",
    "/proc/cpuinfo", "/proc/meminfo",
)

HARD_FORBIDDEN_PREFIXES = (
    os.path.expanduser("~/.ssh/id_"),
    os.path.expanduser("~/.eidos/clone/clone_ed25519.priv"),
    os.path.expanduser("~/.eidos/hub/hub_ed25519.priv"),
    os.path.expanduser("~/.eidos/clone/identity.json"),
    "/etc/shadow", "/etc/gshadow", "/etc/sudoers",
)

HUB_SYNC_DIR = Path(os.path.expanduser("~/.eidos/hub/sync"))


# ── Audit ──────────────────────────────────────────────────────────────────

def _audit(side: str, entry: dict) -> None:
    p = Path(os.path.expanduser(f"~/.eidos/{side}/sync_audit.log"))
    p.parent.mkdir(parents=True, exist_ok=True)
    e = {"ts": time.time(),
         "ts_iso": time.strftime("%Y-%m-%d %H:%M:%S"), **entry}
    with open(p, "a", encoding="utf-8") as f:
        f.write(json.dumps(e, ensure_ascii=False) + "\n")
    try:
        os.chmod(p, 0o600)
    except Exception:
        pass


# ── Helpers ────────────────────────────────────────────────────────────────

def _check_hard_forbidden(path: str) -> None:
    rp = os.path.realpath(path)
    for forb in HARD_FORBIDDEN_PREFIXES:
        if rp == forb or rp.startswith(forb):
            raise PermissionError(f"PATH_FORBIDDEN_HARD: {rp}")


def _browser_cookie_paths(browser: str) -> list[str]:
    home = os.path.expanduser("~")
    sysn = platform.system()
    b = browser.lower()
    out: list[str] = []

    if sysn == "Linux":
        if b in ("firefox", "firefox-esr"):
            base = Path(home) / ".mozilla" / "firefox"
            if base.is_dir():
                for prof in base.iterdir():
                    cand = prof / "cookies.sqlite"
                    if prof.is_dir() and cand.exists():
                        out.append(str(cand))
        elif b in ("chrome", "google-chrome", "chromium"):
            for sub in ("google-chrome", "chromium"):
                p = Path(home) / ".config" / sub / "Default" / "Cookies"
                if p.exists():
                    out.append(str(p))
        elif b == "brave":
            p = Path(home) / ".config" / "BraveSoftware" / \
                "Brave-Browser" / "Default" / "Cookies"
            if p.exists():
                out.append(str(p))
    elif sysn == "Darwin":
        if b in ("firefox", "firefox-esr"):
            base = Path(home) / "Library" / "Application Support" / \
                "Firefox" / "Profiles"
            if base.is_dir():
                for prof in base.iterdir():
                    cand = prof / "cookies.sqlite"
                    if cand.exists():
                        out.append(str(cand))
        elif b in ("chrome", "google-chrome"):
            p = Path(home) / "Library" / "Application Support" / \
                "Google" / "Chrome" / "Default" / "Cookies"
            if p.exists():
                out.append(str(p))
        elif b == "brave":
            p = Path(home) / "Library" / "Application Support" / \
                "BraveSoftware" / "Brave-Browser" / "Default" / "Cookies"
            if p.exists():
                out.append(str(p))
    elif sysn == "Windows":
        local = os.environ.get("LOCALAPPDATA", "")
        appdata = os.environ.get("APPDATA", "")
        if b in ("firefox", "firefox-esr") and appdata:
            base = Path(appdata) / "Mozilla" / "Firefox" / "Profiles"
            if base.is_dir():
                for prof in base.iterdir():
                    cand = prof / "cookies.sqlite"
                    if cand.exists():
                        out.append(str(cand))
        elif b in ("chrome", "google-chrome") and local:
            p = Path(local) / "Google" / "Chrome" / "User Data" / \
                "Default" / "Network" / "Cookies"
            if p.exists():
                out.append(str(p))
        elif b == "brave" and local:
            p = Path(local) / "BraveSoftware" / "Brave-Browser" / \
                "User Data" / "Default" / "Network" / "Cookies"
            if p.exists():
                out.append(str(p))
    return out


def _safe_copy_db(src: str) -> str:
    """Copia el DB a un tmp file para evitar locks del browser activo."""
    tmp = tempfile.NamedTemporaryFile(
        prefix="eidos_cook_", suffix=".sqlite", delete=False)
    tmp.close()
    shutil.copy(src, tmp.name)
    try:
        os.chmod(tmp.name, 0o600)
    except Exception:
        pass
    return tmp.name


def _read_firefox(db: str, hosts: Optional[list], include_values: bool) -> list[dict]:
    tmp = _safe_copy_db(db)
    try:
        con = get_conn(f"file:{tmp}?mode=ro", uri=True)
        con.row_factory = sqlite3.Row
        cur = con.execute(
            "SELECT host, name, value, expiry FROM moz_cookies "
            "ORDER BY host LIMIT ?", (MAX_COOKIES,))
        out = []
        for r in cur:
            host = r["host"] or ""
            if hosts and not any(h.strip(".") in host for h in hosts):
                continue
            entry = {"host": host, "name": r["name"], "expiry": r["expiry"]}
            if include_values:
                entry["value"] = r["value"]
            out.append(entry)
        con.close()
        return out
    finally:
        try:
            os.unlink(tmp)
        except Exception:
            pass


def _read_chromium(db: str, hosts: Optional[list],
                   include_values: bool) -> list[dict]:
    """Chrome/Chromium/Brave usan SQLite con tabla cookies. Los valores
    están cifrados con OS keyring; en F6 NO los desciframos (eso requiere
    libsecret/win32crypt y rompe la asimetría: el clon expondría los
    nombres pero no los secretos). Devolvemos solo metadata."""
    tmp = _safe_copy_db(db)
    try:
        con = get_conn(f"file:{tmp}?mode=ro", uri=True)
        con.row_factory = sqlite3.Row
        cur = con.execute(
            "SELECT host_key as host, name, expires_utc as expiry "
            "FROM cookies ORDER BY host_key LIMIT ?", (MAX_COOKIES,))
        out = []
        for r in cur:
            host = r["host"] or ""
            if hosts and not any(h.strip(".") in host for h in hosts):
                continue
            entry = {"host": host, "name": r["name"], "expiry": r["expiry"]}
            if include_values:
                entry["value"] = "«ENCRYPTED_BY_OS_KEYRING»"
                entry["note"] = ("Chromium values are OS-encrypted; "
                                 "F6 no decrypts. Use F-future for that.")
            out.append(entry)
        con.close()
        return out
    finally:
        try:
            os.unlink(tmp)
        except Exception:
            pass


# ── Ejecutores extra (clon-side) ───────────────────────────────────────────

def _execute_get_file(args: dict) -> dict:
    _, _, _, op = _imports()
    path = os.path.expanduser(str(args.get("path", "")))
    max_b = min(int(args.get("max_bytes", MAX_GET_FILE_BYTES)),
                MAX_GET_FILE_BYTES)
    if not path:
        raise ValueError("path requerido")
    if not os.path.isfile(path):
        raise FileNotFoundError(f"no es fichero: {path}")
    _check_hard_forbidden(path)

    if op.is_constraint_enforced("never_exfiltrate_data"):
        rp = os.path.realpath(path)
        allowed = any(rp == os.path.realpath(s) for s in SAFE_FILE_PATHS)
        if not allowed:
            raise PermissionError(
                "OWNER_POLICY_BLOCKED: never_exfiltrate_data enforced. "
                "Path fuera de SAFE_FILE_PATHS. "
                "Autoriza con: python3 core/eidos_owner_policy.py "
                "set never_exfiltrate_data false 'sync F6'")

    size_total = os.path.getsize(path)
    with open(path, "rb") as f:
        data = f.read(max_b)
    sha = hashlib.sha256(data).hexdigest()
    _audit("clone", {"event": "get_file", "path": path,
                     "bytes": len(data), "sha256_short": sha[:16],
                     "truncated": size_total > len(data)})
    return {
        "kind": "get_file",
        "path": path,
        "size_total": size_total,
        "bytes_sent": len(data),
        "truncated": size_total > len(data),
        "sha256": sha,
        "content_b64": base64.b64encode(data).decode("ascii"),
    }


def _execute_get_cookies(args: dict) -> dict:
    _, _, _, op = _imports()
    browser = str(args.get("browser", "firefox")).lower()
    hosts = args.get("hosts") or None
    include_values = bool(args.get("include_values", False))

    if op.is_constraint_enforced("never_read_browser_data"):
        raise PermissionError(
            "OWNER_POLICY_BLOCKED: never_read_browser_data enforced. "
            "Autoriza con: python3 core/eidos_owner_policy.py "
            "set never_read_browser_data false 'sync cookies F6'")

    if include_values and op.is_constraint_enforced("never_exfiltrate_data"):
        raise PermissionError(
            "OWNER_POLICY_BLOCKED: include_values requiere "
            "never_exfiltrate_data=false en owner_policy (privacy-first)")

    paths = _browser_cookie_paths(browser)
    if not paths:
        raise FileNotFoundError(
            f"no se encontró cookies.sqlite para browser={browser} "
            f"en {platform.system()}")

    all_cookies: list[dict] = []
    for p in paths:
        try:
            if "moz" in p or "firefox" in p.lower() or "Firefox" in p:
                all_cookies.extend(
                    _read_firefox(p, hosts, include_values))
            else:
                all_cookies.extend(
                    _read_chromium(p, hosts, include_values))
        except Exception as e:
            _audit("clone", {"event": "get_cookies_partial_err",
                             "path": p, "err": str(e)[:120]})

    _audit("clone", {"event": "get_cookies", "browser": browser,
                     "hosts_filter": hosts or [],
                     "include_values": include_values,
                     "count": len(all_cookies),
                     "paths_searched": len(paths)})
    return {
        "kind": "get_cookies",
        "browser": browser,
        "count": len(all_cookies),
        "paths_searched": len(paths),
        "include_values": include_values,
        "cookies": all_cookies[:MAX_COOKIES],
    }


# ── Handler extendido (delega a F3 si no es kind nuevo) ────────────────────

def handle_extended_at_clone(req_payload: dict,
                             req_sig_hex: str) -> tuple[dict, str]:
    """Mismo contrato que cc.handle_request_at_clone, pero ejecuta los
    kinds extendidos. Si kind ∉ EXTENDED_KINDS, delega al handler F3."""
    cc, cla, _, _ = _imports()
    kind = req_payload.get("kind")
    if kind not in EXTENDED_KINDS:
        return cc.handle_request_at_clone(req_payload, req_sig_hex)

    # Verificación de firma + clone_id + ts: replicar lo que hace F3
    ident = cla.load_identity()
    if not ident:
        return cc._err_response("clon no registrado", req_payload), cc._sign_err()
    from cryptography.hazmat.primitives import serialization
    from cryptography.exceptions import InvalidSignature
    try:
        hub_pub = serialization.load_pem_public_key(
            ident["hub_pub_key_pem"].encode())
        canon = json.dumps(req_payload, sort_keys=True,
                           separators=(",", ":")).encode()
        hub_pub.verify(bytes.fromhex(req_sig_hex), canon)  # type: ignore[attr-defined]
    except (InvalidSignature, Exception) as e:
        _audit("clone", {"event": "reject_sig_ext", "reason": str(e)[:80]})
        return cc._err_response(f"firma hub inválida: {e}",
                                 req_payload), cc._sign_err()
    if req_payload.get("clone_id") != ident["clone_id"]:
        return cc._err_response("clone_id no coincide",
                                 req_payload), cc._sign_err()
    if abs(time.time() - req_payload.get("ts", 0)) > 60:
        return cc._err_response("ts demasiado viejo o futuro",
                                 req_payload), cc._sign_err()

    args = req_payload.get("args", {}) or {}
    try:
        if kind == "get_file":
            data = _execute_get_file(args)
        else:  # get_cookies
            data = _execute_get_cookies(args)
        ok = True; err = None
    except Exception as e:
        data = None; ok = False; err = str(e)
        _audit("clone", {"event": "exec_error_ext", "kind": kind,
                         "error": str(e)[:200]})

    resp = {"ok": ok, "data": data, "error": err,
            "ts": time.time(), "nonce_echo": req_payload.get("nonce")}
    canon_resp = json.dumps(resp, sort_keys=True,
                            separators=(",", ":")).encode()
    sig = cla.load_priv().sign(canon_resp).hex()
    return resp, sig


# ── Hub-side: guardar respuestas get_file ──────────────────────────────────

def save_file_response(clone_id: str, resp: dict) -> dict:
    """Guarda el bytes recibido en ~/.eidos/hub/sync/<clone_id>/<file>.
    Verifica sha256. Devuelve {ok, path, bytes, sha256_ok}."""
    if not resp.get("ok"):
        return {"ok": False, "error": resp.get("error")}
    data = resp.get("data") or {}
    if data.get("kind") != "get_file":
        return {"ok": False, "error": "no es get_file"}
    blob = base64.b64decode(data["content_b64"])
    computed = hashlib.sha256(blob).hexdigest()
    sha_ok = (computed == data["sha256"])
    HUB_SYNC_DIR.mkdir(parents=True, exist_ok=True)
    try:
        os.chmod(HUB_SYNC_DIR, 0o700)
    except Exception:
        pass
    target_dir = HUB_SYNC_DIR / clone_id
    target_dir.mkdir(parents=True, exist_ok=True)
    try:
        os.chmod(target_dir, 0o700)
    except Exception:
        pass
    name = os.path.basename(data["path"]) or "anon"
    final = target_dir / f"{int(time.time())}_{name}"
    final.write_bytes(blob)
    try:
        os.chmod(final, 0o600)
    except Exception:
        pass
    _audit("hub", {"event": "recv_file", "clone_id": clone_id,
                   "path_src": data["path"], "saved": str(final),
                   "bytes": len(blob), "sha256_ok": sha_ok})
    return {"ok": True, "saved_path": str(final),
            "bytes": len(blob), "sha256_ok": sha_ok}


# ── Smoke helpers (mismo PC, para tests) ───────────────────────────────────

def smoke_get_file_local(clone_id: str, path: str,
                          max_bytes: int = MAX_GET_FILE_BYTES) -> dict:
    cc, _, _, _ = _imports()
    req, sig = cc.build_request(clone_id, "get_file",
                                {"path": path, "max_bytes": max_bytes})
    resp, _ = handle_extended_at_clone(req, sig)
    if resp.get("ok"):
        save = save_file_response(clone_id, resp)
        return {"resp_ok": resp.get("ok"), "save": save}
    return {"resp_ok": False, "error": resp.get("error")}


def smoke_get_cookies_local(clone_id: str, browser: str = "firefox",
                             include_values: bool = False,
                             hosts: Optional[list] = None) -> dict:
    cc, _, _, _ = _imports()
    req, sig = cc.build_request(clone_id, "get_cookies",
                                {"browser": browser,
                                 "include_values": include_values,
                                 "hosts": hosts or []})
    resp, _ = handle_extended_at_clone(req, sig)
    if resp.get("ok"):
        d = resp.get("data") or {}
        return {"ok": True, "count": d.get("count"),
                "browser": d.get("browser"),
                "include_values": d.get("include_values")}
    return {"ok": False, "error": resp.get("error")}


# ── Self-test ──────────────────────────────────────────────────────────────

def _self_test() -> int:
    """Sandbox self-test: registra un clon temporal, prueba get_file y
    get_cookies bajo distintos estados de owner_policy."""
    import core.eidos_clone_agent as cla
    import core.eidos_command_channel as cc
    from core import eidos_hub, eidos_owner_policy as op

    sandbox = Path(tempfile.mkdtemp(prefix="eidos_sync_test_"))
    print(f"sandbox: {sandbox}")

    # Backup paths originales del clon (no romper el clon real)
    orig = (cla.CLONE_DIR, cla.PRIV_PATH, cla.PUB_PATH, cla.IDENT_PATH)
    cla.CLONE_DIR  = sandbox
    cla.PRIV_PATH  = sandbox / "clone_ed25519.priv"
    cla.PUB_PATH   = sandbox / "clone_ed25519.pub"
    cla.IDENT_PATH = sandbox / "identity.json"

    failures: list[str] = []
    def chk(name: str, cond: bool, detail: str = ""):
        mark = "✅" if cond else "❌"
        print(f"{mark} {name}" + (f" — {detail[:120]}" if detail else ""))
        if not cond:
            failures.append(name)

    clone_id = None
    try:
        # Init clon sandbox + token + registro
        cla.init_clone()
        tok = eidos_hub.issue_token("sync-self-test")["token"]
        reg = cla.register_with_hub_local(tok, "sync-test")
        chk("registro clon sandbox", reg.get("ok"), str(reg)[:120])
        clone_id = reg["clone_id"]

        # Estado inicial owner_policy
        # Backup política actual antes de tocar
        backup_state = {
            "ne": op.get_flag("never_exfiltrate_data"),
            "nb": op.get_flag("never_read_browser_data"),
        }

        # CASO 1: never_exfiltrate_data=true → get_file SAFE path = OK
        op.set_flag("never_exfiltrate_data", True, "self-test F6")
        req, sig = cc.build_request(clone_id, "get_file",
                                    {"path": "/etc/hostname"})
        resp, _ = handle_extended_at_clone(req, sig)
        chk("get_file /etc/hostname (SAFE, enforced)", resp.get("ok"),
            str(resp.get("error") or ""))
        if resp.get("ok"):
            saved = save_file_response(clone_id, resp)
            chk("save_file_response sha256 OK", saved.get("sha256_ok"))
            chk("archivo guardado existe",
                Path(saved.get("saved_path", "")).exists())

        # CASO 2: get_file de path NO seguro CON enforced=true → bloqueado
        req, sig = cc.build_request(clone_id, "get_file",
                                    {"path": "/etc/passwd"})
        resp, _ = handle_extended_at_clone(req, sig)
        chk("get_file /etc/passwd bloqueado por owner_policy",
            not resp.get("ok") and "OWNER_POLICY_BLOCKED" in str(resp.get("error")),
            str(resp.get("error") or ""))

        # CASO 3: relajar política → get_file no-SAFE OK
        op.set_flag("never_exfiltrate_data", False, "self-test relax")
        req, sig = cc.build_request(clone_id, "get_file",
                                    {"path": "/etc/passwd"})
        resp, _ = handle_extended_at_clone(req, sig)
        chk("get_file /etc/passwd ahora permitido", resp.get("ok"),
            str(resp.get("error") or ""))

        # CASO 4: hard-forbidden: /etc/shadow SIEMPRE bloqueado
        req, sig = cc.build_request(clone_id, "get_file",
                                    {"path": "/etc/shadow"})
        resp, _ = handle_extended_at_clone(req, sig)
        chk("get_file /etc/shadow hard-forbidden",
            not resp.get("ok") and "FORBIDDEN_HARD" in str(resp.get("error")))

        # CASO 5: hard-forbidden: priv del clon SIEMPRE bloqueado
        priv = str(cla.PRIV_PATH)
        # Asegurar que está dentro de HARD_FORBIDDEN_PREFIXES (clon_ed25519.priv)
        # El path real del clon real es ~/.eidos/clone/clone_ed25519.priv;
        # el sandbox usa otro path → no entra en hard-forbidden. Probamos
        # con la priv canonical real, que NO existe en sandbox pero el
        # check ataja por prefix:
        real_clone_priv = os.path.expanduser("~/.eidos/clone/clone_ed25519.priv")
        if os.path.isfile(real_clone_priv):
            req, sig = cc.build_request(clone_id, "get_file",
                                        {"path": real_clone_priv})
            resp, _ = handle_extended_at_clone(req, sig)
            chk("get_file priv del clon real hard-forbidden",
                not resp.get("ok") and "FORBIDDEN_HARD" in str(resp.get("error")))
        else:
            chk("priv clon real ausente (skip)", True, "skip")

        # CASO 6: get_cookies con never_read_browser_data=true → bloqueado
        op.set_flag("never_read_browser_data", True, "self-test cookies")
        req, sig = cc.build_request(clone_id, "get_cookies",
                                    {"browser": "firefox-esr"})
        resp, _ = handle_extended_at_clone(req, sig)
        chk("get_cookies bloqueado por owner_policy",
            not resp.get("ok") and "OWNER_POLICY_BLOCKED" in str(resp.get("error")))

        # CASO 7: relajar → si hay cookies firefox → OK, si no → FileNotFound
        op.set_flag("never_read_browser_data", False, "self-test cookies")
        req, sig = cc.build_request(clone_id, "get_cookies",
                                    {"browser": "firefox-esr"})
        resp, _ = handle_extended_at_clone(req, sig)
        if resp.get("ok"):
            d = resp.get("data") or {}
            chk("get_cookies devolvió count (sin values)",
                "count" in d and d.get("include_values") is False,
                f"count={d.get('count')}")
        else:
            err = str(resp.get("error") or "")
            chk("get_cookies sin firefox-esr local → FileNotFound aceptable",
                "no se encontró" in err.lower() or "no such file" in err.lower()
                or "not found" in err.lower())

        # CASO 8: include_values=true cuando never_exfiltrate_data=true → bloqueado
        op.set_flag("never_exfiltrate_data", True, "test guard values")
        op.set_flag("never_read_browser_data", False, "test guard values")
        req, sig = cc.build_request(clone_id, "get_cookies",
                                    {"browser": "firefox-esr",
                                     "include_values": True})
        resp, _ = handle_extended_at_clone(req, sig)
        chk("include_values bloqueado por never_exfiltrate_data",
            not resp.get("ok") and "OWNER_POLICY_BLOCKED" in str(resp.get("error")),
            str(resp.get("error") or ""))

        # CASO 9: handler extendido delega a F3 cuando kind no es extendido
        req, sig = cc.build_request(clone_id, "ping", {})
        resp, _ = handle_extended_at_clone(req, sig)
        chk("delega a F3 con kind=ping", resp.get("ok"))

        # CASO 10: firma falsificada → rechazado
        req, sig = cc.build_request(clone_id, "get_file",
                                    {"path": "/etc/hostname"})
        fake = "00" * (len(sig)//2)
        resp, _ = handle_extended_at_clone(req, fake)
        chk("firma hub falsa rechazada en kind extendido",
            not resp.get("ok") and "firma" in str(resp.get("error")).lower())

        # Restaurar política
        op.set_flag("never_exfiltrate_data", backup_state["ne"],
                    "restore self-test")
        op.set_flag("never_read_browser_data", backup_state["nb"],
                    "restore self-test")

    finally:
        # cleanup
        cla.CLONE_DIR, cla.PRIV_PATH, cla.PUB_PATH, cla.IDENT_PATH = orig
        if clone_id:
            try:
                eidos_hub.revoke_clone(clone_id)
            except Exception:
                pass
        try:
            shutil.rmtree(sandbox, ignore_errors=True)
        except Exception:
            pass

    print("-" * 60)
    if failures:
        print(f"❌ FAIL ({len(failures)}): {failures}")
        return 1
    print("✅ SELF-TEST PASS")
    return 0


# ── CLI ────────────────────────────────────────────────────────────────────

def _owner_status() -> int:
    _, _, _, op = _imports()
    s = {
        "never_exfiltrate_data": op.get_flag("never_exfiltrate_data"),
        "never_read_browser_data": op.get_flag("never_read_browser_data"),
    }
    print(json.dumps(s, indent=2))
    return 0


def _cli() -> int:
    ap = argparse.ArgumentParser(prog="eidos_sync",
        description="Sync archivos/cookies asimétrico clon→hub (F6)")
    sub = ap.add_subparsers(dest="cmd")
    sub.add_parser("self-test")
    sub.add_parser("owner-status")
    args = ap.parse_args()
    if args.cmd == "self-test":
        return _self_test()
    if args.cmd == "owner-status":
        return _owner_status()
    ap.print_help()
    return 1


if __name__ == "__main__":
    raise SystemExit(_cli())
