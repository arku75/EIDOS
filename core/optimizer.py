"""
EIDOS core/optimizer.py — Optimización Adaptativa (Fase 6 del Roadmap)
=======================================================================
Según el Oráculo 2:
  ✅ Adaptive model selection (moondream2 para loop frecuente, llama3.2 para OCR fino)
  ✅ Latency optimization (elegir el modelo más rápido que sea suficiente)
  ✅ Context compactor avanzado (ya en compactor.py, aquí se perfecciona)

Bloque 3 de las 5 preguntas al Oráculo 2:
  "Con Ryzen 5 16GB: moondream ~5s, moondream:latest ~25s, moondream2 ~10s.
   En loop de 3-5 iteraciones Computer Use, la latencia multiplica."
   
  Decisión del Oráculo: moondream2 para loop frecuente + llama3.2 solo para OCR fino.
"""
from __future__ import annotations

import time
from enum import Enum
from typing import Any

# ── Modelos disponibles con sus características ──────────────────────────────
OLLAMA_URL = "http://localhost:11434"

class ModelProfile:
    """Perfil de un modelo: capacidades y latencia estimada."""
    def __init__(self, name: str, latency_s: float, vision: bool,
                 tool_calling: bool, ctx: int, use_for: list[str]) -> None:
        self.name         = name
        self.latency_s    = latency_s   # Latencia estimada en Ryzen 5 / 16GB
        self.vision       = vision
        self.tool_calling = tool_calling
        self.ctx          = ctx
        self.use_for      = use_for

MODELS = {
    "lfm2.5-thinking:1.2b": ModelProfile(
        name="lfm2.5-thinking:1.2b",
        latency_s=1.5,
        vision=False, tool_calling=False,
        ctx=4096,
        use_for=["triage", "planning", "compaction", "simple_qa", "code_completion"]
    ),
    "deepseek-r1:14b": ModelProfile(
        name="deepseek-r1:14b",
        latency_s=8.0,
        vision=False, tool_calling=True,
        ctx=8192,
        use_for=["tool_calling", "agent_loop", "complex_reasoning", "json_output"]
    ),
    "moondream2": ModelProfile(
        name="moondream2",
        latency_s=5.0,
        vision=True, tool_calling=False,
        ctx=2048,
        use_for=["screenshot_describe", "ui_quick_check", "vision_loop_fast"]
    ),
    "moondream:latest": ModelProfile(
        name="moondream:latest",
        latency_s=25.0,
        vision=True, tool_calling=False,
        ctx=4096,
        use_for=["ocr_deep", "ui_detail_analysis", "document_reading", "vision_high_accuracy"]
    ),
    "nomic-embed-text:latest": ModelProfile(
        name="nomic-embed-text:latest",
        latency_s=0.5,
        vision=False, tool_calling=False,
        ctx=8192,
        use_for=["embeddings", "semantic_search", "rag"]
    ),
}


class TaskType(Enum):
    """Tipo de tarea para seleccionar el modelo óptimo."""
    QUICK_CHAT        = "quick_chat"        # Chat rápido, triage
    TOOL_CALL         = "tool_call"         # Necesita tool calling JSON
    VISION_FAST       = "vision_fast"       # Screenshot loop frecuente (CU)
    VISION_DEEP       = "vision_deep"       # OCR fino, documento, UI compleja
    MEMORY_COMPRESS   = "memory_compress"   # Comprimir historial
    CODE_COMPLETE     = "code_complete"     # Autocompletar código
    EMBEDDING         = "embedding"         # Generar embeddings


# ══════════════════════════════════════════════════════════════════════════════
#  ADAPTIVE MODEL SELECTOR
# ══════════════════════════════════════════════════════════════════════════════

