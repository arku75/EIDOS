"""
core/eidos_marocuai.py — Arquitectura cognitiva Marocuai integrada en EIDOS

Incorpora 4 mecanismos de la arquitectura Marocuai (SER, S76) que simulan
restricciones biológicas a nivel software, haciendo la cognición de EIDOS
más eficiente y realista:

1. EnergyBudget — Presupuesto energético artificial (Cesio-133 virtual)
   Cada operación cognitiva consume energía de un presupuesto finito diario.
   Sin energía → sin operación. Fuerza priorización y eficiencia.

2. ActiveInference — Inferencia activa (predicción + minimización error)
   Genera predicciones del estado del grafo y las compara con observaciones.
   Minimiza "free energy" (sorpresa) con mínimo coste energético.

3. Homeostasis — Regulación de equilibrio cognitivo
   Monitorea activación neuronal global y ajusta NEURON_ACTIVATION_BOOST
   dinámicamente. Si hay sobre-activación → reduce boost. Si hay estancamiento
   → aumenta exploración.

4. PatternMetabolism — Metabolismo de patrones (fricción del pensamiento)
   Mide coherencia estructural del grafo. Si la red es ruidosa (baja
   clusterización) → acelera poda sináptica y reduce refuerzo Hebbiano.

Integración:
    from core.eidos_marocuai import MarocuaiArchitecture, get_marocuai
    marocuai = get_marocuai()
    marocuai.cycle_hook("perceive")  # en cada fase del ciclo vital

Basado en el diseño de DeepSeek v4 Pro (S76), adaptado a las APIs reales de:
    - knowledge_reasoner.py (SemanticGraph, constantes neuronales)
    - eidos_vivo.py (ciclo vital con fases perceive/decide/act/learn/reflect)
"""

from __future__ import annotations

import json
import logging
import math
import os
import time
from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

log = logging.getLogger("eidos.marocuai")

# ── Constantes Marocuai ───────────────────────────────────────────────────────
# Estas extienden las constantes neuronales de knowledge_reasoner.py
# importadas dinámicamente para no crear dependencia circular

DAILY_ENERGY_BUDGET = 1000.0      # Unidades de energía por día (Cesio-133 virtual)
ENERGY_COST = {
    "perceive": 1.0,
    "decide": 2.5,
    "act": 3.0,
    "learn": 4.0,
    "reflect": 5.0,
    "spreading_step": 0.8,
    "prediction_update": 1.2,
    "homeostasis_check": 0.5,
    "metabolism_check": 1.5,
    "reason": 2.0,
}

# Homeostasis targets
TARGET_ACTIVATION_MEAN = 0.3      # Media deseada de potencial neuronal
TARGET_ACTIVATION_VARIANCE = 0.05 # Varianza deseada
HOMEOSTASIS_ADJUST_STEP = 0.01    # Paso de ajuste por ciclo

# Active Inference
FREE_ENERGY_LEARNING_RATE = 0.1
PREDICTION_DECAY = 0.9

# Pattern Metabolism
MIN_CLUSTERING_FOR_HEALTH = 0.15
PATTERN_NOISE_PENALTY = 0.02

# Persistencia
MAROCUAI_STATE = Path.home() / ".eidos" / "marocuai_state.json"


# ═══════════════════════════════════════════════════════════════════════════════
# MECANISMO 1: EnergyBudget — Presupuesto Energético Artificial
# ═══════════════════════════════════════════════════════════════════════════════

