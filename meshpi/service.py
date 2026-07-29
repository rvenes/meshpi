from __future__ import annotations

import logging
import math
import threading
import time
import uuid
from typing import Any

from meshpi.ble import BLEDiscoveryError, discover_ble
from meshpi.channels import (
    ChannelBinding,
    dm_conversation_id,
    logical_channel_key,
    parse_dm_conversation_id,
    parse_public_conversation_id,
    provisional_channel_key,
    public_conversation_id,
)
from meshpi.config import Settings
from meshpi.connections import (
    ConnectionProfile,
    ConnectionStore,
    discover_local_subnets,
    discover_serial,
    discover_tcp,
    parse_connection_target,
    resolve_serial_profile,
)
from meshpi.database import Database
from meshpi.events import EventHub
from meshpi.models import (
    ConversationKind,
    Direction,
    Message,
    MessageStatus,
    Transport,
    node_num_to_id,
    normalize_node_id,
    now_iso,
    sanitize_terminal_text,
    validate_message_text,
)
from meshpi.node_actions import NodeActionError, parse_traceroute_response
from meshpi.observations import parse_position_packet, parse_telemetry_packet
from meshpi.packet import node_from_registry, parse_text_packet
from meshpi.transports import Interface, InterfaceFactory, default_interface_factory

LOG = logging.getLogger(__name__)
RECONNECT_DELAYS = (2, 5, 10, 30)
TRACEROUTE_TIMEOUT_SECONDS = 120
TRACEROUTE_COOLDOWN_SECONDS = 30
POSITION_EXCHANGE_TIMEOUT_SECONDS = 120
POSITION_EXCHANGE_COOLDOWN_SECONDS = 30
MAX_NODE_ACTIONS = 50


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


def _routing_error_reason(packet: Any) -> str | None:
    if not isinstance(packet, dict):
        return None
    routing = packet.get("decoded", {}).get("routing", {})
    if not isinstance(routing, dict):
        return None
    reason = routing.get("errorReason")
    if reason in (None, "NONE", 0):
        return None
    return sanitize_terminal_text(str(reason), 80)


def _packet_from_node(packet: Any) -> str | None:
    if not isinstance(packet, dict):
        return None
    value = packet.get("fromId")
    if isinstance(value, str) and value.startswith("!") and len(value) == 9:
        return value.lower()
    try:
        return node_num_to_id(int(packet["from"]))
    except (KeyError, TypeError, ValueError):
        return None


def _routing_request_id(packet: Any) -> int | None:
    if not isinstance(packet, dict):
        return None
    decoded = packet.get("decoded")
    if not isinstance(decoded, dict) or not isinstance(decoded.get("routing"), dict):
        return None
    value = decoded.get("requestId", decoded.get("request_id"))
    try:
        return int(value) if value is not None else None
    except (TypeError, ValueError):
        return None


