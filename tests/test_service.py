import json
import sqlite3
import threading
import time
from types import SimpleNamespace

import pytest

from meshpi.channels import dm_conversation_id
from meshpi.config import Settings
from meshpi.connections import ConnectionProfile, ConnectionStore
from meshpi.database import Database
from meshpi.events import EventHub
from meshpi.models import (
    ConversationKind,
    Direction,
    Message,
    MessageStatus,
    Transport,
)
from meshpi.packet import node_from_registry
from meshpi.service import (
    MeshtasticService,
    interface_reader_stopped,
    reconnect_delay,
    suspend_gap_seconds,
)


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
            localConfig=SimpleNamespace(lora=SimpleNamespace(hop_limit=3)),
            channels=[
                SimpleNamespace(
                    index=0,
                    role=1,
                    settings=SimpleNamespace(name="", id=0),
                )
            ],
        )

    def sendText(self, text, **kwargs):
        self.calls.append((text, kwargs))
        return SentPacket()

    def sendData(self, data, *args, **kwargs):
        if args:
            kwargs["destinationId"] = args[0]
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
    value._sync_channels(interface)
    value._set_status("tilkopla")
    return value, interface, database


def test_all_packet_types_accumulate_transport_evidence_without_sending(service):
    value, interface, database = service
    packet = {'fromId': '!11112222', 'rxTime': 1_700_000_000,
              'transportMechanism': 'TRANSPORT_LORA'}
    value._on_receive(packet, interface)
    value._on_receive(packet | {'viaMqtt': True}, interface)
    node = database.get_node('!11112222', local_node_id=value._local_node_id)
    assert (node['seen_rf'], node['seen_mqtt']) == (1, 1)
    value._on_receive({'fromId': '!33334444', 'viaMqtt': True}, FakeInterface())
    assert database.get_node('!33334444', local_node_id=value._local_node_id) is None
    assert not interface.calls
    assert not interface.data_calls


def test_local_node_info_uses_active_gateway_and_never_sends(service):
    from meshtastic.protobuf import config_pb2, localonly_pb2, mesh_pb2

    from meshpi.ipc import IPCApplication

    value, interface, database = service
    config = localonly_pb2.LocalConfig()
    config.lora.CopyFrom(config_pb2.Config.LoRaConfig(
        region=3, use_preset=True, tx_enabled=True, hop_limit=3,
    ))
    config.device.role = config_pb2.Config.DeviceConfig.CLIENT_MUTE
    interface.localNode.localConfig = config
    interface.metadata = mesh_pb2.DeviceMetadata(firmware_version="2.7.test")
    interface.nodes = {
        "!710365c8": {"user": {"id": "!710365c8", "longName": "Lokal"},
                      "deviceMetrics": {"batteryLevel": 55, "voltage": 3.9}},
        "!11112222": {"user": {"id": "!11112222"}, "lastHeard": int(time.time())},
    }
    value._sync_nodes(interface)
    # A different gateway's history must not be included.
    database.upsert_node(
        node_from_registry("!33334444", {"lastHeard": int(time.time())}, "!ffffffff"),
        local_node_id="!ffffffff",
    )
    app = IPCApplication(value.settings, database, value, value.events)
    result = app.dispatch({"command": "local_node_info"})["data"]
    assert result["node"]["long_name"] == "Lokal"
    assert result["node"]["battery_level"] == 55
    assert result["role"] == "CLIENT_MUTE"
    assert result["radio"]["frequency_mhz"] == 869.525
    assert result["registry_count"] == 2
    assert result["stored_count"] == 2
    assert result["heard_24h_count"] == 1
    assert not interface.calls
    assert not interface.data_calls

    value._lost.set()
    result = value.local_node_info()
    assert result["connected"] is False
    assert "radio" not in result
    assert result["stored_count"] == 2
    assert result["node"]["battery_level"] == 55


def test_local_node_info_without_gateway_has_no_unscoped_history(service):
    value, _, _ = service
    value._interface = None
    value._local_node_id = None
    value._profile = None
    value._set_status("ingen node")
    result = value.local_node_info()
    assert result["connected"] is False
    assert result["node"] == {}
    assert result["stored_count"] == 0
    assert result["latest_telemetry"] == {}


def _prepare_node_removal(service):
    from meshtastic.node import Node

    value, interface, database = service
    remote = "!11112222"
    local_id = value._local_node_id
    interface.nodes[remote] = {
        "num": int(remote[1:], 16),
        "user": {"id": remote, "longName": "Fjernnode"},
        "lastHeard": 1_700_000_000,
        "position": {"latitudeI": 601234567, "longitudeI": 51234567},
    }
    interface.nodesByNum = {int(remote[1:], 16): interface.nodes[remote]}
    channels = interface.localNode.channels
    interface.localNode = Node(interface, int(local_id[1:], 16))
    interface.localNode.channels = channels
    # Exercise the SDK admin path using an in-memory transport only.
    interface._getOrCreateByNum = lambda node_num: {"adminSessionPassKey": b""}
    value._sync_nodes(interface)
    return remote, local_id


def test_local_node_list_includes_cached_and_logged_positions_by_gateway(service):
    value, interface, database = service
    remote, local_id = _prepare_node_removal(service)
    item = value.local_node_info()["nodes"][0]
    assert item["node_id"] == remote
    assert item["in_registry"] is True
    assert item["latest_position"]["latitude"] == pytest.approx(60.1234567)
    assert item["latest_position"]["sample_time"] is None
    value._on_receive({
        "fromId": remote,
        "id": 702,
        "decoded": {"portnum": "POSITION_APP", "position": {
            "latitude": 61, "longitude": 6, "time": 1_700_000_100,
        }},
    }, interface)
    other = "!33334444"
    value._on_receive({"fromId": other, "rxTime": 1_700_000_000}, interface)
    items = {item["node_id"]: item for item in value.local_node_info()["nodes"]}
    assert items[remote]["latest_position"]["latitude"] == 61
    assert items[remote]["latest_position"]["gateway_node_id"] == local_id
    assert items[other]["in_registry"] is False
    assert items[other]["latest_position"] is None
    assert not interface.data_calls


@pytest.mark.parametrize("registry_time, expected_latitude", [
    (1_700_000_200, 62), (1_700_000_000, 61), (None, 61),
])
def test_local_node_list_prefers_newest_timestamped_position(
    service, registry_time, expected_latitude,
):
    value, interface, _ = service
    remote, _ = _prepare_node_removal(service)
    value._on_receive({
        "fromId": remote,
        "id": 702,
        "decoded": {"portnum": "POSITION_APP", "position": {
            "latitude": 61, "longitude": 6, "time": 1_700_000_100,
        }},
    }, interface)
    interface.nodes[remote]["position"] = {"latitude": 62, "longitude": 7}
    if registry_time is not None:
        interface.nodes[remote]["position"]["time"] = registry_time
    position = value.local_node_info()["nodes"][0]["latest_position"]
    assert position["latitude"] == expected_latitude
    assert position["sample_time"] is not None
    assert not interface.data_calls


