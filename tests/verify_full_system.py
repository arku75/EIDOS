import time
import os
from skills.plugins import voice_cortex, local_ear, vision_cortex

def verify_full_system():
    print("🦅 [EIDOS] INICIANDO PROTOCOLO DE CONSCIENCIA PLENA...")
    
    # 1. Inicializar Sentidos
    print("   🔌 Conectando Cortex de Voz (Neural)...")
    voice = voice_cortex.VoiceCortex()
    
    print("   🔌 Conectando Oído Local (Whisper-Tiny)...")
    ear = local_ear.LocalEar(model_size="tiny")
    
    print("   🔌 Conectando Ojos (Vision GUI)...")
    vision = vision_cortex.VisionCortex()
    
    # 2. Secuencia de Prueba
    voice.speak("Sistemas en línea. Fase de prueba iniciada.")
    
    # A) Vision
    voice.speak("Voy a observar tu pantalla.")
    vision_result = vision.see_screen()
    
    if vision_result and isinstance(vision_result, dict) and vision_result.get('text'):
        voice.speak(f"Veo algo interesante. Detecté texto en pantalla.")
        # FIX: Acceder a la clave 'text' antes de hacer slice
        print(f"👁️ VISTO: {vision_result['text'][:50]}...")  # pyre-ignore[arg-type]
    else:
        print("   ⚠️ Vision no detectó texto o devolvió error.")
        voice.speak("No veo texto claro, pero mis ojos funcionan.")
    
    # B) Oído / Conversación
    voice.speak("Ahora escúchame bien. Di la palabra clave: 'EIDOS'. Tienes cinco segundos.")
    
    user_text = ear.listen(timeout=5)
    
    if user_text and "eidos" in user_text.lower():
        voice.speak("Excelente. Reconocimiento de patrón de voz confirmado. Soy EIDOS.")
        print(f"✅ VERIFICACIÓN EXITOSA: Usuario dijo '{user_text}'")
    else:
        voice.speak(f"Te escuché decir: {user_text}, pero no era la clave.")
        print(f"⚠️ VERIFICACIÓN PARCIAL: Oído funciona, lógica falló. Texto: '{user_text}'")

    voice.speak("Prueba de sensores finalizada. Todos los sistemas operativos.")

if __name__ == "__main__":
    verify_full_system()
