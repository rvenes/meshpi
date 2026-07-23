import pytest

from meshpi.client import CLIError
from meshpi.config import Settings
from meshpi.lifecycle import stop_daemon


def test_stop_daemon_succeeds_when_response_is_lost_but_daemon_stops(monkeypatch):
    statuses = iter(({"state": "tilkopla"}, None))
    monkeypatch.setattr(
        "meshpi.lifecycle.daemon_status",
        lambda _settings, timeout=0.5: next(statuses),
    )

    def lose_response(*_args, **_kwargs):
        raise CLIError("IPC-sambandet blei brote")

    monkeypatch.setattr("meshpi.lifecycle.request", lose_response)

    assert stop_daemon(Settings(), timeout=1) is True


def test_stop_daemon_preserves_request_error_when_daemon_does_not_stop(monkeypatch):
    monkeypatch.setattr(
        "meshpi.lifecycle.daemon_status",
        lambda _settings, timeout=0.5: {"state": "tilkopla"},
    )

    def reject_request(*_args, **_kwargs):
        raise CLIError("IPC-stopp feila")

    monkeypatch.setattr("meshpi.lifecycle.request", reject_request)

    with pytest.raises(CLIError, match="IPC-stopp feila"):
        stop_daemon(Settings(), timeout=0)
