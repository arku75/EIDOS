import time
from skills.plugins import voice_cortex, local_ear

def test_local_conversation():
    print("🤖 [EIDOS] INICIANDO SISTEMA NEURAL LOCAL...")
    
    # 1. Cargar Cerebro Auditivo
    ear = local_ear.LocalEar(model_size="tiny") # Tiny es rapidísimo
    voice = voice_cortex.VoiceCortex()

    # 2. Prueba
    voice.speak("Sistema local listo. Háblame ahora, soy todo oídos.")
    
    # Escuchar 5 segundos
    text = ear.listen(timeout=5)
    
    if text:
        voice.speak(f"Entendido. Dijiste: {text}")
        print(f"✅ EXITO LOCAL: '{text}'")
    else:
        voice.speak("No escuché nada.")

if __name__ == "__main__":
    test_local_conversation()
