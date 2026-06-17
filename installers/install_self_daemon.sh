#!/usr/bin/env bash
# installers/install_self_daemon.sh — Instalar servicio systemd del daemon de conciencia [S102]
#
# Uso:
#   chmod +x installers/install_self_daemon.sh
#   ./installers/install_self_daemon.sh          # instalar y arrancar
#   ./installers/install_self_daemon.sh --status # solo mostrar estado
#   ./installers/install_self_daemon.sh --remove # desinstalar
#
# El daemon de conciencia ejecuta el bucle vital:
#   eventos → VAD → daemon (4 triggers) → Will → acciones
#   + Night Cycle (5 fases) cada ~8h

set -euo pipefail

SERVICE_NAME="eidos-self.service"
SERVICE_FILE="$HOME/.config/systemd/user/${SERVICE_NAME}"
SOURCE_FILE="$(dirname "$0")/eidos-self.service"

ACTION="${1:-install}"

case "$ACTION" in
    --status|status)
        echo "=== Estado del servicio ${SERVICE_NAME} ==="
        if systemctl --user is-active --quiet "${SERVICE_NAME}" 2>/dev/null; then
            echo "✓ Activo"
            systemctl --user status "${SERVICE_NAME}" --no-pager 2>/dev/null || true
        else
            echo "✗ No activo"
        fi
        echo ""
        echo "=== Logs recientes ==="
        journalctl --user -u "${SERVICE_NAME}" -n 15 --no-pager 2>/dev/null || echo "(sin logs)"
        ;;

    --remove|remove|uninstall)
        echo "Desinstalando ${SERVICE_NAME}..."
        systemctl --user stop "${SERVICE_NAME}" 2>/dev/null || true
        systemctl --user disable "${SERVICE_NAME}" 2>/dev/null || true
        rm -f "${SERVICE_FILE}"
        systemctl --user daemon-reload
        echo "✓ ${SERVICE_NAME} desinstalado"
        ;;

    install|"")
        echo "=== Instalando EIDOS Self Daemon (S102) ==="

        # Verificar que existe el daemon
        SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
        EIDOS_DIR="$(dirname "$SCRIPT_DIR")"
        DAEMON_BIN="${EIDOS_DIR}/bin/eidos-self-daemon"

        if [[ ! -f "$DAEMON_BIN" ]]; then
            echo "ERROR: No se encuentra ${DAEMON_BIN}"
            echo "Ejecuta desde el directorio raíz de EIDOS"
            exit 1
        fi

        # Verificar Python
        if ! python3 -c "from core.eidos_self_core import get_self_core" 2>/dev/null; then
            echo "ERROR: No se pueden importar los módulos de EIDOS"
            echo "Verifica PYTHONPATH y que las dependencias están instaladas"
            exit 1
        fi

        # Copiar archivo de servicio
        mkdir -p "$(dirname "$SERVICE_FILE")"
        cp "${SOURCE_FILE}" "${SERVICE_FILE}"
        echo "✓ Servicio copiado a ${SERVICE_FILE}"

        # Recargar systemd
        systemctl --user daemon-reload
        echo "✓ systemd recargado"

        # Habilitar e iniciar
        systemctl --user enable "${SERVICE_NAME}"
        echo "✓ Servicio habilitado (auto-inicio)"

        systemctl --user start "${SERVICE_NAME}"
        sleep 2

        if systemctl --user is-active --quiet "${SERVICE_NAME}"; then
            echo "✓ Servicio iniciado y funcionando"
        else
            echo "⚠ El servicio no arrancó. Verifica:"
            echo "  journalctl --user -u ${SERVICE_NAME} -n 20"
        fi

        echo ""
        echo "=== Instalación completada ==="
        echo "Comandos útiles:"
        echo "  systemctl --user status ${SERVICE_NAME}"
        echo "  journalctl --user -u ${SERVICE_NAME} -f   # seguir logs"
        echo "  systemctl --user restart ${SERVICE_NAME}   # reiniciar"
        echo "  ${0} --remove                               # desinstalar"
        ;;

    *)
        echo "Uso: $0 [install|--status|--remove]"
        exit 1
        ;;
esac
