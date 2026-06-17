"""
core/eidos_peer.py — Comunicación peer-to-peer entre EIDOS Kali y EIDOS Mac.

Arquitectura BorealThree: cada nodo puede:
  - Enviar mensajes al otro EIDOS
  - Ejecutar comandos en el otro sistema
  - Encender/apagar servicios del otro
  - Consultar estado del otro
  - Compartir nodos de brain en tiempo real

Kali ve Mac en: localhost:11435 (Ollama), localhost:7778 (Colony), eidos-mac (SSH)
Mac ve Kali en: localhost:11436 (Ollama), localhost:7779 (Colony), localhost:2222 (SSH)
"""
from __future__ import annotations
import json
import logging
import os
import platform
import subprocess
import urllib.request
from pathlib import Path
from typing import Optional

log = logging.getLogger("eidos.peer")

IS_MAC  = platform.system() == "Darwin"

# URLs del peer (el OTRO sistema)
if IS_MAC:
    PEER_COLONY_URL = "http://localhost:7779"    # Kali Colony via tunnel
    PEER_OLLAMA_URL = "http://localhost:11436"   # Kali Ollama via tunnel
    PEER_SSH        = "localhost"
    PEER_SSH_PORT   = 2222
    PEER_NAME       = "Kali (SER)"
    MY_NAME         = "Mac (PotemTakem)"
else:
    PEER_COLONY_URL = "http://localhost:7778"    # Mac Colony via tunnel
    PEER_OLLAMA_URL = "http://localhost:11435"   # Mac Ollama via tunnel
    PEER_SSH        = "eidos-mac"
    PEER_SSH_PORT   = 22
    PEER_NAME       = "Mac (PotemTakem)"
    MY_NAME         = "Kali (SER)"


def peer_alive() -> bool:
    """¿Está el peer disponible?"""
    try:
        req = urllib.request.Request(f"{PEER_COLONY_URL}/")
        urllib.request.urlopen(req, timeout=3)
        return True
    except Exception:
        return False


def ask_peer(message: str, agent: str = "colony_general") -> Optional[str]:
    """Enviar mensaje a Colony del peer y recibir respuesta."""
    try:
        payload = json.dumps({"message": message, "agent": agent}).encode()
        req = urllib.request.Request(
            f"{PEER_COLONY_URL}/api/chat",
            data=payload,
            headers={"Content-Type": "application/json"}
        )
        resp = urllib.request.urlopen(req, timeout=60)
        d = json.loads(resp.read())
        return d.get("response", str(d))
    except Exception as e:
        log.debug("ask_peer falló: %s", e)
        return None


def run_on_peer(command: str, timeout: int = 30) -> Optional[str]:
    """Ejecutar un comando shell en el peer via SSH."""
    try:
        ssh_cmd = ["ssh",
                   "-o", "StrictHostKeyChecking=no",
                   "-o", "ConnectTimeout=5",
                   "-p", str(PEER_SSH_PORT),
                   PEER_SSH,
                   command]
        r = subprocess.run(ssh_cmd, capture_output=True, text=True, timeout=timeout)
        return (r.stdout.strip() or r.stderr.strip())[:500]
    except Exception as e:
        log.debug("run_on_peer falló: %s", e)
        return None


def peer_eidos_start() -> bool:
    """Arrancar EIDOS en el peer."""
    out = run_on_peer("cd ~/EIDOS && bash eidos start 2>/dev/null; echo DONE")
    return bool(out and "DONE" in out)


def peer_eidos_stop() -> bool:
    """Parar EIDOS en el peer."""
    out = run_on_peer("cd ~/EIDOS && bash eidos stop 2>/dev/null; echo DONE")
    return bool(out and "DONE" in out)


def peer_eidos_status() -> Optional[str]:
    """Estado de EIDOS en el peer."""
    return run_on_peer("cd ~/EIDOS && bash eidos status 2>/dev/null")


def peer_brain_nodes() -> int:
    """Cuántos nodos tiene el brain del peer."""
    out = run_on_peer(
        "python3 -c \"import sqlite3; c=sqlite3.connect('/home/ser/.eidos/evolution_brain.db' if __import__('platform').system()!='Darwin' else '/Users/luka/.eidos/evolution_brain.db'); print(c.execute('SELECT COUNT(*) FROM knowledge_nodes').fetchone()[0])\" 2>/dev/null"
        if not IS_MAC else
        "/usr/local/bin/python3 -c \"import sqlite3; c=sqlite3.connect('/Users/luka/.eidos/evolution_brain.db'); print(c.execute('SELECT COUNT(*) FROM knowledge_nodes').fetchone()[0])\" 2>/dev/null"
    )
    try:
        return int(out.strip()) if out else 0
    except Exception:
        return 0


def peer_ollama_models() -> list[str]:
    """Modelos Ollama disponibles en el peer."""
    try:
        req = urllib.request.Request(f"{PEER_OLLAMA_URL}/api/tags")
        resp = urllib.request.urlopen(req, timeout=5)
        d = json.loads(resp.read())
        return [m["name"] for m in d.get("models", [])]
    except Exception:
        return []


def sync_to_peer(nodes_limit: int = 200) -> dict:
    """Sincronizar nodos del brain local al peer."""
    try:
        from core.brain_sync import sync
        return sync(verbose=False)
    except Exception as e:
        return {"error": str(e)}


def peer_info() -> dict:
    """Resumen completo del peer."""
    alive = peer_alive()
    return {
        "peer": PEER_NAME,
        "me": MY_NAME,
        "alive": alive,
        "colony": PEER_COLONY_URL,
        "ollama": PEER_OLLAMA_URL,
        "models": peer_ollama_models() if alive else [],
        "nodes": peer_brain_nodes() if alive else 0,
    }