def _ack_status(
    packet: Any,
    destination: str | None,
) -> MessageStatus:
    if _ack_failed(packet):
        return MessageStatus.FAILED
    sender = _packet_from_node(packet)
    if sender and destination and sender == destination.lower():
        return MessageStatus.DELIVERED
    return MessageStatus.ACKNOWLEDGED


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
        interrupted = self.database.fail_started_node_actions(
            "MeshPi-tenesta blei avslutta før traceroute fekk svar"
        )
        if interrupted:
            LOG.info("Merka %s avbrotne traceroute-forsøk som feila", interrupted)
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
        self._ble_operation_lock = threading.Lock()
        self._ble_connect_active = threading.Event()
        self._channel_sync_lock = threading.Lock()
        self._state_lock = threading.Lock()
        self._interface: Interface | None = None
        self._local_node_id: str | None = None
        self._channel_bindings: dict[int, ChannelBinding] = {}
        self._node_actions: dict[str, dict[str, Any]] = {}
        self._node_action_timers: dict[str, threading.Timer] = {}
        self._traceroute_cooldown_until = 0.0
        self._position_exchange_cooldown_until = 0.0
        profile_status = self._profile_status(self._profile) if self._profile else {
            "connection_id": None,
            "connection_name": None,
            "transport": None,
            "endpoint": None,
            "host": None,
            "port": None,
            "device": None,
            "ble_identifier": None,
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
            "ble_identifier": profile.ble_identifier,
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

    def discover_connections(self, *, include_ble: bool = True) -> dict[str, Any]:
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
        if include_ble:
            result.update(self.discover_ble_connections())
        else:
            result["ble"] = []
            result["ble_error"] = None
            result["ble_scanned"] = False
        return result

    def discover_ble_connections(self) -> dict[str, Any]:
        if not self._ble_operation_lock.acquire(blocking=False):
            return {
                "ble": [],
                "ble_error": (
                    "BLE-noden er i ferd med å kople til. Vent litt og prøv på nytt."
                    if self._ble_connect_active.is_set()
                    else "Eit BLE-søk er allereie i gang."
                ),
                "ble_scanned": False,
            }
        try:
            return {
                "ble": discover_ble(),
                "ble_error": None,
                "ble_scanned": True,
            }
        except BLEDiscoveryError as exc:
            return {
                "ble": [],
                "ble_error": str(exc),
                "ble_scanned": False,
            }
        finally:
            self._ble_operation_lock.release()

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
            profile = self._resolve_serial_profile(
                parse_connection_target(target, name=name)
            )
            profile = self.connections.save_and_activate(profile)
        else:
            raise ValueError("Oppgi profil-ID eller tilkoplingsmål")

        with self._lock:
            if profile == self._profile and self._interface is not None:
                return self.status()
            self._profile = profile
            interface = self._interface
            self._interface = None
            self._local_node_id = None
            self._channel_bindings = {}
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

    def _resolve_serial_profile(
        self, profile: ConnectionProfile
    ) -> ConnectionProfile:
        if profile.transport != "serial":
            return profile
        try:
            devices = discover_serial()
        except Exception:
            LOG.debug("Klarte ikkje kontrollere seriellportar", exc_info=True)
            return profile
        return resolve_serial_profile(profile, devices)

    def _refresh_active_serial_profile(
        self, profile: ConnectionProfile
    ) -> ConnectionProfile:
        resolved = self._resolve_serial_profile(profile)
        if resolved == profile:
            return profile
        with self._lock:
            if self._profile != profile:
                return profile
            self.connections.save_and_activate(resolved)
            self._profile = resolved
        if resolved.device != profile.device:
            LOG.info(
                "Fann att USB-noden på ny seriellsti: %s",
                resolved.device,
            )
        return resolved

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

    def _create_interface(self, profile: ConnectionProfile) -> Interface:
        if profile.transport == "ble":
            with self._ble_operation_lock:
                self._ble_connect_active.set()
                try:
                    return self.interface_factory(profile)
                finally:
                    self._ble_connect_active.clear()
        return self.interface_factory(profile)

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
                try:
                    profile = self._refresh_active_serial_profile(profile)
                    with self._state_lock:
                        self._status.update(self._profile_status(profile))
                    state = "søkjer" if profile.transport == "ble" else "koplar til"
                    self._set_status(state, attempt=attempt)
                    LOG.info(
                        "Koplar til Meshtastic-node via %s %s",
                        profile.transport,
                        profile.endpoint,
                    )
                    interface = self._create_interface(profile)
                    with self._lock:
                        if self._stop.is_set():
                            interface.close()
                            break
                        if profile != self._profile:
                            interface.close()
                            self._switch_requested.set()
                            raise RuntimeError("Tilkoplingsprofilen blei endra")
                        self._interface = interface
                    self._discover_local_node(interface)
                    self._sync_channels(interface)
                    self._sync_nodes(interface)
                    self._set_status("tilkopla", attempt=0)
                    LOG.info("Tilkopla Meshtastic-noden")
                    attempt = 0
                    while not self._stop.is_set() and not self._lost.wait(30):
                        self._sync_channels(interface)
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
        self._sync_channels(interface)
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

    def _sync_channels(self, interface: Interface) -> None:
        with self._channel_sync_lock:
            self._sync_channels_locked(interface)

    def _sync_channels_locked(self, interface: Interface) -> None:
        with self._lock:
            local_node_id = self._local_node_id
            profile = self._profile
        if not local_node_id:
            return
        local_node = getattr(interface, "localNode", None)
        raw_channels = getattr(local_node, "channels", None)
        bindings: list[ChannelBinding] = []
        if raw_channels:
            for raw in raw_channels:
                try:
                    index = int(raw.index)
                    role_value = int(raw.role)
                except (AttributeError, TypeError, ValueError):
                    continue
                if role_value == 0 or not 0 <= index <= 7:
                    continue
                settings = getattr(raw, "settings", None)
                name = sanitize_terminal_text(
                    getattr(settings, "name", "") if settings is not None else "",
                    80,
                ).strip()
                try:
                    channel_id_value = (
                        int(settings.id)
                        if settings is not None
                        else None
                    )
                except (AttributeError, TypeError, ValueError):
                    channel_id_value = None
                channel_id = channel_id_value if channel_id_value not in {0, None} else None
                binding = ChannelBinding(
                    local_node_id=local_node_id,
                    channel_index=index,
                    channel_key=logical_channel_key(
                        local_node_id,
                        index,
                        name,
                        channel_id,
                    ),
                    name=name,
                    role={1: "PRIMARY", 2: "SECONDARY"}.get(
                        role_value,
                        f"ROLE_{role_value}",
                    ),
                    meshtastic_id=channel_id,
                    gateway_profile_id=profile.profile_id if profile else None,
                )
                bindings.append(binding)
        with self._lock:
            self._channel_bindings = {
                binding.channel_index: binding for binding in bindings
            }
        self.database.sync_channel_bindings(
            local_node_id,
            profile.profile_id if profile else None,
            bindings,
        )
        for binding in bindings:
            self.database.rebind_provisional_channel(
                local_node_id,
                binding.channel_index,
                provisional_channel_key(local_node_id, binding.channel_index),
                binding.channel_key,
            )
            if binding.channel_index == 0:
                self.database.rebind_legacy_primary_dms(
                    local_node_id,
                    binding.channel_index,
                    binding.channel_key,
                )
        self.events.publish(
            {
                "type": "channels",
                "data": [binding.as_dict() for binding in bindings],
            }
        )

    def _channel_binding(self, channel_index: int) -> ChannelBinding:
        with self._lock:
            binding = self._channel_bindings.get(channel_index)
            local_node_id = self._local_node_id
            profile = self._profile
        if binding is not None:
            return binding
        if not local_node_id:
            raise RuntimeError("Lokal node-ID er ikkje kjend enno")
        binding = ChannelBinding(
            local_node_id=local_node_id,
            channel_index=channel_index,
            channel_key=provisional_channel_key(local_node_id, channel_index),
            name="",
            role="UNKNOWN",
            gateway_profile_id=profile.profile_id if profile else None,
            active=False,
        )
        return binding

    def list_channels(self) -> list[dict[str, Any]]:
        with self._lock:
            local_node_id = self._local_node_id
        if not local_node_id:
            return []
        return self.database.list_channel_bindings(
            local_node_id,
            active_only=True,
        )

    def _on_receive(
        self, packet: dict[str, Any], interface: Interface | None = None, **_: Any
    ) -> None:
        try:
            if interface is not None:
                with self._lock:
                    if interface is not self._interface:
                        return
            self._update_routing_ack(packet)
            self._store_observations(packet)
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
            channel_index = message.channel if message.channel is not None else 0
            with self._lock:
                known_channel = channel_index in self._channel_bindings
            if not known_channel and interface is not None:
                self._sync_channels(interface)
            binding = self._channel_binding(channel_index)
            message.channel_key = binding.channel_key
            message.local_node_id = self._local_node_id
            message.gateway_profile_id = profile.profile_id
            message.received_at = now_iso()
            message.conversation_id = (
                public_conversation_id(binding.channel_key)
                if message.kind == ConversationKind.PUBLIC
                else dm_conversation_id(
                    self._local_node_id or "",
                    message.peer_node or "",
                    binding.channel_key,
                )
            )
            inserted, message_id = self.database.insert_message(message)
            if not inserted:
                return
            message.id = message_id
            event = {"type": "message", "data": message.as_dict()}
            self.events.publish(event)
            label = (
                f"CH{channel_index}"
                if message.kind == ConversationKind.PUBLIC
                else f"DM/CH{channel_index}"
            )
            LOG.info(
                "Motteken %s via %s frå %s [%s]",
                label,
                message.transport,
                message.from_node or "ukjend",
                (message.from_node or "????")[-4:],
            )
        except Exception:
            LOG.exception("Feil under handsaming av Meshtastic-pakke")

    def _store_observations(self, packet: dict[str, Any]) -> None:
        with self._lock:
            profile = self._profile
            gateway_node_id = self._local_node_id
        gateway = {
            "gateway_profile_id": profile.profile_id if profile else None,
            "gateway_node_id": gateway_node_id,
            "gateway_transport": profile.transport if profile else None,
        }

        for parsed in parse_telemetry_packet(packet):
            sample = parsed | gateway
            if not self.database.insert_telemetry(sample):
                continue
            self.events.publish({"type": "telemetry", "data": sample})
            LOG.info(
                "Lagra %s-telemetri frå %s via %s",
                sample["kind"],
                sample["node_id"],
                gateway_node_id or "ukjend gateway",
            )

        parsed_position = parse_position_packet(packet)
        if parsed_position is None:
            return
        position = parsed_position | gateway
        if self.database.insert_position(position):
            self.events.publish({"type": "position", "data": position})
            LOG.info(
                "Lagra posisjon frå %s via %s",
                position["node_id"],
                gateway_node_id or "ukjend gateway",
            )

    def _update_routing_ack(self, packet: dict[str, Any]) -> None:
        request_id = _routing_request_id(packet)
        if request_id is None:
            return
        with self._lock:
            local_node_id = self._local_node_id
        if not local_node_id:
            return
        outgoing = self.database.outgoing_message(
            request_id,
            local_node_id,
        )
        if outgoing is None:
            return
        status = _ack_status(packet, outgoing.get("to_node"))
        failure_reason = _routing_error_reason(packet)
        if self.database.update_message_status(
            request_id,
            status,
            local_node_id,
            failure_reason,
        ):
            self.events.publish(
                {
                    "type": "message_status",
                    "data": {
                        "packet_id": request_id,
                        "status": str(status),
                        "failure_reason": failure_reason,
                    },
                }
            )

    def send_public(
        self,
        text: str,
        conversation: str | None = None,
        channel_index: int | None = None,
    ) -> dict[str, Any]:
        return self._send(
            text,
            destination="!ffffffff",
            public=True,
            conversation=conversation,
            channel_index=channel_index,
        )

    def send_dm(
        self,
        node_id: str,
        text: str,
        conversation: str | None = None,
        channel_index: int | None = None,
    ) -> dict[str, Any]:
        return self._send(
            text,
            destination=normalize_node_id(node_id),
            public=False,
            conversation=conversation,
            channel_index=channel_index,
        )

    def start_node_action(self, action: str, node_id: str) -> dict[str, Any]:
        normalized_action = action.strip().lower()
        normalized_node_id = normalize_node_id(node_id)
        if normalized_action == "traceroute":
            return self._start_traceroute(normalized_node_id)
        if normalized_action == "position_exchange":
            return self._start_position_exchange(normalized_node_id)
        raise ValueError(f"Ukjend nodehandling: {action}")

    def node_action_status(self, action_id: str) -> dict[str, Any]:
        with self._lock:
            action = self._node_actions.get(action_id)
            if action is None:
                raise ValueError("Fann ikkje nodehandlinga")
            return dict(action)

    def node_action_availability(self, action: str, node_id: str) -> dict[str, Any]:
        normalized_action = action.strip().lower()
        if normalized_action not in {"traceroute", "position_exchange"}:
            raise ValueError(f"Ukjend nodehandling: {action}")
        normalized_node_id = normalize_node_id(node_id)
        with self._lock:
            remaining = (
                self._traceroute_cooldown_remaining()
                if normalized_action == "traceroute"
                else self._position_exchange_cooldown_remaining()
            )
            connected = self._interface is not None and self.status()["state"] == "tilkopla"
            local = normalized_node_id == self._local_node_id
        label = (
            "Traceroute"
            if normalized_action == "traceroute"
            else "Posisjonsutveksling"
        )
        reason = None
        if not connected:
            reason = "Meshtastic-noden er ikkje tilkopla"
        elif local:
            reason = (
                "Kan ikkje køyre traceroute til den lokale noden"
                if normalized_action == "traceroute"
                else "Kan ikkje utveksle posisjon med den lokale noden"
            )
        elif remaining:
            reason = f"{label} kan sendast igjen om {remaining} sekund"
        return {
            "action": normalized_action,
            "node_id": normalized_node_id,
            "available": reason is None,
            "cooldown_seconds": remaining,
            "reason": reason,
        }

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
            cooldown = self._traceroute_cooldown_remaining()
            if cooldown:
                raise RuntimeError(
                    f"Traceroute kan sendast igjen om {cooldown} sekund"
                )

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
            self._trim_node_actions()
        self.database.upsert_node_action(action)

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
        start_timer = False
        with self._lock:
            current = self._node_actions.get(action_id)
            if current is not None:
                current["packet_id"] = packet_id
                current["cooldown_seconds"] = TRACEROUTE_COOLDOWN_SECONDS
                self._traceroute_cooldown_until = (
                    time.monotonic() + TRACEROUTE_COOLDOWN_SECONDS
                )
            if current is not None and current.get("status") == "started":
                self._node_action_timers[action_id] = timer
                start_timer = True
            snapshot = dict(current or action)
            self.database.upsert_node_action(snapshot)
            if snapshot.get("status") == "started":
                self.events.publish({"type": "node_action", "data": snapshot})
        if start_timer:
            timer.start()
            LOG.info("Traceroute sendt til %s", node_id)
        return snapshot

    def _start_position_exchange(self, node_id: str) -> dict[str, Any]:
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
                raise ValueError(
                    "Kan ikkje be den lokale noden utveksle posisjon med seg sjølv"
                )
            cooldown = self._position_exchange_cooldown_remaining()
            if cooldown:
                raise RuntimeError(
                    "Posisjonsutveksling kan sendast igjen om "
                    f"{cooldown} sekund"
                )
            local_position = self._local_position_for_exchange(
                interface,
                local_node_id,
            )
            precision = self._position_precision_bits(interface, local_position)
            local_position_shared = local_position is not None and precision not in {
                None,
                0,
            }
            if local_position is None:
                share_reason = "ingen lokal posisjon er tilgjengeleg"
            elif precision is None:
                share_reason = "posisjonspresisjonen er ukjend"
            elif precision == 0:
                share_reason = "posisjonsdeling er slått av for kanalen"
            else:
                share_reason = None

            action_id = uuid.uuid4().hex
            action = {
                "action_id": action_id,
                "action": "position_exchange",
                "node_id": node_id,
                "status": "started",
                "started_at": now_iso(),
                "packet_id": None,
                "local_position_shared": local_position_shared,
                "local_position_precision_bits": (
                    precision if local_position_shared else None
                ),
                "local_position_share_reason": share_reason,
            }
            self._node_actions[action_id] = action
            self._trim_node_actions()
            self._position_exchange_cooldown_until = (
                time.monotonic() + POSITION_EXCHANGE_COOLDOWN_SECONDS
            )
        self.database.upsert_node_action(action)

        def on_response(packet: dict[str, Any]) -> None:
            try:
                with self._lock:
                    current_interface = self._interface
                if interface is not current_interface:
                    raise NodeActionError("Meshtastic-sambandet blei bytt")
                decoded = packet.get("decoded")
                if not isinstance(decoded, dict):
                    raise NodeActionError("Posisjonssvaret manglar dekoda data")
                portnum = decoded.get("portnum")
                if portnum in {"ROUTING_APP", 5}:
                    routing = decoded.get("routing")
                    reason = (
                        routing.get("errorReason")
                        if isinstance(routing, dict)
                        else "UKJEND_FEIL"
                    )
                    raise NodeActionError(
                        f"Posisjonsførespurnaden feila ({reason or 'UKJEND_FEIL'})"
                    )
                if portnum not in {"POSITION_APP", 3}:
                    raise NodeActionError("Mottok feil svartype for posisjon")
            except Exception as exc:
                self._finish_node_action(action_id, error=str(exc))
            else:
                self._finish_node_action(
                    action_id,
                    result={
                        "position_received": True,
                        "local_position_shared": local_position_shared,
                        "local_position_precision_bits": (
                            precision if local_position_shared else None
                        ),
                        "local_position_share_reason": share_reason,
                    },
                )

        payload = mesh_pb2.Position()
        if local_position is not None and precision not in {None, 0}:
            payload.latitude_i = self._mask_position_coordinate(
                round(float(local_position["latitude"]) * 10**7),
                precision,
            )
            payload.longitude_i = self._mask_position_coordinate(
                round(float(local_position["longitude"]) * 10**7),
                precision,
            )
            if local_position.get("altitude_msl") is not None:
                payload.altitude = int(local_position["altitude_msl"])
            payload.precision_bits = precision

        try:
            sent = interface.sendData(
                payload,
                destinationId=node_id,
                portNum=portnums_pb2.PortNum.POSITION_APP,
                wantResponse=True,
                onResponse=on_response,
                channelIndex=0,
                hopLimit=self._traceroute_hop_limit(interface),
            )
        except Exception as exc:
            with self._lock:
                self._position_exchange_cooldown_until = 0.0
            self._finish_node_action(
                action_id,
                error=f"Klarte ikkje sende posisjonsførespurnad: {exc}",
            )
            raise RuntimeError(
                f"Klarte ikkje sende posisjonsførespurnad: {exc}"
            ) from exc

        packet_id = _sent_packet_id(sent)

        def on_timeout() -> None:
            self._discard_response_handler(interface, packet_id)
            self._finish_node_action(
                action_id,
                error="Posisjonsførespurnaden fekk ikkje svar innan tidsfristen",
            )

        timer = threading.Timer(POSITION_EXCHANGE_TIMEOUT_SECONDS, on_timeout)
        timer.daemon = True
        start_timer = False
        with self._lock:
            current = self._node_actions.get(action_id)
            if current is not None:
                current["packet_id"] = packet_id
            if current is not None and current.get("status") == "started":
                self._node_action_timers[action_id] = timer
                start_timer = True
            snapshot = dict(current or action)
            self.database.upsert_node_action(snapshot)
            if snapshot.get("status") == "started":
                self.events.publish({"type": "node_action", "data": snapshot})
        if start_timer:
            timer.start()
            LOG.info("Posisjonsførespurnad sendt til %s", node_id)
        return snapshot

    def _local_position_for_exchange(
        self,
        interface: Interface,
        local_node_id: str,
    ) -> dict[str, Any] | None:
        saved = self.database.node_observation_summary(local_node_id).get(
            "latest_position"
        )
        if isinstance(saved, dict):
            return saved
        nodes = getattr(interface, "nodes", None)
        if not isinstance(nodes, dict):
            return None
        local = nodes.get(local_node_id)
        if not isinstance(local, dict):
            return None
        position = local.get("position")
        if not isinstance(position, dict):
            return None
        return parse_position_packet(
            {
                "fromId": local_node_id,
                "decoded": {
                    "portnum": "POSITION_APP",
                    "position": position,
                },
            }
        )

    @staticmethod
    def _position_precision_bits(
        interface: Interface,
        position: dict[str, Any] | None,
    ) -> int | None:
        local_node = getattr(interface, "localNode", None)
        channels = getattr(local_node, "channels", None)
        if channels:
            try:
                settings = getattr(channels[0], "settings", None)
                if settings is None:
                    return 0
                has_field = getattr(settings, "HasField", None)
                if callable(has_field) and not has_field("module_settings"):
                    # Meshtastic-fastvaren behandlar manglande
                    # modulinnstillingar som avslått posisjonsdeling.
                    return 0
                module_settings = getattr(settings, "module_settings", None)
                configured = getattr(
                    module_settings,
                    "position_precision",
                    None,
                )
                if configured is not None:
                    bits = int(configured)
                    return max(0, min(bits, 32))
            except (IndexError, TypeError, ValueError):
                pass
        if not isinstance(position, dict):
            return None
        try:
            bits = int(position.get("precision_bits"))
        except (TypeError, ValueError):
            return None
        return max(0, min(bits, 32))

    @staticmethod
    def _mask_position_coordinate(value: int, precision_bits: int) -> int:
        if precision_bits >= 32:
            return value
        unsigned = value & 0xFFFFFFFF
        mask = (0xFFFFFFFF << (32 - precision_bits)) & 0xFFFFFFFF
        masked = unsigned & mask
        masked = (masked + (1 << (31 - precision_bits))) & 0xFFFFFFFF
        return masked - 0x100000000 if masked & 0x80000000 else masked

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

    def _traceroute_cooldown_remaining(self) -> int:
        return max(0, math.ceil(self._traceroute_cooldown_until - time.monotonic()))

    def _position_exchange_cooldown_remaining(self) -> int:
        return max(
            0,
            math.ceil(
                self._position_exchange_cooldown_until - time.monotonic()
            ),
        )

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
            snapshot = dict(action)
        self.database.upsert_node_action(snapshot)
        self.events.publish({"type": "node_action", "data": snapshot})
        label = (
            "Traceroute"
            if snapshot.get("action") == "traceroute"
            else "Posisjonsførespurnad"
        )
        if error:
            LOG.warning("%s til %s feila: %s", label, snapshot["node_id"], error)
        else:
            LOG.info("%s til %s er ferdig", label, snapshot["node_id"])

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

    def _send(
        self,
        text: str,
        destination: str,
        public: bool,
        *,
        conversation: str | None = None,
        channel_index: int | None = None,
    ) -> dict[str, Any]:
        text = validate_message_text(text)
        with self._lock:
            interface = self._interface
            profile = self._profile
            state = self.status()["state"]
            if interface is None or profile is None or state != "tilkopla":
                raise RuntimeError("Meshtastic-noden er ikkje tilkopla")
            local_node_id = self._local_node_id
            if not local_node_id:
                raise RuntimeError("Lokal node-ID er ikkje kjend enno")
            requested_key: str | None = None
            if conversation is not None:
                if public:
                    requested_key = parse_public_conversation_id(conversation)
                else:
                    route_local, route_peer, requested_key = (
                        parse_dm_conversation_id(conversation)
                    )
                    if route_local != local_node_id:
                        raise RuntimeError(
                            "Den valde samtaleruta høyrer til ein annan lokal node"
                        )
                    if route_peer != destination:
                        raise ValueError(
                            "Mottakaren samsvarar ikkje med den valde samtaleruta"
                        )
            if requested_key is not None:
                requested_index = next(
                    (
                        index
                        for index, binding in self._channel_bindings.items()
                        if binding.channel_key == requested_key and binding.active
                    ),
                    None,
                )
                if requested_index is None:
                    raise RuntimeError(
                        "Den valde kanalen finst ikkje på den aktive noden"
                    )
                if channel_index is not None and int(channel_index) != requested_index:
                    raise ValueError(
                        "Kanalindeksen samsvarar ikkje med den valde samtaleruta"
                    )
                channel_index = requested_index
            selected_index = 0 if channel_index is None else int(channel_index)
            if not 0 <= selected_index <= 7:
                raise ValueError("Kanalindeksen må vere mellom 0 og 7")
            binding = self._channel_bindings.get(selected_index)
            if binding is None or not binding.active:
                raise RuntimeError(
                    "Den valde kanalen finst ikkje på den aktive noden"
                )
            if requested_key is not None and binding.channel_key != requested_key:
                raise RuntimeError(
                    "Den valde kanalen finst ikkje på den aktive noden"
                )

            pending_id: int | None = None
            stored = threading.Event()
            ack_lock = threading.Lock()
            early_status: list[tuple[MessageStatus, str | None]] = []

            def onAckNak(packet: dict[str, Any]) -> None:  # noqa: N802
                status = _ack_status(packet, destination)
                failure_reason = _routing_error_reason(packet)
                with ack_lock:
                    if pending_id is None or not stored.is_set():
                        early_status[:] = [(status, failure_reason)]
                        return
                if self.database.update_message_status(
                    pending_id,
                    status,
                    local_node_id,
                    failure_reason,
                ):
                    if failure_reason:
                        LOG.warning(
                            "DM-pakke %s feila: %s",
                            pending_id,
                            failure_reason,
                        )
                    self.events.publish(
                        {
                            "type": "message_status",
                            "data": {
                                "packet_id": pending_id,
                                "status": str(status),
                                "failure_reason": failure_reason,
                            },
                        }
                    )

            kwargs: dict[str, Any] = {
                "destinationId": "^all" if public else destination,
                "channelIndex": selected_index,
                "wantAck": not public,
            }
            if not public:
                kwargs["onResponse"] = onAckNak
            sent = interface.sendText(text, **kwargs)
            pending_id = _sent_packet_id(sent)

        message = Message(
            packet_id=pending_id,
            timestamp=now_iso(),
            from_node=local_node_id,
            to_node="!ffffffff" if public else destination,
            channel=selected_index,
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
            conversation_id=(
                public_conversation_id(binding.channel_key)
                if public
                else dm_conversation_id(
                    local_node_id,
                    destination,
                    binding.channel_key,
                )
            ),
            channel_key=binding.channel_key,
            local_node_id=local_node_id,
            gateway_profile_id=profile.profile_id,
            received_at=now_iso(),
        )
        inserted, message_id = self.database.insert_message(message)
        message.id = message_id
        with ack_lock:
            stored.set()
            early_result = early_status[0] if early_status else None
        ack_status = early_result[0] if early_result else None
        failure_reason = early_result[1] if early_result else None
        if pending_id is not None and ack_status is not None:
            self.database.update_message_status(
                pending_id,
                ack_status,
                local_node_id,
                failure_reason,
            )
            message.status = ack_status
            if failure_reason:
                message.raw_metadata["failure_reason"] = failure_reason
                LOG.warning(
                    "DM-pakke %s feila: %s",
                    pending_id,
                    failure_reason,
                )
            self.events.publish(
                {
                    "type": "message_status",
                    "data": {
                        "packet_id": pending_id,
                        "status": str(ack_status),
                        "failure_reason": failure_reason,
                    },
                }
            )
        if inserted:
            self.events.publish({"type": "message", "data": message.as_dict()})
        LOG.info(
            "Sender %s til %s (%s byte)",
            f"CH{selected_index}" if public else f"DM/CH{selected_index}",
            destination,
            len(text.encode("utf-8")),
        )
        return message.as_dict()