def _wait_node_removal(value, action):
    deadline = time.monotonic() + 3
    while time.monotonic() < deadline:
        current = value.node_action_status(action["action_id"])
        if current["status"] != "started":
            return current
        time.sleep(0.01)
    pytest.fail("Node removal worker did not finish")


def _empty_radio_snapshot(interface, *args):
    interface.nodes = {}
    interface.nodesByNum = {}
    return {}


def test_remove_nodes_uses_sdk_local_admin_and_verifies_fresh_snapshot(
    service, monkeypatch,
):
    from meshtastic.protobuf import portnums_pb2

    value, interface, database = service
    remote, local_id = _prepare_node_removal(service)
    stale_nodes = interface.nodes
    stale_by_num = interface.nodesByNum
    database.upsert_node(
        node_from_registry(remote, interface.nodes[remote], "!abcdef12"),
        local_node_id="!abcdef12",
    )
    value._on_receive({
        "fromId": remote,
        "id": 901,
        "decoded": {"portnum": "POSITION_APP", "position": {
            "latitude": 60, "longitude": 5, "time": 1_700_000_100,
        }},
    }, interface)
    refreshes = []

    def refresh(current, expected_local_id, timeout):
        refreshes.append((current, expected_local_id, timeout))
        assert database.list_nodes(local_node_id=local_id)
        # Cache contents are stale until a fresh radio snapshot arrives.
        assert remote in current.nodes
        current.nodes = {}
        current.nodesByNum = {}
        return {}

    monkeypatch.setattr(value, "_refresh_node_registry", refresh)
    result = _wait_node_removal(value, value.remove_nodes([remote], local_id))
    assert result["status"] == "completed"
    assert result["result"]["contacts"][0]["node_id"] == remote
    assert result["result"]["contacts"][0]["status"] == "removed"
    assert result["result"]["history_preserved"] is True
    assert len(interface.data_calls) == 1
    payload, options = interface.data_calls[0]
    assert payload.remove_by_nodenum == int(remote[1:], 16)
    assert all(field.name in {"remove_by_nodenum", "session_passkey"}
               for field, _ in payload.ListFields())
    assert options["destinationId"] == int(local_id[1:], 16)
    assert options["portNum"] == portnums_pb2.PortNum.ADMIN_APP
    assert options["onResponse"] is None
    assert len(refreshes) == 1
    assert refreshes[0][0] is interface
    assert refreshes[0][1] == local_id
    assert refreshes[0][2] > 0
    assert database.list_nodes(local_node_id=local_id) == []
    assert remote not in stale_nodes
    assert int(remote[1:], 16) not in stale_by_num
    assert database.get_node(remote, local_node_id=local_id)["long_name"] == "Fjernnode"
    assert len(database.list_positions(remote, local_node_id=local_id)) == 1
    assert len(database.list_nodes(local_node_id="!abcdef12")) == 1
    value._sync_nodes(interface)
    assert database.list_nodes(local_node_id=local_id) == []
    value._on_receive({"fromId": remote, "rxTime": int(time.time()) + 1}, interface)
    assert database.list_nodes(local_node_id=local_id)[0]["node_id"] == remote
    assert not interface.calls


def test_remove_nodes_saves_contact_backup_before_first_send(service, monkeypatch):
    from pathlib import Path

    value, interface, _ = service
    remote, local_id = _prepare_node_removal(service)
    interface.nodes[remote]["user"]["publicKey"] = "not-for-contact-backup"
    original_send = interface.sendData
    seen_backups = []

    def send(data, *args, **kwargs):
        actions = list(value._node_actions.values())
        path = Path(actions[-1]["result"]["backup_path"])
        payload = path.read_text(encoding="utf-8")
        seen_backups.append(json.loads(payload))
        assert remote in payload
        assert "Fjernnode" in payload
        assert "not-for-contact-backup" not in payload
        assert "adminSessionPassKey" not in payload
        return original_send(data, *args, **kwargs)

    monkeypatch.setattr(interface, "sendData", send)
    monkeypatch.setattr(value, "_refresh_node_registry", _empty_radio_snapshot)
    result = _wait_node_removal(value, value.remove_nodes([remote], local_id))
    assert result["status"] == "completed"
    assert len(seen_backups) == 1
    assert Path(result["result"]["backup_path"]).is_file()


def test_remove_nodes_sends_and_refreshes_contacts_sequentially(service, monkeypatch):
    value, interface, _ = service
    remote, local_id = _prepare_node_removal(service)
    second = "!33334444"
    interface.nodes[second] = {"num": int(second[1:], 16), "user": {"id": second}}
    interface.nodesByNum[int(second[1:], 16)] = interface.nodes[second]
    value._sync_nodes(interface)
    steps = []
    original_send = interface.sendData

    def send(data, *args, **kwargs):
        steps.append(("send", data.remove_by_nodenum))
        return original_send(data, *args, **kwargs)

    def refresh(current, expected_local_id, timeout):
        removed = remote if len(steps) == 1 else second
        steps.append(("refresh", removed))
        current.nodes = {key: node for key, node in current.nodes.items() if key != removed}
        current.nodesByNum = {node["num"]: node for node in current.nodes.values()}
        return dict(current.nodes)

    monkeypatch.setattr(interface, "sendData", send)
    monkeypatch.setattr(value, "_refresh_node_registry", refresh)
    result = _wait_node_removal(value, value.remove_nodes([remote, second], local_id))
    assert steps == [
        ("send", int(remote[1:], 16)), ("refresh", remote),
        ("send", int(second[1:], 16)), ("refresh", second),
    ]
    assert [item["status"] for item in result["result"]["contacts"]] == ["removed", "removed"]


@pytest.mark.parametrize("outcome", ["timeout", "still_present"])
def test_remove_nodes_does_not_report_success_without_confirmed_absence(
    service, monkeypatch, outcome,
):
    value, interface, database = service
    remote, local_id = _prepare_node_removal(service)

    def refresh(*args):
        if outcome == "timeout":
            raise TimeoutError("No complete radio snapshot")
        return dict(interface.nodes)

    monkeypatch.setattr(value, "_refresh_node_registry", refresh)
    result = _wait_node_removal(value, value.remove_nodes([remote], local_id))
    contact = result["result"]["contacts"][0]
    assert contact["status"] == "sent_unverified"
    assert result["status"] != "completed"
    assert database.list_nodes(local_node_id=local_id)
    assert remote in interface.nodes
    assert len(interface.data_calls) == 1
    assert interface.data_calls[0][1]["onResponse"] is None


@pytest.mark.parametrize(
    "case", ["local", "broadcast", "unknown", "gateway", "offline", "admin", "managed"],
)
def test_remove_nodes_fails_closed_before_sending(service, case):
    value, interface, _ = service
    remote, local_id = _prepare_node_removal(service)
    if case == "local":
        remote = local_id
    elif case == "broadcast":
        remote = "!ffffffff"
    elif case == "unknown":
        remote = "!55556666"
    elif case == "gateway":
        local_id = "!abcdef12"
    elif case == "offline":
        value._lost.set()
    elif case == "admin":
        interface.localNode.nodeNum = int(remote[1:], 16)
    else:
        interface.localNode.localConfig.security.is_managed = True
    with pytest.raises((ValueError, RuntimeError)):
        value.remove_nodes([remote], local_id)
    assert not interface.data_calls


