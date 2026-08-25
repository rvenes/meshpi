# MeshPi

[Nynorsk](README.md) | **English**

MeshPi is a small, stable Meshtastic client for the terminal. It keeps one
connection to a Meshtastic node open in the background, stores messages in
SQLite, and provides full-screen and conventional command-line interfaces in
Nynorsk and English, including over SSH.

> [!WARNING]
> MeshPi is still in an early stage of development. Features, user interfaces,
> configuration, and storage formats may change. Test it carefully before use
> in critical or production-like environments.

See [DEVELOPMENT.md](DEVELOPMENT.md) for development history, architecture
decisions, and the prioritized roadmap. The first release has no web
interface, but the core and local IPC protocol are separated from the CLI so
that another interface can be added later.

## Features

- receives and sends text on every enabled Meshtastic channel
- receives and sends direct messages over an explicit channel route
- stores conversation history and unread state in SQLite
- displays known nodes and available node information
- logs received telemetry and GPS positions per source node
- combines node details, telemetry, positions, and traceroute history
- switches between saved, discovered, or manually entered TCP, USB/serial,
  and experimental BLE profiles
- reports RF, MQTT, or Unknown without guessing
- shows RSSI, SNR, and hop information when available
- distinguishes ordinary/implicit ACK from an end-to-end ACK by the DM target
- reconnects automatically after a connection loss or system suspend
- provides a full-screen TUI, regular commands, and line-oriented chat
- downloads and installs fully signature- and hash-verified updates without
  executing commands from the manifest
- runs continuously as a systemd service, LaunchAgent, or per-user Windows
  autostart process

MeshPi never changes the configuration of the Meshtastic node and never sends
messages automatically.

## Architecture

`meshpi daemon` is the background service and is the only process that owns the
Meshtastic connection and SQLite file. CLI and TUI clients use a private Unix
socket on Linux and macOS. Windows uses an exclusively reserved TCP socket on
`127.0.0.1:8765`; it cannot bind to an external address. Both transports check
the IPC token.

This lets MeshPi receive messages while nobody is logged in through SSH and
ensures that only one process uses the radio's TCP connection.

## Requirements

- Linux with systemd, macOS, or Windows 10/11
- Python 3.11 or newer
- a Meshtastic node available through TCP, USB/serial, or BLE

A new installation has no preselected node. The first `meshpi` run opens the
connection picker with discovered TCP, USB, and BLE devices. You may also enter
an IP address, hostname, COM port, serial path, or `ble://IDENTIFIER` manually.

## Language

MeshPi includes complete Nynorsk (`nn`) and English (`en`) interfaces.

```bash
meshpi language       # show the current language
meshpi language nn    # use Nynorsk
meshpi language en    # use English
```

Press `F10` in the full-screen interface and choose **Nynorsk** or **English**.
Visible TUI text changes immediately without restarting the application or
daemon. The saved choice is reused on the next run.

An existing installation that has no saved language choice continues to use
Nynorsk. On a genuinely fresh installation, system/browser languages `nn`,
`nb`, and `no` select Nynorsk; every other language selects English. English is
the runtime fallback if an English translation is unexpectedly missing.

The installers follow the same detection rule. Override it explicitly with
`--language=nn|en` on Linux and macOS, or `-Language nn|en` in PowerShell:

```bash
sh install-linux.sh --language=en
sh install-macos.sh --language=nn
sh uninstall-linux.sh --language=en
sh uninstall-macos.sh --language=nn
```

```powershell
.\install-windows.ps1 -Language en
.\uninstall-windows.ps1 -Language nn
```

`MESHPI_LANGUAGE=nn|en` also selects the language for the app and scripts. A
fresh installer stores its selected language in the private per-user
`language.json`. An upgrade or reinstall with existing MeshPi configuration or
data does not create this file, preserving the Nynorsk legacy default.

## Installation

The default mode is `always`: the daemon starts automatically and receives
messages even while the TUI is closed.

