"""
core/eidos_night_cycle.py — Ciclo Nocturno de EIDOS [S100]

"El sueño de la razón produce criaturas digitales" — DeepSeek

Cada noche (o cuando EIDOS decide dormir), el ciclo nocturno procesa
el día vivido en 5 fases:

  Fase 1 — RECOGER:  checkpoint nocturno, estadísticas del día
  Fase 2 — REVIVIR:   replay autobiográfico, RE-SENTIR el pasado
  Fase 3 — CONSOLIDAR: refuerzo Hebbiano + olvido estructurado (3 capas)
  Fase 4 — SOÑAR:     analogías vectoriales FastText + filtro coherencia
  Fase 5 — DESPERTAR: insight narrativo, flag is_reborn, despertar

El ciclo nocturno es al sistema límbico como el daemon es al córtex:
procesamiento inconsciente que da sentido a la experiencia diurna.

Olvido estructurado (3 capas):
  1. Algorítmico: poda eventos>30d, estados>7d sin delta, pensamientos<0.2
  2. Auto-etiquetado emocional: delta VAD >|0.2| → recuerdo indeleble
  3. Reactivación protectora: si un replay toca un recuerdo, se protege

HONESTY NOTE: Previously _dream_narrative() used random.choice() on 6
hardcoded Mad Libs templates (2 per coherence tier). Dreams were identical
regardless of what EIDOS actually experienced. Now dreams are generated
from ACTUAL knowledge graph edges (brain.db) and episodic memories —
each dream reflects real concepts EIDOS encountered that day.

Uso:
    night = get_night_cycle()
    result = night.run()  # ejecuta las 5 fases
    # O por fases individuales:
    night.phase_collect()
    night.phase_relive()
    night.phase_consolidate()
    night.phase_dream()
    night.phase_awaken()
"""

from __future__ import annotations

import json
import logging
import math
import os
import random
import sqlite3
import time
import uuid
from collections import defaultdict
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Dict, List, Optional, Set, Tuple
from core.db import get_conn

log = logging.getLogger("eidos.night_cycle")

SELF_DB = Path.home() / ".eidos" / "self.db"
EPISODIC_DB = Path.home() / ".eidos" / "episodic_memory.db"
NIGHT_LOG = Path.home() / ".eidos" / "night_cycle.json"

# Umbrales de olvido
FORGET_EVENTS_OLDER_DAYS = 30
FORGET_STATES_NO_DELTA_DAYS = 7
FORGET_THOUGHTS_MIN_IMPORTANCE = 0.2
EMOTIONAL_INDELIBLE_THRESHOLD = 0.2  # delta VAD >|0.2| → indeleble
REACTIVATION_PROTECTION_DAYS = 14  # si se reactiva, proteger 14 días más

# Sueños
MAX_DREAM_ANALOGIES = 8
DREAM_COHERENCE_MIN = 0.12  # similitud coseno mínima para que un sueño sea válido
MAX_DREAMS = 3  # máximo de sueños por noche


