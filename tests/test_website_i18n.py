from __future__ import annotations

import re
from html.parser import HTMLParser
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
WEBSITE = ROOT / "website"


class _WebsiteParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.elements: list[tuple[str, dict[str, str | None], int]] = []
        self.stack: list[tuple[str, dict[str, str | None], int]] = []
        self.untranslated_nynorsk: list[tuple[int, str]] = []

    def handle_starttag(
        self, tag: str, attrs: list[tuple[str, str | None]]
    ) -> None:
        element = (tag, dict(attrs), self.getpos()[0])
        self.elements.append(element)
        self.stack.append(element)

    def handle_startendtag(
        self, tag: str, attrs: list[tuple[str, str | None]]
    ) -> None:
        self.elements.append((tag, dict(attrs), self.getpos()[0]))

    def handle_endtag(self, tag: str) -> None:
        for index in range(len(self.stack) - 1, -1, -1):
            if self.stack[index][0] == tag:
                del self.stack[index:]
                return

    def handle_data(self, data: str) -> None:
        text = " ".join(data.split())
        if not text or (self.stack and self.stack[-1][0] in {"script", "style"}):
            return
        if not re.search(
            r"[æøåÆØÅ]|\b(?:og|eller|ein|eit|frå|på|utan|køyr|"
            r"vis|opne|vel|siste|versjon|kanal|melding|nodar|namn|kopier|lisens)\b",
            text,
            re.IGNORECASE,
        ):
            return
        translated = any(
            "data-en" in attrs or "data-en-html" in attrs
            for _tag, attrs, _line in self.stack
        )
        if not translated:
            self.untranslated_nynorsk.append((self.getpos()[0], text))


def _parse_website(relative_path: str = "index.html") -> _WebsiteParser:
    parser = _WebsiteParser()
    parser.feed((WEBSITE / relative_path).read_text(encoding="utf-8"))
    return parser


def test_public_website_has_complete_nynorsk_and_english_text_contract() -> None:
    parser = _parse_website()
    translated = [
        attrs
        for _tag, attrs, _line in parser.elements
        if "data-en" in attrs or "data-en-html" in attrs
    ]

    assert len(translated) >= 100
    assert all(
        (attrs.get("data-en") or attrs.get("data-en-html") or "").strip()
        for attrs in translated
    )
    assert parser.untranslated_nynorsk == []


def test_language_picker_and_dynamic_accessibility_metadata_are_present() -> None:
    parser = _parse_website()
    elements = parser.elements

    language_buttons = {
        attrs.get("data-language"): attrs
        for tag, attrs, _line in elements
        if tag == "button" and "data-language" in attrs
    }
    assert set(language_buttons) == {"nn", "en"}
    assert language_buttons["nn"]["aria-pressed"] == "true"
    assert language_buttons["en"]["aria-pressed"] == "false"

    localized_aria = [attrs for _tag, attrs, _line in elements if "data-aria-en" in attrs]
    assert len(localized_aria) >= 5
    assert all(attrs.get("aria-label") and attrs.get("data-aria-en") for attrs in localized_aria)


def test_language_runtime_detects_persists_and_applies_language() -> None:
    script = (WEBSITE / "script.js").read_text(encoding="utf-8")

    assert 'LANGUAGE_STORAGE_KEY = "meshpi-language"' in script
    assert "navigator.languages" in script
    assert "/^(nn|nb|no)(-|$)/i" in script
    assert 'return requested.some' in script
    assert '? "nn"' in script
    assert ': "en"' in script
    assert "localStorage.getItem(LANGUAGE_STORAGE_KEY)" in script
    assert "localStorage.setItem(LANGUAGE_STORAGE_KEY, language)" in script
    assert "applyLanguage(savedLanguage() || browserLanguage())" in script

    assert "document.documentElement.lang = activeLanguage" in script
    assert "document.title = title || metadata.title" in script
    assert "meta[name=\"description\"]" in script
    assert 'element.setAttribute("aria-label"' in script
    assert 'button.setAttribute(\n      "aria-pressed"' in script
    assert 'dateLocale: "nn-NO"' in script
    assert 'dateLocale: "en-GB"' in script


def test_language_picker_is_responsive_and_stable_release_copy_is_unchanged() -> None:
    html = (WEBSITE / "index.html").read_text(encoding="utf-8")
    styles = (WEBSITE / "styles.css").read_text(encoding="utf-8")

    assert ".language-switcher" in styles
    assert '.language-switcher button[aria-pressed="true"]' in styles
    assert "@media (max-width: 900px)" in styles
    assert "@media (max-width: 720px)" in styles

    assert "Versjon 0.9.0" in html
    assert "Version 0.9.0" in html
    assert "downloads/meshpi-0.9.0-py3-none-any.whl" in html
    assert "0.8.9" not in html


def test_beta_page_is_bilingual_and_uses_the_shared_language_choice() -> None:
    parser = _parse_website("beta/index.html")
    html = (WEBSITE / "beta" / "index.html").read_text(encoding="utf-8")

    assert parser.untranslated_nynorsk == []
    language_buttons = {
        attrs.get("data-language")
        for tag, attrs, _line in parser.elements
        if tag == "button"
    }
    assert language_buttons == {"nn", "en"}
    assert 'data-title-nn="MeshPi betakanal"' in html
    assert 'data-title-en="MeshPi beta channel"' in html
    assert '<script src="../script.js" defer></script>' in html
    assert "MeshPi 0.9.0" in html
