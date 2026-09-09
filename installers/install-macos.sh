#!/bin/sh
set -eu

BASE_URL="${MESHPI_BASE_URL:-https://venes.org/meshpi}"
MODE="${MESHPI_MODE:-always}"
IPC_PORT_VALUE="${MESHPI_IPC_PORT:-8765}"
SKIP_SERVICE="${MESHPI_SKIP_SERVICE:-0}"
TEST_MODE="${MESHPI_TEST_MODE:-0}"

detect_language() {
    locale_hint="${LC_ALL:-${LC_MESSAGES:-${LANG:-}}}"
    case "$locale_hint" in
        nn* | NN* | nb* | NB* | no* | NO*) printf '%s\n' nn ;;
        *) printf '%s\n' en ;;
    esac
}

DETECTED_LANGUAGE=en
LANGUAGE="${MESHPI_LANGUAGE:-$DETECTED_LANGUAGE}"

message() {
    case "$1:$LANGUAGE" in
        invalid_language:nn) printf '%s' 'Språket må vere «nn» eller «en».' ;;
        invalid_language:en) printf '%s' 'Language must be “nn” or “en”.' ;;
        unknown_argument:nn) printf '%s' 'Ukjent argument: %s' ;;
        unknown_argument:en) printf '%s' 'Unknown argument: %s' ;;
        invalid_mode:nn) printf '%s' 'Modus må vere «always» eller «session».' ;;
        invalid_mode:en) printf '%s' 'Mode must be “always” or “session”.' ;;
        no_sudo:nn) printf '%s' 'macOS-installasjonen skal køyrast utan sudo.' ;;
        no_sudo:en) printf '%s' 'The macOS installation must be run without sudo.' ;;
        check_python:nn) printf '%s' 'Kontrollerer Python 3.11 eller nyare …' ;;
        check_python:en) printf '%s' 'Checking for Python 3.11 or newer …' ;;
        missing_command:nn) printf '%s' 'Manglar kommandoen «%s».' ;;
        missing_command:en) printf '%s' 'Required command “%s” is missing.' ;;
        python_required:nn) printf '%s' 'MeshPi krev Python 3.11 eller nyare.' ;;
        python_required:en) printf '%s' 'MeshPi requires Python 3.11 or newer.' ;;
        brew_python:nn) printf '%s' 'Installer med Homebrew: brew install python@3.11' ;;
        brew_python:en) printf '%s' 'Install with Homebrew: brew install python@3.11' ;;
        fetch_manifest:nn) printf '%s' 'Hentar og kontrollerer signert versjonsinformasjon …' ;;
        fetch_manifest:en) printf '%s' 'Downloading and checking signed version information …' ;;
        download_release:nn) printf '%s' 'Lastar ned MeshPi %s og låste avhengigheiter …' ;;
        download_release:en) printf '%s' 'Downloading MeshPi %s and locked dependencies …' ;;
        check_hashes:nn) printf '%s' 'Kontrollerer SHA-256 for alle nedlasta filer …' ;;
        check_hashes:en) printf '%s' 'Checking SHA-256 for all downloaded files …' ;;
        bad_sha:nn) printf '%s' 'Ugyldig SHA-256 i version.json' ;;
        bad_sha:en) printf '%s' 'Invalid SHA-256 in version.json' ;;
        bad_sha_length:nn) printf '%s' 'Ugyldig SHA-256-lengd i version.json' ;;
        bad_sha_length:en) printf '%s' 'Invalid SHA-256 length in version.json' ;;
        bad_lock_hash:nn) printf '%s' 'Ugyldig låsefil-hash i version.json' ;;
        bad_lock_hash:en) printf '%s' 'Invalid lock-file hash in version.json' ;;
        sha_mismatch:nn) printf '%s' 'SHA-256 stemmer ikkje. Installasjonen er avbroten.' ;;
        sha_mismatch:en) printf '%s' 'SHA-256 does not match. Installation aborted.' ;;
        lock_mismatch:nn) printf '%s' 'SHA-256 for låsefila stemmer ikkje. Installasjonen er avbroten.' ;;
        lock_mismatch:en) printf '%s' 'The lock-file SHA-256 does not match. Installation aborted.' ;;
        create_environment:nn) printf '%s' 'Opprettar programmiljø og installerer avhengigheiter. Dette kan ta nokre minutt …' ;;
        create_environment:en) printf '%s' 'Creating the application environment and installing dependencies. This may take a few minutes …' ;;
        already_installed:nn) printf '%s' 'MeshPi %s er alt installert; bruker programfilene på nytt …' ;;
        already_installed:en) printf '%s' 'MeshPi %s is already installed; reusing the application files …' ;;
        selftest:nn) printf '%s' 'Kontrollerer installert versjon og køyrer sjølvtest …' ;;
        selftest:en) printf '%s' 'Checking the installed version and running the self-test …' ;;
        wrong_version:nn) printf '%s' 'Pakken rapporterer «%s», venta MeshPi %s.' ;;
        wrong_version:en) printf '%s' 'The package reports “%s”; expected MeshPi %s.' ;;
        activate:nn) printf '%s' 'Aktiverer MeshPi og konfigurerer bakgrunnstenesta …' ;;
        activate:en) printf '%s' 'Activating MeshPi and configuring the background service …' ;;
        stop_failed:nn) printf '%s' 'MeshPi-tenesta kunne ikkje stoppast før oppdateringa.' ;;
        stop_failed:en) printf '%s' 'The MeshPi service could not be stopped before the update.' ;;
        service_description:nn) printf '%s' 'MeshPi Meshtastic-bakgrunnsteneste' ;;
        service_description:en) printf '%s' 'MeshPi Meshtastic background service' ;;
        rollback:nn) printf '%s' 'Oppdateringa feila. Førre versjon er sett tilbake.' ;;
        rollback:en) printf '%s' 'The update failed. The previous version has been restored.' ;;
        complete:nn) printf '%s' 'Installasjonen er ferdig.' ;;
        complete:en) printf '%s' 'Installation is complete.' ;;
        installed:nn) printf '%s' 'MeshPi %s er installert i %s-modus.' ;;
        installed:en) printf '%s' 'MeshPi %s is installed in %s mode.' ;;
        start:nn) printf '%s' 'Opne ein ny terminal og start med: meshpi' ;;
        start:en) printf '%s' 'Open a new terminal and start with: meshpi' ;;
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

