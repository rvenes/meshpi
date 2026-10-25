"""HTTP authentication boundaries with local mock IPC and software passkeys."""

import json
import threading
from http.client import HTTPConnection
from http.cookies import SimpleCookie
from urllib.parse import urlsplit

import pytest
from test_web_auth import SoftwareAuthenticator

from meshpi.config import Settings
from meshpi.web import WebServer

PUBLIC_URL = "https://meshpi.example.test"


class Browser:
    def __init__(self, server):
        self.server = server
        self.cookie = ""
        self.csrf = ""

    def headers(self):
        return {
            "Host": urlsplit(self.server.auth_origin).netloc,
            "Origin": self.server.auth_origin,
            "Content-Type": "application/json",
            "Cookie": self.cookie,
            "X-MeshPi-CSRF": self.csrf,
        }

    def request(self, path, payload=None, *, headers=None, method=None):
        connection = HTTPConnection(*self.server.server_address, timeout=5)
        try:
            connection.request(
                method or ("GET" if payload is None else "POST"), "/meshpi/api/" + path,
                body=None if payload is None else json.dumps(payload),
                headers=self.headers() | (headers or {}),
            )
            response = connection.getresponse()
            fields, data = dict(response.getheaders()), json.loads(response.read())
            if "Set-Cookie" in fields:
                cookie = SimpleCookie(fields["Set-Cookie"])
                item = cookie[self.server.cookie_name]
                self.cookie = f"{item.key}={item.value}"
            if isinstance(data.get("data"), dict) and "csrf" in data["data"]:
                self.csrf = data["data"]["csrf"]
            return response.status, fields, data
        finally:
            connection.close()

    def login(self):
        code = self.server.auth.issue_code()
        assert self.request("session")[0] == 200
        assert self.request("auth/code", {"code": code})[0] == 200


@pytest.fixture
def servers(tmp_path, monkeypatch):
    running, calls = [], []

    def request(_settings, payload, **_kwargs):
        calls.append(payload)
        return {"ok": True, "data": {"state": "frakopla"}}

    def forbidden(*_args):
        raise AssertionError("Uautorisert HTTP opna IPC-straum")

    monkeypatch.setattr("meshpi.web.request", request)
    monkeypatch.setattr("meshpi.web.open_export", forbidden)
    monkeypatch.setattr("meshpi.web.open_watch", forbidden)

    def create(public_url=PUBLIC_URL):
        settings = Settings(database_path=tmp_path / str(len(running)) / "mock.db")
        server = WebServer(settings, port=0, public_url=public_url)
        worker = threading.Thread(
            target=lambda: server.serve_forever(poll_interval=0.01), daemon=True,
        )
        worker.start()
        running.append((server, worker))
        return server, Browser(server), calls

    yield create
    for server, worker in running:
        server.shutdown()
        server.server_close()
        worker.join(timeout=2)


@pytest.mark.parametrize("public_url", [PUBLIC_URL, ""])
def test_preauth_cookie_and_health_reveal_no_private_data(servers, public_url):
    server, browser, calls = servers(public_url)
    status, headers, result = browser.request("session")
    assert status == 200
    assert result["data"]["authenticated"] is False
    assert "hostname" not in result["data"]
    assert result["data"]["canonical_url"] == server.auth_origin + "/meshpi/"
    assert result["data"]["passkeys_available"] is bool(public_url)
    cookie = SimpleCookie(headers["Set-Cookie"])[server.cookie_name]
    assert cookie["httponly"] and cookie["samesite"] == "Strict"
    assert cookie["path"] == "/meshpi/"
    assert bool(cookie["secure"]) is bool(public_url)
    assert headers["Cache-Control"] == "no-store"
    assert browser.request("health")[2] == {"ok": True, "service": "meshpi-web"}
    assert calls == []


@pytest.mark.parametrize("path, payload", [
    ("request", {"command": "status"}),
    ("request", {"command": "nodes"}),
    ("request", {"command": "send_public", "text": "never-sent"}),
    ("operation", {"operation_id": "known-operation-123456"}),
    ("export", {}), ("events", None), ("auth/keys", None),
])
def test_anonymous_and_preauth_sessions_cannot_reach_data_or_ipc(servers, path, payload):
    _, browser, calls = servers()
    assert browser.request(path, payload)[0] == 401
    assert browser.request("session")[0] == 200
    assert browser.request(path, payload)[0] == 401
    assert calls == []


@pytest.mark.parametrize("bad_headers", [
    {"X-MeshPi-CSRF": ""}, {"X-MeshPi-CSRF": "wrong"},
    {"Origin": "https://evil.test"}, {"Origin": "null"}, {"Origin": ""},
    {"Host": "evil.test"}, {"Sec-Fetch-Site": "cross-site"},
])
def test_code_login_requires_origin_and_csrf_before_consuming_code(servers, bad_headers):
    server, browser, calls = servers()
    code = server.auth.issue_code()
    browser.request("session")
    assert browser.request("auth/code", {"code": code}, headers=bad_headers)[0] == 403
    assert browser.request("auth/code", {"code": code})[2]["data"]["authenticated"] is True
    assert calls == []


