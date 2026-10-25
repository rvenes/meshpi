from __future__ import annotations

import os
import re
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]


def source(platform: str) -> str:
    return (ROOT / "installers" / f"install-{platform}.sh").read_text()


def function(text: str, name: str) -> str:
    match = re.search(rf"(?ms)^{name}\(\) \{{\n.*?^\}}\n", text)
    assert match
    return match[0]


def run(script: str, directory: Path, **settings: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["sh", "-c", script],
        text=True,
        capture_output=True,
        env={
            **os.environ,
            "CONFIG_FILE": str(directory / "meshpi.env"),
            "TMP_DIR": str(directory / "temporary"),
            **settings,
        },
        check=False,
    )


@pytest.mark.parametrize("platform", ["linux", "macos"])
@pytest.mark.parametrize(
    ("existing", "choice", "expected"),
    [
        ("", "keep", "false"),
        (
            "WEB_ENABLED=true\nWEB_PORT=9123\nWEB_PUBLIC_URL=https://host.example\nWEB_BASE_PATH=/radio\n",
            "keep",
            "true",
        ),
        ("WEB_ENABLED=true\n", "false", "false"),
        ("WEB_ENABLED=false\n", "true", "true"),
    ],
)
def test_web_install_choice_is_optional_and_preserves_settings(
    tmp_path: Path,
    platform: str,
    existing: str,
    choice: str,
    expected: str,
) -> None:
    config = tmp_path / "meshpi.env"
    config.write_text(existing + "IPC_TOKEN=private-test-value\n")
    (tmp_path / "temporary").mkdir()
    text = source(platform)
    script = (
        "\n".join(
            function(text, name)
            for name in (
                "get_env_value",
                "choose_web",
                "set_env_value",
                "save_web_settings",
            )
        )
        + "\nchoose_web\nsave_web_settings\n"
    )
    result = run(script, tmp_path, WEB_CHOICE=choice, FRESH_INSTALL="1")
    assert result.returncode == 0, result.stderr
    values = dict(line.split("=", 1) for line in config.read_text().splitlines())
    assert values["WEB_ENABLED"] == expected
    assert values["IPC_TOKEN"] == "private-test-value"
    assert values["WEB_PORT"] == ("9123" if "9123" in existing else "8780")
    assert values["WEB_BASE_PATH"] == ("/radio" if "/radio" in existing else "/meshpi")
    assert values["WEB_PUBLIC_URL"] == ("https://host.example" if "https:" in existing else "")
    assert not result.stdout  # Noninteractive installations never ask for input.


@pytest.mark.parametrize("platform", ["linux", "macos"])
def test_saved_web_config_cannot_execute_shell(tmp_path: Path, platform: str) -> None:
    (tmp_path / "meshpi.env").write_text("WEB_ENABLED=$(touch forbidden)\n")
    text = source(platform)
    script = "\n".join(function(text, name) for name in ("get_env_value", "choose_web"))
    result = run(script + "\nchoose_web\n", tmp_path, WEB_CHOICE="keep", FRESH_INSTALL="0")
    assert result.returncode != 0
    assert "WEB_ENABLED" in result.stderr
    assert not (ROOT / "forbidden").exists()


@pytest.mark.parametrize("platform", ["linux", "macos"])
def test_web_services_are_private_separate_and_health_checked(platform: str) -> None:
    text = source(platform)
    assert "web-check >/dev/null 2>&1" in text
    assert "MESHPI_FORCE_WEB_HEALTH_FAILURE" in text
    assert "--lan-address" not in text
    assert "tailscale serve" not in text
    assert "--web) WEB_CHOICE=true" in text
    assert "--no-web) WEB_CHOICE=false" in text
    assert text.index("choose_web\n") < text.index("snapshot_file config")
    if platform == "linux":
        assert "After=network-online.target meshpi.service" in text
        assert "Requires=meshpi.service" not in text
        assert "--env-file $CONFIG_FILE web --managed" in text
        assert text.index("stop meshpi-web.service") < text.index("stop meshpi.service")
    else:
        assert '"web", "--managed"' in text
        assert '"KeepAlive": {"SuccessfulExit": False}' in text
        assert 'stop_launch_job "$WEB_LABEL"' in text


