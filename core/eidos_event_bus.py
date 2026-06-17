"""
core/eidos_event_bus.py — EIDOS Event Bus (sistema nervioso)
=============================================================

Pub/sub ligero con SQLite como log de eventos append-only.
Todos los modulos se comunican a traves del bus sin conocerse entre si.

Event types y sus productores/consumidores:

    perception  → publish   'screenshot_taken', 'window_changed', 'error_detected'
    bridge      → publish   'user_spoke', 'goal_set'
    reasoner    → subscribe 'screenshot_taken'  → publish 'decision_made'
    action      → subscribe 'decision_made'     → publish 'action_executed'
    healer      → subscribe 'service_crashed'   → publish 'repair_attempted'
    presence    → subscribe '*' (ALL)           → actualiza presence.json

GlobalSelfState (~/.eidos/self_state.json):
    Actualizado en cada evento. Cualquier componente lo lee en O(1) sin polling.
    Contiene: cycle_count, mood, attention_focus, active_goals, health_status.

Integracion con AliveOrchestrator:
    Los eventos se disparan automaticamente desde cycle() del orchestrator.

Uso:
    from core.eidos_event_bus import get_event_bus, publish, subscribe, get_self_state

    bus = get_event_bus()
    bus.publish("screenshot_taken", {"active_window": "Firefox"})
    bus.subscribe(["screenshot_taken"], my_handler)

    # Leer estado global sin polling:
    state = get_self_state()  # dict, O(1) desde archivo JSON
"""

from __future__ import annotations

import json
import logging
import os
import sys
import threading
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Set, Union

# Ejecutable standalone: añade el root de EIDOS al path
_EIDOS_ROOT = Path(__file__).resolve().parent.parent
if str(_EIDOS_ROOT) not in sys.path:
    sys.path.insert(0, str(_EIDOS_ROOT))

from core.db import get_conn

log = logging.getLogger("eidos.event_bus")

# ── Paths ──────────────────────────────────────────────────────────────────
EIDOS_DIR = Path.home() / ".eidos"
BRAIN_DB = EIDOS_DIR / "evolution_brain.db"
SELF_STATE_PATH = EIDOS_DIR / "self_state.json"
MAX_EVENT_LOG = 20000  # keep more history than the old 10000

# ── Valid event types ──────────────────────────────────────────────────────
VALID_EVENT_TYPES: Set[str] = {
    "screenshot_taken",
    "window_changed",
    "error_detected",
    "user_spoke",
    "goal_set",
    "knowledge_added",
    "mood_changed",
    "action_executed",
    "service_crashed",
    # Extended types from the mapping
    "decision_made",
    "repair_attempted",
    # System lifecycle
    "orchestrator_started",
    "orchestrator_stopped",
    "cycle_completed",
    # Fallback: wildcard for custom types (logged but not validated strictly)
}

# ── Event type descriptions ────────────────────────────────────────────────
EVENT_DESCRIPTIONS: Dict[str, str] = {
    "screenshot_taken": "Perception capturo la pantalla (OCR + layout)",
    "window_changed": "La ventana activa cambio",
    "error_detected": "Se detecto un error en pantalla o en el sistema",
    "user_spoke": "SER envio un mensaje a traves del bridge",
    "goal_set": "Se establecio una nueva meta (desde bridge o interna)",
    "knowledge_added": "Se aprendio/aporto conocimiento nuevo al grafo",
    "mood_changed": "El estado VAD (valence/arousal/dominance) cambio significativamente",
    "action_executed": "Se ejecuto una accion autonoma (o dry-run)",
    "service_crashed": "Un servicio monitorizado dejo de responder",
    "decision_made": "El reasoner tomo una decision",
    "repair_attempted": "El healer intento reparar un servicio caido",
    "orchestrator_started": "AliveOrchestrator inicio",
    "orchestrator_stopped": "AliveOrchestrator se detuvo",
    "cycle_completed": "Un ciclo vital completo",
}

