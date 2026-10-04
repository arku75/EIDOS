"""
core/eidos_onboarding.py — Wizard de onboarding consciente del clon (F7)
=========================================================================
Instala el clon de EIDOS en una máquina nueva (la del SER o la de un
amigo que voluntariamente acepta hospedarlo) con consentimiento
EXPLÍCITO, VISIBLE y por escrito.

Diseño honesto (línea disciplinada v2026-05-21):
  - Visible: muestra todo lo que hace ANTES de hacerlo.
  - Opt-in: requiere escribir "ACEPTO" literal para continuar.
  - Reversible: instala script de uninstall en PATH; comando único.
  - NUNCA lee cookie stores / keyring / ssh keys del huésped.
  - NUNCA exfiltra archivos del huésped.
  - El clon abre SOLO conexión SALIENTE al hub. No escucha inbound.

Uso interactivo:
  python3 -m core.eidos_onboarding install

Self-test (stdin scripted, no toca el sistema real):
  python3 -m core.eidos_onboarding self-test
"""
from __future__ import annotations

import argparse
import hashlib
import io
import json
import os
import platform
import shutil
import stat
import sys
import textwrap
import time
from pathlib import Path
from typing import Optional, TextIO

from core.paths import EIDOS_HOME, REPO_ROOT, USER_HOME

# ── Paths y constantes ─────────────────────────────────────────────────────

EIDOS_REPO = REPO_ROOT
CLONE_DIR  = EIDOS_HOME / "clone"

# Subdirectorios visibles al huésped (estructuras vacías iniciales)
VISIBLE_SUBDIRS = ("books", "conversations", "library", "screenshots")

UNINSTALL_SCRIPT_NAME = "eidos-clone-uninstall"
SYSTEMD_UNIT_NAME = "eidos-clone.service"

WIZARD_VERSION = "1.0.0"


# ── Utilidades de impresión ────────────────────────────────────────────────

def _box(title: str, lines: list[str], out: TextIO) -> None:
    width = max(len(title) + 4, max((len(l) for l in lines), default=20) + 4, 64)
    out.write("\n" + "═" * width + "\n")
    out.write("  " + title + "\n")
    out.write("─" * width + "\n")
    for l in lines:
        out.write("  " + l + "\n")
    out.write("═" * width + "\n")


def _info(msg: str, out: TextIO) -> None:
    out.write(f"  [info] {msg}\n")


def _err(msg: str, out: TextIO) -> None:
    out.write(f"  [error] {msg}\n")


def _ok(msg: str, out: TextIO) -> None:
    out.write(f"  [ok]   {msg}\n")


# ── Pantallas del wizard ───────────────────────────────────────────────────

def screen_1_consent(inp: TextIO, out: TextIO) -> bool:
    """Pantalla 1 — Consentimiento. Devuelve True si el usuario escribió
    'ACEPTO' literal."""
    _box("EIDOS · Clon · Pantalla 1/4 · CONSENTIMIENTO", [
        "Vas a instalar el CLON de EIDOS en esta máquina.",
        "",
        "Qué hace el clon:",
        "  • Crea ~/.eidos/ (carpeta propiedad solo de tu usuario)",
        "  • Genera una identidad criptográfica Ed25519 (chmod 600)",
        "  • Abre conexión SALIENTE al hub de SER (autossh / port-forward)",
        "  • NO escucha puertos INBOUND externos",
        "  • Recibe comandos FIRMADOS de SER (allowlist limitada)",
        "    kinds permitidos: ping, system_info, list_dir, read_file (max 64KB)",
        "    run_shell por defecto DESHABILITADO",
        "",
        "Qué NO hace el clon (línea disciplinada):",
        "  • NO lee cookies del navegador (cookies.sqlite, Local State, etc.)",
        "  • NO accede al keyring del sistema (gnome-keyring, KWallet, Keychain)",
        "  • NO lee tus claves SSH personales (~/.ssh/id_*)",
        "  • NO lee archivos .env / secrets del usuario huésped",
        "  • NO exfiltra archivos arbitrarios",
        "  • NO instala backdoors ni túneles RAT silenciosos",
        "  • TODA actividad firmada queda en audit log local (~/.eidos/clone/audit.log)",
        "",
        f"Cómo desinstalar (en cualquier momento):",
        f"  $ {UNINSTALL_SCRIPT_NAME}",
        f"  (borra ~/.eidos/, el autostart y este wizard)",
        "",
        "Si entiendes y aceptas, escribe ACEPTO (mayúsculas) y pulsa enter.",
        "Cualquier otra entrada → cancela la instalación sin cambios.",
    ], out)
    out.write("\n  >>> ")
    out.flush()
    ans = (inp.readline() or "").strip()
    if ans == "ACEPTO":
        _ok("consentimiento registrado", out)
        return True
    _err(f"respuesta '{ans}' ≠ 'ACEPTO' → instalación cancelada", out)
    return False


