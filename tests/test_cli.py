from meshpi.cli import (
    _battery,
    _format_message,
    _normalize_argv,
    _print_status,
    build_parser,
    run,
)
from meshpi.config import Settings


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


def test_outgoing_message_display_explains_transport_and_ack_status():
    rendered = _format_message(
        {
            "timestamp": "2026-07-20T12:00:00+00:00",
            "kind": "dm",
            "direction": "ut",
            "from_node": "!710365c8",
            "transport": "Ukjend",
            "status": "ACK",
            "text": "Hei",
        }
    )

    assert "transport ukjend" in rendered
    assert "[ACK]" in rendered


def test_cli_parser_supports_json_and_node_details():
    args = build_parser().parse_args(["--json", "node", "!11112222"])
    assert args.json is True
    assert args.command == "node"
    assert args.node_id == "!11112222"


def test_cli_defaults_to_tui():
    args = build_parser().parse_args([])
    assert args.command == "tui"


def test_status_without_selected_node_has_a_readable_meshtastic_label(capsys):
    _print_status(
        {
            "state": "ingen node",
            "transport": None,
            "endpoint": None,
            "host": None,
            "port": None,
            "local_node_id": None,
            "connected_since": None,
        }
    )

    output = capsys.readouterr().out
    assert "Meshtastic:   ingen node vald" in output
    assert "None" not in output


def test_cli_accepts_connection_shortcuts():
    tcp = build_parser().parse_args(_normalize_argv(["10.0.0.135"]))
    serial = build_parser().parse_args(_normalize_argv(["/dev/ttyACM0"]))
    assert (tcp.command, tcp.target) == ("connect", "10.0.0.135")
    assert (serial.command, serial.target) == ("connect", "/dev/ttyACM0")


def test_cli_has_new_connection_dialog_command():
    args = build_parser().parse_args(["new"])
    assert args.command == "new"


def test_tui_opens_connection_picker_when_no_profile_exists(monkeypatch):
    calls = []

    def fake_request(_settings, payload):
        calls.append(payload)
        if payload["command"] == "connections":
            return {"ok": True, "data": {"active_profile_id": None, "profiles": []}}
        if payload["command"] == "connect":
            return {"ok": True, "data": {"state": "koplar til"}}
        raise AssertionError(payload)

    monkeypatch.setattr("meshpi.cli._request", fake_request)
    monkeypatch.setattr(
        "meshpi.connect_tui.choose_connection",
        lambda _settings: {"target": "192.0.2.42"},
    )
    monkeypatch.setattr("meshpi.tui.run_tui", lambda _settings: "leave")

    result = run(build_parser().parse_args([]), Settings())

    assert result == "leave"
    assert calls == [
        {"command": "connections"},
        {"command": "connect", "target": "192.0.2.42"},
    ]
