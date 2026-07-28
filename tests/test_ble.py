from types import SimpleNamespace

import pytest
from bleak.exc import (
    BleakBluetoothNotAvailableError,
    BleakBluetoothNotAvailableReason,
)

from meshpi.ble import BLEDiscoveryError, discover_ble


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
    [
        (BleakBluetoothNotAvailableReason.POWERED_OFF, "slått av"),
        (BleakBluetoothNotAvailableReason.DENIED_BY_USER, "ikkje løyve"),
        (BleakBluetoothNotAvailableReason.NO_BLUETOOTH, "ingen BLE-adapter"),
    ],
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
