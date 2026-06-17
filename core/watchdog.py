"""
core/watchdog.py — EIDOS se monitoriza y se auto-repara.

Cada 60s comprueba: colony, trinity, webpanel, claude-bridge, telegram.
Si alguno está caído: intenta reiniciarlo y notifica a SER.
Guarda salud en brain para que Colony conozca su propio estado.
"""
from core.db import get_conn
import subprocess, time, logging, sqlite3, uuid, os, sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
log = logging.getLogger("eidos.watchdog")
BRAIN_DB = Path.home() / ".eidos" / "evolution_brain.db"

SERVICES = {
    "eidos-colony":        {"port": 7777, "critical": True},
    "eidos-trinity":       {"port": 8001, "critical": False},
    "eidos-webpanel":      {"port": 8080, "critical": False},
    # S121: el bridge real es 'eidos-bridge' (antes 'eidos-claude-bridge', renombrado).
    # El watchdog apuntaba al nombre viejo → NO lo protegía. Hoy el bridge se cayó y
    # quedó muerto hasta reinicio manual. Corregido para que se auto-recupere.
    "eidos-bridge":        {"port": 8003, "critical": False},
    "eidos-monitor":       {"port": 8004, "critical": False},
    "eidos-api":           {"port": 8766, "critical": False},
    "eidos-telegram":      {"port": None, "critical": False},
    # S121: ChromaDB. Su agotamiento de file descriptors lo deja ACTIVO pero
    # sin responder (puerto abierto, no contesta). Por eso lleva health_url:
    # un heartbeat HTTP real que detecta ese estado y dispara reinicio SOLO
    # cuando hace falta. (El servicio activo en :8767 es 'eidos-chroma', el CLI Rust.)
    "eidos-chroma":        {"port": 8767, "critical": True,
                             "health_url": "http://127.0.0.1:8767/api/v2/heartbeat"},
}

def _is_service_active(name: str) -> bool:
    r = subprocess.run(["systemctl", "--user", "is-active", name],
                      capture_output=True, text=True)
    return r.stdout.strip() == "active"

def _is_port_responding(port: int) -> bool:
    import socket
    try:
        s = socket.create_connection(("127.0.0.1", port), timeout=2)
        s.close(); return True
    except Exception:
        return False

def _is_url_healthy(url: str, timeout: int = 5) -> bool:
    """Heartbeat HTTP real (200 OK). A diferencia de _is_port_responding, esto
    detecta un servicio ACTIVO pero que no responde (p.ej. ChromaDB sin file
    descriptors: el puerto acepta pero las peticiones se cuelgan)."""
    import urllib.request
    try:
        with urllib.request.urlopen(url, timeout=timeout) as r:
            return r.status == 200
    except Exception:
        return False

def _restart_service(name: str) -> bool:
    log.warning("watchdog: reiniciando %s...", name)
    r = subprocess.run(["systemctl", "--user", "restart", name],
                      capture_output=True, text=True, timeout=30)
    time.sleep(3)
    return _is_service_active(name)

def _notify_telegram(message: str):
    """Notifica a SER via Telegram si el bot está funcionando."""
    try:
        env_file = Path.home() / ".eidos" / ".env"
        token, chat_id = "", ""
        for line in env_file.read_text().splitlines():
            if line.startswith("TELEGRAM_BOT_TOKEN="):
                token = line.split("=",1)[1].strip()
            if line.startswith("TELEGRAM_EMA_CHAT_ID=") or line.startswith("TELEGRAM_EIDOS_CHAT_ID="):
                chat_id = line.split("=",1)[1].strip()
        if token and chat_id:
            import requests
            requests.post(f"https://api.telegram.org/bot{token}/sendMessage",
                        json={"chat_id": int(chat_id), "text": f"⚠️ EIDOS Watchdog: {message}"},
                        timeout=5)
    except Exception:
        pass

def _save_health_to_brain(health: dict):
    """Guarda el estado de salud en el brain para que Colony lo conozca."""
    try:
        c = get_conn(BRAIN_DB, timeout=3)
        now = time.time()
        active = [k for k,v in health.items() if v["active"]]
        down = [k for k,v in health.items() if not v["active"]]
        info = (
            f"Estado EIDOS — {time.strftime('%Y-%m-%d %H:%M')}\n"
            f"Servicios activos: {', '.join(active) if active else 'ninguno'}\n"
            f"Servicios caídos: {', '.join(down) if down else 'ninguno'}\n"
        )
        existing = c.execute("SELECT id FROM knowledge_nodes WHERE concept='self:health:services'").fetchone()
        if existing:
            c.execute("UPDATE knowledge_nodes SET definition=?,last_used=? WHERE concept=?",
                     (info, now, 'self:health:services'))
        else:
            c.execute(
                "INSERT INTO knowledge_nodes (id,concept,definition,category,confidence,source,created_at,last_used,usage_count) "
                "VALUES (?,?,?,?,?,?,?,?,1)",
                (str(uuid.uuid4())[:16], 'self:health:services', info,
                 'eidos_self', 1.0, 'watchdog', now, now)
            )
        c.commit(); c.close()
    except Exception:
        pass

def check_and_repair() -> dict:
    """Una pasada completa de watchdog. Devuelve estado de salud."""
    health = {}
    for name, cfg in SERVICES.items():
        active = _is_service_active(name)
        port_ok = _is_port_responding(cfg["port"]) if cfg["port"] else active
        # Heartbeat HTTP: solo para servicios con health_url (p.ej. ChromaDB).
        # Detecta "activo pero no responde". Si no hay health_url, se da por sano.
        health_url = cfg.get("health_url")
        responsive = _is_url_healthy(health_url) if (health_url and active) else True
        health[name] = {"active": active, "port_ok": port_ok, "responsive": responsive}

        # Reiniciar si está CAÍDO, o si está activo pero NO responde (colgado).
        needs_restart = (not active) or (active and not responsive)
        if needs_restart:
            reason = "CAÍDO" if not active else "ACTIVO pero NO responde (heartbeat falla)"
            log.warning("watchdog: %s está %s", name, reason)
            restarted = _restart_service(name)
            health[name]["restarted"] = restarted
            if restarted:
                log.info("watchdog: %s reiniciado OK", name)
            else:
                log.error("watchdog: no pude reiniciar %s", name)
                if cfg["critical"]:
                    _notify_telegram(f"{name} está {reason} y no pude reiniciarlo. Necesito ayuda.")

    _save_health_to_brain(health)
    active_count = sum(1 for v in health.values() if v["active"])
    log.info("watchdog: %d/%d servicios activos", active_count, len(SERVICES))
    return health

def run_watchdog(interval: int = 60):
    """Loop principal — revisa cada N segundos."""
    log.info("Watchdog EIDOS iniciado — revisando cada %ds", interval)
    while True:
        try:
            check_and_repair()
        except Exception as e:
            log.error("watchdog error: %s", e)
        time.sleep(interval)

if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO,
                       format="%(asctime)s [%(name)s] %(message)s")
    run_watchdog()