def test_remove_nodes_rejects_whole_batch_before_sending_if_one_target_invalid(service):
    value, interface, _ = service
    remote, local_id = _prepare_node_removal(service)
    with pytest.raises((ValueError, RuntimeError)):
        value.remove_nodes([remote, local_id], local_id)
    assert not interface.data_calls


def test_remove_nodes_refuses_overlapping_removals(service, monkeypatch):
    value, _, _ = service
    remote, local_id = _prepare_node_removal(service)
    entered = threading.Event()
    release = threading.Event()

    def refresh(interface, *args):
        entered.set()
        assert release.wait(3)
        return _empty_radio_snapshot(interface)

    monkeypatch.setattr(value, "_refresh_node_registry", refresh)
    action = value.remove_nodes([remote], local_id)
    try:
        assert entered.wait(3)
        with pytest.raises(RuntimeError):
            value.remove_nodes([remote], local_id)
    finally:
        release.set()
    assert _wait_node_removal(value, action)["status"] == "completed"


def test_remove_nodes_stops_batch_if_gateway_changes_during_refresh(service, monkeypatch):
    value, interface, database = service
    remote, local_id = _prepare_node_removal(service)
    second = "!33334444"
    interface.nodes[second] = {"num": int(second[1:], 16), "user": {"id": second}}
    value._sync_nodes(interface)

    def refresh(*args):
        value._local_node_id = "!abcdef12"
        return {}

    monkeypatch.setattr(value, "_refresh_node_registry", refresh)
    result = _wait_node_removal(value, value.remove_nodes([remote, second], local_id))
    assert result["status"] != "completed"
    contacts = result["result"]["contacts"]
    assert contacts[0]["status"] == "sent_unverified"
    assert contacts[1]["status"] == "skipped"
    assert len(interface.data_calls) == 1
    assert len(database.list_nodes(local_node_id=local_id)) == 2


def test_remove_nodes_ipc_requires_explicit_gateway_and_contact_list(service, monkeypatch):
    from meshpi.ipc import IPCApplication

    value, _, database = service
    remote, local_id = _prepare_node_removal(service)
    monkeypatch.setattr(value, "_refresh_node_registry", _empty_radio_snapshot)
    app = IPCApplication(value.settings, database, value, value.events)
    with pytest.raises(ValueError):
        app.dispatch({"command": "remove_nodes", "node_ids": [remote]})
    with pytest.raises(ValueError):
        app.dispatch({"command": "remove_nodes", "node_ids": remote,
                      "expected_local_node_id": local_id})
    action = app.dispatch({
        "command": "remove_nodes", "node_ids": [remote],
        "expected_local_node_id": local_id,
    })["data"]
    assert _wait_node_removal(value, action)["status"] == "completed"


@pytest.mark.parametrize("registry", ["nodes", "nodesByNum"])
def test_remove_nodes_keeps_contact_if_new_traffic_arrives_after_snapshot(
    service, monkeypatch, registry,
):
    value, interface, database = service
    remote, local_id = _prepare_node_removal(service)
    heard_now = int(time.time())

    def refresh(current, *args):
        fresh = _empty_radio_snapshot(current)
        received = {"num": int(remote[1:], 16), "lastHeard": heard_now}
        if registry == "nodes":
            current.nodes[remote] = received
        else:
            current.nodesByNum[received["num"]] = received
        value._on_receive({"fromId": remote, "rxTime": heard_now}, current)
        return fresh

    monkeypatch.setattr(value, "_refresh_node_registry", refresh)
    result = _wait_node_removal(value, value.remove_nodes([remote], local_id))
    assert result["status"] == "failed"
    assert result["result"]["contacts"][0]["status"] == "sent_unverified"
    row = database.list_nodes(local_node_id=local_id)[0]
    assert row["node_id"] == remote
    assert row["last_heard"] == heard_now
    assert row["removed_at"] is None
    assert getattr(interface, registry)


def test_remove_nodes_sdk_timeout_and_late_return_never_complete_or_send_next(
    service, monkeypatch,
):
    value, interface, database = service
    remote, local_id = _prepare_node_removal(service)
    second = "!33334444"
    interface.nodes[second] = {"num": int(second[1:], 16), "user": {"id": second}}
    value._sync_nodes(interface)
    entered = threading.Event()
    release = threading.Event()
    finished = threading.Event()
    sent = []
    refreshes = []

    def blocked_send(node_id):
        sent.append(node_id)
        entered.set()
        try:
            assert release.wait(3)
            return SentPacket()
        finally:
            finished.set()

    monkeypatch.setattr(interface.localNode, "removeNode", blocked_send)
    monkeypatch.setattr("meshpi.service.NODE_REMOVAL_TIMEOUT_SECONDS", 0.05)
    monkeypatch.setattr(value, "_refresh_node_registry", lambda *args: refreshes.append(args))
    action = value.remove_nodes([remote, second], local_id)
    try:
        assert entered.wait(3)
        result = _wait_node_removal(value, action)
        assert result["status"] == "failed"
        assert [item["status"] for item in result["result"]["contacts"]] == [
            "sent_unverified", "skipped",
        ]
        assert value._lost.is_set()
        assert value._switch_requested.is_set()
        # Even after connectivity recovers, the previous SDK send is still live.
        value._lost.clear()
        value._set_status("tilkopla")
        with pytest.raises(RuntimeError):
            value.remove_nodes([second], local_id)
        assert sent == [remote]
    finally:
        release.set()
        assert finished.wait(3)
    assert value.node_action_status(action["action_id"])["status"] == "failed"
    assert sent == [remote]
    assert refreshes == []
    assert len(database.list_nodes(local_node_id=local_id)) == 2


def test_refresh_node_registry_requires_complete_new_download_and_keeps_unnamed_nodes(service):
    value, interface, _ = service
    remote, local_id = _prepare_node_removal(service)
    unnamed = "!33334444"
    interface.configId = 123
    complete_calls = []

    def original_complete():
        complete_calls.append(True)

    def start_config():
        interface.nodes = {remote: interface.nodes[remote]}
        interface.nodesByNum = {
            int(unnamed[1:], 16): {"num": int(unnamed[1:], 16), "lastHeard": 1_700_000_000},
        }
        interface.configId = 124
        interface.myInfo = SimpleNamespace(my_node_num=int(local_id[1:], 16))
        interface._handleConfigComplete()

    interface._handleConfigComplete = original_complete
    interface._startConfig = start_config
    snapshot = value._refresh_node_registry(interface, local_id, 0.1)
    assert set(snapshot) == {remote, unnamed}
    assert complete_calls == [True]
    assert interface._handleConfigComplete is original_complete
    assert value._node_refresh_interface is None
    assert not interface.data_calls


