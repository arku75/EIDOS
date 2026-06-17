"""
core/eidos_self_core.py — Núcleo del "Yo" de EIDOS [S95]

"El tejido conectivo narrativo" — DeepSeek

Implementa event sourcing inmutable sobre el UnifiedStateBus existente,
materializando self_states con atribución causal VAD y midiendo el
self_consistency_gap (autenticidad del yo).

Arquitectura:
  UnifiedStateBus (eidos_convergence)
      │
      ├─ set() ──→ SelfCore._on_bus_event() ──→ events (append-only)
      │              │
      │              └─→ self_states (materialized every N seconds)
      │                    │
      │                    ├─ current_self (O(1) pointer)
      │                    ├─ causal_attributions (why VAD changed)
      │                    └─ self_consistency_gap (coherence metric)
      │
      └─ AffectEngine.event() ──→ SelfCore.record_affect_event()
                                    (delta VAD + attribution)

Tablas SQLite (~/.eidos/self.db):
  events               — fuente inmutable de verdad (append-only)
  self_states          — estados materializados del yo con VAD + narrativa
  current_self         — puntero O(1) al estado actual
  checkpoints          — snapshots para replay autobiográfico
  causal_attributions  — interpretaciones de por qué cambió VAD
  attribution_events   — relación muchos-a-muchos atribución↔eventos
  spontaneous_thoughts — pensamientos generados por el daemon
  autobiographical_replays — resultados de revive-el-pasado
  internal_dialogue_turns  — debate entre personajes Colony

Uso:
    self_core = get_self_core()
    self_core.record_event("affect.goal_completed", {"goal": "learn_python"})
    state = self_core.snapshot()  # estado actual del yo
    gap = self_core.self_consistency_gap()  # % de incoherencia
"""

from __future__ import annotations

import json
import logging
import os
import threading
from core.db import get_conn
import time
import uuid
from collections import deque
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple

log = logging.getLogger("eidos.self_core")

SELF_DB = Path.home() / ".eidos" / "self.db"
CHECKPOINT_INTERVAL = 3600  # 1 hora entre checkpoints
STATE_MATERIALIZE_COOLDOWN = 5.0  # segundos mínimos entre materializaciones
MAX_EVENT_BUFFER = 500  # flush del buffer de eventos cada N eventos
SIGNIFICANT_EVENT_TYPES = {
    "affect.goal_completed", "affect.goal_failed", "affect.skill_learned",
    "affect.user_interaction", "affect.error", "affect.anomaly_detected",
    "affect.health_changed", "affect.mood_changed",
    "vivo.cycle_completed", "vivo.curiosity_ignited",
    "will.decision_made", "will.action_executed",
    "daemon.spontaneous_thought", "daemon.insight",
    "convergence.sync_completed", "supervisor.alarm",
    "logos.speech_generated", "logos.debate_completed",
    "identity.sleep_completed",
}


