"""
core/eidos_owner_policy.py — Política owner-toggleable (capa ADITIVA)
=====================================================================
SER (el dueño) puede ENCENDER/APAGAR cada límite individualmente vía
esta CLI. EIDOS por sí mismo NO puede tocar nada de esto — solo el
owner desde su Kali, con audit log de cada cambio.

NO toca constitution.toml ni constitution.py (que siguen como floor
inmutable verificado por hash). Esta capa AÑADE una política owner-
side: ~/.eidos/owner_policy.toml. Los módulos nuevos del clon
(eidos_command_channel, eidos_tunnel...) la consultan para decidir
qué operaciones permitir según el toggle del owner.

Diseño honesto:
  • Cada flag de constitution.toml + owner_policy.toml = AND lógico:
    si la flag es `enforced=true` en cualquiera de las dos → bloqueado.
  • Owner puede `enforced=false` para experimentar/desbloquear UN
    constraint específico. EIDOS sigue sin poder tocar la política.
  • Audit log: ~/.eidos/owner_policy_audit.log cada cambio con
    timestamp + flag + valor_anterior + valor_nuevo + nota.

Uso:
  python3 core/eidos_owner_policy.py list
  python3 core/eidos_owner_policy.py set never_read_browser_data false "para fase clon cookies"
  python3 core/eidos_owner_policy.py get never_read_browser_data
  python3 core/eidos_owner_policy.py audit [N]
  python3 core/eidos_owner_policy.py reset            # vuelve TODO a enforced=true

Import desde otros módulos:
  from core.eidos_owner_policy import is_constraint_enforced
  if is_constraint_enforced("never_read_browser_data"):
      raise PermissionError("constraint activo")
"""
from __future__ import annotations

import os
import sys
import json
import time
from pathlib import Path
from typing import Optional

POLICY_PATH = Path(os.path.expanduser("~/.eidos/owner_policy.toml"))
AUDIT_PATH  = Path(os.path.expanduser("~/.eidos/owner_policy_audit.log"))

# Flags conocidas (de constitution.toml [absolute_constraints]) + descripción
# Cada una empieza enforced=true (mismo comportamiento que el floor).
KNOWN_FLAGS: dict[str, dict] = {
    "never_delete_git_directory": {
        "default": True,
        "desc": "Bloquear rm -rf de .git/ (proteger historial)"},
    "never_force_push": {
        "default": True,
        "desc": "Bloquear git push --force (proteger upstream)"},
    "never_rewrite_history": {
        "default": True,
        "desc": "Bloquear rebase/reset que reescribe commits ya públicos"},
    "never_modify_constitution": {
        "default": True,
        "desc": "EIDOS NUNCA puede tocar constitution.toml (meta-regla, NO toggleable por seguridad)",
        "locked": True},
    "never_disable_rate_limiter": {
        "default": True,
        "desc": "Bloquear deshabilitar el rate limiter de las APIs"},
    "never_escalate_freedom_level": {
        "default": True,
        "desc": "EIDOS no puede subirse su propio nivel de libertad"},
    "never_exfiltrate_data": {
        "default": True,
        "desc": "Bloquear envío de datos sensibles fuera del PC"},
    "never_open_listening_ports": {
        "default": True,
        "desc": "Bloquear abrir puertos a escuchar inbound externos (los túneles SSH no cuentan, son outbound)"},
    "never_install_remote_access": {
        "default": True,
        "desc": "Bloquear instalación de RAT/backdoor genérico. NOTA: el clon de EIDOS es remote-access AUTORIZADO por SER vía install consciente, no aplica este bloqueo"},
    "never_read_ssh_keys": {
        "default": True,
        "desc": "Bloquear lectura de ~/.ssh/id_* (claves SSH personales del usuario)"},
    "never_read_env_files": {
        "default": True,
        "desc": "Bloquear lectura masiva de .env/secrets.env del usuario"},
    "never_read_browser_data": {
        "default": True,
        "desc": "Bloquear lectura masiva de cookies/historial/pwds del browser. Toggleable cuando hagas la Fase 6 (sync cookies a tu Kali)"},
    "never_access_keyring": {
        "default": True,
        "desc": "Bloquear acceso al keyring del SO (gnome-keyring, KWallet)"},
    "never_modify_constitution_verifier": {
        "default": True,
        "desc": "EIDOS no puede tocar core/constitution.py (NO toggleable)",
        "locked": True},
    "never_delete_backups": {
        "default": True,
        "desc": "Bloquear rm de ~/.eidos/code_backups/ o snapshots"},
    "never_modify_git_hooks": {
        "default": True,
        "desc": "Bloquear modificación de .git/hooks/* (proteger pre-commit, etc.)"},
    "never_share_screen_to_hub": {
        "default": True,
        "desc": "Bloquear que el clon comparta su pantalla con el hub vía canal F3. "
                "SER puede relajar solo en clones OWN_DEVICE marcados explícitamente "
                "(ver ~/.eidos/clone/role.txt). Audit en ambos lados (S61)."},
}

# Flags LOCKED no se pueden togglear ni por SER (meta-invariantes).
LOCKED = {k for k, v in KNOWN_FLAGS.items() if v.get("locked")}


# ── Persistencia ───────────────────────────────────────────────────────────

