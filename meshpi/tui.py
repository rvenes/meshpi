from __future__ import annotations

import json
import math
import socket
import threading
import time
from collections.abc import Callable
from contextlib import suppress
from datetime import datetime
from typing import Any, BinaryIO

from rich import box
from rich.panel import Panel
from rich.text import Text
from textual import events, on
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Container, Horizontal, Vertical
from textual.css.query import NoMatches
from textual.events import Resize
from textual.message import Message as TextualMessage
from textual.screen import ModalScreen
from textual.selection import Selection
from textual.strip import Strip
from textual.widgets import Button, Input, Label, ListItem, ListView, RichLog, Static

from meshpi import __version__
from meshpi.client import CLIError, open_watch, request
from meshpi.config import Settings
from meshpi.models import normalize_node_id, sanitize_terminal_text, validate_message_text
from meshpi.update import UpdateNotice, check_for_update

Requester = Callable[[Settings, dict[str, Any]], dict[str, Any]]
Watcher = Callable[[Settings, str], tuple[socket.socket, BinaryIO]]
UpdateChecker = Callable[[Settings], UpdateNotice | None]


class SelectableRichLog(RichLog):
    """Rich log with Textual's drag-to-select metadata and copy support."""

    def render_line(self, y: int) -> Strip:
        scroll_x, scroll_y = self.scroll_offset
        content_y = scroll_y + y
        strip = super().render_line(y)
        selection = self.text_selection
        if selection is not None:
            span = selection.get_span(content_y)
            if span is not None:
                start, end = span
                visible_start = max(0, start - scroll_x)
                visible_end = (
                    strip.cell_length if end == -1 else max(0, end - scroll_x)
                )
                visible_end = min(strip.cell_length, visible_end)
                if visible_start < visible_end:
                    selection_style = self.screen.get_component_rich_style(
                        "screen--selection"
                    )
                    strip = Strip.join(
                        [
                            strip.crop(0, visible_start),
                            strip.crop(visible_start, visible_end).apply_style(
                                selection_style
                            ),
                            strip.crop(visible_end),
                        ]
                    )
        return strip.apply_offsets(scroll_x, content_y)

    def get_selection(self, selection: Selection) -> tuple[str, str] | None:
        text = "\n".join(line.text.rstrip() for line in self.lines)
        return selection.extract(text), "\n"


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
    name = sanitize_terminal_text(item.get("long_name") or item.get("short_name") or node_id)
    return f"DM {name} [{node_id[-4:]}]"


class ConversationItem(ListItem):
    def __init__(self, conversation: dict[str, Any]):
        self.conversation = conversation
        self.conversation_id = _conversation_id(conversation)
        self.label_widget = Static(self._render_label(), classes="conversation-label")
        super().__init__(self.label_widget)

    def update_conversation(self, conversation: dict[str, Any]) -> None:
        self.conversation = conversation
        self.label_widget.update(self._render_label())

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
        last_text = sanitize_terminal_text(
            self.conversation.get("last_text") or "Ingen meldingar"
        )
        if len(last_text) > 31:
            last_text = last_text[:30] + "…"
        text.append(f"  {last_time}  {last_text}", style="dim")
        return text


def _node_sort_key(node: dict[str, Any]) -> tuple[str, str]:
    name = node.get("long_name") or node.get("short_name") or node.get("node_id") or ""
    return str(name).casefold(), str(node.get("node_id") or "")


def _node_sidebar_sort_key(node: dict[str, Any]) -> tuple[bool, float, str, str]:
    last_heard = node.get("last_heard")
    recency = 0.0
    try:
        if isinstance(last_heard, (int, float)):
            recency = float(last_heard)
        elif last_heard:
            recency = datetime.fromisoformat(str(last_heard)).timestamp()
    except (ValueError, TypeError, OSError):
        pass
    name, node_id = _node_sort_key(node)
    return not bool(node.get("is_local")), -recency, name, node_id


class NodePickerItem(ListItem):
    def __init__(self, node: dict[str, Any]):
        self.node = node
        self.node_id = str(node["node_id"])
        super().__init__(Static(self._render_label(), classes="node-picker-label"))

    def _render_label(self) -> Text:
        name = sanitize_terminal_text(
            self.node.get("long_name") or self.node.get("short_name") or "Ukjend node"
        )
        short_name = sanitize_terminal_text(self.node.get("short_name"))
        text = Text()
        text.append(str(name), style="bold")
        if short_name and short_name != name:
            text.append(f"  {short_name}", style="dim")
        text.append(f"  {self.node_id}", style="cyan")
        text.append("\n")
        details = [f"sist sett {_time(self.node.get('last_heard'))}"]
        if self.node.get("hops_away") is not None:
            details.append(f"hopp {self.node['hops_away']}")
        if self.node.get("battery_level") is not None:
            details.append(f"batteri {_battery(self.node['battery_level'])}")
        if self.node.get("transport") not in (None, "", "Ukjend"):
            details.append(str(self.node["transport"]))
        text.append("  " + "  •  ".join(details), style="dim")
        return text


class NodeActionRequested(TextualMessage):
    def __init__(self, node_id: str):
        self.node_id = node_id
        super().__init__()


