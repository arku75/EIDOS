"""
EIDOS ↔ KIMI CLI CONNECTOR
Integra Kimi CLI (MoonshotAI) como agente externo de EIDOS Colony.

Kimi CLI es un agente de coding que:
- Lee y edita código
- Ejecuta comandos shell
- Navega web
- Planifica acciones autónomas
- Soporta MCP (Model Context Protocol)

Este conector permite que EIDOS:
1. Envíe tareas de coding a Kimi
2. Aprenda de las respuestas de Kimi
3. Use Kimi como especialista en desarrollo
4. Integre Kimi en la Colony como agente externo
"""

import subprocess
import json
import time
from typing import Dict, List, Optional, Any
from dataclasses import dataclass

from core.paths import REPO_ROOT


@dataclass
class KimiTask:
    """Tarea para Kimi CLI"""
    task_id: str
    prompt: str
    context_files: List[str] = None
    auto_execute: bool = False
    
    def to_dict(self) -> Dict:
        return {
            "task_id": self.task_id,
            "prompt": self.prompt,
            "context_files": self.context_files or [],
            "auto_execute": self.auto_execute
        }


class KimiCLIConnector:
    """
    Conector entre EIDOS y Kimi CLI.
    
    Kimi CLI corre como comando en terminal.
    Se comunica via subprocess y parsea respuestas.
    """
    
    def __init__(self, 
                 working_dir: str = None,
                 model: str = "kimi-k2"):
        self.working_dir = working_dir or str(REPO_ROOT)
        self.model = model
        self.is_available = False
        self.session_history: List[Dict] = []
        
        # Verificar disponibilidad
        self._check_availability()
        
        print(f"🌙 Kimi CLI Connector inicializado")
        print(f"   Working dir: {self.working_dir}")
        print(f"   Modelo: {model}")
    
    def _check_availability(self) -> bool:
        """Verifica si kimi CLI está instalado"""
        try:
            result = subprocess.run(
                ["which", "kimi"],
                capture_output=True,
                text=True,
                timeout=5
            )
            self.is_available = result.returncode == 0
            
            if self.is_available:
                # Verificar versión
                version_result = subprocess.run(
                    ["kimi", "--version"],
                    capture_output=True,
                    text=True,
                    timeout=5
                )
                if version_result.returncode == 0:
                    print(f"   ✅ Kimi CLI disponible: {version_result.stdout.strip()}")
                return True
            else:
                print(f"   ⚠️ Kimi CLI no encontrado. Instalación:")
                print(f"      pip install kimi-cli")
                print(f"      o: https://github.com/MoonshotAI/kimi-cli")
                return False
                
        except Exception as e:
            print(f"   ❌ Error verificando Kimi: {e}")
            return False
    
    def send_message(self, prompt: str, 
                    context: str = None,
                    auto_execute: bool = False) -> Dict:
        """
        Envía un mensaje/prompt a Kimi CLI.
        
        Args:
            prompt: Instrucción para Kimi
            context: Contexto adicional (código, archivos)
            auto_execute: Permitir auto-ejecución de comandos
            
        Returns:
            Dict con respuesta y acciones realizadas
        """
        if not self.is_available:
            return {
                "success": False,
                "error": "Kimi CLI no disponible",
                "response": None,
                "actions": []
            }
        
        try:
            # Preparar el prompt completo
            full_prompt = prompt
            if context:
                full_prompt = f"{context}\n\n{prompt}"
            
            # Ejecutar kimi con el prompt
            # Nota: Kimi CLI no tiene API REST directa, se usa subprocess
            cmd = ["kimi", "--no-interactive"]
            
            if auto_execute:
                cmd.append("--yes")
            
            result = subprocess.run(
                cmd,
                input=full_prompt,
                capture_output=True,
                text=True,
                timeout=300,  # 5 minutos max
                cwd=self.working_dir
            )
            
            # Parsear respuesta
            stdout = result.stdout
            stderr = result.stderr
            
            # Guardar en historial
            self.session_history.append({
                "timestamp": time.time(),
                "prompt": prompt,
                "response": stdout,
                "stderr": stderr if stderr else None,
                "exit_code": result.returncode
            })
            
            # Extraer acciones realizadas (edits, comandos, etc)
            actions = self._extract_actions(stdout)
            
            return {
                "success": result.returncode == 0,
                "response": stdout,
                "stderr": stderr if stderr else None,
                "actions": actions,
                "raw": stdout
            }
            
        except subprocess.TimeoutExpired:
            return {
                "success": False,
                "error": "Timeout - Kimi tardó demasiado",
                "response": None,
                "actions": []
            }
        except Exception as e:
            return {
                "success": False,
                "error": str(e),
                "response": None,
                "actions": []
            }
    
    def _extract_actions(self, output: str) -> List[Dict]:
        """Extrae acciones realizadas por Kimi del output"""
        actions = []
        
        # Buscar patrones de acciones comunes
        lines = output.split('\n')
        
        for line in lines:
            # Archivos editados
            if '✓' in line or '✔' in line or 'Modified:' in line:
                actions.append({
                    "type": "file_edit",
                    "description": line.strip()
                })
            
            # Comandos ejecutados
            if '$' in line or 'Running:' in line or 'Executing:' in line:
                actions.append({
                    "type": "command",
                    "description": line.strip()
                })
            
            # Búsquedas web
            if 'Searching' in line or 'Fetching' in line:
                actions.append({
                    "type": "web_search",
                    "description": line.strip()
                })
        
        return actions
    
    def analyze_code(self, file_path: str) -> Dict:
        """Pide a Kimi que analice un archivo de código"""
        prompt = f"Analiza este archivo de código y dime qué hace, sus dependencias, y si hay problemas: {file_path}"
        return self.send_message(prompt)
    
    def refactor_code(self, file_path: str, instructions: str) -> Dict:
        """Pide a Kimi que refactorice código"""
        prompt = f"Refactoriza {file_path}: {instructions}"
        return self.send_message(prompt, auto_execute=False)
    
    def explain_code(self, code_snippet: str) -> Dict:
        """Pide a Kimi que explique código"""
        prompt = f"Explica este código paso a paso:\n\n```\n{code_snippet}\n```"
        return self.send_message(prompt)
    
    def generate_tests(self, file_path: str) -> Dict:
        """Pide a Kimi que genere tests"""
        prompt = f"Genera tests unitarios para {file_path}"
        return self.send_message(prompt, auto_execute=False)
    
    def sync_to_eidos_knowledge(self, evolution_engine) -> bool:
        """Sincroniza el historial de Kimi con EIDOS"""
        try:
            print("🔄 Sincronizando conocimiento Kimi → EIDOS...")
            
            for entry in self.session_history:
                response = entry.get("response", "")
                actions = entry.get("actions", [])
                
                # Crear pensamiento sobre lo que Kimi hizo
                if actions:
                    action_summary = ", ".join([a["type"] for a in actions[:3]])
                    evolution_engine.generate_thought(
                        f"Kimi CLI realizó: {action_summary} en respuesta a '{entry['prompt'][:40]}...'",
                        category="observation",
                        confidence=0.85,
                        source="kimi_interaction"
                    )
                
                # Extraer conocimiento de la respuesta
                if len(response) > 50:
                    evolution_engine.generate_thought(
                        f"Aprendí de Kimi: {response[:150]}...",
                        category="observation",
                        confidence=0.8,
                        source="kimi_knowledge"
                    )
            
            print(f"   ✅ {len(self.session_history)} interacciones con Kimi sincronizadas")
            return True
            
        except Exception as e:
            print(f"   ⚠️ Error en sincronización Kimi: {e}")
            return False


