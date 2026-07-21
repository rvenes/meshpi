#!/bin/sh
set -eu

BASE_URL="${MESHPI_BASE_URL:-https://venes.org/meshpi}"
MODE="${MESHPI_MODE:-always}"
IPC_PORT_VALUE="${MESHPI_IPC_PORT:-8765}"
SKIP_SERVICE="${MESHPI_SKIP_SERVICE:-0}"

for argument in "$@"; do
    case "$argument" in
        --mode=always) MODE=always ;;
        --mode=session | --no-service) MODE=session ;;
        *) echo "Ukjent argument: $argument" >&2; exit 2 ;;
    esac
done
[ "$MODE" = "always" ] || [ "$MODE" = "session" ] || {
    echo "Modus må vere «always» eller «session»." >&2
    exit 2
}

for command in curl shasum; do
    command -v "$command" >/dev/null 2>&1 || {
        echo "Manglar kommandoen «$command»." >&2
        exit 1
    }
done

find_python() {
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
    echo "MeshPi krev Python 3.11 eller nyare." >&2
    echo "Installer med Homebrew: brew install python@3.11" >&2
    exit 1
fi

APP_ROOT="${MESHPI_APP_ROOT:-$HOME/Library/Application Support/MeshPi}"
DATA_DIR="${MESHPI_DATA_DIR:-$APP_ROOT/data}"
CONFIG_FILE="${MESHPI_CONFIG_FILE:-$APP_ROOT/meshpi.env}"
BIN_DIR="${MESHPI_BIN_DIR:-$HOME/.local/bin}"
LAUNCH_AGENTS_DIR="${MESHPI_LAUNCH_AGENTS_DIR:-$HOME/Library/LaunchAgents}"
PLIST_FILE="$LAUNCH_AGENTS_DIR/org.venes.meshpi.plist"
RELEASES_DIR="$APP_ROOT/releases"
CURRENT_LINK="$APP_ROOT/current"
PREVIOUS_LINK="$APP_ROOT/previous"

TMP_DIR="$(mktemp -d)"
trap 'rm -rf "$TMP_DIR"' EXIT INT TERM
MANIFEST="$TMP_DIR/version.json"
if [ -n "${MESHPI_MANIFEST_FILE:-}" ]; then
    cp "$MESHPI_MANIFEST_FILE" "$MANIFEST"
else
    curl -fsSL "$BASE_URL/version.json" -o "$MANIFEST"
fi

verify_manifest_signature() {
    "$PYTHON" - "$MANIFEST" <<'PY'
import base64, hashlib, hmac, json, sys
modulus = int("c1370fa9e2eb0d22e354c58594e369f9db44156f834522bf69a8da523a30ac0d4539e08a30d76e854b40ae693da388af11ca62ee24c1e6f43ec128be550e8b7655d86955ae858b9f30237ba02e2773e9ad2fcfe1644484e909a8805a6c8a289dda69cedbc973d7427278442d8acb1d00a0c5cd242c34404843ea684ece7ad40a59d902633624ae36ae3f4e8c9e401bb887ef650f1fe001f9fd7661841b98a95f67aea496c05054a4c41c287c09d1dd1e94e9c01cc997162a50e02df6d28645d268cceb35daf7ad1e4202b2b1714a71e2b18d0564f12a468c2bb4d7e678a1c4c493de0c945f0f2665efb658238dd4dd617b73acd8e20e4c5f440d2d4ee13617f2c2857c0457e0a3a73aac43d0e23f5c0f56f9042a6d1e6221383481a9bcc952576904895e013a5f12b6c0aa08b9ba911df7be42a4d0a3c31ca98111b4344d8079fdb55a43379fde9968edf9ce7b3554333d5819ad196935e928012d1b20b4aed5ee48d8851dd69458b15998712530b4d91228b06ae109741c0cf4ab723f092e49", 16)
with open(sys.argv[1], encoding="utf-8") as handle:
    manifest = json.load(handle)
signature = manifest.pop("signature", None)
if not isinstance(signature, dict) or signature.get("algorithm") != "rsa-pkcs1v15-sha256" or signature.get("key_id") != "meshpi-release-2026-01":
    raise SystemExit("Versjonsmanifestet manglar ein gyldig signatur")
try:
    raw = base64.b64decode(signature["value"], validate=True)
except (KeyError, ValueError) as exc:
    raise SystemExit("Ugyldig manifestsignatur") from exc
canonical = json.dumps(manifest, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()
size = (modulus.bit_length() + 7) // 8
actual = pow(int.from_bytes(raw, "big"), 65537, modulus).to_bytes(size, "big")
digest_info = bytes.fromhex("3031300d060960864801650304020105000420")
digest = hashlib.sha256(canonical).digest()
pad = size - len(digest_info) - len(digest) - 3
expected = b"\x00\x01" + b"\xff" * pad + b"\x00" + digest_info + digest
if len(raw) != size or pad < 8 or not hmac.compare_digest(actual, expected):
    raise SystemExit("Signaturen på versjonsmanifestet stemmer ikkje")
PY
}
verify_manifest_signature

manifest_value() {
    "$PYTHON" - "$MANIFEST" "$1" <<'PY'
import json
import sys

with open(sys.argv[1], encoding="utf-8") as handle:
    data = json.load(handle)
value = data
for part in sys.argv[2].split("."):
    value = value[part]
if not isinstance(value, str) or not value:
    raise SystemExit("Ugyldig version.json")
print(value)
PY
}

VERSION="$(manifest_value latest_version)"
PACKAGE_URL="$(manifest_value package.url)"
EXPECTED_SHA256="$(manifest_value package.sha256)"
LOCK_URL="$(manifest_value locks.macos.url)"
EXPECTED_LOCK_SHA256="$(manifest_value locks.macos.sha256)"
IPC_TOKEN="$("$PYTHON" -c 'import secrets; print(secrets.token_hex(32))')"
case "$VERSION" in
    *[!0-9.]* | *.*.*.* | .* | *.) echo "Ugyldig versjon i manifestet" >&2; exit 1 ;;
esac
case "$EXPECTED_SHA256" in
    *[!0-9a-fA-F]* | "") echo "Ugyldig SHA-256 i version.json" >&2; exit 1 ;;
