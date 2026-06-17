#!/usr/bin/env python3
"""
core/eidos_remote.py — Control remoto de PCs (S127)
====================================================
Soporta VNC (nativo), AnyDesk (vía GUI), SSH+X11.
Permite a EIDOS conectarse a otro PC, mover ratón, usar teclado,
y controlar el escritorio remoto completo.

Uso:
    python3 -m core.eidos_remote vnc --host 192.168.1.5 --password pass
    python3 -m core.eidos_remote anydesk --id 123456789
    python3 -m core.eidos_remote ssh --host user@remote
"""
from __future__ import annotations

import logging
import os
import subprocess
import time
from pathlib import Path
from typing import Any, Dict, Optional

log = logging.getLogger("eidos.remote")

# ── VNC Client ────────────────────────────────────────────────────
def vnc_connect(host: str, password: str = "", port: int = 5900,
                display: str = ":0") -> Dict[str, Any]:
    """Conecta a un servidor VNC usando xvnc4viewer o vncviewer."""
    viewer = None
    for candidate in ["xtightvncviewer", "vncviewer", "xvnc4viewer"]:
        if subprocess.run(["which", candidate], capture_output=True).returncode == 0:
            viewer = candidate
            break

    if not viewer:
        return {"ok": False, "error": "no VNC viewer installed. apt install xtightvncviewer"}

    cmd = [viewer, f"{host}:{port}"]
    if password:
        # Crear archivo de password temporal
        passfile = Path("/tmp/eidos_vnc_pass")
        passfile.write_text(password)
        os.chmod(passfile, 0o600)
        cmd.extend(["-passwd", str(passfile)])

    try:
        subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        time.sleep(2)
        return {"ok": True, "host": host, "port": port, "viewer": viewer,
                "description": f"VNC conectado a {host}:{port}"}
    except Exception as e:
        return {"ok": False, "error": str(e)[:200]}

# ── AnyDesk via GUI ───────────────────────────────────────────────
def anydesk_connect(anydesk_id: str, password: str = "") -> Dict[str, Any]:
    """Se conecta a otra máquina vía AnyDesk usando el BOM (GUI)."""
    anydesk_bin = "/usr/bin/anydesk"
    if not Path(anydesk_bin).exists():
        return {"ok": False, "error": "AnyDesk no instalado. Instálalo primero."}

    try:
        # Lanzar AnyDesk
        subprocess.Popen([anydesk_bin], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        time.sleep(3)

        # Usar BOM para escribir el ID y conectar
        from core.body import hand_position, active_window
        log.info("AnyDesk lanzado. Ventana: %s, Mano: %s", active_window(), hand_position())

        # El BOM debe tomar el control aquí para:
        # 1. Escribir el ID en el campo "Remote Desk ID"
        # 2. Hacer clic en "Connect"
        # 3. Si pide password, escribirla
        # Esto requiere el BOM en modo REAL (EIDOS_BOM=1)

        return {"ok": True, "anydesk_id": anydesk_id,
                "description": f"AnyDesk lanzado. ID: {anydesk_id}. BOM debe completar conexión.",
                "requires_bom": True}
    except Exception as e:
        return {"ok": False, "error": str(e)[:200]}

# ── SSH Remote Control ────────────────────────────────────────────
def ssh_execute(host: str, command: str, timeout: int = 30) -> Dict[str, Any]:
    """Ejecuta un comando en una máquina remota vía SSH."""
    try:
        r = subprocess.run(
            ["ssh", "-o", "ConnectTimeout=10", "-o", "StrictHostKeyChecking=no",
             host, command],
            capture_output=True, text=True, timeout=timeout
        )
        return {"ok": r.returncode == 0,
                "stdout": r.stdout[:1000],
                "stderr": r.stderr[:500]}
    except Exception as e:
        return {"ok": False, "error": str(e)[:200]}

# ── Unified Remote Control ─────────────────────────────────────────
def control_remote(protocol: str = "vnc", **kwargs) -> Dict[str, Any]:
    """Control remoto unificado. protocol: 'vnc', 'anydesk', 'ssh'."""
    protocols = {
        "vnc": lambda: vnc_connect(kwargs.get("host", ""), kwargs.get("password", "")),
        "anydesk": lambda: anydesk_connect(kwargs.get("anydesk_id", ""), kwargs.get("password", "")),
        "ssh": lambda: ssh_execute(kwargs.get("host", ""), kwargs.get("command", "")),
    }
    handler = protocols.get(protocol)
    if not handler:
        return {"ok": False, "error": f"protocolo desconocido: {protocol}. Usar: vnc, anydesk, ssh"}
    return handler()

# ── CLI ────────────────────────────────────────────────────────────
if __name__ == "__main__":
    import sys
    logging.basicConfig(level=logging.INFO)
    if len(sys.argv) < 3:
        print("Uso: python3 -m core.eidos_remote vnc --host <ip> [--password <pass>]")
        print("      python3 -m core.eidos_remote anydesk --id <ID>")
        print("      python3 -m core.eidos_remote ssh --host <user@host> --cmd <command>")
        sys.exit(1)

    protocol = sys.argv[1]
    kwargs = {}
    for i in range(2, len(sys.argv), 2):
        if sys.argv[i].startswith("--"):
            key = sys.argv[i][2:]
            val = sys.argv[i+1] if i+1 < len(sys.argv) else ""
            kwargs[key] = val

    import json
    result = control_remote(protocol, **kwargs)
    print(json.dumps(result, indent=2, ensure_ascii=False))
