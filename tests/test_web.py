import json
import struct
import threading
from concurrent.futures import ThreadPoolExecutor
from html.parser import HTMLParser
from http.client import HTTPConnection
from urllib.parse import urljoin, urlsplit

import pytest

from meshpi.channels import dm_conversation_id, public_conversation_id
from meshpi.cli import main
from meshpi.client import CLIError
from meshpi.config import Settings
from meshpi.database import Database
from meshpi.events import EventHub
from meshpi.ipc import IPCApplication, IPCServer
from meshpi.web import MAX_BODY, WebApplication, WebServer, run_web

LOCAL = "!aaaaaaaa"
PEER = "!bbbbbbbb"
CHANNEL = "global:Test:123"
PUBLIC_ROUTE = public_conversation_id(LOCAL, CHANNEL)
DM_ROUTE = dm_conversation_id(LOCAL, PEER, CHANNEL)


@pytest.fixture
def web(monkeypatch, tmp_path):
    calls = []

    def fake_request(settings, payload, **kwargs):
        calls.append(dict(payload))
        return {"ok": True, "data": {"command": payload["command"]}}

    monkeypatch.setattr("meshpi.web.request", fake_request)
    server = WebServer(Settings(ipc_token="private-ipc-token",
                                database_path=tmp_path / "mock.db"), port=0,
                       public_url="https://mesh-test.example.ts.net")
    authenticate(server)
    worker = threading.Thread(
        target=server.serve_forever, kwargs={"poll_interval": 0.01}, daemon=True,
    )
    worker.start()
    yield server, calls
    server.shutdown()
    server.server_close()
    worker.join(timeout=2)


def authenticate(server):
    code = server.auth.issue_code()
    preauth = server.auth.new_session()
    server.test_session = server.auth.login_code(preauth.token, code)


def http(server, path="/meshpi/api/request", payload=None, headers=None, method=None):
    connection = HTTPConnection(*server.server_address, timeout=5)
    default_headers = {
        "Host": urlsplit(server.auth_origin).netloc,
        "Origin": server.auth_origin,
        "Cookie": f"{server.cookie_name}={server.test_session.token}",
        "Content-Type": "application/json",
        "X-MeshPi-CSRF": server.test_session.csrf,
        "X-MeshPi-Operation": "test-operation-123456",
    }
    default_headers.update(headers or {})
    body = None if payload is None else json.dumps(payload).encode()
    connection.request(method or ("GET" if payload is None else "POST"), path,
                       body=body, headers=default_headers)
    response = connection.getresponse()
    data = response.read()
    result = response.status, dict(response.getheaders()), data
    connection.close()
    return result


def test_authenticated_tailnet_client_can_read_and_send(web):
    server, calls = web
    status, headers, body = http(server, payload={"command": "status"})
    assert status == 200
    assert json.loads(body)["data"]["command"] == "status"
    assert "private-ipc-token" not in body.decode()
    assert headers["Cache-Control"] == "no-store"
    assert http(server, payload={"command": "send_public", "conversation": PUBLIC_ROUTE,
                                 "text": "Mocka sending"})[0] == 200
    assert calls[-1]["conversation"] == PUBLIC_ROUTE


def test_session_identifies_web_host_without_calling_daemon(web, monkeypatch):
    server, calls = web
    monkeypatch.setattr("meshpi.web.socket.gethostname", lambda: "test-web-host")
    status, headers, body = http(server, "/meshpi/api/session")
    assert status == 200
    data = json.loads(body)["data"]
    assert data["csrf"] == server.test_session.csrf
    assert data["hostname"] == "test-web-host"
    assert data["authenticated"] is True
    assert data["passkeys_available"] is True
    assert data["canonical_url"] == server.auth_origin + "/meshpi/"
    assert headers["Cache-Control"] == "no-store"
    assert calls == []


@pytest.mark.parametrize("headers", [
    {"Origin": "https://evil.example"},
    {"Origin": "null"},
    {"X-MeshPi-CSRF": "wrong"},
    {"X-MeshPi-CSRF": ""},
    {"Host": "evil.example"},
    {"Sec-Fetch-Site": "cross-site"},
])
def test_untrusted_requests_never_reach_ipc(web, headers):
    server, calls = web
    assert http(server, payload={"command": "status"}, headers=headers)[0] == 403
    assert calls == []


