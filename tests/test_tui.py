import asyncio
import io
import threading
import time
from datetime import datetime, timezone

from textual.widgets import Button, Input, ListView, RichLog, Static

from meshpi.config import Settings
from meshpi.tui import (
    ConversationItem,
    HelpScreen,
    LiveEvent,
    MeshPiTUI,
    NewDMScreen,
    NodeActionScreen,
    NodePickerItem,
    NodeSidebarItem,
    QuitScreen,
    _message_time_parts,
)
from meshpi.update import UpdateNotice


class FakeBackend:
    def __init__(self):
        self.calls = []
        self.archived = set()
        self.traceroute_cooldown = 0
        self.status = {
            "state": "tilkopla",
            "host": "192.0.2.42",
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
            "!2f779c48": [],
        }
        self.node_actions = {
            "!710365c8": [],
            "!2f779c48": [],
        }

    def request(self, settings, payload):
        del settings
        self.calls.append(payload)
        command = payload["command"]
        if command == "status":
            data = self.status
        elif command == "conversations":
            data = [
                conversation
                for conversation in self.conversations
                if conversation["conversation"] not in self.archived
            ]
        elif command == "nodes":
            data = self.nodes
        elif command == "messages":
            data = self.messages[payload["conversation"]]
            if payload.get("mark_read"):
                for conversation in self.conversations:
                    if conversation["conversation"] == payload["conversation"]:
                        conversation["unread"] = 0
        elif command == "node":
            data = next(
                node for node in self.nodes if node["node_id"] == payload["node_id"]
            )
        elif command == "node_actions":
            data = self.node_actions[payload["node_id"]]
        elif command == "archive_conversation":
            self.archived.add(payload["node_id"])
            data = {"node_id": payload["node_id"], "archived": True}
        elif command == "unarchive_conversation":
            self.archived.discard(payload["node_id"])
            data = {"node_id": payload["node_id"], "archived": False}
        elif command in {"send_public", "send_dm"}:
            data = {"packet_id": 123}
        elif command == "node_action":
            data = {
                "action_id": "trace-1",
                "action": payload["action"],
                "node_id": payload["node_id"],
                "status": "started",
                "started_at": "2026-07-20T12:02:00+00:00",
                "cooldown_seconds": 30,
            }
        elif command == "node_action_availability":
            data = {
                "action": payload["action"],
                "node_id": payload["node_id"],
                "available": self.traceroute_cooldown == 0,
                "cooldown_seconds": self.traceroute_cooldown,
                "reason": (
                    None
                    if self.traceroute_cooldown == 0
                    else f"Vent {self.traceroute_cooldown} sekund"
                ),
            }
        else:
            raise RuntimeError(command)
        return {"ok": True, "data": data}


def run_scenario(scenario):
    asyncio.run(scenario())


def test_tui_distinguishes_ack_from_delivery():
    backend = FakeBackend()
    app = MeshPiTUI(
        Settings(), requester=backend.request, watcher=None, update_checker=None
    )
    message = {
        "timestamp": "2026-07-20T12:00:00+00:00",
        "kind": "dm",
        "direction": "ut",
        "from_node": "!040840a0",
        "transport": "Ukjend",
        "status": "ACK",
        "text": "Hei",
    }

    ack = app._render_message(message).plain
    delivered = app._render_message({**message, "status": "levert"}).plain

    assert "transport ukjend  [ACK]" in ack
    assert "transport ukjend  [levert]" in delivered


def test_old_messages_show_a_dim_date_before_the_time():
    date_label, time_label = _message_time_parts(
        "2026-07-21T11:30:00+00:00",
        now=datetime(2026, 7, 22, 12, 0, tzinfo=timezone.utc),
    )
    assert date_label == "21.07.26"
    assert time_label

    app = MeshPiTUI(Settings(), requester=FakeBackend().request, watcher=None)
    rendered = app._render_message(
        {
            "timestamp": "2026-07-21T11:30:00+00:00",
            "kind": "dm",
            "direction": "inn",
            "from_node": "!710365c8",
            "transport": "RF",
            "text": "Hei",
        }
    )
    assert rendered.plain.startswith("21.07.26 ")
    assert rendered.spans[0].style == "dim"


