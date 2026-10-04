"""
core/action_verifier.py — Verificación post-acción y autocorrección [S89.1]

"El que no verifica, se estanca. El que se estanca, muere." — DeepSeek

Ciclo cerrado de verificación post-acción para ScreenController:
  1. Captura escena BEFORE → ejecuta acción → captura escena AFTER
  2. StateChecker compara escenas: ¿cambió algo? ¿cambió lo esperado?
  3. Si no hubo cambio → cuenta como stuck → recovery strategies
  4. Si hubo cambio inesperado → evalúa si es peligroso → backtrack
  5. Confianza acumulativa: acciones verificadas ganan peso con el tiempo

Estrategias de recovery (en orden):
  1. Esperar y reintentar (latencia de carga)
  2. Scroll para forzar renderizado lazy
  3. Escape + reintento (cerrar modales/popups)
  4. Backtracking: volver atrás y probar ruta alternativa
  5. Replanificar con LLM

Uso:
    verifier = ActionVerifier()
    result = verifier.verify(scene_before, scene_after, action, expectation)
    if not result.verified:
        recovery = verifier.recover(scene_after, action, result)
"""

from __future__ import annotations

import difflib
import logging
import re
import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Dict, List, Optional, Tuple

log = logging.getLogger("eidos.action_verifier")


# ── Tipos ───────────────────────────────────────────────────────────────────────


class Verdict(Enum):
    VERIFIED = "verified"            # acción produjo cambio esperado
    STUCK = "stuck"                  # sin cambios detectables
    UNEXPECTED = "unexpected"        # cambió pero no lo esperado
    DANGER = "danger"                # cambio sugiere error/página de error
    SLOW = "slow"                    # cambio parcial, probablemente cargando


class RecoveryAction(Enum):
    RETRY = "retry"                  # reintentar misma acción
    WAIT_RETRY = "wait_retry"        # esperar + reintentar
    SCROLL_RETRY = "scroll_retry"    # scroll + reintentar
    ESCAPE_RETRY = "escape_retry"    # Escape + reintentar
    BACK = "back"                    # volver atrás (Alt+Left)
    SCROLL_DOWN = "scroll_down"      # scroll para ver más
    REPLAN = "replan"                # pedir nuevo plan al LLM
    ABORT = "abort"                  # abortar misión


@dataclass
class VerificationResult:
    """Resultado de verificación post-acción."""
    verdict: Verdict
    verified: bool
    confidence: float
    reason: str
    scene_before_text: str = ""
    scene_after_text: str = ""
    text_diff: str = ""
    new_elements: List[str] = field(default_factory=list)
    lost_elements: List[str] = field(default_factory=list)
    text_similarity: float = 0.0
    recovery_suggested: Optional[RecoveryAction] = None
    elapsed: float = 0.0


# ── StateChecker ───────────────────────────────────────────────────────────────


