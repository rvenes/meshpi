from __future__ import annotations

import json
from contextvars import copy_context

import pytest

from meshpi.i18n import (
    SUPPORTED_LANGUAGES,
    catalog,
    choose_language,
    detect_system_language,
    format_fields,
    get_language,
    load_saved_language,
    normalize_language,
    save_language,
    set_language,
    tr,
    using_language,
)


@pytest.mark.parametrize("language", SUPPORTED_LANGUAGES)
def test_catalogs_have_identical_keys_and_format_fields(language):
    translated = catalog(language)
    en = catalog("en")
    assert translated.keys() == en.keys()
    for key in en:
        assert translated[key], key
        assert format_fields(translated[key]) == format_fields(en[key]), key


@pytest.mark.parametrize(("locale_name", "expected"), [
    ("nn_NO", "nn"), ("nb-NO", "nb"), ("no", "nb"), ("NO_no.UTF-8", "nb"),
])
def test_norwegian_system_languages_choose_matching_language(locale_name, expected):
    assert detect_system_language(locale_name) == expected


@pytest.mark.parametrize("locale_name", ["en_GB", "de_DE", "sv-SE", ""])
def test_other_system_languages_choose_english(locale_name, monkeypatch):
    monkeypatch.delenv("LANG", raising=False)
    monkeypatch.delenv("LC_ALL", raising=False)
    monkeypatch.delenv("LC_MESSAGES", raising=False)
    if locale_name:
        assert detect_system_language(locale_name) == "en"


def test_existing_user_without_saved_choice_keeps_nynorsk(tmp_path, monkeypatch):
    monkeypatch.delenv("MESHPI_LANGUAGE", raising=False)
    legacy = tmp_path / "meshtastic.db"
    legacy.touch()
    assert choose_language(
        path=tmp_path / "language.json",
        legacy_paths=(legacy,),
        system_language="en_US",
    ) == "nn"


def test_fresh_user_defaults_to_english_on_all_system_languages(tmp_path, monkeypatch):
    monkeypatch.delenv("MESHPI_LANGUAGE", raising=False)
    assert choose_language(
        path=tmp_path / "language.json",
        system_language="en_US",
    ) == "en"
    assert choose_language(
        path=tmp_path / "language.json",
        system_language="nb_NO",
    ) == "en"


def test_saved_language_is_atomic_and_reused(tmp_path):
    path = tmp_path / "preferences" / "language.json"
    assert save_language("en", path=path) == path
    assert load_saved_language(path) == "en"
    assert json.loads(path.read_text(encoding="utf-8")) == {
        "schema_version": 1,
        "language": "en",
    }
    assert not list(path.parent.glob("*.tmp"))


def test_language_switch_is_immediate():
    set_language("nn")
    assert tr("common.cancel") == "Avbryt"
    set_language("en")
    assert tr("common.cancel") == "Cancel"


def test_language_switch_invalidates_context_captured_by_existing_timer():
    set_language("nn")
    timer_context = copy_context()

    set_language("en")

    assert timer_context.run(get_language) == "en"
    assert timer_context.run(tr, "common.cancel") == "Cancel"
    with using_language("nn"):
        assert tr("common.cancel") == "Avbryt"
    assert tr("common.cancel") == "Cancel"


def test_english_catalog_is_fallback(monkeypatch):
    import meshpi.i18n as i18n

    catalogs = {"nn": {}, "en": {"fallback.only": "English fallback"}}
    monkeypatch.setattr(i18n, "_catalogs", catalogs)
    with using_language("nn"):
        assert tr("fallback.only") == "English fallback"


def test_missing_key_returns_stable_key():
    with using_language("en"):
        assert tr("missing.stable.key") == "missing.stable.key"


@pytest.mark.parametrize("language", SUPPORTED_LANGUAGES)
def test_all_language_choices_are_saved_and_reused(tmp_path, monkeypatch, language):
    monkeypatch.delenv("MESHPI_LANGUAGE", raising=False)
    path = tmp_path / "language.json"
    save_language(language, path=path)
    assert choose_language(path=path) == language


@pytest.mark.parametrize("value", ["nb", "NB", "nb-NO", "nb_NO"])
def test_bokmal_regional_language_codes(value):
    assert normalize_language(value) == "nb"


def test_bokmal_explicit_environment_choice_takes_priority(tmp_path, monkeypatch):
    path = tmp_path / "language.json"
    save_language("nn", path=path)
    monkeypatch.setenv("MESHPI_LANGUAGE", "nb")
    assert choose_language(path=path) == "nb"


def test_client_translates_older_daemon_errors_and_preserves_request_data(monkeypatch):
    import json

    from meshpi.client import CLIError, request
    from meshpi.config import Settings

    captured = []

    class Stream:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            pass

        def write(self, raw):
            captured.append(json.loads(raw))

        def flush(self):
            pass

        def readline(self, maximum):
            return json.dumps({'ok': False, 'error': 'Meshtastic-noden er ikkje tilkopla'}).encode()

    class Connection(Stream):
        def makefile(self, mode):
            return Stream()

    monkeypatch.setattr('meshpi.client._connect', lambda *_: Connection())
    with using_language('nb'), pytest.raises(CLIError) as error:
        request(Settings(ipc_token='test-only-token'), {'command': 'status'})
    assert str(error.value) == 'Meshtastic-noden er ikke tilkoblet'
    assert captured == [{'command': 'status', 'token': 'test-only-token', 'language': 'nb'}]