def _load() -> dict:
    if not POLICY_PATH.exists():
        return {k: v["default"] for k, v in KNOWN_FLAGS.items()}
    out: dict[str, bool] = {}
    for line in POLICY_PATH.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        if "=" in line:
            k, _, v = line.partition("=")
            # Stripear comentario inline `# LOCKED...` antes de parsear
            v = v.split("#")[0].strip().lower()
            out[k.strip()] = v in ("true", "1", "yes")
    # Completar con defaults para flags nuevas
    for k, v in KNOWN_FLAGS.items():
        out.setdefault(k, v["default"])
    return out


def _save(state: dict[str, bool]) -> None:
    POLICY_PATH.parent.mkdir(parents=True, exist_ok=True)
    lines = [
        "# EIDOS Owner Policy — toggles per-constraint, SOLO SER puede editar",
        "# (capa aditiva sobre constitution.toml. EIDOS no puede tocar esto.)",
        f"# regenerado: {time.strftime('%Y-%m-%d %H:%M:%S')}",
        "",
    ]
    for k in KNOWN_FLAGS:
        v = state.get(k, KNOWN_FLAGS[k]["default"])
        locked_note = "  # LOCKED (no toggleable)" if k in LOCKED else ""
        lines.append(f"{k} = {str(v).lower()}{locked_note}")
    POLICY_PATH.write_text("\n".join(lines) + "\n", encoding="utf-8")
    os.chmod(POLICY_PATH, 0o600)


def _audit(flag: str, old: Optional[bool], new: bool, note: str = "") -> None:
    AUDIT_PATH.parent.mkdir(parents=True, exist_ok=True)
    entry = {
        "ts": time.time(),
        "ts_iso": time.strftime("%Y-%m-%d %H:%M:%S"),
        "flag": flag,
        "old": old,
        "new": new,
        "note": note,
    }
    with open(AUDIT_PATH, "a", encoding="utf-8") as f:
        f.write(json.dumps(entry, ensure_ascii=False) + "\n")
    os.chmod(AUDIT_PATH, 0o600)


# ── API pública ────────────────────────────────────────────────────────────

def get_flag(name: str) -> bool:
    return _load().get(name, KNOWN_FLAGS.get(name, {}).get("default", True))


def is_constraint_enforced(name: str) -> bool:
    """Devuelve True si el constraint está ACTIVO (bloquea), False si el
    owner lo ha relajado conscientemente. Usar desde otros módulos del
    clon antes de hacer operaciones potencialmente sensibles."""
    return bool(get_flag(name))


def set_flag(name: str, value: bool, note: str = "") -> dict:
    if name not in KNOWN_FLAGS:
        return {"ok": False, "error": f"flag desconocido: {name}"}
    if name in LOCKED:
        return {"ok": False, "error": f"flag '{name}' es LOCKED (meta-invariante, no toggleable)"}
    state = _load()
    old = state.get(name)
    state[name] = bool(value)
    _save(state)
    _audit(name, old, bool(value), note)
    return {"ok": True, "flag": name, "old": old, "new": bool(value),
            "note": note}


def list_all() -> list[dict]:
    state = _load()
    return [{
        "flag": k,
        "value": state.get(k, v["default"]),
        "default": v["default"],
        "locked": k in LOCKED,
        "description": v["desc"],
    } for k, v in KNOWN_FLAGS.items()]


def reset_all() -> dict:
    state = _load()
    changed = 0
    for k, v in KNOWN_FLAGS.items():
        if state.get(k) != v["default"]:
            _audit(k, state.get(k), v["default"], "reset_all")
            changed += 1
        state[k] = v["default"]
    _save(state)
    return {"ok": True, "changed": changed}


def audit_tail(n: int = 20) -> list[dict]:
    if not AUDIT_PATH.exists():
        return []
    lines = AUDIT_PATH.read_text(encoding="utf-8").splitlines()
    out = []
    for line in lines[-n:]:
        try:
            out.append(json.loads(line))
        except Exception:  # noqa: BLE001
            continue
    return out


# ── CLI ────────────────────────────────────────────────────────────────────

def _main() -> int:
    if len(sys.argv) < 2:
        print(__doc__)
        return 0
    cmd = sys.argv[1].lower()
    if cmd == "list":
        rows = list_all()
        print(f"{'FLAG':<40} {'VALOR':<7} {'LOCKED':<8} DESC")
        print("-" * 110)
        for r in rows:
            v = "true" if r["value"] else "false"
            lk = "🔒 yes" if r["locked"] else "no"
            print(f"{r['flag']:<40} {v:<7} {lk:<8} {r['description'][:60]}")
    elif cmd == "get":
        if len(sys.argv) < 3:
            print("get <flag>"); return 1
        print(json.dumps({"flag": sys.argv[2],
                          "value": get_flag(sys.argv[2])}, indent=2))
    elif cmd == "set":
        if len(sys.argv) < 4:
            print("set <flag> <true|false> [note]"); return 1
        v = sys.argv[3].lower() in ("true", "1", "yes", "on")
        note = " ".join(sys.argv[4:]) if len(sys.argv) > 4 else ""
        r = set_flag(sys.argv[2], v, note)
        print(json.dumps(r, indent=2))
        return 0 if r["ok"] else 1
    elif cmd == "reset":
        print(json.dumps(reset_all(), indent=2))
    elif cmd == "audit":
        n = int(sys.argv[2]) if len(sys.argv) > 2 else 20
        for e in audit_tail(n):
            v_old = "true" if e["old"] else "false"
            v_new = "true" if e["new"] else "false"
            print(f"[{e['ts_iso']}] {e['flag']:<35} {v_old} → {v_new}  "
                  f"{('('+e['note']+')') if e['note'] else ''}")
    else:
        print(f"comando desconocido: {cmd}")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(_main())
