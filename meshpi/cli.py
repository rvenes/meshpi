from __future__ import annotations

import argparse
import json
import os
import socket
import sys
import tempfile
import threading
from contextlib import suppress
from datetime import datetime
from pathlib import Path
from typing import Any

from meshpi import __version__
from meshpi.channels import parse_dm_conversation_id, parse_public_conversation_id
from meshpi.client import CLIError
from meshpi.client import open_export as _open_export
from meshpi.client import open_watch as _open_watch
from meshpi.client import request as _request
from meshpi.config import Settings
from meshpi.daemon import run_daemon
from meshpi.doctor import offline_checks
from meshpi.i18n import (
    LANGUAGE_NAMES,
    get_language,
    initialize_language,
    language_file,
    set_language,
    tr,
)
from meshpi.lifecycle import (
    DaemonHandle,
    daemon_status,
    start_session_daemon,
    stop_daemon,
    wait_for_daemon,
)
from meshpi.models import normalize_node_id, sanitize_terminal_text
from meshpi.platform_service import manage_service
from meshpi.update import apply_update, check_for_update

EXIT_ERROR = 1
COMMANDS = {
    "tui",
    "update",
    "new",
    "connect",
    "connections",
    "daemon",
    "doctor",
    "export",
    "service",
    "status",
    "nodes",
    "node",
    "conversations",
    "channels",
    "delete-messages",
    "public",
    "dm",
    "send-public",
    "send-dm",
    "watch",
    "chat",
    "language",
}


def _configure_console_output() -> None:
    if sys.platform != "win32":
        return
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if callable(reconfigure):
            reconfigure(errors="replace")


def _normalize_argv(argv: list[str]) -> list[str]:
    if argv and not argv[0].startswith("-") and argv[0] not in COMMANDS:
        return ["connect", argv[0], *argv[1:]]
    return argv


def _default_env_file() -> str:
    """Bruk installatøren sin UTF-8-sidepeikar når launcheren har ein."""
    try:
        pointer = Path(sys.argv[0]).resolve().with_name("meshpi.env-path")
        configured = pointer.read_text(encoding="utf-8").strip()
    except (OSError, UnicodeError):
        return ".env"
    return configured or ".env"


def _default_export_path() -> Path:
    timestamp = datetime.now().astimezone().strftime("%Y%m%d-%H%M%S")
    return Path.cwd() / f"meshpi-export-{timestamp}.jsonl"


def _env_file_from_argv(argv: list[str]) -> str:
    for index, value in enumerate(argv):
        if value == "--env-file" and index + 1 < len(argv):
            return argv[index + 1]
        if value.startswith("--env-file="):
            return value.split("=", 1)[1]
    return _default_env_file()


class MeshPiArgumentParser(argparse.ArgumentParser):
    def format_help(self) -> str:
        value = super().format_help()
        replacements = {
            "usage:": tr("cli.argparse.usage"),
            "options:": tr("cli.argparse.options"),
            "positional arguments:": tr("cli.argparse.arguments"),
        }
        for source, translated in replacements.items():
            value = value.replace(source, translated)
        return value

    def error(self, message: str) -> None:
        self.print_usage(sys.stderr)
        self.exit(2, f"{self.prog}: {tr('cli.argparse.error', message=message)}\n")