### Linux, including Raspberry Pi OS

On a minimal Debian/Ubuntu installation, install `curl` and the normal `venv`
package first:

```bash
sudo apt update
sudo apt install curl python3-venv
curl -fLO https://venes.org/meshpi/install-linux.sh
sudo sh install-linux.sh
```

The installer never installs system packages automatically. If, for example,
Python 3.14 lacks `venv`, it prints
`sudo apt install python3.14-venv` before downloading MeshPi files.

### macOS

```bash
curl -fLO https://venes.org/meshpi/install-macos.sh
sh install-macos.sh
```

Never run the macOS installer with `sudo`. It rejects root to prevent an
accidental installation under `/var/root`.

### Windows PowerShell

```powershell
Invoke-WebRequest https://venes.org/meshpi/install-windows.ps1 -OutFile install-windows.ps1
Set-ExecutionPolicy -Scope Process Bypass
.\install-windows.ps1
```

Choose `session` if the daemon should exist only while MeshPi is in use. It
stores every message it actually receives, but cannot guarantee delivery of
messages sent while MeshPi was stopped:

```bash
# Linux
sh install-linux.sh --mode=session

# macOS
sh install-macos.sh --mode=session
```

```powershell
.\install-windows.ps1 -Mode Session
```

The installers download a signed version manifest, a platform lock file, and
the MeshPi wheel over HTTPS. They verify the lock and wheel with signed size
and SHA-256 values. Every Python dependency has an exact version and hash and
is installed with `pip --require-hashes`. Existing configuration and data are
preserved.

### Transparent installation

Piping directly to `bash`, `sudo bash`, or `iex` means trusting exactly what
the server returns at that moment. Download and inspect the script first:

```bash
# Linux
curl -fLO https://venes.org/meshpi/install-linux.sh
less install-linux.sh
sudo sh install-linux.sh

# macOS
curl -fLO https://venes.org/meshpi/install-macos.sh
less install-macos.sh
sh install-macos.sh
```

```powershell
Invoke-WebRequest https://venes.org/meshpi/install-windows.ps1 -OutFile install-windows.ps1
Get-Content .\install-windows.ps1
Set-ExecutionPolicy -Scope Process Bypass
.\install-windows.ps1
```

The manifest is signed with a separate RSA-3072 release key pinned in the
application and installers. Unsigned or changed manifests are rejected before
package download. The signed hashes bind the wheel, installers, and lock files
to the release.

Meshtastic TCP on port 4403 is unencrypted. Use it only on a trusted network or
through a VPN/SSH tunnel. USB/serial does not send traffic over the LAN.

### Updating

```bash
# Linux, always mode
sudo meshpi update

# Linux session mode and macOS
meshpi update
```

```powershell
meshpi update
```

Use `meshpi update --check` to check only. Installation requires typing the
localized confirmation token (`UPDATE` in English or `OPPDATER` in Nynorsk),
or explicit non-interactive confirmation with `--yes`.

Opt in to the separate beta channel only where preview software is acceptable:

```bash
sudo meshpi update --beta  # Linux always mode
meshpi update --beta       # Linux session mode and macOS
```

```powershell
meshpi update --beta
```

`--beta` is available from MeshPi 0.8.6. Older installations must first use
the full beta installer at `https://venes.org/meshpi/beta/`. The choice applies
to one update only. Normal update checks always use stable. A final `0.9.0` is
newer than prereleases such as `0.9.0b1`.

The TUI checks `https://venes.org/meshpi/version.json` at startup and displays
the local command `meshpi update` when appropriate. It never puts the command
in the message input or sends it over Meshtastic. The updater verifies the
manifest, downloads the installer, lock, and wheel to a private temporary
directory, and runs only the locally verified installer. It never executes a
manifest command. Updates are prepared in a new version directory and checked
offline before the daemon is stopped. After an atomic switch, a health check
automatically restores the previous working release on failure.

## Local development

