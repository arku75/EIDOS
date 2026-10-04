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
from types import SimpleNamespace
from typing import Any, Dict, Optional
import json
import os
import time

from core.agent_bus import get_bus, get_blackboard, get_router
from core.fly_lab import validate_synthetic
from core.action_verifier import ActionVerifier
from core.ui_world_model import UIWorldModel


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
    ComponentStatus("causal-loop", "core.causal_loop", find_spec("core.causal_loop") is not None,
                    "perceive-act-verify-learn loop; not auto-executed by hub"),
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


def _scene_from_snapshot(snapshot: Dict[str, Any]) -> SimpleNamespace:
    """Convert a serializable world snapshot into the shape used by verifier/model."""
    raw_regions = snapshot.get("regions", []) or []
    regions = []
    for region in raw_regions:
        if isinstance(region, dict):
            regions.append(SimpleNamespace(**region))
        else:
            regions.append(SimpleNamespace(text=str(region)))
    return SimpleNamespace(
        ocr_full_text=str(snapshot.get("ocr_full_text", snapshot.get("text", "")) or ""),
        window_title=str(snapshot.get("window_title", snapshot.get("title", "")) or ""),
        regions=regions,
    )


class EIDOSRuntimeHub:
    """Safe integration facade for inspection and collaboration."""

    def __init__(self) -> None:
        self.bus = get_bus()
        self.board = get_blackboard()
        self.router = get_router()
        self.world_model = UIWorldModel()
        self.verifier = ActionVerifier()

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
                "effect_verification_available": True,
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

    def propose_action(
        self,
        action: Dict[str, Any],
        source: str = "shared-terminal",
        expected_outcome: str = "",
    ) -> dict:
        """Publish an action proposal. This does NOT execute the action."""
        proposal = {
            "proposal_id": f"proposal-{time.time_ns()}",
            "action": dict(action),
            "source": source,
            "created_at": time.time(),
            "status": "proposed",
            "requires_verification": True,
            "expected_outcome": expected_outcome,
        }
        self.bus.publish("action.proposed", proposal, source=source)
        self.board.write("last_action_proposal", proposal, agent=source)
        return proposal

    def verify_action_effect(
        self,
        proposal: Dict[str, Any],
        before: Dict[str, Any],
        after: Dict[str, Any],
        *,
        evidence_source: str,
    ) -> dict:
        """Close the evidence loop without executing the proposed action.

        The caller supplies observed before/after world snapshots. Runtime Hub:
        1) predicts the transition,
        2) verifies the observed effect,
        3) records prediction accuracy in the world model,
        4) publishes the evidence for Colony/agents.
        """
        action = dict(proposal.get("action") or {})
        if not action:
            raise ValueError("proposal has no action")
        actor = str(proposal.get("source", "unknown"))
        observer = str(evidence_source or "").strip()
        if not observer:
            raise ValueError("evidence_source is required")
        evidence_independent = observer != actor

        before_scene = _scene_from_snapshot(before)
        after_scene = _scene_from_snapshot(after)
        prediction = self.world_model.predict_transition(before_scene, action)
        verification = self.verifier.verify(
            before_scene,
            after_scene,
            action,
            proposal.get("expected_outcome", ""),
        )
        credited_verified = bool(verification.verified and evidence_independent)
        self.world_model.record_outcome(
            prediction,
            credited_verified,
            after_scene,
        )

        verification_payload = asdict(verification)
        verification_payload["verdict"] = verification.verdict.value
        if verification.recovery_suggested is not None:
            verification_payload["recovery_suggested"] = verification.recovery_suggested.value

        colony_reputation = None
        source = actor
        if source.startswith("colony_") and evidence_independent:
            try:
                from core.colony_community import get_colony_community
                colony_reputation = get_colony_community().record_verified_outcome(
                    source,
                    credited_verified,
                    verification.confidence,
                    reason=verification.reason,
                    proposal_id=str(proposal.get("proposal_id") or ""),
                    action_type=str(action.get("action", "unknown")),
                    evidence={
                        "verdict": verification.verdict.value,
                        "prediction_risk": prediction.risk_score,
                        "prediction_confidence": prediction.confidence,
                    },
                )
            except Exception as exc:
                colony_reputation = {
                    "success": False,
                    "error": f"{type(exc).__name__}: {exc}",
                }

        if verification.verified and not evidence_independent:
            status = "untrusted_evidence"
        else:
            status = "verified" if credited_verified else "failed"

        payload = {
            "proposal_id": proposal.get("proposal_id"),
            "action": action,
            "source": source,
            "evidence_source": observer,
            "evidence_independent": evidence_independent,
            "status": status,
            "prediction": asdict(prediction),
            "verification": verification_payload,
            "credited_verified": credited_verified,
            "before": dict(before),
            "after": dict(after),
            "outcome_recorded": True,
            "colony_reputation": colony_reputation,
            "created_at": time.time(),
        }
        self.bus.publish("action.verified", payload, source="runtime-hub")
        self.board.write("last_action_outcome", payload, agent="runtime-hub")
        if payload["proposal_id"]:
            self.board.write(
                f"action.outcome.{payload['proposal_id']}",
                payload,
                agent="runtime-hub",
            )
        return payload

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
