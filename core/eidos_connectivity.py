"""
core/eidos_connectivity.py — Detección offline/online de EIDOS  [S75]

Permite a EIDOS saber si tiene internet sin depender de servicios frágiles.
Usa 3 capas: interfaces de red → HTTP rápido → DNS fallback.
Cachea el resultado para no repetir tests en cada consulta.

Uso:
    from core.eidos_connectivity import is_online, online_status
    if is_online():
        research_web()
    else:
        research_local()

Flag --force-offline: tocar ~/.eidos/force_offline
Flag --force-online:  tocar ~/.eidos/force_online (anula offline)
"""
from __future__ import annotations

import os
import subprocess
import time
from pathlib import Path

_CACHE = Path("/tmp/eidos_online_status")
_FORCE_OFFLINE = Path.home() / ".eidos" / "force_offline"
_FORCE_ONLINE = Path.home() / ".eidos" / "force_online"
_DEFAULT_CACHE_SEC = 30


def is_online(cache_sec: int = _DEFAULT_CACHE_SEC) -> bool:
    """True si EIDOS tiene conectividad a internet. Resultado cacheado."""
    # Flags manuales (SER controla)
    if _FORCE_OFFLINE.exists():
        return False
    if _FORCE_ONLINE.exists():
        return True

    # Cache reciente
    if _CACHE.exists() and time.time() - _CACHE.stat().st_mtime < cache_sec:
        try:
            return _CACHE.read_text().strip() == "online"
        except Exception:
            pass

    online = _check_connectivity()
    try:
        _CACHE.parent.mkdir(parents=True, exist_ok=True)
        _CACHE.write_text("online" if online else "offline")
    except Exception:
        pass
    return online


def _check_connectivity() -> bool:
    """3-capas: interfaces → HTTP → DNS."""
    # Capa 1: ¿hay interfaces de red no-loopback UP?
    try:
        out = subprocess.check_output(
            ["ip", "link", "show", "up"], text=True, timeout=5
        )
        up_lines = [l for l in out.split("\n") if "state UP" in l and "lo:" not in l]
        if not up_lines:
            return False
    except Exception:
        return False

    # Capa 2: HTTP rápido a example.com (siempre responde, sin redirects)
    try:
        code = subprocess.check_output(
            ["curl", "--max-time", "2", "-s", "-o", "/dev/null",
             "-w", "%{http_code}", "http://example.com"],
            text=True, timeout=5
        ).strip()
        if code == "200":
            return True
    except Exception:
        pass

    # Capa 3: DNS fallback (si resuelve, hay internet o al menos DNS)
    try:
        subprocess.check_output(
            ["nslookup", "google.com"], text=True,
            timeout=3, stderr=subprocess.STDOUT
        )
        return True
    except Exception:
        return False

    return False


def online_status() -> dict:
    """Estado detallado de conectividad para /health y debugging."""
    return {
        "online": is_online(),
        "force_offline": _FORCE_OFFLINE.exists(),
        "force_online": _FORCE_ONLINE.exists(),
        "cache_age_sec": (
            time.time() - _CACHE.stat().st_mtime if _CACHE.exists() else None
        ),
    }


def set_force_offline(offline: bool = True):
    """Activa/desactiva modo offline forzado."""
    if offline:
        _FORCE_OFFLINE.parent.mkdir(parents=True, exist_ok=True)
        _FORCE_OFFLINE.touch()
        _FORCE_ONLINE.unlink(missing_ok=True)
    else:
        _FORCE_OFFLINE.unlink(missing_ok=True)


def set_force_online(online: bool = True):
    """Activa/desactiva modo online forzado (anula offline)."""
    if online:
        _FORCE_ONLINE.parent.mkdir(parents=True, exist_ok=True)
        _FORCE_ONLINE.touch()
        _FORCE_OFFLINE.unlink(missing_ok=True)
    else:
        _FORCE_ONLINE.unlink(missing_ok=True)


if __name__ == "__main__":
    import json
    print(json.dumps(online_status(), indent=2))
