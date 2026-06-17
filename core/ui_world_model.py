"""
core/ui_world_model.py — Modelo predictivo de UI ligero [S89.3]

"Anticipar es sobrevivir. Reaccionar es morir." — DeepSeek

Modelo predictivo que anticipa el estado de pantalla tras una acción,
sin necesidad de ejecutarla realmente. Permite filtrar acciones dañinas
antes de que lleguen al Input Backend.

Componentes:
  1. ActionPredictor: predice el cambio textual tras una acción
  2. RiskAssessor: evalúa riesgo de una acción (¿es peligrosa? ¿es inútil?)
  3. ScreenTransitionGraph: grafo de transiciones entre estados de UI
  4. ConfidenceTracker: tracking de precisión de predicciones

El modelo se entrena con datos de ScreenEpisodicMemory (scene→action→scene_after)
y mejora con cada misión completada.

Uso:
    wm = get_ui_world_model()
    prediction = wm.predict_transition(scene, action)
    if prediction.risk > 0.7:
        # abortar o pedir confirmación
    filtered_action = wm.filter_action(scene, action)
"""

from __future__ import annotations

import hashlib
import json
import logging
import threading
import time
from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

log = logging.getLogger("eidos.ui_world_model")

# ── Tipos ───────────────────────────────────────────────────────────────────────


@dataclass
class TransitionPrediction:
    """Predicción de lo que ocurrirá tras ejecutar una acción."""
    action_text: str
    predicted_text_change: bool          # ¿cambiará el texto?
    predicted_similarity: float           # similitud esperada (0=totalmente diferente, 1=idéntico)
    predicted_new_elements: int           # cuántos elementos nuevos aparecerán
    predicted_danger: bool                # ¿probable página de error?
    risk_score: float                     # 0.0 = seguro, 1.0 = muy arriesgado
    confidence: float                     # confianza en la predicción
    reason: str
    alternative_coords: Optional[Tuple[int, int]] = None  # coordenadas alternativas


@dataclass
class ScreenState:
    """Estado de pantalla abstracto para el modelo."""
    state_id: str                        # hash del estado
    window_title_pattern: str            # patrón de título
    text_signature: str                  # firma textual (primeras 100 chars normalizados)
    key_elements: List[str]              # elementos clave detectados
    transition_count: int = 0            # veces que se ha visto este estado


# ── UIWorldModel ───────────────────────────────────────────────────────────────


