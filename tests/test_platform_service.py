from pathlib import Path

import pytest

from meshpi.config import Settings
from meshpi.platform_service import _linux_action, _macos_action, manage_service


def test_linux_service_action_uses_approved_absolute_systemctl(monkeypatch):
    commands = []
    monkeypatch.setenv("PATH", "/tmp/forgifta")
    monkeypatch.setattr(
        "meshpi.platform_service._systemctl_path",
        lambda: "/usr/bin/systemctl",
    )
    monkeypatch.setattr(
        "meshpi.platform_service._run",
        lambda command, hint=None: commands.append((command, hint)),
    )
    monkeypatch.setattr(
        "meshpi.platform_service.os.geteuid",
        lambda: 1000,
        raising=False,
    )

    _linux_action("start")

    assert commands == [
        (
            ["/usr/bin/systemctl", "start", "meshpi.service"],
            "sudo /usr/bin/systemctl start meshpi.service",
        )
    ]


def test_macos_start_bootstraps_an_unloaded_launchagent(monkeypatch):
    commands = []
    monkeypatch.setattr("meshpi.platform_service.os.getuid", lambda: 501, raising=False)
    monkeypatch.setattr("meshpi.platform_service._macos_job_loaded", lambda _domain, _label: False)
    monkeypatch.setattr("meshpi.platform_service._run", lambda command: commands.append(command))

    assert _macos_action("start") is True
    assert commands[0][:3] == ["/bin/launchctl", "bootstrap", "gui/501"]
    plist = Path(commands[0][3])
    assert plist.name == "org.venes.meshpi.plist"
    assert plist.parent.name == "LaunchAgents"


def test_macos_start_kickstarts_a_loaded_launchagent(monkeypatch):
    commands = []
    monkeypatch.setattr("meshpi.platform_service.os.getuid", lambda: 501, raising=False)
    monkeypatch.setattr("meshpi.platform_service._macos_job_loaded", lambda _domain, _label: True)
    monkeypatch.setattr("meshpi.platform_service._run", lambda command: commands.append(command))

    assert _macos_action("start") is True
    assert commands == [["/bin/launchctl", "kickstart", "gui/501/org.venes.meshpi"]]


def test_macos_stop_boots_out_the_launchagent(monkeypatch):
    commands = []
    monkeypatch.setattr("meshpi.platform_service.os.getuid", lambda: 501, raising=False)
    states = iter([True, True, False])
    monkeypatch.setattr(
        "meshpi.platform_service._macos_job_loaded", lambda _domain, _label: next(states)
    )
    waits = []
    monkeypatch.setattr("meshpi.platform_service.time.sleep", lambda seconds: waits.append(seconds))
    monkeypatch.setattr("meshpi.platform_service._run", lambda command: commands.append(command))

    assert _macos_action("stop") is True
    assert commands == [["/bin/launchctl", "bootout", "gui/501/org.venes.meshpi"]]
    assert waits == [0.1]


def test_macos_stop_is_idempotent_when_launchagent_is_unloaded(monkeypatch):
    commands = []
    monkeypatch.setattr("meshpi.platform_service.os.getuid", lambda: 501, raising=False)
    monkeypatch.setattr("meshpi.platform_service._macos_job_loaded", lambda _domain, _label: False)
    monkeypatch.setattr("meshpi.platform_service._run", lambda command: commands.append(command))

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


@pytest.mark.parametrize("system", ["linux", "darwin"])
@pytest.mark.parametrize("action", ["start", "enable", "disable", "stop"])
def test_always_service_controls_web_in_dependency_order(monkeypatch, system, action):
    calls = []
    monkeypatch.setattr("meshpi.platform_service._system", lambda: system)
    monkeypatch.setattr("meshpi.platform_service._web_service_installed", lambda _: True)
    monkeypatch.setattr(
        "meshpi.platform_service._linux_action",
        lambda action, unit="meshpi.service": calls.append((action, unit)),
    )
    monkeypatch.setattr(
        "meshpi.platform_service._macos_action",
        lambda action, web=False: calls.append((action, "web" if web else "daemon")) or True,
    )
    monkeypatch.setattr(
        "meshpi.platform_service.stop_daemon",
        lambda _: pytest.fail("Managed processes must be stopped through their supervisor"),
    )
    manage_service(action, Settings(background_mode="always", web_enabled=True), ".env")
    units = ["meshpi.service", "meshpi-web.service"] if system == "linux" else ["daemon", "web"]
    if action in {"stop", "disable"}:
        units.reverse()
    assert calls == [(action, unit) for unit in units]


