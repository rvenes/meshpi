from types import SimpleNamespace

import pytest

try:
    from bleak.exc import (
        BleakBluetoothNotAvailableError,
        BleakBluetoothNotAvailableReason,
    )
except ImportError:
    BleakBluetoothNotAvailableError = None
    BleakBluetoothNotAvailableReason = None

from meshpi.ble import BLEDiscoveryError, connection_error_message, discover_ble

BLEAK_REASON_CASES = (
    [
        (BleakBluetoothNotAvailableReason.POWERED_OFF, "slått av"),
        (BleakBluetoothNotAvailableReason.DENIED_BY_USER, "ikkje løyve"),
        (BleakBluetoothNotAvailableReason.NO_BLUETOOTH, "ingen BLE-adapter"),
    ]
    if BleakBluetoothNotAvailableReason is not None
    else []
)


def test_discover_ble_preserves_platform_identifiers_and_duplicate_names():
    devices = [
        SimpleNamespace(
            address="A1:B2:C3:D4:E5:F6",
            name="Meshtastic",
        ),
        SimpleNamespace(
            address="243E23AE-4A99-406C-B317-18F1BD7B4CBE",
            name="Meshtastic",
        ),
    ]

    found = discover_ble(lambda: devices)

    assert [item["ble_identifier"] for item in found] == [
        "243E23AE-4A99-406C-B317-18F1BD7B4CBE",
        "A1:B2:C3:D4:E5:F6",
    ]
    assert found[0]["target"].startswith("ble://")
    assert found[0]["name"] == found[1]["name"]


def test_discover_ble_deduplicates_same_platform_identifier():
    devices = [
        SimpleNamespace(address="A1:B2:C3:D4:E5:F6", name="Først"),
        SimpleNamespace(address="A1:B2:C3:D4:E5:F6", name="Sist"),
    ]

    assert discover_ble(lambda: devices) == [
        {
            "transport": "ble",
            "target": "ble://A1:B2:C3:D4:E5:F6",
            "ble_identifier": "A1:B2:C3:D4:E5:F6",
            "name": "Sist",
        }
    ]


@pytest.mark.parametrize(
    ("reason", "message"),
    BLEAK_REASON_CASES,
)
@pytest.mark.skipif(
    BleakBluetoothNotAvailableError is None,
    reason="installert Bleak har ikkje strukturerte adapterfeil",
)
def test_discover_ble_translates_adapter_errors(reason, message):
    def fail():
        raise BleakBluetoothNotAvailableError("backend detail", reason)

    with pytest.raises(BLEDiscoveryError, match=message):
        discover_ble(fail)


def test_discover_ble_does_not_expose_unknown_backend_details():
    def fail():
        raise RuntimeError("private backend path and platform detail")

    with pytest.raises(BLEDiscoveryError, match="BLE-søket feila") as captured:
        discover_ble(fail)

    assert "private backend" not in str(captured.value)


def test_discover_ble_handles_older_bleak_without_new_error_types(monkeypatch):
    real_import = __import__

    def import_without_new_bleak_errors(name, globals=None, locals=None, fromlist=(), level=0):
        if name == "bleak.exc" and "BleakBluetoothNotAvailableError" in fromlist:
            raise ImportError("eldre Bleak-API")
        return real_import(name, globals, locals, fromlist, level)

    monkeypatch.setattr("builtins.__import__", import_without_new_bleak_errors)

    def fail():
        raise RuntimeError("powered off")

    with pytest.raises(BLEDiscoveryError, match="slått av"):
        discover_ble(fail)


def test_connection_error_explains_macos_pin_dialog():
    message = connection_error_message(RuntimeError("authentication failed"))

    assert "systemdialogen" in message
    assert "PIN-koden" in message