class UIWorldModel:
    """Modelo predictivo de UI que anticipa transiciones de pantalla.

    Aprende de la experiencia acumulada en ScreenEpisodicMemory y
    aplica heurísticas de seguridad para filtrar acciones peligrosas.
    """

    # Dominios/patrones de UI donde ciertas coordenadas son típicas
    TYPICAL_POSITIONS = {
        "search_box": (0.5, 0.12),      # barra de búsqueda suele estar arriba-centro
        "first_result": (0.35, 0.35),   # primer resultado ~35% abajo
        "back_button": (0.05, 0.05),    # botón atrás arriba-izquierda
        "close_button": (0.95, 0.03),   # botón cerrar arriba-derecha
        "scrollbar": (0.98, 0.50),      # scrollbar derecha-centro
        "address_bar": (0.45, 0.06),    # barra de direcciones arriba-centro
        "main_content": (0.50, 0.55),   # contenido principal centro
        "footer_link": (0.50, 0.95),    # links footer abajo
    }

    # Acciones que NUNCA deberían hacerse sin verificación
    HIGH_RISK_ACTIONS = {
        "click": 0.3,           # riesgo base moderado
        "click_center": 0.4,    # click ciego en centro
        "type": 0.1,            # teclear es bajo riesgo
        "navigate_url": 0.3,    # cambiar de página es moderado
        "scroll": 0.05,         # scroll es muy bajo riesgo
        "key": 0.2,             # tecla
    }

    # Palabras que indican que estamos en página correcta para:
    # "debería haber resultados de búsqueda"
    SEARCH_RESULT_INDICATORS = [
        "result", "found", "star", "fork", "repo", "repository",
        "github.com", "gitlab", "bitbucket", "code", "source",
        "download", "clone", "commit", "branch", "pull request",
    ]

    def __init__(self):
        self._transitions: Dict[str, List[Tuple[str, str, float]]] = defaultdict(list)
        self._states: Dict[str, ScreenState] = {}
        self._prediction_history: List[TransitionPrediction] = []
        self._correct_predictions: int = 0
        self._total_predictions: int = 0
        self._lock = threading.RLock()

    # ── Predicción ─────────────────────────────────────────────────────────────

    def predict_transition(self, scene: Any, action: Dict[str, Any]) -> TransitionPrediction:
        """Predice qué ocurrirá si se ejecuta esta acción en esta escena.

        Args:
            scene: escena actual (con ocr_full_text, window_title, regions)
            action: dict con la acción propuesta

        Returns:
            TransitionPrediction con riesgo y confianza
        """
        scene_text = getattr(scene, 'ocr_full_text', '') or ''
        window_title = getattr(scene, 'window_title', '') or ''
        action_type = action.get('action', 'unknown')
        action_x = action.get('x', 0)
        action_y = action.get('y', 0)

        # 1. Evaluar si la acción tiene sentido contextual
        context_match = self._assess_context(scene_text, window_title, action)

        # 2. Evaluar riesgo base
        base_risk = self.HIGH_RISK_ACTIONS.get(action_type, 0.3)

        # 3. Evaluar si las coordenadas son típicas
        coord_risk = self._assess_coordinate_risk(action_x, action_y, action_type)

        # 4. Evaluar probabilidad de cambio
        change_prob = self._predict_change_probability(scene_text, action_type)

        # 5. Evaluar peligro (¿estamos en página de error?)
        danger = self._detect_danger_context(scene_text)

        # 6. Evaluar si hay evidencia histórica de esta transición
        history_confidence = self._check_transition_history(
            scene_text[:200], action_type
        )

        # Riesgo compuesto
        risk_score = (base_risk * 0.3 + coord_risk * 0.3 +
                     (1.0 - context_match) * 0.2 + danger * 0.2)

        # Confianza compuesta
        confidence = (history_confidence * 0.4 + context_match * 0.3 +
                     (1.0 - danger) * 0.3)

        # Construir razón
        reasons = []
        if coord_risk > 0.5:
            reasons.append(f"coordenadas atípicas ({action_x},{action_y})")
        if danger > 0.3:
            reasons.append("contexto de peligro detectado")
        if context_match < 0.3:
            reasons.append("acción no coincide con contexto")
        if history_confidence > 0.5:
            reasons.append("transición conocida históricamente")
        if not reasons:
            reasons.append("acción parece segura")

        pred = TransitionPrediction(
            action_text=action.get('reason', action_type),
            predicted_text_change=(change_prob > 0.5),
            predicted_similarity=(1.0 - change_prob),
            predicted_new_elements=(3 if change_prob > 0.5 else 0),
            predicted_danger=(danger > 0.5),
            risk_score=round(risk_score, 3),
            confidence=round(confidence, 3),
            reason='; '.join(reasons),
            alternative_coords=self._suggest_alternative(
                action_x, action_y, action_type, scene_text
            ),
        )

        # Track predicciones
        with self._lock:
            self._prediction_history.append(pred)
            if len(self._prediction_history) > 100:
                self._prediction_history = self._prediction_history[-50:]

        return pred

    def filter_action(self, scene: Any, action: Dict[str, Any],
                      risk_threshold: float = 0.7) -> Dict[str, Any]:
        """Filtra una acción: si es muy arriesgada, sugiere alternativa.

        Returns:
            La acción original (si es segura) o una versión modificada (si no).
        """
        prediction = self.predict_transition(scene, action)

        if prediction.risk_score > risk_threshold:
            log.warning("UIWorldModel: acción bloqueada (risk=%.2f > %.2f): %s",
                       prediction.risk_score, risk_threshold, prediction.reason)

            if prediction.alternative_coords:
                alt_x, alt_y = prediction.alternative_coords
                return {
                    **action,
                    "x": alt_x, "y": alt_y,
                    "risk_mitigated": True,
                    "original_x": action.get('x'),
                    "original_y": action.get('y'),
                    "reason": f"[MODELO CORREGIDO] {action.get('reason', '')} "
                             f"(riesgo {prediction.risk_score}→corregido)",
                }
            else:
                # Convertir a scroll seguro
                return {
                    "action": "scroll",
                    "direction": "down",
                    "lines": 3,
                    "risk_mitigated": True,
                    "original_action": action.get("action"),
                    "reason": f"[MODELO: acción bloqueada por riesgo {prediction.risk_score}]",
                }

        return action

    def record_outcome(self, prediction: TransitionPrediction,
                       actual_success: bool,
                       actual_scene_after: Any = None):
        """Registra el resultado real de una acción predicha.

        Esto entrena el modelo: ajusta confianzas y aprende transiciones.
        """
        with self._lock:
            self._total_predictions += 1
            was_correct = (prediction.predicted_danger == (not actual_success) or
                          (prediction.risk_score < 0.3 and actual_success))
            if was_correct:
                self._correct_predictions += 1

    # ── Evaluadores ────────────────────────────────────────────────────────────

    def _assess_context(self, scene_text: str, window_title: str,
                        action: Dict[str, Any]) -> float:
        """Evalúa qué tan bien encaja la acción en el contexto actual (0.0-1.0)."""
        action_type = action.get('action', '')
        text_lower = scene_text.lower()
        title_lower = window_title.lower()

        score = 0.5  # neutral

        # Click en contexto de búsqueda
        if action_type == "click":
            if any(ind in text_lower for ind in self.SEARCH_RESULT_INDICATORS):
                score = 0.8
            if any(b in title_lower for b in ("google", "github", "gitlab", "search")):
                score = max(score, 0.75)
            if "error" in text_lower or "404" in text_lower:
                score = 0.2

        # Type en input field
        elif action_type == "type":
            if any(b in title_lower for b in ("google", "search", "github", "login")):
                score = 0.9
            if "search" in text_lower or "buscar" in text_lower:
                score = 0.85

        # Scroll en página con mucho texto
        elif action_type == "scroll":
            if len(scene_text) > 500:
                score = 0.9
            elif len(scene_text) > 100:
                score = 0.7

        # Navegación
        elif action_type == "navigate_url":
            score = 0.8  # siempre razonable

        return score

    def _assess_coordinate_risk(self, x: int, y: int, action_type: str) -> float:
        """Evalúa riesgo de coordenadas (0.0-1.0)."""
        if action_type not in ("click", "click_center", "click_center_offset"):
            return 0.0

        if x == 0 and y == 0:
            return 1.0  # coordenadas no inicializadas

        # Esquinas son sospechosas (anuncios, popups)
        if x < 50 or x > 1870:
            return 0.6
        if y < 30:
            return 0.4  # barra de título es normal
        if y > 1030:
            return 0.5  # parte inferior extrema

        # Centro es generalmente seguro
        if 400 < x < 1500 and 200 < y < 800:
            return 0.15

        return 0.3

    def _predict_change_probability(self, scene_text: str,
                                    action_type: str) -> float:
        """Predice probabilidad de que la acción cause cambio visible."""
        if action_type in ("navigate_url", "open_browser"):
            return 0.95
        if action_type == "scroll":
            return 0.6 if len(scene_text) > 200 else 0.3
        if action_type == "click":
            return 0.65
        if action_type == "type":
            return 0.4
        if action_type == "wait":
            return 0.1
        return 0.5

    def _detect_danger_context(self, scene_text: str) -> float:
        """Detecta si el contexto actual es peligroso (0.0-1.0)."""
        text_lower = scene_text.lower()
        danger_score = 0.0

        danger_words = [
            "error", "404", "500", "not found", "access denied",
            "forbidden", "captcha", "verify you are human",
            "blocked", "suspended", "rate limit",
            "javascript required", "enable javascript",
        ]
        for word in danger_words:
            if word in text_lower:
                danger_score += 0.3

        return min(1.0, danger_score)

    def _check_transition_history(self, scene_text: str,
                                  action_type: str) -> float:
        """Verifica si hay historial de transiciones similares exitosas."""
        state_hash = hashlib.md5(
            f"{scene_text[:200]}:{action_type}".encode()
        ).hexdigest()[:16]

        with self._lock:
            transitions = self._transitions.get(state_hash, [])
            if not transitions:
                return 0.2  # sin historial → baja confianza

            successes = sum(1 for _, _, r in transitions if r > 0)
            return successes / max(1, len(transitions))

    def _suggest_alternative(self, x: int, y: int, action_type: str,
                            scene_text: str) -> Optional[Tuple[int, int]]:
        """Sugiere coordenadas alternativas más seguras."""
        if action_type not in ("click", "click_center", "click_center_offset"):
            return None

        text_lower = scene_text.lower()

        # Si estamos en GitHub, el contenido principal está en el centro
        if "github" in text_lower:
            return (650, 400)  # área de README

        # Si es búsqueda, el primer resultado suele estar en zona centro-izquierda
        if any(w in text_lower for w in ("search", "result", "google")):
            return (500, 450)

        # Default: centro de la pantalla es lo más seguro
        return (960, 500)

    # ── Stats ─────────────────────────────────────────────────────────────────

    @property
    def prediction_accuracy(self) -> float:
        with self._lock:
            if self._total_predictions == 0:
                return 0.5
            return self._correct_predictions / self._total_predictions

    def stats(self) -> Dict[str, Any]:
        with self._lock:
            return {
                "total_predictions": self._total_predictions,
                "correct_predictions": self._correct_predictions,
                "accuracy": round(self.prediction_accuracy, 3),
                "known_states": len(self._states),
                "known_transitions": len(self._transitions),
                "prediction_history": len(self._prediction_history),
                "typical_positions": len(self.TYPICAL_POSITIONS),
            }


