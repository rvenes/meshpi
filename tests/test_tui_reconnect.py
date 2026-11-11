import asyncio
import queue
import threading

from test_tui import FakeBackend
from textual.widgets import Input

from meshpi.config import Settings
from meshpi.tui import LiveEvent, MeshPiTUI, NodeInfoScreen

GATEWAY = "!040840a0"
OTHER_GATEWAY = "!12345678"
PEER = "!710365c8"


async def _open_node_info(app, pilot):
    await pilot.pause(0.2)
    app._node_action_chosen(PEER, "node_info")
    await pilot.pause(0.2)
    assert isinstance(app.screen, NodeInfoScreen)
    return app.screen


def test_node_info_keeps_gateway_binding_and_rejects_other_gateway_events():
    async def scenario():
        backend = FakeBackend()
        app = MeshPiTUI(
            Settings(), requester=backend.request, watcher=None, update_checker=None,
        )
        async with app.run_test(size=(160, 48)) as pilot:
            screen = await _open_node_info(app, pilot)
            assert screen.local_node_id == GATEWAY
            original = list(screen.positions)
            position = {
                "id": 100, "node_id": PEER, "local_node_id": OTHER_GATEWAY,
                "gateway_node_id": OTHER_GATEWAY,
                "sample_time": "2026-11-11T00:00:00+00:00",
                "latitude": 12.0, "longitude": 11.0,
            }
            app.live_event(LiveEvent({"type": "position", "data": position}))
            assert screen.positions == original
            app.live_event(LiveEvent({
                "type": "position",
                "data": position | {"local_node_id": GATEWAY, "gateway_node_id": GATEWAY},
            }))
            assert len(screen.positions) == len(original) + 1

            app._set_status(backend.status | {
                "local_node_id": None, "history_local_node_id": GATEWAY,
                "state": "fråkopla",
            })
            await pilot.pause(0.1)
            assert app.screen is screen
            app._set_status(backend.status)
            screen.action_callback("position_exchange")
            await pilot.pause(0.2)
            assert {
                "command": "node_action", "action": "position_exchange",
                "node_id": PEER, "expected_local_node_id": GATEWAY,
            } in backend.calls

    asyncio.run(scenario())


def test_gateway_switch_closes_node_info_and_invalidates_saved_callback():
    async def scenario():
        backend = FakeBackend()
        app = MeshPiTUI(
            Settings(), requester=backend.request, watcher=None, update_checker=None,
        )
        async with app.run_test(size=(160, 48)) as pilot:
            screen = await _open_node_info(app, pilot)
            backend.status = backend.status | {"local_node_id": OTHER_GATEWAY}
            backend.conversations = []
            app._set_status(backend.status)
            await pilot.pause(0.2)
            assert app.screen is not screen
            screen.action_callback("position_exchange")
            app._push_gateway_screen(screen, GATEWAY)
            await pilot.pause(0.1)
            assert app.screen is not screen
            assert not any(call["command"] == "node_action" for call in backend.calls)

    asyncio.run(scenario())


class _WatchStream:
    def __init__(self):
        self.closed = threading.Event()
        self.items = queue.Queue()

    def __iter__(self):
        while not self.closed.is_set():
            try:
                item = self.items.get(timeout=0.05)
            except queue.Empty:
                continue
            if isinstance(item, Exception):
                raise item
            yield item

    def close(self):
        self.closed.set()


class _WatchSocket:
    def __init__(self, stream):
        self.stream = stream

    def shutdown(self, how):
        self.stream.close()

    def close(self):
        self.stream.close()


def test_watch_reconnect_resyncs_missed_data_without_sending_or_losing_draft():
    async def scenario():
        backend = FakeBackend()
        streams = []

        def watcher(settings, conversation):
            stream = _WatchStream()
            streams.append(stream)
            return _WatchSocket(stream), stream

        app = MeshPiTUI(
            Settings(), requester=backend.request, watcher=watcher, update_checker=None,
        )
        async with app.run_test(size=(160, 48)) as pilot:
            await pilot.pause(0.3)
            message_input = app.query_one("#message-input", Input)
            message_input.value = "Uferdig melding"
            message_input.cursor_position = 4
            conversation = app.current_conversation
            missed = backend.messages[conversation][0] | {
                "id": 456, "text": "Lagra medan IPC var broten",
            }
            backend.messages[conversation].append(missed)
            backend.conversations[0]["last_text"] = missed["text"]
            backend.nodes[1]["long_name"] = "Oppdatert node"
            backend.status = backend.status | {"endpoint": "new.example:4403"}
            streams[0].items.put(OSError("mock IPC break"))
            await pilot.pause(2.6)

            assert len(streams) == 2
            assert app.status_data["endpoint"] == "new.example:4403"
            assert app.nodes[PEER]["long_name"] == "Oppdatert node"
            assert app.conversations[0]["last_text"] == missed["text"]
            visible = [entry["text"] for kind, entry in app._visible_timeline
                       if kind == "message"]
            assert visible.count(missed["text"]) == 1
            assert message_input.value == "Uferdig melding"
            assert message_input.cursor_position == 4
            assert not any(call["command"] in {"send_public", "send_dm"}
                           for call in backend.calls)

    asyncio.run(scenario())
