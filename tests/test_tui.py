import asyncio

from textual.widgets import ListView, RichLog

from meshpi.config import Settings
from meshpi.tui import (
    LiveEvent,
    MeshPiTUI,
    NewDMScreen,
    NodePickerItem,
    NodeSidebarItem,
)


class FakeBackend:
    def __init__(self):
        self.calls = []
        self.status = {
            "state": "tilkopla",
            "host": "10.0.0.152",
            "port": 4403,
            "local_node_id": "!040840a0",
        }
        self.conversations = [
            {
                "conversation": "public",
                "kind": "public",
                "last_timestamp": "2026-07-20T12:00:00+00:00",
                "last_text": "Public test",
                "unread": 0,
            },
            {
                "conversation": "!710365c8",
                "kind": "dm",
                "last_timestamp": "2026-07-20T12:01:00+00:00",
                "last_text": "Hei",
                "unread": 1,
                "long_name": "Venes Reserve",
                "short_name": "65c8",
            },
        ]
        self.nodes = [
            {
                "node_id": "!040840a0",
                "short_name": "40a0",
                "long_name": "Heltec v3 IP 40a0",
                "is_local": True,
            },
            {
                "node_id": "!710365c8",
                "short_name": "65c8",
                "long_name": "Venes Reserve",
                "battery_level": 75,
                "voltage": 4.1,
                "snr": 10.5,
                "rssi": -33,
                "hops_away": 0,
                "transport": "RF",
                "can_receive_dm": True,
                "is_local": False,
            },
            {
                "node_id": "!2f779c48",
                "short_name": "9c48",
                "long_name": "VenesSol-A 9c48",
                "battery_level": 99,
                "snr": 8.5,
                "hops_away": 1,
                "transport": "RF",
                "can_receive_dm": True,
                "is_local": False,
            },
        ]
        self.messages = {
            "public": [
                {
                    "timestamp": "2026-07-20T12:00:00+00:00",
                    "kind": "public",
                    "direction": "inn",
                    "from_node": "!710365c8",
                    "transport": "RF",
                    "text": "Public test",
                }
            ],
            "!710365c8": [
                {
                    "timestamp": "2026-07-20T12:01:00+00:00",
                    "kind": "dm",
                    "direction": "inn",
                    "from_node": "!710365c8",
                    "peer_node": "!710365c8",
                    "transport": "RF",
                    "text": "Hei",
                }
            ],
        }

    def request(self, settings, payload):
        del settings
        self.calls.append(payload)
        command = payload["command"]
        if command == "status":
            data = self.status
        elif command == "conversations":
            data = self.conversations
        elif command == "nodes":
            data = self.nodes
        elif command == "messages":
            data = self.messages[payload["conversation"]]
        elif command == "node":
            data = next(
                node for node in self.nodes if node["node_id"] == payload["node_id"]
            )
        elif command in {"send_public", "send_dm"}:
            data = {"packet_id": 123}
        else:
            raise RuntimeError(command)
        return {"ok": True, "data": data}


def run_scenario(scenario):
    asyncio.run(scenario())


def test_tui_loads_and_tab_switches_conversations():
    async def scenario():
        backend = FakeBackend()
        app = MeshPiTUI(Settings(), requester=backend.request, watcher=None)
        async with app.run_test(size=(160, 48)) as pilot:
            await pilot.pause(0.3)
            assert len(app.conversations) == 2
            assert app.current_conversation == "public"
            await pilot.press("tab")
            await pilot.pause(0.2)
            assert app.current_conversation == "!710365c8"

    run_scenario(scenario)


def test_tui_sends_to_selected_dm():
    async def scenario():
        backend = FakeBackend()
        app = MeshPiTUI(Settings(), requester=backend.request, watcher=None)
        async with app.run_test(size=(160, 48)) as pilot:
            await pilot.pause(0.3)
            await pilot.press("tab", "ctrl+l")
            await pilot.press(*"test")
            await pilot.press("enter")
            await pilot.pause(0.3)
            assert {
                "command": "send_dm",
                "node_id": "!710365c8",
                "text": "test",
            } in backend.calls

    run_scenario(scenario)


