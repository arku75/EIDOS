"""
EIDOS core/shell_background.py — Shell Background System
===========================================================
Sistema de gestión de sesiones de terminal persistentes.
Permite a EIDOS "habitar" terminales en background, ejecutar comandos
long-running, y mantener sesiones entre reinicios.

Responsabilidades:
- Abrir terminales/pty en background (tmux/screen/pty)
- Escribir comandos en shells abiertas
- Leer output de shells en tiempo real
- Mantener sesiones persistentes entre reinicios de EIDOS
- Interactuar con programas interactivos

Uso:
    from core.shell_background import ShellManager
    
    manager = ShellManager()
    
    # Crear sesión
    session = manager.open_session(
        name="eidos_main",
        backend="tmux",  # o "screen", "pty"
        cwd="/home/ser/EIDOS"
    )
    
    # Escribir comando
    manager.write(session.id, "python -c 'print(\"Hello from EIDOS\")'")
    
    # Leer output
    output = manager.read(session.id, timeout=5)
    print(output)
    
    # Cerrar sesión
    manager.close_session(session.id)

Backends soportados:
    - tmux: Sesiones persistentes, recomendado
    - screen: Alternativa legacy
    - pty: Pseudo-terminal puro (no persistente)
    - subprocess: Simple subprocess (sin TTY real)

Autor: EIDOS Autonomy System
Versión: 1.0.0
"""
from __future__ import annotations

import logging
import os
import pty
import select
import subprocess
import threading
import time
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from pathlib import Path
from typing import Dict, List, Optional, Literal, Any, IO
from uuid import uuid4

# Configuración de logging estructurado
log = logging.getLogger("eidos.shell_background")


# ═══════════════════════════════════════════════════════════════════════════════
#  ENUMS & DATA CLASSES
# ═══════════════════════════════════════════════════════════════════════════════

class SessionStatus(Enum):
    """Estados posibles de una sesión de shell."""
    RUNNING = "running"
    STOPPED = "stopped"
    ERROR = "error"
    DETACHED = "detached"


class BackendType(Enum):
    """Tipos de backend soportados."""
    TMUX = "tmux"
    SCREEN = "screen"
    PTY = "pty"
    SUBPROCESS = "subprocess"


@dataclass
class CommandRecord:
    """Registro de un comando ejecutado."""
    command: str
    timestamp: datetime
    output: str = ""
    exit_code: Optional[int] = None
    duration_ms: int = 0
    error: Optional[str] = None


@dataclass
class ShellSession:
    """
    Sesión de shell persistente.
    
    Representa una terminal que puede estar corriendo en background,
    independiente del proceso de EIDOS.
    """
    id: str
    name: str
    backend: str  # Literal["tmux", "screen", "pty", "subprocess"]
    created_at: datetime
    last_activity: datetime
    status: str  # Literal["running", "stopped", "error", "detached"]
    cwd: Path
    env: Dict[str, str]
    history: List[CommandRecord] = field(default_factory=list)
    
    # Backend-specific
    session_name: Optional[str] = None  # Nombre en tmux/screen
    pid: Optional[int] = None  # PID del proceso
    
    # Metadata
    metadata: Dict[str, Any] = field(default_factory=dict)
    
    def touch(self):
        """Actualiza timestamp de última actividad."""
        self.last_activity = datetime.now()
    
    def add_command(self, record: CommandRecord):
        """Añade registro de comando al historial."""
        self.history.append(record)
        # Limitar historial
        if len(self.history) > 1000:
            self.history = self.history[-1000:]
        self.touch()


# ═══════════════════════════════════════════════════════════════════════════════
#  BACKEND INTERFACES
# ═══════════════════════════════════════════════════════════════════════════════

class ShellBackend:
    """Interface base para backends de shell."""
    
    def __init__(self, session: ShellSession):
        self.session = session
    
    def is_available(self) -> bool:
        """Verifica si este backend está disponible en el sistema."""
        raise NotImplementedError
    
    def create(self) -> bool:
        """Crea la sesión en el backend."""
        raise NotImplementedError
    
    def write(self, command: str) -> bool:
        """Escribe comando en la sesión."""
        raise NotImplementedError
    
    def read(self, timeout: int = 5) -> str:
        """Lee output de la sesión."""
        raise NotImplementedError
    
    def close(self) -> bool:
        """Cierra la sesión."""
        raise NotImplementedError
    
    def is_alive(self) -> bool:
        """Verifica si la sesión sigue viva."""
        raise NotImplementedError