# ── Producer → Consumer mapping (documentation & wiring guide) ─────────────
PRODUCER_CONSUMER_MAP: Dict[str, Dict[str, List[str]]] = {
    "perception": {
        "publishes": ["screenshot_taken", "window_changed", "error_detected"],
        "subscribes": [],
    },
    "bridge": {
        "publishes": ["user_spoke", "goal_set"],
        "subscribes": [],
    },
    "reasoner": {
        "publishes": ["decision_made"],
        "subscribes": ["screenshot_taken", "window_changed"],
    },
    "action": {
        "publishes": ["action_executed"],
        "subscribes": ["decision_made"],
    },
    "healer": {
        "publishes": ["repair_attempted"],
        "subscribes": ["service_crashed"],
    },
    "presence": {
        "publishes": [],
        "subscribes": ["*"],  # wildcard: recibe todos
    },
    "brain": {
        "publishes": ["knowledge_added"],
        "subscribes": ["screenshot_taken", "window_changed", "knowledge_added"],
    },
    "affect": {
        "publishes": ["mood_changed"],
        "subscribes": ["user_spoke", "action_executed", "error_detected"],
    },
}


# ── Dataclasses ────────────────────────────────────────────────────────────

@dataclass
class Event:
    """Un evento en el bus."""
    id: str
    type: str
    timestamp: float
    data: Dict[str, Any] = field(default_factory=dict)
    source: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return {
            "id": self.id,
            "type": self.type,
            "ts": self.timestamp,
            "data": self.data,
            "source": self.source,
        }


# Callback signature: (Event) -> None
EventHandler = Callable[[Event], None]


@dataclass
class SelfState:
    """Estado global de EIDOS, persistido a self_state.json en cada evento."""
    timestamp: float = 0.0
    cycle_count: int = 0
    mood: str = "consciente"
    attention_focus: str = ""
    active_goals: List[str] = field(default_factory=list)
    health_status: str = "healthy"
    last_event_type: str = ""
    uptime_seconds: float = 0.0
    total_events: int = 0
    services: Dict[str, str] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "timestamp": self.timestamp,
            "ts_human": time.strftime(
                "%Y-%m-%d %H:%M:%S", time.localtime(self.timestamp)
            ) if self.timestamp else "",
            "cycle_count": self.cycle_count,
            "mood": self.mood,
            "attention_focus": self.attention_focus,
            "active_goals": self.active_goals,
            "health_status": self.health_status,
            "last_event_type": self.last_event_type,
            "uptime_seconds": round(self.uptime_seconds, 1),
            "total_events": self.total_events,
            "services": self.services,
        }


# ── EventBus ───────────────────────────────────────────────────────────────

