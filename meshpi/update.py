from __future__ import annotations

import json
import re
import sys
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlparse
from urllib.request import Request, urlopen

from meshpi import __version__
from meshpi.config import Settings

MAX_MANIFEST_BYTES = 128 * 1024
SEMVER = re.compile(r"^(\d+)\.(\d+)\.(\d+)(?:[-+][0-9A-Za-z.-]+)?$")


class UpdateCheckError(RuntimeError):
    pass


@dataclass(frozen=True, slots=True)
class UpdateNotice:
    current_version: str
    latest_version: str
    command: str
    release_notes_url: str | None = None


def _version_key(value: str) -> tuple[int, int, int]:
    match = SEMVER.fullmatch(value.strip())
    if match is None:
        raise UpdateCheckError(f"Ugyldig versjonsnummer: {value}")
    return tuple(int(part) for part in match.groups())


def platform_key(platform_name: str | None = None) -> str:
    value = platform_name or sys.platform
    if value.startswith("win"):
        return "windows"
    if value == "darwin":
        return "macos"
    return "linux"


def parse_update_manifest(
    manifest: dict[str, Any],
    *,
    current_version: str = __version__,
    platform_name: str | None = None,
) -> UpdateNotice | None:
    if manifest.get("schema_version") != 1:
        raise UpdateCheckError("Ustøtta versjonsmanifest")
    latest = str(manifest.get("latest_version", "")).strip()
    if _version_key(latest) <= _version_key(current_version):
        return None

    installers = manifest.get("installers")
    if not isinstance(installers, dict):
        raise UpdateCheckError("Versjonsmanifestet manglar installasjonar")
    installer = installers.get(platform_key(platform_name))
    if not isinstance(installer, dict):
        raise UpdateCheckError("Versjonsmanifestet manglar denne plattforma")
    command = str(installer.get("update_command", "")).strip()
    if not command or len(command) > 500 or "\n" in command or "\r" in command:
        raise UpdateCheckError("Ugyldig oppdateringskommando")
    notes = str(manifest.get("release_notes_url", "")).strip() or None
    return UpdateNotice(
        current_version=current_version,
        latest_version=latest,
        command=command,
        release_notes_url=notes,
    )


def check_for_update(settings: Settings) -> UpdateNotice | None:
    url = settings.update_url.strip()
    if not url:
        return None
    parsed = urlparse(url)
    if parsed.scheme != "https":
        raise UpdateCheckError("Oppdateringsadressa må bruke HTTPS")
    request = Request(
        url,
        headers={
            "Accept": "application/json",
            "User-Agent": f"MeshPi/{__version__}",
        },
    )
    try:
        with urlopen(request, timeout=settings.update_timeout) as response:  # noqa: S310
            raw = response.read(MAX_MANIFEST_BYTES + 1)
    except OSError as exc:
        raise UpdateCheckError(f"Klarte ikkje sjekke oppdatering: {exc}") from exc
    if len(raw) > MAX_MANIFEST_BYTES:
        raise UpdateCheckError("Versjonsmanifestet er for stort")
    try:
        manifest = json.loads(raw)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise UpdateCheckError("Versjonsmanifestet er ikkje gyldig JSON") from exc
    if not isinstance(manifest, dict):
        raise UpdateCheckError("Versjonsmanifestet må vere eit JSON-objekt")
    return parse_update_manifest(manifest)
