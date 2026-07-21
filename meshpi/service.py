from __future__ import annotations

import logging
import socket
import threading
import uuid
from collections.abc import Callable
from typing import Any, Protocol

from meshpi.config import Settings
from meshpi.connections import (
    ConnectionProfile,
    ConnectionStore,
    discover_local_subnets,
    discover_serial,
    discover_tcp,
    parse_connection_target,
)
from meshpi.database import Database
from meshpi.events import EventHub
from meshpi.models import (
    ConversationKind,
    Direction,
    Message,
    MessageStatus,
    Transport,
    normalize_node_id,
    now_iso,
    validate_message_text,
)
from meshpi.node_actions import NodeActionError, parse_traceroute_response
from meshpi.packet import node_from_registry, parse_text_packet

LOG = logging.getLogger(__name__)
RECONNECT_DELAYS = (2, 5, 10, 30)
TRACEROUTE_TIMEOUT_SECONDS = 120
MAX_NODE_ACTIONS = 50


class Interface(Protocol):
    nodes: dict[str, dict[str, Any]] | None
    isConnected: threading.Event

    def sendText(self, text: str, **kwargs: Any) -> Any: ...

    def sendData(self, data: Any, **kwargs: Any) -> Any: ...

    def close(self) -> None: ...


InterfaceFactory = Callable[[ConnectionProfile], Interface]


def default_interface_factory(profile: ConnectionProfile) -> Interface:
    if profile.transport == "serial":
        from meshtastic.serial_interface import SerialInterface

        return SerialInterface(devPath=profile.device, timeout=30)
    if profile.transport == "tcp":
        from meshtastic.tcp_interface import TCPInterface

        class TimedTCPInterface(TCPInterface):
            def myConnect(self) -> None:  # noqa: N802
                connected = socket.create_connection(
                    (self.hostname, self.portNumber), timeout=10
                )
                connected.settimeout(None)
                self.socket = connected

        return TimedTCPInterface(
            hostname=str(profile.host),
            portNumber=int(profile.port or 4403),
            timeout=10,
        )
    raise ValueError(f"Ustøtta transport: {profile.transport}")


def reconnect_delay(attempt: int) -> int:
    return RECONNECT_DELAYS[min(max(attempt, 0), len(RECONNECT_DELAYS) - 1)]


def _sent_packet_id(packet: Any) -> int | None:
    value = packet.get("id") if isinstance(packet, dict) else getattr(packet, "id", None)
    try:
        return int(value) if value is not None else None
    except (TypeError, ValueError):
        return None


def _ack_failed(packet: Any) -> bool:
    if not isinstance(packet, dict):
        return False
    routing = packet.get("decoded", {}).get("routing", {})
    reason = routing.get("errorReason") if isinstance(routing, dict) else None
    return reason not in (None, "NONE", 0)


