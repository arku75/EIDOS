
import asyncio
from skills.memory_engine import MemoryEngine

async def test_memory():
    print("🧠 [TEST] Iniciando Validación de Motor de Memoria...")
    
    engine = MemoryEngine()
    print("   ✅ Motor inicializado.")
    
    # 1. Test Short Term
    print("\n📝 [TEST 1] Memoria a Corto Plazo")
    engine.save_interaction("user", "Hola, me llamo Ser y me gusta el hacking.")
    engine.save_interaction("assistant", "Entendido, Ser. Guardado.")
    print("   ✅ Interacción guardada en short_term.json")
    
    # 2. Test Context Load
    print("\n📖 [TEST 2] Carga de Contexto")
    ctx = engine.load_context_string()
    print(f"   Contexto generado:\n{ctx}")
    
    print("\n✅ Verificación LOCAL completada.")

if __name__ == "__main__":
    asyncio.run(test_memory())