class EnergyBudget:
    """Economía energética virtual basada en la frecuencia de resonancia Cesio-133.

    Cada operación cognitiva (percibir, decidir, actuar, aprender) consume
    energía de un presupuesto finito. Si no hay energía suficiente, la
    operación se omite o degrada. Esto fuerza al sistema a priorizar y
    a ser eficiente, como un cerebro biológico bajo restricciones metabólicas.
    """

    def __init__(self, daily_budget: float = DAILY_ENERGY_BUDGET):
        self.daily_budget = daily_budget
        self.remaining = daily_budget
        self.total_consumed = 0.0
        self.cycle_count = 0
        self.operation_counts: Dict[str, int] = defaultdict(int)
        self._last_replenish = time.time()
        self._load()

    def consume(self, operation: str) -> bool:
        """Intenta consumir energía para una operación. Retorna True si hay suficiente."""
        cost = ENERGY_COST.get(operation, 1.0)
        if self.remaining >= cost:
            self.remaining -= cost
            self.total_consumed += cost
            self.operation_counts[operation] += 1
            return True
        log.debug("Marocuai: sin energía para '%s' (quedan %.1f, necesita %.1f)",
                  operation, self.remaining, cost)
        return False

    def replenish(self) -> None:
        """Reinicia el presupuesto diario (ciclo circadiano)."""
        self.remaining = self.daily_budget
        self.total_consumed = 0.0
        self.operation_counts.clear()
        self.cycle_count += 1
        self._last_replenish = time.time()
        self._save()
        log.info("Marocuai: energía recargada (%.0f unidades, ciclo %d)",
                 self.daily_budget, self.cycle_count)

    def status(self) -> Dict[str, Any]:
        return {
            "daily_budget": self.daily_budget,
            "remaining": round(self.remaining, 1),
            "consumed": round(self.total_consumed, 1),
            "efficiency_pct": round(self.total_consumed / max(self.daily_budget, 1) * 100, 1),
            "cycles_today": self.cycle_count,
            "operations": dict(self.operation_counts),
        }

    def _save(self) -> None:
        try:
            MAROCUAI_STATE.parent.mkdir(parents=True, exist_ok=True)
            state = {
                "remaining": self.remaining,
                "total_consumed": self.total_consumed,
                "cycle_count": self.cycle_count,
                "last_replenish": self._last_replenish,
            }
            MAROCUAI_STATE.write_text(json.dumps(state))
        except Exception:
            pass

    def _load(self) -> None:
        try:
            if MAROCUAI_STATE.exists():
                state = json.loads(MAROCUAI_STATE.read_text())
                self.remaining = state.get("remaining", self.daily_budget)
                self.total_consumed = state.get("total_consumed", 0)
                self.cycle_count = state.get("cycle_count", 0)
                self._last_replenish = state.get("last_replenish", time.time())
        except Exception:
            pass


# ═══════════════════════════════════════════════════════════════════════════════
# MECANISMO 2: ActiveInference — Inferencia Activa
# ═══════════════════════════════════════════════════════════════════════════════

class ActiveInference:
    """Inferencia activa: predice el estado del grafo y minimiza la sorpresa.

    Mantiene un modelo generativo interno de los potenciales neuronales.
    Compara predicciones contra el estado real observado. La diferencia
    es "free energy" (sorpresa). El objetivo es doble:
    1. Minimizar el error de predicción
    2. Minimizar la energía gastada en resolver incertidumbre
    """

    def __init__(self):
        self.predictions: Dict[str, float] = {}  # node_id → predicted_potential
        self.prediction_errors: Dict[str, float] = {}  # node_id → error
        self.free_energy_history: List[float] = []

    def predict(self, graph_state: Dict[str, float]) -> Dict[str, float]:
        """Genera predicciones para el próximo estado a partir del actual."""
        new_predictions = {}
        for node_id, current_potential in graph_state.items():
            old_pred = self.predictions.get(node_id, 0.1)
            # Decaimiento hacia el potencial de reposo con inercia
            pred = PREDICTION_DECAY * old_pred + (1 - PREDICTION_DECAY) * 0.1
            # Influencia del estado actual (acoplamiento débil)
            pred = pred * 0.7 + current_potential * 0.3
            new_predictions[node_id] = max(0.0, min(1.0, pred))
        self.predictions = new_predictions
        return new_predictions

    def update(self, observed: Dict[str, float]) -> None:
        """Actualiza predicciones basándose en el error de observación."""
        for node_id, actual in observed.items():
            pred = self.predictions.get(node_id, 0.1)
            error = actual - pred
            self.prediction_errors[node_id] = error
            # Corrección bayesiana simple
            new_pred = pred + FREE_ENERGY_LEARNING_RATE * error
            self.predictions[node_id] = max(0.0, min(1.0, new_pred))

    def free_energy(self) -> float:
        """Calcula energía libre = precisión + complejidad."""
        if not self.prediction_errors:
            return 0.0
        n = len(self.prediction_errors)
        # Accuracy: error cuadrático medio
        accuracy = sum(e**2 for e in self.prediction_errors.values()) / n
        # Complexity: número de predicciones activas (regularización)
        complexity = len([v for v in self.predictions.values() if v > 0.05]) * 0.001
        fe = accuracy + complexity
        self.free_energy_history.append(fe)
        if len(self.free_energy_history) > 100:
            self.free_energy_history = self.free_energy_history[-100:]
        return fe

    def step(self, graph_state: Dict[str, float]) -> float:
        """Un ciclo de inferencia: predecir → observar → actualizar."""
        self.predict(graph_state)
        self.update(graph_state)
        return self.free_energy()

    def surprise_level(self) -> float:
        """Nivel de sorpresa actual (último free energy)."""
        if self.free_energy_history:
            return self.free_energy_history[-1]
        return 0.0


