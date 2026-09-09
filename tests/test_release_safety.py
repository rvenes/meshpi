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
        "PREFIX": str(tmp_path), "APP_ROOT": str(tmp_path),
        "CURRENT_LINK": str(current), "PREVIOUS_LINK": str(tmp_path / "previous"),
        "OLD_RELEASE": str(old), "RELEASE": str(new), "STATE_DIR": str(state),
        "DATA_DIR": str(state), "CONFIG_FILE": str(tmp_path / "config.env"),
        "UNIT_FILE": str(tmp_path / "test.service"),
        "PLIST_FILE": str(tmp_path / "test.plist"), "LABEL": "org.venes.meshpi.isolated",
        "DOMAIN": "isolated", "IPC_SOCKET_GID_VALUE": "", "MODE": "always",
        "SKIP_SERVICE": "0", "MESHPI_FORCE_HEALTH_FAILURE": "1",
        "PYTHON": sys.executable, "SYSTEMCTL": "test_systemctl",
    }
    start = ('if [ "$SKIP_SERVICE" = "1" ]; then' if platform == "macos"
             else 'if [ "$MODE" = "always" ] && [ "$SKIP_SERVICE" != "1" ]; then\n'
                  '    cat >"$UNIT_FILE"')
    block = source[source.index(start):].split('\nif [ "$FRESH_INSTALL"', 1)[0]
    script = "set -eu\n" + "\n".join(
        f"{name}={shlex.quote(value)}" for name, value in variables.items()
    ) + "\n"
    script += """
message() { printf '%s' 'Isolated test'; }
say_error() { printf '%s\\n' "$*" >&2; }
sleep() { :; }
test_systemctl() { printf '%s\\n' "$*" >> "$PREFIX/service-calls"; }
launchctl() { printf '%s\\n' "$*" >> "$APP_ROOT/service-calls"; }
"""
    script += _shell_function(source, "switch_link") + block
    result = subprocess.run(["sh", "-c", script], capture_output=True, text=True)
    assert result.returncode == 1, (result.stdout, result.stderr)
    assert "rollback" in result.stderr
    assert current.resolve() == old.resolve()
    assert not (tmp_path / "previous").exists()
    assert sentinel.read_bytes() == b"preserve synthetic history"
    assert "start" in (tmp_path / "service-calls").read_text() or platform == "macos"
