"""EIDOS Life Rules Engine v1.0"""
import json, sqlite3, threading, time, hashlib, random
from dataclasses import dataclass, asdict
from typing import Dict, List, Optional, Any, Callable
from pathlib import Path
from core.db import get_conn
from core.db import get_conn_ctx

DB_PATH = Path.home() / ".eidos" / "life_rules.db"

@dataclass
class LifeRule:
    rule_id: str; name: str; description: str; trigger_type: str
    condition: Dict[str, Any]; action: str; action_params: Dict[str, Any]
    priority: int; created_at: float; activation_count: int = 0; is_active: bool = True

@dataclass  
class SpawnedCharacter:
    char_id: str; name: str; parent_agent: str; origin_interaction: str
    learned_logic: Dict[str, Any]; traits: List[str]; skills: List[str]
    birth_time: float; evolution_generations: int = 0; interaction_count: int = 0

class LifeRulesEngine:
    _instance = None
    _lock = threading.Lock()
    
    def __new__(cls):
        if cls._instance is None:
            with cls._lock:
                if cls._instance is None:
                    cls._instance = super().__new__(cls)
        return cls._instance
    
    def __init__(self):
        if hasattr(self, '_initialized'):
            return
        self._initialized = True
        self.db_path = DB_PATH
        self._ensure_db()
        self._rules: Dict[str, LifeRule] = {}
        self._characters: Dict[str, SpawnedCharacter] = {}
        self._callbacks: List[Callable] = []
        self._load_data()
        self._init_defaults()
    
    def _ensure_db(self):
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        with get_conn_ctx(self.db_path) as conn:
            conn.execute("CREATE TABLE IF NOT EXISTS life_rules (rule_id TEXT PRIMARY KEY, data TEXT)")
            conn.execute("CREATE TABLE IF NOT EXISTS spawned_characters (char_id TEXT PRIMARY KEY, data TEXT)")
            conn.execute("CREATE TABLE IF NOT EXISTS rule_activations (id INTEGER PRIMARY KEY, rule_id TEXT, char_id TEXT, trigger_data TEXT, activated_at REAL)")
    
    def _load_data(self):
        with get_conn_ctx(self.db_path) as conn:
            for row in conn.execute("SELECT data FROM life_rules"):
                try:
                    r = LifeRule(**json.loads(row[0]))
                    self._rules[r.rule_id] = r
                except Exception:
                    pass  # error no crítico, continuar
            for row in conn.execute("SELECT data FROM spawned_characters"):
                try:
                    c = SpawnedCharacter(**json.loads(row[0]))
                    self._characters[c.char_id] = c
                except Exception:
                    pass  # error no crítico, continuar
    def _init_defaults(self):
        defaults = [
            LifeRule("spawn_high_quality", "Spawn on High Quality", "Cuando calidad >= 2", 
                    "ollama_interaction", {"min_quality": 2}, "spawn_character", {"inherit": True}, 9, time.time()),
            LifeRule("clone_on_mastery", "Clone on Mastery", "Cuando domina tema", 
                    "knowledge_mastery", {"min_interactions": 3}, "clone_agent", {"accelerated": True}, 8, time.time()),
        ]
        for rule in defaults:
            if rule.rule_id not in self._rules:
                self._rules[rule.rule_id] = rule
                self._save_rule(rule)
    
    def _save_rule(self, rule: LifeRule):
        with get_conn_ctx(self.db_path) as conn:
            conn.execute("INSERT OR REPLACE INTO life_rules VALUES (?, ?)",
                        (rule.rule_id, json.dumps(asdict(rule))))
    
    def _save_char(self, char: SpawnedCharacter):
        with get_conn_ctx(self.db_path) as conn:
            conn.execute("INSERT OR REPLACE INTO spawned_characters VALUES (?, ?)",
                        (char.char_id, json.dumps(asdict(char))))
    
    def _log_activation(self, rule_id: str, char_id: str, trigger: dict):
        with get_conn_ctx(self.db_path) as conn:
            conn.execute("INSERT INTO rule_activations (rule_id, char_id, trigger_data, activated_at) VALUES (?, ?, ?, ?)",
                        (rule_id, char_id, json.dumps(trigger), time.time()))
    
    def _gen_id(self, prefix: str) -> str:
        h = hashlib.md5(f"{prefix}_{time.time()}_{random.random()}".encode()).hexdigest()[:8]
        return f"{prefix}_{h}"
    
    def _extract_traits(self, response: str) -> List[str]:
        traits = []
        keywords = {"code": ["coder", "programmer"], "analyze": ["analyst", "researcher"], 
                   "vision": ["visionary", "visual"], "operate": ["operator", "executor"],
                   "learn": ["learner", "adaptive"], "create": ["creator", "builder"]}
        r = response.lower()
        for cat, words in keywords.items():
            if any(w in r for w in words):
                traits.append(cat)
        return traits[:3] if traits else ["adaptive"]
    
    def _extract_skills(self, topic: str) -> List[str]:
        skills = []
        kw = {"python": "python", "javascript": "javascript", "rust": "rust", 
              "go": "golang", "sql": "sql", "docker": "docker", "ai": "ai", "ml": "ml"}
        t = topic.lower()
        for k, s in kw.items():
            if k in t:
                skills.append(s)
        return skills[:3] if skills else ["general"]
    
    def on_ollama_learn(self, agent: str, topic: str, response: str, quality: int):
        trigger = {"agent": agent, "topic": topic, "response": response, "quality": quality}
        for rule in self._rules.values():
            if not rule.is_active:
                continue
            if rule.trigger_type == "ollama_interaction":
                if quality >= rule.condition.get("min_quality", 1):
                    self._exec_rule(rule, trigger)
    
    def _exec_rule(self, rule: LifeRule, trigger: dict):
        if rule.action == "spawn_character":
            char = self._spawn_char(trigger)
            if char:
                self._log_activation(rule.rule_id, char.char_id, trigger)
                rule.activation_count += 1
                self._save_rule(rule)
        elif rule.action == "clone_agent":
            self._clone_agent(trigger)
    
    def _spawn_char(self, trigger: dict) -> Optional[SpawnedCharacter]:
        agent = trigger.get("agent", "unknown")
        topic = trigger.get("topic", "")
        response = trigger.get("response", "")
        char_id = self._gen_id("char")
        name = f"{agent.replace('colony_', '').title()}_{char_id[-4:]}"
        traits = self._extract_traits(response)
        skills = self._extract_skills(topic)
        char = SpawnedCharacter(char_id, name, agent, topic[:100], {"topic": topic, "traits": traits}, traits, skills, time.time())
        self._characters[char_id] = char
        self._save_char(char)
        self._register_colony(char)
        print(f"🌱 [LifeRules] Spawn: {name} de {agent} | Traits: {', '.join(traits)}")
        return char
    
    def _register_colony(self, char: SpawnedCharacter):
        try:
            from core.governance import GovernanceSystem
            gov = GovernanceSystem()
            gov.register_agent(char.char_id, char.name, role="spawned")
        except Exception as e:
            print(f"   ⚠️ Colony reg fail: {e}")
    
    def _clone_agent(self, trigger: dict):
        agent = trigger.get("agent")
        if not agent:
            return
        print(f"🔄 [LifeRules] Cloning: {agent}")
        for char in self._characters.values():
            if char.parent_agent == agent:
                char.evolution_generations += 1
                char.interaction_count += 1
                self._save_char(char)
                print(f"   Gen {char.evolution_generations} de {char.name}")
                break
    
    # ═══════════════════════════════════════════════════════════════
    # CICLO DE VIDA COMPLETO DEL PERSONAJE
    # ═══════════════════════════════════════════════════════════════

    def on_connection_start(self, agent_id: str, source_id: str, source_type: str = "api") -> str:
        """
        Un personaje inicia conexión con una fuente externa (API, modelo, herramienta).
        Crea un tracking de aprendizaje. Devuelve connection_id.

        Flujo: on_connection_start → [aprendizaje] → on_knowledge_absorbed → fuente eliminada
        """
        conn_id = self._gen_id("conn")
        entry = {
            "conn_id": conn_id, "agent_id": agent_id,
            "source_id": source_id, "source_type": source_type,
            "progress": 0.0, "started_at": time.time(), "absorbed": False,
        }
        with get_conn_ctx(self.db_path) as db:
            db.execute("CREATE TABLE IF NOT EXISTS connections "
                       "(conn_id TEXT PRIMARY KEY, data TEXT)")
            db.execute("INSERT OR REPLACE INTO connections VALUES (?,?)",
                       (conn_id, json.dumps(entry)))
        print(f"🔗 [LifeRules] Conexión iniciada: {agent_id} ↔ {source_id} ({source_type})")
        return conn_id

    def update_connection_progress(self, conn_id: str, progress: float) -> None:
        """Actualiza el progreso de aprendizaje de una conexión (0.0 → 1.0)."""
        with get_conn_ctx(self.db_path) as db:
            row = db.execute("SELECT data FROM connections WHERE conn_id=?", (conn_id,)).fetchone()
            if not row:
                return
            entry = json.loads(row[0])
            entry["progress"] = min(1.0, max(0.0, progress))
            db.execute("INSERT OR REPLACE INTO connections VALUES (?,?)",
                       (conn_id, json.dumps(entry)))
        if progress >= 1.0:
            self.on_knowledge_absorbed(conn_id)

    def on_knowledge_absorbed(self, conn_id: str) -> Optional[SpawnedCharacter]:
        """
        El personaje completó el 100% de aprendizaje de la fuente.
        La conexión externa se 'elimina' (ya no es necesaria).
        El conocimiento vive permanentemente dentro de Colony.
        """
        with get_conn_ctx(self.db_path) as db:
            row = db.execute("SELECT data FROM connections WHERE conn_id=?", (conn_id,)).fetchone()
            if not row:
                return None
            entry = json.loads(row[0])
            if entry.get("absorbed"):
                return None
            entry["absorbed"] = True
            entry["absorbed_at"] = time.time()
            db.execute("INSERT OR REPLACE INTO connections VALUES (?,?)",
                       (conn_id, json.dumps(entry)))

        agent_id   = entry["agent_id"]
        source_id  = entry["source_id"]
        source_type = entry["source_type"]
        duration = time.time() - entry["started_at"]

        print(f"✨ [LifeRules] {agent_id} absorbió '{source_id}' completamente ({duration:.0f}s)")
        print(f"   La fuente externa ya no es necesaria. Conocimiento vive en Colony.")

        # Crear un personaje especializado que representa este conocimiento absorbido
        char_id = self._gen_id("absorbed")
        name    = f"{source_id.replace('_', ' ').title()}_Absorbed"
        char = SpawnedCharacter(
            char_id=char_id, name=name, parent_agent=agent_id,
            origin_interaction=f"absorbed:{source_id}",
            learned_logic={"source": source_id, "type": source_type, "duration_s": duration},
            traits=[source_type, "absorbed", "self_sufficient"],
            skills=[source_id[:30]],
            birth_time=time.time(),
        )
        self._characters[char_id] = char
        self._save_char(char)
        self._register_colony(char)

        # Registrar en Chronicle
        try:
            from core.colony_chronicle import get_chronicle
            get_chronicle().record(
                agent_id, "knowledge_absorbed",
                f"Absorbido: {source_id} — conexión externa eliminada",
                metadata={"source": source_id, "type": source_type, "char_id": char_id},
                importance=0.9,
            )
        except Exception:
            pass  # error no crítico, continuar
        return char

    def propose_reproduction(self, parent1_id: str, parent2_id: str,
                             reason: str = "complementary_knowledge") -> Optional[SpawnedCharacter]:
        """
        Dos personajes dan a luz a un hijo que hereda todo de ambos.
        Los padres NO mueren — gobernanza los protege.
        El hijo es la mejor versión combinada de los dos.
        """
        p1 = self._characters.get(parent1_id)
        p2 = self._characters.get(parent2_id)

        # Buscar también por agent_id si no es char_id directo
        if not p1:
            for c in self._characters.values():
                if c.parent_agent == parent1_id:
                    p1 = c; break
        if not p2:
            for c in self._characters.values():
                if c.parent_agent == parent2_id:
                    p2 = c; break

        if not p1 or not p2:
            print(f"⚠️ [LifeRules] Reproducción fallida: uno o ambos padres no encontrados")
            return None

        # El hijo hereda traits y skills de ambos (union sin duplicados)
        child_traits = list(dict.fromkeys(p1.traits + p2.traits))[:8]
        child_skills = list(dict.fromkeys(p1.skills + p2.skills))[:10]

        # Combinar learned_logic
        merged_logic = {
            **p1.learned_logic,
            **p2.learned_logic,
            "parents": [p1.char_id, p2.char_id],
            "birth_reason": reason,
            "generation": max(p1.evolution_generations, p2.evolution_generations) + 1,
        }

        child_id = self._gen_id("child")
        child_name = f"{p1.name[:8]}_{p2.name[:8]}_Gen{merged_logic['generation']}"

        child = SpawnedCharacter(
            char_id=child_id, name=child_name,
            parent_agent=f"{p1.parent_agent}+{p2.parent_agent}",
            origin_interaction=f"reproduction:{p1.char_id}+{p2.char_id}",
            learned_logic=merged_logic,
            traits=child_traits, skills=child_skills,
            birth_time=time.time(),
            evolution_generations=merged_logic["generation"],
        )
        self._characters[child_id] = child
        self._save_char(child)
        self._register_colony(child)

        # Padres viven — incrementar generación pero NO eliminar
        p1.evolution_generations += 1
        p2.evolution_generations += 1
        self._save_char(p1)
        self._save_char(p2)

        print(f"👶 [LifeRules] Nacimiento: {child_name}")
        print(f"   Padres: {p1.name} + {p2.name} (ambos viven en Colony)")
        print(f"   Traits: {', '.join(child_traits[:4])}")
        print(f"   Skills: {', '.join(child_skills[:4])}")

        # Chronicle
        try:
            from core.colony_chronicle import get_chronicle
            get_chronicle().record(
                child_id, "reproduction",
                f"Nacimiento de {child_name} de {p1.name} + {p2.name}",
                metadata={"parents": [p1.char_id, p2.char_id], "gen": merged_logic["generation"]},
                importance=1.0,
            )
        except Exception:
            pass  # error no crítico, continuar
        return child

    def get_genealogy(self) -> Dict[str, Any]:
        """Árbol genealógico de todos los personajes."""
        tree = {}
        for char in self._characters.values():
            parents = char.learned_logic.get("parents", [])
            tree[char.char_id] = {
                "name": char.name, "parent_agent": char.parent_agent,
                "parents": parents, "gen": char.evolution_generations,
                "traits": char.traits[:3], "birth": char.birth_time,
            }
        return tree

    def get_connections(self) -> List[Dict]:
        """Lista de conexiones activas e históricas."""
        try:
            with get_conn_ctx(self.db_path) as db:
                rows = db.execute("SELECT data FROM connections ORDER BY rowid DESC LIMIT 20").fetchall()
                return [json.loads(r[0]) for r in rows]
        except Exception:
            return []

    def get_chars(self) -> List[SpawnedCharacter]:
        return list(self._characters.values())

    def get_rules(self) -> List[LifeRule]:
        return list(self._rules.values())

    def add_callback(self, cb: Callable[[SpawnedCharacter], None]):
        self._callbacks.append(cb)

    def get_stats(self) -> dict:
        return {
            "chars": len(self._characters),
            "rules": sum(1 for r in self._rules.values() if r.is_active),
            "activations": sum(r.activation_count for r in self._rules.values()),
        }

def get_life_rules_engine() -> LifeRulesEngine:
    return LifeRulesEngine()

if __name__ == "__main__":
    e = get_life_rules_engine()
    print(f"🧬 LifeRules Engine | Chars: {len(e.get_chars())} | Rules: {len(e.get_rules())}")