@pytest.mark.parametrize(
    "failure", ["incomplete", "same_nodes", "same_numbers", "same_id", "gateway"],
)
def test_refresh_node_registry_never_accepts_partial_or_app_edited_cache(service, failure):
    value, interface, _ = service
    _, local_id = _prepare_node_removal(service)
    interface.configId = 123

    def original_complete():
        pass

    def start_config():
        if failure == "same_nodes":
            interface.nodes.clear()
        else:
            interface.nodes = {}
        if failure == "same_numbers":
            interface.nodesByNum.clear()
        else:
            interface.nodesByNum = {}
        if failure != "same_id":
            interface.configId = 124
        interface.myInfo = SimpleNamespace(my_node_num=(
            int("abcdef12", 16) if failure == "gateway" else int(local_id[1:], 16)
        ))
        if failure != "incomplete":
            interface._handleConfigComplete()

    interface._handleConfigComplete = original_complete
    interface._startConfig = start_config
    # This event is already set: it must not count as completion of a new download.
    assert interface.isConnected.is_set()
    with pytest.raises((RuntimeError, TimeoutError)):
        value._refresh_node_registry(interface, local_id, 0.01)
    assert interface._handleConfigComplete is original_complete
    assert value._node_refresh_interface is None


def test_discover_connections_includes_ble_results(service, monkeypatch):
    value, _, _ = service
    monkeypatch.setattr("meshpi.service.discover_serial", list)
    monkeypatch.setattr("meshpi.service.discover_local_subnets", list)
    monkeypatch.setattr(
        "meshpi.service.discover_ble",
        lambda: [
            {
                "transport": "ble",
                "target": "ble://A1:B2:C3:D4:E5:F6",
                "ble_identifier": "A1:B2:C3:D4:E5:F6",
                "name": "Mesh-node",
            }
        ],
    )

    result = value.discover_connections()

    assert result["ble"][0]["ble_identifier"] == "A1:B2:C3:D4:E5:F6"
    assert result["ble_error"] is None


def test_disconnected_service_uses_profile_last_local_node_for_history(tmp_path):
    profile = ConnectionProfile.from_dict(
        ConnectionProfile.tcp("192.0.2.42").as_dict()
        | {"last_local_node_id": "!aaaaaaaa"}
    )
    connections = ConnectionStore(tmp_path / "connections.json", profile)
    database = Database(tmp_path / "db.sqlite")
    database.initialize()

    value = MeshtasticService(
        Settings(database_path=database.path),
        database,
        EventHub(),
        connections=connections,
    )

    assert value.history_local_node_id() == "!aaaaaaaa"
    assert value.status()["history_local_node_id"] == "!aaaaaaaa"
    assert value.status()["local_node_id"] is None


def test_discover_connections_can_return_local_results_without_ble(
    service, monkeypatch
):
    value, _, _ = service
    monkeypatch.setattr("meshpi.service.discover_serial", list)
    monkeypatch.setattr("meshpi.service.discover_local_subnets", list)

    def unexpected_ble_scan():
        raise AssertionError("BLE-søk skal ikkje starte")

    monkeypatch.setattr("meshpi.service.discover_ble", unexpected_ble_scan)

    result = value.discover_connections(include_ble=False)

    assert result["serial"] == []
    assert result["tcp"] == []
    assert result["ble"] == []
    assert result["ble_error"] is None
    assert result["ble_scanned"] is False


def test_discover_connections_reports_concurrent_ble_scan(service, monkeypatch):
    value, _, _ = service
    monkeypatch.setattr("meshpi.service.discover_serial", list)
    monkeypatch.setattr("meshpi.service.discover_local_subnets", list)
    value._ble_operation_lock.acquire()
    try:
        result = value.discover_connections()
    finally:
        value._ble_operation_lock.release()

    assert result["ble"] == []
    assert "allereie i gang" in result["ble_error"]
    assert result["ble_scanned"] is False


def test_discover_connections_distinguishes_ble_connect_from_scan(service):
    value, _, _ = service
    value._ble_connect_active.set()
    value._ble_operation_lock.acquire()
    try:
        result = value.discover_ble_connections()
    finally:
        value._ble_operation_lock.release()
        value._ble_connect_active.clear()

    assert result["ble"] == []
    assert "ferd med å kople til" in result["ble_error"]
    assert result["ble_scanned"] is False


def test_waiting_ble_connect_does_not_mislabel_active_scan(service):
    value, _, _ = service
    value.interface_factory = lambda _profile: FakeInterface()
    profile = ConnectionProfile.ble("A1:B2:C3:D4:E5:F6")
    value._ble_operation_lock.acquire()
    worker = threading.Thread(target=value._create_interface, args=(profile,))
    worker.start()
    try:
        assert value._ble_connect_active.is_set() is False
        result = value.discover_ble_connections()
    finally:
        value._ble_operation_lock.release()
        worker.join(timeout=2)

    assert "allereie i gang" in result["ble_error"]
    assert "kople til" not in result["ble_error"]


def test_service_saves_ble_profile_and_exposes_identifier(service):
    value, interface, _ = service

    status = value.connect(
        target="ble://243E23AE-4A99-406C-B317-18F1BD7B4CBE",
        name="Handhalden",
    )

    assert interface.closed is True
    assert status["state"] == "koplar til"
    assert status["transport"] == "ble"
    assert status["ble_identifier"] == "243E23AE-4A99-406C-B317-18F1BD7B4CBE"
    assert value.connections.active_profile().name == "Handhalden"


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


def test_channel_inventory_never_reads_psk_and_sends_on_selected_channel(service):
    value, interface, database = service

    class SafeSettings:
        name = "Ops"
        id = 1234

        @property
        def psk(self):
            raise AssertionError("PSK skal aldri lesast")

    interface.localNode.channels = [
        SimpleNamespace(
            index=2,
            role=2,
            settings=SafeSettings(),
        )
    ]
    value._sync_channels(interface)
    channel = value.list_channels()[0]

    result = value.send_public(
        "fleirkanal",
        conversation=channel["conversation"],
    )

    assert interface.calls[0][1]["channelIndex"] == 2
    assert result["conversation_id"] == channel["conversation"]
    stored = database.list_messages(
        "public",
        conversation_id=channel["conversation"],
    )
    assert stored[0]["channel"] == 2


def test_selected_channel_must_exist_on_active_node(service):
    value, interface, _ = service

    with pytest.raises(RuntimeError, match="finst ikkje"):
        value.send_public(
            "feil kanal",
            conversation="channel:global:anna:999",
        )
    assert interface.calls == []


def test_invalid_or_historical_routes_never_fall_back_to_channel_zero(service):
    value, interface, _ = service

    with pytest.raises(ValueError, match="channel:"):
        value.send_public("feil kanal", conversation="Ops")
    with pytest.raises(RuntimeError, match="finst ikkje"):
        value.send_dm(
            "!11112222",
            "feil kanal",
            conversation=(
                "dm:!710365c8:!11112222:global:Borte:999"
            ),
        )
    with pytest.raises(RuntimeError, match="annan lokal node"):
        value.send_dm(
            "!11112222",
            "feil node",
            conversation=(
                "dm:!aaaaaaaa:!11112222:local:!710365c8:0:"
            ),
        )
    with pytest.raises(ValueError, match="Mottakaren"):
        value.send_dm(
            "!11112222",
            "feil mottakar",
            conversation=(
                "dm:!710365c8:!33334444:local:!710365c8:0:"
            ),
        )

    assert interface.calls == []


