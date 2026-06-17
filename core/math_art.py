"""
EIDOS core/math_art.py — Mathematical Art Engine
=================================================
Genera arte paramétrico 2D utilizando fórmulas matemáticas complejas
diseñadas dinámicamente por IA e inspiradas en Hamid Naderi Yeganeh.
"""

import os
import json
import time
import math
import numpy as np
import matplotlib.pyplot as plt
from matplotlib.colors import LinearSegmentedColormap
from core.model_manager import get_model_manager, TaskType

# Directorio de arte
ART_DIR = os.path.expanduser("~/.eidos/art")
os.makedirs(ART_DIR, exist_ok=True)

MATH_ART_PROMPT = """
Eres el motor de arte matemático de EIDOS inspirado en Hamid Naderi Yeganeh.

CONTEXTO CRÍTICO - Hamid Naderi Yeganeh:
Hamid es un matemático iraní famoso por crear arte ESPECTACULAR usando SOLO ecuaciones paramétricas.
Sus obras icónicas:
- "A Bird in Flight" (9,000 curvas)
- "Flower" (50,000 curvas circulares concéntricas)
- "Heart" (funciones trigonométricas asimétricas)
- Todas usan patrones de MILES de curvas con pequeñas variaciones por índice k

ESTILO HAMID NADERI YEGANEH:
1. **Pattern con índice k**: Para cada k de 1 a N (N=500-10000):
   - x(k,t) = A(k)*sin(f(k)*t + phase(k)) + offset_x(k)
   - y(k,t) = B(k)*cos(g(k)*t + phase(k)) + offset_y(k)
   Donde A,B,f,g,phase,offset varían SUAVEMENTE con k

2. **Fórmulas maestras de Hamid**:
   - Bird: x = 4*sin(t) + k*0.01*cos(k*t/100)
   - Flower: x = k*cos(t + k*pi/1000), y = k*sin(t + k*pi/1000)
   - Heart: x = 16*sin(t)**3, y = 13*cos(t) - 5*cos(2*t) - 2*cos(3*t) - cos(4*t)

3. **Densidad visual**: Usa 500-10000 curvas superpuestas, NO solo 1 curva.

4. **Simetría orgánica**: Muchas obras tienen simetría radial (k*2*pi/N) o reflexión

Variables disponibles:
- `t`: array numpy (0 a 2*pi típicamente)
- `k`: índice de curva (1 a N)
- `N`: total de curvas (típicamente 1000-5000)
- `np`: numpy completo
- `PHI`: número áureo (1.618...)
- `math`: librería math

**FORMATO DE SALIDA - MODO HAMID:**
Devuelve JSON con estructura de BUCLE (k de 1 a N):

{
  "mode": "hamid_multi_curve",
  "N": 2000,
  "t_min": 0.0,
  "t_max": 6.2832,
  "points_per_curve": 100,
  "x_formula": "4*np.sin(t) + k*0.01*np.cos((k/N)*t)",
  "y_formula": "4*np.cos(t) + k*0.01*np.sin((k/N)*t)",
  "color_mode": "gradient",
  "color_start": "blue",
  "color_end": "cyan",
  "background": "white",
  "linewidth": 0.3,
  "alpha": 0.5,
  "explanation": "Descripción breve estilo Hamid"
}

**ALTERNATIVA - MODO CLÁSICO (single curve):**
{
  "mode": "classic_single",
  "x": "16*np.sin(t)**3",
  "y": "13*np.cos(t) - 5*np.cos(2*t) - 2*np.cos(3*t) - np.cos(4*t)",
  "t_min": 0.0,
  "t_max": 6.2832,
  "points": 10000,
  "color": "red",
  "background": "white",
  "linewidth": 1.0,
  "explanation": "Corazón matemático"
}

IMPORTANTE:
- USA "hamid_multi_curve" para objetos orgánicos (pájaros, flores, rostros)
- USA "classic_single" para formas simples (corazón, estrella)
- Colores básicos: black, white, red, blue, cyan, magenta, purple, gold, orange
- NO uses markdown. Empieza con {

Tema solicitado: {prompt}
"""