class StateChecker:
    """Compara dos escenas (before/after) para determinar si una acción tuvo efecto.

    Usa múltiples señales:
    1. Diff de texto OCR (¿cambió el contenido?)
    2. Cambio de título de ventana (¿navegamos a otra página?)
    3. Nuevos elementos detectados (¿apareció lo esperado?)
    4. Palabras de error/peligro (¿fuimos a página de error?)
    5. Similitud textual (¿estamos en la misma página?)
    """

    # Palabras que sugieren página de error REAL (no documentación/código)
    # S92: Separadas en niveles de severidad para reducir falsos positivos
    DANGER_WORDS_CRITICAL = [
        # Estas indican error HTTP o bloqueo casi seguro
        "404 not found", "403 forbidden", "500 internal server error",
        "502 bad gateway", "503 service unavailable",
        "access denied", "verify you are human", "captcha",
        "too many requests", "rate limit exceeded",
        "your account has been suspended",
    ]

    DANGER_WORDS_MODERATE = [
        # Estas PUEDEN ser error, pero también aparecen en docs/código
        # Requieren señal adicional (sin código alrededor, en título, etc.)
        "blocked", "suspended", "javascript required",
        "enable javascript", "browser not supported",
        "access forbidden",
    ]

    # Patrones que indican contexto seguro (código, terminal, documentación)
    # Si el texto contiene estos patrones, las danger words probablemente
    # son parte de código/documentación, no de una página de error real
    SAFE_CONTEXT_PATTERNS = [
        # Código Python
        r'\bdef\s+\w+\s*\(',           # definición de función
        r'\bclass\s+\w+[:\(]',         # definición de clase
        r'\bimport\s+\w+',             # import statement
        r'\bfrom\s+\w+\s+import\b',    # from X import Y
        r'^\s*>>>\s',                  # prompt de Python interactivo
        r'^\s*\.\.\.\s',               # continuación en Python
        # Terminal / shell
        r'^\s*\$[\s{]',                # prompt de shell
        r'^\s*#\s',                    # comentario/root prompt
        r'Traceback\s*\(',             # traceback Python
        r'File\s+"[^"]+",\s*line\s+\d+',  # file:line de traceback
        r'^\s*\w+@\w+:',               # user@host prompt
        # Documentación
        r'```\w*\n',                   # bloque de código markdown
        r'`[^`]+`',                    # inline code
        r'Example\s*\d*[:.]',          # ejemplos de documentación
        r'Usage\s*:',                  # sección de uso
        r'Syntax\s*:',                 # sección de sintaxis
        # IDE / editor
        r'\bgit\s+(commit|push|pull|status|log|diff)\b',
        r'\b(npm|pnpm|yarn|pip|apt|brew)\s+\w+',
        r'\.py\b|\.js\b|\.ts\b|\.rs\b|\.go\b',  # extensiones de archivo
    ]

    # Palabras que sugieren carga en progreso
    LOADING_WORDS = [
        "loading", "cargando", "please wait", "wait", "spinner",
        "connecting", "fetching",
    ]

    def __init__(self):
        self._stuck_counter: int = 0
        self._consecutive_same: int = 0
        self._last_text_hash: int = 0
        # Compilar patrones de contexto seguro
        self._safe_patterns = [re.compile(p, re.IGNORECASE | re.MULTILINE)
                               for p in self.SAFE_CONTEXT_PATTERNS]

    def compare(self, scene_before: Any, scene_after: Any,
                expected_change: str = "",
                action_type: str = "") -> VerificationResult:
        """Compara dos escenas y determina el veredicto.

        Args:
            scene_before: escena antes de la acción
            scene_after: escena después de la acción
            expected_change: descripción del cambio esperado (ej: "debe aparecer resultados")
            action_type: tipo de acción ejecutada (click, type, scroll, navigate)

        Returns:
            VerificationResult con veredicto y diagnóstico
        """
        t0 = time.time()

        # Extraer textos
        text_before = getattr(scene_before, 'ocr_full_text', '') or ''
        text_after = getattr(scene_after, 'ocr_full_text', '') or ''
        title_before = getattr(scene_before, 'window_title', '') or ''
        title_after = getattr(scene_after, 'window_title', '') or ''

        # 1. Diff textual
        text_diff = self._compute_diff(text_before, text_after)

        # 2. Similitud
        similarity = self._text_similarity(text_before, text_after)

        # 3. Elementos nuevos y perdidos
        new_elems = self._find_new_elements(scene_before, scene_after)
        lost_elems = self._find_lost_elements(scene_before, scene_after)

        # 4. Detectar peligro (con contexto S92)
        danger_detected, danger_reason = self._check_danger(text_after, title_after)

        # 5. Detectar carga
        loading_detected = self._check_loading(text_after)

        # 6. Determinar veredicto
        verdict, reason, recovery = self._determine_verdict(
            similarity=similarity,
            new_count=len(new_elems),
            lost_count=len(lost_elems),
            title_changed=(title_before != title_after),
            danger=danger_detected,
            loading=loading_detected,
            expected_change=expected_change,
            action_type=action_type,
            danger_reason=danger_reason,
        )

        # Track stuck
        text_hash = hash(text_after[:200])
        if text_hash == self._last_text_hash:
            self._consecutive_same += 1
            self._stuck_counter += 1
        else:
            self._consecutive_same = 0
            if verdict != Verdict.STUCK:
                self._stuck_counter = max(0, self._stuck_counter - 1)
        self._last_text_hash = text_hash

        elapsed = time.time() - t0

        return VerificationResult(
            verdict=verdict,
            verified=(verdict == Verdict.VERIFIED),
            confidence=self._compute_confidence(verdict, similarity, len(new_elems)),
            reason=reason,
            scene_before_text=text_before[:500],
            scene_after_text=text_after[:500],
            text_diff=text_diff[:500],
            new_elements=new_elems[:20],
            lost_elements=lost_elems[:20],
            text_similarity=round(similarity, 3),
            recovery_suggested=recovery,
            elapsed=round(elapsed, 3),
        )

    def _compute_diff(self, before: str, after: str) -> str:
        """Calcula diff textual legible."""
        if not before or not after:
            return ""
        before_words = before.split()
        after_words = after.split()
        diff = difflib.unified_diff(
            before_words, after_words,
            lineterm='', n=0
        )
        return '\n'.join(list(diff)[:50])

    def _text_similarity(self, text1: str, text2: str) -> float:
        """Similitud entre dos textos (0.0 = completamente diferente, 1.0 = idéntico)."""
        if not text1 and not text2:
            return 1.0
        if not text1 or not text2:
            return 0.0
        # Jaccard similarity sobre palabras
        words1 = set(text1.lower().split())
        words2 = set(text2.lower().split())
        if not words1 and not words2:
            return 1.0
        intersection = words1 & words2
        union = words1 | words2
        return len(intersection) / max(1, len(union))

    def _find_new_elements(self, before: Any, after: Any) -> List[str]:
        """Encuentra elementos que aparecieron después de la acción."""
        regions_before = set()
        if hasattr(before, 'regions') and before.regions:
            regions_before = {r.text.lower() for r in before.regions if r.text}

        regions_after = getattr(after, 'regions', []) or []
        new = []
        for r in regions_after:
            if r.text and r.text.lower() not in regions_before:
                new.append(r.text)
        return new

    def _find_lost_elements(self, before: Any, after: Any) -> List[str]:
        """Encuentra elementos que desaparecieron después de la acción."""
        regions_after = set()
        if hasattr(after, 'regions') and after.regions:
            regions_after = {r.text.lower() for r in after.regions if r.text}

        regions_before = getattr(before, 'regions', []) or []
        lost = []
        for r in regions_before:
            if r.text and r.text.lower() not in regions_after:
                lost.append(r.text)
        return lost

    def _is_safe_context(self, text: str) -> bool:
        """Determina si el texto parece código, terminal o documentación.

        Si es contexto seguro, las danger words probablemente son parte del
        contenido normal (ej: buscar "python error handling" en Google).

        Returns:
            True si el texto parece código/docs/terminal (no es página de error)
        """
        if not text:
            return False
        # Si hay 2+ patrones de contexto seguro, probablemente es código/docs
        safe_matches = sum(1 for p in self._safe_patterns if p.search(text))
        return safe_matches >= 2

    def _check_danger(self, text: str, title: str = "") -> Tuple[bool, str]:
        """Detecta palabras de peligro/error en el texto con contexto.

        S92: Análisis contextual para reducir falsos positivos.
        - Danger words en título pesan más
        - Danger words en contexto de código/docs se ignoran
        - Palabras críticas (404, captcha) siempre son danger
        - Palabras moderadas requieren corroboración

        Returns:
            (is_danger: bool, reason: str)
        """
        if not text:
            return False, ""

        text_lower = text.lower()
        title_lower = (title or "").lower()

        # 1. Palabras CRÍTICAS: siempre son danger (en texto o título)
        for word in self.DANGER_WORDS_CRITICAL:
            if word in text_lower:
                return True, f"Detectada palabra crítica en texto: '{word}'"
            if word in title_lower:
                return True, f"Detectada palabra crítica en título: '{word}'"

        # 2. Palabras MODERADAS: requieren análisis contextual
        moderate_hits = []
        for word in self.DANGER_WORDS_MODERATE:
            if word in text_lower:
                moderate_hits.append(word)

        if not moderate_hits:
            return False, ""

        # 3. Si la palabra aparece en el TÍTULO → más probable que sea error real
        for word in moderate_hits:
            if word in title_lower:
                return True, f"Palabra '{word}' en título de ventana"

        # 4. Si estamos en contexto de código/docs → no es danger real
        if self._is_safe_context(text):
            return False, ""

        # 5. Si hay 2+ palabras moderadas → probablemente es error real
        if len(moderate_hits) >= 2:
            return True, f"Múltiples indicadores: {', '.join(moderate_hits)}"

        # 6. Una sola palabra moderada sin corroboración → no es danger
        return False, ""

    def _check_loading(self, text: str) -> bool:
        """Detecta indicadores de carga en progreso."""
        text_lower = text.lower()
        return any(word in text_lower for word in self.LOADING_WORDS)

    def _determine_verdict(self, similarity: float, new_count: int,
                           lost_count: int, title_changed: bool,
                           danger: bool, loading: bool,
                           expected_change: str,
                           action_type: str,
                           danger_reason: str = "") -> Tuple[Verdict, str, Optional[RecoveryAction]]:
        """Lógica de decisión del veredicto."""

        # Peligro siempre gana
        if danger:
            return (Verdict.DANGER,
                    danger_reason or "Detectadas palabras de error/peligro en la página",
                    RecoveryAction.BACK)

        # Navegación o cambio grande de página → verificado
        if action_type in ("navigate_url", "open_browser") and title_changed:
            return (Verdict.VERIFIED,
                    f"Navegación exitosa, título cambió",
                    None)

        # Scroll → verificado si cambió algo o estamos en misma página
        if action_type == "scroll":
            if new_count > 0 or lost_count > 0:
                return (Verdict.VERIFIED,
                        f"Scroll efectivo: {new_count} nuevos, {lost_count} perdidos",
                        None)
            elif loading:
                return (Verdict.SLOW,
                        "Página cargando lentamente tras scroll",
                        RecoveryAction.WAIT_RETRY)
            else:
                return (Verdict.STUCK,
                        "Scroll despachado sin efecto observable",
                        RecoveryAction.REPLAN)

        # Type requires an observable state change; execution alone is not success.
        if action_type == "type":
            if new_count > 0 or lost_count > 0 or title_changed or similarity < 0.92:
                return (Verdict.VERIFIED,
                        "Escritura produjo un cambio observable",
                        None)
            return (Verdict.STUCK,
                    f"Escritura sin efecto observable (sim={similarity:.2f})",
                    RecoveryAction.RETRY)

        # Click o acción interactiva
        if action_type in ("click", "click_center", "click_center_offset", "key"):

            # Si cambió mucho → verificado
            if new_count > 3 or title_changed:
                return (Verdict.VERIFIED,
                        f"Acción efectiva: {new_count} elementos nuevos",
                        None)

            # Si hay elementos nuevos → verificado
            if new_count > 0:
                return (Verdict.VERIFIED,
                        f"Aparecieron {new_count} elementos nuevos",
                        None)

            # Si nada cambió y alta similitud → stuck
            if similarity > 0.92:
                if self._stuck_counter >= 3:
                    return (Verdict.STUCK,
                            f"Sin cambios tras {self._stuck_counter} intentos (sim={similarity:.2f})",
                            RecoveryAction.REPLAN)
                elif self._stuck_counter >= 2:
                    return (Verdict.STUCK,
                            f"Stuck detectado (sim={similarity:.2f})",
                            RecoveryAction.ESCAPE_RETRY)
                else:
                    return (Verdict.STUCK,
                            f"Sin cambios detectables (sim={similarity:.2f})",
                            RecoveryAction.SCROLL_RETRY)

            # Si cambió moderadamente → puede ser carga
            if loading:
                return (Verdict.SLOW,
                        "Página aún cargando",
                        RecoveryAction.WAIT_RETRY)

            # Cambio moderado no esperado
            return (Verdict.VERIFIED,
                    f"Cambio moderado detectado (sim={similarity:.2f}, +{new_count})",
                    None)

        # Wait completion is not itself an externally verified effect.
        if action_type == "wait":
            if new_count > 0 or lost_count > 0 or title_changed or similarity < 0.92:
                return (Verdict.VERIFIED, "Cambio observable durante la espera", None)
            return (Verdict.STUCK, "Espera completada sin efecto observable", None)

        # Extraction/evaluation must surface an observable result.
        if action_type in ("extract", "evaluate"):
            if new_count > 0 or lost_count > 0 or title_changed or similarity < 0.92:
                return (Verdict.VERIFIED, "Extracción/evaluación produjo resultado observable", None)
            return (Verdict.STUCK, "Sin resultado observable de extracción/evaluación", None)

        # Unknown actions fail closed: execution is never equivalent to success.
        if new_count > 0 or lost_count > 0 or title_changed or similarity < 0.92:
            return (Verdict.VERIFIED,
                    f"Cambio observable detectado: +{new_count}/-{lost_count} elementos",
                    None)

        return (Verdict.STUCK, "Acción sin efecto observable", RecoveryAction.REPLAN)

    def _compute_confidence(self, verdict: Verdict, similarity: float,
                            new_count: int) -> float:
        """Calcula confianza del veredicto (0.0 a 1.0)."""
        if verdict == Verdict.VERIFIED:
            if new_count > 5:
                return 0.95
            elif new_count > 0:
                return 0.85
            elif similarity < 0.5:
                return 0.90
            else:
                return 0.70
        elif verdict == Verdict.STUCK:
            return 0.90  # alta confianza en stuck detection
        elif verdict == Verdict.DANGER:
            return 0.85
        elif verdict == Verdict.SLOW:
            return 0.60
        else:
            return 0.50

    @property
    def stuck_count(self) -> int:
        return self._stuck_counter

    def reset_stuck(self):
        self._stuck_counter = 0
        self._consecutive_same = 0


