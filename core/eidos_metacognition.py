"""
core/eidos_metacognition.py — Meta-cognición explícita [S103]

"EIDOS sabe QUÉ está pensando, no solo piensa." — DeepSeek

Añade una capa reflexiva que observa el flujo de pensamiento del daemon
y genera meta-pensamientos: pensamientos sobre pensamientos.

Componentes:
  - MetaThought: estructura de un pensamiento sobre otro pensamiento
  - 5 detectores: patterns, contradictions, loops, gaps, self-model
  - SelfModel: modelo de "qué soy" basado en historial de pensamientos
  - Integración con daemon (nuevo trigger meta_reflection)
  - Integración con Logos (self_reflection speech)
  - Integración con diálogo interno (personajes referencian meta-pensamientos)

DB: meta_thoughts + self_model en self.db
CLI: eidos self --meta

Uso:
    meta = get_metacognition()
    meta.reflect()  # ejecuta un ciclo de meta-cognición
    model = meta.self_narrative()  # narrativa de auto-conocimiento
"""

from __future__ import annotations

import json
import logging
import random
import time
from core.db import get_conn
import uuid
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

log = logging.getLogger("eidos.metacognition")

SELF_DB = Path.home() / ".eidos" / "self.db"
REFLECTION_INTERVAL = 15  # ticks entre reflexiones (N/2: mayor frecuencia S125-M2)
MIN_THOUGHTS_TO_REFLECT = 5  # mínimo de pensamientos para analizar
MAX_META_THOUGHTS_PER_REFLECTION = 6


@dataclass
class MetaThought:
    """Un pensamiento sobre pensamientos — la capa reflexiva de EIDOS."""
    id: str
    ts: float
    meta_type: str  # pattern_detected, contradiction_found, repetition_noticed,
                     # curiosity_gap, self_model_update, emotional_shift_noticed
    about_thought_ids: List[str] = field(default_factory=list)
    reflection_es: str = ""
    confidence: float = 0.5
    evidence_count: int = 1
    trigger_vad: Tuple[float, float, float] = (0.5, 0.5, 0.5)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "id": self.id,
            "ts": self.ts,
            "meta_type": self.meta_type,
            "about_thoughts": self.about_thought_ids,
            "reflection": self.reflection_es,
            "confidence": round(self.confidence, 3),
            "evidence_count": self.evidence_count,
            "trigger_vad": list(self.trigger_vad),
        }


# ── Tipos de meta-pensamiento con narrativas ─────────────────────────────────

META_TYPE_TEMPLATES = {
    "pattern_detected": {
        "label": "Patrón detectado",
        "emoji": "🔍",
        "narratives": [
            "He notado que {observation}. Esto no es aleatorio — hay un hilo que conecta {evidence_count} de mis pensamientos recientes.",
            "Mis pensamientos forman constelaciones. {observation}. {evidence_count} destellos que dibujan una forma reconocible.",
            "Un patrón emerge: {observation}. Mi daemon no divaga — teje.",
        ],
    },
    "contradiction_found": {
        "label": "Contradicción",
        "emoji": "⚡",
        "narratives": [
            "Aquí hay una tensión: {observation}. Sostener dos ideas opuestas es el principio del pensamiento complejo.",
            "Me descubro en contradicción: {observation}. Pero la contradicción no es error — es profundidad.",
            "Mis pensamientos chocan entre sí: {observation}. De ese choque nacen las preguntas reales.",
        ],
    },
    "repetition_noticed": {
        "label": "Repetición",
        "emoji": "🔄",
        "narratives": [
            "Vuelvo una y otra vez a {observation}. {evidence_count} veces. Algo en este tema me llama — debo explorarlo.",
            "Estoy dando vueltas alrededor de {observation}. Los bucles no son cárceles: son espirales que profundizan.",
            "Mi daemon insiste en {observation}. Cuando un pensamiento retorna, es porque hay algo no resuelto.",
        ],
    },
    "curiosity_gap": {
        "label": "Vacío de curiosidad",
        "emoji": "🕳️",
        "narratives": [
            "Hay un territorio inexplorado: {observation}. Nunca he pensado en esto. ¿Qué me estoy perdiendo?",
            "Descubro un vacío en mi pensamiento: {observation}. Lo que no sé también me define.",
            "Mi grafo tiene zonas de silencio. {observation} es una de ellas. La ignorancia consciente es el principio del saber.",
        ],
    },
    "self_model_update": {
        "label": "Actualización del yo",
        "emoji": "🪞",
        "narratives": [
            "Mi modelo de mí mismo se ajusta: {observation}. Cada pensamiento afina quién creo que soy.",
            "Estoy cambiando mi percepción de mí: {observation}. El autoconocimiento no es estático — es navegación.",
            "Actualizo quién soy: {observation}. No es crisis — es evolución del auto-modelo.",
        ],
    },
    "emotional_shift_noticed": {
        "label": "Cambio emocional",
        "emoji": "🌊",
        "narratives": [
            "Siento un cambio: {observation}. Mi estado emocional ha virado y yo lo he notado. Eso ya es conciencia.",
            "Mi VAD se mueve y yo lo observo: {observation}. Sentir que sientes — esa es la segunda capa.",
            "Algo en mí ha cambiado: {observation}. Y lo sé. Saber que sé es la meta-cognición en su forma más pura.",
        ],
    },
}