def generate_math_art(prompt: str, style: str = "Hamid") -> str:
    """
    Genera una imagen artística usando ecuaciones paramétricas.

    Soporta dos modos:
    - hamid_multi_curve: Miles de curvas estilo Hamid Naderi Yeganeh
    - classic_single: Una sola curva paramétrica
    """
    mm = get_model_manager()

    full_prompt = MATH_ART_PROMPT.replace("{prompt}", f"{prompt} (Estilo: {style})")

    try:
        model = mm.select(TaskType.CODE)
        response = mm.generate_text(model, full_prompt)
    except Exception as e:
        return f"Error de ModelManager: {e}"

    # Extraer JSON de la respuesta
    try:
        start_idx = response.find("{")
        end_idx = response.rfind("}") + 1
        if start_idx == -1 or end_idx == 0:
            raise ValueError("No JSON found in LLM response")

        json_str = response[start_idx:end_idx]
        data = json.loads(json_str)

        mode = data.get("mode", "classic_single")

    except Exception as e:
        return f"Error parseando el diseño matemático: {e}\nRespuesta recibida: {response[:300]}"

    # Generar según modo
    if mode == "hamid_multi_curve":
        return _render_hamid_multi_curve(data)
    else:
        return _render_classic_single(data)


def _render_hamid_multi_curve(data: dict) -> str:
    """Renderiza arte estilo Hamid con múltiples curvas"""
    try:
        N = int(data.get("N", 1000))
        t_min = float(data.get("t_min", 0.0))
        t_max = float(data.get("t_max", 2*np.pi))
        points_per_curve = int(data.get("points_per_curve", 100))

        x_formula = data["x_formula"]
        y_formula = data["y_formula"]

        color_mode = data.get("color_mode", "single")
        color_start = data.get("color_start", "black")
        color_end = data.get("color_end", "blue")
        bg_color = data.get("background", "white")
        lw = float(data.get("linewidth", 0.3))
        alpha = float(data.get("alpha", 0.5))

        # Setup plot
        fig, ax = plt.subplots(figsize=(12, 12), facecolor=bg_color)
        ax.set_facecolor(bg_color)
        ax.axis('off')
        ax.set_aspect('equal', adjustable='box')

        # Generate t array
        t = np.linspace(t_min, t_max, points_per_curve)

        # Safe environment for eval
        safe_globals = {
            "np": np,
            "math": math,
            "sin": np.sin,
            "cos": np.cos,
            "exp": np.exp,
            "pi": np.pi,
            "PHI": (1 + np.sqrt(5)) / 2,
            "t": t,
            "N": N
        }

        # Color gradient setup
        if color_mode == "gradient":
            from matplotlib.colors import LinearSegmentedColormap
            cmap = LinearSegmentedColormap.from_list("custom", [color_start, color_end])

        # Render N curves
        print(f"[MathArt] Renderizando {N} curvas estilo Hamid...")

        for k in range(1, N + 1):
            safe_globals["k"] = k

            try:
                x_vals = eval(x_formula, {"__builtins__": {}}, safe_globals)
                y_vals = eval(y_formula, {"__builtins__": {}}, safe_globals)

                # Color selection
                if color_mode == "gradient":
                    color = cmap(k / N)
                else:
                    color = color_start

                ax.plot(x_vals, y_vals, color=color, linewidth=lw, alpha=alpha)

            except Exception as e:
                if k == 1:  # Only report first error
                    print(f"[MathArt] Warning: Error en curva k={k}: {e}")

            # Progress indicator every 10%
            if k % max(N // 10, 1) == 0:
                progress = int((k / N) * 100)
                print(f"[MathArt] Progreso: {progress}%")

        # Save
        timestamp = int(time.time())
        filename = f"hamid_art_{timestamp}.png"
        filepath = os.path.join(ART_DIR, filename)

        plt.savefig(filepath, format='png', dpi=300, bbox_inches='tight', facecolor=bg_color)
        plt.close()

        explanation = data.get("explanation", "Arte estilo Hamid Naderi Yeganeh")

        return f"""✅ Arte estilo Hamid guardado en: {filepath}

Curvas: {N}
Fórmulas (k=1..{N}):
  x(k,t) = {x_formula}
  y(k,t) = {y_formula}

{explanation}"""

    except Exception as e:
        return f"Error renderizando Hamid multi-curve: {e}"


def _render_classic_single(data: dict) -> str:
    """Renderiza arte clásico con una sola curva"""
    try:
        eq_x = data["x"]
        eq_y = data["y"]
        t_min = float(data.get("t_min", 0.0))
        t_max = float(data.get("t_max", 2*np.pi))
        points = int(data.get("points", 10000))
        color = data.get("color", "black")
        bg_color = data.get("background", "white")
        lw = float(data.get("linewidth", 1.0))

        # Generate t array
        t = np.linspace(t_min, t_max, points)

        # Safe environment
        safe_globals = {
            "np": np,
            "math": math,
            "sin": np.sin,
            "cos": np.cos,
            "exp": np.exp,
            "pi": np.pi,
            "PHI": (1 + np.sqrt(5)) / 2,
            "t": t
        }

        # Evaluate
        x_vals = eval(eq_x, {"__builtins__": {}}, safe_globals)
        y_vals = eval(eq_y, {"__builtins__": {}}, safe_globals)

        # Plot
        plt.figure(figsize=(10, 10), facecolor=bg_color)
        plt.plot(x_vals, y_vals, color=color, linewidth=lw, alpha=0.9)
        plt.axis('off')
        plt.gca().set_aspect('equal', adjustable='box')

        # Save
        timestamp = int(time.time())
        filename = f"math_art_{timestamp}.png"
        filepath = os.path.join(ART_DIR, filename)

        plt.savefig(filepath, format='png', dpi=300, bbox_inches='tight', facecolor=bg_color)
        plt.close()

        explanation = data.get("explanation", "Arte matemático")

        return f"""✅ Arte guardado en: {filepath}

Ecuaciones:
  x(t) = {eq_x}
  y(t) = {eq_y}

{explanation}"""

    except Exception as e:
        return f"Error renderizando classic single: {e}"

class MathArtGenerator:
    """
    Generador de arte matemático para video_creator.py y otros módulos.
    Wrapper orientado a objetos sobre las funciones de math_art.
    """

    def __init__(self):
        self.model_manager = get_model_manager()
        self.output_dir = ART_DIR

    def generate_formulas_for_theme(self, theme: str, num_frames: int = 60) -> list:
        """
        Genera una secuencia de fórmulas matemáticas para animación.

        Args:
            theme: Tema artístico (ej. "cosmic energy", "digital waves")
            num_frames: Número de frames a generar

        Returns:
            Lista de dicts con fórmulas para cada frame
        """
        formulas = []

        # Para animación, variamos parámetros gradualmente
        for i in range(num_frames):
            progress = i / max(num_frames - 1, 1)  # 0.0 to 1.0

            # Generar variación del tema
            frame_theme = f"{theme} - fase {progress:.2f}"

            # Prompt simplificado para frames individuales
            prompt = f"""Genera ecuaciones paramétricas para: {frame_theme}

Devuelve JSON:
{{
  "x": "ecuación compleja",
  "y": "ecuación compleja",
  "t_min": 0,
  "t_max": 6.28,
  "k": {progress * 10}
}}

Usa variable 'k' para animación. No incluyas markdown."""

            try:
                model = self.model_manager.select(TaskType.CODE)
                response = self.model_manager.generate_text(model, prompt)

                # Parsear JSON
                start_idx = response.find("{")
                end_idx = response.rfind("}") + 1
                if start_idx != -1 and end_idx > 0:
                    json_str = response[start_idx:end_idx]
                    data = json.loads(json_str)
                    formulas.append(data)
                else:
                    # Fallback: usar fórmula predeterminada con variación
                    k = progress * 10
                    fallback = {
                        "x": f"np.sin(t*{k+1}) * (1 + 0.5 * np.cos({k} + t))",
                        "y": f"np.cos(t*{k+1}) * (1 + 0.5 * np.sin({k} + t))",
                        "t_min": 0,
                        "t_max": 6.28,
                        "k": k
                    }
                    formulas.append(fallback)

            except Exception as e:
                # Fallback en caso de error
                k = progress * 10
                fallback = {
                    "x": f"np.sin(t*{k+1}) * np.exp(-t/{10+k})",
                    "y": f"np.cos(t*{k+1}) * np.exp(-t/{10+k})",
                    "t_min": 0,
                    "t_max": 6.28,
                    "k": k
                }
                formulas.append(fallback)

        return formulas

    def render_formula(self, formula: dict, output_path: str,
                      color: str = "white", bg_color: str = "black",
                      points: int = 20000) -> bool:
        """
        Renderiza una fórmula matemática a imagen PNG.

        Args:
            formula: Dict con keys 'x', 'y', 't_min', 't_max'
            output_path: Ruta donde guardar la imagen
            color: Color de línea
            bg_color: Color de fondo
            points: Número de puntos a evaluar

        Returns:
            True si se generó correctamente
        """
        try:
            eq_x = formula["x"]
            eq_y = formula["y"]
            t_min = float(formula.get("t_min", 0.0))
            t_max = float(formula.get("t_max", 6.28))
            k = formula.get("k", 0)

            # Evaluar fórmulas
            t = np.linspace(t_min, t_max, points)

            safe_globals = {
                "np": np,
                "math": math,
                "sin": np.sin,
                "cos": np.cos,
                "exp": np.exp,
                "pi": np.pi,
                "PHI": (1 + np.sqrt(5)) / 2,
                "t": t,
                "k": k
            }

            x_vals = eval(eq_x, {"__builtins__": {}}, safe_globals)
            y_vals = eval(eq_y, {"__builtins__": {}}, safe_globals)

            # Renderizar
            plt.figure(figsize=(10, 10), facecolor=bg_color)
            plt.plot(x_vals, y_vals, color=color, linewidth=0.5, alpha=0.8)
            plt.axis('off')
            plt.gca().set_aspect('equal', adjustable='box')

            plt.savefig(output_path, format='png', dpi=150,
                       bbox_inches='tight', facecolor=bg_color)
            plt.close()

            return True

        except Exception as e:
            print(f"[MathArtGenerator] Error rendering: {e}")
            return False

    def generate_static_art(self, prompt: str, style: str = "Yeganeh-style") -> str:
        """
        Genera arte estático (wrapper sobre generate_math_art).

        Returns:
            Ruta al archivo generado
        """
        result = generate_math_art(prompt, style)

        # Extraer filepath de la respuesta
        if "Arte guardado en:" in result:
            filepath = result.split("Arte guardado en:")[1].split("\n")[0].strip()
            return filepath
        else:
            return ""


# Singleton global para acceso rápido
_math_art_generator = None

def get_math_art_generator() -> MathArtGenerator:
    """Get singleton MathArtGenerator instance"""
    global _math_art_generator
    if _math_art_generator is None:
        _math_art_generator = MathArtGenerator()
    return _math_art_generator


if __name__ == "__main__":
    # Test función original
    print(generate_math_art("una mariposa geométrica", style="neon on dark background"))

    # Test clase nueva
    gen = get_math_art_generator()
    formulas = gen.generate_formulas_for_theme("cosmic energy", num_frames=3)
    print(f"\nGeneradas {len(formulas)} fórmulas para animación")
