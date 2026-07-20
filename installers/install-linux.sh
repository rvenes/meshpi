#!/bin/sh
set -eu

BASE_URL="${MESHPI_BASE_URL:-https://venes.org/meshpi}"
PREFIX="${MESHPI_PREFIX:-/opt/meshpi}"
STATE_DIR="${MESHPI_STATE_DIR:-/var/lib/meshpi}"
CONFIG_FILE="${MESHPI_CONFIG_FILE:-/etc/meshpi.env}"
BIN_FILE="${MESHPI_BIN_FILE:-/usr/local/bin/meshpi}"
UNIT_FILE="${MESHPI_UNIT_FILE:-/etc/systemd/system/meshpi.service}"
SKIP_SERVICE="${MESHPI_SKIP_SERVICE:-0}"
TEST_MODE="${MESHPI_TEST_MODE:-0}"

if [ "$(id -u)" -ne 0 ] && [ "$TEST_MODE" != "1" ]; then
    echo "Køyr installasjonen som root: curl -fsSL $BASE_URL/install-linux.sh | sudo bash" >&2
    exit 1
fi

for command in curl sha256sum; do
    if ! command -v "$command" >/dev/null 2>&1; then
        echo "Manglar kommandoen «$command»." >&2
        exit 1
    fi
done

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
    echo "På Debian/Raspberry Pi OS: sudo apt install python3 python3-venv" >&2
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
ACTUAL_SHA256="$(sha256sum "$WHEEL" | awk '{print $1}')"
[ "$ACTUAL_SHA256" = "$EXPECTED_SHA256" ] || {
    echo "SHA-256 stemmer ikkje. Installasjonen er avbroten." >&2
    exit 1
}

install -d -m 0755 "$PREFIX"
"$PYTHON" -m venv "$PREFIX/.venv"
"$PREFIX/.venv/bin/python" -m pip install -q --upgrade pip
"$PREFIX/.venv/bin/python" -m pip install -q --upgrade --force-reinstall "$WHEEL"

install -d -m 0755 "$(dirname "$CONFIG_FILE")" "$STATE_DIR" "$(dirname "$BIN_FILE")"
if [ ! -f "$CONFIG_FILE" ]; then
    cat >"$CONFIG_FILE" <<EOF
MESHTASTIC_HOST=10.0.0.152
MESHTASTIC_PORT=4403
DATABASE_PATH=$STATE_DIR/meshtastic.db
CONNECTIONS_PATH=$STATE_DIR/connections.json
DISCOVERY_SUBNET=10.0.0.0/24
IPC_HOST=127.0.0.1
IPC_PORT=8765
LOG_LEVEL=INFO
UPDATE_URL=$BASE_URL/version.json
UPDATE_TIMEOUT=3
EOF
fi
chmod 0644 "$CONFIG_FILE"

rm -f "$BIN_FILE"
cat >"$BIN_FILE" <<EOF
#!/bin/sh
exec "$PREFIX/.venv/bin/meshpi" --env-file "$CONFIG_FILE" "\$@"
EOF
chmod 0755 "$BIN_FILE"

if [ "$SKIP_SERVICE" != "1" ]; then
    command -v systemctl >/dev/null 2>&1 || {
        echo "Denne installasjonen krev systemd." >&2
        exit 1
    }
    getent group dialout >/dev/null 2>&1 || groupadd --system dialout
    if ! id meshpi >/dev/null 2>&1; then
        useradd --system --home-dir "$STATE_DIR" --shell /usr/sbin/nologin --user-group meshpi
    fi
    usermod -aG dialout meshpi
    chown -R meshpi:meshpi "$STATE_DIR"

    cat >"$UNIT_FILE" <<EOF
[Unit]
Description=MeshPi Meshtastic CLI-teneste
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
User=meshpi
Group=meshpi
SupplementaryGroups=dialout
WorkingDirectory=$STATE_DIR
EnvironmentFile=$CONFIG_FILE
Environment=PYTHONDONTWRITEBYTECODE=1
ExecStart=$PREFIX/.venv/bin/meshpi --env-file $CONFIG_FILE daemon
Restart=on-failure
RestartSec=5
NoNewPrivileges=true
PrivateTmp=true
ProtectSystem=strict
ProtectHome=true
ReadWritePaths=$STATE_DIR

[Install]
WantedBy=multi-user.target
EOF
    chmod 0644 "$UNIT_FILE"
    systemctl daemon-reload
    systemctl enable meshpi.service >/dev/null
    systemctl restart meshpi.service
fi

echo "MeshPi $VERSION er installert."
echo "Start terminalgrensesnittet med: meshpi"