install_step() {
    number="$1"
    key="$2"
    shift 2
    format="$(message "$key")"
    printf '[%s/8] ' "$number"
    printf "$format\n" "$@"
}

set_env_value() {
    key="$1"
    value="$2"
    awk -v key="$key" -v value="$value" '
        BEGIN { found = 0 }
        index($0, key "=") == 1 {
            if (!found) print key "=" value
            found = 1
            next
        }
        { print }
        END { if (!found) print key "=" value }
    ' "$CONFIG_FILE" >"$TMP_DIR/config-update"
    config_tmp="$(mktemp "${CONFIG_FILE}.tmp.XXXXXX")"
    if cp -p "$CONFIG_FILE" "$config_tmp" &&
        cat "$TMP_DIR/config-update" >"$config_tmp" &&
        mv -f "$config_tmp" "$CONFIG_FILE"
    then
        return 0
    fi
    rm -f "$config_tmp"
    return 1
}

for argument in "$@"; do
    case "$argument" in
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
if [ "$(id -u)" -eq 0 ] && [ "$TEST_MODE" != "1" ]; then
    say_error no_sudo
    exit 1
fi

install_step 1 check_python
for command in shasum; do
    command -v "$command" >/dev/null 2>&1 || {
        say_error missing_command "$command"
        exit 1
    }
