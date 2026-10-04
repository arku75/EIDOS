#!/usr/bin/env python3
"""Compare a filesystem `tree` snapshot with a Git tracked manifest.

The snapshot may contain backups, virtualenvs, databases and third-party trees.
Those are classified instead of being mistaken for missing source code.

Usage:
    python tools/tree_compare.py EIDOS_TREE_COMPLETO.txt --repo-root .
    python tools/tree_compare.py snapshot.txt --manifest github_paths.txt \
        --json report.json --markdown report.md
"""
from __future__ import annotations

import argparse
import json
import re
import subprocess
from collections import Counter
from pathlib import Path
from typing import Iterable

TREE_LINE = re.compile(
    r"^(?P<prefix>.*?)(?:├──|└──) \[(?P<size>[^\]]+)\]\s{2}(?P<name>.*)$"
)

BACKUP_TOPS = {
    "archivo", "backups", "Backups", "_backups", "backups-cerebro",
    "mac_rescate_s300", "_residuos", "memoria_fenix",
}
GENERATED_TOPS = {
    ".git", ".venv-py314", ".venv.MUERTO-py313-ARCHIVADO",
    ".mypy_cache", ".ruff_cache", ".pytest_cache", "__pycache__",
    "graphify-out", "build", ".nyc_output", ".nuclei-cache",
    ".nuclei-config",
}
THIRD_PARTY_TOPS = {
    "openclaw", "openclaw_master_skills", "vendor", "external",
    "external_repos", "opensource_research", "nuclei", "DefaultPackage",
}
STATE_TOPS = {
    "data", "EIDOS_Knowledge", "EIDOS_Uploads", "OpenClaw_Data",
    "OpenClaw_Config", "OpenClaw_Workspace", "maker_output", "workspace",
    "models", "logs", "deepseek_audit", "debate_results_v2", "research",
    "research_notes", "analysis_reports", "informes",
}
HISTORICAL_MARKERS = (
    ".bak", ".pre-", ".pre_", ".old", ".orig", ".snapshot",
    ".removed", ".dead", ".parcial", ".quarantine", ".archivado",
)
GENERATED_COMPONENTS = {
    "target", "node_modules", "__pycache__", ".pytest_cache", ".mypy_cache",
    ".ruff_cache", ".cache", ".nyc_output", ".next", ".parcel-cache",
    "dist", "build", ".gradle", ".idea", ".vscode-test", "htmlcov", "site-packages",
}
HISTORY_COMPONENTS = {
    "_archived", "_archive", "_legacy", "_RESCATADO_DEL_ZIP",
    "_DEAD_CODE_20260530", "_DEAD_DBS_20260530", "release_tmp",
    "backups_pre_auditoria",
}
STATE_PREFIXES = ("VSEIDOS/data/", "VSEIDOS/cache/", "VSEIDOS/logs/")
ROOT_STATE_SUFFIXES = (".db", ".sqlite", ".sqlite3", ".log", ".dump", ".core", ".pid", ".sock")
ROOT_GENERATED_SUFFIXES = (".aux", ".bib", ".exe", ".o", ".out", ".tree", ".class", ".jar", ".tmp")
ROOT_NOISE_PREFIXES = (".screenshot", ".my.cnf.", ".mysql.", ".my.output.", "#cvsblame.", "temp", "cvsbackport.")
ROOT_GENERATED_NAMES = {
    "allclasses-frame.html", "deprecated-list.html", "help-doc.html",
    "index-all.html", "inherit.gif", "overview-frame.html", "overview-summary.html",
    "package-list", "stylesheet.css", "NamespaceSummaries.xml", "NewClasses.plist",
    "help.pdf", "image0", "pp.save", "a.out",
}


def _clean_tree_name(raw: str) -> tuple[str, bool]:
    is_dir = raw.endswith("/")
    name = raw.rstrip("/")
    if name and name[-1:] in {"*", "@", "|", "="}:
        name = name[:-1]
    if " -> " in name:
        name = name.split(" -> ", 1)[0]
    return name, is_dir


def parse_tree_snapshot(path: Path) -> list[dict]:
    """Parse GNU/tree-style output into normalized relative paths."""
    stack: list[str] = []
    entries: list[dict] = []
    for lineno, line in enumerate(path.read_text(errors="replace").splitlines()[1:], 2):
        match = TREE_LINE.match(line)
        if not match:
            continue
        branch = line.find("├──")
        if branch < 0:
            branch = line.find("└──")
        depth = branch // 4 + 1
        name, is_dir = _clean_tree_name(match.group("name"))
        stack = stack[: depth - 1]
        rel = "/".join([*stack, name])
        entries.append(
            {
                "line": lineno,
                "path": rel,
                "depth": depth,
                "is_dir": is_dir,
                "size": match.group("size").strip(),
            }
        )
        if is_dir:
            stack.append(name)
    return entries