def _write_database_export(
    settings: Settings,
    destination: Path,
    *,
    force: bool = False,
) -> tuple[Path, int]:
    destination = destination.expanduser().absolute()
    if destination.is_dir():
        raise ValueError(tr("cli.export.path_is_directory", path=destination))
    if (destination.exists() or destination.is_symlink()) and not force:
        raise ValueError(tr("cli.export.exists", path=destination))
    parent = destination.parent
    if not parent.is_dir():
        raise ValueError(tr("cli.export.parent_missing", path=parent))

    try:
        descriptor, temporary_name = tempfile.mkstemp(
            prefix=f".{destination.name}.",
            suffix=".tmp",
            dir=parent,
        )
    except OSError as exc:
        raise CLIError(tr("cli.export.create_failed", error=exc)) from exc
    temporary = Path(temporary_name)
    sock = stream = None
    row_count = 0
    saw_metadata = False
    saw_complete = False
    try:
        if os.name == "posix":
            os.chmod(temporary, 0o600)
        sock, stream = _open_export(settings)
        with os.fdopen(descriptor, "wb") as output:
            descriptor = -1
            for raw in stream:
                try:
                    record = json.loads(raw)
                except (UnicodeDecodeError, json.JSONDecodeError) as exc:
                    raise CLIError(tr("cli.export.invalid_json")) from exc
                if not isinstance(record, dict):
                    raise CLIError(tr("cli.export.invalid_line"))
                if record.get("ok") is False:
                    raise CLIError(str(record.get("error", tr("cli.export.failed"))))
                record_type = record.get("record")
                if not saw_metadata:
                    if (
                        record_type != "metadata"
                        or record.get("format") != "meshpi-database-export"
                    ):
                        raise CLIError(tr("cli.export.invalid_metadata"))
                    saw_metadata = True
                elif saw_complete:
                    raise CLIError(tr("cli.export.data_after_end"))
                elif record_type == "row":
                    row_count += 1
                elif record_type == "complete":
                    expected_rows = record.get("rows")
                    if expected_rows != row_count:
                        raise CLIError(tr("cli.export.wrong_row_count"))
                    saw_complete = True
                else:
                    raise CLIError(tr("cli.export.unknown_record"))
                output.write(raw if raw.endswith(b"\n") else raw + b"\n")
            if not saw_complete:
                raise CLIError(tr("cli.export.interrupted"))
            output.flush()
            os.fsync(output.fileno())
        if not force and (destination.exists() or destination.is_symlink()):
            raise ValueError(tr("cli.export.created_during", path=destination))
        os.replace(temporary, destination)
        return destination, row_count
    except OSError as exc:
        raise CLIError(tr("cli.export.save_failed", error=exc)) from exc
    finally:
        if descriptor >= 0:
            os.close(descriptor)
        if stream is not None:
            stream.close()
        if sock is not None:
            sock.close()
        temporary.unlink(missing_ok=True)


def _local_time(value: str | int | None) -> str:
    if value is None:
        return "–"
    try:
        if isinstance(value, int):
            parsed = datetime.fromtimestamp(value).astimezone()
        else:
            parsed = datetime.fromisoformat(value).astimezone()
        return parsed.strftime("%Y-%m-%d %H:%M:%S")
    except (ValueError, TypeError, OSError):
        return str(value)


def _trim(value: Any, width: int) -> str:
    text = "–" if value in (None, "") else sanitize_terminal_text(value)
    if len(text) <= width:
        return text
    return text[: max(1, width - 1)] + "…"


def _battery(value: Any) -> str:
    if value in (None, ""):
        return "–"
    if value in (0, 101, "0", "101"):
        return tr("cli.value.power")
    return f"{value}%"


def _display_value(value: Any) -> str:
    text = str(value or "ukjend")
    key = {
        "ukjend": "common.unknown",
        "tilkopla": "cli.value.connected",
        "fråkopla": "cli.value.disconnected",
        "starta": "cli.value.started",
        "stoppa": "cli.value.stopped",
        "always": "cli.value.always",
        "session": "cli.value.session",
        "ja": "cli.value.yes",
        "nei": "cli.value.no",
    }.get(text)
    return tr(key) if key else sanitize_terminal_text(text)


def _public_channel_options(value: str | None) -> dict[str, Any]:
    if value is None:
        return {}
    channel = value.strip()
    if channel.isdecimal():
        channel_index = int(channel)
        if not 0 <= channel_index <= 7:
            raise CLIError(tr("cli.channel.range"))
        return {"channel_index": channel_index}
    try:
        parse_public_conversation_id(channel)
    except ValueError as exc:
        raise CLIError(tr("cli.channel.invalid")) from exc
    return {"conversation": channel}


def _format_message(message: dict[str, Any]) -> str:
    timestamp = _local_time(message.get("timestamp"))
    timestamp = timestamp[5:] if len(timestamp) >= 19 else timestamp
    name = sanitize_terminal_text(
        message.get("from_long_name")
        or message.get("from_short_name")
        or message.get("from_node")
        or tr("common.unknown")
    )
    short_id = (message.get("from_node") or "????")[-4:]
    transport = message.get("transport") or tr("common.unknown")
    transport = (
        tr("cli.value.unknown_transport") if transport in {"Ukjend", "Unknown"} else transport
    )
    direction = "→" if message.get("direction") == "ut" else "←"
    channel = message.get("channel")
    channel_label = channel if channel is not None else 0
    context = f"CH{channel_label}" if message.get("kind") == "public" else f"DM{channel_label}"
    status_value = "ACK" if message.get("status") == "stadfesta" else message.get("status")
    status_value = status_value or "sendt"
    status = f" [{status_value}]" if message.get("direction") == "ut" else ""
    quality: list[str] = []
    if message.get("rssi") is not None:
        quality.append(f"RSSI {message['rssi']}")
    if message.get("snr") is not None:
        quality.append(f"SNR {message['snr']}")
    if message.get("hop_start") is not None or message.get("hop_limit") is not None:
        quality.append(
            tr(
                "cli.message.hops",
                start=message.get("hop_start", "–"),
                limit=message.get("hop_limit", "–"),
            )
        )
    detail = f"  ({', '.join(quality)})" if quality else ""
    return (
        f"{timestamp}  {context} {direction} {_trim(name, 22):22} [{short_id}] "
        f"{transport}  {sanitize_terminal_text(message.get('text', ''))}{status}{detail}"
    )


