#!/usr/bin/env python3
"""
EIDOS Process Manager - Gestión de Procesos en Background
===========================================================

Permite a EIDOS ejecutar múltiples tareas en paralelo manteniendo control total:
- Ejecutar procesos en background
- Monitorear estado y recursos
- Límites configurables de procesos concurrentes
- Logging detallado estilo Claude (antes/después/cambios)
- Priorización automática
"""

import os
import sys
import time
import psutil
import logging
import threading
import subprocess
from pathlib import Path
from datetime import datetime
from typing import Dict, List, Optional, Callable, Any
from dataclasses import dataclass, field, asdict
from enum import Enum
from queue import Queue, PriorityQueue

logger = logging.getLogger(__name__)


class ProcessStatus(Enum):
    """Estados de un proceso"""
    QUEUED = "queued"           # En cola, esperando ejecución
    STARTING = "starting"       # Iniciando
    RUNNING = "running"         # Ejecutando
    PAUSED = "paused"          # Pausado
    COMPLETED = "completed"     # Completado exitosamente
    FAILED = "failed"          # Falló
    KILLED = "killed"          # Terminado forzosamente
    TIMEOUT = "timeout"        # Timeout


class ProcessPriority(Enum):
    """Prioridad de ejecución"""
    CRITICAL = 1    # Ejecutar AHORA (bloquea otros)
    HIGH = 2        # Alta prioridad
    MEDIUM = 3      # Prioridad media
    LOW = 4         # Baja prioridad
    BACKGROUND = 5  # Background sin límite de tiempo


@dataclass
class ProcessChange:
    """Registro de cambio en un proceso (estilo Claude)"""
    timestamp: str
    change_type: str  # "started", "output", "file_created", "file_modified", "completed", "failed"
    description: str
    before: Optional[str] = None
    after: Optional[str] = None
    file_path: Optional[str] = None
    line_number: Optional[int] = None
    link: Optional[str] = None  # Link clickeable a archivo:línea


@dataclass
class BackgroundProcess:
    """Proceso ejecutándose en background"""
    id: str
    name: str
    command: str
    priority: ProcessPriority
    status: ProcessStatus
    created_at: str
    started_at: Optional[str] = None
    completed_at: Optional[str] = None

    # Proceso y recursos
    pid: Optional[int] = None
    process: Optional[subprocess.Popen] = None
    cpu_percent: float = 0.0
    memory_mb: float = 0.0

    # Output y logging
    stdout: List[str] = field(default_factory=list)
    stderr: List[str] = field(default_factory=list)
    changes: List[ProcessChange] = field(default_factory=list)

    # Configuración
    timeout: Optional[int] = None  # segundos
    max_retries: int = 0
    retry_count: int = 0

    # Callbacks
    on_output: Optional[Callable[[str], None]] = None
    on_complete: Optional[Callable[[int], None]] = None
    on_error: Optional[Callable[[str], None]] = None

    # Metadata
    working_dir: Optional[str] = None
    env: Optional[Dict[str, str]] = None
    tags: List[str] = field(default_factory=list)

    def to_dict(self) -> Dict:
        """Convertir a dict (sin callbacks ni objetos complejos)"""
        data = asdict(self)
        data['priority'] = self.priority.name
        data['status'] = self.status.name
        del data['process']
        del data['on_output']
        del data['on_complete']
        del data['on_error']
        return data


