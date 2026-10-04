from __future__ import annotations

import json
import math
import os
import re
import socket
import threading
import time
from collections.abc import Callable
from contextlib import suppress
from datetime import datetime
from typing import Any
from urllib.parse import urlencode

from rich import box
from rich.panel import Panel
from rich.style import Style
from rich.table import Table
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
from textual.widgets import (
    Button,
    DataTable,
    Input,
    Label,
    ListItem,
    ListView,
    RichLog,
    Select,
    Static,
)

from meshpi import __version__
from meshpi.channels import dm_conversation_id, parse_dm_conversation_id
from meshpi.client import CLIError, WatchStream, open_watch, request
from meshpi.config import Settings
from meshpi.i18n import get_language, set_language, tr
from meshpi.models import normalize_node_id, sanitize_terminal_text, validate_message_text
from meshpi.update import UpdateNotice, check_for_update

Requester = Callable[[Settings, dict[str, Any]], dict[str, Any]]
Watcher = Callable[[Settings, str], tuple[socket.socket, WatchStream]]
UpdateChecker = Callable[[Settings], UpdateNotice | None]


def _t(key: str, **values: Any) -> str:
    return tr(f"tui.{key}", **values)


def _localize_bindings(node: Any, bindings: tuple[tuple[str, str, str, bool], ...]) -> None:
    """Rebind descriptions for the current language on this Textual instance."""
    for key, action, description_key, priority in bindings:
        node._bindings.bind(  # Textual has no public per-Screen rebinding API.
            key,
            action,
            _t(description_key),
            priority=priority,
        )
    node.refresh_bindings()


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


class MessageInput(Input):
    """Message input with shell-style history for the active conversation."""

    BINDINGS = [
        Binding("up", "history_previous", show=False),
        Binding("down", "history_next", show=False),
    ]

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self._message_history: list[str] = []
        self._history_index: int | None = None
        self._history_draft = ""

    def set_history(self, messages: list[str]) -> None:
        self._message_history = list(messages)
        self._history_index = None
        self._history_draft = self.value

    def action_history_previous(self) -> None:
        if not self._message_history:
            return
        if self._history_index is None:
            self._history_draft = self.value
            self._history_index = len(self._message_history) - 1
        elif self._history_index > 0:
            self._history_index -= 1
        self._show_history_entry()

    def action_history_next(self) -> None:
        if self._history_index is None:
            return
        if self._history_index < len(self._message_history) - 1:
            self._history_index += 1
            self._show_history_entry()
            return
        self._history_index = None
        self.value = self._history_draft
        self.cursor_position = len(self.value)

    def _show_history_entry(self) -> None:
        if self._history_index is None:
            return
        self.value = self._message_history[self._history_index]
        self.cursor_position = len(self.value)


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


def _date_time(
    value: str | int | None,
    seconds: bool = False,
    multiline: bool = False,
) -> str:
    if value is None:
        return "–"
    try:
        if isinstance(value, int):
            parsed = datetime.fromtimestamp(value).astimezone()
        else:
            parsed = datetime.fromisoformat(value).astimezone()
        time_format = "%H:%M:%S" if seconds else "%H:%M"
        separator = "\n" if multiline else " "
        return parsed.strftime(f"%d.%m.%y{separator}{time_format}")
    except (ValueError, TypeError, OSError):
        return str(value)


