from meshpi.models import ConversationKind, Direction, Transport
from meshpi.packet import node_from_registry, parse_text_packet

LOCAL = "!710365c8"


def packet(**updates):
    value = {
        "id": 123,
        "from": 0x11112222,
        "fromId": "!11112222",
        "to": 0xFFFFFFFF,
        "toId": "^all",
        "channel": 0,
        "rxTime": 1_700_000_000,
        "decoded": {"portnum": "TEXT_MESSAGE_APP", "payload": b"hei", "text": "hei"},
    }
    value.update(updates)
    return value


def test_public_channel_zero():
    message = parse_text_packet(packet(), LOCAL)
    assert message is not None
    assert message.kind == ConversationKind.PUBLIC
    assert message.channel == 0
    assert message.direction == Direction.INCOMING


def test_other_public_channel_is_ignored():
    assert parse_text_packet(packet(channel=1), LOCAL) is None


def test_incoming_and_outgoing_dm():
    incoming = parse_text_packet(
        packet(to=0x710365C8, toId=LOCAL, viaMqtt=True), LOCAL
    )
    assert incoming is not None
    assert incoming.kind == ConversationKind.DM
    assert incoming.peer_node == "!11112222"
    assert incoming.transport == Transport.MQTT

    outgoing = parse_text_packet(
        packet(
            **{
                "from": 0x710365C8,
                "fromId": LOCAL,
                "to": 0x33334444,
                "toId": "!33334444",
                "transportMechanism": "TRANSPORT_LORA",
            }
        ),
        LOCAL,
    )
    assert outgoing is not None
    assert outgoing.direction == Direction.OUTGOING
    assert outgoing.peer_node == "!33334444"
    assert outgoing.transport == Transport.RF


def test_dm_not_involving_local_node_is_ignored():
    assert (
        parse_text_packet(
            packet(to=0x33334444, toId="!33334444"),
            LOCAL,
        )
        is None
    )


def test_non_text_packet_is_ignored():
    assert (
        parse_text_packet(
            packet(decoded={"portnum": "TELEMETRY_APP", "payload": b"x"}),
            LOCAL,
        )
        is None
    )


def test_transport_is_unknown_without_evidence():
    message = parse_text_packet(packet(), LOCAL)
    assert message is not None
    assert message.transport == Transport.UNKNOWN


def test_node_registry_mapping_and_private_field_filtering():
    node = node_from_registry(
        "!11112222",
        {
            "num": 0x11112222,
            "user": {
                "id": "!11112222",
                "longName": "Testnode",
                "shortName": "TEST",
                "hwModel": "TBEAM",
                "role": "CLIENT",
                "isUnmessagable": False,
            },
            "deviceMetrics": {"batteryLevel": 75, "voltage": 4.1},
            "lastHeard": 1_700_000_000,
            "snr": 7.5,
            "hopsAway": 2,
            "lastReceived": {"rxRssi": -99, "viaMqtt": True},
        },
        LOCAL,
    )
    assert node.long_name == "Testnode"
    assert node.battery_level == 75
    assert node.transport == Transport.MQTT
    assert node.can_receive_dm is True
    assert node.is_local is False