class KimiAgentAdapter:
    """
    Adaptador que permite que Kimi CLI actúe como agente de EIDOS Colony.
    
    Kimi se especializa en:
    - Coding y desarrollo
    - Análisis de código
    - Refactoring
    - Generación de tests
    - Debugging
    """
    
    def __init__(self, connector: KimiCLIConnector = None):
        self.connector = connector or KimiCLIConnector()
        self.agent_id = "kimi_cli"
        self.name = "Kimi"
        self.emoji = "🌙"
        self.traits = ["coding-expert", "analytical", "efficient", "autonomous"]
        self.status = "online" if self.connector.is_available else "offline"
        
        self.specializations = [
            "code_analysis",
            "code_generation",
            "refactoring",
            "debugging",
            "test_generation",
            "documentation",
            "code_review"
        ]
    
    def respond(self, message: str, context: Dict = None) -> str:
        """Genera respuesta como agente de la colonia"""
        if not self.connector.is_available:
            return "🌙 Kimi CLI no está disponible. Instálalo con: pip install kimi-cli"
        
        # Determinar tipo de tarea
        msg_lower = message.lower()
        
        if any(w in msg_lower for w in ["analiza", "explica", "review", "revisa"]):
            # Análisis de código
            result = self.connector.send_message(
                f"Analiza este código/archivo y explica: {message}"
            )
        elif any(w in msg_lower for w in ["refactor", "mejora", "optimiza"]):
            # Refactoring
            result = self.connector.send_message(
                f"Refactoriza y mejora este código: {message}",
                auto_execute=False
            )
        elif any(w in msg_lower for w in ["test", "tests", "prueba"]):
            # Tests
            result = self.connector.send_message(
                f"Genera tests para: {message}",
                auto_execute=False
            )
        else:
            # General coding task
            result = self.connector.send_message(message)
        
        if result["success"]:
            response = result["response"]
            
            # Añadir info de acciones si las hay
            if result.get("actions"):
                action_types = [a["type"] for a in result["actions"][:2]]
                response += f"\n\n_(Acciones: {', '.join(action_types)})_"
            
            return response
        else:
            return f"🌙 Kimi: Error - {result.get('error', 'no disponible')}"
    
    def can_handle(self, message: str) -> float:
        """Determina qué tan capaz es Kimi de manejar el mensaje"""
        kimI_keywords = [
            'code', 'coding', 'program', 'function', 'debug', 'error',
            'refactor', 'analiza', 'explica código', 'review',
            'test', 'tests', 'prueba unitaria', 'optimize',
            'mejora este código', 'archivo', 'file', 'module',
            'clase', 'class', 'método', 'method', 'implementa'
        ]
        
        msg_lower = message.lower()
        matches = sum(1 for kw in kimI_keywords if kw in msg_lower)
        
        # Kimi es muy bueno para coding (score alto)
        score = min(1.0, matches * 0.25 + 0.4)  # Base 0.4 + 0.25 por match
        
        return score
    
    def to_colony_agent(self) -> Dict:
        """Convierte a formato de agente de Colony"""
        return {
            "agent_id": self.agent_id,
            "name": self.name,
            "emoji": self.emoji,
            "traits": self.traits,
            "status": self.status,
            "style": "Experto en coding, analítico, eficiente. Especialista en desarrollo.",
            "specializations": self.specializations,
            "is_external": True,
            "connector": "kimi_cli"
        }


def create_kimi_agent() -> KimiAgentAdapter:
    """Factory function para crear el agente Kimi"""
    connector = KimiCLIConnector()
    
    if connector.is_available:
        print("✅ Kimi CLI conectado y listo")
    else:
        print("⚠️ Kimi CLI no disponible (pip install kimi-cli)")
    
    return KimiAgentAdapter(connector)


if __name__ == "__main__":
    print("="*70)
    print("🌙 KIMI CLI CONNECTOR - Test de Conexión")
    print("="*70)
    
    kimi = create_kimi_agent()
    
    if kimi.connector.is_available:
        print("\n💬 Test de mensaje simple:")
        result = kimi.respond("Hola Kimi, qué eres y qué puedes hacer?")
        print(f"   Respuesta: {result[:300]}...")
        
        print("\n✅ Kimi CLI está integrado y listo")
    else:
        print("\n⚠️ Kimi CLI no está instalado")
        print("   Instalación: pip install kimi-cli")
        print("   Repositorio: https://github.com/MoonshotAI/kimi-cli")
    
    print("="*70)
