import pytest

from meshpi.node_actions import (
    NodeActionError,
    node_info_payload,
    parse_node_info_response,
    parse_traceroute_response,
)


def test_node_info_payload_shares_only_public_owner_information():
    payload = node_info_payload({
        "id": "!00000000", "longName": "Testnode", "shortName": "TEST",
        "publicKey": b"p" * 32, "privateKey": "private", "psk": "channel",
        "macaddr": "private-address", "raw": object(),
    }, "!11112222")
    assert payload.id == "!11112222"
    assert payload.long_name == "Testnode"
    assert payload.public_key == b"p" * 32
    assert not payload.macaddr
    assert b"private" not in payload.SerializeToString()
    assert b"channel" not in payload.SerializeToString()


@pytest.mark.parametrize("user", [None, {}, {"hwModel": "invalid"}, {"publicKey": b"short"}])
def test_node_info_payload_requires_valid_local_info(user):
    with pytest.raises(NodeActionError, match="ikkje klar"):
        node_info_payload(user, "!11112222")


@pytest.mark.parametrize("key", [b"", b"p" * 32])
def test_node_info_response_reports_key_presence_without_retaining_key(key):
    from meshtastic.protobuf import mesh_pb2

    user = mesh_pb2.User(id="!11112222", long_name="Testnode", public_key=key)
    result = parse_node_info_response({
        "from": 0x11112222,
        "decoded": {"portnum": "NODEINFO_APP", "payload": user.SerializeToString()},
    }, "!11112222")
    assert result == {"node_info_received": True, "public_key_received": bool(key)}


@pytest.mark.parametrize("packet", [
    {"fromId": "!33334444", "decoded": {"portnum": 4, "user": {"id": "!33334444"}}},
    {"fromId": "!11112222", "decoded": {"portnum": 3, "user": {"id": "!11112222"}}},
    {"fromId": "!11112222", "decoded": {"portnum": 4, "payload": b""}},
    {"fromId": "!11112222", "decoded": {"portnum": 4, "payload": b"\xff"}},
    {"fromId": "!11112222", "decoded": {"portnum": 4, "user": {"id": "!33334444"}}},
    {"from": "invalid", "decoded": {"portnum": 4}},
])
def test_node_info_response_rejects_wrong_target_type_or_invalid_payload(packet):
    with pytest.raises(NodeActionError):
        parse_node_info_response(packet, "!11112222")


def test_traceroute_response_builds_forward_and_return_paths():
    result = parse_traceroute_response(
        {
            "decoded": {
                "portnum": "TRACEROUTE_APP",
                "traceroute": {
                    "route": [0x11112222],
                    "snrTowards": [30, -128],
                    "routeBack": [0x33334444],
                    "snrBack": [20, 16],
                },
            }
        },
        local_node_id="!040840a0",
        target_node_id="!710365c8",
    )

    assert result["forward"] == [
        {"node_id": "!040840a0", "snr": None},
        {"node_id": "!11112222", "snr": 7.5},
        {"node_id": "!710365c8", "snr": None},
    ]
    assert result["return"] == [
        {"node_id": "!710365c8", "snr": None},
        {"node_id": "!33334444", "snr": 5.0},
        {"node_id": "!040840a0", "snr": 4.0},
    ]


def test_direct_traceroute_and_missing_return_route_are_valid():
    result = parse_traceroute_response(
        {
            "decoded": {
                "portnum": 70,
                "traceroute": {"snrTowards": [24]},
            }
        },
        local_node_id="!040840a0",
        target_node_id="!710365c8",
    )

    assert result["forward"][-1]["snr"] == 6.0
    assert result["return"] is None


def test_routing_error_becomes_readable_node_action_error():
    with pytest.raises(NodeActionError, match="svara ikkje.*NO_RESPONSE"):
        parse_traceroute_response(
            {
                "decoded": {
                    "portnum": "ROUTING_APP",
                    "routing": {"errorReason": "NO_RESPONSE"},
                }
            },
            local_node_id="!040840a0",
            target_node_id="!710365c8",
        )
