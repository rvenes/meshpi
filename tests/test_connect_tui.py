import asyncio
import threading

from meshpi.connect_tui import (
    ConnectionItem,
    ConnectionPickerApp,
    build_connection_choices,
    choose_connection,
)


def discovery_data():
    return {
        "active_profile_id": "tcp-main",
        "profiles": [
            {
                "profile_id": "tcp-main",
                "name": "Fast node",
                "transport": "tcp",
                "host": "192.0.2.42",
                "port": 4403,
                "endpoint": "192.0.2.42:4403",
            }
        ],
        "serial": [
            {
                "name": "Seeed XIAO",
                "transport": "serial",
                "target": "/dev/serial/by-id/xiao",
                "device": "/dev/serial/by-id/xiao",
                "recommended": True,
            },
            {
                "name": "ttyS4",
                "transport": "serial",
                "target": "/dev/ttyS4",
                "device": "/dev/ttyS4",
                "recommended": False,
            },
        ],
        "tcp": [
            {
                "name": "192.0.2.42",
                "transport": "tcp",
                "target": "192.0.2.42:4403",
                "host": "192.0.2.42",
                "port": 4403,
            },
            {
                "name": "10.0.0.135",
                "transport": "tcp",
                "target": "10.0.0.135:4403",
                "host": "10.0.0.135",
                "port": 4403,
            },
        ],
        "ble": [
            {
                "name": "Handhalden",
                "transport": "ble",
                "target": "ble://A1:B2:C3:D4:E5:F6",
                "ble_identifier": "A1:B2:C3:D4:E5:F6",
            }
        ],
        "ble_error": None,
    }


def test_connection_choices_include_saved_serial_and_discovered_tcp():
    choices = build_connection_choices(discovery_data())
    assert len(choices) == 4
    assert {choice["section"] for choice in choices} == {
        "Lagra",
        "USB / seriell",
        "Bluetooth / BLE",
        "TCP på lokalnettet",
    }


def test_connection_choices_can_include_secondary_serial_ports():
    choices = build_connection_choices(
        discovery_data(),
        include_all_serial=True,
    )

    assert len(choices) == 5
    assert any(choice["endpoint"] == "/dev/ttyS4" for choice in choices)


def test_empty_saved_serial_profile_is_unavailable():
    data = discovery_data()
    data["profiles"].append(
        {
            "profile_id": "serial-empty",
            "name": "Korrupt profil",
            "transport": "serial",
            "device": "",
            "endpoint": "",
        }
    )
    data["serial"] = [{"transport": "serial"}]

    choices = build_connection_choices(data)
    saved = next(choice for choice in choices if choice.get("profile_id") == "serial-empty")

    assert saved["available"] is False


def test_saved_ble_profile_matches_discovery_case_insensitively_without_duplicate():
    data = discovery_data()
    data["profiles"].append(
        {
            "profile_id": "ble-saved",
            "name": "Lagra BLE",
            "transport": "ble",
            "ble_identifier": "a1:b2:c3:d4:e5:f6",
            "endpoint": "a1:b2:c3:d4:e5:f6",
        }
    )
    data["ble_scanned"] = True

    choices = build_connection_choices(data)
    matching = [
        choice
        for choice in choices
        if choice["transport"] == "ble"
        and choice["endpoint"].casefold() == "a1:b2:c3:d4:e5:f6"
    ]

    assert len(matching) == 1
    assert matching[0]["profile_id"] == "ble-saved"
    assert matching[0]["available"] is True


def test_saved_serial_profile_is_marked_unavailable_and_sorted_last():
    data = discovery_data()
    data["profiles"].append(
        {
            "profile_id": "serial-old",
            "name": "Gammal XIAO",
            "transport": "serial",
            "device": "/dev/serial/by-id/old",
            "endpoint": "/dev/serial/by-id/old",
            "serial_number": "OLD",
            "vid": 0x239A,
            "pid": 0x810B,
        }
    )

    choices = build_connection_choices(data)
    stale = next(choice for choice in choices if choice.get("profile_id") == "serial-old")

    assert stale["available"] is False
    assert choices[-1] is stale
    assert "IKKJE TILKOPLA" in ConnectionItem(stale)._label().plain


