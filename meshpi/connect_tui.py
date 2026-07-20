from __future__ import annotations

from typing import Any

from rich.text import Text
from textual import on
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Container
from textual.widgets import Input, ListItem, ListView, Static

from meshpi.client import request
from meshpi.config import Settings


def build_connection_choices(data: dict[str, Any]) -> list[dict[str, Any]]:
    active_id = str(data.get("active_profile_id", ""))
    choices: list[dict[str, Any]] = []
    seen: set[tuple[str, str]] = set()

    for profile in data.get("profiles", []):
        profile_id = str(profile.get("profile_id", ""))
        endpoint = str(profile.get("endpoint", ""))
        key = (str(profile.get("transport", "")), endpoint)
        seen.add(key)
        choices.append(
            {
                "section": "Lagra",
                "name": profile.get("name") or endpoint,
                "transport": profile.get("transport"),
                "endpoint": endpoint,
                "profile_id": profile_id,
                "active": profile_id == active_id,
                "search": " ".join(str(value or "") for value in profile.values()),
            }
        )

    for section, entries in (
        ("USB / seriell", data.get("serial", [])),
        ("TCP på lokalnettet", data.get("tcp", [])),
    ):
        for entry in entries:
            transport = str(entry.get("transport", ""))
            endpoint = str(entry.get("device") or entry.get("target") or "")
            if (transport, endpoint) in seen:
                continue
            seen.add((transport, endpoint))
            choices.append(
                {
                    "section": section,
                    "name": entry.get("name") or endpoint,
                    "transport": transport,
                    "endpoint": endpoint,
                    "target": entry.get("target") or endpoint,
                    "active": False,
                    "search": " ".join(str(value or "") for value in entry.values()),
                }
            )
    return choices


class ConnectionItem(ListItem):
    def __init__(self, choice: dict[str, Any]):
        self.choice = choice
        super().__init__(Static(self._label(), classes="connection-label"))

    def _label(self) -> Text:
        text = Text()
        if self.choice.get("active"):
            text.append("● ", style="bold green")
        else:
            text.append("○ ", style="dim")
        text.append(str(self.choice["name"]), style="bold")
        text.append(
            f"  {str(self.choice.get('transport', '')).upper()}",
            style="cyan",
        )
        text.append("\n")
        text.append(
            f"  {self.choice['section']}  •  {self.choice['endpoint']}",
            style="dim",
        )
        return text


class ConnectionPickerApp(App[dict[str, Any] | None]):
    TITLE = "MeshPi – ny tilkopling"
    ENABLE_COMMAND_PALETTE = False
    BINDINGS = [
        Binding("escape", "cancel", "Avbryt", priority=True),
        Binding("ctrl+q", "cancel", "Avbryt", priority=True),
        Binding("down", "next_choice", "Neste", priority=True),
        Binding("up", "previous_choice", "Førre", priority=True),
    ]

    CSS = """
    Screen {
        background: #090c0d;
        color: #d3d8da;
        align: center middle;
    }

    #picker {
        width: 88;
        max-width: 98%;
        height: 38;
        max-height: 96%;
        padding: 1 2;
        border: round #58d65c;
        background: #0d1112;
    }

    #picker-title {
        height: 2;
        color: #58d65c;
        text-style: bold;
    }

    #picker-description {
        height: 2;
        color: #9ba4a7;
    }

    #connection-input {
        height: 3;
        border: round #536166;
        background: #090c0d;
    }

    #connection-input:focus {
        border: round #58d65c;
    }

    #choice-count {
        height: 1;
        margin: 0 1;
        color: #8d9699;
    }

    #connection-list {
        height: 1fr;
        margin: 1 0;
        border: round #394245;
        background: #0d1112;
    }

    ConnectionItem {
        height: 3;
        padding: 0 1;
    }

    ConnectionItem.--highlight {
        background: #245c2a;
        color: white;
    }

    .connection-label {
        width: 1fr;
        height: 2;
    }

    #picker-help {
        height: 1;
        color: #8d9699;
    }
    """

    def __init__(self, discovery: dict[str, Any]):
        super().__init__()
        self.all_choices = build_connection_choices(discovery)
        self.filtered_choices = list(self.all_choices)

    def compose(self) -> ComposeResult:
        with Container(id="picker"):
            yield Static("Vel Meshtastic-tilkopling", id="picker-title")
            yield Static(
                "Vel ei oppdaga/lagra eining, eller skriv IP, vertsnamn eller seriellsti.",
                id="picker-description",
            )
            yield Input(
                placeholder="10.0.0.135, meshtastic.local eller /dev/serial/by-id/…",
                id="connection-input",
            )
            yield Static("", id="choice-count")
            with ListView(id="connection-list"):
                yield from (ConnectionItem(choice) for choice in self.filtered_choices)
            yield Static(
                "Skriv: filtrer/manuelt mål   ↑/↓: vel   Enter: kopla til   Esc: avbryt",
                id="picker-help",
            )

    def on_mount(self) -> None:
        choices = self.query_one("#connection-list", ListView)
        choices.index = 0 if self.filtered_choices else None
        self._update_count()
        self.query_one("#connection-input", Input).focus()

    def _update_count(self) -> None:
        shown = len(self.filtered_choices)
        total = len(self.all_choices)
        label = f"{shown} av {total} tilkoplingar" if shown != total else f"{total} tilkoplingar"
        self.query_one("#choice-count", Static).update(label)

    @on(Input.Changed, "#connection-input")
    async def filter_choices(self, event: Input.Changed) -> None:
        query = event.value.strip().casefold()
        self.filtered_choices = [
            choice
            for choice in self.all_choices
            if not query
            or query
            in f"{choice['name']} {choice['endpoint']} {choice['search']}".casefold()
        ]
        choices = self.query_one("#connection-list", ListView)
        await choices.clear()
        await choices.extend(ConnectionItem(choice) for choice in self.filtered_choices)
        choices.index = 0 if self.filtered_choices else None
        self._update_count()

    @on(Input.Submitted, "#connection-input")
    def submit_input(self, event: Input.Submitted) -> None:
        target = event.value.strip()
        if target and not self.filtered_choices:
            self.exit({"target": target})
            return
        selected = self.query_one("#connection-list", ListView).highlighted_child
        if isinstance(selected, ConnectionItem):
            self.exit(self._result(selected.choice))
            return
        if target:
            self.exit({"target": target})
        else:
            self.notify("Vel ei tilkopling eller skriv eit mål", severity="error")

    @on(ListView.Selected, "#connection-list")
    def select_choice(self, event: ListView.Selected) -> None:
        if isinstance(event.item, ConnectionItem):
            self.exit(self._result(event.item.choice))

    @staticmethod
    def _result(choice: dict[str, Any]) -> dict[str, Any]:
        if choice.get("profile_id"):
            return {"profile_id": choice["profile_id"]}
        return {
            "target": choice["target"],
            "name": choice.get("name"),
        }

    def _move(self, direction: int) -> None:
        choices = self.query_one("#connection-list", ListView)
        count = len(self.filtered_choices)
        if not count:
            return
        current = choices.index if choices.index is not None else 0
        choices.index = (current + direction) % count

    def action_next_choice(self) -> None:
        self._move(1)

    def action_previous_choice(self) -> None:
        self._move(-1)

    def action_cancel(self) -> None:
        self.exit(None)


def choose_connection(
    settings: Settings,
    requester=request,
) -> dict[str, Any] | None:
    discovery = requester(settings, {"command": "discover_connections"})["data"]
    return ConnectionPickerApp(discovery).run()
