"""Avgrensa, privat HTTP-prototype over MeshPi sin lokale IPC.

Ingen filserver, database eller radio blir opna av denne prosessen.
"""

from __future__ import annotations

import hashlib
import html
import json
import logging
import queue
import re
import secrets
import socket
import threading
import time
from collections import OrderedDict
from contextlib import ExitStack, suppress
from dataclasses import dataclass, field
from http.cookies import SimpleCookie
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from importlib.resources import files
from ipaddress import IPv4Address
from typing import Any
from urllib.parse import urlsplit

from meshpi.channels import parse_dm_conversation_id, parse_public_conversation_id
from meshpi.client import CLIError, open_export, open_watch, request
from meshpi.config import Settings
from meshpi.events import EventHub
from meshpi.i18n import using_language
from meshpi.models import normalize_node_id
from meshpi.web_auth import AuthError, AuthStore, Session

LOG = logging.getLogger(__name__)
MAX_BODY = 16_384
READ_COMMANDS = {
    "history_settings", "history_cleanup_preview",
    "status", "nodes", "node_overview", "node_telemetry", "node_positions",
    "node_actions", "node_action_availability", "channels", "conversations", "messages",
    "connections", "local_node_info", "node_action_status",
}
WRITE_COMMANDS = {
    "history_settings_set", "history_cleanup",
    "send_public", "send_dm", "archive_conversation", "unarchive_conversation",
    "connect", "discover_connections", "discover_ble_connections", "node_action",
}
FIELDS = {
    "history_settings": set(),
    "history_settings_set": {"expected_local_node_id", "retention_days", "confirmed"},
    "history_cleanup_preview": {"expected_local_node_id", "scope", "older_than_days"},
    "history_cleanup": {"expected_local_node_id", "cleanup_token", "confirmed"},
    "status": set(), "channels": set(), "connections": set(), "local_node_info": set(),
    "nodes": {"search", "sort"},
    "node_overview": {"node_id"},
    "node_telemetry": {"node_id", "limit", "kind", "before_id"},
    "node_positions": {"node_id", "limit", "before_id"},
    "node_actions": {"node_id", "limit", "action"},
    "node_action_availability": {"node_id", "action"},
    "node_action_status": {"action_id"},
    "conversations": {"preferred_conversation"},
    "messages": {"conversation", "conversations", "limit", "mark_read"},
    "send_public": {"conversation", "text"},
    "send_dm": {"conversation", "node_id", "text"},
    "archive_conversation": {"node_id", "conversation"},
    "unarchive_conversation": {"node_id", "conversation"},
    "connect": {"profile_id", "target", "name"},
    "discover_connections": {"include_ble"}, "discover_ble_connections": set(),
    "node_action": {"action", "node_id"},
}


