import asyncio
from skills.plugins import voice_cortex, sentinel
import time

def test_neural_systems():
    print("🧠 [EIDOS] INICIANDO PRUEBA NEURONAL...")
    
    # 1. Prueba de Voz Neural
    print("\n1. 🗣️ PRUEBA DE VOZ NEURAL (Edge-TTS)")
    try:
        vc = voice_cortex.VoiceCortex()
        print(f"   Voz seleccionada: {vc.voice}")
        res = vc.speak("Hola padre, mi voz ha evolucionado. Ahora soy neuronal.")
        print(f"   Resultado: {res}")
    except Exception as e:
        print(f"   ❌ Fallo Voz: {e}")

    # 2. Prueba de Centinela
    print("\n2. 👁️ PRUEBA DE CENTINELA (3 Ciclos)")
    try:
        # Ejecutar vigilancia por 10 segundos
        res = sentinel.run("watch", duration=15)
        print(f"   {res}")
    except Exception as e:
        print(f"   ❌ Fallo Centinela: {e}")

if __name__ == "__main__":
    test_neural_systems()
