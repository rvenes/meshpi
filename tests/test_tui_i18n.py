import asyncio
import json

from textual.widgets import Input, Static

from meshpi.config import Settings
from meshpi.i18n import using_language
from meshpi.tui import HelpScreen, MeshPiTUI, SettingsScreen

LOCAL_ID = "!040840a0"
PEER_ID = "!710365c8"
CHANNEL_KEY = f"local:{LOCAL_ID}:0:"
PUBLIC = f"channel:{CHANNEL_KEY}"
DM = f"dm:{LOCAL_ID}:{PEER_ID}:{CHANNEL_KEY}"


class I18nBackend:
    def __init__(self) -> None:
        self.calls: list[dict] = []
        self.status = {
            "state": "tilkopla",
            "transport": "tcp",
            "endpoint": "192.0.2.42:4403",
            "local_node_id": LOCAL_ID,
        }
        self.nodes = [
            {
                "node_id": LOCAL_ID,
                "long_name": "Local node",
                "short_name": "40a0",
                "is_local": True,
            },
            {
                "node_id": PEER_ID,
                "long_name": "Remote node",
                "short_name": "65c8",
                "is_local": False,
                "battery_level": 75,
            },
        ]
        self.conversations = [
            {
                "conversation": PUBLIC,
                "kind": "public",
                "channel": 0,
                "channel_key": CHANNEL_KEY,
                "local_node_id": LOCAL_ID,
                "sendable": True,
                "last_timestamp": None,
                "last_text": None,
                "unread": 0,
            },
            {
                "conversation": DM,
                "kind": "dm",
                "peer_node": PEER_ID,
                "channel": 0,
                "channel_key": CHANNEL_KEY,
                "local_node_id": LOCAL_ID,
                "sendable": True,
                "long_name": "Remote node",
                "last_timestamp": None,
                "last_text": "Test",
                "unread": 0,
            },
        ]

    def request(self, settings, payload):
        del settings
        self.calls.append(dict(payload))
        command = payload["command"]
        if command == "status":
            data = self.status
        elif command == "conversations":
            data = self.conversations
        elif command == "nodes":
            data = self.nodes
        elif command in {"messages", "node_actions"}:
            data = []
        elif command == "node":
            data = next(node for node in self.nodes if node["node_id"] == payload["node_id"])
        else:
            raise RuntimeError(command)
        return {"ok": True, "data": data}


def _text(widget: Static) -> str:
    rendered = widget.render()
    return rendered.plain if hasattr(rendered, "plain") else str(rendered)


def test_f10_is_settings_and_shift_f10_remains_node_actions():
    with using_language("nn"):
        app = MeshPiTUI(
            Settings(), requester=I18nBackend().request, watcher=None, update_checker=None
        )

    assert app._bindings.key_to_bindings["f10"][0].action == "settings"
    assert app._bindings.key_to_bindings["shift+f10"][0].action == "node_actions"


def test_tui_starts_in_english_and_help_uses_dynamic_language():
    async def scenario() -> None:
        with using_language("en"):
            app = MeshPiTUI(
                Settings(),
                requester=I18nBackend().request,
                watcher=None,
                update_checker=None,
            )
            async with app.run_test(size=(130, 42)) as pilot:
                await pilot.pause(0.3)
                assert _text(app.query_one("#conversation-panel-title", Static)) == (
                    "Conversations"
                )
                assert app.query_one("#message-input", Input).placeholder == (
                    "Write a message and press Enter"
                )
                await pilot.press("f1")
                await pilot.pause()
                assert isinstance(app.screen, HelpScreen)
                assert _text(app.screen.query_one("#help-title", Static)) == (
                    "Keyboard shortcuts"
                )
                shortcuts = _text(app.screen.query_one("#help-shortcuts", Static))
                assert "F10" in shortcuts
                assert "Choose language and other app settings" in shortcuts

    asyncio.run(scenario())


def test_tui_translates_stored_message_status_without_changing_machine_value():
    with using_language("en"):
        app = MeshPiTUI(
            Settings(),
            requester=I18nBackend().request,
            watcher=None,
            update_checker=None,
        )
        rendered = app._render_message(
            {
                "from_node": LOCAL_ID,
                "direction": "ut",
                "status": "levert",
                "text": "Test",
                "transport": "RF",
            }
        ).plain

    assert "[delivered]" in rendered


def test_live_language_switch_persists_and_preserves_tui_state(tmp_path, monkeypatch):
    language_path = tmp_path / "language.json"
    monkeypatch.setenv("MESHPI_LANGUAGE_FILE", str(language_path))

    async def scenario() -> None:
        backend = I18nBackend()
        with using_language("nn"):
            app = MeshPiTUI(
                Settings(),
                requester=backend.request,
                watcher=None,
                update_checker=None,
            )
            async with app.run_test(size=(170, 42)) as pilot:
                await pilot.pause(0.4)
                app.current_conversation = DM
                app.select_conversation(DM)
                app.selected_node_id = PEER_ID
                app._select_sidebar_node(PEER_ID)
                app._message_history[DM] = ["older message"]
                app._message_history_loaded.add(DM)
                message_input = app.query_one("#message-input", Input)
                message_input.value = "draft message"
                message_input.focus()
                message_input.cursor_position = 5
                app.show_direct_messages = True
                app.show_secondary_channels = False
                await pilot.pause(0.2)
                message_input.cursor_position = 5

                status_calls = sum(call["command"] == "status" for call in backend.calls)
                await pilot.press("f10")
                await pilot.pause()
                assert isinstance(app.screen, SettingsScreen)
                await pilot.click("#settings-en")
                await pilot.pause(0.6)

                assert json.loads(language_path.read_text(encoding="utf-8")) == {
                    "schema_version": 1,
                    "language": "en",
                }
                assert _text(app.query_one("#conversation-panel-title", Static)) == (
                    "Conversations"
                )
                assert app.query_one("#message-input", Input).placeholder == (
                    "Write a message and press Enter"
                )
                assert app.current_conversation == DM
                assert app.selected_node_id == PEER_ID
                assert app.show_direct_messages is True
                assert app.show_secondary_channels is False
                assert app._message_history[DM] == ["older message"]
                assert message_input.value == "draft message"
                assert message_input.cursor_position == 5
                assert app.focused is message_input
                assert sum(call["command"] == "status" for call in backend.calls) == (
                    status_calls
                )

                # Textual sine eksisterande timeroppgåver har konteksten frå
                # før språkbytet. Statuslinja skal likevel halde seg engelsk
                # når eittsekundstimeren teiknar henne på nytt.
                await pilot.pause(1.2)
                status_bar = _text(app.query_one("#status-bar", Static))
                assert "Host:" in status_bar
                assert "Connected" in status_bar
                assert "Vert:" not in status_bar
                assert "Tilkopla" not in status_bar

    asyncio.run(scenario())
