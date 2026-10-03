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
        "World · Actions · Characters · Neural Graph · Fly Lab · Agent Bus\n"
        "Type 'help' for commands. Action commands create proposals only."
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
        """propose JSON -- create a gated action proposal, never execute it"""
        try:
            action = json.loads(arg)
            if not isinstance(action, dict):
                raise ValueError("proposal must be a JSON object")
        except Exception as exc:
            print(f"invalid JSON: {exc}")
            return
        print(json.dumps(self.hub.propose_action(action), indent=2))

    def do_exit(self, arg: str) -> bool:
        """exit -- close terminal"""
        return True

    do_quit = do_exit


if __name__ == "__main__":
    SharedAgentsTerminal().cmdloop()