class NodeSidebarItem(ListItem):
    def __init__(self, node: dict[str, Any]):
        self.node = node
        self.node_id = str(node["node_id"])
        self.label_widget = Static(self._render_label(), classes="node-sidebar-label")
        super().__init__(self.label_widget)

    def update_node(self, node: dict[str, Any]) -> None:
        self.node = node
        self.label_widget.update(self._render_label())

    def _render_label(self) -> Text:
        name = sanitize_terminal_text(
            self.node.get("long_name") or self.node.get("short_name") or "Ukjend node"
        )
        text = Text()
        text.append("◆ " if self.node.get("is_local") else "● ", style="green")
        text.append(str(name), style="bold")
        text.append(f" [{self.node_id[-4:]}]", style="cyan")
        text.append("\n  ")
        details = [f"sist {_time(self.node.get('last_heard'))}"]
        if self.node.get("hops_away") is not None:
            details.append(f"hopp {self.node['hops_away']}")
        if self.node.get("battery_level") is not None:
            details.append(_battery(self.node["battery_level"]))
        transport = self.node.get("transport")
        if transport not in (None, "", "Ukjend"):
            details.append(str(transport))
        text.append("  •  ".join(details), style="dim")
        return text

    def on_mouse_up(self, event: events.MouseUp) -> None:
        if event.button != 3:
            return
        event.stop()
        self.post_message(NodeActionRequested(self.node_id))


class NewDMScreen(ModalScreen[str | None]):
    BINDINGS = [
        Binding("escape", "cancel", "Avbryt", priority=True),
        Binding("down", "next_node", "Neste node", priority=True),
        Binding("up", "previous_node", "Førre node", priority=True),
    ]

    def __init__(self, nodes: list[dict[str, Any]]):
        super().__init__()
        self.all_nodes = sorted(
            (node for node in nodes if not node.get("is_local")),
            key=_node_sort_key,
        )
        self.filtered_nodes = list(self.all_nodes)

    def compose(self) -> ComposeResult:
        with Container(id="new-dm-dialog"):
            yield Label("Ny direkte samtale", id="new-dm-title")
            yield Input(
                placeholder="Søk på namn eller node-ID",
                id="new-dm-input",
            )
            yield Static("", id="new-dm-count")
            with ListView(id="node-picker-list"):
                yield from (NodePickerItem(node) for node in self.filtered_nodes)
            yield Static(
                "Skriv: søk   ↑/↓: vel   Enter: opne   Esc: avbryt",
                id="new-dm-help",
            )

    def on_mount(self) -> None:
        self._update_count()
        node_list = self.query_one("#node-picker-list", ListView)
        if self.filtered_nodes:
            node_list.index = 0
        self.query_one("#new-dm-input", Input).focus()

    def _update_count(self) -> None:
        total = len(self.all_nodes)
        shown = len(self.filtered_nodes)
        label = f"{shown} av {total} nodar" if shown != total else f"{total} nodar"
        if not shown:
            label += " – skriv full node-ID for ein ukjend node"
        self.query_one("#new-dm-count", Static).update(label)

    @on(Input.Changed, "#new-dm-input")
    async def filter_nodes(self, event: Input.Changed) -> None:
        query = event.value.strip().casefold().removeprefix("!")
        self.filtered_nodes = [
            node
            for node in self.all_nodes
            if not query
            or query
            in " ".join(
                str(node.get(field) or "")
                for field in ("long_name", "short_name", "node_id")
            )
            .casefold()
            .replace("!", "")
        ]
        node_list = self.query_one("#node-picker-list", ListView)
        await node_list.clear()
        await node_list.extend(NodePickerItem(node) for node in self.filtered_nodes)
        node_list.index = 0 if self.filtered_nodes else None
        self._update_count()

    @on(Input.Submitted, "#new-dm-input")
    def submit_node(self, event: Input.Submitted) -> None:
        selected = self.query_one("#node-picker-list", ListView).highlighted_child
        if isinstance(selected, NodePickerItem):
            self.dismiss(selected.node_id)
            return
        try:
            self.dismiss(normalize_node_id(event.value))
        except ValueError as exc:
            message = "Ingen nodar passar søket" if event.value.strip() else "Vel ein node"
            self.notify(f"{message}. {exc}", severity="error")

    @on(ListView.Selected, "#node-picker-list")
    def select_node(self, event: ListView.Selected) -> None:
        if isinstance(event.item, NodePickerItem):
            self.dismiss(event.item.node_id)

    def _move_node(self, direction: int) -> None:
        node_list = self.query_one("#node-picker-list", ListView)
        count = len(self.filtered_nodes)
        if not count:
            return
        current = node_list.index if node_list.index is not None else 0
        node_list.index = (current + direction) % count

    def action_next_node(self) -> None:
        self._move_node(1)

    def action_previous_node(self) -> None:
        self._move_node(-1)

    def action_cancel(self) -> None:
        self.dismiss(None)