```bash
git clone https://github.com/rvenes/meshpi.git
cd meshpi
python3 -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
pip install -e ".[test,dev]"
cp .env.example .env
pytest
ruff check .
```

Start the daemon in one terminal and the TUI in another:

```bash
source .venv/bin/activate
meshpi daemon
```

```bash
source .venv/bin/activate
meshpi
```

Regular CLI commands such as `meshpi status`, `meshpi nodes`, and
`meshpi conversations` use the same daemon.

## Selecting a Meshtastic node

Without arguments, MeshPi opens the last-used connection:

```bash
meshpi
```

You may switch directly to TCP, serial, or BLE. The profile is saved and
selected before the TUI opens:

```bash
meshpi 192.0.2.42
meshpi meshtastic.local
meshpi 192.0.2.42:4403
meshpi /dev/serial/by-id/usb-Seeed_Studio_XIAO-BOOT_...-if00
meshpi COM3
meshpi connect 192.0.2.42
meshpi connect /dev/ttyACM0 --name "USB node"
meshpi connect "ble://A1:B2:C3:D4:E5:F6" --name "BLE node"
```

MeshPi stores a USB identity when the device supplies a serial number. On
macOS and Windows it follows a changed serial/COM path only when that identity
has exactly one match. Otherwise, select the port again. Prefer a stable
`/dev/serial/by-id` path on Linux.

Open the interactive connection picker with:

```bash
meshpi new
```

Saved profiles appear immediately. Discovered USB and local Meshtastic TCP
devices appear first while the BLE row shows **Searching …**. BLE results
normally arrive after about ten seconds. Press `F5` to rescan, type to filter
or enter a manual target, use `↑`/`↓`, and press Enter to switch. Missing saved
serial or BLE profiles are marked **NOT CONNECTED** and placed last. Linux
onboard `/dev/ttyS*` ports are hidden by default; press `F4` to show them.

```bash
meshpi connections
```

The daemon still owns only one radio connection. Profile switching closes the
old connection cleanly and opens the new one without restarting systemd. One
database holds all history. Channels merge only when a trusted Meshtastic
channel ID proves they are the same logical channel on the same local node.
Nodes, conversations, unread state, telemetry, positions, and node actions are
scoped per local node, including LongFast. The same radio over TCP, USB, or BLE
uses the same dataset. A profile remembers its last local node and shows that
history while reconnecting.

### Database export

Export the entire database before testing a beta that may migrate it:

```bash
meshpi export
meshpi export meshpi-data.jsonl
```

Without a filename, MeshPi creates a timestamped file in the current
directory. The UTF-8 JSON Lines export begins with format/application/database
schema versions, writes one data row per line, and ends with verified row
counts. It covers messages, nodes, channels, telemetry, positions, node
actions, archive state, and preserved unscoped legacy records. It is one
consistent SQLite snapshot while the daemon runs. Existing files require
`--force`; interrupted output is not left as a completed export.

Legacy rows that cannot safely be assigned to one local node remain
read-oriented and are never made sendable automatically. Export is intended
for preservation and inspection, not as a promised import format. It may
contain private messages and exact positions; protect it accordingly.

### Routing and BLE

MeshPi sends only through channels confirmed by the active node's channel
list. Historical, unknown, or provisional routes remain readable but not
sendable and never silently fall back to channel 0. A message received before
the channel list is ready is stored provisionally and rebound when the actual
channel arrives. `Ctrl+D` asks which local channel a new DM route should use;
the index may differ at the remote node. Radio rejection reasons such as
`NO_CHANNEL` are shown instead of a generic failure.

BLE support remains experimental. `meshpi new` starts an explicit background
scan that normally lasts about ten seconds. Closing the picker discards a late
result. The saved platform identifier is opaque; on macOS it is a
machine-specific CoreBluetooth UUID, not a MAC address. The operating system
handles pairing and permissions. Use the displayed PIN or `123456` for nodes
configured with that default. Close other Meshtastic clients first because
many nodes allow only one BLE client. MeshPi stores no PIN and changes no
Bluetooth or radio configuration. BLE under Linux/macOS system services and
Docker has not yet been fully platform-tested.

