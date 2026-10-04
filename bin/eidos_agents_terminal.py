#!/usr/bin/env python3
"""Interactive shared EIDOS agent terminal.

Safe by default: this REPL shares messages/state through agent_bus and can run
Fly Lab validation, but it does not execute GUI/system actions.
"""
from __future__ import annotations

import cmd
import json
import shlex

from core.runtime_hub import get_runtime_hub


class SharedAgentsTerminal(cmd.Cmd):
    intro = (
        "EIDOS Shared Agents Terminal\n"
        "World · Actions · Evidence · Colony · Characters · Neural Graph · Fly Lab · Agent Bus\n"
        "Type 'help' for commands. Actions are proposals only; effects require independent observations."
    )
    prompt = "eidos-agents> "

    def __init__(self) -> None:
        super().__init__()
        self.hub = get_runtime_hub()

    def do_status(self, arg: str) -> None:
        """status -- show integrated component/safety snapshot"""
        print(json.dumps(self.hub.snapshot(), indent=2, default=str))

    def do_components(self, arg: str) -> None:
        """components -- list World/Actions/Characters/Neural/Fly components"""
        print(json.dumps(self.hub.components(), indent=2))

    def do_fly(self, arg: str) -> None:
        """fly [seed] -- run deterministic insect-inspired validation + negative control"""
        seed = int(arg.strip() or "317")
        print(json.dumps(self.hub.fly_validate(seed), indent=2))

    def do_board(self, arg: str) -> None:
        """board -- show shared blackboard"""
        print(json.dumps(self.hub.board_snapshot(), indent=2, default=str))

    def do_write(self, arg: str) -> None:
        """write KEY JSON_OR_TEXT -- write shared blackboard state"""
        parts = shlex.split(arg)
        if len(parts) < 2:
            print("usage: write KEY VALUE")
            return
        key, raw = parts[0], " ".join(parts[1:])
        try:
            value = json.loads(raw)
        except Exception:
            value = raw
        version = self.hub.board_write(key, value)
        print(f"{key} version={version}")

    def do_read(self, arg: str) -> None:
        """read KEY -- read shared blackboard value"""
        print(json.dumps(self.hub.board_read(arg.strip()), indent=2, default=str))

    def do_publish(self, arg: str) -> None:
        """publish TOPIC JSON_OR_TEXT -- publish an inter-agent event"""
        parts = shlex.split(arg)
        if len(parts) < 2:
            print("usage: publish TOPIC VALUE")
            return
        topic, raw = parts[0], " ".join(parts[1:])
        try:
            value = json.loads(raw)
        except Exception:
            value = raw
        self.hub.publish(topic, value)
        print("published")

    def do_route(self, arg: str) -> None:
        """route '[@agent: message]' -- use the existing TagRouter"""
        print(json.dumps(self.hub.route(arg), indent=2, default=str))

    def do_propose(self, arg: str) -> None:
        """propose JSON -- create a proposal only; supports an optional envelope.

        Raw action:
          propose '{"action":"click","x":10,"y":20}'

        Envelope:
          propose '{"source":"colony_coder","expected_outcome":"results appear","action":{"action":"click","x":10,"y":20}}'
        """
        try:
            payload = json.loads(arg)
            if not isinstance(payload, dict):
                raise ValueError("proposal must be a JSON object")
            if isinstance(payload.get("action"), dict):
                action = payload["action"]
                source = str(payload.get("source", "shared-terminal"))
                expected = str(payload.get("expected_outcome", ""))
            else:
                action = payload
                source = "shared-terminal"
                expected = ""
        except Exception as exc:
            print(f"invalid JSON: {exc}")
            return
        print(json.dumps(
            self.hub.propose_action(
                action,
                source=source,
                expected_outcome=expected,
            ),
            indent=2,
            default=str,
        ))

    def do_observe(self, arg: str) -> None:
        """observe OBSERVER 'JSON' -- register a world snapshot with provenance."""
        parts = shlex.split(arg)
        if len(parts) < 2:
            print("usage: observe OBSERVER 'JSON'")
            return
        observer, raw = parts[0], " ".join(parts[1:])
        try:
            snapshot = json.loads(raw)
            if not isinstance(snapshot, dict):
                raise ValueError("snapshot must be a JSON object")
            result = self.hub.record_observation(snapshot, observer=observer)
        except Exception as exc:
            print(f"observation error: {exc}")
            return
        print(json.dumps(result, indent=2, default=str))

    def do_verify(self, arg: str) -> None:
        """verify PROPOSAL_ID BEFORE_OBS_ID AFTER_OBS_ID -- close the evidence loop."""
        parts = shlex.split(arg)
        if len(parts) != 3:
            print("usage: verify PROPOSAL_ID BEFORE_OBS_ID AFTER_OBS_ID")
            return
        proposal_id, before_id, after_id = parts
        proposal = self.hub.action_proposal(proposal_id)
        if not proposal:
            print(f"proposal not found: {proposal_id}")
            return
        try:
            result = self.hub.verify_observations(
                proposal,
                before_id,
                after_id,
            )
        except Exception as exc:
            print(f"verification error: {exc}")
            return
        print(json.dumps(result, indent=2, default=str))

    def do_outcome(self, arg: str) -> None:
        """outcome [PROPOSAL_ID] -- show latest or correlated action outcome."""
        result = self.hub.action_outcome(arg.strip() or None)
        print(json.dumps(result, indent=2, default=str))

    def do_reputation(self, arg: str) -> None:
        """reputation AGENT_ID -- show effect-derived Colony reputation."""
        agent_id = arg.strip()
        if not agent_id:
            print("usage: reputation AGENT_ID")
            return
        try:
            result = self.hub.colony_reputation(agent_id)
        except Exception as exc:
            print(f"reputation error: {exc}")
            return
        print(json.dumps(result, indent=2, default=str))

    def do_exit(self, arg: str) -> bool:
        """exit -- close terminal"""
        return True

    do_quit = do_exit


if __name__ == "__main__":
    SharedAgentsTerminal().cmdloop()