class MeshtasticService:
    def __init__(
        self,
        settings: Settings,
        database: Database,
        events: EventHub,
        interface_factory: InterfaceFactory = default_interface_factory,
        connections: ConnectionStore | None = None,
    ):
        self.settings = settings
        self.database = database
        self.events = events
        self.interface_factory = interface_factory
        default_profile = (
            ConnectionProfile.tcp(settings.meshtastic_host, settings.meshtastic_port)
            if settings.meshtastic_host
            else None
        )
        self.connections = connections or ConnectionStore(
            settings.database_path.with_name("connections.json"),
            default_profile,
        )
        self._profile = self.connections.active_profile()
        self._thread: threading.Thread | None = None
        self._stop = threading.Event()
        self._lost = threading.Event()
        self._switch_requested = threading.Event()
        self._lock = threading.RLock()
        self._state_lock = threading.Lock()
        self._interface: Interface | None = None
        self._local_node_id: str | None = None
        self._node_actions: dict[str, dict[str, Any]] = {}
        self._node_action_timers: dict[str, threading.Timer] = {}
        self._active_traceroute_id: str | None = None
        profile_status = self._profile_status(self._profile) if self._profile else {
            "connection_id": None,
            "connection_name": None,
            "transport": None,
            "endpoint": None,
            "host": None,
            "port": None,
            "device": None,
        }
        self._status: dict[str, Any] = profile_status | {
            "state": "fråkopla" if self._profile else "ingen node",
            "error": None,
            "connected_since": None,
            "reconnect_attempt": 0,
            "local_node_id": None,
        }

    @staticmethod
    def _profile_status(profile: ConnectionProfile) -> dict[str, Any]:
        return {
            "connection_id": profile.profile_id,
            "connection_name": profile.name,
            "transport": profile.transport,
            "endpoint": profile.endpoint,
            "host": profile.host,
            "port": profile.port,
            "device": profile.device,
        }

    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._run, name="meshtastic", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        self._lost.set()
        self._switch_requested.set()
        self._fail_pending_node_actions("Meshtastic-sambandet blei stoppa")
        with self._lock:
            interface = self._interface
            self._interface = None
        if interface:
            try:
                interface.close()
            except Exception:
                LOG.debug("Feil ved lukking av Meshtastic-samband", exc_info=True)
        if self._thread and self._thread is not threading.current_thread():
            self._thread.join(timeout=10)
        self._set_status("fråkopla")

    def status(self) -> dict[str, Any]:
        with self._state_lock:
            return dict(self._status)

    def list_connections(self) -> dict[str, Any]:
        with self._lock:
            active_id = self._profile.profile_id if self._profile else None
        return {
            "active_profile_id": active_id,
            "profiles": [profile.as_dict() for profile in self.connections.list_profiles()],
        }

    def discover_connections(self) -> dict[str, Any]:
        result = self.list_connections()
        result["serial"] = discover_serial()
        subnets = (
            [self.settings.discovery_subnet]
            if self.settings.discovery_subnet
            else discover_local_subnets()
        )
        tcp: dict[str, dict[str, Any]] = {}
        errors: list[str] = []
        for subnet in subnets:
            try:
                for item in discover_tcp(subnet, self.settings.meshtastic_port):
                    tcp[str(item["target"])] = item
            except Exception as exc:
                errors.append(f"{subnet}: {exc}")
        result["tcp"] = list(tcp.values())
        result["tcp_error"] = "; ".join(errors) or None
        result["scanned_subnets"] = subnets
        return result

    def connect(
        self,
        *,
        profile_id: str | None = None,
        target: str | None = None,
        name: str | None = None,
    ) -> dict[str, Any]:
        if profile_id:
            profile = self.connections.activate(profile_id)
        elif target:
            profile = self.connections.save_and_activate(
                parse_connection_target(target, name=name)
            )
        else:
            raise ValueError("Oppgi profil-ID eller tilkoplingsmål")

        with self._lock:
            if profile == self._profile and self._interface is not None:
                return self.status()
            self._profile = profile
            interface = self._interface
            self._interface = None
            self._local_node_id = None
            self._switch_requested.set()
            self._lost.set()
        self._fail_pending_node_actions("Meshtastic-sambandet blei bytt")
        self.database.set_local_node(None)
        with self._state_lock:
            self._status.update(self._profile_status(profile))
            self._status["local_node_id"] = None
        self._set_status("koplar til", attempt=0)
        if interface:
            try:
                interface.close()
            except Exception:
                LOG.debug("Feil ved profilbyte", exc_info=True)
        return self.status()

    def _set_status(
        self,
        state: str,
        *,
        error: str | None = None,
        attempt: int | None = None,
    ) -> None:
        with self._state_lock:
            self._status["state"] = state
            self._status["error"] = error
            if attempt is not None:
                self._status["reconnect_attempt"] = attempt
            if state == "tilkopla":
                self._status["connected_since"] = now_iso()
            elif state in {"fråkopla", "feil"}:
                self._status["connected_since"] = None
            snapshot = dict(self._status)
        self.events.publish({"type": "status", "data": snapshot})

    def _run(self) -> None:
        from pubsub import pub

        pub.subscribe(self._on_receive, "meshtastic.receive")
        pub.subscribe(self._on_connection, "meshtastic.connection.established")
        pub.subscribe(self._on_lost, "meshtastic.connection.lost")
        attempt = 0
        try:
            while not self._stop.is_set():
                self._lost.clear()
                with self._lock:
                    profile = self._profile
                if profile is None:
                    self._set_status("ingen node", attempt=0)
                    self._switch_requested.wait()
                    self._switch_requested.clear()
                    continue
                with self._state_lock:
                    self._status.update(self._profile_status(profile))
                self._set_status("koplar til", attempt=attempt)
                LOG.info(
                    "Koplar til Meshtastic-node via %s %s",
                    profile.transport,
                    profile.endpoint,
                )
                try:
                    interface = self.interface_factory(profile)
                    with self._lock:
                        if profile != self._profile:
                            interface.close()
                            self._switch_requested.set()
                            raise RuntimeError("Tilkoplingsprofilen blei endra")
                        self._interface = interface
                    self._discover_local_node(interface)
                    self._sync_nodes(interface)
                    self._set_status("tilkopla", attempt=0)
                    LOG.info("Tilkopla Meshtastic-noden")
                    attempt = 0
                    while not self._stop.is_set() and not self._lost.wait(30):
                        self._sync_nodes(interface)
                    if self._stop.is_set():
                        break
                    if not self._switch_requested.is_set():
                        LOG.warning("Meshtastic-sambandet fall ut")
                except Exception as exc:
                    if not self._switch_requested.is_set():
                        LOG.error("Meshtastic-feil: %s", exc)
                        self._set_status("feil", error=str(exc), attempt=attempt + 1)
                finally:
                    self._fail_pending_node_actions("Meshtastic-sambandet blei brote")
                    with self._lock:
                        old_interface = self._interface
                        self._interface = None
                    if old_interface:
                        try:
                            old_interface.close()
                        except Exception:
                            LOG.debug("Feil ved sambandslukking", exc_info=True)
                if self._stop.is_set():
                    break
                if self._switch_requested.is_set():
                    self._switch_requested.clear()
                    attempt = 0
                    continue
                delay = reconnect_delay(attempt)
                attempt += 1
                self._set_status("fråkopla", attempt=attempt)
                LOG.info("Nytt tilkoplingsforsøk om %s sekund", delay)
                self._stop.wait(delay)
        finally:
            for callback, topic in (
                (self._on_receive, "meshtastic.receive"),
                (self._on_connection, "meshtastic.connection.established"),
                (self._on_lost, "meshtastic.connection.lost"),
            ):
                try:
                    pub.unsubscribe(callback, topic)
                except Exception:
                    LOG.debug("Klarte ikkje melde av %s", topic, exc_info=True)

    def _on_connection(self, interface: Interface, **_: Any) -> None:
        with self._lock:
            if interface is not self._interface:
                return
        self._discover_local_node(interface)
        self._sync_nodes(interface)
        self._set_status("tilkopla", attempt=0)

    def _on_lost(self, interface: Interface, **_: Any) -> None:
        with self._lock:
            if interface is not self._interface:
                return
        self._lost.set()
        self._fail_pending_node_actions("Meshtastic-sambandet fall ut")

    def _discover_local_node(self, interface: Interface) -> None:
        local_id: str | None = None
        try:
            getter = getattr(interface, "getMyNodeInfo", None)
            info = getter() if getter else None
            if isinstance(info, dict):
                user = info.get("user", {})
                local_id = user.get("id") if isinstance(user, dict) else None
                if not local_id and info.get("num") is not None:
                    local_id = f"!{int(info['num']) & 0xFFFFFFFF:08x}"
        except Exception:
            LOG.debug("Klarte ikkje hente lokal node-ID", exc_info=True)
        if not local_id:
            node_num = getattr(getattr(interface, "localNode", None), "nodeNum", None)
            if isinstance(node_num, int) and node_num >= 0:
                local_id = f"!{node_num & 0xFFFFFFFF:08x}"
        if local_id:
            self._local_node_id = local_id.lower()
            self.database.set_local_node(self._local_node_id)
            with self._state_lock:
                self._status["local_node_id"] = self._local_node_id

    def _sync_nodes(self, interface: Interface) -> None:
        nodes = getattr(interface, "nodes", None)
        if not isinstance(nodes, dict):
            return
        for node_id, data in list(nodes.items()):
            if not isinstance(data, dict):
                continue
            try:
                node = node_from_registry(node_id, data, self._local_node_id)
                self.database.upsert_node(node)
            except Exception:
                LOG.debug("Klarte ikkje lagre node %s", node_id, exc_info=True)
        self.events.publish({"type": "nodes"})

    def _on_receive(
        self, packet: dict[str, Any], interface: Interface | None = None, **_: Any
    ) -> None:
        try:
            if interface is not None:
                with self._lock:
                    if interface is not self._interface:
                        return
            message = parse_text_packet(packet, self._local_node_id)
            if message is None:
                if interface:
                    self._sync_nodes(interface)
                return
            metadata = dict(message.raw_metadata or {})
            with self._lock:
                profile = self._profile
            metadata["gateway_id"] = profile.profile_id
            metadata["gateway_transport"] = profile.transport
            metadata["gateway_endpoint"] = profile.endpoint
            message.raw_metadata = metadata
            inserted, message_id = self.database.insert_message(message)
            if not inserted:
                return
            message.id = message_id
            event = {"type": "message", "data": message.as_dict()}
            self.events.publish(event)
            label = "CH0" if message.kind == ConversationKind.PUBLIC else "DM"
            LOG.info(
                "Motteken %s via %s frå %s [%s]",
                label,
                message.transport,
                message.from_node or "ukjend",
                (message.from_node or "????")[-4:],
            )
        except Exception:
            LOG.exception("Feil under handsaming av Meshtastic-pakke")

    def send_public(self, text: str) -> dict[str, Any]:
        return self._send(text, destination="!ffffffff", public=True)

    def send_dm(self, node_id: str, text: str) -> dict[str, Any]:
        return self._send(text, destination=normalize_node_id(node_id), public=False)

    def start_node_action(self, action: str, node_id: str) -> dict[str, Any]:
        normalized_action = action.strip().lower()
        if normalized_action != "traceroute":
            raise ValueError(f"Ukjend nodehandling: {action}")
        return self._start_traceroute(normalize_node_id(node_id))

    def node_action_status(self, action_id: str) -> dict[str, Any]:
        with self._lock:
            action = self._node_actions.get(action_id)
            if action is None:
                raise ValueError("Fann ikkje nodehandlinga")
            return dict(action)

    def _start_traceroute(self, node_id: str) -> dict[str, Any]:
        from meshtastic.protobuf import mesh_pb2, portnums_pb2

        with self._lock:
            interface = self._interface
            state = self.status()["state"]
            if interface is None or state != "tilkopla":
                raise RuntimeError("Meshtastic-noden er ikkje tilkopla")
            local_node_id = self._local_node_id
            if not local_node_id:
                raise RuntimeError("Lokal node-ID er ikkje kjend enno")
            if node_id == local_node_id:
                raise ValueError("Kan ikkje køyre traceroute til den lokale noden")
            if self._active_traceroute_id is not None:
                raise RuntimeError("Ein traceroute er allereie i gang")

            action_id = uuid.uuid4().hex
            action = {
                "action_id": action_id,
                "action": "traceroute",
                "node_id": node_id,
                "status": "started",
                "started_at": now_iso(),
                "packet_id": None,
            }
            self._node_actions[action_id] = action
            self._active_traceroute_id = action_id
            self._trim_node_actions()

        self.events.publish({"type": "node_action", "data": dict(action)})

        def on_response(packet: dict[str, Any]) -> None:
            try:
                with self._lock:
                    current_interface = self._interface
                if interface is not current_interface:
                    raise NodeActionError("Meshtastic-sambandet blei bytt")
                result = parse_traceroute_response(
                    packet,
                    local_node_id=local_node_id,
                    target_node_id=node_id,
                )
            except Exception as exc:
                self._finish_node_action(action_id, error=str(exc))
            else:
                self._finish_node_action(action_id, result=result)

        try:
            sent = interface.sendData(
                mesh_pb2.RouteDiscovery(),
                destinationId=node_id,
                portNum=portnums_pb2.PortNum.TRACEROUTE_APP,
                wantResponse=True,
                onResponse=on_response,
                channelIndex=0,
                hopLimit=self._traceroute_hop_limit(interface),
            )
        except Exception as exc:
            self._finish_node_action(action_id, error=f"Klarte ikkje sende traceroute: {exc}")
            raise RuntimeError(f"Klarte ikkje sende traceroute: {exc}") from exc

        packet_id = _sent_packet_id(sent)
        def on_timeout() -> None:
            self._discard_response_handler(interface, packet_id)
            self._finish_node_action(
                action_id,
                error="Traceroute fekk ikkje svar innan tidsfristen",
            )

        timer = threading.Timer(TRACEROUTE_TIMEOUT_SECONDS, on_timeout)
        timer.daemon = True
        with self._lock:
            current = self._node_actions.get(action_id)
            if current is not None:
                current["packet_id"] = packet_id
            if current is not None and current.get("status") == "started":
                self._node_action_timers[action_id] = timer
                timer.start()
            return dict(current or action)

    @staticmethod
    def _traceroute_hop_limit(interface: Interface) -> int | None:
        local_node = getattr(interface, "localNode", None)
        local_config = getattr(local_node, "localConfig", None)
        lora = getattr(local_config, "lora", None)
        value = getattr(lora, "hop_limit", None)
        try:
            hop_limit = int(value)
        except (TypeError, ValueError):
            return None
        return hop_limit if 0 < hop_limit <= 7 else None

    @staticmethod
    def _discard_response_handler(interface: Interface, packet_id: int | None) -> None:
        handlers = getattr(interface, "responseHandlers", None)
        if packet_id is not None and isinstance(handlers, dict):
            handlers.pop(packet_id, None)

    def _finish_node_action(
        self,
        action_id: str,
        *,
        result: dict[str, Any] | None = None,
        error: str | None = None,
    ) -> None:
        with self._lock:
            action = self._node_actions.get(action_id)
            if action is None or action.get("status") != "started":
                return
            timer = self._node_action_timers.pop(action_id, None)
            if timer is not None and timer is not threading.current_thread():
                timer.cancel()
            action["status"] = "failed" if error else "completed"
            action["finished_at"] = now_iso()
            if error:
                action["error"] = error
            else:
                action["result"] = result or {}
            if self._active_traceroute_id == action_id:
                self._active_traceroute_id = None
            snapshot = dict(action)
        self.events.publish({"type": "node_action", "data": snapshot})

    def _fail_pending_node_actions(self, error: str) -> None:
        with self._lock:
            pending = [
                action_id
                for action_id, action in self._node_actions.items()
                if action.get("status") == "started"
            ]
        for action_id in pending:
            self._finish_node_action(action_id, error=error)

    def _trim_node_actions(self) -> None:
        completed = [
            action_id
            for action_id, action in self._node_actions.items()
            if action.get("status") != "started"
        ]
        while len(self._node_actions) > MAX_NODE_ACTIONS and completed:
            self._node_actions.pop(completed.pop(0), None)

    def _send(self, text: str, destination: str, public: bool) -> dict[str, Any]:
        text = validate_message_text(text)
        with self._lock:
            interface = self._interface
            profile = self._profile
            state = self.status()["state"]
            if interface is None or profile is None or state != "tilkopla":
                raise RuntimeError("Meshtastic-noden er ikkje tilkopla")

            pending_id: int | None = None
            stored = threading.Event()
            ack_lock = threading.Lock()
            early_status: list[MessageStatus] = []

            def onAckNak(packet: dict[str, Any]) -> None:  # noqa: N802
                status = MessageStatus.FAILED if _ack_failed(packet) else MessageStatus.ACKNOWLEDGED
                with ack_lock:
                    if pending_id is None or not stored.is_set():
                        early_status[:] = [status]
                        return
                if self.database.update_message_status(pending_id, status):
                    self.events.publish(
                        {
                            "type": "message_status",
                            "data": {"packet_id": pending_id, "status": str(status)},
                        }
                    )

            kwargs: dict[str, Any] = {
                "destinationId": "^all" if public else destination,
                "channelIndex": 0,
                "wantAck": not public,
            }
            if not public:
                kwargs["onResponse"] = onAckNak
            sent = interface.sendText(text, **kwargs)
            pending_id = _sent_packet_id(sent)

        message = Message(
            packet_id=pending_id,
            timestamp=now_iso(),
            from_node=self._local_node_id,
            to_node="!ffffffff" if public else destination,
            channel=0,
            kind=ConversationKind.PUBLIC if public else ConversationKind.DM,
            peer_node=None if public else destination,
            text=text,
            direction=Direction.OUTGOING,
            transport=Transport.UNKNOWN,
            want_ack=not public,
            status=MessageStatus.QUEUED,
            raw_metadata={
                "source": "meshpi",
                "packet_id": pending_id,
                "gateway_id": profile.profile_id,
                "gateway_transport": profile.transport,
                "gateway_endpoint": profile.endpoint,
            },
            is_read=True,
        )
        inserted, message_id = self.database.insert_message(message)
        message.id = message_id
        with ack_lock:
            stored.set()
            ack_status = early_status[0] if early_status else None
        if pending_id is not None and ack_status is not None:
            self.database.update_message_status(pending_id, ack_status)
            message.status = ack_status
            self.events.publish(
                {
                    "type": "message_status",
                    "data": {"packet_id": pending_id, "status": str(ack_status)},
                }
            )
        if inserted:
            self.events.publish({"type": "message", "data": message.as_dict()})
        LOG.info(
            "Sender %s til %s (%s byte)",
            "CH0" if public else "DM",
            destination,
            len(text.encode("utf-8")),
        )
        return message.as_dict()