class TmuxBackend(ShellBackend):
    """Backend usando tmux para sesiones persistentes."""
    
    def is_available(self) -> bool:
        try:
            subprocess.run(
                ["tmux", "-V"],
                capture_output=True,
                check=True
            )
            return True
        except (subprocess.CalledProcessError, FileNotFoundError):
            return False
    
    def create(self) -> bool:
        try:
            session_name = f"eidos_{self.session.id[:8]}"
            self.session.session_name = session_name
            
            cmd = [
                "tmux", "new-session",
                "-d",  # Detached
                "-s", session_name,
                "-c", str(self.session.cwd)
            ]
            
            # Añadir variables de entorno
            env = os.environ.copy()
            env.update(self.session.env)
            
            result = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                env=env
            )
            
            if result.returncode == 0:
                log.info(f"Tmux session creada: {session_name}")
                return True
            else:
                log.error(f"Error creando tmux session: {result.stderr}")
                return False
                
        except Exception as e:
            log.error(f"Excepción creando tmux session: {e}")
            return False
    
    def write(self, command: str) -> bool:
        try:
            if not self.session.session_name:
                return False
            
            # Escape comillas
            safe_command = command.replace('"', '\\"')
            
            cmd = [
                "tmux", "send-keys",
                "-t", self.session.session_name,
                safe_command,
                "Enter"
            ]
            
            result = subprocess.run(cmd, capture_output=True, text=True)
            
            if result.returncode == 0:
                self.session.touch()
                return True
            else:
                log.error(f"Error escribiendo a tmux: {result.stderr}")
                return False
                
        except Exception as e:
            log.error(f"Excepción escribiendo a tmux: {e}")
            return False
    
    def read(self, timeout: int = 5) -> str:
        try:
            if not self.session.session_name:
                return ""
            
            cmd = [
                "tmux", "capture-pane",
                "-t", self.session.session_name,
                "-p"  # Print to stdout
            ]
            
            result = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                timeout=timeout
            )
            
            if result.returncode == 0:
                self.session.touch()
                return result.stdout
            else:
                return ""
                
        except subprocess.TimeoutExpired:
            log.warning(f"Timeout leyendo de tmux session {self.session.session_name}")
            return ""
        except Exception as e:
            log.error(f"Excepción leyendo de tmux: {e}")
            return ""
    
    def close(self) -> bool:
        try:
            if not self.session.session_name:
                return True
            
            cmd = ["tmux", "kill-session", "-t", self.session.session_name]
            result = subprocess.run(cmd, capture_output=True, text=True)
            
            if result.returncode == 0 or "no server running" in result.stderr:
                log.info(f"Tmux session cerrada: {self.session.session_name}")
                return True
            else:
                log.error(f"Error cerrando tmux: {result.stderr}")
                return False
                
        except Exception as e:
            log.error(f"Excepción cerrando tmux: {e}")
            return False
    
    def is_alive(self) -> bool:
        try:
            if not self.session.session_name:
                return False
            
            cmd = ["tmux", "has-session", "-t", self.session.session_name]
            result = subprocess.run(cmd, capture_output=True)
            
            return result.returncode == 0
            
        except Exception:
            return False


