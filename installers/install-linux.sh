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

DETECTED_LANGUAGE="$(detect_language)"
LANGUAGE="${MESHPI_LANGUAGE:-$DETECTED_LANGUAGE}"

message() {
    case "$1:$LANGUAGE" in
        invalid_language:nn) printf '%s' 'Språket må vere «nn» eller «en».' ;;
        invalid_language:en) printf '%s' 'Language must be “nn” or “en”.' ;;
        unknown_argument:nn) printf '%s' 'Ukjent argument: %s' ;;
        unknown_argument:en) printf '%s' 'Unknown argument: %s' ;;
        invalid_mode:nn) printf '%s' 'MESHPI_MODE må vere «always» eller «session».' ;;
        invalid_mode:en) printf '%s' 'MESHPI_MODE must be “always” or “session”.' ;;
        root_required:nn) printf '%s' 'Køyr installasjonen som root.' ;;
        root_required:en) printf '%s' 'Run the installation as root.' ;;
        root_command:nn) printf '%s' 'Last ned install-linux.sh og køyr: sudo sh install-linux.sh' ;;
        root_command:en) printf '%s' 'Download install-linux.sh and run: sudo sh install-linux.sh' ;;
        session_without_sudo:nn) printf '%s' 'Session-modus skal installerast utan sudo.' ;;
        session_without_sudo:en) printf '%s' 'Session mode must be installed without sudo.' ;;
        check_python:nn) printf '%s' 'Kontrollerer Python 3.11 eller nyare …' ;;
        check_python:en) printf '%s' 'Checking for Python 3.11 or newer …' ;;
        missing_command:nn) printf '%s' 'Manglar kommandoen «%s».' ;;
        missing_command:en) printf '%s' 'Required command “%s” is missing.' ;;
        systemd_required:nn) printf '%s' 'Always-modus krev systemd og ein godkjend systemctl-sti.' ;;
        systemd_required:en) printf '%s' 'Always mode requires systemd and an approved systemctl path.' ;;
        python_required:nn) printf '%s' 'MeshPi krev Python 3.11 eller nyare.' ;;
        python_required:en) printf '%s' 'MeshPi requires Python 3.11 or newer.' ;;
        debian_python:nn) printf '%s' 'På Debian/Raspberry Pi OS: sudo apt install python3 python3-venv' ;;
        debian_python:en) printf '%s' 'On Debian/Raspberry Pi OS: sudo apt install python3 python3-venv' ;;
        venv_failed:nn) printf '%s' 'Python %s kan ikkje opprette virtuelle miljø.' ;;
        venv_failed:en) printf '%s' 'Python %s cannot create virtual environments.' ;;
        debian_venv:nn) printf '%s' 'På Debian/Ubuntu: sudo apt install python%s-venv' ;;
        debian_venv:en) printf '%s' 'On Debian/Ubuntu: sudo apt install python%s-venv' ;;
        distro_venv:nn) printf '%s' 'Alternativt kan distribusjonen bruke pakken python3-venv.' ;;
        distro_venv:en) printf '%s' 'Alternatively, your distribution may provide the python3-venv package.' ;;
        shared_group:nn) printf '%s' 'Primærgruppa til %s er delt av fleire kontoar.' ;;
        shared_group:en) printf '%s' 'The primary group of %s is shared by multiple accounts.' ;;
        private_group:nn) printf '%s' 'MeshPi krev ei privat primærgruppe for trygg IPC-tilgang.' ;;
        private_group:en) printf '%s' 'MeshPi requires a private primary group for secure IPC access.' ;;
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
        service_description:nn) printf '%s' 'MeshPi Meshtastic CLI-teneste' ;;
        service_description:en) printf '%s' 'MeshPi Meshtastic CLI service' ;;
        rollback:nn) printf '%s' 'Oppdateringa feila. Førre versjon er sett tilbake.' ;;
        rollback:en) printf '%s' 'The update failed. The previous version has been restored.' ;;
        no_rollback:nn) printf '%s' 'Oppdateringa feila, og ingen førre versjon finst.' ;;
        no_rollback:en) printf '%s' 'The update failed, and no previous version is available.' ;;
        complete:nn) printf '%s' 'Installasjonen er ferdig.' ;;
        complete:en) printf '%s' 'Installation is complete.' ;;
        installed:nn) printf '%s' 'MeshPi %s er installert i %s-modus.' ;;
        installed:en) printf '%s' 'MeshPi %s is installed in %s mode.' ;;
        start:nn) printf '%s' 'Start med: meshpi' ;;
        start:en) printf '%s' 'Start with: meshpi' ;;
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

