#!/bin/sh
set -eu

PURGE=0
MODE="${MESHPI_MODE:-always}"
TEST_MODE="${MESHPI_TEST_MODE:-0}"
SKIP_SERVICE="${MESHPI_SKIP_SERVICE:-0}"

detect_language() {
    locale_hint="${LC_ALL:-${LC_MESSAGES:-${LANG:-}}}"
    case "$locale_hint" in
        nn* | NN*) printf '%s\n' nn ;;
        nb* | NB* | no* | NO*) printf '%s\n' nb ;;
        *) printf '%s\n' en ;;
    esac
}

DETECTED_LANGUAGE="$(detect_language)"
LANGUAGE="${MESHPI_LANGUAGE:-$DETECTED_LANGUAGE}"

message() {
    case "$1:$LANGUAGE" in
        invalid_language:nn) printf '%s' 'Språket må vere «nb», «nn» eller «en».' ;;
        invalid_language:nb) printf '%s' 'Språket må være «nb», «nn» eller «en».' ;;
        invalid_language:en) printf '%s' 'Language must be “nb”, “nn” or “en”.' ;;
        unknown_argument:nn) printf '%s' 'Ukjent argument: %s' ;;
        unknown_argument:nb) printf '%s' 'Ukjent argument: %s' ;;
        unknown_argument:en) printf '%s' 'Unknown argument: %s' ;;
        invalid_mode:nn) printf '%s' 'Modus må vere «always» eller «session».' ;;
        invalid_mode:nb) printf '%s' 'Modus må være «always» eller «session».' ;;
        invalid_mode:en) printf '%s' 'Mode must be “always” or “session”.' ;;
        root_required:nn) printf '%s' 'Køyr avinstalleringa som root.' ;;
        root_required:nb) printf '%s' 'Kjør avinstalleringen som root.' ;;
        root_required:en) printf '%s' 'Run the uninstallation as root.' ;;
        session_without_sudo:nn) printf '%s' 'Session-modus skal avinstallerast utan sudo.' ;;
        session_without_sudo:nb) printf '%s' 'Session-modus skal avinstalleres uten sudo.' ;;
        session_without_sudo:en) printf '%s' 'Session mode must be uninstalled without sudo.' ;;
        removed_service:nn) printf '%s' 'Fjerna MeshPi-programmet og systemtenesta.' ;;
        removed_service:nb) printf '%s' 'Fjernet MeshPi-programmet og systemtjenesten.' ;;
        removed_service:en) printf '%s' 'Removed the MeshPi application and system service.' ;;
        removed_session:nn) printf '%s' 'Fjerna MeshPi-programmet i session-modus.' ;;
        removed_session:nb) printf '%s' 'Fjernet MeshPi-programmet i session-modus.' ;;
        removed_session:en) printf '%s' 'Removed the MeshPi application in session mode.' ;;
        purged:nn) printf '%s' 'Sletta konfigurasjon og lokale data.' ;;
        purged:nb) printf '%s' 'Slettet konfigurasjon og lokale data.' ;;
        purged:en) printf '%s' 'Deleted configuration and local data.' ;;
        kept_config:nn) printf '%s' 'Bevarte konfigurasjon: %s' ;;
        kept_config:nb) printf '%s' 'Bevarte konfigurasjon: %s' ;;
        kept_config:en) printf '%s' 'Preserved configuration: %s' ;;
        kept_data:nn) printf '%s' 'Bevarte database og profilar: %s' ;;
        kept_data:nb) printf '%s' 'Bevarte database og profiler: %s' ;;
        kept_data:en) printf '%s' 'Preserved database and profiles: %s' ;;
        kept_language:nn) printf '%s' 'Bevarte språkval: %s' ;;
        kept_language:nb) printf '%s' 'Bevarte språkvalg: %s' ;;
        kept_language:en) printf '%s' 'Preserved language choice: %s' ;;
        purge_hint:nn) printf '%s' 'Bruk --purge-data for å slette desse òg.' ;;
        purge_hint:nb) printf '%s' 'Bruk --purge-data for å slette disse også.' ;;
        purge_hint:en) printf '%s' 'Use --purge-data to delete these as well.' ;;
        python_paths_required:nn) printf '%s' 'Python 3 krevst for trygg kontroll av stiar.' ;;
        python_paths_required:nb) printf '%s' 'Python 3 kreves for trygg kontroll av stier.' ;;
        python_paths_required:en) printf '%s' 'Python 3 is required for safe path validation.' ;;
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
        --language=nb) LANGUAGE=nb ;;
        --language=nn) LANGUAGE=nn ;;
        --language=en) LANGUAGE=en ;;
        --language=*) say_error invalid_language; exit 2 ;;
        *) say_error unknown_argument "$argument"; exit 2 ;;
    esac
done
case "$LANGUAGE" in
    nb | nn | en) ;;
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

# Validate resolved paths before stopping anything or removing launchers.
PYTHON="$(command -v python3 || true)"
[ -n "$PYTHON" ] || { say_error python_paths_required; exit 1; }
"$PYTHON" -I - "$PREFIX" "$STATE_DIR" "$CONFIG_FILE" "$LANGUAGE" <<'PY'
import sys
from pathlib import Path

messages = {
    "nn": ("Avviser ei utrygg program-/datarot", "Avviser sletting av ein programsti som overlappar brukardata"),
    "nb": ("Avviser en utrygg program-/datarot", "Avviser sletting av en programsti som overlapper brukerdata"),
    "en": ("Refusing an unsafe installation/data root", "Refusing to remove an application path overlapping user data"),
}[sys.argv[4]]
prefix, state, config = (Path(value).expanduser().resolve() for value in sys.argv[1:4])
for path in (prefix, state):
    if path == Path(path.anchor) or path == Path.home():
        raise SystemExit(messages[0])
for name in ("releases", "current", "previous", ".venv"):
    target = (prefix / name).resolve()
    for protected in (state, config):
        if target == protected or target in protected.parents or protected in target.parents:
            raise SystemExit(messages[1])
PY

if [ "$MODE" = "session" ] && [ -x "$BIN_FILE" ]; then
    "$BIN_FILE" service stop
fi
if [ "$SKIP_SERVICE" != "1" ] && command -v systemctl >/dev/null 2>&1; then
    systemctl disable --now meshpi-web.service >/dev/null 2>&1 || true
    systemctl disable --now meshpi.service >/dev/null 2>&1 || true
fi
rm -f "$BIN_FILE"
if [ "$SKIP_SERVICE" != "1" ]; then
    rm -f "$UNIT_FILE" "${MESHPI_WEB_UNIT_FILE:-$(dirname "$UNIT_FILE")/meshpi-web.service}"
fi
if [ "$SKIP_SERVICE" != "1" ] && command -v systemctl >/dev/null 2>&1; then
    systemctl daemon-reload
fi

# Never remove the installation root: session data defaults to PREFIX/data.
for artifact in releases current previous .venv; do
    target="$PREFIX/$artifact"
    rm -rf "$target"
done
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