class NodeActionScreen(ModalScreen[str | None]):
    BINDINGS = [
        Binding("up", "previous_choice", "Førre val", priority=True),
        Binding("down", "next_choice", "Neste val", priority=True),
        Binding("t", "traceroute", "Traceroute", priority=True),
        Binding("escape", "cancel", "Avbryt", priority=True),
    ]

    def __init__(self, node: dict[str, Any], availability: dict[str, Any]):
        super().__init__()
        self.node = node
        self.availability = availability
        cooldown = int(availability.get("cooldown_seconds") or 0)
        self._cooldown_deadline = time.monotonic() + cooldown
        self._blocked_reason = (
            str(availability.get("reason") or "")
            if not availability.get("available") and cooldown == 0
            else ""
        )

    def compose(self) -> ComposeResult:
        node_id = str(self.node.get("node_id") or "")
        name = sanitize_terminal_text(
            self.node.get("long_name") or self.node.get("short_name") or node_id
        )
        local = bool(self.node.get("is_local"))
        with Container(id="node-action-dialog"):
            yield Label("Handlingar for node", id="node-action-title")
            yield Static(f"{name}  [{node_id[-4:]}]", id="node-action-node")
            yield Button(
                "Opne samtale",
                id="node-action-open-dm",
                disabled=local,
            )
            yield Button(
                self._traceroute_label(),
                id="node-action-traceroute",
                disabled=local or not bool(self.availability.get("available")),
            )
            yield Button("Lukk", id="node-action-cancel")
            yield Static(
                "↑/↓: vel   Enter: køyr   T: traceroute   Esc: lukk",
                id="node-action-help",
            )

    def on_mount(self) -> None:
        target = "#node-action-cancel" if self.node.get("is_local") else "#node-action-open-dm"
        self.query_one(target, Button).focus()
        self.set_interval(1, self._update_traceroute_button)

    def _cooldown_remaining(self) -> int:
        return max(0, math.ceil(self._cooldown_deadline - time.monotonic()))

    def _traceroute_label(self) -> str:
        remaining = self._cooldown_remaining()
        suffix = f"  ·  vent {remaining} s" if remaining else ""
        return f"Traceroute  [T]{suffix}"

    def _update_traceroute_button(self) -> None:
        button = self.query_one("#node-action-traceroute", Button)
        button.label = self._traceroute_label()
        button.disabled = bool(
            self.node.get("is_local")
            or self._blocked_reason
            or self._cooldown_remaining()
        )

    @on(Button.Pressed)
    def choose(self, event: Button.Pressed) -> None:
        result = {
            "node-action-open-dm": "open_dm",
            "node-action-traceroute": "traceroute",
            "node-action-cancel": None,
        }.get(event.button.id)
        self.dismiss(result)

    def _move_choice(self, direction: int) -> None:
        buttons = [
            button
            for button in self.query("#node-action-dialog Button")
            if not button.disabled
        ]
        if not buttons:
            return
        focused = next((index for index, button in enumerate(buttons) if button.has_focus), 0)
        buttons[(focused + direction) % len(buttons)].focus()

    def action_next_choice(self) -> None:
        self._move_choice(1)

    def action_previous_choice(self) -> None:
        self._move_choice(-1)

    def action_traceroute(self) -> None:
        remaining = self._cooldown_remaining()
        if self.node.get("is_local"):
            reason = "Kan ikkje køyre traceroute til den lokale noden"
        elif remaining:
            reason = f"Traceroute kan sendast igjen om {remaining} sekund"
        else:
            reason = self._blocked_reason
        if reason:
            self.notify(reason, severity="warning")
            return
        self.dismiss("traceroute")

    def action_cancel(self) -> None:
        self.dismiss(None)


class QuitScreen(ModalScreen[str | None]):
    BINDINGS = [
        Binding("up", "previous_choice", "Førre val", priority=True),
        Binding("left", "previous_choice", "Førre val", priority=True),
        Binding("down", "next_choice", "Neste val", priority=True),
        Binding("right", "next_choice", "Neste val", priority=True),
        Binding("escape", "cancel", "Avbryt", priority=True),
    ]

    def __init__(self, background_mode: str):
        super().__init__()
        self.background_mode = background_mode

    def compose(self) -> ComposeResult:
        with Container(id="quit-dialog"):
            yield Label("Avslutt MeshPi", id="quit-title")
            yield Static(
                "Vil du berre lukke terminalgrensesnittet, eller stoppe "
                "bakgrunnstenesta òg?",
                id="quit-text",
            )
            yield Button("Lukk appen – tenesta held fram", id="quit-leave")
            yield Button("Lukk appen og stopp tenesta", id="quit-stop")
            yield Button("Avbryt", id="quit-cancel")
            yield Static("↑/↓: vel   Enter: stadfest   Esc: avbryt", id="quit-help")

    def on_mount(self) -> None:
        target = "#quit-stop" if self.background_mode == "session" else "#quit-leave"
        self.query_one(target, Button).focus()

    @on(Button.Pressed)
    def choose(self, event: Button.Pressed) -> None:
        result = {
            "quit-leave": "leave",
            "quit-stop": "stop",
            "quit-cancel": None,
        }.get(event.button.id)
        self.dismiss(result)

    def action_cancel(self) -> None:
        self.dismiss(None)

    def _move_choice(self, direction: int) -> None:
        buttons = list(self.query("#quit-dialog Button"))
        if not buttons:
            return
        focused = next((index for index, button in enumerate(buttons) if button.has_focus), 0)
        buttons[(focused + direction) % len(buttons)].focus()

    def action_next_choice(self) -> None:
        self._move_choice(1)

    def action_previous_choice(self) -> None:
        self._move_choice(-1)


