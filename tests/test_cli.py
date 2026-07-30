import json

import pytest

from meshpi.cli import (
    _battery,
    _chat_dm_peer,
    _configure_console_output,
    _default_env_file,
    _format_message,
    _normalize_argv,
    _print_status,
    _public_channel_options,
    build_parser,
    main,
    run,
)
from meshpi.client import CLIError
from meshpi.config import Settings
from meshpi.update import UpdateNotice


def test_battery_display_handles_external_power():
    assert _battery(101) == "Straum"
    assert _battery(0) == "Straum"
    assert _battery(75) == "75%"
    assert _battery(None) == "–"


def test_chat_can_resolve_peer_from_empty_dm_route_history():
    route = "dm:!aaaaaaaa:!deadbeef:global:LongFast:1234"

    assert _chat_dm_peer(route, []) == "!deadbeef"


def test_cli_uses_utf8_environment_pointer_next_to_installed_launcher(
    tmp_path,
    monkeypatch,
):
    launcher = tmp_path / "Brukar Håkon" / "bin" / "meshpi.exe"
    launcher.parent.mkdir(parents=True)
    launcher.touch()
    configured = tmp_path / "Profil Øyvind" / "MeshPi" / "meshpi.env"
    launcher.with_name("meshpi.env-path").write_text(
        f"{configured}\n",
        encoding="utf-8",
    )
    monkeypatch.setattr("meshpi.cli.sys.argv", [str(launcher)])

    assert _default_env_file() == str(configured)
    assert build_parser().parse_args(["status"]).env_file == str(configured)


def test_windows_console_replaces_unencodable_characters(monkeypatch):
    class Stream:
        errors = None

        def reconfigure(self, *, errors):
            self.errors = errors

    stdout = Stream()
    stderr = Stream()
    monkeypatch.setattr("meshpi.cli.sys.platform", "win32")
    monkeypatch.setattr("meshpi.cli.sys.stdout", stdout)
    monkeypatch.setattr("meshpi.cli.sys.stderr", stderr)

    _configure_console_output()

    assert stdout.errors == "replace"
    assert stderr.errors == "replace"


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


def test_message_display_uses_actual_channel_index():
    rendered = _format_message(
        {
            "timestamp": "2026-07-20T12:00:00+00:00",
            "kind": "public",
            "channel": 3,
            "direction": "inn",
            "from_node": "!11112222",
            "transport": "RF",
            "text": "Fleirkanal",
        }
    )
    assert "CH3" in rendered


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


def test_cli_parser_supports_channel_selection():
    channels = build_parser().parse_args(["channels"])
    public = build_parser().parse_args(
        ["send-public", "Hei", "--channel", "3"]
    )
    dm = build_parser().parse_args(
        ["send-dm", "!11112222", "Hei", "--channel", "2"]
    )
    assert channels.command == "channels"
    assert public.channel == "3"
    assert dm.channel == 2


def test_public_channel_selection_rejects_names_and_out_of_range_indexes():
    assert _public_channel_options("3") == {"channel_index": 3}
    assert _public_channel_options("channel:global:Ops:1234") == {
        "conversation": "channel:global:Ops:1234"
    }
    with pytest.raises(CLIError, match="indeks eller ein samtale-ID"):
        _public_channel_options("Ops")
    with pytest.raises(CLIError, match="mellom 0 og 7"):
        _public_channel_options("8")


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


def test_cli_has_verified_update_command():
    args = build_parser().parse_args(["update", "--check"])
    assert args.command == "update"
    assert args.check is True
    assert args.beta is False

    beta = build_parser().parse_args(["update", "--beta", "--check"])
    assert beta.beta is True


def test_cli_parser_supports_database_export(tmp_path):
    output = tmp_path / "data.txt"

    args = build_parser().parse_args(["export", str(output), "--force"])

    assert args.command == "export"
    assert args.output == output
    assert args.force is True


