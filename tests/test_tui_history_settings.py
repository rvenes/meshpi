import asyncio
import threading

import pytest
from textual.app import App
from textual.widgets import Button, Input, Select, Static

from meshpi.tui import HistoryConfirmScreen, HistorySettingsScreen

GATEWAY = "!12345678"
COUNTS = {"messages": 12, "telemetry_samples": 4, "positions": 3, "node_actions": 2}


class HistoryApp(App):
    def __init__(self, *, block_preview=None):
        super().__init__()
        self.calls = []
        self.status_data = {"local_node_id": GATEWAY}
        self.refreshed = False
        self.block_preview = block_preview

    def on_mount(self):
        self.push_screen(HistorySettingsScreen())

    def _call(self, payload):
        self.calls.append(dict(payload))
        command = payload["command"]
        if command in {"history_settings", "history_settings_set"}:
            data = {"local_node_id": GATEWAY, "retention_days": payload.get("retention_days", 30),
                    "needs_review": command == "history_settings", "max_messages": 50000,
                    "counts": dict(COUNTS)}
        elif command == "history_cleanup_preview":
            if self.block_preview:
                self.block_preview.wait(5)
            data = {"local_node_id": GATEWAY, "cleanup_token": "one-use-preview",
                    "scope": payload["scope"], "older_than_days": payload["older_than_days"],
                    "counts": dict(COUNTS), "expires_in": 300}
        elif command == "history_cleanup":
            data = {"local_node_id": GATEWAY, "deleted": dict(COUNTS)}
        else:
            raise AssertionError(command)
        return {"data": data}

    def history_changed(self):
        self.refreshed = True


async def settle(app, pilot):
    await app.workers.wait_for_complete()
    await pilot.pause()


def test_settings_load_does_not_change_existing_history():
    async def run():
        app = HistoryApp()
        async with app.run_test(size=(110, 45)) as pilot:
            await settle(app, pilot)
            assert [call["command"] for call in app.calls] == ["history_settings"]
            assert GATEWAY in str(app.screen.query_one("#history-context", Static).render())
            assert not app.screen.query_one("#history-save", Button).disabled
            await pilot.press("escape")
            assert not any(call.get("confirmed") for call in app.calls)
    asyncio.run(run())


def test_cleanup_requires_preview_and_explicit_confirmation():
    async def run():
        app = HistoryApp()
        async with app.run_test(size=(110, 45)) as pilot:
            await settle(app, pilot)
            screen = app.screen
            screen.query_one("#history-scope", Select).value = "history"
            screen.query_one("#history-age", Select).value = 7
            screen._prepare(policy=False)
            await settle(app, pilot)
            assert isinstance(app.screen, HistoryConfirmScreen)
            assert "12" in app.screen.text and "4" in app.screen.text
            assert GATEWAY in app.screen.text
            assert not any(call["command"] == "history_cleanup" for call in app.calls)
            await pilot.click("#history-confirm-cancel")
            await pilot.pause()
            assert not any(call["command"] == "history_cleanup" for call in app.calls)
            screen._prepare(policy=False)
            await settle(app, pilot)
            await pilot.click("#history-confirm-yes")
            await settle(app, pilot)
            cleanups = [call for call in app.calls if call["command"] == "history_cleanup"]
            assert cleanups == [{"command": "history_cleanup", "expected_local_node_id": GATEWAY,
                                 "cleanup_token": "one-use-preview", "confirmed": True}]
            assert app.refreshed
    asyncio.run(run())


def test_gateway_change_rejects_confirmation():
    async def run():
        app = HistoryApp()
        async with app.run_test(size=(110, 45)) as pilot:
            await settle(app, pilot)
            app.screen._prepare(policy=False)
            await settle(app, pilot)
            app.status_data["local_node_id"] = "!87654321"
            await pilot.click("#history-confirm-yes")
            await settle(app, pilot)
            assert not any(call.get("confirmed") for call in app.calls)
    asyncio.run(run())


@pytest.mark.parametrize("days", [0, 7, 30, 91])
def test_retention_confirmed_for_active_gateway_only(days):
    async def run():
        app = HistoryApp()
        async with app.run_test(size=(110, 45)) as pilot:
            await settle(app, pilot)
            screen = app.screen
            screen.query_one("#history-retention", Select).value = (
                days if days in (0, 7, 30) else -1
            )
            screen.query_one("#history-retention-custom", Input).value = str(days)
            screen._prepare(policy=True)
            await settle(app, pilot)
            assert isinstance(app.screen, HistoryConfirmScreen)
            assert not any(call["command"] == "history_settings_set" for call in app.calls)
            await pilot.click("#history-confirm-yes")
            await settle(app, pilot)
            saved = [call for call in app.calls if call["command"] == "history_settings_set"]
            assert saved == [{"command": "history_settings_set", "expected_local_node_id": GATEWAY,
                              "retention_days": days, "confirmed": True}]
            previews = [call for call in app.calls if call["command"] == "history_cleanup_preview"]
            assert len(previews) == (0 if days == 0 else 1)
    asyncio.run(run())


