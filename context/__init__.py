"""
Context Management para EIDOS
Compresión y gestión de ventana de contexto
"""

from .compressor import ContextCompressor
from .manager import ContextManager

__all__ = ['ContextCompressor', 'ContextManager']
