#!/bin/sh
set -eu

PURGE=0

detect_language() {
    locale_hint="${LC_ALL:-${LC_MESSAGES:-${LANG:-}}}"
    case "$locale_hint" in
        nn* | NN* | nb* | NB* | no* | NO*) printf '%s\n' nn ;;
        *) printf '%s\n' en ;;
    esac
}

DETECTED_LANGUAGE="$(detect_language)"
LANGUAGE="${MESHPI_LANGUAGE:-$DETECTED_LANGUAGE}"

message() {
    case "$1:$LANGUAGE" in
        invalid_language:nn) printf '%s' 'Språket må vere «nn» eller «en».' ;;
        invalid_language:en) printf '%s' 'Language must be “nn” or “en”.' ;;
        unknown_argument:nn) printf '%s' 'Ukjent argument: %s' ;;
        unknown_argument:en) printf '%s' 'Unknown argument: %s' ;;
        removed:nn) printf '%s' 'Fjerna MeshPi-programmet og LaunchAgent.' ;;
        removed:en) printf '%s' 'Removed the MeshPi application and LaunchAgent.' ;;
        purged:nn) printf '%s' 'Sletta konfigurasjon, loggar og lokale data.' ;;
        purged:en) printf '%s' 'Deleted configuration, logs, and local data.' ;;
        kept_config:nn) printf '%s' 'Bevarte konfigurasjon: %s' ;;
        kept_config:en) printf '%s' 'Preserved configuration: %s' ;;
        kept_data:nn) printf '%s' 'Bevarte database, profilar og loggar: %s' ;;
        kept_data:en) printf '%s' 'Preserved database, profiles, and logs: %s' ;;
        purge_hint:nn) printf '%s' 'Bruk --purge-data for å slette desse òg.' ;;
        purge_hint:en) printf '%s' 'Use --purge-data to delete these as well.' ;;
        *) return 1 ;;
    esac
}

say() {
    key="$1"
    shift
    format="$(message "$key")"
    printf "$format\n" "$@"
}

say_error() {
    say "$@" >&2
}

for argument in "$@"; do
    case "$argument" in
        --purge-data) PURGE=1 ;;
        --language=nn) LANGUAGE=nn ;;
        --language=en) LANGUAGE=en ;;
        --language=*) say_error invalid_language; exit 2 ;;
        *) say_error unknown_argument "$argument"; exit 2 ;;
    esac
done
case "$LANGUAGE" in
    nn | en) ;;
    *) LANGUAGE="$DETECTED_LANGUAGE"; say_error invalid_language; exit 2 ;;
esac

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

say removed
if [ "$PURGE" = "1" ]; then
    rm -rf "$APP_ROOT"
    say purged
else
    say kept_config "$CONFIG_FILE"
    say kept_data "$DATA_DIR"
    say purge_hint
fi