done
if {
    [ -z "${MESHPI_MANIFEST_FILE:-}" ] ||
    [ -z "${MESHPI_PACKAGE_FILE:-}" ] ||
    [ -z "${MESHPI_LOCK_FILE:-}" ]
} && ! command -v curl >/dev/null 2>&1
then
    say_error missing_command curl
    exit 1
fi

find_python() {
    if [ -n "${MESHPI_PYTHON:-}" ] &&
        [ -x "$MESHPI_PYTHON" ] &&
        "$MESHPI_PYTHON" -c \
            'import sys; raise SystemExit(sys.version_info < (3, 11))'
    then
        printf '%s\n' "$MESHPI_PYTHON"
        return 0
    fi
    for candidate in python3.14 python3.13 python3.12 python3.11 python3; do
        if command -v "$candidate" >/dev/null 2>&1 &&
            "$candidate" -c 'import sys; raise SystemExit(sys.version_info < (3, 11))'
        then
            command -v "$candidate"
            return 0
        fi
    done
    return 1
}

PYTHON="$(find_python || true)"
if [ -z "$PYTHON" ]; then
    say_error python_required
    say_error brew_python
    exit 1
fi

APP_ROOT="${MESHPI_APP_ROOT:-$HOME/Library/Application Support/MeshPi}"
APP_ROOT="$("$PYTHON" -c 'import pathlib, sys; print(pathlib.Path(sys.argv[1]).expanduser().resolve())' "$APP_ROOT")"
DATA_DIR="${MESHPI_DATA_DIR:-$APP_ROOT/data}"
CONFIG_FILE="${MESHPI_CONFIG_FILE:-$APP_ROOT/meshpi.env}"
BIN_DIR="${MESHPI_BIN_DIR:-$HOME/.local/bin}"
LAUNCH_AGENTS_DIR="${MESHPI_LAUNCH_AGENTS_DIR:-$HOME/Library/LaunchAgents}"
PLIST_FILE="$LAUNCH_AGENTS_DIR/org.venes.meshpi.plist"
RELEASES_DIR="$APP_ROOT/releases"
CURRENT_LINK="$APP_ROOT/current"
PREVIOUS_LINK="$APP_ROOT/previous"
LANGUAGE_FILE="${MESHPI_LANGUAGE_FILE:-$APP_ROOT/language.json}"
FRESH_INSTALL=0
if [ ! -e "$APP_ROOT" ] && [ ! -e "$CONFIG_FILE" ]; then
    FRESH_INSTALL=1
fi

TMP_DIR="$(mktemp -d)"
trap 'rm -rf "$TMP_DIR"' EXIT INT TERM
MANIFEST="$TMP_DIR/version.json"
# install_step 2 "Hentar og kontrollerer signert versjonsinformasjon …"
install_step 2 fetch_manifest
if [ -n "${MESHPI_MANIFEST_FILE:-}" ]; then
    cp "$MESHPI_MANIFEST_FILE" "$MANIFEST"
else
    curl -fsSL "$BASE_URL/version.json" -o "$MANIFEST"
fi

