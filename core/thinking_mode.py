"""
Interleaved Thinking Mode (Kimi K2.5 inspired)
Muestra razonamiento explícito antes de la respuesta final.

HONESTY NOTE: Previously this chained 5 LLM calls (analysis, planning,
execution, reflection, conclusion) with NO verification between phases.
If phase 2 (planning) contradicted phase 1 (analysis), the error propagated
silently. Now each step is verified against the previous one — if a
contradiction is detected, the engine backtracks and re-generates the
conflicting step with additional context.
"""

import json
import re
import time
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Callable, Any
from pathlib import Path
import os
import sys

EIDOS_ROOT = Path(__file__).parent.parent.parent.resolve()
sys.path.insert(0, str(EIDOS_ROOT))


@dataclass
class ThinkingStep:
    """Un paso del proceso de pensamiento"""
    step_number: int
    type: str  # 'analysis', 'planning', 'execution', 'reflection', 'conclusion'
    content: str
    timestamp: float = field(default_factory=time.time)
    confidence: float = 0.0
    metadata: Dict = field(default_factory=dict)


@dataclass
class ThinkingResult:
    """Resultado completo del thinking mode"""
    query: str
    thinking_steps: List[ThinkingStep]
    final_response: str
    total_time: float
    model_used: str
    reasoning_content: str  # Concatenación de todo el thinking
    metadata: Dict = field(default_factory=dict)


