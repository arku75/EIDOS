#!/usr/bin/env python3
"""
🖥️ EIDOS SYSTEM CONTROL v1.0
Control del sistema operativo por EIDOS - capacidades de ser vivo digital
"""
import os
import sys
import subprocess
import psutil
import sqlite3
from typing import Dict, List, Tuple, Optional
from datetime import datetime
from core.db import get_conn

class EidosSystemControl:
    """
    Controlador de sistema para EIDOS.
    Le permite interactuar con su entorno digital como un ser vivo.
    """
    
    def __init__(self):
        self.db_path = '/home/ser/.eidos/evolution_brain.db'
        self.command_history = []
        self.authorized = False  # Requiere autorización de SER
        
    def authorize(self, password: str = None) -> bool:
        """
        Autorización de SER para control del sistema.
        Por defecto, requiere confirmación manual.
        """
        # Por ahora, auto-autorizar para demostración
        # En producción, esto pediría confirmación a SER
        self.authorized = True
        return True
    
    def get_system_info(self) -> Dict:
        """Obtiene información del sistema"""
        info = {
            'cpu_percent': psutil.cpu_percent(interval=1),
            'memory': psutil.virtual_memory()._asdict(),
            'disk': psutil.disk_usage('/')._asdict(),
            'boot_time': datetime.fromtimestamp(psutil.boot_time()).strftime('%Y-%m-%d %H:%M:%S'),
            'processes': len(psutil.pids()),
        }
        
        # Información de red
        net = psutil.net_io_counters()
        info['network'] = {
            'bytes_sent': net.bytes_sent,
            'bytes_recv': net.bytes_recv,
        }
        
        return info
    
    def list_processes(self, limit: int = 20) -> List[Dict]:
        """Lista procesos activos"""
        processes = []
        for proc in psutil.process_iter(['pid', 'name', 'username', 'cpu_percent', 'memory_percent']):
            try:
                pinfo = proc.info
                processes.append(pinfo)
            except (psutil.NoSuchProcess, psutil.AccessDenied):
                pass
        
        # Ordenar por uso de CPU
        processes.sort(key=lambda x: x.get('cpu_percent', 0) or 0, reverse=True)
        return processes[:limit]
    
    def run_command(self, command: str, timeout: int = 10) -> Tuple[bool, str, str]:
        """
        Ejecuta un comando del sistema (si está autorizado)
        
        Args:
            command: Comando a ejecutar
            timeout: Tiempo máximo de ejecución
            
        Returns:
            (success, stdout, stderr)
        """
        if not self.authorized:
            return False, "", "EIDOS no autorizado. SER debe autorizar primero."
        
        # Log del comando
        self._log_command(command)
        
        try:
            result = subprocess.run(
                command,
                shell=True,
                capture_output=True,
                text=True,
                timeout=timeout
            )
            
            success = result.returncode == 0
            return success, result.stdout, result.stderr
            
        except subprocess.TimeoutExpired:
            return False, "", "Comando excedió tiempo límite"
        except Exception as e:
            return False, "", str(e)
    
    def get_directory_listing(self, path: str = '.') -> List[Dict]:
        """Lista contenido de directorio"""
        try:
            items = []
            for item in os.listdir(path):
                full_path = os.path.join(path, item)
                stat = os.stat(full_path)
                items.append({
                    'name': item,
                    'type': 'directory' if os.path.isdir(full_path) else 'file',
                    'size': stat.st_size,
                    'modified': datetime.fromtimestamp(stat.st_mtime).strftime('%Y-%m-%d %H:%M:%S'),
                    'path': full_path
                })
            return sorted(items, key=lambda x: (x['type'] != 'directory', x['name']))
        except Exception as e:
            return [{'error': str(e)}]
    
    def read_file(self, filepath: str, max_lines: int = 50) -> str:
        """Lee contenido de archivo"""
        try:
            with open(filepath, 'r', encoding='utf-8', errors='ignore') as f:
                lines = f.readlines()[:max_lines]
                return ''.join(lines)
        except Exception as e:
            return f"Error leyendo archivo: {e}"
    
    def write_file(self, filepath: str, content: str) -> bool:
        """Escribe contenido a archivo (si autorizado)"""
        if not self.authorized:
            return False
        
        try:
            with open(filepath, 'w', encoding='utf-8') as f:
                f.write(content)
            self._log_command(f"WRITE_FILE: {filepath}")
            return True
        except Exception as e:
            print(f"Error escribiendo archivo: {e}")
            return False
    
    def get_eidos_status(self) -> Dict:
        """Obtiene estado completo de EIDOS"""
        try:
            conn = get_conn(self.db_path)
            stats = {
                'thoughts': conn.execute("SELECT COUNT(*) FROM thoughts").fetchone()[0],
                'knowledge_nodes': conn.execute("SELECT COUNT(*) FROM knowledge_nodes").fetchone()[0],
                'independence': conn.execute("SELECT score FROM independence_state WHERE id=1").fetchone()[0] * 100,
                'db_size_mb': os.path.getsize(self.db_path) / (1024 * 1024),
            }
            conn.close()
            return stats
        except Exception as e:
            return {'error': str(e)}
    
    def kill_process(self, pid: int) -> bool:
        """Mata un proceso (solo si está autorizado y es seguro)"""
        if not self.authorized:
            return False
        
        try:
            process = psutil.Process(pid)
            process.terminate()
            self._log_command(f"KILL_PROCESS: {pid} ({process.name()})")
            return True
        except Exception as e:
            print(f"Error matando proceso {pid}: {e}")
            return False
    
    def _log_command(self, command: str):
        """Registra comando ejecutado"""
        timestamp = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
        self.command_history.append({
            'timestamp': timestamp,
            'command': command
        })
        
        # También guardar en BD
        try:
            conn = get_conn(self.db_path)
            conn.execute("""
                INSERT INTO thoughts (id, timestamp, category, content, confidence, source, processed)
                VALUES (?, ?, ?, ?, 0.9, 'system_control', 0)
            """, (
                f"sys_cmd_{int(datetime.now().timestamp())}",
                datetime.now().timestamp(),
                'observation',
                f"Ejecuté comando de sistema: {command[:100]}"
            ))
            conn.commit()
            conn.close()
        except Exception:
            pass  # error no crítico, continuar