verify_manifest_signature() {
    "$PYTHON" - "$MANIFEST" "$LANGUAGE" <<'PY'
import base64, hashlib, hmac, json, sys
language = sys.argv[2]
messages = {
    "nn": {
        "missing_signature": "Versjonsmanifestet manglar ein gyldig signatur",
        "revoked_key": "Tilbakekalla signeringsnøkkel",
        "unknown_key": "Ukjend signeringsnøkkel",
        "invalid_signature": "Ugyldig manifestsignatur",
        "signature_mismatch": "Signaturen på versjonsmanifestet stemmer ikkje",
    },
    "en": {
        "missing_signature": "The version manifest is missing a valid signature",
        "revoked_key": "Revoked signing key",
        "unknown_key": "Unknown signing key",
        "invalid_signature": "Invalid manifest signature",
        "signature_mismatch": "The version manifest signature does not match",
    },
}[language]
current_modulus = int("c1370fa9e2eb0d22e354c58594e369f9db44156f834522bf69a8da523a30ac0d4539e08a30d76e854b40ae693da388af11ca62ee24c1e6f43ec128be550e8b7655d86955ae858b9f30237ba02e2773e9ad2fcfe1644484e909a8805a6c8a289dda69cedbc973d7427278442d8acb1d00a0c5cd242c34404843ea684ece7ad40a59d902633624ae36ae3f4e8c9e401bb887ef650f1fe001f9fd7661841b98a95f67aea496c05054a4c41c287c09d1dd1e94e9c01cc997162a50e02df6d28645d268cceb35daf7ad1e4202b2b1714a71e2b18d0564f12a468c2bb4d7e678a1c4c493de0c945f0f2665efb658238dd4dd617b73acd8e20e4c5f440d2d4ee13617f2c2857c0457e0a3a73aac43d0e23f5c0f56f9042a6d1e6221383481a9bcc952576904895e013a5f12b6c0aa08b9ba911df7be42a4d0a3c31ca98111b4344d8079fdb55a43379fde9968edf9ce7b3554333d5819ad196935e928012d1b20b4aed5ee48d8851dd69458b15998712530b4d91228b06ae109741c0cf4ab723f092e49", 16)
trusted_keys = {"meshpi-release-2026-01": (65537, current_modulus)}
revoked_key_ids = set()
with open(sys.argv[1], encoding="utf-8") as handle:
    manifest = json.load(handle)
signature = manifest.pop("signature", None)
if not isinstance(signature, dict) or signature.get("algorithm") != "rsa-pkcs1v15-sha256":
    raise SystemExit(messages["missing_signature"])
key_id = str(signature.get("key_id", ""))
if key_id in revoked_key_ids:
    raise SystemExit(messages["revoked_key"])
key = trusted_keys.get(key_id)
if key is None:
    raise SystemExit(messages["unknown_key"])
exponent, modulus = key
try:
    raw = base64.b64decode(signature["value"], validate=True)
except (KeyError, ValueError) as exc:
    raise SystemExit(messages["invalid_signature"]) from exc
canonical = json.dumps(manifest, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()
size = (modulus.bit_length() + 7) // 8
actual = pow(int.from_bytes(raw, "big"), exponent, modulus).to_bytes(size, "big")
digest_info = bytes.fromhex("3031300d060960864801650304020105000420")
digest = hashlib.sha256(canonical).digest()
pad = size - len(digest_info) - len(digest) - 3
expected = b"\x00\x01" + b"\xff" * pad + b"\x00" + digest_info + digest
if len(raw) != size or pad < 8 or not hmac.compare_digest(actual, expected):
    raise SystemExit(messages["signature_mismatch"])
PY
}
verify_manifest_signature

manifest_value() {
    "$PYTHON" - "$MANIFEST" "$1" "$LANGUAGE" <<'PY'
import json
import sys

with open(sys.argv[1], encoding="utf-8") as handle:
    data = json.load(handle)
value = data
for part in sys.argv[2].split("."):
    value = value[part]
if not isinstance(value, str) or not value:
    raise SystemExit("Ugyldig version.json" if sys.argv[3] == "nn" else "Invalid version.json")
print(value)
PY
}

VERSION="$(manifest_value latest_version)"
PACKAGE_URL="$(manifest_value package.url)"
PACKAGE_FILENAME="$(manifest_value package.filename)"
EXPECTED_SHA256="$(manifest_value package.sha256)"
LOCK_URL="$(manifest_value locks.macos.url)"
EXPECTED_LOCK_SHA256="$(manifest_value locks.macos.sha256)"
IPC_TOKEN="$("$PYTHON" -c 'import secrets; print(secrets.token_hex(32))')"
"$PYTHON" - "$VERSION" "$PACKAGE_FILENAME" "$LANGUAGE" <<'PY'
import re
import sys

version, filename = sys.argv[1:3]
pattern = r"(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)(?:(?:a|b|rc)(0|[1-9]\d*))?"
if re.fullmatch(pattern, version) is None:
    raise SystemExit("Ugyldig versjon i manifestet" if sys.argv[3] == "nn" else "Invalid version in the manifest")
if filename != f"meshpi-{version}-py3-none-any.whl":
    raise SystemExit("Ugyldig pakkenamn i manifestet" if sys.argv[3] == "nn" else "Invalid package name in the manifest")
PY
case "$EXPECTED_SHA256" in
    *[!0-9a-fA-F]* | "") say_error bad_sha; exit 1 ;;
