from __future__ import annotations

import hmac
import json
import logging
import os
import queue
import socket
import socketserver
import stat
import threading
from collections.abc import Callable
from contextlib import suppress
from pathlib import Path
from typing import Any

from meshpi.config import Settings
from meshpi.database import Database
from meshpi.events import EventHub
from meshpi.models import normalize_node_id
from meshpi.service import MeshtasticService

LOG = logging.getLogger(__name__)
MAX_REQUEST_BYTES = 1_000_000
MAX_IPC_CLIENTS = 32
MAX_IPC_WATCHERS = 8
IPC_READ_TIMEOUT = 5.0
IPC_WATCH_HEARTBEAT = 20.0


class IPCApplication:
    def __init__(
        self,
        settings: Settings,
        database: Database,
        service: MeshtasticService,
        events: EventHub,
        shutdown_callback: Callable[[], None] | None = None,
    ):
        self.settings = settings
        self.database = database
        self.service = service
        self.events = events
        self.shutdown_callback = shutdown_callback

    def is_authenticated(self, request: dict[str, Any]) -> bool:
        candidate = request.get("token")
        return (
            isinstance(candidate, str)
            and bool(self.settings.ipc_token)
            and hmac.compare_digest(candidate, self.settings.ipc_token)
        )

    def dispatch(self, request: dict[str, Any]) -> dict[str, Any]:
        command = request.get("command")
        if command == "status":
            return {
                "ok": True,
                "data": self.service.status()
                | {
                    "background_mode": self.settings.background_mode,
                    "daemon_pid": os.getpid(),
                },
            }
        if command == "shutdown":
            if self.shutdown_callback is None:
                raise RuntimeError("Denne daemonen kan ikkje stoppast via IPC")
            return {"ok": True, "data": {"stopping": True}}
        if command == "connections":
            return {"ok": True, "data": self.service.list_connections()}
        if command == "discover_connections":
            return {"ok": True, "data": self.service.discover_connections()}
        if command == "connect":
            return {
                "ok": True,
                "data": self.service.connect(
                    profile_id=str(request.get("profile_id", "")).strip() or None,
                    target=str(request.get("target", "")).strip() or None,
                    name=str(request.get("name", "")).strip() or None,
                ),
            }
        if command == "nodes":
            return {
                "ok": True,
                "data": self.database.list_nodes(
                    search=str(request.get("search", "")),
                    sort=str(request.get("sort", "seen")),
                ),
            }
        if command == "node":
            node_id = normalize_node_id(str(request.get("node_id", "")))
            node = self.database.get_node(node_id)
            if node is None:
                raise ValueError(f"Fann ikkje noden {node_id}")
            return {"ok": True, "data": node}
        if command == "conversations":
            return {"ok": True, "data": self.database.conversations()}
        if command == "delete_messages":
            scope = str(request.get("scope", ""))
            return {
                "ok": True,
                "data": {
                    "scope": scope,
                    "deleted": self.database.delete_messages(scope),
                },
            }
        if command == "archive_conversation":
            node_id = normalize_node_id(str(request.get("node_id", "")))
            self.database.archive_conversation(node_id)
            return {"ok": True, "data": {"node_id": node_id, "archived": True}}
        if command == "unarchive_conversation":
            node_id = normalize_node_id(str(request.get("node_id", "")))
            self.database.unarchive_conversation(node_id)
            return {"ok": True, "data": {"node_id": node_id, "archived": False}}
        if command == "messages":
            conversation = str(request.get("conversation", "public"))
            limit = int(request.get("limit", 100))
            mark_read = bool(request.get("mark_read", False))
            if conversation == "public":
                data = self.database.list_messages("public", limit=limit, mark_read=mark_read)
            else:
                node_id = normalize_node_id(conversation)
                data = self.database.list_messages(
                    "dm", peer_node=node_id, limit=limit, mark_read=mark_read
                )
            return {"ok": True, "data": data}
        if command == "send_public":
            return {"ok": True, "data": self.service.send_public(str(request.get("text", "")))}
        if command == "send_dm":
            return {
                "ok": True,
                "data": self.service.send_dm(
                    str(request.get("node_id", "")), str(request.get("text", ""))
                ),
            }
        if command == "node_action":
            return {
                "ok": True,
                "data": self.service.start_node_action(
                    str(request.get("action", "")),
                    str(request.get("node_id", "")),
                ),
            }
        if command == "node_action_status":
            return {
                "ok": True,
                "data": self.service.node_action_status(
                    str(request.get("action_id", ""))
                ),
            }
        if command == "node_actions":
            node_id = normalize_node_id(str(request.get("node_id", "")))
            return {
                "ok": True,
                "data": self.database.list_node_actions(
                    node_id,
                    action=str(request.get("action", "traceroute")),
                    limit=int(request.get("limit", 100)),
                ),
            }
        if command == "node_action_availability":
            return {
                "ok": True,
                "data": self.service.node_action_availability(
                    str(request.get("action", "")),
                    str(request.get("node_id", "")),
                ),
            }
        raise ValueError("Ukjend kommando")

    def complete_request(self, request: dict[str, Any]) -> None:
        if request.get("command") == "shutdown" and self.shutdown_callback is not None:
            threading.Thread(target=self.shutdown_callback, daemon=True).start()


