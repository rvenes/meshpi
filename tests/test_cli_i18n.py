from __future__ import annotations

import json

from meshpi.cli import build_parser, main, run
from meshpi.config import Settings
from meshpi.i18n import get_language, set_language, using_language


def test_cli_help_is_available_in_both_languages():
    with using_language("nn"):
        assert "vis eller vel språk" in build_parser().format_help()
        assert "val:" in build_parser().format_help()
    with using_language("en"):
        assert "show or choose the language" in build_parser().format_help()
        assert "options:" in build_parser().format_help()


def test_language_command_saves_and_reuses_choice(tmp_path, monkeypatch, capsys):
    preference = tmp_path / "language.json"
    monkeypatch.setenv("MESHPI_LANGUAGE_FILE", str(preference))
    set_language("nn")

    run(build_parser().parse_args(["language", "en"]), Settings())

    assert get_language() == "en"
    assert "Language set to English" in capsys.readouterr().out
    assert json.loads(preference.read_text(encoding="utf-8"))["language"] == "en"


def test_language_command_does_not_start_daemon(tmp_path, monkeypatch, capsys):
    preference = tmp_path / "language.json"
    monkeypatch.setenv("MESHPI_LANGUAGE_FILE", str(preference))
    monkeypatch.setattr("meshpi.cli.Settings.load", lambda _path: Settings())
    monkeypatch.setattr(
        "meshpi.cli.manage_service",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("daemon started")),
    )
    monkeypatch.setattr(
        "meshpi.cli.daemon_status",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("daemon checked")),
    )

    main(["language", "en"])

    assert "Language set to English" in capsys.readouterr().out