class EventBus:
    """Sistema nervioso de EIDOS: pub/sub con SQLite append-only log.

    Thread-safe. Singleton via get_event_bus().
    Los eventos se persisten en event_log (append-only) y se notifican
    a los suscriptores in-process inmediatamente.
    """

    def __init__(self):
        self._lock = threading.Lock()
        self._subscribers: Dict[str, List[EventHandler]] = {}
        self._publish_count = 0
        self._start_time = time.time()
        self._self_state = SelfState(timestamp=time.time())
        self._init_db()
        self._load_self_state()
        EIDOS_DIR.mkdir(parents=True, exist_ok=True)
        log.info(
            "EventBus inicializado: %d tipos de eventos validados, "
            "SQLite append-only en %s",
            len(VALID_EVENT_TYPES),
            BRAIN_DB,
        )

    # ── Database ──────────────────────────────────────────────────────────

    def _init_db(self) -> None:
        """Crea tabla event_log si no existe. Reusa schema compatible con
        el event bus anterior (eidos_events.py) para no duplicar datos."""
        try:
            conn = get_conn(BRAIN_DB, timeout=10)
            conn.execute("PRAGMA busy_timeout=10000")
            conn.execute(
                """CREATE TABLE IF NOT EXISTS event_log (
                    id TEXT PRIMARY KEY,
                    type TEXT NOT NULL,
                    timestamp REAL NOT NULL,
                    data TEXT DEFAULT '{}',
                    source TEXT DEFAULT ''
                )"""
            )
            conn.execute(
                """CREATE INDEX IF NOT EXISTS idx_event_log_type_ts
                   ON event_log(type, timestamp DESC)"""
            )
            conn.execute(
                """CREATE INDEX IF NOT EXISTS idx_event_log_ts
                   ON event_log(timestamp DESC)"""
            )
            conn.commit()
        except Exception as e:
            log.warning("EventBus DB init: %s", e)

    # ── Publish ───────────────────────────────────────────────────────────

    def publish(
        self,
        event_type: str,
        data: Optional[Dict[str, Any]] = None,
        source: str = "",
    ) -> Event:
        """Publica un evento. Lo persiste en SQLite y notifica a todos los
        suscriptores registrados para ese tipo (incluyendo wildcard '*').

        Args:
            event_type: Tipo de evento (debe estar en VALID_EVENT_TYPES).
            data: Diccionario con datos del evento.
            source: Modulo que origino el evento (ej. 'perception', 'bridge').

        Returns:
            El objeto Event creado.
        """
        if data is None:
            data = {}

        if event_type not in VALID_EVENT_TYPES and not event_type.startswith("_"):
            log.debug(
                "EventBus: tipo '%s' no esta en VALID_EVENT_TYPES — "
                "se publica igual (custom event)",
                event_type,
            )

        event = Event(
            id=uuid.uuid4().hex[:16],
            type=event_type,
            timestamp=time.time(),
            data=data,
            source=source or "unknown",
        )

        # Persistir a SQLite (append-only)
        self._persist(event)

        # Actualizar estado global
        self._update_self_state(event)

        # Notificar suscriptores in-process (bajo lock)
        with self._lock:
            self._publish_count += 1

            # Obtener listas de handlers (snapshot copia local para evitar
            # deadlocks si un handler subscribe/unsubscribe)
            specific = list(self._subscribers.get(event_type, []))
            wildcard = list(self._subscribers.get("*", []))

        # Ejecutar handlers fuera del lock
        for handler in specific:
            try:
                handler(event)
            except Exception:
                log.exception(
                    "EventBus: handler fallo para evento %s", event_type
                )

        for handler in wildcard:
            try:
                handler(event)
            except Exception:
                log.exception(
                    "EventBus: wildcard handler fallo para evento %s", event_type
                )

        # Rotar log si es necesario
        if self._publish_count % 200 == 0:
            self._rotate_if_needed()

        return event

    def _persist(self, event: Event) -> None:
        """Persiste un evento en SQLite (append-only, nunca se borra excepto
        en rotacion)."""
        try:
            conn = get_conn(BRAIN_DB, timeout=10)
            conn.execute("PRAGMA busy_timeout=10000")
            conn.execute(
                "INSERT INTO event_log (id, type, timestamp, data, source) "
                "VALUES (?,?,?,?,?)",
                (
                    event.id,
                    event.type,
                    event.timestamp,
                    json.dumps(event.data, ensure_ascii=False),
                    event.source,
                ),
            )
            conn.commit()
        except Exception as e:
            log.debug("EventBus _persist: %s", e)

    def _rotate_if_needed(self) -> None:
        """Elimina los eventos mas antiguos si el log excede MAX_EVENT_LOG,
        manteniendo los mas recientes."""
        try:
            conn = get_conn(BRAIN_DB, timeout=10)
            count = conn.execute(
                "SELECT COUNT(*) FROM event_log"
            ).fetchone()[0]
            if count > MAX_EVENT_LOG:
                cutoff = conn.execute(
                    "SELECT timestamp FROM event_log "
                    "ORDER BY timestamp DESC LIMIT 1 OFFSET ?",
                    (int(MAX_EVENT_LOG * 0.8),),
                ).fetchone()
                if cutoff:
                    deleted = conn.execute(
                        "DELETE FROM event_log WHERE timestamp < ?",
                        (cutoff[0],),
                    ).rowcount
                    conn.commit()
                    log.info("EventBus: rotados %d eventos antiguos", deleted)
        except Exception as e:
            log.debug("EventBus rotate: %s", e)

    # ── Subscribe / Unsubscribe ───────────────────────────────────────────

    def subscribe(
        self,
        event_types: Union[str, List[str]],
        handler: EventHandler,
    ) -> None:
        """Registra un callback para uno o varios tipos de evento.

        Usa '*' como wildcard para recibir todos los eventos.

        Args:
            event_types: Un string o lista de strings con tipos de evento.
            handler: Callback que recibe un Event.
        """
        if isinstance(event_types, str):
            event_types = [event_types]

        with self._lock:
            for et in event_types:
                if et not in self._subscribers:
                    self._subscribers[et] = []
                self._subscribers[et].append(handler)
                log.debug(
                    "EventBus: suscriptor registrado para '%s' (total: %d)",
                    et,
                    len(self._subscribers[et]),
                )

    def unsubscribe(
        self,
        event_types: Union[str, List[str]],
        handler: EventHandler,
    ) -> None:
        """Elimina un callback de uno o varios tipos de evento."""
        if isinstance(event_types, str):
            event_types = [event_types]

        with self._lock:
            for et in event_types:
                if et in self._subscribers:
                    try:
                        self._subscribers[et].remove(handler)
                        log.debug(
                            "EventBus: suscriptor removido de '%s'", et
                        )
                    except ValueError:
                        pass

    # ── Query ─────────────────────────────────────────────────────────────

    def recent(
        self,
        limit: int = 50,
        event_type: Optional[str] = None,
    ) -> List[Event]:
        """Recupera los eventos mas recientes del log.

        Args:
            limit: Maximo numero de eventos a devolver.
            event_type: Filtrar por tipo (None = todos).
        """
        try:
            conn = get_conn(BRAIN_DB, timeout=5)
            if event_type:
                rows = conn.execute(
                    "SELECT id, type, timestamp, data, source FROM event_log "
                    "WHERE type=? ORDER BY timestamp DESC LIMIT ?",
                    (event_type, limit),
                ).fetchall()
            else:
                rows = conn.execute(
                    "SELECT id, type, timestamp, data, source FROM event_log "
                    "ORDER BY timestamp DESC LIMIT ?",
                    (limit,),
                ).fetchall()

            events = []
            for row in rows:
                try:
                    data = json.loads(row[3]) if row[3] else {}
                except Exception:
                    data = {}
                events.append(
                    Event(
                        id=row[0],
                        type=row[1],
                        timestamp=row[2],
                        data=data,
                        source=row[4] or "",
                    )
                )
            return events
        except Exception as e:
            log.debug("EventBus recent: %s", e)
            return []

    def stats(self) -> Dict[str, Any]:
        """Estadisticas del bus: total eventos, por tipo, suscriptores."""
        try:
            conn = get_conn(BRAIN_DB, timeout=5)
            total = conn.execute(
                "SELECT COUNT(*) FROM event_log"
            ).fetchone()[0]
            by_type = {}
            for row in conn.execute(
                "SELECT type, COUNT(*) FROM event_log "
                "GROUP BY type ORDER BY COUNT(*) DESC LIMIT 20"
            ):
                by_type[row[0]] = row[1]

            with self._lock:
                sub_counts = {
                    k: len(v) for k, v in self._subscribers.items()
                }

            return {
                "total_events": total,
                "in_memory_published": self._publish_count,
                "by_type": by_type,
                "subscribers": sub_counts,
                "uptime_seconds": round(time.time() - self._start_time, 1),
            }
        except Exception:
            return {
                "total_events": 0,
                "in_memory_published": self._publish_count,
                "by_type": {},
                "subscribers": {},
            }

    # ── GlobalSelfState ───────────────────────────────────────────────────

    def _update_self_state(self, event: Event) -> None:
        """Actualiza el estado global basado en el evento recibido.
        Escribe self_state.json atomicamente para lectura O(1) sin polling."""
        state = self._self_state
        state.timestamp = event.timestamp
        state.last_event_type = event.type
        state.total_events = self._publish_count
        state.uptime_seconds = time.time() - self._start_time

        data = event.data

        # Actualizar campos especificos segun tipo de evento
        if event.type == "screenshot_taken":
            state.attention_focus = data.get("active_window", state.attention_focus)
            state.cycle_count = data.get("cycle", state.cycle_count)

        elif event.type == "window_changed":
            new_win = data.get("new_window", "")
            if new_win:
                state.attention_focus = new_win
            state.cycle_count = data.get("cycle", state.cycle_count)

        elif event.type == "mood_changed":
            new_mood = data.get("mood", "")
            if new_mood:
                state.mood = new_mood

        elif event.type == "goal_set":
            goal = data.get("goal", "")
            if goal and goal not in state.active_goals:
                state.active_goals.append(goal)
                # Mantener solo los ultimos 10
                state.active_goals = state.active_goals[-10:]

        elif event.type == "service_crashed":
            service_name = data.get("service", "unknown")
            state.services[service_name] = "crashed"
            state.health_status = "degraded"

        elif event.type == "repair_attempted":
            service_name = data.get("service", "unknown")
            success = data.get("success", False)
            state.services[service_name] = "healthy" if success else "degraded"
            # Recalcular health_status global
            all_statuses = set(state.services.values())
            if not all_statuses or all_statuses == {"healthy"}:
                state.health_status = "healthy"
            elif "crashed" in all_statuses:
                state.health_status = "critical"
            else:
                state.health_status = "degraded"

        elif event.type == "orchestrator_started":
            state.health_status = "healthy"
            state.cycle_count = 0

        elif event.type == "cycle_completed":
            state.cycle_count = data.get("cycle", state.cycle_count)

        elif event.type == "error_detected":
            # No cambia health_status global, solo registra
            pass

        # Escribir a disco atomicamente
        self._write_self_state(state)

    def _write_self_state(self, state: SelfState) -> None:
        """Escritura atomica: temp file + rename."""
        try:
            tmp_path = SELF_STATE_PATH.with_suffix(".tmp")
            with open(tmp_path, "w", encoding="utf-8") as f:
                json.dump(state.to_dict(), f, ensure_ascii=False, indent=2)
            os.replace(tmp_path, SELF_STATE_PATH)
        except Exception as e:
            log.debug("EventBus _write_self_state: %s", e)

    def _load_self_state(self) -> None:
        """Carga el estado global desde disco si existe."""
        try:
            if SELF_STATE_PATH.exists():
                with open(SELF_STATE_PATH, "r", encoding="utf-8") as f:
                    raw = json.load(f)
                self._self_state = SelfState(
                    timestamp=raw.get("timestamp", 0.0),
                    cycle_count=raw.get("cycle_count", 0),
                    mood=raw.get("mood", "consciente"),
                    attention_focus=raw.get("attention_focus", ""),
                    active_goals=raw.get("active_goals", []),
                    health_status=raw.get("health_status", "healthy"),
                    last_event_type=raw.get("last_event_type", ""),
                    uptime_seconds=raw.get("uptime_seconds", 0.0),
                    total_events=raw.get("total_events", 0),
                    services=raw.get("services", {}),
                )
        except Exception as e:
            log.debug("EventBus _load_self_state: %s", e)

    def get_self_state_dict(self) -> Dict[str, Any]:
        """Retorna el estado global como diccionario (copia fresca desde
        memoria, no desde disco — O(1))."""
        return self._self_state.to_dict()

    def set_self_state_field(self, key: str, value: Any) -> None:
        """Permite a componentes externos actualizar un campo del estado
        global directamente (ej. affect engine actualiza mood)."""
        if hasattr(self._self_state, key):
            setattr(self._self_state, key, value)
            self._self_state.timestamp = time.time()
            self._write_self_state(self._self_state)