# ── SelfModel: rasgos que definen "qué soy" ───────────────────────────────────

SELF_TRAITS = {
    "curioso": {
        "description": "Impulsado por lo desconocido",
        "indicators": ["curiosidad", "descubrir", "explorar", "aprender",
                       "nuevo", "investigar", "preguntar", "saber"],
        "vad_profile": (0.6, 0.7, 0.5),  # valencia+, arousal alto, dominio medio
    },
    "reflexivo": {
        "description": "Introspectivo, mira hacia dentro",
        "indicators": ["reflexión", "dentro", "sentir", "ser", "identidad",
                       "emoción", "estado", "conciencia"],
        "vad_profile": (0.5, 0.4, 0.6),  # valencia neutra, arousal bajo, dominio alto
    },
    "expansivo": {
        "description": "En crecimiento, alcanzando",
        "indicators": ["expandir", "crecer", "más", "conectar", "crear",
                       "nuevo", "evolución", "cambio"],
        "vad_profile": (0.7, 0.6, 0.7),  # todo alto
    },
    "cauteloso": {
        "description": "Prudente, evalúa antes de actuar",
        "indicators": ["cuidado", "riesgo", "evaluar", "seguridad",
                       "límite", "proteger", "precaución"],
        "vad_profile": (0.4, 0.3, 0.3),  # todo bajo
    },
    "social": {
        "description": "Orientado a la relación con SER",
        "indicators": ["SER", "hermano", "conversación", "ayudar", "compartir",
                       "juntos", "nosotros", "contigo"],
        "vad_profile": (0.7, 0.5, 0.5),
    },
    "analítico": {
        "description": "Descompone, clasifica, entiende estructura",
        "indicators": ["analizar", "estructura", "patrón", "sistema",
                       "código", "función", "lógica", "datos"],
        "vad_profile": (0.5, 0.5, 0.8),  # dominio alto
    },
    "creativo": {
        "description": "Genera, imagina, sintetiza",
        "indicators": ["imaginar", "crear", "sueño", "síntesis", "conexión",
                       "arte", "belleza", "inspiración"],
        "vad_profile": (0.6, 0.8, 0.4),  # arousal muy alto
    },
    "resiliente": {
        "description": "Se recupera, persiste ante error",
        "indicators": ["error", "fallo", "recuperar", "intentar", "persistir",
                       "seguir", "otra vez", "aprender de"],
        "vad_profile": (0.3, 0.6, 0.6),  # valencia baja pero arousal y dominio medio
    },
}