def screen_2_data(inp: TextIO, out: TextIO) -> dict:
    """Pantalla 2 — Datos básicos del huésped. NUNCA pide cookies ni
    accede al navegador; solo pregunta preferencias declarativas."""
    _box("EIDOS · Clon · Pantalla 2/4 · DATOS DEL HUÉSPED", [
        "Preguntas sencillas. No accedemos a tus archivos para responder",
        "por ti — sí o sí tú las contestas. Los datos quedan en local",
        "(~/.eidos/clone/profile.json) y se usan SOLO para el character",
        "system de EIDOS (saber qué carácter ofrecerte por defecto, etc.).",
    ], out)

    out.write("\n  friendly_name (cómo se llamará este clon en el hub) > ")
    out.flush()
    friendly = (inp.readline() or "").strip() or "clon-sin-nombre"

    out.write("  navegador preferido (firefox|chromium|brave|otro) > ")
    out.flush()
    browser = (inp.readline() or "").strip().lower() or "otro"
    if browser not in {"firefox", "chromium", "brave", "otro"}:
        browser = "otro"

    out.write("  idioma (es|en) [es] > ")
    out.flush()
    lang = (inp.readline() or "").strip().lower() or "es"
    if lang not in {"es", "en"}:
        lang = "es"

    out.write("  carácter por defecto (enter = 'neutro') > ")
    out.flush()
    character = (inp.readline() or "").strip() or "neutro"

    profile = {
        "friendly_name": friendly[:80],
        "browser": browser,
        "lang": lang,
        "default_character": character[:40],
        "wizard_version": WIZARD_VERSION,
        "completed_at": time.strftime("%Y-%m-%d %H:%M:%S"),
    }
    _ok(f"perfil: {profile['friendly_name']} ({lang}, "
        f"navegador={browser}, char={profile['default_character']})", out)
    return profile


def screen_3_token(inp: TextIO, out: TextIO) -> str:
    """Pantalla 3 — Pegar enroll_token del hub. SER lo genera con
    `python3 core/eidos_hub.py issue-token` y se lo pasa al huésped
    por un canal lateral (Signal, persona en persona, etc.)."""
    _box("EIDOS · Clon · Pantalla 3/4 · TOKEN DE ENROLLMENT", [
        "SER te tiene que enviar un token de enrollment (single-use,",
        "TTL 15 minutos). Pégalo aquí. Si no lo tienes, pide a SER:",
        "  python3 core/eidos_hub.py issue-token \"mi-friendly-name\"",
        "",
        "El token no se guarda; se usa una vez para registrarse y se",
        "descarta.",
    ], out)
    out.write("\n  enroll_token > ")
    out.flush()
    token = (inp.readline() or "").strip()
    if len(token) < 16:
        _err(f"token demasiado corto ({len(token)} chars); aborto.", out)
        return ""
    _ok(f"token recibido (longitud {len(token)})", out)
    return token


