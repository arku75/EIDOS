#!/bin/bash
# ═══════════════════════════════════════════════════════════════════════
# eidos_heartbeat_mac.sh — Mac-side heartbeat protocol
# ═══════════════════════════════════════════════════════════════════════
# Deploy to Mac: scp this file to mac:~/.eidos/scripts/ then set up:
#
#   LaunchAgent: ~/Library/LaunchAgents/com.eidos.heartbeat.plist
#     <key>StartInterval</key><integer>60</integer>
#     <key>ProgramArguments</key>
#     <array>
#       <string>/bin/bash</string>
#       <string>/Users/luka/.eidos/scripts/eidos_heartbeat_mac.sh</string>
#     </array>
#
# Or cron: * * * * * /bin/bash /Users/luka/.eidos/scripts/eidos_heartbeat_mac.sh
#
# File locations:
#   Mac writes to:    Kali at ~/.eidos/heartbeat/mac.heartbeat (via SSH)
#   Mac reads from:   /Users/luka/.eidos/heartbeat/kali.heartbeat (written by Kali via SSH)
# ═══════════════════════════════════════════════════════════════════════

set -euo pipefail

HEARTBEAT_DIR="${HOME}/.eidos/heartbeat"
MY_HEARTBEAT="${HEARTBEAT_DIR}/mac.heartbeat"        # Mac writes this
PEER_HEARTBEAT="${HEARTBEAT_DIR}/kali.heartbeat"     # Kali writes this (Mac reads)
KALI_SSH="ser@localhost"                             # Reverse tunnel from Mac to Kali via tunnel-in
KALI_HEARTBEAT_DIR="/home/ser/.eidos/heartbeat"
KALI_HEARTBEAT_FILE="${KALI_HEARTBEAT_DIR}/mac.heartbeat"  # Mac's file on Kali
LOG="${HOME}/.eidos/logs/heartbeat.log"
MAX_AGE=120  # seconds

# Ensure dirs exist
[ -f "$HEARTBEAT_DIR" ] && rm -f "$HEARTBEAT_DIR"
mkdir -p "$HEARTBEAT_DIR"
mkdir -p "$(dirname "$LOG")"

# ── Status mode ──────────────────────────────────────────────────────
if [ "${1:-}" = "--status" ]; then
    echo "═══ EIDOS Heartbeat Status (Mac) ═══"
    if [ -f "$MY_HEARTBEAT" ]; then
        MY_AGE=$(($(date +%s) - $(cat "$MY_HEARTBEAT")))
        echo "Mac heartbeat:  $(cat "$MY_HEARTBEAT") (${MY_AGE}s ago) ✅"
    else
        echo "Mac heartbeat:  MISSING ❌"
    fi
    if [ -f "$PEER_HEARTBEAT" ]; then
        PEER_AGE=$(($(date +%s) - $(cat "$PEER_HEARTBEAT")))
        if [ "$PEER_AGE" -gt "$MAX_AGE" ]; then
            echo "Kali heartbeat: $(cat "$PEER_HEARTBEAT") (${PEER_AGE}s ago) 💀 DEAD"
        else
            echo "Kali heartbeat: $(cat "$PEER_HEARTBEAT") (${PEER_AGE}s ago) ✅"
        fi
    else
        echo "Kali heartbeat: MISSING 💀 NEVER SEEN"
    fi
    exit 0
fi

# ── Write my heartbeat locally ───────────────────────────────────────
date +%s > "$MY_HEARTBEAT"

# ── Write my heartbeat on Kali (via reverse SSH tunnel) ──────────────
ssh -o ConnectTimeout=5 -o BatchMode=yes -o StrictHostKeyChecking=no \
    -p 2222 -i ~/.ssh/id_eidos_mac "$KALI_SSH" \
    "mkdir -p '$KALI_HEARTBEAT_DIR' && date +%s > '$KALI_HEARTBEAT_FILE'" \
    2>/dev/null || true

# ── Fetch Kali's heartbeat (written by Kali to our disk) ─────────────
if [ -f "$PEER_HEARTBEAT" ]; then
    PEER_TS=$(cat "$PEER_HEARTBEAT")
    PEER_AGE=$(($(date +%s) - PEER_TS))
    if [ "$PEER_AGE" -gt "$MAX_AGE" ]; then
        echo "[$(date '+%Y-%m-%d %H:%M:%S')] ⚠️  KALI DEAD — heartbeat ${PEER_AGE}s old" >> "$LOG"
    fi
fi

exit 0
