#!/usr/bin/env python3
"""
core/loop_governor.py -- Loop Governor for EIDOS orchestrator
==============================================================

Coordina sub-ciclos del orchestrator con timeout, backpressure y health checks.

Proporciona:
  - LoopGovernor: registro de loops con métricas (timeout, last_ok, skips, avg_ms).
  - run_guarded(name, fn, timeout_s): ejecuta fn con timeout via threading.Timer.
  - should_skip(name, interval_s): backpressure -- salta si ya corrió hace muy poco.
  - health_snapshot(): dict con estado de todos los loops registrados.

Singleton accesible via get_loop_governor().

Integracion:
    from core.loop_governor import get_loop_governor

    gov = get_loop_governor()
    gov.run_guarded("enrich_graph", re.enrich_graph, timeout_s=120)
"""

from __future__ import annotations

import logging
import threading
import time
from typing import Any, Callable, Dict, Optional, Set, Tuple

log = logging.getLogger("eidos.loop_governor")


# ── Loop state container ──────────────────────────────────────────────────────

class LoopState:
    """Estado mantenido para un solo loop registrado."""

    __slots__ = ("name", "timeout_s", "last_ok", "last_error",
                 "skips", "total_runs", "total_timeouts",
                 "avg_ms", "min_ms", "max_ms", "last_error_msg")

    def __init__(self, name: str, timeout_s: float):
        self.name = name
        self.timeout_s = timeout_s
        self.last_ok: float = 0.0          # timestamp of last successful run
        self.last_error: float = 0.0       # timestamp of last error / timeout
        self.skips: int = 0                # number of times should_skip returned True
        self.total_runs: int = 0           # total successful completions
        self.total_timeouts: int = 0       # total timeout expirations
        self.avg_ms: float = 0.0           # rolling average execution time (ms)
        self.min_ms: float = float("inf")
        self.max_ms: float = 0.0
        self.last_error_msg: str = ""

    def record_ok(self, elapsed_ms: float):
        """Actualiza métricas tras ejecucion exitosa."""
        now = time.time()
        self.last_ok = now
        self.total_runs += 1
        # Rolling average
        if self.avg_ms == 0.0:
            self.avg_ms = elapsed_ms
        else:
            self.avg_ms = 0.9 * self.avg_ms + 0.1 * elapsed_ms
        if elapsed_ms < self.min_ms:
            self.min_ms = elapsed_ms
        if elapsed_ms > self.max_ms:
            self.max_ms = elapsed_ms

    def record_error(self, msg: str = ""):
        """Actualiza métricas tras error o timeout."""
        self.last_error = time.time()
        self.total_timeouts += 1
        self.last_error_msg = msg

    def record_skip(self):
        """Incrementa contador de skips por backpressure."""
        self.skips += 1

    def snapshot(self) -> Dict[str, Any]:
        """Devuelve dict con el estado completo del loop."""
        return {
            "name": self.name,
            "timeout_s": self.timeout_s,
            "last_ok": self.last_ok,
            "last_error": self.last_error,
            "skips": self.skips,
            "total_runs": self.total_runs,
            "total_timeouts": self.total_timeouts,
            "avg_ms": round(self.avg_ms, 2),
            "min_ms": round(self.min_ms, 2) if self.min_ms != float("inf") else None,
            "max_ms": round(self.max_ms, 2) if self.max_ms > 0 else None,
            "last_error_msg": self.last_error_msg[-200:] if self.last_error_msg else "",
        }


# ── LoopGovernor ──────────────────────────────────────────────────────────────