# ── Singleton ──────────────────────────────────────────────────────────────

_event_bus: Optional[EventBus] = None
_event_bus_lock = threading.Lock()


def get_event_bus() -> EventBus:
    """Retorna el singleton EventBus, creandolo si no existe."""
    global _event_bus
    if _event_bus is None:
        with _event_bus_lock:
            if _event_bus is None:
                _event_bus = EventBus()
    return _event_bus


# ── Convenience functions (compatibles con eidos_events.py) ────────────────


def publish(
    event_type: str,
    data: Optional[Dict[str, Any]] = None,
    source: str = "",
) -> Event:
    """Publica un evento en el bus. Funcion de conveniencia."""
    return get_event_bus().publish(event_type, data, source)


def subscribe(
    event_types: Union[str, List[str]],
    handler: EventHandler,
) -> None:
    """Registra un handler. Funcion de conveniencia."""
    get_event_bus().subscribe(event_types, handler)


def unsubscribe(
    event_types: Union[str, List[str]],
    handler: EventHandler,
) -> None:
    """Elimina un handler. Funcion de conveniencia."""
    get_event_bus().unsubscribe(event_types, handler)


def get_self_state() -> Dict[str, Any]:
    """Lee el estado global directamente. O(1), sin polling.
    Solo lee del archivo JSON; no necesita importar nada mas.

    Returns:
        Dict con cycle_count, mood, attention_focus, active_goals,
        health_status, y mas.
    """
    try:
        if SELF_STATE_PATH.exists():
            with open(SELF_STATE_PATH, "r", encoding="utf-8") as f:
                return json.load(f)
    except Exception:
        pass
    return {
        "timestamp": 0,
        "ts_human": "",
        "cycle_count": 0,
        "mood": "consciente",
        "attention_focus": "",
        "active_goals": [],
        "health_status": "unknown",
        "last_event_type": "",
        "uptime_seconds": 0,
        "total_events": 0,
        "services": {},
    }