resolve_ipc_socket_gid() {
    ipc_client_user="$1"
    ipc_gid="$(id -g "$ipc_client_user")"
    case "$ipc_gid" in
        *[!0-9]* | 0) return 0 ;;
    esac

    ipc_gid_users="$(
        getent passwd |
            awk -F: -v gid="$ipc_gid" \
                '$4 == gid { count++ } END { print count + 0 }'
    )"
    if [ "$ipc_gid_users" -gt 1 ]; then
        say_error shared_group "$ipc_client_user"
        say_error private_group
        return 1
    fi
    printf '%s\n' "$ipc_gid"
}

for argument in "$@"; do
    case "$argument" in
        --mode=always) MODE=always ;;
        --mode=session) MODE=session ;;
        --no-service) MODE=session ;;
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

if [ "$MODE" != "always" ] && [ "$MODE" != "session" ]; then
    say_error invalid_mode
    exit 2
fi
if [ "$MODE" = "always" ] && [ "$(id -u)" -ne 0 ] && [ "$TEST_MODE" != "1" ]; then
    say_error root_required
    say_error root_command
    exit 1
fi
if [ "$MODE" = "session" ] && [ "$(id -u)" -eq 0 ] && [ "$TEST_MODE" != "1" ]; then
    say_error session_without_sudo
    exit 1
fi

install_step 1 check_python
for command in sha256sum awk; do
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

SYSTEMCTL=
if [ "$MODE" = "always" ] && [ "$SKIP_SERVICE" != "1" ]; then
    for candidate in /usr/bin/systemctl /bin/systemctl; do
        if [ -x "$candidate" ]; then
            SYSTEMCTL="$candidate"
            break
        fi
    done
    if [ -z "$SYSTEMCTL" ]; then
        say_error systemd_required
        exit 1
    fi
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
    say_error debian_python
    exit 1
fi

TMP_DIR="$(mktemp -d)"
trap 'rm -rf "$TMP_DIR"' EXIT INT TERM

PYTHON_SERIES="$("$PYTHON" -c \
    'import sys; print(f"{sys.version_info.major}.{sys.version_info.minor}")')"
VENV_CHECK="$TMP_DIR/venv-check"
if ! "$PYTHON" -m venv "$VENV_CHECK" >/dev/null 2>&1 ||
    ! "$VENV_CHECK/bin/python" -m pip --version >/dev/null 2>&1
then
    say_error venv_failed "$PYTHON_SERIES"
    # sudo apt install python$PYTHON_SERIES-venv
    say_error debian_venv "$PYTHON_SERIES"
    say_error distro_venv
    exit 1
fi
rm -rf "$VENV_CHECK"

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
else
    PREFIX="${MESHPI_PREFIX:-/opt/meshpi}"
    STATE_DIR="${MESHPI_STATE_DIR:-/var/lib/meshpi}"
    CONFIG_FILE="${MESHPI_CONFIG_FILE:-/etc/meshpi.env}"
    BIN_FILE="${MESHPI_BIN_FILE:-/usr/local/bin/meshpi}"