def _print_status(data: dict[str, Any]) -> None:
    print(tr("cli.status.state", value=_display_value(data.get("state"))))
    endpoint = data.get("endpoint")
    host = data.get("host")
    port = data.get("port")
    if not endpoint and host not in (None, "") and port not in (None, ""):
        endpoint = f"{host}:{port}"
    if endpoint:
        transport = str(data.get("transport") or "tcp").upper()
        print(tr("cli.status.meshtastic", value=f"{transport} {endpoint}"))
    else:
        print(tr("cli.status.no_node"))
    print(
        tr(
            "cli.status.local_node",
            value=data.get("local_node_id") or tr("cli.value.not_known_yet"),
        )
    )
    print(tr("cli.status.connected_since", value=_local_time(data.get("connected_since"))))
    if data.get("reconnect_attempt"):
        print(tr("cli.status.reconnect", value=data["reconnect_attempt"]))
    if data.get("last_reconnect_reason"):
        print(tr("cli.status.last_disconnect", value=data["last_reconnect_reason"]))
    if data.get("error"):
        print(tr("cli.status.error", value=data["error"]))


def _print_service(data: dict[str, Any], action: str) -> None:
    if action == "status":
        print(tr("cli.service.background", value=_display_value(data.get("state"))))
        print(tr("cli.service.mode", value=_display_value(data.get("background_mode"))))
        if data.get("daemon_pid"):
            print(tr("cli.service.pid", value=data["daemon_pid"]))
        if data.get("endpoint"):
            print(
                tr(
                    "cli.status.meshtastic",
                    value=(f"{str(data.get('transport') or 'tcp').upper()} {data['endpoint']}"),
                )
            )
        if data.get("error"):
            print(tr("cli.status.error", value=data["error"]))
        return
    labels = {
        "start": tr("cli.service.started"),
        "stop": tr("cli.service.stopped"),
        "enable": tr("cli.service.enabled"),
        "disable": tr("cli.service.disabled"),
    }
    print(labels[action])


def _print_nodes(nodes: list[dict[str, Any]]) -> None:
    if not nodes:
        print(tr("cli.nodes.empty"))
        return
    print(tr("cli.nodes.header"))
    print("─" * 105)
    for node in nodes:
        marker = "*" if node.get("is_local") else " "
        name = node.get("long_name") or node.get("short_name") or tr("common.unknown")
        battery = _battery(node.get("battery_level"))
        can_dm = node.get("can_receive_dm")
        dm = (
            tr("cli.value.yes")
            if can_dm is True
            else tr("cli.value.no")
            if can_dm is False
            else tr("common.unknown")
        )
        print(
            f"{marker} {_trim(name, 24):24} "
            f"{_trim(node.get('short_id'), 6):6} "
            f"{_trim(node.get('node_id'), 10):10} "
            f"{_local_time(node.get('last_heard')):19} "
            f"{battery:5} {_trim(node.get('snr'), 6):6} "
            f"{_trim(node.get('hops_away'), 4):4} "
            f"{_trim(node.get('transport'), 7):7} {dm:7}"
        )
    print(tr("cli.nodes.local_legend"))