def test_database_export_writes_utf8_text_atomically(tmp_path, monkeypatch, capsys):
    output = tmp_path / "meshpi-data.jsonl"
    records = [
        {
            "record": "metadata",
            "format": "meshpi-database-export",
            "format_version": 1,
        },
        {
            "record": "row",
            "table": "messages",
            "data": {"text": "Melding frå Ørsta"},
        },
        {
            "record": "complete",
            "tables": {"messages": 1},
            "rows": 1,
        },
    ]

    class FakeSocket:
        closed = False

        def close(self):
            self.closed = True

    class FakeStream:
        closed = False

        def __iter__(self):
            return iter(
                [
                    json.dumps(record, ensure_ascii=False).encode("utf-8") + b"\n"
                    for record in records
                ]
            )

        def close(self):
            self.closed = True

    sock = FakeSocket()
    stream = FakeStream()
    monkeypatch.setattr(
        "meshpi.cli._open_export",
        lambda _settings: (sock, stream),
    )

    run(build_parser().parse_args(["export", str(output)]), Settings())

    saved = [
        json.loads(line)
        for line in output.read_text(encoding="utf-8").splitlines()
    ]
    assert saved == records
    assert sock.closed is True
    assert stream.closed is True
    assert "Eksporterte 1 datarader" in capsys.readouterr().out


def test_database_export_refuses_to_overwrite_existing_file(tmp_path, monkeypatch):
    output = tmp_path / "eksisterer.jsonl"
    output.write_text("behald", encoding="utf-8")
    monkeypatch.setattr(
        "meshpi.cli._open_export",
        lambda _settings: (_ for _ in ()).throw(AssertionError("skal ikkje køyre")),
    )

    with pytest.raises(ValueError, match="--force"):
        run(build_parser().parse_args(["export", str(output)]), Settings())

    assert output.read_text(encoding="utf-8") == "behald"


def test_incomplete_database_export_leaves_no_output_file(tmp_path, monkeypatch):
    output = tmp_path / "uferdig.jsonl"

    class FakeSocket:
        def close(self):
            pass

    class FakeStream:
        def __iter__(self):
            yield (
                b'{"record":"metadata","format":"meshpi-database-export",'
                b'"format_version":1}\n'
            )

        def close(self):
            pass

    monkeypatch.setattr(
        "meshpi.cli._open_export",
        lambda _settings: (FakeSocket(), FakeStream()),
    )

    with pytest.raises(CLIError, match="broten"):
        run(build_parser().parse_args(["export", str(output)]), Settings())

    assert not output.exists()
    assert list(tmp_path.glob(".*.tmp")) == []


def test_update_command_does_not_start_daemon(monkeypatch):
    calls = []
    monkeypatch.setattr("meshpi.cli.Settings.load", lambda _path: Settings())
    monkeypatch.setattr(
        "meshpi.cli.daemon_status",
        lambda _settings: (_ for _ in ()).throw(AssertionError("daemon")),
    )
    monkeypatch.setattr(
        "meshpi.cli.run",
        lambda args, _settings: calls.append(args.command),
    )

    main(["update", "--check"])

    assert calls == ["update"]


def test_update_command_requires_explicit_confirmation(monkeypatch, capsys):
    calls = []
    notice = UpdateNotice("0.6.4", "0.7.0", "meshpi update")
    monkeypatch.setattr(
        "meshpi.cli.check_for_update",
        lambda _settings, *, channel: notice,
    )
    monkeypatch.setattr("builtins.input", lambda _prompt: "nei")
    monkeypatch.setattr(
        "meshpi.cli.apply_update",
        lambda *_args, **_kwargs: calls.append(True),
    )

    run(build_parser().parse_args(["update"]), Settings())

    assert calls == []
    assert "avbroten" in capsys.readouterr().out


