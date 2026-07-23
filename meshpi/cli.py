from __future__ import annotations

import argparse
import json
import socket
import sys
import threading
from contextlib import suppress
from datetime import datetime
from typing import Any

from meshpi import __version__
from meshpi.client import CLIError
from meshpi.client import open_watch as _open_watch
from meshpi.client import request as _request
from meshpi.config import Settings
from meshpi.daemon import run_daemon
from meshpi.doctor import offline_checks
from meshpi.lifecycle import (
    DaemonHandle,
    daemon_status,
    start_session_daemon,
    stop_daemon,
    wait_for_daemon,
)
from meshpi.models import normalize_node_id, sanitize_terminal_text
from meshpi.platform_service import manage_service

EXIT_ERROR = 1
COMMANDS = {
    "tui",
    "new",
    "connect",
    "connections",
    "daemon",
    "doctor",
    "service",
    "status",
    "nodes",
    "node",
    "conversations",
    "delete-messages",
    "public",
    "dm",
    "send-public",
    "send-dm",
    "watch",
    "chat",
}


def _normalize_argv(argv: list[str]) -> list[str]:
    if argv and not argv[0].startswith("-") and argv[0] not in COMMANDS:
        return ["connect", argv[0], *argv[1:]]
    return argv


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
        return "Straum"
    return f"{value}%"


def _format_message(message: dict[str, Any]) -> str:
    timestamp = _local_time(message.get("timestamp"))
    timestamp = timestamp[5:] if len(timestamp) >= 19 else timestamp
    name = sanitize_terminal_text(
        message.get("from_long_name")
        or message.get("from_short_name")
        or message.get("from_node")
        or "Ukjend"
    )
    short_id = (message.get("from_node") or "????")[-4:]
    transport = message.get("transport") or "Ukjend"
    transport = "transport ukjend" if transport == "Ukjend" else transport
    direction = "→" if message.get("direction") == "ut" else "←"
    context = "CH0" if message.get("kind") == "public" else "DM "
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
            f"hopp {message.get('hop_start', '–')}/{message.get('hop_limit', '–')}"
        )
    detail = f"  ({', '.join(quality)})" if quality else ""
    return (
        f"{timestamp}  {context} {direction} {_trim(name, 22):22} [{short_id}] "
        f"{transport}  {sanitize_terminal_text(message.get('text', ''))}{status}{detail}"
    )


def _print_status(data: dict[str, Any]) -> None:
    print(f"Status:       {data.get('state', 'ukjend')}")
    endpoint = data.get("endpoint")
    host = data.get("host")
    port = data.get("port")
    if not endpoint and host not in (None, "") and port not in (None, ""):
        endpoint = f"{host}:{port}"
    if endpoint:
        transport = str(data.get("transport") or "tcp").upper()
        print(f"Meshtastic:   {transport} {endpoint}")
    else:
        print("Meshtastic:   ingen node vald")
    print(f"Lokal node:   {data.get('local_node_id') or 'ikkje kjend enno'}")
    print(f"Tilkopla frå: {_local_time(data.get('connected_since'))}")
    if data.get("reconnect_attempt"):
        print(f"Reconnect:    forsøk {data['reconnect_attempt']}")
    if data.get("error"):
        print(f"Feil:         {data['error']}")


def _print_service(data: dict[str, Any], action: str) -> None:
    if action == "status":
        state = data.get("state", "ukjend")
        print(f"Bakgrunn:     {state}")
        print(f"Modus:        {data.get('background_mode', 'ukjend')}")
        if data.get("daemon_pid"):
            print(f"Prosess-ID:   {data['daemon_pid']}")
        if data.get("endpoint"):
            print(f"Meshtastic:   {str(data.get('transport') or 'tcp').upper()} "
                  f"{data['endpoint']}")
        if data.get("error"):
            print(f"Feil:         {data['error']}")
        return
    labels = {
        "start": "Bakgrunnstenesta er starta.",
        "stop": "Bakgrunnstenesta er stoppa.",
        "enable": "Automatisk oppstart er slått på.",
        "disable": "Automatisk oppstart er slått av.",
    }
    print(labels[action])