def test_post_requires_origin_even_with_csrf(web):
    server, calls = web
    assert http(server, payload={"command": "status"}, headers={"Origin": ""})[0] == 403
    assert calls == []


@pytest.mark.parametrize("payload", [
    {"command": "shutdown"},
    {"command": "delete_messages", "scope": "all"},
    {"command": "status", "token": "replacement"},
    {"command": "status", "language": "en"},
    {"command": "nodes", "sort": "arbitrary-sql"},
    {"command": "messages", "conversation": PUBLIC_ROUTE, "limit": 999999},
    {"command": "messages", "conversation": PUBLIC_ROUTE, "limit": True},
    {"command": "messages", "conversation": PUBLIC_ROUTE, "mark_read": "false"},
    {"command": "send_public", "text": "missing route"},
    {"command": "send_public", "conversation": "public", "text": "wrong route"},
    {"command": "send_dm", "conversation": DM_ROUTE, "node_id": "!cccccccc", "text": "x"},
    {"command": "node_action", "node_id": PEER, "action": "reboot"},
    {"command": "connect", "target": "test", "profile_id": "test"},
    {"command": "node_overview", "node_id": "bbbb"},
    [],
    None,
])
def test_invalid_or_unimplemented_commands_never_reach_ipc(web, payload):
    server, calls = web
    assert http(server, payload=payload, method="POST")[0] == 400
    assert calls == []


def test_duplicate_sends_have_one_ipc_call_even_when_concurrent(web):
    server, calls = web
    payload = {"command": "send_dm", "node_id": PEER, "conversation": DM_ROUTE, "text": "x"}
    with ThreadPoolExecutor(max_workers=4) as pool:
        statuses = list(pool.map(lambda _: http(server, payload=payload)[0], range(4)))
    assert statuses == [200] * 4
    assert len(calls) == 1
    assert http(server, payload=payload | {"text": "different"})[0] == 409
    assert len(calls) == 1


def test_write_requires_operation_id(web):
    server, calls = web
    assert http(server, payload={"command": "send_public", "conversation": PUBLIC_ROUTE,
                                 "text": "x"}, headers={"X-MeshPi-Operation": ""})[0] == 400
    assert calls == []


def test_uncertain_ipc_failure_is_not_sent_again(web, monkeypatch):
    server, _ = web
    attempts = []

    def failed(*args, **kwargs):
        attempts.append(1)
        raise CLIError("IPC-svaret manglar")

    monkeypatch.setattr("meshpi.web.request", failed)
    payload = {"command": "send_dm", "conversation": DM_ROUTE, "node_id": PEER, "text": "x"}
    first = http(server, payload=payload)
    assert first[0] == 503
    assert "Kontroller historikken" in first[2].decode()
    assert http(server, payload=payload)[2] == first[2]
    assert len(attempts) == 1


def test_body_limit_is_checked_before_ipc(web):
    server, calls = web
    assert http(server, payload={"command": "status", "large": "x" * MAX_BODY})[0] == 400
    assert calls == []


def test_proxy_prefix_and_stripped_paths_serve_only_packaged_assets(web):
    server, calls = web
    for path in ("/meshpi/", "/"):
        status, headers, body = http(server, path)
        assert status == 200
        assert b'lang="nn"' in body
        assert "frame-ancestors 'none'" in headers["Content-Security-Policy"]
    status, headers, _ = http(server, "/meshpi")
    assert status == 308
    assert headers["Location"] == "/meshpi/"
    for path in ("/meshpi/../config.py", "/meshpi/%2e%2e/.env", "/meshpi/.env"):
        assert http(server, path)[0] == 404
    assert calls == []


def test_server_is_loopback_only_and_origin_is_explicit(web):
    server, _ = web
    assert server.server_address[0] == "127.0.0.1"
    for url in ("http://example.ts.net", "https://user:secret@example.ts.net",
                "https://example.ts.net/meshpi", "https://example.ts.net?x=1"):
        with pytest.raises(ValueError):
            WebServer(Settings(), port=0, public_url=url)