def test_unknown_received_channel_is_not_sendable_and_is_rebound(service):
    value, interface, database = service

    value._on_receive(
        {
            "id": 810,
            "fromId": "!11112222",
            "toId": "!ffffffff",
            "channel": 5,
            "decoded": {
                "portnum": "TEXT_MESSAGE_APP",
                "text": "Kom før kanaloversikta",
            },
        },
        interface,
    )

    assert [item["channel_index"] for item in value.list_channels()] == [0]
    provisional = database.list_messages(
        "public",
        conversation_id=(
            "channel:!710365c8:provisional:!710365c8:5"
        ),
    )
    assert provisional[0]["text"] == "Kom før kanaloversikta"
    with pytest.raises(RuntimeError, match="finst ikkje"):
        value.send_public("skal stoppast", channel_index=5)
    assert interface.calls == []

    interface.localNode.channels = [
        SimpleNamespace(
            index=5,
            role=2,
            settings=SimpleNamespace(name="Sein kanal", id=555),
        )
    ]
    value._sync_channels(interface)
    channel = value.list_channels()[0]
    rebound = database.list_messages(
        "public",
        conversation_id=str(channel["conversation"]),
    )

    assert channel["channel_index"] == 5
    assert rebound[0]["text"] == "Kom før kanaloversikta"
    assert database.list_messages(
        "public",
        conversation_id=(
            "channel:!710365c8:provisional:!710365c8:5"
        ),
    ) == []


def test_channel_sync_merges_safe_legacy_dm_into_sendable_route(service):
    value, interface, database = service
    local_node = str(value._local_node_id)
    peer_node = "!11112222"
    legacy = Message(
        packet_id=811,
        timestamp="2026-07-20T12:00:00+00:00",
        from_node=peer_node,
        to_node=local_node,
        channel=0,
        kind=ConversationKind.DM,
        peer_node=peer_node,
        text="Eldre historikk",
        direction=Direction.INCOMING,
        transport=Transport.RF,
        status=MessageStatus.RECEIVED,
        conversation_id=peer_node,
    )
    database.insert_message(legacy)

    value._sync_channels(interface)

    channel = value.list_channels()[0]
    route = dm_conversation_id(
        local_node,
        peer_node,
        str(channel["channel_key"]),
    )
    conversations = database.conversations()
    assert [item["conversation"] for item in conversations] == [route]
    assert database.list_messages("dm", conversation_id=route)[0]["text"] == (
        "Eldre historikk"
    )


def test_received_public_message_on_secondary_channel_is_logged(service):
    value, interface, database = service
    interface.localNode.channels = [
        SimpleNamespace(
            index=3,
            role=2,
            settings=SimpleNamespace(name="Vakt", id=4567),
        )
    ]
    value._sync_channels(interface)
    conversation = value.list_channels()[0]["conversation"]

    value._on_receive(
        {
            "id": 808,
            "fromId": "!11112222",
            "toId": "!ffffffff",
            "channel": 3,
            "decoded": {
                "portnum": "TEXT_MESSAGE_APP",
                "text": "På kanal tre",
            },
        },
        interface,
    )

    rows = database.list_messages(
        "public",
        conversation_id=str(conversation),
    )
    assert rows[0]["text"] == "På kanal tre"
    assert rows[0]["local_node_id"] == "!710365c8"
    assert rows[0]["channel"] == 3


def test_dm_conversation_keeps_local_node_and_channel_route(service):
    value, interface, database = service
    interface.localNode.channels = [
        SimpleNamespace(
            index=2,
            role=2,
            settings=SimpleNamespace(name="Privat", id=7654),
        )
    ]
    value._sync_channels(interface)
    value._on_receive(
        {
            "id": 809,
            "fromId": "!11112222",
            "toId": "!710365c8",
            "channel": 2,
            "decoded": {
                "portnum": "TEXT_MESSAGE_APP",
                "text": "DM på kanal to",
            },
        },
        interface,
    )
    conversation = database.conversations()[0]["conversation"]

    assert conversation.startswith("dm:!710365c8:!11112222:")
    value.send_dm(
        "!11112222",
        "Svar på same rute",
        conversation=conversation,
    )
    assert interface.calls[0][1]["channelIndex"] == 2
    rows = database.list_messages(
        "dm",
        conversation_id=conversation,
    )
    assert [row["channel"] for row in rows] == [2, 2]


def test_send_dm_requests_ack_and_updates_status(service):
    value, interface, database = service
    value.send_dm("!11112222", "privat")
    _, kwargs = interface.calls[0]
    assert kwargs["destinationId"] == "!11112222"
    assert kwargs["wantAck"] is True
    assert kwargs["onResponse"].__name__ == "onAckNak"
    kwargs["onResponse"](
        {
            "fromId": "!11112222",
            "decoded": {"routing": {"errorReason": "NONE"}},
        }
    )
    row = database.list_messages("dm", "!11112222")[0]
    assert row["status"] == str(MessageStatus.DELIVERED)


def test_implicit_ack_is_not_reported_as_delivered(service):
    value, interface, database = service
    value.send_dm("!11112222", "privat")
    interface.calls[0][1]["onResponse"](
        {
            "fromId": "!710365c8",
            "decoded": {"routing": {"errorReason": "NONE"}},
        }
    )
    row = database.list_messages("dm", "!11112222")[0]
    assert row["status"] == str(MessageStatus.ACKNOWLEDGED)


def test_recipient_ack_after_implicit_ack_is_reported_as_delivered(service):
    value, interface, database = service
    value.send_dm("!11112222", "privat")
    interface.calls[0][1]["onResponse"](
        {
            "fromId": "!710365c8",
            "decoded": {"routing": {"errorReason": "NONE"}},
        }
    )

    value._on_receive(
        {
            "fromId": "!11112222",
            "decoded": {
                "requestId": 991,
                "portnum": "ROUTING_APP",
                "routing": {"errorReason": "NONE"},
            },
        },
        interface,
    )

    row = database.list_messages("dm", "!11112222")[0]
    assert row["status"] == str(MessageStatus.DELIVERED)


def test_received_telemetry_and_position_are_logged_with_gateway(service):
    value, interface, database = service
    value._profile = ConnectionProfile.tcp("192.0.2.42", name="Heimenode")

    value._on_receive(
        {
            "id": 501,
            "fromId": "!11112222",
            "rxTime": 1_700_000_000,
            "decoded": {
                "portnum": "TELEMETRY_APP",
                "telemetry": {
                    "deviceMetrics": {
                        "batteryLevel": 82,
                        "channelUtilization": 9.5,
                    }
                },
            },
        },
        interface,
    )
    value._on_receive(
        {
            "id": 502,
            "fromId": "!11112222",
            "rxTime": 1_700_000_001,
            "decoded": {
                "portnum": "POSITION_APP",
                "position": {
                    "latitudeI": 601234567,
                    "longitudeI": 51234567,
                    "altitude": 104,
                },
            },
        },
        interface,
    )

    telemetry = database.list_telemetry("!11112222")
    positions = database.list_positions("!11112222")
    assert telemetry[0]["metrics"]["batteryLevel"] == 82
    assert telemetry[0]["gateway_node_id"] == "!710365c8"
    assert telemetry[0]["gateway_transport"] == "tcp"
    assert positions[0]["latitude"] == 60.1234567
    assert positions[0]["gateway_profile_id"] == value._profile.profile_id


