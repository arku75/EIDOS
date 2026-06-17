import asyncio
import edge_tts
import pygame
import os

async def test_voice():
    print("🗣️ [TEST] Generando voz neuronal (es-ES-AlvaroNeural)...")
    voice = "es-ES-AlvaroNeural"
    text = "Hola Ser. Soy EIDOS. Esta es mi nueva voz neuronal de alta definición. ¿Me escuchas bien?"
    output = "test_voice.mp3"
    
    try:
        communicate = edge_tts.Communicate(text, voice)
        await communicate.save(output)
        print(f"   ✅ Audio generado: {output}")
        
        print("   ▶️ Reproduciendo...")
        pygame.mixer.init()
        pygame.mixer.music.load(output)
        pygame.mixer.music.play()
        
        while pygame.mixer.music.get_busy():
            pygame.time.Clock().tick(10)
            
        pygame.mixer.quit()
        print("   ✅ Prueba finalizada exitosamente.")
        
    except Exception as e:
        print(f"   ❌ Error: {e}")

if __name__ == "__main__":
    asyncio.run(test_voice())