fi
UNIT_FILE="${MESHPI_UNIT_FILE:-/etc/systemd/system/meshpi.service}"
RELEASES_DIR="$PREFIX/releases"
CURRENT_LINK="$PREFIX/current"
PREVIOUS_LINK="$PREFIX/previous"
LANGUAGE_FILE="${MESHPI_LANGUAGE_FILE:-$USER_HOME/.config/MeshPi/language.json}"
FRESH_INSTALL=0
if [ ! -e "$PREFIX" ] && [ ! -e "$STATE_DIR" ] && [ ! -e "$CONFIG_FILE" ]; then
    FRESH_INSTALL=1
fi
CLIENT_USER="$INSTALL_USER"
if [ "$MODE" = "always" ] && [ -f "$CONFIG_FILE" ]; then
    EXISTING_OWNER="$(stat -c %U "$CONFIG_FILE" 2>/dev/null || true)"
    if [ -n "$EXISTING_OWNER" ] &&
        [ "$EXISTING_OWNER" != "root" ] &&
        getent passwd "$EXISTING_OWNER" >/dev/null 2>&1
    then
        CLIENT_USER="$EXISTING_OWNER"
    fi
fi
if [ "$MODE" = "always" ]; then
    IPC_TRANSPORT_VALUE=unix
    IPC_SOCKET_PATH_VALUE=/run/meshpi/meshpi.sock
    IPC_SOCKET_GID_VALUE="$(resolve_ipc_socket_gid "$CLIENT_USER")"
else
    IPC_TRANSPORT_VALUE=unix
    IPC_SOCKET_PATH_VALUE="$STATE_DIR/meshpi.sock"
    IPC_SOCKET_GID_VALUE=
fi

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
import base64
import hashlib
import hmac
import json
import sys

language = sys.argv[2]
messages = {
    "nn": {
        "missing_signature": "Versjonsmanifestet manglar signatur",
        "unknown_algorithm": "Ukjend signaturalgoritme",
        "revoked_key": "Tilbakekalla signeringsnøkkel",
        "unknown_key": "Ukjend signeringsnøkkel",
        "invalid_signature": "Ugyldig manifestsignatur",
        "signature_mismatch": "Signaturen på versjonsmanifestet stemmer ikkje",
    },
    "en": {
        "missing_signature": "The version manifest is missing a signature",
        "unknown_algorithm": "Unknown signature algorithm",
        "revoked_key": "Revoked signing key",
        "unknown_key": "Unknown signing key",
        "invalid_signature": "Invalid manifest signature",
        "signature_mismatch": "The version manifest signature does not match",
    },
}[language]

current_modulus = int("c1370fa9e2eb0d22e354c58594e369f9db44156f834522bf69a8da523a30ac0d4539e08a30d76e854b40ae693da388af11ca62ee24c1e6f43ec128be550e8b7655d86955ae858b9f30237ba02e2773e9ad2fcfe1644484e909a8805a6c8a289dda69cedbc973d7427278442d8acb1d00a0c5cd242c34404843ea684ece7ad40a59d902633624ae36ae3f4e8c9e401bb887ef650f1fe001f9fd7661841b98a95f67aea496c05054a4c41c287c09d1dd1e94e9c01cc997162a50e02df6d28645d268cceb35daf7ad1e4202b2b1714a71e2b18d0564f12a468c2bb4d7e678a1c4c493de0c945f0f2665efb658238dd4dd617b73acd8e20e4c5f440d2d4ee13617f2c2857c0457e0a3a73aac43d0e23f5c0f56f9042a6d1e6221383481a9bcc952576904895e013a5f12b6c0aa08b9ba911df7be42a4d0a3c31ca98111b4344d8079fdb55a43379fde9968edf9ce7b3554333d5819ad196935e928012d1b20b4aed5ee48d8851dd69458b15998712530b4d91228b06ae109741c0cf4ab723f092e49", 16)
exponent = 65537
trusted_keys = {"meshpi-release-2026-01": (exponent, current_modulus)}
revoked_key_ids = set()
with open(sys.argv[1], encoding="utf-8") as handle:
    manifest = json.load(handle)