def test_tui_uses_enter_to_activate_and_tab_to_move_between_panes():
    async def scenario():
        backend = FakeBackend()
        app = MeshPiTUI(
            Settings(), requester=backend.request, watcher=None, update_checker=None
        )
        async with app.run_test(size=(160, 48)) as pilot:
            await pilot.pause(0.3)
            assert len(app.conversations) == 2
            assert app.current_conversation == "public"
            await pilot.press("f2", "down")
            assert app.current_conversation == "public"
            await pilot.press("enter")
            assert app.current_conversation == "!710365c8"
            assert app.query_one("#conversation-list", ListView).has_focus

            await pilot.press("tab")
            assert app.query_one("#message-input", Input).has_focus
            await pilot.press("tab")
            assert app.query_one("#node-list", ListView).has_focus
            await pilot.press("tab")
            assert app.query_one("#conversation-list", ListView).has_focus
            await pilot.press("shift+tab")
            assert app.query_one("#node-list", ListView).has_focus

    run_scenario(scenario)


def test_status_bar_shows_current_meshpi_version():
    async def scenario():
        backend = FakeBackend()
        app = MeshPiTUI(
            Settings(), requester=backend.request, watcher=None, update_checker=None
        )
        async with app.run_test(size=(160, 48)) as pilot:
            await pilot.pause(0.3)
            rendered = app.query_one("#status-bar", Static).render()
            text = rendered.plain if hasattr(rendered, "plain") else str(rendered)
            assert "MeshPi 0.6.2" in text

    run_scenario(scenario)


def test_f1_opens_global_shortcut_help_and_f1_closes_it():
    async def scenario():
        backend = FakeBackend()
        app = MeshPiTUI(
            Settings(), requester=backend.request, watcher=None, update_checker=None
        )
        async with app.run_test(size=(160, 48)) as pilot:
            await pilot.pause(0.3)
            await pilot.press("ctrl+l")
            assert app.query_one("#message-input", Input).has_focus

            await pilot.press("f1")
            await pilot.pause(0.1)
            assert isinstance(app.screen, HelpScreen)
            rendered = app.screen.query_one("#help-shortcuts", Static).render()
            text = rendered.plain if hasattr(rendered, "plain") else str(rendered)
            assert "Tab / Shift+Tab" in text
            assert "Ctrl+D" in text
            assert "Ctrl+Q" in text

            await pilot.press("f1")
            await pilot.pause(0.1)
            assert not isinstance(app.screen, HelpScreen)

    run_scenario(scenario)


def test_tui_sends_to_selected_dm():
    async def scenario():
        backend = FakeBackend()
        app = MeshPiTUI(
            Settings(), requester=backend.request, watcher=None, update_checker=None
        )
        async with app.run_test(size=(160, 48)) as pilot:
            await pilot.pause(0.3)
            await pilot.press("f2", "down", "enter", "tab")
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
        app = MeshPiTUI(
            Settings(), requester=backend.request, watcher=None, update_checker=None
        )
        async with app.run_test(size=(160, 48)) as pilot:
            await pilot.pause(0.3)
            await pilot.press("f2", "down", "enter")
            await pilot.pause(0.2)
            log = app.query_one("#message-log", RichLog)
            before = len(log.lines)
            incoming = {
                "timestamp": "2026-06-04T02:58:00+00:00",
                "kind": "dm",
                "direction": "inn",
                "from_node": "!710365c8",
                "peer_node": "!710365c8",
                "transport": "RF",
                "text": "Ny melding",
            }
            # The real daemon commits a message before publishing its live event.
            backend.messages["!710365c8"].append(incoming)
            backend.conversations[1]["unread"] = 1
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
            active_item = next(
                item
                for item in app.query(ConversationItem)
                if item.conversation_id == "!710365c8"
            )
            assert active_item.conversation["unread"] == 0

            app.select_conversation("!2f779c48")
            await pilot.pause(0.2)
            app.select_conversation("!710365c8")
            await pilot.pause(0.2)
            rendered = "\n".join(line.text for line in log.lines)
            assert rendered.index("Hei") < rendered.index("Ny melding")
            conversation_item = next(
                item
                for item in app.query(ConversationItem)
                if item.conversation_id == "!710365c8"
            )
            assert conversation_item.conversation["unread"] == 0

    run_scenario(scenario)


