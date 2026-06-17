#!/bin/bash
# ═══════════════════════════════════════════════════════════════════════
# scripts/restore_all_dbs.sh — Restore all EIDOS DBs from backup
# ═══════════════════════════════════════════════════════════════════════
# Restores the LATEST backup of each DB to ~/.eidos/
#
# Uso: ./scripts/restore_all_dbs.sh [backup_dir]
#   backup_dir defaults to ~/.eidos/backups/
#
# Safety: creates .pre-restore.bak of any existing DB before overwriting.
# ═══════════════════════════════════════════════════════════════════════

set -euo pipefail

BACKUP_DIR="${1:-$HOME/.eidos/backups}"
EIDOS_DIR="$HOME/.eidos"
TIMESTAMP=$(date +%Y%m%d_%H%M%S)
DRY_RUN=false

if [ "${1:-}" = "--dry-run" ]; then
    DRY_RUN=true
    BACKUP_DIR="${2:-$HOME/.eidos/backups}"
fi

if [ ! -d "$BACKUP_DIR" ]; then
    echo "ERROR: Backup directory not found: $BACKUP_DIR"
    echo "Usage: $0 [--dry-run] [backup_dir]"
    exit 1
fi

echo "═══ EIDOS DB RESTORE ═══"
echo "Source: $BACKUP_DIR"
echo "Dest:   $EIDOS_DIR"
if $DRY_RUN; then
    echo "Mode:   DRY RUN (no changes)"
fi
echo ""

RESTORED=0
SKIPPED=0
FAILED=0

# Find all unique DB basenames from backups
# Backup filename format: {dbname}_{YYYYMMDD}_{HHMMSS}.db.gz
for backup_file in "$BACKUP_DIR"/*.db.gz; do
    [ -f "$backup_file" ] || continue

    # Extract original dbname (strip timestamp and .db.gz extension)
    filename=$(basename "$backup_file")
    # Remove the timestamp pattern: _YYYYMMDD_HHMMSS.db.gz -> just the dbname
    dbname=$(echo "$filename" | sed -E 's/_[0-9]{8}_[0-9]{6}\.db\.gz$//')

    # Find the latest backup for this DB
    latest=$(ls -t "$BACKUP_DIR/${dbname}_"*.db.gz 2>/dev/null | head -1)

    if [ -z "$latest" ] || [ "$latest" != "$backup_file" ]; then
        # We'll restore when we hit the latest — skip non-latest
        # This is handled implicitly by processing each file only when it IS the latest
        continue
    fi

    # Actually, simpler approach: for each unique dbname, find latest and restore
done

# Better approach: find unique dbnames first, then restore latest of each
declare -A SEEN
for backup_file in "$BACKUP_DIR"/*.db.gz; do
    [ -f "$backup_file" ] || continue
    filename=$(basename "$backup_file")
    dbname=$(echo "$filename" | sed -E 's/_[0-9]{8}_[0-9]{6}\.db\.gz$//')
    SEEN["$dbname"]=1
done

for dbname in "${!SEEN[@]}"; do
    latest=$(ls -t "$BACKUP_DIR/${dbname}_"*.db.gz 2>/dev/null | head -1)
    if [ -z "$latest" ]; then
        echo "  ⚠️  $dbname: no backup found"
        SKIPPED=$((SKIPPED + 1))
        continue
    fi

    dest="$EIDOS_DIR/${dbname}.db"

    # Backup existing DB before overwriting
    if [ -f "$dest" ] && ! $DRY_RUN; then
        cp "$dest" "${dest}.pre-restore-${TIMESTAMP}.bak"
        echo "  💾 $dbname: existing backed up → .pre-restore-${TIMESTAMP}.bak"
    fi

    if $DRY_RUN; then
        echo "  🔍 $dbname: would restore $(basename "$latest") ($(du -h "$latest" | cut -f1))"
        RESTORED=$((RESTORED + 1))
    else
        if gunzip -c "$latest" > "$dest" 2>/dev/null; then
            # Verify integrity
            integrity=$(sqlite3 "$dest" "PRAGMA integrity_check;" 2>&1)
            if [ "$integrity" = "ok" ]; then
                echo "  ✅ $dbname ($(du -h "$dest" | cut -f1))"
                RESTORED=$((RESTORED + 1))
            else
                echo "  ❌ $dbname: CORRUPT after restore — $integrity"
                FAILED=$((FAILED + 1))
                # Revert to backup if available
                if [ -f "${dest}.pre-restore-${TIMESTAMP}.bak" ]; then
                    mv "${dest}.pre-restore-${TIMESTAMP}.bak" "$dest"
                    echo "     Reverted to pre-restore backup"
                fi
            fi
        else
            echo "  ❌ $dbname: decompress failed"
            FAILED=$((FAILED + 1))
        fi
    fi
done

echo ""
echo "═══ RESTORE SUMMARY ═══"
echo "   Restored: $RESTORED"
echo "   Skipped:  $SKIPPED"
echo "   Failed:   $FAILED"
echo ""

if $DRY_RUN; then
    echo "Dry run complete. Run without --dry-run to actually restore."
elif [ "$FAILED" -eq 0 ]; then
    echo "All DBs restored successfully. ✅"
    echo "Backup of previous DBs saved with suffix .pre-restore-${TIMESTAMP}.bak"
else
    echo "Some restores FAILED — check output above."
    exit 1
fi
