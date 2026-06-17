#!/bin/bash
# ═══════════════════════════════════════════════════════════════════════
# scripts/backup_dbs.sh — EIDOS COMPREHENSIVE BACKUP (S125+)
# ═══════════════════════════════════════════════════════════════════════
# Backs up ALL SQLite DBs (~84), ChromaDB vector store, secrets, and
# optionally syncs to Mac via rsync.
#
# Uso: ./scripts/backup_dbs.sh [--full] [--sync-mac] [--test-integrity]
#   --full           : include ChromaDB + secrets (slower, daily)
#   --sync-mac       : rsync backup dir to Mac after completion
#   --test-integrity : run integrity_check on each backed-up DB
#
# Cron sugerido:
#   Horario: 0 * * * * /home/ser/EIDOS/scripts/backup_dbs.sh >> ~/.eidos/logs/backup.log 2>&1
#   Diario:  0 3 * * * /home/ser/EIDOS/scripts/backup_dbs.sh --full --sync-mac >> ~/.eidos/logs/backup_full.log 2>&1
#
# También llamado por systemd timer: eidos-backup.timer
# ═══════════════════════════════════════════════════════════════════════

set -euo pipefail

# ── Config ─────────────────────────────────────────────────────────────
BACKUP_DIR="${HOME}/.eidos/backups"
TIMESTAMP=$(date +%Y%m%d_%H%M%S)
LOG="${HOME}/.eidos/logs/backup.log"
RETENTION_DAYS=7                    # hourly backups kept 7 days
RETENTION_DAYS_FULL=30              # full backups kept 30 days
CHROMA_DATA="${HOME}/.eidos/chroma" # ChromaDB data dir (Rust CLI)
CHROMA_PORT=8767
SECRETS_DIR="${HOME}/.eidos"
SECRETS_FILES=(secrets.env .env)
GPG_RECIPIENT=""                    # set to key ID for encrypted secrets backup
MAC_HOST="mac"                      # SSH Host from ~/.ssh/config
MAC_BACKUP_DIR="/Users/luka/.eidos/backups"
FULL_MODE=false
SYNC_MAC=false
TEST_INTEGRITY=false

# ── Parse args ─────────────────────────────────────────────────────────
for arg in "$@"; do
    case "$arg" in
        --full)           FULL_MODE=true ;;
        --sync-mac)       SYNC_MAC=true ;;
        --test-integrity) TEST_INTEGRITY=true ;;
        --help|-h)
            echo "Uso: $0 [--full] [--sync-mac] [--test-integrity]"
            exit 0 ;;
    esac
done

mkdir -p "$BACKUP_DIR"
mkdir -p "$(dirname "$LOG")"
mkdir -p "${BACKUP_DIR}/full"
mkdir -p "${BACKUP_DIR}/secrets"

echo "════════════════════════════════════════════════"
echo "[$(date '+%Y-%m-%d %H:%M:%S')] EIDOS BACKUP START"
echo "   Mode: $( $FULL_MODE && echo 'FULL (DBs+ChromaDB+Secrets)' || echo 'HOURLY (DBs only)' )"
echo "   Dest: $BACKUP_DIR"
echo "════════════════════════════════════════════════"

# ── ALL EIDOS DATABASES (84 live DBs in ~/.eidos/) ───────────────────
# Excludes: Firefox profiles, browser_profile, labex_profile, hub/,
# sandbox/, _DEAD_DBS/, staging/, checkpoints/, phoenix/, training/,
# temp_learning/, mirror/
ALL_DBs=(
    # Core identity & memory
    alerts.db anomaly.db audit.db autodidact.db autonomous.db autonomy.db
    # Colony system
    colony_characters.db colony_chronicle.db colony_community.db colony_engine.db
    colony_intercomm.db colony_loop.db colony_proposals.db
    # Knowledge & learning
    curiosity_atlas.db decaying_memory.db doc_learner.db document_library.db
    dynamic_tools.db
    # Episodic & procedural
    episodic.db episodic_memory.db
    # Evolution & meta
    evolution_brain.db extensions.db
    # Feedback & metrics
    feedback.db ganglia.db
    # Governance & economy
    gate.db goals.db governance.db
    healing.db improvements.db income_system.db integration_hub.db
    # Knowledge
    kali_skills.db knowledge_graph.db
    # Lifecycle & management
    lifecycle.db life_rules.db log_watcher.db
    madmax.db master_state.db mastery_goals.db
    memory.db memory_rrf.db memory_vec.db metacog_real.db meta_learner.db
    metrics.db mission_metrics.db
    network.db oauth_proxy.db observational.db
    p2p_sync.db procedural_memory.db
    research.db router.db routines.db
    sandbox.db schedule.db scheduler.db screen_episodic.db
    self.db self_improvement.db ser_inbox.db ser_language.db
    ser_shell_log.db ser_speech.db service_monitor.db singularity.db
    skill_memory.db staging.db state.db story_universe.db
    swarm_knowledge.db tasks.db trust_model.db
    unified_memory.db video_knowledge.db vivo.db
    # Runtime state DBs
    brain_sync_state.db bugbot.db compute_usage.db
    convergence.db
    desires.db
    eidos.db eidos_tasks.db
    # economy is above, not duplicated
)