def validate_payload(payload: Any) -> dict[str, Any]:
    if not isinstance(payload, dict):
        raise ValueError("Førespurnaden må vere eit JSON-objekt.")
    command = payload.get("command")
    if not isinstance(command, str) or command not in FIELDS:
        raise ValueError("Denne handlinga er ikkje tilgjengeleg i webprototypen.")
    if payload.keys() - (FIELDS[command] | {"command"}):
        raise ValueError("Førespurnaden har ukjende felt.")
    for key, value in payload.items():
        if key in {"retention_days", "older_than_days"}:
            if type(value) is not int or not 0 <= value <= 36500:
                raise ValueError("Vel 0–36500 heile dagar.")
        elif key == "confirmed":
            if value is not True:
                raise ValueError("Handlinga må stadfestast.")
        elif key in {"limit", "before_id"}:
            if type(value) is not int or not 1 <= value <= (500 if key == "limit" else 2**63 - 1):
                raise ValueError("Ugyldig radgrense eller sidepeikar.")
        elif key in {"mark_read", "include_ble"}:
            if type(value) is not bool:
                raise ValueError("Ugyldig ja/nei-verdi.")
        elif key == "conversations":
            if not isinstance(value, list) or not 1 <= len(value) <= 64:
                raise ValueError("Ugyldige samtaleruter.")
            for route in value:
                if not isinstance(route, str):
                    raise ValueError("Ugyldig samtalerute.")
                parse_dm_conversation_id(route)
        elif not isinstance(value, str) or len(value) > (4000 if key == "text" else 512):
            raise ValueError("Ugyldig tekstfelt.")
    if "node_id" in FIELDS[command]:
        node_id = payload.get("node_id", "")
        if not re.fullmatch(r"![0-9a-fA-F]{8}", node_id):
            raise ValueError("Oppgi den fulle node-ID-en.")
    if command.startswith("history_") and command != "history_settings":
        if payload.keys() != FIELDS[command] | {"command"}:
            raise ValueError("Historikkførespurnaden manglar nødvendige felt.")
        if not re.fullmatch(r"![0-9a-f]{8}", payload["expected_local_node_id"]):
            raise ValueError("Oppgi den fulle gateway-ID-en.")
        if command == "history_cleanup_preview" and payload["scope"] not in {"messages", "history"}:
            raise ValueError("Vel meldingar eller all historikk.")
    if command in {"send_public", "send_dm"}:
        if not payload.get("text", "").strip():
            raise ValueError("Meldinga er tom.")
        route = payload.get("conversation", "")
        if command == "send_public":
            local, _ = parse_public_conversation_id(route)
            if local is None:
                raise ValueError("Vel ei kanalrute på den aktive noden.")
        else:
            _, peer, _ = parse_dm_conversation_id(route)
            if peer != normalize_node_id(payload["node_id"]):
                raise ValueError("Mottakaren samsvarar ikkje med samtaleruta.")
    if command == "messages" and not isinstance(payload.get("conversation"), str):
        raise ValueError("Vel ei samtale.")
    if "action" in payload and payload["action"] not in {"traceroute", "position_exchange"}:
        raise ValueError("Ugyldig nodehandling.")
    if command == "node_action" and "action" not in payload:
        raise ValueError("Vel ei nodehandling.")
    if "sort" in payload and payload["sort"] not in {"name", "seen", "id"}:
        raise ValueError("Ugyldig sortering.")
    if command == "connect" and bool(payload.get("profile_id")) == bool(payload.get("target")):
        raise ValueError("Vel anten ein lagra profil eller eit manuelt mål.")
    return payload


@dataclass
class _PendingOperation:
    fingerprint: str
    done: threading.Event = field(default_factory=threading.Event)
    response: tuple[int, dict[str, Any]] | None = None


