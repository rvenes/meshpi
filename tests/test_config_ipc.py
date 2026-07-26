import json
import os
import socket
import tempfile
import threading
import time
from pathlib import Path

import pytest

from meshpi.client import CLIError, CLIUnavailableError, open_watch, request
from meshpi.config import Settings
from meshpi.database import Database
from meshpi.events import EventHub
from meshpi.ipc import IPCApplication, IPCServer
from meshpi.lifecycle import daemon_status
from meshpi.models import Node


class FakeService:
    def status(self):
        return {"state": "tilkopla"}

    def send_public(self, text):
        return {"text": text, "kind": "public"}

    def send_dm(self, node_id, text):
        return {"text": text, "peer_node": node_id}

    def start_node_action(self, action, node_id):
        return {
            "action_id": "trace-1",
            "action": action,
            "node_id": node_id,
            "status": "started",
        }

    def node_action_status(self, action_id):
        return {"action_id": action_id, "status": "completed"}

    def node_action_availability(self, action, node_id):
        return {
            "action": action,
            "node_id": node_id,
            "available": True,
            "cooldown_seconds": 0,
            "reason": None,
        }

    def list_connections(self):
        return {"active_profile_id": "tcp-test", "profiles": []}

    def discover_connections(self):
        return {"active_profile_id": "tcp-test", "profiles": [], "serial": [], "tcp": []}

    def connect(self, **kwargs):
        return {"state": "koplar til", **kwargs}


def test_client_connection_error_uses_cross_platform_service_hint(monkeypatch):
    def fail_connection(*_args, **_kwargs):
        raise OSError("ikkje tilgjengeleg")

    monkeypatch.setattr("meshpi.client.socket.create_connection", fail_connection)

    with pytest.raises(CLIUnavailableError, match="meshpi service status"):
        request(Settings(ipc_transport="tcp"), {"command": "status"})


def test_client_does_not_report_reset_connection_as_stopped_service(monkeypatch):
    class ResetStream:
        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        def write(self, _data):
            pass

        def flush(self):
            pass

        def readline(self, _limit):
            raise ConnectionResetError("IPC-grensa er nådd")

    class ConnectedSocket:
        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        def makefile(self, _mode):
            return ResetStream()

    monkeypatch.setattr(
        "meshpi.client.socket.create_connection",
        lambda *_args, **_kwargs: ConnectedSocket(),
    )

    with pytest.raises(CLIError, match="ikkje starta på nytt") as error:
        request(Settings(ipc_transport="tcp"), {"command": "status"})
    assert not isinstance(error.value, CLIUnavailableError)


def test_daemon_status_only_suppresses_unavailable_service(monkeypatch):
    def unavailable(*_args, **_kwargs):
        raise CLIUnavailableError("stoppa")

    monkeypatch.setattr("meshpi.lifecycle.request", unavailable)
    assert daemon_status(Settings()) is None

    def broken(*_args, **_kwargs):
        raise CLIError("IPC-sambandet blei brote")

    monkeypatch.setattr("meshpi.lifecycle.request", broken)
    with pytest.raises(CLIError, match="blei brote"):
        daemon_status(Settings())


def test_settings_load_env_file(tmp_path, monkeypatch):
    for name in (
        "MESHTASTIC_HOST",
        "MESHTASTIC_PORT",
        "DATABASE_PATH",
        "CONNECTIONS_PATH",
        "DISCOVERY_SUBNET",
        "IPC_HOST",
        "IPC_PORT",
        "IPC_SOCKET_GID",
        "IPC_SOCKET_PATH",
        "IPC_TOKEN",
        "IPC_TRANSPORT",
        "LOG_LEVEL",
        "OBSERVATION_RETENTION_DAYS",
        "UPDATE_URL",
        "UPDATE_TIMEOUT",
        "BACKGROUND_MODE",
    ):
        monkeypatch.delenv(name, raising=False)
    path = tmp_path / ".env"
    path.write_text(
        "MESHTASTIC_HOST=192.0.2.42\nMESHTASTIC_PORT=4403\nIPC_HOST=127.0.0.1\n"
        "OBSERVATION_RETENTION_DAYS=730\nPYTHONPATH=/tmp/evil\n",
        encoding="utf-8",
    )
    settings = Settings.load(path)
    assert settings.meshtastic_host == "192.0.2.42"
    assert settings.meshtastic_port == 4403
    assert settings.connections_path == settings.database_path.with_name("connections.json")
    assert settings.update_url == "https://venes.org/meshpi/version.json"
    assert settings.update_timeout == 3
    assert settings.background_mode == "always"
    assert settings.observation_retention_days == 730
    assert settings.ipc_transport == "auto"
    assert settings.ipc_socket_path == settings.database_path.with_name("meshpi.sock")
    assert "PYTHONPATH" not in settings.__dataclass_fields__


