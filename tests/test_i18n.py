from __future__ import annotations

import json
from contextvars import copy_context

import pytest

from meshpi.i18n import (
    catalog,
    choose_language,
    detect_system_language,
    format_fields,
    get_language,
    load_saved_language,
    save_language,
    set_language,
    tr,
    using_language,
)


def test_catalogs_have_identical_keys_and_format_fields():
    nn = catalog("nn")
    en = catalog("en")
    assert nn.keys() == en.keys()
    for key in nn:
        assert format_fields(nn[key]) == format_fields(en[key]), key


@pytest.mark.parametrize("locale_name", ["nn_NO", "nb-NO", "no", "NO_no.UTF-8"])
def test_norwegian_system_languages_choose_nynorsk(locale_name):
    assert detect_system_language(locale_name) == "nn"


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
