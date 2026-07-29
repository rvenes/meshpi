from __future__ import annotations

from contextlib import suppress
from typing import Any

from rich.text import Text
from textual import on
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Container
from textual.widgets import Input, ListItem, ListView, Static

from meshpi.client import request
from meshpi.config import Settings
from meshpi.connections import canonical_ble_identifier

DISCOVERY_TIMEOUT_SECONDS = 30


def _serial_profile_available(
    profile: dict[str, Any],
    serial_entries: list[dict[str, Any]],
) -> bool:
    serial_number = str(profile.get("serial_number") or "").strip()
    if serial_number:
        profile_vid = profile.get("vid")
        profile_pid = profile.get("pid")
        return any(
            str(entry.get("serial_number") or "").strip() == serial_number
            and (profile_vid is None or entry.get("vid") == profile_vid)
            and (profile_pid is None or entry.get("pid") == profile_pid)
            for entry in serial_entries
        )

    endpoint = str(profile.get("device") or profile.get("endpoint") or "").strip()
    if not endpoint:
        return False
    return any(
        endpoint
        in {
            str(entry.get("target") or ""),
            str(entry.get("device") or ""),
            str(entry.get("system_device") or ""),
        }
        for entry in serial_entries
    )


def _ble_profile_available(
    profile: dict[str, Any],
    ble_entries: list[dict[str, Any]],
) -> bool:
    identifier = str(
        profile.get("ble_identifier") or profile.get("endpoint") or ""
    ).strip()
    canonical_identifier = canonical_ble_identifier(identifier)
    return bool(identifier) and any(
        canonical_identifier
        == canonical_ble_identifier(
            str(
                entry.get("ble_identifier")
                or str(entry.get("target") or "").removeprefix("ble://")
            )
        )
        for entry in ble_entries
    )


def _connection_key(transport: str, endpoint: str) -> tuple[str, str]:
    return (
        transport,
        canonical_ble_identifier(endpoint) if transport == "ble" else endpoint,
    )


def build_connection_choices(
    data: dict[str, Any],
    *,
    include_all_serial: bool = False,
) -> list[dict[str, Any]]:
    active_id = str(data.get("active_profile_id", ""))
    choices: list[dict[str, Any]] = []
    seen: set[tuple[str, str]] = set()
    serial_entries = list(data.get("serial", []))
    ble_entries = list(data.get("ble", []))
    serial_scanned = bool(data.get("serial_scanned", True))
    ble_scanned = bool(data.get("ble_scanned", True))
    saved_available: list[dict[str, Any]] = []
    saved_unavailable: list[dict[str, Any]] = []
    available_serial_profiles: list[dict[str, Any]] = []

    for profile in data.get("profiles", []):
        profile_id = str(profile.get("profile_id", ""))
        endpoint = str(profile.get("endpoint", ""))
        transport = str(profile.get("transport", ""))
        available = (
            (
                _serial_profile_available(profile, serial_entries)
                if serial_scanned
                else None
            )
            if transport == "serial"
            else (
                (
                    _ble_profile_available(profile, ble_entries)
                    if ble_scanned
                    else None
                )
                if transport == "ble"
                else None
            )
        )
        choice = {
            "section": "Lagra",
            "name": profile.get("name") or endpoint,
            "transport": transport,
            "endpoint": endpoint,
            "profile_id": profile_id,
            "active": profile_id == active_id,
            "available": available,
            "search": " ".join(str(value or "") for value in profile.values()),
        }
        if available is False:
            saved_unavailable.append(choice)
        else:
            saved_available.append(choice)
            if transport == "serial":
                available_serial_profiles.append(profile)

    saved_available.sort(
        key=lambda choice: (
            not bool(choice["active"]),
            str(choice["name"]).casefold(),
        )
    )
    saved_unavailable.sort(
        key=lambda choice: (
            not bool(choice["active"]),
            str(choice["name"]).casefold(),
        )
    )
    for choice in saved_available:
        seen.add(
            _connection_key(
                str(choice["transport"]),
                str(choice["endpoint"]),
            )
        )
        choices.append(choice)

    for section, entries in (
        ("USB / seriell", data.get("serial", [])),
        ("Bluetooth / BLE", data.get("ble", [])),
        ("TCP på lokalnettet", data.get("tcp", [])),
    ):
        for entry in entries:
            transport = str(entry.get("transport", ""))
            if (
                transport == "serial"
                and not include_all_serial
                and entry.get("recommended") is False
            ):
                continue
            if transport == "serial" and any(
                _serial_profile_available(profile, [entry])
                for profile in available_serial_profiles
            ):
                continue
            endpoint = str(
                entry.get("device")
                or entry.get("ble_identifier")
                or entry.get("target")
                or ""
            )
            key = _connection_key(transport, endpoint)
            if key in seen:
                continue
            seen.add(key)
            choices.append(
                {
                    "section": section,
                    "name": entry.get("name") or endpoint,
                    "transport": transport,
                    "endpoint": endpoint,
                    "target": entry.get("target") or endpoint,
                    "active": False,
                    "available": True,
                    "search": " ".join(str(value or "") for value in entry.values()),
                }
            )
    choices.extend(saved_unavailable)
    return choices