class HelpScreen(ModalScreen[None]):
    BINDINGS = [
        Binding("f1", "close_help", "Lukk hjelp", priority=True),
        Binding("escape", "close_help", "Lukk hjelp", priority=True),
    ]

    SHORTCUTS = (
        ("F1", "Vis eller lukk denne oversikta"),
        ("Tab / Shift+Tab", "Flytt mellom samtalar, melding og nodar"),
        ("Enter", "Opne vald samtale/node eller send melding"),
        ("↑ / ↓", "Naviger i den aktive lista"),
        ("Ctrl+L", "Flytt markøren til meldingsfeltet"),
        ("Ctrl+D", "Finn ein node og start ein ny DM"),
        ("F2", "Flytt markøren til samtalelista"),
        ("F3", "Flytt markøren til nodelista"),
        ("Shift+F10", "Opne handlingar for markert node"),
        ("Delete", "Lukk vald DM utan å slette historikken"),
        ("Ctrl+R", "Oppdater status, samtalar og nodar"),
        ("Ctrl+U", "Kopier oppdateringskommandoen når ein ny versjon finst"),
        ("Ctrl+Q", "Avslutt MeshPi og vel kva som skjer med daemonen"),
        ("Esc", "Lukk dialogen som er open"),
    )

    def compose(self) -> ComposeResult:
        with Container(id="help-dialog"):
            yield Label("Tastatursnarvegar", id="help-title")
            help_text = Text()
            for key, description in self.SHORTCUTS:
                help_text.append(f"{key:<18}", style="bold cyan")
                help_text.append(description + "\n")
            yield Static(help_text, id="help-shortcuts")
            yield Static("Trykk F1 eller Esc for å lukke", id="help-close")

    def action_close_help(self) -> None:
        self.dismiss(None)


class LiveEvent(TextualMessage):
    def __init__(self, event: dict[str, Any]):
        self.event = event
        super().__init__()