## Full-screen interface

`meshpi` or `meshpi tui` opens the conversation list, active conversation,
node details, and scrollable node list for the selected local node. New
messages arrive automatically and the active conversation scrolls to the
newest message. A DM in another conversation raises a visible notification.

Select a node in the right panel to inspect it and press Enter to open its DM.
**New DM** shows the full node list with name/node-ID filtering. Right-click a
node or press `Shift+F10` for node actions. The conversation list shows one DM
entry per peer even when older data has several channel routes. Hidden legacy
public routes remain in the database.

Node actions include traceroute and **Node info and logs**, which combines the
overview, telemetry, positions, and traceroute history. Position rows include
a Google Maps link and full copyable URL. **Exchange position data** sends the
latest known local position only when channel sharing and configured precision
allow it; otherwise only the request is sent. The target is asked to reply in
both cases. No node action is sent automatically.

Traceroute output includes forward/return paths and per-hop SNR when reported.
Attempts and results are stored and shown in both the DM and node log. Firmware
allows one traceroute every 30 seconds; MeshPi disables the action and displays
a countdown. Older messages show a date such as `21.07.26`; today's messages
show only the time.

When message input has focus, `↑`/`↓` recalls messages you sent in that exact
public or DM conversation and restores the draft after the newest entry. A
validated submission is remembered even when sending later fails. Select chat
text with the mouse; releasing copies it automatically through OSC52 where
supported. `Ctrl+C` repeats the copy. Other terminals may require their native
selection, often with `Shift` while dragging.

Press `F10` to open settings and switch between Nynorsk and English. The
current conversation, node selection, and draft are retained while visible
labels, dialogs, bindings, and notifications update immediately.

```text
F1                 show or close this overview
Tab / Shift+Tab    move between conversations, messages, and nodes
Enter              open the selected conversation/node or send a message
↑ / ↓              navigate the list; in the message field: sent messages/drafts
Mouse / Ctrl+C     select and copy text from the conversation window
Ctrl+L             move focus to the message field
Ctrl+D             find a node and start a new DM
F2                 move focus to the conversation list
F3                 move focus to the node list
F8                 show or hide DM conversations
F9                 show or hide secondary channels; the primary remains visible
F10                choose language and other app settings
Shift+F10          open actions for the selected node
Delete             close the selected DM without deleting its history
Ctrl+R             refresh status, conversations, and nodes
Ctrl+U             copy the update command when a new version is available
Ctrl+Q             quit MeshPi and choose what happens to the daemon
Esc                close the open dialog
```

The layout adapts to terminal width and hides node details first in a narrow
window.

A DM progresses from `[sent]` to `[ACK]` after an ordinary or implicit
Meshtastic ACK. That confirms forwarding, not delivery. Only a routing ACK
from the selected peer produces `[delivered]`; a NAK produces `[failed]`.
**transport unknown** means metadata cannot prove RF versus MQTT. MeshPi does
not guess. Closing a DM hides it without deleting messages; opening the node,
sending again, or receiving a new DM restores it.

The node picker filters as you type and accepts `↑`/`↓` plus Enter. It excludes
the local node as a recipient. Enter a full node ID when the recipient is not
listed.

## Commands