esac
[ "${#EXPECTED_SHA256}" -eq 64 ] || {
    say_error bad_sha_length
    exit 1
}
case "$EXPECTED_LOCK_SHA256" in
    *[!0-9a-fA-F]* | "") say_error bad_lock_hash; exit 1 ;;
esac
[ "${#EXPECTED_LOCK_SHA256}" -eq 64 ] || {
    say_error bad_lock_hash
    exit 1
}

WHEEL="$TMP_DIR/$PACKAGE_FILENAME"
LOCK_FILE="$TMP_DIR/requirements-macos.txt"
install_step 3 download_release "$VERSION"
if [ -n "${MESHPI_PACKAGE_FILE:-}" ]; then
    cp "$MESHPI_PACKAGE_FILE" "$WHEEL"
else
    curl -fsSL "$PACKAGE_URL" -o "$WHEEL"
fi
if [ -n "${MESHPI_LOCK_FILE:-}" ]; then
    cp "$MESHPI_LOCK_FILE" "$LOCK_FILE"
else
    curl -fsSL "$LOCK_URL" -o "$LOCK_FILE"
fi
install_step 4 check_hashes
ACTUAL_SHA256="$(shasum -a 256 "$WHEEL" | awk '{print $1}')"
[ "$ACTUAL_SHA256" = "$EXPECTED_SHA256" ] || {
    say_error sha_mismatch
    exit 1
}
ACTUAL_LOCK_SHA256="$(shasum -a 256 "$LOCK_FILE" | awk '{print $1}')"
[ "$ACTUAL_LOCK_SHA256" = "$EXPECTED_LOCK_SHA256" ] || {
    say_error lock_mismatch
    exit 1
}

mkdir -p \
    "$APP_ROOT" "$DATA_DIR" "$BIN_DIR" "$LAUNCH_AGENTS_DIR" "$RELEASES_DIR" \
    "$(dirname "$CONFIG_FILE")"
chmod 0700 "$DATA_DIR"
if [ ! -f "$CONFIG_FILE" ]; then
    cat >"$CONFIG_FILE" <<EOF
MESHTASTIC_HOST=
MESHTASTIC_PORT=4403
DATABASE_PATH=$DATA_DIR/meshtastic.db
CONNECTIONS_PATH=$DATA_DIR/connections.json
DISCOVERY_SUBNET=
IPC_HOST=127.0.0.1
IPC_PORT=$IPC_PORT_VALUE
IPC_TRANSPORT=unix
IPC_SOCKET_PATH=$DATA_DIR/meshpi.sock
IPC_SOCKET_GID=
IPC_TOKEN=$IPC_TOKEN
LOG_LEVEL=INFO
LOG_FILE=$DATA_DIR/meshpi.log
LOG_MAX_BYTES=5242880
LOG_BACKUP_COUNT=3
UPDATE_URL=$BASE_URL/version.json
UPDATE_TIMEOUT=3
BACKGROUND_MODE=$MODE
EOF
else
    set_env_value BACKGROUND_MODE "$MODE"
    set_env_value IPC_TRANSPORT unix
    set_env_value IPC_SOCKET_PATH "$DATA_DIR/meshpi.sock"
    set_env_value IPC_SOCKET_GID ""
    grep -q '^LOG_FILE=.' "$CONFIG_FILE" || set_env_value LOG_FILE "$DATA_DIR/meshpi.log"
    grep -q '^LOG_MAX_BYTES=' "$CONFIG_FILE" || set_env_value LOG_MAX_BYTES 5242880
    grep -q '^LOG_BACKUP_COUNT=' "$CONFIG_FILE" || set_env_value LOG_BACKUP_COUNT 3
    if grep -Eq '^IPC_TOKEN=[0-9a-fA-F]{64}$' "$CONFIG_FILE"; then
        :
    elif grep -q '^IPC_TOKEN=' "$CONFIG_FILE"; then
        sed "s/^IPC_TOKEN=.*/IPC_TOKEN=$IPC_TOKEN/" "$CONFIG_FILE" >"$TMP_DIR/token-config"
        cat "$TMP_DIR/token-config" >"$CONFIG_FILE"
    else
        printf 'IPC_TOKEN=%s\n' "$IPC_TOKEN" >>"$CONFIG_FILE"
    fi
