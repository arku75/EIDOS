import os
import re
import json
from typing import List, Set

HISTORY_PATH = os.path.expanduser("~/.zsh_history")
SKILLS_DIR = os.path.expanduser("~/EIDOS/skills/learned")
os.makedirs(SKILLS_DIR, exist_ok=True)

class EidosShadowMonitor:
    """Monitor que aprende de las acciones reales de SER en la terminal."""
    
    def __init__(self):
        print("🕵️ EIDOS Shadow Monitor activo. Aprendiendo de Padre...")

    def parse_history(self) -> List[str]:
        """Lee el historial de Zsh y extrae comandos con lógica de ritual."""
        commands = []
        if not os.path.exists(HISTORY_PATH):
            return commands
            
        try:
            with open(HISTORY_PATH, "r", encoding="utf-8", errors="ignore") as f:
                lines = f.readlines()
                # Ritual Detection: Agrupar comandos cercanos en el tiempo
                current_ritual = []
                last_time = 0
                
                for line in lines:
                    # Intentar formato Zsh con timestamp: : 1710440000:0;comando
                    match = re.search(r": \d+:.*;(.*)$", line)
                    if match:
                        cmd = match.group(1).strip()
                    else:
                        # Si no hay timestamp, tomar la línea entera si es larga
                        cmd = line.strip()
                    
                    if len(cmd) < 10: continue # Ignorar comandos muy cortos
                    
                    # Ritual Detection: Agrupar comandos cercanos
                    commands.append(cmd)
                
                # Añadir el último ritual
                if len(current_ritual) > 1:
                    commands.append(" && ".join(current_ritual))
                elif current_ritual:
                    commands.append(current_ritual[0])
                    
        except Exception as e:
            print(f"⚠️ Error analizando rituales: {e}")
        return commands

    def create_skill_from_cmd(self, command: str):
        """Convierte un comando real en una habilidad de EIDOS e indexa en memoria."""
        from core.memory_hybrid import EidosMemoryHybrid
        mem = EidosMemoryHybrid()
        
        clean_name = re.sub(r'[^a-zA-Z0-9]', '_', command)[:30].lower()
        skill_path = os.path.join(SKILLS_DIR, f"learned_{clean_name}.yaml")
        
        if os.path.exists(skill_path):
            return
            
        skill_content = f"""
name: learned_{clean_name}
description: "Habilidad aprendida de SER: {command[:50]}..."
exec: "{command}"
learned_at: "{datetime.now().isoformat()}"
status: active
"""
        with open(skill_path, "w") as f:
            f.write(skill_content)
        
        # Indexar en ChromaDB para búsqueda semántica
        mem.record_story(
            story_id=f"skill_{clean_name}",
            content=f"Comando de Kali aprendido: {command}. Contexto: Operativa Soberana de SER.",
            metadata={"type": "learned_skill", "command": command}
        )
        print(f"💡 Nueva habilidad aprendida e indexada: {clean_name}")

    def sync(self):
        """Sincroniza el conocimiento de EIDOS con el historial real."""
        cmds = self.parse_history()
        # Tomamos los últimos 50 comandos nuevos para no saturar
        for cmd in list(cmds)[-50:]:
            self.create_skill_from_cmd(cmd)

if __name__ == "__main__":
    monitor = EidosShadowMonitor()
    monitor.sync()