@pytest.mark.parametrize("system", ["linux", "darwin"])
def test_stop_cleans_up_web_after_config_was_disabled(monkeypatch, system):
    calls = []
    monkeypatch.setattr("meshpi.platform_service._system", lambda: system)
    monkeypatch.setattr("meshpi.platform_service._web_service_installed", lambda _: True)
    monkeypatch.setattr(
        "meshpi.platform_service._linux_action",
        lambda action, unit="meshpi.service": calls.append(unit),
    )
    monkeypatch.setattr(
        "meshpi.platform_service._macos_action",
        lambda action, web=False: calls.append("web" if web else "daemon") or True,
    )
    manage_service("stop", Settings(background_mode="always", web_enabled=False), ".env")
    assert len(calls) == 2
    assert "web" in calls[0]


@pytest.mark.parametrize("installed", [True, False])
def test_start_does_not_enable_unselected_web(monkeypatch, installed):
    calls = []
    monkeypatch.setattr("meshpi.platform_service._system", lambda: "linux")
    monkeypatch.setattr("meshpi.platform_service._web_service_installed", lambda _: installed)
    monkeypatch.setattr(
        "meshpi.platform_service._linux_action",
        lambda action, unit="meshpi.service": calls.append((action, unit)),
    )
    manage_service("start", Settings(background_mode="always", web_enabled=False), ".env")
    assert calls == [("start", "meshpi.service")]


def test_old_installation_without_web_keeps_ipc_stop(monkeypatch):
    stops = []
    monkeypatch.setattr("meshpi.platform_service._system", lambda: "linux")
    monkeypatch.setattr("meshpi.platform_service._web_service_installed", lambda _: False)
    monkeypatch.setattr("meshpi.platform_service.stop_daemon", lambda _: stops.append(True) or True)
    manage_service("stop", Settings(background_mode="always", web_enabled=True), ".env")
    assert stops == [True]


@pytest.mark.parametrize("has_managed_stop", [True, False])
def test_windows_stop_supports_new_and_legacy_managers(monkeypatch, tmp_path, has_managed_stop):
    from meshpi.platform_service import _windows_action

    manager = tmp_path / "bin/meshpi-service.ps1"
    manager.parent.mkdir()
    manager.write_text("# MeshPi managed stop v1" if has_managed_stop else "# legacy")
    monkeypatch.setattr(
        "meshpi.platform_service.sys.executable",
        str(tmp_path / "releases/1.0/venv/Scripts/python.exe"),
    )
    monkeypatch.setattr("meshpi.platform_service.windows_powershell_path", lambda: "powershell")
    calls = []
    monkeypatch.setattr("meshpi.platform_service._run", lambda command: calls.append(command))
    assert _windows_action("stop") is has_managed_stop
    assert bool(calls) is has_managed_stop
    if calls:
        assert calls[0][-2:] == [str(manager), "stop"]


def test_windows_managed_stop_avoids_an_ipc_restart_race(monkeypatch):
    calls = []
    monkeypatch.setattr("meshpi.platform_service._system", lambda: "windows")
    monkeypatch.setattr(
        "meshpi.platform_service._windows_action", lambda action: calls.append(action) or True
    )
    monkeypatch.setattr(
        "meshpi.platform_service.stop_daemon", lambda _: calls.append("graceful") or True
    )
    manage_service("stop", Settings(background_mode="always"), ".env")
    assert calls == ["graceful", "stop"]


def test_windows_graceful_stop_failure_does_not_force_processes(monkeypatch):
    monkeypatch.setattr("meshpi.platform_service._system", lambda: "windows")

    def fail_shutdown(_):
        raise RuntimeError("shutdown timeout")

    monkeypatch.setattr("meshpi.platform_service.stop_daemon", fail_shutdown)
    monkeypatch.setattr(
        "meshpi.platform_service._windows_action", lambda _: pytest.fail("forced cleanup")
    )
    with pytest.raises(RuntimeError, match="shutdown timeout"):
        manage_service("stop", Settings(background_mode="always"), ".env")


@pytest.mark.parametrize("action", ["stop", "disable"])
def test_macos_bootout_timeout_is_bounded(monkeypatch, action):
    monkeypatch.setattr("meshpi.platform_service.os.getuid", lambda: 501, raising=False)
    monkeypatch.setattr("meshpi.platform_service._macos_job_loaded", lambda *_: True)
    calls = []
    monkeypatch.setattr("meshpi.platform_service._run", lambda command: calls.append(command))
    waits = []
    monkeypatch.setattr("meshpi.platform_service.time.sleep", lambda seconds: waits.append(seconds))
    with pytest.raises(RuntimeError, match="bootout"):
        _macos_action(action, web=True)
    assert calls == [["/bin/launchctl", "bootout", "gui/501/org.venes.meshpi.web"]]
    assert waits == [0.1] * 50