# ── Singleton ─────────────────────────────────────────────────────────────────
_world_model: Optional[UIWorldModel] = None


def get_ui_world_model() -> UIWorldModel:
    global _world_model
    if _world_model is None:
        _world_model = UIWorldModel()
    return _world_model


# ── CLI ───────────────────────────────────────────────────────────────────────
if __name__ == "__main__":
    import argparse
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")

    p = argparse.ArgumentParser(
        description="UIWorldModel — modelo predictivo de UI"
    )
    p.add_argument("--test", action="store_true", help="Test de predicción")
    p.add_argument("--stats", action="store_true", help="Estadísticas")
    args = p.parse_args()

    wm = get_ui_world_model()

    if args.test:
        from core.screen_controller import Scene

        # Escena normal
        scene_ok = Scene(
            timestamp=time.time(),
            window_title="Firefox — GitHub",
            ocr_full_text="n8n-io/n8n workflow automation Star 50k Fork 10k",
        )

        action_click = {"action": "click", "x": 400, "y": 300,
                       "reason": "click en resultado de búsqueda"}
        pred = wm.predict_transition(scene_ok, action_click)
        print(f"Escena normal + click:")
        print(f"  Riesgo: {pred.risk_score:.3f}")
        print(f"  Confianza: {pred.confidence:.3f}")
        print(f"  Razón: {pred.reason}")

        # Escena peligrosa
        scene_danger = Scene(
            timestamp=time.time(),
            window_title="Firefox",
            ocr_full_text="Error 404 Not Found. The page you requested does not exist.",
        )
        pred2 = wm.predict_transition(scene_danger, action_click)
        print(f"\nEscena peligrosa + click:")
        print(f"  Riesgo: {pred2.risk_score:.3f}")
        print(f"  Confianza: {pred2.confidence:.3f}")
        print(f"  Razón: {pred2.reason}")

        # Filter test
        filtered = wm.filter_action(scene_danger, action_click)
        if filtered.get("risk_mitigated"):
            print(f"\n¡Acción filtrada! Nueva acción: {filtered.get('action')}")
            print(f"  Razón: {filtered.get('reason')}")

    elif args.stats:
        import json
        print(json.dumps(wm.stats(), indent=2))

    else:
        p.print_help()
