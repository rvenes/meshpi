import asyncio
from copy import deepcopy

import pytest
from test_tui_i18n import I18nBackend
from textual.widgets import Button, DataTable, Input, RichLog, Static

from meshpi.config import Settings
from meshpi.i18n import using_language
from meshpi.tui import MeshPiTUI, RemoveNodeScreen, SettingsScreen, StoredNodeScreen

GATEWAY = "!040840a0"
FIRST = "!710365c8"
SECOND = "!710365c9"


class NodeListBackend(I18nBackend):
    """Only local IPC responses; no SDK, sockets, or radio are involved."""

    def __init__(self):
        super().__init__()
        self.nodes[0].update(long_name="Testgateway", in_registry=True)
        self.nodes[1].update(
            long_name="Rask node", in_registry=True, last_heard="2026-10-04T10:12:13+00:00",
            role="CLIENT_MUTE", hw_model="HELTEC_V3", battery_level=0,
            latest_position={
                "latitude": 60.1234567, "longitude": 5.7654321, "altitude_msl": 143,
                "sample_time": "2026-10-04T10:00:00+00:00",
                "received_at": "2026-10-04T10:00:02+00:00", "precision_bits": 24,
            },
        )
        self.nodes.append({
            "node_id": SECOND, "long_name": "Andre node", "short_name": "65c9",
            "is_local": False, "in_registry": True,
        })
        self.remove_error = None
        self.action_status = None

    def info(self):
        return deepcopy({
            "connected": True, "status": self.status, "local_node_id": GATEWAY,
            "node": self.nodes[0], "nodes": self.nodes, "radio": {},
            "registry_count": len(self.nodes), "stored_count": len(self.nodes),
            "heard_24h_count": 2,
        })

    def request(self, settings, payload):
        command = payload["command"]
        if command == "local_node_info":
            self.calls.append(dict(payload))
            return {"ok": True, "data": self.info()}
        if command == "remove_nodes":
            self.calls.append(dict(payload))
            if self.remove_error:
                raise RuntimeError(self.remove_error)
            return {"ok": True, "data": {"action_id": "remove-test", "status": "started"}}
        if command == "node_action_status":
            self.calls.append(dict(payload))
            removed = {
                contact["node_id"] for contact in self.action_status["result"]["contacts"]
                if contact["status"] == "removed"
            }
            self.nodes = [node for node in self.nodes if node["node_id"] not in removed]
            return {"ok": True, "data": deepcopy(self.action_status)}
        return super().request(settings, payload)


def _text(widget):
    value = widget.render()
    return value.plain if hasattr(value, "plain") else str(value)


def _log_text(screen):
    return "\n".join(line.text for line in screen.query_one("#stored-node-log", RichLog).lines)


def _remove_calls(backend):
    return [call for call in backend.calls if call["command"] == "remove_nodes"]


async def _open_nodes(app, pilot):
    await pilot.pause(0.15)
    await pilot.press("f10")
    await pilot.pause(0.15)
    assert isinstance(app.screen, SettingsScreen)
    await pilot.press("right", "right", "right")
    screen = app.screen
    assert screen.current_tab == "nodes"
    assert screen.query_one("#local-nodes-table", DataTable).row_count == 3
    return screen