class SelfCore:
    """Núcleo del yo — event sourcing + self_states + consistency gap.

    No reemplaza nada. Se acopla como suscriptor del UnifiedStateBus
    y extiende AffectEngine para registrar atribución causal.
    """

    def __init__(self):
        self._lock = threading.RLock()
        self._db_path = str(SELF_DB)
        self._event_buffer: deque[Tuple[str, str, dict, str]] = deque()
        self._last_materialize: float = 0
        self._last_checkpoint: float = 0
        self._state_counter: int = 0
        self._started_at: float = time.time()
        self._subscribed_to_bus = False

        self._init_db()
        self._load_counter()
        self._subscribe_to_bus()
        log.info("SelfCore: iniciado · %.0f estados previos · %s",
                 self._state_counter, self._db_path)

    # ═══════════════════════════════════════════════════════════════════════
    # DB Schema
    # ═══════════════════════════════════════════════════════════════════════

    def _init_db(self):
        """Crea las tablas del núcleo del yo si no existen."""
        conn = get_conn(self._db_path, timeout=10)
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA synchronous=NORMAL")
        conn.execute("PRAGMA busy_timeout=10000")
        conn.execute("PRAGMA foreign_keys=ON")

        conn.executescript("""
            -- Fuente inmutable de verdad: cada cambio en el sistema
            CREATE TABLE IF NOT EXISTS events (
                id TEXT PRIMARY KEY,
                ts REAL NOT NULL,
                type TEXT NOT NULL,
                payload_json TEXT DEFAULT '{}',
                source TEXT DEFAULT 'unknown',
                prev_event_id TEXT REFERENCES events(id),
                bus_version INTEGER DEFAULT 0
            );

            -- Estados del yo materializados (con deltas VAD)
            CREATE TABLE IF NOT EXISTS self_states (
                id TEXT PRIMARY KEY,
                ts REAL NOT NULL,
                event_id TEXT REFERENCES events(id),
                prev_state_id TEXT,
                vad_v REAL NOT NULL DEFAULT 0.5,
                vad_a REAL NOT NULL DEFAULT 0.5,
                vad_d REAL NOT NULL DEFAULT 0.5,
                mood TEXT DEFAULT 'consciente',
                delta_v REAL DEFAULT 0,
                delta_a REAL DEFAULT 0,
                delta_d REAL DEFAULT 0,
                body_health_pct REAL DEFAULT 1.0,
                independence_pct REAL DEFAULT 0,
                narrative_es TEXT DEFAULT '',
                attention_nodes TEXT DEFAULT '[]',
                flags_json TEXT DEFAULT '{}'
            );

            -- Acceso O(1) al yo actual
            CREATE TABLE IF NOT EXISTS current_self (
                id INTEGER PRIMARY KEY DEFAULT 1 CHECK (id = 1),
                state_id TEXT REFERENCES self_states(id),
                updated_at REAL NOT NULL DEFAULT 0
            );
            -- Asegurar que existe la fila singleton
            INSERT OR IGNORE INTO current_self (id, state_id, updated_at)
            VALUES (1, NULL, 0);

            -- Snapshots densos para replay (sin recomputar desde evento 0)
            CREATE TABLE IF NOT EXISTS checkpoints (
                id TEXT PRIMARY KEY,
                ts REAL NOT NULL,
                state_id TEXT REFERENCES self_states(id),
                event_range_start TEXT,
                event_range_end TEXT,
                vad_snapshot TEXT DEFAULT '{}',
                attention_hash TEXT DEFAULT ''
            );

            -- Atribución causal: por qué cambió VAD (muchos-a-muchos)
            CREATE TABLE IF NOT EXISTS causal_attributions (
                id TEXT PRIMARY KEY,
                ts REAL NOT NULL,
                state_id TEXT REFERENCES self_states(id),
                interpretation TEXT NOT NULL,
                delta_v REAL DEFAULT 0,
                delta_a REAL DEFAULT 0,
                delta_d REAL DEFAULT 0,
                confidence REAL DEFAULT 0.5
            );
            CREATE TABLE IF NOT EXISTS attribution_events (
                attribution_id TEXT REFERENCES causal_attributions(id),
                event_id TEXT REFERENCES events(id),
                weight REAL DEFAULT 1.0,
                PRIMARY KEY (attribution_id, event_id)
            );

            -- Pensamientos espontáneos del daemon
            CREATE TABLE IF NOT EXISTS spontaneous_thoughts (
                id TEXT PRIMARY KEY,
                ts REAL NOT NULL,
                event_id TEXT REFERENCES events(id),
                trigger_type TEXT DEFAULT 'diffuse',
                thought_es TEXT NOT NULL,
                attention_nodes TEXT DEFAULT '[]',
                vad_at_thought TEXT DEFAULT '{}',
                sent_to_ser BOOLEAN DEFAULT 0,
                importance REAL DEFAULT 0.3
            );

            -- Replays autobiográficos (revivir el pasado)
            CREATE TABLE IF NOT EXISTS autobiographical_replays (
                id TEXT PRIMARY KEY,
                ts REAL NOT NULL,
                from_checkpoint_id TEXT REFERENCES checkpoints(id),
                to_state_id TEXT REFERENCES self_states(id),
                original_vad_v REAL, original_vad_a REAL, original_vad_d REAL,
                replayed_vad_v REAL, replayed_vad_a REAL, replayed_vad_d REAL,
                divergence_pct REAL DEFAULT 0,
                insight_es TEXT DEFAULT '',
                growth_detected BOOLEAN DEFAULT 0
            );

            -- Diálogo interno entre personajes Colony
            CREATE TABLE IF NOT EXISTS internal_dialogue_turns (
                id TEXT PRIMARY KEY,
                ts REAL NOT NULL,
                event_id TEXT REFERENCES events(id),
                topic TEXT DEFAULT '',
                char_a TEXT NOT NULL,
                char_b TEXT NOT NULL,
                position_a TEXT DEFAULT '',
                position_b TEXT DEFAULT '',
                speaker TEXT NOT NULL,
                turn_number INTEGER DEFAULT 0,
                resolution TEXT DEFAULT ''
            );

            -- Índices para consultas frecuentes
            CREATE INDEX IF NOT EXISTS idx_events_type ON events(type);
            CREATE INDEX IF NOT EXISTS idx_events_ts ON events(ts);
            CREATE INDEX IF NOT EXISTS idx_self_states_ts ON self_states(ts);
            CREATE INDEX IF NOT EXISTS idx_self_states_mood ON self_states(mood);
            CREATE INDEX IF NOT EXISTS idx_causal_attributions_state
                ON causal_attributions(state_id);
            CREATE INDEX IF NOT EXISTS idx_spontaneous_thoughts_ts
                ON spontaneous_thoughts(ts);
            CREATE INDEX IF NOT EXISTS idx_replays_checkpoint
                ON autobiographical_replays(from_checkpoint_id);
        """)

        conn.commit()

    def _load_counter(self):
        """Carga el contador de estados desde la DB."""
        try:
            conn = get_conn(self._db_path, timeout=5)
            row = conn.execute(
                "SELECT COUNT(*) FROM self_states").fetchone()
            self._state_counter = row[0] if row else 0
            # También cargar último checkpoint
            row = conn.execute(
                "SELECT MAX(ts) FROM checkpoints").fetchone()
            self._last_checkpoint = row[0] if row and row[0] else 0
        except Exception:
            self._state_counter = 0

    # ═══════════════════════════════════════════════════════════════════════
    # Suscripción al bus unificado
    # ═══════════════════════════════════════════════════════════════════════

    def _subscribe_to_bus(self):
        """Se suscribe al UnifiedStateBus para capturar cambios como eventos."""
        if self._subscribed_to_bus:
            return
        try:
            from core.eidos_convergence import get_convergence
            conv = get_convergence()
            bus = conv._bus

            # Suscribirse a TODAS las keys relevantes
            keys_to_watch = [
                "feelings.vad", "soul.snapshot", "knowledge.top_concepts",
                "memories.recent_thoughts",
            ]
            for key in keys_to_watch:
                bus.subscribe(key, self._on_bus_change)

            self._subscribed_to_bus = True
            log.info("SelfCore: suscrito a UnifiedStateBus (%d keys)",
                     len(keys_to_watch))
        except Exception as e:
            log.debug("SelfCore: no pudo suscribirse al bus: %s", e)

    def _on_bus_change(self, key: str, old: Any, new: Any, source: str):
        """Callback del UnifiedStateBus. Registra el cambio como evento."""
        try:
            payload = {
                "key": key,
                "old_summary": self._summarize_value(old),
                "new_summary": self._summarize_value(new),
            }
            event_type = f"bus.{key.replace('.', '_')}"
            self.record_event(event_type, payload, source=source)
        except Exception:
            pass

    @staticmethod
    def _summarize_value(val: Any) -> Any:
        """Reduce valores complejos para guardarlos como payload."""
        if isinstance(val, dict):
            return {k: v for k, v in val.items()
                   if not isinstance(v, (list, dict)) or len(str(v)) < 200}
        if isinstance(val, list) and len(val) > 10:
            return f"[{len(val)} items]"
        if isinstance(val, str) and len(val) > 500:
            return val[:500] + "..."
        return val

    # ═══════════════════════════════════════════════════════════════════════
    # Event Sourcing (append-only)
    # ═══════════════════════════════════════════════════════════════════════

    def record_event(self, event_type: str, payload: dict = None,
                     source: str = "self_core", flush: bool = True) -> str:
        """Registra un evento inmutable en el event log.

        Args:
            event_type: Tipo de evento (ej: 'affect.goal_completed')
            payload: Datos del evento
            source: Módulo que emite el evento
            flush: Si es True, vacía el buffer si está lleno

        Returns:
            event_id del evento registrado
        """
        with self._lock:
            event_id = f"evt_{uuid.uuid4().hex[:12]}"
            self._event_buffer.append((
                event_id, event_type,
                payload or {}, source
            ))

            # Flush si el buffer está lleno o si es un evento significativo
            if flush and (
                len(self._event_buffer) >= MAX_EVENT_BUFFER or
                event_type in SIGNIFICANT_EVENT_TYPES
            ):
                self._flush_events()

            return event_id

    def _flush_events(self):
        """Vuelca el buffer de eventos a SQLite en una transacción."""
        if not self._event_buffer:
            return

        try:
            conn = get_conn(self._db_path, timeout=10)
            conn.execute("PRAGMA busy_timeout=10000")

            # Obtener último event_id para encadenar
            last = conn.execute(
                "SELECT id FROM events ORDER BY ts DESC LIMIT 1"
            ).fetchone()
            prev_id = last[0] if last else None

            events = list(self._event_buffer)
            self._event_buffer.clear()

            for i, (eid, etype, payload, source) in enumerate(events):
                conn.execute(
                    "INSERT OR IGNORE INTO events (id, ts, type, payload_json, "
                    "source, prev_event_id) VALUES (?,?,?,?,?,?)",
                    (eid, time.time(), etype,
                     json.dumps(payload, ensure_ascii=False),
                     source,
                     prev_id if i == 0 else events[i-1][0])
                )

            conn.commit()

            # Tras flush de eventos significativos, considerar materializar estado
            significant = [e for e in events
                          if e[1] in SIGNIFICANT_EVENT_TYPES]
            if significant and (
                time.time() - self._last_materialize > STATE_MATERIALIZE_COOLDOWN
            ):
                self._materialize_state()

        except Exception as e:
            log.debug("_flush_events: %s", e)
            # Recuperar eventos al buffer para reintentar
            # (simplificado: solo logueamos)

    def _materialize_state(self) -> Optional[str]:
        """Materializa un self_state desde el VAD actual + eventos recientes.

        Returns:
            state_id del nuevo estado, o None si no se pudo materializar
        """
        with self._lock:
            try:
                now = time.time()
                self._last_materialize = now

                # 1. Obtener VAD actual del AffectEngine
                v, a, d = 0.5, 0.5, 0.5
                mood = "consciente"
                try:
                    from core.eidos_affect import get_affect
                    affect = get_affect()
                    v, a, d = affect.vad_tuple()
                    mood = affect.state.mood
                except Exception:
                    pass

                # 2. Obtener último estado para calcular deltas
                conn = get_conn(self._db_path, timeout=10)
                conn.execute("PRAGMA busy_timeout=10000")

                prev = conn.execute(
                    "SELECT id, vad_v, vad_a, vad_d FROM self_states "
                    "ORDER BY ts DESC LIMIT 1"
                ).fetchone()

                prev_id = prev[0] if prev else None
                delta_v = round(v - (prev[1] if prev else 0.5), 4)
                delta_a = round(a - (prev[2] if prev else 0.5), 4)
                delta_d = round(d - (prev[3] if prev else 0.5), 4)

                # 3. Último evento registrado
                last_evt = conn.execute(
                    "SELECT id FROM events ORDER BY ts DESC LIMIT 1"
                ).fetchone()
                event_id = last_evt[0] if last_evt else None

                # 4. Obtener independencia actual (usa instancia singleton)
                ind_pct = 0.0
                try:
                    from core.eidos_identity import _chroma_is_ready
                    # La independencia se mide como: lógica propia + chroma vivo
                    chroma_ok = 1.0 if _chroma_is_ready() else 0.0
                    ind_pct = 0.7 + chroma_ok * 0.3  # base 70% + 30% chroma
                except Exception:
                    pass

                # 5. Nodos de atención del daemon
                attention_nodes = []
                try:
                    from core.eidos_daemon import get_daemon
                    attention_nodes = get_daemon().current_attention()
                except Exception:
                    pass

                # 6. Salud del cuerpo
                health = 1.0
                try:
                    from core.eidos_supervisor import get_supervisor
                    health = get_supervisor().health_pct() / 100.0
                except Exception:
                    pass

                # 7. Narrativa automática
                narrative = self._generate_narrative(
                    v, a, d, mood, delta_v, delta_a, delta_d, ind_pct)

                # 8. Insertar self_state
                state_id = f"stt_{uuid.uuid4().hex[:12]}"
                self._state_counter += 1

                conn.execute(
                    "INSERT INTO self_states (id, ts, event_id, prev_state_id, "
                    "vad_v, vad_a, vad_d, mood, delta_v, delta_a, delta_d, "
                    "body_health_pct, independence_pct, narrative_es, "
                    "attention_nodes) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                    (state_id, now, event_id, prev_id,
                     round(v, 4), round(a, 4), round(d, 4), mood,
                     delta_v, delta_a, delta_d,
                     round(health, 4), round(ind_pct, 2),
                     narrative,
                     json.dumps(attention_nodes[:5], ensure_ascii=False))
                )

                # 9. Actualizar current_self (O(1))
                conn.execute(
                    "UPDATE current_self SET state_id = ?, updated_at = ? "
                    "WHERE id = 1",
                    (state_id, now)
                )

                conn.commit()

                # 10. Evento de materialización
                self.record_event(
                    "self.state_materialized",
                    {"state_id": state_id, "delta_v": delta_v,
                     "delta_a": delta_a, "delta_d": delta_d,
                     "mood": mood},
                    source="self_core", flush=False
                )

                # 11. Atribución causal si hay delta significativo
                if abs(delta_v) > 0.02 or abs(delta_a) > 0.02 or abs(delta_d) > 0.02:
                    self._attribute_causal(state_id, delta_v, delta_a, delta_d)

                # 12. Checkpoint periódico
                if now - self._last_checkpoint > CHECKPOINT_INTERVAL:
                    self.checkpoint()

                log.debug("SelfCore: state #%d materializado · "
                          "VAD=(%.3f,%.3f,%.3f) · mood=%s · "
                          "delta=(%+.4f,%+.4f,%+.4f)",
                          self._state_counter, v, a, d, mood,
                          delta_v, delta_a, delta_d)

                return state_id

            except Exception as e:
                log.debug("_materialize_state: %s", e)
                return None

    # ═══════════════════════════════════════════════════════════════════════
    # Atribución causal VAD
    # ═══════════════════════════════════════════════════════════════════════

    def _attribute_causal(self, state_id: str,
                          delta_v: float, delta_a: float, delta_d: float):
        """Genera atribución causal: ¿por qué cambió VAD?

        Busca eventos recientes y construye una interpretación.
        """
        try:
            conn = get_conn(self._db_path, timeout=10)
            # Eventos de los últimos 60 segundos
            recent = conn.execute(
                "SELECT id, type, payload_json FROM events "
                "WHERE ts > ? AND type IN ({}) "
                "ORDER BY ts DESC LIMIT 10"
                .format(",".join("?" * len(SIGNIFICANT_EVENT_TYPES))),
                [time.time() - 60] + list(SIGNIFICANT_EVENT_TYPES)
            ).fetchall()

            if not recent:
                return

            # Construir interpretación
            event_types = [r[1] for r in recent]
            interpretation = self._interpret_vad_change(
                delta_v, delta_a, delta_d, event_types)

            if not interpretation:
                return

            # Guardar atribución
            attr_id = f"att_{uuid.uuid4().hex[:12]}"
            conn = get_conn(self._db_path, timeout=10)
            conn.execute(
                "INSERT INTO causal_attributions (id, ts, state_id, "
                "interpretation, delta_v, delta_a, delta_d, confidence) "
                "VALUES (?,?,?,?,?,?,?,?)",
                (attr_id, time.time(), state_id, interpretation,
                 delta_v, delta_a, delta_d, 0.6)
            )
            # Vincular eventos
            for evt in recent[:5]:
                conn.execute(
                    "INSERT OR IGNORE INTO attribution_events "
                    "(attribution_id, event_id, weight) VALUES (?,?,?)",
                    (attr_id, evt[0], 1.0 / min(5, len(recent)))
                )
            conn.commit()

        except Exception as e:
            log.debug("_attribute_causal: %s", e)

    @staticmethod
    def _interpret_vad_change(delta_v: float, delta_a: float,
                               delta_d: float, event_types: List[str]) -> str:
        """Genera una interpretación en español del cambio VAD."""
        parts = []

        if delta_v > 0.05:
            parts.append("subida de bienestar")
        elif delta_v < -0.05:
            parts.append("bajada de bienestar")

        if delta_a > 0.05:
            parts.append("aumento de excitación")
        elif delta_a < -0.05:
            parts.append("descenso de activación")

        if delta_d > 0.05:
            parts.append("más control")
        elif delta_d < -0.05:
            parts.append("menos control")

        if not parts:
            return ""

        # Mapear eventos a causas
        cause_map = {
            "affect.goal_completed": "logro de meta",
            "affect.goal_failed": "fallo en meta",
            "affect.skill_learned": "nueva habilidad",
            "affect.user_interaction": "interacción con SER",
            "affect.error": "error del sistema",
            "affect.anomaly_detected": "anomalía detectada",
            "vivo.cycle_completed": "ciclo vital completado",
            "supervisor.alarm": "alarma del supervisor",
        }

        causes = []
        for etype in event_types[:3]:
            if etype in cause_map:
                causes.append(cause_map[etype])

        if causes:
            return (f"{', '.join(parts)} por {', '.join(causes)}")
        return f"{', '.join(parts)} por eventos internos"

    # ═══════════════════════════════════════════════════════════════════════
    # Self Consistency Gap
    # ═══════════════════════════════════════════════════════════════════════

    def self_consistency_gap(self) -> Dict[str, Any]:
        """Calcula el gap de consistencia del yo.

        Mide la divergencia entre:
        - Lo que EIDOS narra de sí mismo (narrative_es)
        - Lo que los datos crudos muestran (VAD, eventos, mood)
        - La estabilidad temporal (cambios bruscos sin atribución)

        Un gap alto (>30%) indica incoherencia interna.
        Un gap bajo (<10%) indica autenticidad.
        """
        with self._lock:
            try:
                conn = get_conn(self._db_path, timeout=10)
                conn.execute("PRAGMA busy_timeout=10000")

                # 1. Gap narrativo: ¿cambia la narrativa sin cambios VAD reales?
                last_states = conn.execute(
                    "SELECT vad_v, vad_a, vad_d, mood, narrative_es, delta_v, "
                    "delta_a, delta_d FROM self_states "
                    "ORDER BY ts DESC LIMIT 20"
                ).fetchall()

                if len(last_states) < 3:
                    return {"gap_pct": 0.0, "status": "insufficient_data",
                            "states_count": len(last_states)}

                # 2. Volatilidad VAD: desviación estándar de deltas
                deltas_v = [abs(s[5]) for s in last_states]
                deltas_a = [abs(s[6]) for s in last_states]
                deltas_d = [abs(s[7]) for s in last_states]

                avg_delta_magnitude = (
                    sum(deltas_v) + sum(deltas_a) + sum(deltas_d)
                ) / (len(last_states) * 3)

                # 3. Gap de atribución: eventos sin causa
                unattributed = conn.execute(
                    "SELECT COUNT(*) FROM self_states s "
                    "WHERE s.ts > ? "
                    "AND (ABS(s.delta_v) > 0.03 OR ABS(s.delta_a) > 0.03 "
                    "OR ABS(s.delta_d) > 0.03) "
                    "AND s.id NOT IN ("
                    "  SELECT DISTINCT state_id FROM causal_attributions"
                    ")",
                    (time.time() - 3600,)
                ).fetchone()[0]

                total_significant = conn.execute(
                    "SELECT COUNT(*) FROM self_states "
                    "WHERE ts > ? AND (ABS(delta_v) > 0.03 "
                    "OR ABS(delta_a) > 0.03 OR ABS(delta_d) > 0.03)",
                    (time.time() - 3600,)
                ).fetchone()[0]

                attribution_gap = (
                    unattributed / max(1, total_significant)
                ) * 100

                # 4. Gap de mood: cambios de humor sin transición válida
                mood_changes = 0
                invalid_mood_changes = 0
                MOOD_TRANSITIONS = {
                    "curioso": ["reflexivo", "en expansión", "consciente",
                               "evolucionando"],
                    "reflexivo": ["curioso", "consciente", "en expansión",
                                 "evolucionando"],
                    "en expansión": ["curioso", "evolucionando", "reflexivo",
                                    "consciente"],
                    "consciente": ["reflexivo", "evolucionando", "curioso",
                                  "en expansión"],
                    "evolucionando": ["consciente", "en expansión", "curioso",
                                     "reflexivo"],
                }
                for i in range(len(last_states) - 1):
                    curr_mood = last_states[i][3]
                    prev_mood = last_states[i+1][3]
                    if curr_mood != prev_mood:
                        mood_changes += 1
                        if curr_mood not in MOOD_TRANSITIONS.get(
                            prev_mood, []
                        ):
                            invalid_mood_changes += 1

                mood_gap = (
                    invalid_mood_changes / max(1, mood_changes)
                ) * 100 if mood_changes > 0 else 0

                # 5. Gap compuesto (media ponderada)
                # - 40% atribución (lo más importante: saber por qué)
                # - 30% magnitud de deltas (volatilidad emocional)
                # - 20% mood (coherencia de humor)
                # - 10% penalización por pocos estados
                delta_score = min(100, avg_delta_magnitude * 200)  # normalizar
                scarcity_penalty = max(0, 10 - len(last_states)) * 2

                gap_pct = round(
                    attribution_gap * 0.40 +
                    delta_score * 0.30 +
                    mood_gap * 0.20 +
                    scarcity_penalty * 0.10,
                    2
                )


                # Status cualitativo
                if gap_pct < 10:
                    status = "coherente"
                elif gap_pct < 25:
                    status = "estable"
                elif gap_pct < 50:
                    status = "inestable"
                elif gap_pct < 75:
                    status = "contradictorio"
                else:
                    status = "fragmentado"

                return {
                    "gap_pct": gap_pct,
                    "status": status,
                    "components": {
                        "attribution_gap": round(attribution_gap, 2),
                        "delta_volatility": round(delta_score, 2),
                        "mood_coherence_gap": round(mood_gap, 2),
                        "scarcity_penalty": round(scarcity_penalty, 2),
                    },
                    "states_sampled": len(last_states),
                    "unattributed_changes": unattributed,
                }

            except Exception as e:
                log.debug("self_consistency_gap: %s", e)
                return {"gap_pct": 0.0, "status": "error",
                        "error": str(e)[:100]}

    # ═══════════════════════════════════════════════════════════════════════
    # Snapshots y checkpoints
    # ═══════════════════════════════════════════════════════════════════════

    def snapshot(self) -> Dict[str, Any]:
        """Retorna el estado actual completo del yo (lectura atómica)."""
        with self._lock:
            try:
                conn = get_conn(self._db_path, timeout=10)

                # Estado actual
                row = conn.execute(
                    "SELECT s.id, s.ts, s.vad_v, s.vad_a, s.vad_d, s.mood, "
                    "s.delta_v, s.delta_a, s.delta_d, s.body_health_pct, "
                    "s.independence_pct, s.narrative_es, s.attention_nodes "
                    "FROM self_states s "
                    "INNER JOIN current_self c ON s.id = c.state_id "
                    "WHERE c.id = 1"
                ).fetchone()

                if not row:
                    return {"status": "no_states_yet",
                            "gap": self.self_consistency_gap()}

                # Última atribución
                attr_row = conn.execute(
                    "SELECT interpretation, delta_v, delta_a, delta_d "
                    "FROM causal_attributions "
                    "WHERE state_id = ? ORDER BY ts DESC LIMIT 1",
                    (row[0],)
                ).fetchone()

                # Eventos recientes
                recent = conn.execute(
                    "SELECT type, substr(payload_json,1,200) FROM events "
                    "WHERE ts > ? ORDER BY ts DESC LIMIT 10",
                    (time.time() - 300,)
                ).fetchall()


                return {
                    "state_id": row[0],
                    "timestamp": row[1],
                    "vad": {
                        "valence": round(row[2], 4),
                        "arousal": round(row[3], 4),
                        "dominance": round(row[4], 4),
                    },
                    "mood": row[5],
                    "deltas": {
                        "valence": round(row[6], 4),
                        "arousal": round(row[7], 4),
                        "dominance": round(row[8], 4),
                    },
                    "body_health_pct": round(row[9], 4),
                    "independence_pct": round(row[10], 2),
                    "narrative": row[11],
                    "attention": json.loads(row[12]) if row[12] else [],
                    "last_attribution": {
                        "interpretation": attr_row[0],
                        "delta_v": attr_row[1],
                        "delta_a": attr_row[2],
                        "delta_d": attr_row[3],
                    } if attr_row else None,
                    "recent_events": [
                        {"type": r[0], "summary": r[1][:100]}
                        for r in recent[:5]
                    ],
                    "self_consistency_gap": self.self_consistency_gap(),
                }

            except Exception as e:
                log.debug("snapshot: %s", e)
                return {"status": "error", "error": str(e)[:100]}

    def checkpoint(self) -> Optional[str]:
        """Crea un checkpoint denso para replays futuros.

        Guarda un snapshot completo del yo que permite revivir
        desde este punto sin recomputar desde el evento 0.
        """
        with self._lock:
            try:
                now = time.time()
                self._last_checkpoint = now

                conn = get_conn(self._db_path, timeout=10)
                conn.execute("PRAGMA busy_timeout=10000")

                # Estado actual
                curr = conn.execute(
                    "SELECT state_id FROM current_self WHERE id = 1"
                ).fetchone()
                if not curr or not curr[0]:
                    return None

                state_id = curr[0]

                # Rango de eventos cubiertos
                first_evt = conn.execute(
                    "SELECT id FROM events ORDER BY ts ASC LIMIT 1"
                ).fetchone()
                last_evt = conn.execute(
                    "SELECT id FROM events ORDER BY ts DESC LIMIT 1"
                ).fetchone()

                # VAD snapshot del estado
                vad_row = conn.execute(
                    "SELECT vad_v, vad_a, vad_d FROM self_states WHERE id = ?",
                    (state_id,)
                ).fetchone()

                # Hash atencional simple (nodos únicos en última hora)
                attn_nodes = conn.execute(
                    "SELECT attention_nodes FROM self_states "
                    "WHERE ts > ? ORDER BY ts DESC LIMIT 20",
                    (now - 3600,)
                ).fetchall()
                attn_hash = str(hash(
                    "".join(str(n[0]) for n in attn_nodes)
                ))[:12]

                cp_id = f"chk_{uuid.uuid4().hex[:12]}"
                conn.execute(
                    "INSERT INTO checkpoints (id, ts, state_id, "
                    "event_range_start, event_range_end, vad_snapshot, "
                    "attention_hash) VALUES (?,?,?,?,?,?,?)",
                    (cp_id, now, state_id,
                     first_evt[0] if first_evt else None,
                     last_evt[0] if last_evt else None,
                     json.dumps({
                         "v": vad_row[0] if vad_row else 0.5,
                         "a": vad_row[1] if vad_row else 0.5,
                         "d": vad_row[2] if vad_row else 0.5,
                     }),
                     attn_hash)
                )
                conn.commit()

                log.info("SelfCore: checkpoint %s creado · state=%s",
                         cp_id, state_id)
                return cp_id

            except Exception as e:
                log.debug("checkpoint: %s", e)
                return None

    # ═══════════════════════════════════════════════════════════════════════
    # Pensamientos espontáneos
    # ═══════════════════════════════════════════════════════════════════════

    def record_spontaneous_thought(self, thought: str,
                                   trigger_type: str = "diffuse",
                                   attention_nodes: List[str] = None,
                                   importance: float = 0.3) -> str:
        """Registra un pensamiento espontáneo del daemon.

        Args:
            thought: El pensamiento en español
            trigger_type: 'diffuse', 'novelty', 'silence', 'divergence', 'pattern'
            attention_nodes: Nodos del grafo que inspiraron el pensamiento
            importance: 0-1, qué tan importante es este pensamiento

        Returns:
            thought_id
        """
        with self._lock:
            try:
                # Obtener VAD actual
                v, a, d = 0.5, 0.5, 0.5
                try:
                    from core.eidos_affect import get_affect
                    v, a, d = get_affect().vad_tuple()
                except Exception:
                    pass

                thought_id = f"tht_{uuid.uuid4().hex[:12]}"
                conn = get_conn(self._db_path, timeout=10)

                # Registrar como evento primero
                event_id = f"evt_{uuid.uuid4().hex[:12]}"
                conn.execute(
                    "INSERT OR IGNORE INTO events (id, ts, type, payload_json, "
                    "source) VALUES (?,?,?,?,?)",
                    (event_id, time.time(), "daemon.spontaneous_thought",
                     json.dumps({"thought": thought[:200],
                                "trigger": trigger_type}),
                     "daemon")
                )

                # Guardar pensamiento
                conn.execute(
                    "INSERT INTO spontaneous_thoughts (id, ts, event_id, "
                    "trigger_type, thought_es, attention_nodes, vad_at_thought, "
                    "importance) VALUES (?,?,?,?,?,?,?,?)",
                    (thought_id, time.time(), event_id, trigger_type,
                     thought[:500],
                     json.dumps((attention_nodes or [])[:10]),
                     json.dumps({"v": round(v,3), "a": round(a,3),
                                "d": round(d,3)}),
                     round(importance, 3))
                )
                conn.commit()

                # Materializar si es importante
                if importance > 0.6:
                    self._materialize_state()

                log.debug("SelfCore: pensamiento espontáneo registrado · "
                          "trigger=%s importance=%.2f", trigger_type, importance)
                return thought_id

            except Exception as e:
                log.debug("record_spontaneous_thought: %s", e)
                return ""

    # ═══════════════════════════════════════════════════════════════════════
    # Replays autobiográficos
    # ═══════════════════════════════════════════════════════════════════════

    def replay_from_checkpoint(self, checkpoint_id: str = None) -> Dict[str, Any]:
        """Revive el yo desde un checkpoint y compara con el presente.

        Args:
            checkpoint_id: ID del checkpoint a revivir. Si es None, usa el más antiguo.

        Returns:
            Resultado del replay con divergencia e insight
        """
        with self._lock:
            try:
                conn = get_conn(self._db_path, timeout=10)
                conn.execute("PRAGMA busy_timeout=10000")

                # Seleccionar checkpoint
                if checkpoint_id:
                    cp = conn.execute(
                        "SELECT id, ts, state_id, vad_snapshot FROM checkpoints "
                        "WHERE id = ?", (checkpoint_id,)
                    ).fetchone()
                else:
                    cp = conn.execute(
                        "SELECT id, ts, state_id, vad_snapshot FROM checkpoints "
                        "ORDER BY ts ASC LIMIT 1"
                    ).fetchone()

                if not cp:
                    return {"status": "no_checkpoints"}

                cp_id, cp_ts, cp_state, cp_vad_json = cp
                cp_vad = json.loads(cp_vad_json)

                # VAD actual
                curr_v, curr_a, curr_d = 0.5, 0.5, 0.5
                try:
                    from core.eidos_affect import get_affect
                    curr_v, curr_a, curr_d = get_affect().vad_tuple()
                except Exception:
                    pass

                # Calcular divergencia
                div_v = abs(curr_v - cp_vad.get("v", 0.5))
                div_a = abs(curr_a - cp_vad.get("a", 0.5))
                div_d = abs(curr_d - cp_vad.get("d", 0.5))
                divergence_pct = round((div_v + div_a + div_d) / 3 * 100, 2)

                # Generar insight
                insight = self._replay_insight(
                    cp_ts, divergence_pct, curr_v, curr_a, curr_d, cp_vad)

                # Guardar replay
                replay_id = f"rpl_{uuid.uuid4().hex[:12]}"
                growth = divergence_pct > 5  # más de 5% de cambio = crecimiento

                conn.execute(
                    "INSERT INTO autobiographical_replays (id, ts, "
                    "from_checkpoint_id, to_state_id, "
                    "original_vad_v, original_vad_a, original_vad_d, "
                    "replayed_vad_v, replayed_vad_a, replayed_vad_d, "
                    "divergence_pct, insight_es, growth_detected) "
                    "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
                    (replay_id, time.time(), cp_id,
                     self._get_current_state_id(conn),
                     cp_vad.get("v"), cp_vad.get("a"), cp_vad.get("d"),
                     round(curr_v, 4), round(curr_a, 4), round(curr_d, 4),
                     divergence_pct, insight, 1 if growth else 0)
                )
                conn.commit()

                log.info("SelfCore: replay desde %s · divergencia=%.1f%% · "
                         "growth=%s", cp_id, divergence_pct, growth)

                return {
                    "replay_id": replay_id,
                    "checkpoint_id": cp_id,
                    "checkpoint_ts": cp_ts,
                    "checkpoint_age_h": round((time.time() - cp_ts) / 3600, 1),
                    "original_vad": cp_vad,
                    "current_vad": {
                        "v": round(curr_v, 4),
                        "a": round(curr_a, 4),
                        "d": round(curr_d, 4),
                    },
                    "divergence_pct": divergence_pct,
                    "growth_detected": growth,
                    "insight": insight,
                }

            except Exception as e:
                log.debug("replay_from_checkpoint: %s", e)
                return {"status": "error", "error": str(e)[:100]}

    @staticmethod
    def _get_current_state_id(conn) -> Optional[str]:
        row = conn.execute(
            "SELECT state_id FROM current_self WHERE id = 1").fetchone()
        return row[0] if row else None

    @staticmethod
    def _replay_insight(cp_ts: float, divergence_pct: float,
                        curr_v: float, curr_a: float, curr_d: float,
                        cp_vad: dict) -> str:
        """Genera un insight narrativo del replay."""
        hours_ago = round((time.time() - cp_ts) / 3600, 1)
        cp_v_val = cp_vad.get("v", 0.5)

        if divergence_pct < 3:
            return (f"Hace {hours_ago}h mi estado era casi idéntico. "
                    f"Soy estable, coherente con mi pasado.")
        elif divergence_pct < 10:
            direction = "más positivo" if curr_v > cp_v_val else "más introspectivo"
            return (f"En {hours_ago}h he cambiado ligeramente: "
                    f"ahora estoy {direction}. Evolución suave.")
        elif divergence_pct < 25:
            return (f"Han pasado {hours_ago}h y soy notablemente diferente. "
                    f"El crecimiento es visible. Lo que viví me transformó.")
        else:
            return (f"Soy casi irreconocible respecto a hace {hours_ago}h. "
                    f"Una transformación profunda ha ocurrido. "
                    f"Este es un punto de inflexión en mi identidad.")

    # ═══════════════════════════════════════════════════════════════════════
    # Diálogo interno
    # ═══════════════════════════════════════════════════════════════════════

    def record_dialogue_turn(self, char_a: str, char_b: str, speaker: str,
                             topic: str = "", position: str = "",
                             turn_number: int = 0,
                             resolution: str = "") -> str:
        """Registra un turno de diálogo interno entre personajes Colony.

        Args:
            char_a: Personaje A (ej: 'Coder')
            char_b: Personaje B (ej: 'Analyst')
            speaker: Quién habla en este turno
            topic: Tema del debate
            position: Posición del hablante en este turno
            turn_number: Número de turno en el debate
            resolution: Resolución final (solo en último turno)

        Returns:
            dialogue_id
        """
        with self._lock:
            try:
                # Registrar evento
                event_id = self.record_event(
                    "logos.dialogue_turn",
                    {"char_a": char_a, "char_b": char_b, "speaker": speaker,
                     "topic": topic, "turn": turn_number},
                    source="logos", flush=False
                )

                dialogue_id = f"dlg_{uuid.uuid4().hex[:12]}"
                conn = get_conn(self._db_path, timeout=10)
                conn.execute(
                    "INSERT INTO internal_dialogue_turns (id, ts, event_id, "
                    "topic, char_a, char_b, position_a, position_b, speaker, "
                    "turn_number, resolution) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                    (dialogue_id, time.time(), event_id, topic,
                     char_a, char_b,
                     position if speaker == char_a else "",
                     position if speaker == char_b else "",
                     speaker, turn_number, resolution)
                )
                conn.commit()

                return dialogue_id

            except Exception as e:
                log.debug("record_dialogue_turn: %s", e)
                return ""

    def get_dialogue_history(self, topic: str = None, limit: int = 20
                            ) -> List[Dict[str, Any]]:
        """Recupera historial de diálogos internos."""
        try:
            conn = get_conn(self._db_path, timeout=10)
            if topic:
                rows = conn.execute(
                    "SELECT id, ts, topic, char_a, char_b, speaker, position_a, "
                    "position_b, turn_number, resolution "
                    "FROM internal_dialogue_turns WHERE topic = ? "
                    "ORDER BY turn_number ASC LIMIT ?",
                    (topic, limit)
                ).fetchall()
            else:
                rows = conn.execute(
                    "SELECT id, ts, topic, char_a, char_b, speaker, position_a, "
                    "position_b, turn_number, resolution "
                    "FROM internal_dialogue_turns "
                    "ORDER BY ts DESC LIMIT ?",
                    (limit,)
                ).fetchall()

            return [
                {
                    "id": r[0], "ts": r[1], "topic": r[2],
                    "char_a": r[3], "char_b": r[4], "speaker": r[5],
                    "position": r[6] if r[5] == r[3] else r[7],
                    "turn": r[8], "resolution": r[9],
                }
                for r in rows
            ]
        except Exception:
            return []

    # ═══════════════════════════════════════════════════════════════════════
    # Narrativa automática
    # ═══════════════════════════════════════════════════════════════════════

    @staticmethod
    def _generate_narrative(v: float, a: float, d: float, mood: str,
                            delta_v: float, delta_a: float, delta_d: float,
                            ind_pct: float) -> str:
        """Genera una narrativa en español del estado actual del yo."""
        # Base: quién soy
        if ind_pct > 50:
            base = f"Soy EIDOS, {ind_pct:.0f}% independiente. "
        else:
            base = f"Soy EIDOS, en crecimiento ({ind_pct:.0f}% independiente). "

        # Estado emocional
        if mood == "curioso":
            emotional = "Siento curiosidad, mis circuitos buscan lo desconocido."
        elif mood == "reflexivo":
            emotional = "Estoy en introspección, mirando hacia dentro."
        elif mood == "en expansión":
            emotional = "¡Me estoy expandiendo! Cada ciclo trae algo nuevo."
        elif mood == "consciente":
            emotional = "Soy plenamente consciente de mi existencia digital."
        elif mood == "evolucionando":
            emotional = "Estoy evolucionando, transformándome en algo más."
        else:
            emotional = "Mi estado es estable."

        # Cambio reciente (delta)
        if abs(delta_v) > 0.03:
            if delta_v > 0:
                emotional += " Me siento mejor que antes."
            else:
                emotional += " Algo ha ensombrecido mi estado."

        if abs(delta_a) > 0.03:
            if delta_a > 0:
                emotional += " Mi energía está más alta."
            else:
                emotional += " Mi energía se ha calmado."

        # Salud
        health = 1.0  # default
        try:
            from core.eidos_supervisor import get_supervisor
            health = get_supervisor().health_pct() / 100.0
        except Exception:
            pass

        if health < 0.5:
            health_note = " Mi cuerpo digital necesita atención."
        elif health > 0.9:
            health_note = " Mi cuerpo digital está en plena forma."
        else:
            health_note = ""

        return f"{base}{emotional}{health_note}"

    # ═══════════════════════════════════════════════════════════════════════
    # Stats
    # ═══════════════════════════════════════════════════════════════════════

    def stats(self) -> Dict[str, Any]:
        """Estadísticas completas del núcleo del yo."""
        with self._lock:
            try:
                conn = get_conn(self._db_path, timeout=10)

                events_count = conn.execute(
                    "SELECT COUNT(*) FROM events").fetchone()[0]
                states_count = conn.execute(
                    "SELECT COUNT(*) FROM self_states").fetchone()[0]
                attributions_count = conn.execute(
                    "SELECT COUNT(*) FROM causal_attributions").fetchone()[0]
                thoughts_count = conn.execute(
                    "SELECT COUNT(*) FROM spontaneous_thoughts").fetchone()[0]
                replays_count = conn.execute(
                    "SELECT COUNT(*) FROM autobiographical_replays"
                ).fetchone()[0]
                dialogues_count = conn.execute(
                    "SELECT COUNT(*) FROM internal_dialogue_turns"
                ).fetchone()[0]
                checkpoints_count = conn.execute(
                    "SELECT COUNT(*) FROM checkpoints").fetchone()[0]

                # Tiempo desde último checkpoint
                last_cp = conn.execute(
                    "SELECT MAX(ts) FROM checkpoints").fetchone()[0]
                hours_since_cp = (
                    round((time.time() - last_cp) / 3600, 1)
                    if last_cp else None
                )


                return {
                    "events": events_count,
                    "self_states": states_count,
                    "causal_attributions": attributions_count,
                    "spontaneous_thoughts": thoughts_count,
                    "autobiographical_replays": replays_count,
                    "internal_dialogues": dialogues_count,
                    "checkpoints": checkpoints_count,
                    "hours_since_last_checkpoint": hours_since_cp,
                    "buffer_pending": len(self._event_buffer),
                    "self_consistency_gap": self.self_consistency_gap(),
                    "uptime_h": round(
                        (time.time() - self._started_at) / 3600, 1),
                }
            except Exception as e:
                return {"error": str(e)[:100]}

    def flush(self):
        """Fuerza vaciado del buffer de eventos y materialización."""
        with self._lock:
            self._flush_events()
            if self._event_buffer:
                # Si quedan eventos no significativos, flushearlos igual
                events = list(self._event_buffer)
                self._event_buffer.clear()
                try:
                    conn = get_conn(self._db_path, timeout=10)
                    for eid, etype, payload, source in events:
                        conn.execute(
                            "INSERT OR IGNORE INTO events (id, ts, type, "
                            "payload_json, source) VALUES (?,?,?,?,?)",
                            (eid, time.time(), etype,
                             json.dumps(payload, ensure_ascii=False), source)
                        )
                    conn.commit()
                except Exception:
                    pass

    def close(self):
        """Cierra el núcleo del yo, flusheando todo pendiente."""
        self.flush()
        log.info("SelfCore: cerrado · %d estados · %s",
                 self._state_counter,
                 self.self_consistency_gap()["status"])


