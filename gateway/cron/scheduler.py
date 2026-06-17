"""
Cron Scheduler para EIDOS Gateway
Adaptado de Hermes - Ejecuta tareas programadas
"""

import json
import logging
import os
import threading
import time
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional, Any, Callable

try:
    import fcntl
    HAS_FCNTL = True
except ImportError:
    fcntl = None
    HAS_FCNTL = False

logger = logging.getLogger(__name__)

class CronJob:
    """Representa un trabajo programado"""
    
    def __init__(
        self,
        job_id: str,
        name: str,
        prompt: str,
        schedule: str,  # cron format: "*/5 * * * *"
        enabled: bool = True,
        deliver: str = "local",
        last_run: Optional[datetime] = None,
        next_run: Optional[datetime] = None,
        metadata: Optional[Dict] = None
    ):
        self.job_id = job_id
        self.name = name
        self.prompt = prompt
        self.schedule = schedule
        self.enabled = enabled
        self.deliver = deliver
        self.last_run = last_run
        self.next_run = next_run
        self.metadata = metadata or {}
        self._executor: Optional[Callable] = None
        
    def to_dict(self) -> Dict:
        return {
            "job_id": self.job_id,
            "name": self.name,
            "prompt": self.prompt,
            "schedule": self.schedule,
            "enabled": self.enabled,
            "deliver": self.deliver,
            "last_run": self.last_run.isoformat() if self.last_run else None,
            "next_run": self.next_run.isoformat() if self.next_run else None,
            "metadata": self.metadata,
        }
    
    @classmethod
    def from_dict(cls, data: Dict) -> "CronJob":
        return cls(
            job_id=data["job_id"],
            name=data.get("name", "Unnamed"),
            prompt=data.get("prompt", ""),
            schedule=data.get("schedule", ""),
            enabled=data.get("enabled", True),
            deliver=data.get("deliver", "local"),
            last_run=datetime.fromisoformat(data["last_run"]) if data.get("last_run") else None,
            next_run=datetime.fromisoformat(data["next_run"]) if data.get("next_run") else None,
            metadata=data.get("metadata", {}),
        )
    
    def is_due(self) -> bool:
        """Verificar si el job debe ejecutarse ahora"""
        if not self.enabled or not self.next_run:
            return False
        return datetime.now() >= self.next_run
    
    def calculate_next_run(self) -> Optional[datetime]:
        """Calcular siguiente ejecución basado en cron schedule"""
        try:
            # Simple: solo soporta */N format por ahora
            parts = self.schedule.split()
            if len(parts) == 5:
                minute_part = parts[0]
                if minute_part.startswith("*/"):
                    interval = int(minute_part[2:])
                    now = datetime.now()
                    next_min = ((now.minute // interval + 1) * interval) % 60
                    if next_min <= now.minute:
                        return now.replace(minute=next_min, second=0, microsecond=0) + __import__('datetime').timedelta(hours=1)
                    return now.replace(minute=next_min, second=0, microsecond=0)
            # Default: cada hora
            return datetime.now() + __import__('datetime').timedelta(hours=1)
        except Exception as e:
            logger.error(f"Error calculating next run for {self.name}: {e}")
            return None

class CronScheduler:
    """Scheduler de trabajos cron para EIDOS"""
    
    def __init__(self, jobs_dir: Optional[Path] = None):
        self.jobs_dir = jobs_dir or Path.home() / ".eidos" / "cron"
        self.jobs_dir.mkdir(parents=True, exist_ok=True)
        self.jobs_file = self.jobs_dir / "jobs.json"
        self.jobs: Dict[str, CronJob] = {}
        self._lock = threading.Lock()
        self._running = False
        self._thread: Optional[threading.Thread] = None
        self._job_executor: Optional[Callable[[CronJob], Any]] = None
        
        self._load_jobs()
    
    def _load_jobs(self):
        """Cargar jobs desde disco"""
        if self.jobs_file.exists():
            try:
                data = json.loads(self.jobs_file.read_text(encoding="utf-8"))
                for job_data in data.get("jobs", []):
                    job = CronJob.from_dict(job_data)
                    self.jobs[job.job_id] = job
                logger.info(f"Loaded {len(self.jobs)} cron jobs")
            except Exception as e:
                logger.error(f"Error loading jobs: {e}")
    
    def _save_jobs(self):
        """Guardar jobs a disco"""
        try:
            data = {
                "updated_at": datetime.now().isoformat(),
                "jobs": [job.to_dict() for job in self.jobs.values()]
            }
            self.jobs_file.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
        except Exception as e:
            logger.error(f"Error saving jobs: {e}")
    
    def add_job(
        self,
        name: str,
        prompt: str,
        schedule: str,
        deliver: str = "local",
        job_id: Optional[str] = None
    ) -> CronJob:
        """Añadir nuevo job cron"""
        job_id = job_id or f"job_{int(time.time())}_{len(self.jobs)}"
        
        job = CronJob(
            job_id=job_id,
            name=name,
            prompt=prompt,
            schedule=schedule,
            deliver=deliver,
        )
        job.next_run = job.calculate_next_run()
        
        with self._lock:
            self.jobs[job_id] = job
            self._save_jobs()
        
        logger.info(f"Added cron job: {name} ({schedule})")
        return job
    
    def remove_job(self, job_id: str) -> bool:
        """Eliminar job cron"""
        with self._lock:
            if job_id in self.jobs:
                del self.jobs[job_id]
                self._save_jobs()
                return True
        return False
    
    def list_jobs(self) -> List[CronJob]:
        """Listar todos los jobs"""
        return list(self.jobs.values())
    
    def set_executor(self, executor: Callable[[CronJob], Any]):
        """Registrar función para ejecutar jobs"""
        self._job_executor = executor
    
    def _run_job(self, job: CronJob):
        """Ejecutar un job"""
        logger.info(f"Executing job: {job.name}")
        
        try:
            if self._job_executor:
                result = self._job_executor(job)
                logger.info(f"Job {job.name} completed: {result}")
            else:
                logger.warning(f"No executor for job: {job.name}")
                
        except Exception as e:
            logger.error(f"Job {job.name} failed: {e}")
        
        # Actualizar timestamps
        job.last_run = datetime.now()
        job.next_run = job.calculate_next_run()
        self._save_jobs()
    
    def tick(self):
        """Verificar y ejecutar jobs pendientes (llamar cada 60s)"""
        with self._lock:
            for job in self.jobs.values():
                if job.is_due():
                    self._run_job(job)
    
    def start(self, interval: int = 60):
        """Iniciar scheduler en background thread"""
        if self._running:
            return
        
        self._running = True
        
        def scheduler_loop():
            while self._running:
                try:
                    self.tick()
                except Exception as e:
                    logger.error(f"Scheduler tick error: {e}")
                time.sleep(interval)
        
        self._thread = threading.Thread(target=scheduler_loop, daemon=True)
        self._thread.start()
        logger.info(f"Cron scheduler started (interval={interval}s)")
    
    def stop(self):
        """Detener scheduler"""
        self._running = False
        if self._thread:
            self._thread.join(timeout=5)
        logger.info("Cron scheduler stopped")

# Singleton
_scheduler: Optional[CronScheduler] = None

def get_scheduler() -> CronScheduler:
    global _scheduler
    if _scheduler is None:
        _scheduler = CronScheduler()
    return _scheduler
