import sys
import os
sys.path.append("/home/ser/EIDOS")
from core.vision_60fps import generate_video_60fps

# Generar una animación de prueba: "Vórtice de Energía Púrpura"
print("🌀 Iniciando generación de vídeo de prueba (60fps)...")
result = generate_video_60fps("Purple Energy Vortex - Sacred Geometry", duration_s=1)
print(f"🎬 Resultado: {result}")
