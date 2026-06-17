"""
EIDOS core/model_manager.py — Dynamic Model Manager
====================================================
Selección inteligente de modelos según tarea, recursos disponibles
y perfil del hardware (CPU AMD Ryzen 5 7520U, 14GB RAM, sin CUDA).

Estrategia de cuantización para CPU-only (AMD iGPU sin CUDA):
  - Q4_K_M  → Sweet spot: 99% calidad, token/s razonable en CPU
  - Q4_K_S  → Más ligero pero calidad 97% — para tareas rápidas
  - Q8_0    → Alta calidad pero lento en CPU — solo si tenemos tiempo
  - Q2_K    → ❌ Demasiada pérdida de calidad para EIDOS

Modelos instalados:
  lfm2.5-thinking:1.2b    (731MB) — razonamiento y planificación
  lfm2.5-1.2b-instruct:q4_0 (695MB) — rápido, generación código
  moondream:latest        (1.7GB) — visión (VLM)
  nomic-embed-text         (274MB) — embeddings semánticos

Uso:
    from core.model_manager import ModelManager, TaskType
    mm = ModelManager()
    model = mm.select(TaskType.TOOL_CALL)
    response = mm.generate(model, prompt)
"""
from __future__ import annotations

import json
import os
import subprocess
import threading
import time
import urllib.request
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Optional

# ── Hardware profile (actualizar si cambias de máquina) ──────────────────────
OLLAMA_URL        = os.environ.get("OLLAMA_URL", "http://127.0.0.1:11434")
CPU_CORES         = 8       # AMD Ryzen 5 7520U (threads)
RAM_TOTAL_GB      = 14.0
HAS_CUDA          = False   # AMD iGPU → sin CUDA
GPU_VRAM_GB       = 0.0     # VRAM compartida — no usar para inferencia

# ── Umbrales de activación de modelos ────────────────────────────────────────
# RAM libre mínima para cargar cada modelo
MODEL_RAM_REQUIREMENTS: dict[str, float] = {
    "deepseek-r1:14b":          5.5,   # necesita ~5.5GB libres
    "moondream:latest":         2.0,   # visión ~2GB
    "lfm2.5-thinking:1.2b":     1.0,   # razonamiento ~1GB
    "lfm2.5-1.2b-instruct:q4_0": 0.8, # rápido ~0.8GB
    "nomic-embed-text:latest":  0.5,
    "llama3.1:8b":              4.8,   # alternativa si se instala
    "phi4-mini:3.8b-q4_K_M":    4.0,   # alternativa ligera
    "gemma3:4b-q4_K_M":         4.5,   # alternativa Google
}


class TaskType(str, Enum):
    TOOL_CALL    = "tool_call"      # cloud (DeepSeek/Groq) — los únicos con tool-calling fiable
    PLAN         = "plan"           # modelo rápido → lfm2.5-thinking:1.2b
    REFLECT      = "reflect"        # modelo rápido → lfm2.5-thinking:1.2b
    VISION_FAST  = "vision_fast"    # moondream (5-8s en CPU) → capturas frecuentes
    VISION_SEC   = "vision_sec"     # gemma3:4b-it-qat (QAT, nativo) → cuando moondream no basta
    VISION_DEEP  = "vision_deep"    # moondream:latest
    EMBED        = "embed"          # nomic-embed-text
    CODE         = "code"           # lfm2.5-1.2b-instruct:q4_0 (rápido)
    REASON       = "reason"         # lfm2.5-thinking:1.2b → matemáticas, lógica, JSON
    CHAT         = "chat"           # cloud primero, lfm2.5-thinking:1.2b como fallback local


@dataclass
class ModelProfile:
    name:       str
    task_types: list[TaskType]
    ram_needed: float          # GB de RAM libre necesarios
    speed_est:  str            # "fast" | "medium" | "slow"
    quality:    int            # 1-100
    has_vision: bool = False
    has_tools:  bool = False


# Catálogo de modelos disponibles en el sistema (S120 — actualizado jun 2026)
CATALOG: list[ModelProfile] = [
    ModelProfile(
        name="lfm2.5-thinking:1.2b",
        task_types=[TaskType.PLAN, TaskType.REFLECT, TaskType.REASON, TaskType.CHAT],
        ram_needed=1.0, speed_est="medium", quality=82
    ),
    ModelProfile(
        name="lfm2.5-1.2b-instruct:q4_0",
        task_types=[TaskType.CODE, TaskType.REFLECT, TaskType.PLAN],
        ram_needed=0.8, speed_est="fast", quality=78
    ),
    ModelProfile(
        name="moondream:latest",
        task_types=[TaskType.VISION_FAST, TaskType.VISION_DEEP],
        ram_needed=2.0, speed_est="medium", quality=80,
        has_vision=True
    ),
    ModelProfile(
        name="nomic-embed-text:latest",
        task_types=[TaskType.EMBED],
        ram_needed=0.5, speed_est="fast", quality=90
    ),
]