def test_refresh_updates_existing_list_items_without_rebuilding():
    async def scenario():
        backend = FakeBackend()
        app = MeshPiTUI(
            Settings(), requester=backend.request, watcher=None, update_checker=None
        )
        async with app.run_test(size=(160, 48)) as pilot:
            await pilot.pause(0.3)
            conversation_item = list(app.query(ConversationItem))[1]
            node_item = list(app.query(NodeSidebarItem))[1]
            backend.conversations[1]["last_text"] = "Oppdatert"
            backend.nodes[1]["battery_level"] = 60

            await app._apply_refresh(backend.conversations, backend.nodes)

            assert list(app.query(ConversationItem))[1] is conversation_item
            assert list(app.query(NodeSidebarItem))[1] is node_item
            assert conversation_item.conversation["last_text"] == "Oppdatert"
            assert node_item.node["battery_level"] == 60

    run_scenario(scenario)


def test_update_notice_is_local_and_never_fills_or_sends_message_input():
    async def scenario():
        backend = FakeBackend()
        notice = UpdateNotice(
            current_version="0.3.2",
            latest_version="0.4.0",
            command="curl -fsSL https://venes.org/meshpi/install-linux.sh | sudo bash",
        )
        app = MeshPiTUI(
            Settings(),
            requester=backend.request,
            watcher=None,
            update_checker=lambda _settings: notice,
        )
        async with app.run_test(size=(160, 48)) as pilot:
            await pilot.pause(0.3)
            message_input = app.query_one("#message-input", Input)
            log = app.query_one("#message-log", RichLog)
            rendered = "\n".join(line.text for line in log.lines)

            assert message_input.value == ""
            assert notice.command in rendered
            assert "ikkje send som melding" in rendered
            assert "Ctrl+U" in rendered
            await pilot.press("ctrl+u")
            assert app._clipboard == notice.command
            assert message_input.value == ""
            assert not any(
                call["command"] in {"send_public", "send_dm"} for call in backend.calls
            )

    run_scenario(scenario)


def test_quit_dialog_defaults_to_leave_service_in_always_mode():
    async def scenario():
        backend = FakeBackend()
        app = MeshPiTUI(
            Settings(background_mode="always"),
            requester=backend.request,
            watcher=None,
            update_checker=None,
        )
        async with app.run_test(size=(120, 36)) as pilot:
            await pilot.pause(0.2)
            await pilot.press("ctrl+q")
            await pilot.pause(0.1)
            assert isinstance(app.screen, QuitScreen)
            assert app.screen.query_one("#quit-leave").has_focus
            await pilot.press("enter")
            await pilot.pause(0.1)
            assert app.return_value == "leave"

    run_scenario(scenario)


def test_quit_dialog_supports_arrow_navigation_and_enter():
    async def scenario():
        backend = FakeBackend()
        app = MeshPiTUI(
            Settings(background_mode="always"),
            requester=backend.request,
            watcher=None,
            update_checker=None,
        )
        async with app.run_test(size=(120, 36)) as pilot:
            await pilot.press("ctrl+q")
            await pilot.pause(0.1)
            assert app.screen.query_one("#quit-leave").has_focus
            await pilot.press("down")
            assert app.screen.query_one("#quit-stop").has_focus
            await pilot.press("down")
            assert app.screen.query_one("#quit-cancel").has_focus
            await pilot.press("up")
            assert app.screen.query_one("#quit-stop").has_focus
            await pilot.press("enter")
            await pilot.pause(0.1)
            assert app.return_value == "stop"

    run_scenario(scenario)


def test_quit_dialog_defaults_to_stop_service_in_session_mode():
    async def scenario():
        backend = FakeBackend()
        app = MeshPiTUI(
            Settings(background_mode="session"),
            requester=backend.request,
            watcher=None,
            update_checker=None,
        )
        async with app.run_test(size=(120, 36)) as pilot:
            await pilot.pause(0.2)
            await pilot.press("ctrl+q")
            await pilot.pause(0.1)
            assert isinstance(app.screen, QuitScreen)
            assert app.screen.query_one("#quit-stop").has_focus
            await pilot.press("enter")
            await pilot.pause(0.1)
            assert app.return_value == "stop"

    run_scenario(scenario)


