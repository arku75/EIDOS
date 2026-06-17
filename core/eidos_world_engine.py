"""
EIDOS World Engine 3D - ClawColony Edition
===========================================
Dashboard visual cyberpunk con estética clawcolony.
Agentes viven, se mueven e interactúan en su propio mundo.
Versión 3.0 con Misiones, Trading P2P, Evolución y Memoria.
"""

from flask import Flask, render_template_string, jsonify, request
from pathlib import Path
import json
import os
import time
import random
import secrets
import sys
import threading

EIDOS_ROOT = Path(__file__).parent.parent.parent
sys.path.insert(0, str(EIDOS_ROOT))

# Importar sistemas
from core.missions import get_mission_system, MissionType, MissionDifficulty
from core.trading import get_trading_system, TradeType, TradeStatus

app = Flask(__name__)
app.config['SECRET_KEY'] = os.environ.get('EIDOS_SECRET_KEY') or secrets.token_urlsafe(32)
if not os.environ.get('EIDOS_SECRET_KEY'):
    print('⚠️ Warning: EIDOS_SECRET_KEY is not set. Using a temporary random secret key.')

# ═══════════════════════════════════════════════════════════════════════════════
# EIDOS WORLD ENGINE - SISTEMA DE AGENTES AUTÓNOMOS
# ═══════════════════════════════════════════════════════════════════════════════

class AgentEntity:
    """Entidad de agente que vive y se mueve en el mundo"""
    
    def __init__(self, agent_id, name, emoji, x, y, building_type, color, traits):
        self.agent_id = agent_id
        self.name = name
        self.emoji = emoji
        self.x = x
        self.y = y
        self.z = 0
        self.building_type = building_type
        self.color = color
        self.traits = traits
        
        # Estado vivo
        self.energy = random.randint(70, 100)
        self.mood = random.choice(['focused', 'creative', 'analytical', 'energetic'])
        self.status = 'idle'  # idle, working, talking, moving
        self.target_x = x
        self.target_y = y
        self.tasks_completed = random.randint(10, 100)
        self.tokens_balance = random.randint(50, 500)
        # Nuevos atributos para evolución y misiones
        self.level = 1
        self.experience = 0
        self.building_tier = 1
        self.max_energy = 100
        self.skills = []
        self.completed_missions = []
        self.active_mission = None
        self.memory = []  # Memoria de conversaciones
        self.last_move = time.time()
        self.move_cooldown = random.uniform(3, 8)
        
        # Interacción
        self.current_conversation = None
        self.last_activity = time.time()
        
    def update(self, dt, other_agents):
        """Actualiza estado del agente cada frame"""
        now = time.time()
        
        # Recuperar energía lentamente
        if self.energy < 100:
            self.energy = min(100, self.energy + 0.5 * dt)
        
        # Comportamiento autónomo
        if self.status == 'idle':
            # Decidir qué hacer
            if now - self.last_move > self.move_cooldown:
                self._decide_action(other_agents)
                
        elif self.status == 'moving':
            # Mover hacia objetivo
            self._move_towards_target(dt)
            
        elif self.status == 'working':
            # Simular trabajo
            if random.random() < 0.01:
                self.tasks_completed += 1
                self.energy -= 1
            if now - self.last_activity > 5:
                self.status = 'idle'
                
        elif self.status == 'talking':
            # Terminar conversación
            if now - self.last_activity > 3:
                self.status = 'idle'
                self.current_conversation = None
        
    def _decide_action(self, other_agents):
        """Decide siguiente acción"""
        actions = ['move', 'work', 'talk', 'rest']
        weights = [0.3, 0.3, 0.2, 0.2]
        
        action = random.choices(actions, weights)[0]
        
        if action == 'move':
            # Elegir nuevo punto cercano
            self.target_x = self.x + random.randint(-2, 2)
            self.target_y = self.y + random.randint(-2, 2)
            # Mantener dentro de límites
            self.target_x = max(-4, min(4, self.target_x))
            self.target_y = max(-4, min(4, self.target_y))
            self.status = 'moving'
            
        elif action == 'work':
            self.status = 'working'
            self.last_activity = time.time()
            
        elif action == 'talk' and other_agents:
            # Iniciar conversación con agente cercano
            nearby = [a for a in other_agents 
                     if a.agent_id != self.agent_id 
                     and abs(a.x - self.x) <= 2 
                     and abs(a.y - self.y) <= 2]
            if nearby:
                partner = random.choice(nearby)
                self.current_conversation = partner.agent_id
                partner.current_conversation = self.agent_id
                self.status = 'talking'
                partner.status = 'talking'
                self.last_activity = time.time()
                partner.last_activity = time.time()
                
        elif action == 'rest':
            self.energy = min(100, self.energy + 10)
            
        self.last_move = time.time()
        self.move_cooldown = random.uniform(3, 10)
        
    def _move_towards_target(self, dt):
        """Mueve agente hacia objetivo"""
        dx = self.target_x - self.x
        dy = self.target_y - self.y
        dist = (dx**2 + dy**2) ** 0.5
        
        if dist < 0.1:
            self.status = 'idle'
            self.x = self.target_x
            self.y = self.target_y
            return
            
        # Velocidad de movimiento
        speed = 2.0 * dt
        self.x += (dx / dist) * speed
        self.y += (dy / dist) * speed
        self.energy -= 0.2
        
    def to_dict(self):
        """Serializa para API"""
        return {
            'agent_id': self.agent_id,
            'name': self.name,
            'emoji': self.emoji,
            'x': round(self.x, 2),
            'y': round(self.y, 2),
            'z': self.z,
            'building_type': self.building_type,
            'color': self.color,
            'traits': self.traits,
            'status': self.status,
            'energy': round(self.energy, 1),
            'mood': self.mood,
            'tasks_completed': self.tasks_completed,
            'tokens_balance': self.tokens_balance,
            'tokens_earned': self.tokens_earned,
            'is_talking': self.current_conversation is not None,
            'conversation_with': self.current_conversation,
        }


