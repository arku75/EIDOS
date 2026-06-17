"""
EIDOS core/vision_60fps.py — Motor de Video Ultrarealista (Fase 3)
==================================================================
Genera animaciones fluidas de 60fps basadas en ecuaciones dinámicas.
Implementa el flujo: Ecuación -> Secuencia PNG -> FFmpeg Concat.
"""
from __future__ import annotations

import os
import json
import time
import numpy as np
import matplotlib.pyplot as plt
import subprocess
from core.model_manager import get_model_manager, TaskType

VIDEO_DIR = os.path.expanduser("~/.eidos/videos")
os.makedirs(VIDEO_DIR, exist_ok=True)

PROMPT_60FPS = """
Eres el Maestro de la Geometría Sagrada de EIDOS. Tu misión es generar una OBRA DE ARTE matemática.
No hagas gráficos simples. Crea sistemas de partículas y ecuaciones fractales complejas.

IMPORTANTE: Devuelve ÚNICAMENTE un objeto JSON con este formato:
{
  "x": "Ecuación compleja (ej. np.sin(t*k) * np.tan(t/k))",
  "y": "Ecuación compleja (ej. np.cos(t*k) * np.exp(-t/10))",
  "t_min": 0, "t_max": 12.56,
  "k_start": 1, "k_end": 5,
  "color": "Una paleta cinemática (ej. '#8A2BE2', '#4B0082', '#FFD700')",
  "style": "glow" o "scatter" o "line",
  "background": "black"
}
Usa PHI (1.618) y constantes matemáticas para que sea armónico.
"""

def generate_video_60fps(prompt: str, duration_s: int = 2) -> str:
    """Genera un video artístico de 60fps."""
    mm = get_model_manager()
    model = "deepseek-r1:14b"
    
    try:
        full_prompt = PROMPT_60FPS + f"\n\nTema Artístico: {prompt}\nJSON:"
        resp = mm.generate_text(model, full_prompt)
        
        # Limpieza de JSON
        if "```json" in resp: resp = resp.split("```json")[1].split("```")[0]
        start = resp.find("{"); end = resp.rfind("}") + 1
        data = json.loads(resp[start:end])
    except Exception:
        data = {
            "x": "np.sin(t*PHI) * (1 + 0.5 * np.cos(k + t))",
            "y": "np.cos(t*PHI) * (1 + 0.5 * np.sin(k + t))",
            "t_min": 0, "t_max": 12.56, "k_start": 0, "k_end": 6.28,
            "color": "#8A2BE2", "style": "line", "background": "black"
        }

    fps = 60
    total_frames = duration_s * fps
    # Usar directorio persistente en lugar de /tmp
    frames_dir = os.path.expanduser("~/.eidos/frames")
    os.makedirs(frames_dir, exist_ok=True)
    temp_dir = os.path.join(frames_dir, f"art_{int(time.time())}")
    os.makedirs(temp_dir, exist_ok=True)

    t = np.linspace(data["t_min"], data["t_max"], 20000) # Más resolución
    k_vals = np.linspace(data["k_start"], data["k_end"], total_frames)

    print(f"[ART ENGINE] Renderizando {total_frames} fotogramas de alta fidelidad...")

    frames_generated = []
    for i, k in enumerate(k_vals):
        safe_globals = {"np": np, "t": t, "k": k, "PHI": 1.618, "PI": 3.14159}
        try:
            x_vals = eval(data["x"], {"__builtins__": {}}, safe_globals)
            y_vals = eval(data["y"], {"__builtins__": {}}, safe_globals)

            plt.figure(figsize=(10, 10), facecolor=data["background"])
            if data.get("style") == "scatter":
                plt.scatter(x_vals, y_vals, c=data["color"], s=0.1, alpha=0.5)
            else:
                plt.plot(x_vals, y_vals, color=data["color"], linewidth=0.5, alpha=0.8)

            plt.axis('off')
            plt.gca().set_aspect('equal')
            frame_path = f"{temp_dir}/frame_{i:04d}.png"
            plt.savefig(frame_path, dpi=120, facecolor=data["background"])
            plt.close()
            frames_generated.append(frame_path)
        except Exception:
            plt.close()
            continue

    # Esperar a que todos los frames estén escritos completamente
    print(f"[ART ENGINE] Esperando a que {len(frames_generated)} frames se escriban completamente...")
    time.sleep(0.5)  # Safety delay para asegurar que matplotlib terminó de escribir

    # Verificar que los frames existen antes de compilar
    frames_ok = sum(1 for f in frames_generated if os.path.exists(f) and os.path.getsize(f) > 0)
    print(f"[ART ENGINE] Frames válidos: {frames_ok}/{len(frames_generated)}")

    if frames_ok == 0:
        print("[ART ENGINE] ERROR: No se generaron frames válidos")
        return None

    # Compilar
    output_path = os.path.join(VIDEO_DIR, f"art_{int(time.time())}.mp4")
    result = subprocess.run(["ffmpeg", "-y", "-framerate", str(fps), "-i", f"{temp_dir}/frame_%04d.png",
                    "-c:v", "libx264", "-pix_fmt", "yuv420p", output_path],
                    capture_output=True, text=True)

    if result.returncode != 0:
        print(f"[ART ENGINE] ERROR FFmpeg: {result.stderr}")
        return None

    # Limpiar frames temporales
    print(f"[ART ENGINE] Limpiando frames temporales...")
    for frame in frames_generated:
        try:
            os.remove(frame)
        except Exception:
            pass  # error no crítico, continuar
    try:
        os.rmdir(temp_dir)
    except Exception:
        pass  # error no crítico, continuar
    print(f"[ART ENGINE] ✅ Video generado: {output_path}")
    return output_path

if __name__ == "__main__":
    print(generate_video_60fps("una espiral de lucifer transformándose", duration_s=1))
