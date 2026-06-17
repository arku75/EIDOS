import sys
import os

# Ensure EIDOS path is set
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from core.math_art import generate_math_art

print(generate_math_art("Geometría sagrada, Patrón abstracto inspirado en la Semilla de la Vida (Seed of Life) con proporciones áureas, estilo Funebra (fondo oscuro, líneas doradas o naranjas brillantes)"))