def test_delete_archives_selected_dm_without_deleting_messages():
    async def scenario():
        backend = FakeBackend()
        app = MeshPiTUI(
            Settings(), requester=backend.request, watcher=None, update_checker=None
        )
        async with app.run_test(size=(160, 48)) as pilot:
            await pilot.pause(0.3)
            await pilot.press("f2", "down", "enter", "delete")
            await pilot.pause(0.3)

            assert backend.archived == {"!710365c8"}
            assert app.current_conversation == "public"
            assert [item.conversation_id for item in app.query(ConversationItem)] == [
                "public"
            ]
            assert backend.messages["!710365c8"][0]["text"] == "Hei"

    run_scenario(scenario)


def test_tui_hides_node_panel_in_narrow_terminal():
    async def scenario():
        backend = FakeBackend()
        app = MeshPiTUI(
            Settings(), requester=backend.request, watcher=None, update_checker=None
        )
        async with app.run_test(size=(100, 36)) as pilot:
            await pilot.pause(0.3)
            assert app.query_one("#node-panel").display is False
            assert app.query_one("#conversation-panel").display is True

    run_scenario(scenario)


def test_sidebar_lists_nodes_and_opens_selected_node_as_dm():
    async def scenario():
        backend = FakeBackend()
        app = MeshPiTUI(
            Settings(), requester=backend.request, watcher=None, update_checker=None
        )
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


def test_keyboard_opens_node_action_menu_and_starts_traceroute():
    async def scenario():
        backend = FakeBackend()
        app = MeshPiTUI(
            Settings(), requester=backend.request, watcher=None, update_checker=None
        )
        async with app.run_test(size=(160, 48)) as pilot:
            await pilot.pause(0.3)
            await pilot.press("f3", "up", "down", "shift+f10")
            await pilot.pause(0.3)
            assert isinstance(app.screen, NodeActionScreen)
            assert app.screen.node["node_id"] == "!710365c8"

            await pilot.press("t")
            await pilot.pause(0.3)
            assert app.current_conversation == "!710365c8"
            assert {
                "command": "node_action",
                "action": "traceroute",
                "node_id": "!710365c8",
            } in backend.calls
            message_log = app.query_one("#message-log", RichLog)
            assert "TRACEROUTE" in "\n".join(line.text for line in message_log.lines)

    run_scenario(scenario)


def test_right_click_selects_node_and_opens_node_action_menu():
    async def scenario():
        backend = FakeBackend()
        app = MeshPiTUI(
            Settings(), requester=backend.request, watcher=None, update_checker=None
        )
        async with app.run_test(size=(160, 48)) as pilot:
            await pilot.pause(0.3)
            main_screen = app.screen
            target = list(app.query(NodeSidebarItem))[2]
            await pilot.click(target, offset=(2, 1), button=3)
            await pilot.pause(0.3)

            assert app.selected_node_id == "!2f779c48"
            assert app.current_conversation == "public"
            assert isinstance(app.screen, NodeActionScreen)
            assert app.screen.node["node_id"] == "!2f779c48"
            assert main_screen._selecting is False
            assert not main_screen.selections

    run_scenario(scenario)


def test_completed_traceroute_is_rendered_in_dm_without_blocking_the_app():
    async def scenario():
        backend = FakeBackend()
        app = MeshPiTUI(
            Settings(), requester=backend.request, watcher=None, update_checker=None
        )
        async with app.run_test(size=(160, 48)) as pilot:
            await pilot.pause(0.3)
            app._open_node_dm("!710365c8", focus_input=True)
            await pilot.pause(0.3)
            app.post_message(
                LiveEvent(
                    {
                        "type": "node_action",
                        "data": {
                            "action_id": "trace-result-1",
                            "action": "traceroute",
                            "node_id": "!710365c8",
                            "status": "completed",
                            "started_at": "2026-07-20T12:02:00+00:00",
                            "result": {
                                "forward": [
                                    {"node_id": "!040840a0", "snr": None},
                                    {"node_id": "!2f779c48", "snr": 7.5},
                                    {"node_id": "!710365c8", "snr": 6.0},
                                ],
                                "return": None,
                            },
                        },
                    }
                )
            )
            await pilot.pause(0.2)

            assert not isinstance(app.screen, NodeActionScreen)
            assert app.current_conversation == "!710365c8"
            assert app.query_one("#message-input", Input).has_focus
            message_log = app.query_one("#message-log", RichLog)
            text = "\n".join(line.text for line in message_log.lines)
            assert "TRACEROUTE · FERDIG" in text
            assert "VenesSol-A 9c48" in text
            assert "SNR 7.5 dB" in text
            assert "Tilbake" in text

    run_scenario(scenario)


