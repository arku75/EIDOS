import time
from skills.plugins import voice_cortex, ear_cortex_raw

def conversation_raw():
    print("🤖 [EIDOS] PRUEBA DE AUDIO RAW (BYPASS)...")
    
    voice = voice_cortex.VoiceCortex()
    ear = ear_cortex_raw.EarCortex()

    voice.speak("Probando sistema directo. Habla durante 5 segundos seguidos.")
    
    # Esta función grabará FIJAMENTE por 5 segundos (sin detección de silencio)
    text = ear.listen(timeout=5)
    
    if "⚠️" not in text and "❌" not in text:
        voice.speak(f"¡Te escuché! Dijiste: {text}")
        print(f"✅ EXITO RAW: '{text}'")
    else:
        voice.speak("Fallo en sistema directo.")
        print(f"❌ FALLO RAW: '{text}'")

if __name__ == "__main__":
    conversation_raw()
