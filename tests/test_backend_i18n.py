from __future__ import annotations

import json
import socket
import threading
from pathlib import Path

import pytest

from meshpi.config import Settings
from meshpi.database import Database
from meshpi.events import EventHub
from meshpi.i18n import format_fields, using_language
from meshpi.ipc import IPCApplication, IPCServer
from meshpi.node_actions import NodeActionError, parse_traceroute_response
from meshpi.service import MeshtasticService

LOCALES = Path(__file__).parents[1] / "meshpi" / "locales"


def _backend_catalog(language: str) -> dict[str, str]:
    return json.loads(
        (LOCALES / f"backend.{language}.json").read_text(encoding="utf-8")
    )


def test_backend_catalogs_have_matching_keys_and_placeholders():
    nynorsk = _backend_catalog("nn")
    bokmal = _backend_catalog("nb")
    english = _backend_catalog("en")

    assert nynorsk.keys() == bokmal.keys() == english.keys()
    assert nynorsk
    assert all(key.startswith("backend.") for key in nynorsk)
    for key in nynorsk:
        assert (
            format_fields(nynorsk[key])
            == format_fields(bokmal[key])
            == format_fields(english[key])
        ), key


@pytest.mark.parametrize(
    ("language", "expected"),
    [
        ("nn", "Målnoden svara ikkje (NO_RESPONSE)"),
        ("nb", "Målnoden svarte ikke (NO_RESPONSE)"),
        ("en", "The target node did not respond (NO_RESPONSE)"),
    ],
)
def test_traceroute_error_is_translated_without_changing_reason_code(
    language: str,
    expected: str,
):
    packet = {
        "decoded": {
            "portnum": "ROUTING_APP",
            "routing": {"errorReason": "NO_RESPONSE"},
        }
    }

    with using_language(language), pytest.raises(NodeActionError) as captured:
        parse_traceroute_response(
            packet,
            local_node_id="!040840a0",
            target_node_id="!710365c8",
        )

    assert str(captured.value) == expected


def test_service_translates_reason_but_preserves_node_action_machine_values(tmp_path):
    database = Database(tmp_path / "db.sqlite")
    database.initialize()
    service = MeshtasticService(
        Settings(database_path=database.path),
        database,
        EventHub(),
    )

    results = {}
    for language in ("nn", "nb", "en"):
        with using_language(language):
            results[language] = service.node_action_availability(
                "traceroute", "!11112222"
            )

    for result in results.values():
        assert result["action"] == "traceroute"
        assert result["node_id"] == "!11112222"
        assert result["available"] is False
        assert result["cooldown_seconds"] == 0
    assert results["nn"]["reason"] == "Meshtastic-noden er ikkje tilkopla"
    assert results["nb"]["reason"] == "Meshtastic-noden er ikke tilkoblet"
    assert results["en"]["reason"] == "The Meshtastic node is not connected"


class _UnusedService:
    pass


def _ipc_unknown_command(tmp_path: Path, language: str) -> dict[str, object]:
    database = Database(tmp_path / f"{language}.sqlite")
    database.initialize()
    settings = Settings(
        database_path=database.path,
        ipc_port=0,
        ipc_token="b" * 64,
        ipc_transport="tcp",
    )
    server = IPCServer(
        settings,
        IPCApplication(settings, database, _UnusedService(), EventHub()),
    )
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        with socket.create_connection(server.address, timeout=2) as connection:
            stream = connection.makefile("rwb")
            stream.write(
                json.dumps(
                    {
                        "command": "does_not_exist",
                        "language": language,
                        "token": settings.ipc_token,
                    }
                ).encode("utf-8")
                + b"\n"
            )
            stream.flush()
            return json.loads(stream.readline())
    finally:
        server.shutdown()
        thread.join(timeout=2)


def test_ipc_uses_request_language_without_changing_error_response_format(tmp_path):
    nynorsk = _ipc_unknown_command(tmp_path, "nn")
    english = _ipc_unknown_command(tmp_path, "en")

    assert nynorsk == {"ok": False, "error": "Ukjend kommando"}
    assert english == {"ok": False, "error": "Unknown command"}


def test_ipc_bokmal_request_translates_error_without_changing_machine_format(tmp_path):
    assert _ipc_unknown_command(tmp_path, "nb") == {"ok": False, "error": "Ukjent kommando"}


def test_ipc_languages_preserve_node_action_machine_values_and_isolate_requests(tmp_path):
    database = Database(tmp_path / "actions.sqlite")
    database.initialize()
    settings = Settings(
        database_path=database.path,
        ipc_port=0,
        ipc_token="b" * 64,
        ipc_transport="tcp",
    )
    events = EventHub()
    # An offline service exercises the real protocol without opening a radio.
    service = MeshtasticService(settings, database, events)
    server = IPCServer(settings, IPCApplication(settings, database, service, events))
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        for language, expected in (
            ("nb", "Meshtastic-noden er ikke tilkoblet"),
            ("en", "The Meshtastic node is not connected"),
            ("nn", "Meshtastic-noden er ikkje tilkopla"),
            ("nb", "Meshtastic-noden er ikke tilkoblet"),
        ):
            with (
                socket.create_connection(server.address, timeout=2) as connection,
                connection.makefile("rwb") as stream,
            ):
                stream.write(json.dumps({
                    "command": "node_action_availability",
                    "action": "traceroute",
                    "node_id": "!11112222",
                    "language": language,
                    "token": settings.ipc_token,
                }).encode("utf-8") + b"\n")
                stream.flush()
                assert json.loads(stream.readline()) == {
                    "ok": True,
                    "data": {
                        "action": "traceroute",
                        "node_id": "!11112222",
                        "available": False,
                        "cooldown_seconds": 0,
                        "reason": expected,
                    },
                }
    finally:
        server.shutdown()
        thread.join(timeout=2)


@pytest.mark.parametrize(
    ("language", "expected"),
    [
        ("nb", "Webporten må være mellom 1 og 65535."),
        ("nn", "Webporten må vere mellom 1 og 65535."),
        ("en", "The web port must be between 1 and 65535."),
    ],
)
def test_web_configuration_validation_uses_selected_language(language, expected):
    from meshpi.config import validate_web_config

    with using_language(language), pytest.raises(ValueError) as captured:
        validate_web_config(0, "", "")
    assert str(captured.value) == expected


@pytest.mark.parametrize(
    ("language", "expected"),
    [
        ("nb", "Velg en gateway før du åpner historikkinnstillingene."),
        ("nn", "Vel ein gateway før du opnar historikkinnstillingane."),
        ("en", "Choose a gateway before opening history settings."),
    ],
)
def test_history_validation_uses_selected_language(tmp_path, language, expected):
    from types import SimpleNamespace

    database = Database(tmp_path / "db.sqlite")
    database.initialize()
    app = IPCApplication(
        Settings(), database, SimpleNamespace(status=lambda: {}), EventHub()
    )
    with using_language(language), pytest.raises(ValueError) as captured:
        app.dispatch({"command": "history_settings"})
    assert str(captured.value) == expected
