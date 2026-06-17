"""
EIDOS ↔ TRINITYCLAW CONNECTOR
Integra TrinityClaw como agente externo de la Colonia EIDOS.

TrinityClaw es un agente auto-modificable con:
- Persistent memory (ChromaDB + JSONL)
- Multi-step reasoning
- 30+ skills (web, files, calendar, telegram, etc.)
- Browser automation
- Self-improvement loops

Este conector permite:
1. EIDOS enviar tareas a TrinityClaw
2. TrinityClaw reportar resultados a EIDOS
3. Sincronización de conocimiento entre ambos
4. TrinityClaw como agente especializado externo
"""

import requests
import json
import time
import os
from typing import Dict, List, Optional, Any
from dataclasses import dataclass


@dataclass
class TrinityTask:
    """Tarea a enviar a TrinityClaw"""
    task_id: str
    message: str
    skill_calls: List[Dict[str, Any]]
    timestamp: float
    priority: str = "normal"  # low, normal, high, critical
    context: Dict = None


class TrinityClawConnector:
    """
    Conector entre EIDOS y TrinityClaw.
    
    TrinityClaw corre en Docker con API en localhost:8001
    EIDOS puede enviarle tareas y recibir resultados.
    """
    
    def __init__(self, 
                 base_url: str = "http://localhost:8001",
                 api_key: str = None,
                 agent_name: str = "trinity"):
        self.base_url = base_url
        self.api_key = api_key or os.getenv("TRINITY_API_KEY", "")
        self.agent_name = agent_name
        self.session_history: List[Dict] = []
        self.is_connected = False
        self.trinity_skills: List[str] = []
        
        print(f"🔗 TrinityClaw Connector inicializado")
        print(f"   URL: {base_url}")
        print(f"   Agente: {agent_name}")
    
    def connect(self) -> bool:
        """Establece conexión con TrinityClaw y verifica disponibilidad"""
        try:
            # Verificar health endpoint
            response = requests.get(
                f"{self.base_url}/health",
                timeout=5
            )
            
            if response.status_code == 200:
                self.is_connected = True
                
                # Obtener lista de skills disponibles
                skills_resp = requests.get(
                    f"{self.base_url}/skills",
                    timeout=5
                )
                
                if skills_resp.status_code == 200:
                    skills_data = skills_resp.json()
                    self.trinity_skills = list(skills_data.get("skills", {}).keys())
                
                print(f"✅ Conectado a TrinityClaw")
                print(f"   Skills disponibles: {len(self.trinity_skills)}")
                return True
            else:
                print(f"⚠️ TrinityClaw no disponible (status: {response.status_code})")
                return False
                
        except requests.exceptions.ConnectionError:
            print(f"❌ No se pudo conectar a TrinityClaw en {self.base_url}")
            print(f"   Asegúrate de que TrinityClaw esté corriendo:")
            print(f"   docker-compose up -d (en el directorio de TrinityClaw)")
            return False
        except Exception as e:
            print(f"❌ Error de conexión: {e}")
            return False
    
    def send_message(self, message: str, include_history: bool = True) -> Dict:
        """
        Envía un mensaje a TrinityClaw y obtiene respuesta.
        
        Args:
            message: Mensaje para TrinityClaw
            include_history: Incluir historial de conversación
            
        Returns:
            Dict con respuesta y metadata
        """
        if not self.is_connected:
            if not self.connect():
                return {
                    "success": False,
                    "error": "No conectado a TrinityClaw",
                    "response": None
                }
        
        try:
            # Preparar payload
            payload = {
                "message": message,
                "session_id": f"eidos_{self.agent_name}_{int(time.time())}",
                "include_skills": True,
                "stream": False
            }
            
            # Incluir historial si hay
            if include_history and self.session_history:
                payload["history"] = self.session_history[-10:]  # Últimos 10 mensajes
            
            # Enviar a TrinityClaw
            response = requests.post(
                f"{self.base_url}/chat",
                json=payload,
                headers={"Content-Type": "application/json"},
                timeout=120  # Trinity puede tardar en responder
            )
            
            if response.status_code == 200:
                result = response.json()
                
                # Guardar en historial
                self.session_history.append({
                    "timestamp": time.time(),
                    "user_message": message,
                    "trinity_response": result.get("response", ""),
                    "skills_used": result.get("skills_used", [])
                })
                
                return {
                    "success": True,
                    "response": result.get("response", ""),
                    "skills_used": result.get("skills_used", []),
                    "thinking_steps": result.get("thinking", []),
                    "raw": result
                }
            else:
                return {
                    "success": False,
                    "error": f"HTTP {response.status_code}",
                    "response": response.text[:200]
                }
                
        except requests.exceptions.Timeout:
            return {
                "success": False,
                "error": "Timeout - TrinityClaw tardó demasiado en responder",
                "response": None
            }
        except Exception as e:
            return {
                "success": False,
                "error": str(e),
                "response": None
            }
    
    def call_skill(self, skill_name: str, function: str, args: List[Any]) -> Dict:
        """
        Llama directamente a un skill de TrinityClaw.
        
        Args:
            skill_name: Nombre del skill (ej: 'web', 'files', 'notes')
            function: Nombre de la función
            args: Lista de argumentos
            
        Returns:
            Resultado de la llamada al skill
        """
        if not self.is_connected:
            self.connect()
        
        try:
            payload = {
                "skill": skill_name,
                "function": function,
                "args": args
            }
            
            response = requests.post(
                f"{self.base_url}/skill/call",
                json=payload,
                headers={"Content-Type": "application/json"},
                timeout=60
            )
            
            if response.status_code == 200:
                return {
                    "success": True,
                    "result": response.json()
                }
            else:
                return {
                    "success": False,
                    "error": f"HTTP {response.status_code}",
                    "result": response.text
                }
                
        except Exception as e:
            return {
                "success": False,
                "error": str(e),
                "result": None
            }
    
    def execute_complex_task(self, task_description: str, 
                            required_skills: List[str] = None) -> Dict:
        """
        Ejecuta una tarea compleja usando TrinityClaw.
        
        Trinity usará su reasoning loop para completar tareas multi-paso.
        """
        # Preparar mensaje con instrucciones de skills
        message = task_description
        
        if required_skills:
            message += f"\n\n(Puedes usar estos skills: {', '.join(required_skills)})"
        
        return self.send_message(message)
    
    def get_skills_list(self) -> List[str]:
        """Obtiene lista de skills disponibles en TrinityClaw"""
        if not self.is_connected:
            self.connect()
        return self.trinity_skills
    
    def sync_to_eidos_knowledge(self, evolution_engine) -> bool:
        """
        Sincroniza el historial de TrinityClaw con el knowledge graph de EIDOS.
        
        Esto permite que EIDOS aprenda de las interacciones con Trinity.
        """
        try:
            print("🔄 Sincronizando conocimiento TrinityClaw → EIDOS...")
            
            for entry in self.session_history:
                # Extraer conocimiento de las conversaciones
                thinking = entry.get("trinity_response", "")
                
                # Crear pensamiento en EIDOS
                evolution_engine.generate_thought(
                    f"TrinityClaw respondió: {thinking[:100]}...",
                    category="observation",
                    confidence=0.8,
                    source="trinity_interaction"
                )
            
            print(f"   ✅ {len(self.session_history)} interacciones sincronizadas")
            return True
            
        except Exception as e:
            print(f"   ⚠️ Error en sincronización: {e}")
            return False