signature = manifest.pop("signature", None)
if not isinstance(signature, dict):
    raise SystemExit(messages["missing_signature"])
if signature.get("algorithm") != "rsa-pkcs1v15-sha256":
    raise SystemExit(messages["unknown_algorithm"])
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
canonical = json.dumps(
    manifest, ensure_ascii=False, sort_keys=True, separators=(",", ":")
).encode("utf-8")
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
    raise SystemExit(
        "Ugyldig version.json"
        if sys.argv[3] == "nn"
        else "Invalid version.json"
    )
print(value)
PY
}

VERSION="$(manifest_value latest_version)"
PACKAGE_URL="$(manifest_value package.url)"
PACKAGE_FILENAME="$(manifest_value package.filename)"
EXPECTED_SHA256="$(manifest_value package.sha256)"
LOCK_URL="$(manifest_value locks.linux.url)"
EXPECTED_LOCK_SHA256="$(manifest_value locks.linux.sha256)"
IPC_TOKEN="$("$PYTHON" -c 'import secrets; print(secrets.token_hex(32))')"
"$PYTHON" - "$VERSION" "$PACKAGE_FILENAME" "$LANGUAGE" <<'PY'
import re
import sys

version, filename = sys.argv[1:3]
pattern = r"(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)(?:(?:a|b|rc)(0|[1-9]\d*))?"
if re.fullmatch(pattern, version) is None:
    raise SystemExit(
        "Ugyldig versjon i manifestet"
        if sys.argv[3] == "nn"
        else "Invalid version in the manifest"
    )
if filename != f"meshpi-{version}-py3-none-any.whl":
    raise SystemExit(
        "Ugyldig pakkenamn i manifestet"
        if sys.argv[3] == "nn"
        else "Invalid package name in the manifest"
    )
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
LOCK_FILE="$TMP_DIR/requirements-linux.txt"
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
ACTUAL_SHA256="$(sha256sum "$WHEEL" | awk '{print $1}')"
[ "$ACTUAL_SHA256" = "$EXPECTED_SHA256" ] || {
    say_error sha_mismatch
    exit 1
}
ACTUAL_LOCK_SHA256="$(sha256sum "$LOCK_FILE" | awk '{print $1}')"
[ "$ACTUAL_LOCK_SHA256" = "$EXPECTED_LOCK_SHA256" ] || {
    say_error lock_mismatch
    exit 1
}

if [ "$MODE" = "always" ] && [ "$SKIP_SERVICE" != "1" ]; then
    getent group dialout >/dev/null 2>&1 || groupadd --system dialout
    if ! id meshpi >/dev/null 2>&1; then
        useradd --system --home-dir "$STATE_DIR" --shell /usr/sbin/nologin --user-group meshpi
    fi
    usermod -aG dialout meshpi
fi

install -d -m 0755 "$PREFIX" "$RELEASES_DIR" "$(dirname "$BIN_FILE")"
install -d -m 0750 "$STATE_DIR"
install -d -m 0755 "$(dirname "$CONFIG_FILE")"

if [ ! -f "$CONFIG_FILE" ]; then
    cat >"$CONFIG_FILE" <<EOF
