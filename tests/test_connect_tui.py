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
                "host": "10.0.0.152",
                "port": 4403,
                "endpoint": "10.0.0.152:4403",
            }
        ],
        "serial": [
            {
                "name": "Seeed XIAO",
                "transport": "serial",
                "target": "/dev/serial/by-id/xiao",
                "device": "/dev/serial/by-id/xiao",
            }
        ],
        "tcp": [
            {
                "name": "10.0.0.152",
                "transport": "tcp",
                "target": "10.0.0.152:4403",
                "host": "10.0.0.152",
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