@pytest.mark.parametrize("address", [
    "0.0.0.0", "::", "::1", "8.8.8.8", "100.64.0.1", "169.254.1.2",
    "192.0.2.1", "224.0.0.1", "localhost", "192.168.1.2:8780", "bad address",
    "192.168.1.2", "10.1.2.3", "172.16.1.2",
])
def test_web_rejects_every_non_loopback_bind_address(address):
    with pytest.raises(ValueError):
        WebServer(Settings(), port=0, bind_address=address)


def test_secondary_loopback_listener_still_requires_canonical_login(tmp_path):
    settings = Settings(database_path=tmp_path / "mock.db")
    with WebServer(settings, port=0, bind_address="127.0.0.2") as server:
        authenticate(server)
        worker = threading.Thread(
            target=server.serve_forever, kwargs={"poll_interval": 0.01}, daemon=True,
        )
        worker.start()
        try:
            headers = {"Host": f"127.0.0.2:{server.server_address[1]}", "Cookie": "",
                       "Origin": f"http://127.0.0.2:{server.server_address[1]}"}
            assert http(server, "/meshpi/", headers=headers)[0] == 200
            assert http(server, "/meshpi/api/events", headers=headers)[0] == 401
        finally:
            server.shutdown()
            worker.join(timeout=2)


def test_run_web_starts_without_daemon_and_closes_listener(tmp_path, monkeypatch):
    servers = []

    def forbidden(*_args, **_kwargs):
        raise AssertionError("Weboppstart kravde daemonkontakt")

    monkeypatch.setattr("meshpi.web.request", forbidden)

    def serve(server):
        servers.append(server)
        assert server.server_address[0] == "127.0.0.1"
        raise KeyboardInterrupt

    monkeypatch.setattr(WebServer, "serve_forever", serve)
    run_web(Settings(database_path=tmp_path / "mock.db"), port=0,
            public_url="", base_path="/meshpi")
    assert len(servers) == 1
    assert servers[0].socket.fileno() == -1
    assert servers[0].app.stopping.is_set()


def test_run_web_bind_failure_does_not_require_daemon(tmp_path, monkeypatch):
    def forbidden(*_args, **_kwargs):
        raise AssertionError("Weboppstart kravde daemonkontakt")

    monkeypatch.setattr("meshpi.web.request", forbidden)
    settings = Settings(database_path=tmp_path / "mock.db")
    with (
        WebServer(settings, port=0) as occupied,
        pytest.raises(CLIError, match="Kunne ikkje starte weblyttaren"),
    ):
        run_web(settings, port=occupied.server_address[1], public_url="", base_path="/meshpi")


@pytest.mark.parametrize("base_path", ["/meshpi", "", "/apps/meshpi"])
def test_proxy_stripped_html_uses_mount_urls_even_without_trailing_slash(web, base_path):
    server, calls = web
    server.base_path = base_path

    class Assets(HTMLParser):
        def __init__(self):
            super().__init__()
            self.urls = []
            self.base_path = None

        def handle_starttag(self, tag, attrs):
            attrs = dict(attrs)
            if tag == "html":
                self.base_path = attrs.get("data-base-path")
            if tag == "link" and attrs.get("rel") == "stylesheet":
                self.urls.append(attrs["href"])
            if tag == "script":
                self.urls.append(attrs["src"])

    # Tailscale forwards both /meshpi and /meshpi/ as / to the backend.
    status, _, body = http(server, "/")
    assert status == 200
    assert b"__MESHPI_BASE_PATH__" not in body
    assets = Assets()
    assets.feed(body.decode())
    assert assets.base_path == base_path
    assert assets.urls == [
        base_path + "/theme.js", base_path + "/style.css",
        base_path + "/traceroute.js", base_path + "/auth.js", base_path + "/app.js",
        base_path + "/install.js",
    ]
    origin = "https://mesh-test.example.ts.net"
    for suffix in ("", "/"):
        document_url = origin + base_path + suffix
        for asset in assets.urls:
            assert urljoin(document_url, asset) == origin + asset
            assert http(server, asset)[0] == 200
    assert calls == []


