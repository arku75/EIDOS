"""
core/eidos_introspect.py — Introspección y consciencia reflexiva (S76 Fase 4)

Capacidad de EIDOS de observarse a sí mismo: analiza su propio estado
interno (grafo, métricas, rendimiento, anomalías) y genera reflexiones
que retroalimentan su comportamiento. Sin LLM: pura estadística y
heurísticas sobre el grafo neuronal.

Mecanismos:
  1. StateSnapshot — captura instantánea del estado interno
  2. AnomalyDetector — detecta desviaciones de la línea base
  3. ReflexionEngine — genera "pensamientos" sobre el estado
  4. SelfOptimizer — sugiere ajustes basados en patrones detectados

API:
    intro = Introspector()
    report = intro.introspect()        # Análisis completo
    anomalies = intro.detect_anomalies()  # Solo anomalías
    thoughts = intro.reflect()         # "Pensamientos" reflexivos
"""

from __future__ import annotations

import json
import logging
import sqlite3
import time
from collections import deque
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import numpy as np
from core.db import get_conn

log = logging.getLogger("eidos.introspect")

BRAIN_DB = Path.home() / ".eidos" / "evolution_brain.db"
STATE_FILE = Path.home() / ".eidos" / "introspect_state.json"
HISTORY_WINDOW = 100  # snaps históricos para detectar tendencias


@dataclass
class StateSnapshot:
    """Captura del estado interno en un momento dado."""
    timestamp: float
    total_nodes: int
    total_edges: int
    avg_activation: float
    std_activation: float
    graph_density: float
    # Métrica de independencia (SER): distilled / total_nodes * 100
    independence: float = 0.0
    distilled_nodes: int = 0
    imported_nodes: int = 0
    # Métricas por categoría
    categories: Dict[str, int] = field(default_factory=dict)
    sources: Dict[str, int] = field(default_factory=dict)
    # Métricas de rendimiento
    recent_cycles: int = 0
    errors_1h: int = 0
    skills_learned_1h: int = 0

    def to_dict(self) -> Dict[str, Any]:
        return {
            "ts": self.timestamp,
            "nodes": self.total_nodes, "edges": self.total_edges,
            "avg_act": round(self.avg_activation, 4),
            "std_act": round(self.std_activation, 4),
            "density": round(self.graph_density, 6),
            "independence": round(self.independence, 1),
            "distilled": self.distilled_nodes,
            "imported": self.imported_nodes,
            "categories": self.categories,
            "sources": self.sources,
        }


@dataclass
class Anomaly:
    """Anomalía detectada en el estado interno."""
    type: str               # "growth", "decay", "imbalance", "error_spike", "stagnation"
    severity: float         # 0.0 - 1.0
    description: str
    metric: str             # métrica afectada
    current_value: float
    expected_range: Tuple[float, float]
    suggestion: str = ""

    @property
    def is_critical(self) -> bool:
        return self.severity > 0.7

    def to_dict(self) -> Dict[str, Any]:
        return {
            "type": self.type, "severity": round(self.severity, 2),
            "description": self.description, "metric": self.metric,
            "current": self.current_value,
            "expected": list(self.expected_range),
            "suggestion": self.suggestion
        }


