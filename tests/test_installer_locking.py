"""Installer transactions exclude competing processes without touching live installs."""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest
from test_installers import _powershell_function, _shell_function

ROOT = Path(__file__).resolve().parents[1]


def _source(platform: str) -> str:
    return (ROOT / "installers" / f"install-{platform}.sh").read_text(encoding="utf-8")


def _shell_lock(source: str) -> str:
    return "set -eu\n" + "\n".join(
        _shell_function(source, name)
        for name in ("message", "say", "say_error", "acquire_install_lock")
    )


def _environment(directory: Path) -> dict[str, str]:
    return {
        **os.environ,
        "PYTHON": sys.executable,
        "LANGUAGE": "nn",
        "PREFIX": str(directory),
        "APP_ROOT": str(directory),
        "RELEASES_DIR": str(directory / "releases"),
        "CURRENT_LINK": str(directory / "current"),
        "PREVIOUS_LINK": str(directory / "previous"),
        "VERSION": "new",
    }


@pytest.mark.skipif(os.name != "posix", reason="POSIX file descriptor locking")
@pytest.mark.parametrize("platform", ["linux", "macos"])
def test_competing_install_cannot_delete_active_release_and_rerun_is_idempotent(
    tmp_path: Path, platform: str,
) -> None:
    source = _source(platform)
    directory = tmp_path / "installation with spaces"
    old = directory / "releases" / "old"
    old.mkdir(parents=True)
    (directory / "current").symlink_to(old)
    (directory / "meshpi.env").write_text("IPC_TOKEN=kept\n")
    # Use the real release selection/removal and atomic activation. Only venv
    # construction is replaced, so a competing installer would erase the marker.
    build = 'RELEASE="$RELEASES_DIR/$VERSION"' + source.split(
        'RELEASE="$RELEASES_DIR/$VERSION"', 1,
    )[1].split('    "$PYTHON" -m venv', 1)[0]
    if platform == "linux":
        build = build.split("    (\n", 1)[0]
    script = _shell_lock(source) + _shell_function(source, "switch_link") + """
install_step() { :; }
acquire_install_lock "$PREFIX"
printf 'LOCKED\n'
IFS= read -r proceed
""" + build + """
    mkdir -p "$RELEASE"
    printf preserved >"$RELEASE/application"
fi
switch_link "$RELEASE"
printf 'ACTIVATED\n'
IFS= read -r proceed
"""
    environment = _environment(directory)
    with subprocess.Popen(
        ["sh", "-c", script], env=environment, text=True,
        stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
    ) as first:
        assert first.stdout is not None and first.stdin is not None
        assert first.stdout.readline().strip() == "LOCKED"
        first.stdin.write("continue\n")
        first.stdin.flush()
        assert first.stdout.readline().strip() == "ACTIVATED"
        marker = directory / "current" / "application"
        marker.write_text("active application")
        competitor = subprocess.run(
            ["sh", "-c", script], env=environment, input="continue\ncontinue\n",
            capture_output=True, text=True, timeout=10,
        )
        assert competitor.returncode == 1
        assert "Ei anna MeshPi-installering køyrer" in competitor.stderr
        assert "Traceback" not in competitor.stderr
        assert "LOCKED" not in competitor.stdout
        assert marker.read_text() == "active application"
        assert (directory / "meshpi.env").read_text() == "IPC_TOKEN=kept\n"
        first.stdin.write("finish\n")
        first.stdin.flush()
        first.communicate(timeout=10)
        assert first.returncode == 0
    lock_inode = (directory / ".install.lock").stat().st_ino
    rerun = subprocess.run(
        ["sh", "-c", script], env=environment, input="continue\ncontinue\n",
        capture_output=True, text=True, timeout=10,
    )
    assert rerun.returncode == 0, rerun.stderr
    assert marker.read_text() == "active application"
    assert (directory / ".install.lock").stat().st_ino == lock_inode