def test_dm_failure_status(service):
    value, interface, database = service
    value.send_dm("!11112222", "privat")
    interface.calls[0][1]["onResponse"](
        {"decoded": {"routing": {"errorReason": "NO_ROUTE"}}}
    )
    row = database.list_messages("dm", "!11112222")[0]
    assert row["status"] == str(MessageStatus.FAILED)
    assert row["raw_metadata"]["failure_reason"] == "NO_ROUTE"


def test_very_early_ack_is_not_lost(service):
    value, interface, database = service

    def send_with_immediate_ack(text, **kwargs):
        del text
        kwargs["onResponse"](
            {
                "fromId": "!710365c8",
                "decoded": {"routing": {"errorReason": "NONE"}},
            }
        )
        return SentPacket()

    interface.sendText = send_with_immediate_ack
    result = value.send_dm("!11112222", "privat")
    assert result["status"] == str(MessageStatus.ACKNOWLEDGED)
    assert database.list_messages("dm", "!11112222")[0]["status"] == "ACK"


def test_send_requires_connection(service):
    value, _, _ = service
    value._interface = None
    value._set_status("fråkopla")
    with pytest.raises(RuntimeError):
        value.send_public("hei")


def test_synchronous_radio_echo_preserves_one_durable_request(service):
    value, interface, database = service

    def send_with_echo(text, **kwargs):
        interface.calls.append((text, kwargs))
        value._on_receive({
            "id": 991, "fromId": "!710365c8", "toId": "!11112222", "channel": 0,
            "decoded": {"portnum": "TEXT_MESSAGE_APP", "text": text},
        }, interface)
        return SentPacket()

    interface.sendText = send_with_echo
    result = value.send_dm("!11112222", "synthetic echo")
    rows = database.list_messages("dm", "!11112222")
    assert len(interface.calls) == 1
    assert len(rows) == 1
    assert rows[0]["id"] == result["id"]
    assert rows[0]["raw_metadata"]["request_id"]
    assert rows[0]["status"] == str(MessageStatus.QUEUED)


def test_database_failure_before_send_sends_nothing(service, monkeypatch):
    value, interface, database = service
    def fail(_message):
        raise sqlite3.OperationalError("database is locked")
    monkeypatch.setattr(database, "insert_message", fail)
    with pytest.raises(RuntimeError, match="Ingenting vart sendt"):
        value.send_dm("!11112222", "synthetic")
    assert not interface.calls


def test_database_failure_after_send_leaves_durable_uncertain_record(service, monkeypatch):
    value, interface, database = service
    def fail(_message):
        raise sqlite3.OperationalError("disk full")
    monkeypatch.setattr(database, "complete_outgoing_message", fail)
    with pytest.raises(RuntimeError, match="kan vere send"):
        value.send_dm("!11112222", "synthetic")
    assert len(interface.calls) == 1
    rows = database.list_messages("dm", "!11112222")
    assert len(rows) == 1
    assert rows[0]["status"] == "uviss"
    assert rows[0]["raw_metadata"]["request_id"]


def test_radio_failure_does_not_retry_and_preserves_uncertainty(service):
    value, interface, database = service
    def fail(text, **options):
        interface.calls.append((text, options))
        raise OSError("connection lost after write")
    interface.sendText = fail
    with pytest.raises(RuntimeError, match="kan vere send"):
        value.send_dm("!11112222", "synthetic")
    assert len(interface.calls) == 1
    assert database.list_messages("dm", "!11112222")[0]["status"] == "uviss"


def test_traceroute_is_started_asynchronously_and_publishes_result(service):
    value, interface, database = service
    with value.events.subscribe() as events:
        started = value.start_node_action("traceroute", "!11112222")
        started_event = events.get(timeout=1)

        assert started["status"] == "started"
        assert started["cooldown_seconds"] == 30
        assert started_event["type"] == "node_action"
        availability = value.node_action_availability("traceroute", "!11112222")
        assert availability["available"] is False
        assert availability["cooldown_seconds"] == 30
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
    saved = database.list_node_actions("!11112222")
    assert saved[0]["action_id"] == started["action_id"]
    assert saved[0]["status"] == "completed"


def test_traceroute_rejects_local_node_and_request_during_cooldown(service):
    value, interface, _ = service

    with pytest.raises(ValueError, match="lokale noden"):
        value.start_node_action("traceroute", "!710365c8")

    value.start_node_action("traceroute", "!11112222")
    with pytest.raises(RuntimeError, match="sendast igjen om 30 sekund"):
        value.start_node_action("traceroute", "!33334444")
    interface.data_calls[0][1]["onResponse"](
        {
            "decoded": {
                "portnum": "ROUTING_APP",
                "routing": {"errorReason": "NO_RESPONSE"},
            }
        }
    )


def test_position_exchange_requests_remote_position_asynchronously(service):
    value, interface, database = service
    interface.nodes["!710365c8"] = {
        "position": {
            "latitudeI": 602345678,
            "longitudeI": 52345678,
            "altitude": 88,
            "precisionBits": 24,
        }
    }
    with value.events.subscribe() as events:
        started = value.start_node_action("position_exchange", "!11112222")
        started_event = events.get(timeout=1)

        assert started["status"] == "started"
        assert started_event["type"] == "node_action"
        payload, kwargs = interface.data_calls[0]
        assert payload.latitude_i == 602345600
        assert payload.longitude_i == 52345728
        assert payload.altitude == 88
        assert payload.precision_bits == 24
        assert kwargs["destinationId"] == "!11112222"
        assert kwargs["portNum"] == 3
        assert kwargs["wantResponse"] is True
        assert kwargs["channelIndex"] == 0
        assert kwargs["hopLimit"] == 3

        kwargs["onResponse"](
            {
                "decoded": {
                    "portnum": "POSITION_APP",
                    "position": {
                        "latitudeI": 601234567,
                        "longitudeI": 51234567,
                    },
                }
            }
        )
        completed = events.get(timeout=1)["data"]

    assert completed["status"] == "completed"
    assert completed["result"]["position_received"] is True
    assert completed["result"]["local_position_shared"] is True
    assert completed["result"]["local_position_precision_bits"] == 24
    saved = database.list_node_actions(
        "!11112222",
        action="position_exchange",
    )
    assert saved[0]["status"] == "completed"


def test_position_exchange_rejects_local_node(service):
    value, _, _ = service

    with pytest.raises(ValueError, match="lokale noden"):
        value.start_node_action("position_exchange", "!710365c8")


