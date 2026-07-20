import threading
import time

import pytest

from meshpi.config import Settings
from meshpi.database import Database
from meshpi.events import EventHub
from meshpi.models import MessageStatus
from meshpi.service import MeshtasticService, reconnect_delay


class SentPacket:
    id = 991


class FakeInterface:
    def __init__(self):
        self.nodes = {}
        self.isConnected = threading.Event()
        self.isConnected.set()
        self.calls = []
        self.closed = False

    def sendText(self, text, **kwargs):
        self.calls.append((text, kwargs))
        return SentPacket()

    def close(self):
        self.closed = True


@pytest.fixture
def service(tmp_path):
    database = Database(tmp_path / "db.sqlite")
    database.initialize()
    value = MeshtasticService(Settings(database_path=database.path), database, EventHub())
    interface = FakeInterface()
    value._interface = interface
    value._local_node_id = "!710365c8"
    value._set_status("tilkopla")
    return value, interface, database


def test_send_public_channel_zero(service):
    value, interface, database = service
    result = value.send_public("hei")
    text, kwargs = interface.calls[0]
    assert text == "hei"
    assert kwargs["destinationId"] == "^all"
    assert kwargs["channelIndex"] == 0
    assert kwargs["wantAck"] is False
    assert result["packet_id"] == 991
    assert database.list_messages("public")[0]["text"] == "hei"


def test_send_dm_requests_ack_and_updates_status(service):
    value, interface, database = service
    value.send_dm("!11112222", "privat")
    _, kwargs = interface.calls[0]
    assert kwargs["destinationId"] == "!11112222"
    assert kwargs["wantAck"] is True
    assert kwargs["onResponse"].__name__ == "onAckNak"
    kwargs["onResponse"]({"decoded": {"routing": {"errorReason": "NONE"}}})
    row = database.list_messages("dm", "!11112222")[0]
    assert row["status"] == str(MessageStatus.ACKNOWLEDGED)


def test_dm_failure_status(service):
    value, interface, database = service
    value.send_dm("!11112222", "privat")
    interface.calls[0][1]["onResponse"](
        {"decoded": {"routing": {"errorReason": "NO_ROUTE"}}}
    )
    row = database.list_messages("dm", "!11112222")[0]
    assert row["status"] == str(MessageStatus.FAILED)


def test_very_early_ack_is_not_lost(service):
    value, interface, database = service

    def send_with_immediate_ack(text, **kwargs):
        del text
        kwargs["onResponse"]({"decoded": {"routing": {"errorReason": "NONE"}}})
        return SentPacket()

    interface.sendText = send_with_immediate_ack
    result = value.send_dm("!11112222", "privat")
    assert result["status"] == str(MessageStatus.ACKNOWLEDGED)
    assert database.list_messages("dm", "!11112222")[0]["status"] == "stadfesta"


def test_send_requires_connection(service):
    value, _, _ = service
    value._interface = None
    value._set_status("fråkopla")
    with pytest.raises(RuntimeError):
        value.send_public("hei")


def test_reconnect_backoff_is_bounded():
    assert [reconnect_delay(i) for i in range(7)] == [2, 5, 10, 30, 30, 30, 30]


def test_service_retries_after_connection_failure(tmp_path, monkeypatch):
    monkeypatch.setattr("meshpi.service.RECONNECT_DELAYS", (0, 0, 0, 0))
    database = Database(tmp_path / "db.sqlite")
    database.initialize()
    attempts = []
    holder = {}

    def factory(host, port):
        attempts.append((host, port))
        if len(attempts) == 1:
            raise OSError("ingen node")
        holder["service"]._stop.set()
        return FakeInterface()

    value = MeshtasticService(
        Settings(database_path=database.path),
        database,
        EventHub(),
        interface_factory=factory,
    )
    holder["service"] = value
    value.start()
    deadline = time.monotonic() + 2
    while value._thread and value._thread.is_alive() and time.monotonic() < deadline:
        time.sleep(0.01)
    value.stop()
    assert len(attempts) == 2
