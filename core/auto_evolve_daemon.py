"""
core/auto_evolve_daemon.py — Daemon de auto-evolución de EIDOS

Ejecuta ciclos de evolución en background cada N minutos:
1. Llama a knowledge_reasoner.evolve() → nuevos nodos inferidos
2. Sincroniza brain.db → ChromaDB
3. Ejecuta self_study para detectar gaps y estudiarlos
4. Auto-aprendizaje web (auto_learner) cada 3 ciclos
5. Auto-análisis de código (self_code_analyzer) cada 6 ciclos
6. Monitorea servicios caídos y los reinicia
7. Inyecta conocimiento proactivo sobre lo aprendido

HONESTY NOTE: This daemon is an ORCHESTRATOR, not the inference engine.
The actual knowledge inference happens in core/knowledge_reasoner.py
via evolve() — which uses the knowledge graph's spreading activation
(not substring matching on random nodes). The daemon schedules cycles,
but the intelligence is in the reasoner.

Uso:
    python3 core/auto_evolve_daemon.py           # standalone
    # o importar para integración:
    from core.auto_evolve_daemon import start_evolve_daemon
    start_evolve_daemon()
"""
from __future__ import annotations

import json
import logging
import os
import subprocess
import sys
import threading
import time
import traceback
from datetime import datetime
from pathlib import Path

log = logging.getLogger("eidos.evolve_daemon")

EIDOS_ROOT = Path.home() / "EIDOS"
EVOLVE_INTERVAL = 1800  # 30 minutos entre ciclos
HEALTH_INTERVAL = 300   # 5 minutos entre health checks
MAX_LOG_LINES = 100

# Servicios a monitorear: (nombre, pid_pattern, port)
SERVICES = [
    ("colony", "colony_dashboard.py", 7777),
    ("webpanel", "web-panel/server.py", 8080),
    ("bridge", "bridge_to_eidos.py", 8003),
    ("trinity", "trinity_server.py", 8001),
    ("vscode_api", "vscode_api_server.py", 8765),
]


