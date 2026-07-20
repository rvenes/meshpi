import pytest

from meshpi.update import UpdateCheckError, parse_update_manifest, platform_key


def manifest(version="0.4.0"):
    return {
        "schema_version": 1,
        "latest_version": version,
        "release_notes_url": "https://venes.org/meshpi/#release-notes",
        "installers": {
            "linux": {"update_command": "curl linux | sudo bash"},
            "macos": {"update_command": "curl macos | bash"},
            "windows": {"update_command": "powershell update"},
        },
    }


def test_update_manifest_selects_platform_command():
    notice = parse_update_manifest(
        manifest(),
        current_version="0.3.2",
        platform_name="win32",
    )
    assert notice is not None
    assert notice.latest_version == "0.4.0"
    assert notice.command == "powershell update"


def test_update_manifest_returns_none_for_current_or_newer_version():
    assert (
        parse_update_manifest(
            manifest("0.4.0"),
            current_version="0.4.0",
            platform_name="linux",
        )
        is None
    )
    assert (
        parse_update_manifest(
            manifest("0.3.9"),
            current_version="0.4.0",
            platform_name="linux",
        )
        is None
    )


def test_update_manifest_rejects_multiline_command_and_bad_version():
    value = manifest()
    value["installers"]["linux"]["update_command"] = "curl example\nbash"
    with pytest.raises(UpdateCheckError):
        parse_update_manifest(value, current_version="0.3.2", platform_name="linux")
    with pytest.raises(UpdateCheckError):
        parse_update_manifest(
            manifest("latest"),
            current_version="0.3.2",
            platform_name="linux",
        )


def test_platform_key():
    assert platform_key("linux") == "linux"
    assert platform_key("darwin") == "macos"
    assert platform_key("win32") == "windows"