class PtyBackend(ShellBackend):
    """Backend usando pseudo-terminal puro (no persistente)."""
    
    def __init__(self, session: ShellSession):
        super().__init__(session)
        self.master_fd: Optional[int] = None
        self.slave_fd: Optional[int] = None
        self.process: Optional[subprocess.Popen] = None
    
    def is_available(self) -> bool:
        return True  # Siempre disponible en Unix
    
    def create(self) -> bool:
        try:
            # Crear pseudo-terminal
            master, slave = pty.openpty()
            self.master_fd = master
            self.slave_fd = slave
            
            # Iniciar shell
            env = os.environ.copy()
            env.update(self.session.env)
            env["TERM"] = "xterm-256color"
            
            self.process = subprocess.Popen(
                ["/bin/bash", "-i"],
                stdin=slave,
                stdout=slave,
                stderr=slave,
                cwd=self.session.cwd,
                env=env,
                start_new_session=True
            )
            
            self.session.pid = self.process.pid
            
            # Cerrar slave en proceso padre
            os.close(slave)
            
            log.info(f"PTY session creada, PID: {self.process.pid}")
            return True
            
        except Exception as e:
            log.error(f"Error creando PTY: {e}")
            return False
    
    def write(self, command: str) -> bool:
        try:
            if self.master_fd is None:
                return False
            
            # Añadir newline
            if not command.endswith("\n"):
                command += "\n"
            
            os.write(self.master_fd, command.encode())
            self.session.touch()
            return True
            
        except Exception as e:
            log.error(f"Error escribiendo a PTY: {e}")
            return False
    
    def read(self, timeout: int = 5) -> str:
        try:
            if self.master_fd is None:
                return ""
            
            output = b""
            start_time = time.time()
            
            while time.time() - start_time < timeout:
                ready, _, _ = select.select([self.master_fd], [], [], 0.1)
                
                if ready:
                    try:
                        chunk = os.read(self.master_fd, 4096)
                        if chunk:
                            output += chunk
                        else:
                            break
                    except OSError:
                        break
                
                # Pequeña pausa para no saturar CPU
                time.sleep(0.01)
            
            self.session.touch()
            return output.decode("utf-8", errors="replace")
            
        except Exception as e:
            log.error(f"Error leyendo de PTY: {e}")
            return ""
    
    def close(self) -> bool:
        try:
            if self.process:
                self.process.terminate()
                try:
                    self.process.wait(timeout=2)
                except subprocess.TimeoutExpired:
                    self.process.kill()
                    self.process.wait()
            
            if self.master_fd:
                os.close(self.master_fd)
            
            log.info(f"PTY session cerrada")
            return True
            
        except Exception as e:
            log.error(f"Error cerrando PTY: {e}")
            return False
    
    def is_alive(self) -> bool:
        if self.process is None:
            return False
        return self.process.poll() is None


# ═══════════════════════════════════════════════════════════════════════════════
#  SHELL MANAGER
# ═══════════════════════════════════════════════════════════════════════════════

