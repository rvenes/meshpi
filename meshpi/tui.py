from __future__ import annotations

import json
import socket
import threading
import time
from collections.abc import Callable
from contextlib import suppress
from datetime import datetime
from typing import Any, BinaryIO

from rich.text import Text
from textual import on
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Container, Horizontal, Vertical
from textual.css.query import NoMatches
from textual.events import Resize
from textual.message import Message as TextualMessage
from textual.screen import ModalScreen
from textual.widgets import Input, Label, ListItem, ListView, RichLog, Static

from meshpi.client import CLIError, open_watch, request
from meshpi.config import Settings
from meshpi.models import normalize_node_id, validate_message_text

Requester = Callable[[Settings, dict[str, Any]], dict[str, Any]]
Watcher = Callable[[Settings, str], tuple[socket.socket, BinaryIO]]


def _time(value: str | int | None, seconds: bool = False) -> str:
    if value is None:
        return "–"
    try:
        if isinstance(value, int):
            parsed = datetime.fromtimestamp(value).astimezone()
        else:
            parsed = datetime.fromisoformat(value).astimezone()
        return parsed.strftime("%H:%M:%S" if seconds else "%H:%M")
    except (ValueError, TypeError, OSError):
        return str(value)


def _battery(value: Any) -> str:
    if value in (None, ""):
        return "–"
    if value in (0, 101, "0", "101"):
        return "Straum"
    return f"{value}%"


def _conversation_id(item: dict[str, Any]) -> str:
    return "public" if item.get("kind") == "public" else str(item["conversation"])


def _conversation_title(item: dict[str, Any]) -> str:
    if item.get("kind") == "public":
        return "Public – kanal 0"
    node_id = str(item.get("conversation", ""))
    name = item.get("long_name") or item.get("short_name") or node_id
    return f"DM {name} [{node_id[-4:]}]"


class ConversationItem(ListItem):
    def __init__(self, conversation: dict[str, Any]):
        self.conversation = conversation
        self.conversation_id = _conversation_id(conversation)
        super().__init__(Static(self._render_label(), classes="conversation-label"))

    def _render_label(self) -> Text:
        unread = int(self.conversation.get("unread") or 0)
        title = _conversation_title(self.conversation)
        text = Text()
        text.append("● " if self.conversation_id == "public" else "◆ ", style="green")
        text.append(title, style="bold")
        if unread:
            text.append(f"  {unread}", style="bold cyan")
        text.append("\n")
        last_time = _time(self.conversation.get("last_timestamp"))
        last_text = str(self.conversation.get("last_text") or "Ingen meldingar")
        if len(last_text) > 31:
            last_text = last_text[:30] + "…"
        text.append(f"  {last_time}  {last_text}", style="dim")
        return text


class NewDMScreen(ModalScreen[str | None]):
    BINDINGS = [Binding("escape", "cancel", "Avbryt")]

    def compose(self) -> ComposeResult:
        with Container(id="new-dm-dialog"):
            yield Label("Ny direkte samtale", id="new-dm-title")
            yield Label("Skriv full node-ID, med eller utan !")
            yield Input(placeholder="710365c8", id="new-dm-input")
            yield Static("Enter: opne   Esc: avbryt", id="new-dm-help")

    def on_mount(self) -> None:
        self.query_one("#new-dm-input", Input).focus()

    @on(Input.Submitted, "#new-dm-input")
    def submit_node(self, event: Input.Submitted) -> None:
        try:
            self.dismiss(normalize_node_id(event.value))
        except ValueError as exc:
            self.notify(str(exc), severity="error")

    def action_cancel(self) -> None:
        self.dismiss(None)


class LiveEvent(TextualMessage):
    def __init__(self, event: dict[str, Any]):
        self.event = event
        super().__init__()