class AdaptiveModelSelector:
    """
    Selecciona el modelo óptimo según la tarea y las condiciones actuales.
    
    Principio del Oráculo 2: moondream para loop frecuente, llama3.2 solo
    cuando la precisión es crítica (OCR fino, documentos, análisis profundo).
    
    Ejemplo:
        selector = AdaptiveModelSelector()
        model = selector.select("analiza lo que ves en pantalla", vision=True)
        # → "moondream2"  (rápido, suficiente para CU loop)
        
        model = selector.select("lee el texto de este PDF en pantalla", vision=True)
        # → "moondream:latest"  (OCR fino)
    """
    
    def __init__(self) -> None:
        self._latency_history: dict[str, list[float]] = {}  # Latencias reales observadas
        self._call_count: dict[str, int] = {}
    
    def classify_task(self, task: str, vision: bool = False,
                      needs_tools: bool = False) -> TaskType:
        """
        Clasifica una tarea para elegir el tipo de modelo.
        """
        task_lower = task.lower()
        
        if needs_tools:
            return TaskType.TOOL_CALL
        
        if vision:
            # Indicadores de OCR fino (necesita llama3.2)
            deep_vision_keywords = [
                "lee", "transcribe", "ocr", "texto exacto", "documento",
                "leer", "texto del", "copia exacta", "caracteres",
                "read text", "transcribe", "exact text"
            ]
            if any(kw in task_lower for kw in deep_vision_keywords):
                return TaskType.VISION_DEEP
            return TaskType.VISION_FAST
        
        # Text-only tasks
        if any(kw in task_lower for kw in ["resume", "comprime", "resume el historial", "compress"]):
            return TaskType.MEMORY_COMPRESS
        
        if any(kw in task_lower for kw in ["código", "code", "función", "def ", "class ", "import"]):
            return TaskType.CODE_COMPLETE
        
        if any(kw in task_lower for kw in ["embedding", "similar", "busca en memoria"]):
            return TaskType.EMBEDDING
        
        return TaskType.QUICK_CHAT
    
    def select(self, task: str, vision: bool = False,
               needs_tools: bool = False,
               max_latency_s: float | None = None) -> str:
        """
        Selecciona el modelo óptimo para la tarea.
        
        Args:
            task: Descripción de la tarea.
            vision: ¿Necesita analizar imágenes?
            needs_tools: ¿Necesita tool calling JSON?
            max_latency_s: Latencia máxima aceptable (None = sin límite).
        
        Returns:
            Nombre del modelo Ollama a usar.
        """
        task_type = self.classify_task(task, vision, needs_tools)
        
        # Mapa TaskType → modelo
        model_map = {
            TaskType.TOOL_CALL:      "deepseek-r1:14b",
            TaskType.VISION_FAST:    "moondream2",
            TaskType.VISION_DEEP:    "moondream:latest",
            TaskType.MEMORY_COMPRESS: "lfm2.5-thinking:1.2b",
            TaskType.CODE_COMPLETE:  "lfm2.5-thinking:1.2b",
            TaskType.QUICK_CHAT:     "lfm2.5-thinking:1.2b",
            TaskType.EMBEDDING:      "nomic-embed-text:latest",
        }
        
        selected = model_map.get(task_type, "lfm2.5-thinking:1.2b")
        
        # Respetar latencia máxima si se especifica
        if max_latency_s is not None:
            profile = MODELS.get(selected)
            if profile and profile.latency_s > max_latency_s:
                # Degradar a modelo más rápido pero sufficient
                if vision:
                    selected = "moondream2"
                else:
                    selected = "lfm2.5-thinking:1.2b"
        
        print(f"\033[94m[OPTIMIZER]\033[0m {task_type.value} → {selected.split(':')[0]}")  # pyre-ignore[arg-type]
        return selected
    
    def record_latency(self, model: str, latency_s: float) -> None:
        """Registra la latencia real observada para ajustar estimaciones futuras."""
        if model not in self._latency_history:
            self._latency_history[model] = []
        self._latency_history[model].append(latency_s)
        # Mantener solo las últimas 10 medidas
        self._latency_history[model] = self._latency_history[model][-10:]
    
    def get_real_latency(self, model: str) -> float | None:
        """Latencia promedio real observada para un modelo."""
        hist = self._latency_history.get(model, [])
        if not hist:
            return MODELS.get(model, ModelProfile(model, 10.0, False, False, 2048, [])).latency_s
        return sum(hist) / len(hist)
    
    def stats(self) -> dict:
        """Estadísticas de uso de modelos."""
        return {
            model: {
                "calls": len(lats),
                "avg_latency_s": round(sum(lats)/len(lats), 1) if lats else None,
                "estimated_s": MODELS.get(model, ModelProfile(model,10,False,False,2048,[])).latency_s
            }
            for model, lats in self._latency_history.items()
        }


# ══════════════════════════════════════════════════════════════════════════════
#  LATENCY OPTIMIZER — Wrapper para medir y optimizar llamadas a Ollama
# ══════════════════════════════════════════════════════════════════════════════

def timed_ollama_call(fn, model: str, selector: AdaptiveModelSelector, *args, **kwargs):
    """
    Wrapper que mide la latencia real de una llamada a Ollama y la registra.
    
    Uso:
        result = timed_ollama_call(analyze_screen, model, selector,
                                   screenshot_path, question)
    """
    start = time.time()
    result = fn(*args, **kwargs)
    elapsed = round(time.time() - start, 2)
    selector.record_latency(model, elapsed)
    return result