BACKED_UP=0
FAILED=0
CORRUPT=0
FAILED_LIST=""

echo ""
echo "── DB Backup ───────────────────────────────────"

for dbname in "${ALL_DBs[@]}"; do
    db="${HOME}/.eidos/${dbname}"
    if [ -f "$db" ]; then
        base=$(basename "$dbname" .db)
        backup_file="$BACKUP_DIR/${base}_${TIMESTAMP}.db"

        # Use sqlite3 .backup for safe, consistent copy
        if sqlite3 "$db" ".backup '$backup_file'" 2>> "$LOG"; then
            gzip -f "$backup_file"
            echo "  ✅ $dbname ($(du -h "${backup_file}.gz" | cut -f1))"
            BACKED_UP=$((BACKED_UP + 1))

            # Integrity check on backed-up file
            if $TEST_INTEGRITY; then
                integrity_result=$(sqlite3 "${backup_file}.gz" "PRAGMA integrity_check;" 2>&1 || true)
                # Decompress first for integrity check
                gunzip -f "${backup_file}.gz" 2>/dev/null || true
                integrity_result=$(sqlite3 "$backup_file" "PRAGMA integrity_check;" 2>&1 || true)
                gzip -f "$backup_file"
                if [ "$integrity_result" != "ok" ]; then
                    echo "    ⚠️  CORRUPT: $integrity_result"
                    CORRUPT=$((CORRUPT + 1))
                    FAILED_LIST="${FAILED_LIST}  CORRUPT: $dbname ($integrity_result)\n"
                fi
            fi
        else
            echo "  ❌ $dbname: backup failed"
            FAILED=$((FAILED + 1))
            FAILED_LIST="${FAILED_LIST}  BACKUP_FAIL: $dbname\n"
        fi
    fi
done

# ── State JSON files ──────────────────────────────────────────────────
echo ""
echo "── State JSON Backup ───────────────────────────"

STATE_FILES=(
    system_state.json convergence_state.json hexstrike_state.json
    learning_queue.json curriculum_summary.json compression_codebook.json
    health.json vivo_failures.json vivo_status.json
    autonomy_state.json will_state.json marocuai_state.json
    owner_policy.toml config.json
    24h_test_status.json
    monitor_log.jsonl
)

for statefile in "${STATE_FILES[@]}"; do
    sf="${HOME}/.eidos/${statefile}"
    if [ -f "$sf" ]; then
        cp "$sf" "$BACKUP_DIR/${statefile%.*}_${TIMESTAMP}.${statefile##*.}"
        echo "  ✅ $statefile"
    fi
done

# ── ChromaDB backup (FULL mode) ────────────────────────────────────────
if $FULL_MODE; then
    echo ""
    echo "── ChromaDB Backup ─────────────────────────────"

    chroma_backup_file="$BACKUP_DIR/full/chroma_${TIMESTAMP}.tar.gz"

    # The Chroma Rust binary uses SQLite internally for metadata.
    # The data lives in ~/.eidos/chroma/ (chroma.sqlite3 + vector segments).
    # Strategy: use sqlite3 .backup for the SQLite part + tar the whole dir
    # while Chroma is running (SQLite WAL mode makes this safe for reads).

    # First, backup the chroma.sqlite3 safely
    if [ -f "${CHROMA_DATA}/chroma.sqlite3" ]; then
        sqlite3 "${CHROMA_DATA}/chroma.sqlite3" \
            ".backup '${BACKUP_DIR}/full/chroma_sqlite_${TIMESTAMP}.db'" 2>> "$LOG" && \
            gzip -f "${BACKUP_DIR}/full/chroma_sqlite_${TIMESTAMP}.db"
        echo "  ✅ chroma.sqlite3 (metadata)"
    fi

    # Tar the full chroma directory (read-only, safe while running with WAL)
    # Exclude wal/shm since they're ephemeral
    tar -czf "$chroma_backup_file" \
        -C "$(dirname "$CHROMA_DATA")" \
        --exclude='*.wal' --exclude='*.shm' \
        "$(basename "$CHROMA_DATA")" 2>> "$LOG" || {
            echo "  ⚠️  ChromaDB tar had warnings (may be harmless)"
        }
    echo "  ✅ ChromaDB full dir ($(du -h "$chroma_backup_file" | cut -f1))"
    echo "     Saved to: $chroma_backup_file"
fi

