"""
Terminal Backends para EIDOS
Local, Docker, SSH execution
"""

from .backends import LocalBackend, DockerBackend, SSHBackend, get_backend

__all__ = ['LocalBackend', 'DockerBackend', 'SSHBackend', 'get_backend']