class WebApplication:
    def __init__(self, settings: Settings):
        self.settings = settings
        self.csrf = secrets.token_urlsafe(32)
        self.events = EventHub()
        self.stopping = threading.Event()
        self._watch_lock = threading.Lock()
        self._watch_thread: threading.Thread | None = None
        self._watch_socket: socket.socket | None = None
        self._write_lock = threading.Lock()
        self._operation_lock = threading.Lock()
        self._pending_operations: dict[str, _PendingOperation] = {}
        self._results: OrderedDict[str, tuple[float, str, int, dict[str, Any]]] = OrderedDict()

    def _expire_results(self) -> None:
        # Caller holds _operation_lock; completed results retain the original deduplication window.
        cutoff = time.monotonic() - 3600
        while self._results and next(iter(self._results.values()))[0] < cutoff:
            self._results.popitem(last=False)

    def operation(self, payload: Any) -> dict[str, Any]:
        if not isinstance(payload, dict) or payload.keys() != {"operation_id"}:
            raise ValueError("Oppgi berre førespurnads-ID-en for handlinga.")
        operation_id = payload["operation_id"]
        if not isinstance(operation_id, str) or not re.fullmatch(
            r"[a-zA-Z0-9-]{16,80}", operation_id,
        ):
            raise ValueError("Handlinga manglar ein gyldig førespurnads-ID.")
        # This lock is never held during IPC, so a status check cannot block on a radio write.
        with self._operation_lock:
            self._expire_results()
            previous = self._results.get(operation_id)
            if previous is not None:
                data = {"state": "completed", "status": previous[2], "result": previous[3]}
            elif operation_id in self._pending_operations:
                data = {"state": "pending"}
            else:
                data = {"state": "unknown"}
        return {"ok": True, "data": data}

    def execute(self, payload: dict[str, Any]) -> tuple[int, dict[str, Any]]:
        try:
            with using_language("nn"):
                return 200, request(self.settings, payload, timeout=35)
        except CLIError as exc:
            return 503, {"ok": False, "error": str(exc)}

    def dispatch(self, payload: Any, operation_id: str) -> tuple[int, dict[str, Any]]:
        payload = validate_payload(payload)
        if payload["command"] not in WRITE_COMMANDS:
            return self.execute(payload)
        if not isinstance(operation_id, str) or not re.fullmatch(
            r"[a-zA-Z0-9-]{16,80}", operation_id,
        ):
            raise ValueError("Handlinga manglar ein gyldig førespurnads-ID.")
        fingerprint = json.dumps(payload, sort_keys=True, ensure_ascii=False)
        # Register before waiting for another write: queued sends must also report pending.
        with self._operation_lock:
            self._expire_results()
            previous = self._results.get(operation_id)
            if previous:
                if previous[1] != fingerprint:
                    return 409, {"ok": False, "error": "Førespurnads-ID-en er allereie brukt."}
                return previous[2], previous[3]
            pending = self._pending_operations.get(operation_id)
            owner = pending is None
            if pending is None:
                pending = _PendingOperation(fingerprint)
                self._pending_operations[operation_id] = pending
            elif pending.fingerprint != fingerprint:
                return 409, {"ok": False, "error": "Førespurnads-ID-en er allereie brukt."}
        if not owner:
            pending.done.wait()
            # Keep this response even if the bounded cache evicts it before a waiter resumes.
            assert pending.response is not None
            return pending.response
        # Only the registered owner executes IPC; different writes remain serialized.
        with self._write_lock:
            now = time.monotonic()
            try:
                status, result = self.execute(payload)
            except Exception:
                # Once IPC starts, even an unexpected failure must not permit an automatic replay.
                status, result = 503, {
                    "ok": False, "error": "Svaret frå daemonen kunne ikkje lesast.",
                }
            if status != 200:
                result = {"ok": False, "error": result["error"] + (
                    " Kontroller historikken før du prøver handlinga på nytt; "
                    "daemonen kan ha motteke førespurnaden."
                )}
            with self._operation_lock:
                self._results[operation_id] = (now, fingerprint, status, result)
                pending.response = (status, result)
                del self._pending_operations[operation_id]
                while len(self._results) > 1024:
                    self._results.popitem(last=False)
                pending.done.set()
            return status, result

    def start_watch(self) -> None:
        with self._watch_lock:
            if self._watch_thread is None:
                self._watch_thread = threading.Thread(target=self._watch, daemon=True)
                self._watch_thread.start()

    def _watch(self) -> None:
        while not self.stopping.is_set():
            sock = stream = None
            try:
                with using_language("nn"):
                    sock, stream = open_watch(self.settings)
                with self._watch_lock:
                    self._watch_socket = sock
                    if self.stopping.is_set():
                        sock.shutdown(socket.SHUT_RDWR)
                self.events.publish({"type": "refresh"})
                for line in stream:
                    event = json.loads(line)
                    if event.get("type") != "heartbeat":
                        self.events.publish({"type": "refresh"})
                    if self.stopping.is_set():
                        break
            except (OSError, CLIError, ValueError):
                self.events.publish({"type": "unavailable"})
            finally:
                with self._watch_lock:
                    self._watch_socket = None
                if stream is not None:
                    stream.close()
                if sock is not None:
                    sock.close()
            self.stopping.wait(3)

    def close(self) -> None:
        self.stopping.set()
        with self._watch_lock:
            if self._watch_socket is not None:
                with suppress(OSError):
                    self._watch_socket.shutdown(socket.SHUT_RDWR)
        if self._watch_thread is not None:
            self._watch_thread.join(timeout=12)


def validate_web_address(address: str) -> str:
    try:
        parsed = IPv4Address(address)
    except ValueError as exc:
        raise ValueError("Webserveren støttar berre lokal loopback-tilgang.") from exc
    if not parsed.is_loopback:
        raise ValueError("Weblyttaren må bruke loopback. Bruk Tailscale Serve for andre einingar.")
    return str(parsed)


