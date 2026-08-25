#!/bin/sh
set -eu

PURGE=0
MODE="${MESHPI_MODE:-always}"
TEST_MODE="${MESHPI_TEST_MODE:-0}"
SKIP_SERVICE="${MESHPI_SKIP_SERVICE:-0}"

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
        invalid_mode:nn) printf '%s' 'Modus må vere «always» eller «session».' ;;
        invalid_mode:en) printf '%s' 'Mode must be “always” or “session”.' ;;
        root_required:nn) printf '%s' 'Køyr avinstalleringa som root.' ;;
        root_required:en) printf '%s' 'Run the uninstallation as root.' ;;
        session_without_sudo:nn) printf '%s' 'Session-modus skal avinstallerast utan sudo.' ;;
        session_without_sudo:en) printf '%s' 'Session mode must be uninstalled without sudo.' ;;
        removed_service:nn) printf '%s' 'Fjerna MeshPi-programmet og systemtenesta.' ;;
        removed_service:en) printf '%s' 'Removed the MeshPi application and system service.' ;;
        removed_session:nn) printf '%s' 'Fjerna MeshPi-programmet i session-modus.' ;;
        removed_session:en) printf '%s' 'Removed the MeshPi application in session mode.' ;;
        purged:nn) printf '%s' 'Sletta konfigurasjon og lokale data.' ;;
        purged:en) printf '%s' 'Deleted configuration and local data.' ;;
        kept_config:nn) printf '%s' 'Bevarte konfigurasjon: %s' ;;
        kept_config:en) printf '%s' 'Preserved configuration: %s' ;;
        kept_data:nn) printf '%s' 'Bevarte database og profilar: %s' ;;
        kept_data:en) printf '%s' 'Preserved database and profiles: %s' ;;
        kept_language:nn) printf '%s' 'Bevarte språkval: %s' ;;
        kept_language:en) printf '%s' 'Preserved language choice: %s' ;;
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
        --mode=always) MODE=always ;;
        --mode=session | --no-service) MODE=session ;;
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
[ "$MODE" = "always" ] || [ "$MODE" = "session" ] || {
    say_error invalid_mode
    exit 2
}
if [ "$MODE" = "always" ] && [ "$(id -u)" -ne 0 ] && [ "$TEST_MODE" != "1" ]; then
    say_error root_required
    exit 1
fi
if [ "$MODE" = "session" ] && [ "$(id -u)" -eq 0 ] && [ "$TEST_MODE" != "1" ]; then
    say_error session_without_sudo
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
LANGUAGE_FILE="${MESHPI_LANGUAGE_FILE:-$USER_HOME/.config/MeshPi/language.json}"

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
    say removed_service
else
    say removed_session
fi

if [ "$PURGE" = "1" ]; then
    rm -f "$CONFIG_FILE"
    rm -f "$LANGUAGE_FILE"
    if [ -d "$STATE_DIR" ]; then
        rm -rf "$STATE_DIR"
    fi
    if [ "$SKIP_SERVICE" != "1" ] && id meshpi >/dev/null 2>&1; then
        userdel meshpi >/dev/null 2>&1 || true
    fi
    say purged
else
    say kept_config "$CONFIG_FILE"
    say kept_data "$STATE_DIR"
    say kept_language "$LANGUAGE_FILE"
    say purge_hint
fi