fi
chmod 0600 "$CONFIG_FILE"

RELEASE="$RELEASES_DIR/$VERSION"
OLD_RELEASE=""
if [ -L "$CURRENT_LINK" ]; then
    OLD_RELEASE="$(cd "$CURRENT_LINK" 2>/dev/null && pwd -P || true)"
fi
if [ "$RELEASE" != "$OLD_RELEASE" ]; then
    install_step 5 create_environment
    rm -rf "$RELEASE"
    "$PYTHON" -m venv "$RELEASE/venv"
    "$RELEASE/venv/bin/python" -I -c \
        'import runpy,sys; sys.path.insert(0,sys.argv.pop(1)); runpy.run_module("meshpi.bootstrap",run_name="__main__")' \
        "$WHEEL" "$LOCK_FILE"
    "$RELEASE/venv/bin/python" -m pip install -q --no-deps "$WHEEL"
else
    install_step 5 already_installed "$VERSION"
fi
install_step 6 selftest
INSTALLED_VERSION="$("$RELEASE/venv/bin/meshpi" --version)"
[ "$INSTALLED_VERSION" = "MeshPi $VERSION" ] || {
    say_error wrong_version "$INSTALLED_VERSION" "$VERSION"
    exit 1
}
MESHPI_LANGUAGE="$LANGUAGE" \
    "$RELEASE/venv/bin/meshpi" --env-file "$CONFIG_FILE" doctor --offline

install_step 7 activate
DOMAIN="gui/$(id -u)"
LABEL="org.venes.meshpi"
if [ "$SKIP_SERVICE" != "1" ]; then
    launchctl bootout "$DOMAIN/$LABEL" >/dev/null 2>&1 || true

    # launchctl may return from bootout before the old job has been removed
    # from the GUI domain.  Vent før same label blir registrert på nytt.
    i=0
    while launchctl print "$DOMAIN/$LABEL" >/dev/null 2>&1; do
        i=$((i + 1))
        if [ "$i" -ge 50 ]; then
            say_error stop_failed
            exit 1
        fi
        sleep 0.1
    done
fi

if [ "$SKIP_SERVICE" != "1" ] && [ -x "$BIN_DIR/meshpi" ]; then
    "$BIN_DIR/meshpi" service stop >/dev/null 2>&1 || true
fi

if [ -z "$OLD_RELEASE" ] && [ -d "$APP_ROOT/venv" ]; then
    LEGACY_VERSION="$("$APP_ROOT/venv/bin/python" -m pip show meshpi 2>/dev/null |
        awk '/^Version:/{print $2; exit}')"
    LEGACY_VERSION="${LEGACY_VERSION:-legacy}"
    OLD_RELEASE="$RELEASES_DIR/$LEGACY_VERSION"
    if [ ! -e "$OLD_RELEASE" ]; then
        mkdir -p "$OLD_RELEASE"
        mv "$APP_ROOT/venv" "$OLD_RELEASE/venv"
    fi
fi