def test_update_command_installs_confirmed_version(monkeypatch, capsys):
    notice = UpdateNotice("0.6.4", "0.7.0", "meshpi update")
    calls = []
    monkeypatch.setattr(
        "meshpi.cli.check_for_update",
        lambda _settings, *, channel: notice,
    )
    monkeypatch.setattr("builtins.input", lambda _prompt: "OPPDATER")

    def update(_settings, *, expected_version, channel):
        calls.append((expected_version, channel))
        return "0.7.0"

    monkeypatch.setattr("meshpi.cli.apply_update", update)

    run(build_parser().parse_args(["update"]), Settings())

    assert calls == [("0.7.0", "stable")]
    assert "0.7.0 er installert" in capsys.readouterr().out


def test_beta_update_uses_beta_channel(monkeypatch, capsys):
    notice = UpdateNotice(
        "0.8.6",
        "0.9.0b1",
        "meshpi update --beta",
        channel="beta",
    )
    calls = []

    def check(_settings, *, channel):
        calls.append(("check", channel))
        return notice

    def update(_settings, *, expected_version, channel):
        calls.append(("update", expected_version, channel))
        return expected_version

    monkeypatch.setattr("meshpi.cli.check_for_update", check)
    monkeypatch.setattr("meshpi.cli.apply_update", update)

    run(build_parser().parse_args(["update", "--beta", "--yes"]), Settings())

    assert calls == [
        ("check", "beta"),
        ("update", "0.9.0b1", "beta"),
    ]
    assert "0.9.0b1 er installert" in capsys.readouterr().out


def test_main_starts_stopped_always_service_before_running_command(monkeypatch):
    settings = Settings(background_mode="always")
    calls = []
    monkeypatch.setattr("meshpi.cli.Settings.load", lambda _path: settings)
    monkeypatch.setattr("meshpi.cli.daemon_status", lambda _settings: None)
    monkeypatch.setattr(
        "meshpi.cli.manage_service",
        lambda action, _settings, env_file: calls.append((action, env_file)),
    )
    monkeypatch.setattr(
        "meshpi.cli.wait_for_daemon",
        lambda _settings: calls.append(("wait", None)),
    )
    monkeypatch.setattr("meshpi.cli.run", lambda _args, _settings: "leave")

    main(["status"])

    assert calls == [("start", ".env"), ("wait", None)]


def test_main_uses_platform_service_when_tui_requests_stop(monkeypatch):
    settings = Settings(background_mode="always")
    calls = []
    monkeypatch.setattr("meshpi.cli.Settings.load", lambda _path: settings)
    monkeypatch.setattr("meshpi.cli.daemon_status", lambda _settings: {"state": "klar"})
    monkeypatch.setattr("meshpi.cli.run", lambda _args, _settings: "stop")
    monkeypatch.setattr(
        "meshpi.cli.manage_service",
        lambda action, _settings, env_file: calls.append((action, env_file)),
    )
    monkeypatch.setattr(
        "meshpi.cli.stop_daemon",
        lambda _settings: calls.append(("direct-stop", None)),
    )

    main([])

    assert calls == [("stop", ".env")]


def test_delete_messages_command_requires_confirmation(monkeypatch, capsys):
    calls = []
    monkeypatch.setattr("builtins.input", lambda _prompt: "nei")
    monkeypatch.setattr("meshpi.cli._request", lambda _settings, payload: calls.append(payload))

    run(build_parser().parse_args(["delete-messages", "all"]), Settings())

    assert calls == []
    assert "Ingen meldingar blei sletta" in capsys.readouterr().out


def test_delete_messages_command_can_delete_all_in_one_command(monkeypatch, capsys):
    calls = []

    def fake_request(_settings, payload):
        calls.append(payload)
        return {"ok": True, "data": {"scope": "all", "deleted": 7}}

    monkeypatch.setattr("meshpi.cli._request", fake_request)

    run(build_parser().parse_args(["delete-messages", "all", "--yes"]), Settings())

    assert calls == [{"command": "delete_messages", "scope": "all"}]
    assert "Sletta 7 meldingar" in capsys.readouterr().out


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