class MeshPiTUI(App[None]):
    TITLE = "MeshPi"
    SUB_TITLE = "Meshtastic"
    ENABLE_COMMAND_PALETTE = False

    CSS = """
    $background: #090c0d;
    $panel: #0d1112;
    $border: #556064;
    $accent: #58d65c;
    $cyan: #65d6ee;

    Screen {
        background: $background;
        color: #d3d8da;
    }

    #status-bar {
        dock: top;
        height: 3;
        padding: 0 2;
        content-align: left middle;
        border: round $border;
        background: $panel;
    }

    #body {
        height: 1fr;
    }

    #conversation-panel {
        width: 34;
        min-width: 25;
        border: round $border;
        background: $panel;
    }

    #message-panel {
        width: 1fr;
        min-width: 42;
        border: round $border;
        background: $panel;
    }

    #node-panel {
        width: 36;
        min-width: 29;
        border: round $border;
        background: $panel;
    }

    .panel-title {
        height: 2;
        padding: 0 1;
        color: $accent;
        text-style: bold;
        content-align: left middle;
        border-bottom: solid #31393c;
    }

    #conversation-list {
        height: 1fr;
        background: $panel;
        border: none;
        padding: 0;
    }

    ConversationItem {
        height: 4;
        padding: 0 1;
        color: #cbd0d2;
    }

    ConversationItem.--highlight {
        background: #245c2a;
        color: white;
    }

    .conversation-label {
        width: 1fr;
        height: 3;
    }

    #message-log {
        height: 1fr;
        padding: 0 1;
        background: $panel;
        scrollbar-color: $accent;
        scrollbar-background: $panel;
    }

    #message-input {
        height: 3;
        margin: 0 1;
        border: round #536166;
        background: #0a0d0e;
    }

    #message-input:focus {
        border: round $accent;
    }

    #input-help {
        height: 1;
        padding: 0 2;
        color: #8d9699;
    }

    #node-details {
        height: 1fr;
        padding: 1 2;
        color: #c2c7c9;
    }

    #key-bar {
        dock: bottom;
        height: 2;
        padding: 0 2;
        content-align: left middle;
        border: round $border;
        background: $panel;
        color: #aeb5b7;
    }

    NewDMScreen {
        align: center middle;
        background: rgba(0, 0, 0, 0.72);
    }

    #new-dm-dialog {
        width: 54;
        height: 11;
        padding: 1 2;
        border: round $accent;
        background: $panel;
    }

    #new-dm-title {
        color: $accent;
        text-style: bold;
        margin-bottom: 1;
    }

    #new-dm-input {
        margin-top: 1;
    }

    #new-dm-help {
        margin-top: 1;
        color: #8d9699;
    }

    """

    BINDINGS = [
        Binding("tab", "next_conversation", "Neste samtale", priority=True),
        Binding("shift+tab", "previous_conversation", "Førre samtale", priority=True),
        Binding("ctrl+l", "focus_input", "Skriv melding"),
        Binding("ctrl+d", "new_dm", "Ny DM"),
        Binding("f2", "focus_conversations", "Samtalar"),
        Binding("ctrl+r", "refresh", "Oppdater"),
        Binding("ctrl+q", "quit", "Avslutt"),
    ]

    def __init__(
        self,
        settings: Settings,
        requester: Requester = request,
        watcher: Watcher | None = open_watch,
    ):
        super().__init__()
        self.settings = settings
        self.requester = requester
        self.watcher = watcher
        self.status_data: dict[str, Any] = {
            "state": "koplar til",
            "host": settings.meshtastic_host,
            "port": settings.meshtastic_port,
        }
        self.conversations: list[dict[str, Any]] = []
        self.nodes: dict[str, dict[str, Any]] = {}
        self.current_conversation = "public"
        self._watch_socket: socket.socket | None = None
        self._watch_stop = threading.Event()
        self._rebuilding_list = False

    def compose(self) -> ComposeResult:
        yield Static("", id="status-bar")
        with Horizontal(id="body"):
            with Vertical(id="conversation-panel"):
                yield Static("Samtalar", classes="panel-title")
                yield ListView(id="conversation-list")
            with Vertical(id="message-panel"):
                yield Static("Public – kanal 0", id="conversation-title", classes="panel-title")
                yield RichLog(
                    id="message-log",
                    wrap=True,
                    highlight=False,
                    markup=False,
                    auto_scroll=True,
                )
                yield Input(
                    placeholder="Skriv melding og trykk Enter",
                    id="message-input",
                )
                yield Static(
                    "Enter: send   Tab/Shift+Tab: samtale   Ctrl+D: ny DM",
                    id="input-help",
                )
            with Vertical(id="node-panel"):
                yield Static("Nodedetaljar", classes="panel-title")
                yield Static("Ingen node vald", id="node-details")
        yield Static(
            " Tab neste  Shift+Tab førre  Ctrl+L skriv  Ctrl+D ny DM  "
            "F2 samtalar  Ctrl+R oppdater  Ctrl+Q avslutt ",
            id="key-bar",
        )

    def on_mount(self) -> None:
        self._update_status_bar()
        self.run_worker(
            self._initial_worker,
            name="initial",
            group="initial",
            thread=True,
            exclusive=True,
            exit_on_error=False,
        )
        if self.watcher is not None:
            self.run_worker(
                self._watch_worker,
                name="events",
                group="events",
                thread=True,
                exclusive=True,
                exit_on_error=False,
            )
        self.set_interval(1, self._update_status_bar)
        self.set_interval(5, self._schedule_status_refresh)

    def on_resize(self, event: Resize) -> None:
        width = event.size.width
        node_panel = self.query_one("#node-panel", Vertical)
        conversation_panel = self.query_one("#conversation-panel", Vertical)
        input_help = self.query_one("#input-help", Static)
        node_panel.display = width >= 112
        conversation_panel.display = width >= 62
        conversation_panel.styles.width = 34 if width >= 125 else 31 if width >= 90 else 25
        input_help.display = width >= 76

    def on_unmount(self) -> None:
        self._watch_stop.set()
        if self._watch_socket is not None:
            with suppress(OSError):
                self._watch_socket.shutdown(socket.SHUT_RDWR)
            with suppress(OSError):
                self._watch_socket.close()

    def _call(self, payload: dict[str, Any]) -> dict[str, Any]:
        return self.requester(self.settings, payload)

    def _initial_worker(self) -> None:
        try:
            status = self._call({"command": "status"})["data"]
            conversations = self._call({"command": "conversations"})["data"]
            nodes = self._call({"command": "nodes", "sort": "seen"})["data"]
            self.call_from_thread(self._apply_initial, status, conversations, nodes)
        except Exception as exc:
            self.call_from_thread(
                self.notify,
                f"Klarte ikkje laste data: {exc}",
                severity="error",
                timeout=8,
            )

    async def _apply_initial(
        self,
        status: dict[str, Any],
        conversations: list[dict[str, Any]],
        nodes: list[dict[str, Any]],
    ) -> None:
        self.status_data = status
        self.nodes = {str(node["node_id"]): node for node in nodes}
        await self._apply_conversations(conversations)
        self._update_status_bar()
        self.select_conversation(self.current_conversation)

    def _schedule_status_refresh(self) -> None:
        self.run_worker(
            self._status_worker,
            name="status",
            group="status",
            thread=True,
            exclusive=True,
            exit_on_error=False,
        )

    def _status_worker(self) -> None:
        try:
            status = self._call({"command": "status"})["data"]
            self.call_from_thread(self._set_status, status)
        except Exception:
            self.call_from_thread(
                self._set_status,
                self.status_data | {"state": "fråkopla"},
            )

    def _set_status(self, status: dict[str, Any]) -> None:
        self.status_data = status
        self._update_status_bar()

    def _update_status_bar(self) -> None:
        if not self.is_mounted:
            return
        status = self.status_data
        state = str(status.get("state", "ukjend"))
        state_style = "bold green" if state == "tilkopla" else "bold yellow"
        local_id = str(status.get("local_node_id") or "–")
        local_node = self.nodes.get(local_id, {})
        local_name = local_node.get("short_name") or local_node.get("long_name") or local_id
        text = Text()
        text.append("meshpi", style="bold green")
        text.append("  │  ")
        text.append("● ", style="green" if state == "tilkopla" else "yellow")
        text.append(state.capitalize(), style=state_style)
        text.append("  │  Lokal node: ")
        text.append(f"{local_name} [{local_id[-4:]}]", style="green")
        text.append("  │  Node: ")
        text.append(
            f"{status.get('host', self.settings.meshtastic_host)}:"
            f"{status.get('port', self.settings.meshtastic_port)}",
            style="cyan",
        )
        text.append("  │  ")
        text.append(datetime.now().astimezone().strftime("%H:%M:%S"), style="cyan")
        try:
            self.query_one("#status-bar", Static).update(text)
        except NoMatches:
            # A timer may fire while Textual is dismantling the screen.
            return

    def _with_public(self, conversations: list[dict[str, Any]]) -> list[dict[str, Any]]:
        public = next(
            (item for item in conversations if item.get("kind") == "public"),
            {
                "conversation": "public",
                "kind": "public",
                "last_timestamp": None,
                "last_text": None,
                "unread": 0,
            },
        )
        direct = [item for item in conversations if item.get("kind") == "dm"]
        if self.current_conversation != "public" and not any(
            _conversation_id(item) == self.current_conversation for item in direct
        ):
            direct.insert(
                0,
                {
                    "conversation": self.current_conversation,
                    "kind": "dm",
                    "last_timestamp": None,
                    "last_text": None,
                    "unread": 0,
                },
            )
        return [public, *direct]

    async def _apply_conversations(self, conversations: list[dict[str, Any]]) -> None:
        self.conversations = self._with_public(conversations)
        list_view = self.query_one("#conversation-list", ListView)
        self._rebuilding_list = True
        await list_view.clear()
        await list_view.extend(ConversationItem(item) for item in self.conversations)
        ids = [_conversation_id(item) for item in self.conversations]
        list_view.index = (
            ids.index(self.current_conversation) if self.current_conversation in ids else 0
        )
        self._rebuilding_list = False

    @on(ListView.Highlighted, "#conversation-list")
    def conversation_highlighted(self, event: ListView.Highlighted) -> None:
        if self._rebuilding_list or not isinstance(event.item, ConversationItem):
            return
        self.select_conversation(event.item.conversation_id)

    @on(ListView.Selected, "#conversation-list")
    def conversation_selected(self, event: ListView.Selected) -> None:
        if isinstance(event.item, ConversationItem):
            self.select_conversation(event.item.conversation_id)
            self.query_one("#message-input", Input).focus()

    def select_conversation(self, conversation: str) -> None:
        self.current_conversation = conversation
        title = next(
            (
                _conversation_title(item)
                for item in self.conversations
                if _conversation_id(item) == conversation
            ),
            f"DM {conversation}",
        )
        self.query_one("#conversation-title", Static).update(title)
        self.run_worker(
            lambda: self._conversation_worker(conversation),
            name=f"conversation-{conversation}",
            group="conversation",
            thread=True,
            exclusive=True,
            exit_on_error=False,
        )

    def _conversation_worker(self, conversation: str) -> None:
        try:
            messages = self._call(
                {
                    "command": "messages",
                    "conversation": conversation,
                    "limit": 300,
                    "mark_read": True,
                }
            )["data"]
            node_id = conversation if conversation != "public" else self._latest_peer(messages)
            node = None
            if node_id and node_id not in {"!ffffffff", "^all"}:
                try:
                    node = self._call({"command": "node", "node_id": node_id})["data"]
                except (CLIError, ValueError):
                    node = self.nodes.get(node_id)
            self.call_from_thread(
                self._show_conversation,
                conversation,
                messages,
                node,
            )
        except Exception as exc:
            self.call_from_thread(
                self.notify,
                f"Klarte ikkje laste samtalen: {exc}",
                severity="error",
            )

    def _latest_peer(self, messages: list[dict[str, Any]]) -> str | None:
        local_id = self.status_data.get("local_node_id")
        for message in reversed(messages):
            node_id = message.get("from_node")
            if node_id and node_id != local_id:
                return str(node_id)
        return str(local_id) if local_id else None

    def _show_conversation(
        self,
        conversation: str,
        messages: list[dict[str, Any]],
        node: dict[str, Any] | None,
    ) -> None:
        if conversation != self.current_conversation:
            return
        log = self.query_one("#message-log", RichLog)
        log.clear()
        for message in messages:
            log.write(self._render_message(message), scroll_end=False)
        log.scroll_end(animate=False)
        self._show_node(node)
        for item in self.conversations:
            if _conversation_id(item) == conversation:
                item["unread"] = 0

    def _render_message(self, message: dict[str, Any]) -> Text:
        node_id = str(message.get("from_node") or "")
        node = self.nodes.get(node_id, {})
        name = (
            message.get("from_long_name")
            or message.get("from_short_name")
            or node.get("long_name")
            or node.get("short_name")
            or node_id
            or "Ukjend"
        )
        outgoing = message.get("direction") == "ut"
        transport = str(message.get("transport") or "Ukjend")
        text = Text()
        text.append(_time(message.get("timestamp")), style="cyan")
        text.append("  ")
        text.append(
            f"{name} [{node_id[-4:] if node_id else '????'}]",
            style="bold green" if outgoing else "bold bright_cyan",
        )
        text.append("  ")
        transport_style = (
            "bold green" if transport == "RF" else "bold magenta" if transport == "MQTT" else "dim"
        )
        text.append(transport, style=transport_style)
        details: list[str] = []
        if message.get("snr") is not None:
            details.append(f"SNR {message['snr']}")
        if message.get("rssi") is not None:
            details.append(f"RSSI {message['rssi']}")
        if message.get("hop_start") is not None or message.get("hop_limit") is not None:
            details.append(
                f"hopp {message.get('hop_start', '–')}/{message.get('hop_limit', '–')}"
            )
        if details:
            text.append("  " + "  ".join(details), style="dim")
        if outgoing:
            text.append(f"  [{message.get('status', 'sendt')}]", style="dim")
        text.append("\n  ")
        text.append(str(message.get("text") or ""))
        text.append("\n" + "─" * 72, style="#394245")
        return text

    def _show_node(self, node: dict[str, Any] | None) -> None:
        panel = self.query_one("#node-details", Static)
        if not node:
            panel.update("Ingen nodeinformasjon tilgjengeleg.")
            return
        battery = node.get("battery_level")
        bar = ""
        if isinstance(battery, int) and 0 < battery <= 100:
            filled = round(battery / 10)
            bar = "  " + "█" * filled + "░" * (10 - filled)
        can_dm = node.get("can_receive_dm")
        dm = "ja" if can_dm is True else "nei" if can_dm is False else "ukjend"
        rows = (
            ("Langt namn", node.get("long_name")),
            ("Kort namn", node.get("short_name")),
            ("Node-ID", node.get("node_id")),
            ("Maskinvare", node.get("hw_model")),
            ("Rolle", node.get("role")),
            ("Sist sett", _time(node.get("last_heard"), seconds=True)),
            ("Batteri", _battery(battery) + bar),
            ("Spenning", f"{node['voltage']} V" if node.get("voltage") else None),
            ("SNR", node.get("snr")),
            ("RSSI", node.get("rssi")),
            ("Hopp", node.get("hops_away")),
            ("Transport", node.get("transport")),
            ("Kan ta imot DM", dm),
        )
        text = Text()
        for label, value in rows:
            text.append(f"{label:16}", style="dim")
            text.append(f"{value if value not in (None, '') else '–'}\n")
        panel.update(text)

    @on(Input.Submitted, "#message-input")
    def message_submitted(self, event: Input.Submitted) -> None:
        try:
            text = validate_message_text(event.value)
        except ValueError as exc:
            self.notify(str(exc), severity="error")
            return
        conversation = self.current_conversation
        event.input.value = ""
        self.run_worker(
            lambda: self._send_worker(conversation, text),
            name="send",
            group="send",
            thread=True,
            exclusive=False,
            exit_on_error=False,
        )

    def _send_worker(self, conversation: str, text: str) -> None:
        payload = (
            {"command": "send_public", "text": text}
            if conversation == "public"
            else {"command": "send_dm", "node_id": conversation, "text": text}
        )
        try:
            self._call(payload)
        except Exception as exc:
            self.call_from_thread(
                self.notify,
                f"Sending feila: {exc}",
                severity="error",
                timeout=8,
            )

    def _watch_worker(self) -> None:
        while not self._watch_stop.is_set():
            try:
                assert self.watcher is not None
                sock, stream = self.watcher(self.settings, "all")
                self._watch_socket = sock
                for raw in stream:
                    if self._watch_stop.is_set():
                        return
                    event = json.loads(raw)
                    if event.get("type") != "heartbeat":
                        self.call_from_thread(self.post_message, LiveEvent(event))
            except (OSError, ValueError, CLIError):
                if not self._watch_stop.is_set():
                    self.call_from_thread(
                        self.post_message,
                        LiveEvent(
                            {
                                "type": "status",
                                "data": self.status_data | {"state": "fråkopla"},
                            }
                        ),
                    )
                    time.sleep(2)
            finally:
                self._watch_socket = None

    @on(LiveEvent)
    def live_event(self, message: LiveEvent) -> None:
        event = message.event
        event_type = event.get("type")
        if event_type == "status":
            self._set_status(event.get("data", {}))
            return
        if event_type == "message_status":
            self.select_conversation(self.current_conversation)
            return
        if event_type == "nodes":
            self.action_refresh()
            return
        if event_type != "message":
            return
        data = event.get("data", {})
        conversation = "public" if data.get("kind") == "public" else data.get("peer_node")
        if conversation == self.current_conversation:
            self.query_one("#message-log", RichLog).write(
                self._render_message(data),
                scroll_end=True,
            )
        elif data.get("kind") == "dm" and data.get("direction") == "inn":
            node_id = str(data.get("from_node") or "")
            node = self.nodes.get(node_id, {})
            name = node.get("long_name") or node.get("short_name") or node_id
            self.notify(f"Ny DM frå {name}: {data.get('text', '')}", timeout=6)
        self.run_worker(
            self._refresh_lists_worker,
            name="refresh-live",
            group="refresh",
            thread=True,
            exclusive=True,
            exit_on_error=False,
        )

    def _refresh_lists_worker(self) -> None:
        try:
            conversations = self._call({"command": "conversations"})["data"]
            nodes = self._call({"command": "nodes", "sort": "seen"})["data"]
            self.call_from_thread(self._apply_refresh, conversations, nodes)
        except Exception:
            return

    async def _apply_refresh(
        self,
        conversations: list[dict[str, Any]],
        nodes: list[dict[str, Any]],
    ) -> None:
        self.nodes = {str(node["node_id"]): node for node in nodes}
        await self._apply_conversations(conversations)
        self._update_status_bar()

    def action_next_conversation(self) -> None:
        self._move_conversation(1)

    def action_previous_conversation(self) -> None:
        self._move_conversation(-1)

    def _move_conversation(self, direction: int) -> None:
        list_view = self.query_one("#conversation-list", ListView)
        count = len(self.conversations)
        if not count:
            return
        current = list_view.index if list_view.index is not None else 0
        list_view.index = (current + direction) % count
        item = list_view.highlighted_child
        if isinstance(item, ConversationItem):
            self.select_conversation(item.conversation_id)

    def action_focus_input(self) -> None:
        self.query_one("#message-input", Input).focus()

    def action_focus_conversations(self) -> None:
        self.query_one("#conversation-list", ListView).focus()

    def action_new_dm(self) -> None:
        self.push_screen(NewDMScreen(), self._open_new_dm)

    def _open_new_dm(self, node_id: str | None) -> None:
        if node_id is None:
            return
        self.current_conversation = node_id
        self.run_worker(
            self._refresh_lists_worker,
            name="new-dm-refresh",
            group="refresh",
            thread=True,
            exclusive=True,
            exit_on_error=False,
        )
        self.select_conversation(node_id)
        self.query_one("#message-input", Input).focus()

    def action_refresh(self) -> None:
        self._schedule_status_refresh()
        self.run_worker(
            self._refresh_lists_worker,
            name="manual-refresh",
            group="refresh",
            thread=True,
            exclusive=True,
            exit_on_error=False,
        )
        self.select_conversation(self.current_conversation)


def run_tui(settings: Settings) -> None:
    MeshPiTUI(settings).run()
