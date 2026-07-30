import json

import pytest
from cryptography.hazmat.primitives.asymmetric import rsa

from meshpi.versions import is_prerelease
from scripts import prepare_release
from scripts.prepare_release import (
    beta_seed_manifest,
    sign_manifest,
    validate_release_request,
)


def test_prepare_release_rejects_private_key_that_does_not_match_key_id():
    unrelated_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    manifest = {"schema_version": 1, "product": "MeshPi"}

    with pytest.raises(SystemExit, match="samsvarar ikkje med key_id"):
        sign_manifest(manifest, unrelated_key)


@pytest.mark.parametrize(
    ("version", "expected"),
    [
        ("0.8.6", False),
        ("0.9.0a1", True),
        ("0.9.0b1", True),
        ("0.9.0rc1", True),
    ],
)
def test_release_versions_identify_prereleases(version, expected):
    assert is_prerelease(version) is expected


def test_release_channel_requires_matching_version_kind_and_beta_notes():
    validate_release_request("0.8.6", "stable", None)
    validate_release_request("0.9.0b1", "beta", ["Testutgåve"])

    with pytest.raises(SystemExit, match="Stable-kanalen"):
        validate_release_request("0.9.0b1", "stable", ["Feil kanal"])
    with pytest.raises(SystemExit, match="Beta-kanalen krev ein versjon"):
        validate_release_request("0.9.0", "beta", ["Feil kanal"])
    with pytest.raises(SystemExit, match="minst eitt"):
        validate_release_request("0.9.0b1", "beta", None)


def test_beta_seed_keeps_stable_artifacts_but_changes_signed_channel(monkeypatch):
    stable = {
        "channel": "stable",
        "latest_version": "0.8.6",
        "package": {"url": "https://venes.org/meshpi/downloads/meshpi.whl"},
        "installers": {
            "linux": {"update_command": "sudo meshpi update"},
            "macos": {"update_command": "meshpi update"},
            "windows": {"update_command": "meshpi update"},
        },
        "signature": {"value": "stable"},
    }
    monkeypatch.setattr(
        prepare_release,
        "sign_manifest",
        lambda manifest, _key: manifest.update(signature={"value": "beta"}),
    )

    beta = beta_seed_manifest(stable, object())

    assert beta["channel"] == "beta"
    assert beta["latest_version"] == "0.8.6"
    assert beta["package"] == stable["package"]
    assert beta["installers"]["linux"]["update_command"] == (
        "sudo meshpi update --beta"
    )
    assert beta["signature"] == {"value": "beta"}
    assert stable["channel"] == "stable"
    assert stable["signature"] == {"value": "stable"}


def test_beta_release_writes_only_beta_manifest_with_beta_urls(tmp_path, monkeypatch):
    def write(relative, content):
        path = tmp_path / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
        return path

    write("pyproject.toml", '[project]\nversion = "0.9.0b1"\n')
    write("meshpi/__init__.py", '__version__ = "0.9.0b1"\n')
    stable_manifest = {
        "schema_version": 1,
        "product": "MeshPi",
        "latest_version": "0.8.6",
    }
    write("website/version.json", json.dumps(stable_manifest))
    for platform_name in ("linux", "macos", "windows"):
        write(f"locks/{platform_name}.txt", f"{platform_name}\n")
    for filename in (
        "install-linux.sh",
        "install-macos.sh",
        "install-windows.ps1",
    ):
        write(f"installers/{filename}", filename)
    wheel = write(
        "build/release-0.9.0b1/meshpi-0.9.0b1-py3-none-any.whl",
        "wheel",
    )
    key = write("signing.pem", "test")

    def fake_sign(manifest, _private_key):
        manifest["signature"] = {"value": "test"}

    monkeypatch.setattr(prepare_release, "ROOT", tmp_path)
    monkeypatch.setattr(prepare_release, "sign_manifest", fake_sign)
    monkeypatch.setattr(
        prepare_release.serialization,
        "load_pem_private_key",
        lambda *_args, **_kwargs: object(),
    )
    monkeypatch.setattr(
        prepare_release.sys,
        "argv",
        [
            "prepare_release.py",
            "--skip-build",
            "--channel",
            "beta",
            "--release-note",
            "Intern test",
            "--signing-key",
            str(key),
        ],
    )

    prepare_release.main()

    generated = json.loads(
        (tmp_path / "website/beta/version.json").read_text(encoding="utf-8")
    )
    assert json.loads(
        (tmp_path / "website/version.json").read_text(encoding="utf-8")
    ) == stable_manifest
    assert generated["channel"] == "beta"
    assert generated["latest_version"] == "0.9.0b1"
    assert generated["package"]["filename"] == wheel.name
    assert generated["package"]["url"].startswith(
        "https://venes.org/meshpi/beta/downloads/"
    )
    assert generated["locks"]["linux"]["url"].startswith(
        "https://venes.org/meshpi/beta/locks/"
    )
    assert generated["installers"]["windows"]["url"] == (
        "https://venes.org/meshpi/beta/install-windows.ps1"
    )