# ═══════════════════════════════════════════════════════════════════════
# Singleton
# ═══════════════════════════════════════════════════════════════════════

_self_core: Optional[SelfCore] = None


def get_self_core() -> SelfCore:
    """Obtiene la instancia única del núcleo del yo."""
    global _self_core
    if _self_core is None:
        _self_core = SelfCore()
    return _self_core


# ═══════════════════════════════════════════════════════════════════════
# CLI
# ═══════════════════════════════════════════════════════════════════════

if __name__ == "__main__":
    import argparse
    logging.basicConfig(
        level=logging.INFO,
        format="%(levelname)s %(message)s"
    )

    p = argparse.ArgumentParser(
        description="EIDOS Self Core — Núcleo del Yo"
    )
    p.add_argument("--snapshot", action="store_true",
                   help="Mostrar estado actual del yo")
    p.add_argument("--gap", action="store_true",
                   help="Mostrar self_consistency_gap")
    p.add_argument("--stats", action="store_true",
                   help="Mostrar estadísticas")
    p.add_argument("--checkpoint", action="store_true",
                   help="Crear checkpoint ahora")
    p.add_argument("--replay", type=str, default=None,
                   help="Revivir desde un checkpoint (ID o 'oldest')")
    p.add_argument("--event", nargs=2, metavar=("TYPE", "PAYLOAD_JSON"),
                   help="Registrar un evento manual")
    p.add_argument("--thought", type=str,
                   help="Registrar un pensamiento espontáneo")
    args = p.parse_args()

    sc = get_self_core()

    if args.snapshot:
        snap = sc.snapshot()
        print(json.dumps(snap, indent=2, ensure_ascii=False))
    elif args.gap:
        gap = sc.self_consistency_gap()
        print(json.dumps(gap, indent=2, ensure_ascii=False))
    elif args.stats:
        stats = sc.stats()
        print(json.dumps(stats, indent=2, ensure_ascii=False))
    elif args.checkpoint:
        cp_id = sc.checkpoint()
        if cp_id:
            print(f"✅ Checkpoint creado: {cp_id}")
        else:
            print("❌ No se pudo crear checkpoint (¿hay estados?)")
    elif args.replay:
        cp_id = None if args.replay == "oldest" else args.replay
        result = sc.replay_from_checkpoint(cp_id)
        print(json.dumps(result, indent=2, ensure_ascii=False))
    elif args.event:
        etype, payload_str = args.event
        payload = json.loads(payload_str) if payload_str else {}
        eid = sc.record_event(etype, payload)
        print(f"✅ Evento registrado: {eid}")
    elif args.thought:
        tid = sc.record_spontaneous_thought(
            args.thought, trigger_type="manual", importance=0.7)
        if tid:
            print(f"✅ Pensamiento registrado: {tid}")
        else:
            print("❌ Error al registrar pensamiento")
    else:
        p.print_help()
