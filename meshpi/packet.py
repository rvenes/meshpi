from __future__ import annotations

import base64
from datetime import datetime, timezone
from enum import Enum
from typing import Any

from meshpi.models import (
    BROADCAST_IDS,
    BROADCAST_NUM,
    ConversationKind,
    Direction,
    Message,
    MessageStatus,
    Node,
    Transport,
    node_num_to_id,
    now_iso,
    sanitize_terminal_text,
)


def _packet_id(packet: dict[str, Any]) -> int | None:
    value = packet.get("id")
    try:
        return int(value) if value is not None else None
    except (TypeError, ValueError):
        return None


def _node_id(packet: dict[str, Any], id_key: str, num_key: str) -> str | None:
    value = packet.get(id_key)
    if isinstance(value, str) and value:
        lowered = value.lower()
        if lowered in BROADCAST_IDS:
            return "!ffffffff"
        if lowered.startswith("!") and len(lowered) == 9:
            return lowered
    try:
        return node_num_to_id(int(packet[num_key]))
    except (KeyError, TypeError, ValueError):
        return None


def _is_broadcast(packet: dict[str, Any], to_node: str | None) -> bool:
    if to_node in {"!ffffffff", "^all"}:
        return True
    try:
        return int(packet.get("to")) == BROADCAST_NUM
    except (TypeError, ValueError):
        return False


def _timestamp(packet: dict[str, Any]) -> str:
    value = packet.get("rxTime")
    try:
        return datetime.fromtimestamp(int(value), tz=timezone.utc).isoformat(timespec="seconds")
    except (TypeError, ValueError, OSError):
        return now_iso()


def _transport(packet: dict[str, Any]) -> Transport:
    if packet.get("viaMqtt") is True:
        return Transport.MQTT
    mechanism = packet.get("transportMechanism")
    if isinstance(mechanism, Enum):
        mechanism = mechanism.name
    normalized = str(mechanism or "").upper()
    if normalized in {"TRANSPORT_MQTT", "MQTT"}:
        return Transport.MQTT
    if normalized in {
        "TRANSPORT_LORA",
        "TRANSPORT_LORA_ALT1",
        "TRANSPORT_LORA_ALT2",
        "TRANSPORT_LORA_ALT3",
        "LORA",
        "RF",
    }:
        return Transport.RF
    return Transport.UNKNOWN


def _safe_json(value: Any, depth: int = 0) -> Any:
    if depth > 8:
        return "<maks djupn>"
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, bytes):
        return {"bytes_base64": base64.b64encode(value).decode("ascii")}
    if isinstance(value, Enum):
        return value.name
    if isinstance(value, dict):
        return {
            str(key): _safe_json(item, depth + 1)
            for key, item in value.items()
            if str(key) not in {"raw", "publicKey", "privateKey", "psk"}
        }
    if isinstance(value, (list, tuple)):
        return [_safe_json(item, depth + 1) for item in value]
    return str(value)


def parse_text_packet(packet: dict[str, Any], local_node_id: str | None) -> Message | None:
    decoded = packet.get("decoded")
    if not isinstance(decoded, dict):
        return None
    portnum = decoded.get("portnum")
    if portnum not in {"TEXT_MESSAGE_APP", 1}:
        return None
    text = decoded.get("text")
    if not isinstance(text, str):
        payload = decoded.get("payload")
        if not isinstance(payload, bytes):
            return None
        try:
            text = payload.decode("utf-8")
        except UnicodeDecodeError:
            return None
    if not text:
        return None
    text = sanitize_terminal_text(text, max_bytes=237).strip()
    if not text:
        return None

    from_node = _node_id(packet, "fromId", "from")
    to_node = _node_id(packet, "toId", "to")
    local = local_node_id.lower() if local_node_id else None
    outgoing = bool(local and from_node == local)
    direction = Direction.OUTGOING if outgoing else Direction.INCOMING
    broadcast = _is_broadcast(packet, to_node)
    try:
        channel = int(packet.get("channel", 0))
    except (TypeError, ValueError):
        channel = None

    if broadcast:
        if channel != 0:
            return None
        kind = ConversationKind.PUBLIC
        peer_node = None
    else:
        involved = local and (from_node == local or to_node == local)
        if not involved:
            return None
        kind = ConversationKind.DM
        peer_node = to_node if outgoing else from_node

    def number(name: str, number_type: type[int] | type[float]) -> int | float | None:
        value = packet.get(name)
        try:
            return number_type(value) if value is not None else None
        except (TypeError, ValueError):
            return None

    return Message(
        packet_id=_packet_id(packet),
        timestamp=_timestamp(packet),
        from_node=from_node,
        to_node=to_node,
        channel=channel,
        kind=kind,
        peer_node=peer_node,
        text=text,
        direction=direction,
        transport=_transport(packet),
        rssi=number("rxRssi", int),
        snr=number("rxSnr", float),
        hop_limit=number("hopLimit", int),
        hop_start=number("hopStart", int),
        want_ack=bool(packet.get("wantAck", False)),
        status=MessageStatus.QUEUED if outgoing else MessageStatus.RECEIVED,
        raw_metadata=_safe_json(packet),
        is_read=outgoing,
    )


def node_from_registry(
    node_id: str,
    data: dict[str, Any],
    local_node_id: str | None,
) -> Node:
    user = data.get("user") if isinstance(data.get("user"), dict) else {}
    metrics = (
        data.get("deviceMetrics") if isinstance(data.get("deviceMetrics"), dict) else {}
    )
    last_packet = data.get("lastReceived") if isinstance(data.get("lastReceived"), dict) else {}
    canonical = str(user.get("id") or node_id).lower()
    if not canonical.startswith("!"):
        try:
            canonical = node_num_to_id(int(data.get("num"))) or canonical
        except (TypeError, ValueError):
            canonical = node_id.lower()
    unmessagable = user.get("isUnmessagable")
    can_receive = None if unmessagable is None else not bool(unmessagable)
    try:
        node_num = int(data["num"]) if data.get("num") is not None else None
    except (TypeError, ValueError):
        node_num = None
    return Node(
        node_id=canonical,
        node_num=node_num,
        long_name=sanitize_terminal_text(user.get("longName"), 160) or None,
        short_name=sanitize_terminal_text(user.get("shortName"), 80) or None,
        hw_model=sanitize_terminal_text(user.get("hwModel"), 80) or None,
        role=sanitize_terminal_text(user.get("role"), 80) or None,
        last_heard=data.get("lastHeard"),
        battery_level=metrics.get("batteryLevel"),
        voltage=metrics.get("voltage"),
        snr=data.get("snr"),
        rssi=last_packet.get("rxRssi"),
        hops_away=data.get("hopsAway"),
        transport=_transport(last_packet),
        can_receive_dm=can_receive,
        is_local=bool(local_node_id and canonical == local_node_id.lower()),
        updated_at=now_iso(),
    )
