"""
EIDOS Smart Cache - Sistema de cache inteligente
Evita re-procesar datos costosos (OCR, Vision, screenshots, etc.)

Filosofía:
- Cache solo lo costoso (>1s de procesamiento)
- TTL configurable por tipo de dato
- Límite de tamaño global (500MB max)
- Auto-limpieza de cache antiguo
- Persistente en disco
"""
import hashlib
import json
import pickle
import time
import shutil
from pathlib import Path
from typing import Any, Optional, Callable
from functools import wraps
from dataclasses import dataclass


@dataclass
class CacheEntry:
    """Entrada de cache"""
    key: str
    value: Any
    timestamp: float
    ttl: int
    size_bytes: int


class SmartCache:
    """
    Cache inteligente para resultados costosos

    Casos de uso:
    - OCR de screenshots (evitar re-procesar mismo screenshot)
    - Vision models (resultados de moondream/llama3.2-vision)
    - Web scraping (páginas ya visitadas)
    - Compilación de código
    """

    CACHE_DIR = Path.home() / ".eidos" / "cache"
    MAX_CACHE_SIZE_MB = 500  # Máximo 500MB de cache total
    DEFAULT_TTL = 300  # 5 minutos por defecto

    # TTLs específicos por tipo
    TTL_CONFIG = {
        'ocr': 300,        # 5 min - screenshots cambian frecuentemente
        'vision': 600,     # 10 min - vision models son costosos
        'web': 1800,       # 30 min - páginas web cambian menos
        'code': 3600,      # 1 hora - código compilado
        'generic': 300,    # 5 min - por defecto
    }

    def __init__(self):
        self.CACHE_DIR.mkdir(parents=True, exist_ok=True)
        self._memory_cache: dict[str, CacheEntry] = {}

        # Estadísticas
        self.hits = 0
        self.misses = 0

        # Auto-limpieza al inicio
        self._cleanup_old_cache()

        print(f"💾 [Smart Cache] Inicializado")
        print(f"   Dir: {self.CACHE_DIR}")
        print(f"   Max size: {self.MAX_CACHE_SIZE_MB}MB")

    def _get_cache_path(self, key: str) -> Path:
        """Obtiene ruta de archivo de cache"""
        key_hash = hashlib.md5(key.encode()).hexdigest()
        return self.CACHE_DIR / f"{key_hash}.cache"

    def _get_cache_size_mb(self) -> float:
        """Obtiene tamaño total de cache en MB"""
        total_bytes = 0
        for cache_file in self.CACHE_DIR.glob("*.cache"):
            try:
                total_bytes += cache_file.stat().st_size
            except Exception:
                pass  # error no crítico, continuar
        return total_bytes / (1024 * 1024)

    def get(self, key: str, cache_type: str = 'generic') -> Optional[Any]:
        """
        Obtiene valor de cache si existe y no ha expirado

        Args:
            key: Clave única (ej: hash de screenshot)
            cache_type: Tipo de cache para determinar TTL

        Returns:
            Valor cacheado o None si no existe/expiró
        """
        # 1. Revisar memoria (más rápido)
        if key in self._memory_cache:
            entry = self._memory_cache[key]
            age = time.time() - entry.timestamp

            if age < entry.ttl:
                self.hits += 1
                return entry.value
            else:
                # Expirado, eliminar de memoria
                del self._memory_cache[key]

        # 2. Revisar disco
        cache_path = self._get_cache_path(key)
        if cache_path.exists():
            try:
                with open(cache_path, "rb") as f:
                    data = pickle.load(f)

                timestamp = data["timestamp"]
                ttl = data.get("ttl", self.TTL_CONFIG.get(cache_type, self.DEFAULT_TTL))
                age = time.time() - timestamp

                if age < ttl:
                    # Válido, cargar en memoria para próximo acceso
                    value = data["value"]
                    entry = CacheEntry(
                        key=key,
                        value=value,
                        timestamp=timestamp,
                        ttl=ttl,
                        size_bytes=cache_path.stat().st_size
                    )
                    self._memory_cache[key] = entry

                    self.hits += 1
                    return value
                else:
                    # Expirado, eliminar
                    cache_path.unlink()
                    self.misses += 1

            except Exception as e:
                # Error leyendo cache, eliminar
                try:
                    cache_path.unlink()
                except Exception:
                    pass  # error no crítico, continuar
                self.misses += 1

        else:
            self.misses += 1

        return None

    def set(self, key: str, value: Any, cache_type: str = 'generic'):
        """
        Guarda valor en cache

        Args:
            key: Clave única
            value: Valor a cachear
            cache_type: Tipo para determinar TTL
        """
        timestamp = time.time()
        ttl = self.TTL_CONFIG.get(cache_type, self.DEFAULT_TTL)

        # Guardar en disco
        cache_path = self._get_cache_path(key)

        try:
            with open(cache_path, "wb") as f:
                pickle.dump({
                    "value": value,
                    "timestamp": timestamp,
                    "ttl": ttl,
                    "cache_type": cache_type
                }, f)

            size_bytes = cache_path.stat().st_size

            # Guardar en memoria
            entry = CacheEntry(
                key=key,
                value=value,
                timestamp=timestamp,
                ttl=ttl,
                size_bytes=size_bytes
            )
            self._memory_cache[key] = entry

            # Verificar tamaño total
            self._check_cache_size()

        except Exception as e:
            print(f"⚠️  [Cache] Error guardando: {e}")

    def _check_cache_size(self):
        """Verifica y limpia si cache excede tamaño máximo"""
        current_size_mb = self._get_cache_size_mb()

        if current_size_mb > self.MAX_CACHE_SIZE_MB:
            print(f"⚠️  [Cache] Tamaño excedido ({current_size_mb:.1f}MB > {self.MAX_CACHE_SIZE_MB}MB)")
            print(f"   Limpiando cache antiguo...")

            # Eliminar archivos más antiguos primero
            cache_files = list(self.CACHE_DIR.glob("*.cache"))
            cache_files.sort(key=lambda p: p.stat().st_mtime)

            deleted = 0
            for cache_file in cache_files:
                try:
                    cache_file.unlink()
                    deleted += 1

                    # Verificar si ya estamos bajo el límite
                    current_size_mb = self._get_cache_size_mb()
                    if current_size_mb < self.MAX_CACHE_SIZE_MB * 0.8:  # 80% del máximo
                        break
                except Exception:
                    pass  # error no crítico, continuar
            print(f"   ✅ Limpiados {deleted} archivos")

    def _cleanup_old_cache(self, max_age: int = 3600):
        """Limpia cache antiguo (>1 hora por defecto)"""
        now = time.time()
        cleaned = 0

        for cache_file in self.CACHE_DIR.glob("*.cache"):
            try:
                age = now - cache_file.stat().st_mtime
                if age > max_age:
                    cache_file.unlink()
                    cleaned += 1
            except Exception:
                pass  # error no crítico, continuar
        if cleaned > 0:
            print(f"   🗑️  Limpiados {cleaned} archivos antiguos al inicio")

    def clear_all(self):
        """Limpia TODO el cache"""
        try:
            shutil.rmtree(self.CACHE_DIR)
            self.CACHE_DIR.mkdir(parents=True, exist_ok=True)
            self._memory_cache.clear()
            print("🗑️  [Cache] Todo el cache limpiado")
        except Exception as e:
            print(f"❌ [Cache] Error limpiando: {e}")

    def get_stats(self) -> dict:
        """Obtiene estadísticas de cache"""
        total_requests = self.hits + self.misses
        hit_rate = (self.hits / total_requests * 100) if total_requests > 0 else 0

        return {
            'hits': self.hits,
            'misses': self.misses,
            'hit_rate': hit_rate,
            'memory_entries': len(self._memory_cache),
            'disk_size_mb': self._get_cache_size_mb(),
            'disk_files': len(list(self.CACHE_DIR.glob("*.cache")))
        }


