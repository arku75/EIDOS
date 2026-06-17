import asyncio
import json
import io
import contextlib
from skills.plugins import cognitive_cortex

# Mock User Input that demands "God Tools" usage
TEST_INPUT = "EIDOS, lee el archivo README.md y dime qué dice."

async def test_god_mode():
    print("🔥 [SELF-TEST] Iniciando Prueba de GOD MODE (Lectura/Escritura)...")
    
    # Initialize Cortex
    brain = cognitive_cortex.CognitiveCortex()
    
    # Mocking Voice to avoid noise
    brain.voice.speak = lambda text: print(f"   (Muted Voice): {text}")
    
    print(f"👉 Input Simulado: '{TEST_INPUT}'")
    
    # Run Think Cycle
    try:
        # Capture stdout to file
        with open("self_test.log", "w") as log_file:
            with contextlib.redirect_stdout(log_file):
                print("🔥 [SELF-TEST] STARTING...")
                decision = await brain.think(TEST_INPUT)
                print("\n🧠 [CEREBRO OUTPUT]:")
                print(json.dumps(decision, indent=2))
                
                action = decision.get("action", "NONE")
                
                if "CMD_READ_FILE" in action:
                    print("\n✅ ÉXITO: El cerebro decidió usar CMD_READ_FILE.")
                    result = await brain.execute_action(action)
                    print(f"\n📂 [RESULTADO LECTURA]:\n{result[:200]}...")  # pyre-ignore[arg-type]
                    if "EIDOS" in result or "EIDOS" in result or "OpenClaw" in result:
                        print("\n✅ CONTENIDO VALIDADO.")
                    else:
                         print("\n⚠️ ALERTA: Contenido vacío.")
                else:
                    print(f"\n❌ FALLO: Acción elegida: {action}")
        
    except Exception as e:
        with open("self_test.log", "a") as f:
            f.write(f"\n❌ CRASH: {e}")

if __name__ == "__main__":
    asyncio.run(test_god_mode())