def test_saved_serial_profile_matches_usb_identity_after_path_change():
    data = discovery_data()
    data["serial"][0].update(
        {
            "serial_number": "ABC123",
            "vid": 0x239A,
            "pid": 0x810B,
        }
    )
    data["profiles"].append(
        {
            "profile_id": "serial-xiao",
            "name": "XIAO",
            "transport": "serial",
            "device": "/dev/ttyACM9",
            "endpoint": "/dev/ttyACM9",
            "serial_number": "ABC123",
            "vid": 0x239A,
            "pid": 0x810B,
        }
    )

    choices = build_connection_choices(data)
    saved = next(choice for choice in choices if choice.get("profile_id") == "serial-xiao")

    assert saved["available"] is True
    assert all(choice.get("target") != "/dev/serial/by-id/xiao" for choice in choices)


def test_saved_serial_profile_rejects_reused_path_with_other_identity():
    data = discovery_data()
    data["serial"][0].update(
        {
            "serial_number": "CURRENT",
            "vid": 0x239A,
            "pid": 0x810B,
        }
    )
    data["profiles"].append(
        {
            "profile_id": "serial-old",
            "name": "Gammal XIAO",
            "transport": "serial",
            "device": "/dev/serial/by-id/xiao",
            "endpoint": "/dev/serial/by-id/xiao",
            "serial_number": "OLD",
            "vid": 0x239A,
            "pid": 0x810B,
        }
    )

    choices = build_connection_choices(data)
    stale = next(choice for choice in choices if choice.get("profile_id") == "serial-old")

    assert stale["available"] is False
    assert any(choice.get("target") == "/dev/serial/by-id/xiao" for choice in choices)


def test_connection_picker_filters_and_selects_usb():
    async def scenario():
        app = ConnectionPickerApp(discovery_data())
        async with app.run_test(size=(120, 42)) as pilot:
            await pilot.pause(0.2)
            assert len(app.query(ConnectionItem)) == 4
            await pilot.press(*"xiao")
            await pilot.pause(0.2)
            assert len(app.query(ConnectionItem)) == 1
            await pilot.press("enter")
        assert app.return_value == {
            "target": "/dev/serial/by-id/xiao",
            "name": "Seeed XIAO",
        }

    asyncio.run(scenario())


def test_connection_picker_toggles_secondary_serial_ports():
    async def scenario():
        app = ConnectionPickerApp(discovery_data())
        async with app.run_test(size=(120, 42)) as pilot:
            await pilot.pause(0.2)
            assert len(app.query(ConnectionItem)) == 4
            assert "1 serieportar skjulte" in str(
                app.query_one("#choice-count").render()
            )

            await pilot.press("f4")
            await pilot.pause(0.2)
            assert len(app.query(ConnectionItem)) == 5
            assert any(
                item.choice["endpoint"] == "/dev/ttyS4"
                for item in app.query(ConnectionItem)
            )

            await pilot.press("f4")
            await pilot.pause(0.2)
            assert len(app.query(ConnectionItem)) == 4

    asyncio.run(scenario())


def test_hidden_serial_count_excludes_port_deduplicated_by_saved_profile():
    data = discovery_data()
    data["profiles"].append(
        {
            "profile_id": "serial-ttys4",
            "name": "Lagra ttyS4",
            "transport": "serial",
            "device": "/dev/ttyS4",
            "endpoint": "/dev/ttyS4",
        }
    )

    app = ConnectionPickerApp(data)

    assert app.hidden_serial_count == 0
    assert build_connection_choices(data) == build_connection_choices(
        data,
        include_all_serial=True,
    )


