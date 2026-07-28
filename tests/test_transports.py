import sys
from types import SimpleNamespace

import pytest

from meshpi.connections import ConnectionProfile
from meshpi.transports import (
    TRANSPORT_FACTORIES,
    create_ble_interface,
    create_serial_interface,
    create_tcp_interface,
    default_interface_factory,
)


def test_default_interface_factory_uses_registered_transport(monkeypatch):
    profile = ConnectionProfile.tcp("192.0.2.42")
    interface = object()
    called = []

    def factory(selected):
        called.append(selected)
        return interface

    monkeypatch.setitem(TRANSPORT_FACTORIES, "tcp", factory)

    assert default_interface_factory(profile) is interface
    assert called == [profile]


def test_default_interface_factory_rejects_unknown_transport():
    profile = SimpleNamespace(transport="test")

    with pytest.raises(ValueError, match="Ustøtta transport: test"):
        default_interface_factory(profile)


def test_serial_interface_preserves_existing_parameters(monkeypatch):
    captured = {}
    interface = object()

    def serial_interface(**kwargs):
        captured.update(kwargs)
        return interface

    monkeypatch.setitem(
        sys.modules,
        "meshtastic.serial_interface",
        SimpleNamespace(SerialInterface=serial_interface),
    )
    profile = ConnectionProfile.serial("COM7")

    assert create_serial_interface(profile) is interface
    assert captured == {"devPath": "COM7", "timeout": 30}


def test_tcp_interface_preserves_existing_timeouts(monkeypatch):
    captured = {}

    class Connection:
        def settimeout(self, timeout):
            captured["socket_timeout"] = timeout

    connection = Connection()

    def socket_connection(address, timeout):
        captured["address"] = address
        captured["connect_timeout"] = timeout
        return connection

    class TCPInterface:
        def __init__(self, *, hostname, portNumber, timeout):
            captured["interface_timeout"] = timeout
            self.hostname = hostname
            self.portNumber = portNumber
            self.myConnect()

    monkeypatch.setitem(
        sys.modules,
        "meshtastic.tcp_interface",
        SimpleNamespace(TCPInterface=TCPInterface),
    )
    monkeypatch.setattr(
        "meshpi.transports.socket.create_connection",
        socket_connection,
    )

    interface = create_tcp_interface(ConnectionProfile.tcp("192.0.2.42", 4404))

    assert interface.socket is connection
    assert captured == {
        "interface_timeout": 10,
        "address": ("192.0.2.42", 4404),
        "connect_timeout": 10,
        "socket_timeout": None,
    }


def test_ble_interface_uses_opaque_identifier(monkeypatch):
    captured = {}

    class BLEInterface:
        def __init__(self, **kwargs):
            captured.update(kwargs)

    monkeypatch.setitem(
        sys.modules,
        "meshtastic.ble_interface",
        SimpleNamespace(BLEInterface=BLEInterface),
    )

    interface = create_ble_interface(
        ConnectionProfile.ble("243E23AE-4A99-406C-B317-18F1BD7B4CBE")
    )

    assert isinstance(interface, BLEInterface)
    assert captured == {
        "address": "243E23AE-4A99-406C-B317-18F1BD7B4CBE",
    }


def test_ble_interface_ignores_reentrant_close_callback(monkeypatch):
    closed = []

    class BLEInterface:
        def __init__(self, **_kwargs):
            pass

        def close(self):
            self.close()
            closed.append(self)

    monkeypatch.setitem(
        sys.modules,
        "meshtastic.ble_interface",
        SimpleNamespace(BLEInterface=BLEInterface),
    )
    interface = create_ble_interface(
        ConnectionProfile.ble("A1:B2:C3:D4:E5:F6")
    )

    interface.close()
    interface.close()

    assert closed == [interface]


def test_ble_interface_cleans_up_and_translates_failed_connection(monkeypatch):
    closed = []

    class BLEInterface:
        def __init__(self, **_kwargs):
            self._want_receive = True
            self.kind = "device_not_found"
            raise self

        def close(self):
            self._want_receive = False
            closed.append(self)

    class BLEFailure(BLEInterface, Exception):
        pass

    monkeypatch.setitem(
        sys.modules,
        "meshtastic.ble_interface",
        SimpleNamespace(BLEInterface=BLEFailure),
    )

    with pytest.raises(RuntimeError, match="Fann ikkje BLE-noden"):
        create_ble_interface(ConnectionProfile.ble("A1:B2:C3:D4:E5:F6"))

    assert len(closed) == 1
