"""
EIDOS Gateway - Adaptado de Hermes Agent
Servicio de gateway multi-canal para EIDOS Colony
"""

from .run import GatewayRunner, start_gateway
from .session import SessionManager, SessionSource, SessionContext

__all__ = [
    'GatewayRunner',
    'start_gateway', 
    'SessionManager',
    'SessionSource',
    'SessionContext',
]