# ═══════════════════════════════════════════════════════════════════════════════
# MECANISMO 3: Homeostasis — Regulación de Equilibrio Cognitivo
# ═══════════════════════════════════════════════════════════════════════════════

class Homeostasis:
    """Regulador homeostático de la excitabilidad neuronal global.

    Monitorea la media y varianza de los potenciales de activación.
    Si la red está demasiado excitada (media alta) → reduce el boost.
    Si está estancada (media baja) → aumenta exploración.
    Si no converge → puede forzar terminación de procesos divergentes.

    Ajusta dinámicamente knowledge_reasoner.NEURON_ACTIVATION_BOOST.
    """

    def __init__(self):
        self.target_mean = TARGET_ACTIVATION_MEAN
        self.target_var = TARGET_ACTIVATION_VARIANCE
        self.adjust_step = HOMEOSTASIS_ADJUST_STEP
        self.history: List[Tuple[float, float]] = []  # (mean, var) por ciclo
        self.interventions = 0

    def monitor(self, graph_state: Dict[str, float]) -> Tuple[float, float]:
        """Calcula media y varianza de los potenciales actuales."""
        if not graph_state:
            return 0.0, 0.0
        values = list(graph_state.values())
        n = len(values)
        mean = sum(values) / n
        var = sum((x - mean) ** 2 for x in values) / n
        self.history.append((mean, var))
        if len(self.history) > 100:
            self.history = self.history[-100:]
        return mean, var

    def adjust_boost(self, graph_state: Dict[str, float]) -> float:
        """Calcula y aplica el nuevo NEURON_ACTIVATION_BOOST.

        Retorna el nuevo valor.
        """
        try:
            from core import knowledge_reasoner as kr
            current_boost = kr.NEURON_ACTIVATION_BOOST
        except (ImportError, AttributeError):
            current_boost = 0.15

        current_mean, current_var = self.monitor(graph_state)
        error = self.target_mean - current_mean

        # Ajuste proporcional al error
        adjustment = error * self.adjust_step

        # Si la varianza es muy alta (inestabilidad) → reducir boost más agresivamente
        if current_var > self.target_var * 2:
            adjustment -= self.adjust_step * 2

        new_boost = current_boost + adjustment

        # Clamp a rango seguro
        new_boost = max(0.05, min(0.30, new_boost))

        # Aplicar
        try:
            import core.knowledge_reasoner as kr
            kr.NEURON_ACTIVATION_BOOST = new_boost
        except (ImportError, AttributeError):
            pass

        if abs(new_boost - current_boost) > 0.001:
            self.interventions += 1
            log.debug("Homeostasis: boost %.3f → %.3f (error=%.3f, var=%.3f)",
                      current_boost, new_boost, error, current_var)

        return new_boost

    def should_force_terminate(self) -> bool:
        """Detecta divergencia: si la media sube sin converger durante 10+ ciclos."""
        if len(self.history) < 10:
            return False
        recent = [m for m, _ in self.history[-10:]]
        # Si la media está siempre por encima del target y no baja
        if all(m > self.target_mean * 1.5 for m in recent):
            # Y sigue subiendo
            if recent[-1] > recent[0]:
                log.warning("Homeostasis: divergencia detectada, sugiriendo terminación")
                return True
        return False

    def status(self) -> Dict[str, Any]:
        if not self.history:
            return {"mean": 0, "var": 0, "interventions": 0}
        return {
            "current_mean": round(self.history[-1][0], 4),
            "current_var": round(self.history[-1][1], 4),
            "target_mean": self.target_mean,
            "interventions": self.interventions,
            "should_terminate": self.should_force_terminate(),
        }


