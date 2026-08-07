import json
import os
from pathlib import Path
from types import SimpleNamespace

import pytest

from meshpi.connections import (
    ConnectionProfile,
    ConnectionStore,
    SerialIdentityMismatchError,
    discover_serial,
    discover_tcp,
    parse_connection_target,
    resolve_serial_profile,
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
        ("ble://A1:B2:C3:D4:E5:F6", "ble", "A1:B2:C3:D4:E5:F6"),
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


def test_connection_store_remembers_last_local_node(tmp_path):
    path = tmp_path / "connections.json"
    profile = ConnectionProfile.tcp("192.0.2.42")
    store = ConnectionStore(path, profile)

    updated = store.remember_local_node(profile.profile_id, "!AABBCCDD")

    assert updated.last_local_node_id == "!aabbccdd"
    assert ConnectionStore(path).active_profile() == updated


def test_connection_store_starts_empty_without_default_profile(tmp_path):
    path = tmp_path / "connections.json"
    store = ConnectionStore(path)

    assert store.active_profile() is None
    assert store.list_profiles() == []
    saved = json.loads(path.read_text(encoding="utf-8"))
    assert saved == {"version": 3, "active_profile_id": None, "profiles": []}


@pytest.mark.skipif(os.name != "posix", reason="POSIX-filrettar")
def test_connection_store_is_private_on_posix(tmp_path, monkeypatch):
    path = tmp_path / "connections.json"
    original_replace = os.replace

    def assert_private_before_replace(source, destination):
        assert Path(source).stat().st_mode & 0o777 == 0o600
        original_replace(source, destination)

    monkeypatch.setattr("meshpi.connections.os.replace", assert_private_before_replace)

    ConnectionStore(path)

    assert path.stat().st_mode & 0o777 == 0o600


def test_connection_profile_rejects_unknown_transport():
    with pytest.raises(ValueError):
        ConnectionProfile.from_dict({"transport": "unknown", "name": "test"})


@pytest.mark.parametrize(
    "identifier",
    [
        "A1:B2:C3:D4:E5:F6",
        "A1B2C3D4E5F6",
        "243E23AE-4A99-406C-B317-18F1BD7B4CBE",
    ],
)
def test_ble_profile_preserves_opaque_platform_identifier(identifier):
    profile = ConnectionProfile.ble(identifier, name="Mesh-node")

    assert profile.ble_identifier == identifier
    assert profile.endpoint == identifier
    assert ConnectionProfile.from_dict(profile.as_dict()) == profile


def test_ble_profiles_with_duplicate_names_keep_distinct_ids():
    first = ConnectionProfile.ble("A1:B2:C3:D4:E5:F6", name="Meshtastic")
    second = ConnectionProfile.ble("B1:C2:D3:E4:F5:A6", name="Meshtastic")

    assert first.name == second.name
    assert first.profile_id != second.profile_id


def test_ble_mac_addresses_are_case_insensitive_and_use_platform_spelling():
    lower = ConnectionProfile.ble("a1:b2:c3:d4:e5:f6")
    upper = ConnectionProfile.ble("A1:B2:C3:D4:E5:F6")

    assert lower.profile_id == upper.profile_id
    assert lower.ble_identifier == "A1:B2:C3:D4:E5:F6"


def test_ble_uuids_are_canonical_but_other_opaque_identifiers_are_preserved():
    lower_uuid = ConnectionProfile.ble(
        "243e23ae-4a99-406c-b317-18f1bd7b4cbe"
    )
    upper_uuid = ConnectionProfile.ble(
        "243E23AE-4A99-406C-B317-18F1BD7B4CBE"
    )
    opaque_lower = ConnectionProfile.ble("platform-Token")
    opaque_upper = ConnectionProfile.ble("platform-TOKEN")

    assert lower_uuid == upper_uuid
    assert lower_uuid.ble_identifier == "243E23AE-4A99-406C-B317-18F1BD7B4CBE"
    assert opaque_lower.ble_identifier == "platform-Token"
    assert opaque_lower.profile_id != opaque_upper.profile_id


def test_ble_profile_id_is_always_derived_from_identifier():
    profile = ConnectionProfile.from_dict(
        {
            "profile_id": "display-name-derived-id",
            "name": "Meshtastic",
            "transport": "ble",
            "ble_identifier": "A1:B2:C3:D4:E5:F6",
        }
    )

    assert profile.profile_id == ConnectionProfile.ble(
        "A1:B2:C3:D4:E5:F6"
    ).profile_id


def test_connection_store_migrates_case_variant_ble_profiles(tmp_path):
    path = tmp_path / "connections.json"
    lower = "a1:b2:c3:d4:e5:f6"
    old_active_id = "ble-old-case-sensitive-id"
    path.write_text(
        json.dumps(
            {
                "version": 2,
                "active_profile_id": old_active_id,
                "profiles": [
                    {
                        "profile_id": ConnectionProfile.ble(lower).profile_id,
                        "name": "Første",
                        "transport": "ble",
                        "ble_identifier": "A1:B2:C3:D4:E5:F6",
                    },
                    {
                        "profile_id": old_active_id,
                        "name": "Aktiv",
                        "transport": "ble",
                        "ble_identifier": lower,
                    },
                ],
            }
        ),
        encoding="utf-8",
    )

    store = ConnectionStore(path)

    assert [profile.name for profile in store.list_profiles()] == ["Aktiv"]
    active = store.active_profile()
    assert active is not None
    assert active.name == "Aktiv"
    assert active.ble_identifier == "A1:B2:C3:D4:E5:F6"


def test_connection_store_migrates_version_one_and_preserves_active_profile(tmp_path):
    path = tmp_path / "connections.json"
    tcp = ConnectionProfile.tcp("192.0.2.42")
    serial = ConnectionProfile.serial("/dev/ttyACM0")
    path.write_text(
        json.dumps(
            {
                "version": 1,
                "active_profile_id": serial.profile_id,
                "profiles": [tcp.as_dict(), serial.as_dict()],
            }
        ),
        encoding="utf-8",
    )

    store = ConnectionStore(path)

    assert store.active_profile() == serial
    saved = json.loads(path.read_text(encoding="utf-8"))
    assert saved["version"] == 3
    assert saved["active_profile_id"] == serial.profile_id
    assert {item["transport"] for item in saved["profiles"]} == {"tcp", "serial"}


def test_connection_store_rejects_unknown_future_version(tmp_path):
    path = tmp_path / "connections.json"
    path.write_text(
        json.dumps({"version": 999, "active_profile_id": None, "profiles": []}),
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="Ustøtta versjon"):
        ConnectionStore(path)


def test_discover_serial_prefers_stable_by_id_path(monkeypatch):
    port = SimpleNamespace(
        device="/dev/ttyACM0",
        description="Seeed XIAO",
        serial_number="ABC",
        vid=0x239A,
        pid=0x810B,
        hwid="USB test",
    )
    monkeypatch.setattr("serial.tools.list_ports.comports", lambda: [port])
    monkeypatch.setattr(
        "meshpi.connections._stable_serial_paths",
        lambda: {str(Path("/dev/ttyACM0").resolve()): "/dev/serial/by-id/xiao"},
    )

    assert discover_serial()[0]["target"] == "/dev/serial/by-id/xiao"


def test_discover_serial_marks_linux_ttys_as_secondary(monkeypatch):
    ports = [
        SimpleNamespace(
            device="/dev/ttyS4",
            description="ttyS4",
            serial_number=None,
            vid=None,
            pid=None,
            hwid="PNP0501",
        ),
        SimpleNamespace(
            device="/dev/ttyACM0",
            description="Seeed XIAO",
            serial_number="ABC",
            vid=0x239A,
            pid=0x810B,
            hwid="USB test",
        ),
    ]
    monkeypatch.setattr("serial.tools.list_ports.comports", lambda: ports)
    monkeypatch.setattr("meshpi.connections._stable_serial_paths", dict)
    monkeypatch.setattr("meshpi.connections.sys.platform", "linux")

    found = discover_serial()

    assert [item["system_device"] for item in found] == [
        "/dev/ttyACM0",
        "/dev/ttyS4",
    ]
    assert [item["recommended"] for item in found] == [True, False]


def test_serial_profile_persists_usb_identity(tmp_path):
    path = tmp_path / "connections.json"
    profile = ConnectionProfile.serial(
        "/dev/cu.usbmodem101",
        name="XIAO-BOOT",
        serial_number="ABC123",
        vid=0x239A,
        pid=0x810B,
    )

    ConnectionStore(path).save_and_activate(profile)

    assert ConnectionStore(path).active_profile() == profile


def test_resolve_serial_profile_enriches_current_device_identity():
    profile = ConnectionProfile.serial("/dev/cu.usbmodem101", name="XIAO-BOOT")
    devices = [
        {
            "device": "/dev/cu.usbmodem101",
            "system_device": "/dev/cu.usbmodem101",
            "serial_number": "ABC123",
            "vid": 0x239A,
            "pid": 0x810B,
        }
    ]

    resolved = resolve_serial_profile(profile, devices)

    assert resolved.device == profile.device
    assert resolved.serial_number == "ABC123"
    assert resolved.vid == 0x239A
    assert resolved.pid == 0x810B


def test_resolve_serial_profile_promotes_linux_device_to_stable_path():
    profile = ConnectionProfile.serial("/dev/ttyACM0", name="XIAO-BOOT")
    devices = [
        {
            "device": "/dev/serial/by-id/xiao",
            "system_device": "/dev/ttyACM0",
            "serial_number": "ABC123",
            "vid": 0x239A,
            "pid": 0x810B,
        }
    ]

    resolved = resolve_serial_profile(profile, devices)

    assert resolved.device == "/dev/serial/by-id/xiao"
    assert resolved.serial_number == "ABC123"


def test_resolve_serial_profile_rejects_unverifiable_reused_port():
    profile = ConnectionProfile.serial(
        "/dev/cu.usbmodem101",
        serial_number="ABC123",
        vid=0x239A,
        pid=0x810B,
    )
    devices = [
        {
            "device": "/dev/cu.usbmodem101",
            "system_device": "/dev/cu.usbmodem101",
            "serial_number": None,
            "vid": None,
            "pid": None,
        }
    ]

    with pytest.raises(SerialIdentityMismatchError, match="lagra USB-eininga"):
        resolve_serial_profile(profile, devices)


def test_resolve_serial_profile_rejects_port_reused_by_different_device():
    profile = ConnectionProfile.serial(
        "/dev/cu.usbmodem101",
        serial_number="ABC123",
        vid=0x239A,
        pid=0x810B,
    )
    devices = [
        {
            "device": "/dev/cu.usbmodem101",
            "system_device": "/dev/cu.usbmodem101",
            "serial_number": "OTHER",
            "vid": 0x239A,
            "pid": 0x810B,
        }
    ]

    with pytest.raises(SerialIdentityMismatchError, match="lagra USB-eininga"):
        resolve_serial_profile(profile, devices)


def test_resolve_serial_profile_moves_to_unique_matching_usb_device():
    profile = ConnectionProfile.serial(
        "/dev/cu.usbmodem101",
        serial_number="ABC123",
        vid=0x239A,
        pid=0x810B,
    )
    devices = [
        {
            "device": "/dev/cu.usbmodem1101",
            "serial_number": "ABC123",
            "vid": 0x239A,
            "pid": 0x810B,
        }
    ]

    resolved = resolve_serial_profile(profile, devices)

    assert resolved.device == "/dev/cu.usbmodem1101"
    assert resolved.profile_id == profile.profile_id


def test_resolve_serial_profile_does_not_guess_without_identity():
    profile = ConnectionProfile.serial("/dev/cu.usbmodem101")
    devices = [
        {
            "device": "/dev/cu.usbmodem1101",
            "serial_number": "ABC123",
            "vid": 0x239A,
            "pid": 0x810B,
        }
    ]

    assert resolve_serial_profile(profile, devices) == profile


def test_resolve_serial_profile_does_not_choose_ambiguous_identity():
    profile = ConnectionProfile.serial(
        "/dev/cu.usbmodem101",
        serial_number="DUPLICATE",
        vid=0x239A,
        pid=0x810B,
    )
    devices = [
        {
            "device": f"/dev/cu.usbmodem{number}",
            "serial_number": "DUPLICATE",
            "vid": 0x239A,
            "pid": 0x810B,
        }
        for number in (1101, 1201)
    ]

    assert resolve_serial_profile(profile, devices) == profile


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