def test_settings_reject_non_loopback_ipc(monkeypatch):
    monkeypatch.setenv("IPC_HOST", "0.0.0.0")
    with pytest.raises(ValueError):
        Settings.load("missing")


def test_settings_load_explicit_ipc_transport(tmp_path, monkeypatch):
    socket_path = tmp_path / "run" / "meshpi.sock"
    monkeypatch.setenv("IPC_TRANSPORT", "tcp")
    monkeypatch.setenv("IPC_SOCKET_PATH", str(socket_path))
    monkeypatch.setenv("IPC_SOCKET_GID", "1234")

    settings = Settings.load(tmp_path / "missing.env")

    assert settings.ipc_transport == "tcp"
    assert settings.ipc_socket_path == socket_path
    assert settings.ipc_socket_gid == 1234
    assert settings.ipc_uses_unix is False


def test_empty_ipc_socket_path_uses_database_sibling(tmp_path, monkeypatch):
    database_path = tmp_path / "state" / "meshpi.db"
    monkeypatch.setenv("DATABASE_PATH", str(database_path))
    monkeypatch.setenv("IPC_SOCKET_PATH", "")

    settings = Settings.load(tmp_path / "missing.env")

    assert settings.ipc_socket_path == database_path.with_name("meshpi.sock")


def test_settings_reject_unknown_ipc_transport(monkeypatch):
    monkeypatch.setenv("IPC_TRANSPORT", "radio")
    with pytest.raises(ValueError, match="IPC_TRANSPORT"):
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
    assert app.dispatch({"command": "delete_messages", "scope": "public"})["data"] == {
        "scope": "public",
        "deleted": 0,
    }
    assert app.dispatch({"command": "send_public", "text": "hei"})["data"]["text"] == "hei"
    assert (
        app.dispatch(
            {"command": "send_dm", "node_id": "!11112222", "text": "privat"}
        )["data"]["peer_node"]
        == "!11112222"
    )
    assert app.dispatch(
        {
            "command": "node_action",
            "action": "traceroute",
            "node_id": "!11112222",
        }
    )["data"] == {
        "action_id": "trace-1",
        "action": "traceroute",
        "node_id": "!11112222",
        "status": "started",
    }
    assert app.dispatch(
        {"command": "node_action_status", "action_id": "trace-1"}
    )["data"]["status"] == "completed"
    database.upsert_node_action(
        {
            "action_id": "trace-saved",
            "action": "traceroute",
            "node_id": "!11112222",
            "status": "completed",
            "started_at": "2026-07-21T12:00:00+00:00",
            "result": {"forward": []},
        }
    )
    assert app.dispatch(
        {
            "command": "node_actions",
            "action": "traceroute",
            "node_id": "!11112222",
        }
    )["data"][0]["action_id"] == "trace-saved"
    assert app.dispatch(
        {
            "command": "node_action_availability",
            "action": "traceroute",
            "node_id": "!11112222",
        }
    )["data"]["available"] is True
    database.upsert_node(Node(node_id="!11112222", long_name="Test"))
    common_observation = {
        "node_id": "!11112222",
        "sample_time": "2026-07-26T12:00:00+00:00",
        "received_at": "2026-07-26T12:00:01+00:00",
        "packet_id": 42,
        "transport": "RF",
        "rssi": -90,
        "snr": 7.5,
        "hop_limit": 3,
        "hop_start": 3,
        "gateway_profile_id": "tcp-test",
        "gateway_node_id": "!aaaaaaaa",
        "gateway_transport": "tcp",
    }
    database.insert_telemetry(
        common_observation
        | {
            "dedupe_key": "ipc-telemetry-42",
            "kind": "device",
            "metrics": {"batteryLevel": 75},
        }
    )
    database.insert_position(
        common_observation
        | {
            "dedupe_key": "ipc-position-42",
            "latitude": 60.123,
            "longitude": 5.456,
            "altitude_msl": 104,
            "metadata": {},
        }
    )
    assert (
        app.dispatch({"command": "node", "node_id": "!11112222"})["data"]["long_name"]
        == "Test"
    )
    overview = app.dispatch(
        {"command": "node_overview", "node_id": "!11112222"}
    )["data"]
    assert overview["counts"]["telemetry"] == 1
    assert overview["latest_position"]["altitude_msl"] == 104
    assert app.dispatch(
        {"command": "node_telemetry", "node_id": "!11112222"}
    )["data"][0]["metrics"]["batteryLevel"] == 75
    assert app.dispatch(
        {"command": "node_positions", "node_id": "!11112222"}
    )["data"][0]["latitude"] == 60.123
    with pytest.raises(ValueError):
        app.dispatch({"command": "messages", "conversation": "!kort"})
    with pytest.raises(ValueError):
        app.dispatch({"command": "ukjend"})


