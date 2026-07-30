from __future__ import annotations

import socket
import sys
import threading
from collections.abc import Callable
from contextlib import suppress
from typing import Any, Protocol

from meshpi.ble import BLEConnectionError, connection_error_message
from meshpi.connections import ConnectionProfile


class Interface(Protocol):
    nodes: dict[str, dict[str, Any]] | None
    isConnected: threading.Event

    def sendText(self, text: str, **kwargs: Any) -> Any: ...

    def sendData(self, data: Any, **kwargs: Any) -> Any: ...

    def close(self) -> None: ...


InterfaceFactory = Callable[[ConnectionProfile], Interface]
TransportFactory = Callable[[ConnectionProfile], Interface]


def create_serial_interface(profile: ConnectionProfile) -> Interface:
    from meshtastic.serial_interface import SerialInterface

    return SerialInterface(devPath=profile.device, timeout=30)


def create_tcp_interface(profile: ConnectionProfile) -> Interface:
    from meshtastic.tcp_interface import TCPInterface

    class TimedTCPInterface(TCPInterface):
        def myConnect(self) -> None:  # noqa: N802
            connected = socket.create_connection(
                (self.hostname, self.portNumber), timeout=10
            )
            connected.settimeout(None)
            self.socket = connected

    return TimedTCPInterface(
        hostname=str(profile.host),
        portNumber=int(profile.port or 4403),
        timeout=10,
    )


def create_ble_interface(profile: ConnectionProfile) -> Interface:
    from meshtastic.ble_interface import BLEInterface

    class ManagedBLEInterface(BLEInterface):
        def __init__(self, *args: Any, **kwargs: Any):
            self._meshpi_close_lock = threading.Lock()
            self._meshpi_closing = False
            self._meshpi_closed = False
            try:
                super().__init__(*args, **kwargs)
            except Exception:
                if getattr(self, "_want_receive", False) or getattr(
                    self, "client", None
                ) is not None:
                    with suppress(Exception):
                        self.close()
                raise

        def close(self) -> None:
            # Bleak kan utløyse disconnected_callback frå eventtråden medan
            # BLEInterface.close() ventar på same tråd. Eit nytt close-kall
            # må då returnere, elles kan daemonen låse seg under avslutting.
            with self._meshpi_close_lock:
                if self._meshpi_closing or self._meshpi_closed:
                    return
                self._meshpi_closing = True
            try:
                super().close()
            finally:
                with self._meshpi_close_lock:
                    self._meshpi_closing = False
                    self._meshpi_closed = True

        def connect(self, address: str | None = None) -> Any:
            if sys.platform != "darwin":
                return super().connect(address)

            # BLEInterface finn først ein BLEDevice i ei kortliva eventsløyfe,
            # før Bleak må søkje på nytt i den permanente eventsløyfa. På
            # macOS kan ikkje CoreBluetooth-eininga flyttast frå den lukka
            # sløyfa. Søk og kople difor til med same klient og eventsløyfe.
            from meshtastic.ble_interface import (
                SERVICE_UUID,
                BleakClient,
                BLEClient,
            )

            client = BLEClient()
            try:
                response = client.discover(
                    timeout=10,
                    return_adv=True,
                    service_uuids=[SERVICE_UUID],
                )
                devices = [
                    device
                    for device, advertisement in response.values()
                    if SERVICE_UUID in advertisement.service_uuids
                    and (
                        not address
                        or address in (device.name, device.address)
                    )
                ]
                if not devices:
                    raise self.BLEError(
                        f"No Meshtastic BLE peripheral with identifier or "
                        f"address '{address}' found.",
                        self.BLEError.DEVICE_NOT_FOUND,
                    )
                if len(devices) > 1:
                    raise self.BLEError(
                        f"More than one Meshtastic BLE peripheral with "
                        f"identifier or address '{address}' found.",
                        self.BLEError.MULTIPLE_DEVICES,
                    )

                client.bleak_client = BleakClient(
                    devices[0],
                    disconnected_callback=lambda _: self.close(),
                )
                client.connect()
                return client
            except Exception:
                with suppress(Exception):
                    client.close()
                raise

    try:
        return ManagedBLEInterface(address=profile.ble_identifier)
    except Exception as exc:
        raise BLEConnectionError(connection_error_message(exc)) from exc


TRANSPORT_FACTORIES: dict[str, TransportFactory] = {
    "ble": create_ble_interface,
    "serial": create_serial_interface,
    "tcp": create_tcp_interface,
}


def default_interface_factory(profile: ConnectionProfile) -> Interface:
    try:
        factory = TRANSPORT_FACTORIES[profile.transport]
    except KeyError as exc:
        raise ValueError(f"Ustøtta transport: {profile.transport}") from exc
    return factory(profile)