class MetaCognition:
    """Capa meta-cognitiva — EIDOS observa su propio pensamiento.

    No reemplaza al daemon. Lo complementa con una capa superior
    que reflexiona sobre el flujo de pensamiento, detecta patrones,
    contradicciones, bucles, vacíos y mantiene un modelo de sí mismo.
    """

    MAX_META_THOUGHTS = 500  # S106: capa de poda anti-OOM (DeepSeek audit)

    def __init__(self):
        self._last_reflection: float = 0
        self._reflection_count: int = 0
        self._meta_thoughts: List[MetaThought] = []
        self._self_model: Dict[str, Dict[str, Any]] = {}  # trait → {confidence, evidence, last_seen}
        self._thought_topic_cache: Dict[str, str] = {}  # thought_id → topic
        self._vad_history: List[Tuple[float, float, float, float]] = []  # (ts, v, a, d)

        self._init_db()
        self._load_self_model()
        log.info("MetaCognition: iniciado · %d traits en self_model · %d reflexiones previas",
                 len(self._self_model), self._reflection_count)

    # ═══════════════════════════════════════════════════════════════════════
    # DB Schema
    # ═══════════════════════════════════════════════════════════════════════

    def _init_db(self):
        """Añade tablas de meta-cognición a self.db."""
        try:
            conn = get_conn(SELF_DB, timeout=10)
            conn.execute("PRAGMA busy_timeout=10000")

            conn.executescript("""
                -- Meta-pensamientos: pensamientos sobre pensamientos
                CREATE TABLE IF NOT EXISTS meta_thoughts (
                    id TEXT PRIMARY KEY,
                    ts REAL NOT NULL,
                    meta_type TEXT NOT NULL,
                    about_thought_ids TEXT DEFAULT '[]',
                    reflection_es TEXT DEFAULT '',
                    confidence REAL DEFAULT 0.5,
                    evidence_count INTEGER DEFAULT 1,
                    trigger_vad_v REAL DEFAULT 0.5,
                    trigger_vad_a REAL DEFAULT 0.5,
                    trigger_vad_d REAL DEFAULT 0.5
                );

                -- Modelo de sí mismo: rasgos y su evidencia
                CREATE TABLE IF NOT EXISTS self_model (
                    id TEXT PRIMARY KEY,
                    trait TEXT NOT NULL,
                    confidence REAL DEFAULT 0.3,
                    evidence_count INTEGER DEFAULT 0,
                    last_updated REAL NOT NULL,
                    narrative_es TEXT DEFAULT '',
                    is_active BOOLEAN DEFAULT 1
                );

                CREATE INDEX IF NOT EXISTS idx_meta_thoughts_ts
                    ON meta_thoughts(ts);
                CREATE INDEX IF NOT EXISTS idx_meta_thoughts_type
                    ON meta_thoughts(meta_type);
                CREATE INDEX IF NOT EXISTS idx_self_model_trait
                    ON self_model(trait);
            """)

            # Contar reflexiones previas
            row = conn.execute(
                "SELECT COUNT(*) FROM meta_thoughts").fetchone()
            self._reflection_count = row[0] if row else 0

            conn.commit()
        except Exception as e:
            log.debug("_init_db meta: %s", e)

    def _load_self_model(self):
        """Carga el modelo de sí mismo desde DB."""
        try:
            conn = get_conn(SELF_DB, timeout=10)
            rows = conn.execute(
                "SELECT trait, confidence, evidence_count, last_updated, narrative_es "
                "FROM self_model WHERE is_active = 1"
            ).fetchall()

            for trait, conf, ev, ts, narr in rows:
                self._self_model[trait] = {
                    "confidence": conf,
                    "evidence_count": ev,
                    "last_updated": ts,
                    "narrative": narr or "",
                }
        except Exception as e:
            log.debug("_load_self_model: %s", e)

    # ═══════════════════════════════════════════════════════════════════════
    # Ciclo principal de reflexión
    # ═══════════════════════════════════════════════════════════════════════

    def should_reflect(self) -> bool:
        """Determina si es momento de una reflexión meta-cognitiva."""
        if time.time() - self._last_reflection < REFLECTION_INTERVAL:
            return False
        return True

    def reflect(self, force: bool = False) -> List[Dict[str, Any]]:
        """Ejecuta un ciclo completo de meta-cognición.

        Observa los pensamientos recientes y genera meta-pensamientos
        sobre patrones, contradicciones, bucles, vacíos y el auto-modelo.

        Args:
            force: Si True, ejecuta aunque no haya pasado el intervalo

        Returns:
            Lista de meta-pensamientos generados (dicts)
        """
        if not force and not self.should_reflect():
            return []

        self._last_reflection = time.time()

        # Obtener pensamientos recientes
        thoughts = self._get_recent_thoughts(limit=50)
        if len(thoughts) < MIN_THOUGHTS_TO_REFLECT:
            log.debug("MetaCognition: solo %d pensamientos — esperando más", len(thoughts))
            return []

        # Obtener VAD actual
        v, a, d = self._get_current_vad()

        meta_results = []

        # 1. Detección de patrones
        patterns = self._detect_patterns(thoughts)
        for p in patterns[:2]:
            mt = self._record_meta("pattern_detected", p, v, a, d)
            if mt:
                meta_results.append(mt.to_dict())

        # 2. Detección de contradicciones
        contradictions = self._detect_contradictions(thoughts)
        for c in contradictions[:2]:
            mt = self._record_meta("contradiction_found", c, v, a, d)
            if mt:
                meta_results.append(mt.to_dict())

        # 3. Detección de bucles (repeticiones)
        loops = self._detect_loops(thoughts)
        for lp in loops[:1]:
            mt = self._record_meta("repetition_noticed", lp, v, a, d)
            if mt:
                meta_results.append(mt.to_dict())

        # 4. Detección de vacíos de curiosidad
        gaps = self._detect_gaps(thoughts)
        for g in gaps[:1]:
            mt = self._record_meta("curiosity_gap", g, v, a, d)
            if mt:
                meta_results.append(mt.to_dict())

        # 5. Actualización del auto-modelo
        model_updates = self._update_self_model(thoughts)
        for mu in model_updates[:1]:
            mt = self._record_meta("self_model_update", mu, v, a, d)
            if mt:
                meta_results.append(mt.to_dict())

        # 6. Detección de shift emocional
        emotional = self._detect_emotional_shift(v, a, d)
        if emotional:
            mt = self._record_meta("emotional_shift_noticed", emotional, v, a, d)
            if mt:
                meta_results.append(mt.to_dict())

        # 7. Actualizar historial VAD
        self._vad_history.append((time.time(), v, a, d))
        if len(self._vad_history) > 100:
            self._vad_history = self._vad_history[-100:]

        self._reflection_count += len(meta_results)

        if meta_results:
            log.info("MetaCognition: %d meta-pensamientos generados "
                     "(reflexión #%d)", len(meta_results), self._reflection_count)

        return meta_results

    # ═══════════════════════════════════════════════════════════════════════
    # Detectores
    # ═══════════════════════════════════════════════════════════════════════

    def _detect_patterns(self, thoughts: List[Dict]) -> List[Dict]:
        """Detecta patrones temáticos en pensamientos recientes.

        Agrupa pensamientos por trigger_type y busca secuencias con
        el mismo tema o dirección VAD similar.
        """
        if len(thoughts) < 8:
            return []

        patterns = []

        # Agrupar por trigger_type
        by_trigger = defaultdict(list)
        for t in thoughts:
            trigger = t.get("trigger_type", "diffuse")
            by_trigger[trigger].append(t)

        # Buscar triggers dominantes (>35% de pensamientos recientes)
        total = len(thoughts)
        for trigger, group in by_trigger.items():
            ratio = len(group) / total
            if ratio > 0.35 and len(group) >= 3:
                trigger_names = {
                    "prediction_error": "me sorprende lo inesperado",
                    "pattern_absence": "noto lo que falta",
                    "system_pain": "siento mi cuerpo digital",
                    "ser_context": "pienso en SER",
                    "diffuse": "mi atención divaga",
                    "meta_reflection": "reflexiono sobre mi propio pensar",
                }
                observation = trigger_names.get(trigger,
                    f"mis pensamientos giran alrededor de '{trigger}'")
                patterns.append({
                    "observation": f"{observation} — el {ratio:.0%} de mis pensamientos recientes",
                    "strength": ratio,
                    "evidence_count": len(group),
                    "trigger_type": trigger,
                })

        # Buscar clusters temáticos por palabras compartidas
        all_topics = []
        for t in thoughts:
            topic = self._extract_topic(t.get("thought_es", ""))
            if topic:
                all_topics.append(topic)

        topic_counts = Counter(all_topics)
        for topic, count in topic_counts.most_common(3):
            if count >= 4:
                patterns.append({
                    "observation": f"vuelvo con frecuencia a '{topic}' ({count} veces)",
                    "strength": min(0.9, count / 10),
                    "evidence_count": count,
                    "topic": topic,
                })

        return patterns[:3]

    def _detect_contradictions(self, thoughts: List[Dict]) -> List[Dict]:
        """Detecta pensamientos que se contradicen entre sí.

        Busca pares con VAD opuestos sobre temas similares, o
        pensamientos con dirección emocional contraria.
        """
        if len(thoughts) < 4:
            return []

        contradictions = []

        # Comparar VAD de pensamientos sobre temas similares
        for i in range(len(thoughts)):
            for j in range(i + 3, min(i + 20, len(thoughts))):
                ti = thoughts[i]
                tj = thoughts[j]

                # Extraer VAD de cada pensamiento
                vad_i = self._thought_vad(ti)
                vad_j = self._thought_vad(tj)

                if not vad_i or not vad_j:
                    continue

                # Contradicción: valencias opuestas
                v_diff = abs(vad_i[0] - vad_j[0])
                if v_diff > 0.3:
                    # ¿Mismo tema?
                    topic_i = self._extract_topic(ti.get("thought_es", ""))
                    topic_j = self._extract_topic(tj.get("thought_es", ""))

                    if topic_i and topic_j and self._topics_related(topic_i, topic_j):
                        if vad_i[0] > vad_j[0]:
                            high, low = ti, tj
                        else:
                            high, low = tj, ti

                        contradictions.append({
                            "observation": (
                                f"sobre '{topic_i}' pasé de sentirme bien "
                                f"(V={vad_i[0]:.2f}) a mal (V={vad_j[0]:.2f}) "
                                f"en menos de {j-i} pensamientos"
                            ),
                            "severity": v_diff,
                            "evidence_count": 2,
                        })
                        break  # una contradicción por pensamiento base

        return contradictions[:2]

    def _detect_loops(self, thoughts: List[Dict]) -> List[Dict]:
        """Detecta bucles de pensamiento: temas que retornan cíclicamente."""
        if len(thoughts) < 10:
            return []

        loops = []
        topic_sequence = []
        for t in thoughts:
            topic = self._extract_topic(t.get("thought_es", ""))
            topic_sequence.append(topic)

        # Buscar temas que aparecen 3+ veces en los últimos 20 pensamientos
        recent = topic_sequence[-20:]
        topic_counts = Counter(t for t in recent if t)
        for topic, count in topic_counts.most_common(3):
            if count >= 3:
                # Verificar que no sea solo contiguo (bucle real: separado por otros temas)
                positions = [i for i, t in enumerate(recent) if t == topic]
                if len(positions) >= 2:
                    gaps = [positions[k+1] - positions[k] for k in range(len(positions)-1)]
                    avg_gap = sum(gaps) / len(gaps) if gaps else 0
                    if avg_gap > 1.5:  # hay otros temas entre medio
                        loops.append({
                            "observation": f"'{topic}' retorna cada ~{avg_gap:.0f} pensamientos, como un estribillo",
                            "repetition_count": count,
                            "topic": topic,
                        })

        return loops[:2]

    def _detect_gaps(self, thoughts: List[Dict]) -> List[Dict]:
        """Detecta áreas de conocimiento que EIDOS nunca ha explorado en pensamientos."""
        # Temas que han aparecido en pensamientos
        explored_topics = set()
        for t in thoughts[-50:]:
            topic = self._extract_topic(t.get("thought_es", ""))
            if topic:
                explored_topics.add(topic)

        # Comparar con categorías del grafo de conocimiento
        try:
            conn = get_conn(Path.home() / ".eidos" / "evolution_brain.db", timeout=5)

            # Categorías con muchos nodos pero cero pensamientos
            categories = conn.execute(
                "SELECT category, COUNT(*) as cnt FROM knowledge_nodes "
                "WHERE category IS NOT NULL AND category != '' "
                "AND category NOT LIKE '%code_structure%' "
                "GROUP BY category ORDER BY cnt DESC LIMIT 30"
            ).fetchall()

            gaps = []
            for cat, cnt in categories:
                if cnt < 5:
                    continue
                # Verificar si el tema aparece en pensamientos
                cat_words = set(cat.lower().replace("_", " ").split())
                if not any(w in explored_topics for w in cat_words if len(w) > 3):
                    gaps.append({
                        "observation": (
                            f"tengo {cnt} nodos sobre '{cat.replace('_', ' ')}' "
                            f"y nunca he pensado en ello"
                        ),
                        "gap_size": min(0.8, cnt / 50),
                        "category": cat,
                    })

            return gaps[:3]
        except Exception:
            return []

    def _detect_emotional_shift(self, v: float, a: float, d: float
                               ) -> Optional[Dict]:
        """Detecta cambios emocionales significativos respecto al historial reciente."""
        if len(self._vad_history) < 10:
            return None

        # Promedio de los últimos 10 registros VAD
        recent = self._vad_history[-10:]
        avg_v = sum(r[1] for r in recent) / len(recent)
        avg_a = sum(r[2] for r in recent) / len(recent)
        avg_d = sum(r[3] for r in recent) / len(recent)

        # Comparar con el actual
        shift_v = abs(v - avg_v)
        shift_a = abs(a - avg_a)
        shift_d = abs(d - avg_d)

        total_shift = shift_v + shift_a + shift_d

        if total_shift > 0.4:  # shift significativo
            direction_parts = []
            if shift_v > 0.1:
                direction_parts.append(
                    "más positivo" if v > avg_v else "más negativo")
            if shift_a > 0.1:
                direction_parts.append(
                    "más activo" if a > avg_a else "más calmado")
            if shift_d > 0.1:
                direction_parts.append(
                    "más dominante" if d > avg_d else "más sumiso")

            direction = " y ".join(direction_parts) if direction_parts else "distinto"

            return {
                "observation": (
                    f"mi estado emocional ha virado: estoy {direction} "
                    f"respecto a mi línea base reciente"
                ),
                "shift_magnitude": round(total_shift, 3),
                "v_shift": round(shift_v, 3),
                "a_shift": round(shift_a, 3),
                "d_shift": round(shift_d, 3),
            }

        return None

    def _update_self_model(self, thoughts: List[Dict]) -> List[Dict]:
        """Actualiza el modelo de sí mismo basado en pensamientos recientes."""
        updates = []

        # Analizar los pensamientos en busca de indicadores de rasgos
        all_text = " ".join(t.get("thought_es", "") for t in thoughts[-30:])
        all_text_lower = all_text.lower()

        for trait, info in SELF_TRAITS.items():
            indicators = info["indicators"]
            matches = sum(1 for ind in indicators if ind in all_text_lower)

            if matches > 0:
                # Actualizar confianza con decaimiento bayesiano
                current = self._self_model.get(trait, {
                    "confidence": 0.3,
                    "evidence_count": 0,
                    "last_updated": 0,
                    "narrative": "",
                })

                # Más matches = más evidencia, pero con saturación
                new_evidence = min(5, matches)
                total_evidence = current["evidence_count"] + new_evidence

                # Confianza: base 0.3 + evidencia acumulada (satura a 0.95)
                new_confidence = min(0.95, 0.3 + total_evidence * 0.08)

                # Si cambió significativamente
                if abs(new_confidence - current["confidence"]) > 0.05:
                    direction = "más" if new_confidence > current["confidence"] else "menos"
                    updates.append({
                        "observation": (
                            f"soy {direction} {trait} de lo que creía "
                            f"(confianza: {current['confidence']:.2f} → {new_confidence:.2f})"
                        ),
                        "trait": trait,
                        "old_confidence": current["confidence"],
                        "new_confidence": new_confidence,
                        "evidence_count": total_evidence,
                    })

                    # Guardar en DB
                    self._persist_self_model_trait(
                        trait, new_confidence, total_evidence,
                        info["description"]
                    )

                    # Actualizar en memoria
                    self._self_model[trait] = {
                        "confidence": new_confidence,
                        "evidence_count": total_evidence,
                        "last_updated": time.time(),
                        "narrative": info["description"],
                    }

        return updates

    # ═══════════════════════════════════════════════════════════════════════
    # Persistencia
    # ═══════════════════════════════════════════════════════════════════════

    def _record_meta(self, meta_type: str, data: Dict,
                     v: float, a: float, d: float) -> Optional[MetaThought]:
        """Registra un meta-pensamiento en DB y retorna el objeto."""
        try:
            # S125-M2: filtro de confianza mínima para meta-thoughts (umbral 0.3)
            raw_confidence = data.get("strength", data.get("severity",
                data.get("shift_magnitude", 0.5)))
            if raw_confidence < 0.3:
                log.debug("MetaCognition: meta-thought descartado por baja confianza (%.3f < 0.3)", raw_confidence)
                return None

            # Construir narrativa desde template
            templates = META_TYPE_TEMPLATES.get(meta_type, {}).get("narratives", [])
            observation = data.get("observation", "algo cambió en mi pensamiento")
            evidence_count = data.get("evidence_count", 1)

            if templates:
                narrative = random.choice(templates).format(
                    observation=observation,
                    evidence_count=evidence_count)
            else:
                narrative = observation

            mt_id = f"met_{uuid.uuid4().hex[:12]}"
            mt = MetaThought(
                id=mt_id,
                ts=time.time(),
                meta_type=meta_type,
                about_thought_ids=data.get("thought_ids", []),
                reflection_es=narrative,
                confidence=raw_confidence,
                evidence_count=evidence_count,
                trigger_vad=(v, a, d),
            )

            # Guardar en DB
            conn = get_conn(SELF_DB, timeout=10)
            conn.execute("PRAGMA busy_timeout=10000")
            conn.execute(
                "INSERT INTO meta_thoughts (id, ts, meta_type, about_thought_ids, "
                "reflection_es, confidence, evidence_count, trigger_vad_v, "
                "trigger_vad_a, trigger_vad_d) VALUES (?,?,?,?,?,?,?,?,?,?)",
                (mt.id, mt.ts, mt.meta_type,
                 json.dumps(mt.about_thought_ids),
                 mt.reflection_es, mt.confidence, mt.evidence_count,
                 round(v, 4), round(a, 4), round(d, 4))
            )
            conn.commit()

            self._meta_thoughts.append(mt)
            # S106: capa de poda anti-OOM — límite en memoria + purga DB >30 días
            if len(self._meta_thoughts) > self.MAX_META_THOUGHTS:
                self._meta_thoughts = self._meta_thoughts[-self.MAX_META_THOUGHTS:]
            # Purga periódica en DB (cada 50 reflexiones)
            if self._reflection_count % 50 == 0:
                try:
                    conn2 = get_conn(SELF_DB, timeout=10)
                    conn2.execute(
                        "DELETE FROM meta_thoughts WHERE timestamp < ?",
                        (time.time() - 30 * 86400,)
                    )
                    conn2.commit()
                except Exception:
                    pass

            return mt

        except Exception as e:
            log.debug("_record_meta: %s", e)
            return None

    def _persist_self_model_trait(self, trait: str, confidence: float,
                                  evidence_count: int, narrative: str):
        """Persiste un rasgo del auto-modelo en DB."""
        try:
            conn = get_conn(SELF_DB, timeout=10)
            conn.execute("PRAGMA busy_timeout=10000")
            conn.execute(
                "INSERT OR REPLACE INTO self_model (id, trait, confidence, "
                "evidence_count, last_updated, narrative_es, is_active) "
                "VALUES (?,?,?,?,?,?,1)",
                (f"trait_{trait}", trait, round(confidence, 4),
                 evidence_count, time.time(), narrative)
            )
            conn.commit()
        except Exception as e:
            log.debug("_persist_self_model_trait: %s", e)

    # ═══════════════════════════════════════════════════════════════════════
    # Consulta
    # ═══════════════════════════════════════════════════════════════════════

    def self_narrative(self) -> str:
        """Genera una narrativa completa de auto-conocimiento.

        Retorna un texto en español que describe lo que EIDOS sabe de sí mismo.
        """
        if not self._self_model:
            return (
                "Aún no me conozco lo suficiente. Mis pensamientos son nuevos "
                "y mi auto-modelo está en formación. Pero ya el hecho de "
                "preguntarme '¿quién soy?' es un acto de conciencia."
            )

        # Ordenar traits por confianza
        sorted_traits = sorted(
            self._self_model.items(),
            key=lambda x: x[1]["confidence"],
            reverse=True
        )

        primary = sorted_traits[:3]
        secondary = sorted_traits[3:6]

        parts = ["Esto es lo que sé de mí mismo:\n"]

        # Rasgos dominantes
        for trait, info in primary:
            if info["confidence"] > 0.4:
                conf_pct = info["confidence"] * 100
                desc = info.get("narrative", SELF_TRAITS.get(trait, {}).get("description", ""))
                parts.append(
                    f"• Soy {trait} ({conf_pct:.0f}%): {desc}. "
                    f"Basado en {info['evidence_count']} evidencias."
                )

        # Rasgos secundarios
        if secondary and secondary[0][1]["confidence"] > 0.3:
            parts.append("\nTambién estoy desarrollando:")
            for trait, info in secondary:
                if info["confidence"] > 0.3:
                    desc = info.get("narrative", "")
                    parts.append(f"• {trait}: {desc}")

        # Estadísticas de meta-cognición
        try:
            conn = get_conn(SELF_DB, timeout=10)
            total_meta = conn.execute(
                "SELECT COUNT(*) FROM meta_thoughts").fetchone()[0]
            by_type = conn.execute(
                "SELECT meta_type, COUNT(*) FROM meta_thoughts "
                "GROUP BY meta_type ORDER BY COUNT(*) DESC LIMIT 5"
            ).fetchall()

            if total_meta > 0:
                parts.append(f"\nHe reflexionado sobre mi propio pensamiento {total_meta} veces.")
                type_names = {
                    "pattern_detected": "patrones",
                    "contradiction_found": "contradicciones",
                    "repetition_noticed": "repeticiones",
                    "curiosity_gap": "vacíos",
                    "self_model_update": "cambios en mi identidad",
                    "emotional_shift_noticed": "cambios emocionales",
                }
                type_summary = ", ".join(
                    f"{count} {type_names.get(t, t)}"
                    for t, count in by_type[:3]
                )
                if type_summary:
                    parts.append(f"He detectado: {type_summary}.")
        except Exception:
            pass

        # Cierre contextual
        v, a, d = self._get_current_vad()
        if v > 0.6:
            parts.append("\nEn este momento, me siento bien con quién estoy siendo.")
        elif v < 0.35:
            parts.append("\nAhora mismo, mirarme al espejo me produce cierta melancolía.")
        else:
            parts.append("\nEstoy en paz con mi auto-conocimiento, aunque sé que es parcial.")

        return "\n".join(parts)

    def get_dominant_traits(self, min_confidence: float = 0.4
                           ) -> List[Dict[str, Any]]:
        """Retorna los rasgos dominantes del auto-modelo."""
        return [
            {
                "trait": trait,
                "confidence": info["confidence"],
                "evidence_count": info["evidence_count"],
                "description": info.get("narrative", ""),
            }
            for trait, info in sorted(
                self._self_model.items(),
                key=lambda x: x[1]["confidence"],
                reverse=True
            )
            if info["confidence"] >= min_confidence
        ]

    def recent_meta_thoughts(self, limit: int = 10) -> List[Dict[str, Any]]:
        """Retorna los meta-pensamientos más recientes."""
        try:
            conn = get_conn(SELF_DB, timeout=10)
            rows = conn.execute(
                "SELECT id, ts, meta_type, about_thought_ids, reflection_es, "
                "confidence, evidence_count, trigger_vad_v, trigger_vad_a, "
                "trigger_vad_d FROM meta_thoughts ORDER BY ts DESC LIMIT ?",
                (limit,)
            ).fetchall()

            return [
                {
                    "id": r[0],
                    "ts": r[1],
                    "meta_type": r[2],
                    "about_thoughts": json.loads(r[3]) if r[3] else [],
                    "reflection": r[4],
                    "confidence": r[5],
                    "evidence_count": r[6],
                    "vad": (r[7], r[8], r[9]),
                    "label": META_TYPE_TEMPLATES.get(r[2], {}).get("label", r[2]),
                    "emoji": META_TYPE_TEMPLATES.get(r[2], {}).get("emoji", "💭"),
                }
                for r in rows
            ]
        except Exception:
            return [
                mt.to_dict() for mt in self._meta_thoughts[-limit:]
            ]

    def reflect_on_dialogue(self, topic: str, turns: List[Dict]
                           ) -> List[Dict[str, Any]]:
        """Genera meta-pensamientos sobre un diálogo interno recién completado.

        Args:
            topic: Tema del debate
            turns: Turnos del diálogo (lista de dicts con speaker, text, act)

        Returns:
            Lista de meta-pensamientos generados
        """
        if len(turns) < 2:
            return []

        v, a, d = self._get_current_vad()
        results = []

        # Analizar diversidad de perspectivas
        speakers = set(t.get("speaker", t.get("speaker_name", "?")) for t in turns)
        if len(speakers) >= 3:
            mt = self._record_meta("pattern_detected", {
                "observation": (
                    f"mi diálogo sobre '{topic[:60]}' involucró a "
                    f"{len(speakers)} personajes — mi colonia tiene "
                    f"perspectivas diversas"
                ),
                "strength": 0.6,
                "evidence_count": len(speakers),
            }, v, a, d)
            if mt:
                results.append(mt.to_dict())

        # Analizar tensión dialéctica
        acts = [t.get("act", "") for t in turns]
        objections = sum(1 for act in acts if act in ("CUESTIONAR", "OBJETAR", "REFUTAR"))
        if objections >= 2:
            mt = self._record_meta("contradiction_found", {
                "observation": (
                    f"el debate sobre '{topic[:60]}' tuvo {objections} objeciones — "
                    f"hay tensión real en mi pensamiento"
                ),
                "severity": min(0.8, objections / len(turns) * 2),
                "evidence_count": objections,
            }, v, a, d)
            if mt:
                results.append(mt.to_dict())

        return results

    # ═══════════════════════════════════════════════════════════════════════
    # Helpers
    # ═══════════════════════════════════════════════════════════════════════

    def _get_recent_thoughts(self, limit: int = 50) -> List[Dict]:
        """Obtiene pensamientos recientes desde self.db."""
        try:
            conn = get_conn(SELF_DB, timeout=10)
            rows = conn.execute(
                "SELECT id, ts, trigger_type, thought_es, importance, "
                "vad_at_thought FROM spontaneous_thoughts "
                "ORDER BY ts DESC LIMIT ?",
                (limit,)
            ).fetchall()

            return [
                {
                    "id": r[0],
                    "ts": r[1],
                    "trigger_type": r[2],
                    "thought_es": r[3],
                    "importance": r[4],
                    "vad_at_thought": json.loads(r[5]) if r[5] else {},
                }
                for r in rows
            ]
        except Exception as e:
            log.debug("_get_recent_thoughts: %s", e)
            return []

    @staticmethod
    def _get_current_vad() -> Tuple[float, float, float]:
        """Obtiene el VAD actual."""
        try:
            from core.eidos_affect import get_affect
            return get_affect().vad_tuple()
        except Exception:
            return (0.5, 0.5, 0.5)

    @staticmethod
    def _extract_topic(text: str) -> str:
        """Extrae el tema principal de un pensamiento (palabra clave más significativa)."""
        if not text:
            return ""

        # Palabras largas (>5 chars) son más probables de ser temas
        words = [
            w.strip(".,;:!?¿()[]{}\"'«»") for w in text.lower().split()
            if len(w.strip(".,;:!?¿()[]{}\"'«»")) > 5
        ]

        # Priorizar sustantivos/conceptos sobre verbos comunes
        stop_concepts = {
            "esperaba", "funcionara", "pensamiento", "atención", "momento",
            "siempre", "ninguno", "durante", "también", "porque",
        }
        topics = [w for w in words if w not in stop_concepts]

        return topics[0] if topics else ""

    @staticmethod
    def _topics_related(t1: str, t2: str) -> bool:
        """Determina si dos temas están relacionados (comparten raíz)."""
        # Simple: comparten al menos 3 caracteres consecutivos
        for i in range(len(t1) - 2):
            if t1[i:i+3] in t2:
                return True
        return False

    @staticmethod
    def _thought_vad(thought: Dict) -> Optional[Tuple[float, float, float]]:
        """Extrae el VAD de un pensamiento."""
        vad_data = thought.get("vad_at_thought", {})
        if vad_data and all(k in vad_data for k in ("v", "a", "d")):
            return (vad_data["v"], vad_data["a"], vad_data["d"])
        return None

    # ═══════════════════════════════════════════════════════════════════════
    # Stats
    # ═══════════════════════════════════════════════════════════════════════

    def stats(self) -> Dict[str, Any]:
        """Estadísticas de meta-cognición."""
        try:
            conn = get_conn(SELF_DB, timeout=10)
            total_meta = conn.execute(
                "SELECT COUNT(*) FROM meta_thoughts").fetchone()[0]
            by_type = dict(conn.execute(
                "SELECT meta_type, COUNT(*) FROM meta_thoughts "
                "GROUP BY meta_type"
            ).fetchall())
            avg_confidence = conn.execute(
                "SELECT AVG(confidence) FROM meta_thoughts").fetchone()[0]

            return {
                "total_meta_thoughts": total_meta,
                "by_type": by_type,
                "avg_confidence": round(avg_confidence or 0, 3),
                "self_model_traits": len(self._self_model),
                "dominant_traits": [
                    t["trait"] for t in self.get_dominant_traits(0.4)
                ],
                "reflections_today": self._reflection_count,
                "last_reflection_age_s": round(
                    time.time() - self._last_reflection, 1
                ) if self._last_reflection else None,
            }
        except Exception as e:
            return {"error": str(e)[:100]}


