from meshpi.cli import _battery, _format_message, _normalize_argv, build_parser


def test_battery_display_handles_external_power():
    assert _battery(101) == "Straum"
    assert _battery(0) == "Straum"
    assert _battery(75) == "75%"
    assert _battery(None) == "–"


def test_message_display_contains_context_and_metadata():
    rendered = _format_message(
        {
            "timestamp": "2026-07-20T12:00:00+00:00",
            "kind": "public",
            "direction": "inn",
            "from_long_name": "Testnode",
            "from_node": "!11112222",
            "transport": "RF",
            "text": "Hei",
            "rssi": -99,
            "snr": 7.5,
            "hop_start": 3,
            "hop_limit": 2,
        }
    )
    assert "CH0" in rendered
    assert "Testnode" in rendered
    assert "[2222]" in rendered
    assert "RSSI -99" in rendered
    assert "hopp 3/2" in rendered


def test_cli_parser_supports_json_and_node_details():
    args = build_parser().parse_args(["--json", "node", "!11112222"])
    assert args.json is True
    assert args.command == "node"
    assert args.node_id == "!11112222"


def test_cli_defaults_to_tui():
    args = build_parser().parse_args([])
    assert args.command == "tui"


def test_cli_accepts_connection_shortcuts():
    tcp = build_parser().parse_args(_normalize_argv(["10.0.0.135"]))
    serial = build_parser().parse_args(_normalize_argv(["/dev/ttyACM0"]))
    assert (tcp.command, tcp.target) == ("connect", "10.0.0.135")
    assert (serial.command, serial.target) == ("connect", "/dev/ttyACM0")


def test_cli_has_new_connection_dialog_command():
    args = build_parser().parse_args(["new"])
    assert args.command == "new"
