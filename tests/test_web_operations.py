"""Operation reconciliation uses mocked IPC and never opens a radio connection."""

import json
import threading
from concurrent.futures import ThreadPoolExecutor
from http.client import HTTPConnection
from urllib.parse import urlsplit

import pytest

from meshpi.channels import dm_conversation_id
from meshpi.client import CLIError
from meshpi.config import Settings
from meshpi.web import MAX_BODY, WebApplication, WebServer
from meshpi.web import _PendingOperation as PendingOperation

OPERATION_ID = "test-operation-123456"
SEND = {
    "command": "send_dm", "node_id": "!bbbbbbbb", "text": "Mocka sending",
    "conversation": dm_conversation_id("!aaaaaaaa", "!bbbbbbbb", "global:Test:123"),
}


@pytest.fixture
def operation_web(monkeypatch, tmp_path):
    calls = []

    def fake_request(settings, payload, **kwargs):
        calls.append(payload)
        return {"ok": True, "data": {"message_id": 42}}

    monkeypatch.setattr("meshpi.web.request", fake_request)
    server = WebServer(Settings(database_path=tmp_path / "mock.db"), port=0)
    code = server.auth.issue_code()
    preauth = server.auth.new_session()
    server.test_session = server.auth.login_code(preauth.token, code)
    worker = threading.Thread(
        target=server.serve_forever, kwargs={"poll_interval": 0.01}, daemon=True,
    )
    worker.start()
    try:
        yield server, calls
    finally:
        server.shutdown()
        server.server_close()
        worker.join(timeout=2)


def post(server, payload, *, path="/meshpi/api/operation", headers=None, raw=None):
    request_headers = {
        "Host": urlsplit(server.auth_origin).netloc,
        "Origin": server.auth_origin,
        "Cookie": f"{server.cookie_name}={server.test_session.token}",
        "Content-Type": "application/json", "X-MeshPi-CSRF": server.test_session.csrf,
        "X-MeshPi-Operation": OPERATION_ID,
    }
    request_headers.update(headers or {})
    connection = HTTPConnection(*server.server_address, timeout=2)
    try:
        connection.request("POST", path, body=raw if raw is not None else json.dumps(payload),
                           headers=request_headers)
        response = connection.getresponse()
        return response.status, json.loads(response.read())
    finally:
        connection.close()


def test_completed_operation_returns_original_response_without_ipc_replay(operation_web):
    server, calls = operation_web
    status, result = post(server, SEND, path="/meshpi/api/request")
    for _ in range(2):
        assert post(server, {"operation_id": OPERATION_ID}) == (200, {
            "ok": True, "data": {"state": "completed", "status": status, "result": result},
        })
    assert calls == [SEND]


def test_unknown_operation_never_reaches_ipc(operation_web):
    server, calls = operation_web
    assert post(server, {"operation_id": OPERATION_ID}) == (
        200, {"ok": True, "data": {"state": "unknown"}},
    )
    assert calls == []


def test_pending_lookup_does_not_wait_for_ipc(operation_web, monkeypatch):
    server, calls = operation_web
    started, finish = threading.Event(), threading.Event()

    def delayed_request(*args, **kwargs):
        calls.append(SEND)
        started.set()
        assert finish.wait(timeout=5)
        return {"ok": True, "data": {"message_id": 42}}

    monkeypatch.setattr("meshpi.web.request", delayed_request)
    with ThreadPoolExecutor(max_workers=1) as pool:
        sending = pool.submit(server.app.dispatch, SEND, OPERATION_ID)
        try:
            assert started.wait(timeout=2)
            assert post(server, {"operation_id": OPERATION_ID}) == (
                200, {"ok": True, "data": {"state": "pending"}},
            )
        finally:
            finish.set()
        assert sending.result(timeout=2)[0] == 200
    assert post(server, {"operation_id": OPERATION_ID})[1]["data"]["state"] == "completed"
    assert calls == [SEND]


