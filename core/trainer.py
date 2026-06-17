"""
EIDOS core/trainer.py — Training & Feedback System
===================================================
Sistema de entrenamiento continuo que permite a EIDOS:
  - Aprender de sus errores y éxitos
  - Mejorar sus decisiones con cada interacción
  - Auto-ajustar parámetros de herramientas
  - Guardar y recuperar conocimiento de ejecuciones pasadas

El flujo de entrenamiento es:
    1. OBSERVE → Observar resultado de una acción
    2. EVALUATE → Evaluar si fue exitosa o no
    3. LEARN → Extraer aprendizajes
    4. STORE → Guardar conocimiento
    5. APPLY → Aplicar en futuras ejecuciones

Autor: EIDOS AI System
Fecha: 2026-03-17
"""
from __future__ import annotations

import json
import time
from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Any, Dict, List, Optional
from enum import Enum


# ── Tipos ────────────────────────────────────────────────────────────────────

class OutcomeType(Enum):
    """Tipo de resultado de una acción"""
    SUCCESS = "success"
    PARTIAL_SUCCESS = "partial_success"
    FAILURE = "failure"
    ERROR = "error"


@dataclass
class ActionOutcome:
    """Resultado observado de una acción"""
    action: str  # Nombre de la herramienta o acción
    args: Dict[str, Any]
    outcome: OutcomeType
    result: str
    error: Optional[str] = None
    timestamp: float = field(default_factory=time.time)
    context: str = ""  # Contexto de la tarea


@dataclass
class Learning:
    """Aprendizaje extraído de una ejecución"""
    pattern: str  # Patrón identificado (e.g., "nmap slow on large networks")
    recommendation: str  # Recomendación (e.g., "use -F flag for fast scan")
    confidence: float  # 0.0-1.0
    applies_to: str  # Herramienta o contexto donde aplica
    learned_at: float = field(default_factory=time.time)
    times_validated: int = 0  # Veces que se ha validado este aprendizaje

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


# ── Trainer System ───────────────────────────────────────────────────────────

class Trainer:
    """
    Sistema de entrenamiento y feedback continuo para EIDOS.
    """

    def __init__(self, storage_dir: Optional[Path] = None):
        self.storage_dir = storage_dir or Path.home() / ".eidos" / "training"
        self.storage_dir.mkdir(parents=True, exist_ok=True)

        # Archivos de almacenamiento
        self.outcomes_file = self.storage_dir / "outcomes.jsonl"
        self.learnings_file = self.storage_dir / "learnings.json"

        # Cargar aprendizajes previos
        self.learnings: List[Learning] = self._load_learnings()

        # Stats
        self.total_observations = 0
        self.successes = 0
        self.failures = 0

    # ── Storage ──────────────────────────────────────────────────────────────

    def _load_learnings(self) -> List[Learning]:
        """Carga aprendizajes previos desde disco"""
        if not self.learnings_file.exists():
            return []

        try:
            with open(self.learnings_file, 'r') as f:
                data = json.load(f)
                return [Learning(**l) for l in data]
        except Exception as e:
            print(f"⚠️  [Trainer] Error cargando learnings: {e}")
            return []

    def _save_learnings(self) -> None:
        """Guarda aprendizajes a disco"""
        try:
            with open(self.learnings_file, 'w') as f:
                json.dump([l.to_dict() for l in self.learnings], f, indent=2)
        except Exception as e:
            print(f"⚠️  [Trainer] Error guardando learnings: {e}")

    def _append_outcome(self, outcome: ActionOutcome) -> None:
        """Añade un outcome al log (JSONL)"""
        try:
            with open(self.outcomes_file, 'a') as f:
                f.write(json.dumps(asdict(outcome)) + '\n')
        except Exception as e:
            print(f"⚠️  [Trainer] Error guardando outcome: {e}")

    # ── API Principal ────────────────────────────────────────────────────────

    def observe(
        self,
        action: str,
        args: Dict[str, Any],
        result: str,
        success: bool,
        error: Optional[str] = None,
        context: str = ""
    ) -> ActionOutcome:
        """
        Registra el resultado de una acción ejecutada.

        Args:
            action: Nombre de la herramienta/acción
            args: Argumentos usados
            result: Resultado obtenido
            success: Si fue exitosa
            error: Mensaje de error (si aplica)
            context: Contexto de la tarea

        Returns:
            ActionOutcome registrado
        """
        # Determinar tipo de outcome
        if success and not error:
            outcome_type = OutcomeType.SUCCESS
        elif success and error:
            outcome_type = OutcomeType.PARTIAL_SUCCESS
        elif error and "timeout" in error.lower():
            outcome_type = OutcomeType.ERROR
        else:
            outcome_type = OutcomeType.FAILURE

        outcome = ActionOutcome(
            action=action,
            args=args,
            outcome=outcome_type,
            result=result,
            error=error,
            context=context
        )

        # Actualizar stats
        self.total_observations += 1
        if success:
            self.successes += 1
        else:
            self.failures += 1

        # Guardar
        self._append_outcome(outcome)

        print(f"📊 [Trainer] Observado: {action} → {outcome_type.value}")

        # Auto-aprendizaje si hay patrones claros
        self._auto_learn_from_outcome(outcome)

        return outcome

    def _auto_learn_from_outcome(self, outcome: ActionOutcome) -> None:
        """
        Intenta extraer aprendizajes automáticos de un outcome.
        """
        # Heurísticas simples para detectar patrones

        # 1. Timeouts repetidos → recomendar timeout más largo o flags más rápidos
        if outcome.error and "timeout" in outcome.error.lower():
            pattern = f"{outcome.action}_timeout"
            existing = next((l for l in self.learnings if l.pattern == pattern), None)

            if existing:
                existing.times_validated += 1
                existing.confidence = min(1.0, existing.confidence + 0.1)
            else:
                self.learnings.append(Learning(
                    pattern=pattern,
                    recommendation=f"Incrementar timeout o usar flags más rápidos para {outcome.action}",
                    confidence=0.5,
                    applies_to=outcome.action,
                ))
                print(f"🧠 [Trainer] Nuevo aprendizaje: {pattern}")
                self._save_learnings()

        # 2. Errores de dependencias faltantes
        if outcome.error and ("not found" in outcome.error.lower() or "no such file" in outcome.error.lower()):
            pattern = f"{outcome.action}_dependency_missing"
            existing = next((l for l in self.learnings if l.pattern == pattern), None)

            if not existing:
                self.learnings.append(Learning(
                    pattern=pattern,
                    recommendation=f"Verificar dependencias antes de ejecutar {outcome.action}",
                    confidence=0.7,
                    applies_to=outcome.action,
                ))
                print(f"🧠 [Trainer] Nuevo aprendizaje: {pattern}")
                self._save_learnings()

        # 3. Comandos lentos → recomendar alternativas
        # (requeriría medir tiempos, por ahora skip)

    def get_recommendations(self, action: str, args: Dict[str, Any]) -> List[Learning]:
        """
        Obtiene recomendaciones basadas en aprendizajes previos.

        Args:
            action: Herramienta que se va a ejecutar
            args: Argumentos que se van a usar

        Returns:
            Lista de aprendizajes aplicables con recomendaciones
        """
        relevant = [
            l for l in self.learnings
            if l.applies_to == action and l.confidence > 0.5
        ]

        # Ordenar por confidence desc
        relevant.sort(key=lambda x: x.confidence, reverse=True)

        return relevant

    def apply_recommendations(
        self,
        action: str,
        args: Dict[str, Any]
    ) -> Dict[str, Any]:
        """
        Aplica recomendaciones automáticamente a los argumentos.

        Args:
            action: Herramienta
            args: Argumentos originales

        Returns:
            Argumentos ajustados según aprendizajes
        """
        recommendations = self.get_recommendations(action, args)

        if not recommendations:
            return args

        adjusted_args = args.copy()

        # Aplicar heurísticas simples
        for learning in recommendations:
            # Ejemplo: si hay timeouts repetidos, incrementar timeout
            if "timeout" in learning.pattern and "timeout" in adjusted_args:
                original = adjusted_args.get("timeout", 30)
                adjusted_args["timeout"] = int(original * 1.5)
                print(f"🧠 [Trainer] Ajustando timeout: {original} → {adjusted_args['timeout']}")

        return adjusted_args

    def get_stats(self) -> Dict[str, Any]:
        """Obtiene estadísticas del trainer"""
        success_rate = (self.successes / self.total_observations * 100) if self.total_observations > 0 else 0

        return {
            "total_observations": self.total_observations,
            "successes": self.successes,
            "failures": self.failures,
            "success_rate": f"{success_rate:.1f}%",
            "learnings_count": len(self.learnings),
            "high_confidence_learnings": sum(1 for l in self.learnings if l.confidence > 0.7),
        }

    def get_learnings_summary(self) -> str:
        """
        Genera un resumen legible de los aprendizajes.
        """
        if not self.learnings:
            return "Sin aprendizajes aún."

        lines = ["📚 APRENDIZAJES DE EIDOS:\n"]
        for i, learning in enumerate(self.learnings[:10], 1):  # Top 10
            lines.append(f"{i}. [{learning.applies_to}] {learning.recommendation}")
            lines.append(f"   Confidence: {learning.confidence:.2f} | Validado {learning.times_validated} veces\n")

        return "\n".join(lines)


