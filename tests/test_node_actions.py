import pytest

from meshpi.node_actions import NodeActionError, parse_traceroute_response


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