class ProcessManager:
    """
    Gestor de procesos en background con límites y monitoreo

    Features:
    - Ejecuta múltiples procesos en paralelo con límite configurable
    - Monitorea CPU y memoria de cada proceso
    - Cola de prioridad para procesos pendientes
    - Logging detallado estilo Claude (antes/después)
    - Auto-retry en fallos
    - Timeout configurable
    - Callbacks para eventos
    """

    def __init__(self, max_concurrent: int = 5, max_cpu_percent: float = 80.0):
        """
        Args:
            max_concurrent: Máximo de procesos concurrentes
            max_cpu_percent: Si CPU total > esto, pausar nuevos procesos
        """
        self.max_concurrent = max_concurrent
        self.max_cpu_percent = max_cpu_percent

        # Procesos
        self.processes: Dict[str, BackgroundProcess] = {}
        self.process_queue: PriorityQueue = PriorityQueue()

        # Control
        self.running = False
        self.manager_thread: Optional[threading.Thread] = None
        self.monitor_thread: Optional[threading.Thread] = None

        # Stats
        self.total_started = 0
        self.total_completed = 0
        self.total_failed = 0

        logger.info(f"🎛️  Process Manager initialized (max_concurrent={max_concurrent})")

    def start(self):
        """Iniciar el gestor de procesos"""
        if self.running:
            logger.warning("Process Manager ya está corriendo")
            return

        self.running = True

        # Thread para gestionar cola de procesos
        self.manager_thread = threading.Thread(target=self._manage_processes, daemon=True)
        self.manager_thread.start()

        # Thread para monitorear recursos
        self.monitor_thread = threading.Thread(target=self._monitor_resources, daemon=True)
        self.monitor_thread.start()

        logger.info("✅ Process Manager started")

    def stop(self):
        """Detener el gestor (termina procesos activos)"""
        self.running = False

        # Terminar procesos activos
        for proc in self.processes.values():
            if proc.status == ProcessStatus.RUNNING:
                self.kill_process(proc.id)

        logger.info("🛑 Process Manager stopped")

    def submit(
        self,
        name: str,
        command: str,
        priority: ProcessPriority = ProcessPriority.MEDIUM,
        timeout: Optional[int] = None,
        working_dir: Optional[str] = None,
        on_output: Optional[Callable[[str], None]] = None,
        on_complete: Optional[Callable[[int], None]] = None,
        on_error: Optional[Callable[[str], None]] = None,
        tags: Optional[List[str]] = None
    ) -> str:
        """
        Submit proceso para ejecución

        Returns:
            process_id: ID único del proceso
        """
        process_id = f"proc_{int(time.time() * 1000)}_{len(self.processes)}"

        proc = BackgroundProcess(
            id=process_id,
            name=name,
            command=command,
            priority=priority,
            status=ProcessStatus.QUEUED,
            created_at=datetime.now().isoformat(),
            timeout=timeout,
            working_dir=working_dir,
            on_output=on_output,
            on_complete=on_complete,
            on_error=on_error,
            tags=tags or []
        )

        self.processes[process_id] = proc

        # Añadir a cola de prioridad
        self.process_queue.put((priority.value, time.time(), process_id))

        # Log estilo Claude
        change = ProcessChange(
            timestamp=datetime.now().isoformat(),
            change_type="queued",
            description=f"Proceso '{name}' añadido a cola",
            before=f"Estado: ninguno",
            after=f"Estado: {ProcessStatus.QUEUED.value}",
            link=None
        )
        proc.changes.append(change)

        logger.info(f"📝 Proceso añadido: {name} (prioridad={priority.name}, id={process_id})")

        return process_id

    def _manage_processes(self):
        """Loop principal para gestionar procesos"""
        while self.running:
            try:
                # Contar procesos activos
                active_count = sum(
                    1 for p in self.processes.values()
                    if p.status in [ProcessStatus.RUNNING, ProcessStatus.STARTING]
                )

                # Verificar si podemos iniciar más procesos
                if active_count < self.max_concurrent and not self.process_queue.empty():
                    # Verificar CPU
                    cpu_percent = psutil.cpu_percent(interval=0.1)
                    if cpu_percent > self.max_cpu_percent:
                        logger.debug(f"⏸️  CPU alta ({cpu_percent}%), esperando...")
                        time.sleep(2)
                        continue

                    # Obtener siguiente proceso de cola
                    priority, queued_time, process_id = self.process_queue.get_nowait()

                    if process_id in self.processes:
                        proc = self.processes[process_id]
                        if proc.status == ProcessStatus.QUEUED:
                            self._start_process(proc)

                # Check timeouts
                self._check_timeouts()

                time.sleep(0.5)

            except Exception as e:
                logger.error(f"Error in process manager loop: {e}")
                time.sleep(1)

    def _start_process(self, proc: BackgroundProcess):
        """Iniciar proceso en background"""
        proc.status = ProcessStatus.STARTING
        proc.started_at = datetime.now().isoformat()

        logger.info(f"🚀 Iniciando proceso: {proc.name}")

        try:
            # Preparar environment
            env = os.environ.copy()
            if proc.env:
                env.update(proc.env)

            # Iniciar proceso
            process = subprocess.Popen(
                proc.command,
                shell=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                cwd=proc.working_dir,
                env=env,
                text=True,
                bufsize=1
            )

            proc.process = process
            proc.pid = process.pid
            proc.status = ProcessStatus.RUNNING

            self.total_started += 1

            # Log cambio
            change = ProcessChange(
                timestamp=datetime.now().isoformat(),
                change_type="started",
                description=f"Proceso iniciado con PID {process.pid}",
                before=f"Estado: {ProcessStatus.QUEUED.value}",
                after=f"Estado: {ProcessStatus.RUNNING.value}, PID: {process.pid}"
            )
            proc.changes.append(change)

            # Thread para leer output
            threading.Thread(
                target=self._read_output,
                args=(proc,),
                daemon=True
            ).start()

            logger.info(f"✅ Proceso '{proc.name}' iniciado (PID={process.pid})")

        except Exception as e:
            proc.status = ProcessStatus.FAILED
            error_msg = str(e)
            proc.stderr.append(error_msg)

            if proc.on_error:
                proc.on_error(error_msg)

            logger.error(f"❌ Error iniciando '{proc.name}': {e}")

            # Log error
            change = ProcessChange(
                timestamp=datetime.now().isoformat(),
                change_type="failed",
                description=f"Error al iniciar proceso",
                before=f"Estado: {ProcessStatus.STARTING.value}",
                after=f"Estado: {ProcessStatus.FAILED.value}, Error: {error_msg}"
            )
            proc.changes.append(change)

    def _read_output(self, proc: BackgroundProcess):
        """Leer output del proceso"""
        if not proc.process:
            return

        try:
            # Leer stdout
            for line in proc.process.stdout:
                line = line.rstrip()
                proc.stdout.append(line)

                # Callback
                if proc.on_output:
                    proc.on_output(line)

                # Log cambio si detecta archivo modificado
                if "✅" in line or "Created" in line or "Modified" in line:
                    change = ProcessChange(
                        timestamp=datetime.now().isoformat(),
                        change_type="output",
                        description=line,
                        after=line
                    )
                    proc.changes.append(change)

            # Esperar a que termine
            returncode = proc.process.wait()

            # Leer stderr
            stderr_output = proc.process.stderr.read()
            if stderr_output:
                proc.stderr.append(stderr_output)

            # Actualizar estado
            proc.completed_at = datetime.now().isoformat()

            if returncode == 0:
                proc.status = ProcessStatus.COMPLETED
                self.total_completed += 1
                logger.info(f"✅ Proceso '{proc.name}' completado")

                if proc.on_complete:
                    proc.on_complete(returncode)
            else:
                proc.status = ProcessStatus.FAILED
                self.total_failed += 1
                logger.error(f"❌ Proceso '{proc.name}' falló (code={returncode})")

                if proc.on_error:
                    proc.on_error(f"Exit code: {returncode}")

            # Log final
            change = ProcessChange(
                timestamp=datetime.now().isoformat(),
                change_type="completed" if returncode == 0 else "failed",
                description=f"Proceso terminado con código {returncode}",
                before=f"Estado: {ProcessStatus.RUNNING.value}",
                after=f"Estado: {proc.status.value}"
            )
            proc.changes.append(change)

        except Exception as e:
            logger.error(f"Error leyendo output de '{proc.name}': {e}")

    def _monitor_resources(self):
        """Monitorear recursos de procesos activos"""
        while self.running:
            try:
                for proc in self.processes.values():
                    if proc.pid and proc.status == ProcessStatus.RUNNING:
                        try:
                            p = psutil.Process(proc.pid)
                            proc.cpu_percent = p.cpu_percent(interval=0.1)
                            proc.memory_mb = p.memory_info().rss / 1024 / 1024
                        except psutil.NoSuchProcess:
                            pass

                time.sleep(5)
            except Exception as e:
                logger.error(f"Error monitoring resources: {e}")
                time.sleep(5)

    def _check_timeouts(self):
        """Verificar timeouts"""
        now = time.time()

        for proc in self.processes.values():
            if proc.status == ProcessStatus.RUNNING and proc.timeout:
                if proc.started_at:
                    started = datetime.fromisoformat(proc.started_at).timestamp()
                    elapsed = now - started

                    if elapsed > proc.timeout:
                        logger.warning(f"⏱️  Timeout para proceso '{proc.name}' ({elapsed:.1f}s > {proc.timeout}s)")
                        self.kill_process(proc.id, reason="timeout")

    def kill_process(self, process_id: str, reason: str = "manual"):
        """Terminar proceso forzosamente"""
        if process_id not in self.processes:
            return False

        proc = self.processes[process_id]

        if proc.process and proc.status == ProcessStatus.RUNNING:
            try:
                proc.process.terminate()
                proc.process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                proc.process.kill()

            proc.status = ProcessStatus.KILLED if reason == "manual" else ProcessStatus.TIMEOUT
            proc.completed_at = datetime.now().isoformat()

            # Log
            change = ProcessChange(
                timestamp=datetime.now().isoformat(),
                change_type="killed",
                description=f"Proceso terminado ({reason})",
                before=f"Estado: {ProcessStatus.RUNNING.value}",
                after=f"Estado: {proc.status.value}"
            )
            proc.changes.append(change)

            logger.info(f"🛑 Proceso '{proc.name}' terminado ({reason})")
            return True

        return False

    def get_process(self, process_id: str) -> Optional[BackgroundProcess]:
        """Obtener info de proceso"""
        return self.processes.get(process_id)

    def list_processes(
        self,
        status: Optional[ProcessStatus] = None,
        tags: Optional[List[str]] = None
    ) -> List[BackgroundProcess]:
        """Listar procesos con filtros opcionales"""
        result = list(self.processes.values())

        if status:
            result = [p for p in result if p.status == status]

        if tags:
            result = [p for p in result if any(tag in p.tags for tag in tags)]

        return result

    def get_stats(self) -> Dict[str, Any]:
        """Obtener estadísticas"""
        active_count = sum(
            1 for p in self.processes.values()
            if p.status in [ProcessStatus.RUNNING, ProcessStatus.STARTING]
        )

        queued_count = sum(
            1 for p in self.processes.values()
            if p.status == ProcessStatus.QUEUED
        )

        total_cpu = sum(p.cpu_percent for p in self.processes.values() if p.status == ProcessStatus.RUNNING)
        total_memory = sum(p.memory_mb for p in self.processes.values() if p.status == ProcessStatus.RUNNING)

        return {
            "total_processes": len(self.processes),
            "active": active_count,
            "queued": queued_count,
            "completed": self.total_completed,
            "failed": self.total_failed,
            "max_concurrent": self.max_concurrent,
            "cpu_usage_total": round(total_cpu, 2),
            "memory_mb_total": round(total_memory, 2),
            "system_cpu_percent": psutil.cpu_percent(interval=0.1),
            "system_memory_percent": psutil.virtual_memory().percent
        }


# Singleton global
_process_manager: Optional[ProcessManager] = None


def get_process_manager(max_concurrent: int = 5) -> ProcessManager:
    """Obtener instancia global del Process Manager"""
    global _process_manager

    if _process_manager is None:
        _process_manager = ProcessManager(max_concurrent=max_concurrent)
        _process_manager.start()

    return _process_manager


if __name__ == "__main__":
    # Test
    logging.basicConfig(level=logging.INFO)

    pm = get_process_manager(max_concurrent=3)

    # Submit varios procesos
    pm.submit("Test 1", "sleep 5 && echo 'Test 1 done'", ProcessPriority.HIGH)
    pm.submit("Test 2", "sleep 3 && echo 'Test 2 done'", ProcessPriority.MEDIUM)
    pm.submit("Test 3", "sleep 2 && echo 'Test 3 done'", ProcessPriority.LOW)
    pm.submit("Test 4", "sleep 1 && echo 'Test 4 done'", ProcessPriority.HIGH)

    # Monitor
    time.sleep(10)

    stats = pm.get_stats()
    print(f"\n📊 Stats: {stats}")

    # Listar procesos
    for proc in pm.list_processes():
        print(f"  - {proc.name}: {proc.status.value}")