class EvolveDaemon:
    def __init__(self):
        self._running = False
        self._thread = None
        self._cycle_count = 0
        self._last_evolve: float = 0
        self._last_health: float = 0
        self._stats: dict = {
            "cycles": 0,
            "inferences_total": 0,
            "services_restarted": 0,
            "errors": [],
        }
        # Prevenir import circular
        self._reasoner = None

    def _get_reasoner(self):
        if self._reasoner is None:
            from core.knowledge_reasoner import get_reasoner
            self._reasoner = get_reasoner()
        return self._reasoner

    def start(self):
        if self._running:
            return
        self._running = True
        self._thread = threading.Thread(target=self._loop, daemon=True,
                                        name="evolve-daemon")
        self._thread.start()
        log.info("Auto-evolución daemon iniciado (cada %ds)", EVOLVE_INTERVAL)

    def stop(self):
        self._running = False

    def _loop(self):
        while self._running:
            now = time.time()
            try:
                # Health check cada 5 min
                if now - self._last_health >= HEALTH_INTERVAL:
                    self._health_check()
                    self._last_health = now

                # Ciclo de evolución cada 30 min
                if now - self._last_evolve >= EVOLVE_INTERVAL:
                    self._evolve_cycle()
                    self._last_evolve = now

            except Exception as e:
                log.error("Evolve loop error: %s", traceback.format_exc())
                self._stats["errors"].append(str(e)[:100])

            time.sleep(60)  # check cada minuto

    def _evolve_cycle(self):
        """Ciclo completo de evolución."""
        t0 = time.time()
        self._cycle_count += 1
        log.info("=== Ciclo de evolución #%d ===", self._cycle_count)

        # 1. Evolución del conocimiento
        try:
            r = self._get_reasoner()
            evolve_result = r.evolve()
            inferred = evolve_result.get("persisted", 0)
            self._stats["inferences_total"] += inferred
            log.info("Evolve: %d nuevas inferencias persistidas", inferred)
        except Exception as e:
            log.error("Evolve error: %s", e)
            inferred = 0

        # 2. Auto-estudio: detectar gaps y estudiar
        try:
            from core.self_study import get_self_study
            ss = get_self_study()
            study_result = ss.run_cycle(max_topics=2)
            if study_result.get("studied", 0) > 0:
                log.info("Self-study: %d temas estudiados", study_result["studied"])
        except Exception as e:
            log.error("Self-study error: %s", e)

        # 3. Sincronizar ChromaDB
        try:
            from core.colony_chroma import get_chroma_memory
            chroma = get_chroma_memory()
            if not chroma.is_ready():
                # S94: no resetear si hay sync en progreso (evita bucle de resets)
                if getattr(chroma, '_syncing', False):
                    log.debug("ChromaDB sync en progreso, esperando...")
                else:
                    log.info("ChromaDB no listo, forzando re-sync...")
                    import core.colony_chroma as _cm
                    _cm._instance = None
                    time.sleep(2)
                    chroma = get_chroma_memory()
                    time.sleep(5)
            chroma_count = chroma.count()
            log.info("ChromaDB: %d vectores", chroma_count)
        except Exception as e:
            log.error("Chroma sync error: %s", e)

        # 4. Aprendizaje autónomo web (cada 3 ciclos)
        if self._cycle_count % 3 == 0:
            try:
                from core.auto_learner import get_auto_learner
                al = get_auto_learner()
                # Aprender sobre temas de los gaps de conocimiento
                try:
                    import urllib.request as U, json as J
                    gaps = J.loads(U.urlopen("http://localhost:8003/self_study/gaps", timeout=5).read())
                    for gap in gaps.get("gaps", [])[:2]:
                        result = al.learn_topic(gap, depth=1, max_pages=2)
                        log.info("Auto-learn '%s': %d nodes", gap, result.get("nodes_injected", 0))
                except Exception as e:
                    log.error("Auto-learn from gaps error: %s", e)
            except Exception as e:
                log.error("Auto-learner error: %s", e)

        # 5. Auto-análisis de código (cada 6 ciclos)
        if self._cycle_count % 6 == 0:
            try:
                from core.self_code_analyzer import get_code_analyzer
                ca = get_code_analyzer()
                result = ca.analyze_all(force=False)
                log.info("Code analysis: %d nodes injected", result.get("nodes_injected", 0))
            except Exception as e:
                log.error("Code analyzer error: %s", e)

        # 6. Inyectar conocimiento proactivo sobre evolución
        if inferred > 0:
            try:
                from core.colony_proactive import push_message
                push_message(
                    actor="AI-Assistant",
                    message=(
                        f"Ciclo de evolución #{self._cycle_count}: generé {inferred} "
                        f"nuevos nodos de conocimiento por inferencia semántica. "
                        f"Total inferido: {self._stats['inferences_total']} nodos."
                    ),
                    topic=f"evolution_cycle:{self._cycle_count}",
                    priority=6,
                )
            except Exception as e:
                log.error("Proactive push error: %s", e)

        elapsed = round(time.time() - t0, 1)
        self._stats["cycles"] = self._cycle_count
        log.info("Ciclo #%d completado en %ss", self._cycle_count, elapsed)

    def _health_check(self):
        """Verifica servicios y reinicia los caídos."""
        for name, pattern, port in SERVICES:
            try:
                # Verificar por puerto
                import socket
                s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
                s.settimeout(2)
                result = s.connect_ex(('127.0.0.1', port))
                s.close()
                if result == 0:
                    continue  # servicio OK
                # Puerto cerrado — intentar reiniciar
                log.warning("Servicio %s caído (puerto %d), reiniciando...", name, port)
                self._restart_service(name, pattern)
                self._stats["services_restarted"] += 1
            except Exception as e:
                log.error("Health check %s error: %s", name, e)

    def _restart_service(self, name: str, pattern: str):
        """Reinicia un servicio caído."""
        script_map = {
            "colony": "core/colony_dashboard.py",
            "webpanel": "web-panel/server.py",
            "bridge": "core/bridge_to_eidos.py",
            "trinity": "core/trinity_server.py",
            "vscode_api": "core/vscode_api_server.py",
        }
        script = script_map.get(name)
        if not script:
            return
        try:
            subprocess.Popen(
                ["python3", script],
                cwd=str(EIDOS_ROOT),
                stdout=open(os.devnull, 'w'),
                stderr=subprocess.STDOUT,
            )
            log.info("Reiniciado %s → %s", name, script)
            # Inyectar notificación
            try:
                from core.colony_proactive import push_message
                push_message(
                    actor="AI-Assistant",
                    message=f"Servicio {name} reiniciado automáticamente tras detectar caída.",
                    topic=f"service_restart:{name}",
                    priority=7,
                )
            except Exception:
                pass
        except Exception as e:
            log.error("Error reiniciando %s: %s", name, e)

    def get_status(self) -> dict:
        return {
            "running": self._running,
            "cycle_count": self._cycle_count,
            "last_evolve": self._last_evolve,
            "last_health": self._last_health,
            "stats": self._stats,
        }


# ── Singleton ──
_instance = None
_lock = threading.Lock()


def get_evolve_daemon() -> EvolveDaemon:
    global _instance
    if _instance is None:
        with _lock:
            if _instance is None:
                _instance = EvolveDaemon()
    return _instance


def start_evolve_daemon():
    d = get_evolve_daemon()
    d.start()
    return d


if __name__ == "__main__":
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [evolve] %(levelname)s %(message)s",
    )
    d = start_evolve_daemon()
    log.info("Auto-evolución daemon activo. Ctrl+C para parar.")
    try:
        while True:
            time.sleep(60)
            s = d.get_status()
            print(f"[{datetime.now().strftime('%H:%M:%S')}] "
                  f"Ciclos: {s['cycle_count']} | "
                  f"Inferencias: {s['stats']['inferences_total']} | "
                  f"Reinicios: {s['stats']['services_restarted']}")
    except KeyboardInterrupt:
        print("\nParando...")
        d.stop()
