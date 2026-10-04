import contextlib
import importlib.util
import io
import json
import pathlib
import unittest
from unittest.mock import patch


ROOT = pathlib.Path(__file__).resolve().parents[1]
TERMINAL_PATH = ROOT / "bin" / "eidos_agents_terminal.py"

spec = importlib.util.spec_from_file_location("eidos_agents_terminal_testmod", TERMINAL_PATH)
terminal_mod = importlib.util.module_from_spec(spec)
assert spec and spec.loader
spec.loader.exec_module(terminal_mod)


class FakeHub:
    def __init__(self):
        self.proposals = {}
        self.observations = {}
        self.calls = []

    def capability_catalog(self):
        return {"tools": {"state": "EXISTS", "items": ["read_file"]}, "world_effect_verification": {"state": "WIRED"}}

    def propose_action(self, action, source="shared-terminal", expected_outcome=""):
        self.calls.append(("propose", action, source, expected_outcome))
        result = {
            "proposal_id": "proposal-test",
            "action": action,
            "source": source,
            "expected_outcome": expected_outcome,
            "status": "proposed",
        }
        self.proposals[result["proposal_id"]] = result
        return result

    def record_observation(self, snapshot, observer):
        self.calls.append(("observe", snapshot, observer))
        result = {
            "observation_id": f"obs-{len(self.observations)+1}",
            "observer": observer,
            "snapshot": snapshot,
        }
        self.observations[result["observation_id"]] = result
        return result

    def action_proposal(self, proposal_id=None):
        return self.proposals.get(proposal_id)

    def record_action_execution(self, proposal, executor, execution_id):
        self.calls.append(("executed", proposal["proposal_id"], executor, execution_id))
        return {
            "proposal_id": proposal["proposal_id"],
            "executor": executor,
            "execution_id": execution_id,
        }

    def verify_observations(self, proposal, before_id, after_id):
        self.calls.append(("verify", proposal["proposal_id"], before_id, after_id))
        return {
            "proposal_id": proposal["proposal_id"],
            "status": "verified",
            "evidence_independent": True,
        }

    def action_outcome(self, proposal_id=None):
        return {"proposal_id": proposal_id or "latest", "status": "verified"}

    def colony_reputation(self, agent_id):
        return {"agent_id": agent_id, "reputation_score": 0.75}


class TestSharedAgentsTerminal(unittest.TestCase):
    def setUp(self):
        self.hub = FakeHub()
        patcher = patch.object(terminal_mod, "get_runtime_hub", return_value=self.hub)
        self.addCleanup(patcher.stop)
        patcher.start()
        self.term = terminal_mod.SharedAgentsTerminal()

    def capture(self, method, arg):
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            method(arg)
        return output.getvalue()

    def test_capabilities_uses_runtime_hub_catalog(self):
        out = self.capture(self.term.do_capabilities, "")
        self.assertIn('"state": "EXISTS"', out)
        self.assertIn("read_file", out)
        self.assertIn('"state": "WIRED"', out)

    def test_propose_envelope_preserves_actor_and_expected_effect(self):
        raw = json.dumps({
            "source": "colony_coder",
            "expected_outcome": "results appear",
            "action": {"action": "click", "x": 10, "y": 20},
        })
        out = self.capture(self.term.do_propose, raw)
        self.assertIn("proposal-test", out)
        self.assertEqual(
            self.hub.calls[-1],
            (
                "propose",
                {"action": "click", "x": 10, "y": 20},
                "colony_coder",
                "results appear",
            ),
        )

    def test_execution_evidence_is_a_separate_terminal_channel(self):
        self.hub.propose_action({"action": "click"}, source="colony_coder")
        out = self.capture(
            self.term.do_executed,
            "proposal-test guardian-executor exec-123",
        )
        self.assertIn("exec-123", out)
        self.assertEqual(
            self.hub.calls[-1],
            ("executed", "proposal-test", "guardian-executor", "exec-123"),
        )

    def test_observe_and_verify_use_separate_evidence_channel(self):
        out = self.capture(
            self.term.do_observe,
            "runtime-observer " + repr(json.dumps({
                "window_title": "Before",
                "ocr_full_text": "Search",
                "regions": [],
            })),
        )
        self.assertIn("obs-1", out)

        self.hub.propose_action(
            {"action": "click"},
            source="colony_coder",
            expected_outcome="changed",
        )
        self.hub.record_observation(
            {"window_title": "After", "ocr_full_text": "Results", "regions": []},
            observer="runtime-observer",
        )

        out = self.capture(
            self.term.do_verify,
            "proposal-test obs-1 obs-2",
        )
        self.assertIn('"status": "verified"', out)
        self.assertEqual(
            self.hub.calls[-1],
            ("verify", "proposal-test", "obs-1", "obs-2"),
        )

    def test_outcome_and_reputation_are_inspectable(self):
        out = self.capture(self.term.do_outcome, "proposal-123")
        self.assertIn("proposal-123", out)

        out = self.capture(self.term.do_reputation, "colony_coder")
        self.assertIn("colony_coder", out)
        self.assertIn("0.75", out)


if __name__ == "__main__":
    unittest.main()