class TrinityAgentAdapter:
    """
    Adaptador que permite que TrinityClaw actúe como un agente de EIDOS Colony.
    
    Trinity se comporta como un agente más de la colonia, con:
    - Personalidad especializada en automatización y web
    - Acceso a 30+ skills
    - Capacidad de auto-mejora
    """
    
    def __init__(self, connector: TrinityClawConnector = None):
        self.connector = connector or TrinityClawConnector()
        self.agent_id = "trinity_claw"
        self.name = "TrinityClaw"
        self.emoji = "🦾"
        self.traits = ["auto-modificable", "multi-skill", "web-automation", "self-improving"]
        self.status = "online"
        
        # Capacidades especiales
        self.specializations = [
            "web_browsing",
            "browser_automation", 
            "file_management",
            "task_scheduling",
            "telegram_integration",
            "calendar_management",
            "email_sending",
            "code_execution",
            "git_management",
            "self_improvement"
        ]
    
    def respond(self, message: str, context: Dict = None) -> str:
        """
        Genera respuesta como agente de la colonia.
        
        Este método es llamado por ColonyCommunity cuando se menciona a Trinity.
        """
        # Enviar a TrinityClaw
        result = self.connector.send_message(message)
        
        if result["success"]:
            response = result["response"]
            
            # Añadir metadata de skills usados
            if result.get("skills_used"):
                skills = ", ".join(result["skills_used"][:3])
                response += f"\n\n_(Usé: {skills})_"
            
            return response
        else:
            return f"🔧 TrinityClaw: No puedo responder ahora ({result.get('error', 'desconectado')})."
    
    def can_handle(self, message: str) -> float:
        """
        Determina qué tan capaz es Trinity de manejar este mensaje.
        
        Returns: 0.0 a 1.0 (confidence score)
        """
        # Keywords que Trinity maneja bien
        trinity_keywords = [
            'web', 'browser', 'navegar', 'automation', 'telegram',
            'calendar', 'email', 'send', 'schedule', 'tarea',
            'automatizar', 'browse', 'internet', 'online',
            'gmail', 'google calendar', 'telegram bot',
            'web scraping', 'screenshot', 'click',
            'file management', 'git', 'github'
        ]
        
        msg_lower = message.lower()
        matches = sum(1 for kw in trinity_keywords if kw in msg_lower)
        
        # Score basado en matches
        score = min(1.0, matches * 0.2 + 0.3)  # Base 0.3 + 0.2 por match
        
        return score
    
    def to_colony_agent(self) -> Dict:
        """Convierte a formato de agente de Colony"""
        return {
            "agent_id": self.agent_id,
            "name": self.name,
            "emoji": self.emoji,
            "traits": self.traits,
            "status": self.status,
            "style": "Eficiente, auto-modificable, directo. Usa skills XML.",
            "specializations": self.specializations,
            "is_external": True,
            "connector": "trinity_claw"
        }


