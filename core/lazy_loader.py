"""
EIDOS Lazy Loader - Carga módulos solo cuando se necesitan
Reduce RAM inicial de ~1.5GB a ~200MB

Inspirado en la lógica de Claude: cargar solo lo necesario, cuando se necesita
"""
import importlib
import sys
from typing import Any, Dict, Callable, Optional
import time


class LazyModule:
    """Wrapper para módulo que se carga bajo demanda (thread-safe)."""

    def __init__(self, module_name: str, description: str = ""):
        import threading
        self.module_name = module_name
        self.description = description
        self._module: Optional[Any] = None
        self._load_time: Optional[float] = None
        self._lock = threading.Lock()  # thread-safety para carga concurrente

    def _load(self) -> Any:
        """Carga el módulo real (thread-safe: solo se carga una vez)."""
        if self._module is None:
            with self._lock:
                if self._module is None:  # double-checked locking
                    start = time.time()
                    print(f"🔄 [Lazy] Cargando {self.module_name}...", end=" ")
                    try:
                        self._module = importlib.import_module(self.module_name)
                        elapsed = (time.time() - start) * 1000
                        self._load_time = elapsed
                        print(f"✅ ({elapsed:.1f}ms)")
                    except Exception as e:
                        print(f"❌ Error: {e}")
                        raise
        return self._module

    def __getattr__(self, name: str) -> Any:
        """Intercepta accesos y carga módulo si es necesario"""
        return getattr(self._load(), name)

    def __call__(self, *args, **kwargs):
        """Permite llamar al módulo si es callable"""
        module = self._load()
        if callable(module):
            return module(*args, **kwargs)
        raise TypeError(f"{self.module_name} is not callable")


class LazyLoader:
    """
    Gestiona carga lazy de módulos de EIDOS

    Filosofía Claude:
    - Cargar SOLO lo core al inicio
    - Todo lo demás lazy (vision, tools especializados, etc.)
    - Medir tiempos de carga
    - Cachear lo cargado
    """

    # Módulos CORE que se cargan SIEMPRE al inicio (mínimo absoluto)
    CORE_MODULES = [
        "core.memory",
        "core.autonomous",
    ]

    # Módulos LAZY que se cargan bajo demanda
    LAZY_MODULES = {
        # Vision (solo cargar cuando se use)
        "perception": ("core.perception", "Sistema de visión multimodal"),
        "vision_trainer": ("core.vision_trainer", "Entrenador de visión"),
        "vision_60fps": ("core.vision_60fps", "Generador de video 60fps"),
        "computer_use": ("core.computer_use_v2", "Control de escritorio"),
        "gui_observer": ("core.gui_observer", "Observador de GUI"),

        # Creación de contenido (solo cuando se necesite)
        "multi_uploader": ("core.multi_uploader", "Subida a redes sociales"),
        "math_art": ("core.math_art", "Arte matemático"),

        # Desarrollo y debugging (solo cuando se necesite)
        "bugbot": ("core.bugbot", "Auto-corrector de bugs"),
        "adb_bridge": ("core.adb_bridge", "Puente Android ADB"),

        # Navegador (solo cuando se use)
        "eidos_browser": ("core.eidos_browser", "Navegador integrado"),
        "claude_web": ("core.claude_web", "Cliente web de Claude"),

        # Knowledge y training
        "knowledge_evolver": ("core.knowledge_evolver", "Evolucionador de conocimiento"),
        "soul_builder": ("core.soul_builder", "Constructor de alma/personalidad"),

        # Herramientas avanzadas
        "cronos_factory": ("core.cronos_factory", "Fábrica de agentes Cronos"),
        "web_hunter": ("core.web_hunter", "Cazador web"),
        "compactor": ("core.compactor", "Compactador de datos"),

        # Sistema
        "optimizer": ("core.optimizer", "Optimizador del sistema"),
        "audit_log": ("core.audit_log", "Log de auditoría"),
        "shadow_monitor": ("core.shadow_monitor", "Monitor de sombra"),

        # DAG y orquestación
        "dag_planner": ("core.dag_planner", "Planificador DAG"),
        "orchestrator": ("core.orchestrator", "Orquestador de tareas"),
        "task_queue": ("core.task_queue", "Cola de tareas"),

        # Skills (bajo demanda)
        "video_creator": ("skills.video_creator", "Creador de videos faceless"),
        "apk_builder": ("skills.apk_builder", "Constructor de APKs Android"),
    }

    def __init__(self):
        self._loaded: Dict[str, Any] = {}
        self._load_times: Dict[str, float] = {}
        self._access_count: Dict[str, int] = {}

    def setup(self):
        """Configura lazy loading (solo tracking, NO modifica sys.modules)"""
        # NO modificamos sys.modules - causa recursión infinita
        # En su lugar, usamos lazy_loader.get() para cargar módulos
        print(f"✅ [Lazy Loader] {len(self.LAZY_MODULES)} módulos configurados para lazy loading")

    def get(self, alias: str) -> Any:
        """
        Obtiene módulo (lo carga si no está cargado)

        Args:
            alias: Alias del módulo (ej: "perception", "bugbot")

        Returns:
            Módulo cargado
        """
        # Actualizar contador de accesos
        self._access_count[alias] = self._access_count.get(alias, 0) + 1

        # Si ya está cargado, retornar
        if alias in self._loaded:
            return self._loaded[alias]

        # Obtener info del módulo
        module_info = self.LAZY_MODULES.get(alias)
        if not module_info:
            raise ValueError(f"Módulo desconocido: {alias}")

        module_name, description = module_info

        # Cargar módulo
        start = time.time()
        print(f"🔄 [Lazy] Cargando {alias} ({description})...", end=" ")

        try:
            module = importlib.import_module(module_name)
            elapsed_ms = (time.time() - start) * 1000

            self._loaded[alias] = module
            self._load_times[alias] = elapsed_ms

            print(f"✅ ({elapsed_ms:.1f}ms)")
            return module

        except Exception as e:
            print(f"❌ Error: {e}")
            raise

    def preload(self, aliases: list[str]):
        """
        Pre-carga módulos específicos

        Útil para cargar módulos que sabes que vas a necesitar pronto
        """
        print(f"📦 [Lazy] Pre-cargando {len(aliases)} módulos...")
        for alias in aliases:
            self.get(alias)

    def get_stats(self) -> Dict[str, Any]:
        """Obtiene estadísticas de uso de módulos"""
        return {
            "loaded": len(self._loaded),
            "total": len(self.LAZY_MODULES),
            "load_times": self._load_times,
            "access_count": self._access_count,
            "most_used": sorted(
                self._access_count.items(),
                key=lambda x: x[1],
                reverse=True
            )[:10]
        }

    def unload(self, alias: str):
        """
        Descarga módulo de RAM (útil para liberar memoria)

        ADVERTENCIA: Solo usar si estás seguro que no se usará más
        """
        if alias in self._loaded:
            del self._loaded[alias]
            module_name, _ = self.LAZY_MODULES[alias]
            if module_name in sys.modules:
                del sys.modules[module_name]
            print(f"🗑️  [Lazy] Módulo {alias} descargado")

    def print_stats(self):
        """Imprime estadísticas de lazy loading"""
        stats = self.get_stats()

        print("\n" + "="*60)
        print("📊 LAZY LOADER STATS")
        print("="*60)
        print(f"Módulos cargados: {stats['loaded']}/{stats['total']}")
        print(f"\nMás usados:")
        for alias, count in stats['most_used']:
            load_time = stats['load_times'].get(alias, 0)
            print(f"  {alias:20s} - {count:3d} accesos ({load_time:.1f}ms carga)")
        print("="*60 + "\n")


