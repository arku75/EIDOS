"""
core/eidos_rl.py — Q-Learning sobre el grafo neuronal (S77 "El Latido")

Q(λ) con eligibility traces. Sin GPU, sin LLM. Valores Q en aristas del grafo.

API:
    rl = get_rl_agent()
    action = rl.select_action(state_hash)
    rl.learn(state, action, reward, next_state)
"""

from __future__ import annotations

import hashlib
import json
import logging
import random
import sqlite3
import time
from pathlib import Path
from typing import Any, Dict, List, Optional
from core.db import get_conn

log = logging.getLogger("eidos.rl")

BRAIN_DB = Path.home() / ".eidos" / "evolution_brain.db"
RL_STATE_FILE = Path.home() / ".eidos" / "rl_state.json"

ALPHA = 0.1
GAMMA = 0.9
LAMBDA = 0.7
EPSILON_BASE = 0.15
EPSILON_MIN = 0.02
EPSILON_DECAY = 0.9995


class QLearningAgent:
    def __init__(self):
        self.epsilon = EPSILON_BASE
        self.episodes = 0
        self.total_reward = 0.0
        self._eligibility: Dict[str, float] = {}
        self._cache: Dict[str, Dict[str, float]] = {}
        self._cache_lock = __import__('threading').Lock()  # S82: protege acceso concurrente a _cache
        self._node_names: Dict[str, str] = {}  # state_hash -> comma-separated node names for similarity
        self._action_list: List[str] = self._load_actions()
        self._unpersisted_updates: int = 0  # track updates since last persist
        self._init_db()
        self._load_state()
        log.info("QLearningAgent: %d acciones, eps=%.4f", len(self._action_list), self.epsilon)

    def select_action(self, state_hash: str, *,
                      available_actions: Optional[List[str]] = None,
                      node_names: str = "") -> str:
        actions = available_actions if available_actions is not None else self._action_list
        if not actions:
            return "null_action"
        try:
            from core.eidos_affect import get_affect
            effective_epsilon = self.epsilon + get_affect().state.exploration_bias * 0.1
        except Exception:
            effective_epsilon = self.epsilon
        effective_epsilon = max(EPSILON_MIN, effective_epsilon)
        if random.random() < effective_epsilon:
            return random.choice(actions)
        else:
            return self.best_action(state_hash, actions, node_names=node_names)

    def best_action(self, state_hash: str,
                    available_actions: Optional[List[str]] = None,
                    node_names: str = "") -> str:
        actions = available_actions or self._action_list
        if not actions:
            return "null_action"
        # Use similarity-based lookup when node_names is provided
        if node_names:
            q_values = self._get_q_values_with_similarity(state_hash, node_names)
        else:
            q_values = self._get_q_values(state_hash)
        if not q_values:
            return random.choice(actions)
        return max(actions, key=lambda a: q_values.get(a, 0.0))

    def learn(self, state_hash: str, action: str, reward: float,
              next_state_hash: str, node_names: str = ""):
        q_current = self._get_q(state_hash, action)
        next_q = self._get_q_values(next_state_hash)
        max_next_q = max(next_q.values()) if next_q else 0.0
        td_target = reward + GAMMA * max_next_q
        td_error = td_target - q_current
        trace_key = f"{state_hash}:{action}"
        self._eligibility[trace_key] = 1.0
        for key, trace in list(self._eligibility.items()):
            if trace < 0.001:
                del self._eligibility[key]
                continue
            s, a = key.split(":", 1)
            old_q = self._get_q(s, a)
            new_q = old_q + ALPHA * td_error * trace
            self._set_q(s, a, new_q, node_names=node_names)
            self._eligibility[key] = trace * GAMMA * LAMBDA
        self.total_reward += reward
        self.episodes += 1
        self._unpersisted_updates += 1
        self.epsilon = max(EPSILON_MIN, self.epsilon * EPSILON_DECAY)
        # A learning event is only durable if it survives restart. Persist each
        # update; SQLite WAL keeps this bounded and avoids a 1-9 experience loss
        # window on crash/restart.
        self._persist_q_values()
        self._save_state()

    def reward_from_events(self, event_type: str, **kwargs) -> float:
        if event_type == "goal_completed":
            return 10.0
        elif event_type == "goal_failed":
            return -5.0
        elif event_type == "skill_learned":
            return 2.0
        elif event_type == "health_changed":
            return kwargs.get("delta", 0) * 5.0
        elif event_type == "curiosity":
            return 1.0
        elif event_type == "energy_cost":
            return -0.01
        elif event_type == "user_interaction":
            return 0.5
        elif event_type == "error":
            return -1.0 * kwargs.get("severity", 0.5)
        else:
            return 0.0

    def q_value_report(self, state_hash: str) -> Dict[str, float]:
        q = self._get_q_values(state_hash)
        return dict(sorted(q.items(), key=lambda x: x[1], reverse=True)[:10])

    def stats(self) -> Dict[str, Any]:
        return {
            "episodes": self.episodes, "epsilon": round(self.epsilon, 4),
            "total_reward": round(self.total_reward, 1),
            "actions_known": len(self._action_list),
            "q_values_cached": sum(len(v) for v in self._cache.values()),
            "eligibility_traces": len(self._eligibility),
        }

    @staticmethod
    def hash_state(active_nodes: List[str]) -> str:
        normalized = sorted(set(active_nodes))
        raw = ",".join(normalized[:20])
        return hashlib.md5(raw.encode()).hexdigest()[:16]

    @staticmethod
    def state_node_set(active_nodes: List[str]) -> str:
        """Return the canonical comma-separated node-name set for similarity lookups."""
        normalized = sorted(set(active_nodes))
        return ",".join(normalized[:30])

    @staticmethod
    def jaccard_similarity(nodes_a: str, nodes_b: str) -> float:
        """Compute Jaccard similarity between two comma-separated node-name sets."""
        if not nodes_a or not nodes_b:
            return 0.0
        set_a = set(nodes_a.split(","))
        set_b = set(nodes_b.split(","))
        if not set_a or not set_b:
            return 0.0
        intersection = set_a & set_b
        union = set_a | set_b
        if not union:
            return 0.0
        return len(intersection) / len(union)

    def find_similar_states(self, node_names: str, k: int = 5,
                           min_similarity: float = 0.1) -> List[Tuple[str, float]]:
        """Find k-nearest states by Jaccard similarity of their node-name sets.

        Queries the database for all known state node-name sets and ranks them
        by similarity to the given node_names. Returns list of (state_hash, similarity)
        sorted by similarity descending.
        """
        try:
            conn = get_conn(BRAIN_DB, timeout=5)
            rows = conn.execute(
                "SELECT DISTINCT state_hash, state_nodes FROM rl_q_values "
                "WHERE state_nodes != ''"
            ).fetchall()
            if not rows:
                return []
            scored = []
            for row in rows:
                stored_hash, stored_nodes = row[0], row[1]
                if not stored_nodes:
                    continue
                sim = self.jaccard_similarity(node_names, stored_nodes)
                if sim >= min_similarity:
                    scored.append((stored_hash, sim))
            scored.sort(key=lambda x: -x[1])
            return scored[:k]
        except Exception as e:
            log.debug("find_similar_states: %s", e)
            return []

    def _get_q_values_with_similarity(self, state_hash: str,
                                      node_names: str = "") -> Dict[str, float]:
        """Get Q-values for a state, falling back to similar states if exact match fails.

        Strategy:
          1. Exact hash match (fast path, backwards compatible)
          2. If not found and node_names provided: blend Q-values from k-nearest
             similar states, weighted by Jaccard similarity.
        """
        # 1. Exact match
        exact = self._get_q_values(state_hash)
        if exact:
            return exact

        # 2. Similarity fallback
        if not node_names:
            return {}

        similar = self.find_similar_states(node_names, k=5, min_similarity=0.15)
        if not similar:
            return {}

        # Blend Q-values from similar states, weighted by similarity
        blended: Dict[str, float] = {}
        total_weight = 0.0
        for similar_hash, sim in similar:
            q_vals = self._get_q_values(similar_hash)
            if not q_vals:
                continue
            weight = sim
            total_weight += weight
            for action, q_val in q_vals.items():
                blended[action] = blended.get(action, 0.0) + q_val * weight

        if total_weight > 0 and blended:
            for action in blended:
                blended[action] /= total_weight
            log.debug("Similarity Q-blend: %d states, top_sim=%.3f, actions=%d",
                     len(similar), similar[0][1] if similar else 0, len(blended))
            return blended

        return {}

    def register_action(self, action_name: str):
        if action_name not in self._action_list:
            self._action_list.append(action_name)

    def _get_q(self, state_hash: str, action: str) -> float:
        with self._cache_lock:
            if state_hash in self._cache:
                return self._cache[state_hash].get(action, 0.0)
        return 0.0

    def _get_q_values(self, state_hash: str) -> Dict[str, float]:
        with self._cache_lock:
            if state_hash in self._cache:
                return dict(self._cache[state_hash])
        try:
            conn = get_conn(BRAIN_DB, timeout=5)
            rows = conn.execute(
                "SELECT action, q_value FROM rl_q_values WHERE state_hash=?",
                (state_hash,)).fetchall()

            return {r[0]: r[1] for r in rows}
        except Exception:
            return {}

    def _set_q(self, state_hash: str, action: str, value: float,
              node_names: str = ""):
        with self._cache_lock:
            if state_hash not in self._cache:
                self._cache[state_hash] = {}
            self._cache[state_hash][action] = value
            if node_names:
                self._node_names[state_hash] = node_names

    def _persist_q_values(self):
        try:
            conn = get_conn(BRAIN_DB, timeout=10)
            conn.execute("PRAGMA busy_timeout=10000")
            count = 0
            # Save ALL cached states (not just top-500) with their node-name sets
            # for similarity lookups across restarts.
            with self._cache_lock:
                snapshot = [
                    (sh, a, v, self._node_names.get(sh, ""))
                    for sh, actions in self._cache.items()
                    for a, v in actions.items()
                ]
            for state_hash, action, value, nodes in snapshot:
                conn.execute(
                    "INSERT OR REPLACE INTO rl_q_values "
                    "(state_hash, action, q_value, updated_at, state_nodes) "
                    "VALUES (?,?,?,?,?)",
                    (state_hash, action, value, time.time(), nodes))
                count += 1
            conn.commit()
            self._unpersisted_updates = 0
            if count > 0:
                log.debug("_persist_q_values: %d rows saved", count)

        except Exception as e:
            log.debug("_persist_q_values: %s", e)

    def _load_actions(self) -> List[str]:
        actions = ["null_action", "research", "execute_shell",
                   "execute_code", "gui_click", "gui_type"]
        try:
            conn = get_conn(BRAIN_DB, timeout=5)
            rows = conn.execute(
                "SELECT concept FROM knowledge_nodes WHERE source='skill_learned' LIMIT 100"
            ).fetchall()

            for row in rows:
                name = row[0].replace("skill:", "").replace("shell:", "").replace("code:", "").strip()[:60]
                if name and name not in actions:
                    actions.append(name)
        except Exception:
            pass
        return actions

    def _init_db(self):
        try:
            conn = get_conn(BRAIN_DB, timeout=10)
            conn.execute("PRAGMA busy_timeout=10000")
            conn.execute("""CREATE TABLE IF NOT EXISTS rl_q_values (
                state_hash TEXT NOT NULL, action TEXT NOT NULL,
                q_value REAL DEFAULT 0.0, updated_at REAL,
                state_nodes TEXT DEFAULT '',
                PRIMARY KEY (state_hash, action))""")
            conn.execute("""CREATE INDEX IF NOT EXISTS idx_rl_q_state ON rl_q_values(state_hash)""")
            conn.execute("""CREATE TABLE IF NOT EXISTS rl_experiences (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                state_hash TEXT NOT NULL, action TEXT NOT NULL,
                reward REAL NOT NULL, next_state TEXT NOT NULL,
                timestamp REAL NOT NULL)""")
            # Add state_nodes column to existing table if missing (migration)
            try:
                conn.execute("ALTER TABLE rl_q_values ADD COLUMN state_nodes TEXT DEFAULT ''")
            except Exception:
                pass  # Column already exists
            conn.commit()

        except Exception as e:
            log.debug("RL _init_db: %s", e)

    def _load_state(self):
        try:
            if RL_STATE_FILE.exists():
                with open(RL_STATE_FILE) as f:
                    data = json.load(f)
                self.epsilon = data.get("epsilon", EPSILON_BASE)
                self.episodes = data.get("episodes", 0)
                self.total_reward = data.get("total_reward", 0.0)
        except Exception:
            pass

    def _save_state(self):
        try:
            RL_STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
            with open(RL_STATE_FILE, "w") as f:
                json.dump({"epsilon": self.epsilon, "episodes": self.episodes,
                          "total_reward": self.total_reward, "updated": time.time()}, f)
        except Exception:
            pass


_rl_agent: Optional[QLearningAgent] = None

def get_rl_agent() -> QLearningAgent:
    global _rl_agent
    if _rl_agent is None:
        _rl_agent = QLearningAgent()
    return _rl_agent


if __name__ == "__main__":
    import argparse
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    p = argparse.ArgumentParser(description="EIDOS Q-Learning Agent")
    p.add_argument("--stats", action="store_true")
    p.add_argument("--demo", action="store_true")
    args = p.parse_args()
    agent = QLearningAgent()
    if args.stats:
        print(json.dumps(agent.stats(), indent=2, ensure_ascii=False))
    if args.demo:
        print("Demo Q-Learning: 5 episodios")
        states = ["s1", "s2", "s3"]
        actions = ["investigar", "ejecutar", "navegar"]
        for ep in range(5):
            s = random.choice(states)
            a = agent.select_action(s, available_actions=actions)
            r = random.uniform(-5, 10)
            ns = random.choice(states)
            agent.learn(s, a, r, ns)
            print(f"  Ep {ep+1}: s={s} a={a} r={r:.1f} ns={ns} eps={agent.epsilon:.4f}")