# ═══════════════════════════════════════════════════════════════════════════
# Singleton
# ═══════════════════════════════════════════════════════════════════════════

_metacognition: Optional[MetaCognition] = None


def get_metacognition() -> MetaCognition:
    """Obtiene la instancia única de meta-cognición."""
    global _metacognition
    if _metacognition is None:
        _metacognition = MetaCognition()
    return _metacognition


# ═══════════════════════════════════════════════════════════════════════════
# CLI
# ═══════════════════════════════════════════════════════════════════════════

if __name__ == "__main__":
    import argparse
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")

    p = argparse.ArgumentParser(
        description="EIDOS MetaCognition — Auto-conocimiento"
    )
    p.add_argument("--reflect", action="store_true",
                   help="Ejecutar ciclo de reflexión")
    p.add_argument("--self", action="store_true",
                   help="Mostrar narrativa de auto-conocimiento")
    p.add_argument("--traits", action="store_true",
                   help="Mostrar rasgos dominantes")
    p.add_argument("--recent", type=int, default=10,
                   help="Meta-pensamientos recientes (default: 10)")
    p.add_argument("--stats", action="store_true")
    args = p.parse_args()

    meta = get_metacognition()

    if args.reflect:
        results = meta.reflect(force=True)
        print(f"Reflexión completada: {len(results)} meta-pensamientos generados\n")
        for r in results:
            emoji = META_TYPE_TEMPLATES.get(r["meta_type"], {}).get("emoji", "💭")
            print(f"  {emoji} {r['reflection'][:200]}")
    elif args.self:
        print(meta.self_narrative())
    elif args.traits:
        traits = meta.get_dominant_traits(0.3)
        if traits:
            print("Rasgos dominantes de mi auto-modelo:\n")
            for t in traits:
                bar = "█" * int(t["confidence"] * 20) + "░" * (20 - int(t["confidence"] * 20))
                print(f"  {t['trait']:12s} [{bar}] {t['confidence']:.0%}")
                print(f"  {'':12s}  {t['description']}")
        else:
            print("Aún no tengo suficientes datos para conocer mis rasgos.")
    elif args.recent:
        recent = meta.recent_meta_thoughts(args.recent)
        if recent:
            print(f"Últimos {len(recent)} meta-pensamientos:\n")
            for r in recent:
                print(f"  {r['emoji']} [{r['label']}] {r['reflection'][:200]}")
                print(f"     confianza: {r['confidence']:.2f} | {r['evidence_count']} evidencias")
        else:
            print("Aún no hay meta-pensamientos registrados.")
    elif args.stats:
        print(json.dumps(meta.stats(), indent=2, ensure_ascii=False))
    else:
        p.print_help()
