import json
import socket
import threading

import pytest

from meshpi.config import Settings
from meshpi.database import Database
from meshpi.events import EventHub
from meshpi.ipc import IPCApplication, IPCServer
from meshpi.models import Node


class FakeService:
    def status(self):
        return {"state": "tilkopla"}

    def send_public(self, text):
        return {"text": text, "kind": "public"}

    def send_dm(self, node_id, text):
        return {"text": text, "peer_node": node_id}

    def list_connections(self):
        return {"active_profile_id": "tcp-test", "profiles": []}

    def discover_connections(self):
        return {"active_profile_id": "tcp-test", "profiles": [], "serial": [], "tcp": []}

    def connect(self, **kwargs):
        return {"state": "koplar til", **kwargs}


def test_settings_load_env_file(tmp_path, monkeypatch):
    for name in (
        "MESHTASTIC_HOST",
        "MESHTASTIC_PORT",
        "DATABASE_PATH",
        "CONNECTIONS_PATH",
        "DISCOVERY_SUBNET",
        "IPC_HOST",
        "IPC_PORT",
        "IPC_TOKEN",
        "LOG_LEVEL",
        "UPDATE_URL",
        "UPDATE_TIMEOUT",
        "BACKGROUND_MODE",
    ):
        monkeypatch.delenv(name, raising=False)
    path = tmp_path / ".env"
    path.write_text(
        "MESHTASTIC_HOST=192.0.2.42\nMESHTASTIC_PORT=4403\nIPC_HOST=127.0.0.1\n"
        "PYTHONPATH=/tmp/evil\n",
        encoding="utf-8",
    )
    settings = Settings.load(path)
    assert settings.meshtastic_host == "192.0.2.42"
    assert settings.meshtastic_port == 4403
    assert settings.connections_path == settings.database_path.with_name("connections.json")
    assert settings.update_url == "https://venes.org/meshpi/version.json"
    assert settings.update_timeout == 3
    assert settings.background_mode == "always"
    assert "PYTHONPATH" not in settings.__dataclass_fields__


def test_settings_reject_non_loopback_ipc(monkeypatch):
    monkeypatch.setenv("IPC_HOST", "0.0.0.0")
    with pytest.raises(ValueError):
        Settings.load("missing")


def test_settings_have_no_default_meshtastic_node(tmp_path, monkeypatch):
    monkeypatch.delenv("MESHTASTIC_HOST", raising=False)
    monkeypatch.delenv("DISCOVERY_SUBNET", raising=False)

    settings = Settings.load(tmp_path / "missing.env")

    assert settings.meshtastic_host == ""
    assert settings.discovery_subnet == ""


def test_settings_reject_unknown_background_mode(monkeypatch):
    monkeypatch.setenv("BACKGROUND_MODE", "ukjend")
    with pytest.raises(ValueError):
        Settings.load("missing")


def test_ipc_dispatch_and_validation(tmp_path):
    database = Database(tmp_path / "db.sqlite")
    database.initialize()
    app = IPCApplication(Settings(database_path=database.path), database, FakeService(), EventHub())
    status = app.dispatch({"command": "status"})["data"]
    assert status["state"] == "tilkopla"
    assert status["background_mode"] == "always"
    assert isinstance(status["daemon_pid"], int)
    assert app.dispatch({"command": "connections"})["data"]["active_profile_id"] == "tcp-test"
    assert app.dispatch({"command": "discover_connections"})["data"]["serial"] == []
    assert (
        app.dispatch({"command": "connect", "target": "10.0.0.135"})["data"]["target"]
        == "10.0.0.135"
    )
    archived = app.dispatch(
        {"command": "archive_conversation", "node_id": "11112222"}
    )["data"]
    assert archived == {"node_id": "!11112222", "archived": True}
    restored = app.dispatch(
        {"command": "unarchive_conversation", "node_id": "!11112222"}
    )["data"]
    assert restored == {"node_id": "!11112222", "archived": False}
    assert app.dispatch({"command": "send_public", "text": "hei"})["data"]["text"] == "hei"
    assert (
        app.dispatch(
            {"command": "send_dm", "node_id": "!11112222", "text": "privat"}
        )["data"]["peer_node"]
        == "!11112222"
    )
    database.upsert_node(Node(node_id="!11112222", long_name="Test"))
    assert (
        app.dispatch({"command": "node", "node_id": "!11112222"})["data"]["long_name"]
        == "Test"
    )
    with pytest.raises(ValueError):
        app.dispatch({"command": "messages", "conversation": "!kort"})
    with pytest.raises(ValueError):
        app.dispatch({"command": "ukjend"})


def test_ipc_socket_roundtrip(tmp_path):
    database = Database(tmp_path / "db.sqlite")
    database.initialize()
    settings = Settings(database_path=database.path, ipc_port=0, ipc_token="a" * 64)
    stopped = threading.Event()
    app = IPCApplication(
        settings,
        database,
        FakeService(),
        EventHub(),
        shutdown_callback=stopped.set,
    )
    server = IPCServer(settings, app)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        with socket.create_connection(server.address, timeout=2) as connection:
            stream = connection.makefile("rwb")
            stream.write(json.dumps({"command": "status", "token": "a" * 64}).encode() + b"\n")
            stream.flush()
            response = json.loads(stream.readline())
        assert response["ok"] is True
        assert response["data"]["state"] == "tilkopla"
        with socket.create_connection(server.address, timeout=2) as connection:
            stream = connection.makefile("rwb")
            stream.write(json.dumps({"command": "shutdown", "token": "a" * 64}).encode() + b"\n")
            stream.flush()
            response = json.loads(stream.readline())
        assert response == {"ok": True, "data": {"stopping": True}}
        assert stopped.wait(1)
    finally:
        server.shutdown()
        thread.join(timeout=2)


def test_ipc_rejects_missing_token(tmp_path):
    database = Database(tmp_path / "db.sqlite")
    database.initialize()
    settings = Settings(database_path=database.path, ipc_port=0, ipc_token="b" * 64)
    server = IPCServer(settings, IPCApplication(settings, database, FakeService(), EventHub()))
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        with socket.create_connection(server.address, timeout=2) as connection:
            stream = connection.makefile("rwb")
            stream.write(b'{"command":"status"}\n')
            stream.flush()
            response = json.loads(stream.readline())
        assert response == {"ok": False, "error": "IPC-autentisering feila"}
    finally:
        server.shutdown()
        thread.join(timeout=2)