def test_ipc_shutdown_starts_only_after_response_is_completed(tmp_path):
    database = Database(tmp_path / "db.sqlite")
    database.initialize()
    stopped = threading.Event()
    app = IPCApplication(
        Settings(database_path=database.path),
        database,
        FakeService(),
        EventHub(),
        shutdown_callback=stopped.set,
    )
    request_data = {"command": "shutdown"}

    response = app.dispatch(request_data)

    assert response == {"ok": True, "data": {"stopping": True}}
    assert not stopped.is_set()
    app.complete_request(request_data)
    assert stopped.wait(1)


def test_ipc_socket_roundtrip(tmp_path):
    database = Database(tmp_path / "db.sqlite")
    database.initialize()
    settings = Settings(
        database_path=database.path,
        ipc_port=0,
        ipc_token="a" * 64,
        ipc_transport="tcp",
    )
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
    settings = Settings(
        database_path=database.path,
        ipc_port=0,
        ipc_token="b" * 64,
        ipc_transport="tcp",
    )
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


def test_watchers_have_a_separate_quota_from_regular_requests(tmp_path):
    database = Database(tmp_path / "db.sqlite")
    database.initialize()
    settings = Settings(
        database_path=database.path,
        ipc_port=0,
        ipc_token="w" * 64,
        ipc_transport="tcp",
    )
    server = IPCServer(
        settings,
        IPCApplication(settings, database, FakeService(), EventHub()),
    )
    settings = Settings(
        database_path=database.path,
        ipc_host=server.address[0],
        ipc_port=server.address[1],
        ipc_token=settings.ipc_token,
        ipc_transport="tcp",
    )
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    watchers = []
    try:
        for _ in range(8):
            watchers.append(open_watch(settings))
        with pytest.raises(CLIError, match="For mange aktive overvakingar"):
            open_watch(settings)
        assert request(settings, {"command": "status"})["data"]["state"] == "tilkopla"
    finally:
        for sock, stream in watchers:
            stream.close()
            sock.close()
        server.shutdown()
        thread.join(timeout=2)


@pytest.mark.parametrize("close_mode", ["close", "shutdown"])
def test_closed_watcher_releases_watcher_and_client_slots(
    tmp_path, monkeypatch, close_mode
):
    monkeypatch.setattr("meshpi.ipc.IPC_WATCH_HEARTBEAT", 0.05)
    database = Database(tmp_path / "db.sqlite")
    database.initialize()
    server_settings = Settings(
        database_path=database.path,
        ipc_port=0,
        ipc_token="r" * 64,
        ipc_transport="tcp",
    )
    events = EventHub()
    server = IPCServer(
        server_settings,
        IPCApplication(server_settings, database, FakeService(), events),
    )
    client_settings = Settings(
        database_path=database.path,
        ipc_host=server.address[0],
        ipc_port=server.address[1],
        ipc_token=server_settings.ipc_token,
        ipc_transport="tcp",
    )
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    sock = stream = replacement_sock = replacement_stream = None
    try:
        sock, stream = open_watch(client_settings)
        if close_mode == "shutdown":
            sock.shutdown(socket.SHUT_RDWR)
        stream.close()
        sock.close()
        stream = sock = None
        events.publish({"type": "message", "data": {"kind": "public"}})

        deadline = time.monotonic() + 2
        while time.monotonic() < deadline:
            if (
                server._server._watcher_slots._value == 8
                and server._server._client_slots._value == 40
            ):
                break
            time.sleep(0.01)

        assert server._server._watcher_slots._value == 8
        assert server._server._client_slots._value == 40
        replacement_sock, replacement_stream = open_watch(client_settings)
        assert request(client_settings, {"command": "status"})["ok"] is True
    finally:
        if stream is not None:
            stream.close()
        if sock is not None:
            sock.close()
        if replacement_stream is not None:
            replacement_stream.close()
        if replacement_sock is not None:
            replacement_sock.close()
        server.shutdown()
        thread.join(timeout=2)


