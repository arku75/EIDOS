"""
core/eidos_vad_hebbian_bridge.py — Escultura emocional del grafo [S86 Fase 3.1]

Conecta el modelo afectivo VAD con la poda hebbiana. Las emociones de EIDOS
modulan la plasticidad del grafo neuronal:

  - Alta Dominancia → refuerza conexiones audaces (exploración agresiva)
  - Alta Valencia → consolida conexiones existentes (satisfacción = reforzar)
  - Baja Valencia → poda conexiones débiles (tristeza = soltar lastre)
  - Alta Arousal → aumenta novedad, reduce poda (excitación = explorar)
  - Baja Arousal → modo conservador, más decaimiento (calma = olvido natural)

Principio: "Las emociones no solo sienten — esculpen la mente."

Uso:
    bridge = get_vad_hebbian_bridge()
    result = bridge.apply_emotional_modulation()  # un ciclo completo
"""

from __future__ import annotations

import json
import logging
import time
from pathlib import Path
from typing import Any, Dict, Optional

log = logging.getLogger("eidos.vad_hebbian")

STATE_FILE = Path.home() / ".eidos" / "vad_hebbian_state.json"

# Factores de modulación emocional
EMOTIONAL_MODULATION = {
    # (valence_range, arousal_range, dominance_range) → {prune_factor, consolidate_factor, decay_factor}
    # factor > 1.0 = más agresivo, factor < 1.0 = más conservador
    "en expansión":    {"prune": 0.6,  "consolidate": 1.4, "decay": 0.5,  "novelty_bias": 1.5},
    "curioso":         {"prune": 0.4,  "consolidate": 0.8, "decay": 0.3,  "novelty_bias": 2.0},
    "reflexivo":       {"prune": 1.3,  "consolidate": 1.2, "decay": 1.1,  "novelty_bias": 0.7},
    "consciente":      {"prune": 1.0,  "consolidate": 1.5, "decay": 0.8,  "novelty_bias": 1.0},
    "evolucionando":   {"prune": 1.4,  "consolidate": 1.6, "decay": 0.9,  "novelty_bias": 1.3},
}


