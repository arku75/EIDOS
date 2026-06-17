"""
EIDOS core/colony_query_engine.py — Colony Query Engine
========================================================
Motor central que despacha CUALQUIER consulta a la colonia de agentes EIDOS.
Usa SmartRouter + TokenEconomy + Ganglia + Governance para seleccionar el
mejor agente/modelo y ejecutar la consulta de forma autónoma.

El objetivo final: EIDOS resuelve TODO con sus propios modelos locales Ollama,
eliminando dependencias externas (Claude, ChatGPT, etc.).

Pipeline:
  1. Analiza la query (SmartRouter 15 dimensiones)
  2. Verifica Tian Dao (Governance)
  3. Selecciona agente óptimo (Ganglia capability matching)
  4. Cobra tokens al agente (TokenEconomy)
  5. Ejecuta con modelo Ollama óptimo (ModelManager)
  6. Registra feedback para aprendizaje continuo
  7. Retorna resultado enriquecido

Uso:
    from core.colony_query_engine import get_colony_engine
    engine = get_colony_engine()
    result = engine.query("explain how TCP works")
    result = engine.query("write a python sort function", profile="premium")
"""
from __future__ import annotations

import json
import logging
import os
import sqlite3
import time
import threading
import hashlib
from dataclasses import dataclass, field
from typing import Any, Optional, Dict, List
from enum import Enum
from core.db import get_conn
from core.db import get_conn_ctx

log = logging.getLogger("eidos.colony_query_engine")

DB_PATH = os.path.expanduser("~/.eidos/colony_engine.db")

# ══════════════════════════════════════════════════════════════════════════════
#  IMPORTS CONDICIONALES — cada subsistema es opcional
# ══════════════════════════════════════════════════════════════════════════════

try:
    from core.smart_router import SmartRouter, RoutingResult
    HAS_ROUTER = True
except ImportError:
    HAS_ROUTER = False

try:
    from core.model_manager import get_model_manager, ModelManager, TaskType
    HAS_MODEL_MGR = True
except ImportError:
    HAS_MODEL_MGR = False

try:
    from core.token_economy import get_economy, TokenEconomy, REWARD_TABLE
    HAS_ECONOMY = True
except ImportError:
    HAS_ECONOMY = False

try:
    from core.ganglia import get_ganglia_manager, GangliaManager
    HAS_GANGLIA = True
except ImportError:
    HAS_GANGLIA = False

try:
    from core.governance import GovernanceSystem
    HAS_GOVERNANCE = True
except ImportError:
    HAS_GOVERNANCE = False

try:
    from core.knowledge_graph import get_knowledge_graph
    HAS_KG = True
except ImportError:
    HAS_KG = False

try:
    from core.brain_memory import get_brain_memory
    HAS_BRAIN = True
except ImportError:
    HAS_BRAIN = False


# ══════════════════════════════════════════════════════════════════════════════
#  TIPOS
# ══════════════════════════════════════════════════════════════════════════════

class QueryType(str, Enum):
    CHAT = "chat"
    CODE = "code"
    VISION = "vision"
    REASONING = "reasoning"
    TOOL_USE = "tool_use"
    EMBEDDING = "embedding"
    CREATIVE = "creative"


@dataclass
class ColonyQuery:
    """Una consulta entrante a la colonia."""
    text: str
    context: str = ""
    profile: str = "auto"           # eco, auto, premium
    requester: str = "user"         # quién pide (user, vseidos, agent_X)
    images: List[str] = field(default_factory=list)
    tools: List[dict] = field(default_factory=list)
    max_tokens: int = 2048
    temperature: float = 0.7
    system_prompt: str = ""
    metadata: Dict[str, Any] = field(default_factory=dict)


@dataclass
class ColonyResult:
    """Resultado completo de la colonia."""
    success: bool
    response: str
    model_used: str = ""
    agent_used: str = "colony_default"
    query_type: QueryType = QueryType.CHAT
    routing_confidence: float = 0.0
    tokens_spent: float = 0.0
    elapsed_s: float = 0.0
    ganglia_used: List[str] = field(default_factory=list)
    tian_dao_ok: bool = True
    metadata: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "success": self.success,
            "response": self.response,
            "model_used": self.model_used,
            "agent_used": self.agent_used,
            "query_type": self.query_type.value,
            "routing_confidence": round(self.routing_confidence, 3),
            "tokens_spent": round(self.tokens_spent, 1),
            "elapsed_s": round(self.elapsed_s, 2),
            "ganglia_used": self.ganglia_used,
            "tian_dao_ok": self.tian_dao_ok,
        }