class ShellManager:
    """
    Gestor de sesiones de shell background.
    
    Thread-safe singleton para manejar múltiples sesiones.
    """
    
    _instance: Optional["ShellManager"] = None
    _lock = threading.Lock()
    
    def __new__(cls) -> "ShellManager":
        if cls._instance is None:
            with cls._lock:
                if cls._instance is None:
                    cls._instance = super().__new__(cls)
        return cls._instance
    
    def __init__(self):
        if hasattr(self, "_initialized"):
            return
        
        self._sessions: Dict[str, ShellSession] = {}
        self._backends: Dict[str, ShellBackend] = {}
        self._lock = threading.RLock()
        self._initialized = True
        
        # Detectar backend preferido
        self._preferred_backend = self._detect_preferred_backend()
        
        log.info(f"ShellManager inicializado, backend preferido: {self._preferred_backend}")
    
    def _detect_preferred_backend(self) -> str:
        """Detecta el mejor backend disponible."""
        # Probar tmux primero
        try:
            subprocess.run(["tmux", "-V"], capture_output=True, check=True)
            return "tmux"
        except Exception:
            pass  # error no crítico, continuar
        # Fallback a pty
        return "pty"
    
    def open_session(
        self,
        name: str,
        backend: Optional[str] = None,
        cwd: Optional[Path] = None,
        env: Optional[Dict[str, str]] = None,
        metadata: Optional[Dict[str, Any]] = None
    ) -> ShellSession:
        """
        Abre una nueva sesión de shell.
        
        Args:
            name: Nombre descriptivo de la sesión
            backend: Backend a usar (tmux, screen, pty, subprocess)
            cwd: Directorio de trabajo
            env: Variables de entorno adicionales
            metadata: Metadata adicional
            
        Returns:
            ShellSession creada
            
        Raises:
            RuntimeError: Si no se pudo crear la sesión
        """
        with self._lock:
            # Usar backend preferido si no se especifica
            backend = backend or self._preferred_backend
            
            # Verificar si ya existe sesión con este nombre
            for session in self._sessions.values():
                if session.name == name:
                    log.warning(f"Sesión '{name}' ya existe, retornando existente")
                    return session
            
            # Crear sesión
            session_id = f"shell_{uuid4().hex[:12]}"
            now = datetime.now()
            
            session = ShellSession(
                id=session_id,
                name=name,
                backend=backend,
                created_at=now,
                last_activity=now,
                status="running",
                cwd=cwd or Path.home(),
                env=env or {},
                metadata=metadata or {}
            )
            
            # Crear backend
            if backend == "tmux":
                backend_impl = TmuxBackend(session)
            elif backend == "pty":
                backend_impl = PtyBackend(session)
            else:
                raise ValueError(f"Backend no soportado: {backend}")
            
            # Verificar disponibilidad
            if not backend_impl.is_available():
                raise RuntimeError(f"Backend '{backend}' no disponible")
            
            # Crear sesión en backend
            if not backend_impl.create():
                raise RuntimeError(f"No se pudo crear sesión con backend {backend}")
            
            # Guardar
            self._sessions[session_id] = session
            self._backends[session_id] = backend_impl
            
            log.info(f"Sesión creada: {name} (ID: {session_id}, Backend: {backend})")
            return session
    
    def write(self, session_id: str, command: str) -> bool:
        """
        Escribe un comando en una sesión.
        
        Args:
            session_id: ID de la sesión
            command: Comando a ejecutar
            
        Returns:
            True si se escribió exitosamente
        """
        # CAPA 2: Validación Constitution - última línea de defensa
        # Esto captura bypasses que llamen directamente a ShellManager sin pasar por execute_command()
        try:
            from core.constitution import check_command, ConstitutionViolation
            if not check_command(command):
                log.critical(f"🚫 ShellManager BLOQUEO comando por Constitution: {command[:80]}")
                return False
        except ConstitutionViolation:
            # Fail-closed: si Constitution no está disponible, bloquear
            log.critical("🚫 Constitution no disponible - comando bloqueado por seguridad")
            return False
        except Exception as e:
            # Fail-closed: cualquier error en validación = bloquear
            log.critical(f"🚫 Error validando contra Constitution: {e} - comando bloqueado")
            return False
        
        with self._lock:
            if session_id not in self._sessions:
                log.error(f"Sesión no encontrada: {session_id}")
                return False
            
            session = self._sessions[session_id]
            backend = self._backends[session_id]
            
            if not backend.is_alive():
                log.error(f"Sesión {session_id} no está viva")
                session.status = "error"
                return False
            
            # Escribir comando
            if backend.write(command):
                # Registrar en historial
                record = CommandRecord(
                    command=command,
                    timestamp=datetime.now()
                )
                session.add_command(record)
                return True
            
            return False
    
    def read(self, session_id: str, timeout: int = 5) -> str:
        """
        Lee output de una sesión.
        
        Args:
            session_id: ID de la sesión
            timeout: Timeout en segundos
            
        Returns:
            Output leído
        """
        with self._lock:
            if session_id not in self._sessions:
                return ""
            
            backend = self._backends[session_id]
            return backend.read(timeout)
    
    def execute(
        self,
        session_id: str,
        command: str,
        timeout: int = 5,
        wait_for_output: bool = True
    ) -> tuple[bool, str]:
        """
        Ejecuta comando y opcionalmente espera output.
        
        Args:
            session_id: ID de la sesión
            command: Comando a ejecutar
            timeout: Timeout para leer output
            wait_for_output: Si True, espera y retorna output
            
        Returns:
            (success, output)
        """
        success = self.write(session_id, command)
        
        if not success:
            return False, ""
        
        if wait_for_output:
            # Pequeña pausa para que el comando se ejecute
            time.sleep(0.5)
            output = self.read(session_id, timeout)
            return True, output
        
        return True, ""
    
    def close_session(self, session_id: str) -> bool:
        """
        Cierra una sesión.
        
        Args:
            session_id: ID de la sesión
            
        Returns:
            True si se cerró exitosamente
        """
        with self._lock:
            if session_id not in self._sessions:
                return False
            
            session = self._sessions[session_id]
            backend = self._backends[session_id]
            
            # Cerrar backend
            backend.close()
            
            # Actualizar estado
            session.status = "stopped"
            
            # Limpiar
            del self._sessions[session_id]
            del self._backends[session_id]
            
            log.info(f"Sesión cerrada: {session_id}")
            return True
    
    def list_sessions(self) -> List[ShellSession]:
        """
        Lista todas las sesiones activas.
        
        Returns:
            Lista de sesiones
        """
        with self._lock:
            # Actualizar estado de cada sesión
            for session_id, session in self._sessions.items():
                backend = self._backends[session_id]
                if not backend.is_alive():
                    session.status = "error"
            
            return list(self._sessions.values())
    
    def get_session(self, session_id: str) -> Optional[ShellSession]:
        """
        Obtiene una sesión por ID.
        
        Args:
            session_id: ID de la sesión
            
        Returns:
            ShellSession o None
        """
        with self._lock:
            return self._sessions.get(session_id)
    
    def get_session_by_name(self, name: str) -> Optional[ShellSession]:
        """
        Busca sesión por nombre.
        
        Args:
            name: Nombre de la sesión
            
        Returns:
            ShellSession o None
        """
        with self._lock:
            for session in self._sessions.values():
                if session.name == name:
                    return session
            return None
    
    def close_all(self):
        """Cierra todas las sesiones."""
        with self._lock:
            for session_id in list(self._sessions.keys()):
                self.close_session(session_id)
    
    def get_stats(self) -> Dict[str, Any]:
        """Obtiene estadísticas del manager."""
        with self._lock:
            sessions = self.list_sessions()
            
            return {
                "total_sessions": len(sessions),
                "running": len([s for s in sessions if s.status == "running"]),
                "error": len([s for s in sessions if s.status == "error"]),
                "by_backend": {
                    backend: len([s for s in sessions if s.backend == backend])
                    for backend in set(s.backend for s in sessions)
                }
            }