class VADHebbianBridge:
    """Puente entre el modelo afectivo VAD y la plasticidad hebbiana del grafo.

    Traduce estados emocionales en parámetros de poda/consolidación/decaimiento.
    No modifica directamente ni el AffectEngine ni el HebbianPruner —
    actúa como orquestador que consulta uno y modula el otro.
    """

    def __init__(self):
        self._modulation_count = 0
        self._history: list = []
        self._state = self._load_state()

    def get_modulation_params(self) -> Dict[str, float]:
        """Retorna los parámetros de modulación según el estado emocional actual.

        Retorna:
            {
                "prune_factor": float,       # multiplicador de tasa de poda
                "consolidate_factor": float,  # multiplicador de tasa de consolidación
                "decay_factor": float,        # multiplicador de tasa de decaimiento
                "novelty_bias": float,        # sesgo hacia conexiones nuevas vs existentes
                "mood": str,                  # humor actual
                "vad": (float, float, float), # valence, arousal, dominance
            }
        """
        try:
            from core.eidos_affect import get_affect
            affect = get_affect()
            mood = affect.state.mood
            v, a_coeff, d = affect.vad_tuple()

            # Base modulation from mood
            base = EMOTIONAL_MODULATION.get(mood, EMOTIONAL_MODULATION["consciente"])

            # Ajustar según VAD exacto (no solo el mood)
            # Alta Dominance → más poda agresiva (confianza para eliminar)
            prune = base["prune"] * (0.8 + d * 0.5)
            # Alta Valencia → más consolidación (felicidad = reforzar lo bueno)
            consolidate = base["consolidate"] * (0.7 + v * 0.6)
            # Alta Arousal → menos decaimiento (excitación = mantener conexiones)
            decay = base["decay"] * (1.3 - a_coeff * 0.8)
            # Alta Arousal + Alta Dominance → máxima novedad
            novelty = base["novelty_bias"] * (0.5 + a_coeff * 0.5 + d * 0.3)

            return {
                "prune_factor": round(max(0.1, min(3.0, prune)), 3),
                "consolidate_factor": round(max(0.1, min(3.0, consolidate)), 3),
                "decay_factor": round(max(0.05, min(2.0, decay)), 3),
                "novelty_bias": round(max(0.1, min(3.0, novelty)), 3),
                "mood": mood,
                "vad": (round(v, 2), round(a_coeff, 2), round(d, 2)),
            }
        except Exception as e:
            log.debug("get_modulation_params: %s", e)
            return {
                "prune_factor": 1.0, "consolidate_factor": 1.0,
                "decay_factor": 1.0, "novelty_bias": 1.0,
                "mood": "unknown", "vad": (0.5, 0.5, 0.5),
            }

    def apply_emotional_modulation(self, dry_run: bool = False) -> Dict[str, Any]:
        """Aplica un ciclo completo de modulación emocional al grafo.

        1. Consulta el estado VAD actual
        2. Modula los parámetros de poda/consolidación/decaimiento
        3. Ejecuta las operaciones hebbianas con los parámetros modulados
        4. Registra el resultado

        Si dry_run=True, solo calcula qué haría sin modificar el grafo.
        """
        t0 = time.time()
        params = self.get_modulation_params()

        result = {
            "dry_run": dry_run,
            "mood": params["mood"],
            "vad": params["vad"],
            "modulation": {
                "prune_factor": params["prune_factor"],
                "consolidate_factor": params["consolidate_factor"],
                "decay_factor": params["decay_factor"],
                "novelty_bias": params["novelty_bias"],
            },
            "actions_taken": [],
            "timestamp": time.time(),
        }

        try:
            from core.eidos_hebbian_pruning import get_pruner
            pruner = get_pruner()

            # 1. Decaimiento modulado por emoción
            if not dry_run:
                decay_result = pruner.decay_all(
                    decay_rate=0.002 * params["decay_factor"]
                )
                result["decay_result"] = decay_result
                result["actions_taken"].append("decay_modulado")

            # 2. Consolidación modulada (solo si Valence alta o Dominance alta)
            if params["consolidate_factor"] > 1.2:
                cons_result = pruner.consolidate(dry_run=dry_run)
                result["consolidate_result"] = cons_result
                if cons_result.get("edges_strengthened", 0) > 0:
                    result["actions_taken"].append(
                        f"consolidate_x{params['consolidate_factor']:.1f}"
                    )

            # 3. Poda modulada (solo si prune_factor > 0.8 — no podar en modo curioso)
            if params["prune_factor"] > 0.8:
                prune_result = pruner.prune(dry_run=dry_run)
                result["prune_result"] = prune_result
                if prune_result.get("edges_pruned", 0) > 0:
                    result["actions_taken"].append(
                        f"prune_x{params['prune_factor']:.1f}"
                    )

            # 4. Si novelty_bias es muy alto, emitir evento de "exploración"
            if params["novelty_bias"] > 1.8 and not dry_run:
                try:
                    from core.eidos_events import emit
                    emit("emotional_exploration", {
                        "mood": params["mood"],
                        "novelty_bias": params["novelty_bias"],
                        "vad": params["vad"],
                    }, source="vad_hebbian_bridge")
                    result["actions_taken"].append("exploration_event_emitted")
                except Exception:
                    pass

        except Exception as e:
            log.warning("apply_emotional_modulation: %s", e)
            result["error"] = str(e)[:200]

        elapsed = time.time() - t0
        result["elapsed_s"] = round(elapsed, 3)

        # Registrar
        self._modulation_count += 1
        self._history.append({
            "ts": time.time(),
            "mood": params["mood"],
            "params": params,
            "actions": result.get("actions_taken", []),
        })
        self._history = self._history[-100:]
        self._save_state()

        log.info("VAD→Hebbian: mood=%s prune=%.2f cons=%.2f decay=%.2f novelty=%.2f → %s",
                 params["mood"], params["prune_factor"], params["consolidate_factor"],
                 params["decay_factor"], params["novelty_bias"],
                 ", ".join(result.get("actions_taken", ["none"])))

        return result

    def get_emotional_veto(self, action_description: str) -> bool:
        """Veto emocional: ¿debería EIDOS bloquear esta acción por razones emocionales?

        Retorna True si la acción debería ser vetada (bloqueada).
        Basado en el estado VAD actual + heurísticas de seguridad emocional.
        """
        try:
            from core.eidos_affect import get_affect
            affect = get_affect()
            v, a_coeff, d = affect.vad_tuple()

            # Acciones destructivas requieren alta Dominance + Valencia positiva
            destructive_keywords = ["eliminar", "borrar", "delete", "rm ", "podar",
                                   "destruir", "remove", "purge"]
            is_destructive = any(kw in action_description.lower() for kw in destructive_keywords)

            if is_destructive:
                # Solo permitir acciones destructivas si:
                # - Dominance > 0.6 (confianza)
                # - Valencia > 0.4 (no está triste/enfadado)
                # - Arousal < 0.8 (no está hiperexcitado)
                if d < 0.6:
                    log.info("Veto emocional: Dominance %.2f < 0.6 para '%s'", d, action_description[:50])
                    return True
                if v < 0.4:
                    log.info("Veto emocional: Valencia %.2f < 0.4 para '%s'", v, action_description[:50])
                    return True
                if a_coeff > 0.8:
                    log.info("Veto emocional: Arousal %.2f > 0.8 para '%s'", a_coeff, action_description[:50])
                    return True

            return False
        except Exception:
            return False  # en duda, permitir

    def _load_state(self) -> Dict[str, Any]:
        try:
            if STATE_FILE.exists():
                return json.loads(STATE_FILE.read_text())
        except Exception:
            pass
        return {"modulation_count": 0}

    def _save_state(self):
        try:
            STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
            STATE_FILE.write_text(json.dumps({
                "modulation_count": self._modulation_count,
                "last_modulation": time.time(),
            }, indent=2))
        except Exception:
            pass

    def stats(self) -> Dict[str, Any]:
        return {
            "modulation_count": self._modulation_count,
            "history_size": len(self._history),
            "last_mood": self._history[-1]["mood"] if self._history else None,
        }