# ── Singleton ────────────────────────────────────────────────────────────────

_trainer: Optional[Trainer] = None

def get_trainer() -> Trainer:
    """Obtiene el singleton del Trainer"""
    global _trainer
    if _trainer is None:
        _trainer = Trainer()
        print(f"🎓 [Trainer] Sistema de entrenamiento inicializado")
        print(f"   Learnings previos: {len(_trainer.learnings)}")
    return _trainer


# ── CLI de prueba ────────────────────────────────────────────────────────────

if __name__ == "__main__":
    trainer = Trainer()

    # Simulación de observaciones
    print("\n" + "="*70)
    print("SIMULACIÓN DE ENTRENAMIENTO")
    print("="*70 + "\n")

    # Simular varios outcomes
    trainer.observe(
        action="nmap",
        args={"target": "192.168.1.0/24", "flags": "-sV"},
        result="Scan timed out after 30s",
        success=False,
        error="Timeout exceeded",
        context="Network reconnaissance"
    )

    trainer.observe(
        action="nmap",
        args={"target": "192.168.1.1", "flags": "-F"},
        result="22/tcp open ssh\n80/tcp open http",
        success=True,
        context="Quick scan"
    )

    trainer.observe(
        action="gobuster",
        args={"url": "http://example.com", "wordlist": "/usr/share/wordlists/dirb/common.txt"},
        result="/admin found",
        success=True,
        context="Directory brute force"
    )

    # Ver estadísticas
    print("\n" + "="*70)
    stats = trainer.get_stats()
    print("ESTADÍSTICAS:")
    for k, v in stats.items():
        print(f"  {k}: {v}")

    # Ver aprendizajes
    print("\n" + "="*70)
    print(trainer.get_learnings_summary())

    # Probar recomendaciones
    print("\n" + "="*70)
    print("RECOMENDACIONES PARA PRÓXIMO SCAN NMAP:")
    recs = trainer.get_recommendations("nmap", {"target": "192.168.1.0/24"})
    for rec in recs:
        print(f"  • {rec.recommendation} (confidence: {rec.confidence:.2f})")
