"""Isolated execution of the actual activation/rollback blocks; no live services."""

from __future__ import annotations

import os
import shlex
import subprocess
import sys
from pathlib import Path

import pytest
from test_installers import _shell_function

ROOT = Path(__file__).resolve().parents[1]


@pytest.mark.skipif(os.name != "posix", reason="native POSIX permissions")
@pytest.mark.parametrize("mode, expected", [("always", 0o755), ("session", 0o700)])
def test_linux_application_creation_scopes_umask_without_exposing_data(tmp_path, mode, expected):
    source = (ROOT / "installers/install-linux.sh").read_text()
    block = source.split('    rm -rf "$RELEASE"\n', 1)[1].split("\nelse\n", 1)[0]
    # Execute the real build block with a stand-in Python command. It creates
    # synthetic program directories with the inherited effective umask.
    python = tmp_path / "python"
    python.write_text(
        '#!/bin/sh\nmkdir -p "$RELEASE/.venv/bin"\ncp "$PYTHON" "$RELEASE/.venv/bin/python"\n'
    )
    python.chmod(0o755)
    release = tmp_path / "release"
    environment = dict(
        os.environ,
        PYTHON=str(python),
        RELEASE=str(release),
        MODE=mode,
        WHEEL="synthetic",
        LOCK_FILE="synthetic",
    )
    subprocess.run(
        ["sh", "-c", "set -eu; umask 077\n" + block + '\nmkdir "$RELEASE/data"'],
        env=environment,
        check=True,
        capture_output=True,
    )
    assert (release / ".venv").stat().st_mode & 0o777 == expected
    assert (release / "data").stat().st_mode & 0o777 == 0o700


@pytest.mark.skipif(os.name != "posix", reason="native POSIX symlinks")
def test_native_installer_activation_automatically_rolls_back(tmp_path):
    platform = "macos" if sys.platform == "darwin" else "linux"
    source = (ROOT / "installers" / f"install-{platform}.sh").read_text()
    old = tmp_path / "releases" / "old"
    new = tmp_path / "releases" / "new"
    old.mkdir(parents=True)
    new.mkdir()
    state = tmp_path / "data"
    state.mkdir()
    sentinel = state / "synthetic.db"
    sentinel.write_bytes(b"preserve synthetic history")
    current = tmp_path / "current"
    current.symlink_to(new, target_is_directory=True)
    (tmp_path / "previous").symlink_to(old, target_is_directory=True)
    variables = {
        "PREFIX": str(tmp_path),
        "APP_ROOT": str(tmp_path),
        "CURRENT_LINK": str(current),
        "PREVIOUS_LINK": str(tmp_path / "previous"),
        "OLD_RELEASE": str(old),
        "RELEASE": str(new),
        "STATE_DIR": str(state),
        "DATA_DIR": str(state),
        "CONFIG_FILE": str(tmp_path / "config.env"),
        "UNIT_FILE": str(tmp_path / "test.service"),
        "WEB_UNIT_FILE": str(tmp_path / "web.service"),
        "WEB_PLIST_FILE": str(tmp_path / "web.plist"),
        "WEB_LABEL": "org.venes.meshpi.web.isolated",
        "WEB_ENABLED": "true",
        "TMP_DIR": str(tmp_path / "snapshots"),
        "BIN_DIR": str(tmp_path / "bin"),
        "BIN_FILE": str(tmp_path / "bin/meshpi"),
        "ACTIVATION_STARTED": "1",
        "OLD_DAEMON_ACTIVE": "1",
        "OLD_DAEMON_ENABLED": "1",
        "OLD_WEB_ACTIVE": "1",
        "OLD_WEB_ENABLED": "1",
        "PLIST_FILE": str(tmp_path / "test.plist"),
        "LABEL": "org.venes.meshpi.isolated",
        "DOMAIN": "isolated",
        "IPC_SOCKET_GID_VALUE": "",
        "MODE": "always",
        "SKIP_SERVICE": "0",
        "MESHPI_FORCE_HEALTH_FAILURE": "1",
        "PYTHON": sys.executable,
        "SYSTEMCTL": "test_systemctl",
    }
    start = (
        'if [ "$SKIP_SERVICE" = "1" ]; then'
        if platform == "macos"
        else 'if [ "$MODE" = "always" ] && [ "$SKIP_SERVICE" != "1" ]; then\n    cat >"$UNIT_FILE"'
    )
    block = source[source.index(start) :].split('\nif [ "$FRESH_INSTALL"', 1)[0]
    script = (
        "set -eu\n"
        + "\n".join(f"{name}={shlex.quote(value)}" for name, value in variables.items())
        + "\n"
    )
    script += """
message() { printf '%s' 'Isolated test'; }
say_error() { printf '%s\\n' "$*" >&2; }
sleep() { :; }
test_systemctl() { printf '%s\\n' "$*" >> "$PREFIX/service-calls"; }
launchctl() { printf '%s\\n' "$*" >> "$APP_ROOT/service-calls"; }
"""
    (tmp_path / "snapshots").mkdir()
    config = tmp_path / "config.env"
    config.write_text("WEB_ENABLED=true\n")
    (tmp_path / "snapshots/config.saved").write_bytes(config.read_bytes())
    config.write_text("WEB_ENABLED=false\n")
    script += "stop_launch_job() { :; }\n"
    script += "\n".join(
        _shell_function(source, name)
        for name in (
            "switch_link",
            "restore_file",
            "finish_install",
        )
    )
    script += "\ntrap finish_install EXIT\n" + block
    result = subprocess.run(["sh", "-c", script], capture_output=True, text=True)
    assert result.returncode == 1, (result.stdout, result.stderr)
    assert "rollback" in result.stderr
    assert current.resolve() == old.resolve()
    assert not (tmp_path / "previous").exists()
    assert sentinel.read_bytes() == b"preserve synthetic history"
    assert config.read_text() == "WEB_ENABLED=true\n"
    assert "start" in (tmp_path / "service-calls").read_text() or platform == "macos"
