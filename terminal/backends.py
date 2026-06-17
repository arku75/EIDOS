"""
Terminal Backends - Container execution for EIDOS
Adaptado de Hermes
"""

import asyncio
import json
import logging
import os
import subprocess
import tempfile
from abc import ABC, abstractmethod
from pathlib import Path
from typing import Dict, Any, List, Optional, Tuple

logger = logging.getLogger(__name__)

class TerminalBackend(ABC):
    """Backend abstracto para ejecución de comandos"""
    
    def __init__(self, config: Dict[str, Any]):
        self.config = config
        self.backend_type = "abstract"
        
    @abstractmethod
    async def execute(
        self,
        command: str,
        cwd: Optional[str] = None,
        env: Optional[Dict[str, str]] = None,
        timeout: int = 300
    ) -> Tuple[int, str, str]:
        """
        Ejecutar comando.
        Returns: (exit_code, stdout, stderr)
        """
        pass
    
    @abstractmethod
    async def check_health(self) -> bool:
        """Verificar si el backend está disponible"""
        pass

class LocalBackend(TerminalBackend):
    """Backend local - ejecuta en la máquina actual"""
    
    def __init__(self, config: Dict[str, Any]):
        super().__init__(config)
        self.backend_type = "local"
        
    async def execute(
        self,
        command: str,
        cwd: Optional[str] = None,
        env: Optional[Dict[str, str]] = None,
        timeout: int = 300
    ) -> Tuple[int, str, str]:
        """Ejecutar comando localmente"""
        env_vars = {**os.environ, **(env or {})}
        
        try:
            proc = await asyncio.create_subprocess_shell(
                command,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                cwd=cwd or os.getcwd(),
                env=env_vars
            )
            
            stdout, stderr = await asyncio.wait_for(
                proc.communicate(),
                timeout=timeout
            )
            
            return (
                proc.returncode or 0,
                stdout.decode('utf-8', errors='replace'),
                stderr.decode('utf-8', errors='replace')
            )
            
        except asyncio.TimeoutError:
            proc.kill()
            return -1, "", f"Timeout after {timeout}s"
        except Exception as e:
            return -1, "", str(e)
    
    async def check_health(self) -> bool:
        """Local siempre disponible"""
        return True

class DockerBackend(TerminalBackend):
    """Backend Docker - ejecuta en contenedor"""
    
    def __init__(self, config: Dict[str, Any]):
        super().__init__(config)
        self.backend_type = "docker"
        self.image = config.get("image", "nikolaik/python-nodejs:python3.11-nodejs20")
        self.volumes = config.get("volumes", [])
        self.memory = config.get("memory", "512m")
        self.cpu = config.get("cpu", "1")
        
    async def execute(
        self,
        command: str,
        cwd: Optional[str] = None,
        env: Optional[Dict[str, str]] = None,
        timeout: int = 300
    ) -> Tuple[int, str, str]:
        """Ejecutar comando en contenedor Docker"""
        
        # Construir comando docker
        docker_cmd = ["docker", "run", "--rm"]
        
        # Límites de recursos
        docker_cmd.extend(["--memory", self.memory])
        docker_cmd.extend(["--cpus", str(self.cpu)])
        
        # Timeout
        docker_cmd.extend(["--stop-signal", "SIGKILL"])
        
        # Volumes
        for vol in self.volumes:
            docker_cmd.extend(["-v", vol])
        
        # Working directory
        if cwd:
            docker_cmd.extend(["-w", "/workspace"])
            docker_cmd.extend(["-v", f"{cwd}:/workspace"])
        
        # Environment
        for key, value in (env or {}).items():
            docker_cmd.extend(["-e", f"{key}={value}"])
        
        # Image y comando
        docker_cmd.append(self.image)
        docker_cmd.extend(["sh", "-c", command])
        
        try:
            proc = await asyncio.create_subprocess_exec(
                *docker_cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE
            )
            
            stdout, stderr = await asyncio.wait_for(
                proc.communicate(),
                timeout=timeout
            )
            
            return (
                proc.returncode or 0,
                stdout.decode('utf-8', errors='replace'),
                stderr.decode('utf-8', errors='replace')
            )
            
        except asyncio.TimeoutError:
            return -1, "", f"Docker timeout after {timeout}s"
        except Exception as e:
            return -1, "", str(e)
    
    async def check_health(self) -> bool:
        """Verificar Docker disponible"""
        try:
            proc = await asyncio.create_subprocess_exec(
                "docker", "version",
                stdout=asyncio.subprocess.DEVNULL,
                stderr=asyncio.subprocess.DEVNULL
            )
            await proc.wait()
            return proc.returncode == 0
        except:
            return False

class SSHBackend(TerminalBackend):
    """Backend SSH - ejecuta en servidor remoto"""
    
    def __init__(self, config: Dict[str, Any]):
        super().__init__(config)
        self.backend_type = "ssh"
        self.host = config.get("host", "")
        self.user = config.get("user", "")
        self.port = config.get("port", 22)
        self.key_file = config.get("key_file")
        
    async def execute(
        self,
        command: str,
        cwd: Optional[str] = None,
        env: Optional[Dict[str, str]] = None,
        timeout: int = 300
    ) -> Tuple[int, str, str]:
        """Ejecutar comando vía SSH"""
        
        ssh_cmd = ["ssh", "-o", "StrictHostKeyChecking=no"]
        
        if self.port != 22:
            ssh_cmd.extend(["-p", str(self.port)])
        
        if self.key_file:
            ssh_cmd.extend(["-i", self.key_file])
        
        target = f"{self.user}@{self.host}"
        
        # Construir comando completo
        full_cmd = command
        if cwd:
            full_cmd = f"cd {cwd} && {full_cmd}"
        
        ssh_cmd.extend([target, full_cmd])
        
        try:
            proc = await asyncio.create_subprocess_exec(
                *ssh_cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE
            )
            
            stdout, stderr = await asyncio.wait_for(
                proc.communicate(),
                timeout=timeout
            )
            
            return (
                proc.returncode or 0,
                stdout.decode('utf-8', errors='replace'),
                stderr.decode('utf-8', errors='replace')
            )
            
        except asyncio.TimeoutError:
            return -1, "", f"SSH timeout after {timeout}s"
        except Exception as e:
            return -1, "", str(e)
    
    async def check_health(self) -> bool:
        """Verificar SSH conectable"""
        if not all([self.host, self.user]):
            return False
        
        try:
            proc = await asyncio.create_subprocess_exec(
                "ssh", "-o", "StrictHostKeyChecking=no",
                "-o", "ConnectTimeout=5",
                f"{self.user}@{self.host}", "echo OK",
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE
            )
            stdout, _ = await asyncio.wait_for(proc.communicate(), timeout=10)
            return b"OK" in stdout
        except:
            return False

# Registry
_backends: Dict[str, type] = {
    "local": LocalBackend,
    "docker": DockerBackend,
    "ssh": SSHBackend,
}

def get_backend(backend_type: str, config: Dict[str, Any]) -> Optional[TerminalBackend]:
    """Obtener instancia de backend"""
    backend_class = _backends.get(backend_type)
    if backend_class:
        return backend_class(config)
    return None

def list_backends() -> List[str]:
    """Listar backends disponibles"""
    return list(_backends.keys())