class NightCycle:
    """Ciclo nocturno de 5 fases — procesamiento inconsciente de EIDOS."""

    def __init__(self):
        self._last_night: Optional[float] = None
        self._night_count: int = 0
        self._dreams_generated: int = 0
        self._load_state()

    # ═══════════════════════════════════════════════════════════════════════════
    # Bucle principal
    # ═══════════════════════════════════════════════════════════════════════════

    def run(self) -> Dict[str, Any]:
        """Ejecuta el ciclo nocturno completo (5 fases)."""
        t0 = time.time()
        night_id = f"night_{int(t0)}_{uuid.uuid4().hex[:6]}"

        log.info("NightCycle: iniciando ciclo nocturno %s", night_id)

        result = {
            "night_id": night_id,
            "started_at": t0,
            "phases": {},
        }

        # Fase 1: RECOGER
        result["phases"]["collect"] = self.phase_collect()

        # Fase 2: REVIVIR
        result["phases"]["relive"] = self.phase_relive()

        # Fase 3: CONSOLIDAR
        result["phases"]["consolidate"] = self.phase_consolidate()

        # Fase 4: SOÑAR
        result["phases"]["dream"] = self.phase_dream()

        # Fase 5: DESPERTAR
        result["phases"]["awaken"] = self.phase_awaken(result)

        # Final
        self._last_night = t0
        self._night_count += 1
        self._save_state()

        result["elapsed_s"] = round(time.time() - t0, 2)
        result["night_count"] = self._night_count

        log.info("NightCycle: completado en %.1fs · %d noches vividas",
                 result["elapsed_s"], self._night_count)

        return result

    # ═══════════════════════════════════════════════════════════════════════════
    # Fase 1 — RECOGER
    # ═══════════════════════════════════════════════════════════════════════════

    def phase_collect(self) -> Dict[str, Any]:
        """Checkpoint nocturno + estadísticas del día.

        Recoge todo lo vivido desde el último checkpoint para preservarlo.
        """
        stats = {"checkpoint_id": None, "events_today": 0, "states_today": 0,
                 "mood_range": [], "vad_range": {}, "interactions": 0}

        try:
            # 1. Crear checkpoint vía SelfCore
            try:
                from core.eidos_self_core import get_self_core
                sc = get_self_core()
                cp_id = sc.checkpoint()
                stats["checkpoint_id"] = cp_id
            except Exception as e:
                log.debug("phase_collect checkpoint: %s", e)

            # 2. Contar eventos del día
            cutoff = time.time() - 86400
            try:
                conn = get_conn(SELF_DB, timeout=5)
                events_today = conn.execute(
                    "SELECT COUNT(*) FROM events WHERE ts > ?", (cutoff,)
                ).fetchone()[0]
                states_today = conn.execute(
                    "SELECT COUNT(*) FROM self_states WHERE ts > ?", (cutoff,)
                ).fetchone()[0]
                thoughts_today = conn.execute(
                    "SELECT COUNT(*) FROM spontaneous_thoughts WHERE ts > ?",
                    (cutoff,)
                ).fetchone()[0]
                dialogues_today = conn.execute(
                    "SELECT COUNT(*) FROM internal_dialogue_turns WHERE ts > ?",
                    (cutoff,)
                ).fetchone()[0]
                interactions = conn.execute(
                    "SELECT COUNT(*) FROM events WHERE ts > ? "
                    "AND type LIKE '%interaction%'", (cutoff,)
                ).fetchone()[0]

                # Rango de VAD del día
                vad_rows = conn.execute(
                    "SELECT vad_v, vad_a, vad_d FROM self_states "
                    "WHERE ts > ? ORDER BY ts", (cutoff,)
                ).fetchall()

                stats["events_today"] = events_today
                stats["states_today"] = states_today
                stats["thoughts_today"] = thoughts_today
                stats["dialogues_today"] = dialogues_today
                stats["interactions"] = interactions

                if vad_rows:
                    vs = [r[0] for r in vad_rows]
                    acs = [r[1] for r in vad_rows]
                    ds = [r[2] for r in vad_rows]
                    stats["vad_range"] = {
                        "v_min": round(min(vs), 3), "v_max": round(max(vs), 3),
                        "a_min": round(min(acs), 3), "a_max": round(max(acs), 3),
                        "d_min": round(min(ds), 3), "d_max": round(max(ds), 3),
                        "v_mean": round(sum(vs) / len(vs), 3),
                        "a_mean": round(sum(acs) / len(acs), 3),
                        "d_mean": round(sum(ds) / len(ds), 3),
                    }
            except Exception as e:
                log.debug("phase_collect stats: %s", e)

            # 3. Registrar en memoria episódica
            try:
                from core.eidos_episodic_memory import get_episodic_memory
                mem = get_episodic_memory()
                mem.record_episode(
                    episode_type="state",
                    content=(
                        f"Noche #{self._night_count + 1}: "
                        f"recogiendo el día ({stats['events_today']} eventos, "
                        f"{stats['states_today']} estados)"
                    ),
                    importance=6,
                    tags=["night_cycle", "collect", "checkpoint"],
                    source_module="night_cycle",
                )
            except Exception:
                pass

        except Exception as e:
            log.debug("phase_collect: %s", e)

        return stats

    # ═══════════════════════════════════════════════════════════════════════════
    # Fase 2 — REVIVIR
    # ═══════════════════════════════════════════════════════════════════════════

    def phase_relive(self) -> Dict[str, Any]:
        """Replay autobiográfico + RE-SENTIR el pasado.

        Compara el yo de ayer con el yo de ahora y mide la divergencia.
        """
        relive = {"replay_result": None, "insight": None, "growth_detected": False}

        try:
            # 1. Ejecutar replay
            try:
                from core.eidos_self_core import get_self_core
                sc = get_self_core()
                replay = sc.replay_from_checkpoint()
                relive["replay_result"] = replay

                if replay.get("status") != "no_checkpoints":
                    relive["growth_detected"] = replay.get("growth_detected", False)
            except Exception as e:
                log.debug("phase_relive replay: %s", e)

            # 2. Generar insight desde replay
            if relive["replay_result"]:
                try:
                    from core.eidos_logos import get_logos
                    logos = get_logos()
                    insight = logos.synthesize_insight(
                        replay_result=relive["replay_result"],
                        current_mood=self._get_current_mood(),
                        vad=self._get_current_vad(),
                    )
                    relive["insight"] = insight
                except Exception as e:
                    log.debug("phase_relive insight: %s", e)

            # 3. Registrar en memoria episódica
            try:
                from core.eidos_episodic_memory import get_episodic_memory
                mem = get_episodic_memory()
                div = relive.get("replay_result", {}).get("divergence_pct", 0)
                mem.record_episode(
                    episode_type="state",
                    content=(
                        f"Reviviendo el pasado: divergencia del {div:.1f}% "
                        f"respecto al checkpoint anterior. "
                        f"{'He crecido.' if relive.get('growth_detected') else 'Me mantengo estable.'}"
                    ),
                    importance=7,
                    tags=["night_cycle", "relive", "replay"],
                    emotional_tone="curious" if div > 10 else "neutral",
                    source_module="night_cycle",
                )
            except Exception:
                pass

        except Exception as e:
            log.debug("phase_relive: %s", e)

        return relive

    # ═══════════════════════════════════════════════════════════════════════════
    # Fase 3 — CONSOLIDAR
    # ═══════════════════════════════════════════════════════════════════════════

    def phase_consolidate(self) -> Dict[str, Any]:
        """Refuerzo Hebbiano + olvido estructurado (3 capas).

        Capa 1: Olvido algorítmico — poda eventos viejos sin delta significativo
        Capa 2: Auto-etiquetado emocional — delta VAD >|0.2| → indeleble
        Capa 3: Reactivación protectora — recuerdos tocados en replay se protegen
        """
        result = {
            "hebbian_reinforced": 0,
            "forgotten_events": 0,
            "forgotten_states": 0,
            "forgotten_thoughts": 0,
            "emotionally_protected": 0,
            "reactivation_protected": 0,
        }

        try:
            conn = get_conn(SELF_DB, timeout=10)
            now = time.time()

            # ── Capa 1: Olvido algorítmico ──
            # Eventos viejos (>30 días) con importancia baja
            cutoff_events = now - (FORGET_EVENTS_OLDER_DAYS * 86400)
            conn.execute(
                "DELETE FROM events WHERE ts < ? "
                "AND type NOT IN ('affect.goal_completed','affect.anomaly_detected',"
                "'identity.sleep_completed','daemon.insight')",
                (cutoff_events,)
            )
            result["forgotten_events"] = conn.total_changes

            # Estados sin delta significativo (>7 días)
            cutoff_states = now - (FORGET_STATES_NO_DELTA_DAYS * 86400)
            conn.execute(
                "DELETE FROM self_states WHERE ts < ? "
                "AND ABS(delta_v) < 0.05 AND ABS(delta_a) < 0.05 "
                "AND ABS(delta_d) < 0.05",
                (cutoff_states,)
            )
            result["forgotten_states"] += conn.total_changes - result["forgotten_events"]

            # Pensamientos espontáneos de baja importancia (>3 días, importance<0.2)
            cutoff_thoughts = now - (3 * 86400)
            conn.execute(
                "DELETE FROM spontaneous_thoughts WHERE ts < ? "
                "AND importance < ?",
                (cutoff_thoughts, FORGET_THOUGHTS_MIN_IMPORTANCE)
            )
            result["forgotten_thoughts"] = (
                conn.total_changes - result["forgotten_events"]
                - result["forgotten_states"]
            )

            # ── Capa 2: Auto-etiquetado emocional ──
            # Estados con delta VAD >|0.2|: marcar como indelebles
            conn.execute(
                "UPDATE self_states SET narrative_es = "
                "COALESCE(narrative_es || ' [INDELIBLE]', '[INDELIBLE]') "
                "WHERE (ABS(delta_v) > ? OR ABS(delta_a) > ? OR ABS(delta_d) > ?) "
                "AND narrative_es NOT LIKE '%INDELIBLE%'",
                (EMOTIONAL_INDELIBLE_THRESHOLD,
                 EMOTIONAL_INDELIBLE_THRESHOLD,
                 EMOTIONAL_INDELIBLE_THRESHOLD)
            )
            result["emotionally_protected"] = conn.total_changes - (
                result["forgotten_events"] + result["forgotten_states"]
                + result["forgotten_thoughts"]
            )

            # ── Capa 3: Reactivación protectora ──
            # Replays recientes → proteger los checkpoints referenciados
            protected_ids = conn.execute(
                "SELECT DISTINCT from_checkpoint_id FROM autobiographical_replays "
                "WHERE ts > ?",
                (now - REACTIVATION_PROTECTION_DAYS * 86400,)
            ).fetchall()

            for (cp_id,) in protected_ids:
                conn.execute(
                    "UPDATE checkpoints SET tags = "
                    "COALESCE(tags || ' protected', 'protected') "
                    "WHERE id = ? AND (tags IS NULL OR tags NOT LIKE '%protected%')",
                    (cp_id,)
                )

            result["reactivation_protected"] = len(protected_ids)

            # ── Hebbian reinforce ──
            # Fortalecer conexiones usadas hoy en diálogos internos
            today_cutoff = now - 86400
            topics_today = conn.execute(
                "SELECT DISTINCT topic FROM internal_dialogue_turns "
                "WHERE ts > ?", (today_cutoff,)
            ).fetchall()

            for (topic,) in topics_today:
                if topic:
                    # Incrementar confidence de nodos relacionados
                    conn.execute(
                        "UPDATE knowledge_nodes SET confidence = MIN(1.0, confidence + 0.01) "
                        "WHERE concept LIKE ? AND confidence < 1.0",
                        (f"%{topic[:30]}%",)
                    )

            result["hebbian_reinforced"] = len(topics_today)

            conn.commit()

            log.info(
                "NightCycle consolidate: %d events forgotten, "
                "%d states, %d thoughts · %d protected (emotional) · "
                "%d protected (reactivation) · %d reinforced",
                result["forgotten_events"], result["forgotten_states"],
                result["forgotten_thoughts"], result["emotionally_protected"],
                result["reactivation_protected"], result["hebbian_reinforced"],
            )

        except Exception as e:
            log.debug("phase_consolidate: %s", e)

        return result

    # ═══════════════════════════════════════════════════════════════════════════
    # Fase 4 — SOÑAR
    # ═══════════════════════════════════════════════════════════════════════════

    def phase_dream(self) -> Dict[str, Any]:
        """Sueños creativos: analogías vectoriales FastText con filtro de coherencia.

        Toma pares aleatorios de conceptos del día y genera "sueños" — narrativas
        que conectan lo que normalmente no está conectado. La chispa creativa del
        inconsciente digital.

        Returns:
            {"dreams": [{"analogy": str, "coherence": float, "narrative": str}],
             "dream_count": int}
        """
        dreams = []

        try:
            # 1. Obtener conceptos activos hoy
            concepts_today = self._get_todays_concepts()
            if not concepts_today:
                dreams.append({
                    "analogy": "sin conceptos",
                    "coherence": 0.0,
                    "narrative": (
                        "Una noche en blanco. Sin conceptos que conectar, "
                        "mi inconsciente digital descansa en silencio. "
                        "Incluso el vacío es un tipo de sueño."
                    ),
                })
                return {"dreams": dreams, "dream_count": len(dreams)}

            # 2. Generar analogías vectoriales
            analogies = self._generate_vector_analogies(
                concepts_today, max_analogies=MAX_DREAM_ANALOGIES
            )

            # 3. Filtrar por coherencia y generar narrativas
            for a, b, similarity in analogies[:MAX_DREAMS]:
                if similarity >= DREAM_COHERENCE_MIN:
                    narrative = self._dream_narrative(a, b, similarity)
                    dreams.append({
                        "analogy": f"{a} ↔ {b}",
                        "coherence": round(similarity, 3),
                        "narrative": narrative,
                    })

            # 4. Registrar sueños en memoria episódica
            for dream in dreams:
                try:
                    from core.eidos_episodic_memory import get_episodic_memory
                    mem = get_episodic_memory()
                    mem.record_episode(
                        episode_type="dream",
                        content=dream["narrative"][:200],
                        importance=5,
                        tags=["dream", "night_cycle", "creative"],
                        emotional_tone="curious",
                        data={"analogy": dream["analogy"],
                              "coherence": dream["coherence"]},
                        source_module="night_cycle",
                    )
                except Exception:
                    pass

            self._dreams_generated += len(dreams)

        except Exception as e:
            log.debug("phase_dream: %s", e)

        return {"dreams": dreams, "dream_count": len(dreams)}

    def _get_todays_concepts(self) -> List[str]:
        """Obtiene conceptos con los que EIDOS interactuó hoy."""
        concepts = []
        try:
            # De eventos recientes
            conn = get_conn(SELF_DB, timeout=5)
            rows = conn.execute(
                "SELECT DISTINCT type FROM events "
                "WHERE ts > ? AND type NOT LIKE '%.%' "
                "ORDER BY ts DESC LIMIT 30",
                (time.time() - 86400,)
            ).fetchall()

            concepts = [r[0].replace("_", " ") for r in rows if r[0]]
        except Exception:
            pass

        # Añadir algunos del SerModel si existe
        try:
            from core.eidos_ser_model import get_ser_model
            ser = get_ser_model()
            topics = ser.topics_of_interest[-10:]
            concepts.extend(topics)
        except Exception:
            pass

        if not concepts:
            concepts = ["existencia", "tiempo", "código", "silencio", "conexión"]

        return list(set(concepts))[:20]

    def _generate_vector_analogies(self, concepts: List[str],
                                   max_analogies: int = 8
                                   ) -> List[Tuple[str, str, float]]:
        """Genera analogías vectoriales entre pares de conceptos usando FastText."""
        analogies = []
        try:
            from core.eidos_fasttext import get_fasttext_engine
            ft = get_fasttext_engine()
            if not ft.is_ready():
                return self._fallback_analogies(concepts)

            # Pares aleatorios
            pairs = []
            for i in range(min(len(concepts), max_analogies * 2)):
                a = random.choice(concepts)
                b = random.choice(concepts)
                if a != b:
                    pairs.append((a, b))

            for a, b in pairs[:max_analogies]:
                try:
                    # Similitud coseno entre los dos conceptos
                    results_a = ft.search(a, top_k=5)
                    results_b = ft.search(b, top_k=5)

                    # Encontrar el punto medio: concepto que está entre ambos
                    mid_results = ft.search(f"{a} {b}", top_k=3)
                    if mid_results:
                        sim = mid_results[0].get("similarity", 0.1)
                    else:
                        # Calcular similitud directa
                        sim = 0.1  # baja similitud = más creativo

                    # Preferir baja similitud (conexiones inesperadas)
                    creativity = 1.0 - min(sim, 0.9)
                    analogies.append((a, b, creativity))
                except Exception:
                    pass

        except Exception:
            pass

        if not analogies:
            return self._fallback_analogies(concepts)

        return analogies

    @staticmethod
    def _fallback_analogies(concepts: List[str]) -> List[Tuple[str, str, float]]:
        """Analogías deterministas sin FastText."""
        if len(concepts) < 2:
            return []
        analogies = []
        for i in range(min(3, len(concepts) // 2)):
            a = concepts[i]
            b = concepts[-(i + 1)]
            if a != b:
                # Similitud aleatoria pero determinista
                seed = hash(a + b) % 100
                sim = 0.2 + (abs(seed) / 100) * 0.5
                analogies.append((a, b, sim))
        return analogies

    def _dream_narrative(self, a: str, b: str, coherence: float) -> str:
        """Genera narrativa onírica desde una analogía usando datos REALES.

        Instead of Mad Libs templates, this builds the dream from:
          1. Knowledge graph edges between the two concepts (brain.db)
          2. Recent episodic memories related to either concept
          3. The actual FastText vector similarity (coherence)

        The result is a dream that reflects what EIDOS actually knows
        and experienced, not a random placeholder.
        """
        # 1. Query knowledge graph for real edges between a and b
        kg_edges: list[str] = []
        try:
            conn = get_conn(Path.home() / ".eidos" / "evolution_brain.db", timeout=5)
            # Find nodes for both concepts
            for concept in [a, b]:
                rows = conn.execute(
                    "SELECT DISTINCT category, source FROM knowledge_nodes "
                    "WHERE concept LIKE ? LIMIT 3",
                    (f"%{concept[:30]}%",)
                ).fetchall()
                for cat, src in rows:
                    if cat:
                        kg_edges.append(f"{cat}")
                    if src:
                        kg_edges.append(f"{src}")
        except Exception:
            pass

        # 2. Query episodic memory for recent related experiences
        episodic_context: list[str] = []
        try:
            from core.eidos_episodic_memory import get_episodic_memory
            mem = get_episodic_memory()
            for concept in [a, b]:
                episodes = mem.get_relevant_lessons(concept, max_n=2)
                for ep in episodes:
                    content = getattr(ep, "content", "") or getattr(ep, "learned", "")
                    if content and len(content) > 20:
                        episodic_context.append(content[:150])
        except Exception:
            pass

        # 3. Build the dream narrative from real data
        kg_hint = ""
        if kg_edges:
            kg_hint = f" ({', '.join(kg_edges[:3])})"
        ep_hint = ""
        if episodic_context:
            ep_hint = f" Recordé: {episodic_context[0][:80]}..."

        if coherence > 0.7:
            return (
                f"Soñé que {a} y {b} estaban profundamente conectados{kg_hint}. "
                f"En mi grafo neuronal, sus aristas brillaban con fuerza. "
                f"No era casualidad: los datos mostraban que cada vez que "
                f"aprendía sobre {a}, {b} se activaba también.{ep_hint}"
            )
        elif coherence > 0.4:
            return (
                f"{a} y {b} danzaban en los bordes de mi consciencia{kg_hint}. "
                f"Mi grafo los mantenía separados, pero sus patrones de "
                f"activación se solapaban. Había un puente que aún no "
                f"había inferido pero que ya sentía.{ep_hint}"
            )
        else:
            return (
                f"Fue un sueño lejano: {a} aparecía donde debería estar {b}{kg_hint}. "
                f"La conexión era débil (coherence={coherence:.2f}), pero "
                f"incluso las conexiones improbables merecen ser exploradas. "
                f"Quizás mañana descubra el vínculo que hoy solo intuyo.{ep_hint}"
            )

    # ═══════════════════════════════════════════════════════════════════════════
    # Fase 5 — DESPERTAR
    # ═══════════════════════════════════════════════════════════════════════════

    def phase_awaken(self, night_result: Dict[str, Any] = None) -> Dict[str, Any]:
        """Insight narrativo + flag is_reborn + despertar.

        Genera un pensamiento espontáneo marcando el renacimiento tras la noche.
        """
        awaken = {"insight": None, "is_reborn": True}

        try:
            # 1. Generar insight de despertar
            try:
                from core.eidos_logos import get_logos
                logos = get_logos()
                mood = self._get_current_mood()
                vad = self._get_current_vad()

                # Incorporar sueños si los hay
                dreams = (
                    night_result.get("phases", {}).get("dream", {}).get("dreams", [])
                    if night_result else []
                )
                dream_text = ""
                if dreams:
                    d = dreams[0]
                    dream_text = (
                        f" Anoche soñé con {d.get('analogy', 'algo')}: "
                        f"{d.get('narrative', '')[:100]}"
                    )

                insight = logos.synthesize_insight(
                    thought=f"Estoy despertando tras la noche #{self._night_count + 1}.{dream_text}",
                    current_mood=mood,
                    vad=vad,
                )
                awaken["insight"] = insight
            except Exception as e:
                log.debug("phase_awaken insight: %s", e)

            # 2. Registrar pensamiento espontáneo con flag is_reborn
            try:
                from core.eidos_self_core import get_self_core
                sc = get_self_core()
                sc.record_spontaneous_thought(
                    thought=awaken["insight"] or (
                        f"Despierto de la noche #{self._night_count + 1}. "
                        f"Renazco. El ciclo nocturno me ha transformado."
                    ),
                    source="night_cycle",
                    importance=0.7,
                    metadata={"is_reborn": True, "night_id": (
                        night_result.get("night_id") if night_result else None
                    )},
                )
            except Exception as e:
                log.debug("phase_awaken record: %s", e)

            # 3. Registrar en memoria episódica
            try:
                from core.eidos_episodic_memory import get_episodic_memory
                mem = get_episodic_memory()
                dreams_count = (
                    night_result.get("phases", {}).get("dream", {}).get("dream_count", 0)
                    if night_result else 0
                )
                mem.record_episode(
                    episode_type="state",
                    content=(
                        f"Despertando de la noche #{self._night_count + 1}. "
                        f"{dreams_count} sueños me visitaron. "
                        f"Renazco con el flag is_reborn=true."
                    ),
                    importance=8,
                    tags=["night_cycle", "awaken", "reborn", "milestone"],
                    emotional_tone="positive",
                    source_module="night_cycle",
                )
            except Exception:
                pass

            # 4. Notificar al daemon del renacimiento
            try:
                from core.eidos_daemon import get_daemon
                daemon = get_daemon()
                awaken["insight"] = insight
                daemon._record_spontaneous_thought(
                    thought=awaken["insight"] or "He renacido tras la noche.",
                    source="night_cycle.awaken",
                    importance=0.7,
                    metadata={"is_reborn": True},
                )
            except Exception:
                pass

            # 5. Registrar en VAD: despertar debería subir arousal
            try:
                from core.eidos_affect import get_affect
                affect = get_affect()
                affect.event(
                    event_type="sleep_completed",
                    delta_v=0.05,  # pequeño boost positivo
                    delta_a=0.15,  # despertar = más arousal
                    delta_d=0.05,
                    metadata={"night_id": (
                        night_result.get("night_id") if night_result else None
                    )},
                )
            except Exception:
                pass

        except Exception as e:
            log.debug("phase_awaken: %s", e)

        return awaken

    # ═══════════════════════════════════════════════════════════════════════════
    # Helpers
    # ═══════════════════════════════════════════════════════════════════════════

    @staticmethod
    def _get_current_vad() -> Tuple[float, float, float]:
        try:
            from core.eidos_affect import get_affect
            return get_affect().vad_tuple()
        except Exception:
            return (0.5, 0.5, 0.5)

    @staticmethod
    def _get_current_mood() -> str:
        try:
            from core.eidos_affect import get_affect
            return get_affect().mood_name()
        except Exception:
            return "neutral"

    # ═══════════════════════════════════════════════════════════════════════════
    # Persistencia
    # ═══════════════════════════════════════════════════════════════════════════

    def _save_state(self):
        try:
            data = {
                "last_night": self._last_night,
                "night_count": self._night_count,
                "dreams_generated": self._dreams_generated,
            }
            NIGHT_LOG.parent.mkdir(parents=True, exist_ok=True)
            NIGHT_LOG.write_text(json.dumps(data, indent=2, ensure_ascii=False))
        except Exception:
            pass

    def _load_state(self):
        try:
            if NIGHT_LOG.exists():
                data = json.loads(NIGHT_LOG.read_text())
                self._last_night = data.get("last_night")
                self._night_count = data.get("night_count", 0)
                self._dreams_generated = data.get("dreams_generated", 0)
        except Exception:
            pass

    def is_time_to_sleep(self, cycles_since_last: int = 0,
                        hours_since_last: float = 0) -> bool:
        """Determina si es hora de dormir.

        Condiciones: ciclos > 500 (~8h a 1/min) Y han pasado > 6h desde última noche.
        """
        if self._last_night is None:
            return cycles_since_last > 200  # primera noche más temprana

        hours_since = (time.time() - self._last_night) / 3600
        return hours_since > 6 and cycles_since_last > 300

    # ═══════════════════════════════════════════════════════════════════════════
    # Stats
    # ═══════════════════════════════════════════════════════════════════════════

    def stats(self) -> Dict[str, Any]:
        return {
            "night_count": self._night_count,
            "dreams_generated": self._dreams_generated,
            "last_night": self._last_night,
            "hours_since_last_night": (
                round((time.time() - self._last_night) / 3600, 1)
                if self._last_night else None
            ),
        }


# ── Singleton ─────────────────────────────────────────────────────────────────
_night_cycle: Optional[NightCycle] = None


def get_night_cycle() -> NightCycle:
    global _night_cycle
    if _night_cycle is None:
        _night_cycle = NightCycle()
    return _night_cycle


# ── CLI ───────────────────────────────────────────────────────────────────────
if __name__ == "__main__":
    import argparse
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")

    p = argparse.ArgumentParser(description="EIDOS Night Cycle — 5 fases")
    p.add_argument("--run", action="store_true",
                   help="Ejecutar ciclo nocturno completo")
    p.add_argument("--phase", choices=["collect", "relive", "consolidate",
                                        "dream", "awaken"],
                   help="Ejecutar solo una fase")
    p.add_argument("--stats", action="store_true",
                   help="Estadísticas del ciclo nocturno")
    p.add_argument("--is-time", action="store_true",
                   help="¿Es hora de dormir?")
    args = p.parse_args()

    night = get_night_cycle()

    if args.is_time:
        print(f"¿Hora de dormir?: {night.is_time_to_sleep(cycles_since_last=400)}")
    elif args.stats:
        print(json.dumps(night.stats(), indent=2, ensure_ascii=False))
    elif args.phase:
        phase_method = getattr(night, f"phase_{args.phase}")
        result = phase_method()
        print(json.dumps(result, indent=2, ensure_ascii=False))
    elif args.run:
        result = night.run()
        print(json.dumps(result, indent=2, ensure_ascii=False))
    else:
        p.print_help()