def _print_nodes(nodes: list[dict[str, Any]]) -> None:
    if not nodes:
        print("Ingen kjende nodar.")
        return
    print(
        f"{' ':1} {'Namn':24} {'Kort':6} {'Node-ID':10} {'Sist sett':19} "
        f"{'Batt':5} {'SNR':6} {'Hopp':4} {'Veg':7} {'DM':7}"
    )
    print("─" * 105)
    for node in nodes:
        marker = "*" if node.get("is_local") else " "
        name = node.get("long_name") or node.get("short_name") or "Ukjend"
        battery = _battery(node.get("battery_level"))
        can_dm = node.get("can_receive_dm")
        dm = "ja" if can_dm is True else "nei" if can_dm is False else "ukjend"
        print(
            f"{marker} {_trim(name, 24):24} "
            f"{_trim(node.get('short_id'), 6):6} "
            f"{_trim(node.get('node_id'), 10):10} "
            f"{_local_time(node.get('last_heard')):19} "
            f"{battery:5} {_trim(node.get('snr'), 6):6} "
            f"{_trim(node.get('hops_away'), 4):4} "
            f"{_trim(node.get('transport'), 7):7} {dm:7}"
        )
    print("\n* = lokal node")


def _print_node(node: dict[str, Any]) -> None:
    can_dm = node.get("can_receive_dm")
    dm = "ja" if can_dm is True else "nei" if can_dm is False else "ukjend"
    fields = (
        ("Namn", node.get("long_name")),
        ("Kortnamn", node.get("short_name")),
        ("Node-ID", node.get("node_id")),
        ("Kort-ID", node.get("short_id")),
        ("Nodenummer", node.get("node_num")),
        ("Maskinvare", node.get("hw_model")),
        ("Rolle", node.get("role")),
        ("Sist sett", _local_time(node.get("last_heard"))),
        ("Batteri", _battery(node.get("battery_level"))),
        ("Spenning", f"{node['voltage']} V" if node.get("voltage") is not None else None),
        ("SNR", node.get("snr")),
        ("RSSI", node.get("rssi")),
        ("Hopp", node.get("hops_away")),
        ("Siste transport", node.get("transport")),
        ("Kan ta imot DM", dm),
        ("Lokal node", "ja" if node.get("is_local") else "nei"),
    )
    for label, value in fields:
        rendered = sanitize_terminal_text(value) if value not in (None, "") else "–"
        print(f"{label + ':':18} {rendered}")
    if not node.get("is_local"):
        print(f"\nStart samtale: meshpi chat {node['node_id']}")


def _print_conversations(conversations: list[dict[str, Any]]) -> None:
    if not conversations:
        print("Ingen samtalar er lagra enno.")
        return
    print(f"{'Samtale':28} {'Ulest':5} {'Siste melding':19}  Tekst")
    print("─" * 90)
    for item in conversations:
        if item["kind"] == "public":
            label = "Public – kanal 0"
        else:
            name = item.get("long_name") or item.get("short_name") or item["conversation"]
            label = f"{name} [{item['conversation'][-4:]}]"
        print(
            f"{_trim(label, 28):28} {item.get('unread', 0):5} "
            f"{_local_time(item.get('last_timestamp')):19}  "
            f"{_trim(item.get('last_text'), 32)}"
        )


def _print_connections(data: dict[str, Any]) -> None:
    active_id = data.get("active_profile_id")
    profiles = data.get("profiles", [])
    if not profiles:
        print("Ingen lagra tilkoplingar.")
        return
    print(f"{' ':1} {'Namn':24} {'Type':8} Endepunkt")
    print("─" * 76)
    for profile in profiles:
        marker = "*" if profile.get("profile_id") == active_id else " "
        print(
            f"{marker} {_trim(profile.get('name'), 24):24} "
            f"{str(profile.get('transport', '')).upper():8} "
            f"{profile.get('endpoint', '–')}"
        )
    print("\n* = aktiv tilkopling")


def _print_messages(messages: list[dict[str, Any]]) -> None:
    if not messages:
        print("Ingen meldingar.")
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
                    f"Status for pakke {data.get('packet_id')}: {data.get('status')}",
                    flush=True,
                )
            elif event.get("type") == "status":
                print(f"— Samband: {event['data'].get('state')} —", flush=True)
    except KeyboardInterrupt:
        pass
    finally:
        stream.close()
        sock.close()