@pytest.mark.parametrize("base_path", ["/meshpi", "", "/apps/meshpi"])
def test_installable_app_resources_respect_mount_and_security(web, base_path):
    server, calls = web
    server.base_path = base_path
    status, headers, body = http(server, base_path + "/manifest.webmanifest")
    assert status == 200
    assert headers["Content-Type"] == "application/manifest+json"
    manifest = json.loads(body)
    assert manifest["id"] == manifest["start_url"] == manifest["scope"] == base_path + "/"
    assert manifest["name"] == "MeshPi"
    assert manifest["display"] == "standalone"
    assert manifest["lang"] == "nn"
    assert b"__MESHPI_BASE_PATH__" not in body
    for icon in manifest["icons"]:
        status, headers, content = http(server, icon["src"])
        assert status == 200
        assert headers["Content-Type"] == "image/png"
        assert content[:8] == b"\x89PNG\r\n\x1a\n"
        assert "x".join(map(str, struct.unpack(">II", content[16:24]))) == icon["sizes"]
        assert set(icon["purpose"].split()) == {"any", "maskable"}
    _, headers, content = http(server, base_path + "/apple-touch-icon.png")
    assert struct.unpack(">II", content[16:24]) == (180, 180)
    for resource, mime in (
        ("sw.js", "text/javascript; charset=utf-8"),
        ("install.js", "text/javascript; charset=utf-8"),
        ("icon.svg", "image/svg+xml"),
    ):
        for prefix in (base_path, ""):
            status, headers, _ = http(server, prefix + "/" + resource)
            assert status == 200
            assert headers["Content-Type"] == mime
            assert headers["Cache-Control"] == "no-store"
            assert headers["X-Content-Type-Options"] == "nosniff"
            assert "Service-Worker-Allowed" not in headers
        assert http(server, base_path + "/" + resource,
                    headers={"Origin": "https://untrusted.example"})[0] == 403
    assert calls == []


def test_many_browsers_share_one_watch_thread(monkeypatch):
    entered = threading.Event()
    finished = threading.Event()
    calls = []

    class FakeSocket:
        def shutdown(self, _how):
            finished.set()

        def close(self):
            pass

    class FakeStream:
        def __iter__(self):
            entered.set()
            finished.wait(3)
            return iter([])

        def close(self):
            pass

    def fake_watch(_settings):
        calls.append(1)
        return FakeSocket(), FakeStream()

    monkeypatch.setattr("meshpi.web.open_watch", fake_watch)
    app = WebApplication(Settings())
    try:
        for _ in range(20):
            app.start_watch()
        assert entered.wait(2)
        assert calls == [1]
    finally:
        app.close()


@pytest.mark.parametrize("mode, owned", [("always", False), ("session", False), ("session", True)])
def test_web_cli_stops_only_session_daemon_it_started(tmp_path, monkeypatch, mode, owned):
    from meshpi.lifecycle import DaemonHandle

    monkeypatch.setenv("MESHPI_LANGUAGE_FILE", str(tmp_path / "language.json"))
    settings = Settings(database_path=tmp_path / "mock.db", background_mode=mode)
    monkeypatch.setattr("meshpi.cli.Settings.load", lambda _: settings)
    starts, stops, calls = [], [], []

    def start(*_args):
        starts.append(True)
        return DaemonHandle(owned=owned)

    monkeypatch.setattr("meshpi.cli.start_session_daemon", start)
    monkeypatch.setattr("meshpi.cli.stop_daemon", lambda *_: stops.append(True))
    monkeypatch.setattr("meshpi.web.run_web", lambda *a, **kw: calls.append(kw))
    main(["web", "--port", "8780", "--base-path", "/meshpi"])
    assert starts == ([True] if mode == "session" else [])
    assert stops == ([True] if mode == "session" and owned else [])
    assert calls == [{"port": 8780, "public_url": "", "base_path": "/meshpi"}]