def test_loopback_proxy_and_forwarded_identity_cannot_bypass_canonical_auth(servers):
    server, browser, calls = servers()
    browser.login()
    headers = {
        "Host": f"127.0.0.1:{server.server_address[1]}",
        "Origin": server.auth_origin,
        "X-Forwarded-Host": urlsplit(server.auth_origin).netloc,
        "X-Forwarded-Proto": "https",
        "Tailscale-User-Login": "owner@example.test",
    }
    assert browser.request("request", {"command": "status"}, headers=headers)[0] == 401
    assert browser.request("events", headers=headers)[0] == 401
    assert browser.request("auth/keys", headers=headers)[0] == 401
    assert calls == []


@pytest.mark.parametrize("public_url", [PUBLIC_URL, ""])
def test_code_rotates_cookie_and_logout_revokes_it(servers, public_url):
    server, browser, calls = servers(public_url)
    code = server.auth.issue_code()
    browser.request("session")
    preauth_cookie, preauth_csrf = browser.cookie, browser.csrf
    status, _, result = browser.request("auth/code", {"code": code})
    assert status == 200 and result["data"]["authenticated"] is True
    assert browser.cookie != preauth_cookie and browser.csrf != preauth_csrf
    owner_cookie, owner_csrf = browser.cookie, browser.csrf
    assert browser.request("request", {"command": "status"})[0] == 200
    assert calls == [{"command": "status"}]
    assert browser.request("request", {"command": "status"}, headers={
        "Cookie": preauth_cookie, "X-MeshPi-CSRF": preauth_csrf,
    })[0] == 401
    status, _, result = browser.request("auth/logout", {})
    assert status == 200 and result["data"]["authenticated"] is False
    assert browser.cookie != owner_cookie and browser.csrf != owner_csrf
    assert browser.request("request", {"command": "status"}, headers={
        "Cookie": owner_cookie, "X-MeshPi-CSRF": owner_csrf,
    })[0] == 401
    assert browser.request("auth/code", {"code": code})[0] == 400
    assert calls == [{"command": "status"}]


def test_local_recovery_revokes_other_browser_and_does_not_reuse_code(servers):
    server, browser, calls = servers()
    browser.login()
    old_cookie, old_csrf = browser.cookie, browser.csrf
    recovery = server.auth.issue_code()
    assert browser.request("request", {"command": "status"})[0] == 401
    browser.request("session")
    assert browser.request("auth/code", {"code": recovery})[0] == 200
    assert browser.request("request", {"command": "status"}, headers={
        "Cookie": old_cookie, "X-MeshPi-CSRF": old_csrf,
    })[0] == 401
    second = Browser(server)
    second.request("session")
    assert second.request("auth/code", {"code": recovery})[0] == 400
    assert calls == []


def test_http_registration_assertion_and_key_revocation_with_software_authenticator(servers):
    server, browser, calls = servers()
    browser.login()
    authenticator = SoftwareAuthenticator()
    status, _, result = browser.request("auth/register/begin", {})
    assert status == 200
    begin = result["data"]
    credential = authenticator.register(begin, origin=server.auth_origin)
    status, _, result = browser.request("auth/register/finish", {
        "challenge_id": begin["challenge_id"], "credential": credential, "name": "Test-PC",
    })
    assert status == 200
    keys = browser.request("auth/keys")[2]["data"]["keys"]
    assert len(keys) == 1 and keys[0]["name"] == "Test-PC"
    browser.request("auth/logout", {})
    begin = browser.request("auth/login/begin", {})[2]["data"]
    assertion = authenticator.authenticate(begin, origin=server.auth_origin)
    status, _, result = browser.request("auth/login/finish", {
        "challenge_id": begin["challenge_id"], "credential": assertion,
    })
    assert status == 200 and result["data"]["authenticated"] is True
    assert browser.request("auth/remove", {"id": keys[0]["id"]})[0] == 200
    assert browser.request("auth/keys")[0] == 401
    assert browser.request("request", {"command": "status"})[0] == 401
    assert calls == []


def test_localhost_uses_code_login_without_webauthn(servers):
    _, browser, calls = servers("")
    browser.login()
    assert browser.request("auth/register/begin", {})[0] == 400
    browser.request("auth/logout", {})
    assert browser.request("auth/login/begin", {})[0] == 400
    assert calls == []


def test_event_stream_stops_before_delivering_events_after_recovery(servers, monkeypatch):
    server, browser, calls = servers()
    browser.login()
    monkeypatch.setattr(server.app, "start_watch", lambda: None)
    connection = HTTPConnection(*server.server_address, timeout=5)
    try:
        connection.request("GET", "/meshpi/api/events", headers=browser.headers())
        response = connection.getresponse()
        assert response.status == 200
        assert b'"refresh"' in response.readline()
        assert response.readline() == b"\n"
        server.auth.issue_code()
        server.app.events.publish({"type": "private-new-message"})
        assert b"private-new-message" not in response.read()
        assert calls == []
    finally:
        connection.close()