@pytest.mark.skipif(os.name == "nt", reason="POSIX Unix-socket")
def test_posix_ipc_uses_private_unix_socket():
    with tempfile.TemporaryDirectory(prefix="meshpi-ipc-", dir="/tmp") as temporary:
        root = Path(temporary)
        database = Database(root / "db.sqlite")
        database.initialize()
        socket_path = root / "run" / "meshpi.sock"
        settings = Settings(
            database_path=database.path,
            ipc_token="u" * 64,
            ipc_transport="unix",
            ipc_socket_path=socket_path,
        )
        server = IPCServer(
            settings,
            IPCApplication(settings, database, FakeService(), EventHub()),
        )
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            assert socket_path.stat().st_mode & 0o777 == 0o600
            assert request(settings, {"command": "status"})["data"]["state"] == "tilkopla"
        finally:
            server.shutdown()
            thread.join(timeout=2)
        assert not socket_path.exists()


@pytest.mark.skipif(os.name == "nt", reason="POSIX Unix-socket")
def test_posix_ipc_rejects_unsafe_socket_path():
    with tempfile.TemporaryDirectory(prefix="meshpi-ipc-", dir="/tmp") as temporary:
        root = Path(temporary)
        database = Database(root / "db.sqlite")
        database.initialize()
        socket_path = root / "meshpi.sock"
        socket_path.write_text("ikkje ein socket", encoding="utf-8")
        settings = Settings(
            database_path=database.path,
            ipc_token="u" * 64,
            ipc_transport="unix",
            ipc_socket_path=socket_path,
        )

        with pytest.raises(PermissionError, match="trygg"):
            IPCServer(
                settings,
                IPCApplication(settings, database, FakeService(), EventHub()),
            )


@pytest.mark.skipif(os.name == "nt", reason="POSIX Unix-socket")
def test_posix_shutdown_does_not_remove_replacement_socket():
    with tempfile.TemporaryDirectory(prefix="meshpi-ipc-", dir="/tmp") as temporary:
        root = Path(temporary)
        database = Database(root / "db.sqlite")
        database.initialize()
        socket_path = root / "meshpi.sock"
        settings = Settings(
            database_path=database.path,
            ipc_token="u" * 64,
            ipc_transport="unix",
            ipc_socket_path=socket_path,
        )
        server = IPCServer(
            settings,
            IPCApplication(settings, database, FakeService(), EventHub()),
        )
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        socket_path.unlink()
        replacement = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        replacement.bind(str(socket_path))
        replacement.listen()
        try:
            server.shutdown()
            thread.join(timeout=2)
            assert socket_path.exists()
        finally:
            replacement.close()


@pytest.mark.skipif(os.name != "nt", reason="Windows-spesifikk socketåtferd")
def test_windows_ipc_server_uses_exclusive_address(tmp_path):
    database = Database(tmp_path / "db.sqlite")
    database.initialize()
    settings = Settings(database_path=database.path, ipc_port=0, ipc_token="c" * 64)
    server = IPCServer(
        settings,
        IPCApplication(settings, database, FakeService(), EventHub()),
    )
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        with socket.socket() as contender:
            contender.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            with pytest.raises(OSError):
                contender.bind(server.address)
    finally:
        server.shutdown()
        thread.join(timeout=2)


@pytest.mark.skipif(os.name != "nt", reason="Windows-spesifikk socketåtferd")
def test_windows_ipc_server_rejects_prebound_reusable_address(tmp_path):
    with socket.socket() as blocker:
        blocker.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        blocker.bind(("127.0.0.1", 0))
        blocker.listen()
        database = Database(tmp_path / "db.sqlite")
        database.initialize()
        settings = Settings(
            database_path=database.path,
            ipc_port=blocker.getsockname()[1],
            ipc_token="d" * 64,
        )

        with pytest.raises(OSError):
            IPCServer(
                settings,
                IPCApplication(settings, database, FakeService(), EventHub()),
            )