def test_live_message_is_appended_to_active_dm():
    async def scenario():
        backend = FakeBackend()
        app = MeshPiTUI(Settings(), requester=backend.request, watcher=None)
        async with app.run_test(size=(160, 48)) as pilot:
            await pilot.pause(0.3)
            await pilot.press("tab")
            await pilot.pause(0.2)
            log = app.query_one("#message-log", RichLog)
            before = len(log.lines)
            incoming = {
                "timestamp": "2026-07-20T12:02:00+00:00",
                "kind": "dm",
                "direction": "inn",
                "from_node": "!710365c8",
                "peer_node": "!710365c8",
                "transport": "RF",
                "text": "Ny melding",
            }
            # The real daemon commits a message before publishing its live event.
            backend.messages["!710365c8"].append(incoming)
            app.post_message(
                LiveEvent(
                    {
                        "type": "message",
                        "data": incoming,
                    }
                )
            )
            await pilot.pause(0.2)
            assert len(log.lines) > before

    run_scenario(scenario)


def test_tui_hides_node_panel_in_narrow_terminal():
    async def scenario():
        backend = FakeBackend()
        app = MeshPiTUI(Settings(), requester=backend.request, watcher=None)
        async with app.run_test(size=(100, 36)) as pilot:
            await pilot.pause(0.3)
            assert app.query_one("#node-panel").display is False
            assert app.query_one("#conversation-panel").display is True

    run_scenario(scenario)


def test_sidebar_lists_nodes_and_opens_selected_node_as_dm():
    async def scenario():
        backend = FakeBackend()
        app = MeshPiTUI(Settings(), requester=backend.request, watcher=None)
        async with app.run_test(size=(160, 48)) as pilot:
            await pilot.pause(0.3)
            items = list(app.query(NodeSidebarItem))
            assert len(items) == 3
            assert items[0].node["is_local"] is True

            await pilot.press("f3")
            node_list = app.query_one("#node-list", ListView)
            assert node_list.has_focus
            node_list.index = 2
            await pilot.pause(0.1)
            assert app.selected_node_id == "!2f779c48"

            await pilot.press("enter")
            await pilot.pause(0.3)
            assert app.current_conversation == "!2f779c48"

    run_scenario(scenario)


def test_new_dm_picker_lists_and_filters_remote_nodes():
    async def scenario():
        backend = FakeBackend()
        app = MeshPiTUI(Settings(), requester=backend.request, watcher=None)
        async with app.run_test(size=(160, 48)) as pilot:
            await pilot.pause(0.3)
            await pilot.press("ctrl+d")
            await pilot.pause(0.2)
            assert isinstance(app.screen, NewDMScreen)
            assert len(app.screen.query(NodePickerItem)) == 2

            await pilot.press(*"reserve")
            await pilot.pause(0.2)
            assert len(app.screen.query(NodePickerItem)) == 1
            await pilot.press("enter")
            await pilot.pause(0.3)
            assert app.current_conversation == "!710365c8"

    run_scenario(scenario)


def test_tui_closes_socket_safely_if_watch_worker_clears_reference():
    class RacingSocket:
        def __init__(self, app):
            self.app = app
            self.closed = False

        def shutdown(self, _how):
            self.app._watch_socket = None

        def close(self):
            self.closed = True

    async def scenario():
        backend = FakeBackend()
        app = MeshPiTUI(Settings(), requester=backend.request, watcher=None)
        racing_socket = RacingSocket(app)
        async with app.run_test(size=(120, 36)) as pilot:
            await pilot.pause(0.2)
            app._watch_socket = racing_socket
        assert racing_socket.closed is True

    run_scenario(scenario)
