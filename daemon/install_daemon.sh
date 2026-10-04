#!/usr/bin/env bash
# Portable installer for the EIDOS continuous-learning daemon.
# Installs Python dependencies into an isolated project venv. systemd activation
# remains explicit because it changes host state and requires administrator access.
set -euo pipefail

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
EIDOS_ROOT="$(cd -- "$SCRIPT_DIR/.." && pwd)"
VENV="${EIDOS_VENV:-$EIDOS_ROOT/.venv}"
PYTHON="${PYTHON:-python3}"

printf 'EIDOS learning daemon installer\nroot: %s\nvenv: %s\n' "$EIDOS_ROOT" "$VENV"

"$PYTHON" -m venv "$VENV"
"$VENV/bin/python" -m pip install --upgrade pip
"$VENV/bin/python" -m pip install yt-dlp PyPDF2 beautifulsoup4 requests

mkdir -p "$HOME/.eidos"/{logs,downloads,transcripts,extracted_knowledge}
test -f "$EIDOS_ROOT/daemon/eidos_learning_daemon.py"
test -f "$EIDOS_ROOT/core/continuous_learner.py"

cat <<EOF
Dependencies installed in $VENV.

Run manually:
  $VENV/bin/python $EIDOS_ROOT/daemon/eidos_learning_daemon.py

Optional systemd installation (explicit host mutation):
  sudo install -m 0644 $EIDOS_ROOT/daemon/eidos-learning.service /etc/systemd/system/eidos-learning.service
  sudo systemctl daemon-reload
  sudo systemctl enable --now eidos-learning.service

The service template uses EIDOS_ROOT/EIDOS_VENV placeholders. Create a host-specific
copy before installing it; do not commit machine-specific paths.
EOF