esac
[ "${#EXPECTED_SHA256}" -eq 64 ] || {
    echo "Ugyldig SHA-256-lengd i version.json" >&2
    exit 1
}
case "$EXPECTED_LOCK_SHA256" in
    *[!0-9a-fA-F]* | "") echo "Ugyldig låsefil-hash i version.json" >&2; exit 1 ;;
esac
[ "${#EXPECTED_LOCK_SHA256}" -eq 64 ] || {
    echo "Ugyldig låsefil-hash i version.json" >&2
    exit 1
}

WHEEL="$TMP_DIR/meshpi-$VERSION-py3-none-any.whl"
LOCK_FILE="$TMP_DIR/requirements-macos.txt"
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
ACTUAL_SHA256="$(shasum -a 256 "$WHEEL" | awk '{print $1}')"
[ "$ACTUAL_SHA256" = "$EXPECTED_SHA256" ] || {
    echo "SHA-256 stemmer ikkje. Installasjonen er avbroten." >&2
    exit 1
}
ACTUAL_LOCK_SHA256="$(shasum -a 256 "$LOCK_FILE" | awk '{print $1}')"
[ "$ACTUAL_LOCK_SHA256" = "$EXPECTED_LOCK_SHA256" ] || {
    echo "SHA-256 for låsefila stemmer ikkje. Installasjonen er avbroten." >&2
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
IPC_TOKEN=$IPC_TOKEN
LOG_LEVEL=INFO
UPDATE_URL=$BASE_URL/version.json
UPDATE_TIMEOUT=3
BACKGROUND_MODE=$MODE
EOF
else
    if grep -q '^BACKGROUND_MODE=' "$CONFIG_FILE"; then
        sed "s/^BACKGROUND_MODE=.*/BACKGROUND_MODE=$MODE/" "$CONFIG_FILE" >"$TMP_DIR/config"
        cat "$TMP_DIR/config" >"$CONFIG_FILE"
    else
        printf '\nBACKGROUND_MODE=%s\n' "$MODE" >>"$CONFIG_FILE"
    fi
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
    rm -rf "$RELEASE"
    "$PYTHON" -m venv "$RELEASE/venv"
    "$RELEASE/venv/bin/python" -m pip install -q --require-hashes -r "$LOCK_FILE"
    "$RELEASE/venv/bin/python" -m pip install -q --no-deps "$WHEEL"
fi
INSTALLED_VERSION="$("$RELEASE/venv/bin/meshpi" --version)"
[ "$INSTALLED_VERSION" = "MeshPi $VERSION" ] || {
    echo "Pakken rapporterer «$INSTALLED_VERSION», venta MeshPi $VERSION." >&2
    exit 1
}
"$RELEASE/venv/bin/meshpi" --env-file "$CONFIG_FILE" doctor --offline >/dev/null

DOMAIN="gui/$(id -u)"
LABEL="org.venes.meshpi"
launchctl bootout "$DOMAIN/$LABEL" >/dev/null 2>&1 || true
if [ -x "$BIN_DIR/meshpi" ]; then
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
    mv -f "$temporary" "$CURRENT_LINK"
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

if [ "$MODE" = "always" ] && [ "$SKIP_SERVICE" != "1" ]; then
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
    <key>StandardOutPath</key><string>$DATA_DIR/meshpi.log</string>
    <key>StandardErrorPath</key><string>$DATA_DIR/meshpi-error.log</string>
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
            launchctl bootstrap "$DOMAIN" "$PLIST_FILE" >/dev/null 2>&1 || true
            echo "Oppdateringa feila. Førre versjon er sett tilbake." >&2
        fi
        exit 1
    fi
else
    rm -f "$PLIST_FILE"
fi

echo "MeshPi $VERSION er installert i $MODE-modus."
echo "Opne ein ny terminal og start med: meshpi"
