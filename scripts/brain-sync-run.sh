#!/bin/bash
# brain-sync-run.sh — Wrapper de core/brain_sync.py para systemd [S94]
# Llamado por eidos-brain-sync.timer cada 10 min
# Sincroniza nodos knowledge_nodes entre Kali y Mac vía túnel SSH

set -euo pipefail

EIDOS_DIR="/home/ser/EIDOS"
LOG_DIR="$HOME/.eidos/logs"
LOG_FILE="$LOG_DIR/brain_sync.log"

mkdir -p "$LOG_DIR"

echo "[$(date -Iseconds)] brain-sync iniciado" >> "$LOG_FILE"

cd "$EIDOS_DIR"

# Si el túnel SSH no está activo (Mac apagado a propósito), OMITIR el sync.
# [S122-I] exit 0 (antes 1): el Mac apagado es estado ESPERADO, no un error →
# así el servicio NO queda "failed" (ruido) cuando SER tiene el Mac off.
if ! ssh -o ConnectTimeout=3 -o BatchMode=yes -o LogLevel=ERROR eidos-mac echo ok &>/dev/null; then
    echo "[$(date -Iseconds)] Mac no alcanzable (apagado) — sync omitido, OK" >> "$LOG_FILE"
    exit 0
fi

# Ejecutar sync
python3 -c "
import sys, logging, subprocess
sys.path.insert(0, '$EIDOS_DIR')
logging.basicConfig(level=logging.INFO)

from core.brain_sync import sync

# [S123] El check inicial de túnel puede pasar aunque el Mac esté off
# (el túnel local acepta TCP). Si el ssh real falla/timeout a mitad,
# es el MISMO estado esperado 'Mac off' → capturar y salir 0, no failed.
try:
    result = sync(verbose=True)
except (subprocess.TimeoutExpired, subprocess.CalledProcessError, OSError) as e:
    print(f'AVISO: Mac no alcanzable a mitad del sync ({type(e).__name__}) — sync omitido, OK')
    sys.exit(0)

# [S122-I] No marcar failure si el Mac dejó de estar alcanzable a mitad
# (estado esperado). Solo loguear el aviso. El servicio queda OK.
if result.get('error') and not (result.get('kali_to_mac') or result.get('mac_to_kali')):
    print(f'AVISO: {result[\"error\"]} (sync omitido, no es error)')
" >> "$LOG_FILE" 2>&1

exit_code=$?
echo "[$(date -Iseconds)] brain-sync completado (exit=$exit_code)" >> "$LOG_FILE"
exit $exit_code
