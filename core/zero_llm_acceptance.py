"""Zero-LLM acceptance contract for the installable EIDOS core.

This module is intentionally dependency-light and deterministic.  It proves that
EIDOS can boot useful core machinery without importing or contacting an LLM,
close one real controlled filesystem effect through Runtime Hub verification,
persist RL learning, survive a fresh Python process, and validate a self-edit in
staging without overwriting its source.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path
from typing import Any

FORBIDDEN_PREFIXES = (
    "core.eidos_llm",
    "core.ollama_fallback",
    "torch",
    "transformers",
    "openai",
)


def _home() -> Path:
    raw = os.environ.get("EIDOS_HOME")
    if not raw:
        raise RuntimeError("EIDOS_HOME must be set for zero-LLM acceptance")
    home = Path(raw).expanduser().resolve()
    home.mkdir(parents=True, exist_ok=True)
    return home


def _assert_zero_llm_loaded() -> None:
    loaded = sorted(
        name
        for name in sys.modules
        if any(name == prefix or name.startswith(prefix + ".")
               for prefix in FORBIDDEN_PREFIXES)
    )
    if loaded:
        raise AssertionError(f"zero-LLM contract violated; loaded modules: {loaded}")


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    path.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")


def learn_phase() -> dict[str, Any]:
    home = _home()
    _assert_zero_llm_loaded()

    from core.eidos_rl import QLearningAgent
    from core.runtime_hub import EIDOSRuntimeHub
    from core.self_edit_lab import stage_candidate

    _assert_zero_llm_loaded()

    hub = EIDOSRuntimeHub()
    snap = hub.snapshot()
    assert snap["safety"]["executes_actions"] is False
    assert snap["safety"]["effect_verification_available"] is True

    fixture = home / "acceptance_fixture.txt"
    if fixture.exists():
        fixture.unlink()

    before = hub.record_observation(
        {
            "ocr_full_text": "fixture absent",
            "window_title": "zero-llm-acceptance",
            "regions": [],
        },
        observer="acceptance-observer",
    )
    proposal = hub.propose_action(
        {"action": "write_fixture", "path": str(fixture)},
        source="acceptance-core",
        expected_outcome="fixture present",
    )
    fixture.write_text("fixture present", encoding="utf-8")
    execution = hub.record_action_execution(
        proposal,
        executor="acceptance-fixture-executor",
        execution_id="fixture-write-1",
    )
    after = hub.record_observation(
        {
            "ocr_full_text": fixture.read_text(encoding="utf-8"),
            "window_title": "zero-llm-acceptance",
            "regions": [],
        },
        observer="acceptance-observer",
    )
    outcome = hub.verify_observations(
        proposal,
        before["observation_id"],
        after["observation_id"],
    )
    assert outcome["observed_verified"] is True
    assert outcome["credited_verified"] is True
    assert outcome["status"] == "verified"
    assert outcome["evidence_independent"] is True

    rl = QLearningAgent()
    state = rl.hash_state(["fixture", "absent"])
    next_state = rl.hash_state(["fixture", "present"])
    action = "write_fixture"
    rl.learn(state, action, 10.0, next_state, node_names="fixture,absent")
    q_value = rl.q_value_report(state).get(action, 0.0)
    assert q_value > 0.0

    source = home / "self_edit_source.py"
    source.write_text("VALUE = 1\n", encoding="utf-8")
    staged, assessment = stage_candidate(source, "VALUE = 2\n")
    assert assessment.accepted is True
    assert source.read_text(encoding="utf-8") == "VALUE = 1\n"
    assert staged.read_text(encoding="utf-8") == "VALUE = 2\n"
    assert staged.resolve() != source.resolve()

    contract = {
        "phase": "learn",
        "state": state,
        "next_state": next_state,
        "action": action,
        "q_value": q_value,
        "proposal_id": proposal["proposal_id"],
        "execution_id": execution["execution_id"],
        "effect_verified": outcome["credited_verified"],
        "fixture": str(fixture),
        "self_edit_source": str(source),
        "runtime_components": len(snap["components"]),
    }
    _write_json(home / "zero_llm_acceptance.json", contract)
    _assert_zero_llm_loaded()
    return contract


def verify_phase() -> dict[str, Any]:
    home = _home()
    _assert_zero_llm_loaded()

    contract_path = home / "zero_llm_acceptance.json"
    if not contract_path.exists():
        raise RuntimeError("learn phase evidence is missing")
    previous = json.loads(contract_path.read_text(encoding="utf-8"))

    from core.eidos_rl import QLearningAgent
    from core.runtime_hub import EIDOSRuntimeHub

    _assert_zero_llm_loaded()

    fixture = Path(previous["fixture"])
    assert fixture.read_text(encoding="utf-8") == "fixture present"

    rl = QLearningAgent()
    state = previous["state"]
    action = previous["action"]
    report = rl.q_value_report(state)
    q_value = report.get(action, 0.0)
    assert q_value > 0.0
    chosen = rl.best_action(state, [action, "unlearned_alternative"])
    assert chosen == action

    source = Path(previous["self_edit_source"])
    assert source.read_text(encoding="utf-8") == "VALUE = 1\n"

    hub = EIDOSRuntimeHub()
    snap = hub.snapshot()
    assert snap["safety"]["executes_actions"] is False

    result = {
        "phase": "verify-after-restart",
        "persisted_q_value": q_value,
        "chosen_action": chosen,
        "fixture_effect_persisted": True,
        "self_edit_source_unchanged": True,
        "runtime_components": len(snap["components"]),
        "llm_modules_loaded": False,
    }
    _assert_zero_llm_loaded()
    return result


def main() -> None:
    parser = argparse.ArgumentParser(description="EIDOS zero-LLM core acceptance")
    parser.add_argument("phase", choices=("learn", "verify"))
    args = parser.parse_args()
    result = learn_phase() if args.phase == "learn" else verify_phase()
    print(json.dumps(result, sort_keys=True))


if __name__ == "__main__":
    main()