def _print_node(node: dict[str, Any]) -> None:
    can_dm = node.get("can_receive_dm")
    dm = (
        tr("cli.value.yes")
        if can_dm is True
        else tr("cli.value.no")
        if can_dm is False
        else tr("common.unknown")
    )
    fields = (
        (tr("cli.node.name"), node.get("long_name")),
        (tr("cli.node.short_name"), node.get("short_name")),
        ("Node-ID", node.get("node_id")),
        (tr("cli.node.short_id"), node.get("short_id")),
        (tr("cli.node.number"), node.get("node_num")),
        (tr("cli.node.hardware"), node.get("hw_model")),
        (tr("cli.node.role"), node.get("role")),
        (tr("cli.node.last_seen"), _local_time(node.get("last_heard"))),
        (tr("cli.node.battery"), _battery(node.get("battery_level"))),
        (
            tr("cli.node.voltage"),
            f"{node['voltage']} V" if node.get("voltage") is not None else None,
        ),
        ("SNR", node.get("snr")),
        ("RSSI", node.get("rssi")),
        (tr("cli.node.hops"), node.get("hops_away")),
        (tr("cli.node.transport"), node.get("transport")),
        (tr("cli.node.can_dm"), dm),
        (tr("cli.node.local"), tr("cli.value.yes") if node.get("is_local") else tr("cli.value.no")),
    )
    for label, value in fields:
        rendered = sanitize_terminal_text(value) if value not in (None, "") else "–"
        print(f"{label + ':':18} {rendered}")
    if not node.get("is_local"):
        print(tr("cli.node.start_chat", node_id=node["node_id"]))


def _print_conversations(conversations: list[dict[str, Any]]) -> None:
    if not conversations:
        print(tr("cli.conversations.empty"))
        return
    print(tr("cli.conversations.header"))
    print("─" * 90)
    for item in conversations:
        if item["kind"] == "public":
            channel = item.get("channel")
            name = item.get("channel_name")
            channel_key = str(item.get("channel_key") or "")
            legacy_scope = (
                channel_key.split(":", 2)[1]
                if channel_key.startswith("legacy:") and ":" in channel_key
                else ""
            )
            suffix = (
                f" [{str(item.get('local_node_id'))[-4:]}]"
                if channel_key.startswith("local:") and item.get("local_node_id")
                else ""
            )
            if legacy_scope:
                label = tr(
                    "cli.conversations.public_archive",
                    archive=_trim(legacy_scope, 10),
                    channel=channel if channel is not None else "?",
                )
            elif channel_key.startswith("provisional:"):
                label = tr(
                    "cli.conversations.public_provisional",
                    channel=channel if channel is not None else "?",
                )
            else:
                label = (
                    tr(
                        "cli.conversations.public_named",
                        name=name,
                        channel=channel if channel is not None else "?",
                        suffix=suffix,
                    )
                    if name
                    else (
                        tr(
                            "cli.conversations.public",
                            channel=channel if channel is not None else "?",
                            suffix=suffix,
                        )
                    )
                )
        else:
            peer = str(item.get("peer_node") or item["conversation"])
            name = item.get("long_name") or item.get("short_name") or peer
            label = f"{name} [{peer[-4:]}]"
        print(
            f"{_trim(label, 28):28} {item.get('unread', 0):5} "
            f"{_local_time(item.get('last_timestamp')):19}  "
            f"{_trim(item.get('last_text'), 32)}"
        )


def _print_channels(channels: list[dict[str, Any]]) -> None:
    if not channels:
        print(tr("cli.channels.empty"))
        return
    print(tr("cli.channels.header"))
    print("─" * 90)
    for channel in channels:
        print(
            f"{channel.get('channel_index', '?'):6} "
            f"{str(channel.get('role') or '–'):10} "
            f"{_trim(channel.get('display_name'), 20):20} "
            f"{channel.get('conversation')}"
        )


def _print_connections(data: dict[str, Any]) -> None:
    active_id = data.get("active_profile_id")
    profiles = data.get("profiles", [])
    if not profiles:
        print(tr("cli.connections.empty"))
        return
    print(tr("cli.connections.header"))
    print("─" * 76)
    for profile in profiles:
        marker = "*" if profile.get("profile_id") == active_id else " "
        print(
            f"{marker} {_trim(profile.get('name'), 24):24} "
            f"{str(profile.get('transport', '')).upper():8} "
            f"{profile.get('endpoint', '–')}"
        )
    print(tr("cli.connections.active_legend"))


def _print_messages(messages: list[dict[str, Any]]) -> None:
    if not messages:
        print(tr("cli.messages.empty"))
        return
    for message in messages:
        print(_format_message(message))


def _watch(settings: Settings, conversation: str, raw_json: bool = False) -> None:
    sock, stream = _open_watch(settings, conversation)
    try:
        for raw in stream:
            event = json.loads(raw)
            if raw_json and event.get("type") != "heartbeat":
                print(json.dumps(event, ensure_ascii=False), flush=True)
                continue
            if event.get("type") == "message":
                print(_format_message(event["data"]), flush=True)
            elif event.get("type") == "message_status":
                data = event["data"]
                print(
                    tr(
                        "cli.watch.packet_status",
                        packet=data.get("packet_id"),
                        status=data.get("status"),
                    ),
                    flush=True,
                )
            elif event.get("type") == "status":
                print(
                    tr("cli.watch.connection", state=_display_value(event["data"].get("state"))),
                    flush=True,
                )
    except KeyboardInterrupt:
        pass
    finally:
        stream.close()
        sock.close()