def test_queued_send_reports_pending_and_owns_its_identity_until_complete(monkeypatch):
    app = WebApplication(Settings())
    registered = threading.Event()
    calls = []

    def pending_operation(fingerprint):
        result = PendingOperation(fingerprint)
        registered.set()
        return result

    monkeypatch.setattr("meshpi.web._PendingOperation", pending_operation)
    monkeypatch.setattr("meshpi.web.request", lambda *a, **kw: calls.append(1) or {"ok": True})
    with ThreadPoolExecutor(max_workers=2) as pool:
        # Another IPC write holds the lock; this send must be discoverable before it starts.
        with app._write_lock:
            sending = pool.submit(app.dispatch, SEND, OPERATION_ID)
            assert registered.wait(timeout=2)
            assert app.operation({"operation_id": OPERATION_ID})["data"] == {"state": "pending"}
            assert calls == []
            assert app.dispatch(SEND | {"text": "Different"}, OPERATION_ID)[0] == 409
            duplicate = pool.submit(app.dispatch, SEND, OPERATION_ID)
            assert app.operation({"operation_id": OPERATION_ID})["data"] == {"state": "pending"}
        assert sending.result(timeout=2) == duplicate.result(timeout=2) == (200, {"ok": True})
    assert calls == [1]
    assert app.operation({"operation_id": OPERATION_ID})["data"]["state"] == "completed"


@pytest.mark.parametrize("failure", [CLIError("IPC-svaret manglar"), RuntimeError("private")])
def test_failed_operation_is_reconciled_without_retry(operation_web, monkeypatch, failure):
    server, calls = operation_web

    def failed_request(*args, **kwargs):
        calls.append(SEND)
        raise failure

    monkeypatch.setattr("meshpi.web.request", failed_request)
    status, result = post(server, SEND, path="/meshpi/api/request")
    assert status == 503
    assert "Kontroller historikken" in result["error"]
    assert "private" not in result["error"]
    assert post(server, {"operation_id": OPERATION_ID}) == (200, {
        "ok": True, "data": {"state": "completed", "status": status, "result": result},
    })
    assert post(server, SEND, path="/meshpi/api/request") == (status, result)
    assert calls == [SEND]


@pytest.mark.parametrize("headers", [
    {"Host": "evil.example"}, {"Origin": "https://evil.example"}, {"Origin": ""},
    {"X-MeshPi-CSRF": "wrong"}, {"X-MeshPi-CSRF": ""}, {"Sec-Fetch-Site": "cross-site"},
])
def test_operation_lookup_requires_same_request_protection(operation_web, headers):
    server, calls = operation_web
    assert post(server, {"operation_id": OPERATION_ID}, headers=headers)[0] == 403
    assert calls == []


@pytest.mark.parametrize("payload", [
    None, [], "text", {}, {"operation_id": None}, {"operation_id": 1234567890123456},
    {"operation_id": []}, {"operation_id": {}}, {"operation_id": True},
    {"operation_id": "short"}, {"operation_id": "a" * 81},
    {"operation_id": "!" * 16}, {"operation_id": "ø" * 16},
    {"operation_id": OPERATION_ID, "command": "send_dm"},
])
def test_invalid_operation_payload_is_rejected_without_ipc(operation_web, payload):
    server, calls = operation_web
    assert post(server, payload)[0] == 400
    assert calls == []


@pytest.mark.parametrize("raw, headers", [
    (b"{", {}), (b"\xff", {}), (b"{}", {"Content-Type": "text/plain"}),
    (b"x" * (MAX_BODY + 1), {}), (b"{}", {"Transfer-Encoding": "chunked"}),
    (b"[" * 1100 + b"]" * 1100, {}),
])
def test_operation_lookup_requires_bounded_json(operation_web, raw, headers):
    server, calls = operation_web
    assert post(server, {}, raw=raw, headers=headers)[0] == 400
    assert calls == []


def test_expired_operation_is_unknown_without_ipc(monkeypatch):
    now = [1000.0]
    calls = []
    monkeypatch.setattr("meshpi.web.time.monotonic", lambda: now[0])
    monkeypatch.setattr("meshpi.web.request", lambda *a, **kw: calls.append(1) or {"ok": True})
    app = WebApplication(Settings())
    app.dispatch(SEND, OPERATION_ID)
    now[0] += 3601
    assert app.operation({"operation_id": OPERATION_ID})["data"] == {"state": "unknown"}
    assert calls == [1]


def test_cache_stays_bounded_and_restart_does_not_guess_send_status(monkeypatch):
    calls = []
    monkeypatch.setattr("meshpi.web.request", lambda *a, **kw: calls.append(1) or {"ok": True})
    app = WebApplication(Settings())
    app.dispatch(SEND, OPERATION_ID)
    for index in range(1024):
        app.dispatch(SEND, f"test-operation-{index:06d}")
    assert len(app._results) == 1024
    assert app.operation({"operation_id": OPERATION_ID})["data"] == {"state": "unknown"}
    last_id = "test-operation-001023"
    assert app.operation({"operation_id": last_id})["data"]["state"] == "completed"
    restarted = WebApplication(Settings())
    assert restarted.operation({"operation_id": last_id})["data"] == {"state": "unknown"}
    assert len(calls) == 1025