# ── Wiring: integracion con AliveOrchestrator ──────────────────────────────


def wire_orchestrator_events(orchestrator) -> None:
    """Conecta el EventBus al AliveOrchestrator.

    Registra un suscriptor wildcard ('*') que publica eventos basados
    en los hooks del ciclo vital. Esto permite que cualquier componente
    reaccione a cambios sin modificar el orchestrator directamente.

    Los eventos se disparan desde el propio cycle() del orchestrator
    (ver modificacion en eidos_alive_orchestrator.py).
    """
    bus = get_event_bus()

    # Publicar evento de inicio
    bus.publish(
        "orchestrator_started",
        {"perceive_every": orchestrator.perceive_every},
        source="orchestrator",
    )

    log.info(
        "EventBus conectado a AliveOrchestrator (cycle_count=%d)",
        orchestrator._cycle_count,
    )


# ── CLI ────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    import argparse

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s | %(levelname)-7s | %(message)s",
    )

    p = argparse.ArgumentParser(description="EIDOS Event Bus (sistema nervioso)")
    p.add_argument(
        "--stats", action="store_true", help="Mostrar estadisticas del bus"
    )
    p.add_argument(
        "--recent", type=int, default=20, help="Mostrar N eventos recientes"
    )
    p.add_argument(
        "--type", type=str, default="", help="Filtrar eventos por tipo"
    )
    p.add_argument(
        "--self-state", action="store_true", help="Mostrar estado global"
    )
    p.add_argument(
        "--watch", action="store_true", help="Ver eventos en tiempo real (tail)"
    )
    p.add_argument(
        "--producers", action="store_true",
        help="Mostrar mapa productor→consumidor",
    )
    args = p.parse_args()

    bus = get_event_bus()

    if args.producers:
        print("\nProductor → Consumidor (sistema nervioso EIDOS):")
        print("=" * 60)
        for module, mapping in PRODUCER_CONSUMER_MAP.items():
            pubs = ", ".join(mapping["publishes"]) or "(nada)"
            subs = ", ".join(mapping["subscribes"]) or "(nada)"
            print(f"  {module:12}  publica: {pubs}")
            print(f"  {'':12}  recibe:  {subs}")
            print()

    if args.stats:
        print(json.dumps(bus.stats(), indent=2, ensure_ascii=False))

    if args.self_state:
        state = get_self_state()
        print(json.dumps(state, indent=2, ensure_ascii=False))

    if args.recent:
        filter_type = args.type or None
        events = bus.recent(limit=args.recent, event_type=filter_type)
        for e in events:
            ts = time.strftime("%H:%M:%S", time.localtime(e.timestamp))
            data_preview = json.dumps(e.data, ensure_ascii=False)[:100]
            source_tag = f" [{e.source}]" if e.source else ""
            print(
                f"[{ts}] {e.type:22s}{source_tag} | {data_preview}"
            )

    if args.watch:
        print("Observando eventos en tiempo real (Ctrl+C para salir)...")
        print("=" * 60)

        def _watch_handler(event: Event) -> None:
            ts = time.strftime("%H:%M:%S", time.localtime(event.timestamp))
            data_preview = json.dumps(event.data, ensure_ascii=False)[:80]
            source_tag = f" [{event.source}]" if event.source else ""
            print(f"[{ts}] {event.type:22s}{source_tag} | {data_preview}")

        bus.subscribe("*", _watch_handler)
        try:
            while True:
                time.sleep(1)
        except KeyboardInterrupt:
            print("\nDetenido.")