def test_saved_traceroute_is_loaded_into_dm_history():
    async def scenario():
        backend = FakeBackend()
        backend.node_actions["!710365c8"] = [
            {
                "action_id": "trace-saved-1",
                "action": "traceroute",
                "node_id": "!710365c8",
                "status": "completed",
                "started_at": "2026-07-20T12:02:00+00:00",
                "finished_at": "2026-07-20T12:02:05+00:00",
                "result": {"forward": [], "return": None},
            }
        ]
        app = MeshPiTUI(
            Settings(), requester=backend.request, watcher=None, update_checker=None
        )
        async with app.run_test(size=(160, 48)) as pilot:
            await pilot.pause(0.3)
            app._open_node_dm("!710365c8", focus_input=False)
            await pilot.pause(0.3)

            message_log = app.query_one("#message-log", RichLog)
            text = "\n".join(line.text for line in message_log.lines)
            assert "TRACEROUTE · FERDIG" in text
            assert {
                "command": "node_actions",
                "action": "traceroute",
                "node_id": "!710365c8",
                "limit": 100,
            } in backend.calls

    run_scenario(scenario)


def test_message_text_can_still_be_selected_with_left_mouse_drag():
    async def scenario():
        backend = FakeBackend()
        app = MeshPiTUI(
            Settings(), requester=backend.request, watcher=None, update_checker=None
        )
        async with app.run_test(size=(160, 48)) as pilot:
            await pilot.pause(0.3)
            message_log = app.query_one("#message-log", RichLog)

            await pilot.mouse_down(message_log, offset=(2, 1))
            await pilot.hover(message_log, offset=(35, 1))
            await pilot.mouse_up(message_log, offset=(35, 1))
            await pilot.pause(0.1)

            selected = app.screen.get_selected_text()
            assert selected is not None
            assert "Public test" in selected

    run_scenario(scenario)


def test_node_action_menu_counts_down_traceroute_cooldown():
    async def scenario():
        backend = FakeBackend()
        backend.traceroute_cooldown = 30
        app = MeshPiTUI(
            Settings(), requester=backend.request, watcher=None, update_checker=None
        )
        async with app.run_test(size=(160, 48)) as pilot:
            await pilot.pause(0.3)
            await pilot.press("f3", "up", "down", "shift+f10")
            await pilot.pause(0.3)

            screen = app.screen
            assert isinstance(screen, NodeActionScreen)
            button = screen.query_one("#node-action-traceroute", Button)
            assert button.disabled is True
            assert "vent 30 s" in str(button.label)

            screen._cooldown_deadline = time.monotonic() - 1
            screen._update_traceroute_button()
            assert button.disabled is False
            assert "vent" not in str(button.label)

    run_scenario(scenario)


def test_new_dm_picker_lists_and_filters_remote_nodes():
    async def scenario():
        backend = FakeBackend()
        app = MeshPiTUI(
            Settings(), requester=backend.request, watcher=None, update_checker=None
        )
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
        app = MeshPiTUI(
            Settings(), requester=backend.request, watcher=None, update_checker=None
        )
        racing_socket = RacingSocket(app)
        async with app.run_test(size=(120, 36)) as pilot:
            await pilot.pause(0.2)
            app._watch_socket = racing_socket
        assert racing_socket.closed is True

    run_scenario(scenario)


def test_tui_closes_watch_socket_that_finishes_opening_during_shutdown():
    started = threading.Event()
    release = threading.Event()

    class WatchSocket:
        def __init__(self):
            self.closed = False

        def shutdown(self, _how):
            pass

        def close(self):
            self.closed = True

    watch_socket = WatchSocket()

    def delayed_watcher(_settings, _conversation):
        started.set()
        release.wait(1)
        return watch_socket, io.BytesIO()

    app = MeshPiTUI(
        Settings(),
        requester=FakeBackend().request,
        watcher=delayed_watcher,
        update_checker=None,
    )
    worker = threading.Thread(target=app._watch_worker)
    worker.start()
    assert started.wait(1)

    app.on_unmount()
    release.set()
    worker.join(1)

    assert worker.is_alive() is False
    assert watch_socket.closed is True