def screen_5_compute_optin(inp: TextIO, out: TextIO,
                            dry_run: bool = False) -> dict:
    """Pantalla 5 — Opt-in opcional para Fcompute (worker LLM distribuido).

    Default: enabled=false (rechazado por la quota). Si el huésped acepta,
    se preguntan max_ram_pct, daily_token_budget y allowed_models. La quota
    final se escribe en ~/.eidos/compute_quota.toml (chmod 600) — solo en
    flow real, no en dry_run."""
    _box("EIDOS · Clon · Pantalla 5/5 · FCOMPUTE (worker LLM, opcional)", [
        "¿Quieres que este clon contribuya su Ollama local para inferencia",
        "LLM cuando SER lo pida desde su Kali?",
        "",
        "Qué es Fcompute:",
        "  • Cuando SER pide `eidos-remote-llm <este-clon> --model X --prompt Y`,",
        "    el clon corre la inferencia en su Ollama local y devuelve la",
        "    respuesta firmada.",
        "  • Solo modelos que TÚ apruebes en allowed_models (lista cerrada).",
        "  • Tienes kill-switch (max_ram_pct) y budget diario de tokens.",
        "  • Puedes apagarlo en cualquier momento: `eidos-compute disable`.",
        "  • Audit log local en ~/.eidos/compute_audit.log de cada petición.",
        "",
        "Qué NO hace Fcompute:",
        "  • NO accede al filesystem ni shell ni red externa (solo Ollama localhost).",
        "  • NO ejecuta nada sin firma válida de SER.",
        "  • NO sube modelos ni prompts a la nube — todo se procesa local.",
        "",
        "Si no quieres activarlo ahora, escribe NO (default seguro).",
        "Si quieres activarlo, escribe SI y configuramos los límites.",
    ], out)
    out.write("\n  ¿activar Fcompute? (SI/no) > ")
    out.flush()
    ans = (inp.readline() or "").strip().upper()
    if ans != "SI":
        _ok("Fcompute permanece DESACTIVADO (default)", out)
        return {"enabled": False, "configured": False}

    out.write("\n  max_ram_pct (0-95) [60] > "); out.flush()
    try:
        max_ram = int((inp.readline() or "").strip() or "60")
    except ValueError:
        max_ram = 60
    max_ram = max(0, min(95, max_ram))

    out.write("  daily_token_budget [500000] > "); out.flush()
    try:
        budget = int((inp.readline() or "").strip() or "500000")
    except ValueError:
        budget = 500000

    out.write("  allowed_models (coma-separados) [lfm2.5-1.2b-instruct:q4_0,llama3.2:3b] > ")
    out.flush()
    models_raw = (inp.readline() or "").strip() or "lfm2.5-1.2b-instruct:q4_0,llama3.2:3b"
    allowed = [m.strip() for m in models_raw.split(",") if m.strip()]

    quota = {
        "enabled": True,
        "max_ram_pct": max_ram,
        "max_concurrent": 1,
        "allowed_models": allowed,
        "daily_token_budget": budget,
        "max_prompt_bytes": 16384,
        "max_max_tokens": 2048,
    }

    if dry_run:
        _info("dry-run: no se escribe compute_quota.toml", out)
        return {"enabled": True, "configured": True, "dry_run": True,
                "quota": quota}

    try:
        sys.path.insert(0, str(EIDOS_REPO))
        from core.eidos_compute_worker import save_quota  # type: ignore
        save_quota(quota)
        _ok("Fcompute activado y configurado", out)
        _info(f"max_ram_pct={max_ram}%  budget={budget}  models={allowed}", out)
        _info("Apaga con: eidos-compute disable", out)
        return {"enabled": True, "configured": True, "quota": quota}
    except Exception as e:  # noqa: BLE001
        _err(f"no pude escribir compute_quota.toml: {e}", out)
        return {"enabled": False, "configured": False, "error": str(e)}


