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
    english = _backend_catalog("en")

    assert nynorsk.keys() == english.keys()
    assert nynorsk
    assert all(key.startswith("backend.") for key in nynorsk)
    for key in nynorsk:
        assert format_fields(nynorsk[key]) == format_fields(english[key]), key


@pytest.mark.parametrize(
    ("language", "expected"),
    [
        ("nn", "Målnoden svara ikkje (NO_RESPONSE)"),
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
    for language in ("nn", "en"):
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
