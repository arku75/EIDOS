"""core/eidos_cron_daemon.py — Stub para smoke test. El cron real usa systemd timers."""
import logging
import time
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

log = logging.getLogger("eidos.cron_daemon")


@dataclass
class CronDaemon:
    """Stub de cron daemon para smoke test."""
    _jobs: List[Dict[str, Any]] = field(default_factory=list)
    _jobs_executed: int = 0
    _running: bool = True
    _started_at: float = field(default_factory=time.time)

    def schedule(self, job_id: str, description: str, interval: str) -> Dict[str, Any]:
        """Programa un job. Stub: siempre OK."""
        job = {
            "id": job_id,
            "description": description,
            "schedule": interval,
            "created_at": time.time(),
            "last_run": None,
        }
        self._jobs.append(job)
        log.info("cron job scheduled: %s (%s)", job_id, interval)
        return job

    def tick(self):
        """Ejecuta un tick del scheduler. Stub: incrementa contador."""
        self._jobs_executed += 1

    def stats(self) -> Dict[str, Any]:
        """Estadísticas del cron daemon."""
        return {
            "running": self._running,
            "jobs_total": len(self._jobs),
            "jobs_executed": self._jobs_executed,
            "uptime_s": time.time() - self._started_at,
        }

    def run(self) -> bool:
        return self._running


# Singleton
_cron_daemon: Optional[CronDaemon] = None


def get_cron_daemon() -> CronDaemon:
    """Obtiene la instancia singleton del cron daemon (stub)."""
    global _cron_daemon
    if _cron_daemon is None:
        _cron_daemon = CronDaemon()
    return _cron_daemon


def run() -> bool:
    return True