def _chat_dm_peer(
    conversation: str,
    history: list[dict[str, Any]],
) -> str:
    if not conversation.startswith("dm:"):
        return conversation
    peer = next(
        (str(message["peer_node"]) for message in reversed(history) if message.get("peer_node")),
        "",
    )
    if peer:
        return peer
    _, peer, _ = parse_dm_conversation_id(conversation)
    return peer


def _chat(settings: Settings, conversation: str, limit: int) -> None:
    from prompt_toolkit import PromptSession
    from prompt_toolkit.patch_stdout import patch_stdout

    normalized = (
        conversation
        if conversation == "public" or conversation.startswith(("channel:", "dm:"))
        else normalize_node_id(conversation)
    )
    history = _request(
        settings,
        {
            "command": "messages",
            "conversation": normalized,
            "limit": limit,
            "mark_read": True,
        },
    )["data"]
    dm_peer = _chat_dm_peer(normalized, history)
    label = (
        tr("cli.chat.public_primary")
        if normalized == "public"
        else (
            tr("cli.chat.channel", conversation=normalized)
            if normalized.startswith("channel:")
            else tr("cli.chat.dm", conversation=normalized)
        )
    )
    status = _request(settings, {"command": "status"})["data"]
    print(f"\n{label}   |   {status.get('state')}")
    print("─" * 78)
    _print_messages(history)
    print(tr("cli.chat.intro"))

    sock, stream = _open_watch(settings, normalized)
    stop = threading.Event()

    def receive() -> None:
        try:
            for raw in stream:
                if stop.is_set():
                    return
                event = json.loads(raw)
                if event.get("type") == "message":
                    print(_format_message(event["data"]))
                elif event.get("type") == "message_status":
                    data = event["data"]
                    print(
                        tr(
                            "cli.watch.packet",
                            packet=data.get("packet_id"),
                            status=data.get("status"),
                        )
                    )
                elif event.get("type") == "status":
                    print(
                        tr("cli.watch.connection", state=_display_value(event["data"].get("state")))
                    )
        except (OSError, ValueError):
            if not stop.is_set():
                print(tr("cli.watch.broken"))

    receiver = threading.Thread(target=receive, name="cli-watch", daemon=True)
    receiver.start()
    session: PromptSession[str] = PromptSession()
    try:
        with patch_stdout():
            while True:
                try:
                    text = session.prompt("> ")
                except (EOFError, KeyboardInterrupt):
                    break
                command = text.strip()
                if not command:
                    continue
                if command in {"/slutt", "/quit", "/exit"}:
                    break
                if command in {"/hjelp", "/help"}:
                    print(tr("cli.chat.commands"))
                    continue
                if command == "/status":
                    _print_status(_request(settings, {"command": "status"})["data"])
                    continue
                if command in {"/nodar", "/nodes"}:
                    _print_nodes(_request(settings, {"command": "nodes"})["data"])
                    continue
                payload = (
                    {
                        "command": "send_public",
                        "text": command,
                        **(
                            {"conversation": normalized}
                            if normalized.startswith("channel:")
                            else {}
                        ),
                    }
                    if normalized == "public" or normalized.startswith("channel:")
                    else {
                        "command": "send_dm",
                        "node_id": dm_peer,
                        "text": command,
                        **({"conversation": normalized} if normalized.startswith("dm:") else {}),
                    }
                )
                try:
                    _request(settings, payload)
                except CLIError as exc:
                    print(f"{tr('common.error')}: {exc}")
    finally:
        stop.set()
        with suppress(OSError):
            sock.shutdown(socket.SHUT_RDWR)
        stream.close()
        sock.close()
        receiver.join(timeout=2)


