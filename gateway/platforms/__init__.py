"""
Platform Adapters para EIDOS Gateway
Base para Telegram, Discord, Slack, Matrix, etc.
"""

from .base import BasePlatformAdapter, PlatformMessage, PlatformResponse

__all__ = ['BasePlatformAdapter', 'PlatformMessage', 'PlatformResponse']