@pytest.mark.parametrize("value", ["", "-1", "0", "36501", "1.5"])
def test_invalid_custom_age_never_requests_cleanup(value):
    async def run():
        app = HistoryApp()
        async with app.run_test(size=(110, 45)) as pilot:
            await settle(app, pilot)
            app.screen.query_one("#history-age", Select).value = -1
            app.screen.query_one("#history-age-custom", Input).value = value
            app.screen._prepare(policy=False)
            await settle(app, pilot)
            assert len(app.calls) == 1
    asyncio.run(run())


def test_closed_screen_ignores_late_preview_and_repeat_click():
    async def run():
        release = threading.Event()
        app = HistoryApp(block_preview=release)
        async with app.run_test(size=(110, 45)) as pilot:
            await settle(app, pilot)
            screen = app.screen
            screen._prepare(policy=False)
            screen._prepare(policy=False)
            await pilot.pause()
            screen.action_cancel()
            release.set()
            await settle(app, pilot)
            assert not isinstance(app.screen, HistoryConfirmScreen)
            assert len([call for call in app.calls
                        if call["command"] == "history_cleanup_preview"]) == 1
            assert not any(call.get("confirmed") for call in app.calls)
    asyncio.run(run())


def test_f10_opens_history_without_replacing_settings_or_draft():
    from meshpi.config import Settings
    from meshpi.tui import LiveEvent, MeshPiTUI, SettingsScreen

    backend = HistoryApp()

    def request(settings, payload):
        if payload["command"].startswith("history_"):
            return backend._call(payload)
        if payload["command"] == "status":
            data = {"state": "tilkopla", "local_node_id": GATEWAY}
        elif payload["command"] == "local_node_info":
            data = {"local_node_id": GATEWAY, "connected": True}
        else:
            data = []
        return {"ok": True, "data": data}

    async def run():
        app = MeshPiTUI(Settings(), requester=request, watcher=None,
                        update_checker=None)
        async with app.run_test(size=(100, 30)) as pilot:
            await settle(app, pilot)
            app.query_one("#message-input", Input).value = "Keep this draft"
            app._message_history["old-route"] = ["Deleted old message"]
            app.live_event(LiveEvent({"type": "history_changed",
                                      "data": {"local_node_id": "!87654321"}}))
            assert app._message_history["old-route"] == ["Deleted old message"]
            app.live_event(LiveEvent({"type": "history_changed",
                                      "data": {"local_node_id": GATEWAY}}))
            await settle(app, pilot)
            assert "old-route" not in app._message_history
            assert app.query_one("#message-input", Input).value == "Keep this draft"
            await pilot.press("f10")
            await settle(app, pilot)
            assert isinstance(app.screen, SettingsScreen)
            await pilot.click("#local-tab-app")
            await pilot.click("#settings-history")
            await settle(app, pilot)
            assert isinstance(app.screen, HistorySettingsScreen)
            app.screen.query_one("#history-retention", Select).value = 0
            app.screen._prepare(policy=True)
            await settle(app, pilot)
            await pilot.click("#history-confirm-yes")
            await settle(app, pilot)
            assert any(call["command"] == "history_settings_set" for call in backend.calls)
            await pilot.press("escape")
            assert isinstance(app.screen, SettingsScreen)
            await pilot.press("escape")
            await pilot.pause()
            assert app.query_one("#message-input", Input).value == "Keep this draft"
    asyncio.run(run())


def test_retention_settings_available_for_saved_offline_gateway():
    async def run():
        app = HistoryApp()
        app.status_data = {"local_node_id": None, "history_local_node_id": GATEWAY}
        async with app.run_test(size=(110, 45)) as pilot:
            await settle(app, pilot)
            assert app.screen.data["local_node_id"] == GATEWAY
            app.screen._prepare(policy=False)
            await settle(app, pilot)
            assert isinstance(app.screen, HistoryConfirmScreen)
            await pilot.press("escape")
            assert not any(call.get("confirmed") for call in app.calls)
    asyncio.run(run())