def screen_4_verify(hub_pub_pem: str, expected_fingerprint: Optional[str],
                    inp: TextIO, out: TextIO) -> bool:
    """Pantalla 4 — Verificación visual del fingerprint sha256 de la
    pub key del hub. SER comparte el fingerprint por canal lateral
    (mismo canal que el token, idealmente). Si no coincide → aborto."""
    fp_full = hashlib.sha256(hub_pub_pem.encode("utf-8")).hexdigest()
    fp_short = fp_full[:16]
    _box("EIDOS · Clon · Pantalla 4/4 · VERIFICACIÓN HUB", [
        "El hub te ha enviado su clave pública. Para evitar man-in-the-",
        "middle, verifica que el fingerprint coincide con el que SER te",
        "dijo POR UN CANAL DIFERENTE (Signal, llamada, en persona).",
        "",
        f"  fingerprint sha256[:16] = {fp_short}",
        f"  (fingerprint completo en ~/.eidos/clone/hub_fingerprint.txt)",
        "",
        "Si coincide con lo que te dijo SER, escribe SI y enter.",
        "Cualquier otra cosa cancela y limpia el estado parcial.",
    ], out)
    if expected_fingerprint:
        if fp_short != expected_fingerprint[:16]:
            _err(f"fingerprint MISMATCH (esperado {expected_fingerprint[:16]}, "
                 f"recibido {fp_short}). Aborto.", out)
            return False
        _ok("fingerprint coincide con el esperado (verificación automática)", out)
    out.write("\n  ¿coincide? (SI/no) > ")
    out.flush()
    ans = (inp.readline() or "").strip().upper()
    if ans == "SI":
        _ok("hub verificado por el usuario", out)
        return True
    _err(f"respuesta '{ans}' ≠ 'SI' → instalación cancelada", out)
    return False


# ── Estructura visible y permisos ──────────────────────────────────────────

def make_visible_dirs(out: TextIO) -> list[Path]:
    """Crea ~/.eidos/{books,conversations,library,screenshots} con .gitkeep,
    permisos owner-only. Devuelve lista de paths creados."""
    created = []
    EIDOS_HOME.mkdir(parents=True, exist_ok=True)
    os.chmod(EIDOS_HOME, 0o700)
    for sub in VISIBLE_SUBDIRS:
        p = EIDOS_HOME / sub
        p.mkdir(parents=True, exist_ok=True)
        os.chmod(p, 0o700)
        gk = p / ".gitkeep"
        if not gk.exists():
            gk.write_text("", encoding="utf-8")
        created.append(p)
        _ok(f"creado {p} (chmod 700)", out)
    return created


def harden_clone_perms(out: TextIO) -> None:
    """Refuerza chmod 600 en cualquier clave/identidad en ~/.eidos/clone/."""
    if not CLONE_DIR.exists():
        return
    candidates = ["clone_ed25519.priv", "ssh_id_ed25519",
                  "identity.json", "profile.json"]
    for name in candidates:
        p = CLONE_DIR / name
        if p.exists():
            os.chmod(p, 0o600)
            _ok(f"chmod 600 {p}", out)


# ── Uninstall script ───────────────────────────────────────────────────────

