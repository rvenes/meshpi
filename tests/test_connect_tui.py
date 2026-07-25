import asyncio

from meshpi.connect_tui import (
    ConnectionItem,
    ConnectionPickerApp,
    build_connection_choices,
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
    }


def test_connection_choices_include_saved_serial_and_discovered_tcp():
    choices = build_connection_choices(discovery_data())
    assert len(choices) == 3
    assert {choice["section"] for choice in choices} == {
        "Lagra",
        "USB / seriell",
        "TCP på lokalnettet",
    }


def test_connection_choices_can_include_secondary_serial_ports():
    choices = build_connection_choices(
        discovery_data(),
        include_all_serial=True,
    )

    assert len(choices) == 4
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
            assert len(app.query(ConnectionItem)) == 3
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
            assert len(app.query(ConnectionItem)) == 3
            assert "1 serieportar skjulte" in str(
                app.query_one("#choice-count").render()
            )

            await pilot.press("f4")
            await pilot.pause(0.2)
            assert len(app.query(ConnectionItem)) == 4
            assert any(
                item.choice["endpoint"] == "/dev/ttyS4"
                for item in app.query(ConnectionItem)
            )

            await pilot.press("f4")
            await pilot.pause(0.2)
            assert len(app.query(ConnectionItem)) == 3

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