| Command | Purpose |
|---|---|
| `meshpi` or `meshpi tui` | Start the full-screen interface. |
| `meshpi new` | Discover, select, or add a connection; F5 rescans. |
| `meshpi connect TARGET [--name NAME]` | Switch TCP, serial, or BLE connection. |
| `meshpi connections` | List saved connection profiles. |
| `meshpi daemon` | Run the daemon in the foreground for debugging/service setup. |
| `meshpi doctor [--offline]` | Run self-tests; offline needs no available node. |
| `meshpi export [FILE] [--force]` | Export the database as UTF-8 JSON Lines. |
| `meshpi service {status,start,stop,enable,disable}` | Control service/autostart. |
| `meshpi update [--check] [--yes] [--beta]` | Check/install a signed update. |
| `meshpi language [nn\|en]` | Show or save the interface language. |
| `meshpi status` | Show connection status. |
| `meshpi nodes [--search TEXT] [--sort name\|seen\|id]` | List/filter/sort nodes. |
| `meshpi node NODE-ID` | Show stored details for one node. |
| `meshpi conversations` | List conversations and unread counts. |
| `meshpi channels` | List confirmed channel names, indices, and IDs. |
| `meshpi delete-messages {public,dm,all} [--yes]` | Delete selected message history. |
| `meshpi public [--channel INDEX\|CONVERSATION-ID] [--limit NUMBER]` | Show public history. |
| `meshpi dm NODE-ID [--channel INDEX] [--limit NUMBER]` | Show DM history. |
| `meshpi send-public TEXT [--channel INDEX\|CONVERSATION-ID]` | Send publicly. |
| `meshpi send-dm NODE-ID TEXT [--channel INDEX]` | Send a DM. |
| `meshpi watch [all\|public\|CONVERSATION-ID\|NODE-ID]` | Follow messages live. |
| `meshpi chat {public\|CONVERSATION-ID\|NODE-ID} [--limit NUMBER]` | Line chat. |

Global options precede the command:

| Option | Purpose |
|---|---|
| `-h`, `--help` | Show main-command or subcommand help. |
| `--version` | Show the installed version. |
| `--env-file FILE` | Use an environment file other than `.env`. |
| `--json` | Produce machine-readable JSON for non-interactive commands. |

For example, `meshpi nodes --help` shows node-list options and
`meshpi --json nodes --sort name` writes the sorted list as JSON.

### Start and connections

```bash
meshpi
meshpi tui
meshpi new
meshpi connect 192.0.2.42 --name "Home node"
meshpi connect COM4 --name "USB node"
meshpi connect "ble://A1:B2:C3:D4:E5:F6" --name "BLE node"
meshpi connections
```

Direct forms such as `meshpi 192.0.2.42`, `meshpi COM4`, and
`meshpi ble://A1:B2:C3:D4:E5:F6` are shortcuts for
`meshpi connect TARGET`.

### Status and nodes

```bash
meshpi status
meshpi nodes
meshpi nodes --search mountain --sort name
meshpi node deadbeef
```

An asterisk marks the local Meshtastic node. After a profile switch, the list
contains only nodes observed through the local radio associated with that
profile.

### History

```bash
meshpi conversations
meshpi channels
meshpi public
meshpi public --channel 2
meshpi public --limit 200
meshpi dm deadbeef
```

Delete all public channels, all DMs, or both:

```bash
meshpi delete-messages public
meshpi delete-messages dm
meshpi delete-messages all
```

The command requires the localized confirmation token (`DELETE` in English or
`SLETT` in Nynorsk). `--yes` is explicit non-interactive confirmation, for
example `meshpi delete-messages all --yes`. This command does not delete the
traceroute log or node list.

### Sending

Nothing is sent until an explicit send command or Enter in interactive chat:

```bash
meshpi send-public "Test on the primary channel"
meshpi send-public "Test on channel 2" --channel 2
meshpi send-dm deadbeef "Direct test message" --channel 2
```

Text must be valid UTF-8 and at most 237 bytes. A DM node ID is eight
hexadecimal characters, with or without `!`.

### Live and interactive chat

```bash
meshpi watch
meshpi watch public
meshpi watch channel:!01234567:global:Ops:1234
meshpi watch deadbeef
meshpi chat public
meshpi chat deadbeef
```

`public` follows only the active primary channel; `all` includes all channels
and DMs. Bash history expansion requires quoting an ID with `!`, for example
`meshpi dm '!deadbeef'`.

English line-chat commands are:

```text
/status   show connection status
/nodes    show the node list
/help     show chat commands
/quit     exit
```

