from __future__ import annotations

from collections.abc import Callable, Iterable
from typing import Any

from meshpi.i18n import tr


class BLEDiscoveryError(RuntimeError):
    pass


class BLEConnectionError(RuntimeError):
    pass


def _discovery_error_message(exc: Exception) -> str:
    try:
        from bleak.exc import (
            BleakBluetoothNotAvailableError,
            BleakBluetoothNotAvailableReason,
        )
    except (ImportError, AttributeError):
        BleakBluetoothNotAvailableError = ()  # type: ignore[assignment,misc]
        BleakBluetoothNotAvailableReason = None  # type: ignore[assignment,misc]

    if BleakBluetoothNotAvailableReason is not None and isinstance(
        exc,
        BleakBluetoothNotAvailableError,
    ):
        if exc.reason == BleakBluetoothNotAvailableReason.POWERED_OFF:
            return tr("ble.powered_off")
        if exc.reason in {
            BleakBluetoothNotAvailableReason.DENIED_BY_USER,
            BleakBluetoothNotAvailableReason.DENIED_BY_SYSTEM,
            BleakBluetoothNotAvailableReason.DENIED_BY_UNKNOWN,
        }:
            return tr("ble.permission_system")
        if exc.reason in {
            BleakBluetoothNotAvailableReason.NO_BLUETOOTH,
            BleakBluetoothNotAvailableReason.NO_BLE_CENTRAL_ROLE,
        }:
            return tr("ble.no_adapter")

    detail = str(exc).casefold()
    if any(
        marker in detail
        for marker in ("access denied", "not authorized", "permission denied")
    ):
        return tr("ble.permission")
    if any(marker in detail for marker in ("powered off", "not ready")):
        return tr("ble.not_ready")
    return tr("ble.discovery_failed")


def _meshtastic_scan() -> Iterable[Any]:
    from meshtastic.ble_interface import BLEInterface

    return BLEInterface.scan()


def connection_error_message(exc: Exception) -> str:
    kind = str(getattr(exc, "kind", "") or "")
    if kind == "device_not_found":
        return tr("ble.device_not_found")
    if kind == "multiple_devices":
        return tr("ble.multiple_devices")
    if kind in {"read_error", "write_error"}:
        return tr("ble.connection_denied")

    detail = str(exc).casefold()
    if any(marker in detail for marker in ("pair", "authentication", "encrypt")):
        return tr("ble.pairing_failed")
    adapter_message = _discovery_error_message(exc)
    if adapter_message != tr("ble.discovery_failed"):
        return adapter_message
    return tr("ble.connection_failed")


def discover_ble(
    scan: Callable[[], Iterable[Any]] = _meshtastic_scan,
) -> list[dict[str, Any]]:
    try:
        devices = scan()
    except Exception as exc:
        raise BLEDiscoveryError(_discovery_error_message(exc)) from exc

    found: dict[str, dict[str, Any]] = {}
    for device in devices:
        identifier = str(getattr(device, "address", "") or "").strip()
        if not identifier:
            continue
        name = str(getattr(device, "name", "") or "").strip() or identifier
        found[identifier] = {
            "transport": "ble",
            "target": f"ble://{identifier}",
            "ble_identifier": identifier,
            "name": name,
        }
    return sorted(
        found.values(),
        key=lambda item: (
            str(item["name"]).casefold(),
            str(item["ble_identifier"]),
        ),
    )