class MeshPiTUI(App[str | None]):
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
        height: 17;
        min-height: 12;
        padding: 1 2;
        color: #c2c7c9;
        border-bottom: solid #31393c;
        overflow-y: auto;
    }

    #node-list-title {
        height: 2;
    }

    #node-list {
        height: 1fr;
        background: $panel;
        border: none;
        padding: 0;
        scrollbar-color: $accent;
        scrollbar-background: $panel;
    }

    NodeSidebarItem {
        height: 3;
        padding: 0 1;
        color: #cbd0d2;
    }

    NodeSidebarItem.--highlight {
        background: #245c2a;
        color: white;
    }

    .node-sidebar-label {
        width: 1fr;
        height: 2;
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
        width: 78;
        max-width: 96%;
        height: 32;
        max-height: 90%;
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
        margin-top: 0;
    }

    #new-dm-count {
        height: 1;
        margin: 0 1;
        color: #8d9699;
    }

    #node-picker-list {
        height: 1fr;
        margin: 1 0;
        border: round #394245;
        background: $panel;
    }

    NodePickerItem {
        height: 3;
        padding: 0 1;
    }

    NodePickerItem.--highlight {
        background: #245c2a;
        color: white;
    }

    .node-picker-label {
        width: 1fr;
        height: 2;
    }

    #new-dm-help {
        height: 1;
        color: #8d9699;
    }

    NodeActionScreen {
        align: center middle;
        background: rgba(0, 0, 0, 0.72);
    }

    #node-action-dialog {
        width: 58;
        max-width: 94%;
        height: 25;
        padding: 1 2;
        border: round $accent;
        background: $panel;
    }

    #node-action-title {
        height: 2;
        color: $accent;
        text-style: bold;
    }

    #node-action-node {
        height: 3;
        color: $cyan;
    }

    #node-action-dialog Button {
        width: 1fr;
        margin: 0 0 1 0;
    }

    #node-action-help {
        height: 2;
        color: #8d9699;
        content-align: center middle;
    }

    QuitScreen {
        align: center middle;
        background: rgba(0, 0, 0, 0.72);
    }

    #quit-dialog {
        width: 58;
        max-width: 94%;
        height: 23;
        padding: 1 2;
        border: round $accent;
        background: $panel;
    }

    #quit-title {
        height: 2;
        color: $accent;
        text-style: bold;
    }

    #quit-text {
        height: 4;
        color: #cbd0d2;
    }

    #quit-dialog Button {
        width: 1fr;
        margin: 0 0 1 0;
    }

    #quit-help {
        height: 2;
        color: #8d9699;
        content-align: center middle;
    }

    HelpScreen {
        align: center middle;
        background: rgba(0, 0, 0, 0.72);
    }

    #help-dialog {
        width: 76;
        max-width: 96%;
        height: 31;
        max-height: 94%;
        padding: 1 2;
        border: round $accent;
        background: $panel;
    }

    #help-title {
        height: 2;
        color: $accent;
        text-style: bold;
    }

    #help-shortcuts {
        height: 1fr;
        padding: 1 0;
        color: #cbd0d2;
    }

    #help-close {
        height: 1;
        color: #8d9699;
        text-align: center;
    }

    """

    BINDINGS = [
        Binding("f1", "show_help", "Hjelp", priority=True),
        Binding("tab", "focus_next_pane", "Neste felt", priority=True),
        Binding("shift+tab", "focus_previous_pane", "Førre felt", priority=True),
        Binding("ctrl+l", "focus_input", "Skriv melding"),
        Binding("ctrl+d", "new_dm", "Ny DM"),
        Binding("f2", "focus_conversations", "Samtalar"),
        Binding("f3", "focus_nodes", "Nodar"),
        Binding("shift+f10", "node_actions", "Nodehandlingar", priority=True),
        Binding("delete", "archive_conversation", "Lukk DM"),
        Binding("ctrl+r", "refresh", "Oppdater"),
        Binding("ctrl+u", "copy_update_command", "Kopier oppdatering", priority=True),
        Binding("ctrl+q", "quit", "Avslutt"),
    ]

    def __init__(
        self,
        settings: Settings,
        requester: Requester = request,
        watcher: Watcher | None = open_watch,
        update_checker: UpdateChecker | None = check_for_update,
    ):
        super().__init__()
        self.settings = settings
        self.requester = requester
        self.watcher = watcher
        self.update_checker = update_checker
        self.status_data: dict[str, Any] = {
            "state": "koplar til" if settings.meshtastic_host else "ingen node",
            "transport": "tcp" if settings.meshtastic_host else None,
            "endpoint": (
                f"{settings.meshtastic_host}:{settings.meshtastic_port}"
                if settings.meshtastic_host
                else None
            ),
            "host": settings.meshtastic_host or None,
            "port": settings.meshtastic_port if settings.meshtastic_host else None,
        }
        self.conversations: list[dict[str, Any]] = []
        self.nodes: dict[str, dict[str, Any]] = {}
        self.current_conversation = "public"
        self._watch_socket: socket.socket | None = None
        self._watch_stop = threading.Event()
        self._rebuilding_list = False
        self._rebuilding_nodes = False
        self.selected_node_id: str | None = None
        self._right_click_node_id: str | None = None
        self.node_action_entries: dict[str, dict[str, Any]] = {}
        self.update_notice: UpdateNotice | None = None

    def compose(self) -> ComposeResult:
        yield Static("", id="status-bar")
        with Horizontal(id="body"):
            with Vertical(id="conversation-panel"):
                yield Static("Samtalar", classes="panel-title")
                yield ListView(id="conversation-list")
            with Vertical(id="message-panel"):
                yield Static("Public – kanal 0", id="conversation-title", classes="panel-title")
                yield SelectableRichLog(
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
                    "Enter: send   Tab/Shift+Tab: neste/førre felt   Ctrl+D: ny DM",
                    id="input-help",
                )
            with Vertical(id="node-panel"):
                yield Static("Nodedetaljar", classes="panel-title")
                yield Static("Ingen node vald", id="node-details")
                yield Static("Nodar", id="node-list-title", classes="panel-title")
                yield ListView(id="node-list")
        yield Static(
            " F1 hjelp  Tab/Shift+Tab byter felt  Enter opnar  Del lukk DM  Ctrl+D ny DM  "
            "F2 samtalar  F3 nodar  Shift+F10 nodehandlingar  Ctrl+R oppdater  "
            "Ctrl+U kopier oppdatering  "
            "Ctrl+Q avslutt ",
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
        if self.update_checker is not None:
            self.run_worker(
                self._update_worker,
                name="update-check",
                group="update",
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
        watch_socket = self._watch_socket
        self._watch_socket = None
        if watch_socket is not None:
            with suppress(OSError):
                watch_socket.shutdown(socket.SHUT_RDWR)
            with suppress(OSError):
                watch_socket.close()

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
        await self._apply_nodes(nodes)
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

    def _update_worker(self) -> None:
        try:
            notice = self.update_checker(self.settings) if self.update_checker else None
        except Exception:
            return
        if notice is not None:
            self.call_from_thread(self._set_update_notice, notice)

    def _set_update_notice(self, notice: UpdateNotice) -> None:
        self.update_notice = notice
        if self.is_mounted:
            self.query_one("#message-log", RichLog).write(
                self._render_update_notice(notice),
                scroll_end=True,
            )

    @staticmethod
    def _render_update_notice(notice: UpdateNotice) -> Text:
        text = Text()
        text.append("⬆ MeshPi-oppdatering tilgjengeleg", style="bold yellow")
        text.append(
            f"  {notice.current_version} → {notice.latest_version}\n",
            style="yellow",
        )
        text.append("Køyr i terminalen – ikkje send som melding:\n", style="dim")
        text.append(notice.command, style="bold cyan")
        text.append("\nCtrl+U: kopier kommandoen", style="dim")
        text.append("\n" + "─" * 72, style="#725f24")
        return text

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
        text.append(f"MeshPi {__version__}", style="bold green")
        text.append("  │  ")
        text.append("● ", style="green" if state == "tilkopla" else "yellow")
        text.append(state.capitalize(), style=state_style)
        text.append("  │  Lokal node: ")
        text.append(f"{local_name} [{local_id[-4:]}]", style="green")
        transport = str(status.get("transport") or "").upper()
        endpoint = str(status.get("endpoint") or "")
        if len(endpoint) > 46:
            endpoint = "…" + endpoint[-45:]
        if endpoint:
            text.append(f"  │  {transport}: ")
            text.append(endpoint, style="cyan")
        else:
            text.append("  │  Ingen Meshtastic-node vald", style="yellow")
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
        incoming = self._with_public(conversations)
        list_view = self.query_one("#conversation-list", ListView)
        existing = [
            item for item in list_view.children if isinstance(item, ConversationItem)
        ]
        existing_ids = [item.conversation_id for item in existing]
        incoming_by_id = {_conversation_id(item): item for item in incoming}
        incoming_ids = list(incoming_by_id)

        if existing and set(existing_ids) == set(incoming_ids):
            self.conversations = [incoming_by_id[item_id] for item_id in existing_ids]
            for item in existing:
                item.update_conversation(incoming_by_id[item.conversation_id])
            return

        self.conversations = incoming
        self._rebuilding_list = True
        await list_view.clear()
        await list_view.extend(ConversationItem(item) for item in self.conversations)
        ids = [_conversation_id(item) for item in self.conversations]
        list_view.index = (
            ids.index(self.current_conversation) if self.current_conversation in ids else 0
        )
        self._rebuilding_list = False

    async def _apply_nodes(self, nodes: list[dict[str, Any]]) -> None:
        self.nodes = {str(node["node_id"]): node for node in nodes}
        ordered = sorted(self.nodes.values(), key=_node_sidebar_sort_key)
        preferred = self.selected_node_id
        if preferred not in self.nodes:
            preferred = (
                self.current_conversation
                if self.current_conversation != "public"
                else str(self.status_data.get("local_node_id") or "")
            )
        node_list = self.query_one("#node-list", ListView)
        existing = [
            item for item in node_list.children if isinstance(item, NodeSidebarItem)
        ]
        existing_ids = [item.node_id for item in existing]
        incoming_ids = list(self.nodes)
        existing_local = next(
            (item.node_id for item in existing if item.node.get("is_local")),
            None,
        )
        incoming_local = next(
            (
                str(node["node_id"])
                for node in self.nodes.values()
                if node.get("is_local")
            ),
            None,
        )

        if (
            existing
            and set(existing_ids) == set(incoming_ids)
            and existing_local == incoming_local
        ):
            for item in existing:
                item.update_node(self.nodes[item.node_id])
            if self.selected_node_id in self.nodes:
                self._show_node(self.nodes[self.selected_node_id])
            self.query_one("#node-list-title", Static).update(
                f"Nodar · {len(self.nodes)}"
            )
            return

        self._rebuilding_nodes = True
        await node_list.clear()
        await node_list.extend(NodeSidebarItem(node) for node in ordered)
        ids = [str(node["node_id"]) for node in ordered]
        node_list.index = ids.index(preferred) if preferred in ids else (0 if ids else None)
        self.selected_node_id = ids[node_list.index] if node_list.index is not None else None
        self.query_one("#node-list-title", Static).update(f"Nodar · {len(ordered)}")
        self._rebuilding_nodes = False

    @on(ListView.Highlighted, "#conversation-list")
    def conversation_highlighted(self, event: ListView.Highlighted) -> None:
        del event

    @on(ListView.Selected, "#conversation-list")
    def conversation_selected(self, event: ListView.Selected) -> None:
        if isinstance(event.item, ConversationItem):
            self.select_conversation(event.item.conversation_id)

    @on(ListView.Highlighted, "#node-list")
    def node_highlighted(self, event: ListView.Highlighted) -> None:
        if self._rebuilding_nodes or not isinstance(event.item, NodeSidebarItem):
            return
        self.selected_node_id = event.item.node_id
        self._show_node(event.item.node)

    @on(ListView.Selected, "#node-list")
    def node_selected(self, event: ListView.Selected) -> None:
        if not isinstance(event.item, NodeSidebarItem):
            return
        if self._right_click_node_id == event.item.node_id:
            self._right_click_node_id = None
            return
        if isinstance(self.screen, NodeActionScreen):
            return
        if event.item.node.get("is_local"):
            self.notify("Dette er den lokale noden", timeout=3)
            return
        self._open_node_dm(event.item.node_id, focus_input=False)

    @on(NodeActionRequested)
    def node_action_requested(self, message: NodeActionRequested) -> None:
        self._right_click_node_id = message.node_id
        self._select_sidebar_node(message.node_id)
        self.query_one("#node-list", ListView).focus()
        self._open_node_actions(message.node_id)

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
        timeline: list[tuple[str, int, str, dict[str, Any]]] = [
            (str(message.get("timestamp") or ""), 0, "message", message)
            for message in messages
        ]
        if conversation != "public":
            timeline.extend(
                (
                    str(action.get("started_at") or ""),
                    1,
                    "node_action",
                    action,
                )
                for action in self.node_action_entries.values()
                if action.get("node_id") == conversation
            )
        for _, _, entry_type, entry in sorted(
            timeline, key=lambda item: (item[0], item[1])
        ):
            renderable = (
                self._render_message(entry)
                if entry_type == "message"
                else self._render_node_action(entry)
            )
            log.write(renderable, scroll_end=False)
        if self.update_notice is not None:
            log.write(
                self._render_update_notice(self.update_notice),
                scroll_end=False,
            )
        log.scroll_end(animate=False)
        self._show_node(node)
        if node:
            self._select_sidebar_node(str(node.get("node_id") or ""))
        for item in self.conversations:
            if _conversation_id(item) == conversation:
                item["unread"] = 0

    def _render_message(self, message: dict[str, Any]) -> Text:
        node_id = str(message.get("from_node") or "")
        node = self.nodes.get(node_id, {})
        name = sanitize_terminal_text(
            message.get("from_long_name")
            or message.get("from_short_name")
            or node.get("long_name")
            or node.get("short_name")
            or node_id
            or "Ukjend"
        )
        outgoing = message.get("direction") == "ut"
        transport = str(message.get("transport") or "Ukjend")
        transport_label = "transport ukjend" if transport == "Ukjend" else transport
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
        text.append(transport_label, style=transport_style)
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
            status = str(message.get("status") or "sendt")
            if status == "stadfesta":
                status = "ACK"
            status_style = "bold green" if status == "levert" else "dim"
            text.append(f"  [{status}]", style=status_style)
        text.append("\n  ")
        text.append(sanitize_terminal_text(message.get("text") or ""))
        text.append("\n" + "─" * 72, style="#394245")
        return text

    def _node_action_label(self, node_id: str) -> str:
        node = self.nodes.get(node_id, {})
        name = sanitize_terminal_text(
            node.get("long_name") or node.get("short_name") or node_id or "Ukjend"
        )
        return f"{name} [{node_id[-4:] if node_id else '????'}]"

    def _append_traceroute_path(self, text: Text, title: str, path: Any) -> None:
        text.append(f"{title}:\n", style="bold cyan")
        if not isinstance(path, list) or not path:
            text.append("  Ikkje rapportert\n", style="dim")
            return
        for index, hop in enumerate(path):
            if not isinstance(hop, dict):
                continue
            if index:
                snr = hop.get("snr")
                snr_text = "ukjend SNR" if snr is None else f"SNR {snr:g} dB"
                text.append(f"  ↓ {snr_text}\n", style="dim")
            text.append(
                f"  {self._node_action_label(str(hop.get('node_id') or ''))}\n"
            )

    def _render_node_action(self, action: dict[str, Any]) -> Panel:
        status = str(action.get("status") or "started")
        target = self._node_action_label(str(action.get("node_id") or ""))
        text = Text()
        text.append(f"Mål: {target}\n")
        if status == "started":
            text.append("Førespurnaden er sendt. Ventar på svar.\n", style="yellow")
            text.append(
                "Du kan halda fram med å bruke MeshPi medan traceroute går.",
                style="dim",
            )
            title = "TRACEROUTE · VENTAR"
            border_style = "yellow"
        elif status == "failed":
            text.append(
                sanitize_terminal_text(action.get("error") or "Ukjend feil"),
                style="bold red",
            )
            title = "TRACEROUTE · FEILA"
            border_style = "red"
        else:
            result = action.get("result")
            if isinstance(result, dict):
                self._append_traceroute_path(text, "Fram", result.get("forward"))
                text.append("\n")
                self._append_traceroute_path(text, "Tilbake", result.get("return"))
            else:
                text.append("Resultatet manglar rutedata.", style="yellow")
            title = "TRACEROUTE · FERDIG"
            border_style = "cyan"
        return Panel(
            text,
            title=title,
            title_align="left",
            border_style=border_style,
            box=box.SQUARE,
            padding=(0, 1),
            expand=True,
        )

    def _show_node(self, node: dict[str, Any] | None) -> None:
        panel = self.query_one("#node-details", Static)
        if not node:
            panel.update("Ingen nodeinformasjon tilgjengeleg.")
            return
        battery = node.get("battery_level")
        bar = ""
        if isinstance(battery, int) and 0 < battery <= 100:
            filled = round(battery / 20)
            bar = "  " + "█" * filled + "░" * (5 - filled)
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
            rendered = sanitize_terminal_text(value) if value not in (None, "") else "–"
            text.append(f"{rendered}\n")
        panel.update(text)

    def _select_sidebar_node(self, node_id: str) -> None:
        if not node_id or node_id not in self.nodes:
            return
        node_list = self.query_one("#node-list", ListView)
        for index, item in enumerate(node_list.children):
            if isinstance(item, NodeSidebarItem) and item.node_id == node_id:
                self.selected_node_id = node_id
                node_list.index = index
                return

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
                if self.watcher is None:
                    return
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
            self.run_worker(
                self._refresh_lists_worker,
                name="refresh-nodes",
                group="refresh",
                thread=True,
                exclusive=True,
                exit_on_error=False,
            )
            return
        if event_type == "node_action":
            self._handle_node_action(event.get("data", {}))
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
        await self._apply_nodes(nodes)
        await self._apply_conversations(conversations)
        self._update_status_bar()

    def _focus_targets(self) -> list[ListView | Input]:
        targets: list[ListView | Input] = []
        if self.query_one("#conversation-panel", Vertical).display:
            targets.append(self.query_one("#conversation-list", ListView))
        targets.append(self.query_one("#message-input", Input))
        if self.query_one("#node-panel", Vertical).display:
            targets.append(self.query_one("#node-list", ListView))
        return targets

    def _move_focus(self, direction: int) -> None:
        targets = self._focus_targets()
        if not targets:
            return
        focused = self.focused
        current = targets.index(focused) if focused in targets else -1
        targets[(current + direction) % len(targets)].focus()

    def action_focus_next_pane(self) -> None:
        self._move_focus(1)

    def action_focus_previous_pane(self) -> None:
        self._move_focus(-1)

    def action_focus_input(self) -> None:
        self.query_one("#message-input", Input).focus()

    def action_focus_conversations(self) -> None:
        self.query_one("#conversation-list", ListView).focus()

    def action_focus_nodes(self) -> None:
        node_panel = self.query_one("#node-panel", Vertical)
        if not node_panel.display:
            self.action_new_dm()
            return
        self.query_one("#node-list", ListView).focus()

    def action_node_actions(self) -> None:
        node_list = self.query_one("#node-list", ListView)
        if not node_list.has_focus:
            self.notify("Trykk F3 og marker ein node først", timeout=3)
            return
        selected = node_list.highlighted_child
        if not isinstance(selected, NodeSidebarItem):
            self.notify("Ingen node er markert", timeout=3)
            return
        self._open_node_actions(selected.node_id)

    def _open_node_actions(self, node_id: str) -> None:
        node = self.nodes.get(node_id)
        if node is None:
            self.notify("Fann ikkje den markerte noden", severity="error")
            return
        self.run_worker(
            lambda: self._node_action_availability_worker(node_id),
            name="node-action-availability",
            group="node-action-menu",
            thread=True,
            exclusive=True,
            exit_on_error=False,
        )

    def _node_action_availability_worker(self, node_id: str) -> None:
        try:
            availability = self._call(
                {
                    "command": "node_action_availability",
                    "action": "traceroute",
                    "node_id": node_id,
                }
            )["data"]
        except Exception as exc:
            availability = {
                "available": False,
                "cooldown_seconds": 0,
                "reason": f"Klarte ikkje kontrollere traceroute: {exc}",
            }
        self.call_from_thread(
            self._show_node_action_screen,
            node_id,
            availability,
        )

    def _show_node_action_screen(
        self,
        node_id: str,
        availability: dict[str, Any],
    ) -> None:
        node = self.nodes.get(node_id)
        if node is None:
            return
        self.push_screen(
            NodeActionScreen(node, availability),
            lambda action: self._node_action_chosen(node_id, action),
        )

    def _node_action_chosen(self, node_id: str, action: str | None) -> None:
        if action == "open_dm":
            self._open_node_dm(node_id, focus_input=False)
        elif action == "traceroute":
            self._open_node_dm(node_id, focus_input=False)
            self.run_worker(
                lambda: self._start_node_action_worker(node_id, action),
                name=f"node-action-{action}",
                group="node-action",
                thread=True,
                exclusive=False,
                exit_on_error=False,
            )

    def _start_node_action_worker(self, node_id: str, action: str) -> None:
        try:
            data = self._call(
                {
                    "command": "node_action",
                    "action": action,
                    "node_id": node_id,
                }
            )["data"]
        except Exception as exc:
            self.call_from_thread(
                self.notify,
                f"Klarte ikkje starte traceroute: {exc}",
                severity="error",
                timeout=8,
            )
            return
        self.call_from_thread(self._handle_node_action, data)

    def _handle_node_action(self, action: dict[str, Any]) -> None:
        if action.get("action") != "traceroute":
            return
        action_id = str(action.get("action_id") or "")
        if not action_id:
            return
        status = str(action.get("status") or "started")
        previous = self.node_action_entries.get(action_id)
        rank = {"started": 0, "completed": 1, "failed": 1}
        if previous is not None:
            previous_status = str(previous.get("status") or "started")
            if rank.get(previous_status, 0) > rank.get(status, 0):
                return
            if previous == action:
                return
        self.node_action_entries[action_id] = dict(action)
        node_id = str(action.get("node_id") or "")
        if node_id == self.current_conversation:
            self.select_conversation(node_id)
        if status == "started":
            self.notify(
                f"Traceroute til {self._node_action_label(node_id)} er sendt",
                timeout=5,
            )
            return
        if status == "failed":
            self.notify(
                f"Traceroute feila: {action.get('error', 'ukjend feil')}",
                severity="error",
                timeout=10,
            )
            return
        if node_id != self.current_conversation:
            self.notify(
                f"Traceroute til {self._node_action_label(node_id)} er ferdig",
                timeout=7,
            )

    def action_new_dm(self) -> None:
        self.push_screen(NewDMScreen(list(self.nodes.values())), self._open_new_dm)

    def _open_new_dm(self, node_id: str | None) -> None:
        if node_id is None:
            return
        self._open_node_dm(node_id, focus_input=True)

    def _open_node_dm(self, node_id: str, *, focus_input: bool) -> None:
        self.current_conversation = node_id
        self.run_worker(
            lambda: self._unarchive_and_refresh_worker(node_id),
            name="new-dm-refresh",
            group="refresh",
            thread=True,
            exclusive=True,
            exit_on_error=False,
        )
        self.select_conversation(node_id)
        if focus_input:
            self.query_one("#message-input", Input).focus()

    def _unarchive_and_refresh_worker(self, node_id: str) -> None:
        with suppress(Exception):
            self._call({"command": "unarchive_conversation", "node_id": node_id})
        self._refresh_lists_worker()

    def action_archive_conversation(self) -> None:
        conversation_list = self.query_one("#conversation-list", ListView)
        if not conversation_list.has_focus:
            return
        selected = conversation_list.highlighted_child
        if not isinstance(selected, ConversationItem):
            return
        if selected.conversation_id == "public":
            self.notify("Public-kanalen kan ikkje lukkast", timeout=3)
            return
        node_id = selected.conversation_id
        self.run_worker(
            lambda: self._archive_conversation_worker(node_id),
            name=f"archive-{node_id}",
            group="archive",
            thread=True,
            exclusive=True,
            exit_on_error=False,
        )

    def _archive_conversation_worker(self, node_id: str) -> None:
        try:
            self._call({"command": "archive_conversation", "node_id": node_id})
            conversations = self._call({"command": "conversations"})["data"]
            self.call_from_thread(self._finish_archive, node_id, conversations)
        except Exception as exc:
            self.call_from_thread(
                self.notify,
                f"Klarte ikkje lukke samtalen: {exc}",
                severity="error",
            )

    async def _finish_archive(
        self,
        node_id: str,
        conversations: list[dict[str, Any]],
    ) -> None:
        if self.current_conversation == node_id:
            self.current_conversation = "public"
            self.select_conversation("public")
        await self._apply_conversations(conversations)
        self.query_one("#conversation-list", ListView).focus()
        self.notify("Samtalen er lukka. Ein ny DM opnar han att.", timeout=5)

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

    def action_copy_update_command(self) -> None:
        if self.update_notice is None:
            self.notify("Ingen oppdateringskommando er tilgjengeleg enno.")
            return
        self.copy_to_clipboard(self.update_notice.command)
        self.notify("Oppdateringskommandoen er kopiert.")

    def action_show_help(self) -> None:
        if isinstance(self.screen, HelpScreen):
            self.screen.dismiss(None)
        else:
            self.push_screen(HelpScreen())

    def action_quit(self) -> None:
        self.push_screen(QuitScreen(self.settings.background_mode), self._finish_quit)

    def _finish_quit(self, result: str | None) -> None:
        if result is not None:
            self.exit(result)


def run_tui(settings: Settings) -> str | None:
    return MeshPiTUI(settings).run()