def _get_ram_free_gb() -> float:
    """Lee RAM disponible desde /proc/meminfo."""
    try:
        with open("/proc/meminfo") as f:
            lines = f.readlines()
        mem = {l.split()[0].rstrip(":"): int(l.split()[1])  # pyre-ignore[arg-type]
               for l in lines if len(l.split()) >= 2 and l.split()[1].isdigit()}  # pyre-ignore[arg-type]
        available_kb = mem.get("MemAvailable", 0)
        return available_kb / (1024 * 1024)  # → GB
    except Exception:
        return 4.0  # fallback conservador


def _get_ollama_loaded_models() -> list[str]:
    """Qué modelos tiene Ollama actualmente en memoria."""
    try:
        req = urllib.request.Request(f"{OLLAMA_URL}/api/ps")
        with urllib.request.urlopen(req, timeout=3) as resp:
            data = json.load(resp)
        return [m.get("name", "") for m in data.get("models", [])]
    except Exception:
        return []


class ModelManager:
    """
    Selecciona el modelo óptimo para cada tarea basándose en:
    1. RAM disponible en tiempo real
    2. Modelo ya cargado en Ollama (evitar swap)
    3. Tipo de tarea (tool_call, vision, embed, etc.)
    4. Calidad mínima requerida

    En CPU AMD sin CUDA → prioriza modelos Q4_K_M ya cargados.
    """

    def __init__(self) -> None:
        self._lock         = threading.Lock()
        self._last_used:Optional[str] = None
        self._cache: dict[str, str]   = {}   # task_type → model_name
        self.catalog       = CATALOG          # expuesto para acceso externo

    def select(self, task: TaskType, min_quality: int = 70) -> str:
        """
        Devuelve el nombre del modelo más adecuado para la tarea.
        Prioriza:
          1. Modelo ya cargado en RAM
          2. Menor RAM necesaria dentro del perfil adecuado
          3. Mayor calidad dentro de los que caben
        """
        ram_free   = _get_ram_free_gb()
        loaded     = set(_get_ollama_loaded_models())

        # Candidatos que pueden hacer esta tarea y caben en RAM
        candidates = [
            m for m in CATALOG
            if task in m.task_types
            and m.ram_needed <= ram_free
            and m.quality >= min_quality
        ]

        if not candidates:
            # Fallback: el más ligero disponible con tools si es tool_call
            if task == TaskType.TOOL_CALL:
                return "deepseek-r1:14b"
            return "lfm2.5-thinking:1.2b"

        # Priorizar: primero los ya cargados
        loaded_candidates = [m for m in candidates if m.name in loaded]
        if loaded_candidates:
            best = max(loaded_candidates, key=lambda m: m.quality)
            print(f"\033[90m[🧠 MODEL] {task.value} → {best.name} (ya cargado, RAM:{ram_free:.1f}GB)\033[0m")
            return best.name

        # Si ninguno está cargado, elegir el de mayor calidad que quepa
        best = max(candidates, key=lambda m: m.quality)
        print(f"\033[90m[🧠 MODEL] {task.value} → {best.name} (RAM:{ram_free:.1f}GB libre)\033[0m")
        return best.name

    def generate(
        self,
        model: str,
        prompt: str,
        system: str = "",
        images: list[str] = [],
        tools: list[dict] = [],
        max_tokens: int = 4096,
        temperature: float = 0.7,
        stream: bool = False,
    ) -> dict[str, Any]:
        """
        Llamada unificada a Ollama. Retorna el dict completo de la respuesta.
        Compatible con tool-calling, vision y texto puro.
        """
        payload: dict[str, Any] = {
            "model":   model,
            "stream":  stream,
            "options": {
                "num_predict": max_tokens,
                "temperature": temperature,
                "num_thread":  CPU_CORES,      # usar todos los cores AMD
                "num_ctx":     8192,           # contexto razonable para 14GB RAM
            }
        }

        if images:
            # Modo vision: usar /api/generate con imágenes base64
            payload["prompt"] = prompt
            payload["images"] = images
            if system:
                payload["system"] = system
            endpoint = f"{OLLAMA_URL}/api/generate"
        elif tools:
            # Modo tool-calling: usar /api/chat con messages
            messages = []
            if system:
                messages.append({"role": "system", "content": system})
            messages.append({"role": "user", "content": prompt})
            payload["messages"] = messages
            payload["tools"]    = tools
            endpoint = f"{OLLAMA_URL}/api/chat"
        else:
            # Modo chat puro
            messages = []
            if system:
                messages.append({"role": "system", "content": system})
            messages.append({"role": "user", "content": prompt})
            payload["messages"] = messages
            endpoint = f"{OLLAMA_URL}/api/chat"

        data = json.dumps(payload).encode()
        req  = urllib.request.Request(
            endpoint, data=data,
            headers={"Content-Type": "application/json"}
        )

        try:
            t0 = time.time()
            with urllib.request.urlopen(req, timeout=120) as resp:
                result = json.load(resp)
            elapsed = round(time.time() - t0, 1)
            print(f"\033[90m[⏱  MODEL] {model.split(':')[0]} → {elapsed}s\033[0m")  # pyre-ignore[arg-type]
            self._last_used = model
            return result
        except Exception as e:
            return {"error": str(e), "model": model}

    def generate_text(self, model: str, prompt: str, system: str = "", max_tokens: int = 1024) -> str:
        """Wrapper simplificado que retorna solo el texto."""
        result = self.generate(model, prompt, system=system, max_tokens=max_tokens)
        if "error" in result:
            return f"[MODEL ERROR] {result['error']}"
        # Chat response
        if "message" in result:
            return result["message"].get("content", "")
        # Generate response
        return result.get("response", "")

    def pull_model_if_missing(self, model_name: str) -> bool:
        """Descarga un modelo de Ollama si no está instalado."""
        try:
            req = urllib.request.Request(f"{OLLAMA_URL}/api/tags")
            with urllib.request.urlopen(req, timeout=5) as resp:
                tags = json.load(resp)
            installed = [m["name"] for m in tags.get("models", [])]
            if model_name in installed:
                return True
        except Exception:
            pass  # error no crítico, continuar
        print(f"\033[93m[MODEL] Descargando {model_name}...\033[0m")
        r = subprocess.run(
            ["ollama", "pull", model_name],
            capture_output=True, text=True, timeout=600
        )
        return r.returncode == 0

    def suggest_missing_quantizations(self) -> list[str]:
        """
        Sugiere modelos ligeros. En descarga ahora:
          - gemma3:4b-it-qat (visión nativa QAT, 2.5GB) → VISION_SEC
          - lfm2.5-thinking:1.2b (cerebro secundario, 4.7GB) → PLAN/REFLECT/REASON
        phi4-mini descartado: sin visión, overlap con lfm2.5-thinking:1.2b.
        Monitoriza el progreso en /tmp/eidos_pull_*.log
        """
        suggestions = []
        try:
            req = urllib.request.Request(f"{OLLAMA_URL}/api/tags")
            with urllib.request.urlopen(req, timeout=5) as resp:
                tags = json.load(resp)
            installed_names = [m["name"] for m in tags.get("models", [])]
        except Exception:
            installed_names = []

        wish_list = [
            ("gemma3:4b-it-qat", "Visión nativa QAT (BF16 calidad, 2.5GB) — DESCARGANDO"),
            ("lfm2.5-thinking:1.2b",       "Cerebro secundario PLAN/REFLECT (4.7GB, 128K ctx) — DESCARGANDO"),
        ]
        for name, reason in wish_list:
            if name not in installed_names:
                suggestions.append(f"  ollama pull {name}  # {reason}")

        return suggestions

    def status_report(self) -> str:
        """Resumen del estado de los modelos para EIDOS."""
        ram_free = _get_ram_free_gb()
        loaded   = _get_ollama_loaded_models()
        lines = [
            f"[🧠 MODEL MANAGER] RAM libre: {ram_free:.1f}GB / {RAM_TOTAL_GB}GB",
            f"  Cargados ahora: {loaded or ['ninguno']}",
            f"  CPU: AMD Ryzen 5 7520U | Threads: {CPU_CORES} | CUDA: {HAS_CUDA}",
            "",
            "  Perfil de modelos para esta sesión:"
        ]
        for task in TaskType:
            chosen = self.select(task)
            lines.append(f"    {task.value:20s} → {chosen}")
        return "\n".join(lines)


# Singleton global
_mm: Optional[ModelManager] = None

def get_model_manager() -> ModelManager:
    global _mm
    if _mm is None:
        _mm = ModelManager()
    return _mm


# ── CLI rápido ────────────────────────────────────────────────────────────────
if __name__ == "__main__":
    mm = get_model_manager()
    print(mm.status_report())
    print()
    suggestions = mm.suggest_missing_quantizations()
    if suggestions:
        print("  💡 Modelos recomendados para descargar:")
        for s in suggestions:
            print(s)
    else:
        print("  ✅ Perfil de modelos completo")