class ConnectionItem(ListItem):
    def __init__(self, choice: dict[str, Any]):
        self.choice = choice
        super().__init__(Static(self._label(), classes="connection-label"))

    def _label(self) -> Text:
        text = Text()
        unavailable = self.choice.get("available") is False
        if unavailable:
            text.append("◌ ", style="bold yellow")
        elif self.choice.get("active"):
            text.append("● ", style="bold green")
        else:
            text.append("○ ", style="dim")
        text.append(str(self.choice["name"]), style="bold")
        text.append(
            f"  {str(self.choice.get('transport', '')).upper()}",
            style="cyan",
        )
        if unavailable:
            text.append("  IKKJE TILKOPLA", style="bold yellow")
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
        Binding("f4", "toggle_serial_ports", "Alle serieportar", priority=True),
        Binding("f5", "refresh_discovery", "Søk på nytt", priority=True),
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

    #discovery-status {
        height: 2;
        color: #e4c65b;
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

    def __init__(
        self,
        discovery: dict[str, Any],
        *,
        settings: Settings | None = None,
        requester=request,
        auto_discover: bool = False,
    ):
        super().__init__()
        self.discovery = discovery
        self.settings = settings
        self.requester = requester
        self.auto_discover = auto_discover
        self.show_all_serial = False
        self._discovering = False
        self._discovery_generation = 0
        self._closed = False
        self.all_choices = build_connection_choices(discovery)
        choices_with_all_serial = build_connection_choices(
            discovery,
            include_all_serial=True,
        )
        self.hidden_serial_count = len(choices_with_all_serial) - len(self.all_choices)
        self.filtered_choices = list(self.all_choices)

    def compose(self) -> ComposeResult:
        description = (
            "Vel ei oppdaga/lagra eining, eller skriv IP, vertsnamn, "
            "seriellsti eller ble://identifikator."
        )
        with Container(id="picker"):
            yield Static("Vel Meshtastic-tilkopling", id="picker-title")
            yield Static(description, id="picker-description")
            yield Static("", id="discovery-status")
            yield Input(
                placeholder="IP, vertsnamn, seriellsti eller ble://identifikator",
                id="connection-input",
            )
            yield Static("", id="choice-count")
            with ListView(id="connection-list"):
                yield from (ConnectionItem(choice) for choice in self.filtered_choices)
            yield Static(
                "Skriv: filtrer/mål   ↑/↓: vel   Enter: kopla til   "
                "F5: søk på nytt   Esc: avbryt",
                id="picker-help",
            )

    def on_mount(self) -> None:
        choices = self.query_one("#connection-list", ListView)
        choices.index = 0 if self.filtered_choices else None
        self._update_count()
        self._update_discovery_status()
        self.query_one("#connection-input", Input).focus()
        if self.auto_discover:
            self._start_discovery()

    def on_unmount(self) -> None:
        self._closed = True
        self._discovery_generation += 1

    def _update_discovery_status(self) -> None:
        status = self.query_one("#discovery-status", Static)
        local_error = str(self.discovery.get("local_error") or "").strip()
        local_prefix = (
            f"Lokal oppdaging feila: {local_error}  ·  "
            if local_error
            else ""
        )
        if self.discovery.get("ble_scanning"):
            status.update(
                f"{local_prefix}Bluetooth / BLE: Søkjer … "
                "(tek vanlegvis om lag 10 sekund)"
            )
            return
        error = str(self.discovery.get("ble_error") or "").strip()
        if error:
            status.update(
                f"{local_prefix}Bluetooth / BLE: {error}  ·  F5: søk på nytt"
            )
            return
        ble_scanned = self.discovery.get(
            "ble_scanned",
            "ble" in self.discovery,
        )
        if not ble_scanned:
            status.update(
                f"{local_prefix}Bluetooth / BLE: Ventar på søk  ·  F5: start søk"
            )
            return
        count = len(self.discovery.get("ble", []))
        if count:
            suffix = "eining funnen" if count == 1 else "einingar funne"
            status.update(
                f"{local_prefix}Bluetooth / BLE: {count} {suffix}  ·  "
                "F5: søk på nytt"
            )
        else:
            status.update(
                f"{local_prefix}Bluetooth / BLE: Ingen Meshtastic-einingar "
                "funne  ·  "
                "F5: søk på nytt"
            )

    def _update_count(self) -> None:
        shown = len(self.filtered_choices)
        total = len(self.all_choices)
        label = f"{shown} av {total} tilkoplingar" if shown != total else f"{total} tilkoplingar"
        if self.hidden_serial_count and not self.show_all_serial:
            label += f" · {self.hidden_serial_count} serieportar skjulte (F4)"
        self.query_one("#choice-count", Static).update(label)

    async def _replace_choices(self, query: str) -> None:
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

    async def _rebuild_choices(self) -> None:
        self.all_choices = build_connection_choices(
            self.discovery,
            include_all_serial=self.show_all_serial,
        )
        choices_with_all_serial = build_connection_choices(
            self.discovery,
            include_all_serial=True,
        )
        self.hidden_serial_count = len(choices_with_all_serial) - len(
            build_connection_choices(self.discovery)
        )
        query = self.query_one("#connection-input", Input).value.strip().casefold()
        await self._replace_choices(query)

    def _start_discovery(self) -> None:
        if self._discovering:
            self.notify("Eit BLE-søk er allereie i gang")
            return
        if self.settings is None:
            self.notify("Oppdaging er ikkje tilgjengeleg", severity="error")
            return
        self._discovering = True
        self._discovery_generation += 1
        generation = self._discovery_generation
        self.discovery["ble_scanning"] = True
        self.discovery["ble_error"] = None
        self.discovery["local_error"] = None
        self.discovery["ble_scanned"] = False
        self.discovery["ble"] = []
        self._update_discovery_status()
        self.run_worker(
            lambda: self._discovery_worker(generation),
            name="connection-discovery",
            group="connection-discovery",
            thread=True,
            exclusive=True,
            exit_on_error=False,
        )

    def _can_deliver_discovery(self, generation: int) -> bool:
        return not self._closed and generation == self._discovery_generation

    def _deliver_discovery(self, callback, generation: int, *args: Any) -> None:
        if not self._can_deliver_discovery(generation):
            return
        with suppress(RuntimeError):
            self.call_from_thread(callback, generation, *args)

    def _discovery_worker(self, generation: int) -> None:
        try:
            local = self.requester(
                self.settings,
                {
                    "command": "discover_connections",
                    "include_ble": False,
                },
                timeout=DISCOVERY_TIMEOUT_SECONDS,
            )["data"]
            self._deliver_discovery(
                self._apply_local_discovery,
                generation,
                local,
            )
        except Exception as exc:
            self._deliver_discovery(
                self._apply_local_discovery_error,
                generation,
                str(exc),
            )
        if not self._can_deliver_discovery(generation):
            return
        try:
            ble = self.requester(
                self.settings,
                {"command": "discover_ble_connections"},
                timeout=DISCOVERY_TIMEOUT_SECONDS,
            )["data"]
            self._deliver_discovery(
                self._finish_discovery,
                generation,
                ble,
                None,
            )
        except Exception as exc:
            self._deliver_discovery(
                self._finish_discovery,
                generation,
                None,
                str(exc),
            )

    async def _apply_local_discovery_error(
        self,
        generation: int,
        error: str,
    ) -> None:
        if not self._can_deliver_discovery(generation):
            return
        self.discovery["local_error"] = error
        self.discovery["serial_scanned"] = False
        self.notify(
            f"Klarte ikkje søkje etter lokale tilkoplingar: {error}",
            severity="error",
        )
        self._update_discovery_status()

    async def _apply_local_discovery(
        self,
        generation: int,
        data: dict[str, Any],
    ) -> None:
        if not self._can_deliver_discovery(generation):
            return
        preserved_ble = list(self.discovery.get("ble", []))
        self.discovery.update(data)
        self.discovery["local_error"] = None
        self.discovery["ble"] = preserved_ble
        self.discovery["serial_scanned"] = True
        self.discovery["ble_scanning"] = True
        self.discovery["ble_scanned"] = False
        await self._rebuild_choices()
        self._update_discovery_status()

    async def _finish_discovery(
        self,
        generation: int,
        data: dict[str, Any] | None,
        error: str | None,
    ) -> None:
        if not self._can_deliver_discovery(generation):
            return
        if data is not None:
            self.discovery.update(data)
        if error:
            self.discovery["ble"] = []
            self.discovery["ble_error"] = (
                f"Klarte ikkje fullføre søket: {error}"
            )
        self.discovery["ble_scanning"] = False
        self.discovery["ble_scanned"] = bool(
            data.get("ble_scanned", True) if data is not None else False
        )
        self._discovering = False
        await self._rebuild_choices()
        self._update_discovery_status()

    @on(Input.Changed, "#connection-input")
    async def filter_choices(self, event: Input.Changed) -> None:
        query = event.value.strip().casefold()
        await self._replace_choices(query)

    async def action_toggle_serial_ports(self) -> None:
        if not self.hidden_serial_count:
            return
        self.show_all_serial = not self.show_all_serial
        self.all_choices = build_connection_choices(
            self.discovery,
            include_all_serial=self.show_all_serial,
        )
        query = self.query_one("#connection-input", Input).value.strip().casefold()
        await self._replace_choices(query)

    def action_refresh_discovery(self) -> None:
        self._start_discovery()

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
        self._closed = True
        self._discovery_generation += 1
        self.exit(None)


def choose_connection(
    settings: Settings,
    requester=request,
) -> dict[str, Any] | None:
    connections = requester(settings, {"command": "connections"})["data"]
    discovery = connections | {
        "serial": [],
        "serial_scanned": False,
        "tcp": [],
        "tcp_error": None,
        "scanned_subnets": [],
        "ble": [],
        "ble_error": None,
        "ble_scanned": False,
        "ble_scanning": False,
    }
    return ConnectionPickerApp(
        discovery,
        settings=settings,
        requester=requester,
        auto_discover=True,
    ).run()
