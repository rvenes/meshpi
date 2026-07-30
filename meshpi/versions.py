from __future__ import annotations

import re

VERSION_PATTERN = re.compile(
    r"^(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)"
    r"(?:(a|b|rc)(0|[1-9]\d*))?$"
)
_STAGE_ORDER = {
    "a": 0,
    "b": 1,
    "rc": 2,
    None: 3,
}


class VersionError(ValueError):
    pass


def version_key(value: str) -> tuple[int, int, int, int, int]:
    """Lag ein samanlikningsnøkkel for MeshPi si avgrensa PEP 440-form."""
    text = value.strip()
    match = VERSION_PATTERN.fullmatch(text)
    if match is None:
        raise VersionError(f"Ugyldig versjonsnummer: {value}")
    major, minor, patch, stage, stage_number = match.groups()
    return (
        int(major),
        int(minor),
        int(patch),
        _STAGE_ORDER[stage],
        int(stage_number or 0),
    )


def is_prerelease(value: str) -> bool:
    version_key(value)
    return VERSION_PATTERN.fullmatch(value.strip()).group(4) is not None  # type: ignore[union-attr]