@pytest.mark.parametrize("platform", ["linux", "macos"])
def test_failed_activation_restores_config_service_and_release(
    tmp_path: Path,
    platform: str,
) -> None:
    text = source(platform)
    work = tmp_path / "temporary"
    work.mkdir()
    config = tmp_path / "meshpi.env"
    config.write_text("WEB_ENABLED=true\nIPC_TOKEN=kept\n")
    daemon = tmp_path / "daemon.service"
    daemon.write_text("original daemon")
    web = tmp_path / "web.service"
    web.write_text("original web")
    launcher = tmp_path / "meshpi"
    launcher.write_text("original launcher")
    old = tmp_path / "old"
    old.mkdir()
    current = tmp_path / "current"
    current.symlink_to(tmp_path / "bad")
    log = tmp_path / "services.log"
    ctl = tmp_path / "control"
    ctl.write_text('#!/bin/sh\nprintf "%s\\n" "$*" >> "$CONTROL_LOG"\n')
    ctl.chmod(0o700)
    keys = [
        "config",
        "daemon-unit" if platform == "linux" else "daemon-plist",
        "web-unit" if platform == "linux" else "web-plist",
        "launcher",
    ]
    for key, path in zip(keys, (config, daemon, web, launcher), strict=True):
        (work / f"{key}.saved").write_bytes(path.read_bytes())
        path.write_text("bad replacement")
    script = "\n".join(function(text, name) for name in ("restore_file", "finish_install"))
    script += """
say_error() { :; }
switch_link() { rm -f "$CURRENT_LINK"; ln -s "$1" "$CURRENT_LINK"; }
stop_launch_job() { :; }
launchctl() { "$SYSTEMCTL" "$@"; }
trap finish_install EXIT
exit 1
"""
    result = run(
        script,
        tmp_path,
        ACTIVATION_STARTED="1",
        SYSTEMCTL=str(ctl),
        CONTROL_LOG=str(log),
        UNIT_FILE=str(daemon),
        WEB_UNIT_FILE=str(web),
        BIN_FILE=str(launcher),
        PLIST_FILE=str(daemon),
        WEB_PLIST_FILE=str(web),
        BIN_DIR=str(tmp_path),
        OLD_RELEASE=str(old),
        CURRENT_LINK=str(current),
        PREVIOUS_LINK=str(tmp_path / "previous"),
        SKIP_SERVICE="0",
        DOMAIN="gui/1000",
        LABEL="org.venes.meshpi",
        WEB_LABEL="org.venes.meshpi.web",
        OLD_DAEMON_ACTIVE="1",
        OLD_DAEMON_ENABLED="1",
        OLD_WEB_ACTIVE="1",
        OLD_WEB_ENABLED="1",
    )
    assert result.returncode == 1, result.stderr
    assert config.read_text() == "WEB_ENABLED=true\nIPC_TOKEN=kept\n"
    assert daemon.read_text() == "original daemon"
    assert web.read_text() == "original web"
    assert launcher.read_text() == "original launcher"
    assert current.resolve() == old
    assert (
        "start meshpi-web.service" if platform == "linux" else f"bootstrap gui/1000 {web}"
    ) in log.read_text()


def test_windows_web_supervision_and_rollback_contract() -> None:
    text = (ROOT / "installers/install-windows.ps1").read_text(encoding="utf-8-sig")
    assert '[ValidateSet("Enabled", "Disabled", "Keep")]' in text
    assert "-not [Console]::IsInputRedirected" in text
    assert 'Set-EnvValue $configFile "WEB_ENABLED" $webEnabled' in text
    assert '`$settings -contains "WEB_ENABLED=true"' in text
    assert "`$children[`$command]" in text
    assert '`$commands = @("daemon")' in text
    assert "& $meshpiCmd web-check" in text
    assert "Copy-Item -LiteralPath $backupFiles[$target] -Destination $target -Force" in text
    assert "Write-MeshPiLaunchers $binDir $oldRelease $configFile" in text


