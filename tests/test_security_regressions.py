from __future__ import annotations

import hashlib
import json
import logging
import os
import shutil
import socket
import sqlite3
import subprocess
import sys
from pathlib import Path
from unittest.mock import Mock

import pytest

from meshpi.bootstrap import fetch, pip_requirement
from meshpi.bootstrap import main as bootstrap_main
from meshpi.client import CLIError, request
from meshpi.config import Settings
from meshpi.database import Database
from meshpi.ipc_identity import verify_windows_peer
from meshpi.private_logging import PrivateRotatingFileHandler

ROOT = Path(__file__).resolve().parents[1]


def test_future_schema_is_rejected_without_modification(tmp_path):
    path = tmp_path / "future.db"
    database = Database(path)
    database.initialize()
    with sqlite3.connect(path) as connection:
        connection.execute("PRAGMA user_version=999")
    before = path.read_bytes()
    with pytest.raises(ValueError, match="nyare|newer"):
        database.initialize()
    assert path.read_bytes() == before


@pytest.mark.skipif(os.name != "posix", reason="POSIX permissions")
@pytest.mark.parametrize("mask", [0o022, 0o077])
def test_every_rotated_log_remains_private(tmp_path, mask):
    old_mask = os.umask(mask)
    handler = PrivateRotatingFileHandler(tmp_path / "private.log", maxBytes=100, backupCount=3)
    try:
        for _index in range(30):
            handler.emit(logging.LogRecord("test", logging.INFO, "", 0, "x" * 50,
                                           (), None))
        handler.close()
        files = list(tmp_path.iterdir())
        assert len(files) == 4
        assert all(path.stat().st_mode & 0o777 == 0o600 for path in files)
    finally:
        handler.close()
        os.umask(old_mask)


def test_peer_from_different_windows_account_is_rejected(monkeypatch):
    monkeypatch.setattr("meshpi.ipc_identity._connection_owner", lambda _sock: -123)
    monkeypatch.setattr("meshpi.ipc_identity._process_sid",
                        lambda pid: b"attacker" if pid == -123 else b"operator")
    with pytest.raises(OSError, match="another Windows account"):
        verify_windows_peer(Mock())


@pytest.mark.skipif(os.name != "nt", reason="Windows credential transmission")
def test_unverifiable_peer_receives_no_credentials(monkeypatch):
    def reject(_sock):
        raise OSError("Denied")
    monkeypatch.setattr("meshpi.client.verify_windows_peer", reject)
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        listener.listen(1)
        listener.settimeout(2)
        settings = Settings(ipc_transport="tcp", ipc_port=listener.getsockname()[1],
                            ipc_token="synthetic-token-not-a-secret" * 2)
        with pytest.raises(CLIError, match="IPC"):
            request(settings, {"command": "status"})
        connection, _ = listener.accept()
        with connection:
            connection.settimeout(2)
            assert connection.recv(100) == b""


def test_session_uninstall_preserves_nested_data(tmp_path):
    shell = shutil.which("sh") or "C:/Program Files/Git/bin/sh.exe"
    if not Path(shell).is_file():
        pytest.skip("POSIX shell unavailable")
    prefix = tmp_path / "installation with spaces"
    data = prefix / "data"
    data.mkdir(parents=True)
    (data / "messages.db").write_bytes(b"synthetic database")
    (data / "connections.json").write_text("{}")
    (prefix / "releases" / "0.0.1").mkdir(parents=True)
    config = tmp_path / "config.env"
    config.write_text("synthetic configuration")
    environment = {key: value for key, value in os.environ.items()
                   if not key.startswith("MESHPI_")}
    environment.update({"MESHPI_MODE": "session", "MESHPI_TEST_MODE": "1",
                        "MESHPI_PREFIX": prefix.as_posix(),
                        "MESHPI_CONFIG_FILE": config.as_posix(),
                        "MESHPI_BIN_FILE": (tmp_path / "launcher").as_posix(),
                        "MESHPI_SKIP_SERVICE": "1", "SUDO_USER": "root"})
    python = Path(sys.executable).as_posix().replace("'", "'\"'\"'")
    script = (ROOT / "installers/uninstall-linux.sh").as_posix()
    command = f"python3() {{ '{python}' \"$@\"; }}; . '{script}'"
    subprocess.run([shell, "-c", command],
                   env=environment, check=True, capture_output=True)
    assert (data / "messages.db").read_bytes() == b"synthetic database"
    assert (data / "connections.json").read_text() == "{}"
    assert config.read_text() == "synthetic configuration"
    assert not (prefix / "releases").exists()


def test_bootstrap_requires_safe_pinned_hashes():
    digest = "a" * 64
    assert pip_requirement(f"pip==26.2.1 \\\n+    --hash=sha256:{digest}\n") == ("26.2.1", {digest})
    for lock in ("pip==26.1.2 --hash=sha256:" + digest, "pip==26.2.1", "pip>=26.2"):
        with pytest.raises(ValueError):
            pip_requirement(lock)


def test_bootstrap_rejects_non_pypi_and_insecure_urls():
    for url in ("http://pypi.org/pip", "https://example.invalid/pip", "file:///tmp/pip"):
        with pytest.raises(ValueError):
            fetch(url, 100)


@pytest.mark.parametrize("valid_hash", [True, False])
def test_bootstrap_checks_hash_before_executing_pip(tmp_path, monkeypatch, valid_hash):
    payload = b"synthetic verified wheel"
    digest = hashlib.sha256(payload).hexdigest() if valid_hash else "0" * 64
    lock = tmp_path / "lock.txt"
    lock.write_text(f"pip==26.2.1 --hash=sha256:{digest}\n")
    metadata = json.dumps({"urls": [{"filename": "pip-26.2.1-py3-none-any.whl",
                                    "url": "https://files.pythonhosted.org/test.whl"}]}).encode()
    monkeypatch.setattr("meshpi.bootstrap.fetch", lambda url, _max:
                        metadata if url.endswith("/json") else payload)
    monkeypatch.setattr(sys, "argv", ["bootstrap", str(lock)])
    monkeypatch.setattr(sys, "path", list(sys.path))
    monkeypatch.setenv("PIP_CONFIG_FILE", "synthetic")
    calls = []
    def execute(module, **options):
        assert module == "pip"
        assert options == {"run_name": "__main__", "alter_sys": True}
        assert Path(sys.path[0]).read_bytes() == payload
        assert "--require-hashes" in sys.argv and "--only-binary=:all:" in sys.argv
        calls.append(module)
    monkeypatch.setattr("meshpi.bootstrap.runpy.run_module", execute)
    if valid_hash:
        bootstrap_main()
        assert calls == ["pip"]
    else:
        with pytest.raises(ValueError, match="signed lock"):
            bootstrap_main()
        assert not calls
