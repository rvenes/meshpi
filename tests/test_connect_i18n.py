from __future__ import annotations

import asyncio
import json
from pathlib import Path

import pytest
from textual.widgets import Input, Static

from meshpi.connect_tui import ConnectionItem, ConnectionPickerApp, build_connection_choices
from meshpi.connections import (
    ConnectionProfile,
    ConnectionStore,
    discover_tcp,
    parse_connection_target,
)
from meshpi.i18n import format_fields, using_language

ROOT = Path(__file__).resolve().parents[1]


def _discovery(*, scanning: bool = False) -> dict[str, object]:
    return {
        "active_profile_id": "serial-saved",
        "profiles": [
            {
                "profile_id": "serial-saved",
                "name": "Field node",
                "transport": "serial",
                "device": "/dev/ttyUSB9",
                "endpoint": "/dev/ttyUSB9",
            }
        ],
        "serial": [],
        "serial_scanned": True,
        "ble": [],
        "ble_scanned": not scanning,
        "ble_scanning": scanning,
        "tcp": [],
    }


def test_connect_catalogs_match_and_have_identical_format_fields() -> None:
    locale_root = ROOT / "meshpi" / "locales"
    nn = json.loads((locale_root / "connect.nn.json").read_text(encoding="utf-8"))
    en = json.loads((locale_root / "connect.en.json").read_text(encoding="utf-8"))

    assert nn.keys() == en.keys()
    assert len(nn) >= 45
    for key in nn:
        assert key.startswith("connect.")
        assert nn[key]
        assert en[key]
        assert format_fields(nn[key]) == format_fields(en[key]), key


@pytest.mark.parametrize(
    ("language", "sections", "unavailable"),
    [
        ("nn", ["Lagra"], "IKKJE TILKOPLA"),
        ("en", ["Saved"], "NOT CONNECTED"),
    ],
)
def test_connection_choices_and_profile_labels_follow_active_language(
    language: str,
    sections: list[str],
    unavailable: str,
) -> None:
    with using_language(language):
        choices = build_connection_choices(_discovery())

        assert [choice["section"] for choice in choices] == sections
        assert unavailable in ConnectionItem(choices[0])._label().plain


@pytest.mark.parametrize(
    ("language", "title", "picker_title", "placeholder", "status", "binding"),
    [
        (
            "nn",
            "MeshPi – ny tilkopling",
            "Vel Meshtastic-tilkopling",
            "IP, vertsnamn, seriellsti eller ble://identifikator",
            "Søkjer",
            "Avbryt",
        ),
        (
            "en",
            "MeshPi – new connection",
            "Choose a Meshtastic connection",
            "IP address, hostname, serial path, or ble://identifier",
            "Searching",
            "Cancel",
        ),
    ],
)
def test_picker_uses_active_language_at_startup(
    language: str,
    title: str,
    picker_title: str,
    placeholder: str,
    status: str,
    binding: str,
) -> None:
    async def scenario() -> None:
        with using_language(language):
            app = ConnectionPickerApp(_discovery(scanning=True))
            assert app.title == title
            assert (
                app._bindings.get_bindings_for_key("escape")[0].description
                == binding
            )

            async with app.run_test(size=(120, 42)) as pilot:
                await pilot.pause(0.1)
                assert picker_title in str(app.query_one("#picker-title", Static).render())
                assert app.query_one("#connection-input", Input).placeholder == placeholder
                assert status in str(app.query_one("#discovery-status", Static).render())
                help_text = str(app.query_one("#picker-help", Static).render())
                assert ("avbryt" if language == "nn" else "cancel") in help_text

    asyncio.run(scenario())


@pytest.mark.parametrize(
    ("language", "tcp_error", "target_error", "subnet_error"),
    [
        (
            "nn",
            "TCP-adressa kan ikkje vere tom",
            "Tilkoplingsmålet kan ikkje vere tomt",
            "Oppdagingsnettet kan ikkje vere større enn /22",
        ),
        (
            "en",
            "The TCP address cannot be empty",
            "The connection target cannot be empty",
            "The discovery network cannot be larger than /22",
        ),
    ],
)
def test_connection_validation_errors_follow_active_language(
    language: str,
    tcp_error: str,
    target_error: str,
    subnet_error: str,
) -> None:
    with using_language(language):
        with pytest.raises(ValueError, match=f"^{tcp_error}$"):
            ConnectionProfile.tcp("")
        with pytest.raises(ValueError, match=f"^{target_error}$"):
            parse_connection_target("")
        with pytest.raises(ValueError, match=f"^{subnet_error}$"):
            discover_tcp("10.0.0.0/8")


def test_profile_store_errors_are_english_when_english_is_active(tmp_path: Path) -> None:
    profile_file = tmp_path / "connections.json"
    profile_file.write_text("not json", encoding="utf-8")

    with (
        using_language("en"),
        pytest.raises(ValueError, match="Could not read connection profiles"),
    ):
        ConnectionStore(profile_file)
