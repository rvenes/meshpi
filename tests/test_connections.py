import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from meshpi.connections import (
    ConnectionProfile,
    ConnectionStore,
    discover_serial,
    discover_tcp,
    parse_connection_target,
)


@pytest.mark.parametrize(
    ("target", "transport", "endpoint"),
    [
        ("10.0.0.135", "tcp", "10.0.0.135:4403"),
        ("10.0.0.135:4404", "tcp", "10.0.0.135:4404"),
        ("tcp://meshtastic.local", "tcp", "meshtastic.local:4403"),
        ("/dev/ttyACM0", "serial", "/dev/ttyACM0"),
        ("serial:///dev/serial/by-id/test", "serial", "/dev/serial/by-id/test"),
        ("COM3", "serial", "COM3"),
    ],
)
def test_parse_connection_target(target, transport, endpoint):
    profile = parse_connection_target(target)
    assert profile.transport == transport
    assert profile.endpoint == endpoint


def test_parse_connection_target_rejects_invalid_port():
    with pytest.raises(ValueError):
        parse_connection_target("10.0.0.135:70000")


def test_connection_store_persists_profiles_and_active_choice(tmp_path):
    path = tmp_path / "connections.json"
    default = ConnectionProfile.tcp("192.0.2.42")
    store = ConnectionStore(path, default)
    serial = ConnectionProfile.serial("/dev/ttyACM0", name="USB-node")

    store.save_and_activate(serial)

    reloaded = ConnectionStore(path, default)
    assert reloaded.active_profile() == serial
    assert {item.transport for item in reloaded.list_profiles()} == {"tcp", "serial"}
    saved = json.loads(path.read_text(encoding="utf-8"))
    assert saved["active_profile_id"] == serial.profile_id


def test_connection_store_starts_empty_without_default_profile(tmp_path):
    path = tmp_path / "connections.json"
    store = ConnectionStore(path)

    assert store.active_profile() is None
    assert store.list_profiles() == []
    saved = json.loads(path.read_text(encoding="utf-8"))
    assert saved == {"version": 1, "active_profile_id": None, "profiles": []}


def test_connection_profile_rejects_unknown_transport():
    with pytest.raises(ValueError):
        ConnectionProfile.from_dict({"transport": "ble", "name": "test"})


def test_discover_serial_prefers_stable_by_id_path(monkeypatch):
    port = SimpleNamespace(
        device="/dev/ttyACM0",
        description="Seeed XIAO",
        serial_number="ABC",
        hwid="USB test",
    )
    monkeypatch.setattr("serial.tools.list_ports.comports", lambda: [port])
    monkeypatch.setattr(
        "meshpi.connections._stable_serial_paths",
        lambda: {str(Path("/dev/ttyACM0").resolve()): "/dev/serial/by-id/xiao"},
    )

    assert discover_serial()[0]["target"] == "/dev/serial/by-id/xiao"


def test_discover_tcp_returns_only_open_hosts(monkeypatch):
    class Connection:
        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

    def connect(address, timeout):
        del timeout
        if address[0] == "10.0.0.2":
            return Connection()
        raise OSError("lukka")

    monkeypatch.setattr("meshpi.connections.socket.create_connection", connect)
    found = discover_tcp("10.0.0.0/30")
    assert [item["host"] for item in found] == ["10.0.0.2"]


def test_discover_tcp_rejects_large_network():
    with pytest.raises(ValueError):
        discover_tcp("10.0.0.0/16")