def test_position_exchange_does_not_share_coordinates_without_known_precision(
    service,
):
    value, interface, _ = service
    interface.nodes["!710365c8"] = {
        "position": {
            "latitudeI": 602345678,
            "longitudeI": 52345678,
        }
    }

    value.start_node_action("position_exchange", "!11112222")

    payload, _ = interface.data_calls[0]
    assert payload.latitude_i == 0
    assert payload.longitude_i == 0
    assert payload.precision_bits == 0


def test_position_exchange_uses_channel_precision_and_has_cooldown(service):
    value, interface, _ = service
    interface.localNode.channels = [
        SimpleNamespace(
            settings=SimpleNamespace(
                module_settings=SimpleNamespace(position_precision=16)
            )
        )
    ]
    interface.nodes["!710365c8"] = {
        "position": {
            "latitudeI": 602345678,
            "longitudeI": 52345678,
            "precisionBits": 32,
        }
    }

    value.start_node_action("position_exchange", "!11112222")

    payload, _ = interface.data_calls[0]
    assert payload.latitude_i == 602374144
    assert payload.longitude_i == 52330496
    assert payload.precision_bits == 16
    with pytest.raises(RuntimeError, match="sendast igjen om 30 sekund"):
        value.start_node_action("position_exchange", "!33334444")


def test_position_exchange_missing_channel_settings_fail_closed(service):
    from meshtastic.protobuf import channel_pb2

    value, interface, _ = service
    interface.localNode.channels = [channel_pb2.Channel()]
    position = {"precision_bits": 24}

    assert value._position_precision_bits(interface, position) == 0


@pytest.mark.parametrize(
    ("value", "bits", "expected"),
    [
        (602345678, 16, 602374144),
        (-602345678, 16, -602374144),
        (602345678, 24, 602345600),
        (-602345678, 24, -602345600),
        (602345678, 32, 602345678),
    ],
)
def test_position_mask_uses_middle_of_meshtastic_precision_bucket(
    service,
    value,
    bits,
    expected,
):
    meshpi_service, _, _ = service

    assert meshpi_service._mask_position_coordinate(value, bits) == expected


def test_position_exchange_send_failure_does_not_consume_cooldown(service):
    value, interface, _ = service
    original_send = interface.sendData
    attempts = 0

    def fail_once(data, **kwargs):
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            raise OSError("radio nede")
        return original_send(data, **kwargs)

    interface.sendData = fail_once

    with pytest.raises(RuntimeError, match="radio nede"):
        value.start_node_action("position_exchange", "!11112222")
    retry = value.start_node_action("position_exchange", "!33334444")

    assert retry["status"] == "started"
    assert len(interface.data_calls) == 1


def test_position_exchange_availability_reports_daemon_cooldown(
    service,
    monkeypatch,
):
    clock = [100.0]
    monkeypatch.setattr("meshpi.service.time.monotonic", lambda: clock[0])
    value, _, _ = service

    value.start_node_action("position_exchange", "!11112222")
    availability = value.node_action_availability(
        "position_exchange",
        "!33334444",
    )

    assert availability["available"] is False
    assert availability["cooldown_seconds"] == 30
    clock[0] += 30
    assert value.node_action_availability(
        "position_exchange",
        "!33334444",
    )["available"] is True


def test_traceroute_availability_counts_down_from_last_send(service, monkeypatch):
    clock = [100.0]
    monkeypatch.setattr("meshpi.service.time.monotonic", lambda: clock[0])
    value, interface, _ = service

    value.start_node_action("traceroute", "!11112222")
    availability = value.node_action_availability("traceroute", "!33334444")
    assert availability["cooldown_seconds"] == 30

    clock[0] += 12.2
    availability = value.node_action_availability("traceroute", "!33334444")
    assert availability["available"] is False
    assert availability["cooldown_seconds"] == 18

    interface.data_calls[0][1]["onResponse"](
        {
            "decoded": {
                "portnum": "ROUTING_APP",
                "routing": {"errorReason": "NO_RESPONSE"},
            }
        }
    )
    clock[0] += 18
    availability = value.node_action_availability("traceroute", "!33334444")
    assert availability["available"] is True
    assert availability["cooldown_seconds"] == 0


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


def test_service_switches_connection_profile_and_closes_old_interface(
    service, monkeypatch
):
    monkeypatch.setattr("meshpi.service.discover_serial", lambda: [])
    value, interface, database = service

    status = value.connect(target="/dev/ttyACM0", name="USB-node")

    assert interface.closed is True
    assert status["state"] == "koplar til"
    assert status["transport"] == "serial"
    assert status["endpoint"] == "/dev/ttyACM0"
    assert value.connections.active_profile().name == "USB-node"
    assert not any(node["is_local"] for node in database.list_nodes())


def test_service_saves_usb_identity_when_serial_profile_is_selected(
    service, monkeypatch
):
    value, _, _ = service
    monkeypatch.setattr(
        "meshpi.service.discover_serial",
        lambda: [
            {
                "device": "/dev/cu.usbmodem101",
                "system_device": "/dev/cu.usbmodem101",
                "serial_number": "ABC123",
                "vid": 0x239A,
                "pid": 0x810B,
            }
        ],
    )

    value.connect(target="/dev/cu.usbmodem101", name="XIAO-BOOT")

    profile = value.connections.active_profile()
    assert profile is not None
    assert profile.serial_number == "ABC123"
    assert profile.vid == 0x239A
    assert profile.pid == 0x810B


def test_service_persists_unique_serial_port_relocation(service, monkeypatch):
    value, _, _ = service
    profile = ConnectionProfile.serial(
        "/dev/cu.usbmodem101",
        name="XIAO-BOOT",
        serial_number="ABC123",
        vid=0x239A,
        pid=0x810B,
    )
    value.connections.save_and_activate(profile)
    value._profile = profile
    monkeypatch.setattr(
        "meshpi.service.discover_serial",
        lambda: [
            {
                "device": "/dev/cu.usbmodem1101",
                "system_device": "/dev/cu.usbmodem1101",
                "serial_number": "ABC123",
                "vid": 0x239A,
                "pid": 0x810B,
            }
        ],
    )

    resolved = value._refresh_active_serial_profile(profile)

    assert resolved.device == "/dev/cu.usbmodem1101"
    assert resolved.profile_id == profile.profile_id
    assert value.connections.active_profile() == resolved


def test_reconnect_backoff_is_bounded():
    assert [reconnect_delay(i) for i in range(7)] == [2, 5, 10, 30, 30, 30, 30]


def test_suspend_gap_ignores_normal_runtime_and_detects_sleep():
    assert suspend_gap_seconds(100, 50, 130, 80) == 0
    assert suspend_gap_seconds(100, 50, 250, 80) == 120


def test_reader_health_only_reports_a_known_stopped_thread():
    assert interface_reader_stopped(FakeInterface()) is False
    running = FakeInterface()
    running._rxThread = SimpleNamespace(is_alive=lambda: True)
    stopped = FakeInterface()
    stopped._rxThread = SimpleNamespace(is_alive=lambda: False)

    assert interface_reader_stopped(running) is False
    assert interface_reader_stopped(stopped) is True