# Singleton
lazy_loader = LazyLoader()


# ─────────────────────────────────────────────────────────────────────────────
# Funciones de conveniencia
# ─────────────────────────────────────────────────────────────────────────────

def lazy_import(alias: str) -> Any:
    """
    Importa módulo lazy

    Ejemplo:
        perception = lazy_import("perception")
        screenshot = perception.take_screenshot()
    """
    return lazy_loader.get(alias)


# ─────────────────────────────────────────────────────────────────────────────
# Test
# ─────────────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    import sys
    from pathlib import Path

    # Añadir EIDOS root al path
    eidos_root = Path(__file__).parent.parent
    if str(eidos_root) not in sys.path:
        sys.path.insert(0, str(eidos_root))

    print("=== Test Lazy Loader ===\n")

    # Setup
    lazy_loader.setup()

    # Test 1: Cargar perception
    print("\nTest 1: Cargar perception")
    perception = lazy_loader.get("perception")
    print(f"  Tipo: {type(perception)}")
    print(f"  Tiene take_screenshot: {hasattr(perception, 'take_screenshot')}")

    # Test 2: Cargar bugbot
    print("\nTest 2: Cargar bugbot")
    bugbot = lazy_loader.get("bugbot")
    print(f"  Tipo: {type(bugbot)}")

    # Test 3: Cargar perception de nuevo (debe ser instantáneo)
    print("\nTest 3: Re-cargar perception (debería estar en cache)")
    perception2 = lazy_loader.get("perception")
    assert perception is perception2, "Debería ser el mismo objeto"
    print("  ✅ Cache funciona - es el mismo objeto")

    # Test 4: Stats
    lazy_loader.print_stats()

    print("\n✅ Lazy Loader funciona correctamente")
