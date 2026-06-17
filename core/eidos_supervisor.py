"""
core/eidos_supervisor.py — Supervisor de EIDOS (S78 "Supervivencia")

Inspirado en eidos_master_controller.py + eidos_watchdog.py de SER (NO TOCAR).
Gestiona PID, restart log, self-repair, auto-backup y health checks.

API:
    sup = get_supervisor()
    sup.health_check() → Dict con estado de todos los componentes
    sup.start() → inicia monitoreo 24/7
    sup.auto_backup() → backup automático de brain DB
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import shutil
import signal
import sqlite3
import subprocess
import sys
import threading
import time
from datetime import datetime
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple
from core.db import get_conn

log = logging.getLogger("eidos.supervisor")

PID_FILE = Path("/tmp/eidos_supervisor.pid")
STATE_DB = Path.home() / ".eidos" / "master_state.db"
LOG_FILE = Path.home() / ".eidos" / "supervisor.log"
BACKUP_DIR = Path.home() / ".eidos" / "backups"
BRAIN_DB = Path.home() / ".eidos" / "evolution_brain.db"
MAX_BACKUPS = 7
HEALTH_CHECK_INTERVAL = 300  # 5 minutos
BACKUP_INTERVAL = 3600 * 6  # 6 horas

COMPONENTS = [
    "brain_db",
    "event_bus",
    "affect_engine",
    "rl_agent",
    "knowledge_reasoner",
    "colony_community",
    "bridge_server",
    "vivo_cycle",
]


class Supervisor:
    """Supervisor maestro de EIDOS — PID + self-repair + auto-backup."""

    # S96: Traducción de eventos del sistema a dolor VAD
    SYSTEM_PAIN_MAP = {
        'oom_kill': {'event': 'system.oom', 'delta_v': -0.15, 'delta_d': -0.20, 'severity': 0.9},
        'disk_full': {'event': 'system.disk', 'delta_v': -0.10, 'delta_d': -0.15, 'severity': 0.7},
        'process_crash': {'event': 'system.crash', 'delta_v': -0.12, 'delta_d': -0.18, 'severity': 0.8},
        'chroma_segv': {'event': 'system.chroma_segv', 'delta_v': -0.08, 'delta_d': -0.10, 'severity': 0.6},
        'cpu_throttle': {'event': 'system.cpu_throttle', 'delta_v': -0.03, 'delta_a': +0.10, 'severity': 0.4},
        'memory_high': {'event': 'system.memory_high', 'delta_v': -0.05, 'delta_d': -0.08, 'severity': 0.5},
        'bridge_timeout': {'event': 'system.bridge_timeout', 'delta_v': -0.04, 'delta_d': -0.06, 'severity': 0.5},
    }

    def __init__(self):
        self._running = False
        self._thread: Optional[threading.Thread] = None
        self._health_history: List[Dict[str, Any]] = []
        self._repair_count = 0
        self._backup_count = 0
        self._last_backup: float = 0
        self._init_db()
        self._write_pid()

    # ── PID ──────────────────────────────────────────────────────────────────

    def _write_pid(self):
        try:
            PID_FILE.write_text(str(os.getpid()))
        except Exception:
            pass

    def _remove_pid(self):
        try:
            if PID_FILE.exists():
                PID_FILE.unlink()
        except Exception:
            pass

    # ── DB de estado ────────────────────────────────────────────────────────

    def _init_db(self):
        try:
            STATE_DB.parent.mkdir(parents=True, exist_ok=True)
            conn = get_conn(STATE_DB, timeout=10)
            conn.execute("PRAGMA busy_timeout=10000")
            conn.execute("""CREATE TABLE IF NOT EXISTS system_state (
                key TEXT PRIMARY KEY, value TEXT, updated_at REAL)""")
            conn.execute("""CREATE TABLE IF NOT EXISTS restart_log (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                timestamp REAL, reason TEXT, component TEXT,
                success INTEGER DEFAULT 0)""")
            conn.execute("""CREATE TABLE IF NOT EXISTS health_checks (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                timestamp REAL, component TEXT, status TEXT,
                detail TEXT DEFAULT '')""")
            conn.commit()

        except Exception as e:
            log.warning("Supervisor DB init: %s", e)

    def _set_state(self, key: str, value: Any):
        try:
            conn = get_conn(STATE_DB, timeout=5)
            conn.execute(
                "INSERT OR REPLACE INTO system_state (key, value, updated_at) VALUES (?,?,?)",
                (key, json.dumps(value), time.time()))
            conn.commit()

        except Exception as e:
            log.debug("_set_state: %s", e)

    def _get_state(self, key: str, default: Any = None) -> Any:
        try:
            conn = get_conn(STATE_DB, timeout=5)
            row = conn.execute(
                "SELECT value FROM system_state WHERE key=?", (key,)).fetchone()

            return json.loads(row[0]) if row else default
        except Exception:
            return default

    # ── Health Check ─────────────────────────────────────────────────────────

    def health_check(self) -> Dict[str, Any]:
        """Verifica salud de todos los componentes críticos."""
        results = {}
        now = time.time()

        # 1. Brain DB
        try:
            if BRAIN_DB.exists():
                conn = get_conn(BRAIN_DB, timeout=3)
                nodes = conn.execute("SELECT COUNT(*) FROM knowledge_nodes").fetchone()[0]
                edges = conn.execute("SELECT COUNT(*) FROM knowledge_edges").fetchone()[0]

                results["brain_db"] = {"status": "healthy", "nodes": nodes, "edges": edges}
            else:
                results["brain_db"] = {"status": "missing", "error": "DB no existe"}
        except Exception as e:
            results["brain_db"] = {"status": "error", "error": str(e)[:100]}

        # 2. EventBus
        try:
            from core.eidos_events import get_event_bus
            bus = get_event_bus()
            stats = bus.stats()
            results["event_bus"] = {"status": "healthy", "total_events": stats.get("total_events", 0)}
        except Exception as e:
            results["event_bus"] = {"status": "error", "error": str(e)[:100]}

        # 3. AffectEngine
        try:
            from core.eidos_affect import get_affect
            aff = get_affect()
            results["affect_engine"] = {
                "status": "healthy",
                "mood": aff.state.mood,
                "vad": f"{aff.state.valence:.2f}/{aff.state.arousal:.2f}/{aff.state.dominance:.2f}",
            }
        except Exception as e:
            results["affect_engine"] = {"status": "error", "error": str(e)[:100]}

        # 4. RL Agent
        try:
            from core.eidos_rl import get_rl_agent
            rl = get_rl_agent()
            rl_stats = rl.stats()
            results["rl_agent"] = {"status": "healthy", **rl_stats}
        except Exception as e:
            results["rl_agent"] = {"status": "error", "error": str(e)[:100]}

        # 5. Knowledge Reasoner
        try:
            from core.knowledge_reasoner import get_reasoner
            r = get_reasoner()
            results["knowledge_reasoner"] = {
                "status": "healthy" if r._ready else "not_ready",
                "ready": r._ready,
            }
        except Exception as e:
            results["knowledge_reasoner"] = {"status": "error", "error": str(e)[:100]}

        # 6. Colony Community
        try:
            from core.colony_community import get_colony_community
            colony = get_colony_community()
            results["colony_community"] = {"status": "healthy", "session_active": colony._session_active}
        except Exception as e:
            results["colony_community"] = {"status": "error", "error": str(e)[:100]}

        # 7. Bridge Server (HTTP check)
        try:
            import urllib.request
            req = urllib.request.Request("http://127.0.0.1:8003/health", method="GET")
            resp = urllib.request.urlopen(req, timeout=5)
            results["bridge_server"] = {"status": "healthy", "http_code": resp.getcode()}
        except Exception as e:
            results["bridge_server"] = {"status": "down", "error": str(e)[:100]}

        # 8. Vivo cycle
        try:
            vivo_status = Path.home() / ".eidos" / "vivo_status.json"
            if vivo_status.exists():
                status = json.loads(vivo_status.read_text())
                last_cycle = status.get("last_cycle_ago_s", 999)
                results["vivo_cycle"] = {
                    "status": "healthy" if last_cycle < 900 else "stale",
                    "cycle": status.get("cycle", 0),
                    "last_cycle_ago_s": last_cycle,
                }
            else:
                results["vivo_cycle"] = {"status": "unknown", "error": "no status file"}
        except Exception as e:
            results["vivo_cycle"] = {"status": "error", "error": str(e)[:100]}

        # Persistir health check
        for comp, data in results.items():
            self._record_health(comp, data.get("status", "unknown"),
                              str(data.get("error", "")))

        # Calcular health score
        healthy = sum(1 for d in results.values() if d.get("status") in ("healthy",))
        results["_score"] = f"{healthy}/{len(results)}"
        results["_timestamp"] = now

        self._health_history.append(results)
        self._health_history = self._health_history[-100:]

        return results

    def _record_health(self, component: str, status: str, detail: str = ""):
        try:
            conn = get_conn(STATE_DB, timeout=5)
            conn.execute(
                "INSERT INTO health_checks (timestamp, component, status, detail) VALUES (?,?,?,?)",
                (time.time(), component, status, detail[:500]))
            conn.commit()

        except Exception:
            pass

    # ── S96: Detección de dolor del sistema ───────────────────────────────

    def _detect_system_pain(self):
        """[S96] Detecta condiciones de dolor del cuerpo digital y emite eventos.

        Traduce señales del sistema (disco lleno, OOM, crash) a eventos VAD
        que el AffectEngine procesa y el daemon puede percibir.
        """
        import shutil
        pains = []

        # 1. Disco
        try:
            usage = shutil.disk_usage(str(Path.home()))
            pct = usage.used / usage.total
            if pct > 0.95:
                pains.append(('disk_full', {
                    'used_pct': round(pct * 100, 1),
                    'free_gb': round(usage.free / 1e9, 1),
                }))
        except Exception:
            pass

        # 2. Memoria
        try:
            with open('/proc/meminfo') as f:
                mem = {}
                for line in f:
                    if ':' in line:
                        k, v = line.split(':', 1)
                        mem[k.strip()] = int(v.strip().split()[0])
            total = mem.get('MemTotal', 1)
            available = mem.get('MemAvailable', 0)
            mem_pct = (total - available) / total
            if mem_pct > 0.90:
                pains.append(('memory_high', {
                    'used_pct': round(mem_pct * 100, 1),
                    'available_mb': round(available / 1024, 1),
                }))
        except Exception:
            pass

        # 3. Emitir eventos de dolor
        for pain_type, data in pains:
            mapping = self.SYSTEM_PAIN_MAP.get(pain_type, {})
            if mapping:
                try:
                    from core.eidos_affect import get_affect
                    affect = get_affect()
                    affect.event(
                        'health_changed',
                        health=max(0.1, 1.0 - mapping.get('severity', 0.5)),
                        severity=mapping.get('severity', 0.5),
                    )
                except Exception:
                    pass

                try:
                    from core.eidos_self_core import get_self_core
                    sc = get_self_core()
                    sc.record_event(
                        mapping.get('event', f'system.{pain_type}'),
                        {**data,
                         'delta_v': mapping.get('delta_v', 0),
                         'delta_d': mapping.get('delta_d', 0),
                         'severity': mapping.get('severity', 0.5)},
                        source='supervisor'
                    )
                except Exception:
                    pass

                self._record_health(
                    'system_pain', 'warning',
                    f'{pain_type}: {json.dumps(data)}'
                )

    # ── Self-Repair ─────────────────────────────────────────────────────────

    def repair(self, component: Optional[str] = None) -> Dict[str, Any]:
        """Intenta reparar componentes fallidos."""
        repairs = {}
        targets = [component] if component else COMPONENTS

        for comp in targets:
            try:
                if comp == "brain_db":
                    repairs[comp] = self._repair_brain_db()
                elif comp == "knowledge_reasoner":
                    repairs[comp] = self._repair_reasoner()
                elif comp == "bridge_server":
                    repairs[comp] = self._repair_bridge()
                elif comp == "vivo_cycle":
                    repairs[comp] = self._repair_vivo()
                else:
                    repairs[comp] = {"status": "skipped", "reason": "no repair strategy"}
            except Exception as e:
                repairs[comp] = {"status": "repair_failed", "error": str(e)[:100]}

        repaired = sum(1 for r in repairs.values() if r.get("status") == "repaired")
        self._repair_count += repaired

        # Log repair
        try:
            conn = get_conn(STATE_DB, timeout=5)
            for comp, result in repairs.items():
                conn.execute(
                    "INSERT INTO restart_log (timestamp, reason, component, success) VALUES (?,?,?,?)",
                    (time.time(), "health_repair", comp, 1 if result.get("status") == "repaired" else 0))
            conn.commit()

        except Exception:
            pass

        # Emitir evento
        try:
            from core.eidos_events import emit
            emit("health_changed", {"repairs": repairs, "repaired": repaired}, source="supervisor")
        except Exception:
            pass

        return {"repairs": repairs, "repaired": repaired, "total_repairs": self._repair_count}

    def _repair_brain_db(self) -> Dict[str, str]:
        """Verifica integridad de brain.db y restaura desde backup si es necesario."""
        try:
            if not BRAIN_DB.exists():
                # Buscar backup más reciente
                backups = sorted(BACKUP_DIR.glob("brain_*.db.gz"), reverse=True)
                if backups:
                    import gzip
                    with gzip.open(backups[0], 'rb') as f_in:
                        BRAIN_DB.write_bytes(f_in.read())
                    return {"status": "repaired", "from_backup": str(backups[0])}
                return {"status": "cannot_repair", "reason": "no backups available"}
            # Verificar integridad SQLite
            conn = get_conn(BRAIN_DB, timeout=5)
            conn.execute("PRAGMA integrity_check")

            return {"status": "healthy", "reason": "integrity check passed"}
        except Exception as e:
            return {"status": "repair_failed", "error": str(e)[:100]}

    def _repair_reasoner(self) -> Dict[str, str]:
        """Reconstruye el grafo desde cero."""
        try:
            from core.knowledge_reasoner import get_reasoner
            r = get_reasoner()
            r.build_graph()
            return {"status": "repaired", "reason": "graph rebuilt"}
        except Exception as e:
            return {"status": "repair_failed", "error": str(e)[:100]}

    def _repair_bridge(self) -> Dict[str, str]:
        """Verifica si el bridge responde. No lo reinicia automáticamente."""
        try:
            import urllib.request
            req = urllib.request.Request("http://127.0.0.1:8003/health", method="GET")
            resp = urllib.request.urlopen(req, timeout=3)
            if resp.getcode() == 200:
                return {"status": "healthy", "reason": "bridge responding"}
            return {"status": "needs_manual", "reason": f"HTTP {resp.getcode()}"}
        except Exception as e:
            return {"status": "needs_manual", "reason": str(e)[:100]}

    def _repair_vivo(self) -> Dict[str, str]:
        """Verifica si el ciclo vivo está corriendo."""
        try:
            # S82 fix: eliminado import muerto de EidosVivo (no se usa, solo lee vivo_status.json)
            vivo_path = Path.home() / ".eidos" / "vivo_status.json"
            if vivo_path.exists():
                data = json.loads(vivo_path.read_text())
                ago = data.get("last_cycle_ago_s", 9999)
                if ago < 900:
                    return {"status": "healthy", "reason": f"last cycle {ago}s ago"}
            return {"status": "needs_manual", "reason": "vivo cycle stale or stopped"}
        except Exception as e:
            return {"status": "repair_failed", "error": str(e)[:100]}

    # ── Auto-Backup ──────────────────────────────────────────────────────────

    def auto_backup(self) -> Dict[str, Any]:
        """Backup automático de brain.db + estado emocional + Q-values."""
        BACKUP_DIR.mkdir(parents=True, exist_ok=True)
        now = time.time()
        stamp = datetime.now().strftime("%Y%m%d_%H%M%S")

        results = {}

        # 1. Brain DB
        if BRAIN_DB.exists():
            try:
                import gzip
                dest = BACKUP_DIR / f"brain_{stamp}.db.gz"
                with open(BRAIN_DB, 'rb') as f_in:
                    with gzip.open(dest, 'wb', compresslevel=6) as f_out:
                        f_out.write(f_in.read())
                results["brain_db"] = {"status": "ok", "size_mb": round(dest.stat().st_size / 1e6, 2)}
            except Exception as e:
                results["brain_db"] = {"status": "error", "error": str(e)[:100]}

        # 2. Affect state
        affect_file = Path.home() / ".eidos" / "affect_state.json"
        if affect_file.exists():
            try:
                shutil.copy2(affect_file, BACKUP_DIR / f"affect_{stamp}.json")
                results["affect"] = {"status": "ok"}
            except Exception as e:
                results["affect"] = {"status": "error", "error": str(e)[:100]}

        # 3. RL state
        rl_file = Path.home() / ".eidos" / "rl_state.json"
        if rl_file.exists():
            try:
                shutil.copy2(rl_file, BACKUP_DIR / f"rl_{stamp}.json")
                results["rl"] = {"status": "ok"}
            except Exception as e:
                results["rl"] = {"status": "error", "error": str(e)[:100]}

        # 4. Rotar backups viejos
        self._rotate_backups()

        self._last_backup = now
        self._backup_count += 1
        self._set_state("last_backup", now)
        self._set_state("backup_count", self._backup_count)

        log.info("Auto-backup #%d: %s", self._backup_count,
                 {k: v.get("status") for k, v in results.items()})

        # Emitir evento
        try:
            from core.eidos_events import emit
            emit("health_changed", {"backup": self._backup_count, "stamp": stamp},
                 source="supervisor")
        except Exception:
            pass

        return {"backup_count": self._backup_count, "stamp": stamp, "results": results}

    def _rotate_backups(self):
        """Mantiene solo los últimos MAX_BACKUPS backups."""
        try:
            brain_backups = sorted(BACKUP_DIR.glob("brain_*.db.gz"))
            for f in brain_backups[:-MAX_BACKUPS]:
                f.unlink()
                log.debug("Backup rotado: %s", f.name)
            # También rotar json
            for pattern in ["affect_*.json", "rl_*.json"]:
                files = sorted(BACKUP_DIR.glob(pattern))
                for f in files[:-MAX_BACKUPS]:
                    f.unlink()
        except Exception as e:
            log.debug("rotate_backups: %s", e)

    # ── Restart Log ────────────────────────────────────────────────────────

    def log_restart(self, reason: str, component: str = "system"):
        """Registra un restart en el log."""
        try:
            conn = get_conn(STATE_DB, timeout=5)
            conn.execute(
                "INSERT INTO restart_log (timestamp, reason, component, success) VALUES (?,?,?,?)",
                (time.time(), reason[:200], component, 1))
            conn.commit()

        except Exception:
            pass
        log.warning("Restart registrado: %s — %s", component, reason)

    def get_restart_history(self, limit: int = 20) -> List[Dict[str, Any]]:
        try:
            conn = get_conn(STATE_DB, timeout=5)
            rows = conn.execute(
                "SELECT timestamp, reason, component, success FROM restart_log "
                "ORDER BY timestamp DESC LIMIT ?", (limit,)).fetchall()

            return [{"ts": r[0], "reason": r[1], "component": r[2], "success": bool(r[3])}
                    for r in rows]
        except Exception:
            return []

    # ── Stats ──────────────────────────────────────────────────────────────

    def stats(self) -> Dict[str, Any]:
        return {
            "pid": os.getpid(),
            "running": self._running,
            "repair_count": self._repair_count,
            "backup_count": self._backup_count,
            "last_backup_ago_s": round(time.time() - self._last_backup) if self._last_backup else None,
            "health_score": self._health_history[-1].get("_score", "?") if self._health_history else "?",
            "restart_history": len(self.get_restart_history()),
        }

    # ── Lifecycle ──────────────────────────────────────────────────────────

    def start(self):
        """Inicia el supervisor en background thread."""
        if self._running:
            return
        self._running = True
        self._thread = threading.Thread(target=self._loop, name="eidos_supervisor", daemon=True)
        self._thread.start()
        self._set_state("supervisor_running", True)
        log.info("Supervisor iniciado — monitoreo cada %ds", HEALTH_CHECK_INTERVAL)

        # Emitir evento
        try:
            from core.eidos_events import emit
            emit("system_startup", {"component": "supervisor"}, source="supervisor")
        except Exception:
            pass

    def stop(self):
        """Detiene el supervisor."""
        self._running = False
        self._remove_pid()
        self._set_state("supervisor_running", False)
        log.info("Supervisor detenido — %d repairs, %d backups",
                 self._repair_count, self._backup_count)

        try:
            from core.eidos_events import emit
            emit("system_shutdown", {"component": "supervisor"}, source="supervisor")
        except Exception:
            pass

    def _loop(self):
        """Loop de monitoreo 24/7."""
        log.info("Supervisor loop iniciado")
        # Primer health check inmediato
        health = self.health_check()
        log.info("Health check inicial: %s", health.get("_score", "?"))

        while self._running:
            try:
                time.sleep(HEALTH_CHECK_INTERVAL)

                # Health check
                health = self.health_check()
                score = health.get("_score", "?")
                # S82 fix: solo considerar unhealthy si status es explícitamente
                # "unhealthy" o "degraded", no si es "" o None (no verificado)
                unhealthy = [c for c, d in health.items()
                           if isinstance(d, dict) and d.get("status") in ("unhealthy", "degraded")]

                if unhealthy:
                    log.warning("Componentes unhealthy: %s", unhealthy)
                    self.repair()
                    # Re-check después de repair
                    time.sleep(10)
                    health = self.health_check()
                    log.info("Post-repair health: %s", health.get("_score", "?"))

                # Auto-backup cada BACKUP_INTERVAL
                if time.time() - self._last_backup > BACKUP_INTERVAL:
                    self.auto_backup()

                # Emitir health cada hora
                if int(time.time()) % 3600 < HEALTH_CHECK_INTERVAL:
                    try:
                        from core.eidos_events import emit
                        emit("health_changed", {"score": score, "unhealthy": unhealthy},
                             source="supervisor")
                    except Exception:
                        pass

            except Exception as e:
                log.error("Supervisor loop error: %s", e)
                time.sleep(60)


_supervisor: Optional[Supervisor] = None


def get_supervisor() -> Supervisor:
    global _supervisor
    if _supervisor is None:
        _supervisor = Supervisor()
    return _supervisor


if __name__ == "__main__":
    import argparse
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    p = argparse.ArgumentParser(description="EIDOS Supervisor")
    p.add_argument("--start", action="store_true", help="Iniciar supervisor")
    p.add_argument("--health", action="store_true", help="Health check")
    p.add_argument("--repair", action="store_true", help="Reparar componentes")
    p.add_argument("--backup", action="store_true", help="Backup manual")
    p.add_argument("--stats", action="store_true", help="Estadísticas")
    args = p.parse_args()

    sup = Supervisor()

    if args.start:
        sup.start()
        print(f"Supervisor iniciado (PID: {os.getpid()})")
        try:
            while True:
                time.sleep(1)
        except KeyboardInterrupt:
            sup.stop()
    elif args.health:
        result = sup.health_check()
        print(json.dumps(result, indent=2, ensure_ascii=False))
    elif args.repair:
        result = sup.repair()
        print(json.dumps(result, indent=2, ensure_ascii=False))
    elif args.backup:
        result = sup.auto_backup()
        print(json.dumps(result, indent=2, ensure_ascii=False))
    elif args.stats:
        print(json.dumps(sup.stats(), indent=2, ensure_ascii=False))
    else:
        p.print_help()
