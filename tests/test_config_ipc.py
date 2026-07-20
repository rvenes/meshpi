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


def test_settings_load_env_file(tmp_path, monkeypatch):
    for name in (
        "MESHTASTIC_HOST",
        "MESHTASTIC_PORT",
        "DATABASE_PATH",
        "IPC_HOST",
        "IPC_PORT",
        "LOG_LEVEL",
    ):
        monkeypatch.delenv(name, raising=False)
    path = tmp_path / ".env"
    path.write_text(
        "MESHTASTIC_HOST=10.0.0.152\nMESHTASTIC_PORT=4403\nIPC_HOST=127.0.0.1\n",
        encoding="utf-8",
    )
    settings = Settings.load(path)
    assert settings.meshtastic_host == "10.0.0.152"
    assert settings.meshtastic_port == 4403


def test_settings_reject_non_loopback_ipc(monkeypatch):
    monkeypatch.setenv("IPC_HOST", "0.0.0.0")
    with pytest.raises(ValueError):
        Settings.load("missing")


def test_ipc_dispatch_and_validation(tmp_path):
    database = Database(tmp_path / "db.sqlite")
    database.initialize()
    app = IPCApplication(Settings(database_path=database.path), database, FakeService(), EventHub())
    assert app.dispatch({"command": "status"})["data"]["state"] == "tilkopla"
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
    settings = Settings(database_path=database.path, ipc_port=0)
    app = IPCApplication(settings, database, FakeService(), EventHub())
    server = IPCServer(settings, app)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        with socket.create_connection(server.address, timeout=2) as connection:
            stream = connection.makefile("rwb")
            stream.write(b'{"command":"status"}\n')
            stream.flush()
            response = json.loads(stream.readline())
        assert response == {"ok": True, "data": {"state": "tilkopla"}}
    finally:
        server.shutdown()
        thread.join(timeout=2)