# Singleton
smart_cache = SmartCache()


# ═══════════════════════════════════════════════════════════════════════════
# Decorator para cachear funciones automáticamente
# ═══════════════════════════════════════════════════════════════════════════

def cached(cache_type: str = 'generic', ttl: Optional[int] = None):
    """
    Decorator para cachear resultados de funciones costosas

    Ejemplo:
        @cached(cache_type='vision', ttl=600)
        def analyze_image(image_path):
            # Procesamiento costoso...
            return result
    """
    def decorator(func: Callable) -> Callable:
        @wraps(func)
        def wrapper(*args, **kwargs):
            # Generar key única basada en función + argumentos
            key_data = f"{func.__name__}:{str(args)}:{str(kwargs)}"
            key = hashlib.md5(key_data.encode()).hexdigest()

            # Intentar obtener de cache
            cached_result = smart_cache.get(key, cache_type=cache_type)
            if cached_result is not None:
                return cached_result

            # Ejecutar función
            result = func(*args, **kwargs)

            # Guardar en cache
            smart_cache.set(key, result, cache_type=cache_type)

            return result

        return wrapper
    return decorator


# ═══════════════════════════════════════════════════════════════════════════
# Funciones de conveniencia para integración con EIDOS
# ═══════════════════════════════════════════════════════════════════════════