def _chat(settings: Settings, conversation: str, limit: int) -> None:
    from prompt_toolkit import PromptSession
    from prompt_toolkit.patch_stdout import patch_stdout

    normalized = "public" if conversation == "public" else normalize_node_id(conversation)
    history = _request(
        settings,
        {
            "command": "messages",
            "conversation": normalized,
            "limit": limit,
            "mark_read": True,
        },
    )["data"]
    label = "Public – kanal 0" if normalized == "public" else f"DM {normalized}"
    status = _request(settings, {"command": "status"})["data"]
    print(f"\n{label}   |   {status.get('state')}")
    print("─" * 78)
    _print_messages(history)
    print("\nSkriv /hjelp for kommandoar. Ctrl-D eller /slutt avsluttar.\n")

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
                    print(f"— Pakke {data.get('packet_id')}: {data.get('status')} —")
                elif event.get("type") == "status":
                    print(f"— Samband: {event['data'].get('state')} —")
        except (OSError, ValueError):
            if not stop.is_set():
                print("— Overvakingssambandet blei brote —")

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
                if command == "/hjelp":
                    print("/hjelp  /status  /nodar  /slutt")
                    continue
                if command == "/status":
                    _print_status(_request(settings, {"command": "status"})["data"])
                    continue
                if command == "/nodar":
                    _print_nodes(_request(settings, {"command": "nodes"})["data"])
                    continue
                payload = (
                    {"command": "send_public", "text": command}
                    if normalized == "public"
                    else {"command": "send_dm", "node_id": normalized, "text": command}
                )
                try:
                    _request(settings, payload)
                except CLIError as exc:
                    print(f"Feil: {exc}")
    finally:
        stop.set()
        with suppress(OSError):
            sock.shutdown(socket.SHUT_RDWR)
        stream.close()
        sock.close()
        receiver.join(timeout=2)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="meshpi",
        description="Meshtastic-chat for terminalen",
    )
    parser.add_argument(
        "--version",
        action="version",
        version=f"MeshPi {__version__}",
    )
    parser.add_argument(
        "--env-file",
        default=".env",
        help="sti til miljøfil (standard: .env)",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="skriv maskinlesbar JSON for ikkje-interaktive kommandoar",
    )
    sub = parser.add_subparsers(dest="command")
    parser.set_defaults(command="tui")
    sub.add_parser("tui", help="start fullskjerms terminalgrensesnitt")
    sub.add_parser("new", help="oppdag, vel eller legg til ei tilkopling")
    connect = sub.add_parser("connect", help="byt til TCP- eller serielltilkopling")
    connect.add_argument("target", help="IP, vert[:port], /dev/sti eller COM-port")
    connect.add_argument("--name", help="namn på den lagra profilen")
    sub.add_parser("connections", help="vis lagra tilkoplingar")
    daemon = sub.add_parser("daemon", help="køyr bakgrunnstenesta i framgrunnen")
    daemon.add_argument("--parent-pid", type=int, help=argparse.SUPPRESS)
    doctor = sub.add_parser("doctor", help="køyr ein offline sjølvtest")
    doctor.add_argument(
        "--offline",
        action="store_true",
        help="ikkje krev ein tilgjengeleg Meshtastic-node",
    )
    service = sub.add_parser("service", help="styr bakgrunnstenesta")
    service.add_argument(
        "action",
        choices=("status", "start", "stop", "enable", "disable"),
    )
    sub.add_parser("status", help="vis tilkoplingsstatus")

    nodes = sub.add_parser("nodes", help="vis kjende nodar")
    nodes.add_argument("--search", default="", help="søk på namn eller node-ID")
    nodes.add_argument(
        "--sort",
        choices=("name", "seen", "id"),
        default="seen",
        help="sorter nodelista",
    )
    node = sub.add_parser("node", help="vis alle detaljar om éin node")
    node.add_argument("node_id")
    sub.add_parser("conversations", help="vis samtalar og uleste meldingar")
    delete_messages = sub.add_parser(
        "delete-messages",
        help="slett lagra meldingar frå public, DM eller begge",
    )
    delete_messages.add_argument("scope", choices=("public", "dm", "all"))
    delete_messages.add_argument(
        "--yes",
        action="store_true",
        help="stadfest slettinga utan interaktivt spørsmål",
    )

    public = sub.add_parser("public", help="vis meldingar frå public kanal 0")
    public.add_argument("--limit", type=int, default=100)
    dm = sub.add_parser("dm", help="vis DM-samtale")
    dm.add_argument("node_id")
    dm.add_argument("--limit", type=int, default=100)

    send_public = sub.add_parser("send-public", help="send til public kanal 0")
    send_public.add_argument("text")
    send_dm = sub.add_parser("send-dm", help="send direkte melding")
    send_dm.add_argument("node_id")
    send_dm.add_argument("text")

    watch = sub.add_parser("watch", help="følg nye meldingar")
    watch.add_argument("conversation", nargs="?", default="all", help="all, public eller node-ID")
    chat = sub.add_parser("chat", help="start interaktiv chat")
    chat.add_argument("conversation", help="public eller node-ID")
    chat.add_argument("--limit", type=int, default=50)
    return parser