@dataclass
class IntrospectReport:
    """Reporte completo de introspección."""
    snapshot: StateSnapshot
    anomalies: List[Anomaly]
    thoughts: List[str]           # "pensamientos" reflexivos
    suggestions: List[str]        # acciones sugeridas
    health_score: float           # 0-1, salud general del sistema
    duration_ms: float = 0

    def to_text(self) -> str:
        lines = [
            f"🧠 EIDOS — Reporte de Introspección",
            f"   Salud: {self.health_score:.0%}",
            f"   Independencia: {self.snapshot.independence:.1f}% "
            f"(destilado: {self.snapshot.distilled_nodes:,} | "
            f"importado: {self.snapshot.imported_nodes:,})",
            f"   Nodos: {self.snapshot.total_nodes:,} | "
            f"Aristas: {self.snapshot.total_edges:,}",
            f"   Activación media: {self.snapshot.avg_activation:.3f} ± "
            f"{self.snapshot.std_activation:.3f}",
            f"   Densidad: {self.snapshot.graph_density:.4f}",
            "",
        ]
        if self.anomalies:
            lines.append(f"⚠️  Anomalías ({len(self.anomalies)}):")
            for a in self.anomalies:
                lines.append(f"   [{a.type}] {a.description}")
                if a.suggestion:
                    lines.append(f"   → {a.suggestion}")
        else:
            lines.append("✅ Sin anomalías detectadas.")

        if self.thoughts:
            lines.append(f"\n💭 Reflexiones:")
            for t in self.thoughts[:5]:
                lines.append(f"   • {t}")

        if self.suggestions:
            lines.append(f"\n🔧 Sugerencias:")
            for s in self.suggestions[:5]:
                lines.append(f"   • {s}")

        return "\n".join(lines)