The Nynorsk aliases `/nodar`, `/hjelp`, and `/slutt` remain accepted.

### JSON for scripts

Place the global option before the command:

```bash
meshpi --json status
meshpi --json nodes
meshpi --json public
meshpi --json watch public
```

JSON reads do not mark messages as read. Machine values and wire fields stay
stable regardless of the interface language.

### Service and self-test

```bash
meshpi doctor
meshpi doctor --offline
meshpi service status
meshpi service start
meshpi service stop
meshpi service enable
meshpi service disable
```

`meshpi daemon` runs in the foreground for debugging or service integration.
Normal use should go through the installed background service or session mode.

## Configuration

MeshPi reads `.env` from the working directory when present. Existing
environment variables take precedence.

```dotenv
MESHTASTIC_HOST=
MESHTASTIC_PORT=4403
DATABASE_PATH=./data/meshtastic.db
CONNECTIONS_PATH=./data/connections.json
DISCOVERY_SUBNET=
IPC_TRANSPORT=auto
IPC_HOST=127.0.0.1
IPC_PORT=8765
IPC_SOCKET_PATH=./data/meshpi.sock
IPC_SOCKET_GID=
IPC_TOKEN=replace-with-64-random-hex-characters
LOG_LEVEL=INFO
OBSERVATION_RETENTION_DAYS=365
UPDATE_URL=https://venes.org/meshpi/version.json
UPDATE_TIMEOUT=3
BACKGROUND_MODE=always
```

`IPC_TRANSPORT=auto` selects a Unix socket on Linux/macOS and loopback TCP on
Windows. Explicit TCP is available everywhere, but `IPC_HOST` then accepts
only `127.0.0.1`, `::1`, or `localhost`. Installers choose a private
platform-appropriate `IPC_SOCKET_PATH`. `IPC_SOCKET_GID` is optional POSIX
group access and should be set only by the installer or an administrator.
`IPC_TOKEN` must contain at least 32 characters and is checked before every IPC
command.

`OBSERVATION_RETENTION_DAYS` retains received telemetry and positions for 1 to
3650 days; the default is 365. Data is associated with both its source and the
local Meshtastic node that received it. MeshPi also records profile/transport
provenance. It deduplicates observations across profiles for the same local
node, never across different local nodes.

When `DISCOVERY_SUBNET` is empty, MeshPi discovers the local IPv4 network and
scans it. Set, for example, `DISCOVERY_SUBNET=192.168.1.0/24` to constrain the
search; the largest accepted network is `/22`. Serial discovery uses the OS
port list and prefers `/dev/serial/by-id`. BLE discovery happens only in the
connection picker and filters for the Meshtastic BLE service. Give otherwise
identical devices without stable IDs clear profile names and verify the port.

An empty `UPDATE_URL` disables startup update checks. Network errors during a
check are ignored and never prevent startup.

`BACKGROUND_MODE=always` keeps the daemon independent of the TUI. If you stop
the service while quitting, the next `meshpi` starts it again.
`BACKGROUND_MODE=session` starts on demand and lets `Ctrl+Q` stop it on exit.

The saved interface language is per user and separate from `.env` and the
SQLite database. `MESHPI_LANGUAGE=nn|en` temporarily overrides the saved
choice. `MESHPI_LANGUAGE_FILE` is intended for controlled testing or custom
packaging; normal users should use `meshpi language` or `F10`.

## Operations, files, and uninstallation

Common commands:

```text
meshpi service status
meshpi service start
meshpi service stop
meshpi doctor --offline
```

On Linux, enabling/disabling or starting a stopped system service may require
`sudo`. `meshpi service stop` shuts the daemon down cleanly and unloads the
platform service when necessary so it cannot restart immediately.

### Linux / Raspberry Pi OS