class WebServer(ThreadingHTTPServer):
    daemon_threads = True
    block_on_close = False

    def __init__(
        self, settings: Settings, *, port: int = 8780, public_url: str = "",
        base_path: str = "/meshpi", bind_address: str = "127.0.0.1",
        app: WebApplication | None = None,
    ):
        bind_address = validate_web_address(bind_address)
        if not 0 <= port <= 65535:
            raise ValueError("Ugyldig webport.")
        if not re.fullmatch(r"(?:/[a-zA-Z0-9_-]+)*", base_path):
            raise ValueError("Ugyldig websti.")
        external = urlsplit(public_url)
        if public_url and (
            external.scheme != "https" or not external.hostname or external.username
            or external.password or external.path not in {"", "/"}
            or external.query or external.fragment
        ):
            raise ValueError("Nettadressa må vere ei privat HTTPS-rotadresse.")
        self.base_path = base_path
        self.app = app if app is not None else WebApplication(settings)
        self._slots = threading.BoundedSemaphore(24)
        self.event_slots = threading.BoundedSemaphore(8)
        self.export_slots = threading.BoundedSemaphore(2)
        super().__init__((bind_address, port), WebHandler)
        actual_port = self.server_address[1]
        self.origins = {f"http://{bind_address}:{actual_port}"}
        if bind_address == "127.0.0.1":
            self.origins.add(f"http://localhost:{actual_port}")
        if public_url:
            self.origins.add(public_url.rstrip("/"))
        self.hosts = {urlsplit(origin).netloc for origin in self.origins}
        self.auth_origin = public_url.rstrip("/") or f"http://localhost:{actual_port}"
        try:
            self.auth = AuthStore(settings.web_auth_dir, self.auth_origin)
        except BaseException:
            super().server_close()
            raise
        identity = hashlib.sha256((self.auth_origin + base_path).encode()).hexdigest()[:12]
        self.cookie_name = f"meshpi_session_{identity}"

    def process_request(self, connection: socket.socket, address: Any) -> None:
        if not self._slots.acquire(blocking=False):
            connection.close()
            return
        try:
            super().process_request(connection, address)
        except Exception:
            self._slots.release()
            raise

    def process_request_thread(self, connection: socket.socket, address: Any) -> None:
        try:
            super().process_request_thread(connection, address)
        finally:
            self._slots.release()

    def server_close(self) -> None:
        self.app.close()
        super().server_close()