MESHTASTIC_HOST=
MESHTASTIC_PORT=4403
DATABASE_PATH=$STATE_DIR/meshtastic.db
CONNECTIONS_PATH=$STATE_DIR/connections.json
DISCOVERY_SUBNET=
IPC_HOST=127.0.0.1
IPC_PORT=$IPC_PORT_VALUE
IPC_TRANSPORT=$IPC_TRANSPORT_VALUE
IPC_SOCKET_PATH=$IPC_SOCKET_PATH_VALUE
IPC_SOCKET_GID=$IPC_SOCKET_GID_VALUE
IPC_TOKEN=$IPC_TOKEN
LOG_LEVEL=INFO
UPDATE_URL=$BASE_URL/version.json
UPDATE_TIMEOUT=3
BACKGROUND_MODE=$MODE
EOF
else
    set_env_value BACKGROUND_MODE "$MODE"
    set_env_value IPC_TRANSPORT "$IPC_TRANSPORT_VALUE"
    set_env_value IPC_SOCKET_PATH "$IPC_SOCKET_PATH_VALUE"
    set_env_value IPC_SOCKET_GID "$IPC_SOCKET_GID_VALUE"
    if grep -Eq '^IPC_TOKEN=[0-9a-fA-F]{64}$' "$CONFIG_FILE"; then
        :
    elif grep -q '^IPC_TOKEN=' "$CONFIG_FILE"; then
        sed "s/^IPC_TOKEN=.*/IPC_TOKEN=$IPC_TOKEN/" "$CONFIG_FILE" >"$TMP_DIR/token-config"
        cat "$TMP_DIR/token-config" >"$CONFIG_FILE"
    else
        printf 'IPC_TOKEN=%s\n' "$IPC_TOKEN" >>"$CONFIG_FILE"
    fi
fi

if [ "$MODE" = "always" ] && [ "$SKIP_SERVICE" != "1" ]; then
    chown "$CLIENT_USER:meshpi" "$CONFIG_FILE"
    chmod 0640 "$CONFIG_FILE"
    chown -R meshpi:meshpi "$STATE_DIR"
    chmod 0750 "$STATE_DIR"
else
    chmod 0600 "$CONFIG_FILE"
fi

RELEASE="$RELEASES_DIR/$VERSION"
OLD_RELEASE=""
if [ -L "$CURRENT_LINK" ]; then
    OLD_RELEASE="$(readlink -f "$CURRENT_LINK" || true)"
fi
if [ "$RELEASE" != "$OLD_RELEASE" ]; then
    install_step 5 create_environment
    rm -rf "$RELEASE"
    "$PYTHON" -m venv "$RELEASE/.venv"
    "$RELEASE/.venv/bin/python" -m pip install -q --require-hashes -r "$LOCK_FILE"
    "$RELEASE/.venv/bin/python" -m pip install -q --no-deps "$WHEEL"
else
    install_step 5 already_installed "$VERSION"
fi
install_step 6 selftest
INSTALLED_VERSION="$("$RELEASE/.venv/bin/meshpi" --version)"
[ "$INSTALLED_VERSION" = "MeshPi $VERSION" ] || {
    say_error wrong_version "$INSTALLED_VERSION" "$VERSION"
    exit 1
}
MESHPI_LANGUAGE="$LANGUAGE" \
    "$RELEASE/.venv/bin/meshpi" --env-file "$CONFIG_FILE" doctor --offline

switch_link() {
    target="$1"
    temporary="$PREFIX/.current-$$"
    rm -f "$temporary"
    ln -s "$target" "$temporary"
    mv -Tf "$temporary" "$CURRENT_LINK"
}

install_step 7 activate
if [ "$SKIP_SERVICE" != "1" ]; then
    if [ "$MODE" = "always" ]; then
        "$SYSTEMCTL" stop meshpi.service >/dev/null 2>&1 || true
    elif [ -x "$BIN_FILE" ]; then
        "$BIN_FILE" service stop >/dev/null 2>&1 || true
    fi
fi

if [ -z "$OLD_RELEASE" ] && [ -d "$PREFIX/.venv" ]; then
    LEGACY_VERSION="$("$PREFIX/.venv/bin/python" -m pip show meshpi 2>/dev/null |
        awk '/^Version:/{print $2; exit}')"
    LEGACY_VERSION="${LEGACY_VERSION:-legacy}"
    OLD_RELEASE="$RELEASES_DIR/$LEGACY_VERSION"
    if [ ! -e "$OLD_RELEASE" ]; then
        mkdir -p "$OLD_RELEASE"
        mv "$PREFIX/.venv" "$OLD_RELEASE/.venv"
    fi