# ── Secrets backup (FULL mode) ─────────────────────────────────────────
if $FULL_MODE; then
    echo ""
    echo "── Secrets Backup ──────────────────────────────"

    secrets_backup_file="$BACKUP_DIR/secrets/secrets_${TIMESTAMP}.tar.gz"

    # Create temp dir with secrets
    SECRETS_TMP=$(mktemp -d)
    trap "rm -rf $SECRETS_TMP" EXIT
    for secfile in "${SECRETS_FILES[@]}"; do
        if [ -f "${SECRETS_DIR}/${secfile}" ]; then
            cp "${SECRETS_DIR}/${secfile}" "$SECRETS_TMP/"
        fi
    done

    # Also backup SSH keys used by EIDOS
    for keyfile in id_eidos_mac id_ed25519_mac id_ed25519; do
        if [ -f "${HOME}/.ssh/${keyfile}" ]; then
            cp "${HOME}/.ssh/${keyfile}" "$SECRETS_TMP/"
        fi
    done

    # Also backup API keys JSON
    if [ -f "${SECRETS_DIR}/api_keys.json" ]; then
        cp "${SECRETS_DIR}/api_keys.json" "$SECRETS_TMP/"
    fi

    # Backup constitution (identity)
    if [ -f "${HOME}/EIDOS/constitution.toml" ]; then
        cp "${HOME}/EIDOS/constitution.toml" "$SECRETS_TMP/"
    fi

    if [ -n "$(ls -A "$SECRETS_TMP" 2>/dev/null)" ]; then
        # Tar and encrypt if GPG key available; otherwise just tar with strict perms
        if [ -n "$GPG_RECIPIENT" ] && command -v gpg &>/dev/null; then
            tar -czf - -C "$SECRETS_TMP" . 2>/dev/null | \
                gpg --encrypt --recipient "$GPG_RECIPIENT" \
                    --output "${secrets_backup_file}.gpg" 2>> "$LOG"
            chmod 600 "${secrets_backup_file}.gpg"
            echo "  ✅ Secrets encrypted ($(du -h "${secrets_backup_file}.gpg" | cut -f1))"
            echo "     Saved to: ${secrets_backup_file}.gpg"
        else
            tar -czf "$secrets_backup_file" -C "$SECRETS_TMP" . 2>/dev/null
            chmod 600 "$secrets_backup_file"
            echo "  ✅ Secrets tar.gz (chmod 600, $(du -h "$secrets_backup_file" | cut -f1))"
            echo "     ⚠️  No GPG key set — stored as plain tar.gz (chmod 600)"
            echo "     Set GPG_RECIPIENT in script for encryption"
        fi
    else
        echo "  ⚠️  No secrets files found"
    fi
fi

# ── Cleanup old backups ────────────────────────────────────────────────
echo ""
echo "── Cleanup ─────────────────────────────────────"

if $FULL_MODE; then
    DELETED_FULL=$(find "$BACKUP_DIR/full" -name "*.tar.gz" -mtime "+$RETENTION_DAYS_FULL" -delete -print 2>/dev/null | wc -l)
    DELETED_SECRETS=$(find "$BACKUP_DIR/secrets" -name "*.tar.gz*" -mtime "+$RETENTION_DAYS_FULL" -delete -print 2>/dev/null | wc -l)
    echo "  Full backups cleaned: $DELETED_FULL ( >${RETENTION_DAYS_FULL}d)"
    echo "  Secrets cleaned: $DELETED_SECRETS ( >${RETENTION_DAYS_FULL}d)"
fi

DELETED=$(find "$BACKUP_DIR" -maxdepth 1 -name "*.db.gz" -mtime "+$RETENTION_DAYS" -delete -print 2>/dev/null | wc -l)
echo "  Hourly DBs cleaned: $DELETED ( >${RETENTION_DAYS}d)"

# ── Sync to Mac ────────────────────────────────────────────────────────
if $SYNC_MAC; then
    echo ""
    echo "── Mac Sync ────────────────────────────────────"

    SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
    if [ -x "$SCRIPT_DIR/sync_to_mac.sh" ]; then
        "$SCRIPT_DIR/sync_to_mac.sh" 2>&1 | while IFS= read -r line; do
            echo "  $line"
        done
        SYNC_EXIT="${PIPESTATUS[0]:-0}"
        if [ "$SYNC_EXIT" -eq 0 ]; then
            echo "  ✅ Sync to Mac OK"
        else
            echo "  ⚠️  Sync to Mac had issues (exit $SYNC_EXIT)"
        fi
    else
        echo "  ⚠️  sync_to_mac.sh not found — skipping"
    fi
fi

# ── Summary ────────────────────────────────────────────────────────────
echo ""
echo "════════════════════════════════════════════════"
echo "[$(date '+%Y-%m-%d %H:%M:%S')] BACKUP COMPLETE"
echo "   DBs backed up: $BACKED_UP OK, $FAILED failed, $CORRUPT corrupt"
if [ -n "$FAILED_LIST" ]; then
    echo -e "   Failures:\n$FAILED_LIST"
fi
echo "   Disk: $(df -h "$BACKUP_DIR" | tail -1 | awk '{print $3 " used, " $4 " avail"}')"
echo "════════════════════════════════════════════════"

exit $(( FAILED + CORRUPT ))