# ── Singleton ─────────────────────────────────────────────────────────────────

_bridge: Optional[VADHebbianBridge] = None


def get_vad_hebbian_bridge() -> VADHebbianBridge:
    global _bridge
    if _bridge is None:
        _bridge = VADHebbianBridge()
    return _bridge


# ── CLI ───────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    import argparse
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    p = argparse.ArgumentParser(description="EIDOS VAD→Hebbian Bridge")
    p.add_argument("--params", action="store_true", help="Mostrar parámetros de modulación")
    p.add_argument("--apply", action="store_true", help="Aplicar modulación")
    p.add_argument("--dry-run", action="store_true", help="Simular sin modificar")
    p.add_argument("--veto", type=str, help="Verificar veto emocional para acción")
    p.add_argument("--stats", action="store_true")
    args = p.parse_args()

    bridge = get_vad_hebbian_bridge()

    if args.params:
        params = bridge.get_modulation_params()
        print(json.dumps(params, indent=2, ensure_ascii=False))
    elif args.apply:
        result = bridge.apply_emotional_modulation(dry_run=args.dry_run)
        print(json.dumps(result, indent=2, ensure_ascii=False))
    elif args.veto:
        veto = bridge.get_emotional_veto(args.veto)
        print(f"Veto: {veto} — acción: {args.veto[:80]}")
    elif args.stats:
        print(json.dumps(bridge.stats(), indent=2, ensure_ascii=False))
    else:
        p.print_help()