class _IPCServerMixin:
    app: IPCApplication

    def __init__(self, address: Any, app: IPCApplication):
        self.app = app
        self._client_slots = threading.BoundedSemaphore(
            MAX_IPC_CLIENTS + MAX_IPC_WATCHERS
        )
        self._watcher_slots = threading.BoundedSemaphore(MAX_IPC_WATCHERS)
        super().__init__(address, _IPCHandler)  # type: ignore[misc]

    def process_request(self, request: Any, client_address: Any) -> None:
        if not self._client_slots.acquire(blocking=False):
            request.close()
            return
        try:
            super().process_request(request, client_address)
        except Exception:
            self._client_slots.release()
            raise

    def process_request_thread(self, request: Any, client_address: Any) -> None:
        try:
            super().process_request_thread(request, client_address)
        finally:
            self._client_slots.release()


class _IPCTCPServer(_IPCServerMixin, socketserver.ThreadingTCPServer):
    allow_reuse_address = os.name != "nt"
    daemon_threads = True

    def server_bind(self) -> None:
        if os.name == "nt":
            self.socket.setsockopt(
                socket.SOL_SOCKET,
                socket.SO_EXCLUSIVEADDRUSE,
                1,
            )
        super().server_bind()


if os.name != "nt":

    class _IPCUnixServer(  # type: ignore[misc]
        _IPCServerMixin,
        socketserver.ThreadingMixIn,
        socketserver.UnixStreamServer,  # type: ignore[attr-defined]
    ):
        daemon_threads = True

else:

    class _IPCUnixServer:  # type: ignore[no-redef]
        def __init__(self, address: str, app: IPCApplication):
            del address, app
            raise RuntimeError("Unix-socket er ikkje støtta på Windows")


class _IPCHandler(socketserver.StreamRequestHandler):
    server: _IPCServerMixin

    def _write(self, response: dict[str, Any]) -> None:
        payload = json.dumps(response, ensure_ascii=False, separators=(",", ":"))
        self.wfile.write(payload.encode("utf-8") + b"\n")
        self.wfile.flush()

    def handle(self) -> None:
        try:
            self.request.settimeout(IPC_READ_TIMEOUT)
            raw = self.rfile.readline(MAX_REQUEST_BYTES + 1)
            if not raw:
                return
            if len(raw) > MAX_REQUEST_BYTES:
                raise ValueError("Førespurnaden er for stor")
            request = json.loads(raw)
            if not isinstance(request, dict):
                raise ValueError("Førespurnaden må vere eit JSON-objekt")
            if not self.server.app.is_authenticated(request):
                raise PermissionError("IPC-autentisering feila")
            if request.get("command") == "watch":
                self._watch(request)
                return
            response = self.server.app.dispatch(request)
            try:
                self._write(response)
            finally:
                self.server.app.complete_request(request)
        except (BrokenPipeError, ConnectionResetError):
            return
        except Exception as exc:
            with suppress(BrokenPipeError, ConnectionResetError):
                self._write({"ok": False, "error": str(exc)})

    def _watch(self, request: dict[str, Any]) -> None:
        if not self.server._watcher_slots.acquire(blocking=False):
            raise RuntimeError("For mange aktive overvakingar; prøv igjen seinare")
        conversation = str(request.get("conversation", "all"))
        try:
            if conversation not in {"all", "public"}:
                conversation = normalize_node_id(conversation)
            self._write({"ok": True, "data": {"watching": conversation}})
            with self.server.app.events.subscribe() as subscriber:
                while True:
                    try:
                        event = subscriber.get(timeout=IPC_WATCH_HEARTBEAT)
                    except queue.Empty:
                        if self._peer_closed():
                            raise ConnectionResetError from None
                        self._write({"type": "heartbeat"})
                        continue
                    if self._peer_closed():
                        raise ConnectionResetError
                    if self._matches(event, conversation):
                        self._write(event)
        finally:
            self.server._watcher_slots.release()

    def _peer_closed(self) -> bool:
        timeout = self.request.gettimeout()
        try:
            self.request.setblocking(False)
            try:
                return self.request.recv(1, socket.MSG_PEEK) == b""
            except (BlockingIOError, InterruptedError):
                return False
            except OSError:
                return True
        finally:
            self.request.settimeout(timeout)

    @staticmethod
    def _matches(event: dict[str, Any], conversation: str) -> bool:
        if conversation == "all" or event.get("type") != "message":
            return True
        data = event.get("data", {})
        if conversation == "public":
            return data.get("kind") == "public"
        return data.get("kind") == "dm" and data.get("peer_node") == conversation


