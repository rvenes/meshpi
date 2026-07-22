from __future__ import annotations

import unicodedata
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from enum import Enum
from typing import Any

MAX_MESSAGE_BYTES = 237
BROADCAST_NUM = 0xFFFFFFFF
BROADCAST_IDS = {"^all", "!ffffffff", "ffffffff"}


def sanitize_terminal_text(value: Any, max_bytes: int | None = None) -> str:
    """Fjern terminal-, bidi- og andre usynlege kontrollteikn frå ekstern tekst."""
    clean = "".join(
        " " if unicodedata.category(char) in {"Cc", "Cf", "Cs"} else char
        for char in str(value or "")
    )
    if max_bytes is None:
        return clean
    encoded = clean.encode("utf-8")
    if len(encoded) <= max_bytes:
        return clean
    return encoded[:max_bytes].decode("utf-8", errors="ignore")


def _contains_unsafe_control(value: str) -> bool:
    return any(unicodedata.category(char) in {"Cc", "Cf", "Cs"} for char in value)


class ValueEnum(str, Enum):
    def __str__(self) -> str:
        return self.value


class ConversationKind(ValueEnum):
    PUBLIC = "public"
    DM = "dm"


class Direction(ValueEnum):
    INCOMING = "inn"
    OUTGOING = "ut"


class Transport(ValueEnum):
    RF = "RF"
    MQTT = "MQTT"
    UNKNOWN = "Ukjend"


class MessageStatus(ValueEnum):
    RECEIVED = "motteken"
    QUEUED = "sendt"
    ACKNOWLEDGED = "ACK"
    DELIVERED = "levert"
    FAILED = "feila"


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def short_node_id(node_id: str | None) -> str:
    if not node_id:
        return "????"
    clean = node_id.removeprefix("!").lower()
    return clean[-4:].rjust(4, "0")


def node_num_to_id(node_num: int | None) -> str | None:
    if node_num is None:
        return None
    return f"!{node_num & 0xFFFFFFFF:08x}"


def normalize_node_id(value: str) -> str:
    node_id = value.strip().lower()
    if node_id.startswith("!"):
        node_id = node_id[1:]
    if len(node_id) != 8 or any(ch not in "0123456789abcdef" for ch in node_id):
        raise ValueError("Node-ID må vere åtte heksadesimale teikn, til dømes !710365c8")
    if node_id == "ffffffff":
        raise ValueError("Broadcast-ID kan ikkje brukast som DM-mottakar")
    return f"!{node_id}"


def validate_message_text(text: str) -> str:
    clean = text.strip()
    if not clean:
        raise ValueError("Meldinga kan ikkje vere tom")
    if _contains_unsafe_control(clean):
        raise ValueError("Meldinga kan ikkje innehalde kontrollteikn")
    length = len(clean.encode("utf-8"))
    if length > MAX_MESSAGE_BYTES:
        raise ValueError(
            f"Meldinga er {length} byte; maksimum er {MAX_MESSAGE_BYTES} UTF-8-byte"
        )
    return clean


@dataclass(slots=True)
class Message:
    packet_id: int | None
    timestamp: str
    from_node: str | None
    to_node: str | None
    channel: int | None
    kind: ConversationKind
    peer_node: str | None
    text: str
    direction: Direction
    transport: Transport
    rssi: int | None = None
    snr: float | None = None
    hop_limit: int | None = None
    hop_start: int | None = None
    want_ack: bool = False
    status: MessageStatus = MessageStatus.RECEIVED
    raw_metadata: dict[str, Any] | None = None
    id: int | None = None
    is_read: bool = False

    def as_dict(self) -> dict[str, Any]:
        result = asdict(self)
        for key in ("kind", "direction", "transport", "status"):
            result[key] = str(result[key])
        result["short_from"] = short_node_id(self.from_node)
        result["short_to"] = short_node_id(self.to_node)
        return result


@dataclass(slots=True)
class Node:
    node_id: str
    node_num: int | None = None
    long_name: str | None = None
    short_name: str | None = None
    hw_model: str | None = None
    role: str | None = None
    last_heard: int | None = None
    battery_level: int | None = None
    voltage: float | None = None
    snr: float | None = None
    rssi: int | None = None
    hops_away: int | None = None
    transport: Transport = Transport.UNKNOWN
    can_receive_dm: bool | None = None
    is_local: bool = False
    updated_at: str = ""

    def as_dict(self) -> dict[str, Any]:
        result = asdict(self)
        result["transport"] = str(self.transport)
        result["short_id"] = short_node_id(self.node_id)
        return result
