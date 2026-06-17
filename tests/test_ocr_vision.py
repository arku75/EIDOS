import sys
import os

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from core.perception import take_screenshot, ocr_screenshot, find_element_by_text

print("=== TEST OCR PIPELINE (Fase 25) ===")

# 1. Tomar screenshot en vivo
screen_path = take_screenshot(label="test_ocr")
if not screen_path:
    print("Error capturando pantalla.")
    sys.exit(1)

print(f"✅ Screenshot guardado en {screen_path}")

# 2. Imprimir información básica
print("\nEjecutando Tesseract OCR sobre la captura actual...")
elems = ocr_screenshot(screen_path)

if not elems:
    print("❌ No se encontró ningún texto en absoluto. Verifica si tesseract funciona.")
else:
    print(f"✅ Se encontraron {len(elems)} palabras/frases en la pantalla.")
    print("Buscando las coordenadas de la palabra 'Aplicaciones' o 'Terminal'...")
    
    target = find_element_by_text(elems, "Terminal")
    if target:
        print(f"✅ ENCONTRADO 'Terminal' en las coordenadas X:{target.x} Y:{target.y}")
    else:
        target2 = find_element_by_text(elems, "Aplicaciones")
        if target2:
            print(f"✅ ENCONTRADO 'Aplicaciones' en las coordenadas X:{target2.x} Y:{target2.y}")
        else:
            print("❌ No se encontró 'Terminal' ni 'Aplicaciones' en pantalla (puede ser normal según el wallpaper). Muestra los primeros 5 textos:")
            for e in elems[:5]:  # pyre-ignore[arg-type]
                print(f"   - '{e.text}' en ({e.x}, {e.y})")

print("\n=== Test Finalizado ===")