def build_parser() -> argparse.ArgumentParser:
    parser = MeshPiArgumentParser(
        prog="meshpi",
        description=tr("app.description"),
        add_help=False,
    )
    parser.add_argument("-h", "--help", action="help", help=tr("cli.help.help"))
    parser.add_argument(
        "--version",
        action="version",
        version=f"MeshPi {__version__}",
    )
    parser.add_argument(
        "--env-file",
        default=_default_env_file(),
        help=tr("cli.help.env_file"),
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help=tr("cli.help.json"),
    )
    sub = parser.add_subparsers(dest="command")
    parser.set_defaults(command="tui")
    sub.add_parser("tui", help=tr("cli.help.tui"))
    sub.add_parser("new", help=tr("cli.help.new"))
    connect = sub.add_parser("connect", help=tr("cli.help.connect"))
    connect.add_argument(
        "target",
        help=tr("cli.help.connect_target"),
    )
    connect.add_argument("--name", help=tr("cli.help.profile_name"))
    sub.add_parser("connections", help=tr("cli.help.connections"))
    daemon = sub.add_parser("daemon", help=tr("cli.help.daemon"))
    daemon.add_argument("--parent-pid", type=int, help=argparse.SUPPRESS)
    doctor = sub.add_parser("doctor", help=tr("cli.help.doctor"))
    doctor.add_argument(
        "--offline",
        action="store_true",
        help=tr("cli.help.offline"),
    )
    export = sub.add_parser(
        "export",
        help=tr("cli.help.export"),
    )
    export.add_argument(
        "output",
        nargs="?",
        type=Path,
        help=tr("cli.help.export_output"),
    )
    export.add_argument(
        "--force",
        action="store_true",
        help=tr("cli.help.force"),
    )
    service = sub.add_parser("service", help=tr("cli.help.service"))
    service.add_argument(
        "action",
        choices=("status", "start", "stop", "enable", "disable"),
    )
    update = sub.add_parser(
        "update",
        help=tr("cli.help.update"),
    )
    update.add_argument(
        "--check",
        action="store_true",
        help=tr("cli.help.update_check"),
    )
    update.add_argument(
        "--yes",
        action="store_true",
        help=tr("cli.help.yes_update"),
    )
    update.add_argument(
        "--beta",
        action="store_true",
        help=tr("cli.help.beta"),
    )
    sub.add_parser("status", help=tr("cli.help.status"))

    nodes = sub.add_parser("nodes", help=tr("cli.help.nodes"))
    nodes.add_argument("--search", default="", help=tr("cli.help.search"))
    nodes.add_argument(
        "--sort",
        choices=("name", "seen", "id"),
        default="seen",
        help=tr("cli.help.sort"),
    )
    node = sub.add_parser("node", help=tr("cli.help.node"))
    node.add_argument("node_id")
    sub.add_parser("conversations", help=tr("cli.help.conversations"))
    sub.add_parser("channels", help=tr("cli.help.channels"))
    delete_messages = sub.add_parser(
        "delete-messages",
        help=tr("cli.help.delete_messages"),
    )
    delete_messages.add_argument("scope", choices=("public", "dm", "all"))
    delete_messages.add_argument(
        "--yes",
        action="store_true",
        help=tr("cli.help.yes_delete"),
    )

    public = sub.add_parser("public", help=tr("cli.help.public"))
    public.add_argument("--limit", type=int, default=100)
    public.add_argument(
        "--channel",
        help=tr("cli.help.public_channel"),
    )
    dm = sub.add_parser("dm", help=tr("cli.help.dm"))
    dm.add_argument("node_id")
    dm.add_argument("--limit", type=int, default=100)
    dm.add_argument("--channel", type=int, help=tr("cli.help.dm_channel"))

    send_public = sub.add_parser("send-public", help=tr("cli.help.send_public"))
    send_public.add_argument("text")
    send_public.add_argument(
        "--channel",
        help=tr("cli.help.public_channel"),
    )
    send_dm = sub.add_parser("send-dm", help=tr("cli.help.send_dm"))
    send_dm.add_argument("node_id")
    send_dm.add_argument("text")
    send_dm.add_argument("--channel", type=int, help=tr("cli.help.dm_channel"))

    watch = sub.add_parser("watch", help=tr("cli.help.watch"))
    watch.add_argument(
        "conversation",
        nargs="?",
        default="all",
        help=tr("cli.help.watch_conversation"),
    )
    chat = sub.add_parser("chat", help=tr("cli.help.chat"))
    chat.add_argument("conversation", help=tr("cli.help.chat_conversation"))
    chat.add_argument("--limit", type=int, default=50)
    language = sub.add_parser("language", help=tr("cli.help.language"))
    language.add_argument("language", nargs="?", choices=("nn", "en"))
    return parser


