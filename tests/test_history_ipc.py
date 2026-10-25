from concurrent.futures import ThreadPoolExecutor
from types import SimpleNamespace

import pytest

from meshpi.config import Settings
from meshpi.events import EventHub
from meshpi.ipc import IPCApplication
from meshpi.web import validate_payload

GATEWAY = "!aaaaaaaa"
OTHER = "!bbbbbbbb"


class HistoryStore:
    def __init__(self):
        self.deleted = []
        self.policies = []

    def history_settings(self, gateway):
        return {"local_node_id": gateway, "retention_days": 30, "counts": {"messages": 3}}

    def set_history_policy(self, gateway, days):
        self.policies.append((gateway, days))
        return self.history_settings(gateway) | {"retention_days": days}

    def preview_history_cleanup(self, gateway, **options):
        return {"local_node_id": gateway, "counts": {"messages": 3},
                "_snapshot": {"messages": [[1], [2], [3]]}, **options}

    def cleanup_history(self, preview):
        self.deleted.append(preview)
        return preview["counts"]


@pytest.fixture
def history():
    store = HistoryStore()
    service = SimpleNamespace(gateway=GATEWAY)
    service.status = lambda: {"local_node_id": service.gateway}
    app = IPCApplication(Settings(), store, service, EventHub())
    return app, store, service


def preview(app):
    return app.dispatch({"command": "history_cleanup_preview",
                         "expected_local_node_id": GATEWAY,
                         "scope": "messages", "older_than_days": 7})["data"]


def cleanup_request(data):
    return {"command": "history_cleanup", "cleanup_token": data["cleanup_token"],
            "expected_local_node_id": GATEWAY, "confirmed": True}


def test_preview_is_read_only_and_keeps_snapshot_private(history):
    app, store, _ = history
    result = preview(app)
    assert result["counts"] == {"messages": 3}
    assert "_snapshot" not in result
    assert result["expires_in"] == 300
    assert not store.deleted
    app.dispatch(cleanup_request(result))
    assert store.deleted[0]["_snapshot"] == {"messages": [[1], [2], [3]]}
    with pytest.raises(ValueError):
        app.dispatch(cleanup_request(result))


def test_cleanup_confirmation_expiry_and_gateway_guards(history, monkeypatch):
    app, store, service = history
    result = preview(app)
    request = cleanup_request(result)
    with pytest.raises(ValueError):
        app.dispatch(request | {"confirmed": False})
    service.gateway = OTHER
    with pytest.raises(ValueError):
        app.dispatch(request)
    service.gateway = GATEWAY
    monkeypatch.setattr("meshpi.ipc.time.monotonic", lambda: float("inf"))
    with pytest.raises(ValueError):
        app.dispatch(request)
    assert not store.deleted


def test_cleanup_token_consumed_once_with_concurrent_requests(history):
    app, store, _ = history
    request = cleanup_request(preview(app))

    def run():
        try:
            return app.dispatch(request)["ok"]
        except ValueError:
            return False

    with ThreadPoolExecutor(max_workers=4) as pool:
        assert list(pool.map(lambda _: run(), range(4))).count(True) == 1
    assert len(store.deleted) == 1


@pytest.mark.parametrize("days", [True, -1, 36501, 1.5, "30", None])
def test_policy_rejects_invalid_days_without_writing(history, days):
    app, store, _ = history
    with pytest.raises(ValueError):
        app.dispatch({"command": "history_settings_set", "expected_local_node_id": GATEWAY,
                      "retention_days": days, "confirmed": True})
    assert not store.policies


def test_policy_requires_consent_and_retains_gateway_scope(history):
    app, store, _ = history
    request = {"command": "history_settings_set", "expected_local_node_id": GATEWAY,
               "retention_days": 0}
    with pytest.raises(ValueError):
        app.dispatch(request)
    app.dispatch(request | {"confirmed": True})
    assert store.policies == [(GATEWAY, 0)]


def test_web_history_allowlist_validates_types_scope_and_confirmation():
    valid = {"command": "history_cleanup_preview", "expected_local_node_id": GATEWAY,
             "scope": "history", "older_than_days": 30}
    assert validate_payload(valid) == valid
    for change in ({"scope": "database"}, {"older_than_days": True},
                   {"expected_local_node_id": "../../file"}, {"path": "/tmp/database"}):
        with pytest.raises(ValueError):
            validate_payload(valid | change)
    with pytest.raises(ValueError):
        validate_payload({"command": "history_cleanup", "expected_local_node_id": GATEWAY,
                          "cleanup_token": "a" * 32, "confirmed": False})


def test_no_gateway_cannot_read_or_create_history_settings(history):
    app, store, service = history
    service.gateway = None
    with pytest.raises(ValueError):
        app.dispatch({"command": "history_settings"})
    assert not store.policies