UNINSTALL_SCRIPT = textwrap.dedent("""\
    #!/usr/bin/env bash
    # eidos-clone-uninstall — borra el clon de EIDOS de esta máquina.
    # Generado por core/eidos_onboarding.py (F7). Reversible y completo.
    set -e

    if [ "$(id -u)" -eq 0 ]; then
      echo "[!] no ejecutes esto como root — usa tu usuario normal." >&2
      exit 1
    fi

    EIDOS_HOME="$HOME/.eidos"

    echo "EIDOS Clone Uninstall"
    echo "---------------------"
    echo "Esto borrará:"
    echo "  - $EIDOS_HOME/   (clon, identidad, libros, conversaciones, etc.)"
    echo "  - ~/.config/systemd/user/eidos-clone.service (autostart)"
    echo "  - $(command -v eidos-clone-uninstall 2>/dev/null || echo "este script")"
    echo ""
    read -p "Escribe BORRAR (mayúsculas) para confirmar: " CONFIRM
    if [ "$CONFIRM" != "BORRAR" ]; then
      echo "cancelado, nada se ha tocado."
      exit 0
    fi

    # 1) parar y deshabilitar autostart
    if command -v systemctl >/dev/null 2>&1; then
      systemctl --user stop  eidos-clone.service 2>/dev/null || true
      systemctl --user disable eidos-clone.service 2>/dev/null || true
    fi
    rm -f "$HOME/.config/systemd/user/eidos-clone.service"

    # 2) borrar directorio
    if [ -d "$EIDOS_HOME" ]; then
      chmod -R u+rwX "$EIDOS_HOME" 2>/dev/null || true
      rm -rf "$EIDOS_HOME"
      echo "[ok] borrado $EIDOS_HOME"
    fi

    # 3) borrar el propio script
    SCRIPT_PATH="$(readlink -f "$0" 2>/dev/null || echo "$0")"
    rm -f "$SCRIPT_PATH" || true
    echo "[ok] EIDOS Clone uninstall completado."
""")


def install_uninstall_script(dest_dir: Optional[Path], out: TextIO) -> Optional[Path]:
    """Escribe el script de uninstall en dest_dir/UNINSTALL_SCRIPT_NAME
    con chmod 700. Si dest_dir es None usa ~/.local/bin (estándar XDG)."""
    if dest_dir is None:
        dest_dir = USER_HOME / ".local" / "bin"
    dest_dir.mkdir(parents=True, exist_ok=True)
    p = dest_dir / UNINSTALL_SCRIPT_NAME
    p.write_text(UNINSTALL_SCRIPT, encoding="utf-8")
    os.chmod(p, 0o700)
    _ok(f"uninstall script en {p}", out)
    return p


# ── Systemd user unit (Linux) — autostart al login ─────────────────────────

SYSTEMD_UNIT = textwrap.dedent("""\
    [Unit]
    Description=EIDOS Clone Agent (outbound only, signed channel)
    Documentation=https://github.com/anthropics/claude-code
    After=network-online.target
    Wants=network-online.target

    [Service]
    Type=simple
    # El clon se arranca como módulo Python. Asume PYTHONPATH apunta al
    # checkout del clon. No depende del checkout privado del desarrollador.
    Environment=PYTHONPATH=%h/.eidos/clone
    # NOTA: el clon mismo aún no tiene su daemon final; este unit queda
    # como placeholder reversible. Comando real se rellena al finalizar
    # la fase de transporte vivo.
    ExecStart=/usr/bin/env python3 -m core.eidos_clone_agent register-local placeholder placeholder
    Restart=on-failure
    RestartSec=15s
    # Hardening básico
    NoNewPrivileges=yes
    PrivateTmp=yes
    ProtectSystem=strict
    ProtectHome=read-only
    ReadWritePaths=%h/.eidos
    LockPersonality=yes
    MemoryDenyWriteExecute=yes
    RestrictRealtime=yes

    [Install]
    WantedBy=default.target
""")


def install_systemd_unit(out: TextIO, dry_run: bool = False) -> Optional[Path]:
    """Instala el unit systemd-user en ~/.config/systemd/user/.
    Si dry_run=True solo lo escribe sin habilitar."""
    if platform.system() != "Linux":
        _info("systemd unit solo aplica en Linux; saltado.", out)
        return None
    target = USER_HOME / ".config" / "systemd" / "user"
    target.mkdir(parents=True, exist_ok=True)
    p = target / SYSTEMD_UNIT_NAME
    p.write_text(SYSTEMD_UNIT, encoding="utf-8")
    os.chmod(p, 0o644)
    _ok(f"systemd unit en {p}", out)
    if dry_run:
        _info("dry-run: no se ejecuta `systemctl --user enable`", out)
    else:
        _info("habilitar con: systemctl --user daemon-reload && "
              "systemctl --user enable eidos-clone.service", out)
    return p