fi

if [ -n "$OLD_RELEASE" ] && [ "$OLD_RELEASE" != "$RELEASE" ]; then
    rm -f "$PREVIOUS_LINK"
    ln -s "$OLD_RELEASE" "$PREVIOUS_LINK"
fi
switch_link "$RELEASE"

rm -f "$BIN_FILE"
if [ "$MODE" = "session" ]; then
    cat >"$BIN_FILE" <<EOF
#!/bin/sh
exec "$CURRENT_LINK/.venv/bin/meshpi" --env-file "$CONFIG_FILE" "\$@"
EOF
else
    cat >"$BIN_FILE" <<EOF
#!/bin/sh
exec "$CURRENT_LINK/.venv/bin/meshpi" --env-file "$CONFIG_FILE" "\$@"
EOF
fi
chmod 0755 "$BIN_FILE"

if [ "$MODE" = "always" ] && [ "$SKIP_SERVICE" != "1" ]; then
    cat >"$UNIT_FILE" <<EOF
[Unit]
Description=$(message service_description)
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
User=meshpi
Group=meshpi
SupplementaryGroups=dialout $IPC_SOCKET_GID_VALUE
WorkingDirectory=$STATE_DIR
Environment=PYTHONDONTWRITEBYTECODE=1
UMask=0077
RuntimeDirectory=meshpi
RuntimeDirectoryMode=0711
ExecStart=$CURRENT_LINK/.venv/bin/meshpi --env-file $CONFIG_FILE daemon
Restart=on-failure
RestartSec=5
NoNewPrivileges=true
PrivateTmp=true
ProtectSystem=strict
ProtectHome=true
ReadWritePaths=$STATE_DIR /run/meshpi

[Install]
WantedBy=multi-user.target
EOF
    chmod 0644 "$UNIT_FILE"
    "$SYSTEMCTL" daemon-reload
    "$SYSTEMCTL" enable meshpi.service >/dev/null
    "$SYSTEMCTL" start meshpi.service

    READY=0
    i=0
    while [ "$i" -lt 40 ]; do
        if [ "${MESHPI_FORCE_HEALTH_FAILURE:-0}" != "1" ] &&
            "$CURRENT_LINK/.venv/bin/meshpi" --env-file "$CONFIG_FILE" status >/dev/null 2>&1
        then
            READY=1
            break
        fi
        i=$((i + 1))
        sleep 0.25
    done
    if [ "$READY" != "1" ]; then
        "$SYSTEMCTL" stop meshpi.service >/dev/null 2>&1 || true
        if [ -n "$OLD_RELEASE" ] && [ -d "$OLD_RELEASE" ]; then
            switch_link "$OLD_RELEASE"
            rm -f "$PREVIOUS_LINK"
            "$SYSTEMCTL" start meshpi.service || true
            say_error rollback
        else
            say_error no_rollback
        fi
        exit 1
    fi
fi

if [ "$FRESH_INSTALL" = "1" ] && [ ! -e "$LANGUAGE_FILE" ]; then
    language_dir="$(dirname "$LANGUAGE_FILE")"
    install -d -m 0700 "$language_dir"
    language_tmp="$(mktemp "$language_dir/.language.json.tmp.XXXXXX")"
    printf '{"language":"%s"}\n' "$LANGUAGE" >"$language_tmp"
    chmod 0600 "$language_tmp"
    if [ "$(id -u)" -eq 0 ] && [ "$INSTALL_USER" != "root" ]; then
        chown "$INSTALL_USER" "$language_dir" "$language_tmp"
    fi
    mv -f "$language_tmp" "$LANGUAGE_FILE"
fi

install_step 8 complete
say installed "$VERSION" "$MODE"
say start