@pytest.mark.skipif(os.name != "posix", reason="POSIX file descriptor locking")
@pytest.mark.parametrize("platform", ["linux", "macos"])
def test_install_lock_covers_rollback_and_releases_after_failure(tmp_path: Path, platform: str):
    source = _source(platform)
    work = tmp_path / "temporary"
    work.mkdir()
    config = tmp_path / "meshpi.env"
    config.write_text("original configuration\n")
    (work / "config.saved").write_bytes(config.read_bytes())
    environment = {
        **_environment(tmp_path), "TMP_DIR": str(work), "CONFIG_FILE": str(config),
        "ACTIVATION_STARTED": "0", "SYSTEMCTL": "", "BIN_FILE": str(tmp_path / "bin"),
        "BIN_DIR": str(tmp_path / "bin-dir"), "PLIST_FILE": str(tmp_path / "daemon.plist"),
        "WEB_PLIST_FILE": str(tmp_path / "web.plist"),
    }
    restore = _shell_function(source, "restore_file").replace(
        "restore_file()", "original_restore_file()", 1,
    )
    script = _shell_lock(source) + restore + _shell_function(source, "finish_install") + """
restore_file() {
    if [ "$1" = config ]; then
        printf 'ROLLBACK\n'
        IFS= read -r proceed
    fi
    original_restore_file "$@"
}
acquire_install_lock "$PREFIX"
trap finish_install EXIT
printf changed >"$CONFIG_FILE"
exit 1
"""
    competitor_script = _shell_lock(source) + '\nacquire_install_lock "$PREFIX"\n'
    with subprocess.Popen(
        ["sh", "-c", script], env=environment, text=True,
        stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
    ) as process:
        assert process.stdout is not None and process.stdin is not None
        assert process.stdout.readline().strip() == "ROLLBACK"
        competitor = subprocess.run(
            ["sh", "-c", competitor_script], env=environment,
            capture_output=True, text=True, timeout=10,
        )
        assert competitor.returncode == 1
        process.stdin.write("restore\n")
        process.stdin.flush()
        process.communicate(timeout=10)
        assert process.returncode == 1
    assert config.read_text() == "original configuration\n"
    retry = subprocess.run(
        ["sh", "-c", competitor_script], env=environment,
        capture_output=True, text=True, timeout=10,
    )
    assert retry.returncode == 0, retry.stderr


@pytest.mark.skipif(os.name != "posix", reason="POSIX file descriptor locking")
@pytest.mark.parametrize("platform", ["linux", "macos"])
def test_killed_installer_does_not_leave_a_stale_lock(tmp_path: Path, platform: str):
    script = _shell_lock(_source(platform)) + """
acquire_install_lock "$PREFIX"
printf 'LOCKED\n'
IFS= read -r proceed
"""
    environment = _environment(tmp_path)
    with subprocess.Popen(
        ["sh", "-c", script], env=environment, text=True,
        stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
    ) as process:
        assert process.stdout is not None
        assert process.stdout.readline().strip() == "LOCKED"
        process.kill()
        process.communicate(timeout=10)
    retry = subprocess.run(
        ["sh", "-c", script], env=environment, input="continue\n",
        capture_output=True, text=True, timeout=10,
    )
    assert retry.returncode == 0, retry.stderr


def test_installers_lock_before_config_snapshot_and_keep_lock_through_rollback():
    for platform, root in (("linux", "PREFIX"), ("macos", "APP_ROOT")):
        source = _source(platform)
        acquired = source.index(f'acquire_install_lock "${root}"\n')
        assert acquired < source.index("choose_web\n")
        assert acquired < source.index('snapshot_file config "$CONFIG_FILE"')
        assert acquired < source.index('RELEASE="$RELEASES_DIR/$VERSION"')
        assert 'rm -f "$1/.install.lock"' not in source
    windows = (ROOT / "installers" / "install-windows.ps1").read_text(encoding="utf-8-sig")
    assert windows.index("$installLock = Open-InstallLock") < windows.index('$webEnabled = "false"')
    assert windows.index("$installLock = Open-InstallLock") < windows.index("$backupFiles = @{}")
    assert windows.index("$installLock.Dispose()") > windows.index(
        "Write-Warning (Get-Message rollback)",
    )


@pytest.mark.skipif(os.name != "nt", reason="native Windows FileShare.None semantics")
def test_windows_install_lock_excludes_other_process_and_releases_after_exit(tmp_path: Path):
    source = (ROOT / "installers" / "install-windows.ps1").read_text(encoding="utf-8-sig")
    function = _powershell_function(source, "Open-InstallLock")
    root = str(tmp_path).replace("'", "''")
    prefix = "function Get-Message { param($Key) return $Key }\n" + function
    acquire = prefix + f"\n$installLock = Open-InstallLock '{root}'\n"
    holder = acquire + "Write-Output 'LOCKED'; [Console]::ReadLine() | Out-Null\n"
    powershell = shutil.which("powershell.exe")
    assert powershell is not None
    with subprocess.Popen(
        [powershell, "-NoProfile", "-NonInteractive", "-Command", holder],
        stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
    ) as process:
        assert process.stdout is not None
        assert process.stdout.readline().strip() == "LOCKED"
        competitor = subprocess.run(
            [powershell, "-NoProfile", "-NonInteractive", "-Command", acquire],
            capture_output=True, text=True, timeout=10,
        )
        assert competitor.returncode != 0
        assert "install_busy" in competitor.stderr
        process.kill()
        process.communicate(timeout=10)
    retry = subprocess.run(
        [powershell, "-NoProfile", "-NonInteractive", "-Command", acquire],
        capture_output=True, text=True, timeout=10,
    )
    assert retry.returncode == 0, retry.stderr