def classify(path: str) -> str:
    top = path.split("/", 1)[0]
    lower = path.lower()
    parts = set(path.split("/"))
    if (
        top in BACKUP_TOPS
        or parts & HISTORY_COMPONENTS
        or any(marker in lower for marker in HISTORICAL_MARKERS)
    ):
        return "backup_history"
    if top in GENERATED_TOPS or parts & GENERATED_COMPONENTS:
        return "generated_env_vcs_cache"
    if any(path.startswith(prefix) for prefix in STATE_PREFIXES):
        return "state_data_reports"
    if top in THIRD_PARTY_TOPS or "vendor" in parts:
        return "third_party_vendor"
    if top in STATE_TOPS:
        return "state_data_reports"
    if "/" not in path:
        if path.endswith(ROOT_STATE_SUFFIXES):
            return "state_data_reports"
        if (
            path.endswith(ROOT_GENERATED_SUFFIXES)
            or path in ROOT_GENERATED_NAMES
            or path.startswith(ROOT_NOISE_PREFIXES)
        ):
            return "generated_env_vcs_cache"
    return "project_candidate"


def git_tracked_files(repo_root: Path) -> set[str]:
    proc = subprocess.run(
        ["git", "-C", str(repo_root), "ls-files", "-z"],
        check=True,
        stdout=subprocess.PIPE,
    )
    return {p for p in proc.stdout.decode("utf-8").split("\0") if p}


def manifest_files(path: Path) -> set[str]:
    return {
        line.strip()
        for line in path.read_text(errors="replace").splitlines()
        if line.strip() and not line.lstrip().startswith("#")
    }


def compare(snapshot_entries: Iterable[dict], tracked: set[str]) -> dict:
    files = [entry for entry in snapshot_entries if not entry["is_dir"]]
    local = {entry["path"] for entry in files}
    local_project = {
        entry["path"] for entry in files if classify(entry["path"]) == "project_candidate"
    }
    categories = Counter(classify(entry["path"]) for entry in files)

    return {
        "snapshot_files": len(local),
        "snapshot_project_candidates": len(local_project),
        "tracked_files": len(tracked),
        "common_all": sorted(local & tracked),
        "local_only_project_candidates": sorted(local_project - tracked),
        "tracked_only": sorted(tracked - local),
        "category_file_counts": dict(sorted(categories.items())),
    }


def render_markdown(result: dict) -> str:
    local_only = result["local_only_project_candidates"]
    tracked_only = result["tracked_only"]
    common = result["common_all"]
    cats = result["category_file_counts"]

    lines = [
        "# EIDOS tree comparison",
        "",
        "This report compares filenames/paths only. It does not claim that files",
        "with matching names have matching contents.",
        "",
        "## Counts",
        "",
        f"- Snapshot files: **{result['snapshot_files']}**",
        f"- Snapshot project candidates: **{result['snapshot_project_candidates']}**",
        f"- Git tracked files: **{result['tracked_files']}**",
        f"- Common paths: **{len(common)}**",
        f"- Project-candidate paths only in snapshot: **{len(local_only)}**",
        f"- Paths only in Git: **{len(tracked_only)}**",
        "",
        "## Snapshot classification",
        "",
    ]
    for key, value in sorted(cats.items()):
        lines.append(f"- {key}: **{value}**")
    lines.extend(["", "## Project candidates only in snapshot", ""])
    lines.extend(f"- `{path}`" for path in local_only)
    lines.extend(["", "## Paths only in Git", ""])
    lines.extend(f"- `{path}`" for path in tracked_only)
    lines.append("")
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("snapshot", type=Path)
    source = parser.add_mutually_exclusive_group(required=False)
    source.add_argument("--manifest", type=Path)
    source.add_argument("--repo-root", type=Path, default=None)
    parser.add_argument("--json", type=Path, dest="json_out")
    parser.add_argument("--markdown", type=Path, dest="md_out")
    args = parser.parse_args()

    entries = parse_tree_snapshot(args.snapshot)
    if args.manifest:
        tracked = manifest_files(args.manifest)
    else:
        tracked = git_tracked_files(args.repo_root or Path("."))

    result = compare(entries, tracked)
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
