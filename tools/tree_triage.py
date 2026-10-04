#!/usr/bin/env python3
"""Name-level triage for an EIDOS tree comparison report.

This is deliberately conservative: filename similarity is navigation evidence,
not proof that two modules are functionally equivalent.
"""
from __future__ import annotations

import argparse
import difflib
import json
import re
from pathlib import Path

VERSION_SUFFIX = re.compile(r"(?:_v\d+|_v\d+_\d+)$", re.I)


def normalized_stem(path: str) -> str:
    stem = Path(path).stem.lower().replace("-", "_")
    if stem.startswith("eidos_"):
        stem = stem[6:]
    stem = VERSION_SUFFIX.sub("", stem)
    return stem


def best_name_match(path: str, candidates: list[str]) -> tuple[str | None, float]:
    src = normalized_stem(path)
    best_path: str | None = None
    best_score = 0.0
    for candidate in sorted(candidates):
        dst = normalized_stem(candidate)
        score = 1.0 if src == dst else difflib.SequenceMatcher(None, src, dst).ratio()
        if score > best_score or (score == best_score and best_path and candidate < best_path):
            best_path, best_score = candidate, score
    return best_path, round(best_score, 3)


def triage(comparison: dict) -> dict:
    local_only = [
        p for p in comparison.get("local_only_project_candidates", [])
        if p.startswith("core/") and p.endswith(".py")
    ]
    github_core = sorted({
        p for p in (
            comparison.get("common_all", []) + comparison.get("tracked_only", [])
        )
        if p.startswith("core/") and p.endswith(".py")
    })

    rows = []
    for path in sorted(local_only):
        match, score = best_name_match(path, github_core)
        if score == 1.0:
            bucket = "direct_normalized_name_match"
        elif score >= 0.65:
            bucket = "similar_name_review"
        else:
            bucket = "name_unique_review"
        rows.append({
            "local_path": path,
            "bucket": bucket,
            "best_github_name_match": match,
            "name_similarity": score,
        })

    counts: dict[str, int] = {}
    for row in rows:
        counts[row["bucket"]] = counts.get(row["bucket"], 0) + 1

    return {
        "scope": "local-only core Python paths",
        "warning": (
            "Name similarity is not semantic equivalence. Source content must be "
            "read before deciding to migrate, replace, or discard a local module."
        ),
        "github_core_candidates": len(github_core),
        "local_only_core_python": len(local_only),
        "bucket_counts": dict(sorted(counts.items())),
        "rows": rows,
    }


def render_markdown(result: dict) -> str:
    lines = [
        "# EIDOS local-core name triage",
        "",
        f"> {result['warning']}",
        "",
        f"- Local-only core Python: **{result['local_only_core_python']}**",
        f"- GitHub core Python candidates: **{result['github_core_candidates']}**",
    ]
    for key, value in result["bucket_counts"].items():
        lines.append(f"- {key}: **{value}**")
    for bucket in (
        "direct_normalized_name_match",
        "similar_name_review",
        "name_unique_review",
    ):
        lines.extend(["", f"## {bucket}", ""])
        for row in result["rows"]:
            if row["bucket"] != bucket:
                continue
            lines.append(
                f"- `{row['local_path']}` -> "
                f"`{row['best_github_name_match']}` "
                f"({row['name_similarity']:.3f})"
            )
    lines.append("")
    return "\n".join(lines)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("comparison_json", type=Path)
    ap.add_argument("--json", dest="json_out", type=Path)
    ap.add_argument("--markdown", dest="md_out", type=Path)
    args = ap.parse_args()

    comparison = json.loads(args.comparison_json.read_text(encoding="utf-8"))
    result = triage(comparison)
    payload = json.dumps(result, indent=2, ensure_ascii=False, sort_keys=True)
    markdown = render_markdown(result)
    if args.json_out:
        args.json_out.write_text(payload + "\n", encoding="utf-8")
    if args.md_out:
        args.md_out.write_text(markdown, encoding="utf-8")
    if not args.json_out and not args.md_out:
        print(markdown)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
