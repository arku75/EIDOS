#!/bin/bash
# ═══════════════════════════════════════════════════════════════════════
# scripts/eidos_heartbeat.sh — Kali↔Mac heartbeat protocol
# ═══════════════════════════════════════════════════════════════════════
# Every 60s, each side writes a timestamp file to the other via SSH.
# If the peer's heartbeat file is >120s old, the peer is "dead".
#
# Kali-side deployment:
#   systemd timer: eidos-heartbeat.timer (every 60s)
#   systemd service: eidos-heartbeat.service
#
# Mac-side deployment:
#   LaunchAgent: com.eidos.heartbeat.plist
#   Or cron:     * * * * * /Users/luka/EIDOS/scripts/eidos_heartbeat.sh
#
# File locations:
#   Kali writes to:   Mac at /Users/luka/.eidos/heartbeat/kali.heartbeat
#   Kali reads from:  ~/.eidos/heartbeat/mac.heartbeat
#   Mac writes to:    Kali at ~/.eidos/heartbeat/mac.heartbeat
#   Mac reads from:   /Users/luka/.eidos/heartbeat/kali.heartbeat
#
# Status check (either side):
#   ./scripts/eidos_heartbeat.sh --status
# ═══════════════════════════════════════════════════════════════════════

set -euo pipefail

HEARTBEAT_DIR="${HOME}/.eidos/heartbeat"
MY_HEARTBEAT="${HEARTBEAT_DIR}/kali.heartbeat"       # Kali writes this
PEER_HEARTBEAT="${HEARTBEAT_DIR}/mac.heartbeat"       # Mac writes this (Kali reads)
MAC_HOST=""
MAC_HEARTBEAT_DIR="/Users/luka/.eidos/heartbeat"
MAC_HEARTBEAT_FILE="${MAC_HEARTBEAT_DIR}/kali.heartbeat"  # Kali's file on Mac
LOG="${HOME}/.eidos/logs/heartbeat.log"
MAX_AGE=120  # seconds — peer considered DEAD if heartbeat older than this

# Ensure heartbeat dir exists (remove any old file that blocks mkdir -p)
[ -f "$HEARTBEAT_DIR" ] && rm -f "$HEARTBEAT_DIR"
mkdir -p "$HEARTBEAT_DIR"
mkdir -p "$(dirname "$LOG")"

# ── Parse args ─────────────────────────────────────────────────────────
if [ "${1:-}" = "--status" ]; then
    echo "═══ EIDOS Heartbeat Status (Kali) ═══"
    echo ""

    # My own heartbeat
    if [ -f "$MY_HEARTBEAT" ]; then
        MY_AGE=$(($(date +%s) - $(cat "$MY_HEARTBEAT")))
        echo "Kali heartbeat: $(cat "$MY_HEARTBEAT") (${MY_AGE}s ago) ✅"
    else
        echo "Kali heartbeat: MISSING ❌"
    fi

    # Peer heartbeat
    if [ -f "$PEER_HEARTBEAT" ]; then
        PEER_AGE=$(($(date +%s) - $(cat "$PEER_HEARTBEAT")))
        if [ "$PEER_AGE" -gt "$MAX_AGE" ]; then
            echo "Mac heartbeat:  $(cat "$PEER_HEARTBEAT") (${PEER_AGE}s ago) 💀 DEAD (>${MAX_AGE}s)"
        else
            echo "Mac heartbeat:  $(cat "$PEER_HEARTBEAT") (${PEER_AGE}s ago) ✅"
        fi
    else
        echo "Mac heartbeat:  MISSING 💀 NEVER SEEN"
    fi

    echo ""
    echo "Heartbeat dir: $HEARTBEAT_DIR"
    ls -la "$HEARTBEAT_DIR/" 2>/dev/null || echo "  (empty)"
    exit 0
fi

# ── Detect Mac route ──────────────────────────────────────────────────
detect_mac() {
    if nc -z -w 2 127.0.0.1 2222 2>/dev/null; then
        MAC_HOST="mac"
        return 0
    elif nc -z -w 2 192.168.178.62 22 2>/dev/null; then
        MAC_HOST="mac-lan"
        return 0
    elif nc -z -w 3 l2b4arv3mao7mr7z.myfritz.net 22 2>/dev/null; then
        MAC_HOST="mac-external"
        return 0
    fi
    return 1
}

# ── Write my heartbeat locally ─────────────────────────────────────────
date +%s > "$MY_HEARTBEAT"

# ── Write my heartbeat on Mac ──────────────────────────────────────────
if detect_mac; then
    ssh -o ConnectTimeout=5 -o BatchMode=yes "$MAC_HOST" \
        "mkdir -p '$MAC_HEARTBEAT_DIR' && date +%s > '$MAC_HEARTBEAT_FILE'" \
        2>/dev/null || true
fi

# ── Fetch Mac's heartbeat ──────────────────────────────────────────────
if detect_mac; then
    ssh -o ConnectTimeout=5 -o BatchMode=yes "$MAC_HOST" \
        "cat '$MAC_HEARTBEAT_DIR/mac.heartbeat' 2>/dev/null || echo 0" \
        2>/dev/null > "${PEER_HEARTBEAT}.tmp" || true

    if [ -s "${PEER_HEARTBEAT}.tmp" ]; then
        PEER_TS=$(cat "${PEER_HEARTBEAT}.tmp")
        if [ "$PEER_TS" != "0" ]; then
            echo "$PEER_TS" > "$PEER_HEARTBEAT"

            # Check if peer is dead
            PEER_AGE=$(($(date +%s) - PEER_TS))
            if [ "$PEER_AGE" -gt "$MAX_AGE" ]; then
                echo "[$(date '+%Y-%m-%d %H:%M:%S')] ⚠️  MAC DEAD — heartbeat ${PEER_AGE}s old (threshold: ${MAX_AGE}s)" >> "$LOG"
            fi
        fi
    fi
    rm -f "${PEER_HEARTBEAT}.tmp"
fi

exit 0
