#!/bin/bash
# ═══════════════════════════════════════════════════════════════════════
# scripts/sync_to_mac.sh — Rsync EIDOS backups to Mac (off-machine)
# ═══════════════════════════════════════════════════════════════════════
# Syncs the ~/.eidos/backups/ directory to Mac via SSH.
# Uses the 'mac' Host from ~/.ssh/config (tunnel localhost:2222 or
# mac-lan 192.168.178.62, or mac-external DDNS).
#
# Uso: ./scripts/sync_to_mac.sh
#   Auto-detects best route: local tunnel > LAN > external DDNS.
# ═══════════════════════════════════════════════════════════════════════

set -euo

BACKUP_DIR="${HOME}/.eidos/backups"
MAC_HOST=""
MAC_BACKUP_DIR="/Users/luka/.eidos/backups"
LOG="${HOME}/.eidos/logs/mac_sync.log"
LOCKFILE="${HOME}/.eidos/.mac_sync.lock"

mkdir -p "$(dirname "$LOG")"

# ── Prevent concurrent runs ────────────────────────────────────────────
exec 200>"$LOCKFILE"
if ! flock -n 200; then
    echo "[$(date '+%Y-%m-%d %H:%M:%S')] sync_to_mac: another instance running, exiting"
    exit 0
fi

# ── Detect best Mac route ──────────────────────────────────────────────
detect_mac_host() {
    # 1. Try local tunnel (localhost:2222) — fastest, always available if tunnel is up
    if nc -z -w 2 127.0.0.1 2222 2>/dev/null; then
        MAC_HOST="mac"
        echo "[$(date '+%Y-%m-%d %H:%M:%S')] Route: tunnel (localhost:2222)"
        return 0
    fi
    # 2. Try LAN direct
    if nc -z -w 2 192.168.178.62 22 2>/dev/null; then
        MAC_HOST="mac-lan"
        echo "[$(date '+%Y-%m-%d %H:%M:%S')] Route: LAN (192.168.178.62:22)"
        return 0
    fi
    # 3. Try external DDNS
    if nc -z -w 3 l2b4arv3mao7mr7z.myfritz.net 22 2>/dev/null; then
        MAC_HOST="mac-external"
        echo "[$(date '+%Y-%m-%d %H:%M:%S')] Route: external (DDNS)"
        return 0
    fi
    echo "[$(date '+%Y-%m-%d %H:%M:%S')] Mac unreachable — sync skipped" >> "$LOG"
    return 1
}

if ! detect_mac_host; then
    exit 0
fi

# ── Ensure remote dir exists ───────────────────────────────────────────
ssh -o ConnectTimeout=5 "$MAC_HOST" "mkdir -p '$MAC_BACKUP_DIR'" 2>/dev/null || {
    echo "[$(date '+%Y-%m-%d %H:%M:%S')] Cannot create remote dir — Mac may be sleeping" >> "$LOG"
    exit 0
}

# ── Rsync backups ──────────────────────────────────────────────────────
# --delete removes old backups on Mac that are gone from Kali
# --exclude full/ on hourly syncs (huge); only sync full dir on explicit --full
echo "[$(date '+%Y-%m-%d %H:%M:%S')] Syncing $BACKUP_DIR → $MAC_HOST:$MAC_BACKUP_DIR" >> "$LOG"

rsync -avz \
    --timeout=60 \
    --delete \
    --exclude='*.tmp' \
    -e "ssh -o ConnectTimeout=10" \
    "$BACKUP_DIR/" \
    "$MAC_HOST:$MAC_BACKUP_DIR/" 2>&1 | tail -3 >> "$LOG"

RSYNC_EXIT=${PIPESTATUS[0]}

if [ "$RSYNC_EXIT" -eq 0 ]; then
    echo "[$(date '+%Y-%m-%d %H:%M:%S')] Sync OK ($(du -sh "$BACKUP_DIR" | cut -f1) total)" >> "$LOG"
    exit 0
else
    echo "[$(date '+%Y-%m-%d %H:%M:%S')] Sync FAILED (exit $RSYNC_EXIT)" >> "$LOG"
    exit 1
fi
