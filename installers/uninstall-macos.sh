#!/bin/sh
set -eu

PURGE=0
for argument in "$@"; do
    case "$argument" in
        --purge-data) PURGE=1 ;;
        *) echo "Ukjent argument: $argument" >&2; exit 2 ;;
    esac
done

APP_ROOT="${MESHPI_APP_ROOT:-$HOME/Library/Application Support/MeshPi}"
DATA_DIR="${MESHPI_DATA_DIR:-$APP_ROOT/data}"
CONFIG_FILE="${MESHPI_CONFIG_FILE:-$APP_ROOT/meshpi.env}"
BIN_DIR="${MESHPI_BIN_DIR:-$HOME/.local/bin}"
PLIST_FILE="${MESHPI_PLIST_FILE:-$HOME/Library/LaunchAgents/org.venes.meshpi.plist}"
DOMAIN="gui/$(id -u)"

launchctl bootout "$DOMAIN/org.venes.meshpi" >/dev/null 2>&1 || true
rm -f "$PLIST_FILE" "$BIN_DIR/meshpi"
rm -rf "$APP_ROOT/releases" "$APP_ROOT/current" "$APP_ROOT/previous" "$APP_ROOT/venv"

PATH_MARKER="$APP_ROOT/path-added-by-meshpi"
PROFILE="$HOME/.zprofile"
PATH_LINE='export PATH="$HOME/.local/bin:$PATH"'
if [ -f "$PATH_MARKER" ] && [ -f "$PROFILE" ]; then
    temporary="$PROFILE.meshpi-$$"
    grep -F -v "$PATH_LINE" "$PROFILE" >"$temporary" || true
    mv "$temporary" "$PROFILE"
    rm -f "$PATH_MARKER"
fi

echo "Fjerna MeshPi-programmet og LaunchAgent."
if [ "$PURGE" = "1" ]; then
    rm -rf "$APP_ROOT"
    echo "Sletta konfigurasjon, loggar og lokale data."
else
    echo "Bevarte konfigurasjon: $CONFIG_FILE"
    echo "Bevarte database, profilar og loggar: $DATA_DIR"
    echo "Bruk --purge-data for å slette desse òg."
fi