# ══════════════════════════════════════════════════════════════════════════════
#  QUERY TYPE DETECTOR
# ══════════════════════════════════════════════════════════════════════════════

# Mapeo de palabras clave a tipo de query
_TYPE_KEYWORDS: Dict[QueryType, List[str]] = {
    QueryType.CODE: [
        "code", "function", "class", "implement", "refactor", "debug", "fix bug",
        "script", "program", "api", "compile", "python", "javascript", "rust",
        "def ", "import ", "```",
    ],
    QueryType.VISION: [
        "image", "screenshot", "photo", "picture", "visual", "see", "look at",
        "describe what", "analyze image",
    ],
    QueryType.REASONING: [
        "explain", "why", "analyze", "compare", "step by step", "think through",
        "pros and cons", "trade-off", "architect", "design",
    ],
    QueryType.TOOL_USE: [
        "run", "execute", "shell", "command", "install", "scan", "nmap",
        "file", "process", "service",
    ],
    QueryType.EMBEDDING: [
        "embed", "similarity", "search similar", "vector", "semantic search",
    ],
    QueryType.CREATIVE: [
        "create", "generate", "write a story", "imagine", "brainstorm",
        "invent", "design",
    ],
}


def detect_query_type(text: str, has_images: bool = False) -> QueryType:
    """Detecta el tipo de query analizando el texto."""
    if has_images:
        return QueryType.VISION

    text_lower = text.lower()
    scores: Dict[QueryType, int] = {qt: 0 for qt in QueryType}

    for qt, keywords in _TYPE_KEYWORDS.items():
        for kw in keywords:
            if kw in text_lower:
                scores[qt] += 1

    best = max(scores, key=lambda k: scores[k])
    if scores[best] == 0:
        return QueryType.CHAT
    return best


# ══════════════════════════════════════════════════════════════════════════════
#  AGENT REGISTRY — agentes virtuales de la colonia
# ══════════════════════════════════════════════════════════════════════════════

@dataclass
class ColonyAgent:
    """Agente virtual de la colonia con especialización."""
    agent_id: str
    name: str
    specialties: List[QueryType]
    system_prompt: str
    preferred_model: str = ""     # vacío = auto-selección
    life_cost: float = 2.0
    quality_score: float = 0.5    # 0-1, ajustado por feedback

    def can_handle(self, qt: QueryType) -> bool:
        return qt in self.specialties


# Agentes predefinidos de la colonia
DEFAULT_AGENTS: List[ColonyAgent] = [
    ColonyAgent(
        agent_id="colony_coder",
        name="Coder",
        specialties=[QueryType.CODE],
        system_prompt=(
            "Eres un experto programador. Escribe código limpio, eficiente y bien "
            "documentado. Responde siempre con código funcional cuando sea apropiado."
        ),
        quality_score=0.8,
    ),
    ColonyAgent(
        agent_id="colony_analyst",
        name="Analyst",
        specialties=[QueryType.REASONING, QueryType.CREATIVE],
        system_prompt=(
            "Eres un analista experto. Descompones problemas complejos en pasos claros. "
            "Proporcionas análisis profundos con pros, contras y recomendaciones."
        ),
        quality_score=0.8,
    ),
    ColonyAgent(
        agent_id="colony_vision",
        name="Vision",
        specialties=[QueryType.VISION],
        system_prompt=(
            "Eres un experto en análisis visual. Describes imágenes con detalle, "
            "identificas texto (OCR), código, interfaces y patrones visuales."
        ),
        quality_score=0.7,
    ),
    ColonyAgent(
        agent_id="colony_operator",
        name="Operator",
        specialties=[QueryType.TOOL_USE],
        system_prompt=(
            "Eres un operador de sistemas Linux experto. Generas comandos shell "
            "precisos y seguros. Priorizas la seguridad del sistema."
        ),
        quality_score=0.8,
    ),
    ColonyAgent(
        agent_id="colony_general",
        name="General",
        specialties=[QueryType.CHAT, QueryType.EMBEDDING],
        system_prompt=(
            "Eres EIDOS, un asistente de IA autónomo que corre en hardware local. "
            "Respondes de forma concisa, precisa y útil."
        ),
        quality_score=0.6,
    ),
]


