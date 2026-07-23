from pathlib import Path

from meshpi.config import Settings
from meshpi.platform_service import _macos_action, manage_service


def test_macos_start_bootstraps_an_unloaded_launchagent(monkeypatch):
    commands = []
    monkeypatch.setattr(
        "meshpi.platform_service.os.getuid", lambda: 501, raising=False
    )
    monkeypatch.setattr(
        "meshpi.platform_service._macos_job_loaded", lambda _domain, _label: False
    )
    monkeypatch.setattr(
        "meshpi.platform_service._run", lambda command: commands.append(command)
    )

    assert _macos_action("start") is True
    assert commands[0][:3] == ["/bin/launchctl", "bootstrap", "gui/501"]
    plist = Path(commands[0][3])
    assert plist.name == "org.venes.meshpi.plist"
    assert plist.parent.name == "LaunchAgents"


def test_macos_start_kickstarts_a_loaded_launchagent(monkeypatch):
    commands = []
    monkeypatch.setattr(
        "meshpi.platform_service.os.getuid", lambda: 501, raising=False
    )
    monkeypatch.setattr(
        "meshpi.platform_service._macos_job_loaded", lambda _domain, _label: True
    )
    monkeypatch.setattr(
        "meshpi.platform_service._run", lambda command: commands.append(command)
    )

    assert _macos_action("start") is True
    assert commands == [
        ["/bin/launchctl", "kickstart", "gui/501/org.venes.meshpi"]
    ]


def test_macos_stop_boots_out_the_launchagent(monkeypatch):
    commands = []
    monkeypatch.setattr(
        "meshpi.platform_service.os.getuid", lambda: 501, raising=False
    )
    monkeypatch.setattr(
        "meshpi.platform_service._macos_job_loaded", lambda _domain, _label: True
    )
    monkeypatch.setattr(
        "meshpi.platform_service._run", lambda command: commands.append(command)
    )

    assert _macos_action("stop") is True
    assert commands == [["/bin/launchctl", "bootout", "gui/501/org.venes.meshpi"]]


def test_macos_stop_is_idempotent_when_launchagent_is_unloaded(monkeypatch):
    commands = []
    monkeypatch.setattr(
        "meshpi.platform_service.os.getuid", lambda: 501, raising=False
    )
    monkeypatch.setattr(
        "meshpi.platform_service._macos_job_loaded", lambda _domain, _label: False
    )
    monkeypatch.setattr(
        "meshpi.platform_service._run", lambda command: commands.append(command)
    )

    assert _macos_action("stop") is False
    assert commands == []


def test_always_mode_stop_unloads_macos_launchagent(monkeypatch):
    actions = []
    daemon_stops = []
    monkeypatch.setattr(
        "meshpi.platform_service.stop_daemon",
        lambda _settings: daemon_stops.append(True) or True,
    )
    monkeypatch.setattr("meshpi.platform_service._system", lambda: "darwin")
    monkeypatch.setattr(
        "meshpi.platform_service._macos_action",
        lambda action: actions.append(action) or True,
    )

    result = manage_service("stop", Settings(background_mode="always"), ".env")

    assert result == {"state": "stoppa", "changed": True}
    assert actions == ["stop"]
    assert daemon_stops == []


def test_always_mode_stops_daemon_when_macos_launchagent_is_already_unloaded(
    monkeypatch,
):
    daemon_stops = []
    monkeypatch.setattr(
        "meshpi.platform_service.stop_daemon",
        lambda _settings: daemon_stops.append(True) or True,
    )
    monkeypatch.setattr("meshpi.platform_service._system", lambda: "darwin")
    monkeypatch.setattr("meshpi.platform_service._macos_action", lambda _action: False)

    result = manage_service("stop", Settings(background_mode="always"), ".env")

    assert result == {"state": "stoppa", "changed": True}
    assert daemon_stops == [True]


def test_session_mode_stop_does_not_touch_macos_launchagent(monkeypatch):
    actions = []
    monkeypatch.setattr("meshpi.platform_service.stop_daemon", lambda _settings: True)
    monkeypatch.setattr("meshpi.platform_service._system", lambda: "darwin")
    monkeypatch.setattr(
        "meshpi.platform_service._macos_action", lambda action: actions.append(action)
    )

    result = manage_service("stop", Settings(background_mode="session"), ".env")

    assert result == {"state": "stoppa", "changed": True}
    assert actions == []