def run(args: argparse.Namespace, settings: Settings) -> str | None:
    command = args.command
    if command == "tui":
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
            print(f"{'OK' if ok else 'FEIL':4}  {name:18} {detail}")
            failed = failed or not ok
        if failed:
            raise RuntimeError("Sjølvtesten fann feil")
    elif command == "service":
        data = manage_service(args.action, settings, args.env_file)
        print(json.dumps(data, ensure_ascii=False)) if args.json else _print_service(
            data, args.action
        )
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
    elif command == "delete-messages":
        if not args.yes:
            if args.json:
                raise ValueError("Bruk --yes saman med --json for å stadfeste slettinga")
            labels = {
                "public": "alle meldingar frå public kanal 0",
                "dm": "alle DM-meldingar",
                "all": "alle meldingar frå public kanal 0 og DM",
            }
            answer = input(
                f"Dette slettar {labels[args.scope]} permanent. Skriv SLETT for å halde fram: "
            )
            if answer.strip() != "SLETT":
                print("Ingen meldingar blei sletta.")
                return None
        data = _request(
            settings,
            {"command": "delete_messages", "scope": args.scope},
        )["data"]
        if args.json:
            print(json.dumps(data, ensure_ascii=False))
        else:
            print(f"Sletta {data['deleted']} meldingar.")
    elif command in {"public", "dm"}:
        conversation = "public" if command == "public" else normalize_node_id(args.node_id)
        data = _request(
            settings,
            {
                "command": "messages",
                "conversation": conversation,
                "limit": args.limit,
                "mark_read": not args.json,
            },
        )["data"]
        print(json.dumps(data, ensure_ascii=False)) if args.json else _print_messages(data)
    elif command == "send-public":
        message = _request(
            settings, {"command": "send_public", "text": args.text}
        )["data"]
        if args.json:
            print(json.dumps(message, ensure_ascii=False))
        else:
            print(f"Sendt som pakke {message.get('packet_id') or 'utan kjend ID'}.")
    elif command == "send-dm":
        message = _request(
            settings,
            {"command": "send_dm", "node_id": args.node_id, "text": args.text},
        )["data"]
        if args.json:
            print(json.dumps(message, ensure_ascii=False))
        else:
            print(f"Sendt som pakke {message.get('packet_id') or 'utan kjend ID'}.")
    elif command == "watch":
        conversation = args.conversation
        if conversation not in {"all", "public"}:
            conversation = normalize_node_id(conversation)
        _watch(settings, conversation, raw_json=args.json)
    elif command == "chat":
        _chat(settings, args.conversation, args.limit)


def main(argv: list[str] | None = None) -> None:
    raw_argv = _normalize_argv(list(sys.argv[1:] if argv is None else argv))
    parser = build_parser()
    args = parser.parse_args(raw_argv)
    try:
        settings = Settings.load(args.env_file)
        handle = DaemonHandle()
        needs_daemon = args.command not in {"daemon", "doctor", "service"}
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
        print(f"Feil: {exc}", file=sys.stderr)
        raise SystemExit(EXIT_ERROR) from exc