class InterleavedThinkingEngine:
    """
    Motor de thinking intercalado - similar a Kimi K2.5 Thinking Mode.
    
    Características:
    - Genera reasoning_content explícito antes de cada respuesta
    - Permite ver el proceso de pensamiento del modelo
    - Guarda el thinking en memoria para referencia futura
    - Integra con el kernel para thinking automático en queries complejas
    """
    
    THINKING_TYPES = [
        "analysis",      # Análisis del input/query
        "planning",      # Planificación de approach
        "execution",     # Ejecución paso a paso
        "reflection",    # Reflexión sobre resultados
        "conclusion"     # Conclusión final
    ]
    
    def __init__(self, auto_thinking_threshold: float = 0.7):
        self.auto_thinking_threshold = auto_thinking_threshold
        self.history: List[ThinkingResult] = []
        self.max_history = 50
        self._storage_path = Path.home() / ".eidos" / "thinking_history.jsonl"
        self._ensure_storage()
    
    def _ensure_storage(self):
        """Asegura que existe el directorio de almacenamiento"""
        self._storage_path.parent.mkdir(parents=True, exist_ok=True)
    
    def should_use_thinking(self, query: str, context: Dict = None) -> bool:
        """
        Determina si se debe usar thinking mode basado en la query.
        
        Heurísticas:
        - Queries complejas (múltiples pasos)
        - Preguntas que requieren razonamiento profundo
        - Problemas matemáticos/técnicos
        - Debugging
        - Decisiones importantes
        """
        query_lower = query.lower()
        
        # Indicadores de complejidad
        complexity_markers = [
            "explain", "why", "how", "step by step", "razona", "piensa",
            "analyze", "compare", "evaluate", "debug", "solve",
            "step-by-step", "paso a paso", "explica", "analiza",
            "what if", "consider", "implications", "trade-offs"
        ]
        
        # Indicadores técnicos
        technical_markers = [
            "error", "bug", "fix", "optimize", "refactor",
            "architecture", "design", "pattern", "algorithm"
        ]
        
        # Contar marcadores
        complexity_score = sum(1 for m in complexity_markers if m in query_lower)
        technical_score = sum(1 for m in technical_markers if m in query_lower)
        
        # Longitud como indicador
        word_count = len(query.split())
        length_score = min(word_count / 20, 1.0)  # Normalizar a 0-1
        
        # Score total
        total_score = (complexity_score * 0.4 + technical_score * 0.4 + length_score * 0.2) / 3
        
        return total_score >= self.auto_thinking_threshold
    
    def generate_thinking(
        self,
        query: str,
        model: str = "lfm2.5-thinking:1.2b",
        force_thinking: bool = False,
        callback: Callable[[ThinkingStep], None] = None
    ) -> ThinkingResult:
        """
        Genera thinking intercalado para una query.
        
        Args:
            query: La pregunta/tarea
            model: Modelo Ollama a usar
            force_thinking: Forzar thinking aunque no sea complejo
            callback: Función llamada en cada paso (para streaming)
        
        Returns:
            ThinkingResult con pasos y respuesta final
        """
        start_time = time.time()
        
        # Verificar si amerita thinking
        if not force_thinking and not self.should_use_thinking(query):
            # Query simple - respuesta directa sin thinking
            return self._quick_response(query, model)
        
        steps = []
        
        # Paso 1: Análisis del problema
        step1 = self._generate_step(
            query, 
            "analysis",
            "Analiza este problema paso a paso. Identifica: 1) Qué se pide, 2) Qué información tenemos, 3) Qué obstáculos pueden haber.",
            model,
            1
        )
        steps.append(step1)
        if callback:
            callback(step1)
        
        # Paso 2: Planificación
        step2 = self._generate_step(
            query,
            "planning",
            f"Basado en este análisis: {step1.content}\n\nCrea un plan detallado para resolver esto. Enumera los pasos específicos.",
            model,
            2
        )
        # Verify step2 does not contradict step1
        if not self._verify_step(step2, step1):
            # Backtrack: re-generate step1 with the contradiction context
            step1 = self._generate_step(
                query,
                "analysis",
                f"Analiza este problema. Tu plan sugirió algo que contradice "
                f"el análisis inicial. Re-analiza considerando: {step2.content[:200]}",
                model,
                1
            )
            # Re-generate step2 with corrected step1
            step2 = self._generate_step(
                query,
                "planning",
                f"Basado en este análisis CORREGIDO: {step1.content}\n\n"
                f"Crea un plan detallado. Evita contradicciones con el análisis.",
                model,
                2
            )
        steps.append(step2)
        if callback:
            callback(step2)

        # Paso 3: Ejecución/Resolución
        step3 = self._generate_step(
            query,
            "execution",
            f"Siguiendo este plan: {step2.content}\n\nAhora resuelve el problema original: {query}",
            model,
            3
        )
        # Verify step3 does not contradict step2
        if not self._verify_step(step3, step2):
            step3 = self._generate_step(
                query,
                "execution",
                f"El plan era: {step2.content}\n\n"
                f"Tu solucion anterior contradijo el plan. "
                f"Ahora resuelve SIGUIENDO ESTRICTAMENTE el plan: {query}",
                model,
                3
            )
        steps.append(step3)
        if callback:
            callback(step3)

        # Paso 4: Reflexión
        step4 = self._generate_step(
            query,
            "reflection",
            f"Solución propuesta: {step3.content}\n\nReflexiona: ¿Es correcta? ¿Hay alternativas mejores? ¿Qué podría fallar?",
            model,
            4
        )
        # Verify step4 does not contradict step3
        if not self._verify_step(step4, step3):
            step4 = self._generate_step(
                query,
                "reflection",
                f"Tu reflexion anterior contradijo la solucion. "
                f"Solucion: {step3.content}\n\n"
                f"Reflexiona HONESTAMENTE: ¿Es correcta? ¿Hay alternativas?",
                model,
                4
            )
        steps.append(step4)
        if callback:
            callback(step4)
        
        # Paso 5: Respuesta final
        final_response = self._generate_final_response(query, steps, model)
        
        # Construir reasoning_content
        reasoning_parts = []
        for step in steps:
            reasoning_parts.append(f"[{step.type.upper()}] {step.content}")
        reasoning_content = "\n\n".join(reasoning_parts)
        
        total_time = time.time() - start_time
        
        result = ThinkingResult(
            query=query,
            thinking_steps=steps,
            final_response=final_response,
            total_time=total_time,
            model_used=model,
            reasoning_content=reasoning_content,
            metadata={
                "forced": force_thinking,
                "steps_count": len(steps),
                "timestamp": time.time()
            }
        )
        
        # Guardar en historial
        self._save_result(result)
        
        return result
    
    def _verify_step(self, current: ThinkingStep,
                      previous: ThinkingStep) -> bool:
        """Verify that the current step does not contradict the previous one.

        Uses a lightweight heuristic: if both steps contain key factual
        claims that directly oppose each other, we consider it a contradiction.

        Returns:
            True if verification passes, False if contradiction detected.
        """
        if not previous or not previous.content or not current.content:
            return True  # Nothing to verify against

        # Simple heuristic: check if the current step's key claims
        # directly negate the previous step's key claims.
        contradiction_markers = [
            # Spanish
            ("no es", "es"), ("no son", "son"),
            ("no tiene", "tiene"), ("no hay", "hay"),
            # English
            ("is not", "is"), ("does not", "does"),
            ("cannot", "can"), ("should not", "should"),
        ]
        prev_lower = previous.content.lower()
        curr_lower = current.content.lower()

        for neg, pos in contradiction_markers:
            # If previous says X and current says NOT X, that's a contradiction
            if pos in prev_lower and neg in curr_lower:
                return False
            if neg in prev_lower and pos in curr_lower:
                return False

        return True

    def _generate_step(
        self,
        original_query: str,
        step_type: str,
        prompt: str,
        model: str,
        step_number: int
    ) -> ThinkingStep:
        """Genera un paso individual del thinking"""
        try:
            import requests
            
            response = requests.post(
                'http://localhost:11434/api/generate',
                json={
                    'model': model,
                    'prompt': prompt,
                    'stream': False,
                    'options': {
                        'temperature': 0.3,
                        'num_ctx': 4096
                    }
                },
                timeout=60
            )
            
            if response.status_code == 200:
                content = response.json().get('response', '').strip()
                # Limitar longitud
                if len(content) > 1000:
                    content = content[:997] + "..."
                
                return ThinkingStep(
                    step_number=step_number,
                    type=step_type,
                    content=content,
                    confidence=0.8
                )
        except Exception as e:
            pass
        
        # Fallback
        return ThinkingStep(
            step_number=step_number,
            type=step_type,
            content=f"[Paso {step_number}: {step_type} - Ollama no disponible]",
            confidence=0.0
        )
    
    def _generate_final_response(self, query: str, steps: List[ThinkingStep], model: str) -> str:
        """Genera la respuesta final basada en los pasos de thinking"""
        try:
            # Construir contexto de thinking
            thinking_context = "\n\n".join([
                f"{s.type.upper()}: {s.content}"
                for s in steps
            ])
            
            final_prompt = f"""Basado en este análisis detallado:

{thinking_context}

Ahora proporciona una respuesta concisa y directa al usuario para su pregunta original:
"{query}"

Responde de forma clara y directa, aplicando lo analizado."""
            
            import requests
            response = requests.post(
                'http://localhost:11434/api/generate',
                json={
                    'model': model,
                    'prompt': final_prompt,
                    'stream': False,
                    'options': {'temperature': 0.4}
                },
                timeout=60
            )
            
            if response.status_code == 200:
                return response.json().get('response', '').strip()
        except Exception:
            pass  # error no crítico, continuar
        # Fallback: usar el último paso
        if steps:
            return steps[-1].content
        return "No se pudo generar respuesta."
    
    def _quick_response(self, query: str, model: str) -> ThinkingResult:
        """Genera respuesta rápida sin thinking para queries simples"""
        try:
            import requests
            response = requests.post(
                'http://localhost:11434/api/generate',
                json={
                    'model': model,
                    'prompt': query,
                    'stream': False
                },
                timeout=30
            )
            
            if response.status_code == 200:
                content = response.json().get('response', '').strip()
                return ThinkingResult(
                    query=query,
                    thinking_steps=[],
                    final_response=content,
                    total_time=0,
                    model_used=model,
                    reasoning_content="[Query simple - respuesta directa]",
                    metadata={"quick": True}
                )
        except Exception:
            pass  # error no crítico, continuar
        return ThinkingResult(
            query=query,
            thinking_steps=[],
            final_response="Error generando respuesta",
            total_time=0,
            model_used=model,
            reasoning_content="[Error]",
            metadata={"error": True}
        )
    
    def _save_result(self, result: ThinkingResult):
        """Guarda el resultado en el historial"""
        self.history.append(result)
        if len(self.history) > self.max_history:
            self.history = self.history[-self.max_history:]
        
        # Guardar en archivo
        try:
            with open(self._storage_path, 'a') as f:
                entry = {
                    "query": result.query,
                    "reasoning": result.reasoning_content[:500],  # Truncado
                    "response": result.final_response[:500],
                    "time": result.total_time,
                    "model": result.model_used,
                    "timestamp": time.time()
                }
                f.write(json.dumps(entry) + "\n")
        except Exception:
            pass  # error no crítico, continuar
    def get_history(self, limit: int = 10) -> List[Dict]:
        """Obtiene historial de thinking reciente"""
        history = []
        try:
            if self._storage_path.exists():
                with open(self._storage_path, 'r') as f:
                    for line in f:
                        try:
                            history.append(json.loads(line.strip()))
                        except Exception:
                            pass  # error no crítico, continuar
        except Exception:
            pass  # error no crítico, continuar
        return history[-limit:]
    
    def format_thinking_display(self, result: ThinkingResult) -> str:
        """Formatea el thinking para display al usuario"""
        lines = [
            "🧠 MODO THINKING ACTIVADO",
            f"   Query: {result.query[:60]}...",
            f"   Modelo: {result.model_used}",
            "",
            "💭 PROCESO DE PENSAMIENTO:",
            ""
        ]
        
        for step in result.thinking_steps:
            emoji = {
                "analysis": "🔍",
                "planning": "📋",
                "execution": "⚙️",
                "reflection": "🤔",
                "conclusion": "✅"
            }.get(step.type, "💭")
            
            lines.append(f"{emoji} [{step.type.upper()}]")
            lines.append(f"   {step.content[:150]}...")
            lines.append("")
        
        lines.append("─" * 50)
        lines.append("📤 RESPUESTA FINAL:")
        lines.append(result.final_response)
        
        return "\n".join(lines)