def test_saved_ble_profile_is_deduplicated_and_available():
    data = discovery_data()
    data["profiles"].append(
        {
            "profile_id": "ble-handhalden",
            "name": "Lagra handhalden",
            "transport": "ble",
            "ble_identifier": "A1:B2:C3:D4:E5:F6",
            "endpoint": "A1:B2:C3:D4:E5:F6",
        }
    )

    choices = build_connection_choices(data)
    saved = next(choice for choice in choices if choice.get("profile_id") == "ble-handhalden")

    assert saved["available"] is True
    assert len(
        [
            choice
            for choice in choices
            if choice["endpoint"] == "A1:B2:C3:D4:E5:F6"
        ]
    ) == 1


def test_discovered_ble_choice_uses_ble_target():
    choice = next(
        item
        for item in build_connection_choices(discovery_data())
        if item["transport"] == "ble"
    )

    assert ConnectionPickerApp._result(choice) == {
        "target": "ble://A1:B2:C3:D4:E5:F6",
        "name": "Handhalden",
    }


def test_picker_shows_ble_error():
    async def scenario():
        data = discovery_data()
        data["ble"] = []
        data["ble_error"] = "Bluetooth er slått av."
        app = ConnectionPickerApp(data)
        async with app.run_test(size=(120, 42)) as pilot:
            await pilot.pause(0.2)
            assert "Bluetooth er slått av" in str(
                app.query_one("#discovery-status").render()
            )

    asyncio.run(scenario())


def test_choose_connection_opens_with_saved_profiles_before_discovery(
    monkeypatch,
):
    captured = {}
    calls = []

    class Picker:
        def __init__(self, discovery, **kwargs):
            captured["discovery"] = discovery
            captured["kwargs"] = kwargs

        def run(self):
            return {"profile_id": "tcp-main"}

    def requester(settings, payload, **kwargs):
        calls.append((settings, payload, kwargs))
        return {
            "data": {
                "active_profile_id": "tcp-main",
                "profiles": discovery_data()["profiles"],
            }
        }

    monkeypatch.setattr("meshpi.connect_tui.ConnectionPickerApp", Picker)
    settings = object()

    result = choose_connection(settings, requester=requester)

    assert result == {"profile_id": "tcp-main"}
    assert [call[1]["command"] for call in calls] == ["connections"]
    assert captured["discovery"]["profiles"] == discovery_data()["profiles"]
    assert captured["discovery"]["ble_scanned"] is False
    assert captured["kwargs"]["auto_discover"] is True


def test_picker_shows_local_results_while_ble_search_runs_and_supports_f5():
    release_ble = threading.Event()
    ble_started = threading.Event()
    ble_calls = []
    data = discovery_data()
    initial = {
        "active_profile_id": data["active_profile_id"],
        "profiles": data["profiles"],
        "serial": [],
        "serial_scanned": False,
        "tcp": [],
        "ble": [],
        "ble_error": None,
        "ble_scanned": False,
    }

    def requester(_settings, payload, *, timeout):
        assert timeout == 30
        if payload["command"] == "discover_connections":
            assert payload["include_ble"] is False
            local = discovery_data()
            local["ble"] = []
            local["ble_error"] = None
            return {"data": local}
        if payload["command"] == "discover_ble_connections":
            ble_calls.append(object())
            ble_started.set()
            assert release_ble.wait(2)
            return {
                "data": {
                    "ble": discovery_data()["ble"],
                    "ble_error": None,
                }
            }
        raise AssertionError(payload)

    async def scenario():
        app = ConnectionPickerApp(
            initial,
            settings=object(),
            requester=requester,
            auto_discover=True,
        )
        async with app.run_test(size=(120, 42)) as pilot:
            for _ in range(20):
                if ble_started.is_set():
                    break
                await pilot.pause(0.05)
            assert ble_started.is_set()
            assert "Søkjer" in str(app.query_one("#discovery-status").render())
            assert any(
                item.choice["transport"] == "serial"
                for item in app.query(ConnectionItem)
            )
            assert not any(
                item.choice["transport"] == "ble"
                for item in app.query(ConnectionItem)
            )

            await pilot.press("f5")
            await pilot.pause(0.05)
            assert len(ble_calls) == 1

            release_ble.set()
            for _ in range(20):
                if any(
                    item.choice["transport"] == "ble"
                    for item in app.query(ConnectionItem)
                ):
                    break
                await pilot.pause(0.05)
            assert any(
                item.choice["transport"] == "ble"
                for item in app.query(ConnectionItem)
            )
            assert "1 eining funnen" in str(
                app.query_one("#discovery-status").render()
            )

            await pilot.press("f5")
            for _ in range(20):
                if len(ble_calls) == 2:
                    break
                await pilot.pause(0.05)
            assert len(ble_calls) == 2

    asyncio.run(scenario())