# ── ActionVerifier ─────────────────────────────────────────────────────────────


class ActionVerifier:
    """Ciclo completo de verificación y recuperación post-acción.

    Integra StateChecker con estrategias de recovery y gestión de confianza.
    Mantiene un historial de verificaciones para detectar patrones de fallo.
    """

    def __init__(self):
        self.checker = StateChecker()
        self._verification_history: List[VerificationResult] = []
        self._recovery_attempts: int = 0
        self._max_recovery_attempts: int = 5
        self._success_streak: int = 0
        self._fail_streak: int = 0

    def verify(self, scene_before: Any, scene_after: Any,
               action: Dict[str, Any],
               expected_outcome: str = "") -> VerificationResult:
        """Verifica si una acción produjo el cambio esperado.

        Args:
            scene_before: escena antes de la acción
            scene_after: escena después de la acción
            action: dict con la acción ejecutada (action, reason, x, y, text, etc.)
            expected_outcome: descripción del resultado esperado

        Returns:
            VerificationResult con veredicto
        """
        action_type = action.get("action", "unknown")

        result = self.checker.compare(
            scene_before=scene_before,
            scene_after=scene_after,
            expected_change=expected_outcome or action.get("reason", ""),
            action_type=action_type,
        )

        self._verification_history.append(result)

        # Actualizar rachas
        if result.verified:
            self._success_streak += 1
            self._fail_streak = 0
            self._recovery_attempts = 0
        else:
            self._fail_streak += 1
            self._success_streak = 0

        return result

    def get_recovery_action(self, failed_result: VerificationResult,
                           current_scene: Any) -> Dict[str, Any]:
        """Genera acción de recuperación basada en el veredicto.

        Args:
            failed_result: resultado de verificación fallido
            current_scene: escena actual

        Returns:
            Dict con recovery_action para ejecutar
        """
        self._recovery_attempts += 1

        if self._recovery_attempts > self._max_recovery_attempts:
            return {
                "action": "replan",
                "reason": f"max_recovery_attempts ({self._max_recovery_attempts}) alcanzado",
                "recovery_attempt": self._recovery_attempts,
            }

        recovery = failed_result.recovery_suggested or RecoveryAction.RETRY

        if recovery == RecoveryAction.RETRY:
            return {
                "action": "retry",
                "reason": "reintentando misma acción",
                "recovery_attempt": self._recovery_attempts,
            }

        elif recovery == RecoveryAction.WAIT_RETRY:
            return {
                "action": "wait",
                "seconds": 3.0 + self._recovery_attempts,
                "reason": "esperando carga de página",
                "recovery_attempt": self._recovery_attempts,
            }

        elif recovery == RecoveryAction.SCROLL_RETRY:
            return {
                "action": "scroll",
                "direction": "down",
                "lines": 5,
                "reason": "scroll para forzar renderizado",
                "recovery_attempt": self._recovery_attempts,
            }

        elif recovery == RecoveryAction.ESCAPE_RETRY:
            return {
                "action": "key",
                "key": "Escape",
                "reason": "cerrando posibles modales/popups",
                "recovery_attempt": self._recovery_attempts,
            }

        elif recovery == RecoveryAction.BACK:
            return {
                "action": "key",
                "key": "Alt+Left",
                "reason": "volviendo a página anterior",
                "recovery_attempt": self._recovery_attempts,
            }

        elif recovery == RecoveryAction.SCROLL_DOWN:
            return {
                "action": "scroll",
                "direction": "down",
                "lines": 10,
                "reason": "explorando más contenido",
                "recovery_attempt": self._recovery_attempts,
            }

        elif recovery == RecoveryAction.REPLAN:
            return {
                "action": "replan",
                "reason": f"solicitando nuevo plan: {failed_result.reason}",
                "recovery_attempt": self._recovery_attempts,
            }

        elif recovery == RecoveryAction.ABORT:
            return {
                "action": "abort",
                "reason": f"abortando: {failed_result.reason}",
                "recovery_attempt": self._recovery_attempts,
            }

        return {
            "action": "retry",
            "reason": "recovery genérico",
            "recovery_attempt": self._recovery_attempts,
        }

    def is_mission_hopeless(self) -> bool:
        """Determina si la misión debe abortarse por fallos acumulados."""
        if self._recovery_attempts > self._max_recovery_attempts:
            return True
        if self._fail_streak >= 8:
            return True
        if self.checker.stuck_count >= 10:
            return True
        return False

    # ── Stats ─────────────────────────────────────────────────────────────────

    def stats(self) -> Dict[str, Any]:
        return {
            "verifications": len(self._verification_history),
            "recovery_attempts": self._recovery_attempts,
            "success_streak": self._success_streak,
            "fail_streak": self._fail_streak,
            "stuck_count": self.checker.stuck_count,
            "last_verdict": (self._verification_history[-1].verdict.value
                            if self._verification_history else None),
            "last_recovery": (self._verification_history[-1].recovery_suggested.value
                             if self._verification_history and
                             self._verification_history[-1].recovery_suggested
                             else None),
        }

    def reset(self):
        """Resetea el estado para una nueva misión."""
        self._verification_history = []
        self._recovery_attempts = 0
        self._success_streak = 0
        self._fail_streak = 0
        self.checker.reset_stuck()