def test_uninstall_removes_web_registration_but_preserves_auth_state() -> None:
    for name, service in [("linux", "meshpi-web.service"), ("macos", "org.venes.meshpi.web")]:
        text = (ROOT / "installers" / f"uninstall-{name}.sh").read_text()
        assert service in text
        assert "web-auth" not in text
    windows = (ROOT / "installers/uninstall-windows.ps1").read_text(encoding="utf-8-sig")
    assert "$_.CommandLine.IndexOf(" in windows  # includes supervised web child
    assert "web-auth" not in windows


@pytest.mark.parametrize(
    ("enabled", "mode", "health_failure", "expected_status"),
    [
        ("true", "always", "0", 0),
        ("false", "always", "0", 0),
        ("true", "session", "0", 0),
        ("true", "always", "1", 1),
    ],
)
def test_linux_activation_controls_only_selected_web_service(
    tmp_path: Path,
    enabled: str,
    mode: str,
    health_failure: str,
    expected_status: int,
) -> None:
    text = source("linux")
    start = text.index(
        'if [ "$MODE" = "always" ] && [ "$SKIP_SERVICE" != "1" ]; then\n    cat >"$UNIT_FILE"'
    )
    end = text.index('\nif [ "$FRESH_INSTALL"', start)
    block = text[start:end]
    executable = tmp_path / "current/.venv/bin/meshpi"
    executable.parent.mkdir(parents=True)
    executable.write_text('#!/bin/sh\nprintf "%s\\n" "$*" >> "$CLIENT_LOG"\nexit 0\n')
    executable.chmod(0o700)
    ctl = tmp_path / "control"
    ctl.write_text('#!/bin/sh\nprintf "%s\\n" "$*" >> "$CONTROL_LOG"\n')
    ctl.chmod(0o700)
    log = tmp_path / "services.log"
    result = run(
        "set -eu\nmessage() { echo MeshPi; }\nsleep() { :; }\n" + block,
        tmp_path,
        MODE=mode,
        SKIP_SERVICE="0",
        WEB_ENABLED=enabled,
        SYSTEMCTL=str(ctl),
        CONTROL_LOG=str(log),
        CLIENT_LOG=str(tmp_path / "client.log"),
        UNIT_FILE=str(tmp_path / "daemon.service"),
        WEB_UNIT_FILE=str(tmp_path / "web.service"),
        STATE_DIR=str(tmp_path),
        CURRENT_LINK=str(tmp_path / "current"),
        IPC_SOCKET_GID_VALUE="",
        MESHPI_FORCE_WEB_HEALTH_FAILURE=health_failure,
    )
    assert result.returncode == expected_status, result.stderr
    if mode == "session":
        assert not log.exists()
        assert not (tmp_path / "web.service").exists()
        return
    assert (tmp_path / "web.service").exists()  # Can enable later without reinstall.
    calls = log.read_text().splitlines()
    assert "start meshpi.service" in calls
    assert ("start meshpi-web.service" in calls) == (enabled == "true")
    assert ("enable meshpi-web.service" in calls) == (enabled == "true")
    assert ("disable meshpi-web.service" in calls) == (enabled == "false")
    assert ("web-check" in (tmp_path / "client.log").read_text()) == (
        enabled == "true" and health_failure == "0"
    )


