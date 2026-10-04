"""
EIDOS Self-Edit Lab
===================

Stages and validates Python self-edits without modifying the source checkout.

The lab is intentionally conservative:
- no shell execution
- no live-file overwrite
- syntax must parse
- high-risk dynamic-execution calls are rejected
- candidate is written only inside a temporary directory
"""
from __future__ import annotations

import ast
from dataclasses import dataclass
from difflib import unified_diff
from pathlib import Path
import tempfile
from typing import List


FORBIDDEN_CALLS = {"eval", "exec", "compile", "__import__"}


@dataclass(frozen=True)
class EditAssessment:
    accepted: bool
    reasons: List[str]
    changed_lines: int
    new_imports: List[str]
    diff: str

    def to_dict(self) -> dict:
        return {
            "accepted": self.accepted,
            "reasons": list(self.reasons),
            "changed_lines": self.changed_lines,
            "new_imports": list(self.new_imports),
            "diff": self.diff,
        }


def _imports(tree: ast.AST) -> set[str]:
    result: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            result.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            result.add(node.module)
    return result


def assess_python_edit(original: str, candidate: str, max_growth_ratio: float = 2.0) -> EditAssessment:
    reasons: List[str] = []
    try:
        original_tree = ast.parse(original)
    except SyntaxError as exc:
        return EditAssessment(False, [f"original source is invalid: {exc}"], 0, [], "")

    try:
        candidate_tree = ast.parse(candidate)
    except SyntaxError as exc:
        return EditAssessment(False, [f"candidate syntax error: {exc}"], 0, [], "")

    original_lines = original.splitlines()
    candidate_lines = candidate.splitlines()
    if len(candidate_lines) > max(1, int(len(original_lines) * max_growth_ratio)):
        reasons.append("candidate grows file beyond configured limit")

    dangerous = []
    for node in ast.walk(candidate_tree):
        if isinstance(node, ast.Call):
            if isinstance(node.func, ast.Name) and node.func.id in FORBIDDEN_CALLS:
                dangerous.append(node.func.id)
            if (
                isinstance(node.func, ast.Attribute)
                and isinstance(node.func.value, ast.Name)
                and node.func.value.id == "os"
                and node.func.attr == "system"
            ):
                dangerous.append("os.system")
    if dangerous:
        reasons.append("dynamic/high-risk execution introduced: " + ", ".join(sorted(set(dangerous))))

    before_imports = _imports(original_tree)
    after_imports = _imports(candidate_tree)
    new_imports = sorted(after_imports - before_imports)

    diff_lines = list(unified_diff(
        original_lines,
        candidate_lines,
        fromfile="before.py",
        tofile="candidate.py",
        lineterm="",
    ))
    changed = sum(
        1 for line in diff_lines
        if (line.startswith("+") or line.startswith("-"))
        and not line.startswith("+++")
        and not line.startswith("---")
    )

    if changed == 0:
        reasons.append("candidate makes no change")

    return EditAssessment(
        accepted=not reasons,
        reasons=reasons,
        changed_lines=changed,
        new_imports=new_imports,
        diff="\n".join(diff_lines),
    )


def stage_candidate(source_path: str | Path, candidate: str) -> tuple[Path, EditAssessment]:
    source = Path(source_path).expanduser().resolve()
    original = source.read_text(encoding="utf-8")
    assessment = assess_python_edit(original, candidate)
    temp_dir = Path(tempfile.mkdtemp(prefix="eidos-self-edit-"))
    staged = temp_dir / source.name
    staged.write_text(candidate, encoding="utf-8")
    return staged, assessment


# A deliberately small, deterministic regression gate that is known to run
# on a clean public checkout. Hardware/private-state tests stay outside this list.
SAFE_REGRESSION_MODULES = (
    "tests.test_self_edit_lab",
    "tests.test_self_edit_guardrails",
    "tests.test_self_edit_staging_integrity",
    "tests.test_fly_lab",
    "tests.test_runtime_hub",
    "tests.test_agents_terminal",
    "tests.test_autonomy_benchmark",
    "tests.test_hardware_validation",
    "tests.test_constitution",
    "tests.test_colony_community",
    "tests.test_colony_effect_reputation",
    "tests.test_character_lifecycle_contract",
    "tests.test_character_neuron_inheritance",
    "tests.test_skill_evolver_safety",
    "tests.test_dynamic_tools_promotion",
    "tests.test_action_verifier_causality",
    "tests.test_causal_loop_safety",
    "tests.test_recovery_causality",
    "tests.test_learning_retention_contract",
    "tests.test_antibiblioteca_causality",
    "tests.test_rl_persistence",
    "tests.test_actuator_selector",
    "tests.test_eidos_cli_entrypoint",
    "tests.test_hebbian_pruning_contract",
    "tests.test_operation_modes_security",
    "tests.test_db_thread_contract",
    "tests.test_model_fabric_contract",
)


@dataclass(frozen=True)
class RegressionGateResult:
    passed: bool
    returncode: int
    tests_run: int
    stdout: str
    stderr: str
    modules: tuple[str, ...]

    def to_dict(self) -> dict:
        return {
            "passed": self.passed,
            "returncode": self.returncode,
            "tests_run": self.tests_run,
            "stdout": self.stdout,
            "stderr": self.stderr,
            "modules": list(self.modules),
        }


def _parse_unittest_count(output: str) -> int:
    """Extract the unittest 'Ran N tests' count without treating prose as proof."""
    import re

    match = re.search(r"Ran\s+(\d+)\s+tests?", output)
    return int(match.group(1)) if match else 0


def run_safe_regression_suite(
    root: str | Path | None = None,
    *,
    timeout: int = 180,
    modules: tuple[str, ...] = SAFE_REGRESSION_MODULES,
) -> RegressionGateResult:
    """Run the public, deterministic regression gate in an isolated checkout.

    This is a regression gate, not a claim of held-out generalization. A truly
    private/held-out evaluator must be supplied by a separate environment.
    """
    import subprocess
    import sys

    checkout = Path(root).expanduser().resolve() if root else Path(__file__).resolve().parents[1]
    existing = tuple(
        module for module in modules
        if (checkout / (module.replace(".", "/") + ".py")).exists()
    )
    if not existing:
        return RegressionGateResult(
            passed=False,
            returncode=2,
            tests_run=0,
            stdout="",
            stderr="no safe regression modules found",
            modules=(),
        )

    proc = subprocess.run(
        [sys.executable, "-m", "unittest", "-v", *existing],
        cwd=checkout,
        capture_output=True,
        text=True,
        timeout=timeout,
        check=False,
    )
    combined = (proc.stdout or "") + "\n" + (proc.stderr or "")
    return RegressionGateResult(
        passed=proc.returncode == 0,
        returncode=proc.returncode,
        tests_run=_parse_unittest_count(combined),
        stdout=proc.stdout or "",
        stderr=proc.stderr or "",
        modules=existing,
    )
