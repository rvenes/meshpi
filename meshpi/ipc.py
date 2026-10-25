from __future__ import annotations

import errno
import hmac
import json
import logging
import os
import queue
import secrets
import socket
import socketserver
import stat
import threading
import time
from collections import OrderedDict
from collections.abc import Callable
from contextlib import suppress
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

from meshpi import __version__
from meshpi.channels import (
    dm_conversation_id,
    parse_dm_conversation_id,
    parse_public_conversation_id,
    public_conversation_id,
)
from meshpi.config import Settings
from meshpi.database import Database
from meshpi.events import EventHub
from meshpi.gateway_database import GatewayDatabase
from meshpi.i18n import tr, using_language
from meshpi.models import normalize_node_id
from meshpi.service import MeshtasticService

LOG = logging.getLogger(__name__)
MAX_REQUEST_BYTES = 1_000_000
MAX_IPC_CLIENTS = 32
MAX_IPC_WATCHERS = 8
MAX_IPC_EXPORTS = 2
MAX_HISTORY_PREVIEWS = 8
MAX_HISTORY_SNAPSHOT_ROWS = 1_000_000
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
        self._history_lock = threading.Lock()
        self._history_previews: OrderedDict[str, tuple[float, str, dict[str, Any]]] = (
            OrderedDict()
        )

    def _history_request(self, request: dict[str, Any]) -> dict[str, Any]:
        command = request["command"]
        local_id = self.history_local_node_id()
        if not local_id:
            raise ValueError("Vel ein gateway før du opnar historikkinnstillingane.")
        if command == "history_settings":
            return self.database.history_settings(local_id)
        expected = request.get("expected_local_node_id")
        if expected != local_id:
            raise ValueError("Gatewayen er bytt. Opne innstillingane på nytt.")
        if command == "history_settings_set":
            days = request.get("retention_days")
            if type(days) is not int or not 0 <= days <= 36500:
                raise ValueError("Lagringstida må vere 0–36500 heile dagar.")
            if request.get("confirmed") is not True:
                raise ValueError("Stadfest lagringstida før ho blir endra.")
            result = self.database.set_history_policy(local_id, days)
            self.events.publish({"type": "history_changed", "local_node_id": local_id})
            return result
        if command == "history_cleanup_preview":
            days = request.get("older_than_days")
            scope = request.get("scope")
            if type(days) is not int or not 0 <= days <= 36500:
                raise ValueError("Vel 0–36500 heile dagar for oppryddinga.")
            if scope not in {"messages", "history"}:
                raise ValueError("Vel meldingar eller all historikk.")
            token = secrets.token_urlsafe(32)
            # Serialize allocations so concurrent clients cannot each allocate a
            # full history snapshot on a small Pi.
            with self._history_lock:
                preview = self.database.preview_history_cleanup(
                    local_id, scope=scope, older_than_days=days,
                )
                now = time.monotonic()
                self._history_previews = OrderedDict(
                    (key, value) for key, value in self._history_previews.items()
                    if value[0] > now
                )
                rows = sum(preview["counts"].values())
                if rows > MAX_HISTORY_SNAPSHOT_ROWS:
                    raise ValueError("For mykje historikk i éi opprydding. Vel ein eldre periode.")
                while self._history_previews and (
                    len(self._history_previews) >= MAX_HISTORY_PREVIEWS
                    or rows + sum(sum(value[2]["counts"].values())
                                  for value in self._history_previews.values())
                    > MAX_HISTORY_SNAPSHOT_ROWS
                ):
                    self._history_previews.popitem(last=False)

                self._history_previews[token] = (now + 300, local_id, preview)
            return {
                "cleanup_token": token, "local_node_id": local_id,
                "scope": scope, "older_than_days": days,
                "cutoff": preview.get("cutoff"), "counts": preview["counts"],
                "expires_in": 300,
            }
        if request.get("confirmed") is not True:
            raise ValueError("Stadfest førehandsvisinga før historikken blir sletta.")
        token = request.get("cleanup_token")
        if not isinstance(token, str) or not 20 <= len(token) <= 100:
            raise ValueError("Hent ei ny førehandsvising av oppryddinga.")
        with self._history_lock:
            saved = self._history_previews.get(token)
            if saved is None or saved[0] <= time.monotonic() or saved[1] != local_id:
                raise ValueError("Førehandsvisinga er utgått. Hent ei ny før du slettar.")
            # A failed/uncertain operation must never be retried with this token.
            del self._history_previews[token]
        deleted = self.database.cleanup_history(saved[2])
        self.events.publish({"type": "history_changed", "local_node_id": local_id})
        return {"local_node_id": local_id, "deleted": deleted}

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

    @staticmethod
    def _visible_conversations(
        conversations: list[dict[str, Any]],
        preferred_conversation: str = "",
    ) -> list[dict[str, Any]]:
        visible: list[dict[str, Any]] = []
        dm_positions: dict[str, int] = {}
        dm_routes: dict[str, list[dict[str, Any]]] = {}

        def dm_priority(
            item: dict[str, Any],
        ) -> tuple[bool, bool, bool, int, str]:
            conversation = str(item.get("conversation") or "")
            return (
                item.get("sendable") is True,
                conversation == preferred_conversation,
                conversation.startswith("dm:"),
                int(item.get("last_message_id") or 0),
                str(item.get("last_timestamp") or ""),
            )

        for item in conversations:
            if (
                item.get("kind") == "public"
                and str(item.get("channel_key") or "").startswith("legacy:")
            ):
                continue
            if item.get("kind") != "dm":
                visible.append(item)
                continue
            peer = str(
                item.get("peer_node")
                or item.get("conversation")
                or ""
            ).lower()
            position = dm_positions.get(peer)
            if position is None:
                dm_positions[peer] = len(visible)
                dm_routes[peer] = [item]
                visible.append(dict(item))
            else:
                dm_routes[peer].append(item)
                if dm_priority(item) > dm_priority(visible[position]):
                    visible[position] = dict(item)

        for peer, routes in dm_routes.items():
            position = dm_positions[peer]
            selected = visible[position]
            selected_conversation = str(selected.get("conversation") or "")
            newest = max(
                routes,
                key=lambda route: (
                    int(route.get("last_message_id") or 0),
                    str(route.get("last_timestamp") or ""),
                ),
            )
            selected["last_message_id"] = newest.get("last_message_id")
            selected["last_timestamp"] = newest.get("last_timestamp")
            selected["last_text"] = newest.get("last_text")
            selected["unread"] = sum(
                int(route.get("unread") or 0) for route in routes
            )
            selected["merged_routes"] = [
                str(route.get("conversation") or "")
                for route in routes
                if str(route.get("conversation") or "") != selected_conversation
            ]
        return visible

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

    def history_local_node_id(self) -> str | None:
        getter = getattr(self.service, "history_local_node_id", None)
        if callable(getter):
            return getter()
        status = self.service.status()
        return str(
            status.get("local_node_id")
            or status.get("history_local_node_id")
            or ""
        ) or None

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
                    tr("error.channel.dm_invalid")
                ) from exc
            if legacy_peer == node_id:
                return None
            raise ValueError(tr("error.channel.dm_invalid"))
        _, route_peer, _ = parse_dm_conversation_id(conversation)
        if route_peer != node_id:
            raise ValueError(
                tr("backend.ipc.archive_route_peer_mismatch")
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
        if command in {
            "history_settings", "history_settings_set",
            "history_cleanup_preview", "history_cleanup",
        }:
            return {"ok": True, "data": self._history_request(request)}
        if command == "web_login_code":
            # Local authenticated IPC only; deliberately absent from the web command allowlist.
            from meshpi.config import validate_web_config
            from meshpi.web_auth import AuthStore

            origin = request.get("origin", self.settings.web_origin)
            if not isinstance(origin, str):
                raise ValueError("Ugyldig innloggingsadresse.")
            local = urlsplit(origin)
            if (local.scheme == "http" and local.hostname == "localhost"
                    and local.port is not None and 1 <= local.port <= 65535
                    and origin == f"http://localhost:{local.port}"):
                pass
            else:
                origin = validate_web_config(self.settings.web_port, origin, "")
                if not origin:
                    raise ValueError("Innloggingsadressa manglar.")
            auth = AuthStore(self.settings.web_auth_dir, origin)
            return {"ok": True, "data": {"code": auth.issue_code()}}
        if command == "status":
            return {
                "ok": True,
                "data": self.service.status()
                | {
                    "background_mode": self.settings.background_mode,
                    "daemon_pid": os.getpid(),
                },
            }
        if command == "local_node_info":
            return {"ok": True, "data": self.service.local_node_info()}
        if command == "remove_nodes":
            return {
                "ok": True,
                "data": self.service.remove_nodes(
                    request.get("node_ids", []),
                    str(request.get("expected_local_node_id", "")),
                ),
            }
        if command == "shutdown":
            if self.shutdown_callback is None:
                raise RuntimeError(tr("backend.ipc.shutdown_unavailable"))
            return {"ok": True, "data": {"stopping": True}}
        if command == "connections":
            return {"ok": True, "data": self.service.list_connections()}
        if command == "discover_connections":
            return {
                "ok": True,
                "data": self.service.discover_connections(
                    include_ble=request.get("include_ble") is not False
                ),
            }
        if command == "discover_ble_connections":
            return {"ok": True, "data": self.service.discover_ble_connections()}
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
            local_node_id = self.history_local_node_id()
            return {
                "ok": True,
                "data": self.database.list_nodes(
                    search=str(request.get("search", "")),
                    sort=str(request.get("sort", "seen")),
                    local_node_id=local_node_id,
                ) if local_node_id else [],
            }
        if command == "node":
            local_node_id = self.history_local_node_id()
            node_id = normalize_node_id(str(request.get("node_id", "")))
            node = (
                self.database.get_node(node_id, local_node_id=local_node_id)
                if local_node_id
                else None
            )
            if node is None:
                raise ValueError(tr("backend.ipc.node_not_found", node_id=node_id))
            return {"ok": True, "data": node}
        if command == "node_overview":
            local_node_id = self.history_local_node_id()
            node_id = normalize_node_id(str(request.get("node_id", "")))
            node = (
                self.database.get_node(node_id, local_node_id=local_node_id)
                if local_node_id
                else None
            )
            if node is None:
                raise ValueError(tr("backend.ipc.node_not_found", node_id=node_id))
            return {
                "ok": True,
                "data": {
                    "node": node,
                    **self.database.node_observation_summary(
                        node_id,
                        local_node_id=local_node_id,
                    ),
                },
            }
        if command == "node_telemetry":
            local_node_id = self.history_local_node_id()
            node_id = normalize_node_id(str(request.get("node_id", "")))
            return {
                "ok": True,
                "data": self.database.list_telemetry(
                    node_id,
                    local_node_id=local_node_id,
                    kind=str(request.get("kind", "")).strip() or None,
                    limit=int(request.get("limit", 100)),
                    before_id=(
                        int(request["before_id"])
                        if request.get("before_id") is not None
                        else None
                    ),
                ) if local_node_id else [],
            }
        if command == "node_positions":
            local_node_id = self.history_local_node_id()
            node_id = normalize_node_id(str(request.get("node_id", "")))
            return {
                "ok": True,
                "data": self.database.list_positions(
                    node_id,
                    local_node_id=local_node_id,
                    limit=int(request.get("limit", 100)),
                    before_id=(
                        int(request["before_id"])
                        if request.get("before_id") is not None
                        else None
                    ),
                ) if local_node_id else [],
            }
        if command == "conversations":
            local_node_id = self.history_local_node_id()
            conversations = (
                self.database.conversations(local_node_id)
                if local_node_id
                else []
            )
            channels = self.active_channels()
            active_public_routes = {str(channel["conversation"]) for channel in channels}
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
            return {
                "ok": True,
                "data": self._visible_conversations(
                    [
                        item for item in conversations
                        if item["kind"] != "public"
                        or str(item["conversation"]) in active_public_routes
                    ],
                    str(request.get("preferred_conversation") or ""),
                ),
            }
        if command == "channels":
            channel_lister = getattr(self.service, "list_channels", None)
            return {
                "ok": True,
                "data": channel_lister() if callable(channel_lister) else [],
            }
        if command == "delete_messages":
            scope = str(request.get("scope", ""))
            local_node_id = self.history_local_node_id()
            if not local_node_id:
                raise RuntimeError(tr("backend.local_node.not_selected"))
            return {
                "ok": True,
                "data": {
                    "scope": scope,
                    "deleted": self.database.delete_messages(scope, local_node_id),
                },
            }
        if command == "archive_conversation":
            local_node_id = self.history_local_node_id()
            if not local_node_id:
                raise RuntimeError(tr("backend.local_node.not_selected"))
            node_id = normalize_node_id(str(request.get("node_id", "")))
            conversation = self._validated_archived_route(
                node_id,
                request.get("conversation"),
            )
            if conversation:
                route_local, _, _ = parse_dm_conversation_id(conversation)
                if route_local != local_node_id:
                    raise ValueError(
                        tr("backend.ipc.route_other_local_node")
                    )
            self.database.archive_conversation(
                node_id,
                conversation,
                local_node_id=local_node_id,
            )
            return {"ok": True, "data": {"node_id": node_id, "archived": True}}
        if command == "unarchive_conversation":
            local_node_id = self.history_local_node_id()
            if not local_node_id:
                raise RuntimeError(tr("backend.local_node.not_selected"))
            node_id = normalize_node_id(str(request.get("node_id", "")))
            conversation = self._validated_archived_route(
                node_id,
                request.get("conversation"),
            )
            if conversation:
                route_local, _, _ = parse_dm_conversation_id(conversation)
                if route_local != local_node_id:
                    raise ValueError(
                        tr("backend.ipc.route_other_local_node")
                    )
            self.database.unarchive_conversation(
                node_id,
                conversation,
                local_node_id=local_node_id,
            )
            return {"ok": True, "data": {"node_id": node_id, "archived": False}}
        if command == "messages":
            local_node_id = self.history_local_node_id()
            if not local_node_id:
                return {"ok": True, "data": []}
            conversation = str(request.get("conversation", "public"))
            limit = int(request.get("limit", 100))
            mark_read = bool(request.get("mark_read", False))
            requested_routes = request.get("conversations")
            if requested_routes is not None:
                if (
                    not isinstance(requested_routes, list)
                    or not 1 <= len(requested_routes) <= 64
                ):
                    raise ValueError(tr("backend.ipc.routes_invalid"))
                routes: list[str] = []
                peers: set[str] = set()
                for value in requested_routes:
                    route_local, route_peer, route_key = parse_dm_conversation_id(
                        str(value)
                    )
                    if route_local != local_node_id:
                        raise ValueError(
                            tr("backend.ipc.merged_routes_other_local_node")
                        )
                    routes.append(
                        dm_conversation_id(route_local, route_peer, route_key)
                    )
                    peers.add(route_peer)
                if len(peers) != 1:
                    raise ValueError(tr("backend.ipc.merged_routes_peer_mismatch"))
                data = self.database.list_messages(
                    "dm",
                    conversation_ids=list(dict.fromkeys(routes)),
                    limit=limit,
                    mark_read=mark_read,
                    local_node_id=local_node_id,
                )
            elif conversation == "public":
                primary = self.active_channel(
                    int(request.get("channel_index", 0))
                )
                if request.get("channel_index") is not None and primary is None:
                    raise ValueError(
                        tr("backend.channel.index_missing")
                    )
                data = (
                    self.database.list_messages(
                        "public",
                        conversation_id=str(primary["conversation"]),
                        limit=limit,
                        mark_read=mark_read,
                        local_node_id=local_node_id,
                    )
                    if primary
                    else []
                )
            elif conversation.startswith("channel:"):
                route_local, channel_key = parse_public_conversation_id(conversation)
                if route_local is not None and route_local != local_node_id:
                    raise ValueError(
                        tr("backend.ipc.public_route_other_local_node")
                    )
                data = self.database.list_messages(
                    "public",
                    conversation_id=public_conversation_id(
                        local_node_id,
                        channel_key,
                    ),
                    limit=limit,
                    mark_read=mark_read,
                    local_node_id=local_node_id,
                )
            elif conversation.startswith("dm:"):
                route_local, route_peer, route_key = parse_dm_conversation_id(
                    conversation
                )
                if route_local != local_node_id:
                    raise ValueError(tr("backend.ipc.dm_route_other_local_node"))
                data = self.database.list_messages(
                    "dm",
                    conversation_id=dm_conversation_id(
                        route_local,
                        route_peer,
                        route_key,
                    ),
                    limit=limit,
                    mark_read=mark_read,
                    local_node_id=local_node_id,
                )
            else:
                node_id = normalize_node_id(conversation)
                if request.get("channel_index") is None:
                    data = self.database.list_messages(
                        "dm",
                        peer_node=node_id,
                        limit=limit,
                        mark_read=mark_read,
                        local_node_id=local_node_id,
                    )
                else:
                    channel = self.active_channel(int(request["channel_index"]))
                    if channel is None:
                        raise ValueError(
                            tr("backend.channel.index_missing")
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
                        local_node_id=local_node_id,
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
            local_node_id = self.history_local_node_id()
            node_id = normalize_node_id(str(request.get("node_id", "")))
            return {
                "ok": True,
                "data": self.database.list_node_actions(
                    node_id,
                    action=str(request.get("action", "traceroute")),
                    limit=int(request.get("limit", 100)),
                    local_node_id=local_node_id,
                ) if local_node_id else [],
            }
        if command == "node_action_availability":
            return {
                "ok": True,
                "data": self.service.node_action_availability(
                    str(request.get("action", "")),
                    str(request.get("node_id", "")),
                ),
            }
        raise ValueError(tr("backend.ipc.unknown_command"))

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
        self._export_slots = threading.BoundedSemaphore(MAX_IPC_EXPORTS)
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
            raise RuntimeError(tr("backend.ipc.unix_windows"))


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
                raise ValueError(tr("backend.ipc.request_too_large"))
            try:
                request = json.loads(raw)
            except (UnicodeDecodeError, json.JSONDecodeError) as exc:
                raise ValueError(tr("backend.ipc.request_invalid_json")) from exc
            if not isinstance(request, dict):
                raise ValueError(tr("backend.ipc.request_not_object"))
            language = str(request.get("language") or "nn")
            if language not in {"nn", "en"}:
                language = "nn"
            with using_language(language):
                if not self.server.app.is_authenticated(request):
                    raise PermissionError(tr("backend.ipc.authentication_failed"))
                if request.get("command") == "watch":
                    self._watch(request)
                    return
                if request.get("command") == "export":
                    self._export()
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
            raise RuntimeError(tr("backend.ipc.too_many_watchers"))
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

    def _export(self) -> None:
        if not self.server._export_slots.acquire(blocking=False):
            raise RuntimeError(tr("backend.ipc.too_many_exports"))
        try:
            app = self.server.app
            if isinstance(app.database, GatewayDatabase):
                gateway_id = app.history_local_node_id()
                if not gateway_id:
                    raise ValueError("Vel ein gateway før du eksporterer historikken.")
                records = app.database.export_records(__version__, local_node_id=gateway_id)
            else:
                records = app.database.export_records(__version__)
            self._write(
                {
                    "ok": True,
                    "data": {
                        "format": "meshpi-database-export",
                        "encoding": "utf-8",
                    },
                }
            )
            for record in records:
                self._write(record)
        finally:
            self.server._export_slots.release()

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
    def __init__(self, settings: Settings, app: IPCApplication | None = None):
        if len(settings.ipc_token) < 32:
            raise ValueError(tr("backend.ipc.token_too_short"))
        self.settings = settings
        self._lifecycle_lock = threading.Lock()
        self._serving = False
        self._closed = False
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
            raise ValueError(tr("backend.ipc.socket_path_too_long"))
        parent = path.parent
        if parent.exists():
            parent_stat = parent.lstat()
            if stat.S_ISLNK(parent_stat.st_mode) or not stat.S_ISDIR(
                parent_stat.st_mode
            ):
                raise ValueError(tr("backend.ipc.socket_parent_invalid"))
            if parent_stat.st_uid != os.geteuid():
                raise PermissionError(tr("backend.ipc.socket_parent_wrong_owner"))
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
            raise PermissionError(tr("backend.ipc.socket_path_unsafe"))
        probe = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        probe.settimeout(0.2)
        try:
            probe.connect(str(path))
        except OSError as exc:
            # A full listen queue or timeout is not proof of a stale socket.
            if exc.errno != errno.ECONNREFUSED:
                raise
            current = path.lstat()
            if (current.st_dev, current.st_ino) != (existing.st_dev, existing.st_ino):
                raise OSError(tr("backend.ipc.socket_path_in_use")) from exc
            path.unlink()
        else:
            raise OSError(tr("backend.ipc.socket_path_in_use"))
        finally:
            probe.close()

    @property
    def address(self) -> tuple[str, int] | str:
        if self._unix_path is not None:
            return str(self._unix_path)
        host, port = self._server.server_address
        return str(host), int(port)

    def set_application(self, app: IPCApplication) -> None:
        with self._lifecycle_lock:
            if self._serving or self._closed:
                raise RuntimeError("IPC-tenesta kan ikkje få ny applikasjon no.")
            self._server.app = app

    def serve_forever(self) -> None:
        with self._lifecycle_lock:
            if self._closed:
                return
            if self._server.app is None:
                raise RuntimeError("IPC-applikasjonen er ikkje klar.")
            self._serving = True
        try:
            if self._unix_path is not None:
                LOG.info("Lokal CLI-teneste lyttar på %s", self._unix_path)
            else:
                LOG.info("Lokal CLI-teneste lyttar på %s:%s", *self._server.server_address)
            self._server.serve_forever(poll_interval=0.5)
        finally:
            with self._lifecycle_lock:
                self._serving = False

    def shutdown(self, *, close: bool = True) -> None:
        with self._lifecycle_lock:
            serving = self._serving
        if serving:
            self._server.shutdown()
        if close:
            self.close()

    def close(self) -> None:
        """Release an unstarted or stopped listener without waiting for serve_forever."""
        with self._lifecycle_lock:
            if self._closed:
                return
            self._closed = True
        self._server.server_close()
        if self._unix_path is not None and self._unix_identity is not None:
            try:
                current = self._unix_path.lstat()
            except FileNotFoundError:
                return
            if (current.st_dev, current.st_ino) == self._unix_identity:
                self._unix_path.unlink()
