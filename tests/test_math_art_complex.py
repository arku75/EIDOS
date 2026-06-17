import os
import json
import math
import numpy as np
import matplotlib.pyplot as plt

# Ecuación polinómica de Fourier (Fénix) determinista. 
# Evitamos llamar al LLM local para no agotar la RAM de Ser.
data = {
  "x": "100*np.sin(t) + 45*np.cos(3*t) - 30*np.sin(5*t) + 20*np.cos(7*t) - 15*np.sin(9*t) + 10*np.cos(11*t) - 8*np.sin(13*t) + 6*np.cos(15*t) - 5*np.sin(17*t) + 4*np.cos(19*t) - 3*np.sin(21*t) + 2.5*np.cos(23*t) - 2*np.sin(25*t) + 1.5*np.cos(27*t) - 1*np.sin(29*t) + 0.5*np.cos(31*t)",
  "y": "80*np.cos(t) - 35*np.sin(2*t) + 25*np.cos(4*t) - 18*np.sin(6*t) + 12*np.cos(8*t) - 9*np.sin(10*t) + 7*np.cos(12*t) - 5*np.sin(14*t) + 4*np.cos(16*t) - 3*np.sin(18*t) + 2.5*np.cos(20*t) - 2*np.sin(22*t) + 1.5*np.cos(24*t) - 1*np.sin(26*t) + 0.8*np.cos(28*t) - 0.5*np.sin(30*t)",
  "t_min": 0.0,
  "t_max": 62.8318,
  "points": 300000,
  "linewidth": 0.3,
  "color": "magenta",
  "background": "black"
}

t = np.linspace(data["t_min"], data["t_max"], data["points"])

# Entorno seguro básico para simulacion
safe_globals = {
    "np": np,
    "math": math,
    "sin": np.sin,
    "cos": np.cos,
    "exp": np.exp,
    "pi": np.pi,
    "PHI": 1.61803398875,
    "t": t
}

try:
    x_vals = eval(data["x"], {"__builtins__": None}, safe_globals)
    y_vals = eval(data["y"], {"__builtins__": None}, safe_globals)
    
    art_path = os.path.expanduser("~/.eidos/art/phoenix_fourier.png")
    os.makedirs(os.path.dirname(art_path), exist_ok=True)
    
    fig, ax = plt.subplots(figsize=(10, 10))
    fig.patch.set_facecolor(data["background"])
    ax.set_facecolor(data["background"])
    
    ax.plot(x_vals, y_vals, color=data["color"], linewidth=data["linewidth"], alpha=0.9)
    ax.axis("off")
    plt.tight_layout()
    plt.savefig(art_path, dpi=300, facecolor=data["background"])
    print(f"✅ Matemáticas Hipercomplejas evaluadas.")
    print(f"✅ Fénix de Fourier generado espectacularmente y guardado en:\n {art_path}")

except Exception as e:
    print(f"Error evaluando: {e}")