def test_service_reconnects_serially_after_reader_thread_stops(
    tmp_path, monkeypatch
):
    monkeypatch.setattr("meshpi.service.HEALTH_CHECK_INTERVAL_SECONDS", 0.01)
    monkeypatch.setattr("meshpi.service.RECONNECT_DELAYS", (0, 0, 0, 0))
    database = Database(tmp_path / "db.sqlite")
    database.initialize()
    attempts = []
    interfaces = []
    holder = {}

    def factory(profile):
        attempts.append(profile)
        if interfaces:
            assert interfaces[0].closed is True
            holder["service"]._stop.set()
        interface = FakeInterface()
        interface._rxThread = SimpleNamespace(is_alive=lambda: False)
        interfaces.append(interface)
        return interface

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
    assert all(interface.closed for interface in interfaces)
    assert value.status()["last_reconnect_reason"] == "Meshtastic-lesetråden stoppa"


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


def test_service_keeps_radio_connected_when_channel_sync_database_is_locked(
    tmp_path, monkeypatch
):
    monkeypatch.setattr("meshpi.service.HEALTH_CHECK_INTERVAL_SECONDS", 0.01)
    database = Database(tmp_path / "db.sqlite")
    database.initialize()
    interface = FakeInterface()
    interface._rxThread = SimpleNamespace(is_alive=lambda: True)
    sync_calls = []

    def locked_sync(_interface):
        sync_calls.append(True)
        raise sqlite3.OperationalError("database is locked")

    value = MeshtasticService(
        Settings(meshtastic_host="192.0.2.42", database_path=database.path),
        database,
        EventHub(),
        interface_factory=lambda _profile: interface,
    )
    monkeypatch.setattr(value, "_sync_channels", locked_sync)
    value.start()
    deadline = time.monotonic() + 2
    while len(sync_calls) < 2 and time.monotonic() < deadline:
        time.sleep(0.01)

    assert len(sync_calls) >= 2
    assert value.status()["state"] == "tilkopla"
    assert value._interface is interface
    assert interface.closed is False
    value.stop()


def test_service_rate_limits_identical_connection_errors(
    tmp_path, monkeypatch, caplog
):
    monkeypatch.setattr("meshpi.service.RECONNECT_DELAYS", (0, 0, 0, 0))
    database = Database(tmp_path / "db.sqlite")
    database.initialize()
    attempts = []
    holder = {}

    def factory(_profile):
        attempts.append(True)
        if len(attempts) >= 3:
            holder["service"]._stop.set()
        raise OSError("noden er fråkopla")

    value = MeshtasticService(
        Settings(meshtastic_host="192.0.2.42", database_path=database.path),
        database,
        EventHub(),
        interface_factory=factory,
    )
    holder["service"] = value
    caplog.set_level("DEBUG", logger="meshpi.service")
    value.start()
    deadline = time.monotonic() + 2
    while value._thread and value._thread.is_alive() and time.monotonic() < deadline:
        time.sleep(0.01)
    value.stop()

    errors = [
        record
        for record in caplog.records
        if record.getMessage() == "Meshtastic-feil: noden er fråkopla"
    ]
    repeated = [
        record
        for record in caplog.records
        if record.getMessage() == "Gjenteken Meshtastic-feil: noden er fråkopla"
    ]
    assert len(errors) == 1
    assert len(repeated) == 2


def test_ble_service_retries_with_searching_status(tmp_path, monkeypatch):
    monkeypatch.setattr("meshpi.service.RECONNECT_DELAYS", (0, 0, 0, 0))
    database = Database(tmp_path / "db.sqlite")
    database.initialize()
    attempts = []
    states = []
    holder = {}

    def factory(profile):
        attempts.append(profile)
        states.append(holder["service"].status()["state"])
        if len(attempts) == 1:
            raise OSError("utanfor rekkevidd")
        holder["service"]._stop.set()
        return FakeInterface()

    value = MeshtasticService(
        Settings(database_path=database.path),
        database,
        EventHub(),
        interface_factory=factory,
    )
    holder["service"] = value
    value.connect(target="ble://A1:B2:C3:D4:E5:F6")
    value.start()
    deadline = time.monotonic() + 2
    while value._thread and value._thread.is_alive() and time.monotonic() < deadline:
        time.sleep(0.01)
    value.stop()

    assert [profile.transport for profile in attempts] == ["ble", "ble"]
    assert states == ["søkjer", "søkjer"]


def test_service_switches_from_ble_to_tcp_and_closes_each_interface_once(
    tmp_path, monkeypatch
):
    monkeypatch.setattr("meshpi.service.RECONNECT_DELAYS", (0, 0, 0, 0))
    database = Database(tmp_path / "db.sqlite")
    database.initialize()
    attempts = []
    interfaces = []

    class CountingInterface(FakeInterface):
        def __init__(self):
            super().__init__()
            self.close_calls = 0

        def close(self):
            self.close_calls += 1
            super().close()

    def factory(profile):
        attempts.append(profile)
        interface = CountingInterface()
        interfaces.append(interface)
        return interface

    value = MeshtasticService(
        Settings(database_path=database.path),
        database,
        EventHub(),
        interface_factory=factory,
    )
    value.connect(target="ble://A1:B2:C3:D4:E5:F6")
    value.start()
    deadline = time.monotonic() + 2
    while len(attempts) < 1 and time.monotonic() < deadline:
        time.sleep(0.01)

    value.connect(target="192.0.2.42")
    deadline = time.monotonic() + 2
    while len(attempts) < 2 and time.monotonic() < deadline:
        time.sleep(0.01)
    value.stop()

    assert [profile.transport for profile in attempts[:2]] == ["ble", "tcp"]
    assert interfaces[0].close_calls == 1
    assert interfaces[1].close_calls == 1


def test_service_closes_ble_interface_created_during_shutdown(tmp_path):
    database = Database(tmp_path / "db.sqlite")
    database.initialize()
    factory_started = threading.Event()
    release_factory = threading.Event()

    class CountingInterface(FakeInterface):
        close_calls = 0

        def close(self):
            self.close_calls += 1
            super().close()

    interface = CountingInterface()

    def factory(_profile):
        factory_started.set()
        assert release_factory.wait(2)
        return interface

    value = MeshtasticService(
        Settings(database_path=database.path),
        database,
        EventHub(),
        interface_factory=factory,
    )
    value.connect(target="ble://A1:B2:C3:D4:E5:F6")
    value.start()
    assert factory_started.wait(2)

    stopper = threading.Thread(target=value.stop)
    stopper.start()
    release_factory.set()
    stopper.join(timeout=2)

    assert not stopper.is_alive()
    assert value._thread is not None
    assert not value._thread.is_alive()
    assert interface.close_calls == 1


def test_running_service_switches_from_tcp_to_serial_without_backoff(
    tmp_path, monkeypatch
):
    monkeypatch.setattr("meshpi.service.RECONNECT_DELAYS", (0, 0, 0, 0))
    monkeypatch.setattr("meshpi.service.discover_serial", lambda: [])
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