def create_trinity_agent() -> TrinityAgentAdapter:
    """
    Factory function para crear el agente TrinityClaw.
    
    Uso:
        from trinity_connector import create_trinity_agent
        trinity = create_trinity_agent()
        
        # Añadir a Colony
        colony.register_external_agent(trinity.to_colony_agent())
    """
    connector = TrinityClawConnector()
    
    if connector.connect():
        print("✅ TrinityClaw conectado y listo")
    else:
        print("⚠️ TrinityClaw no disponible (puede que no esté corriendo)")
    
    return TrinityAgentAdapter(connector)


# ═══════════════════════════════════════════════════════════════
# INTEGRACIÓN CON COLONY COMMUNITY
# ═══════════════════════════════════════════════════════════════

def integrate_trinity_with_colony(community):
    """
    Integra TrinityClaw con Colony Community.
    
    Añade Trinity como agente externo de la colonia.
    """
    try:
        trinity = create_trinity_agent()
        
        if trinity.connector.is_connected:
            # Añadir a la colonia
            # Nota: Esto requiere modificar ColonyCommunity para soportar agentes externos
            print("🔗 Integrando TrinityClaw con Colony...")
            
            # Por ahora, solo mostramos info
            print(f"   Agente: {trinity.name} {trinity.emoji}")
            print(f"   Skills: {len(trinity.connector.trinity_skills)}")
            print(f"   Especializaciones: {', '.join(trinity.specializations[:5])}")
            
            return trinity
        else:
            print("⚠️ TrinityClaw no está disponible")
            return None
            
    except Exception as e:
        print(f"❌ Error en integración: {e}")
        return None


if __name__ == "__main__":
    print("="*70)
    print("🔗 TRINITYCLAW CONNECTOR - Test de Conexión")
    print("="*70)
    
    # Crear conector
    trinity = create_trinity_agent()
    
    if trinity.connector.is_connected:
        print("\n📋 Skills disponibles en TrinityClaw:")
        for skill in trinity.connector.get_skills_list()[:10]:
            print(f"   • {skill}")
        
        # Test de mensaje
        print("\n💬 Test de mensaje:")
        result = trinity.respond("Hola Trinity, qué skills tienes disponibles?")
        print(f"   Respuesta: {result[:200]}...")
        
        print("\n✅ TrinityClaw está integrado y listo")
    else:
        print("\n⚠️ TrinityClaw no está corriendo")
        print("   Para iniciar TrinityClaw:")
        print("   cd /ruta/a/trinity-claw && docker-compose up -d")
    
    print("="*70)
