"""
EIDOS Runtime Hub
=================

Read-mostly integration layer joining the public architectural pieces:
World, Actions, Characters/Colony, Artificial-Neural/Graph, Fly Lab and the
inter-agent bus.

The hub is intentionally conservative:
- importing it must not start GUI automation or services
- proposing an action publishes an intent, it does not execute it
- Fly experiments are isolated from the live EIDOS graph
"""
from __future__ import annotations

from dataclasses import dataclass, asdict
from importlib.util import find_spec
from pathlib import Path
from typing import Any, Dict, Optional
import json
import os
import time

from core.agent_bus import get_bus, get_blackboard, get_router
from core.fly_lab import validate_synthetic


@dataclass(frozen=True)
class ComponentStatus:
    name: str
    module: str
    available: bool
    role: str

    def to_dict(self) -> dict:
        return asdict(self)


_COMPONENTS = (
    ComponentStatus("world", "core.ui_world_model", find_spec("core.ui_world_model") is not None,
                    "predictive representation of UI/world transitions"),
    ComponentStatus("world-visual", "core.eidos_world_engine", find_spec("core.eidos_world_engine") is not None,
                    "visual/social world dashboard; not auto-started by hub"),
    ComponentStatus("actions", "core.action_system", find_spec("core.action_system") is not None,
                    "action representation and execution plumbing"),
    ComponentStatus("action-verifier", "core.action_verifier", find_spec("core.action_verifier") is not None,
                    "effect verification"),
    ComponentStatus("characters", "core.character_lifecycle", find_spec("core.character_lifecycle") is not None,
                    "character lifecycle and genealogy"),
    ComponentStatus("character-neurons", "core.character_neuron", find_spec("core.character_neuron") is not None,
                    "per-character synaptic-style state"),
    ComponentStatus("colony", "core.colony_community", find_spec("core.colony_community") is not None,
                    "deliberative/social layer"),
    ComponentStatus("neural-graph", "core.knowledge_reasoner", find_spec("core.knowledge_reasoner") is not None,
                    "graph/spreading-activation reasoning"),
    ComponentStatus("fly", "core.fly_lab", find_spec("core.fly_lab") is not None,
                    "isolated insect-inspired experiment lab"),
    ComponentStatus("agent-bus", "core.agent_bus", find_spec("core.agent_bus") is not None,
                    "shared pub/sub and blackboard"),
)


class EIDOSRuntimeHub:
    """Safe integration facade for inspection and collaboration."""

    def __init__(self) -> None:
        self.bus = get_bus()
        self.board = get_blackboard()
        self.router = get_router()

    def components(self) -> Dict[str, dict]:
        return {component.name: component.to_dict() for component in _COMPONENTS}

    def snapshot(self) -> dict:
        """Return a read-only architectural snapshot without starting services."""
        return {
            "timestamp": time.time(),
            "root": str(Path(os.environ.get("EIDOS_ROOT", Path.cwd())).expanduser().resolve()),
            "components": self.components(),
            "blackboard_keys": self.board.keys(),
            "registered_agents": sorted(getattr(self.router, "_handlers", {}).keys()),
            "safety": {
                "executes_actions": False,
                "starts_gui": False,
                "starts_network_services": False,
                "fly_writes_live_graph": False,
            },
        }

    def publish(self, topic: str, data: Any, source: str = "shared-terminal") -> None:
        self.bus.publish(topic, data, source=source)

    def board_write(self, key: str, value: Any, agent: str = "shared-terminal") -> int:
        return self.board.write(key, value, agent=agent)

    def board_read(self, key: str) -> Any:
        return self.board.read(key)

    def board_snapshot(self) -> Dict[str, Any]:
        return self.board.snapshot()

    def route(self, tagged_message: str) -> Dict[str, str]:
        return self.router.route(tagged_message)

    def propose_action(self, action: Dict[str, Any], source: str = "shared-terminal") -> dict:
        """Publish an action proposal. This does NOT execute the action."""
        proposal = {
            "action": dict(action),
            "source": source,
            "created_at": time.time(),
            "status": "proposed",
            "requires_verification": True,
        }
        self.bus.publish("action.proposed", proposal, source=source)
        self.board.write("last_action_proposal", proposal, agent=source)
        return proposal

    def fly_validate(self, seed: int = 317) -> dict:
        result = validate_synthetic(seed)
        payload = result.to_dict()
        self.board.write("fly.last_validation", payload, agent="fly_lab")
        self.bus.publish("fly.validation", payload, source="fly_lab")
        return payload


_hub: Optional[EIDOSRuntimeHub] = None


def get_runtime_hub() -> EIDOSRuntimeHub:
    global _hub
    if _hub is None:
        _hub = EIDOSRuntimeHub()
    return _hub


if __name__ == "__main__":
    print(json.dumps(get_runtime_hub().snapshot(), indent=2))