def _message_time_parts(
    value: str | int | None,
    now: datetime | None = None,
) -> tuple[str | None, str]:
    if value is None:
        return None, "–"
    try:
        if isinstance(value, int):
            parsed = datetime.fromtimestamp(value).astimezone()
        else:
            parsed = datetime.fromisoformat(value).astimezone()
        current = now.astimezone() if now is not None else datetime.now().astimezone()
        age = current.timestamp() - parsed.timestamp()
        if 0 <= age < 60:
            return None, _t("time.now")
        if 60 <= age < 3600:
            return None, _t("time.minutes", count=int(age // 60))
        if 3600 <= age < 6 * 3600:
            hours = int(age // 3600)
            return None, _t("time.hour" if hours == 1 else "time.hours", count=hours)
        if age >= 24 * 3600:
            return parsed.strftime("%d.%m.%y"), parsed.strftime("%H:%M")
        return None, _t("time.clock", time=parsed.strftime("%H:%M"))
    except (ValueError, TypeError, OSError, OverflowError):
        return None, str(value)


def _age_time(value: str | int | None, now: datetime | None = None) -> str:
    date, time = _message_time_parts(value, now=now)
    return f"{date} {time}" if date else time


def _node_transports(node: dict[str, Any]) -> set[str]:
    transports = {str(node.get("transport"))} & {"RF", "MQTT"}
    if node.get("seen_rf"):
        transports.add("RF")
    if node.get("seen_mqtt"):
        transports.add("MQTT")
    return transports


def _node_transport_label(node: dict[str, Any]) -> str:
    transports = _node_transports(node)
    return " + ".join(value for value in ("RF", "MQTT") if value in transports)


def _node_matches_transport(node: dict[str, Any], mode: str) -> bool:
    transports = _node_transports(node)
    return mode == "all" or transports == {
        "rf": {"RF"}, "mqtt": {"MQTT"}, "both": {"RF", "MQTT"},
    }.get(mode)


def _battery(value: Any) -> str:
    if value in (None, ""):
        return "–"
    if value in (0, 101, "0", "101"):
        return _t("value.external_power")
    return f"{value}%"


METRIC_PRESENTATION = {
    "battery_level": ("metric.battery", "%"),
    "voltage": ("metric.voltage", "V"),
    "channel_utilization": ("metric.channel_utilization", "%"),
    "air_util_tx": ("metric.air_util_tx", "%"),
    "uptime_seconds": ("metric.uptime", "s"),
    "temperature": ("metric.temperature", "°C"),
    "relative_humidity": ("metric.humidity", "%"),
    "barometric_pressure": ("metric.pressure", "hPa"),
    "gas_resistance": ("metric.gas_resistance", "MΩ"),
    "current": ("metric.current", "A"),
    "iaq": ("metric.iaq", ""),
    "distance": ("metric.distance", "mm"),
    "lux": ("metric.lux", "lx"),
    "white_lux": ("metric.white_lux", "lx"),
    "ir_lux": ("metric.ir_lux", "lx"),
    "uv_lux": ("metric.uv_lux", "lx"),
    "wind_direction": ("metric.wind_direction", "°"),
    "wind_speed": ("metric.wind_speed", "m/s"),
    "wind_gust": ("metric.wind_gust", "m/s"),
    "wind_lull": ("metric.wind_lull", "m/s"),
    "weight": ("metric.weight", "kg"),
    "rainfall_1h": ("metric.rainfall_1h", "mm"),
    "rainfall_24h": ("metric.rainfall_24h", "mm"),
    "soil_moisture": ("metric.soil_moisture", "%"),
    "soil_temperature": ("metric.soil_temperature", "°C"),
    "one_wire_temperature": ("metric.external_temperature", "°C"),
    "co2": ("CO₂", "ppm"),
}

TELEMETRY_KIND_LABELS = {
    "device": "telemetry.device",
    "environment": "telemetry.environment",
    "air_quality": "telemetry.air_quality",
    "power": "telemetry.power",
    "local_stats": "telemetry.local_stats",
    "health": "telemetry.health",
    "host": "telemetry.host",
    "traffic_management": "telemetry.traffic_management",
}

METRIC_TABLE_ORDER = (
    "air_util_tx",
    "battery_level",
    "channel_utilization",
    "uptime_seconds",
    "voltage",
    "temperature",
    "relative_humidity",
    "barometric_pressure",
    "gas_resistance",
    "iaq",
    "co2",
)

METRIC_TABLE_LABELS = {
    "air_util_tx": "metric.short.air_util_tx",
    "channel_utilization": "metric.short.channel_utilization",
    "relative_humidity": "metric.short.humidity",
    "barometric_pressure": "metric.short.pressure",
    "gas_resistance": "metric.short.gas_resistance",
    "uptime_seconds": "metric.uptime",
}


def _telemetry_kind_label(kind: str) -> str:
    key = TELEMETRY_KIND_LABELS.get(kind)
    return _t(key) if key else kind.replace("_", " ").capitalize()


def _metric_table_label(name: str) -> str:
    key = METRIC_TABLE_LABELS.get(name)
    if key:
        return _t(key)
    presentation = METRIC_PRESENTATION.get(name)
    if presentation:
        return _t(presentation[0])
    return name.replace("_", " ").capitalize()


def _canonical_metric_name(value: str) -> str:
    return re.sub(r"(?<!^)(?=[A-Z])", "_", value).replace("-", "_").lower()


def _metric_label_and_value(name: str, value: Any) -> tuple[str, str]:
    canonical = _canonical_metric_name(name)
    label_key, unit = METRIC_PRESENTATION.get(
        canonical,
        ("", ""),
    )
    label = _t(label_key) if label_key else canonical.replace("_", " ").capitalize()
    if canonical == "battery_level":
        return label, _battery(value)
    if canonical == "uptime_seconds":
        try:
            seconds = int(value)
        except (TypeError, ValueError):
            return label, str(value)
        days, remainder = divmod(seconds, 86_400)
        hours, remainder = divmod(remainder, 3_600)
        minutes = remainder // 60
        parts = []
        if days:
            parts.append(_t("duration.days", value=days))
        if hours or days:
            parts.append(_t("duration.hours", value=hours))
        parts.append(_t("duration.minutes", value=minutes))
        return label, " ".join(parts)
    rendered = (
        f"{value:.3f}".rstrip("0").rstrip(".")
        if isinstance(value, float)
        else str(value)
    )
    return label, f"{rendered} {unit}".rstrip()


def _google_maps_url(position: dict[str, Any]) -> str:
    latitude = float(position["latitude"])
    longitude = float(position["longitude"])
    query = urlencode({"api": "1", "query": f"{latitude:.7f},{longitude:.7f}"})
    return f"https://www.google.com/maps/search/?{query}"


def _map_link(url: str, label: str | None = None) -> Text:
    style = Style(
        color="cyan",
        underline=True,
        link=url,
        meta={"@click": f"screen.open_map({url!r})"},
    )
    return Text(label or url, style=style)


def _gateway_label(item: dict[str, Any]) -> str:
    gateway = str(item.get("gateway_node_id") or "")
    transport = str(item.get("transport") or "Ukjend")
    parts = []
    if gateway:
        parts.append(_t("value.via", gateway=gateway[-4:]))
    if transport != "Ukjend":
        parts.append(transport)
    return " · ".join(parts) or _t("value.unknown_route")


def _host_name() -> str:
    try:
        value = sanitize_terminal_text(socket.gethostname().strip())
    except OSError:
        value = ""
    return value[:32] or _t("value.unknown")


def _fit_status_text(value: str, width: int) -> str:
    if width <= 0:
        return ""
    if len(value) <= width:
        return value
    if width == 1:
        return "…"
    return value[: width - 1] + "…"


def _fit_status_endpoint(transport: str, endpoint: str, width: int) -> str:
    value = f"{transport}:{endpoint}" if transport else endpoint
    if len(value) <= width:
        return value
    prefix = f"{transport}:" if transport else ""
    if width <= len(prefix) + 1:
        return _fit_status_text(value, width)
    remaining = width - len(prefix) - 1
    return prefix + "…" + endpoint[-remaining:]


def _conversation_id(item: dict[str, Any]) -> str:
    return str(item["conversation"])


def _conversation_title(item: dict[str, Any]) -> str:
    if item.get("kind") == "public":
        channel = item.get("channel")
        name = sanitize_terminal_text(item.get("channel_name") or "")
        channel_key = str(item.get("channel_key") or "")
        if channel_key.startswith("legacy:"):
            scope = sanitize_terminal_text(channel_key.split(":", 2)[1], 40)
            return _t(
                "conversation.public_archive",
                scope=scope,
                channel=channel if channel is not None else "?",
            )
        if channel_key.startswith("provisional:"):
            return _t(
                "conversation.public_provisional",
                channel=channel if channel is not None else "?",
            )
        local_suffix = (
            f" [{str(item.get('local_node_id'))[-4:]}]"
            if channel_key.startswith("local:")
            and item.get("local_node_id")
            else ""
        )
        if name:
            return _t(
                "conversation.named_channel",
                name=name,
                channel=channel,
                local_suffix=local_suffix,
            )
        return _t(
            "conversation.public_channel",
            channel=channel if channel is not None else 0,
            local_suffix=local_suffix,
        )
    node_id = str(item.get("peer_node") or item.get("conversation", ""))
    name = sanitize_terminal_text(item.get("long_name") or item.get("short_name") or node_id)
    channel = item.get("channel")
    channel_name = sanitize_terminal_text(item.get("channel_name") or "")
    route_label = ""
    if channel is not None:
        route_label = (
            _t("conversation.route_named", name=channel_name, channel=channel)
            if channel_name
            else _t("conversation.route", channel=channel)
        )
    return _t("conversation.dm", name=name, node=node_id[-4:], route=route_label)


def _conversation_sidebar_title(item: dict[str, Any]) -> str:
    if item.get("kind") == "public":
        return _conversation_title(item)
    node_id = str(item.get("peer_node") or item.get("conversation", ""))
    name = sanitize_terminal_text(
        item.get("long_name") or item.get("short_name") or node_id
    )
    return f"{name} [{node_id[-4:]}]"


class ConversationItem(ListItem):
    def __init__(self, conversation: dict[str, Any]):
        self.conversation = conversation
        self.conversation_id = _conversation_id(conversation)
        self.label_widget = Static(self._render_label(), classes="conversation-label")
        super().__init__(self.label_widget)
        self._update_classes()

    def update_conversation(self, conversation: dict[str, Any]) -> None:
        self.conversation = conversation
        self._update_classes()
        self.label_widget.update(self._render_label())

    def _update_classes(self) -> None:
        self.set_class(
            bool(self.conversation.get("_section_label")),
            "conversation-section-start",
        )
        self.set_class(
            bool(int(self.conversation.get("unread") or 0)),
            "conversation-unread",
        )

    def _render_label(self) -> Text:
        unread = int(self.conversation.get("unread") or 0)
        title = _conversation_sidebar_title(self.conversation)
        text = Text()
        section_label = sanitize_terminal_text(
            self.conversation.get("_section_label") or ""
        )
        section_hint = sanitize_terminal_text(
            self.conversation.get("_section_hint") or ""
        )
        if section_label:
            text.append(section_label.upper(), style="bold #8da1aa")
            if section_hint:
                text.append(f"  {section_hint}", style="dim")
            text.append("\n")
        if self.conversation.get("kind") == "public":
            symbol = "● " if int(self.conversation.get("channel") or 0) == 0 else "○ "
        else:
            symbol = "◆ "
        emphasis = "bold cyan" if unread else None
        text.append(
            symbol,
            style="cyan" if unread else "green",
        )
        text.append(title, style=emphasis or "bold")
        if unread:
            text.append(f"  {unread}", style="bold cyan")
        text.append("\n")
        last_time = _age_time(self.conversation.get("last_timestamp"))
        last_text = sanitize_terminal_text(
            self.conversation.get("last_text") or _t("conversation.no_messages")
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
            self.node.get("long_name")
            or self.node.get("short_name")
            or _t("node.unknown")
        )
        short_name = sanitize_terminal_text(self.node.get("short_name"))
        text = Text()
        text.append(str(name), style="bold")
        if short_name and short_name != name:
            text.append(f"  {short_name}", style="dim")
        text.append(f"  {self.node_id}", style="cyan")
        text.append("\n")
        details = [_t("node.last_seen", time=_age_time(self.node.get("last_heard")))]
        if self.node.get("hops_away") is not None:
            details.append(_t("node.hops", value=self.node["hops_away"]))
        if self.node.get("battery_level") is not None:
            details.append(_t("node.battery", value=_battery(self.node["battery_level"])))
        if transport := _node_transport_label(self.node):
            details.append(transport)
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
            self.node.get("long_name")
            or self.node.get("short_name")
            or _t("node.unknown")
        )
        text = Text()
        text.append("◆ " if self.node.get("is_local") else "● ", style="green")
        text.append(str(name), style="bold")
        text.append(f" [{self.node_id[-4:]}]", style="cyan")
        text.append("\n  ")
        details = [_t("node.last", time=_age_time(self.node.get("last_heard")))]
        if self.node.get("hops_away") is not None:
            details.append(_t("node.hops", value=self.node["hops_away"]))
        if self.node.get("battery_level") is not None:
            details.append(_battery(self.node["battery_level"]))
        if transport := _node_transport_label(self.node):
            details.append(transport)
        text.append("  •  ".join(details), style="dim")
        return text

    def on_mouse_up(self, event: events.MouseUp) -> None:
        if event.button != 3:
            return
        event.stop()
        self.post_message(NodeActionRequested(self.node_id))


class NewDMScreen(ModalScreen[tuple[str, str] | None]):
    BINDINGS = [
        Binding("escape", "cancel", "", priority=True),
        Binding("down", "next_node", "", priority=True),
        Binding("up", "previous_node", "", priority=True),
    ]

    def __init__(
        self,
        nodes: list[dict[str, Any]],
        channels: list[dict[str, Any]],
    ):
        super().__init__()
        _localize_bindings(
            self,
            (
                ("escape", "cancel", "binding.cancel", True),
                ("down", "next_node", "binding.next_node", True),
                ("up", "previous_node", "binding.previous_node", True),
            ),
        )
        self.all_nodes = sorted(
            (node for node in nodes if not node.get("is_local")),
            key=_node_sort_key,
        )
        self.filtered_nodes = list(self.all_nodes)
        self.channels = sorted(
            (
                channel
                for channel in channels
                if channel.get("sendable") is True
                and channel.get("channel_key")
                and channel.get("local_node_id")
            ),
            key=lambda item: int(item.get("channel", 0)),
        )

    def compose(self) -> ComposeResult:
        options = [
            (
                (
                    f"{sanitize_terminal_text(channel.get('channel_name') or '')}"
                    f"{' · ' if channel.get('channel_name') else ''}"
                    + _t("conversation.channel", channel=channel.get("channel", 0))
                ),
                str(channel["channel_key"]),
            )
            for channel in self.channels
        ]
        primary = next(
            (
                str(channel["channel_key"])
                for channel in self.channels
                if int(channel.get("channel", -1)) == 0
            ),
            options[0][1] if options else "",
        )
        with Container(id="new-dm-dialog"):
            yield Label(_t("new_dm.title"), id="new-dm-title")
            yield Select(
                options,
                value=primary,
                allow_blank=False,
                id="new-dm-channel",
                prompt=_t("new_dm.select_channel"),
            )
            yield Input(
                placeholder=_t("new_dm.search_placeholder"),
                id="new-dm-input",
            )
            yield Static("", id="new-dm-count")
            with ListView(id="node-picker-list"):
                yield from (NodePickerItem(node) for node in self.filtered_nodes)
            yield Static(
                _t("new_dm.help"),
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
        label = (
            _t("new_dm.count_filtered", shown=shown, total=total)
            if shown != total
            else _t("new_dm.count", total=total)
        )
        if not shown:
            label += _t("new_dm.unknown_hint")
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
            self._dismiss_node(selected.node_id)
            return
        try:
            self._dismiss_node(normalize_node_id(event.value))
        except ValueError as exc:
            message = (
                _t("new_dm.no_match")
                if event.value.strip()
                else _t("new_dm.select_node")
            )
            self.notify(f"{message}. {exc}", severity="error")

    @on(ListView.Selected, "#node-picker-list")
    def select_node(self, event: ListView.Selected) -> None:
        if isinstance(event.item, NodePickerItem):
            self._dismiss_node(event.item.node_id)

    def _dismiss_node(self, node_id: str) -> None:
        channel_key = self.query_one("#new-dm-channel", Select).value
        if not isinstance(channel_key, str) or not channel_key:
            self.notify(_t("new_dm.select_channel_error"), severity="error")
            return
        self.dismiss((node_id, channel_key))

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
        Binding("up", "previous_choice", "", priority=True),
        Binding("down", "next_choice", "", priority=True),
        Binding("t", "traceroute", "", priority=True),
        Binding("i", "node_info", "", priority=True),
        Binding("escape", "cancel", "", priority=True),
    ]

    def __init__(self, node: dict[str, Any], availability: dict[str, Any]):
        super().__init__()
        _localize_bindings(
            self,
            (
                ("up", "previous_choice", "binding.previous_choice", True),
                ("down", "next_choice", "binding.next_choice", True),
                ("t", "traceroute", "binding.traceroute", True),
                ("i", "node_info", "binding.node_info", True),
                ("escape", "cancel", "binding.cancel", True),
            ),
        )
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
            yield Label(_t("node_action.title"), id="node-action-title")
            yield Static(Text(f"{name}  [{node_id[-4:]}]"), id="node-action-node")
            yield Button(
                _t("node_action.open_conversation"),
                id="node-action-open-dm",
                disabled=local,
            )
            yield Button(
                self._traceroute_label(),
                id="node-action-traceroute",
                disabled=local or not bool(self.availability.get("available")),
            )
            yield Button(_t("node_action.info"), id="node-action-info")
            yield Button(_t("common.close"), id="node-action-cancel")
            yield Static(
                _t("node_action.help"),
                id="node-action-help",
            )

    def on_mount(self) -> None:
        target = (
            "#node-action-info"
            if self.node.get("is_local")
            else "#node-action-open-dm"
        )
        self.query_one(target, Button).focus()
        self.set_interval(1, self._update_traceroute_button)

    def _cooldown_remaining(self) -> int:
        return max(
            0,
            math.ceil(self._cooldown_deadline - time.monotonic() - 1e-6),
        )

    def _traceroute_label(self) -> str:
        remaining = self._cooldown_remaining()
        suffix = _t("node_action.wait_suffix", seconds=remaining) if remaining else ""
        return _t("node_action.traceroute", suffix=suffix)

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
            "node-action-info": "node_info",
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
            reason = _t("node_action.local_traceroute_error")
        elif remaining:
            reason = _t("node_action.cooldown", seconds=remaining)
        else:
            reason = self._blocked_reason
        if reason:
            self.notify(reason, severity="warning")
            return
        self.dismiss("traceroute")

    def action_node_info(self) -> None:
        self.dismiss("node_info")

    def action_cancel(self) -> None:
        self.dismiss(None)


class NodeInfoScreen(ModalScreen[None]):
    TABS = ("overview", "telemetry", "position", "traceroute")
    BINDINGS = [
        Binding("left", "previous_tab", "", priority=True),
        Binding("right", "next_tab", "", priority=True),
        Binding("o", "overview", "", priority=True),
        Binding("m", "telemetry", "", priority=True),
        Binding("p", "position", "", priority=True),
        Binding("t", "traceroute", "", priority=True),
        Binding("r", "run_traceroute", "", priority=True),
        Binding("x", "exchange_position", "", priority=True),
        Binding("escape", "close", "", priority=True),
    ]

    def __init__(
        self,
        overview: dict[str, Any],
        telemetry: list[dict[str, Any]],
        positions: list[dict[str, Any]],
        traceroutes: list[dict[str, Any]],
        traceroute_formatter: Callable[
            [dict[str, Any]],
            tuple[str, str, str, str, str],
        ],
        action_callback: Callable[[str], None],
        action_availability: dict[str, dict[str, Any]] | None = None,
    ):
        super().__init__()
        _localize_bindings(
            self,
            (
                ("left", "previous_tab", "binding.previous_tab", True),
                ("right", "next_tab", "binding.next_tab", True),
                ("o", "overview", "binding.overview", True),
                ("m", "telemetry", "binding.telemetry", True),
                ("p", "position", "binding.position", True),
                ("t", "traceroute", "binding.traceroute", True),
                ("r", "run_traceroute", "binding.run_traceroute", True),
                ("x", "exchange_position", "binding.exchange_position", True),
                ("escape", "close", "binding.close", True),
            ),
        )
        self.overview_data = overview
        self.telemetry = sorted(
            (dict(sample) for sample in telemetry),
            key=self._observation_sort_key,
            reverse=True,
        )
        self.positions = sorted(
            (dict(position) for position in positions),
            key=self._observation_sort_key,
            reverse=True,
        )
        self.traceroutes = sorted(
            (dict(action) for action in traceroutes),
            key=self._traceroute_sort_key,
        )
        self.traceroute_formatter = traceroute_formatter
        self.action_callback = action_callback
        self.current_tab = "overview"
        now = time.monotonic()
        availability = action_availability or {}
        self._action_cooldown_until = {}
        self._action_blocked = {}
        for action in ("traceroute", "position_exchange"):
            details = availability.get(action, {})
            try:
                cooldown = max(0, int(details.get("cooldown_seconds") or 0))
            except (TypeError, ValueError):
                cooldown = 0
            self._action_cooldown_until[action] = now + cooldown
            self._action_blocked[action] = (
                not bool(details.get("available", True)) and cooldown == 0
            )

    @staticmethod
    def _observation_sort_key(item: dict[str, Any]) -> tuple[str, int, str]:
        try:
            item_id = int(item.get("id") or 0)
        except (TypeError, ValueError):
            item_id = 0
        return (
            str(item.get("sample_time") or ""),
            item_id,
            str(item.get("dedupe_key") or ""),
        )

    @staticmethod
    def _traceroute_sort_key(item: dict[str, Any]) -> tuple[str, str]:
        return (
            str(item.get("started_at") or ""),
            str(item.get("action_id") or ""),
        )

    def compose(self) -> ComposeResult:
        node = self.overview_data.get("node", {})
        node_id = str(node.get("node_id") or "")
        name = sanitize_terminal_text(
            node.get("long_name") or node.get("short_name") or node_id
        )
        with Container(id="node-info-dialog"):
            yield Label(
                Text(_t("node_info.title", name=name, node=node_id[-4:])),
                id="node-info-title",
            )
            with Horizontal(id="node-info-tabs"):
                yield Button(_t("node_info.tab.overview"), id="node-info-tab-overview")
                yield Button(_t("node_info.tab.telemetry"), id="node-info-tab-telemetry")
                yield Button(_t("node_info.tab.position"), id="node-info-tab-position")
                yield Button(_t("node_info.tab.traceroute"), id="node-info-tab-traceroute")
            yield SelectableRichLog(
                id="node-info-log",
                wrap=True,
                highlight=False,
                markup=False,
                auto_scroll=False,
            )
            with Horizontal(id="node-info-footer"):
                yield Static(
                    _t("node_info.help"),
                    id="node-info-help",
                )
                yield Button(
                    _t("node_info.exchange"),
                    id="node-info-exchange-position",
                    variant="primary",
                )
                yield Button(
                    _t("node_info.run_trace"),
                    id="node-info-run-traceroute",
                    variant="primary",
                )
                yield Button(_t("common.close"), id="node-info-close")

    def on_mount(self) -> None:
        self._show_tab("overview")
        self.set_interval(1, self._refresh_action_buttons)
        now = time.monotonic()
        for action, deadline in self._action_cooldown_until.items():
            remaining = deadline - now
            if remaining > 0:
                self.set_timer(
                    remaining,
                    lambda selected=action: self._end_action_cooldown(selected),
                )

    def _refresh_action_buttons(self) -> None:
        node = self.overview_data.get("node", {})
        local = bool(node.get("is_local")) if isinstance(node, dict) else False
        exchange = self.query_one("#node-info-exchange-position", Button)
        run_trace = self.query_one("#node-info-run-traceroute", Button)
        exchange_remaining = self._action_cooldown_remaining("position_exchange")
        traceroute_remaining = self._action_cooldown_remaining("traceroute")
        exchange.label = (
            _t("common.wait_seconds", seconds=exchange_remaining)
            if exchange_remaining
            else _t("node_info.exchange")
        )
        run_trace.label = (
            _t("common.wait_seconds", seconds=traceroute_remaining)
            if traceroute_remaining
            else _t("node_info.run_trace")
        )
        exchange.display = self.current_tab == "position"
        exchange.disabled = (
            local
            or self._action_blocked["position_exchange"]
            or exchange_remaining > 0
        )
        run_trace.display = self.current_tab == "traceroute"
        run_trace.disabled = (
            local
            or self._action_blocked["traceroute"]
            or traceroute_remaining > 0
        )
        help_text = _t("node_info.help")
        if self.current_tab == "traceroute" and traceroute_remaining:
            help_text += _t(
                "node_info.next_traceroute", seconds=traceroute_remaining
            )
        elif self.current_tab == "position" and exchange_remaining:
            help_text += _t(
                "node_info.next_position", seconds=exchange_remaining
            )
        self.query_one("#node-info-help", Static).update(help_text)

    def _action_cooldown_remaining(self, action: str) -> int:
        return max(
            0,
            math.ceil(
                self._action_cooldown_until[action] - time.monotonic() - 1e-6
            ),
        )

    def _show_tab(self, tab: str) -> None:
        if tab not in self.TABS:
            return
        self.current_tab = tab
        for name in self.TABS:
            button = self.query_one(f"#node-info-tab-{name}", Button)
            button.variant = "success" if name == tab else "default"
        self._refresh_action_buttons()
        log = self.query_one("#node-info-log", RichLog)
        log.clear()
        {
            "overview": self._render_overview,
            "telemetry": self._render_telemetry,
            "position": self._render_positions,
            "traceroute": self._render_traceroutes,
        }[tab](log)
        log.scroll_home(animate=False)

    @on(Button.Pressed, "#node-info-tabs Button")
    def choose_tab(self, event: Button.Pressed) -> None:
        if event.button.id:
            self._show_tab(event.button.id.removeprefix("node-info-tab-"))

    @on(Button.Pressed, "#node-info-close")
    def close_with_mouse(self) -> None:
        self.action_close()

    @on(Button.Pressed, "#node-info-exchange-position")
    def exchange_position_with_mouse(self) -> None:
        self.action_exchange_position()

    @on(Button.Pressed, "#node-info-run-traceroute")
    def run_traceroute_with_mouse(self) -> None:
        self.action_run_traceroute()

    def _render_overview(self, log: RichLog) -> None:
        node = self.overview_data.get("node", {})
        text = Text()
        fields = (
            (_t("field.node_id"), node.get("node_id")),
            (_t("field.short_name"), node.get("short_name")),
            (_t("field.hardware"), node.get("hw_model")),
            (_t("field.role"), node.get("role")),
            (_t("field.last_heard"), _date_time(node.get("last_heard"), seconds=True)),
            (_t("field.transport"), _node_transport_label(node)),
            (_t("field.hops"), node.get("hops_away")),
            ("SNR", f"{node['snr']:g} dB" if node.get("snr") is not None else None),
            ("RSSI", f"{node['rssi']} dBm" if node.get("rssi") is not None else None),
        )
        for label, value in fields:
            if value not in (None, "", "Ukjend"):
                text.append(f"{label:<15}", style="bold cyan")
                text.append(f"{value}\n")

        latest = self.overview_data.get("latest_telemetry", {})
        if isinstance(latest, dict) and latest:
            text.append("\n" + _t("node_info.latest_telemetry") + "\n", style="bold green")
            for kind, sample in latest.items():
                metrics = sample.get("metrics") if isinstance(sample, dict) else None
                if not isinstance(metrics, dict):
                    continue
                text.append(
                    f"{_telemetry_kind_label(str(kind))}: ",
                    style="bold",
                )
                rendered = [
                    f"{label} {value}"
                    for name, raw in metrics.items()
                    for label, value in [_metric_label_and_value(str(name), raw)]
                ]
                text.append(" · ".join(rendered) + "\n")
                text.append(
                    f"  {_date_time(sample.get('sample_time'), seconds=True)} · "
                    f"{_gateway_label(sample)}\n",
                    style="dim",
                )

        position = self.overview_data.get("latest_position")
        if isinstance(position, dict):
            text.append(
                "\n" + _t("node_info.latest_position") + "\n",
                style="bold green",
            )
            text.append(
                f"{position['latitude']:.7f}, {position['longitude']:.7f}"
            )
            if position.get("altitude_msl") is not None:
                text.append(_t("node_info.altitude", value=position["altitude_msl"]))
            text.append(
                f"\n  {_date_time(position.get('sample_time'), seconds=True)} · "
                f"{_gateway_label(position)}\n",
                style="dim",
            )
            url = _google_maps_url(position)
            text.append("Google Maps: ", style="bold cyan")
            text.append_text(_map_link(url))
            text.append("\n")

        counts = self.overview_data.get("counts", {})
        text.append("\n" + _t("node_info.stored_history") + "\n", style="bold green")
        text.append(
            _t(
                "node_info.history_counts",
                telemetry=int(counts.get("telemetry") or 0),
                positions=int(counts.get("positions") or 0),
                traceroutes=int(counts.get("traceroutes") or 0),
            )
        )
        log.write(Panel(text, border_style="cyan", box=box.SQUARE), scroll_end=False)

    def _render_telemetry(self, log: RichLog) -> None:
        if not self.telemetry:
            log.write(Text(_t("node_info.no_telemetry"), style="dim"))
            return
        grouped: dict[str, list[dict[str, Any]]] = {}
        for sample in self.telemetry:
            kind = str(sample.get("kind") or "telemetry")
            grouped.setdefault(kind, []).append(sample)

        for kind, samples in grouped.items():
            normalized_metrics = []
            metric_names: set[str] = set()
            for sample in samples:
                metrics = sample.get("metrics")
                normalized = (
                    {
                        _canonical_metric_name(str(name)): value
                        for name, value in metrics.items()
                    }
                    if isinstance(metrics, dict)
                    else {}
                )
                normalized_metrics.append(normalized)
                metric_names.update(normalized)
            ordered_names = [
                name for name in METRIC_TABLE_ORDER if name in metric_names
            ]
            ordered_names.extend(sorted(metric_names.difference(ordered_names)))

            table = Table(
                title=Text(
                    _telemetry_kind_label(kind),
                    style="bold green",
                ),
                title_justify="left",
                box=box.SIMPLE_HEAD,
                show_edge=False,
                pad_edge=False,
                collapse_padding=True,
            )
            table.add_column(_t("field.date_time"), style="dim", no_wrap=True)
            table.add_column(_t("field.via"), style="dim", no_wrap=True)
            for name in ordered_names:
                label = _metric_table_label(name)
                table.add_column(
                    Text(str(label), style="bold cyan"),
                    no_wrap=True,
                )

            for sample, metrics in zip(samples, normalized_metrics, strict=True):
                values = []
                for name in ordered_names:
                    raw = metrics.get(name)
                    values.append(
                        "–"
                        if raw is None
                        else _metric_label_and_value(name, raw)[1]
                    )
                table.add_row(
                    Text(
                        _date_time(
                            sample.get("sample_time"),
                            seconds=True,
                            multiline=True,
                        )
                    ),
                    Text(_gateway_label(sample).removeprefix("via ")),
                    *(Text(str(value)) for value in values),
                )
            log.write(table, scroll_end=False)

    def _render_positions(self, log: RichLog) -> None:
        if not self.positions:
            log.write(Text(_t("node_info.no_positions"), style="dim"))
            return

        table = Table(
            title=_t("node_info.position_log"),
            title_justify="left",
            title_style="bold green",
            box=box.SIMPLE_HEAD,
            show_edge=False,
            pad_edge=False,
            collapse_padding=True,
        )
        for label in (
            _t("field.date_time"),
            _t("field.via"),
            _t("field.latitude"),
            _t("field.longitude"),
            _t("field.altitude_msl"),
            "HAE",
            _t("field.satellites"),
            _t("field.gps_accuracy"),
            "PDOP",
        ):
            table.add_column(
                label,
                header_style="bold cyan",
                no_wrap=label not in {_t("field.via")},
            )

        for position in self.positions:
            table.add_row(
                Text(
                    _date_time(
                        position.get("sample_time"),
                        seconds=True,
                        multiline=True,
                    )
                ),
                Text(_gateway_label(position).removeprefix("via ")),
                Text(f"{position['latitude']:.7f}"),
                Text(f"{position['longitude']:.7f}"),
                Text(
                    f"{position['altitude_msl']} m"
                    if position.get("altitude_msl") is not None
                    else "–"
                ),
                Text(
                    f"{position['altitude_hae']} m"
                    if position.get("altitude_hae") is not None
                    else "–"
                ),
                Text(str(position.get("sats_in_view") or "–")),
                Text(
                    f"{position['gps_accuracy_mm']} mm"
                    if position.get("gps_accuracy_mm") is not None
                    else "–"
                ),
                Text(
                    f"{position['pdop']:g}"
                    if position.get("pdop") is not None
                    else "–"
                ),
            )
        log.write(table, scroll_end=False)

        links = Table(
            title=_t("node_info.map_links"),
            title_justify="left",
            title_style="bold green",
            box=box.SIMPLE_HEAD,
            show_edge=False,
            pad_edge=False,
            collapse_padding=True,
        )
        links.add_column(_t("field.date_time"), style="dim", no_wrap=True)
        links.add_column("Google Maps", header_style="bold cyan", overflow="fold")
        for position in self.positions:
            url = _google_maps_url(position)
            links.add_row(
                Text(
                    _date_time(
                        position.get("sample_time"),
                        seconds=True,
                        multiline=True,
                    )
                ),
                _map_link(url),
            )
        log.write(links, scroll_end=False)

    def _render_traceroutes(self, log: RichLog) -> None:
        if not self.traceroutes:
            log.write(Text(_t("node_info.no_traceroutes"), style="dim"))
            return

        table = Table(
            title=_t("node_info.traceroute_log"),
            title_justify="left",
            title_style="bold green",
            box=box.SIMPLE_HEAD,
            show_edge=False,
            pad_edge=False,
            collapse_padding=True,
        )
        table.add_column(_t("field.date_time"), style="dim", no_wrap=True)
        table.add_column(_t("field.status"), no_wrap=True)
        table.add_column(_t("field.hops_forward_return"), justify="right", no_wrap=True)
        table.add_column(_t("field.forward"), overflow="fold")
        table.add_column(_t("field.return_error"), overflow="fold")
        for action in self.traceroutes:
            table.add_row(
                *(Text(str(cell)) for cell in self.traceroute_formatter(action))
            )
        log.write(table, scroll_end=False)

    def upsert_traceroute(self, action: dict[str, Any]) -> None:
        action_id = str(action.get("action_id") or "")
        if not action_id:
            return
        rank = {"started": 0, "completed": 1, "failed": 1}
        for index, previous in enumerate(self.traceroutes):
            if str(previous.get("action_id") or "") == action_id:
                if rank.get(str(previous.get("status") or "started"), 0) > rank.get(
                    str(action.get("status") or "started"),
                    0,
                ):
                    return
                self.traceroutes[index] = dict(action)
                break
        else:
            self.traceroutes.append(dict(action))
        self.traceroutes.sort(key=self._traceroute_sort_key)
        if self.current_tab == "traceroute":
            self._show_tab("traceroute")

    def upsert_position(self, position: dict[str, Any]) -> None:
        dedupe_key = str(position.get("dedupe_key") or "")
        inserted = True
        if dedupe_key:
            for index, previous in enumerate(self.positions):
                if str(previous.get("dedupe_key") or "") == dedupe_key:
                    self.positions[index] = dict(position)
                    inserted = False
                    break
        if inserted:
            self.positions.append(dict(position))
            counts = self.overview_data.setdefault("counts", {})
            if isinstance(counts, dict):
                counts["positions"] = int(counts.get("positions") or 0) + 1
        self.positions.sort(key=self._observation_sort_key, reverse=True)
        if self.positions:
            self.overview_data["latest_position"] = dict(self.positions[0])
        if self.current_tab in {"overview", "position"}:
            self._show_tab(self.current_tab)

    def upsert_telemetry(self, sample: dict[str, Any]) -> None:
        dedupe_key = str(sample.get("dedupe_key") or "")
        inserted = True
        if dedupe_key:
            for index, previous in enumerate(self.telemetry):
                if str(previous.get("dedupe_key") or "") == dedupe_key:
                    self.telemetry[index] = dict(sample)
                    inserted = False
                    break
        if inserted:
            self.telemetry.append(dict(sample))
            counts = self.overview_data.setdefault("counts", {})
            if isinstance(counts, dict):
                counts["telemetry"] = int(counts.get("telemetry") or 0) + 1
        self.telemetry.sort(key=self._observation_sort_key, reverse=True)
        kind = str(sample.get("kind") or "telemetry")
        newest = next(
            (
                candidate
                for candidate in self.telemetry
                if str(candidate.get("kind") or "telemetry") == kind
            ),
            None,
        )
        if newest is not None:
            latest = self.overview_data.setdefault("latest_telemetry", {})
            if isinstance(latest, dict):
                latest[kind] = dict(newest)
        if self.current_tab in {"overview", "telemetry"}:
            self._show_tab(self.current_tab)

    def _move_tab(self, direction: int) -> None:
        current = self.TABS.index(self.current_tab)
        self._show_tab(self.TABS[(current + direction) % len(self.TABS)])

    def action_previous_tab(self) -> None:
        self._move_tab(-1)

    def action_next_tab(self) -> None:
        self._move_tab(1)

    def action_overview(self) -> None:
        self._show_tab("overview")

    def action_telemetry(self) -> None:
        self._show_tab("telemetry")

    def action_position(self) -> None:
        self._show_tab("position")

    def action_traceroute(self) -> None:
        self._show_tab("traceroute")

    def action_run_traceroute(self) -> None:
        if self.current_tab != "traceroute":
            self._show_tab("traceroute")
            return
        button = self.query_one("#node-info-run-traceroute", Button)
        if button.disabled or not button.display:
            return
        self._action_cooldown_until["traceroute"] = time.monotonic() + 30
        button.disabled = True
        self.set_timer(30, lambda: self._end_action_cooldown("traceroute"))
        self.action_callback("traceroute")

    def action_exchange_position(self) -> None:
        if self.current_tab != "position":
            self._show_tab("position")
            return
        button = self.query_one("#node-info-exchange-position", Button)
        if button.disabled or not button.display:
            return
        self._action_cooldown_until["position_exchange"] = time.monotonic() + 30
        button.disabled = True
        self.set_timer(
            30,
            lambda: self._end_action_cooldown("position_exchange"),
        )
        self.action_callback("position_exchange")

    def _end_action_cooldown(self, action: str) -> None:
        self._action_cooldown_until[action] = 0.0
        if self.is_mounted:
            self._refresh_action_buttons()

    def clear_action_cooldown(self, action: str) -> None:
        self._end_action_cooldown(action)

    def action_open_map(self, url: str) -> None:
        if not url.startswith("https://www.google.com/maps/search/?"):
            self.notify(_t("map.invalid"), severity="error")
            return
        if os.environ.get("SSH_CONNECTION") or os.environ.get("SSH_TTY"):
            self.notify(
                _t("map.ssh_hint"),
                title=_t("map.ssh_title"),
            )
            return
        self.app.open_url(url)

    def action_close(self) -> None:
        self.dismiss(None)


class QuitScreen(ModalScreen[str | None]):
    BINDINGS = [
        Binding("up", "previous_choice", "", priority=True),
        Binding("left", "previous_choice", "", priority=True),
        Binding("down", "next_choice", "", priority=True),
        Binding("right", "next_choice", "", priority=True),
        Binding("escape", "cancel", "", priority=True),
    ]

    def __init__(self, background_mode: str):
        super().__init__()
        _localize_bindings(
            self,
            (
                ("up", "previous_choice", "binding.previous_choice", True),
                ("left", "previous_choice", "binding.previous_choice", True),
                ("down", "next_choice", "binding.next_choice", True),
                ("right", "next_choice", "binding.next_choice", True),
                ("escape", "cancel", "binding.cancel", True),
            ),
        )
        self.background_mode = background_mode

    def compose(self) -> ComposeResult:
        with Container(id="quit-dialog"):
            yield Label(_t("quit.title"), id="quit-title")
            yield Static(
                _t("quit.question"),
                id="quit-text",
            )
            yield Button(_t("quit.leave"), id="quit-leave")
            yield Button(_t("quit.stop"), id="quit-stop")
            yield Button(_t("common.cancel"), id="quit-cancel")
            yield Static(_t("quit.help"), id="quit-help")

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
        Binding("f1", "close_help", "", priority=True),
        Binding("escape", "close_help", "", priority=True),
    ]

    SHORTCUTS = (
        ("F1", "help.f1"),
        ("Tab / Shift+Tab", "help.tab"),
        ("Enter", "help.enter"),
        ("↑ / ↓", "help.arrows"),
        ("Mouse / Ctrl+C", "help.copy"),
        ("Ctrl+L", "help.input"),
        ("Ctrl+D", "help.new_dm"),
        ("F2", "help.conversations"),
        ("F3", "help.nodes"),
        ("F4", "help.node_filter"),
        ("F8", "help.toggle_dm"),
        ("F9", "help.toggle_channels"),
        ("F10", "help.settings"),
        ("Shift+F10", "help.node_actions"),
        ("Delete", "help.archive"),
        ("Ctrl+R", "help.refresh"),
        ("Ctrl+U", "help.update"),
        ("Ctrl+Q", "help.quit"),
        ("Esc", "help.escape"),
    )

    def __init__(self) -> None:
        super().__init__()
        _localize_bindings(
            self,
            (
                ("f1", "close_help", "binding.close_help", True),
                ("escape", "close_help", "binding.close_help", True),
            ),
        )

    def compose(self) -> ComposeResult:
        with Container(id="help-dialog"):
            yield Label(_t("help.title"), id="help-title")
            help_text = Text()
            for key, description_key in self.SHORTCUTS:
                help_text.append(f"{key:<18}", style="bold cyan")
                help_text.append(_t(description_key) + "\n")
            yield Static(help_text, id="help-shortcuts")
            yield Static(_t("help.close"), id="help-close")

    def action_close_help(self) -> None:
        self.dismiss(None)


def _known_position(node: dict[str, Any]) -> dict[str, Any] | None:
    position = node.get("latest_position")
    if not isinstance(position, dict):
        return None
    try:
        lat, lon = float(position["latitude"]), float(position["longitude"])
    except (KeyError, TypeError, ValueError):
        return None
    return position if math.isfinite(lat) and math.isfinite(lon) and (
        -90 <= lat <= 90 and -180 <= lon <= 180
    ) else None


class StoredNodeScreen(ModalScreen[None]):
    """Details from the gateway cache; opening this screen never requests radio data."""

    BINDINGS = [Binding("escape", "close", "", priority=True)]
    DEFAULT_CSS = """
    StoredNodeScreen, RemoveNodeScreen { align: center middle; background: rgba(0,0,0,0.8); }
    #stored-node-dialog {
        width: 88; max-width: 96%; height: 32; max-height: 94%;
        padding: 1 2; border: round #58d65c; background: #0d1112;
    }
    #stored-node-title { height: 2; color: #58d65c; text-style: bold; }
    #stored-node-log { height: 1fr; }
    #stored-node-buttons { height: 3; }
    #stored-node-buttons Button { width: 1fr; }
    """

    def __init__(self, node: dict[str, Any]):
        super().__init__()
        self.node = dict(node)

    def compose(self) -> ComposeResult:
        with Vertical(id="stored-node-dialog"):
            yield Label(_t("local.nodes.details"), id="stored-node-title")
            yield SelectableRichLog(id="stored-node-log", wrap=True, markup=False)
            with Horizontal(id="stored-node-buttons"):
                yield Button(_t("local.nodes.map"), id="stored-node-map",
                             disabled=_known_position(self.node) is None)
                yield Button(_t("common.close"), id="stored-node-close")

    def on_mount(self) -> None:
        text = Text()

        def row(key: str, value: Any) -> None:
            rendered = _t("node_action.not_reported") if value in (None, "") else str(value)
            text.append(f"{_t(key):<23}", style="bold cyan")
            text.append(sanitize_terminal_text(rendered) + "\n")

        for field in ("long_name", "short_name", "node_id", "last_heard", "role",
                      "hw_model", "battery_level", "voltage", "snr", "rssi", "hops_away"):
            value = self.node.get(field)
            key = {"hw_model": "hardware", "battery_level": "battery",
                   "hops_away": "hops"}.get(field, field)
            if field == "last_heard":
                value = _date_time(value, seconds=True)
            elif field in {"battery_level", "voltage", "snr", "rssi"} and value is not None:
                _, value = _metric_label_and_value(field, value)
                if field == "battery_level" and self.node[field] == 0:
                    value = "0%"
            label_key = (f"metric.{key}" if field in {"battery_level", "voltage"}
                         else f"local.nodes.{key}" if field in {"snr", "rssi"}
                         else f"field.{key}")
            row(label_key, value)
        row("local.nodes.in_registry", _t(
            "value.yes" if self.node.get("in_registry") else "value.no"
        ))
        text.append("\nGPS\n", style="bold green")
        position = _known_position(self.node)
        if position:
            row("local.nodes.latitude", f"{float(position['latitude']):.7f}")
            row("local.nodes.longitude", f"{float(position['longitude']):.7f}")
            altitude = position.get("altitude_msl")
            row("local.nodes.altitude", f"{altitude} m" if altitude is not None else None)
            row("local.nodes.position_time", _date_time(position.get("sample_time"), seconds=True))
            row("local.nodes.position_received", _date_time(
                position.get("received_at"), seconds=True
            ))
            row("local.nodes.precision", position.get("precision_bits"))
        else:
            text.append(_t("local.nodes.no_position") + "\n")
        text.append("\n" + _t("local.nodes.position_note"), style="dim")
        self.query_one("#stored-node-log", RichLog).write(text)
        self.query_one("#stored-node-log", RichLog).focus()

    @on(Button.Pressed)
    def choose(self, event: Button.Pressed) -> None:
        event.stop()
        if event.button.id == "stored-node-map":
            position = _known_position(self.node)
            if position:
                self.app.open_url(_google_maps_url(position))
        elif event.button.id == "stored-node-close":
            self.action_close()

    def action_close(self) -> None:
        self.dismiss(None)


class RemoveNodeScreen(ModalScreen[bool]):
    BINDINGS = [Binding("escape", "cancel", "", priority=True)]
    DEFAULT_CSS = """
    RemoveNodeScreen { align: center middle; background: rgba(0,0,0,0.8); }
    #remove-node-dialog {
        width: 74; max-width: 96%; height: 24; max-height: 94%;
        padding: 1 2; border: round #e7ac57; background: #0d1112;
    }
    #remove-node-text { height: 1fr; overflow-y: auto; margin-bottom: 1; }
    #remove-node-buttons { height: 3; }
    #remove-node-buttons Button { width: 1fr; }
    """

    def __init__(self, nodes: list[dict[str, Any]], gateway: str):
        super().__init__()
        self.nodes, self.gateway = [dict(node) for node in nodes], gateway

    def compose(self) -> ComposeResult:
        contacts = "\n".join(
            f"{node['node_id']}  "
            + sanitize_terminal_text(node.get("long_name") or node.get("short_name") or "")
            for node in self.nodes
        )
        with Vertical(id="remove-node-dialog"):
            yield Static(Text(_t(
                "local.nodes.confirm", contacts=contacts,
                count=len(self.nodes), gateway=self.gateway,
            )), id="remove-node-text")
            with Horizontal(id="remove-node-buttons"):
                yield Button(_t("common.cancel"), id="remove-node-cancel")
                yield Button(_t("local.nodes.remove"), id="remove-node-confirm", variant="error")

    def on_mount(self) -> None:
        self.query_one("#remove-node-cancel", Button).focus()

    @on(Button.Pressed)
    def choose(self, event: Button.Pressed) -> None:
        event.stop()
        self.dismiss(event.button.id == "remove-node-confirm")

    def action_cancel(self) -> None:
        self.dismiss(False)


class SettingsScreen(ModalScreen[str | None]):
    TABS = ("overview", "radio", "status", "nodes", "network", "app")
    BINDINGS = [
        Binding("left", "previous_tab", "", priority=True),
        Binding("right", "next_tab", "", priority=True),
        Binding("r", "refresh_info", ""),
        Binding("ctrl+r", "refresh_info", "", priority=True),
        Binding("delete", "remove_node", ""),
        Binding("space", "toggle_node", ""),
        Binding("shift+left", "scroll_node_columns(-1)", "", priority=True),
        Binding("shift+right", "scroll_node_columns(1)", "", priority=True),
        Binding("f10", "cancel", "", priority=True),
        Binding("escape", "cancel", "", priority=True),
    ]

    def __init__(self, language: str):
        super().__init__()
        self.language = language
        self.current_tab = "overview"
        self.info: dict[str, Any] | None = None
        self.load_error: str | None = None
        self.removing_node = False
        self.node_message = ""
        self.visible_nodes: dict[str, dict[str, Any]] = {}
        self.node_rows: list[str] = []
        self.checked_nodes: set[str] = set()
        _localize_bindings(
            self,
            (
                ("left", "previous_tab", "binding.previous_tab", True),
                ("right", "next_tab", "binding.next_tab", True),
                ("r", "refresh_info", "binding.refresh", False),
                ("ctrl+r", "refresh_info", "binding.refresh", True),
                ("escape", "cancel", "binding.close", True),
            ),
        )

    def compose(self) -> ComposeResult:
        with Container(id="settings-dialog"):
            yield Label(_t("settings.title"), id="settings-title")
            with Horizontal(id="local-node-tabs"):
                for tab in self.TABS:
                    yield Button(_t(f"local.tab.{tab}"), id=f"local-tab-{tab}")
            yield SelectableRichLog(id="local-node-content", wrap=True, markup=False)
            with Vertical(id="local-nodes-pane"):
                yield Static(id="local-nodes-summary", markup=False)
                yield Input(placeholder=_t("local.nodes.search"), id="local-nodes-search")
                yield DataTable(id="local-nodes-table", cursor_type="row", zebra_stripes=True)
                yield Static(id="local-nodes-message", markup=False)
                with Horizontal(id="local-nodes-actions"):
                    yield Button(_t("local.nodes.details"), id="local-nodes-details")
                    yield Button(_t("local.nodes.select"), id="local-nodes-select")
                    yield Button(_t("local.nodes.remove"), id="local-nodes-remove", variant="error")
            with Vertical(id="settings-app"):
                yield Static(_t("settings.language"), id="settings-language-label")
                yield Button("Nynorsk", id="settings-nn")
                yield Button("English", id="settings-en")
            yield Button(_t("common.close"), id="settings-cancel")
            yield Static(_t("settings.help"), id="settings-help")

    def on_mount(self) -> None:
        self.query_one("#local-nodes-table", DataTable).add_columns(
            _t("local.nodes.selected"),
            _t("field.long_name"), _t("field.node_id"), _t("field.last_heard"),
            "GPS", _t("metric.battery"), _t("local.nodes.registry_column"),
        )
        self.set_class(self.app.size.height < 32, "compact")
        self._show_tab("overview")
        self.set_interval(10, self.action_refresh_info)
        self.action_refresh_info()

    def on_resize(self, event: Resize) -> None:
        self.set_class(event.size.height < 32, "compact")

    @on(Button.Pressed)
    def choose(self, event: Button.Pressed) -> None:
        event.stop()
        button_id = event.button.id or ""
        if button_id.startswith("local-tab-"):
            self._show_tab(button_id.removeprefix("local-tab-"))
            return
        if button_id == "local-nodes-details":
            self.action_node_details()
            return
        if button_id == "local-nodes-remove":
            self.action_remove_node()
            return
        if button_id == "local-nodes-select":
            self.action_toggle_node()
            return
        results = {
            "settings-nn": "nn",
            "settings-en": "en",
            "settings-cancel": None,
        }
        if button_id in results:
            self.dismiss(results[button_id])

    def _show_tab(self, tab: str) -> None:
        self.current_tab = tab
        self.query_one("#settings-app").display = tab == "app"
        self.query_one("#local-node-content").display = tab not in {"app", "nodes"}
        self.query_one("#local-nodes-pane").display = tab == "nodes"
        for name in self.TABS:
            self.query_one(f"#local-tab-{name}", Button).variant = (
                "primary" if name == tab else "default"
            )
        if tab == "app":
            self.query_one(f"#settings-{self.language}", Button).focus()
        elif tab == "nodes":
            self.query_one("#local-nodes-table", DataTable).focus()
        else:
            self.query_one("#local-node-content", RichLog).focus()
        self._render_info(preserve_scroll=False)

    def check_action(self, action: str, parameters: tuple[object, ...]) -> bool | None:
        return not (
            action in {"next_tab", "previous_tab", "scroll_node_columns"}
            and isinstance(self.focused, Input)
        )

    def action_scroll_node_columns(self, direction: int) -> None:
        if self.current_tab == "nodes":
            self.query_one("#local-nodes-table", DataTable).scroll_relative(
                x=direction * 15, animate=False,
            )

    def _selected_node(self) -> dict[str, Any] | None:
        table = self.query_one("#local-nodes-table", DataTable)
        if 0 <= table.cursor_row < len(self.node_rows):
            return self.visible_nodes.get(self.node_rows[table.cursor_row])
        return None

    @on(Input.Changed, "#local-nodes-search")
    def filter_nodes(self) -> None:
        self._render_nodes()

    @on(Input.Submitted, "#local-nodes-search")
    def focus_node_table(self) -> None:
        self.query_one("#local-nodes-table", DataTable).focus()

    @on(DataTable.RowHighlighted, "#local-nodes-table")
    def selected_node_changed(self) -> None:
        self._update_node_buttons()

    @on(DataTable.RowSelected, "#local-nodes-table")
    def node_selected(self) -> None:
        self.action_node_details()

    def _update_node_buttons(self) -> None:
        node = self._selected_node()
        info = self.info or {}
        gateway = info.get("local_node_id") or info.get("status", {}).get("local_node_id")
        self.query_one("#local-nodes-details", Button).disabled = node is None
        allowed = node is not None and self._removable_node(node, gateway)
        self.query_one("#local-nodes-select", Button).disabled = not allowed or self.removing_node
        targets = self._removal_targets()
        self.query_one("#local-nodes-remove", Button).disabled = (
            not targets or not info.get("connected") or self.removing_node
        )
        self.query_one("#local-nodes-remove", Button).label = (
            _t("local.nodes.remove_count", count=len(targets)) if len(targets) > 1
            else _t("local.nodes.remove")
        )

    @staticmethod
    def _removable_node(node: dict[str, Any], gateway: str | None) -> bool:
        node_id = str(node.get("node_id") or "")
        if not re.fullmatch(r"![0-9a-fA-F]{8}", node_id):
            return False
        number = int(node_id[1:], 16)
        return number not in {0, 1, 2, 3, 0xFFFFFFFF} and (
            not node.get("is_local") and node_id.lower() != str(gateway).lower()
        )

    def _removal_targets(self) -> list[dict[str, Any]]:
        info = self.info or {}
        gateway = info.get("local_node_id") or info.get("status", {}).get("local_node_id")
        if self.checked_nodes:
            candidates = [node for node in info.get("nodes", [])
                          if node.get("node_id") in self.checked_nodes]
        else:
            node = self._selected_node()
            candidates = [node] if node else []
        return [node for node in candidates if self._removable_node(node, gateway)]

    def action_toggle_node(self) -> None:
        if self.current_tab != "nodes" or self.removing_node:
            return
        node = self._selected_node()
        info = self.info or {}
        gateway = info.get("local_node_id") or info.get("status", {}).get("local_node_id")
        if not node or not self._removable_node(node, gateway):
            return
        node_id = str(node["node_id"])
        if node_id in self.checked_nodes:
            self.checked_nodes.remove(node_id)
        else:
            self.checked_nodes.add(node_id)
        self._render_nodes()

    def _render_nodes(self) -> None:
        table = self.query_one("#local-nodes-table", DataTable)
        selected = self._selected_node()
        old_index = table.cursor_row
        search = self.query_one("#local-nodes-search", Input).value.casefold().strip()
        info = self.info or {}
        nodes = info.get("nodes", [])
        self.checked_nodes.intersection_update(node["node_id"] for node in nodes)
        self.visible_nodes = {
            str(node["node_id"]): node for node in nodes
            if not search or search in " ".join(str(node.get(key) or "") for key in (
                "node_id", "long_name", "short_name"
            )).casefold()
        }
        self.node_rows = list(self.visible_nodes)
        table.clear()
        for node_id, node in self.visible_nodes.items():
            name = str(node.get("long_name") or node.get("short_name") or node_id)
            if node.get("is_local"):
                name = _t("local.nodes.self", name=name)
            position = _known_position(node)
            gps = (f"{float(position['latitude']):.5f}, {float(position['longitude']):.5f}"
                   if position else "—")
            battery = node.get("battery_level")
            battery_text = ("—" if battery is None else "0%" if battery == 0 else
                            _metric_label_and_value("battery_level", battery)[1])
            table.add_row(
                Text("✓" if node_id in self.checked_nodes else "·"),
                Text(sanitize_terminal_text(name[:32])), Text(node_id),
                Text(_date_time(node.get("last_heard"))), Text(gps), Text(battery_text),
                Text(_t("value.yes" if node.get("in_registry") else "value.no")), key=node_id,
            )
        if selected and selected["node_id"] in self.node_rows:
            index = self.node_rows.index(selected["node_id"])
        else:
            index = max(0, min(old_index, len(self.node_rows) - 1))
        table.move_cursor(row=index, animate=False)
        summary = _t("local.nodes.summary", shown=len(self.node_rows), total=len(nodes),
                     registry=info.get("registry_count", "—"), selected=len(self.checked_nodes))
        if not info.get("connected"):
            summary += " · " + _t("state.disconnected")
        self.query_one("#local-nodes-summary", Static).update(summary)
        message = self.node_message or (
            _t("local.load_error", error=self.load_error) if self.load_error else
            _t("local.loading") if self.info is None else
            _t("local.nodes.empty") if not self.node_rows else _t("local.nodes.help")
        )
        self.query_one("#local-nodes-message", Static).update(message)
        self._update_node_buttons()

    def action_node_details(self) -> None:
        node = self._selected_node()
        if node and self.current_tab == "nodes":
            self.app.push_screen(StoredNodeScreen(node))

    def action_remove_node(self) -> None:
        if self.current_tab != "nodes" or self.query_one("#local-nodes-remove", Button).disabled:
            return
        nodes = self._removal_targets()
        if not nodes:
            return
        info = self.info or {}
        gateway = str(
            info.get("local_node_id") or info.get("status", {}).get("local_node_id") or ""
        )
        node_ids = [str(node["node_id"]) for node in nodes]

        def confirmed(remove: bool) -> None:
            if remove and self.is_mounted:
                self.removing_node = True
                self.node_message = _t("local.nodes.removing", count=len(node_ids))
                self._render_nodes()
                self.app.remove_local_registry_nodes(self, node_ids, gateway)

        self.app.push_screen(RemoveNodeScreen(nodes, gateway), confirmed)

    def removal_finished(self, result: dict[str, Any] | None, error: str | None) -> None:
        self.removing_node = False
        results = (result or {}).get("result", {}).get("contacts", [])
        self.checked_nodes.difference_update(
            item["node_id"] for item in results if item.get("status") == "removed"
        )
        self.node_message = self.removal_message(result, error)
        self._render_nodes()
        self.action_refresh_info()

    @staticmethod
    def removal_message(result: dict[str, Any] | None, error: str | None) -> str:
        if error:
            return _t("local.nodes.failed", error=error)
        results = (result or {}).get("result", {}).get("contacts", [])
        completed = [item for item in results if item.get("status") == "removed"]
        message = _t("local.nodes.result", confirmed=len(completed), total=len(results))
        errors = [f"{item['node_id']}: {item.get('error') or _t('local.nodes.unverified')}"
                  for item in results if item.get("status") != "removed"]
        return message + (" · " + "; ".join(dict.fromkeys(errors)) if errors else "")

    def action_next_tab(self) -> None:
        self._show_tab(self.TABS[(self.TABS.index(self.current_tab) + 1) % len(self.TABS)])

    def action_previous_tab(self) -> None:
        self._show_tab(self.TABS[(self.TABS.index(self.current_tab) - 1) % len(self.TABS)])

    def action_refresh_info(self) -> None:
        if self.app.screen is self:
            self.app.load_local_node_info(self)

    def update_info(self, info: dict[str, Any] | None, error: str | None = None) -> None:
        self.info = info
        self.load_error = error
        self._render_info()

    def _render_info(self, *, preserve_scroll: bool = True) -> None:
        self._render_nodes()
        log = self.query_one("#local-node-content", RichLog)
        old_scroll = log.scroll_y if preserve_scroll else 0
        log.clear()
        text = Text()

        def row(key: str, value: Any) -> None:
            label = _t(key)
            if isinstance(value, bool):
                value = _t("value.yes" if value else "value.no")
            rendered = (
                sanitize_terminal_text(str(value))
                if value not in (None, "") else _t("node_action.not_reported")
            )
            text.append(f"{label:<27}", style="bold cyan")
            text.append(f"{rendered}\n")

        if self.load_error:
            text.append(_t("local.load_error", error=self.load_error), style="yellow")
        elif self.info is None:
            text.append(_t("local.loading"), style="dim")
        else:
            info = self.info
            node = info.get("node", {})
            status = info.get("status", {})
            radio = info.get("radio", {})
            text.append(_t(f"local.tab.{self.current_tab}") + "\n\n", style="bold green")
            if not info.get("connected"):
                text.append(_t("local.offline") + "\n\n", style="yellow")
            if self.current_tab == "overview":
                row("field.long_name", node.get("long_name"))
                row("field.short_name", node.get("short_name"))
                row("field.node_id", node.get("node_id") or status.get("local_node_id"))
                row("field.hardware", info.get("hw_model") or node.get("hw_model"))
                row("local.firmware", info.get("firmware_version"))
                row("field.role", info.get("role") or node.get("role"))
                state_key = "state.connected" if info.get("connected") else "state.disconnected"
                row("local.connection", _t(state_key))
                row("field.transport", status.get("transport"))
                row("local.endpoint", status.get("endpoint"))
                row("local.profile", status.get("connection_name"))
                row("local.config_received", _date_time(info.get("config_received_at")))
                text.append("\n" + _t("local.read_only"), style="dim")
            elif self.current_tab == "radio":
                if radio.get("derivation_unavailable"):
                    text.append(_t("local.radio_version_unknown") + "\n\n", style="yellow")
                row("local.region", radio.get("region"))
                band_min, band_max = radio.get("band_min_mhz"), radio.get("band_max_mhz")
                row("local.band", f"{band_min:g}–{band_max:g} MHz" if band_min else None)
                frequency = radio.get("frequency_mhz")
                row("local.frequency", f"{frequency:g} MHz" if frequency is not None else None)
                source = radio.get("frequency_source")
                row("local.frequency_source", _t(f"local.source.{source}") if source else None)
                slot = radio.get("channel_num")
                row("local.slot_configured", _t("local.automatic") if slot == 0 else slot)
                row("local.slot_effective", radio.get("effective_slot"))
                row("local.preset", radio.get("modem_preset") if radio.get("use_preset") else (
                    _t("local.custom") if radio.get("use_preset") is False else None
                ))
                bw = radio.get("effective_bandwidth_khz")
                row("local.bandwidth", f"{bw:g} kHz" if bw else None)
                row("local.spread_factor", radio.get("effective_spread_factor"))
                cr = radio.get("effective_coding_rate")
                row("local.coding_rate", f"4/{cr}" if cr else None)
                row("local.hop_limit", radio.get("hop_limit"))
                power = radio.get("tx_power")
                row("local.tx_power", _t("local.automatic") if power == 0 else (
                    f"{power} dBm" if power is not None else None
                ))
                row("local.tx_enabled", radio.get("tx_enabled"))
                row("local.ignore_mqtt", radio.get("ignore_mqtt"))
                row("local.mqtt_allowed", radio.get("config_ok_to_mqtt"))
                text.append("\n" + _t("local.frequency_note"), style="dim")
            elif self.current_tab == "status":
                latest = info.get("latest_telemetry", {})
                device = latest.get("device", {})
                metrics = device.get("metrics", {})
                canonical = {
                    _canonical_metric_name(str(key)): value for key, value in metrics.items()
                }
                for key in ("battery_level", "voltage", "uptime_seconds",
                            "channel_utilization", "air_util_tx"):
                    value = canonical.get(key, node.get(key))
                    if value is not None:
                        label, rendered = _metric_label_and_value(key, value)
                    else:
                        label = _t(METRIC_PRESENTATION[key][0])
                        rendered = _t("node_action.not_reported")
                    if key == "battery_level" and value == 0:
                        rendered = "0%"
                    text.append(f"{label:<27}", style="bold cyan")
                    text.append(f"{sanitize_terminal_text(rendered)}\n")
                row("local.telemetry_time", _date_time(device.get("sample_time")))
                row("field.last_heard", _date_time(node.get("last_heard")))
                text.append("\n" + _t("local.telemetry_note"), style="dim")
            elif self.current_tab == "network":
                row("local.registry_count", info.get("registry_count"))
                row("local.stored_count", info.get("stored_count"))
                row("local.heard_count", info.get("heard_24h_count"))
                channels = info.get("channels", [])
                row("local.channel_count", len(channels) if info.get("connected") else None)
                text.append("\n" + _t("local.channels") + "\n", style="bold green")
                for channel in channels:
                    name = channel.get("display_name") or _t("local.default_channel")
                    role = _t(f"local.channel_role.{channel.get('role')}")
                    text.append(f"  {channel.get('index')} · {name} · {role}\n")
                    row("local.uplink", channel.get("uplink_enabled"))
                    row("local.downlink", channel.get("downlink_enabled"))
                text.append("\n" + _t("local.count_note"), style="dim")
        log.write(text)
        log.scroll_to(y=old_scroll, animate=False)

    def action_cancel(self) -> None:
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
        height: 3;
        padding: 0 1;
        color: #cbd0d2;
    }

    ConversationItem.conversation-section-start {
        height: 5;
        padding-top: 1;
        border-top: solid #31393c;
    }

    #conversation-list > ConversationItem.-highlight {
        background: transparent;
        color: #cbd0d2;
    }

    #conversation-list:focus > ConversationItem.-highlight {
        background: #245c2a;
        color: white;
    }

    #conversation-list > ConversationItem.conversation-unread,
    #conversation-list:focus > ConversationItem.conversation-unread.-highlight {
        background: $block-cursor-background;
        color: white;
    }

    .conversation-label {
        width: 1fr;
        height: 2;
    }

    ConversationItem.conversation-section-start .conversation-label {
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

    #node-transport-filter {
        height: 3;
        margin: 0 1;
        width: 1fr;
    }

    #node-filter-empty {
        height: auto;
        padding: 1 2;
        color: #8d9699;
        display: none;
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
        height: auto;
        min-height: 3;
        padding: 0 1;
        color: #cbd0d2;
    }

    NodeSidebarItem.-highlight {
        background: #245c2a;
        color: white;
    }

    .node-sidebar-label {
        width: 1fr;
        height: auto;
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
        height: 29;
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

    NodeInfoScreen {
        align: center middle;
        background: rgba(0, 0, 0, 0.72);
    }

    #node-info-dialog {
        width: 112;
        max-width: 96%;
        height: 46;
        max-height: 94%;
        padding: 1 2;
        border: round $accent;
        background: $panel;
    }

    #node-info-title {
        height: 2;
        color: $accent;
        text-style: bold;
    }

    #node-info-tabs {
        height: 3;
        margin-bottom: 1;
    }

    #node-info-tabs Button {
        width: 1fr;
        min-width: 10;
    }

    #node-info-footer {
        height: 3;
        margin-top: 1;
    }

    #node-info-footer Button {
        width: auto;
        min-width: 14;
        margin-left: 1;
    }

    #node-info-close {
        min-width: 10;
    }

    #node-info-log {
        height: 1fr;
        padding: 0 1;
        border: round #394245;
        background: $panel;
        scrollbar-color: $accent;
        scrollbar-background: $panel;
    }

    #node-info-help {
        width: 1fr;
        height: 3;
        color: #8d9699;
        content-align: left middle;
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

    SettingsScreen {
        align: center middle;
        background: rgba(0, 0, 0, 0.72);
    }

    #settings-dialog {
        width: 132;
        max-width: 98%;
        height: 36;
        max-height: 94%;
        padding: 1 2;
        border: round $accent;
        background: $panel;
    }

    #settings-title {
        height: 2;
        color: $accent;
        text-style: bold;
    }

    #settings-language-label {
        height: 2;
        color: #cbd0d2;
    }

    #settings-dialog Button {
        width: 1fr;
        margin: 0 0 1 0;
    }

    #local-node-tabs {
        height: 3;
        margin-bottom: 1;
    }

    #local-node-tabs Button {
        min-width: 8;
        margin: 0;
    }

    #local-node-content {
        height: 1fr;
        padding: 0 1;
        scrollbar-color: $accent;
    }

    #settings-app {
        height: 1fr;
        padding-top: 1;
    }

    #local-nodes-pane { height: 1fr; }
    #local-nodes-summary { height: 1; color: $cyan; }
    #local-nodes-search { height: 3; margin: 0; }
    #local-nodes-table { height: 1fr; min-height: 3; scrollbar-color: $accent; }
    #local-nodes-message { height: 2; color: #aeb5b7; overflow-y: auto; }
    #local-nodes-actions { height: 3; }
    #local-nodes-actions Button { margin: 0; min-width: 12; }

    SettingsScreen.compact #settings-dialog { padding: 0 1; }
    SettingsScreen.compact #settings-title { height: 1; }
    SettingsScreen.compact #local-node-tabs { margin-bottom: 0; }
    SettingsScreen.compact #settings-cancel { display: none; }
    SettingsScreen.compact #settings-help { height: 1; }

    #settings-cancel {
        margin: 0;
        height: 3;
    }

    #settings-help {
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
        Binding("f1", "show_help", "", priority=True),
        Binding("tab", "focus_next_pane", "", priority=True),
        Binding("shift+tab", "focus_previous_pane", "", priority=True),
        Binding("ctrl+l", "focus_input", ""),
        Binding("ctrl+d", "new_dm", ""),
        Binding("f2", "focus_conversations", ""),
        Binding("f3", "focus_nodes", ""),
        Binding("f4", "focus_node_filter", ""),
        Binding("f8", "toggle_direct_messages", "", priority=True),
        Binding("f9", "toggle_channels", "", priority=True),
        Binding("f10", "settings", "", priority=True),
        Binding("shift+f10", "node_actions", "", priority=True),
        Binding("delete", "archive_conversation", ""),
        Binding("ctrl+r", "refresh", ""),
        Binding("ctrl+u", "copy_update_command", "", priority=True),
        Binding("ctrl+q", "quit", "", priority=True),
    ]

    def __init__(
        self,
        settings: Settings,
        requester: Requester = request,
        watcher: Watcher | None = open_watch,
        update_checker: UpdateChecker | None = check_for_update,
    ):
        super().__init__()
        self.title = _t("main.title")
        self.sub_title = _t("main.subtitle")
        self._localize_app_bindings()
        self.settings = settings
        self.requester = requester
        self.watcher = watcher
        self.update_checker = update_checker
        self.host_name = _host_name()
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
        self.show_direct_messages = True
        self.show_secondary_channels = True
        self.nodes: dict[str, dict[str, Any]] = {}
        self.node_transport_filter = "all"
        self._visible_timeline: list[tuple[str, dict[str, Any]]] = []
        self._timeline_time_labels: list[tuple[str | None, str]] = []
        self.current_conversation = "public"
        self._watch_socket: socket.socket | None = None
        self._watch_lock = threading.Lock()
        self._watch_stop = threading.Event()
        self._rebuilding_list = False
        self._rebuilding_nodes = False
        self.selected_node_id: str | None = None
        self._right_click_node_id: str | None = None
        self.node_action_entries: dict[str, dict[str, Any]] = {}
        self.update_notice: UpdateNotice | None = None
        self._message_history: dict[str, list[str]] = {}
        self._message_history_loaded: set[str] = set()
        self._pending_message_history: dict[str, list[str]] = {}
        self._settings_snapshot: dict[str, Any] | None = None
        self._last_node_removal_message = ""

    def _localize_app_bindings(self) -> None:
        _localize_bindings(
            self,
            (
                ("f1", "show_help", "binding.help", True),
                ("tab", "focus_next_pane", "binding.next_field", True),
                ("shift+tab", "focus_previous_pane", "binding.previous_field", True),
                ("ctrl+l", "focus_input", "binding.write_message", False),
                ("ctrl+d", "new_dm", "binding.new_dm", False),
                ("f2", "focus_conversations", "binding.conversations", False),
                ("f3", "focus_nodes", "binding.nodes", False),
                ("f4", "focus_node_filter", "binding.node_filter", False),
                ("f8", "toggle_direct_messages", "binding.toggle_dm", True),
                ("f9", "toggle_channels", "binding.toggle_channels", True),
                ("f10", "settings", "binding.settings", True),
                ("shift+f10", "node_actions", "binding.node_actions", True),
                ("delete", "archive_conversation", "binding.archive_dm", False),
                ("ctrl+r", "refresh", "binding.refresh", False),
                ("ctrl+u", "copy_update_command", "binding.copy_update", True),
                ("ctrl+q", "quit", "binding.quit", True),
            ),
        )

    def compose(self) -> ComposeResult:
        yield Static("", id="status-bar")
        with Horizontal(id="body"):
            with Vertical(id="conversation-panel"):
                yield Static(
                    _t("main.conversations"),
                    id="conversation-panel-title",
                    classes="panel-title",
                )
                yield ListView(id="conversation-list")
            with Vertical(id="message-panel"):
                yield Static(
                    _t("conversation.public_channel", channel=0, local_suffix=""),
                    id="conversation-title",
                    classes="panel-title",
                )
                yield SelectableRichLog(
                    id="message-log",
                    wrap=True,
                    highlight=False,
                    markup=False,
                    auto_scroll=True,
                )
                yield MessageInput(
                    placeholder=_t("main.message_placeholder"),
                    id="message-input",
                )
                yield Static(
                    _t("main.input_help"),
                    id="input-help",
                )
            with Vertical(id="node-panel"):
                yield Static(_t("main.node_details"), id="node-panel-title", classes="panel-title")
                yield Static(_t("main.no_node_selected"), id="node-details")
                yield Static(_t("main.nodes"), id="node-list-title", classes="panel-title")
                node_filter = Select(
                    self._node_filter_options(),
                    value=self.node_transport_filter,
                    allow_blank=False,
                    id="node-transport-filter",
                )
                node_filter.tooltip = _t("node_filter.help")
                yield node_filter
                yield Static(_t("node_filter.empty"), id="node-filter-empty")
                yield ListView(id="node-list")
        yield Static(
            _t("main.key_bar"),
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
        self.set_interval(60, self._refresh_time_labels)

    @staticmethod
    def _node_filter_options() -> list[tuple[str, str]]:
        return [(_t(f"node_filter.{mode}"), mode) for mode in ("all", "rf", "mqtt", "both")]

    @on(Select.Changed, "#node-transport-filter")
    async def node_transport_changed(self, event: Select.Changed) -> None:
        if event.value is Select.BLANK:
            return
        self.node_transport_filter = str(event.value)
        await self._apply_nodes(list(self.nodes.values()))

    def action_focus_node_filter(self) -> None:
        if self.query_one("#node-panel", Vertical).display:
            self.query_one("#node-transport-filter", Select).focus()

    async def _refresh_time_labels(self) -> None:
        await self._apply_nodes(list(self.nodes.values()))
        await self._render_conversation_sidebar()
        log = self.query_one("#message-log", RichLog)
        labels = [
            _message_time_parts(entry.get("timestamp" if kind == "message" else "started_at"))
            for kind, entry in self._visible_timeline
        ]
        if labels != self._timeline_time_labels and log.text_selection is None:
            offset, at_end = log.scroll_offset, log.is_vertical_scroll_end
            self._write_visible_timeline(log)
            if at_end:
                log.scroll_end(animate=False)
            else:
                log.scroll_to(x=offset.x, y=offset.y, animate=False, force=True)

    def on_resize(self, event: Resize) -> None:
        width = event.size.width
        node_panel = self.query_one("#node-panel", Vertical)
        conversation_panel = self.query_one("#conversation-panel", Vertical)
        input_help = self.query_one("#input-help", Static)
        node_panel.display = width >= 112
        conversation_panel.display = width >= 62
        conversation_panel.styles.width = 34 if width >= 125 else 31 if width >= 90 else 25
        input_help.display = width >= 76
        self._update_status_bar()

    def on_unmount(self) -> None:
        self._watch_stop.set()
        with self._watch_lock:
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
                _t("error.load_data", error=exc),
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
        text.append(_t("update.available"), style="bold yellow")
        text.append(
            f"  {notice.current_version} → {notice.latest_version}\n",
            style="yellow",
        )
        text.append(_t("update.terminal_hint") + "\n", style="dim")
        text.append(notice.command, style="bold cyan")
        text.append("\n" + _t("update.copy_hint"), style="dim")
        text.append("\n" + "─" * 72, style="#725f24")
        return text

    def _update_status_bar(self) -> None:
        if not self.is_mounted:
            return
        try:
            status_widget = self.query_one("#status-bar", Static)
        except NoMatches:
            # A timer may fire while Textual is dismantling the screen.
            return
        status = self.status_data
        state = str(status.get("state", "ukjend"))
        state_display = {
            "tilkopla": _t("state.connected"),
            "fråkopla": _t("state.disconnected"),
            "koplar til": _t("state.connecting"),
            "ingen node": _t("state.no_node"),
        }.get(state, state)
        state_style = "bold green" if state == "tilkopla" else "bold yellow"
        local_id = str(status.get("local_node_id") or "–")
        local_node = self.nodes.get(local_id, {})
        local_name = local_node.get("short_name") or local_node.get("long_name") or local_id
        transport = str(status.get("transport") or "").upper()
        endpoint = str(status.get("endpoint") or "")
        clock = datetime.now().astimezone().strftime("%H:%M:%S")
        width = status_widget.content_size.width
        if width <= 0:
            width = max(1, self.size.width - 6)

        state_label = f"● {state_display.capitalize()}"
        fixed: list[tuple[str, str | None]] = [
            (state_label, state_style),
            (clock, "cyan"),
        ]
        version: tuple[str, str | None] | None = None
        local: tuple[str, str | None] | None = None
        if width >= 125:
            version = (f"MeshPi {__version__}", "bold green")
            local_label = _fit_status_text(
                _t("status.local", name=local_name, node=local_id[-4:]),
                28,
            )
            local = (local_label, "green")
        elif width >= 88:
            local = (f"L:{local_id[-4:]}", "green")

        item_count = len(fixed) + 2 + int(version is not None) + int(local is not None)
        separator_width = 3 * (item_count - 1)
        fixed_width = sum(len(value) for value, _ in fixed)
        if version is not None:
            fixed_width += len(version[0])
        if local is not None:
            fixed_width += len(local[0])
        dynamic_width = max(8, width - separator_width - fixed_width)
        host_source = (
            _t("status.host", host=self.host_name) if width >= 125 else self.host_name
        )
        host_width = min(len(host_source), max(4, dynamic_width // 3))
        endpoint_width = max(4, dynamic_width - host_width)
        host = _fit_status_text(host_source, host_width)
        endpoint_label = (
            _fit_status_endpoint(transport, endpoint, endpoint_width)
            if endpoint
            else _fit_status_text(_t("state.no_node"), endpoint_width)
        )

        items: list[tuple[str, str | None]] = []
        if version is not None:
            items.append(version)
        items.append((host, "bold cyan"))
        items.append(
            (
                state_label,
                state_style,
            )
        )
        if local is not None:
            items.append(local)
        items.append(
            (
                endpoint_label,
                "cyan" if endpoint else "yellow",
            )
        )
        items.append((clock, "cyan"))

        text = Text()
        for index, (value, style) in enumerate(items):
            if index:
                text.append(" │ ")
            if value.startswith("● "):
                text.append("● ", style="green" if state == "tilkopla" else "yellow")
                text.append(value[2:], style=style)
            else:
                text.append(value, style=style)
        if text.cell_len > width:
            text.truncate(width, overflow="ellipsis")
        status_widget.update(text)

    def _with_public(self, conversations: list[dict[str, Any]]) -> list[dict[str, Any]]:
        public = [item for item in conversations if item.get("kind") == "public"]
        if not public:
            public = [{
                "conversation": "public",
                "kind": "public",
                "last_timestamp": None,
                "last_text": None,
                "unread": 0,
                "channel": 0,
                "sendable": False,
            }]
        elif self.current_conversation == "public":
            primary = next(
                (item for item in public if item.get("channel") == 0),
                public[0],
            )
            self.current_conversation = _conversation_id(primary)
        direct = [item for item in conversations if item.get("kind") == "dm"]
        known_ids = {
            _conversation_id(item) for item in [*public, *direct]
        }
        if (
            self.current_conversation not in known_ids
            and self.current_conversation != "public"
            and not self.current_conversation.startswith("channel:")
        ):
            synthetic: dict[str, Any] = {
                "conversation": self.current_conversation,
                "kind": "dm",
                "last_timestamp": None,
                "last_text": None,
                "unread": 0,
                "sendable": False,
                "_transient": True,
            }
            if self.current_conversation.startswith("dm:"):
                with suppress(ValueError):
                    route_local, route_peer, route_key = parse_dm_conversation_id(
                        self.current_conversation
                    )
                    active_route = next(
                        (
                            item
                            for item in public
                            if item.get("sendable") is True
                            and item.get("local_node_id") == route_local
                            and item.get("channel_key") == route_key
                        ),
                        None,
                    )
                    synthetic.update(
                        {
                            "peer_node": route_peer,
                            "local_node_id": route_local,
                            "channel_key": route_key,
                            "channel": (
                                active_route.get("channel")
                                if active_route
                                else None
                            ),
                            "channel_name": (
                                active_route.get("channel_name")
                                if active_route
                                else None
                            ),
                            "sendable": active_route is not None,
                        }
                    )
                    direct = [
                        item
                        for item in direct
                        if str(item.get("peer_node") or "").lower()
                        != route_peer
                    ]
            direct.insert(
                0,
                synthetic,
            )
        return [*public, *direct]

    def _sidebar_conversations(
        self,
        conversations: list[dict[str, Any]],
    ) -> list[dict[str, Any]]:
        public = [item for item in conversations if item.get("kind") == "public"]
        direct = [
            item
            for item in conversations
            if item.get("kind") == "dm" and not item.get("_transient")
        ]
        primary = next(
            (item for item in public if item.get("channel") == 0),
            public[0] if public else None,
        )
        channels: list[dict[str, Any]] = []
        if primary is not None:
            channels.append(primary)
        if self.show_secondary_channels:
            channels.extend(item for item in public if item is not primary)

        visible: list[dict[str, Any]] = []
        for index, item in enumerate(channels):
            rendered = dict(item)
            if index == 0:
                rendered["_section_label"] = _t("sidebar.channels")
                rendered["_section_hint"] = (
                    _t("sidebar.hide_secondary")
                    if self.show_secondary_channels
                    else _t("sidebar.show_secondary")
                )
            visible.append(rendered)

        if self.show_direct_messages:
            grouped_direct: dict[int | None, list[dict[str, Any]]] = {}
            for item in direct:
                try:
                    channel = int(item["channel"])
                except (KeyError, TypeError, ValueError):
                    channel = None
                grouped_direct.setdefault(channel, []).append(item)
            ordered_channels = sorted(
                grouped_direct,
                key=lambda channel: (channel is None, channel or 0),
            )
            first_group = True
            for channel in ordered_channels:
                for index, item in enumerate(grouped_direct[channel]):
                    rendered = dict(item)
                    if index == 0:
                        rendered["_section_label"] = (
                            _t("sidebar.direct_channel", channel=channel)
                            if channel is not None
                            else _t("sidebar.direct_unknown_channel")
                        )
                        if first_group:
                            rendered["_section_hint"] = "F8"
                        first_group = False
                    visible.append(rendered)
        return visible

    async def _render_conversation_sidebar(self) -> None:
        visible = self._sidebar_conversations(self.conversations)
        list_view = self.query_one("#conversation-list", ListView)
        existing = [
            item for item in list_view.children if isinstance(item, ConversationItem)
        ]
        existing_ids = [item.conversation_id for item in existing]
        visible_by_id = {_conversation_id(item): item for item in visible}
        visible_ids = list(visible_by_id)

        if existing and existing_ids == visible_ids:
            for item in existing:
                item.update_conversation(visible_by_id[item.conversation_id])
            return

        self._rebuilding_list = True
        await list_view.clear()
        await list_view.extend(ConversationItem(item) for item in visible)
        list_view.index = (
            visible_ids.index(self.current_conversation)
            if self.current_conversation in visible_ids
            else 0
        )
        self._rebuilding_list = False

    async def _apply_conversations(self, conversations: list[dict[str, Any]]) -> None:
        self.conversations = self._with_public(conversations)
        await self._render_conversation_sidebar()

    async def _apply_nodes(self, nodes: list[dict[str, Any]]) -> None:
        self.nodes = {str(node["node_id"]): node for node in nodes}
        visible = {
            node_id: node for node_id, node in self.nodes.items()
            if _node_matches_transport(node, self.node_transport_filter)
        }
        ordered = sorted(visible.values(), key=_node_sidebar_sort_key)
        self.query_one("#node-list-title", Static).update(
            _t("main.nodes_count", count=len(visible)) if self.node_transport_filter == "all"
            else _t("main.nodes_filtered", count=len(visible), total=len(self.nodes))
        )
        self.query_one("#node-filter-empty", Static).display = not visible
        preferred = self.selected_node_id
        if preferred not in visible:
            preferred = (
                self._conversation_peer(self.current_conversation)
                if not self._is_public_conversation(self.current_conversation)
                else str(self.status_data.get("local_node_id") or "")
            )
        node_list = self.query_one("#node-list", ListView)
        existing = [
            item for item in node_list.children if isinstance(item, NodeSidebarItem)
        ]
        existing_ids = [item.node_id for item in existing]
        incoming_ids = list(visible)
        existing_local = next(
            (item.node_id for item in existing if item.node.get("is_local")),
            None,
        )
        incoming_local = next(
            (
                str(node["node_id"])
                for node in visible.values()
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
            return

        self._rebuilding_nodes = True
        await node_list.clear()
        await node_list.extend(NodeSidebarItem(node) for node in ordered)
        ids = [str(node["node_id"]) for node in ordered]
        node_list.index = ids.index(preferred) if preferred in ids else (0 if ids else None)
        self.selected_node_id = ids[node_list.index] if node_list.index is not None else None
        self._show_node(self.nodes.get(self.selected_node_id or ""))
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
            self.notify(_t("notice.local_node"), timeout=3)
            return
        self._open_node_dm(event.item.node_id, focus_input=False)

    @on(NodeActionRequested)
    def node_action_requested(self, message: NodeActionRequested) -> None:
        self._right_click_node_id = message.node_id
        self._select_sidebar_node(message.node_id)
        self.query_one("#node-list", ListView).focus()
        self._open_node_actions(message.node_id)

    def _conversation_data(self, conversation: str) -> dict[str, Any] | None:
        return next(
            (
                item
                for item in self.conversations
                if _conversation_id(item) == conversation
            ),
            None,
        )

    def _is_public_conversation(self, conversation: str) -> bool:
        item = self._conversation_data(conversation)
        return bool(
            conversation == "public"
            or conversation.startswith("channel:")
            or (item and item.get("kind") == "public")
        )

    def _conversation_peer(self, conversation: str) -> str | None:
        item = self._conversation_data(conversation)
        peer = item.get("peer_node") if item else None
        if peer:
            return str(peer)
        if conversation.startswith("dm:"):
            return None
        return conversation if conversation.startswith("!") else None

    def _message_conversation_query(self, conversation: str) -> dict[str, Any]:
        item = self._conversation_data(conversation)
        if item and item.get("merged_routes"):
            return {
                "conversations": [
                    conversation,
                    *(
                        str(route)
                        for route in item.get("merged_routes", [])
                    ),
                ]
            }
        return {"conversation": conversation}

    def _conversation_contains_route(
        self,
        conversation: str,
        route: str,
    ) -> bool:
        if route == conversation:
            return True
        item = self._conversation_data(conversation)
        return bool(item and route in item.get("merged_routes", []))

    def select_conversation(self, conversation: str) -> None:
        self.current_conversation = conversation
        selected_data = self._conversation_data(conversation)
        title = next(
            (
                _conversation_title(item)
                for item in self.conversations
                if _conversation_id(item) == conversation
            ),
            f"DM {conversation}",
        )
        self.query_one("#conversation-title", Static).update(Text(title))
        message_input = self.query_one("#message-input", MessageInput)
        message_input.disabled = not bool(
            selected_data and selected_data.get("sendable") is True
        )
        message_input.set_history(
            self._message_history.get(
                conversation,
                self._pending_message_history.get(conversation, []),
            )
        )
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
            is_public = self._is_public_conversation(conversation)
            peer_node = self._conversation_peer(conversation)
            messages = self._call(
                {
                    "command": "messages",
                    "limit": 300,
                    "mark_read": True,
                    **self._message_conversation_query(conversation),
                }
            )["data"]
            node_actions = (
                []
                if is_public or not peer_node
                else self._call(
                    {
                        "command": "node_actions",
                        "action": "traceroute",
                        "node_id": peer_node,
                        "limit": 100,
                    }
                )["data"]
            )
            node_id = self._latest_peer(messages) if is_public else peer_node
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
                node_actions,
            )
        except Exception as exc:
            self.call_from_thread(
                self.notify,
                _t("error.load_conversation", error=exc),
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
        node_actions: list[dict[str, Any]],
    ) -> None:
        if conversation != self.current_conversation:
            return
        if conversation not in self._message_history_loaded:
            persisted_history = [
                str(message["text"])
                for message in messages
                if message.get("direction") == "ut"
                and isinstance(message.get("text"), str)
                and message["text"]
            ]
            persisted_history.extend(
                self._pending_message_history.pop(conversation, [])
            )
            self._message_history[conversation] = persisted_history
            self._message_history_loaded.add(conversation)
            self.query_one("#message-input", MessageInput).set_history(
                persisted_history
            )
        rank = {"started": 0, "completed": 1, "failed": 1}
        for action in node_actions:
            action_id = str(action.get("action_id") or "")
            if not action_id:
                continue
            previous = self.node_action_entries.get(action_id)
            if previous is not None and rank.get(
                str(previous.get("status") or "started"), 0
            ) > rank.get(str(action.get("status") or "started"), 0):
                continue
            self.node_action_entries[action_id] = dict(action)
        log = self.query_one("#message-log", RichLog)
        log.clear()
        timeline: list[tuple[str, dict[str, Any]]] = [
            ("message", message) for message in messages
        ]
        peer_node = self._conversation_peer(conversation)
        if not self._is_public_conversation(conversation) and peer_node:
            actions = sorted(
                (
                    action
                    for action in self.node_action_entries.values()
                    if action.get("node_id") == peer_node
                ),
                key=lambda item: (
                    str(item.get("started_at") or ""),
                    str(item.get("action_id") or ""),
                ),
            )
            for action in actions:
                started_at = str(action.get("started_at") or "")
                insert_at = len(timeline)
                if started_at:
                    for index, (entry_type, entry) in enumerate(timeline):
                        entry_time = str(
                            entry.get(
                                "timestamp"
                                if entry_type == "message"
                                else "started_at"
                            )
                            or ""
                        )
                        if entry_time and entry_time > started_at:
                            insert_at = index
                            break
                timeline.insert(
                    insert_at,
                    ("node_action", action),
                )
        self._visible_timeline = timeline
        self._write_visible_timeline(log)
        log.scroll_end(animate=False)
        self._show_node(node)
        if node:
            self._select_sidebar_node(str(node.get("node_id") or ""))
        for item in self.conversations:
            if _conversation_id(item) == conversation:
                item["unread"] = 0
        for item in self.query(ConversationItem):
            if item.conversation_id == conversation:
                item.conversation["unread"] = 0
                item.update_conversation(item.conversation)

    def _write_visible_timeline(self, log: RichLog) -> None:
        log.clear()
        self._timeline_time_labels = []
        for entry_type, entry in self._visible_timeline:
            self._timeline_time_labels.append(_message_time_parts(
                entry.get("timestamp" if entry_type == "message" else "started_at")
            ))
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

    def _render_message(self, message: dict[str, Any]) -> Text:
        node_id = str(message.get("from_node") or "")
        node = self.nodes.get(node_id, {})
        name = sanitize_terminal_text(
            message.get("from_long_name")
            or message.get("from_short_name")
            or node.get("long_name")
            or node.get("short_name")
            or node_id
            or _t("value.unknown")
        )
        outgoing = message.get("direction") == "ut"
        transport = str(message.get("transport") or "Ukjend")
        transport_label = _t("value.unknown_transport") if transport == "Ukjend" else transport
        text = Text()
        date_label, time_label = _message_time_parts(message.get("timestamp"))
        if date_label:
            text.append(f"{date_label} ", style="dim")
        text.append(time_label, style="cyan")
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
                _t(
                    "message.hops",
                    start=message.get("hop_start", "–"),
                    limit=message.get("hop_limit", "–"),
                )
            )
        if details:
            text.append("  " + "  ".join(details), style="dim")
        if outgoing:
            raw_status = str(message.get("status") or "sendt")
            status = {
                "motteken": _t("message.status.received"),
                "sendt": _t("message.status.sent"),
                "uviss": _t("message.status.uncertain"),
                "stadfesta": _t("message.status.acknowledged"),
                "ACK": _t("message.status.acknowledged"),
                "levert": _t("message.status.delivered"),
                "feila": _t("message.status.failed"),
            }.get(raw_status, sanitize_terminal_text(raw_status))
            metadata = message.get("raw_metadata")
            failure_reason = (
                sanitize_terminal_text(metadata.get("failure_reason"), 80)
                if raw_status == "feila" and isinstance(metadata, dict)
                else ""
            )
            if failure_reason:
                status = f"{status}: {failure_reason}"
            status_style = "bold green" if raw_status == "levert" else "dim"
            text.append(f"  [{status}]", style=status_style)
        text.append("\n  ")
        text.append(sanitize_terminal_text(message.get("text") or ""))
        text.append("\n" + "─" * 72, style="#394245")
        return text

    def _node_action_label(self, node_id: str) -> str:
        node = self.nodes.get(node_id, {})
        name = sanitize_terminal_text(
            node.get("long_name")
            or node.get("short_name")
            or node_id
            or _t("value.unknown")
        )
        return f"{name} [{node_id[-4:] if node_id else '????'}]"

    def _append_traceroute_path(self, text: Text, title: str, path: Any) -> None:
        text.append(f"{title}:\n", style="bold cyan")
        if not isinstance(path, list) or not path:
            text.append("  " + _t("node_action.not_reported") + "\n", style="dim")
            return
        for index, hop in enumerate(path):
            if not isinstance(hop, dict):
                continue
            if index:
                snr = hop.get("snr")
                snr_text = _t("node_action.unknown_snr") if snr is None else f"SNR {snr:g} dB"
                text.append(f"  ↓ {snr_text}\n", style="dim")
            text.append(
                f"  {self._node_action_label(str(hop.get('node_id') or ''))}\n"
            )

    def _render_node_action(self, action: dict[str, Any]) -> Panel:
        status = str(action.get("status") or "started")
        target = self._node_action_label(str(action.get("node_id") or ""))
        text = Text()
        date_label, time_label = _message_time_parts(action.get("started_at"))
        text.append(_t("node_action.sent") + ": ", style="dim")
        if date_label:
            text.append(f"{date_label} ", style="dim")
        text.append(f"{time_label}\n", style="cyan")
        text.append(_t("node_action.target", target=target) + "\n")
        if status == "started":
            text.append(_t("node_action.request_waiting") + "\n", style="yellow")
            text.append(
                _t("node_action.background_hint"),
                style="dim",
            )
            title = _t("node_action.panel_waiting")
            border_style = "yellow"
        elif status == "failed":
            text.append(
                sanitize_terminal_text(action.get("error") or _t("common.unknown_error")),
                style="bold red",
            )
            title = _t("node_action.panel_failed")
            border_style = "red"
        else:
            result = action.get("result")
            if isinstance(result, dict):
                self._append_traceroute_path(text, _t("field.forward"), result.get("forward"))
                text.append("\n")
                self._append_traceroute_path(text, _t("field.return"), result.get("return"))
            else:
                text.append(_t("node_action.missing_route_data"), style="yellow")
            title = _t("node_action.panel_complete")
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

    def _format_traceroute_table_row(
        self,
        action: dict[str, Any],
    ) -> tuple[str, str, str, str, str]:
        timestamp = _date_time(action.get("started_at"), multiline=True)
        status = str(action.get("status") or "started")
        status_label = {
            "started": _t("state.waiting"),
            "completed": _t("state.completed"),
            "failed": _t("state.failed"),
        }.get(status, status)
        if status == "failed":
            return (
                timestamp,
                status_label,
                "–",
                "–",
                sanitize_terminal_text(action.get("error") or _t("common.unknown_error")),
            )
        result = action.get("result")
        if status != "completed" or not isinstance(result, dict):
            return timestamp, status_label, "–", _t("node_action.waiting_for_reply"), "–"
        return (
            timestamp,
            status_label,
            self._traceroute_hop_summary(result),
            self._traceroute_path_summary(result.get("forward")),
            self._traceroute_path_summary(result.get("return")),
        )

    @staticmethod
    def _traceroute_hop_summary(result: dict[str, Any]) -> str:
        def count(path: Any) -> str:
            return str(max(0, len(path) - 1)) if isinstance(path, list) else "–"

        return f"{count(result.get('forward'))}/{count(result.get('return'))}"

    def _traceroute_path_summary(self, path: Any) -> str:
        if not isinstance(path, list) or not path:
            return _t("node_action.not_reported")
        parts = []
        for hop in path:
            if not isinstance(hop, dict):
                continue
            node_id = str(hop.get("node_id") or "")
            label = self._node_action_label(node_id)
            snr = hop.get("snr")
            if snr is not None:
                label += f" ({snr:g} dB)"
            parts.append(label)
        return " → ".join(parts) or _t("node_action.not_reported")

    def _show_node(self, node: dict[str, Any] | None) -> None:
        panel = self.query_one("#node-details", Static)
        if not node:
            panel.update(_t("main.no_node_information"))
            return
        battery = node.get("battery_level")
        bar = ""
        if isinstance(battery, int) and 0 < battery <= 100:
            filled = round(battery / 20)
            bar = "  " + "█" * filled + "░" * (5 - filled)
        can_dm = node.get("can_receive_dm")
        dm = (
            _t("value.yes")
            if can_dm is True
            else _t("value.no")
            if can_dm is False
            else _t("value.unknown")
        )
        rows = (
            (_t("field.long_name"), node.get("long_name")),
            (_t("field.short_name"), node.get("short_name")),
            (_t("field.node_id"), node.get("node_id")),
            (_t("field.hardware"), node.get("hw_model")),
            (_t("field.role"), node.get("role")),
            (_t("field.last_seen"), _date_time(node.get("last_heard"), seconds=True)),
            (_t("metric.battery"), _battery(battery) + bar),
            (_t("metric.voltage"), f"{node['voltage']} V" if node.get("voltage") else None),
            ("SNR", node.get("snr")),
            ("RSSI", node.get("rssi")),
            (_t("field.hops"), node.get("hops_away")),
            (_t("field.transport"), _node_transport_label(node)),
            (_t("field.can_receive_dm"), dm),
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
        if conversation in self._message_history_loaded:
            history = self._message_history.setdefault(conversation, [])
        else:
            history = self._pending_message_history.setdefault(conversation, [])
        history.append(text)
        self.query_one("#message-input", MessageInput).set_history(history)
        event.input.value = ""
        self.run_worker(
            lambda: self._send_worker(conversation, text),
            name="send",
            group="send",
            thread=True,
            exclusive=False,
            exit_on_error=False,
        )

    @on(events.TextSelected)
    def copy_selected_message_text(self, event: events.TextSelected) -> None:
        del event
        message_log = self.query_one("#message-log", SelectableRichLog)
        selection = self.screen.selections.get(message_log)
        if selection is None:
            return
        extracted = message_log.get_selection(selection)
        selected_text = extracted[0].rstrip("\n") if extracted is not None else ""
        if not selected_text:
            return
        self.copy_to_clipboard(selected_text)
        self.notify(_t("notice.selected_text_copied"), timeout=2)

    def _send_worker(self, conversation: str, text: str) -> None:
        selected_data = self._conversation_data(conversation)
        if not selected_data or selected_data.get("sendable") is not True:
            self.call_from_thread(
                self.notify,
                _t("error.route_not_sendable"),
                severity="error",
            )
            return
        if self._is_public_conversation(conversation):
            payload = {
                "command": "send_public",
                "text": text,
                **(
                    {"conversation": conversation}
                    if conversation.startswith("channel:")
                    else {}
                ),
            }
        else:
            peer_node = self._conversation_peer(conversation)
            if not peer_node:
                self.call_from_thread(
                    self.notify,
                    _t("error.recipient_missing"),
                    severity="error",
                )
                return
            payload = {
                "command": "send_dm",
                "node_id": peer_node,
                "text": text,
                **(
                    {"conversation": conversation}
                    if conversation.startswith("dm:")
                    else {}
                ),
            }
        try:
            self._call(payload)
        except Exception as exc:
            self.call_from_thread(
                self.notify,
                _t("error.send_failed", error=exc),
                severity="error",
                timeout=8,
            )

    def _watch_worker(self) -> None:
        while not self._watch_stop.is_set():
            try:
                if self.watcher is None:
                    return
                sock, stream = self.watcher(self.settings, "all")
                with self._watch_lock:
                    stopping = self._watch_stop.is_set()
                    if not stopping:
                        self._watch_socket = sock
                if stopping:
                    with suppress(OSError):
                        sock.shutdown(socket.SHUT_RDWR)
                    with suppress(OSError):
                        sock.close()
                    stream.close()
                    return
                try:
                    for raw in stream:
                        if self._watch_stop.is_set():
                            return
                        event = json.loads(raw)
                        if event.get("type") != "heartbeat":
                            self.call_from_thread(self.post_message, LiveEvent(event))
                finally:
                    with self._watch_lock:
                        if self._watch_socket is sock:
                            self._watch_socket = None
                    stream.close()
                    with suppress(OSError):
                        sock.close()
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
                    self._watch_stop.wait(2)

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
        if event_type in {"nodes", "channels"}:
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
        if event_type == "position":
            data = event.get("data", {})
            screen = self.screen
            if (
                isinstance(data, dict)
                and isinstance(screen, NodeInfoScreen)
                and screen.overview_data.get("node", {}).get("node_id")
                == data.get("node_id")
            ):
                screen.upsert_position(data)
            return
        if event_type == "telemetry":
            data = event.get("data", {})
            screen = self.screen
            if (
                isinstance(data, dict)
                and isinstance(screen, NodeInfoScreen)
                and screen.overview_data.get("node", {}).get("node_id")
                == data.get("node_id")
            ):
                screen.upsert_telemetry(data)
            return
        if event_type != "message":
            return
        data = event.get("data", {})
        conversation = (
            data.get("conversation_id")
            or ("public" if data.get("kind") == "public" else data.get("peer_node"))
        )
        if self._conversation_contains_route(
            self.current_conversation,
            str(conversation),
        ):
            self._visible_timeline.append(("message", data))
            self._timeline_time_labels.append(_message_time_parts(data.get("timestamp")))
            self.query_one("#message-log", RichLog).write(
                self._render_message(data),
                scroll_end=True,
            )
            if data.get("direction") == "inn":
                selected_conversation = self.current_conversation
                self.run_worker(
                    lambda: self._mark_conversation_read_worker(
                        selected_conversation
                    ),
                    name=f"mark-read-{conversation}",
                    group="mark-read",
                    thread=True,
                    exclusive=True,
                    exit_on_error=False,
                )
        elif data.get("kind") == "dm" and data.get("direction") == "inn":
            node_id = str(data.get("from_node") or "")
            node = self.nodes.get(node_id, {})
            name = node.get("long_name") or node.get("short_name") or node_id
            self.notify(
                _t("notice.new_dm", name=name, text=data.get("text", "")),
                timeout=6,
            )
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
            conversations = self._call(
                {
                    "command": "conversations",
                    "preferred_conversation": self.current_conversation,
                }
            )["data"]
            nodes = self._call({"command": "nodes", "sort": "seen"})["data"]
            self.call_from_thread(self._apply_refresh, conversations, nodes)
        except Exception:
            return

    def _mark_conversation_read_worker(self, conversation: str) -> None:
        try:
            self._call(
                {
                    "command": "messages",
                    "limit": 1,
                    "mark_read": True,
                    **self._message_conversation_query(conversation),
                }
            )
            conversations = self._call(
                {
                    "command": "conversations",
                    "preferred_conversation": self.current_conversation,
                }
            )["data"]
            self.call_from_thread(self._apply_conversations, conversations)
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
        if isinstance(self.screen, ModalScreen):
            if direction > 0:
                self.screen.focus_next()
            else:
                self.screen.focus_previous()
            return
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
        if not self.query_one("#conversation-panel", Vertical).display:
            self.query_one("#message-input", Input).focus()
            self.notify(_t("notice.conversation_list_hidden"), timeout=3)
            return
        self.query_one("#conversation-list", ListView).focus()

    def action_focus_nodes(self) -> None:
        node_panel = self.query_one("#node-panel", Vertical)
        if not node_panel.display:
            self.action_new_dm()
            return
        self.query_one("#node-list", ListView).focus()

    async def action_toggle_direct_messages(self) -> None:
        self.show_direct_messages = not self.show_direct_messages
        await self._render_conversation_sidebar()
        self.notify(
            _t(
                "notice.dm_visibility",
                state=_t("value.shown") if self.show_direct_messages else _t("value.hidden"),
            ),
            timeout=2,
        )

    async def action_toggle_channels(self) -> None:
        self.show_secondary_channels = not self.show_secondary_channels
        await self._render_conversation_sidebar()
        self.notify(
            _t(
                "notice.channel_visibility",
                state=(
                    _t("value.shown")
                    if self.show_secondary_channels
                    else _t("value.hidden")
                ),
            ),
            timeout=3,
        )

    def action_node_actions(self) -> None:
        node_list = self.query_one("#node-list", ListView)
        if not node_list.has_focus:
            self.notify(_t("notice.select_node_first"), timeout=3)
            return
        selected = node_list.highlighted_child
        if not isinstance(selected, NodeSidebarItem):
            self.notify(_t("notice.no_node_marked"), timeout=3)
            return
        self._open_node_actions(selected.node_id)

    def _open_node_actions(self, node_id: str) -> None:
        node = self.nodes.get(node_id)
        if node is None:
            self.notify(_t("error.marked_node_missing"), severity="error")
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
                "reason": _t("error.check_traceroute", error=exc),
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
        elif action == "node_info":
            self.run_worker(
                lambda: self._node_info_worker(node_id),
                name=f"node-info-{node_id}",
                group="node-info",
                thread=True,
                exclusive=True,
                exit_on_error=False,
            )
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

    def _node_info_worker(self, node_id: str) -> None:
        try:
            overview = self._call(
                {"command": "node_overview", "node_id": node_id}
            )["data"]
            latest = overview.get("latest_telemetry", {})
            kinds = list(latest) if isinstance(latest, dict) else []
            if kinds:
                telemetry = []
                for kind in kinds:
                    telemetry.extend(
                        self._call(
                            {
                                "command": "node_telemetry",
                                "node_id": node_id,
                                "kind": str(kind),
                                "limit": 200,
                            }
                        )["data"]
                    )
            else:
                telemetry = self._call(
                    {
                        "command": "node_telemetry",
                        "node_id": node_id,
                        "limit": 200,
                    }
                )["data"]
            positions = self._call(
                {
                    "command": "node_positions",
                    "node_id": node_id,
                    "limit": 100,
                }
            )["data"]
            traceroutes = self._call(
                {
                    "command": "node_actions",
                    "action": "traceroute",
                    "node_id": node_id,
                    "limit": 100,
                }
            )["data"]
            action_availability = {
                action: self._call(
                    {
                        "command": "node_action_availability",
                        "action": action,
                        "node_id": node_id,
                    }
                )["data"]
                for action in ("traceroute", "position_exchange")
            }
        except Exception as exc:
            self.call_from_thread(
                self.notify,
                _t("error.load_node_info", error=exc),
                severity="error",
                timeout=8,
            )
            return
        self.call_from_thread(
            self.push_screen,
            NodeInfoScreen(
                overview,
                telemetry,
                positions,
                traceroutes,
                self._format_traceroute_table_row,
                lambda action: self._node_info_action_requested(node_id, action),
                action_availability,
            ),
        )

    def _node_info_action_requested(self, node_id: str, action: str) -> None:
        self.run_worker(
            lambda: self._start_node_action_worker(node_id, action),
            name=f"node-info-action-{action}",
            group=f"node-info-action-{action}",
            thread=True,
            exclusive=True,
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
            label = (
                _t("action.traceroute")
                if action == "traceroute"
                else _t("action.position_exchange")
            )
            self.call_from_thread(
                self.notify,
                _t("error.start_action", action=label, error=exc),
                severity="error",
                timeout=8,
            )
            self.call_from_thread(
                self._clear_node_info_action_cooldown,
                node_id,
                action,
            )
            return
        self.call_from_thread(self._handle_node_action, data)

    def _clear_node_info_action_cooldown(
        self,
        node_id: str,
        action: str,
    ) -> None:
        screen = self.screen
        if (
            isinstance(screen, NodeInfoScreen)
            and screen.overview_data.get("node", {}).get("node_id") == node_id
        ):
            screen.clear_action_cooldown(action)

    def _handle_node_action(self, action: dict[str, Any]) -> None:
        action_name = str(action.get("action") or "")
        if action_name not in {"traceroute", "position_exchange"}:
            return
        action_id = str(action.get("action_id") or "")
        if not action_id:
            return
        status = str(action.get("status") or "started")
        node_id = str(action.get("node_id") or "")
        if action_name == "position_exchange":
            label = self._node_action_label(node_id)
            share_notice = self._position_share_notice(action)
            if status == "started":
                message = _t("notice.position_request_sent", target=label)
                if share_notice:
                    message += f" {share_notice}"
                self.notify(
                    message,
                    timeout=8,
                )
            elif status == "failed":
                self.notify(
                    _t(
                        "notice.position_failed",
                        error=action.get("error", _t("common.unknown_error")),
                    ),
                    severity="error",
                    timeout=10,
                )
            else:
                message = _t("notice.position_reply", target=label)
                if share_notice:
                    message += f" {share_notice}"
                self.notify(
                    message,
                    timeout=10,
                )
            return

        previous = self.node_action_entries.get(action_id)
        rank = {"started": 0, "completed": 1, "failed": 1}
        if previous is not None:
            previous_status = str(previous.get("status") or "started")
            if rank.get(previous_status, 0) > rank.get(status, 0):
                return
        if previous == action:
            return
        self.node_action_entries[action_id] = dict(action)
        screen = self.screen
        if (
            isinstance(screen, NodeInfoScreen)
            and screen.overview_data.get("node", {}).get("node_id") == node_id
        ):
            screen.upsert_traceroute(action)
        if node_id == self._conversation_peer(self.current_conversation):
            self.select_conversation(self.current_conversation)
        if status == "started":
            self.notify(
                _t("notice.traceroute_sent", target=self._node_action_label(node_id)),
                timeout=5,
            )
            return
        if status == "failed":
            self.notify(
                _t(
                    "notice.traceroute_failed",
                    error=action.get("error", _t("common.unknown_error")),
                ),
                severity="error",
                timeout=10,
            )
            return
        if node_id != self._conversation_peer(self.current_conversation):
            self.notify(
                _t("notice.traceroute_complete", target=self._node_action_label(node_id)),
                timeout=7,
            )

    @staticmethod
    def _position_share_notice(action: dict[str, Any]) -> str:
        result = action.get("result")
        details = result if isinstance(result, dict) else action
        if "local_position_shared" not in details:
            return ""
        if details.get("local_position_shared"):
            precision = details.get("local_position_precision_bits")
            if precision is not None:
                return (
                    _t("notice.position_shared_precision", precision=precision)
                )
            return _t("notice.position_shared")
        reason = sanitize_terminal_text(
            details.get("local_position_share_reason") or ""
        )
        suffix = f" ({reason})" if reason else ""
        return _t("notice.position_not_shared", suffix=suffix)

    def action_new_dm(self) -> None:
        channels = [
            item
            for item in self.conversations
            if item.get("kind") == "public"
        ]
        self.push_screen(
            NewDMScreen(list(self.nodes.values()), channels),
            self._open_new_dm,
        )

    def _open_new_dm(self, selected: tuple[str, str] | None) -> None:
        if selected is None:
            return
        node_id, channel_key = selected
        self._open_node_dm(
            node_id,
            focus_input=True,
            channel_key=channel_key,
        )

    def _open_node_dm(
        self,
        node_id: str,
        *,
        focus_input: bool,
        channel_key: str | None = None,
    ) -> None:
        normalized_node_id = normalize_node_id(node_id)
        route = next(
            (
                item
                for item in self.conversations
                if item.get("kind") == "public"
                and item.get("sendable") is True
                and item.get("local_node_id")
                and item.get("channel_key")
                and (
                    item.get("channel_key") == channel_key
                    if channel_key is not None
                    else item.get("channel") == 0
                )
            ),
            None,
        )
        if route is None:
            self.notify(
                (
                    _t("error.selected_channel_unavailable")
                    if channel_key is not None
                    else _t("error.primary_channel_unavailable")
                ),
                severity="error",
            )
            return
        conversation = dm_conversation_id(
            str(route["local_node_id"]),
            normalized_node_id,
            str(route["channel_key"]),
        )
        if self._conversation_data(conversation) is None:
            self.conversations.append(
                {
                    "conversation": conversation,
                    "kind": "dm",
                    "peer_node": normalized_node_id,
                    "local_node_id": route["local_node_id"],
                    "channel_key": route["channel_key"],
                    "channel": route.get("channel"),
                    "channel_name": route.get("channel_name"),
                    "last_timestamp": None,
                    "last_text": None,
                    "unread": 0,
                    "sendable": True,
                    "_transient": True,
                }
            )
        self.current_conversation = conversation
        self.run_worker(
            lambda: self._unarchive_and_refresh_worker(
                normalized_node_id,
                conversation,
            ),
            name="new-dm-refresh",
            group="refresh",
            thread=True,
            exclusive=True,
            exit_on_error=False,
        )
        self.select_conversation(conversation)
        if focus_input:
            self.query_one("#message-input", Input).focus()

    def _unarchive_and_refresh_worker(
        self,
        node_id: str,
        conversation: str,
    ) -> None:
        with suppress(Exception):
            self._call(
                {
                    "command": "unarchive_conversation",
                    "node_id": node_id,
                    "conversation": conversation,
                }
            )
        self._refresh_lists_worker()

    def action_archive_conversation(self) -> None:
        conversation_list = self.query_one("#conversation-list", ListView)
        if not conversation_list.has_focus:
            return
        selected = conversation_list.highlighted_child
        if not isinstance(selected, ConversationItem):
            return
        if selected.conversation.get("kind") == "public":
            self.notify(_t("error.public_cannot_close"), timeout=3)
            return
        conversation = selected.conversation_id
        node_id = str(
            selected.conversation.get("peer_node")
            or self._conversation_peer(conversation)
            or ""
        )
        if not node_id:
            self.notify(_t("error.recipient_missing"), severity="error")
            return
        self.run_worker(
            lambda: self._archive_conversation_worker(node_id, conversation),
            name=f"archive-{conversation}",
            group="archive",
            thread=True,
            exclusive=True,
            exit_on_error=False,
        )

    def _archive_conversation_worker(
        self,
        node_id: str,
        conversation: str,
    ) -> None:
        try:
            self._call(
                {
                    "command": "archive_conversation",
                    "node_id": node_id,
                    "conversation": conversation,
                }
            )
            conversations = self._call({"command": "conversations"})["data"]
            self.call_from_thread(
                self._finish_archive,
                conversation,
                conversations,
            )
        except Exception as exc:
            self.call_from_thread(
                self.notify,
                _t("error.close_conversation", error=exc),
                severity="error",
            )

    async def _finish_archive(
        self,
        conversation: str,
        conversations: list[dict[str, Any]],
    ) -> None:
        if self.current_conversation == conversation:
            self.current_conversation = "public"
        await self._apply_conversations(conversations)
        self.select_conversation(self.current_conversation)
        self.query_one("#conversation-list", ListView).focus()
        self.notify(_t("notice.conversation_closed"), timeout=5)

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
            self.notify(_t("notice.no_update_command"))
            return
        self.copy_to_clipboard(self.update_notice.command)
        self.notify(_t("notice.update_command_copied"))

    def action_settings(self) -> None:
        if isinstance(self.screen, (StoredNodeScreen, RemoveNodeScreen)):
            return
        if isinstance(self.screen, SettingsScreen):
            self.screen.action_cancel()
            return
        focused = self.focused
        message_input = self.query_one("#message-input", MessageInput)
        self._settings_snapshot = {
            "focused_id": focused.id if focused is not None else None,
            "draft": message_input.value,
            "cursor": message_input.cursor_position,
            "selected_node": self.selected_node_id,
            "current_conversation": self.current_conversation,
        }
        screen = SettingsScreen(get_language())
        screen.node_message = self._last_node_removal_message
        self.push_screen(screen, self._finish_settings)

    def load_local_node_info(self, screen: SettingsScreen) -> None:
        def load() -> None:
            try:
                info = self._call({"command": "local_node_info"})["data"]
                error = None
            except Exception as exc:
                info, error = None, str(exc)

            def apply() -> None:
                if self.screen is screen and screen.is_mounted:
                    screen.update_info(info, error)

            self.call_from_thread(apply)

        self.run_worker(
            load, name="local-node-info", group="local-node-info", thread=True,
            exclusive=True, exit_on_error=False,
        )

    def remove_local_registry_nodes(
        self, screen: SettingsScreen, node_ids: list[str], gateway: str,
    ) -> None:
        def remove() -> None:
            result = None
            error = None
            try:
                result = self._call({
                    "command": "remove_nodes", "node_ids": node_ids,
                    "expected_local_node_id": gateway,
                })["data"]
                deadline = time.monotonic() + 60 * len(node_ids) + 30
                while result.get("status") == "started":
                    if time.monotonic() >= deadline:
                        raise TimeoutError(_t("local.nodes.unverified"))
                    time.sleep(0.3)
                    result = self._call({
                        "command": "node_action_status", "action_id": result["action_id"],
                    })["data"]
                if not result.get("result", {}).get("contacts"):
                    error = str(result.get("error") or _t("local.nodes.unverified"))
            except Exception as exc:
                error = str(exc)

            def apply() -> None:
                self._last_node_removal_message = screen.removal_message(result, error)
                if screen.is_mounted:
                    screen.removal_finished(result, error)
                if not screen.is_mounted or error:
                    self.notify(
                        self._last_node_removal_message,
                        severity="warning" if error or (result or {}).get("status") != "completed"
                        else "information", timeout=10,
                    )

            self.call_from_thread(apply)
            self._refresh_lists_worker()

        self.run_worker(
            remove, name="remove-local-nodes", group="remove-local-nodes",
            thread=True, exit_on_error=False,
        )

    def _finish_settings(self, language: str | None) -> None:
        if language is None or language == get_language():
            self._settings_snapshot = None
            return
        try:
            set_language(language, persist=True)
        except OSError as exc:
            self.notify(_t("error.save_language", error=exc), severity="error")
            return
        self.run_worker(
            self._refresh_language(),
            name="language-refresh",
            group="language-refresh",
            exclusive=True,
            exit_on_error=False,
        )

    async def _refresh_language(self) -> None:
        message_input = self.query_one("#message-input", MessageInput)
        snapshot = self._settings_snapshot or {}
        self._settings_snapshot = None
        focused_id = snapshot.get("focused_id")
        draft = str(snapshot.get("draft", message_input.value))
        cursor = int(snapshot.get("cursor", message_input.cursor_position))
        selected_node = snapshot.get("selected_node", self.selected_node_id)
        current_conversation = str(
            snapshot.get("current_conversation", self.current_conversation)
        )

        self.title = _t("main.title")
        self.sub_title = _t("main.subtitle")
        self._localize_app_bindings()
        self.query_one("#conversation-panel-title", Static).update(
            _t("main.conversations")
        )
        self.query_one("#node-panel-title", Static).update(_t("main.node_details"))
        node_filter = self.query_one("#node-transport-filter", Select)
        node_filter.set_options(self._node_filter_options())
        node_filter.value = self.node_transport_filter
        node_filter.tooltip = _t("node_filter.help")
        self.query_one("#node-filter-empty", Static).update(_t("node_filter.empty"))
        self.query_one("#input-help", Static).update(_t("main.input_help"))
        self.query_one("#key-bar", Static).update(_t("main.key_bar"))
        message_input.placeholder = _t("main.message_placeholder")

        await self._render_conversation_sidebar()
        await self._apply_nodes(list(self.nodes.values()))
        self.current_conversation = current_conversation
        self.selected_node_id = selected_node
        self.select_conversation(current_conversation)
        if selected_node and selected_node in self.nodes:
            self._select_sidebar_node(selected_node)
            self._show_node(self.nodes[selected_node])
        self._update_status_bar()

        message_input.value = draft
        if focused_id:
            try:
                self.query_one(f"#{focused_id}").focus()
            except NoMatches:
                message_input.focus()
        message_input.cursor_position = min(cursor, len(draft))
        self.notify(
            _t(
                "settings.saved",
                language="Nynorsk" if get_language() == "nn" else "English",
            ),
            timeout=3,
        )

    def action_show_help(self) -> None:
        if isinstance(self.screen, HelpScreen):
            self.screen.dismiss(None)
        else:
            self.push_screen(HelpScreen())

    def action_quit(self) -> None:
        if isinstance(self.screen, QuitScreen):
            return
        self.push_screen(QuitScreen(self.settings.background_mode), self._finish_quit)

    def _finish_quit(self, result: str | None) -> None:
        if result is not None:
            self.exit(result)


def run_tui(settings: Settings) -> str | None:
    return MeshPiTUI(settings).run()