@pytest.mark.parametrize("size", [(80, 24), (130, 42)])
def test_node_table_search_keyboard_details_and_refresh_preserve_selection(size):
    async def scenario():
        backend = NodeListBackend()
        with using_language("nn"):
            app = MeshPiTUI(
                Settings(), requester=backend.request, watcher=None, update_checker=None,
            )
            async with app.run_test(size=size) as pilot:
                screen = await _open_nodes(app, pilot)
                table = screen.query_one("#local-nodes-table", DataTable)
                rendered = " ".join(str(cell) for cell in table.get_row(FIRST))
                assert "Rask node" in rendered
                assert "04.10.26" in rendered
                assert "60.12346, 5.76543" in rendered
                assert "0%" in rendered
                pane = screen.query_one("#local-nodes-pane")
                message = screen.query_one("#local-nodes-message")
                actions = screen.query_one("#local-nodes-actions")
                help_text = screen.query_one("#settings-help")
                assert table.size.height >= 3
                assert table.region.bottom <= message.region.y
                assert message.region.bottom <= actions.region.y
                assert actions.region.bottom <= pane.region.bottom
                assert actions.region.bottom <= help_text.region.y
                assert help_text.region.bottom <= size[1]
                close = screen.query_one("#settings-cancel")
                if close.display:
                    assert actions.region.bottom <= close.region.y
                    assert close.region.bottom <= help_text.region.y
                if size[0] == 80:
                    await pilot.press("shift+right")
                    assert table.scroll_x > 0
                    assert screen.current_tab == "nodes"
                    await pilot.press("shift+left")
                    assert table.scroll_x == 0

                search = screen.query_one("#local-nodes-search", Input)
                search.focus()
                await pilot.press("r", "a", "s", "k")
                assert search.value == "rask"
                assert screen.current_tab == "nodes"
                assert screen.node_rows == [FIRST]
                position = search.cursor_position
                await pilot.press("left")
                assert search.cursor_position == position - 1
                assert screen.current_tab == "nodes"
                await pilot.press("enter", "enter")
                assert isinstance(app.screen, StoredNodeScreen)
                details = _log_text(app.screen)
                assert FIRST in details
                assert "CLIENT_MUTE" in details
                assert "60.1234567" in details
                assert "5.7654321" in details
                assert "143 m" in details
                assert "04.10.26" in details
                assert not app.screen.query_one("#stored-node-map", Button).disabled
                await pilot.press("escape")
                assert app.screen is screen

                search.value = ""
                await pilot.pause()
                table.move_cursor(row=screen.node_rows.index(FIRST), animate=False)
                await pilot.pause()
                backend.nodes = [backend.nodes[0], backend.nodes[2], backend.nodes[1]]
                await pilot.press("ctrl+r")
                await pilot.pause(0.15)
                assert screen._selected_node()["node_id"] == FIRST
                assert table.cursor_row == 2
                assert await pilot.click("#local-nodes-remove")
                await pilot.pause()
                assert isinstance(app.screen, RemoveNodeScreen)
                assert FIRST in _text(app.screen.query_one("#remove-node-text", Static))
                assert await pilot.click("#remove-node-cancel")
                assert app.screen is screen
                assert not _remove_calls(backend)
                assert not any(call["command"].startswith("send") for call in backend.calls)

    asyncio.run(scenario())


def test_own_node_cannot_be_selected_or_removed_and_cancel_sends_nothing():
    async def scenario():
        backend = NodeListBackend()
        with using_language("nn"):
            app = MeshPiTUI(
                Settings(), requester=backend.request, watcher=None, update_checker=None,
            )
            async with app.run_test(size=(100, 34)) as pilot:
                screen = await _open_nodes(app, pilot)
                assert screen.query_one("#local-nodes-remove", Button).disabled
                await pilot.press("space", "delete")
                assert app.screen is screen
                assert GATEWAY not in screen.checked_nodes
                await pilot.press("down", "delete")
                assert isinstance(app.screen, RemoveNodeScreen)
                confirmation = _text(app.screen.query_one("#remove-node-text", Static))
                assert FIRST in confirmation
                assert GATEWAY in confirmation
                assert "Rask node" in confirmation
                assert app.focused.id == "remove-node-cancel"
                await pilot.press("enter")
                assert app.screen is screen
                assert not _remove_calls(backend)

    asyncio.run(scenario())


def test_multiple_selected_contacts_are_sent_as_one_batch_with_gateway_guard():
    async def scenario():
        backend = NodeListBackend()
        backend.remove_error = "Testfeil: sletting vart ikkje sendt"
        with using_language("nn"):
            app = MeshPiTUI(
                Settings(), requester=backend.request, watcher=None, update_checker=None,
            )
            async with app.run_test(size=(100, 34)) as pilot:
                screen = await _open_nodes(app, pilot)
                await pilot.press("down", "space", "down", "space")
                assert screen.checked_nodes == {FIRST, SECOND}
                screen.action_refresh_info()
                await pilot.pause(0.15)
                assert screen.checked_nodes == {FIRST, SECOND}
                await pilot.press("delete")
                assert isinstance(app.screen, RemoveNodeScreen)
                confirmation = _text(app.screen.query_one("#remove-node-text", Static))
                assert FIRST in confirmation and SECOND in confirmation
                assert "Rask node" in confirmation and "Andre node" in confirmation
                assert GATEWAY in confirmation
                await pilot.click("#remove-node-confirm")
                await pilot.pause(0.2)
                assert _remove_calls(backend) == [{
                    "command": "remove_nodes", "node_ids": [FIRST, SECOND],
                    "expected_local_node_id": GATEWAY,
                }]
                assert "Testfeil" in _text(screen.query_one("#local-nodes-message", Static))
                assert screen.node_rows == [GATEWAY, FIRST, SECOND]
                assert not screen.removing_node

    asyncio.run(scenario())