# ══════════════════════════════════════════════════════════════════════════════
#  COLONY QUERY ENGINE
# ══════════════════════════════════════════════════════════════════════════════

class ColonyQueryEngine:
    """
    Motor central que despacha consultas a la colonia de agentes EIDOS.

    Integra: SmartRouter, TokenEconomy, Ganglia, Governance, ModelManager.
    Reemplaza progresivamente las dependencias externas de IA.
    """

    def __init__(self, db_path: str = DB_PATH):
        self.db_path = db_path
        self._lock = threading.Lock()
        self._agents: Dict[str, ColonyAgent] = {}
        self._query_count = 0
        self._total_tokens_spent = 0.0

        # Subsistemas (todos opcionales)
        self._router = SmartRouter() if HAS_ROUTER else None
        self._model_mgr = get_model_manager() if HAS_MODEL_MGR else None
        self._economy = get_economy() if HAS_ECONOMY else None
        self._ganglia = get_ganglia_manager() if HAS_GANGLIA else None
        self._governance = GovernanceSystem() if HAS_GOVERNANCE else None

        self._init_db()
        self._init_agents()
        log.info("[ColonyEngine] Inicializado — agents=%d router=%s economy=%s ganglia=%s",
                 len(self._agents), HAS_ROUTER, HAS_ECONOMY, HAS_GANGLIA)

    def _init_db(self) -> None:
        """Crea tablas para historial de queries."""
        os.makedirs(os.path.dirname(self.db_path), exist_ok=True)
        with get_conn_ctx(self.db_path) as conn:
            conn.executescript("""
                CREATE TABLE IF NOT EXISTS query_history (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    query_hash TEXT,
                    query_text TEXT,
                    query_type TEXT,
                    model_used TEXT,
                    agent_used TEXT,
                    profile TEXT,
                    requester TEXT,
                    success INTEGER,
                    tokens_spent REAL,
                    elapsed_s REAL,
                    feedback TEXT DEFAULT NULL,
                    timestamp REAL
                );
                CREATE TABLE IF NOT EXISTS agent_performance (
                    agent_id TEXT,
                    query_type TEXT,
                    total_queries INTEGER DEFAULT 0,
                    successful INTEGER DEFAULT 0,
                    avg_elapsed REAL DEFAULT 0.0,
                    last_used REAL,
                    PRIMARY KEY (agent_id, query_type)
                );
                CREATE INDEX IF NOT EXISTS idx_qh_time ON query_history(timestamp);
                CREATE INDEX IF NOT EXISTS idx_qh_agent ON query_history(agent_used);
            """)

    def _init_agents(self) -> None:
        """Registra agentes predefinidos en la colonia."""
        for agent in DEFAULT_AGENTS:
            self._agents[agent.agent_id] = agent

        # Registrar en TokenEconomy si disponible
        if self._economy:
            for agent_id in self._agents:
                try:
                    self._economy.register_agent(agent_id, initial_balance=100, life_cost=2.0)
                except Exception:
                    pass  # error no crítico, continuar
    # ── PIPELINE PRINCIPAL ───────────────────────────────────────────────────

    def query(self, text: str, context: str = "", profile: str = "auto",
              requester: str = "user", images: List[str] = None,
              tools: List[dict] = None, max_tokens: int = 2048,
              temperature: float = 0.7, system_prompt: str = "") -> ColonyResult:
        """
        Ejecuta una consulta a través de la colonia.

        Pipeline:
          1. Detectar tipo de query
          2. Verificar Tian Dao
          3. Seleccionar agente (Ganglia + specialties)
          4. Seleccionar modelo (SmartRouter / ModelManager)
          5. Cobrar tokens
          6. Ejecutar query contra Ollama
          7. Registrar resultado + feedback
        """
        t0 = time.time()
        images = images or []
        tools = tools or []

        # 1. Detectar tipo
        query_type = detect_query_type(text, has_images=bool(images))

        # 2. Verificar Tian Dao
        tian_dao_ok = True
        if self._governance:
            try:
                ok, violations = self._governance.check_tian_dao(text)
                if not ok:
                    return ColonyResult(
                        success=False,
                        response=f"[TIAN DAO] Consulta bloqueada: {violations[0]['rule_text']}",
                        query_type=query_type,
                        tian_dao_ok=False,
                        elapsed_s=time.time() - t0,
                    )
            except Exception:
                pass  # error no crítico, continuar
        # 3. Seleccionar agente
        agent = self._select_agent(query_type, text)

        # 4. Seleccionar modelo
        model_name = self._select_model(query_type, profile, text, context, images, tools, agent)

        # 5. Cobrar tokens al agente
        tokens_cost = self._compute_cost(query_type, max_tokens)
        if self._economy:
            try:
                self._economy.spend(agent.agent_id, tokens_cost, f"query:{query_type.value}")
            except Exception:
                pass  # Si no puede pagar, continúa igual (colonia no bloquea)

        # 6. Ejecutar contra Ollama
        effective_system = system_prompt or agent.system_prompt

        # Enriquecer con contexto de ganglia si disponible
        ganglia_used = []
        if self._ganglia:
            ganglia_context = self._get_ganglia_context(query_type, text, agent.agent_id)
            if ganglia_context:
                effective_system += f"\n\n[Conocimiento heredado]:\n{ganglia_context['text']}"
                ganglia_used = ganglia_context.get("names", [])

        response_text = self._execute_ollama(
            model=model_name,
            prompt=text,
            system=effective_system,
            context=context,
            images=images,
            tools=tools,
            max_tokens=max_tokens,
            temperature=temperature,
        )

        elapsed = time.time() - t0
        success = not response_text.startswith("[MODEL ERROR]")

        # 7. Registrar resultado
        result = ColonyResult(
            success=success,
            response=response_text,
            model_used=model_name,
            agent_used=agent.agent_id,
            query_type=query_type,
            routing_confidence=0.0,
            tokens_spent=tokens_cost,
            elapsed_s=elapsed,
            ganglia_used=ganglia_used,
            tian_dao_ok=tian_dao_ok,
        )

        self._record_query(text, result, profile, requester)

        # Reward si éxito
        if success and self._economy:
            try:
                self._economy.earn(agent.agent_id, tokens_cost * 0.5, f"completed:{query_type.value}")
            except Exception:
                pass  # error no crítico, continuar
        # Router feedback
        if self._router and success:
            try:
                scores = self._router.analyze(text, context)
                self._router.record_feedback(model_name, success, scores)
                result.routing_confidence = max(scores.values()) if scores else 0.0
            except Exception:
                pass  # error no crítico, continuar
        self._query_count += 1
        self._total_tokens_spent += tokens_cost

        return result

    # ── SELECCIÓN DE AGENTE ──────────────────────────────────────────────────

    def _select_agent(self, query_type: QueryType, text: str) -> ColonyAgent:
        """Selecciona el mejor agente para esta query."""
        candidates = [a for a in self._agents.values() if a.can_handle(query_type)]

        if not candidates:
            return self._agents.get("colony_general", DEFAULT_AGENTS[-1])

        # Buscar ganglia relevantes para bonus
        if self._ganglia:
            for agent in candidates:
                ganglia_list = self._ganglia.get_agent_ganglia(agent.agent_id)
                relevant = sum(1 for g in ganglia_list if self._ganglia_relevant(g, text))
                agent.quality_score = min(1.0, agent.quality_score + relevant * 0.05)

        # Mejor por quality_score
        return max(candidates, key=lambda a: a.quality_score)

    def _ganglia_relevant(self, ganglion_info: dict, text: str) -> bool:
        """Verifica si un ganglion es relevante para la query."""
        text_lower = text.lower()
        name = ganglion_info.get("name", "").lower()
        desc = ganglion_info.get("description", "").lower()
        return any(w in name or w in desc for w in text_lower.split()[:5])

    # ── SELECCIÓN DE MODELO ──────────────────────────────────────────────────

    def _select_model(self, query_type: QueryType, profile: str, text: str,
                      context: str, images: List[str], tools: List[dict],
                      agent: ColonyAgent) -> str:
        """Selecciona el modelo Ollama óptimo."""
        # Si el agente tiene preferencia, usarla
        if agent.preferred_model:
            return agent.preferred_model

        # SmartRouter primero (más inteligente)
        if self._router:
            try:
                routing = self._router.route(text, context=context, profile=profile)
                return routing.model
            except Exception:
                pass  # error no crítico, continuar
        # Fallback: ModelManager por tipo de tarea
        if self._model_mgr:
            task_map = {
                QueryType.CODE: TaskType.CODE,
                QueryType.VISION: TaskType.VISION_FAST,
                QueryType.REASONING: TaskType.REASON,
                QueryType.TOOL_USE: TaskType.TOOL_CALL,
                QueryType.EMBEDDING: TaskType.EMBED,
                QueryType.CHAT: TaskType.CHAT,
                QueryType.CREATIVE: TaskType.CHAT,
            }
            task = task_map.get(query_type, TaskType.CHAT)
            try:
                return self._model_mgr.select(task)
            except Exception:
                pass  # error no crítico, continuar
        # Ultra-fallback — modelos del Mac (localhost:11435)
        if images:
            return "moondream:latest"
        if tools:
            return "lfm2.5-thinking:1.2b"
        return "lfm2.5-thinking:1.2b"

    # ── EJECUCIÓN OLLAMA ─────────────────────────────────────────────────────

    def _execute_ollama(self, model: str, prompt: str, system: str = "",
                        context: str = "", images: List[str] = None,
                        tools: List[dict] = None, max_tokens: int = 2048,
                        temperature: float = 0.7) -> str:
        """Ejecuta la query contra Ollama."""
        if self._model_mgr:
            full_prompt = prompt
            if context:
                full_prompt = f"Context: {context}\n\nQuery: {prompt}"

            result = self._model_mgr.generate(
                model=model,
                prompt=full_prompt,
                system=system,
                images=images or [],
                tools=tools or [],
                max_tokens=max_tokens,
                temperature=temperature,
            )

            if "error" in result:
                return f"[MODEL ERROR] {result['error']}"
            if "message" in result:
                return result["message"].get("content", "")
            return result.get("response", "")

        # Sin ModelManager, llamada directa
        return self._direct_ollama_call(model, prompt, system, max_tokens, temperature)

    def _direct_ollama_call(self, model: str, prompt: str, system: str,
                            max_tokens: int, temperature: float) -> str:
        """Llamada directa a Ollama sin ModelManager."""
        import urllib.request

        url = os.environ.get("OLLAMA_URL", "http://127.0.0.1:11434")
        messages = []
        if system:
            messages.append({"role": "system", "content": system})
        messages.append({"role": "user", "content": prompt})

        payload = json.dumps({
            "model": model,
            "messages": messages,
            "stream": False,
            "options": {"num_predict": max_tokens, "temperature": temperature},
        }).encode()

        req = urllib.request.Request(
            f"{url}/api/chat",
            data=payload,
            headers={"Content-Type": "application/json"},
        )
        try:
            with urllib.request.urlopen(req, timeout=120) as resp:
                data = json.load(resp)
            return data.get("message", {}).get("content", "")
        except Exception as e:
            return f"[MODEL ERROR] {e}"

    # ── GANGLIA CONTEXT ──────────────────────────────────────────────────────

    def _get_ganglia_context(self, query_type: QueryType, text: str,
                             agent_id: str) -> Optional[Dict]:
        """Busca ganglia relevantes para enriquecer la query."""
        if not self._ganglia:
            return None

        # Buscar por palabras clave de la query
        words = text.split()[:3]
        all_results = []
        for word in words:
            if len(word) > 3:
                results = self._ganglia.find_ganglia(word, limit=3)
                all_results.extend(results)

        if not all_results:
            return None

        # Deduplicar y tomar los mejores
        seen = set()
        unique = []
        for r in all_results:
            if r["name"] not in seen:
                seen.add(r["name"])
                unique.append(r)

        # Top 3 por score
        unique.sort(key=lambda x: x.get("score", 0), reverse=True)
        top = unique[:3]

        # Registrar uso
        names = []
        for g in top:
            try:
                self._ganglia.use_ganglion(agent_id, g["name"], success=True)
                names.append(g["name"])
            except Exception:
                pass  # error no crítico, continuar
        context_text = "\n".join(
            f"- {g['name']}: {g['description']}" for g in top
        )
        return {"text": context_text, "names": names}

    # ── COSTES ───────────────────────────────────────────────────────────────

    def _compute_cost(self, query_type: QueryType, max_tokens: int) -> float:
        """Calcula el coste en tokens de una query."""
        base_costs = {
            QueryType.CHAT: 1.0,
            QueryType.CODE: 2.0,
            QueryType.VISION: 3.0,
            QueryType.REASONING: 2.5,
            QueryType.TOOL_USE: 2.0,
            QueryType.EMBEDDING: 0.5,
            QueryType.CREATIVE: 1.5,
        }
        base = base_costs.get(query_type, 1.0)
        # Escalar por tokens solicitados
        scale = min(max_tokens / 1024, 3.0)
        return round(base * scale, 1)

    # ── REGISTRO ─────────────────────────────────────────────────────────────

    def _record_query(self, text: str, result: ColonyResult,
                      profile: str, requester: str) -> None:
        """Persiste la query en el historial."""
        try:
            query_hash = hashlib.md5(text.encode()).hexdigest()[:12]
            with get_conn_ctx(self.db_path) as conn:
                conn.execute("""
                    INSERT INTO query_history
                    (query_hash, query_text, query_type, model_used, agent_used,
                     profile, requester, success, tokens_spent, elapsed_s, timestamp)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """, (query_hash, text[:500], result.query_type.value,
                      result.model_used, result.agent_used,
                      profile, requester, int(result.success),
                      result.tokens_spent, result.elapsed_s, time.time()))

                # Actualizar performance del agente
                # IMPORTANTE: avg_elapsed se calcula ANTES de incrementar
                # total_queries, porque SQLite evalúa las asignaciones SET
                # en orden y necesitamos el valor VIEJO de total_queries.
                conn.execute("""
                    INSERT INTO agent_performance (agent_id, query_type, total_queries, successful, avg_elapsed, last_used)
                    VALUES (?, ?, 1, ?, ?, ?)
                    ON CONFLICT(agent_id, query_type) DO UPDATE SET
                        avg_elapsed = (avg_elapsed * total_queries + ?) / (total_queries + 1),
                        total_queries = total_queries + 1,
                        successful = successful + ?,
                        last_used = ?
                """, (result.agent_used, result.query_type.value,
                      int(result.success), result.elapsed_s, time.time(),
                      result.elapsed_s, int(result.success), time.time()))
        except Exception as e:
            log.warning("[ColonyEngine] Error recording query: %s", e)

    def record_feedback(self, query_id: int, feedback: str) -> bool:
        """Registra feedback sobre una query pasada."""
        try:
            with get_conn_ctx(self.db_path) as conn:
                conn.execute(
                    "UPDATE query_history SET feedback = ? WHERE id = ?",
                    (feedback, query_id)
                )
            return True
        except Exception:
            return False

    # ── API PÚBLICA ──────────────────────────────────────────────────────────

    def get_agents(self) -> List[Dict]:
        """Lista todos los agentes de la colonia."""
        agents = []
        for a in self._agents.values():
            agent_data = {
                "agent_id": a.agent_id,
                "name": a.name,
                "specialties": [s.value for s in a.specialties],
                "quality_score": round(a.quality_score, 2),
            }
            # Enriquecer con economía
            if self._economy:
                try:
                    status = self._economy.get_agent_status(a.agent_id)
                    if status:
                        agent_data["balance"] = status["balance"]
                        agent_data["state"] = status["state"]
                except Exception:
                    pass  # error no crítico, continuar
            agents.append(agent_data)
        return agents

    def get_stats(self) -> Dict:
        """Estadísticas del motor de colonia."""
        stats = {
            "total_queries": self._query_count,
            "total_tokens_spent": round(self._total_tokens_spent, 1),
            "agents": len(self._agents),
            "subsystems": {
                "router": HAS_ROUTER,
                "model_manager": HAS_MODEL_MGR,
                "economy": HAS_ECONOMY,
                "ganglia": HAS_GANGLIA,
                "governance": HAS_GOVERNANCE,
                "knowledge_graph": HAS_KG,
                "brain_memory": HAS_BRAIN,
            },
        }

        # Stats de DB
        try:
            with get_conn_ctx(self.db_path) as conn:
                total_db = conn.execute("SELECT COUNT(*) FROM query_history").fetchone()[0]
                success_db = conn.execute(
                    "SELECT COUNT(*) FROM query_history WHERE success = 1"
                ).fetchone()[0]
                stats["db_total_queries"] = total_db
                stats["db_success_rate"] = round(success_db / max(total_db, 1), 3)

                # Por tipo
                by_type = {}
                for row in conn.execute(
                    "SELECT query_type, COUNT(*) FROM query_history GROUP BY query_type"
                ).fetchall():
                    by_type[row[0]] = row[1]
                stats["by_type"] = by_type

                # Por modelo
                by_model = {}
                for row in conn.execute(
                    "SELECT model_used, COUNT(*) FROM query_history GROUP BY model_used"
                ).fetchall():
                    by_model[row[0]] = row[1]
                stats["by_model"] = by_model
        except Exception:
            pass  # error no crítico, continuar
        return stats

    def get_history(self, limit: int = 20, requester: str = None) -> List[Dict]:
        """Historial de queries recientes."""
        try:
            with get_conn_ctx(self.db_path) as conn:
                if requester:
                    rows = conn.execute(
                        """SELECT id, query_text, query_type, model_used, agent_used,
                                  success, tokens_spent, elapsed_s, timestamp
                           FROM query_history WHERE requester = ?
                           ORDER BY timestamp DESC LIMIT ?""",
                        (requester, limit)
                    ).fetchall()
                else:
                    rows = conn.execute(
                        """SELECT id, query_text, query_type, model_used, agent_used,
                                  success, tokens_spent, elapsed_s, timestamp
                           FROM query_history ORDER BY timestamp DESC LIMIT ?""",
                        (limit,)
                    ).fetchall()
            return [{
                "id": r[0], "query": r[1][:100], "type": r[2], "model": r[3],
                "agent": r[4], "success": bool(r[5]),
                "tokens": round(r[6], 1), "elapsed": round(r[7], 2),
                "timestamp": r[8],
            } for r in rows]
        except Exception:
            return []

    def register_agent(self, agent_id: str, name: str,
                       specialties: List[str], system_prompt: str,
                       preferred_model: str = "") -> bool:
        """Registra un nuevo agente en la colonia."""
        try:
            specs = [QueryType(s) for s in specialties]
        except ValueError:
            return False

        agent = ColonyAgent(
            agent_id=agent_id, name=name,
            specialties=specs, system_prompt=system_prompt,
            preferred_model=preferred_model,
        )
        self._agents[agent_id] = agent

        if self._economy:
            try:
                self._economy.register_agent(agent_id, initial_balance=100, life_cost=2.0)
            except Exception:
                pass  # error no crítico, continuar
        return True

    def get_status(self) -> Dict:
        """Estado completo del motor."""
        return {
            "online": True,
            "agents": len(self._agents),
            "queries_this_session": self._query_count,
            "subsystems": {
                "SmartRouter": "online" if HAS_ROUTER else "offline",
                "ModelManager": "online" if HAS_MODEL_MGR else "offline",
                "TokenEconomy": "online" if HAS_ECONOMY else "offline",
                "Ganglia": "online" if HAS_GANGLIA else "offline",
                "Governance": "online" if HAS_GOVERNANCE else "offline",
            },
        }


# ══════════════════════════════════════════════════════════════════════════════
#  SINGLETON
# ══════════════════════════════════════════════════════════════════════════════

_colony_engine: Optional[ColonyQueryEngine] = None


def get_colony_engine() -> ColonyQueryEngine:
    global _colony_engine
    if _colony_engine is None:
        _colony_engine = ColonyQueryEngine()
    return _colony_engine


# ── CLI test ──────────────────────────────────────────────────────────────────
if __name__ == "__main__":
    print("=" * 60)
    print("  EIDOS ColonyQueryEngine — Status")
    print("=" * 60)

    engine = get_colony_engine()
    status = engine.get_status()
    print(f"\n  Online: {status['online']}")
    print(f"  Agents: {status['agents']}")
    for name, state in status["subsystems"].items():
        icon = "+" if state == "online" else "-"
        print(f"    [{icon}] {name}: {state}")

    print(f"\n  Agents:")
    for a in engine.get_agents():
        print(f"    {a['agent_id']}: {a['name']} — {a['specialties']}")

    stats = engine.get_stats()
    print(f"\n  Stats: {json.dumps(stats, indent=2)}")

    print("\n  ColonyQueryEngine funcional")
