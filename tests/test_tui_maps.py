"""Map launch regression tests never start a browser, daemon, or radio."""

import asyncio
import os
import subprocess

import pytest
from test_tui_node_list import FIRST, NodeListBackend, _open_nodes
from textual.widgets import DataTable, Input

from meshpi.config import Settings
from meshpi.tui import MapLinkScreen, MeshPiTUI, StoredNodeScreen, _launch_map_browser

URL = "https://www.google.com/maps/search/?api=1&query=60.1234567%2C5.7654321"


@pytest.mark.parametrize("remote", ["SSH_CONNECTION", "SSH_TTY", "SSH_CLIENT", "headless"])
def test_f10_map_keeps_terminal_responsive_and_offers_copy(monkeypatch, remote):
    for key in ("SSH_CONNECTION", "SSH_TTY", "SSH_CLIENT", "DISPLAY", "WAYLAND_DISPLAY"):
        monkeypatch.delenv(key, raising=False)
    if remote != "headless":
        monkeypatch.setenv(remote, "mock")
        monkeypatch.setenv("DISPLAY", ":forwarded")
    else:
        monkeypatch.setattr("meshpi.tui.sys.platform", "linux")
    monkeypatch.setattr("meshpi.tui._launch_map_browser",
                        lambda _: pytest.fail("A remote map started a browser"))

    async def scenario():
        backend = NodeListBackend()
        app = MeshPiTUI(Settings(), requester=backend.request, watcher=None, update_checker=None)
        copied = []
        app.copy_to_clipboard = copied.append
        async with app.run_test(size=(80, 24)) as pilot:
            settings = await _open_nodes(app, pilot)
            table = settings.query_one("#local-nodes-table", DataTable)
            table.move_cursor(row=settings.node_rows.index(FIRST))
            table.focus()
            await pilot.press("enter")
            await pilot.pause()
            details = app.screen
            assert isinstance(details, StoredNodeScreen)
            await pilot.click("#stored-node-map")
            await pilot.pause()
            assert isinstance(app.screen, MapLinkScreen)
            assert app.screen.query_one("#map-link-url", Input).value == URL
            await pilot.click("#map-link-copy")
            assert copied == [URL]
            await pilot.press("escape")
            assert app.screen is details
            await pilot.press("escape")
            assert app.screen is settings
            assert not any(call["command"] == "node_action" for call in backend.calls)

    asyncio.run(scenario())


@pytest.mark.parametrize("platform, opener", [("linux", "/usr/bin/xdg-open"),
                                               ("darwin", "/usr/bin/open")])
@pytest.mark.skipif(os.name != "posix", reason="POSIX desktop launchers")
def test_desktop_opener_is_detached_from_terminal(monkeypatch, platform, opener):
    calls = []
    monkeypatch.setattr("meshpi.tui.sys.platform", platform)
    monkeypatch.setattr("meshpi.tui.os.name", "posix")
    monkeypatch.setattr("meshpi.tui.Path.is_file", lambda _: True)
    monkeypatch.setattr("meshpi.tui.subprocess.Popen",
                        lambda *args, **kwargs: calls.append((args, kwargs)))
    assert _launch_map_browser(URL)
    assert calls == [(([opener, URL],), {
        "stdin": subprocess.DEVNULL, "stdout": subprocess.DEVNULL,
        "stderr": subprocess.DEVNULL, "start_new_session": True,
    })]


@pytest.mark.skipif(os.name != "posix", reason="POSIX desktop launchers")
def test_unavailable_desktop_opener_offers_link_without_error(monkeypatch):
    monkeypatch.setattr("meshpi.tui.os.name", "posix")
    monkeypatch.setattr("meshpi.tui.Path.is_file", lambda _: False)
    assert not _launch_map_browser(URL)


@pytest.mark.skipif(os.name != "nt", reason="Windows desktop launcher")
def test_windows_uses_nonblocking_desktop_launcher(monkeypatch):
    opened = []
    monkeypatch.setattr("meshpi.tui.os.startfile", opened.append)
    assert _launch_map_browser(URL)
    assert opened == [URL]