class WebHandler(BaseHTTPRequestHandler):
    server: WebServer
    server_version = "MeshPi-web"
    sys_version = ""

    def setup(self) -> None:
        self.request.settimeout(10)
        super().setup()

    def log_message(self, _format: str, *_args: Any) -> None:
        # Paths, message bodies, credentials and node identities stay out of access logs.
        pass

    def send_error(self, code: int, message: str | None = None, explain: str | None = None):
        del message, explain
        self._json(code, {"ok": False, "error": "HTTP-førespurnaden kunne ikkje handsamast."})

    def _reply(self, status: int, data: bytes, content_type: str) -> None:
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(data)))
        self._security_headers()
        if getattr(self, "_set_session", None) is not None:
            session = self._set_session
            cookie = SimpleCookie()
            cookie[self.server.cookie_name] = session.token
            item = cookie[self.server.cookie_name]
            item["path"] = self.server.base_path + "/" if self.server.base_path else "/"
            item["httponly"] = True
            item["samesite"] = "Strict"
            if self.server.auth_origin.startswith("https://"):
                item["secure"] = True
            self.send_header("Set-Cookie", item.OutputString())
        self.end_headers()
        self.wfile.write(data)

    def _security_headers(self) -> None:
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Referrer-Policy", "no-referrer")
        self.send_header("Content-Security-Policy", (
            "default-src 'self'; script-src 'self'; style-src 'self'; "
            "connect-src 'self'; img-src 'self'; object-src 'none'; "
            "base-uri 'none'; frame-ancestors 'none'; form-action 'self'"
        ))

    def _json(self, status: int, payload: dict[str, Any]) -> None:
        self._reply(status, json.dumps(payload, ensure_ascii=False).encode(),
                    "application/json; charset=utf-8")

    def _path(self) -> str:
        path = urlsplit(self.path).path
        if self.server.base_path and path == self.server.base_path:
            return "@redirect"
        if self.server.base_path and path.startswith(self.server.base_path + "/"):
            path = path[len(self.server.base_path):]
        return path

    def _allowed(self, *, write: bool = False) -> bool:
        hosts = self.headers.get_all("Host", [])
        if len(hosts) != 1 or hosts[0] not in self.server.hosts:
            self._json(403, {"ok": False, "error": "Ugyldig vert."})
            return False
        origins = self.headers.get_all("Origin", [])
        if len(origins) > 1 or (origins and origins[0] not in self.server.origins):
            self._json(403, {"ok": False, "error": "Ugyldig opphav."})
            return False
        if self.headers.get("Sec-Fetch-Site") == "cross-site" and self._path() != "/":
            self._json(403, {"ok": False, "error": "Opne MeshPi frå den private nettadressa."})
            return False
        if write and (len(origins) != 1 or origins[0] != self.server.auth_origin):
            self._json(403, {"ok": False, "error": "Bruk den faste innloggingsadressa."})
            return False
        return True

    def _token(self) -> str:
        raw = self.headers.get_all("Cookie", [])
        if len(raw) != 1 or len(raw[0]) > 8192:
            return ""
        cookie = SimpleCookie()
        try:
            cookie.load(raw[0])
            value = cookie.get(self.server.cookie_name)
            return value.value if value else ""
        except Exception:
            return ""

    def _canonical_request(self) -> bool:
        # Do not trust forwarding headers or the loopback peer as authentication.
        return self.headers.get("Host") == urlsplit(self.server.auth_origin).netloc

    def _session(self) -> Session | None:
        if not self._canonical_request():
            return None
        try:
            return self.server.auth.session(self._token())
        except AuthError:
            return None

    def _authenticated(self) -> bool:
        session = self._session()
        if session is None or not session.authenticated:
            self._json(401, {"ok": False, "error": "Logg inn for å bruke MeshPi."})
            return False
        return True

    def _csrf(self) -> bool:
        tokens = self.headers.get_all("X-MeshPi-CSRF", [])
        session = self._session()
        if session is None:
            self._json(401, {"ok": False, "error": "Innlogginga er utgått. Logg inn igjen."})
            return False
        try:
            valid = len(tokens) == 1 and self.server.auth.verify_csrf(session.token, tokens[0])
        except AuthError:
            valid = False
        if not valid:
            self._json(403, {"ok": False, "error": "Last sida på nytt før du prøver igjen."})
            return False
        return True

    def _session_data(self, session: Session) -> dict[str, Any]:
        return {
            "authenticated": session.authenticated,
            "csrf": session.csrf,
            "passkeys_available": self.server.auth.passkeys_available and self._canonical_request(),
            "canonical_url": self.server.auth_origin + self.server.base_path + "/",
            **({"hostname": socket.gethostname()} if session.authenticated else {}),
        }

    def _auth_request(self, path: str, payload: Any) -> None:
        if not isinstance(payload, dict):
            raise ValueError("Førespurnaden må vere eit JSON-objekt.")
        auth, token = self.server.auth, self._token()
        data = {}
        if path == "/api/auth/code":
            session = auth.login_code(token, payload.get("code", ""))
            self._set_session = session
            data = self._session_data(session)
        elif path == "/api/auth/login/begin":
            data = auth.authentication_begin(token)
        elif path == "/api/auth/login/finish":
            session = auth.authentication_complete(
                token, payload.get("challenge_id", ""), payload.get("credential", {}),
            )
            self._set_session = session
            data = self._session_data(session)
        elif path == "/api/auth/register/begin":
            data = auth.registration_begin(token)
        elif path == "/api/auth/register/finish":
            data = auth.registration_complete(
                token, payload.get("challenge_id", ""), payload.get("credential", {}),
                payload.get("name", ""),
            )
        elif path == "/api/auth/remove":
            auth.delete_credential(token, payload.get("id", ""))
        elif path == "/api/auth/logout":
            auth.logout(token)
            session = auth.new_session()
            self._set_session = session
            data = self._session_data(session)
        else:
            self._json(404, {"ok": False, "error": "Handlinga finst ikkje."})
            return
        self._json(200, {"ok": True, "data": data})

    def do_GET(self) -> None:
        if not self._allowed():
            return
        path = self._path()
        if path == "@redirect":
            self.send_response(308)
            self.send_header("Location", self.server.base_path + "/")
            self.send_header("Content-Length", "0")
            self.end_headers()
            return
        resources = {
            "/": ("index.html", "text/html; charset=utf-8"),
            "/app.js": ("app.js", "text/javascript; charset=utf-8"),
            "/auth.js": ("auth.js", "text/javascript; charset=utf-8"),
            "/traceroute.js": ("traceroute.js", "text/javascript; charset=utf-8"),
            "/theme.js": ("theme.js", "text/javascript; charset=utf-8"),
            "/install.js": ("install.js", "text/javascript; charset=utf-8"),
            "/sw.js": ("sw.js", "text/javascript; charset=utf-8"),
            "/manifest.webmanifest": ("manifest.webmanifest", "application/manifest+json"),
            "/icon.svg": ("icon.svg", "image/svg+xml"),
            "/icon-192.png": ("icon-192.png", "image/png"),
            "/icon-512.png": ("icon-512.png", "image/png"),
            "/apple-touch-icon.png": ("apple-touch-icon.png", "image/png"),
            "/style.css": ("style.css", "text/css; charset=utf-8"),
        }
        if path in resources:
            name, mime = resources[path]
            content = files("meshpi").joinpath("web_assets", name).read_bytes()
            if name in {"index.html", "manifest.webmanifest"}:
                # Serve strips the mount prefix, including for /meshpi without a trailing slash.
                # Explicit mount URLs keep assets and API calls independent of the document URL.
                content = content.replace(
                    b"__MESHPI_BASE_PATH__", self.server.base_path.encode("utf-8")
                )
            if name == "index.html":
                # The configured HTTPS name is already public; do not expose the
                # OS hostname before login or trust request headers for identity.
                origin = urlsplit(self.server.auth_origin)
                server_name = (
                    (origin.hostname or "").split(".")[0] if origin.scheme == "https" else ""
                )
                content = content.replace(
                    b"__MESHPI_SERVER_NAME__", html.escape(server_name, quote=True).encode()
                ).replace(
                    b"__MESHPI_PAGE_LABEL__",
                    html.escape(server_name or "Privat meshklient", quote=True).encode(),
                )
            self._reply(200, content, mime)
        elif path == "/api/session":
            if not self._canonical_request():
                self._json(200, {"ok": True, "data": {
                    "authenticated": False, "csrf": "canonical-address-required",
                    "passkeys_available": False,
                    "canonical_url": self.server.auth_origin + self.server.base_path + "/",
                }})
                return
            try:
                session = self._session()
                if session is None:
                    session = self.server.auth.new_session()
                    self._set_session = session
                self._json(200, {"ok": True, "data": self._session_data(session)})
            except AuthError:
                self._json(503, {"ok": False, "error":
                    "Innlogginga er utilgjengeleg. Prøv igjen eller køyr meshpi web-code lokalt."})
        elif path == "/api/health":
            self._json(200, {"ok": True, "service": "meshpi-web"})
        elif path == "/api/auth/keys":
            if self._authenticated():
                try:
                    keys = self.server.auth.list_credentials(self._token())
                    self._json(200, {"ok": True, "data": {"keys": keys}})
                except AuthError:
                    self._json(401, {"ok": False, "error": "Logg inn igjen."})
        elif path == "/api/events":
            if self._authenticated():
                self._events()
        else:
            self._json(404, {"ok": False, "error": "Sida finst ikkje."})

    def do_POST(self) -> None:
        if not self._allowed(write=True):
            return
        path = self._path()
        if not path.startswith("/api/auth/") and not self._authenticated():
            return
        if not self._csrf():
            return
        if path == "/api/export":
            self._export()
            return
        if path not in {"/api/request", "/api/operation"} and not path.startswith("/api/auth/"):
            self._json(404, {"ok": False, "error": "Handlinga finst ikkje."})
            return
        lengths = self.headers.get_all("Content-Length", [])
        if self.headers.get("Transfer-Encoding") or len(lengths) != 1:
            self._json(400, {"ok": False, "error": "Ugyldig meldingslengd."})
            return
        try:
            length = int(lengths[0])
            if not 1 <= length <= MAX_BODY:
                raise ValueError("Førespurnaden er for stor eller tom.")
            if self.headers.get_content_type() != "application/json":
                raise ValueError("Førespurnaden må bruke JSON.")
            raw = self.rfile.read(length)
            if len(raw) != length:
                raise ValueError("Førespurnaden er ufullstendig.")
            payload = json.loads(raw)
            if path.startswith("/api/auth/"):
                self._auth_request(path, payload)
                return
            if path == "/api/operation":
                status, result = 200, self.server.app.operation(payload)
            else:
                status, result = self.server.app.dispatch(
                    payload, self.headers.get("X-MeshPi-Operation", ""),
                )
            self._json(status, result)
        except (ValueError, UnicodeError) as exc:
            self._json(400, {"ok": False, "error": str(exc)})
        except RecursionError:
            self._json(400, {"ok": False, "error": "JSON-førespurnaden har for mange nivå."})
        except (OSError, TimeoutError):
            with suppress(OSError):
                self._json(408, {"ok": False, "error": "Førespurnaden tok for lang tid."})

    def _export(self) -> None:
        if not self.server.export_slots.acquire(blocking=False):
            self._json(503, {"ok": False, "error": "For mange samtidige eksportar."})
            return
        sock = stream = None
        try:
            with using_language("nn"):
                sock, stream = open_export(self.server.app.settings)
            content = bytearray()
            metadata = complete = False
            rows = 0
            for raw in stream:
                if len(content) + len(raw) > 20 * 1024 * 1024:
                    raise CLIError(
                        "Eksporten er større enn 20 MiB. Bruk meshpi export i terminalen."
                    )
                record = json.loads(raw)
                if not isinstance(record, dict) or complete or record.get("ok") is False:
                    raise CLIError("Daemonen leverte ein ugyldig eksport.")
                kind = record.get("record")
                if not metadata:
                    if kind != "metadata" or record.get("format") != "meshpi-database-export":
                        raise CLIError("Eksporten manglar gyldige metadata.")
                    metadata = True
                elif kind == "row":
                    rows += 1
                elif kind == "complete" and record.get("rows") == rows:
                    complete = True
                else:
                    raise CLIError("Eksporten har ugyldige radtal eller postar.")
                content.extend(raw if raw.endswith(b"\n") else raw + b"\n")
            if not complete:
                raise CLIError("Eksporten blei avbroten; inga fil blir levert.")
            if not self._authenticated():
                return
            self._reply(200, bytes(content), "application/x-ndjson; charset=utf-8")
        except (OSError, ValueError, CLIError) as exc:
            self._json(503, {"ok": False, "error": str(exc)})
        finally:
            if stream is not None:
                stream.close()
            if sock is not None:
                sock.close()
            self.server.export_slots.release()

    def _events(self) -> None:
        if not self.server.event_slots.acquire(blocking=False):
            self._json(503, {"ok": False, "error": "For mange opne sanntidsvisingar."})
            return
        try:
            with self.server.app.events.subscribe() as subscriber:
                self.server.app.start_watch()
                self.send_response(200)
                self.send_header("Content-Type", "text/event-stream")
                self._security_headers()
                self.end_headers()
                self.wfile.write(b"data: {\"type\":\"refresh\"}\n\n")
                self.wfile.flush()
                while not self.server.app.stopping.is_set():
                    session = self._session()
                    if session is None or not session.authenticated:
                        self.wfile.write(b'data: {"type":"authentication_required"}\n\n')
                        self.wfile.flush()
                        break
                    try:
                        event = subscriber.get(timeout=5)
                        chunk = ("data: " + json.dumps(event) + "\n\n").encode()
                    except queue.Empty:
                        chunk = b": heartbeat\n\n"
                    session = self._session()
                    if session is None or not session.authenticated:
                        break
                    self.wfile.write(chunk)
                    self.wfile.flush()
        except (OSError, TimeoutError):
            pass
        finally:
            self.server.event_slots.release()


def run_web(
    settings: Settings, *, port: int, public_url: str, base_path: str,
) -> None:
    # The UI must be reachable during daemon restart or first setup without a node.
    with ExitStack() as stack:
        try:
            server = WebServer(settings, port=port, public_url=public_url, base_path=base_path)
            stack.callback(server.server_close)
        except OSError as exc:
            raise CLIError(f"Kunne ikkje starte weblyttaren: {exc}") from exc
        print(f"MeshPi-web: {server.auth_origin}{base_path}/")
        print("Bruk meshpi web-code i ein lokal terminal for innlogging eller gjenoppretting.")
        with suppress(KeyboardInterrupt):
            server.serve_forever()
