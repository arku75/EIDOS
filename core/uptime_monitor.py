"""EIDOS Uptime Monitor - Test 24h estabilidad"""
import time
import json
import os
from datetime import datetime
from pathlib import Path

DB_PATH = Path.home() / ".eidos" / "uptime.db"

class UptimeMonitor:
    def __init__(self):
        self.start_time = time.time()
        self.checks = 0
        self.failures = 0
        
    def check_health(self) -> dict:
        """Verifica salud del sistema"""
        try:
            # Check Ollama
            import urllib.request
            ollama_ok = False
            try:
                urllib.request.urlopen("http://localhost:11434", timeout=2)
                ollama_ok = True
            except Exception:
                pass  # error no crítico, continuar
            uptime = time.time() - self.start_time
            self.checks += 1
            
            return {
                "timestamp": datetime.now().isoformat(),
                "uptime_seconds": uptime,
                "uptime_hours": uptime / 3600,
                "checks": self.checks,
                "ollama_ok": ollama_ok,
                "healthy": ollama_ok
            }
        except Exception as e:
            self.failures += 1
            return {"healthy": False, "error": str(e)}
    
    def run_24h_test(self):
        """Ejecuta test de 24h con checks cada 5 minutos"""
        print("[UPTIME] Iniciando test 24h...")
        checks_24h = (24 * 60) / 5  # 288 checks
        
        for i in range(int(checks_24h)):
            status = self.check_health()
            if not status.get("healthy"):
                print(f"[UPTIME] Check {i}: FALLIDO - {status.get('error', 'unknown')}")
            else:
                print(f"[UPTIME] Check {i}: OK - {status['uptime_hours']:.1f}h")
            
            time.sleep(300)  # 5 minutos
        
        print(f"[UPTIME] Test 24h completado: {self.checks} checks, {self.failures} fallos")

if __name__ == "__main__":
    monitor = UptimeMonitor()
    monitor.run_24h_test()