class IPCServer:
    def __init__(self, settings: Settings, app: IPCApplication):
        if len(settings.ipc_token) < 32:
            raise ValueError("IPC_TOKEN må vere minst 32 teikn")
        self.settings = settings
        self._unix_identity: tuple[int, int] | None = None
        if settings.ipc_uses_unix:
            path = settings.ipc_socket_path.expanduser().absolute()
            self._prepare_unix_path(path)
            self._server = _IPCUnixServer(str(path), app)
            try:
                if settings.ipc_socket_gid is not None:
                    os.chown(path, -1, settings.ipc_socket_gid)
                path.chmod(0o660 if settings.ipc_socket_gid is not None else 0o600)
                current = path.lstat()
                self._unix_identity = (current.st_dev, current.st_ino)
            except Exception:
                self._server.server_close()
                path.unlink(missing_ok=True)
                raise
            self._unix_path: Path | None = path
        else:
            self._server = _IPCTCPServer(
                (settings.ipc_host, settings.ipc_port),
                app,
            )
            self._unix_path = None

    @staticmethod
    def _prepare_unix_path(path: Path) -> None:
        if len(os.fsencode(path)) > 100:
            raise ValueError("IPC-socketstien er for lang")
        parent = path.parent
        if parent.exists():
            parent_stat = parent.lstat()
            if stat.S_ISLNK(parent_stat.st_mode) or not stat.S_ISDIR(
                parent_stat.st_mode
            ):
                raise ValueError("IPC-socketmappa må vere ei vanleg mappe")
            if parent_stat.st_uid != os.geteuid():
                raise PermissionError("IPC-socketmappa har feil eigar")
        else:
            parent.mkdir(parents=True, mode=0o700)
        try:
            existing = path.lstat()
        except FileNotFoundError:
            return
        if (
            stat.S_ISLNK(existing.st_mode)
            or not stat.S_ISSOCK(existing.st_mode)
            or existing.st_uid != os.geteuid()
        ):
            raise PermissionError("IPC-socketstien er ikkje ein trygg, eigd socket")
        probe = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        probe.settimeout(0.2)
        try:
            probe.connect(str(path))
        except OSError:
            path.unlink()
        else:
            raise OSError("IPC-socketen er allereie i bruk")
        finally:
            probe.close()

    @property
    def address(self) -> tuple[str, int] | str:
        if self._unix_path is not None:
            return str(self._unix_path)
        host, port = self._server.server_address
        return str(host), int(port)

    def serve_forever(self) -> None:
        if self._unix_path is not None:
            LOG.info("Lokal CLI-teneste lyttar på %s", self._unix_path)
        else:
            LOG.info("Lokal CLI-teneste lyttar på %s:%s", *self._server.server_address)
        self._server.serve_forever(poll_interval=0.5)

    def shutdown(self) -> None:
        self._server.shutdown()
        self._server.server_close()
        if self._unix_path is not None and self._unix_identity is not None:
            try:
                current = self._unix_path.lstat()
            except FileNotFoundError:
                return
            if (current.st_dev, current.st_ino) == self._unix_identity:
                self._unix_path.unlink()