class LoopGovernor:
    """Gobernador de sub-ciclos del orchestrator.

    Registra loops con nombre, timeout y opcional interval de backpressure.
    Proporciona ejecucion protegida con timeout via threading.Timer y
    metodos de inspeccion (should_skip, health_snapshot).

    Thread-safe: usa un Lock interno para el registro y las metricas.
    """

    def __init__(self):
        self._lock = threading.Lock()
        self._loops: Dict[str, LoopState] = {}
        self._started_at: float = time.time()

    # ── Registration ───────────────────────────────────────────────────────

    def register(self, name: str, timeout_s: float = 300.0):
        """Registra un nuevo loop (idempotente: no re-registra si ya existe)."""
        with self._lock:
            if name not in self._loops:
                self._loops[name] = LoopState(name=name, timeout_s=timeout_s)
                log.debug("LoopGovernor: registrado loop '%s' (timeout=%.0fs)", name, timeout_s)

    def register_many(self, loops: Dict[str, float]):
        """Registra multiples loops: {name: timeout_s, ...}."""
        for name, timeout_s in loops.items():
            self.register(name, timeout_s)

    # ── Guarded execution ──────────────────────────────────────────────────

    def run_guarded(self, name: str, fn: Callable[[], Any],
                    timeout_s: float = 300.0) -> bool:
        """Ejecuta fn con timeout via threading.Timer.

        Args:
            name: Nombre del loop (se registra automaticamente si no existe).
            fn: Callable sin argumentos a ejecutar.
            timeout_s: Timeout en segundos (default 300).

        Returns:
            True si la ejecucion fue completada sin timeout, False si hubo timeout.
        """
        # Auto-register if unknown
        if name not in self._loops:
            self.register(name, timeout_s)

        state = self._loops[name]
        result_container: Dict[str, Any] = {"value": None, "done": False, "exc": None}
        timer_triggered = threading.Event()

        def worker():
            try:
                result_container["value"] = fn()
                result_container["done"] = True
            except Exception as exc:
                result_container["exc"] = exc
                result_container["done"] = True

        t0 = time.time()
        thread = threading.Thread(target=worker, daemon=True)
        thread.start()

        # Timeout timer
        timer = threading.Timer(timeout_s, timer_triggered.set)
        timer.start()

        # Wait with polling to detect timer expiry
        poll_interval = 0.05  # 50ms
        while thread.is_alive():
            if timer_triggered.is_set():
                # Timeout — the thread is abandoned (daemon), timer already fired
                elapsed_ms = (time.time() - t0) * 1000
                msg = f"TIMEOUT after {elapsed_ms:.0f}ms (limit={timeout_s}s)"
                log.warning("LoopGovernor [%s]: %s", name, msg)
                with self._lock:
                    state.record_error(msg)
                timer.cancel()
                return False
            time.sleep(poll_interval)

        timer.cancel()
        elapsed_ms = (time.time() - t0) * 1000
        thread.join(timeout=1)

        if result_container["exc"] is not None:
            msg = f"Exception: {result_container['exc']}"
            log.error("LoopGovernor [%s]: %s", name, msg)
            with self._lock:
                state.record_error(msg)
            return False

        # Success
        with self._lock:
            state.record_ok(elapsed_ms)
        log.debug("LoopGovernor [%s]: OK (%.0fms)", name, elapsed_ms)
        return True

    # ── Backpressure ───────────────────────────────────────────────────────

    def should_skip(self, name: str, interval_s: float) -> bool:
        """Backpressure: True si el loop ya corrio hace menos de interval_s.

        Args:
            name: Nombre del loop.
            interval_s: Intervalo minimo entre ejecuciones en segundos.

        Returns:
            True si se debe saltar (last_ok fue hace menos de interval_s).
        """
        state = self._loops.get(name)
        if state is None:
            return False  # never ran -> don't skip
        if state.last_ok == 0.0:
            return False  # never succeeded -> don't skip
        elapsed = time.time() - state.last_ok
        if elapsed < interval_s:
            with self._lock:
                state.record_skip()
            log.debug("LoopGovernor [%s]: skip (last ok %.0fs ago < interval %.0fs)",
                      name, elapsed, interval_s)
            return True
        return False

    def time_since_last_ok(self, name: str) -> Optional[float]:
        """Segundos desde la ultima ejecucion exitosa, o None si nunca corrio."""
        state = self._loops.get(name)
        if state is None or state.last_ok == 0.0:
            return None
        return time.time() - state.last_ok

    def time_since_last_error(self, name: str) -> Optional[float]:
        """Segundos desde el ultimo error, o None si nunca erro."""
        state = self._loops.get(name)
        if state is None or state.last_error == 0.0:
            return None
        return time.time() - state.last_error

    # ── Health ─────────────────────────────────────────────────────────────

    def health_snapshot(self) -> Dict[str, Any]:
        """Devuelve dict con el estado de salud de todos los loops.

        Returns:
            {
                "governor": {"uptime_s": ..., "loops_registered": N},
                "loops": {"<name>": {...estado...}, ...},
                "summary": {"ok": N, "errored": N, "stale": N, ...},
            }
        """
        with self._lock:
            loop_snapshots = {
                name: state.snapshot()
                for name, state in sorted(self._loops.items())
            }

        now = time.time()
        stale_threshold = 600.0  # 10 min sin correr = stale
        ok_count = 0
        errored_count = 0
        stale_count = 0
        never_run_count = 0

        for snap in loop_snapshots.values():
            if snap["total_runs"] == 0:
                never_run_count += 1
            elif snap["last_error"] > snap["last_ok"]:
                errored_count += 1
            elif (now - snap["last_ok"]) > stale_threshold:
                stale_count += 1
            else:
                ok_count += 1

        return {
            "governor": {
                "uptime_s": round(now - self._started_at, 0),
                "loops_registered": len(self._loops),
            },
            "loops": loop_snapshots,
            "summary": {
                "ok": ok_count,
                "errored": errored_count,
                "stale": stale_count,
                "never_run": never_run_count,
                "total": len(self._loops),
            },
        }

    def summary(self) -> str:
        """Resumen legible de una linea."""
        snap = self.health_snapshot()
        s = snap["summary"]
        return (f"LoopGovernor: {s['total']} loops | "
                f"ok={s['ok']} errored={s['errored']} "
                f"stale={s['stale']} never={s['never_run']}")