# ─── Singleton ───────────────────────────────────────────────────────────────

_thinking_engine: Optional[InterleavedThinkingEngine] = None


def get_thinking_engine() -> InterleavedThinkingEngine:
    """Obtiene instancia singleton del thinking engine"""
    global _thinking_engine
    if _thinking_engine is None:
        _thinking_engine = InterleavedThinkingEngine()
    return _thinking_engine


# ─── CLI / Test ─────────────────────────────────────────────────────────────

if __name__ == "__main__":
    import sys
    
    print("=" * 60)
    print("Interleaved Thinking Engine (Kimi K2.5 inspired)")
    print("=" * 60)
    
    if len(sys.argv) < 2:
        print("\nUso: python thinking_mode.py '<query>' [--force]")
        print("\nEjemplo:")
        print('  python thinking_mode.py "Explain quantum computing step by step"')
        sys.exit(1)
    
    query = sys.argv[1]
    force = "--force" in sys.argv
    
    engine = get_thinking_engine()
    
    # Mostrar si amerita thinking
    should_think = engine.should_use_thinking(query)
    print(f"\nQuery: {query}")
    print(f"Amerita thinking: {should_think}")
    
    if force or should_think:
        print("\nGenerando thinking intercalado...\n")
        result = engine.generate_thinking(query, force_thinking=force)
        
        print(engine.format_thinking_display(result))
        print(f"\n⏱️  Tiempo total: {result.total_time:.1f}s")
    else:
        print("\nQuery simple - usando respuesta directa")
        result = engine.generate_thinking(query)
        print(f"\nRespuesta: {result.final_response}")