# ── Singleton ─────────────────────────────────────────────────────────────────
_verifier: Optional[ActionVerifier] = None


def get_action_verifier() -> ActionVerifier:
    global _verifier
    if _verifier is None:
        _verifier = ActionVerifier()
    return _verifier


# ── CLI ───────────────────────────────────────────────────────────────────────
if __name__ == "__main__":
    import argparse
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")

    p = argparse.ArgumentParser(
        description="ActionVerifier — verificación post-acción y autocorrección"
    )
    p.add_argument("--test", action="store_true", help="Test de verificación")
    p.add_argument("--stats", action="store_true", help="Estadísticas")
    args = p.parse_args()

    if args.test:
        from core.screen_controller import Scene, ScreenRegion

        # Simular escenas before/after
        before = Scene(
            timestamp=time.time(),
            window_title="Firefox — Google",
            ocr_full_text="Google Search Images Gmail Sign in",
            regions=[
                ScreenRegion(100, 50, 80, 20, "Search", "text", 0.9),
                ScreenRegion(200, 50, 60, 20, "Images", "text", 0.8),
            ],
        )

        after = Scene(
            timestamp=time.time() + 2,
            window_title="Firefox — Google Search: n8n automation",
            ocr_full_text="n8n-io/n8n workflow automation GitHub Star 50k",
            regions=[
                ScreenRegion(100, 100, 120, 24, "n8n-io/n8n", "link", 0.85),
                ScreenRegion(100, 150, 200, 24, "workflow automation", "text", 0.7),
                ScreenRegion(100, 200, 80, 20, "Star 50k", "text", 0.6),
            ],
        )

        av = ActionVerifier()
        action = {"action": "click", "reason": "buscar n8n en github", "x": 500, "y": 300}
        result = av.verify(before, after, action, "debe mostrar resultados de búsqueda")

        print(f"Veredicto: {result.verdict.value}")
        print(f"Verificado: {result.verified}")
        print(f"Confianza: {result.confidence:.2f}")
        print(f"Razón: {result.reason}")
        print(f"Nuevos: {result.new_elements}")
        print(f"Perdidos: {result.lost_elements}")
        print(f"Similitud: {result.text_similarity:.3f}")
        if result.recovery_suggested:
            print(f"Recovery: {result.recovery_suggested.value}")

        # Test stuck
        after2 = Scene(
            timestamp=time.time() + 4,
            window_title="Firefox — Google",
            ocr_full_text="Google Search Images Gmail Sign in",
            regions=before.regions,
        )
        result2 = av.verify(before, after2, action, "debe cambiar")
        print(f"\nVeredicto stuck: {result2.verdict.value}")
        print(f"Verificado: {result2.verified}")
        print(f"Recovery: {result2.recovery_suggested.value if result2.recovery_suggested else 'none'}")

    elif args.stats:
        av = get_action_verifier()
        import json
        print(json.dumps(av.stats(), indent=2))

    else:
        p.print_help()
