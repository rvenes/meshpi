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
    NodeInfoScreen,
    NodePickerItem,
    NodeSidebarItem,
    QuitScreen,
    _conversation_title,
    _map_link,
    _message_time_parts,
    _metric_label_and_value,
)
from meshpi.update import UpdateNotice

PRIMARY_CHANNEL_KEY = "local:!040840a0:0:"
PUBLIC_CONVERSATION = f"channel:{PRIMARY_CHANNEL_KEY}"
RESERVE_DM_CONVERSATION = f"dm:!040840a0:!710365c8:{PRIMARY_CHANNEL_KEY}"
VENESSOL_DM_CONVERSATION = f"dm:!040840a0:!2f779c48:{PRIMARY_CHANNEL_KEY}"


class FakeBackend:
    def __init__(self):
        self.calls = []
        self.archived = set()
        self.traceroute_cooldown = 0
        self.position_exchange_cooldown = 0
        self.status = {
            "state": "tilkopla",
            "host": "192.0.2.42",
            "port": 4403,
            "transport": "tcp",
            "endpoint": "192.0.2.42:4403",
            "local_node_id": "!040840a0",
        }
        self.conversations = [
            {
                "conversation": PUBLIC_CONVERSATION,
                "kind": "public",
                "channel": 0,
                "channel_key": PRIMARY_CHANNEL_KEY,
                "local_node_id": "!040840a0",
                "sendable": True,
                "last_timestamp": "2026-07-20T12:00:00+00:00",
                "last_text": "Public test",
                "unread": 0,
            },
            {
                "conversation": RESERVE_DM_CONVERSATION,
                "kind": "dm",
                "peer_node": "!710365c8",
                "channel": 0,
                "channel_key": PRIMARY_CHANNEL_KEY,
                "local_node_id": "!040840a0",
                "sendable": True,
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
            PUBLIC_CONVERSATION: [
                {
                    "timestamp": "2026-07-20T12:00:00+00:00",
                    "kind": "public",
                    "direction": "inn",
                    "from_node": "!710365c8",
                    "transport": "RF",
                    "text": "Public test",
                }
            ],
            RESERVE_DM_CONVERSATION: [
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
            VENESSOL_DM_CONVERSATION: [],
        }
        self.node_actions = {
            "!710365c8": [],
            "!2f779c48": [],
        }
        self.telemetry = {
            "!710365c8": [
                {
                    "id": 1,
                    "node_id": "!710365c8",
                    "kind": "device",
                    "sample_time": "2026-07-20T12:10:00+00:00",
                    "metrics": {
                        "batteryLevel": 75,
                        "voltage": 4.1,
                        "channelUtilization": 12.5,
                    },
                    "transport": "RF",
                    "gateway_node_id": "!040840a0",
                },
                {
                    "id": 2,
                    "node_id": "!710365c8",
                    "kind": "environment",
                    "sample_time": "2026-07-20T12:11:00+00:00",
                    "metrics": {"temperature": 18.75, "relativeHumidity": 72},
                    "transport": "RF",
                    "gateway_node_id": "!040840a0",
                },
            ],
            "!2f779c48": [],
            "!040840a0": [],
        }
        self.positions = {
            "!710365c8": [
                {
                    "id": 1,
                    "node_id": "!710365c8",
                    "sample_time": "2026-07-20T12:12:00+00:00",
                    "latitude": 60.1234567,
                    "longitude": 5.1234567,
                    "altitude_msl": 104,
                    "gps_accuracy_mm": 3000,
                    "sats_in_view": 9,
                    "transport": "RF",
                    "gateway_node_id": "!040840a0",
                }
            ],
            "!2f779c48": [],
            "!040840a0": [],
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
            data = self.messages.get(payload["conversation"], [])
            if payload.get("mark_read"):
                for conversation in self.conversations:
                    if conversation["conversation"] == payload["conversation"]:
                        conversation["unread"] = 0
        elif command == "node":
            data = next(
                node for node in self.nodes if node["node_id"] == payload["node_id"]
            )
        elif command == "node_overview":
            node = next(
                node for node in self.nodes if node["node_id"] == payload["node_id"]
            )
            telemetry = self.telemetry[payload["node_id"]]
            positions = self.positions[payload["node_id"]]
            data = {
                "node": node,
                "latest_telemetry": {
                    sample["kind"]: sample for sample in telemetry
                },
                "latest_position": positions[0] if positions else None,
                "counts": {
                    "telemetry": len(telemetry),
                    "positions": len(positions),
                    "traceroutes": len(self.node_actions.get(payload["node_id"], [])),
                },
            }
        elif command == "node_telemetry":
            data = self.telemetry[payload["node_id"]]
            if payload.get("kind"):
                data = [
                    sample
                    for sample in data
                    if sample["kind"] == payload["kind"]
                ]
        elif command == "node_positions":
            data = self.positions[payload["node_id"]]
        elif command == "node_actions":
            data = self.node_actions[payload["node_id"]]
        elif command == "archive_conversation":
            self.archived.add(payload.get("conversation") or payload["node_id"])
            data = {"node_id": payload["node_id"], "archived": True}
        elif command == "unarchive_conversation":
            self.archived.discard(payload.get("conversation") or payload["node_id"])
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
            if payload["action"] == "position_exchange":
                data |= {
                    "local_position_shared": False,
                    "local_position_precision_bits": None,
                    "local_position_share_reason": (
                        "posisjonsdeling er slått av for kanalen"
                    ),
                }
        elif command == "node_action_availability":
            cooldown = (
                self.traceroute_cooldown
                if payload["action"] == "traceroute"
                else self.position_exchange_cooldown
            )
            data = {
                "action": payload["action"],
                "node_id": payload["node_id"],
                "available": cooldown == 0,
                "cooldown_seconds": cooldown,
                "reason": (
                    None
                    if cooldown == 0
                    else f"Vent {cooldown} sekund"
                ),
            }
        else:
            raise RuntimeError(command)
        return {"ok": True, "data": data}


def run_scenario(scenario):
    asyncio.run(scenario())


def test_channel_conversation_title_uses_safe_name_and_index():
    assert (
        _conversation_title(
            {
                "conversation": "channel:global:ops:1234",
                "kind": "public",
                "channel": 2,
                "channel_name": "Ops",
            }
        )
        == "Ops – kanal 2"
    )


def test_archived_and_provisional_channel_titles_are_distinct():
    assert _conversation_title(
        {
            "conversation": "channel:legacy:tcp-felt:0",
            "kind": "public",
            "channel": 0,
            "channel_key": "legacy:tcp-felt:0",
        }
    ) == "Public (arkiv tcp-felt) – kanal 0"
    assert _conversation_title(
        {
            "conversation": "channel:provisional:!aaaaaaaa:5",
            "kind": "public",
            "channel": 5,
            "channel_key": "provisional:!aaaaaaaa:5",
        }
    ) == "Public (uavklart rute) – kanal 5"


def test_tui_routes_send_to_selected_channel_conversation():
    backend = FakeBackend()
    app = MeshPiTUI(
        Settings(),
        requester=backend.request,
        watcher=None,
        update_checker=None,
    )
    conversation = "channel:global:ops:1234"
    app.conversations = [
        {
            "conversation": conversation,
            "kind": "public",
            "channel": 2,
            "channel_name": "Ops",
            "sendable": True,
        }
    ]

    app._send_worker(conversation, "Hei")

    assert backend.calls[-1] == {
        "command": "send_public",
        "text": "Hei",
        "conversation": conversation,
    }


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
            assert app.current_conversation == PUBLIC_CONVERSATION
            await pilot.press("f2", "down")
            assert app.current_conversation == PUBLIC_CONVERSATION
            await pilot.press("enter")
            assert app.current_conversation == RESERVE_DM_CONVERSATION
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


def test_status_bar_shows_current_meshpi_version_and_host(monkeypatch):
    monkeypatch.setattr("meshpi.tui.socket.gethostname", lambda: "testvert")

    async def scenario():
        backend = FakeBackend()
        app = MeshPiTUI(
            Settings(), requester=backend.request, watcher=None, update_checker=None
        )
        async with app.run_test(size=(160, 48)) as pilot:
            await pilot.pause(0.3)
            rendered = app.query_one("#status-bar", Static).render()
            text = rendered.plain if hasattr(rendered, "plain") else str(rendered)
            assert "MeshPi 0.8.2" in text
            assert "Vert: testvert" in text

    run_scenario(scenario)


def test_remote_node_markup_is_literal_in_conversation_title():
    async def scenario():
        backend = FakeBackend()
        hostile_name = "[/] [@click=app.quit]Trykk[/]"
        backend.conversations[1]["long_name"] = hostile_name
        app = MeshPiTUI(
            Settings(), requester=backend.request, watcher=None, update_checker=None
        )
        async with app.run_test(size=(160, 48)) as pilot:
            await pilot.pause(0.3)
            await pilot.press("f2", "down", "enter")
            await pilot.pause(0.2)

            rendered = app.query_one("#conversation-title", Static).render()
            assert hostile_name in rendered.plain
            assert all(
                not getattr(span.style, "meta", None)
                for span in rendered.spans
            )

    run_scenario(scenario)


def test_remote_node_markup_is_literal_in_node_action_dialog():
    async def scenario():
        backend = FakeBackend()
        hostile_name = "[/] [@click=app.quit]Trykk[/]"
        backend.nodes[1]["long_name"] = hostile_name
        app = MeshPiTUI(
            Settings(), requester=backend.request, watcher=None, update_checker=None
        )
        async with app.run_test(size=(160, 48)) as pilot:
            await pilot.pause(0.3)
            await pilot.press("f3", "up", "down", "shift+f10")
            await pilot.pause(0.2)

            assert isinstance(app.screen, NodeActionScreen)
            rendered = app.screen.query_one("#node-action-node", Static).render()
            assert hostile_name in rendered.plain
            assert all(
                not getattr(span.style, "meta", None)
                for span in rendered.spans
            )

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
                "conversation": RESERVE_DM_CONVERSATION,
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
            incoming["conversation_id"] = RESERVE_DM_CONVERSATION
            backend.messages[RESERVE_DM_CONVERSATION].append(incoming)
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
                if item.conversation_id == RESERVE_DM_CONVERSATION
            )
            assert active_item.conversation["unread"] == 0

            app.select_conversation(VENESSOL_DM_CONVERSATION)
            await pilot.pause(0.2)
            app.select_conversation(RESERVE_DM_CONVERSATION)
            await pilot.pause(0.2)
            rendered = "\n".join(line.text for line in log.lines)
            assert rendered.index("Hei") < rendered.index("Ny melding")
            conversation_item = next(
                item
                for item in app.query(ConversationItem)
                if item.conversation_id == RESERVE_DM_CONVERSATION
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

            assert backend.archived == {RESERVE_DM_CONVERSATION}
            assert app.current_conversation == PUBLIC_CONVERSATION
            assert app.query_one("#message-input", Input).disabled is False
            assert "Public" in str(
                app.query_one("#conversation-title", Static).render()
            )
            assert [item.conversation_id for item in app.query(ConversationItem)] == [
                PUBLIC_CONVERSATION
            ]
            assert backend.messages[RESERVE_DM_CONVERSATION][0]["text"] == "Hei"

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
            assert app.current_conversation == VENESSOL_DM_CONVERSATION

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
            assert app.current_conversation == RESERVE_DM_CONVERSATION
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
            assert app.current_conversation == PUBLIC_CONVERSATION
            assert isinstance(app.screen, NodeActionScreen)
            assert app.screen.node["node_id"] == "!2f779c48"
            assert main_screen._selecting is False
            assert not main_screen.selections

    run_scenario(scenario)


def test_status_bar_budgets_long_host_and_endpoint_at_all_widths(monkeypatch):
    async def scenario(width, host, transport, endpoint):
        monkeypatch.setattr("meshpi.tui.socket.gethostname", lambda: host)
        backend = FakeBackend()
        backend.status |= {
            "state": "koplar til",
            "transport": transport,
            "endpoint": endpoint,
        }
        app = MeshPiTUI(
            Settings(), requester=backend.request, watcher=None, update_checker=None
        )
        async with app.run_test(size=(width, 30)) as pilot:
            await pilot.pause(0.3)
            widget = app.query_one("#status-bar", Static)
            rendered = widget.render()
            text = rendered.plain if hasattr(rendered, "plain") else str(rendered)
            assert host[:4] in text
            assert transport.upper() in text
            assert endpoint[-8:] in text
            assert len(text) <= widget.content_size.width
            datetime.strptime(text[-8:], "%H:%M:%S")

    cases = (
        ("testvert", "tcp", "192.0.2.42:4403"),
        (
            "workstation-laboratory-01234567",
            "serial",
            "/dev/serial/by-id/usb-1a86_USB_Single_Serial_58FD012345-if00",
        ),
    )
    for width in (60, 80, 100, 116, 130, 160):
        for host, transport, endpoint in cases:
            run_scenario(
                lambda width=width, host=host, transport=transport, endpoint=endpoint: scenario(
                    width,
                    host,
                    transport,
                    endpoint,
                )
            )


def test_node_info_uses_one_screen_for_metrics_position_and_traceroute():
    async def scenario():
        backend = FakeBackend()
        backend.node_actions["!710365c8"] = [
            {
                "action_id": "trace-saved",
                "action": "traceroute",
                "node_id": "!710365c8",
                "status": "completed",
                "started_at": "2026-07-20T12:09:00+00:00",
                "result": {
                    "forward": [
                        {"node_id": "!040840a0", "snr": None},
                        {"node_id": "!710365c8", "snr": 6.0},
                    ],
                    "return": None,
                },
            }
        ]
        app = MeshPiTUI(
            Settings(), requester=backend.request, watcher=None, update_checker=None
        )
        async with app.run_test(size=(160, 50)) as pilot:
            await pilot.pause(0.3)
            await pilot.press("f3", "up", "down", "shift+f10")
            await pilot.pause(0.2)
            assert isinstance(app.screen, NodeActionScreen)

            await pilot.press("i")
            await pilot.pause(0.4)
            assert isinstance(app.screen, NodeInfoScreen)
            assert app.screen.overview_data["node"]["node_id"] == "!710365c8"
            close_button = app.screen.query_one("#node-info-close", Button)
            assert close_button.parent.id == "node-info-footer"

            log = app.screen.query_one("#node-info-log", RichLog)
            overview = "\n".join(line.text for line in log.lines)
            assert "Siste telemetri" in overview
            assert "Batteri 75%" in overview
            assert "Google Maps" in overview
            assert "Nodens plassering" in overview

            await pilot.press("m")
            await pilot.pause(0.1)
            telemetry = "\n".join(line.text for line in log.lines)
            assert "Kanalbruk" in telemetry
            assert "Temperatur" in telemetry
            assert telemetry.count("Eining") == 1
            assert "┌" not in telemetry
            assert "│" not in telemetry

            await pilot.press("p")
            await pilot.pause(0.1)
            positions = "\n".join(line.text for line in log.lines)
            assert "60.1234567" in positions
            assert "5.1234567" in positions
            assert "104 m" in positions
            assert "api=1" in positions
            assert "Posisjonslogg" in positions
            assert "Kartlenkjer" in positions
            exchange = app.screen.query_one(
                "#node-info-exchange-position",
                Button,
            )
            assert exchange.display is True
            assert exchange.parent.id == "node-info-footer"
            await pilot.click(exchange)
            await pilot.pause(0.2)
            assert {
                "command": "node_action",
                "action": "position_exchange",
                "node_id": "!710365c8",
            } in backend.calls
            position_calls = [
                call
                for call in backend.calls
                if call.get("command") == "node_action"
                and call.get("action") == "position_exchange"
            ]
            await pilot.press("x")
            await pilot.pause(0.1)
            assert [
                call
                for call in backend.calls
                if call.get("command") == "node_action"
                and call.get("action") == "position_exchange"
            ] == position_calls

            opened = []
            app.open_url = opened.append
            url = (
                "https://www.google.com/maps/search/"
                "?api=1&query=60.1234567%2C5.1234567"
            )
            app.screen.action_open_map(url)
            assert opened == [url]

            await pilot.press("t")
            await pilot.pause(0.1)
            traceroutes = "\n".join(line.text for line in log.lines)
            assert "Traceroute-logg" in traceroutes
            assert "Ferdig" in traceroutes
            assert "Hopp F/T" in traceroutes
            assert "1/–" in traceroutes
            assert "6 dB" in traceroutes
            assert "┌" not in traceroutes
            run_trace = app.screen.query_one(
                "#node-info-run-traceroute",
                Button,
            )
            assert run_trace.display is True
            assert run_trace.parent.id == "node-info-footer"
            await pilot.click(run_trace)
            await pilot.pause(0.2)
            assert {
                "command": "node_action",
                "action": "traceroute",
                "node_id": "!710365c8",
            } in backend.calls

            await pilot.click("#node-info-close")
            await pilot.pause(0.1)
            assert not isinstance(app.screen, NodeInfoScreen)

            assert {
                "command": "node_telemetry",
                "node_id": "!710365c8",
                "kind": "device",
                "limit": 200,
            } in backend.calls
            assert {
                "command": "node_telemetry",
                "node_id": "!710365c8",
                "kind": "environment",
                "limit": 200,
            } in backend.calls

    run_scenario(scenario)


def test_node_info_tabs_fit_an_80_column_terminal():
    async def scenario():
        backend = FakeBackend()
        app = MeshPiTUI(
            Settings(), requester=backend.request, watcher=None, update_checker=None
        )
        async with app.run_test(size=(80, 24)) as pilot:
            await pilot.pause(0.3)
            overview = backend.request(
                Settings(),
                {"command": "node_overview", "node_id": "!710365c8"},
            )["data"]
            app.push_screen(
                NodeInfoScreen(
                    overview,
                    backend.telemetry["!710365c8"],
                    backend.positions["!710365c8"],
                    [],
                    app._format_traceroute_table_row,
                    lambda _: None,
                )
            )
            await pilot.pause(0.3)

            assert isinstance(app.screen, NodeInfoScreen)
            dialog = app.screen.query_one("#node-info-dialog")
            for tab in NodeInfoScreen.TABS:
                button = app.screen.query_one(f"#node-info-tab-{tab}", Button)
                assert button.region.right <= dialog.content_region.right

    run_scenario(scenario)


def test_node_info_cooldown_refresh_keeps_scroll_position():
    async def scenario():
        backend = FakeBackend()
        positions = [
            {
                "id": index,
                "node_id": "!710365c8",
                "dedupe_key": f"position-{index}",
                "sample_time": f"2026-07-20T12:{index % 60:02d}:00+00:00",
                "latitude": 60.0 + index / 10_000,
                "longitude": 5.0,
            }
            for index in range(80)
        ]
        overview = backend.request(
            Settings(),
            {"command": "node_overview", "node_id": "!710365c8"},
        )["data"]
        app = MeshPiTUI(
            Settings(), requester=backend.request, watcher=None, update_checker=None
        )
        async with app.run_test(size=(100, 30)) as pilot:
            app.push_screen(
                NodeInfoScreen(
                    overview,
                    [],
                    positions,
                    [],
                    app._format_traceroute_table_row,
                    lambda _: None,
                )
            )
            await pilot.pause(0.3)
            assert isinstance(app.screen, NodeInfoScreen)
            app.screen.action_position()
            log = app.screen.query_one("#node-info-log", RichLog)
            log.scroll_to(y=30, animate=False)
            await pilot.pause(0.2)
            before = log.scroll_offset

            app.screen._end_action_cooldown("position_exchange")
            await pilot.pause(0.1)

            assert before.y > 0
            assert log.scroll_offset == before
            assert not app.screen.query_one(
                "#node-info-exchange-position",
                Button,
            ).disabled

    run_scenario(scenario)


def test_reopened_node_info_uses_daemon_position_cooldown():
    async def scenario():
        backend = FakeBackend()
        backend.position_exchange_cooldown = 25
        app = MeshPiTUI(
            Settings(), requester=backend.request, watcher=None, update_checker=None
        )
        async with app.run_test(size=(160, 50)) as pilot:
            await pilot.pause(0.3)
            await pilot.press("f3", "up", "down", "shift+f10", "i")
            await pilot.pause(0.4)
            assert isinstance(app.screen, NodeInfoScreen)

            await pilot.press("p")

            assert app.screen.query_one(
                "#node-info-exchange-position",
                Button,
            ).disabled
            assert {
                "command": "node_action_availability",
                "action": "position_exchange",
                "node_id": "!710365c8",
            } in backend.calls

    run_scenario(scenario)


def test_position_exchange_notification_reports_whether_position_was_shared():
    async def scenario():
        backend = FakeBackend()
        app = MeshPiTUI(
            Settings(), requester=backend.request, watcher=None, update_checker=None
        )
        async with app.run_test(size=(160, 48)) as pilot:
            await pilot.pause(0.3)
            notices = []
            app.notify = lambda message, **_: notices.append(message)

            app._handle_node_action(
                {
                    "action_id": "position-no-share",
                    "action": "position_exchange",
                    "node_id": "!710365c8",
                    "status": "completed",
                    "result": {
                        "position_received": True,
                        "local_position_shared": False,
                        "local_position_share_reason": (
                            "posisjonsdeling er slått av for kanalen"
                        ),
                    },
                }
            )
            app._handle_node_action(
                {
                    "action_id": "position-shared",
                    "action": "position_exchange",
                    "node_id": "!710365c8",
                    "status": "completed",
                    "result": {
                        "position_received": True,
                        "local_position_shared": True,
                        "local_position_precision_bits": 16,
                    },
                }
            )

            assert "Eigen posisjon blei ikkje delt" in notices[0]
            assert "slått av for kanalen" in notices[0]
            assert "delt med 16 bits presisjon" in notices[1]

    run_scenario(scenario)


def test_node_info_keyboard_actions_do_not_bypass_disabled_local_buttons():
    async def scenario():
        backend = FakeBackend()
        app = MeshPiTUI(
            Settings(), requester=backend.request, watcher=None, update_checker=None
        )
        async with app.run_test(size=(160, 50)) as pilot:
            await pilot.pause(0.3)
            overview = backend.request(
                Settings(),
                {"command": "node_overview", "node_id": "!040840a0"},
            )["data"]
            app.push_screen(
                NodeInfoScreen(
                    overview,
                    [],
                    [],
                    [],
                    app._format_traceroute_table_row,
                    lambda action: app._node_info_action_requested(
                        "!040840a0",
                        action,
                    ),
                )
            )
            await pilot.pause(0.2)
            await pilot.press("p", "x", "t", "r")
            await pilot.pause(0.2)

            assert not any(
                call.get("command") == "node_action" for call in backend.calls
            )

    run_scenario(scenario)


def test_node_info_loads_telemetry_history_per_metric_kind():
    async def scenario():
        backend = FakeBackend()
        backend.telemetry["!710365c8"] = [
            {
                "id": index,
                "node_id": "!710365c8",
                "kind": "device",
                "sample_time": f"2026-07-20T12:{index % 60:02d}:00+00:00",
                "metrics": {"batteryLevel": 75},
            }
            for index in range(1, 201)
        ] + [
            {
                "id": 201,
                "node_id": "!710365c8",
                "kind": "environment",
                "sample_time": "2026-07-20T11:00:00+00:00",
                "metrics": {"temperature": 18.5},
            }
        ]
        app = MeshPiTUI(
            Settings(), requester=backend.request, watcher=None, update_checker=None
        )
        async with app.run_test(size=(160, 50)) as pilot:
            await pilot.pause(0.3)
            await pilot.press("f3", "up", "down", "shift+f10", "i", "m")
            await pilot.pause(0.5)

            assert isinstance(app.screen, NodeInfoScreen)
            kinds = {sample["kind"] for sample in app.screen.telemetry}
            assert kinds == {"device", "environment"}

    run_scenario(scenario)


def test_node_info_tables_render_remote_markup_as_literal_text():
    async def scenario():
        backend = FakeBackend()
        backend.nodes.extend(
            [
                {
                    "node_id": "!1111abcd",
                    "long_name": "[/b]x",
                    "short_name": "abcd",
                    "is_local": False,
                },
                {
                    "node_id": "!2222cdef",
                    "long_name": "[link=https://evil.example]a[/link]",
                    "short_name": "cdef",
                    "is_local": False,
                },
            ]
        )
        backend.telemetry["!710365c8"][0]["gateway_node_id"] = "!0408abcd"
        backend.positions["!710365c8"][0]["gateway_node_id"] = "!0408abcd"
        backend.node_actions["!710365c8"] = [
            {
                "action_id": "hostile-trace",
                "action": "traceroute",
                "node_id": "!710365c8",
                "status": "completed",
                "started_at": "2026-07-20T12:09:00+00:00",
                "result": {
                    "forward": [
                        {"node_id": "!1111abcd", "snr": 4.0},
                        {"node_id": "!2222cdef", "snr": 3.0},
                        {"node_id": "!710365c8", "snr": 2.0},
                    ],
                    "return": None,
                },
            }
        ]
        app = MeshPiTUI(
            Settings(), requester=backend.request, watcher=None, update_checker=None
        )
        async with app.run_test(size=(160, 50)) as pilot:
            await pilot.pause(0.3)
            await pilot.press("f3", "up", "down", "shift+f10", "i", "m")
            await pilot.pause(0.4)
            log = app.screen.query_one("#node-info-log", RichLog)
            assert "[abcd]" in "\n".join(line.text for line in log.lines)

            await pilot.press("p")
            await pilot.pause(0.1)
            assert "[abcd]" in "\n".join(line.text for line in log.lines)

            await pilot.press("t")
            await pilot.pause(0.1)
            rendered = "\n".join(line.text for line in log.lines)
            assert "[/b]x" in rendered
            assert "evil.example" in rendered
            assert app.is_running
            assert all(
                segment.style is None or segment.style.link is None
                for line in log.lines
                for segment in line._segments
            )

    run_scenario(scenario)


def test_open_node_info_updates_observations_chronologically():
    async def scenario():
        backend = FakeBackend()
        app = MeshPiTUI(
            Settings(), requester=backend.request, watcher=None, update_checker=None
        )
        async with app.run_test(size=(160, 50)) as pilot:
            await pilot.pause(0.3)
            await pilot.press("f3", "up", "down", "shift+f10", "i")
            await pilot.pause(0.4)
            assert isinstance(app.screen, NodeInfoScreen)
            screen = app.screen

            app.post_message(
                LiveEvent(
                    {
                        "type": "position",
                        "data": {
                            "node_id": "!710365c8",
                            "dedupe_key": "older-position",
                            "sample_time": "2026-07-20T12:00:00+00:00",
                            "latitude": 61.0,
                            "longitude": 6.0,
                        },
                    }
                )
            )
            app.post_message(
                LiveEvent(
                    {
                        "type": "telemetry",
                        "data": {
                            "node_id": "!710365c8",
                            "dedupe_key": "new-device",
                            "kind": "device",
                            "sample_time": "2026-07-20T12:15:00+00:00",
                            "metrics": {"batteryLevel": 80},
                        },
                    }
                )
            )
            app.post_message(
                LiveEvent(
                    {
                        "type": "telemetry",
                        "data": {
                            "node_id": "!710365c8",
                            "dedupe_key": "old-device",
                            "kind": "device",
                            "sample_time": "2026-07-20T12:01:00+00:00",
                            "metrics": {"batteryLevel": 70},
                        },
                    }
                )
            )
            await pilot.pause(0.2)

            assert (
                screen.overview_data["latest_position"]["sample_time"]
                == "2026-07-20T12:12:00+00:00"
            )
            assert screen.positions[0]["sample_time"] == "2026-07-20T12:12:00+00:00"
            assert (
                screen.overview_data["latest_telemetry"]["device"]["metrics"][
                    "batteryLevel"
                ]
                == 80
            )
            assert screen.overview_data["counts"]["positions"] == 2
            assert screen.overview_data["counts"]["telemetry"] == 4

    run_scenario(scenario)


def test_completed_traceroute_cannot_be_reverted_and_order_stays_stable():
    async def scenario():
        backend = FakeBackend()
        app = MeshPiTUI(
            Settings(), requester=backend.request, watcher=None, update_checker=None
        )
        async with app.run_test(size=(160, 50)) as pilot:
            await pilot.pause(0.3)
            await pilot.press("f3", "up", "down", "shift+f10", "i")
            await pilot.pause(0.4)
            assert isinstance(app.screen, NodeInfoScreen)
            completed = {
                "action_id": "trace-race",
                "action": "traceroute",
                "node_id": "!710365c8",
                "status": "completed",
                "started_at": "2026-07-20T12:20:00+00:00",
                "result": {"forward": [], "return": []},
            }
            app._handle_node_action(completed)
            app._handle_node_action(completed | {"status": "started", "result": None})
            app.screen.upsert_traceroute(
                completed
                | {
                    "action_id": "trace-older",
                    "started_at": "2026-07-20T12:19:00+00:00",
                }
            )

            by_id = {
                action["action_id"]: action for action in app.screen.traceroutes
            }
            assert by_id["trace-race"]["status"] == "completed"
            assert [
                action["action_id"] for action in app.screen.traceroutes
            ] == ["trace-older", "trace-race"]

    run_scenario(scenario)


def test_environment_metric_units_match_the_protocol():
    assert _metric_label_and_value("gasResistance", 12.3) == (
        "Gassmotstand",
        "12.3 MΩ",
    )
    assert _metric_label_and_value("distance", 1500) == (
        "Avstand",
        "1500 mm",
    )


def test_ctrl_q_opens_quit_dialog_above_node_info():
    async def scenario():
        backend = FakeBackend()
        app = MeshPiTUI(
            Settings(), requester=backend.request, watcher=None, update_checker=None
        )
        async with app.run_test(size=(160, 50)) as pilot:
            await pilot.pause(0.3)
            await pilot.press("f3", "up", "down", "shift+f10", "i")
            await pilot.pause(0.4)
            assert isinstance(app.screen, NodeInfoScreen)

            await pilot.press("ctrl+q")
            await pilot.pause(0.1)

            assert isinstance(app.screen, QuitScreen)

    run_scenario(scenario)


def test_map_link_has_terminal_and_textual_click_targets():
    url = "https://www.google.com/maps/search/?api=1&query=60%2C5"

    link = _map_link(url)

    assert link.style.link == url
    assert link.style.meta["@click"] == f"screen.open_map({url!r})"


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
            assert app.current_conversation == RESERVE_DM_CONVERSATION
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


def test_message_stays_below_older_traceroute_after_ack_refresh():
    async def scenario():
        backend = FakeBackend()
        backend.node_actions["!710365c8"] = [
            {
                "action_id": "trace-before-message",
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
            app._open_node_dm("!710365c8", focus_input=True)
            await pilot.pause(0.3)

            outgoing = {
                "timestamp": "2026-07-20T12:03:00+00:00",
                "kind": "dm",
                "direction": "ut",
                "from_node": "!040840a0",
                "peer_node": "!710365c8",
                "transport": "Ukjend",
                "status": "sendt",
                "text": "Melding etter traceroute",
            }
            outgoing["conversation_id"] = RESERVE_DM_CONVERSATION
            backend.messages[RESERVE_DM_CONVERSATION].append(outgoing)
            app.post_message(LiveEvent({"type": "message", "data": outgoing}))
            await pilot.pause(0.2)

            message_log = app.query_one("#message-log", RichLog)
            before_ack = "\n".join(line.text for line in message_log.lines)
            assert before_ack.index("TRACEROUTE · FERDIG") < before_ack.index(
                "Melding etter traceroute"
            )

            outgoing["status"] = "ACK"
            app.post_message(
                LiveEvent(
                    {
                        "type": "message_status",
                        "data": {"packet_id": 123, "status": "ACK"},
                    }
                )
            )
            await pilot.pause(0.3)

            after_ack = "\n".join(line.text for line in message_log.lines)
            assert "[ACK]" in after_ack
            assert after_ack.index("TRACEROUTE · FERDIG") < after_ack.index(
                "Melding etter traceroute"
            )

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
            assert app.current_conversation == RESERVE_DM_CONVERSATION
            assert {
                "command": "unarchive_conversation",
                "node_id": "!710365c8",
                "conversation": RESERVE_DM_CONVERSATION,
            } in backend.calls

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