# ── Singleton ─────────────────────────────────────────────────────────────────

_governor: Optional[LoopGovernor] = None
_governor_lock = threading.Lock()


def get_loop_governor() -> LoopGovernor:
    """Devuelve la instancia singleton de LoopGovernor."""
    global _governor
    if _governor is None:
        with _governor_lock:
            if _governor is None:
                _governor = LoopGovernor()
    return _governor


# ── Quick CLI test ────────────────────────────────────────────────────────────

if __name__ == "__main__":
    import sys
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")

    gov = get_loop_governor()

    # Test 1: successful run
    print("\n--- Test 1: run_guarded (fast, success) ---")
    ok = gov.run_guarded("fast_loop", lambda: time.sleep(0.1), timeout_s=2)
    print(f"  result={ok}")

    # Test 2: timeout
    print("\n--- Test 2: run_guarded (slow, timeout) ---")
    ok = gov.run_guarded("slow_loop", lambda: time.sleep(3), timeout_s=0.5)
    print(f"  result={ok}")

    # Test 3: should_skip
    print("\n--- Test 3: should_skip ---")
    print(f"  skip(fast_loop, 60s) = {gov.should_skip('fast_loop', 60)} (True=skip)")
    print(f"  skip(slow_loop, 60s) = {gov.should_skip('slow_loop', 60)} (False=no skip, never ok)")

    # Test 4: health snapshot
    print("\n--- Test 4: health_snapshot ---")
    snap = gov.health_snapshot()
    print(f"  {gov.summary()}")
    for name, info in snap["loops"].items():
        print(f"    {name}: runs={info['total_runs']} timeouts={info['total_timeouts']} "
              f"avg={info['avg_ms']}ms skips={info['skips']}")

    print("\nLoopGovernor CLI test complete.")