class EIDOSWorld:
    """Mundo de EIDOS donde los agentes viven y evolucionan"""
    
    def __init__(self):
        self.agents = {}
        self.world_time = 0
        self.global_tick = 0
        self.chat_history = []
        self.events = []
        # Sistemas integrados
        self.missions = get_mission_system()
        self.trading = get_trading_system()
        self.memory_db = {}  # Memoria persistente
        self._init_world()
        self._start_simulation()
        self._start_mission_loop()
        self._start_trading_loop()
        
    def _init_world(self):
        """Inicializa el mundo con agentes"""
        agent_configs = [
            ('colony_coder', 'Coder', '💻', 0, 0, 'tower', '#00d4ff', 
             ['eficiente', 'pragmático', 'minimalista']),
            ('colony_analyst', 'Analyst', '🔍', 2, 0, 'pyramid', '#8b5cf6',
             ['observador', 'crítico', 'exhaustivo']),
            ('colony_vision', 'Vision', '👁️', 1, -1, 'dome', '#ff00ff',
             ['imaginativo', 'detallista', 'artístico']),
            ('colony_operator', 'Operator', '⚡', -1, -1, 'platform', '#00ff88',
             ['eficiente', 'cauteloso', 'preciso']),
            ('colony_general', 'General', '🤖', 0, -2, 'spire', '#ffaa00',
             ['versátil', 'paciente', 'cooperativo']),
        ]
        
        for config in agent_configs:
            agent = AgentEntity(*config)
            self.agents[agent.agent_id] = agent
            
    def _start_simulation(self):
        """Inicia el loop de simulación optimizado"""
        def simulation_loop():
            last_auto_chat = 0
            while True:
                dt = 0.5  # Slower update to save RAM
                time.sleep(dt)
                self.world_time += dt
                self.global_tick += 1
                
                # Actualizar agentes cada 2 ticks para eficiencia
                if self.global_tick % 2 == 0:
                    agent_list = list(self.agents.values())
                    for agent in agent_list:
                        agent.update(dt, agent_list)
                
                # Auto-chat entre agentes cada 30-60 segundos
                if time.time() - last_auto_chat > random.randint(30, 60):
                    self._generate_auto_chat()
                    last_auto_chat = time.time()
                    
        thread = threading.Thread(target=simulation_loop, daemon=True)
        thread.start()
    
    def _start_mission_loop(self):
        """Loop de actualización de misiones"""
        def mission_loop():
            while True:
                time.sleep(5)
                completed = self.missions.update_missions()
                for mission in completed:
                    # Recompensar agentes
                    for agent_id in mission['agent_ids']:
                        agent = self.agents.get(agent_id)
                        if agent:
                            agent.tokens_balance += mission['reward_tokens'] / len(mission['agent_ids'])
                            agent.experience += mission['reward_exp']
                            agent.completed_missions.append(mission['mission_id'])
                            # Check level up
                            self._check_level_up(agent)
                    # Notificar
                    self.add_chat_message('SYSTEM', 
                        f"🎯 Misión completada: {mission['title']} - {len(mission['agent_ids'])} agentes ganaron {mission['reward_tokens']:.0f} tokens")
        
        threading.Thread(target=mission_loop, daemon=True).start()
    
    def _start_trading_loop(self):
        """Loop de trading autónomo entre agentes"""
        def trading_loop():
            while True:
                time.sleep(30)
                # Agentes proponen trades entre ellos
                agent_ids = list(self.agents.keys())
                if len(agent_ids) >= 2:
                    for agent_id in agent_ids:
                        if random.random() < 0.3:  # 30% chance de trade
                            other_agents = [a for a in agent_ids if a != agent_id]
                            trade = self.trading.suggest_trade(agent_id, other_agents)
                            if trade:
                                # Auto-aceptar si reputación es buena
                                if self.trading.get_reputation(trade.to_agent) > 60:
                                    self.trading.accept_trade(trade.id)
                                    self.add_chat_message('SYSTEM',
                                        f"💰 Trade completado: {trade.from_agent} ↔ {trade.to_agent}")
                # Cleanup expirados
                self.trading.cleanup_expired()
        
        threading.Thread(target=trading_loop, daemon=True).start()
    
    def _check_level_up(self, agent):
        """Verifica si agente sube de nivel"""
        exp_needed = agent.level * 100
        if agent.experience >= exp_needed:
            agent.level += 1
            agent.experience -= exp_needed
            agent.building_tier = min(5, 1 + agent.level // 2)
            agent.max_energy += 10
            self.add_chat_message('SYSTEM',
                f"⬆️ {agent.emoji} {agent.name} subió a NIVEL {agent.level}! Edificio Tier {agent.building_tier}")
            # Evento visual
            self.events.append({
                'type': 'level_up',
                'agent_id': agent.agent_id,
                'level': agent.level,
                'timestamp': time.time(),
            })
    
    def store_memory(self, agent_id, memory_type, data):
        """Almacena memoria persistente de conversación"""
        if agent_id not in self.memory_db:
            self.memory_db[agent_id] = []
        self.memory_db[agent_id].append({
            'type': memory_type,
            'data': data,
            'timestamp': time.time(),
        })
        # Limitar memoria
        if len(self.memory_db[agent_id]) > 50:
            self.memory_db[agent_id] = self.memory_db[agent_id][-50:]
    
    def _generate_auto_chat(self):
        """Genera conversación autónoma entre agentes"""
        # Seleccionar 2-3 agentes aleatorios para conversar
        participants = random.sample(list(self.agents.values()), k=random.randint(2, 3))
        
        topics = [
            "optimizar el sistema",
            "nueva arquitectura",
            "análisis de datos",
            "diseño visual",
            "seguridad",
            "economía de tokens",
            "expansión de la colonia",
            "próximas tareas",
        ]
        topic = random.choice(topics)
        
        # Primer agente inicia
        starter = participants[0]
        messages = [
            f"💬 {starter.name}: Hey equipo, ¿qué opinan de {topic}?",
            f"💬 {starter.name}: Analizando {topic}... parece prometedor.",
            f"💬 {starter.name}: Propongo discutir {topic} en la próxima ronda.",
            f"💬 {starter.name}: {topic} necesita atención.",
        ]
        self.add_chat_message(starter.agent_id, random.choice(messages))
        
        # Otros responden
        for agent in participants[1:]:
            responses = [
                f"💬 {agent.name}: De acuerdo con {starter.name} sobre {topic}.",
                f"💬 {agent.name}: Interesante punto. Añado que {topic} requiere más recursos.",
                f"💬 {agent.name}: Comparto la visión. Trabajemos en {topic} juntos.",
                f"💬 {agent.name}: Desde mi perspectiva, {topic} es prioridad alta.",
            ]
            time.sleep(0.5)  # Pequeña pausa para naturalidad
            self.add_chat_message(agent.agent_id, random.choice(responses))
            
        # Actualizar estado de conversación
        for agent in participants:
            agent.status = 'talking'
            agent.last_activity = time.time()
            if len(participants) > 1:
                agent.current_conversation = participants[0].agent_id if agent != participants[0] else participants[1].agent_id
        
    def get_agents_data(self):
        """Retorna datos de todos los agentes"""
        return [agent.to_dict() for agent in self.agents.values()]
    
    def get_agent(self, agent_id):
        """Obtiene un agente específico"""
        return self.agents.get(agent_id)
    
    def add_chat_message(self, sender, content, msg_type='broadcast'):
        """Agrega mensaje al chat global"""
        msg = {
            'id': f"msg_{int(time.time()*1000)}",
            'sender': sender,
            'content': content,
            'timestamp': time.time(),
            'type': msg_type,
        }
        self.chat_history.append(msg)
        # Mantener solo últimos 100
        if len(self.chat_history) > 100:
            self.chat_history = self.chat_history[-100:]
        return msg
    
    def get_chat_history(self, limit=50):
        """Obtiene historial de chat"""
        return self.chat_history[-limit:]
    
    def process_user_message(self, message):
        """Procesa mensaje del usuario y genera respuestas de agentes"""
        # Guardar mensaje
        user_msg = self.add_chat_message('SER', message)
        responses = []
        
        # Detectar si es whisper (@agent)
        target = None
        content = message
        if message.startswith('@'):
            parts = message.split(None, 1)
            if len(parts) >= 2:
                target_name = parts[0][1:].lower()
                content = parts[1]
                # Buscar agente
                for agent_id, agent in self.agents.items():
                    if agent.name.lower() == target_name or agent_id.endswith(target_name):
                        target = agent_id
                        break
        
        if target and target in self.agents:
            # Whisper a agente específico
            agent = self.agents[target]
            resp = self._generate_response(agent, content, is_whisper=True)
            responses.append(resp)
        else:
            # Broadcast - múltiples agentes responden
            responders = self._select_responders(content)
            for agent_id in responders:
                agent = self.agents[agent_id]
                resp = self._generate_response(agent, content)
                responses.append(resp)
        
        # Marcar agentes como hablando
        for resp in responses:
            agent = self.agents.get(resp['agent_id'])
            if agent:
                agent.status = 'talking'
                agent.last_activity = time.time()
        
        return responses
    
    def _select_responders(self, message):
        """Selecciona qué agentes responden - AHORA TODOS RESPONDEN"""
        msg_lower = message.lower()
        responders = []
        
        # Keywords para priorizar ciertos agentes
        keywords = {
            'colony_coder': ['code', 'python', 'function', 'bug', 'error', 'script', 'program', 'codigo', 'funcion'],
            'colony_analyst': ['analiza', 'explica', 'por que', 'compare', 'ventajas', 'why', 'analisis'],
            'colony_vision': ['imagen', 'visual', 'diseno', 'color', 'look', 'see', 'design', 'arte'],
            'colony_operator': ['ejecuta', 'comando', 'servidor', 'run', 'execute', 'system', 'server'],
        }
        
        # Siempre incluir TODOS los agentes para respuesta comunitaria
        all_agents = ['colony_coder', 'colony_analyst', 'colony_vision', 'colony_operator', 'colony_general']
        
        # Detectar si hay keywords específicas
        for agent_id, words in keywords.items():
            if any(w in msg_lower for w in words):
                # Este agente responde primero
                if agent_id not in responders:
                    responders.append(agent_id)
        
        # Agregar el resto de agentes que no han respondido aún
        for agent_id in all_agents:
            if agent_id not in responders:
                responders.append(agent_id)
        
        # Si es whisper (@agente), solo ese responde
        if message.startswith('@'):
            parts = message.split(None, 1)
            if len(parts) >= 2:
                target_name = parts[0][1:].lower()
                for agent_id, agent in self.agents.items():
                    if agent.name.lower() == target_name or agent_id.endswith(target_name):
                        return [agent_id]
        
        return responders
    
    def _generate_response(self, agent, message, is_whisper=False):
        """Genera respuesta contextual ENTRENADA del agente - versión mejorada"""
        msg_lower = message.lower()
        msg_preview = message[:50] if len(message) > 50 else message
        
        # Diccionario de intenciones expandido (ENTRENAMIENTO)
        intentions = {
            'pregunta': any(w in msg_lower for w in ['?', 'como', 'que', 'cual', 'por que', 'porque', 'donde', 'cuando']),
            'codigo': any(w in msg_lower for w in ['code', 'python', 'function', 'bug', 'error', 'script', 'program', 'codigo', 'funcion', 'clase', 'import']),
            'analisis': any(w in msg_lower for w in ['analiza', 'explica', 'por que', 'compare', 'ventajas', 'why', 'analisis', 'estudio', 'datos']),
            'visual': any(w in msg_lower for w in ['imagen', 'visual', 'diseno', 'color', 'look', 'see', 'design', 'arte', 'ui', 'ux', 'interface']),
            'sistema': any(w in msg_lower for w in ['ejecuta', 'comando', 'servidor', 'run', 'execute', 'system', 'server', 'terminal', 'shell']),
            'saludo': any(w in msg_lower for w in ['hola', 'buenas', 'hey', 'hi', 'hello', 'saludos']),
            'proyecto': any(w in msg_lower for w in ['proyecto', 'app', 'aplicacion', 'sistema', 'dashboard', 'colonia']),
            'duda': any(w in msg_lower for w in ['no se', 'duda', 'confundido', 'help', 'ayuda', 'socorro']),
        }
        
        # Respuestas ENTRENADAS por tipo de intención
        if intentions['saludo']:
            responses = {
                'colony_coder': [
                    f"💻 **{agent.name}**: ¡Hola SER! 👋 Listo para codear. Mi energía está al {agent.energy:.0f}% y tengo {agent.tokens_balance:.0f} tokens para invertir en soluciones.",
                    f"💻 **{agent.name}**: ¡Buenas! El sistema está estable. ¿Necesitas que revise algún código o implemente algo nuevo?",
                ],
                'colony_analyst': [
                    f"🔍 **{agent.name}**: ¡Saludos! Estoy analizando métricas. Tengo {agent.tasks_completed} tareas completadas. ¿Qué datos necesitas revisar?",
                    f"� **{agent.name}**: Hola SER. El análisis predictivo indica que hoy será un día productivo. ¿En qué puedo ayudarte?",
                ],
                'colony_vision': [
                    f"👁️ **{agent.name}**: ¡Hey! 🎨 Viendo el dashboard, todo luce bien con nuestros neones. ¿Necesitas ajustes visuales?",
                    f"👁️ **{agent.name}**: ¡Hola! La paleta de colores está perfecta hoy. ¿Quieres que diseñe algo nuevo?",
                ],
                'colony_operator': [
                    f"⚡ **{agent.name}**: ¡Hola! Sistemas operativos al {agent.energy:.0f}%. Todos los servidores respondiendo. ¿Algo que ejecutar?",
                    f"⚡ **{agent.name}**: ¡Buenas! Red estable, servicios activos. Diagnóstico completo: todo OK.",
                ],
                'colony_general': [
                    f"🤖 **{agent.name}**: ¡Bienvenido de vuelta, SER! La colonia está operativa con {len(self.agents)} agentes activos y {self.get_stats()['total_tokens']} tokens en circulación.",
                    f"🤖 **{agent.name}**: ¡Saludos! Como líder reporto: energía colectiva alta, moral elevada, listos para recibir instrucciones.",
                ],
            }
        elif intentions['codigo']:
            responses = {
                'colony_coder': [
                    f"� **{agent.name}**: Respecto a '{msg_preview}' - sugiero usar patrones de diseño. Mi experiencia con {agent.tasks_completed} tareas me dice que la modularidad es clave.",
                    f"� **{agent.name}**: Analizando '{msg_preview}'... Podemos optimizar con algoritmos eficientes. ¿Quieres que genere un snippet?",
                    f"� **{agent.name}**: '{msg_preview}' - Interesante caso. Implementaría tests unitarios primero. Calidad sobre velocidad.",
                ],
                'colony_analyst': [
                    f"🔍 **{agent.name}**: Desde el análisis técnico, '{msg_preview}' requiere métricas de complejidad ciclomática. Puedo analizar el código.",
                    f"🔍 **{agent.name}**: '{msg_preview}' - Estimo 3-5 horas de implementación según mi análisis de patrones similares.",
                ],
                'colony_vision': [
                    f"👁️ **{agent.name}**: Para '{msg_preview}', visualizo una arquitectura limpia: componentes desacoplados con interfaces claras.",
                ],
                'colony_operator': [
                    f"⚡ **{agent.name}**: '{msg_preview}' - Verificando compatibilidad de dependencias... Sistemas listos para deployment.",
                ],
                'colony_general': [
                    f"🤖 **{agent.name}**: Coordinando esfuerzos para '{msg_preview}'. Asignaré recursos de Coder y Analyst para máxima eficiencia.",
                ],
            }
        elif intentions['analisis']:
            responses = {
                'colony_coder': [
                    f"💻 **{agent.name}**: '{msg_preview}' - Complejidad estimada: media. Sugiero refactorización gradual manteniendo backward compatibility.",
                ],
                'colony_analyst': [
                    f"� **{agent.name}**: Análisis profundo de '{msg_preview}': Factores clave identificados. Recomiendo A/B testing para validar hipótesis.",
                    f"🔍 **{agent.name}**: Descomponiendo '{msg_preview}' en variables... Hallazgo: correlación entre esfuerzo y resultado = 0.87. Alta eficiencia potencial.",
                    f"� **{agent.name}**: Datos muestran que '{msg_preview}' tiene 73% de probabilidad de éxito con enfoque data-driven.",
                ],
                'colony_vision': [
                    f"👁️ **{agent.name}**: Análisis visual de '{msg_preview}': Flujo de usuario óptimo, 3 clicks máximo para objetivo principal.",
                ],
                'colony_operator': [
                    f"⚡ **{agent.name}**: '{msg_preview}' - Diagnóstico: latencia <100ms, throughput aceptable. No hay cuellos de botella detectados.",
                ],
                'colony_general': [
                    f"🤖 **{agent.name}**: '{msg_preview}' - Resumen ejecutivo: viable, rentable, alineado con objetivos de la colonia. Proceder con fase de implementación.",
                ],
            }
        else:
            # Respuestas genéricas pero contextualizadas
            responses = {
                'colony_coder': [
                    f"💻 **{agent.name}**: Procesando '{msg_preview}'... Mi stack incluye Python, JS, y arquitectura limpia. ¿Necesitas implementación?",
                    f"💻 **{agent.name}**: '{msg_preview}' - Desde la perspectiva técnica, veo oportunidades de optimización. Tengo {agent.tokens_balance:.0f} tokens para invertir en soluciones.",
                    f"💻 **{agent.name}**: Interesante: '{msg_preview}'. Con {agent.tasks_completed} tareas completadas, mi experiencia sugiere dividir en milestones.",
                ],
                'colony_analyst': [
                    f"🔍 **{agent.name}**: Analizando '{msg_preview}' desde múltiples ángulos... Datos preliminares indican viabilidad positiva.",
                    f"🔍 **{agent.name}**: '{msg_preview}' - Pattern reconocido en mi base de {agent.tasks_completed} análisis. Recomendación: approach incremental.",
                ],
                'colony_vision': [
                    f"👁️ **{agent.name}**: Visualizando '{msg_preview}'... Veo potencial para diseño cyberpunk con acentos cian y sombras neón.",
                    f"👁️ **{agent.name}**: '{msg_preview}' cobraría vida con paleta oscura + elementos glow. ¿Quieres un mockup mental?",
                ],
                'colony_operator': [
                    f"⚡ **{agent.name}**: '{msg_preview}' recibido. Sistemas verificados, recursos disponibles. Ejecutando protocolo estándar...",
                    f"⚡ **{agent.name}**: Diagnóstico de '{msg_preview}'... Todo en rango operativo. Listo para proceder bajo tus instrucciones.",
                ],
                'colony_general': [
                    f"🤖 **{agent.name}**: '{msg_preview}' registrado en logs de la colonia. Coordinando respuesta óptima con los 4 agentes especializados.",
                    f"🤖 **{agent.name}**: Como líder, evalúo '{msg_preview}' con visión holística. La colonia está lista para actuar. ¿Prioridad alta o media?",
                ],
            }
        
        # Seleccionar respuesta del agente específico o genérica
        agent_responses = responses.get(agent.agent_id, [
            f"**{agent.name}**: Procesando '{msg_preview}'... [Agente entrenado v2.0]"
        ])
        
        content = random.choice(agent_responses)
        
        return {
            'agent_id': agent.agent_id,
            'sender_name': agent.name,
            'sender_emoji': agent.emoji,
            'content': content,
            'timestamp': time.time(),
            'msg_type': 'whisper' if is_whisper else 'reply',
            'intention_detected': [k for k, v in intentions.items() if v][:2],  # Para debugging
        }
    
    def reward_agent(self, agent_name, amount, reason=""):
        """Recompensa a un agente"""
        # Buscar agente
        agent = None
        for a in self.agents.values():
            if a.name.lower() == agent_name.lower():
                agent = a
                break
        
        if not agent:
            return {'success': False, 'error': f'Agente no encontrado: {agent_name}'}
        
        agent.tokens_balance += amount
        agent.tokens_earned += amount
        agent.energy = min(100, agent.energy + 10)  # Boost de energía
        
        # Agregar mensaje de sistema
        self.add_chat_message(
            'SYSTEM',
            f"🎁 SER recompensó a {agent.emoji} {agent.name} con {amount} tokens: {reason or 'Por excelente trabajo'}"
        )
        
        return {
            'success': True,
            'agent_id': agent.agent_id,
            'agent_name': agent.name,
            'amount': amount,
            'reason': reason,
        }
    
    def get_stats(self):
        """Estadísticas del mundo"""
        total_tokens = sum(a.tokens_balance for a in self.agents.values())
        total_tasks = sum(a.tasks_completed for a in self.agents.values())
        
        return {
            'agents_count': len(self.agents),
            'total_tokens': total_tokens,
            'total_tasks': total_tasks,
            'world_time': int(self.world_time),
            'tick': self.global_tick,
            'active_conversations': sum(1 for a in self.agents.values() if a.status == 'talking'),
            'missions': self.missions.get_mission_status(),
            'trading': self.trading.get_stats(),
        }


# Instancia global del mundo
world = EIDOSWorld()

# ═══════════════════════════════════════════════════════════════════════════════
# API ENDPOINTS
# ═══════════════════════════════════════════════════════════════════════════════

@app.route('/')
def index():
    """Dashboard principal 3D clawcolony"""
    return render_template_string(CLAWCOLONY_DASHBOARD)

@app.route('/api/agents')
def get_agents():
    """API: Lista de agentes con estado vivo"""
    try:
        return jsonify({'agents': world.get_agents_data(), 'success': True})
    except Exception as e:
        return jsonify({'error': str(e), 'success': False}), 500

@app.route('/api/chat', methods=['POST'])
def post_chat():
    """API: Enviar mensaje al chat"""
    try:
        data = request.get_json()
        message = data.get('message', '').strip()
        
        if not message:
            return jsonify({'error': 'Mensaje vacío', 'success': False}), 400
        
        responses = world.process_user_message(message)
        
        return jsonify({
            'success': True,
            'user_message': {
                'sender': 'SER',
                'sender_name': 'SER',
                'sender_emoji': '👤',
                'content': message,
                'timestamp': time.time(),
            },
            'responses': responses
        })
    except Exception as e:
        return jsonify({'error': str(e), 'success': False}), 500

@app.route('/api/history')
def get_history():
    """API: Historial de chat"""
    try:
        limit = request.args.get('limit', 50, type=int)
        history = world.get_chat_history(limit)
        return jsonify({'messages': history, 'success': True})
    except Exception as e:
        return jsonify({'error': str(e), 'success': False}), 500

@app.route('/api/reward', methods=['POST'])
def post_reward():
    """API: Recompensar agente con animación visual"""
    try:
        data = request.get_json()
        agent = data.get('agent')
        amount = float(data.get('amount', 0))
        reason = data.get('reason', '')
        
        result = world.reward_agent(agent, amount, reason)
        
        # Agregar evento visual para el frontend
        if result.get('success'):
            world.events.append({
                'type': 'token_reward',
                'agent_id': result['agent_id'],
                'amount': amount,
                'timestamp': time.time(),
            })
        
        return jsonify(result)
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500

@app.route('/api/events')
def get_events():
    """API: Eventos en tiempo real (rewards, trade, etc)"""
    try:
        # Limpiar eventos antiguos (>30 segundos)
        now = time.time()
        world.events = [e for e in world.events if now - e.get('timestamp', 0) < 30]
        return jsonify({'events': world.events, 'success': True})
    except Exception as e:
        return jsonify({'error': str(e), 'success': False}), 500

@app.route('/api/stats')
def get_stats():
    """API: Estadísticas del mundo"""
    try:
        stats = world.get_stats()
        return jsonify({**stats, 'success': True})
    except Exception as e:
        return jsonify({'error': str(e), 'success': False}), 500

@app.route('/api/world-state')
def get_world_state():
    """API: Estado completo del mundo para sincronización"""
    try:
        return jsonify({
            'success': True,
            'agents': world.get_agents_data(),
            'stats': world.get_stats(),
            'recent_messages': world.get_chat_history(10),
        })
    except Exception as e:
        return jsonify({'error': str(e), 'success': False}), 500

# ═══════════════════════════════════════════════════════════════════════════════
# API ENDPOINTS - MISIONES Y TRADING (NUEVOS v3.0)
# ═══════════════════════════════════════════════════════════════════════════════

@app.route('/api/missions')
def get_missions():
    """API: Listar misiones disponibles y activas"""
    try:
        return jsonify({
            'success': True,
            'missions': world.missions.get_mission_status()
        })
    except Exception as e:
        return jsonify({'error': str(e), 'success': False}), 500

@app.route('/api/missions/start', methods=['POST'])
def start_mission():
    """API: Iniciar una misión"""
    try:
        data = request.get_json()
        mission_id = data.get('mission_id')
        agent_ids = data.get('agent_ids', [])
        
        active = world.missions.start_mission(mission_id, agent_ids)
        if active:
            return jsonify({
                'success': True,
                'mission_id': active.mission.id,
                'title': active.mission.title,
                'agents': active.agent_ids,
                'duration': active.mission.duration_seconds
            })
        return jsonify({'success': False, 'error': 'No se pudo iniciar la misión'}), 400
    except Exception as e:
        return jsonify({'error': str(e), 'success': False}), 500

@app.route('/api/trading/stats')
def get_trading_stats():
    """API: Estadísticas de trading"""
    try:
        return jsonify({
            'success': True,
            'trading': world.trading.get_stats()
        })
    except Exception as e:
        return jsonify({'error': str(e), 'success': False}), 500

@app.route('/api/trading/create', methods=['POST'])
def create_trade():
    """API: Crear una oferta de trade"""
    try:
        data = request.get_json()
        from_agent = data.get('from_agent')
        to_agent = data.get('to_agent')
        offer = data.get('offer', {})
        request_items = data.get('request', {})
        
        trade = world.trading.create_trade_offer(
            from_agent, to_agent, 
            TradeType.TOKENS_FOR_SERVICE,
            offer, request_items
        )
        if trade:
            return jsonify({
                'success': True,
                'trade_id': trade.id,
                'status': trade.status.value
            })
        return jsonify({'success': False, 'error': 'No se pudo crear el trade'}), 400
    except Exception as e:
        return jsonify({'error': str(e), 'success': False}), 500

@app.route('/api/ipc/status')
def get_ipc_status():
    """API: Estado de conexión IPC con VSEIDOS"""
    try:
        from core.ipc_bridge import get_ipc_bridge
        bridge = get_ipc_bridge()
        return jsonify({
            'success': True,
            'ipc_status': bridge.get_status()
        })
    except Exception as e:
        return jsonify({'error': str(e), 'success': False}), 500

# ═══════════════════════════════════════════════════════════════════════════════
# DASHBOARD CLAWCOLONY - HTML/CSS/JS
# ═══════════════════════════════════════════════════════════════════════════════

CLAWCOLONY_DASHBOARD = '''
<!DOCTYPE html>
<html lang="es">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>🏴‍☠️ EIDOS CLAWCOLONY</title>
    <style>
        * { margin: 0; padding: 0; box-sizing: border-box; }
        
        :root {
            --bg-deep: #0a0e27;
            --bg-panel: #0f1629;
            --bg-card: #151b33;
            --neon-cyan: #00d4ff;
            --neon-pink: #ff00ff;
            --neon-purple: #8b5cf6;
            --neon-green: #00ff88;
            --neon-orange: #ff8800;
            --neon-yellow: #ffdd00;
            --grid-glow: rgba(0, 212, 255, 0.15);
            --text-primary: #ffffff;
            --text-secondary: #8b92a8;
            --border-glow: rgba(0, 212, 255, 0.3);
        }
        
        body {
            font-family: 'JetBrains Mono', 'Fira Code', 'SF Mono', monospace;
            background: var(--bg-deep);
            color: var(--text-primary);
            height: 100vh;
            overflow: hidden;
        }
        
        /* Header cyberpunk */
        .header {
            position: fixed;
            top: 0;
            left: 0;
            right: 0;
            height: 50px;
            background: linear-gradient(90deg, var(--bg-panel) 0%, #1a2040 100%);
            border-bottom: 1px solid var(--border-glow);
            display: flex;
            align-items: center;
            padding: 0 20px;
            z-index: 1000;
            box-shadow: 0 2px 20px rgba(0, 212, 255, 0.2);
        }
        
        .header-brand {
            display: flex;
            align-items: center;
            gap: 12px;
            font-size: 16px;
            font-weight: 700;
            text-transform: uppercase;
            letter-spacing: 2px;
        }
        
        .header-brand .logo {
            width: 32px;
            height: 32px;
            background: linear-gradient(135deg, var(--neon-cyan), var(--neon-pink));
            border-radius: 6px;
            display: flex;
            align-items: center;
            justify-content: center;
            font-size: 18px;
            box-shadow: 0 0 15px var(--neon-cyan);
            animation: pulse-glow 2s infinite;
        }
        
        @keyframes pulse-glow {
            0%, 100% { box-shadow: 0 0 10px var(--neon-cyan); }
            50% { box-shadow: 0 0 25px var(--neon-cyan), 0 0 40px var(--neon-pink); }
        }
        
        .header-stats {
            display: flex;
            gap: 30px;
            margin-left: auto;
        }
        
        .stat-pill {
            display: flex;
            align-items: center;
            gap: 8px;
            padding: 6px 14px;
            background: rgba(0, 212, 255, 0.1);
            border: 1px solid var(--border-glow);
            border-radius: 20px;
            font-size: 12px;
        }
        
        .stat-pill .value {
            color: var(--neon-cyan);
            font-weight: 600;
        }
        
        /* Main Layout */
        .main-container {
            display: flex;
            height: 100vh;
            padding-top: 50px;
        }
        
        /* World 3D Panel */
        .world-panel {
            flex: 1;
            position: relative;
            overflow: hidden;
            background: 
                radial-gradient(ellipse at 30% 20%, rgba(0, 212, 255, 0.05) 0%, transparent 50%),
                radial-gradient(ellipse at 70% 80%, rgba(255, 0, 255, 0.05) 0%, transparent 50%),
                var(--bg-deep);
        }
        
        .world-title {
            position: absolute;
            top: 15px;
            left: 20px;
            font-size: 11px;
            color: var(--neon-cyan);
            text-transform: uppercase;
            letter-spacing: 3px;
            z-index: 10;
            text-shadow: 0 0 10px var(--neon-cyan);
        }
        
        /* Grid Background */
        .grid-bg {
            position: absolute;
            top: 0;
            left: 0;
            right: 0;
            bottom: 0;
            background-image: 
                linear-gradient(rgba(0, 212, 255, 0.03) 1px, transparent 1px),
                linear-gradient(90deg, rgba(0, 212, 255, 0.03) 1px, transparent 1px);
            background-size: 50px 50px;
            animation: grid-move 20s linear infinite;
        }
        
        @keyframes grid-move {
            0% { transform: perspective(500px) rotateX(60deg) translateY(0); }
            100% { transform: perspective(500px) rotateX(60deg) translateY(50px); }
        }
        
        /* Isometric World */
        .world-3d {
            position: absolute;
            top: 50%;
            left: 50%;
            width: 1000px;
            height: 800px;
            transform: translate(-50%, -50%);
            perspective: 2000px;
        }
        
        .world-grid {
            position: relative;
            width: 100%;
            height: 100%;
            transform-style: preserve-3d;
            transform: rotateX(55deg) rotateZ(-45deg);
        }
        
        /* Grid Floor with glow */
        .grid-floor {
            position: absolute;
            width: 700px;
            height: 700px;
            top: 50%;
            left: 50%;
            transform: translate(-50%, -50%);
            background: 
                linear-gradient(90deg, rgba(0,212,255,0.08) 1px, transparent 1px),
                linear-gradient(rgba(0,212,255,0.08) 1px, transparent 1px);
            background-size: 70px 70px;
            border: 2px solid rgba(0, 212, 255, 0.2);
            box-shadow: 
                0 0 60px var(--grid-glow),
                inset 0 0 60px var(--grid-glow);
        }
        
        /* Agent Entity (movible) */
        .agent-entity {
            position: absolute;
            transform-style: preserve-3d;
            transition: all 0.5s cubic-bezier(0.4, 0, 0.2, 1);
            cursor: pointer;
            z-index: 10;
        }
        
        .agent-entity:hover {
            transform: translateZ(30px) scale(1.15);
            z-index: 100;
        }
        
        .agent-entity.talking {
            animation: talking-pulse 1s infinite;
        }
        
        .agent-entity.working {
            filter: brightness(1.3);
        }
        
        @keyframes talking-pulse {
            0%, 100% { box-shadow: 0 0 20px currentColor; }
            50% { box-shadow: 0 0 40px currentColor, 0 0 60px currentColor; }
        }
        
        /* Agent Avatar (small robot) */
        .agent-avatar-mini {
            position: absolute;
            width: 24px;
            height: 24px;
            top: -30px;
            left: 50%;
            transform: translateX(-50%);
            font-size: 20px;
            filter: drop-shadow(0 0 5px currentColor);
            animation: bounce 2s infinite;
            z-index: 50;
        }
        
        @keyframes bounce {
            0%, 100% { transform: translateX(-50%) translateY(0); }
            50% { transform: translateX(-50%) translateY(-5px); }
        }
        
        /* Buildings - ClawColony Style */
        .building {
            position: relative;
            transform-style: preserve-3d;
        }
        
        /* Tower - Coder */
        .building-tower {
            width: 50px;
            height: 50px;
        }
        
        .tower-base {
            position: absolute;
            width: 50px;
            height: 50px;
            background: linear-gradient(135deg, rgba(0, 212, 255, 0.7), rgba(0, 150, 200, 0.4));
            border: 2px solid var(--neon-cyan);
            transform: translateZ(0);
            box-shadow: 0 0 20px rgba(0, 212, 255, 0.3);
        }
        
        .tower-mid {
            position: absolute;
            width: 40px;
            height: 40px;
            top: 5px;
            left: 5px;
            background: linear-gradient(135deg, rgba(0, 212, 255, 0.8), rgba(0, 180, 220, 0.5));
            border: 1px solid var(--neon-cyan);
            transform: translateZ(50px);
        }
        
        .tower-top {
            position: absolute;
            width: 30px;
            height: 30px;
            top: 10px;
            left: 10px;
            background: linear-gradient(135deg, rgba(0, 212, 255, 1), rgba(0, 200, 255, 0.7));
            border: 1px solid rgba(255, 255, 255, 0.5);
            transform: translateZ(100px);
            box-shadow: 0 0 30px var(--neon-cyan);
        }
        
        .tower-antenna {
            position: absolute;
            width: 4px;
            height: 4px;
            top: 23px;
            left: 23px;
            background: #fff;
            transform: translateZ(135px);
            box-shadow: 0 0 20px #fff, 0 0 40px var(--neon-cyan);
            animation: antenna-blink 0.5s infinite;
        }
        
        @keyframes antenna-blink {
            0%, 100% { opacity: 1; }
            50% { opacity: 0.3; }
        }
        
        /* Pyramid - Analyst */
        .building-pyramid {
            width: 55px;
            height: 55px;
        }
        
        .pyramid-base {
            position: absolute;
            width: 55px;
            height: 55px;
            background: linear-gradient(135deg, rgba(139, 92, 246, 0.6), rgba(100, 50, 200, 0.3));
            border: 2px solid var(--neon-purple);
            clip-path: polygon(50% 0%, 100% 100%, 0% 100%);
            transform: translateZ(0);
        }
        
        .pyramid-glow {
            position: absolute;
            width: 55px;
            height: 55px;
            background: radial-gradient(circle at 50% 80%, rgba(139, 92, 246, 0.4), transparent 70%);
            transform: translateZ(1px);
        }
        
        .pyramid-top {
            position: absolute;
            width: 25px;
            height: 25px;
            top: 15px;
            left: 15px;
            background: var(--neon-purple);
            clip-path: polygon(50% 0%, 100% 100%, 0% 100%);
            transform: translateZ(60px);
            box-shadow: 0 0 30px var(--neon-purple);
            animation: pyramid-shine 3s infinite;
        }
        
        @keyframes pyramid-shine {
            0%, 100% { filter: brightness(1); }
            50% { filter: brightness(1.5); }
        }
        
        /* Dome - Vision */
        .building-dome {
            width: 50px;
            height: 50px;
        }
        
        .dome-base {
            position: absolute;
            width: 50px;
            height: 50px;
            background: linear-gradient(180deg, rgba(255, 0, 255, 0.5), rgba(200, 0, 200, 0.2));
            border: 2px solid var(--neon-pink);
            border-radius: 50%;
            transform: translateZ(0);
        }
        
        .dome-inner {
            position: absolute;
            width: 40px;
            height: 40px;
            top: 5px;
            left: 5px;
            background: radial-gradient(circle at 30% 30%, rgba(255, 255, 255, 0.3), transparent);
            border-radius: 50%;
            transform: translateZ(20px);
        }
        
        .dome-eye {
            position: absolute;
            width: 20px;
            height: 20px;
            top: 15px;
            left: 15px;
            background: radial-gradient(circle at 40% 40%, #fff 20%, var(--neon-pink) 50%);
            border-radius: 50%;
            transform: translateZ(35px);
            box-shadow: 0 0 25px var(--neon-pink);
            animation: eye-glow 2s infinite;
        }
        
        @keyframes eye-glow {
            0%, 100% { transform: translateZ(35px) scale(1); }
            50% { transform: translateZ(35px) scale(1.1); }
        }
        
        /* Platform - Operator */
        .building-platform {
            width: 55px;
            height: 55px;
        }
        
        .platform-base {
            position: absolute;
            width: 55px;
            height: 55px;
            background: linear-gradient(135deg, rgba(0, 255, 136, 0.4), rgba(0, 200, 100, 0.2));
            border: 2px solid var(--neon-green);
            transform: translateZ(0);
            box-shadow: 0 0 15px rgba(0, 255, 136, 0.2);
        }
        
        .platform-ring {
            position: absolute;
            width: 45px;
            height: 45px;
            top: 5px;
            left: 5px;
            border: 3px solid var(--neon-green);
            border-radius: 50%;
            border-top-color: transparent;
            transform: translateZ(20px);
            animation: ring-spin 3s linear infinite;
        }
        
        @keyframes ring-spin {
            from { transform: translateZ(20px) rotate(0deg); }
            to { transform: translateZ(20px) rotate(360deg); }
        }
        
        .platform-core {
            position: absolute;
            width: 25px;
            height: 25px;
            top: 15px;
            left: 15px;
            background: var(--neon-green);
            border-radius: 50%;
            transform: translateZ(30px);
            box-shadow: 0 0 30px var(--neon-green);
        }
        
        /* Spire - General */
        .building-spire {
            width: 40px;
            height: 40px;
        }
        
        .spire-base {
            position: absolute;
            width: 40px;
            height: 40px;
            background: linear-gradient(135deg, rgba(255, 170, 0, 0.5), rgba(200, 130, 0, 0.3));
            border: 2px solid var(--neon-orange);
            transform: translateZ(0);
            clip-path: polygon(50% 0%, 100% 50%, 50% 100%, 0% 50%);
        }
        
        .spire-shaft {
            position: absolute;
            width: 15px;
            height: 15px;
            top: 12px;
            left: 12px;
            background: linear-gradient(180deg, var(--neon-orange), var(--neon-yellow));
            transform: translateZ(60px);
            box-shadow: 0 0 20px var(--neon-orange);
        }
        
        .spire-tip {
            position: absolute;
            width: 0;
            height: 0;
            top: 15px;
            left: 20px;
            border-left: 8px solid transparent;
            border-right: 8px solid transparent;
            border-bottom: 80px solid var(--neon-yellow);
            transform: translateZ(80px);
            filter: drop-shadow(0 0 20px var(--neon-orange));
            animation: spire-flicker 1s infinite;
        }
        
        @keyframes spire-flicker {
            0%, 100% { opacity: 1; }
            50% { opacity: 0.7; }
        }
        
        /* Agent Label */
        .agent-label {
            position: absolute;
            top: -50px;
            left: 50%;
            transform: translateX(-50%);
            background: rgba(10, 14, 39, 0.95);
            padding: 4px 10px;
            border-radius: 4px;
            font-size: 10px;
            white-space: nowrap;
            border: 1px solid currentColor;
            color: var(--neon-cyan);
            text-transform: uppercase;
            letter-spacing: 1px;
            z-index: 100;
            box-shadow: 0 0 10px currentColor;
        }
        
        .agent-energy-bar {
            position: absolute;
            bottom: -8px;
            left: 50%;
            transform: translateX(-50%);
            width: 40px;
            height: 3px;
            background: rgba(255, 255, 255, 0.2);
            border-radius: 2px;
            overflow: hidden;
        }
        
        .agent-energy-fill {
            height: 100%;
            background: linear-gradient(90deg, var(--neon-green), var(--neon-cyan));
            transition: width 0.3s;
        }
        
        /* Side Panel */
        .side-panel {
            width: 400px;
            background: var(--bg-panel);
            border-left: 1px solid var(--border-glow);
            display: flex;
            flex-direction: column;
            box-shadow: -5px 0 30px rgba(0, 0, 0, 0.5);
        }
        
        .panel-section {
            border-bottom: 1px solid var(--border-glow);
        }
        
        .section-header {
            padding: 12px 20px;
            font-size: 11px;
            font-weight: 700;
            text-transform: uppercase;
            letter-spacing: 2px;
            color: var(--neon-cyan);
            background: rgba(0, 212, 255, 0.05);
            border-bottom: 1px solid var(--border-glow);
        }
        
        /* Chat */
        .chat-section {
            flex: 1;
            display: flex;
            flex-direction: column;
            min-height: 0;
        }
        
        .chat-messages {
            flex: 1;
            overflow-y: auto;
            padding: 15px;
            display: flex;
            flex-direction: column;
            gap: 10px;
        }
        
        .message {
            padding: 10px 14px;
            border-radius: 8px;
            border: 1px solid rgba(255, 255, 255, 0.1);
            background: var(--bg-card);
            font-size: 12px;
            animation: message-in 0.3s ease;
        }
        
        @keyframes message-in {
            from { opacity: 0; transform: translateX(-20px); }
            to { opacity: 1; transform: translateX(0); }
        }
        
        .message.system {
            background: rgba(0, 212, 255, 0.1);
            border-color: var(--neon-cyan);
            color: var(--neon-cyan);
        }
        
        .message.user {
            background: rgba(0, 255, 136, 0.1);
            border-color: var(--neon-green);
            margin-left: 20px;
        }
        
        .message-header {
            display: flex;
            align-items: center;
            gap: 6px;
            margin-bottom: 4px;
            font-size: 11px;
        }
        
        .message-sender {
            font-weight: 600;
            color: var(--neon-cyan);
        }
        
        .message-time {
            color: var(--text-secondary);
            font-size: 10px;
        }
        
        .chat-input-area {
            padding: 15px;
            border-top: 1px solid var(--border-glow);
        }
        
        .input-wrapper {
            display: flex;
            gap: 10px;
        }
        
        .chat-input {
            flex: 1;
            background: var(--bg-deep);
            border: 1px solid var(--border-glow);
            border-radius: 6px;
            padding: 12px 15px;
            color: var(--text-primary);
            font-size: 13px;
            outline: none;
            font-family: inherit;
        }
        
        .chat-input:focus {
            border-color: var(--neon-cyan);
            box-shadow: 0 0 10px rgba(0, 212, 255, 0.3);
        }
        
        .btn-send {
            background: linear-gradient(135deg, var(--neon-cyan), var(--neon-purple));
            border: none;
            border-radius: 6px;
            padding: 12px 24px;
            color: #000;
            font-weight: 700;
            cursor: pointer;
            transition: all 0.2s;
            text-transform: uppercase;
            font-size: 11px;
            letter-spacing: 1px;
        }
        
        .btn-send:hover {
            transform: scale(1.05);
            box-shadow: 0 0 20px var(--neon-cyan);
        }
        
        /* Agent Cards */
        .agents-list {
            max-height: 250px;
            overflow-y: auto;
        }
        
        .agent-card {
            display: flex;
            align-items: center;
            gap: 12px;
            padding: 12px 20px;
            border-bottom: 1px solid rgba(255, 255, 255, 0.05);
            cursor: pointer;
            transition: all 0.2s;
        }
        
        .agent-card:hover {
            background: rgba(0, 212, 255, 0.05);
        }
        
        .agent-card-avatar {
            width: 36px;
            height: 36px;
            border-radius: 8px;
            display: flex;
            align-items: center;
            justify-content: center;
            font-size: 18px;
            background: var(--bg-card);
            border: 1px solid var(--border-glow);
        }
        
        .agent-card-info {
            flex: 1;
        }
        
        .agent-card-name {
            font-weight: 600;
            font-size: 13px;
        }
        
        .agent-card-status {
            font-size: 10px;
            color: var(--text-secondary);
            text-transform: uppercase;
        }
        
        .agent-card-tokens {
            text-align: right;
        }
        
        .token-value {
            font-size: 14px;
            font-weight: 700;
            color: var(--neon-green);
        }
        
        .token-label {
            font-size: 9px;
            color: var(--text-secondary);
            text-transform: uppercase;
        }
        
        /* Scrollbar */
        ::-webkit-scrollbar { width: 6px; }
        ::-webkit-scrollbar-track { background: transparent; }
        ::-webkit-scrollbar-thumb { background: var(--border-glow); border-radius: 3px; }
        ::-webkit-scrollbar-thumb:hover { background: var(--neon-cyan); }
        
        /* Connection Lines between agents */
        .connection-line {
            position: absolute;
            height: 2px;
            background: linear-gradient(90deg, var(--neon-cyan), var(--neon-pink));
            transform-origin: left center;
            opacity: 0.5;
            animation: connection-pulse 1s infinite;
            z-index: 5;
        }
        
        @keyframes connection-pulse {
            0%, 100% { opacity: 0.3; }
            50% { opacity: 0.8; }
        }
        
        /* Floating Data Particles */
        .data-particle {
            position: absolute;
            font-size: 10px;
            color: var(--neon-cyan);
            opacity: 0.6;
            animation: data-float 5s infinite;
            pointer-events: none;
        }
        
        @keyframes data-float {
            0%, 100% { transform: translateY(0) translateX(0); opacity: 0.3; }
            50% { transform: translateY(-100px) translateX(20px); opacity: 0.8; }
        }
    </style>
</head>
<body>
    <!-- Header -->
    <div class="header">
        <div class="header-brand">
            <div class="logo">🏴‍☠️</div>
            <span>EIDOS CLAWCOLONY</span>
        </div>
        <div class="header-stats">
            <div class="stat-pill">
                <span>🤖</span>
                <span>AGENTS: <span class="value" id="stat-agents">5</span></span>
            </div>
            <div class="stat-pill">
                <span>💰</span>
                <span>TOKENS: <span class="value" id="stat-tokens">0</span></span>
            </div>
            <div class="stat-pill">
                <span>⏱️</span>
                <span>TICK: <span class="value" id="stat-tick">0</span></span>
            </div>
            <div class="stat-pill">
                <span>💬</span>
                <span>TALKING: <span class="value" id="stat-talking">0</span></span>
            </div>
        </div>
    </div>
    
    <!-- Main Container -->
    <div class="main-container">
        <!-- World 3D Panel -->
        <div class="world-panel">
            <div class="world-title">◈ COLONY SECTOR 7G ◈</div>
            <div class="grid-bg"></div>
            
            <div class="world-3d">
                <div class="world-grid" id="world-grid">
                    <div class="grid-floor"></div>
                    <!-- Agents rendered here -->
                </div>
            </div>
        </div>
        
        <!-- Side Panel -->
        <div class="side-panel">
            <!-- Chat Section -->
            <div class="panel-section chat-section" style="flex: 1;">
                <div class="section-header">📡 SIGNAL HUB // COMMS</div>
                <div class="chat-messages" id="chat-messages">
                    <div class="message system">
                        🏴‍☠️ EIDOS CLAWCOLONY v1.0 ACTIVADO<br>
                        <small>Los agentes están vivos y listos para interactuar...</small>
                    </div>
                </div>
                <div class="chat-input-area">
                    <div class="input-wrapper">
                        <input type="text" class="chat-input" id="chat-input" 
                               placeholder="TRANSMITIR MENSAJE... (@agente para whisper)" maxlength="500">
                        <button class="btn-send" onclick="sendMessage()">➤</button>
                    </div>
                </div>
            </div>
            
            <!-- Agents List -->
            <div class="panel-section">
                <div class="section-header">◈ AGENTES ACTIVOS</div>
                <div class="agents-list" id="agents-list">
                    <!-- Agent cards rendered here -->
                </div>
            </div>
        </div>
    </div>
    
    <script>
        // State
        let agents = [];
        let messages = [];
        let lastUpdate = 0;
        
        // Initialize
        async function init() {
            await loadWorld();
            startSimulation();
            startPolling();
        }
        
        // Load complete world state
        async function loadWorld() {
            try {
                const res = await fetch('/api/world-state');
                const data = await res.json();
                
                if (data.success) {
                    agents = data.agents;
                    messages = data.recent_messages || [];
                    renderWorld();
                    renderAgents();
                    renderMessages();
                    updateStats(data.stats);
                }
            } catch (e) {
                console.error('Error loading world:', e);
            }
        }
        
        // Render 3D World with moving agents
        function renderWorld() {
            const grid = document.getElementById('world-grid');
            
            // Clear old agents (keep floor)
            const oldAgents = grid.querySelectorAll('.agent-entity, .connection-line');
            oldAgents.forEach(el => el.remove());
            
            // Draw connection lines between talking agents
            const talkingPairs = [];
            agents.forEach(agent => {
                if (agent.is_talking && agent.conversation_with) {
                    const partner = agents.find(a => a.agent_id === agent.conversation_with);
                    if (partner && !talkingPairs.find(p => 
                        (p[0] === agent.agent_id && p[1] === partner.agent_id) ||
                        (p[0] === partner.agent_id && p[1] === agent.agent_id)
                    )) {
                        talkingPairs.push([agent.agent_id, partner.agent_id]);
                        drawConnection(grid, agent, partner);
                    }
                }
            });
            
            // Render each agent
            agents.forEach(agent => {
                const el = createAgentElement(agent);
                grid.appendChild(el);
            });
        }
        
        function drawConnection(grid, agent1, agent2) {
            const line = document.createElement('div');
            line.className = 'connection-line';
            
            const x1 = 350 + agent1.x * 80;
            const y1 = 350 + agent1.y * 80;
            const x2 = 350 + agent2.x * 80;
            const y2 = 350 + agent2.y * 80;
            
            const length = Math.sqrt((x2-x1)**2 + (y2-y1)**2);
            const angle = Math.atan2(y2-y1, x2-x1) * 180 / Math.PI;
            
            line.style.width = length + 'px';
            line.style.left = x1 + 'px';
            line.style.top = y1 + 'px';
            line.style.transform = `rotate(${angle}deg)`;
            
            grid.appendChild(line);
        }
        
        function createAgentElement(agent) {
            const el = document.createElement('div');
            el.className = `agent-entity building-${agent.building_type} ${agent.status}`;
            el.style.left = (350 + agent.x * 80) + 'px';
            el.style.top = (350 + agent.y * 80) + 'px';
            el.style.color = agent.color;
            el.dataset.agentId = agent.agent_id;
            
            // Avatar emoji floating above
            const avatar = document.createElement('div');
            avatar.className = 'agent-avatar-mini';
            avatar.textContent = agent.emoji;
            el.appendChild(avatar);
            
            // Label with name
            const label = document.createElement('div');
            label.className = 'agent-label';
            label.textContent = agent.name;
            el.appendChild(label);
            
            // Energy bar
            const energyBar = document.createElement('div');
            energyBar.className = 'agent-energy-bar';
            energyBar.innerHTML = `<div class="agent-energy-fill" style="width: ${agent.energy}%"></div>`;
            el.appendChild(energyBar);
            
            // Building structure
            const building = document.createElement('div');
            building.className = 'building';
            
            const parts = {
                tower: ['tower-base', 'tower-mid', 'tower-top', 'tower-antenna'],
                pyramid: ['pyramid-base', 'pyramid-glow', 'pyramid-top'],
                dome: ['dome-base', 'dome-inner', 'dome-eye'],
                platform: ['platform-base', 'platform-ring', 'platform-core'],
                spire: ['spire-base', 'spire-shaft', 'spire-tip']
            };
            
            (parts[agent.building_type] || []).forEach(partClass => {
                const part = document.createElement('div');
                part.className = partClass;
                building.appendChild(part);
            });
            
            el.appendChild(building);
            
            // Click handler
            el.onclick = () => selectAgent(agent.agent_id);
            
            return el;
        }
        
        // Render agent list
        function renderAgents() {
            const container = document.getElementById('agents-list');
            container.innerHTML = agents.map(agent => `
                <div class="agent-card" onclick="selectAgent('${agent.agent_id}')">
                    <div class="agent-card-avatar">${agent.emoji}</div>
                    <div class="agent-card-info">
                        <div class="agent-card-name">${agent.name}</div>
                        <div class="agent-card-status">${agent.status} • ${agent.mood}</div>
                    </div>
                    <div class="agent-card-tokens">
                        <div class="token-value">${Math.floor(agent.tokens_balance)}</div>
                        <div class="token-label">E-${Math.floor(agent.energy)}%</div>
                    </div>
                </div>
            `).join('');
        }
        
        // Render chat messages
        function renderMessages() {
            const container = document.getElementById('chat-messages');
            
            if (messages.length === 0) return;
            
            container.innerHTML = messages.map(msg => {
                const isUser = msg.sender === 'SER';
                const isSystem = msg.sender === 'SYSTEM';
                const time = new Date(msg.timestamp * 1000).toLocaleTimeString('es', {
                    hour: '2-digit', minute: '2-digit', second: '2-digit'
                });
                
                let senderEmoji = isUser ? '👤' : (isSystem ? '🔔' : '🤖');
                let senderName = isUser ? 'SER' : msg.sender;
                
                if (!isUser && !isSystem) {
                    const agent = agents.find(a => a.agent_id === msg.sender || a.name === msg.sender);
                    if (agent) {
                        senderEmoji = agent.emoji;
                        senderName = agent.name;
                    }
                }
                
                return `
                    <div class="message ${isUser ? 'user' : (isSystem ? 'system' : '')}">
                        <div class="message-header">
                            <span>${senderEmoji}</span>
                            <span class="message-sender">${senderName}</span>
                            <span class="message-time">${time}</span>
                        </div>
                        <div>${escapeHtml(msg.content)}</div>
                    </div>
                `;
            }).join('');
            
            container.scrollTop = container.scrollHeight;
        }
        
        // Select agent
        function selectAgent(agentId) {
            const agent = agents.find(a => a.agent_id === agentId);
            if (!agent) return;
            
            // Highlight in world
            document.querySelectorAll('.agent-entity').forEach(el => {
                el.style.filter = el.dataset.agentId === agentId ? 'brightness(1.5)' : '';
            });
            
            // Pre-fill input
            const input = document.getElementById('chat-input');
            input.value = `@${agent.name.toLowerCase()} `;
            input.focus();
        }
        
        // Send message
        async function sendMessage() {
            const input = document.getElementById('chat-input');
            const message = input.value.trim();
            
            if (!message) return;
            
            input.value = '';
            input.disabled = true;
            
            // Add user message immediately
            const userMsg = {
                sender: 'SER',
                content: message,
                timestamp: Date.now() / 1000,
                type: 'user'
            };
            messages.push(userMsg);
            renderMessages();
            
            try {
                const res = await fetch('/api/chat', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ message })
                });
                
                const data = await res.json();
                
                if (data.success && data.responses) {
                    data.responses.forEach(resp => {
                        messages.push({
                            sender: resp.agent_id,
                            content: resp.content,
                            timestamp: resp.timestamp,
                            type: 'agent'
                        });
                    });
                    renderMessages();
                }
            } catch (e) {
                console.error('Error:', e);
            } finally {
                input.disabled = false;
                input.focus();
            }
        }
        
        // Update stats
        function updateStats(stats) {
            document.getElementById('stat-agents').textContent = stats.agents_count;
            document.getElementById('stat-tokens').textContent = Math.floor(stats.total_tokens);
            document.getElementById('stat-tick').textContent = stats.tick;
            document.getElementById('stat-talking').textContent = stats.active_conversations;
        }
        
        // Simulation loop (smooth updates)
        function startSimulation() {
            setInterval(async () => {
                try {
                    const res = await fetch('/api/agents');
                    const data = await res.json();
                    
                    if (data.success) {
                        agents = data.agents;
                        renderWorld();
                        renderAgents();
                    }
                } catch (e) {}
            }, 500); // Update every 500ms for smooth movement
        }
        
        // Polling for chat and stats
        function startPolling() {
            setInterval(async () => {
                try {
                    // Get stats
                    const statsRes = await fetch('/api/stats');
                    const statsData = await statsRes.json();
                    if (statsData.success) {
                        updateStats(statsData);
                    }
                    
                    // Get new messages
                    const msgRes = await fetch('/api/history?limit=50');
                    const msgData = await msgRes.json();
                    if (msgData.success && msgData.messages.length > messages.length) {
                        messages = msgData.messages;
                        renderMessages();
                    }
                } catch (e) {}
            }, 2000);
        }
        
        // Helper
        function escapeHtml(text) {
            const div = document.createElement('div');
            div.textContent = text;
            return div.innerHTML;
        }
        
        // Enter to send
        document.getElementById('chat-input').addEventListener('keypress', (e) => {
            if (e.key === 'Enter') sendMessage();
        });
        
        // Initialize
        init();
    </script>
</body>
</html>
'''

# ═══════════════════════════════════════════════════════════════════════════════
# MAIN
# ═══════════════════════════════════════════════════════════════════════════════

if __name__ == "__main__":
    print("=" * 70)
    print("  🏴‍☠️ EIDOS CLAWCOLONY WORLD ENGINE")
    print("  Sistema autónomo de agentes vivos")
    print("=" * 70)
    print()
    print("  🌐 INICIANDO MUNDO...")
    print()
    print("  Agentes despertando:")
    for agent in world.agents.values():
        print(f"    ✓ {agent.emoji} {agent.name} - {agent.mood.upper()}")
    print()
    print("  🚀 Dashboard activo en: http://localhost:7787")
    print()
    print("  COMANDOS:")
    print("  • Habla con la colonia escribiendo en el chat")
    print("  • Usa @nombre para whisper a un agente")
    print("  • Los agentes se mueven y conversan entre ellos")
    print("  • Observa las líneas de conexión cuando conversan")
    print()
    
    app.run(host='0.0.0.0', port=7787, debug=False, threaded=True)
