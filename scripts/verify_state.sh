#!/bin/bash
# scripts/verify_state.sh — Verificación reproducible del estado de EIDOS [S109]
#
# DeepSeek S108: "Toda métrica debe ser verificable con un comando."
# Uso: ./scripts/verify_state.sh [--json|--short]

EIDOS_DIR="$(cd "$(dirname "$0")/.." && pwd)"
EIDOS_DB_DIR="${HOME}/.eidos"
FORMAT="${1:---short}"

TOTAL_DBS=$(find "$EIDOS_DB_DIR" -maxdepth 1 -name "*.db" -type f 2>/dev/null | wc -l)
WAL_COUNT=0
DEAD_DBS=0
# Solo muestrear 10 DBs para WAL (más rápido, representativo)
for db in "$EIDOS_DB_DIR"/{self,evolution_brain,vivo,memory,episodic,goals,desires,trust_model,autonomy,state}.db; do
    [ -f "$db" ] || continue
    mode=$(sqlite3 "$db" "PRAGMA journal_mode;" 2>/dev/null || echo "ERROR")
    [ "$mode" = "wal" ] && WAL_COUNT=$((WAL_COUNT + 1))
    tables=$(sqlite3 "$db" "SELECT COUNT(*) FROM sqlite_master WHERE type='table';" 2>/dev/null || echo "0")
    [ "$tables" = "0" ] && DEAD_DBS=$((DEAD_DBS + 1))
done

CONNECTS=$(grep -r "sqlite3.connect" "$EIDOS_DIR/core/" --include="*.py" 2>/dev/null | grep -v "db.py" | grep -v ".bak" | wc -l)
FILES_RAW=$(grep -rl "sqlite3.connect" "$EIDOS_DIR/core/" --include="*.py" 2>/dev/null | grep -v "db.py" | grep -v ".bak" | wc -l)
FILES_DB=$(grep -rl "from core.db import get_conn" "$EIDOS_DIR/core/" --include="*.py" 2>/dev/null | wc -l)
MODULES=$(find "$EIDOS_DIR/core" -name "*.py" -type f ! -name "*.bak*" 2>/dev/null | wc -l)
LOC=$(find "$EIDOS_DIR/core" -name "*.py" -type f ! -name "*.bak*" -exec cat {} + 2>/dev/null | wc -l)
SVC_EIDOS=$(systemctl --user is-active eidos 2>/dev/null || echo "unknown")
SVC_RAM=$(systemctl --user is-active eidos-ram-guardian 2>/dev/null || echo "unknown")

# Top 5 DBs por tamaño
TOP_DBS=$(for db in "$EIDOS_DB_DIR"/*.db; do
    [ -f "$db" ] || continue
    echo "$(stat -c%s "$db") $(basename "$db")"
done | sort -rn | head -5 | while read size name; do
    printf "  %s  %s\n" "$(du -h "$EIDOS_DB_DIR/$name" 2>/dev/null | cut -f1)" "$name"
done)

MIG_PCT=$(( (609 - CONNECTS) * 100 / 609 ))

case "$FORMAT" in
    --json)
        cat <<EOJ
{
  "timestamp": "$(date -Iseconds)",
  "dbs": {"total": $TOTAL_DBS, "wal_sampled": $WAL_COUNT, "dead": $DEAD_DBS},
  "connections": {"raw": $CONNECTS, "files_raw": $FILES_RAW, "files_migrated": $FILES_DB, "migration_pct": $MIG_PCT},
  "code": {"modules": $MODULES, "loc": $LOC},
  "services": {"eidos": "$SVC_EIDOS", "ram_guardian": "$SVC_RAM"}
}
EOJ
        ;;
    --short)
        echo "EIDOS $(date +%Y-%m-%d) | DBs:$TOTAL_DBS WAL:$WAL_COUNT | Connects:$CONNECTS(raw) $FILES_DB(mig) ${MIG_PCT}% | Mods:$MODULES | LOC:$LOC | eidos:$SVC_EIDOS ram:$SVC_RAM"
        ;;
    *)
        echo "════════ EIDOS STATE $(date '+%Y-%m-%d %H:%M') ════════"
        echo "DBs: $TOTAL_DBS total, $WAL_COUNT WAL (muestreado), $DEAD_DBS muertos"
        echo "Connects: $CONNECTS raw en $FILES_RAW archivos | $FILES_DB usan core/db.py | ${MIG_PCT}% migrado"
        echo "Code: $MODULES módulos, $LOC líneas"
        echo "Services: eidos=$SVC_EIDOS, ram=$SVC_RAM"
        echo "Top DBs:"
        echo "$TOP_DBS"
        echo "═══════════════════════════════════════════════════"
        ;;
esac
