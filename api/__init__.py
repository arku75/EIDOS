"""
API Server para EIDOS
OpenAI-compatible API para integración con frontends
"""

from .server import APIServer, get_api_server

__all__ = ['APIServer', 'get_api_server']
