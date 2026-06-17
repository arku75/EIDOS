import time
import os
from skills.plugins import gui_master, vision_cortex, voice_cortex, ear_cortex

def test_senses():
    print("🤖 [EIDOS] INICIANDO PRUEBA DE SENTIDOS...")
    print("=========================================")
    
    # 1. TACTO (GUI Master)
    print("\n1. 🖐️ PRUEBA DE TACTO (Mouse)")
    print("   Moviendo mouse en cuadrado...")
    try:
        start_x, start_y = 500, 500
        size = 200
        gui_master.run("move", x=start_x, y=start_y)
        gui_master.run("move", x=start_x+size, y=start_y)
        gui_master.run("move", x=start_x+size, y=start_y+size)
        gui_master.run("move", x=start_x, y=start_y+size)
        gui_master.run("move", x=start_x, y=start_y)
        print("   ✅ Mouse movido correctamente.")
    except Exception as e:
        print(f"   ❌ Fallo motor: {e}")

    # 2. VISTA (Vision Cortex)
    print("\n2. 👁️ PRUEBA DE VISTA (OCR)")
    try:
        # Escribir algo en un archivo temporal para leerlo
        with open("test_vision.txt", "w") as f:
            f.write("PRUEBA DE VISION DE EIDOS EXITOSA")
        # Abrir editor simple (o un cat en terminal si fuera visible, pero usaremos screenshot directo del entorno actual)
        # Asumimos que la terminal muestra el texto de este script
        print("   Mirando la pantalla...")
        res = vision_cortex.run("see")
        if isinstance(res, dict) and 'text' in res:
            print("   ✅ Visión funcional.")
        else:
            print(f"   ⚠️ Visión borrosa o error: {res}")
    except Exception as e:
        print(f"   ❌ Ceguera: {e}")

    # 3. VOZ (Voice Cortex)
    print("\n3. 🗣️ PRUEBA DE VOZ (TTS)")
    try:
        res = voice_cortex.run("say", text="Sistemas operativos.")
        print(f"   {res}")
    except Exception as e:
         print(f"   ❌ Mudo: {e}")

    print("\n=========================================")
    print("✅ REPORTE FINALIZADO")

if __name__ == "__main__":
    test_senses()
