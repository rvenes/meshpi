from __future__ import annotations

import hashlib
import json
import re
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]


def _text(name: str) -> str:
    return (ROOT / "installers" / name).read_text(encoding="utf-8")


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _shell_function(source: str, name: str) -> str:
    match = re.search(rf"(?ms)^{re.escape(name)}\(\) \{{\n.*?^\}}\n", source)
    assert match is not None
    return match.group(0)


def test_manifest_matches_all_dependency_locks() -> None:
    manifest = json.loads((ROOT / "website" / "version.json").read_text(encoding="utf-8"))

    assert manifest["security"] == {
        "integrity": "sha256",
        "dependency_policy": "pip-require-hashes",
        "manifest_signature": "rsa-pkcs1v15-sha256",
    }
    for platform_name in ("linux", "macos", "windows"):
        lock = ROOT / "locks" / f"{platform_name}.txt"
        assert manifest["locks"][platform_name]["sha256"] == _sha256(lock)
        assert manifest["locks"][platform_name]["size"] == lock.stat().st_size
        assert "--hash=sha256:" in lock.read_text(encoding="utf-8")


def test_installers_use_locked_dependencies_and_offline_selftest() -> None:
    for name in ("install-linux.sh", "install-macos.sh"):
        source = _text(name)
        assert "--require-hashes" in source
        assert "--no-deps" in source
        assert "doctor --offline" in source
        assert "MESHPI_FORCE_HEALTH_FAILURE" in source
        assert "pip install -q --upgrade pip" not in source

    windows = _text("install-windows.ps1")
    assert '"--require-hashes"' in windows
    assert '"--no-deps"' in windows
    assert '"doctor", "--offline"' in windows
    assert "MESHPI_FORCE_HEALTH_FAILURE" in windows


def test_installers_have_a_rotatable_signing_key_registry() -> None:
    for name in ("install-linux.sh", "install-macos.sh", "install-windows.ps1"):
        source = _text(name)
        assert 'trusted_keys = {"meshpi-release-2026-01":' in source
        assert "revoked_key_ids = set()" in source
        assert "key = trusted_keys.get(key_id)" in source


def test_service_installers_use_approved_absolute_control_programs() -> None:
    linux = _text("install-linux.sh")
    windows = _text("install-windows.ps1")

    assert "/usr/bin/systemctl /bin/systemctl" in linux
    assert '"$SYSTEMCTL" daemon-reload' in linux
    assert '"$SYSTEMCTL" start meshpi.service' in linux
    assert "Get-Command powershell.exe" not in windows
    assert "[Environment]::SystemDirectory" in windows


def test_macos_switches_current_symlink_without_following_it() -> None:
    source = _text("install-macos.sh")
    assert "os.replace(sys.argv[1], sys.argv[2])" in source
    assert 'mv -f "$temporary" "$CURRENT_LINK"' not in source
    assert 'while launchctl print "$DOMAIN/$LABEL"' in source


def test_uninstallers_preserve_data_without_explicit_purge() -> None:
    linux = _text("uninstall-linux.sh")
    macos = _text("uninstall-macos.sh")
    windows = _text("uninstall-windows.ps1")

    assert "--purge-data" in linux and '[ "$PURGE" = "1" ]' in linux
    assert "--mode=session" in linux
    assert "--purge-data" in macos and '[ "$PURGE" = "1" ]' in macos
    assert "PurgeData" in windows and "if ($PurgeData)" in windows


def test_linux_always_mode_has_restricted_permissions() -> None:
    source = _text("install-linux.sh")
    assert 'chown "$CLIENT_USER:meshpi" "$CONFIG_FILE"' in source
    assert 'EXISTING_OWNER="$(stat -c %U "$CONFIG_FILE"' in source
    assert 'CLIENT_USER="$EXISTING_OWNER"' in source
    assert 'chmod 0640 "$CONFIG_FILE"' in source
    assert 'chown -R meshpi:meshpi "$STATE_DIR"' in source
    assert 'chmod 0750 "$STATE_DIR"' in source
    assert "EnvironmentFile=" not in source
    assert "UMask=0077" in source
    assert "RuntimeDirectory=meshpi" in source
    assert "RuntimeDirectoryMode=0711" in source
    assert "SupplementaryGroups=dialout $IPC_SOCKET_GID_VALUE" in source
    assert 'if [ "$ipc_gid_users" -gt 1 ]' in source
    assert "MeshPi krev ei privat primærgruppe" in source