def test_picker_discards_late_ble_result_after_cancel():
    release_ble = threading.Event()
    ble_started = threading.Event()
    initial = discovery_data()
    initial["ble"] = []
    initial["ble_scanned"] = False

    def requester(_settings, payload, *, timeout):
        del timeout
        if payload["command"] == "discover_connections":
            local = discovery_data()
            local["ble"] = []
            return {"data": local}
        if payload["command"] == "discover_ble_connections":
            ble_started.set()
            assert release_ble.wait(2)
            return {
                "data": {
                    "ble": discovery_data()["ble"],
                    "ble_error": None,
                }
            }
        raise AssertionError(payload)

    async def scenario():
        app = ConnectionPickerApp(
            initial,
            settings=object(),
            requester=requester,
            auto_discover=True,
        )
        async with app.run_test(size=(120, 42)) as pilot:
            for _ in range(20):
                if ble_started.is_set():
                    break
                await pilot.pause(0.05)
            assert ble_started.is_set()
            await pilot.press("escape")
            release_ble.set()
        assert app.return_value is None
        assert app._closed is True

    asyncio.run(scenario())


def test_picker_keeps_ble_search_when_local_discovery_fails():
    calls = []
    initial = discovery_data()
    initial["serial"] = []
    initial["tcp"] = []
    initial["ble"] = []
    initial["ble_scanned"] = False

    def requester(_settings, payload, *, timeout):
        del timeout
        calls.append(payload["command"])
        if payload["command"] == "discover_connections":
            raise RuntimeError("lokalt søk feila")
        if payload["command"] == "discover_ble_connections":
            return {
                "data": {
                    "ble": discovery_data()["ble"],
                    "ble_error": None,
                    "ble_scanned": True,
                }
            }
        raise AssertionError(payload)

    async def scenario():
        app = ConnectionPickerApp(
            initial,
            settings=object(),
            requester=requester,
            auto_discover=True,
        )
        async with app.run_test(size=(120, 42)) as pilot:
            for _ in range(30):
                if not app._discovering:
                    break
                await pilot.pause(0.05)
            assert calls == [
                "discover_connections",
                "discover_ble_connections",
            ]
            assert app.discovery["local_error"] == "lokalt søk feila"
            assert app.discovery["ble_error"] is None
            assert app.discovery["ble_scanned"] is True
            assert "Lokal oppdaging feila: lokalt søk feila" in str(
                app.query_one("#discovery-status").render()
            )
            assert any(
                item.choice["transport"] == "ble"
                for item in app.query(ConnectionItem)
            )

    asyncio.run(scenario())


def test_picker_does_not_mark_ble_unavailable_when_scan_did_not_run():
    initial = discovery_data()
    initial["profiles"].append(
        {
            "profile_id": "ble-saved",
            "name": "Lagra BLE",
            "transport": "ble",
            "ble_identifier": "A1:B2:C3:D4:E5:F6",
            "endpoint": "A1:B2:C3:D4:E5:F6",
        }
    )

    async def scenario():
        app = ConnectionPickerApp(initial)
        async with app.run_test(size=(120, 42)) as pilot:
            await app._finish_discovery(
                app._discovery_generation,
                {
                    "ble": [],
                    "ble_error": "BLE-noden koplar til.",
                    "ble_scanned": False,
                },
                None,
            )
            await pilot.pause()
            saved = next(
                item.choice
                for item in app.query(ConnectionItem)
                if item.choice.get("profile_id") == "ble-saved"
            )
            assert saved["available"] is None

    asyncio.run(scenario())