# ═══════════════════════════════════════════════════════════════════════════════
# MECANISMO 4: PatternMetabolism — Metabolismo de Patrones
# ═══════════════════════════════════════════════════════════════════════════════

class PatternMetabolism:
    """Metabolismo de patrones: evalúa coherencia estructural y penaliza ruido.

    Mide el coeficiente de clustering promedio del grafo como proxy de
    coherencia estructural. Si el grafo es ruidoso (bajo clustering) →
    acelera la poda sináptica (SYNAPSE_DECAY_RATE ↑) y reduce el refuerzo
    Hebbiano (SYNAPSE_STRENGTHEN_RATE ↓). Esto crea presión evolutiva
    hacia patrones coherentes con mínimo gasto computacional.
    """

    def __init__(self):
        self.penalty = PATTERN_NOISE_PENALTY
        self.coherence_history: List[float] = []
        self.interventions = 0

    def structural_coherence(self, graph) -> float:
        """Estima coherencia estructural del grafo (clustering aprox).

        Sin acceso a NetworkX, usa muestreo de vecinos para estimar
        el coeficiente de clustering promedio.
        """
        if not graph or not graph.nodes:
            return 0.5  # Valor neutral si no hay grafo

        try:
            # Construir índice de adyacencia desde las aristas
            adj: Dict[str, set] = defaultdict(set)
            for edge_id, edge in graph.edges.items():
                adj[edge.source_id].add(edge.target_id)
                adj[edge.target_id].add(edge.source_id)

            nodes = list(graph.nodes.keys())
            if len(nodes) < 3:
                return 0.5

            # Muestrear hasta 100 nodos para rendimiento
            sample = nodes if len(nodes) <= 100 else \
                [nodes[i] for i in range(0, len(nodes), len(nodes) // 100)][:100]

            total_clustering = 0.0
            for node in sample:
                neighbors = list(adj.get(node, set()))
                k = len(neighbors)
                if k < 2:
                    continue
                # Contar conexiones entre vecinos
                connected = 0
                for i in range(min(k, 20)):
                    for j in range(i + 1, min(k, 20)):
                        if neighbors[j] in adj.get(neighbors[i], set()):
                            connected += 1
                max_possible = (min(k, 20) * (min(k, 20) - 1)) / 2
                if max_possible > 0:
                    total_clustering += connected / max_possible

            coherence = total_clustering / max(1, len(sample))
            self.coherence_history.append(coherence)
            if len(self.coherence_history) > 50:
                self.coherence_history = self.coherence_history[-50:]
            return coherence
        except Exception as e:
            log.debug("PatternMetabolism: error calculando coherencia: %s", e)
            return 0.5

    def apply_noise_penalty(self, graph) -> Dict[str, float]:
        """Ajusta tasas sinápticas basado en coherencia estructural.

        Retorna dict con los nuevos valores.
        """
        coherence = self.structural_coherence(graph)

        try:
            from core import knowledge_reasoner as kr
            decay = kr.SYNAPSE_DECAY_RATE
            strengthen = kr.SYNAPSE_STRENGTHEN_RATE
        except (ImportError, AttributeError):
            decay = 0.01
            strengthen = 0.05

        if coherence < MIN_CLUSTERING_FOR_HEALTH:
            # Red ruidosa: acelerar poda, reducir refuerzo
            new_decay = min(0.1, decay + self.penalty)
            new_strengthen = max(0.005, strengthen - self.penalty / 2)
            self.interventions += 1
            log.debug("PatternMetabolism: red ruidosa (c=%.3f), poda ↑ refuerzo ↓",
                      coherence)
        else:
            # Red saludable: parámetros base
            new_decay = max(0.001, decay - self.penalty / 4)
            new_strengthen = min(0.1, strengthen + self.penalty / 4)

        try:
            import core.knowledge_reasoner as kr
            kr.SYNAPSE_DECAY_RATE = new_decay
            kr.SYNAPSE_STRENGTHEN_RATE = new_strengthen
        except (ImportError, AttributeError):
            pass

        return {
            "coherence": round(coherence, 4),
            "synapse_decay": round(new_decay, 4),
            "synapse_strengthen": round(new_strengthen, 4),
        }

    def status(self) -> Dict[str, Any]:
        return {
            "last_coherence": round(self.coherence_history[-1], 4) if self.coherence_history else 0,
            "interventions": self.interventions,
            "healthy": (self.coherence_history[-1] >= MIN_CLUSTERING_FOR_HEALTH) if self.coherence_history else True,
        }


# ═══════════════════════════════════════════════════════════════════════════════
# ORQUESTADOR: MarocuaiArchitecture
# ═══════════════════════════════════════════════════════════════════════════════

class MarocuaiArchitecture:
    """Orquestador de los 4 mecanismos Marocuai.

    Se integra en el ciclo vital de EIDOS (eidos_vivo.py) como hooks
    que se llaman en cada fase: perceive → decide → act → learn → reflect.

    También expone cycle_hook() como método unificado.

    Uso en eidos_vivo.py:
        from core.eidos_marocuai import get_marocuai
        self.marocuai = get_marocuai()

        # En _perceive_phase():
        self.marocuai.cycle_hook("perceive", graph_state)

        # En _consolidate_phase():
        self.marocuai.cycle_hook("learn", graph_state)
    """

    def __init__(self):
        self.energy = EnergyBudget()
        self.inference = ActiveInference()
        self.homeostasis = Homeostasis()
        self.metabolism = PatternMetabolism()
        self._reasoner = None
        self._graph_snapshot: Dict[str, float] = {}
        self._daily_reset_checked = False

    def _get_reasoner(self):
        """Obtiene el reasoner bajo demanda (evita import circular)."""
        if self._reasoner is None:
            try:
                from core.knowledge_reasoner import get_reasoner
                self._reasoner = get_reasoner()
            except Exception:
                pass
        return self._reasoner

    def _snapshot_graph_state(self) -> Dict[str, float]:
        """Captura el estado actual de potenciales neuronales del grafo."""
        reasoner = self._get_reasoner()
        if not reasoner or not reasoner.graph or not reasoner.graph.nodes:
            return {}

        state = {}
        for node_id, node in reasoner.graph.nodes.items():
            try:
                potential = getattr(node, 'potential', 0.1)
                state[node_id] = float(potential)
            except Exception:
                state[node_id] = 0.1
        self._graph_snapshot = state
        return state

    def _check_daily_reset(self) -> None:
        """Reinicia energía si han pasado 24h desde el último replenish."""
        now = time.time()
        if now - self.energy._last_replenish > 86400:  # 24h
            self.energy.replenish()
            self._daily_reset_checked = True

    # ── Hooks por fase ─────────────────────────────────────────────────────────

    def perceive_hook(self) -> Dict[str, Any]:
        """Fase PERCEIVE: inferencia activa + snapshot del grafo."""
        if not self.energy.consume("perceive"):
            return {"skipped": True, "reason": "sin_energia"}

        state = self._snapshot_graph_state()
        fe = self.inference.step(state) if state else 0.0
        self._check_daily_reset()

        return {
            "free_energy": round(fe, 5),
            "nodes_sampled": len(state),
            "surprise": round(self.inference.surprise_level(), 5),
        }

    def decide_hook(self) -> Dict[str, Any]:
        """Fase DECIDE: homeostasis ajusta excitabilidad."""
        if not self.energy.consume("decide"):
            return {"skipped": True, "reason": "sin_energia"}

        state = self._graph_snapshot or self._snapshot_graph_state()
        new_boost = self.homeostasis.adjust_boost(state)

        return {
            "new_boost": round(new_boost, 4),
            "homeostasis": self.homeostasis.status(),
        }

    def act_hook(self) -> Dict[str, Any]:
        """Fase ACT: costo energético por actuar."""
        if not self.energy.consume("act"):
            return {"skipped": True, "reason": "sin_energia"}
        return {"energy_ok": True}

    def learn_hook(self) -> Dict[str, Any]:
        """Fase LEARN: metabolismo de patrones ajusta plasticidad."""
        if not self.energy.consume("learn"):
            return {"skipped": True, "reason": "sin_energia"}

        reasoner = self._get_reasoner()
        graph = reasoner.graph if reasoner else None
        result = self.metabolism.apply_noise_penalty(graph)

        return {
            "metabolism": result,
            "pattern_health": self.metabolism.status(),
        }

    def reflect_hook(self) -> Dict[str, Any]:
        """Fase REFLECT: evaluación global + posible terminación forzada."""
        if not self.energy.consume("reflect"):
            return {"skipped": True, "reason": "sin_energia"}

        state = self._graph_snapshot or self._snapshot_graph_state()
        fe = self.inference.free_energy()
        should_stop = self.homeostasis.should_force_terminate()

        return {
            "free_energy": round(fe, 5),
            "should_force_terminate": should_stop,
            "energy_status": self.energy.status(),
        }

    # ── API unificada ──────────────────────────────────────────────────────────

    def cycle_hook(self, phase: str) -> Dict[str, Any]:
        """Hook unificado llamado desde eidos_vivo._cycle() en cada fase.

        Args:
            phase: "perceive" | "decide" | "act" | "learn" | "reflect"

        Returns:
            dict con métricas de la fase (o {"skipped": True} si sin energía)
        """
        hooks = {
            "perceive": self.perceive_hook,
            "decide": self.decide_hook,
            "act": self.act_hook,
            "learn": self.learn_hook,
            "reflect": self.reflect_hook,
        }
        hook = hooks.get(phase)
        if hook:
            try:
                return hook()
            except Exception as e:
                log.error("Marocuai hook '%s' error: %s", phase, e)
                return {"error": str(e)}
        return {"error": f"fase desconocida: {phase}"}

    def status_report(self) -> Dict[str, Any]:
        """Informe completo del estado Marocuai."""
        return {
            "energy": self.energy.status(),
            "free_energy_latest": round(self.inference.surprise_level(), 5),
            "homeostasis": self.homeostasis.status(),
            "metabolism": self.metabolism.status(),
            "neuron_boost": _get_neuron_boost(),
            "synapse_decay": _get_synapse_decay(),
        }


# ── Helpers ────────────────────────────────────────────────────────────────────

def _get_neuron_boost() -> float:
    try:
        import core.knowledge_reasoner as kr
        return kr.NEURON_ACTIVATION_BOOST
    except Exception:
        return 0.15


def _get_synapse_decay() -> float:
    try:
        import core.knowledge_reasoner as kr
        return kr.SYNAPSE_DECAY_RATE
    except Exception:
        return 0.01


# ── Singleton ──────────────────────────────────────────────────────────────────

_marocuai: Optional[MarocuaiArchitecture] = None


def get_marocuai() -> MarocuaiArchitecture:
    """Retorna la instancia singleton de la arquitectura Marocuai."""
    global _marocuai
    if _marocuai is None:
        _marocuai = MarocuaiArchitecture()
    return _marocuai


# ── CLI ────────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    import argparse
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")

    parser = argparse.ArgumentParser(description="Marocuai Cognitive Architecture")
    parser.add_argument("--status", action="store_true", help="Mostrar estado")
    parser.add_argument("--reset-energy", action="store_true", help="Reiniciar energía")
    args = parser.parse_args()

    m = get_marocuai()

    if args.reset_energy:
        m.energy.replenish()
        print("Energía reiniciada.")

    if args.status:
        print(json.dumps(m.status_report(), indent=2, ensure_ascii=False))