@pytest.mark.skipif(shutil.which("sh") is None, reason="krev POSIX-shell")
@pytest.mark.parametrize(
    ("gid", "passwd_entries", "expected_status", "expected_output"),
    [
        (
            "0",
            "\n".join(
                (
                    "root:x:0:0:root:/root:/bin/sh",
                    "sync:x:4:0:sync:/bin:/bin/sync",
                    "shutdown:x:6:0:shutdown:/sbin:/sbin/shutdown",
                    "halt:x:7:0:halt:/sbin:/sbin/halt",
                )
            ),
            0,
            "",
        ),
        ("1000", "rune:x:1000:1000:Rune:/home/rune:/bin/sh", 0, "1000"),
        (
            "100",
            "\n".join(
                (
                    "alice:x:1000:100:Alice:/home/alice:/bin/sh",
                    "bob:x:1001:100:Bob:/home/bob:/bin/sh",
                )
            ),
            1,
            "",
        ),
    ],
)
def test_linux_ipc_gid_handles_root_and_shared_groups(
    gid: str,
    passwd_entries: str,
    expected_status: int,
    expected_output: str,
) -> None:
    function = _shell_function(_text("install-linux.sh"), "resolve_ipc_socket_gid")
    script = f"""
id() {{
    printf '%s\\n' "$TEST_GID"
}}
getent() {{
    printf '%s\\n' "$TEST_PASSWD"
}}
{function}
resolve_ipc_socket_gid test-user
"""
    result = subprocess.run(
        ["sh", "-c", script],
        check=False,
        capture_output=True,
        text=True,
        env={"PATH": "/usr/bin:/bin", "TEST_GID": gid, "TEST_PASSWD": passwd_entries},
    )

    assert result.returncode == expected_status
    assert result.stdout.strip() == expected_output
    if expected_status:
        assert "privat primærgruppe" in result.stderr


def test_installers_select_safe_platform_ipc_transport() -> None:
    linux = _text("install-linux.sh")
    macos = _text("install-macos.sh")
    windows = _text("install-windows.ps1")

    assert "IPC_TRANSPORT_VALUE=unix" in linux
    assert "IPC_SOCKET_PATH_VALUE=/run/meshpi/meshpi.sock" in linux
    assert 'IPC_SOCKET_GID_VALUE="$(resolve_ipc_socket_gid "$CLIENT_USER")"' in linux
    assert "IPC_TRANSPORT=unix" in macos
    assert "IPC_SOCKET_PATH=$DATA_DIR/meshpi.sock" in macos
    assert "IPC_TRANSPORT=tcp" in windows
    assert 'Set-EnvValue $configFile "IPC_TRANSPORT" "tcp"' in windows


def test_linux_checks_venv_before_downloading_release_files() -> None:
    source = _text("install-linux.sh")

    assert '"$PYTHON" -m venv "$VENV_CHECK"' in source
    assert '"$VENV_CHECK/bin/python" -m pip --version' in source
    assert "sudo apt install python$PYTHON_SERIES-venv" in source
    assert source.index('VENV_CHECK="$TMP_DIR/venv-check"') < source.index(
        'install_step 2 "Hentar'
    )


def test_updater_can_supply_python_and_local_files_without_curl() -> None:
    for name in ("install-linux.sh", "install-macos.sh"):
        source = _text(name)
        assert '[ -n "${MESHPI_PYTHON:-}" ]' in source
        assert '[ -z "${MESHPI_MANIFEST_FILE:-}" ] ||' in source
        assert '[ -z "${MESHPI_PACKAGE_FILE:-}" ] ||' in source
        assert '[ -z "${MESHPI_LOCK_FILE:-}" ]' in source

    windows = _text("install-windows.ps1")
    assert "$env:MESHPI_PYTHON" in windows
    assert "Test-PythonCommand $env:MESHPI_PYTHON" in windows


def test_macos_installer_rejects_root_outside_test_mode() -> None:
    source = _text("install-macos.sh")

    assert 'TEST_MODE="${MESHPI_TEST_MODE:-0}"' in source
    assert '[ "$(id -u)" -eq 0 ] && [ "$TEST_MODE" != "1" ]' in source
    assert "macOS-installasjonen skal køyrast utan sudo" in source


def test_installers_do_not_ship_a_preselected_meshtastic_node() -> None:
    for name in ("install-linux.sh", "install-macos.sh", "install-windows.ps1"):
        source = _text(name)
        assert "MESHTASTIC_HOST=" in source
        development_address = ".".join(("10", "0", "0", "152"))
        assert development_address not in source


def test_windows_installer_reports_progress_and_ignores_missing_legacy_task() -> None:
    windows = _text("install-windows.ps1")

    assert (ROOT / "installers" / "install-windows.ps1").read_bytes().startswith(
        b"\xef\xbb\xbf"
    )
    for step in range(1, 9):
        assert f"Write-InstallStep {step} " in windows
    assert "Dette kan ta nokre minutt" in windows
    assert '$savedErrorActionPreference = $ErrorActionPreference' in windows
    assert '$ErrorActionPreference = "SilentlyContinue"' in windows
    assert '$ErrorActionPreference = $savedErrorActionPreference' in windows
    assert '& schtasks.exe /Delete /TN $taskName /F *> $null' in windows


def test_posix_installers_report_the_same_progress_as_windows() -> None:
    for name in ("install-linux.sh", "install-macos.sh"):
        source = _text(name)
        for step in range(1, 9):
            assert f"install_step {step} " in source
        assert "Dette kan ta nokre minutt" in source
        assert "doctor --offline >/dev/null" not in source
