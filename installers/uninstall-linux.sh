#!/bin/sh
set -eu

PURGE=0
MODE="${MESHPI_MODE:-always}"
TEST_MODE="${MESHPI_TEST_MODE:-0}"
SKIP_SERVICE="${MESHPI_SKIP_SERVICE:-0}"
for argument in "$@"; do
    case "$argument" in
        --purge-data) PURGE=1 ;;
        --mode=always) MODE=always ;;
        --mode=session | --no-service) MODE=session ;;
        *) echo "Ukjent argument: $argument" >&2; exit 2 ;;
    esac
done
[ "$MODE" = "always" ] || [ "$MODE" = "session" ] || {
    echo "Modus må vere «always» eller «session»." >&2
    exit 2
}
if [ "$(id -u)" -ne 0 ] && [ "$TEST_MODE" != "1" ]; then
    echo "Køyr avinstalleringa som root." >&2
    exit 1
fi

INSTALL_USER="${SUDO_USER:-${USER:-root}}"
if [ "$INSTALL_USER" = "root" ]; then
    USER_HOME="${HOME:-/root}"
else
    USER_HOME="$(getent passwd "$INSTALL_USER" | awk -F: '{print $6}')"
fi
if [ "$MODE" = "session" ]; then
    PREFIX="${MESHPI_PREFIX:-$USER_HOME/.local/share/meshpi}"
    STATE_DIR="${MESHPI_STATE_DIR:-$PREFIX/data}"
    CONFIG_FILE="${MESHPI_CONFIG_FILE:-$USER_HOME/.config/meshpi/meshpi.env}"
    BIN_FILE="${MESHPI_BIN_FILE:-$USER_HOME/.local/bin/meshpi}"
    SKIP_SERVICE=1
else
    PREFIX="${MESHPI_PREFIX:-/opt/meshpi}"
    STATE_DIR="${MESHPI_STATE_DIR:-/var/lib/meshpi}"
    CONFIG_FILE="${MESHPI_CONFIG_FILE:-/etc/meshpi.env}"
    BIN_FILE="${MESHPI_BIN_FILE:-/usr/local/bin/meshpi}"
fi
UNIT_FILE="${MESHPI_UNIT_FILE:-/etc/systemd/system/meshpi.service}"

if [ "$SKIP_SERVICE" != "1" ] && command -v systemctl >/dev/null 2>&1; then
    systemctl disable --now meshpi.service >/dev/null 2>&1 || true
fi
rm -f "$BIN_FILE"
if [ "$SKIP_SERVICE" != "1" ]; then
    rm -f "$UNIT_FILE"
fi
if [ "$SKIP_SERVICE" != "1" ] && command -v systemctl >/dev/null 2>&1; then
    systemctl daemon-reload
fi

if [ -d "$PREFIX" ]; then
    rm -rf "$PREFIX"
fi
if [ "$MODE" = "always" ]; then
    echo "Fjerna MeshPi-programmet og systemtenesta."
else
    echo "Fjerna MeshPi-programmet i session-modus."
fi

if [ "$PURGE" = "1" ]; then
    rm -f "$CONFIG_FILE"
    if [ -d "$STATE_DIR" ]; then
        rm -rf "$STATE_DIR"
    fi
    if [ "$SKIP_SERVICE" != "1" ] && id meshpi >/dev/null 2>&1; then
        userdel meshpi >/dev/null 2>&1 || true
    fi
    echo "Sletta konfigurasjon og lokale data."
else
    echo "Bevarte konfigurasjon: $CONFIG_FILE"
    echo "Bevarte database og profilar: $STATE_DIR"
    echo "Bruk --purge-data for å slette desse òg."
fi