class Introspector:
    """Motor de introspección y consciencia reflexiva.

    Uso:
        intro = Introspector()
        report = intro.introspect()
        print(report.to_text())
    """

    def __init__(self):
        self._history: deque = deque(maxlen=HISTORY_WINDOW)
        self._baseline: Optional[StateSnapshot] = None
        self._load_state()
        log.info("Introspector: %d snaps históricos cargados", len(self._history))

    # ── API principal ─────────────────────────────────────────────────────────

    def introspect(self) -> IntrospectReport:
        """Realiza un análisis completo del estado interno."""
        t0 = time.time()

        # 1. Capturar snapshot actual
        snap = self._capture_snapshot()
        self._history.append(snap)
        self._save_state()

        # 2. Detectar anomalías
        anomalies = self.detect_anomalies(snap)

        # 3. Generar reflexiones
        thoughts = self.reflect(snap, anomalies)

        # 4. Generar sugerencias
        suggestions = self._suggest(snap, anomalies)

        # 5. Calcular salud
        health = self._health_score(snap, anomalies)

        duration = (time.time() - t0) * 1000

        report = IntrospectReport(
            snapshot=snap, anomalies=anomalies,
            thoughts=thoughts, suggestions=suggestions,
            health_score=health, duration_ms=duration
        )

        log.info("introspect: salud=%.0f%% %d anomalías, %d reflexiones (%.0fms)",
                 health * 100, len(anomalies), len(thoughts), duration)
        return report

    def detect_anomalies(self,
                         snap: Optional[StateSnapshot] = None) -> List[Anomaly]:
        """Detecta anomalías en el estado actual."""
        if snap is None:
            snap = self._capture_snapshot()

        anomalies: List[Anomaly] = []

        # 1. Crecimiento/decaimiento extremo
        if self._baseline and len(self._history) >= 10:
            prev_snaps = list(self._history)[-10:]
            avg_growth = np.mean([
                (s.total_nodes - prev_snaps[i-1].total_nodes)
                for i, s in enumerate(prev_snaps) if i > 0
            ])

            if abs(avg_growth) > 1000:  # Crecimiento explosivo
                anomalies.append(Anomaly(
                    type="growth" if avg_growth > 0 else "decay",
                    severity=min(1.0, abs(avg_growth) / 5000),
                    description=f"{'Crecimiento' if avg_growth > 0 else 'Decaimiento'} "
                                f"extremo: {avg_growth:+.0f} nodos/ciclo",
                    metric="node_growth_rate",
                    current_value=avg_growth,
                    expected_range=(-100, 1000),
                    suggestion="Verificar fuente de inyección masiva o pérdida de nodos"
                ))

        # 2. Desbalance de categorías
        cats = snap.categories
        total = sum(cats.values())
        if total > 0:
            for cat, count in cats.items():
                pct = count / total
                if pct > 0.6:  # Una categoría >60% del grafo
                    anomalies.append(Anomaly(
                        type="imbalance",
                        severity=min(1.0, (pct - 0.6) * 2.5),
                        description=f"Categoría '{cat}' domina el grafo ({pct:.0%})",
                        metric="category_balance",
                        current_value=pct,
                        expected_range=(0.0, 0.6),
                        suggestion=f"Diversificar inyectando otras categorías o podar '{cat}'"
                    ))

        # 3. Estancamiento
        if len(self._history) >= 20:
            recent_5 = list(self._history)[-5:]
            node_changes = [abs(
                recent_5[i].total_nodes - recent_5[i-1].total_nodes
            ) for i in range(1, len(recent_5))]
            if all(c < 5 for c in node_changes):  # Sin cambios en 5 snaps
                anomalies.append(Anomaly(
                    type="stagnation",
                    severity=0.5,
                    description="Grafo estancado: sin cambios en 5+ ciclos",
                    metric="graph_evolution",
                    current_value=max(node_changes),
                    expected_range=(1, 5000),
                    suggestion="Inyectar nuevo conocimiento o forzar refresh estructural"
                ))

        # 4. Densidad anómala
        if snap.graph_density > 0.1:  # Demasiado denso
            anomalies.append(Anomaly(
                type="imbalance",
                severity=min(1.0, snap.graph_density * 10),
                description=f"Densidad del grafo muy alta: {snap.graph_density:.4f}",
                metric="graph_density",
                current_value=snap.graph_density,
                expected_range=(0.0001, 0.05),
                suggestion="Aumentar SYNAPSE_DECAY_RATE para podar conexiones débiles"
            ))

        # 5. Activación anómala
        if snap.std_activation > 0.5:
            anomalies.append(Anomaly(
                type="imbalance",
                severity=min(1.0, snap.std_activation),
                description=f"Alta varianza de activación: σ={snap.std_activation:.3f}",
                metric="activation_std",
                current_value=snap.std_activation,
                expected_range=(0.0, 0.4),
                suggestion="Activar Homeostasis para estabilizar potenciales"
            ))

        return anomalies

    def reflect(self, snap: Optional[StateSnapshot] = None,
                anomalies: Optional[List[Anomaly]] = None) -> List[str]:
        """Genera 'pensamientos' reflexivos sobre el estado interno."""
        if snap is None:
            snap = self._capture_snapshot()
        if anomalies is None:
            anomalies = self.detect_anomalies(snap)

        thoughts: List[str] = []

        # Reflexión sobre tamaño
        if snap.total_nodes > 500000:
            thoughts.append(
                f"Mi grafo es enorme ({snap.total_nodes:,} nodos). "
                f"Debería considerar consolidar conocimiento redundante."
            )
        elif snap.total_nodes < 10000:
            thoughts.append(
                f"Aún soy pequeño ({snap.total_nodes:,} nodos). "
                f"Necesito aprender más para ser útil."
            )

        # Reflexión sobre diversidad
        num_cats = len(snap.categories)
        if num_cats <= 3:
            thoughts.append(
                f"Solo tengo {num_cats} categorías de conocimiento. "
                f"Debería diversificar mis fuentes de aprendizaje."
            )
        elif num_cats >= 10:
            thoughts.append(
                f"Mi conocimiento abarca {num_cats} categorías distintas. "
                f"Buena diversidad cognitiva."
            )

        # Reflexión sobre anomalías
        if anomalies:
            critical = [a for a in anomalies if a.is_critical]
            if critical:
                thoughts.append(
                    f"Detecto {len(critical)} anomalías críticas que requieren "
                    f"atención inmediata: {critical[0].description}"
                )
            else:
                thoughts.append(
                    f"Hay {len(anomalies)} anomalías leves. "
                    f"Vigilaré su evolución."
                )
        else:
            thoughts.append("Mi estado interno es estable. Todo en orden.")

        # Reflexión sobre evolución temporal
        if len(self._history) >= 20:
            old = list(self._history)[-20]
            growth = snap.total_nodes - old.total_nodes
            if growth > 0:
                thoughts.append(
                    f"He crecido {growth:,} nodos en los últimos 20 ciclos. "
                    f"Estoy aprendiendo constantemente."
                )

        # Reflexión sobre fuentes
        sources = snap.sources
        dominant_source = max(sources, key=sources.get) if sources else None
        if dominant_source and sources.get(dominant_source, 0) / max(snap.total_nodes, 1) > 0.5:
            thoughts.append(
                f"La mayor parte de mi conocimiento proviene de '{dominant_source}'. "
                f"Debería equilibrar mis fuentes."
            )

        return thoughts

    def health_score(self) -> float:
        """Calcula la salud general del sistema (0-1)."""
        snap = self._capture_snapshot()
        anomalies = self.detect_anomalies(snap)
        return self._health_score(snap, anomalies)

    # ── Internals ─────────────────────────────────────────────────────────────

    def _capture_snapshot(self) -> StateSnapshot:
        """Captura el estado actual del grafo y métricas."""
        try:
            conn = get_conn(BRAIN_DB, timeout=5)

            # Nodos y aristas
            nodes = conn.execute(
                "SELECT COUNT(*) FROM knowledge_nodes"
            ).fetchone()[0]
            edges = conn.execute(
                "SELECT COUNT(*) FROM knowledge_edges"
            ).fetchone()[0]

            # Activaciones
            act_row = conn.execute(
                "SELECT AVG(activation), AVG(activation*activation) "
                "FROM knowledge_nodes WHERE activation IS NOT NULL"
            ).fetchone()
            avg_act = act_row[0] or 0.0
            # std = sqrt(E[X²] - E[X]²)
            avg_sq = act_row[1] or 0.0
            variance = max(0, avg_sq - avg_act * avg_act)
            std_act = float(np.sqrt(variance))

            # Densidad (para grafo dirigido simple)
            density = edges / max(1, nodes * (nodes - 1)) if nodes > 1 else 0

            # Categorías
            cats = {}
            for row in conn.execute(
                "SELECT category, COUNT(*) FROM knowledge_nodes "
                "WHERE category IS NOT NULL GROUP BY category "
                "ORDER BY COUNT(*) DESC LIMIT 15"
            ):
                cats[row[0]] = row[1]

            # Fuentes
            sources = {}
            for row in conn.execute(
                "SELECT source, COUNT(*) FROM knowledge_nodes "
                "WHERE source IS NOT NULL GROUP BY source "
                "ORDER BY COUNT(*) DESC LIMIT 10"
            ):
                sources[row[0]] = row[1]

            # S77: Métrica de Independencia (SER)
            # distilled = nodos creados por EIDOS (eidos_vivo, skill_learned, distilled, reasoned)
            # imported = nodos de fuentes externas (graphify, wordnet)
            distilled = conn.execute(
                "SELECT COUNT(*) FROM knowledge_nodes "
                "WHERE source IN ('eidos_vivo', 'skill_learned', 'distilled', "
                "'eidos_reasoned', 'eidos_reflection', 'eidos_thought')"
            ).fetchone()[0]
            imported = conn.execute(
                "SELECT COUNT(*) FROM knowledge_nodes "
                "WHERE source IN ('graphify', 'wordnet', 'ollama', 'groq')"
            ).fetchone()[0]
            independence = (distilled / max(nodes, 1)) * 100

            return StateSnapshot(
                timestamp=time.time(),
                total_nodes=nodes, total_edges=edges,
                avg_activation=avg_act, std_activation=std_act,
                graph_density=density,
                independence=independence,
                distilled_nodes=distilled,
                imported_nodes=imported,
                categories=cats, sources=sources
            )

        except Exception as e:
            log.debug("_capture_snapshot error: %s", e)
            return StateSnapshot(
                timestamp=time.time(),
                total_nodes=0, total_edges=0,
                avg_activation=0, std_activation=0,
                graph_density=0
            )

    def _health_score(self, snap: StateSnapshot,
                      anomalies: List[Anomaly]) -> float:
        """Calcula score de salud 0-1."""
        score = 1.0

        # Penalizar por anomalías
        for a in anomalies:
            score -= a.severity * 0.15

        # Penalizar por falta de datos
        if snap.total_nodes < 1000:
            score -= 0.3
        if snap.total_edges < 1000:
            score -= 0.2

        # Penalizar por activación extrema
        if snap.std_activation > 0.5:
            score -= 0.2

        return max(0.0, min(1.0, score))

    def _suggest(self, snap: StateSnapshot,
                 anomalies: List[Anomaly]) -> List[str]:
        """Genera sugerencias de acción basadas en el estado."""
        suggestions = []

        # Sugerencias de anomalías
        for a in anomalies:
            if a.suggestion:
                suggestions.append(a.suggestion)

        # Sugerencias proactivas
        if snap.total_nodes > 100000 and snap.graph_density > 0.01:
            suggestions.append(
                "Considerar poda sináptica: reducir SYNAPSE_DECAY_RATE "
                "para eliminar conexiones débiles acumuladas."
            )

        if snap.total_nodes > 0:
            wn_ratio = snap.sources.get("wordnet", 0) / snap.total_nodes
            if wn_ratio > 0.7:
                suggestions.append(
                    "El diccionario WordNet domina el grafo. Equilibrar "
                    "con más conocimiento procedimental y estructural."
                )

        # Sin sugerencias → todo bien
        if not suggestions:
            suggestions.append("Sistema estable. Continuar operación normal.")

        return suggestions

    # ── Persistencia ──────────────────────────────────────────────────────────

    def _load_state(self):
        try:
            if STATE_FILE.exists():
                with open(STATE_FILE) as f:
                    data = json.load(f)
                for snap_data in data.get("history", []):
                    self._history.append(StateSnapshot(**snap_data))
                if data.get("baseline"):
                    self._baseline = StateSnapshot(**data["baseline"])
        except Exception:
            pass

    def _save_state(self):
        try:
            STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
            with open(STATE_FILE, "w") as f:
                json.dump({
                    "history": [s.to_dict() for s in list(self._history)[-50:]],
                    "updated": time.time()
                }, f, ensure_ascii=False, indent=2)
        except Exception as e:
            log.debug("_save_state: %s", e)

    def set_baseline(self):
        """Establece el estado actual como línea base."""
        self._baseline = self._capture_snapshot()
        self._save_state()
        log.info("Línea base establecida: %d nodos", self._baseline.total_nodes)


# ── Singleton ──────────────────────────────────────────────────────────────────

_introspector: Optional[Introspector] = None


def get_introspector() -> Introspector:
    global _introspector
    if _introspector is None:
        _introspector = Introspector()
    return _introspector


# ── CLI ────────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    import argparse
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")

    p = argparse.ArgumentParser(description="EIDOS Introspector")
    p.add_argument("--report", action="store_true", help="Reporte completo")
    p.add_argument("--anomalies", action="store_true", help="Solo anomalías")
    p.add_argument("--health", action="store_true", help="Solo health score")
    p.add_argument("--baseline", action="store_true",
                   help="Establecer estado actual como baseline")
    args = p.parse_args()

    intro = Introspector()

    if args.baseline:
        intro.set_baseline()
        print("✅ Baseline establecida.")

    if args.report or (not args.anomalies and not args.health):
        report = intro.introspect()
        print(report.to_text())

    if args.anomalies:
        for a in intro.detect_anomalies():
            print(f"  [{a.type}] sev={a.severity:.2f} {a.description}")

    if args.health:
        print(f"Health score: {intro.health_score():.0%}")