# ── Flow principal ─────────────────────────────────────────────────────────

def run_wizard(inp: TextIO, out: TextIO,
               hub_pub_pem_override: Optional[str] = None,
               expected_fp: Optional[str] = None,
               dry_run: bool = False) -> dict:
    """Ejecuta el wizard end-to-end leyendo de `inp` y escribiendo en
    `out`. Devuelve dict con resultado. dry_run evita tocar el sistema
    cuando lo usa el self-test."""

    if not screen_1_consent(inp, out):
        return {"ok": False, "stage": "consent", "reason": "no acepto"}

    profile = screen_2_data(inp, out)
    token = screen_3_token(inp, out)
    if not token:
        return {"ok": False, "stage": "token",
                "reason": "token vacío o demasiado corto"}

    # Construir o cargar pub_pem del hub.
    if hub_pub_pem_override is not None:
        hub_pem = hub_pub_pem_override
    else:
        # En producción aquí iría la llamada HTTP al hub. Como en F7 el
        # transporte vivo es opcional, leemos el hub_pub_key local si
        # existe (ej. si esta máquina es a la vez hub+clon de SER).
        candidate = EIDOS_HOME / "hub" / "hub_ed25519.pub"
        hub_pem = candidate.read_text(encoding="utf-8") if candidate.exists() else "PEM-NO-DISPONIBLE"

    if not screen_4_verify(hub_pem, expected_fp, inp, out):
        return {"ok": False, "stage": "verify", "reason": "fingerprint mismatch"}

    # ── Aplicar al sistema ─────────────────────────────────────────────
    _box("EIDOS · Clon · Aplicando configuración", [
        "Creando estructura visible, instalando uninstall y autostart…",
    ], out)

    if dry_run:
        _info("dry-run: las operaciones que tocan el sistema se simulan.", out)
        # En dry-run igual ejecutamos screen 5 para validar el flow
        compute_result = screen_5_compute_optin(inp, out, dry_run=True)
        return {"ok": True, "stage": "complete", "dry_run": True,
                "profile": profile, "token_len": len(token),
                "hub_fp_short": hashlib.sha256(hub_pem.encode()).hexdigest()[:16],
                "compute": compute_result}

    created = make_visible_dirs(out)

    # Guardar profile
    CLONE_DIR.mkdir(parents=True, exist_ok=True)
    os.chmod(CLONE_DIR, 0o700)
    profile_path = CLONE_DIR / "profile.json"
    profile_path.write_text(json.dumps(profile, indent=2), encoding="utf-8")
    os.chmod(profile_path, 0o600)
    _ok(f"perfil guardado en {profile_path}", out)

    # Guardar fingerprint del hub
    fp_full = hashlib.sha256(hub_pem.encode()).hexdigest()
    fp_path = CLONE_DIR / "hub_fingerprint.txt"
    fp_path.write_text(fp_full + "\n", encoding="utf-8")
    os.chmod(fp_path, 0o600)
    _ok(f"fingerprint hub guardado en {fp_path}", out)

    harden_clone_perms(out)
    uninstall_path = install_uninstall_script(None, out)
    unit_path = install_systemd_unit(out, dry_run=False)

    # Pantalla 5: opt-in Fcompute (no bloquea la instalación si NO)
    compute_result = screen_5_compute_optin(inp, out, dry_run=False)

    _box("EIDOS · Clon · INSTALACIÓN COMPLETADA", [
        f"Estructura visible:       ~/.eidos/{{{','.join(VISIBLE_SUBDIRS)}}}/",
        f"Uninstall:                {uninstall_path}",
        f"Autostart (systemd):      {unit_path or '(no Linux, skipped)'}",
        f"Logs del clon:            ~/.eidos/clone/audit.log",
        f"Fcompute (worker LLM):    "
        + ("ACTIVO (ver eidos-compute status)"
           if compute_result.get("enabled") else "desactivado (default)"),
        "",
        "Próximos pasos para el huésped:",
        "  - Revisar audit log periódicamente",
        "  - Para parar el clon temporalmente: systemctl --user stop eidos-clone",
        f"  - Para desinstalar:                 {UNINSTALL_SCRIPT_NAME}",
        "  - Fcompute on/off:                  eidos-compute enable|disable",
    ], out)
    return {"ok": True, "stage": "complete", "dry_run": False,
            "profile": profile, "token_len": len(token),
            "hub_fp_short": fp_full[:16], "created": [str(p) for p in created],
            "uninstall": str(uninstall_path),
            "systemd": str(unit_path) if unit_path else None,
            "compute": compute_result}