def cache_ocr_result(screenshot_path: str, elements: list):
    """Cachea resultado de OCR"""
    key = f"ocr:{screenshot_path}"
    smart_cache.set(key, elements, cache_type='ocr')


def get_cached_ocr(screenshot_path: str) -> Optional[list]:
    """Obtiene OCR cacheado"""
    key = f"ocr:{screenshot_path}"
    return smart_cache.get(key, cache_type='ocr')


def cache_vision_result(screenshot_path: str, question: str, result: str):
    """Cachea resultado de Vision model"""
    key = f"vision:{screenshot_path}:{question}"
    smart_cache.set(key, result, cache_type='vision')


def get_cached_vision(screenshot_path: str, question: str) -> Optional[str]:
    """Obtiene Vision cacheado"""
    key = f"vision:{screenshot_path}:{question}"
    return smart_cache.get(key, cache_type='vision')


# ═══════════════════════════════════════════════════════════════════════════
# Test
# ═══════════════════════════════════════════════════════════════════════════

if __name__ == "__main__":
    import time

    print("=== Test Smart Cache ===\n")

    # Test 1: Set y Get básico
    print("Test 1: Set y Get")
    smart_cache.set("test_key", "test_value", cache_type='generic')
    result = smart_cache.get("test_key")
    assert result == "test_value", "Cache debería retornar el valor"
    print(f"  ✅ Valor cacheado: {result}\n")

    # Test 2: TTL (expiración)
    print("Test 2: TTL (expiración)")
    smart_cache.TTL_CONFIG['test'] = 2  # 2 segundos
    smart_cache.set("expire_key", "expire_value", cache_type='test')
    result1 = smart_cache.get("expire_key", cache_type='test')
    print(f"  Antes de expirar: {result1}")
    time.sleep(3)  # Esperar expiración
    result2 = smart_cache.get("expire_key", cache_type='test')
    print(f"  Después de expirar: {result2}")
    assert result2 is None, "Cache debería expirar"

    # Test 3: Decorator
    print("\nTest 3: Decorator @cached")

    @cached(cache_type='generic')
    def expensive_function(x):
        """Simula función costosa"""
        time.sleep(0.1)  # Simular procesamiento
        return x * 2

    # Primera llamada (sin cache)
    start = time.time()
    result1 = expensive_function(5)
    time1 = time.time() - start

    # Segunda llamada (con cache)
    start = time.time()
    result2 = expensive_function(5)
    time2 = time.time() - start

    print(f"  Primera llamada: {time1:.3f}s")
    print(f"  Segunda llamada (cache): {time2:.3f}s")
    assert time2 < time1, "Segunda llamada debería ser más rápida"
    print(f"  ✅ Cache {(time1/time2):.1f}x más rápido\n")

    # Test 4: Estadísticas
    print("Test 4: Estadísticas")
    stats = smart_cache.get_stats()
    print(f"  Hits: {stats['hits']}")
    print(f"  Misses: {stats['misses']}")
    print(f"  Hit rate: {stats['hit_rate']:.1f}%")
    print(f"  Cache size: {stats['disk_size_mb']:.2f}MB")

    print("\n✅ Smart Cache funcional")
