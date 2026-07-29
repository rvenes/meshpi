from __future__ import annotations

from collections.abc import Callable, Iterable
from typing import Any


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
            return "Bluetooth er slått av. Slå på Bluetooth og prøv på nytt."
        if exc.reason in {
            BleakBluetoothNotAvailableReason.DENIED_BY_USER,
            BleakBluetoothNotAvailableReason.DENIED_BY_SYSTEM,
            BleakBluetoothNotAvailableReason.DENIED_BY_UNKNOWN,
        }:
            return (
                "MeshPi har ikkje løyve til å bruke Bluetooth. "
                "Gi Bluetooth-løyve i systeminnstillingane og prøv på nytt."
            )
        if exc.reason in {
            BleakBluetoothNotAvailableReason.NO_BLUETOOTH,
            BleakBluetoothNotAvailableReason.NO_BLE_CENTRAL_ROLE,
        }:
            return "Fann ingen BLE-adapter som MeshPi kan bruke."

    detail = str(exc).casefold()
    if any(
        marker in detail
        for marker in ("access denied", "not authorized", "permission denied")
    ):
        return (
            "MeshPi har ikkje løyve til å bruke Bluetooth. "
            "Kontroller Bluetooth-løyva og prøv på nytt."
        )
    if any(marker in detail for marker in ("powered off", "not ready")):
        return "Bluetooth er slått av eller ikkje klart. Prøv på nytt når adapteren er på."
    return "BLE-søket feila. Kontroller Bluetooth-adapteren og prøv på nytt."


def _meshtastic_scan() -> Iterable[Any]:
    from meshtastic.ble_interface import BLEInterface

    return BLEInterface.scan()


def connection_error_message(exc: Exception) -> str:
    kind = str(getattr(exc, "kind", "") or "")
    if kind == "device_not_found":
        return (
            "Fann ikkje BLE-noden. Kontroller at han er slått på, "
            "i rekkevidd og framleis har same identifikator."
        )
    if kind == "multiple_devices":
        return "Fann fleire BLE-einingar med same identifikator eller namn."
    if kind in {"read_error", "write_error"}:
        return (
            "BLE-sambandet blei avvist. Kontroller paring og Bluetooth-løyve "
            "i operativsystemet."
        )

    detail = str(exc).casefold()
    if any(marker in detail for marker in ("pair", "authentication", "encrypt")):
        return (
            "BLE-noden er ikkje para eller godkjenninga feila. "
            "Par eininga i operativsystemet og prøv på nytt."
        )
    adapter_message = _discovery_error_message(exc)
    if not adapter_message.startswith("BLE-søket feila"):
        return adapter_message
    return "Klarte ikkje kople til BLE-noden. Kontroller rekkevidd og paring."


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