- releases: `/opt/meshpi/releases/`
- active and previous: `/opt/meshpi/current`, `/opt/meshpi/previous`
- configuration: `/etc/meshpi.env` (installer user:`meshpi`, `0640`)
- database and profiles: `/var/lib/meshpi` (`meshpi:meshpi`, `0750`)
- IPC socket: `/run/meshpi/meshpi.sock`, private with installer-user access
- language: `~/.config/MeshPi/language.json`
- log: `journalctl -u meshpi -f`
- service: `sudo systemctl status|start|stop|restart meshpi`

`always` mode uses the installer user's private primary group for socket
access without requiring a new login. A group shared by several accounts is
rejected. On systems with a shared `users` or `staff` group, use session mode
or ask the administrator for a private primary group.

```bash
curl -fLO https://venes.org/meshpi/uninstall-linux.sh
sudo sh uninstall-linux.sh
```

Add `--purge-data` to delete configuration, database, profiles, and the saved
language. For a session installation also use `--mode=session`.

### macOS

- application, configuration, data, logs, and language:
  `~/Library/Application Support/MeshPi/`
- autostart: `~/Library/LaunchAgents/org.venes.meshpi.plist`
- logs: `meshpi.log` and `meshpi-error.log` in the data directory

```bash
curl -fLO https://venes.org/meshpi/uninstall-macos.sh
sh uninstall-macos.sh
```

Add `--purge-data` to delete personal data. The uninstaller removes the PATH
line only when the MeshPi installer added it.

### Windows

- releases: `%LOCALAPPDATA%\MeshPi\releases`
- database and logs: `%LOCALAPPDATA%\MeshPi\data`
- language: `%LOCALAPPDATA%\MeshPi\language.json`
- configuration: `%APPDATA%\MeshPi\meshpi.env`
- autostart: shortcut in the user's Startup folder
- supervisor: `meshpi-supervisor.ps1`, which restarts the daemon after a crash

```powershell
Invoke-WebRequest https://venes.org/meshpi/uninstall-windows.ps1 -OutFile uninstall-windows.ps1
Set-ExecutionPolicy -Scope Process Bypass
.\uninstall-windows.ps1
```

Use `-PurgeData` only when configuration, database, profiles, logs, and saved
language should also be deleted. The scripts are idempotent.

## Docker (optional)

Systemd is recommended on Raspberry Pi. The Docker variant exposes no host
port:

```bash
cp .env.example .env
python -c "import secrets; print(secrets.token_hex(32))"
# Put the result in .env as IPC_TOKEN.
docker compose up -d --build
docker compose exec meshpi meshpi status
docker compose exec meshpi meshpi chat public
```

## Tests

All automated tests mock the Meshtastic connection and send nothing by radio:

```bash
pytest
pytest --cov=meshpi --cov-report=term-missing
ruff check .
```

Tests cover multiple channels and gateways, DM routes, node IDs, RF/MQTT,
deduplication, SQLite migration, sending, ACK/NAK, reconnect, language-key
parity, immediate language switching, and input validation.

## Release keys

The manifest declares a `key_id`. The application and three installers contain
an allow-listed key registry and a revocation list. A new public key must first
ship in a transition release signed by an already trusted key. Later manifests
may use the new key only after that release is available. A compromised key is
added to the revocation list in the next safely distributed release. See
[SECURITY.en.md](SECURITY.en.md) for the full procedure and limitations.

## Safe live testing

Use this order:

1. Inspect `meshpi status` and `meshpi nodes` without sending.
2. Test passive reception without an agent sending on a public channel.
3. If the user explicitly requests a real transmission, confirm the complete
   recipient ID and send only one clearly labeled DM to that agreed node.
4. Inspect history, transport metadata, and any ACK/NAK.

An agent-controlled live test must never send on a public channel. Public
messages may be forwarded to MQTT and other parts of the mesh. A DM requires
fresh, explicit approval for that test.

Do not use Meshtastic configuration commands through the same TCP node while
MeshPi is running.

## License

MeshPi is free software distributed under the GNU General Public License,
version 3 (`GPL-3.0-only`). See [LICENSE](LICENSE) for the complete terms.
