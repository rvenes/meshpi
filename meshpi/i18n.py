from __future__ import annotations

import json
import locale
import os
import string
import sys
import tempfile
from collections.abc import Iterator
from contextlib import contextmanager, suppress
from contextvars import ContextVar
from importlib.resources import files
from pathlib import Path
from typing import Any

SUPPORTED_LANGUAGES = ("nn", "en")
FALLBACK_LANGUAGE = "en"
NORWEGIAN_LANGUAGE_PREFIXES = frozenset({"nn", "nb", "no"})
LANGUAGE_NAMES = {"nn": "Nynorsk", "en": "English"}

_active_language: ContextVar[tuple[str, int] | None] = ContextVar(
    "meshpi_active_language", default=None
)
_process_language = "nn"
_language_generation = 0
_catalogs: dict[str, dict[str, str]] | None = None


def _load_catalogs() -> dict[str, dict[str, str]]:
    global _catalogs
    if _catalogs is None:
        root = files("meshpi").joinpath("locales")
        loaded: dict[str, dict[str, str]] = {}
        for language in SUPPORTED_LANGUAGES:
            merged: dict[str, str] = {}
            resources = sorted(
                (
                    resource
                    for resource in root.iterdir()
                    if resource.name == f"{language}.json"
                    or resource.name.endswith(f".{language}.json")
                ),
                key=lambda resource: resource.name,
            )
            for resource in resources:
                value = json.loads(resource.read_text(encoding="utf-8"))
                if not isinstance(value, dict) or not all(
                    isinstance(key, str) and isinstance(text, str)
                    for key, text in value.items()
                ):
                    raise RuntimeError(f"Ugyldig språkressurs: {resource.name}")
                duplicates = merged.keys() & value.keys()
                if duplicates:
                    duplicate = sorted(duplicates)[0]
                    raise RuntimeError(f"Duplisert omsetjingsnøkkel: {duplicate}")
                merged.update(value)
            loaded[language] = merged
        _catalogs = loaded
    return _catalogs


def catalog(language: str) -> dict[str, str]:
    return dict(_load_catalogs()[normalize_language(language)])


def normalize_language(language: str | None) -> str:
    value = str(language or "").strip().lower().replace("_", "-")
    prefix = value.split("-", 1)[0]
    if prefix in SUPPORTED_LANGUAGES:
        return prefix
    raise ValueError(f"Unsupported language: {language}")


def detect_system_language(system_language: str | None = None) -> str:
    value = system_language
    if value is None:
        value = (
            os.environ.get("LC_ALL")
            or os.environ.get("LC_MESSAGES")
            or os.environ.get("LANG")
        )
    if not value:
        value = locale.getlocale()[0]
    prefix = str(value or "").strip().lower().replace("_", "-").split("-", 1)[0]
    return "nn" if prefix in NORWEGIAN_LANGUAGE_PREFIXES else "en"


def language_file() -> Path:
    override = os.environ.get("MESHPI_LANGUAGE_FILE", "").strip()
    if override:
        return Path(override).expanduser()
    if os.name == "nt":
        root = Path(os.environ.get("LOCALAPPDATA") or Path.home() / "AppData/Local")
    elif sys.platform == "darwin":
        root = Path.home() / "Library/Application Support"
    else:
        root = Path(os.environ.get("XDG_CONFIG_HOME") or Path.home() / ".config")
    return root / "MeshPi" / "language.json"


def load_saved_language(path: Path | None = None) -> str | None:
    target = path or language_file()
    try:
        value = json.loads(target.read_text(encoding="utf-8"))
        if isinstance(value, dict):
            if value.get("schema_version", 1) != 1:
                return None
            return normalize_language(str(value.get("language", "")))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError, ValueError):
        return None
    return None


def _has_legacy_data(paths: tuple[Path, ...]) -> bool:
    return any(path.exists() for path in paths)


def choose_language(
    *,
    path: Path | None = None,
    legacy_paths: tuple[Path, ...] = (),
    system_language: str | None = None,
) -> str:
    explicit = os.environ.get("MESHPI_LANGUAGE", "").strip()
    if explicit:
        try:
            return normalize_language(explicit)
        except ValueError:
            pass
    saved = load_saved_language(path)
    if saved:
        return saved
    if _has_legacy_data(legacy_paths):
        return "nn"
    return FALLBACK_LANGUAGE


def initialize_language(
    *,
    path: Path | None = None,
    legacy_paths: tuple[Path, ...] = (),
    system_language: str | None = None,
) -> str:
    global _language_generation, _process_language
    language = choose_language(
        path=path,
        legacy_paths=legacy_paths,
        system_language=system_language,
    )
    _language_generation += 1
    _process_language = language
    _active_language.set((language, _language_generation))
    return language


def get_language() -> str:
    active = _active_language.get()
    if active is not None:
        language, generation = active
        if generation == _language_generation:
            return language
    return _process_language


def set_language(language: str, *, persist: bool = False, path: Path | None = None) -> str:
    global _language_generation, _process_language
    selected = normalize_language(language)
    if persist:
        save_language(selected, path=path)
    _language_generation += 1
    _process_language = selected
    _active_language.set((selected, _language_generation))
    return selected


def save_language(language: str, *, path: Path | None = None) -> Path:
    selected = normalize_language(language)
    target = path or language_file()
    target.parent.mkdir(parents=True, exist_ok=True)
    with suppress(OSError):
        target.parent.chmod(0o700)
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{target.name}.", suffix=".tmp", dir=target.parent
    )
    temporary = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n") as output:
            json.dump(
                {"schema_version": 1, "language": selected},
                output,
                ensure_ascii=False,
                indent=2,
            )
            output.write("\n")
            output.flush()
            os.fsync(output.fileno())
        with suppress(OSError):
            temporary.chmod(0o600)
        os.replace(temporary, target)
    finally:
        temporary.unlink(missing_ok=True)
    return target


@contextmanager
def using_language(language: str) -> Iterator[None]:
    token = _active_language.set(
        (normalize_language(language), _language_generation)
    )
    try:
        yield
    finally:
        _active_language.reset(token)


def tr(key: str, /, **values: Any) -> str:
    catalogs = _load_catalogs()
    language = get_language()
    text = catalogs.get(language, {}).get(key) or catalogs[FALLBACK_LANGUAGE].get(key)
    if text is None:
        return key
    return text.format(**values) if values else text


def format_fields(text: str) -> frozenset[str]:
    return frozenset(
        field_name
        for _literal, field_name, _format_spec, _conversion in string.Formatter().parse(text)
        if field_name
    )