@pytest.mark.parametrize("contact_status", ["removed", "sent_unverified", "failed"])
def test_final_radio_verification_status_controls_success_and_visible_cache(contact_status):
    async def scenario():
        backend = NodeListBackend()
        success = contact_status == "removed"
        backend.action_status = {
            "action_id": "remove-test", "action": "remove_nodes",
            "status": "completed" if success else "failed",
            "result": {
                "contacts": [{"node_id": FIRST, "status": contact_status,
                              **({} if success else {"error": "Ikkje verifisert på radioen"})}],
                "history_preserved": True,
            },
        }
        with using_language("nn"):
            app = MeshPiTUI(
                Settings(), requester=backend.request, watcher=None, update_checker=None,
            )
            async with app.run_test(size=(100, 34)) as pilot:
                screen = await _open_nodes(app, pilot)
                await pilot.press("down", "space", "delete")
                await pilot.click("#remove-node-confirm")
                for _ in range(40):
                    await pilot.pause(0.05)
                    if not screen.removing_node and _remove_calls(backend):
                        break
                await pilot.pause(0.1)
                assert not screen.removing_node
                assert any(call["command"] == "node_action_status" for call in backend.calls)
                assert (FIRST not in screen.node_rows) == success
                assert (FIRST not in screen.checked_nodes) == success
                message = _text(screen.query_one("#local-nodes-message", Static))
                if success:
                    assert "Stadfesta sletta: 1/1" in message
                else:
                    assert "Stadfesta sletta: 0/1" in message
                    assert "Ikkje verifisert på radioen" in message
                assert GATEWAY in screen.node_rows and SECOND in screen.node_rows
                assert backend.conversations[1]["last_text"] == "Test"

    asyncio.run(scenario())


def test_partial_batch_keeps_unverified_contact_selected_without_retrying():
    async def scenario():
        backend = NodeListBackend()
        backend.action_status = {
            "action_id": "remove-test", "action": "remove_nodes", "status": "failed",
            "result": {
                "contacts": [
                    {"node_id": FIRST, "status": "removed"},
                    {"node_id": SECOND, "status": "sent_unverified", "error": "Timeout"},
                ],
                "history_preserved": True,
            },
        }
        with using_language("nn"):
            app = MeshPiTUI(
                Settings(), requester=backend.request, watcher=None, update_checker=None,
            )
            async with app.run_test(size=(80, 24)) as pilot:
                screen = await _open_nodes(app, pilot)
                await pilot.press("down", "space", "down", "space", "delete")
                assert isinstance(app.screen, RemoveNodeScreen)
                confirm = app.screen.query_one("#remove-node-confirm", Button)
                assert confirm.region.bottom <= 24
                await pilot.click("#remove-node-confirm")
                for _ in range(40):
                    await pilot.pause(0.05)
                    if not screen.removing_node and _remove_calls(backend):
                        break
                await pilot.pause(0.1)
                message = _text(screen.query_one("#local-nodes-message", Static))
                assert "Stadfesta sletta: 1/2" in message
                assert SECOND in message and "Timeout" in message
                assert screen.node_rows == [GATEWAY, SECOND]
                assert screen.checked_nodes == {SECOND}
                await pilot.press("ctrl+r")
                await pilot.pause(0.15)
                assert len(_remove_calls(backend)) == 1
                assert not any(call["command"] == "archive_conversation" for call in backend.calls)

    asyncio.run(scenario())


@pytest.mark.parametrize("invalid_id", [GATEWAY.upper(), "!ffffffff", "!00000000", "invalid"])
def test_invalid_broadcast_and_own_ids_are_blocked_even_without_local_flag(invalid_id):
    async def scenario():
        backend = NodeListBackend()
        backend.nodes[1].update(node_id=invalid_id, is_local=False)
        with using_language("nn"):
            app = MeshPiTUI(
                Settings(), requester=backend.request, watcher=None, update_checker=None,
            )
            async with app.run_test(size=(100, 34)) as pilot:
                screen = await _open_nodes(app, pilot)
                await pilot.press("down")
                assert screen.query_one("#local-nodes-select", Button).disabled
                assert screen.query_one("#local-nodes-remove", Button).disabled
                await pilot.press("space", "delete")
                assert not screen.checked_nodes
                assert app.screen is screen
                assert not _remove_calls(backend)

    asyncio.run(scenario())
