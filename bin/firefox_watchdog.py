#!/usr/bin/env python3
"""firefox_watchdog.py — Watchdog que mantiene Firefox vivo para EIDOS [S94]

"Sin navegador no hay misiones." — DeepSeek

Monitorea que Firefox tenga ventanas detectables. Si se cae (0 ventanas),
lo reinicia automáticamente. Corre como proceso daemon ligero.

Uso:
    python3 bin/firefox_watchdog.py [--interval 30] [--once]

    --interval N  : segundos entre chequeos (default: 30)
    --once        : ejecutar un chequeo y salir
"""

import logging
import os
import signal
import subprocess
import sys
import time
from pathlib import Path

log = logging.getLogger("eidos.firefox_watchdog")
LOG_PATH = Path.home() / ".eidos" / "logs" / "firefox_watchdog.log"

# ── Configuración ────────────────────────────────────────────────────────────────
CHECK_INTERVAL = 30  # segundos entre chequeos
MAX_RESTARTS_PER_HOUR = 10  # límite para evitar bucle de reinicio
STARTUP_GRACE = 10  # segundos de gracia tras reinicio

_restart_times = []  # timestamps de reinicios


def _has_firefox_windows() -> bool:
    """Retorna True si hay al menos 1 ventana Firefox detectable."""
    try:
        # Intentar por clase (Firefox ESR en Kali tiene títulos vacíos)
        for cls in ["Firefox", "firefox", "Navigator"]:
            r = subprocess.run(
                ["xdotool", "search", "--class", cls],
                capture_output=True, text=True, timeout=3
            )
            if r.returncode == 0 and r.stdout.strip():
                return True
        return False
    except Exception:
        return False


def _is_firefox_process_alive() -> bool:
    """Verifica si el proceso Firefox está corriendo."""
    try:
        r = subprocess.run(
            ["pgrep", "-f", "firefox"],
            capture_output=True, text=True, timeout=3
        )
        return r.returncode == 0
    except Exception:
        return False


def _can_restart() -> bool:
    """Verifica que no estemos en un bucle de reinicios."""
    global _restart_times
    now = time.time()
    # Limpiar timestamps viejos (>1 hora)
    _restart_times = [t for t in _restart_times if now - t < 3600]
    return len(_restart_times) < MAX_RESTARTS_PER_HOUR


def _restart_firefox() -> bool:
    """Reinicia Firefox completamente. Retorna True si éxito."""
    global _restart_times

    if not _can_restart():
        log.error("Límite de reinicios/hora alcanzado (%d). ABORTANDO watchdog.",
                  MAX_RESTARTS_PER_HOUR)
        return False

    log.warning("Reiniciando Firefox...")

    # Matar firefox existente
    subprocess.run(["pkill", "-9", "firefox"], capture_output=True, timeout=5)
    subprocess.run(["pkill", "-9", "firefox-esr"], capture_output=True, timeout=5)
    time.sleep(2)

    # Limpiar locks
    locks = list(Path.home().glob(".mozilla/firefox/*.default*/lock"))
    locks += list(Path.home().glob(".mozilla/firefox/*.default*/.parentlock"))
    for lock in locks:
        try:
            lock.unlink()
        except Exception:
            pass

    # Iniciar Firefox
    env = dict(os.environ, DISPLAY=":0")
    subprocess.Popen(
        ["firefox-esr", "about:blank"],
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        env=env,
    )

    time.sleep(STARTUP_GRACE)

    # Verificar
    if _has_firefox_windows():
        _restart_times.append(time.time())
        log.info("Firefox reiniciado correctamente. %d ventanas detectadas.",
                 _count_firefox_windows())
        return True
    else:
        log.error("Firefox iniciado pero SIN ventanas. Posible problema de perfil/X11.")
        return False


def _count_firefox_windows() -> int:
    """Cuenta ventanas Firefox detectables."""
    try:
        count = 0
        for cls in ["Firefox", "firefox"]:
            r = subprocess.run(
                ["xdotool", "search", "--class", cls],
                capture_output=True, text=True, timeout=3
            )
            if r.returncode == 0:
                count += len(r.stdout.strip().split("\n"))
        return count
    except Exception:
        return -1


def run_once() -> int:
    """Ejecuta un único chequeo y retorna 0 si OK, 1 si necesitó reinicio, 2 si fallo."""
    if _has_firefox_windows():
        log.info("Firefox OK: %d ventanas", _count_firefox_windows())
        return 0

    # Verificar 2 veces con delay (xdotool puede fallar momentáneamente)
    log.debug("Ventanas no detectadas — rechequeando en 3s...")
    time.sleep(3)
    if _has_firefox_windows():
        log.info("Firefox OK tras rechequeo (falso positivo).")
        return 0

    log.warning("Firefox SIN ventanas detectables (2 chequeos).")

    if _is_firefox_process_alive():
        # El proceso está vivo pero no detectamos ventanas.
        # Puede estar minimizado, en otro workspace, o xdotool fallando.
        # NO matar un proceso vivo — solo advertir.
        log.warning("Proceso Firefox VIVO pero sin ventanas detectables. "
                     "NO se reinicia (posible minimizado o error xdotool).")
        return 0  # Tratar como OK — el proceso está ahí

    # Proceso muerto → reiniciar
    log.warning("Proceso Firefox MUERTO. Reiniciando.")
    if _restart_firefox():
        return 1
    return 2


def run_daemon():
    """Ejecuta el watchdog como daemon (loop infinito)."""
    log.info("Firefox Watchdog iniciado. Intervalo: %ds", CHECK_INTERVAL)
    signal.signal(signal.SIGTERM, lambda *_: sys.exit(0))
    signal.signal(signal.SIGINT, lambda *_: sys.exit(0))

    consecutive_failures = 0

    while True:
        try:
            result = run_once()
            if result == 0:
                consecutive_failures = 0
            elif result == 1:
                consecutive_failures = 0  # Se reinició, es OK
            else:
                consecutive_failures += 1

            if consecutive_failures >= 3:
                log.critical("3 fallos consecutivos. Watchdog en estado crítico.")
                # No salir, seguir intentando pero con intervalo más largo

        except Exception as e:
            log.error("Error en watchdog: %s", e)
            consecutive_failures += 1

        time.sleep(CHECK_INTERVAL)


# ── CLI ──────────────────────────────────────────────────────────────────────────
if __name__ == "__main__":
    import argparse

    p = argparse.ArgumentParser(
        description="Firefox Watchdog para EIDOS — mantiene Firefox vivo"
    )
    p.add_argument("--interval", type=int, default=CHECK_INTERVAL,
                   help=f"Segundos entre chequeos (default: {CHECK_INTERVAL})")
    p.add_argument("--once", action="store_true",
                   help="Ejecutar un solo chequeo y salir")
    args = p.parse_args()

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s",
        handlers=[
            logging.FileHandler(LOG_PATH),
            logging.StreamHandler(sys.stderr),
        ],
    )

    CHECK_INTERVAL = args.interval

    if args.once:
        sys.exit(run_once())
    else:
        run_daemon()
