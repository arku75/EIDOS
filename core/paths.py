"""Portable path policy for EIDOS."""
from __future__ import annotations
import os
from pathlib import Path

USER_HOME = Path.home()
PACKAGE_ROOT = Path(__file__).resolve().parents[1]
REPO_ROOT = Path(os.environ.get("EIDOS_SOURCE_ROOT", str(PACKAGE_ROOT))).expanduser().resolve()
EIDOS_HOME = Path(os.environ.get("EIDOS_HOME", str(USER_HOME / ".eidos"))).expanduser().resolve()
SANDBOX_ROOT = Path(os.environ.get("EIDOS_SANDBOX_ROOT", str(EIDOS_HOME / "sandbox"))).expanduser().resolve()
ARCHIVE_ROOT = Path(os.environ.get("EIDOS_ARCHIVE_ROOT", str(EIDOS_HOME / "archive"))).expanduser().resolve()