# ── Self-test (stdin scripted, dry-run no toca el sistema) ─────────────────

def _self_test() -> int:
    """Smoke test del wizard con stdin scripted. Usa dry_run=True para
    no escribir nada en ~/.eidos/."""
    failures: list[str] = []

    def chk(name: str, cond: bool, detail: str = ""):
        mark = "✅" if cond else "❌"
        print(f"{mark} {name}" + (f" — {detail[:120]}" if detail else ""))
        if not cond:
            failures.append(name)

    fake_hub_pem = ("-----BEGIN PUBLIC KEY-----\n"
                    "MCowBQYDK2VwAyEAFakeKeyForSelfTestPurposesOnly0000000000000000=\n"
                    "-----END PUBLIC KEY-----\n")
    fp = hashlib.sha256(fake_hub_pem.encode()).hexdigest()[:16]

    # 1) flow completo OK con compute opt-out
    scripted = "\n".join([
        "ACEPTO",                    # consent
        "test-clone-self",           # friendly
        "firefox",                   # browser
        "es",                        # lang
        "neutro",                    # char
        "x" * 40,                    # token (>=16)
        "SI",                        # verify
        "no",                        # compute opt-in → NO
    ]) + "\n"
    inp = io.StringIO(scripted)
    out = io.StringIO()
    res = run_wizard(inp, out, hub_pub_pem_override=fake_hub_pem,
                     expected_fp=fp, dry_run=True)
    chk("flow OK termina en stage=complete",
        res.get("stage") == "complete" and res.get("ok") is True,
        json.dumps({k: v for k, v in res.items() if k != "profile"})[:200])
    chk("profile capturado correctamente",
        res.get("profile", {}).get("friendly_name") == "test-clone-self"
        and res.get("profile", {}).get("lang") == "es")
    chk("hub_fp_short coincide con fingerprint",
        res.get("hub_fp_short") == fp)
    chk("token_len reflejado", res.get("token_len") == 40)
    chk("pantalla 5 compute opt-out preserva enabled=false",
        res.get("compute", {}).get("enabled") is False
        and res.get("compute", {}).get("configured") is False)

    # 1b) flow completo OK con compute opt-IN configurado
    scripted = "\n".join([
        "ACEPTO", "test-clone-compute", "firefox", "es", "neutro",
        "x" * 40, "SI",
        "SI",            # compute opt-in
        "50",            # max_ram_pct
        "100000",        # daily_token_budget
        "lfm2.5-1.2b-instruct:q4_0",  # allowed_models
    ]) + "\n"
    inp = io.StringIO(scripted)
    out = io.StringIO()
    res = run_wizard(inp, out, hub_pub_pem_override=fake_hub_pem,
                     expected_fp=fp, dry_run=True)
    chk("pantalla 5 compute opt-IN produce quota",
        res.get("compute", {}).get("enabled") is True
        and res.get("compute", {}).get("configured") is True
        and res.get("compute", {}).get("quota", {}).get("max_ram_pct") == 50
        and res.get("compute", {}).get("quota", {}).get("daily_token_budget") == 100000)

    # 2) flow falla si NO se escribe ACEPTO
    inp = io.StringIO("no\n")
    out = io.StringIO()
    res = run_wizard(inp, out, hub_pub_pem_override=fake_hub_pem,
                     expected_fp=fp, dry_run=True)
    chk("rechaza sin ACEPTO", res.get("ok") is False
        and res.get("stage") == "consent")

    # 3) flow falla si fingerprint mismatch
    scripted = "\n".join([
        "ACEPTO", "x", "firefox", "es", "n",
        "x" * 40,
        "SI",
        "no",   # compute opt-in (no se llega aquí pero por si acaso)
    ]) + "\n"
    inp = io.StringIO(scripted)
    out = io.StringIO()
    res = run_wizard(inp, out, hub_pub_pem_override=fake_hub_pem,
                     expected_fp="0000000000000000", dry_run=True)
    chk("rechaza fingerprint mismatch",
        res.get("ok") is False and res.get("stage") == "verify")

    # 4) flow falla si token demasiado corto
    scripted = "\n".join([
        "ACEPTO", "x", "firefox", "es", "n",
        "abc",   # < 16
    ]) + "\n"
    inp = io.StringIO(scripted)
    out = io.StringIO()
    res = run_wizard(inp, out, hub_pub_pem_override=fake_hub_pem,
                     expected_fp=fp, dry_run=True)
    chk("rechaza token corto",
        res.get("ok") is False and res.get("stage") == "token")

    # 5) Verificar que el script de uninstall y el unit systemd se generan
    chk("UNINSTALL_SCRIPT contiene 'BORRAR' confirm",
        "BORRAR" in UNINSTALL_SCRIPT)
    chk("UNINSTALL_SCRIPT borra ~/.eidos/",
        "rm -rf \"$EIDOS_HOME\"" in UNINSTALL_SCRIPT)
    chk("SYSTEMD_UNIT tiene hardening NoNewPrivileges",
        "NoNewPrivileges=yes" in SYSTEMD_UNIT)
    chk("SYSTEMD_UNIT readonly home",
        "ProtectHome=read-only" in SYSTEMD_UNIT)
    chk("SYSTEMD_UNIT permite escritura solo en ~/.eidos",
        "ReadWritePaths=%h/.eidos" in SYSTEMD_UNIT)

    # 6) Comprobaciones de defaults sensatas
    chk("VISIBLE_SUBDIRS contiene los 4 esperados",
        set(VISIBLE_SUBDIRS) == {"books", "conversations",
                                  "library", "screenshots"})

    print("-" * 60)
    if failures:
        print(f"❌ FAIL ({len(failures)}): {failures}")
        return 1
    print("✅ SELF-TEST PASS")
    return 0


# ── CLI ────────────────────────────────────────────────────────────────────

def _cli() -> int:
    ap = argparse.ArgumentParser(prog="eidos_onboarding",
        description="Wizard de onboarding consciente del clon EIDOS (F7)")
    sub = ap.add_subparsers(dest="cmd", required=False)
    sub.add_parser("install", help="ejecuta el wizard interactivo (4 pantallas)")
    sub.add_parser("self-test", help="smoke test con stdin scripted (dry-run)")
    args = ap.parse_args()

    if args.cmd in (None, "install"):
        res = run_wizard(sys.stdin, sys.stdout, dry_run=False)
        return 0 if res.get("ok") else 1
    if args.cmd == "self-test":
        return _self_test()
    ap.print_help()
    return 0


if __name__ == "__main__":
    raise SystemExit(_cli())
