#!/bin/bash
# git-snapshot-run.sh — Git Guardian: snapshot LOCAL diario del repo EIDOS [S123]
# Llamado por eidos-git-snapshot.timer.
# Commitea en 'eidos-local' y hace push al backup local (~/.eidos/git-backup/EIDOS.git).
# GitHub remoto (origin) está DELETED — backup local es la única copia remota.
# Solo commitea en la rama 'eidos-local' (si SER está en otra rama, se omite).

set -uo pipefail

EIDOS_DIR="/home/ser/EIDOS"
LOG_FILE="$HOME/.eidos/logs/git_snapshot.log"
mkdir -p "$(dirname "$LOG_FILE")"

cd "$EIDOS_DIR"

branch=$(git branch --show-current 2>/dev/null || echo "")
if [ "$branch" != "eidos-local" ]; then
    echo "[$(date -Iseconds)] rama actual '$branch' != eidos-local — snapshot omitido, OK" >> "$LOG_FILE"
    exit 0
fi

if git diff-index --quiet HEAD -- 2>/dev/null && [ -z "$(git status --porcelain)" ]; then
    echo "[$(date -Iseconds)] sin cambios — nada que commitear" >> "$LOG_FILE"
    exit 0
fi

git add -A
git commit -m "Git Guardian: snapshot local automático $(date '+%Y-%m-%d %H:%M')" >> "$LOG_FILE" 2>&1
echo "[$(date -Iseconds)] snapshot commiteado ($(git rev-parse --short HEAD))" >> "$LOG_FILE"

# Push to local backup (bare repo) — GitHub origin is dead
if git remote get-url backup &>/dev/null; then
    git push backup eidos-local >> "$LOG_FILE" 2>&1 && \
        echo "[$(date -Iseconds)] push a backup OK" >> "$LOG_FILE" || \
        echo "[$(date -Iseconds)] push a backup FALLÓ" >> "$LOG_FILE"
fi

exit 0
