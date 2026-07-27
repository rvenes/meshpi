from __future__ import annotations

from dataclasses import asdict, dataclass
from urllib.parse import quote

from meshpi.models import normalize_node_id, sanitize_terminal_text


def _component(value: str) -> str:
    return quote(value.strip(), safe="")


def logical_channel_key(
    local_node_id: str,
    channel_index: int,
    name: str = "",
    meshtastic_id: int | None = None,
) -> str:
    """Lag ein kanalidentitet utan å lese eller avleie noko frå PSK-en."""
    safe_name = sanitize_terminal_text(name, 80).strip()
    if meshtastic_id not in {None, 0}:
        return f"global:{_component(safe_name)}:{int(meshtastic_id)}"
    return (
        f"local:{local_node_id.lower()}:{int(channel_index)}:"
        f"{_component(safe_name)}"
    )


def provisional_channel_key(local_node_id: str, channel_index: int) -> str:
    """Lag ein mellombels nøkkel når kanaloversikta ikkje er klar enno."""
    return f"provisional:{local_node_id.lower()}:{int(channel_index)}"


def public_conversation_id(channel_key: str) -> str:
    return f"channel:{channel_key}"


def dm_conversation_id(
    local_node_id: str,
    peer_node: str,
    channel_key: str,
) -> str:
    return (
        f"dm:{local_node_id.lower()}:{peer_node.lower()}:"
        f"{channel_key}"
    )


def parse_public_conversation_id(value: str) -> str:
    conversation = value.strip()
    if not conversation.startswith("channel:"):
        raise ValueError("Samtale-ID-en må starte med channel:")
    channel_key = conversation.removeprefix("channel:")
    if not _valid_channel_key(channel_key):
        raise ValueError("Ugyldig samtale-ID for public-kanal")
    return channel_key


def parse_dm_conversation_id(value: str) -> tuple[str, str, str]:
    conversation = value.strip()
    parts = conversation.split(":", 3)
    if len(parts) != 4 or parts[0] != "dm" or not parts[3]:
        raise ValueError("Ugyldig samtale-ID for direkte melding")
    if (
        len(conversation) > 768
        or _has_control_characters(conversation)
        or not _valid_channel_key(parts[3])
    ):
        raise ValueError("Ugyldig samtale-ID for direkte melding")
    return normalize_node_id(parts[1]), normalize_node_id(parts[2]), parts[3]


def _has_control_characters(value: str) -> bool:
    return any(ord(character) < 32 or ord(character) == 127 for character in value)


def _valid_channel_key(value: str) -> bool:
    return bool(
        value
        and len(value) <= 512
        and not _has_control_characters(value)
        and value.startswith(("global:", "local:", "legacy:", "provisional:"))
    )


@dataclass(frozen=True, slots=True)
class ChannelBinding:
    local_node_id: str
    channel_index: int
    channel_key: str
    name: str
    role: str
    meshtastic_id: int | None = None
    gateway_profile_id: str | None = None
    active: bool = True

    @property
    def display_name(self) -> str:
        return self.name or f"Kanal {self.channel_index}"

    @property
    def conversation_id(self) -> str:
        return public_conversation_id(self.channel_key)

    def as_dict(self) -> dict[str, object]:
        return asdict(self) | {
            "display_name": self.display_name,
            "conversation": self.conversation_id,
            "kind": "public",
        }
