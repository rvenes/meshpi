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

from meshpi.channels import (
    dm_conversation_id,
    parse_dm_conversation_id,
    parse_public_conversation_id,
    public_conversation_id,
)
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

    def active_channels(self) -> list[dict[str, Any]]:
        channel_lister = getattr(self.service, "list_channels", None)
        return channel_lister() if callable(channel_lister) else []

    def active_channel(self, channel_index: int) -> dict[str, Any] | None:
        return next(
            (
                channel
                for channel in self.active_channels()
                if int(channel.get("channel_index", -1)) == channel_index
            ),
            None,
        )

    def matches_message_event(
        self,
        event: dict[str, Any],
        conversation: str,
    ) -> bool:
        if conversation == "all" or event.get("type") != "message":
            return True
        data = event.get("data", {})
        if conversation == "public":
            primary = self.active_channel(0)
            return bool(
                primary
                and data.get("conversation_id") == primary.get("conversation")
            )
        if conversation.startswith(("channel:", "dm:")):
            return data.get("conversation_id") == conversation
        return data.get("kind") == "dm" and data.get("peer_node") == conversation

    @staticmethod
    def _validated_archived_route(
        node_id: str,
        value: Any,
    ) -> str | None:
        conversation = str(value or "").strip()
        if not conversation:
            return None
        if not conversation.startswith("dm:"):
            try:
                legacy_peer = normalize_node_id(conversation)
            except ValueError as exc:
                raise ValueError(
                    "Ugyldig samtale-ID for direkte melding"
                ) from exc
            if legacy_peer == node_id:
                return None
            raise ValueError("Ugyldig samtale-ID for direkte melding")
        _, route_peer, _ = parse_dm_conversation_id(conversation)
        if route_peer != node_id:
            raise ValueError(
                "Samtaleruta samsvarar ikkje med noden som skal arkiverast"
            )
        return conversation

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
        if command == "node_overview":
            node_id = normalize_node_id(str(request.get("node_id", "")))
            node = self.database.get_node(node_id)
            if node is None:
                raise ValueError(f"Fann ikkje noden {node_id}")
            return {
                "ok": True,
                "data": {
                    "node": node,
                    **self.database.node_observation_summary(node_id),
                },
            }
        if command == "node_telemetry":
            node_id = normalize_node_id(str(request.get("node_id", "")))
            return {
                "ok": True,
                "data": self.database.list_telemetry(
                    node_id,
                    kind=str(request.get("kind", "")).strip() or None,
                    limit=int(request.get("limit", 100)),
                    before_id=(
                        int(request["before_id"])
                        if request.get("before_id") is not None
                        else None
                    ),
                ),
            }
        if command == "node_positions":
            node_id = normalize_node_id(str(request.get("node_id", "")))
            return {
                "ok": True,
                "data": self.database.list_positions(
                    node_id,
                    limit=int(request.get("limit", 100)),
                    before_id=(
                        int(request["before_id"])
                        if request.get("before_id") is not None
                        else None
                    ),
                ),
            }
        if command == "conversations":
            conversations = self.database.conversations()
            channels = self.active_channels()
            by_id = {
                str(item["conversation"]): item for item in conversations
            }
            for channel in channels:
                conversation = str(channel["conversation"])
                if conversation in by_id:
                    by_id[conversation].update(
                        {
                            "channel": channel.get("channel_index"),
                            "channel_key": channel.get("channel_key"),
                            "channel_name": channel.get("name"),
                            "local_node_id": channel.get("local_node_id"),
                            "sendable": True,
                        }
                    )
                    continue
                item = {
                    "conversation": conversation,
                    "kind": "public",
                    "channel": channel.get("channel_index"),
                    "channel_key": channel.get("channel_key"),
                    "channel_name": channel.get("name"),
                    "local_node_id": channel.get("local_node_id"),
                    "last_timestamp": None,
                    "last_text": None,
                    "unread": 0,
                    "sendable": True,
                }
                conversations.append(item)
                by_id[conversation] = item
            for item in conversations:
                dm_sendable = False
                if item["kind"] == "dm" and str(item["conversation"]).startswith(
                    "dm:"
                ):
                    try:
                        route_local, route_peer, route_key = (
                            parse_dm_conversation_id(str(item["conversation"]))
                        )
                        dm_sendable = route_peer == item.get("peer_node") and any(
                            channel.get("channel_key") == route_key
                            and channel.get("local_node_id") == route_local
                            for channel in channels
                        )
                    except ValueError:
                        dm_sendable = False
                item.setdefault(
                    "sendable",
                    (
                        item["conversation"] == "public"
                        and any(
                            int(channel.get("channel_index", -1)) == 0
                            for channel in channels
                        )
                    )
                    or dm_sendable,
                )
            return {"ok": True, "data": conversations}
        if command == "channels":
            channel_lister = getattr(self.service, "list_channels", None)
            return {
                "ok": True,
                "data": channel_lister() if callable(channel_lister) else [],
            }
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
            conversation = self._validated_archived_route(
                node_id,
                request.get("conversation"),
            )
            self.database.archive_conversation(node_id, conversation)
            return {"ok": True, "data": {"node_id": node_id, "archived": True}}
        if command == "unarchive_conversation":
            node_id = normalize_node_id(str(request.get("node_id", "")))
            conversation = self._validated_archived_route(
                node_id,
                request.get("conversation"),
            )
            self.database.unarchive_conversation(node_id, conversation)
            return {"ok": True, "data": {"node_id": node_id, "archived": False}}
        if command == "messages":
            conversation = str(request.get("conversation", "public"))
            limit = int(request.get("limit", 100))
            mark_read = bool(request.get("mark_read", False))
            if conversation == "public":
                primary = self.active_channel(
                    int(request.get("channel_index", 0))
                )
                if request.get("channel_index") is not None and primary is None:
                    raise ValueError(
                        "Kanalindeksen finst ikkje på den aktive noden"
                    )
                data = (
                    self.database.list_messages(
                        "public",
                        conversation_id=str(primary["conversation"]),
                        limit=limit,
                        mark_read=mark_read,
                    )
                    if primary
                    else []
                )
            elif conversation.startswith("channel:"):
                channel_key = parse_public_conversation_id(conversation)
                data = self.database.list_messages(
                    "public",
                    conversation_id=public_conversation_id(channel_key),
                    limit=limit,
                    mark_read=mark_read,
                )
            elif conversation.startswith("dm:"):
                route_local, route_peer, route_key = parse_dm_conversation_id(
                    conversation
                )
                data = self.database.list_messages(
                    "dm",
                    conversation_id=dm_conversation_id(
                        route_local,
                        route_peer,
                        route_key,
                    ),
                    limit=limit,
                    mark_read=mark_read,
                )
            else:
                node_id = normalize_node_id(conversation)
                if request.get("channel_index") is None:
                    data = self.database.list_messages(
                        "dm",
                        peer_node=node_id,
                        limit=limit,
                        mark_read=mark_read,
                    )
                else:
                    channel = self.active_channel(int(request["channel_index"]))
                    if channel is None:
                        raise ValueError(
                            "Kanalindeksen finst ikkje på den aktive noden"
                        )
                    data = self.database.list_messages(
                        "dm",
                        conversation_id=dm_conversation_id(
                            str(channel["local_node_id"]),
                            node_id,
                            str(channel["channel_key"]),
                        ),
                        limit=limit,
                        mark_read=mark_read,
                    )
            return {"ok": True, "data": data}
        if command == "send_public":
            send_options: dict[str, Any] = {}
            if str(request.get("conversation", "")).strip():
                send_options["conversation"] = str(request["conversation"]).strip()
            if request.get("channel_index") is not None:
                send_options["channel_index"] = int(request["channel_index"])
            return {
                "ok": True,
                "data": self.service.send_public(
                    str(request.get("text", "")),
                    **send_options,
                ),
            }
        if command == "send_dm":
            send_options = {}
            if str(request.get("conversation", "")).strip():
                send_options["conversation"] = str(request["conversation"]).strip()
            if request.get("channel_index") is not None:
                send_options["channel_index"] = int(request["channel_index"])
            return {
                "ok": True,
                "data": self.service.send_dm(
                    str(request.get("node_id", "")),
                    str(request.get("text", "")),
                    **send_options,
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
            if (
                conversation not in {"all", "public"}
                and not conversation.startswith(("channel:", "dm:"))
            ):
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
                    if self.server.app.matches_message_event(event, conversation):
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