# ═══════════════════════════════════════════════════════════════════════════════
#  SINGLETON & UTILIDADES
# ═══════════════════════════════════════════════════════════════════════════════

_manager: Optional[ShellManager] = None


def get_shell_manager() -> ShellManager:
    """Obtiene instancia singleton de ShellManager."""
    global _manager
    if _manager is None:
        _manager = ShellManager()
    return _manager


def reset_shell_manager():
    """Resetea singleton (útil para tests)."""
    global _manager
    if _manager:
        _manager.close_all()
    _manager = None


# ═══════════════════════════════════════════════════════════════════════════════
#  MAIN (tests rápidos)
# ═══════════════════════════════════════════════════════════════════════════════

if __name__ == "__main__":
    # Configurar logging
    logging.basicConfig(
        level=logging.INFO,
        format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
    )
    
    print("ShellManager - Test de inicialización")
    print("=" * 50)
    
    try:
        manager = ShellManager()
        print(f"✅ ShellManager inicializado")
        print(f"   Backend preferido: {manager._preferred_backend}")
        
        # Test crear sesión
        print("\n🧪 Test creando sesión...")
        session = manager.open_session(
            name="test_session",
            cwd=Path.home()
        )
        print(f"✅ Sesión creada: {session.name} (ID: {session.id})")
        print(f"   Backend: {session.backend}")
        
        # Test escribir comando
        print("\n🧪 Test ejecutando comando...")
        success, output = manager.execute(
            session.id,
            "echo 'Hello from EIDOS Shell Background System'",
            timeout=3
        )
        
        if success:
            print(f"✅ Comando ejecutado")
            print(f"   Output: {output[:100]}...")
        else:
            print("❌ Falló ejecución")
        
        # Test listar sesiones
        print("\n🧪 Test listando sesiones...")
        sessions = manager.list_sessions()
        print(f"   Sesiones activas: {len(sessions)}")
        
        for s in sessions:
            print(f"   - {s.name}: {s.status} ({s.backend})")
        
        # Test cerrar sesión
        print("\n🧪 Test cerrando sesión...")
        if manager.close_session(session.id):
            print("✅ Sesión cerrada exitosamente")
        else:
            print("❌ Error cerrando sesión")
        
        # Stats finales
        print("\n📊 Estadísticas finales:")
        stats = manager.get_stats()
        print(f"   {stats}")
        
        print("\n✅ Tests completados")
        
    except Exception as e:
        print(f"❌ Error: {e}")
        import traceback
        traceback.print_exc()
