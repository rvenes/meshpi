from __future__ import annotations

import json
import logging
import queue
import socketserver
from contextlib import suppress
from typing import Any

from meshpi.config import Settings
from meshpi.database import Database
from meshpi.events import EventHub
from meshpi.models import normalize_node_id
from meshpi.service import MeshtasticService

LOG = logging.getLogger(__name__)
MAX_REQUEST_BYTES = 1_000_000


class IPCApplication:
    def __init__(
        self,
        settings: Settings,
        database: Database,
        service: MeshtasticService,
        events: EventHub,
    ):
        self.settings = settings
        self.database = database
        self.service = service
        self.events = events

    def dispatch(self, request: dict[str, Any]) -> dict[str, Any]:
        command = request.get("command")
        if command == "status":
            return {"ok": True, "data": self.service.status()}
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
        raise ValueError("Ukjend kommando")


class _IPCServer(socketserver.ThreadingTCPServer):
    allow_reuse_address = True
    daemon_threads = True

    def __init__(self, address: tuple[str, int], app: IPCApplication):
        self.app = app
        super().__init__(address, _IPCHandler)


class _IPCHandler(socketserver.StreamRequestHandler):
    server: _IPCServer

    def _write(self, response: dict[str, Any]) -> None:
        payload = json.dumps(response, ensure_ascii=False, separators=(",", ":"))
        self.wfile.write(payload.encode("utf-8") + b"\n")
        self.wfile.flush()

    def handle(self) -> None:
        try:
            raw = self.rfile.readline(MAX_REQUEST_BYTES + 1)
            if not raw:
                return
            if len(raw) > MAX_REQUEST_BYTES:
                raise ValueError("Førespurnaden er for stor")
            request = json.loads(raw)
            if not isinstance(request, dict):
                raise ValueError("Førespurnaden må vere eit JSON-objekt")
            if request.get("command") == "watch":
                self._watch(request)
                return
            self._write(self.server.app.dispatch(request))
        except (BrokenPipeError, ConnectionResetError):
            return
        except Exception as exc:
            with suppress(BrokenPipeError, ConnectionResetError):
                self._write({"ok": False, "error": str(exc)})

    def _watch(self, request: dict[str, Any]) -> None:
        conversation = str(request.get("conversation", "all"))
        if conversation not in {"all", "public"}:
            conversation = normalize_node_id(conversation)
        self._write({"ok": True, "data": {"watching": conversation}})
        with self.server.app.events.subscribe() as subscriber:
            while True:
                try:
                    event = subscriber.get(timeout=20)
                except queue.Empty:
                    self._write({"type": "heartbeat"})
                    continue
                if self._matches(event, conversation):
                    self._write(event)

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
        self.settings = settings
        self._server = _IPCServer((settings.ipc_host, settings.ipc_port), app)

    @property
    def address(self) -> tuple[str, int]:
        host, port = self._server.server_address
        return str(host), int(port)

    def serve_forever(self) -> None:
        LOG.info("Lokal CLI-teneste lyttar på %s:%s", *self._server.server_address)
        self._server.serve_forever(poll_interval=0.5)

    def shutdown(self) -> None:
        self._server.shutdown()
        self._server.server_close()
