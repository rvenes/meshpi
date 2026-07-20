#!/bin/sh
set -eu

BASE_URL="${MESHPI_BASE_URL:-https://venes.org/meshpi}"
APP_ROOT="${MESHPI_APP_ROOT:-$HOME/Library/Application Support/MeshPi}"
DATA_DIR="${MESHPI_DATA_DIR:-$APP_ROOT/data}"
CONFIG_FILE="${MESHPI_CONFIG_FILE:-$APP_ROOT/meshpi.env}"
BIN_DIR="${MESHPI_BIN_DIR:-$HOME/.local/bin}"
LAUNCH_AGENTS_DIR="${MESHPI_LAUNCH_AGENTS_DIR:-$HOME/Library/LaunchAgents}"
PLIST_FILE="$LAUNCH_AGENTS_DIR/org.venes.meshpi.plist"
SKIP_SERVICE="${MESHPI_SKIP_SERVICE:-0}"

find_python() {
    for candidate in python3.13 python3.12 python3.11 python3; do
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

TMP_DIR="$(mktemp -d)"
trap 'rm -rf "$TMP_DIR"' EXIT INT TERM
MANIFEST="$TMP_DIR/version.json"

if [ -n "${MESHPI_MANIFEST_FILE:-}" ]; then
    cp "$MESHPI_MANIFEST_FILE" "$MANIFEST"
else
    curl -fsSL "$BASE_URL/version.json" -o "$MANIFEST"
fi

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
WHEEL="$TMP_DIR/meshpi-$VERSION-py3-none-any.whl"

case "$EXPECTED_SHA256" in
    *[!0-9a-fA-F]* | "") echo "Ugyldig SHA-256 i version.json" >&2; exit 1 ;;
esac
[ "${#EXPECTED_SHA256}" -eq 64 ] || {
    echo "Ugyldig SHA-256-lengd i version.json" >&2
    exit 1
}

curl -fsSL "$PACKAGE_URL" -o "$WHEEL"
ACTUAL_SHA256="$(shasum -a 256 "$WHEEL" | awk '{print $1}')"
[ "$ACTUAL_SHA256" = "$EXPECTED_SHA256" ] || {
    echo "SHA-256 stemmer ikkje. Installasjonen er avbroten." >&2
    exit 1
}

mkdir -p "$APP_ROOT" "$DATA_DIR" "$BIN_DIR" "$LAUNCH_AGENTS_DIR"
"$PYTHON" -m venv "$APP_ROOT/venv"
"$APP_ROOT/venv/bin/python" -m pip install -q --upgrade pip
"$APP_ROOT/venv/bin/python" -m pip install -q --upgrade --force-reinstall "$WHEEL"

if [ ! -f "$CONFIG_FILE" ]; then
    cat >"$CONFIG_FILE" <<EOF
MESHTASTIC_HOST=10.0.0.152
MESHTASTIC_PORT=4403
DATABASE_PATH=$DATA_DIR/meshtastic.db
CONNECTIONS_PATH=$DATA_DIR/connections.json
DISCOVERY_SUBNET=10.0.0.0/24
IPC_HOST=127.0.0.1
IPC_PORT=8765
LOG_LEVEL=INFO
UPDATE_URL=$BASE_URL/version.json
UPDATE_TIMEOUT=3
EOF
    chmod 0600 "$CONFIG_FILE"
fi

rm -f "$BIN_DIR/meshpi"
cat >"$BIN_DIR/meshpi" <<EOF
#!/bin/sh
exec "$APP_ROOT/venv/bin/meshpi" --env-file "$CONFIG_FILE" "\$@"
EOF
chmod 0755 "$BIN_DIR/meshpi"

PROFILE="$HOME/.zprofile"
PATH_LINE='export PATH="$HOME/.local/bin:$PATH"'
if [ "${MESHPI_SKIP_PATH:-0}" != "1" ] &&
    ! grep -F "$PATH_LINE" "$PROFILE" >/dev/null 2>&1
then
    printf '\n%s\n' "$PATH_LINE" >>"$PROFILE"
fi

if [ "$SKIP_SERVICE" != "1" ]; then
    cat >"$PLIST_FILE" <<EOF
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>Label</key>
    <string>org.venes.meshpi</string>
    <key>ProgramArguments</key>
    <array>
        <string>$APP_ROOT/venv/bin/meshpi</string>
        <string>--env-file</string>
        <string>$CONFIG_FILE</string>
        <string>daemon</string>
    </array>
    <key>WorkingDirectory</key>
    <string>$DATA_DIR</string>
    <key>RunAtLoad</key>
    <true/>
    <key>KeepAlive</key>
    <true/>
    <key>StandardOutPath</key>
    <string>$DATA_DIR/meshpi.log</string>
    <key>StandardErrorPath</key>
    <string>$DATA_DIR/meshpi-error.log</string>
</dict>
</plist>
EOF
    plutil -lint "$PLIST_FILE" >/dev/null
    launchctl bootout "gui/$(id -u)/org.venes.meshpi" >/dev/null 2>&1 || true
    launchctl bootstrap "gui/$(id -u)" "$PLIST_FILE"
fi

echo "MeshPi $VERSION er installert."
echo "Opne ein ny terminal og start med: meshpi"