def test_preview_cache_evicts_old_tokens_by_rows_and_count(history, monkeypatch):
    app, store, _ = history
    monkeypatch.setattr("meshpi.ipc.MAX_HISTORY_SNAPSHOT_ROWS", 6)
    first, second, third = preview(app), preview(app), preview(app)
    assert len(app._history_previews) == 2
    with pytest.raises(ValueError):
        app.dispatch(cleanup_request(first))
    app.dispatch(cleanup_request(second))
    app.dispatch(cleanup_request(third))
    monkeypatch.setattr("meshpi.ipc.MAX_HISTORY_PREVIEWS", 1)
    first, second = preview(app), preview(app)
    with pytest.raises(ValueError):
        app.dispatch(cleanup_request(first))
    assert len(app._history_previews) == 1
    monkeypatch.setattr("meshpi.ipc.MAX_HISTORY_SNAPSHOT_ROWS", 2)
    with pytest.raises(ValueError, match="mykje historikk"):
        preview(app)


def test_http_cleanup_and_cli_export_use_same_physical_gateway(tmp_path):
    import json
    import threading
    from dataclasses import replace

    from meshpi.client import open_export
    from meshpi.gateway_database import GatewayDatabase
    from meshpi.ipc import IPCServer
    from meshpi.web import WebServer
    from tests.test_gateway_database import message
    from tests.test_web import authenticate, http

    settings = Settings(database_path=tmp_path / "history.db", ipc_transport="tcp",
                        ipc_port=0, ipc_token="a" * 64)
    database = GatewayDatabase(settings.database_path)
    database.insert_message(message(GATEWAY))
    database.insert_message(message(OTHER))
    service = SimpleNamespace(status=lambda: {"local_node_id": GATEWAY})
    ipc = IPCServer(settings, IPCApplication(settings, database, service, EventHub()))
    settings = replace(settings, ipc_port=ipc.address[1])
    worker = threading.Thread(target=ipc.serve_forever, daemon=True)
    worker.start()
    server = WebServer(settings, port=0, public_url="https://test.example.ts.net")
    authenticate(server)
    web_worker = threading.Thread(target=server.serve_forever, daemon=True)
    web_worker.start()
    try:
        sock, stream = open_export(settings)
        try:
            records = [json.loads(line) for line in stream]
        finally:
            stream.close()
            sock.close()
        assert records[0]["local_node_id"] == GATEWAY
        assert records[-1]["record"] == "complete"
        exported = [r["data"] for r in records if r.get("table") == "messages"]
        assert len(exported) == 1 and exported[0]["local_node_id"] == GATEWAY
        request = {"command": "history_cleanup_preview", "expected_local_node_id": GATEWAY,
                   "scope": "history", "older_than_days": 0}
        status, _, body = http(server, payload=request)
        assert status == 200
        planned = json.loads(body)["data"]
        assert planned["counts"]["messages"] == 1
        assert not any(key.startswith("_") for key in planned)
        request = cleanup_request(planned)
        assert http(server, payload=request, headers={"X-MeshPi-CSRF": "wrong"})[0] == 403
        assert database.history_settings(GATEWAY)["counts"]["messages"] == 1
        assert http(server, payload=request, headers={"Cookie": ""})[0] == 401
        assert http(server, payload=request)[0] == 200
        assert http(server, payload=request)[0] == 200  # Same operation returns its cached result.
        assert database.history_settings(GATEWAY)["counts"]["messages"] == 0
        assert database.history_settings(OTHER)["counts"]["messages"] == 1
    finally:
        server.shutdown()
        server.server_close()
        ipc.shutdown()
        worker.join(timeout=2)
        web_worker.join(timeout=2)


def test_real_service_routes_mock_radio_events_through_gateway_files(tmp_path):
    from meshpi.gateway_database import GatewayDatabase
    from meshpi.models import MessageStatus
    from meshpi.service import MeshtasticService
    from tests.test_service import FakeInterface

    database = GatewayDatabase(tmp_path / "history.db")
    service = MeshtasticService(
        Settings(database_path=database.path, meshtastic_host="192.0.2.42"), database, EventHub(),
    )
    peer = "!cccccccc"
    for gateway in (GATEWAY, OTHER):
        interface = FakeInterface()
        service._interface = interface
        service._local_node_id = gateway
        service._sync_channels(interface)
        service._set_status("tilkopla")
        service._on_receive({"id": 501, "fromId": peer, "rxTime": 1_700_000_000,
                             "decoded": {"portnum": "TELEMETRY_APP", "telemetry": {
                                 "deviceMetrics": {"batteryLevel": 82}}}}, interface)
        service.send_dm(peer, "Berre mocka radiosending")
        interface.calls[0][1]["onResponse"]({
            "fromId": peer, "decoded": {"routing": {"errorReason": "NONE"}},
        })
        assert database.list_messages("dm", peer, local_node_id=gateway)[0]["status"] == str(
            MessageStatus.DELIVERED,
        )
        assert len(database.list_telemetry(peer, local_node_id=gateway)) == 1
        assert database.get_node(peer, local_node_id=gateway) is not None
        assert database.gateway_path(gateway).is_file()