def test_windows_rollback_restores_autostart_separately_from_running_state():
    text = (ROOT / "installers/install-windows.ps1").read_text(encoding="utf-8-sig")
    rollback = text[text.index("    $installError = $_") :]
    assert "Copy-Item -LiteralPath $backupFiles[$target] -Destination $target -Force" in rollback
    assert "if ($wasRunning -and" in rollback
    assert "-File $managerFile start" in rollback
    assert "-File $managerFile enable" not in rollback
    assert "-File $managerFile disable" not in rollback
    assert "$oldMode" not in rollback
    assert '$null -eq (Get-EnvValue $configFile "WEB_BASE_PATH")' in text
    assert (
        "$_.CommandLine -match" in text[text.index("$wasRunning =") : text.index("$backupFiles =")]
    )


def test_windows_manager_never_forces_daemon_shutdown():
    text = (ROOT / "installers/install-windows.ps1").read_text(encoding="utf-8-sig")
    block = text.split('} elseif (`$Action -eq "stop") {', 1)[1].split(
        '} elseif (`$Action -eq "start") {', 1
    )[0]
    assert "`$daemons.Count -gt 0" in block
    assert "throw 'MeshPi-daemonen" in block
    assert "Get-MeshPiChildren | Where-Object" in block
    assert "\\sweb\\s*$" in block
    assert block.index("Get-MeshPiSupervisor") < block.index("Get-MeshPiChildren")


@pytest.mark.parametrize("platform", ["linux", "macos"])
@pytest.mark.parametrize("enabled", ["true", "false"])
@pytest.mark.parametrize("mode", ["always", "session"])
def test_completion_explains_optional_web_access(
    tmp_path: Path, platform: str, enabled: str, mode: str
) -> None:
    (tmp_path / "meshpi.env").write_text(
        "WEB_PORT=9123\nWEB_BASE_PATH=/radio\nWEB_PUBLIC_URL=\n"
    )
    text = source(platform)
    script = "\n".join(function(text, name) for name in ("get_env_value", "show_web_access"))
    result = run(script + "\nshow_web_access\n", tmp_path, WEB_ENABLED=enabled, MODE=mode)
    assert result.returncode == 0, result.stderr
    assert "meshpi web-info" in result.stdout
    if enabled == "true":
        assert "http://localhost:9123/radio/" in result.stdout
        assert "Tailscale Serve må setjast opp separat" in result.stdout
        assert "meshpi web-code" in result.stdout
        assert ("Start webgrensesnittet med:" in result.stdout) == (mode == "session")
    else:
        assert "Webgrensesnittet er avslått" in result.stdout
        assert "steg til aktivering og tilgang" in result.stdout
        assert "http://" not in result.stdout
        assert "meshpi web-code" not in result.stdout


def test_linux_missing_optional_unit_probe_is_quiet(tmp_path: Path) -> None:
    text = source("linux")
    block = text[text.index("OLD_DAEMON_ACTIVE=0\n") : text.index("finish_install() {")]
    control = tmp_path / "control"
    control.write_text(
        '#!/bin/sh\nif [ "$1" = is-enabled ]; then\n'
        '  echo "Failed to get unit file state: No such file" >&2\nfi\nexit 1\n'
    )
    control.chmod(0o700)
    result = run(
        block + '\nprintf "%s %s\\n" "$OLD_DAEMON_ENABLED" "$OLD_WEB_ENABLED"\n',
        tmp_path,
        SYSTEMCTL=str(control),
    )
    assert result.returncode == 0
    assert result.stdout == "0 0\n"
    assert not result.stderr


def test_windows_completion_explains_enabled_and_disabled_web_access() -> None:
    text = (ROOT / "installers/install-windows.ps1").read_text(encoding="utf-8-sig")
    completion = text[
        text.index("    Write-InstallStep 8 complete") : text.index("} catch {\n    $installError")
    ]
    enabled, disabled = completion.split('    } else {', 1)
    assert "Tailscale Serve må setjast opp separat" in enabled
    assert "Webgrensesnittet er avslått" in disabled
    assert "meshpi web-info" in enabled
    assert "meshpi web-info" in disabled