# ══════════════════════════════════════════════════════════════════════════════
#  VISION PIPELINE OPTIMIZER
# ══════════════════════════════════════════════════════════════════════════════

class VisionPipelineOptimizer:
    """
    Optimiza el pipeline de visión para Computer Use:
    - En loop frecuente (cada iteración): usa moondream (~5s)
    - Solo escala a llama3.2-vision cuando moondream no es suficiente
    - Cachea el último screenshot para evitar tomas redundantes
    
    Oráculo 2: "Adaptive model selection + parallel vision preprocessing"
    """
    
    def __init__(self) -> None:
        self.selector = AdaptiveModelSelector()
        self._last_screenshot: str | None = None
        self._last_screenshot_time: float = 0.0
        self._screenshot_cache_ttl: float = 2.0  # Segundos
        self._iteration_count: int = 0
    
    def should_take_new_screenshot(self) -> bool:
        """¿Necesitamos un screenshot nuevo o podemos usar el cacheado?"""
        if self._last_screenshot is None:
            return True
        age = time.time() - self._last_screenshot_time
        return age > self._screenshot_cache_ttl
    
    def analyze(self, question: str, force_deep: bool = False,
                screenshot_path: str | None = None) -> str:
        """
        Analiza la pantalla eligiendo el modelo óptimo automáticamente.
        
        Args:
            question: Pregunta sobre la pantalla.
            force_deep: Forzar llama3.2-vision (más lento pero más preciso).
            screenshot_path: Usar este path en vez de tomar uno nuevo.
        """
        from core.perception import take_screenshot, analyze_screen
        
        self._iteration_count += 1
        
        # Usar screenshot cacheado si es reciente
        if screenshot_path:
            path = screenshot_path
        elif not self.should_take_new_screenshot() and self._last_screenshot:
            path = self._last_screenshot
            print(f"\033[90m[OPTIMIZER] Usando screenshot cacheado (age < {self._screenshot_cache_ttl}s)\033[0m")
        else:
            path = take_screenshot(f"opt_{self._iteration_count}")
            if path:
                self._last_screenshot = path
                self._last_screenshot_time = time.time()
        
        if path is None:
            return "[OPTIMIZER] No se pudo capturar pantalla"
        
        # Seleccionar modelo
        use_deep = force_deep or "ocr" in question.lower() or "texto exacto" in question.lower()
        model_name = self.selector.select(question, vision=True)
        if use_deep:
            model_name = "moondream:latest"
        
        # Ejecutar con medición de latencia
        start = time.time()
        result = analyze_screen(path, question=question, deep=use_deep)
        elapsed = round(time.time() - start, 1)
        self.selector.record_latency(model_name, elapsed)
        
        print(f"\033[90m[OPTIMIZER] iter={self._iteration_count} model={model_name.split(':')[0]} latency={elapsed}s\033[0m")  # pyre-ignore[arg-type]
        return result
    
    def reset_iteration(self) -> None:
        """Resetear contador para nueva tarea."""
        self._iteration_count = 0


# Singletons globales
adaptive_selector = AdaptiveModelSelector()
vision_optimizer  = VisionPipelineOptimizer()


# ── Test rápido ──────────────────────────────────────────────────────────────
if __name__ == "__main__":
    print("=== Test Adaptive Model Selector ===")
    sel = AdaptiveModelSelector()
    
    tests = [
        ("haz ls en el sistema", False, True),
        ("¿qué hay en pantalla?", True, False),
        ("lee el texto exacto del documento en pantalla", True, False),
        ("ejecuta nmap -sn 192.168.1.0/24", False, True),
        ("resume el historial de conversación", False, False),
        ("completa esta función Python", False, False),
    ]
    
    for task, vision, tools in tests:
        model = sel.select(task, vision=vision, needs_tools=tools)
        print(f"  '{task[:40]}' → {model.split(':')[0]}")  # pyre-ignore[arg-type]
    
    # Test con latencia máxima
    fast = sel.select("describe la pantalla", vision=True, max_latency_s=6.0)
    print(f"\n  Con max_latency=6s: {fast.split(':')[0]}")  # pyre-ignore[arg-type]
    
    slow = sel.select("lee el texto exacto", vision=True, max_latency_s=6.0)
    print(f"  OCR con max_latency=6s (forzará degradación): {slow.split(':')[0]}")  # pyre-ignore[arg-type]
    
    print("\n✅ Adaptive Model Selector listo")