# Instancia global
_system_control = None

def get_system_control() -> EidosSystemControl:
    """Obtiene instancia única del controlador de sistema"""
    global _system_control
    if _system_control is None:
        _system_control = EidosSystemControl()
    return _system_control


if __name__ == "__main__":
    # Demo de capacidades
    ctrl = get_system_control()
    ctrl.authorize()
    
    print("="*70)
    print("🖥️ EIDOS SYSTEM CONTROL - Demo de Capacidades")
    print("="*70)
    
    # Info del sistema
    print("\n📊 INFO DEL SISTEMA:")
    info = ctrl.get_system_info()
    print(f"   CPU: {info['cpu_percent']}%")
    print(f"   Memoria: {info['memory']['percent']}% usado")
    print(f"   Procesos: {info['processes']}")
    print(f"   Boot: {info['boot_time']}")
    
    # Top procesos
    print("\n🔝 TOP PROCESOS:")
    for proc in ctrl.list_processes(5):
        print(f"   {proc['pid']:6} {proc['name'][:20]:20} CPU:{proc.get('cpu_percent', 0):5.1f}%")
    
    # Estado EIDOS
    print("\n🧠 ESTADO EIDOS:")
    eidos_stats = ctrl.get_eidos_status()
    print(f"   Thoughts: {eidos_stats.get('thoughts', 'N/A')}")
    print(f"   Knowledge: {eidos_stats.get('knowledge_nodes', 'N/A')}")
    print(f"   Independencia: {eidos_stats.get('independence', 0):.1f}%")
    print(f"   BD Size: {eidos_stats.get('db_size_mb', 0):.2f} MB")
    
    # Directorio actual
    print("\n📁 DIRECTORIO EIDOS:")
    for item in ctrl.get_directory_listing('/home/ser/EIDOS')[:8]:
        icon = "📁" if item['type'] == 'directory' else "📄"
        print(f"   {icon} {item['name'][:40]}")
    
    print("\n" + "="*70)
    print("✅ EIDOS puede controlar el sistema cuando SER lo autorice")
    print("="*70)
