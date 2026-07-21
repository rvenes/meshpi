from __future__ import annotations

import hashlib
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def _text(name: str) -> str:
    return (ROOT / "installers" / name).read_text(encoding="utf-8")


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def test_manifest_matches_all_dependency_locks() -> None:
    manifest = json.loads((ROOT / "website" / "version.json").read_text(encoding="utf-8"))

    assert manifest["security"] == {
        "integrity": "sha256",
        "dependency_policy": "pip-require-hashes",
        "manifest_signature": "rsa-pkcs1v15-sha256",
    }
    for platform_name in ("linux", "macos", "windows"):
        lock = ROOT / "locks" / f"{platform_name}.txt"
        assert manifest["locks"][platform_name]["sha256"] == _sha256(lock)
        assert manifest["locks"][platform_name]["size"] == lock.stat().st_size
        assert "--hash=sha256:" in lock.read_text(encoding="utf-8")


def test_installers_use_locked_dependencies_and_offline_selftest() -> None:
    for name in ("install-linux.sh", "install-macos.sh"):
        source = _text(name)
        assert "--require-hashes" in source
        assert "--no-deps" in source
        assert "doctor --offline" in source
        assert "MESHPI_FORCE_HEALTH_FAILURE" in source
        assert "pip install -q --upgrade pip" not in source

    windows = _text("install-windows.ps1")
    assert '"--require-hashes"' in windows
    assert '"--no-deps"' in windows
    assert '"doctor", "--offline"' in windows
    assert "MESHPI_FORCE_HEALTH_FAILURE" in windows


def test_macos_switches_current_symlink_without_following_it() -> None:
    source = _text("install-macos.sh")
    assert "os.replace(sys.argv[1], sys.argv[2])" in source
    assert 'mv -f "$temporary" "$CURRENT_LINK"' not in source
    assert 'while launchctl print "$DOMAIN/$LABEL"' in source


def test_uninstallers_preserve_data_without_explicit_purge() -> None:
    linux = _text("uninstall-linux.sh")
    macos = _text("uninstall-macos.sh")
    windows = _text("uninstall-windows.ps1")

    assert "--purge-data" in linux and '[ "$PURGE" = "1" ]' in linux
    assert "--mode=session" in linux
    assert "--purge-data" in macos and '[ "$PURGE" = "1" ]' in macos
    assert "PurgeData" in windows and "if ($PurgeData)" in windows


def test_linux_always_mode_has_restricted_permissions() -> None:
    source = _text("install-linux.sh")
    assert 'chown "$INSTALL_USER:meshpi" "$CONFIG_FILE"' in source
    assert 'chmod 0640 "$CONFIG_FILE"' in source
    assert 'chown -R meshpi:meshpi "$STATE_DIR"' in source
    assert 'chmod 0750 "$STATE_DIR"' in source


def test_installers_do_not_ship_a_preselected_meshtastic_node() -> None:
    for name in ("install-linux.sh", "install-macos.sh", "install-windows.ps1"):
        source = _text(name)
        assert "MESHTASTIC_HOST=" in source
        development_address = ".".join(("10", "0", "0", "152"))
        assert development_address not in source


def test_windows_installer_reports_progress_and_ignores_missing_legacy_task() -> None:
    windows = _text("install-windows.ps1")

    assert (ROOT / "installers" / "install-windows.ps1").read_bytes().startswith(
        b"\xef\xbb\xbf"
    )
    for step in range(1, 9):
        assert f"Write-InstallStep {step} " in windows
    assert "Dette kan ta nokre minutt" in windows
    assert '$savedErrorActionPreference = $ErrorActionPreference' in windows
    assert '$ErrorActionPreference = "SilentlyContinue"' in windows
    assert '$ErrorActionPreference = $savedErrorActionPreference' in windows
    assert '& schtasks.exe /Delete /TN $taskName /F *> $null' in windows
