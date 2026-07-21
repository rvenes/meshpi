import threading
import time
from types import SimpleNamespace

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
        self.data_calls = []
        self.responseHandlers = {}
        self.closed = False
        self.localNode = SimpleNamespace(
            localConfig=SimpleNamespace(lora=SimpleNamespace(hop_limit=3))
        )

    def sendText(self, text, **kwargs):
        self.calls.append((text, kwargs))
        return SentPacket()

    def sendData(self, data, **kwargs):
        self.data_calls.append((data, kwargs))
        return SentPacket()

    def close(self):
        self.closed = True


@pytest.fixture
def service(tmp_path):
    database = Database(tmp_path / "db.sqlite")
    database.initialize()
    value = MeshtasticService(
        Settings(meshtastic_host="192.0.2.42", database_path=database.path),
        database,
        EventHub(),
    )
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


def test_traceroute_is_started_asynchronously_and_publishes_result(service):
    value, interface, _ = service
    with value.events.subscribe() as events:
        started = value.start_node_action("traceroute", "!11112222")
        started_event = events.get(timeout=1)

        assert started["status"] == "started"
        assert started_event["type"] == "node_action"
        _, kwargs = interface.data_calls[0]
        assert kwargs["destinationId"] == "!11112222"
        assert kwargs["portNum"] == 70
        assert kwargs["wantResponse"] is True
        assert kwargs["channelIndex"] == 0
        assert kwargs["hopLimit"] == 3

        kwargs["onResponse"](
            {
                "decoded": {
                    "portnum": "TRACEROUTE_APP",
                    "traceroute": {"snrTowards": [24]},
                }
            }
        )
        completed = events.get(timeout=1)["data"]

    assert completed["status"] == "completed"
    assert completed["result"]["forward"][-1] == {
        "node_id": "!11112222",
        "snr": 6.0,
    }
    assert value.node_action_status(started["action_id"])["status"] == "completed"


def test_traceroute_rejects_local_node_and_parallel_request(service):
    value, interface, _ = service

    with pytest.raises(ValueError, match="lokale noden"):
        value.start_node_action("traceroute", "!710365c8")

    value.start_node_action("traceroute", "!11112222")
    with pytest.raises(RuntimeError, match="allereie i gang"):
        value.start_node_action("traceroute", "!33334444")
    interface.data_calls[0][1]["onResponse"](
        {
            "decoded": {
                "portnum": "ROUTING_APP",
                "routing": {"errorReason": "NO_RESPONSE"},
            }
        }
    )


def test_traceroute_routing_failure_is_published(service):
    value, interface, _ = service
    with value.events.subscribe() as events:
        started = value.start_node_action("traceroute", "!11112222")
        events.get(timeout=1)
        interface.data_calls[0][1]["onResponse"](
            {
                "decoded": {
                    "portnum": "ROUTING_APP",
                    "routing": {"errorReason": "NO_ROUTE"},
                }
            }
        )
        failed = events.get(timeout=1)["data"]

    assert failed["action_id"] == started["action_id"]
    assert failed["status"] == "failed"
    assert "NO_ROUTE" in failed["error"]


def test_traceroute_timeout_fails_action_and_discards_response_handler(
    service, monkeypatch
):
    monkeypatch.setattr("meshpi.service.TRACEROUTE_TIMEOUT_SECONDS", 0.01)
    value, interface, _ = service
    interface.responseHandlers[991] = object()

    with value.events.subscribe() as events:
        started = value.start_node_action("traceroute", "!11112222")
        events.get(timeout=1)
        failed = events.get(timeout=1)["data"]

    assert failed["action_id"] == started["action_id"]
    assert failed["status"] == "failed"
    assert "tidsfristen" in failed["error"]
    assert interface.responseHandlers == {}


def test_service_switches_connection_profile_and_closes_old_interface(service):
    value, interface, database = service

    status = value.connect(target="/dev/ttyACM0", name="USB-node")

    assert interface.closed is True
    assert status["state"] == "koplar til"
    assert status["transport"] == "serial"
    assert status["endpoint"] == "/dev/ttyACM0"
    assert value.connections.active_profile().name == "USB-node"
    assert not any(node["is_local"] for node in database.list_nodes())


def test_reconnect_backoff_is_bounded():
    assert [reconnect_delay(i) for i in range(7)] == [2, 5, 10, 30, 30, 30, 30]


def test_service_retries_after_connection_failure(tmp_path, monkeypatch):
    monkeypatch.setattr("meshpi.service.RECONNECT_DELAYS", (0, 0, 0, 0))
    database = Database(tmp_path / "db.sqlite")
    database.initialize()
    attempts = []
    holder = {}

    def factory(profile):
        attempts.append(profile)
        if len(attempts) == 1:
            raise OSError("ingen node")
        holder["service"]._stop.set()
        return FakeInterface()

    value = MeshtasticService(
        Settings(meshtastic_host="192.0.2.42", database_path=database.path),
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
    assert attempts[0].transport == "tcp"
    assert attempts[0].endpoint == "192.0.2.42:4403"


def test_running_service_switches_from_tcp_to_serial_without_backoff(
    tmp_path, monkeypatch
):
    monkeypatch.setattr("meshpi.service.RECONNECT_DELAYS", (0, 0, 0, 0))
    database = Database(tmp_path / "db.sqlite")
    database.initialize()
    attempts = []
    interfaces = []

    def factory(profile):
        attempts.append(profile)
        interface = FakeInterface()
        interfaces.append(interface)
        return interface

    value = MeshtasticService(
        Settings(meshtastic_host="192.0.2.42", database_path=database.path),
        database,
        EventHub(),
        interface_factory=factory,
    )
    value.start()
    deadline = time.monotonic() + 2
    while len(attempts) < 1 and time.monotonic() < deadline:
        time.sleep(0.01)

    value.connect(target="/dev/ttyACM0", name="USB")
    deadline = time.monotonic() + 2
    while len(attempts) < 2 and time.monotonic() < deadline:
        time.sleep(0.01)

    value.stop()
    assert [profile.transport for profile in attempts[:2]] == ["tcp", "serial"]
    assert attempts[1].endpoint == "/dev/ttyACM0"
    assert interfaces[0].closed is True


def test_service_waits_without_connecting_when_no_profile_exists(tmp_path):
    database = Database(tmp_path / "db.sqlite")
    database.initialize()
    attempts = []

    def factory(profile):
        attempts.append(profile)
        return FakeInterface()

    value = MeshtasticService(
        Settings(database_path=database.path),
        database,
        EventHub(),
        interface_factory=factory,
    )
    value.start()
    time.sleep(0.05)

    assert attempts == []
    assert value.status()["state"] == "ingen node"
    assert value.list_connections()["profiles"] == []

    value.connect(target="192.0.2.42")
    deadline = time.monotonic() + 2
    while not attempts and time.monotonic() < deadline:
        time.sleep(0.01)
    value.stop()
    assert attempts[0].endpoint == "192.0.2.42:4403"
