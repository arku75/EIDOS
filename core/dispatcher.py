import asyncio
import logging
import traceback
import sys
import os

# Asegurar path
EIDOS_DIR = os.path.expanduser("~/EIDOS")
if EIDOS_DIR not in sys.path:
    sys.path.insert(0, EIDOS_DIR)

try:
    from core.agent import EIDOSAgent
    from eidos_cli import SOUL
    HAS_AGENT = True
except ImportError:
    HAS_AGENT = False
    SOUL = "Eres EIDOS. Módulo base."

try:
    from core.orchestrator import master_orchestrator
    HAS_ORCHESTRATOR = True
except ImportError:
    HAS_ORCHESTRATOR = False

try:
    from core.intent_classifier import IntentClassifier
    HAS_CLASSIFIER = True
except ImportError:
    HAS_CLASSIFIER = False

class UnifiedDispatcher:
    """
    Despachador central asíncrono para EIDOS.
    Enruta mensajes de diferentes canales (Telegram, Discord) hacia el núcleo agéntico.
    """
    def __init__(self):
        self.queue = asyncio.Queue()
        self.logger = logging.getLogger("UnifiedDispatcher")
        self.logger.setLevel(logging.INFO)
        # Formato de logger console
        if not self.logger.handlers:
            ch = logging.StreamHandler()
            ch.setFormatter(logging.Formatter('%(asctime)s - %(name)s - %(levelname)s - %(message)s'))
            self.logger.addHandler(ch)

        self.channels = {} # ej: {"telegram": instancia}
        self.user_histories = {} # user_id -> list[dict]
        
        if HAS_CLASSIFIER:
            self.intent_classifier = IntentClassifier()
        else:
            self.intent_classifier = None

    def register_channel(self, name: str, channel_instance):
        self.channels[name] = channel_instance
        self.logger.info(f"✅ Canal registrado: {name.upper()}")

    async def put_message(self, source: str, user_id: str, text: str):
        """Agrega un nuevo mensaje entrante a la cola de procesamiento."""
        await self.queue.put((source, user_id, text))
        self.logger.debug(f"📥 Encolado mensaje de {source}/{user_id}")

    async def dispatch_loop(self):
        """Bucle consumidor asíncrono principal."""
        self.logger.info("🛰️  Dispatcher Loop Iniciado... Esperando transmisiones.")
        while True:
            try:
                source, user_id, text = await self.queue.get()
                self.logger.info(f"⚡ Procesando msg de {source} (User: {user_id}): {text[:50]}...")  # pyre-ignore[arg-type]
                
                # Procesar en un thread para no bloquear el loop Async del bot
                loop = asyncio.get_running_loop()
                response_text = await loop.run_in_executor(None, self._process_sync, user_id, text, source)
                
                # Enviar respuesta al canal correspondiente
                if source in self.channels:
                    await self.channels[source].send_reply(user_id, response_text)
                else:
                    self.logger.error(f"❌ Canal origen desconocido: {source}")
                    
                self.queue.task_done()
            except Exception as e:
                self.logger.error(f"❌ Error en Dispatcher Loop: {e}\n{traceback.format_exc()}")
                await asyncio.sleep(2)

    def _process_sync(self, user_id: str, text: str, source: str) -> str:
        """Procesa el mensaje mediante el agente o el orquestador (síncrono)."""
        t = text.strip()
        
        intent = "CHAT"
        if self.intent_classifier:
            # Bypass para comandos directos heredados
            if t.startswith("/plan") or t.startswith(":plan"):
                intent = "PLAN"
            else:
                intent = self.intent_classifier.classify(t)
            self.logger.info(f"🧠 Intención detectada: [{intent}] para '{t[:20]}...'")  # pyre-ignore[arg-type]
        
        # Meta-Orquestación explícita (Fénix)
        if intent == "PLAN" or t.startswith("/plan ") or t.startswith(":plan "):
            if HAS_ORCHESTRATOR:
                # Limpiar prefijo si viene del bypass
                goal = t
                if goal.startswith("/plan "): goal = goal[6:].strip()  # pyre-ignore[arg-type]
                if goal.startswith(":plan "): goal = goal[6:].strip()  # pyre-ignore[arg-type]
                
                self.logger.info(f"🦅 Activando Fénix (MasterOrchestrator) para objetivo: {goal}")
                try:
                    res = master_orchestrator.run(goal)
                    return f"🔥 [FÉNIX COMPLETADO]\n\n{res}"
                except Exception as e:
                    return f"❌ Falla de Orquestación: {e}"
            else:
                return "❌ MasterOrchestrator no instalado o no accesible."

        # Procesamiento normal ReAct Tool-calling (EIDOSAgent)
        if HAS_AGENT:
            # Recuperar historial
            if user_id not in self.user_histories:
                self.user_histories[user_id] = []
            
            history = self.user_histories[user_id]
            
            agent = EIDOSAgent(soul=SOUL, agent_id=f"disp_{source}_{user_id}", verbose=True)
            self.logger.info(f"🤖 Ejecutando EIDOSAgent [user={user_id}]...")
            
            try:
                res = agent.run(task=text, history=history, use_tools=True, verify=False)
                # Sincronizar historial truncado
                self.user_histories[user_id] = agent.history[-20:] # Mantener 20 turnos max
                return res
            except Exception as e:
                self.logger.error(f"AgentError: {e}\n{traceback.format_exc()}")
                return f"[EIDOSAgentError: {e}]"
                
        return "[DISPATCHER] Comando recibido, pero el cerebro de EIDOSAgent no está cargado."

# Instancia Singleton Global (lazy - no se crea al importar)
_dispatcher = None

def get_dispatcher():
    global _dispatcher
    if _dispatcher is None:
        _dispatcher = UnifiedDispatcher()
    return _dispatcher
