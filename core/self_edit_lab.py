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