def test_web_cli_rejects_direct_lan_listener(tmp_path, monkeypatch):
    monkeypatch.setenv("MESHPI_LANGUAGE_FILE", str(tmp_path / "language.json"))
    with pytest.raises(SystemExit) as error:
        main(["web", "--lan-address", "192.168.1.10"])
    assert error.value.code == 2


def test_http_send_preserves_route_through_real_ipc_with_mocked_radio(tmp_path):
    class Radio:
        def __init__(self):
            self.sent = []

        def status(self):
            return {"state": "tilkopla", "local_node_id": LOCAL}

        def send_dm(self, node_id, text, **options):
            self.sent.append((node_id, text, options))
            return {"status": "sendt"}

    settings = Settings(ipc_transport="tcp", ipc_port=0, ipc_token="a" * 64,
                        database_path=tmp_path / "mock.db")
    database = Database(settings.database_path)
    database.initialize()
    radio = Radio()
    ipc = IPCServer(settings, IPCApplication(settings, database, radio, EventHub()))
    from dataclasses import replace

    settings = replace(settings, ipc_port=ipc.address[1])
    ipc_worker = threading.Thread(target=ipc.serve_forever, daemon=True)
    ipc_worker.start()
    server = WebServer(settings, port=0, public_url="https://mesh-test.example.ts.net")
    authenticate(server)
    worker = threading.Thread(
        target=server.serve_forever, kwargs={"poll_interval": 0.01}, daemon=True,
    )
    worker.start()
    try:
        status, _, data = http(server, payload={"command": "send_dm", "node_id": PEER,
                                               "conversation": DM_ROUTE, "text": "Mocka test"})
        assert status == 200
        assert json.loads(data)["data"]["status"] == "sendt"
        assert radio.sent == [(PEER, "Mocka test", {"conversation": DM_ROUTE})]
    finally:
        server.shutdown()
        server.server_close()
        ipc.shutdown()
        worker.join(timeout=2)
        ipc_worker.join(timeout=2)


@pytest.mark.parametrize("complete", [True, False])
def test_export_requires_complete_consistent_ipc_snapshot(web, monkeypatch, complete):
    server, _ = web
    records = [
        {"record": "metadata", "format": "meshpi-database-export"},
        {"record": "row", "table": "messages", "data": {"text": "Mocka historikk"}},
    ]
    if complete:
        records.append({"record": "complete", "rows": 1})

    class Stream:
        def __iter__(self):
            return iter(json.dumps(row).encode() + b"\n" for row in records)

        def close(self):
            pass

    class Socket:
        def close(self):
            pass

    monkeypatch.setattr("meshpi.web.open_export", lambda _: (Socket(), Stream()))
    status, headers, body = http(server, "/meshpi/api/export", method="POST")
    assert status == (200 if complete else 503)
    if complete:
        assert headers["Content-Type"].startswith("application/x-ndjson")
        assert json.loads(body.splitlines()[-1])["record"] == "complete"
    else:
        assert "inga fil" in json.loads(body)["error"]


def test_tab_title_identifies_https_server_before_login_without_private_hostname(web, monkeypatch):
    server, calls = web
    monkeypatch.setattr('meshpi.web.socket.gethostname', lambda: 'private-os-hostname')
    status, _, body = http(server, '/meshpi/', headers={'Cookie': ''})
    assert status == 200
    assert '<title>MeshPi · mesh-test</title>' in body.decode()
    assert b'data-server-name="mesh-test"' in body
    assert b'private-os-hostname' not in body
    assert b'__MESHPI_PAGE_LABEL__' not in body
    assert b'__MESHPI_SERVER_NAME__' not in body
    assert calls == []


def test_local_title_does_not_expose_os_hostname_before_login(web, monkeypatch):
    server, calls = web
    server.auth_origin = f'http://localhost:{server.server_address[1]}'
    monkeypatch.setattr('meshpi.web.socket.gethostname', lambda: 'private-os-hostname')
    status, _, body = http(server, '/meshpi/', headers={'Cookie': ''})
    assert status == 200
    assert '<title>MeshPi · Privat meshklient</title>' in body.decode()
    assert b'data-server-name=""' in body
    assert b'private-os-hostname' not in body
    assert calls == []