switch_link() {
    target="$1"
    temporary="$APP_ROOT/.current-$$"
    rm -f "$temporary"
    ln -s "$target" "$temporary"
    "$PYTHON" - "$temporary" "$CURRENT_LINK" <<'PY'
import os
import sys

os.replace(sys.argv[1], sys.argv[2])
PY
}

if [ -n "$OLD_RELEASE" ] && [ "$OLD_RELEASE" != "$RELEASE" ]; then
    rm -f "$PREVIOUS_LINK"
    ln -s "$OLD_RELEASE" "$PREVIOUS_LINK"
fi
switch_link "$RELEASE"

rm -f "$BIN_DIR/meshpi"
cat >"$BIN_DIR/meshpi" <<EOF
#!/bin/sh
exec "$CURRENT_LINK/venv/bin/meshpi" --env-file "$CONFIG_FILE" "\$@"
EOF
chmod 0755 "$BIN_DIR/meshpi"

PROFILE="$HOME/.zprofile"
PATH_LINE='export PATH="$HOME/.local/bin:$PATH"'
if [ "${MESHPI_SKIP_PATH:-0}" != "1" ] &&
    ! grep -F "$PATH_LINE" "$PROFILE" >/dev/null 2>&1
then
    printf '\n%s\n' "$PATH_LINE" >>"$PROFILE"
    : >"$APP_ROOT/path-added-by-meshpi"
fi

if [ "$SKIP_SERVICE" = "1" ]; then
    :
elif [ "$MODE" = "always" ]; then
    cat >"$PLIST_FILE" <<EOF
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>Label</key>
    <string>$LABEL</string>
    <key>ProgramArguments</key>
    <array>
        <string>$CURRENT_LINK/venv/bin/meshpi</string>
        <string>--env-file</string>
        <string>$CONFIG_FILE</string>
        <string>daemon</string>
    </array>
    <key>WorkingDirectory</key>
    <string>$DATA_DIR</string>
    <key>RunAtLoad</key><true/>
    <key>KeepAlive</key><true/>
    <key>ThrottleInterval</key><integer>5</integer>
    <key>Umask</key><integer>63</integer>
    <key>StandardOutPath</key><string>$DATA_DIR/meshpi-launchd.log</string>
    <key>StandardErrorPath</key><string>$DATA_DIR/meshpi-launchd-error.log</string>
</dict>
</plist>
EOF
    plutil -lint "$PLIST_FILE" >/dev/null
    launchctl bootstrap "$DOMAIN" "$PLIST_FILE"

    READY=0
    i=0
    while [ "$i" -lt 40 ]; do
        if [ "${MESHPI_FORCE_HEALTH_FAILURE:-0}" != "1" ] &&
            "$CURRENT_LINK/venv/bin/meshpi" --env-file "$CONFIG_FILE" status >/dev/null 2>&1
        then
            READY=1
            break
        fi
        i=$((i + 1))
        sleep 0.25
    done
    if [ "$READY" != "1" ]; then
        launchctl bootout "$DOMAIN/$LABEL" >/dev/null 2>&1 || true
        if [ -n "$OLD_RELEASE" ] && [ -d "$OLD_RELEASE" ]; then
            switch_link "$OLD_RELEASE"
            rm -f "$PREVIOUS_LINK"
            launchctl bootstrap "$DOMAIN" "$PLIST_FILE" >/dev/null 2>&1 || true
            say_error rollback
        fi
        exit 1
    fi
else
    rm -f "$PLIST_FILE"
fi

if [ "$FRESH_INSTALL" = "1" ] && [ ! -e "$LANGUAGE_FILE" ]; then
    language_tmp="$(mktemp "$APP_ROOT/.language.json.tmp.XXXXXX")"
    printf '{"language":"%s"}\n' "$LANGUAGE" >"$language_tmp"
    chmod 0600 "$language_tmp"
    mv -f "$language_tmp" "$LANGUAGE_FILE"
fi

install_step 8 complete
say installed "$VERSION" "$MODE"
say start