def run(args: argparse.Namespace, settings: Settings) -> str | None:
    command = args.command
    if command == "language":
        if args.language is None:
            print(tr("language.current", language=LANGUAGE_NAMES[get_language()]))
        else:
            selected = set_language(args.language, persist=True)
            print(tr("language.saved", language=LANGUAGE_NAMES[selected]))
    elif command == "tui":
        from meshpi.connect_tui import choose_connection
        from meshpi.tui import run_tui

        connections = _request(settings, {"command": "connections"})["data"]
        if not connections.get("profiles"):
            selection = choose_connection(settings)
            if selection is None:
                return None
            _request(settings, {"command": "connect"} | selection)
        return run_tui(settings)
    elif command == "new":
        from meshpi.connect_tui import choose_connection
        from meshpi.tui import run_tui

        selection = choose_connection(settings)
        if selection is not None:
            _request(settings, {"command": "connect"} | selection)
            return run_tui(settings)
    elif command == "connect":
        from meshpi.tui import run_tui

        _request(
            settings,
            {
                "command": "connect",
                "target": args.target,
                "name": args.name,
            },
        )
        return run_tui(settings)
    elif command == "connections":
        data = _request(settings, {"command": "connections"})["data"]
        print(json.dumps(data, ensure_ascii=False)) if args.json else _print_connections(data)
    elif command == "daemon":
        run_daemon(settings, parent_pid=args.parent_pid)
    elif command == "doctor":
        failed = False
        for name, ok, detail in offline_checks(settings):
            result_label = "OK" if ok else tr("cli.doctor.failed_label")
            print(f"{result_label:4}  {name:18} {detail}")
            failed = failed or not ok
        if failed:
            raise RuntimeError(tr("cli.doctor.failed"))
    elif command == "export":
        destination, rows = _write_database_export(
            settings,
            args.output or _default_export_path(),
            force=args.force,
        )
        result = {
            "exported": True,
            "path": str(destination),
            "rows": rows,
            "format": "meshpi-database-export",
        }
        if args.json:
            print(json.dumps(result, ensure_ascii=False))
        else:
            print(tr("cli.export.completed", rows=rows, path=destination))
            print(tr("cli.export.privacy_warning"))
    elif command == "service":
        data = manage_service(args.action, settings, args.env_file)
        print(json.dumps(data, ensure_ascii=False)) if args.json else _print_service(
            data, args.action
        )
    elif command == "update":
        channel = "beta" if args.beta else "stable"
        notice = check_for_update(settings, channel=channel)
        if notice is None:
            result = {
                "updated": False,
                "version": __version__,
                "channel": channel,
            }
            if args.json:
                print(json.dumps(result, ensure_ascii=False))
            else:
                channel_label = tr(
                    "cli.update.beta_channel" if args.beta else "cli.update.stable_channel"
                )
                print(tr("cli.update.none", channel=channel_label, version=__version__))
            return None
        if args.check:
            result = {
                "updated": False,
                "current_version": notice.current_version,
                "latest_version": notice.latest_version,
                "channel": channel,
            }
            if args.json:
                print(json.dumps(result, ensure_ascii=False))
            else:
                channel_label = tr(
                    "cli.update.beta_channel" if args.beta else "cli.update.stable_channel"
                )
                print(
                    tr(
                        "cli.update.available",
                        latest=notice.latest_version,
                        channel=channel_label,
                        current=notice.current_version,
                    )
                )
            return None
        if args.json and not args.yes:
            raise ValueError(tr("cli.update.json_requires_yes"))
        if not args.yes:
            answer = input(tr("cli.update.confirm", version=notice.latest_version))
            if answer.strip().upper() not in {"OPPDATER", "UPDATE"}:
                print(tr("cli.update.cancelled"))
                return None
        installed = apply_update(
            settings,
            expected_version=notice.latest_version,
            channel=channel,
        )
        result = {
            "updated": installed is not None,
            "version": installed or __version__,
        }
        if args.json:
            print(json.dumps(result, ensure_ascii=False))
        else:
            print(tr("cli.update.installed", version=result["version"]))
    elif command == "status":
        data = _request(settings, {"command": "status"})["data"]
        print(json.dumps(data, ensure_ascii=False)) if args.json else _print_status(data)
    elif command == "nodes":
        data = _request(
            settings,
            {"command": "nodes", "search": args.search, "sort": args.sort},
        )["data"]
        print(json.dumps(data, ensure_ascii=False)) if args.json else _print_nodes(data)
    elif command == "node":
        data = _request(
            settings,
            {"command": "node", "node_id": args.node_id},
        )["data"]
        print(json.dumps(data, ensure_ascii=False)) if args.json else _print_node(data)
    elif command == "conversations":
        data = _request(settings, {"command": "conversations"})["data"]
        print(json.dumps(data, ensure_ascii=False)) if args.json else _print_conversations(data)
    elif command == "channels":
        data = _request(settings, {"command": "channels"})["data"]
        print(json.dumps(data, ensure_ascii=False)) if args.json else _print_channels(data)
    elif command == "delete-messages":
        if not args.yes:
            if args.json:
                raise ValueError(tr("cli.delete.json_requires_yes"))
            labels = {
                "public": tr("cli.delete.scope_public"),
                "dm": tr("cli.delete.scope_dm"),
                "all": tr("cli.delete.scope_all"),
            }
            answer = input(tr("cli.delete.confirm", scope=labels[args.scope]))
            if answer.strip().upper() not in {"SLETT", "DELETE"}:
                print(tr("cli.delete.cancelled"))
                return None
        data = _request(
            settings,
            {"command": "delete_messages", "scope": args.scope},
        )["data"]
        if args.json:
            print(json.dumps(data, ensure_ascii=False))
        else:
            print(tr("cli.delete.completed", count=data["deleted"]))
    elif command in {"public", "dm"}:
        conversation = "public" if command == "public" else normalize_node_id(args.node_id)
        public_options = _public_channel_options(args.channel) if command == "public" else {}
        if public_options.get("conversation"):
            conversation = str(public_options["conversation"])
        data = _request(
            settings,
            {
                "command": "messages",
                "conversation": conversation,
                "limit": args.limit,
                "mark_read": not args.json,
                **(
                    {"channel_index": public_options["channel_index"]}
                    if command == "public" and "channel_index" in public_options
                    else (
                        {"channel_index": args.channel}
                        if command == "dm" and args.channel is not None
                        else {}
                    )
                ),
            },
        )["data"]
        print(json.dumps(data, ensure_ascii=False)) if args.json else _print_messages(data)
    elif command == "send-public":
        send_payload: dict[str, Any] = {
            "command": "send_public",
            "text": args.text,
        }
        send_payload.update(_public_channel_options(args.channel))
        message = _request(settings, send_payload)["data"]
        if args.json:
            print(json.dumps(message, ensure_ascii=False))
        else:
            print(
                tr(
                    "cli.send.completed",
                    packet=message.get("packet_id") or tr("cli.value.unknown_id"),
                )
            )
    elif command == "send-dm":
        message = _request(
            settings,
            {
                "command": "send_dm",
                "node_id": args.node_id,
                "text": args.text,
                **({"channel_index": args.channel} if args.channel is not None else {}),
            },
        )["data"]
        if args.json:
            print(json.dumps(message, ensure_ascii=False))
        else:
            print(
                tr(
                    "cli.send.completed",
                    packet=message.get("packet_id") or tr("cli.value.unknown_id"),
                )
            )
    elif command == "watch":
        conversation = args.conversation
        if conversation not in {"all", "public"} and not conversation.startswith(
            ("channel:", "dm:")
        ):
            conversation = normalize_node_id(conversation)
        _watch(settings, conversation, raw_json=args.json)
    elif command == "chat":
        _chat(settings, args.conversation, args.limit)


def main(argv: list[str] | None = None) -> None:
    _configure_console_output()
    raw_argv = _normalize_argv(list(sys.argv[1:] if argv is None else argv))
    initial_env_file = Path(_env_file_from_argv(raw_argv)).expanduser()
    initialize_language(path=language_file(), legacy_paths=(initial_env_file,))
    parser = build_parser()
    args = parser.parse_args(raw_argv)
    try:
        settings = Settings.load(args.env_file)
        handle = DaemonHandle()
        needs_daemon = args.command not in {
            "daemon",
            "doctor",
            "language",
            "service",
            "update",
        }
        if needs_daemon:
            if settings.background_mode == "session":
                handle = start_session_daemon(settings, args.env_file)
            elif daemon_status(settings) is None:
                manage_service("start", settings, args.env_file)
                wait_for_daemon(settings)
        outcome: str | None = None
        try:
            outcome = run(args, settings)
        finally:
            if outcome == "stop":
                manage_service("stop", settings, args.env_file)
            elif handle.owned and outcome != "leave":
                stop_daemon(settings)
    except (CLIError, ValueError, RuntimeError) as exc:
        print(f"{tr('common.error')}: {exc}", file=sys.stderr)
        raise SystemExit(EXIT_ERROR) from exc
