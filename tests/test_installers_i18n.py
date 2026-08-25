from __future__ import annotations

import os
import re
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
INSTALLERS = ROOT / "installers"
POSIX_SCRIPTS = (
    "install-linux.sh",
    "install-macos.sh",
    "uninstall-linux.sh",
    "uninstall-macos.sh",
)
POWERSHELL_SCRIPTS = ("install-windows.ps1", "uninstall-windows.ps1")


def _posix_shell() -> str | None:
    shell = shutil.which("sh")
    if shell:
        return shell
    git = shutil.which("git")
    if git:
        git_shell = Path(git).resolve().parents[1] / "bin" / "sh.exe"
        if git_shell.is_file():
            return str(git_shell)
    return None


POSIX_SHELL = _posix_shell()


def _text(name: str) -> str:
    return (INSTALLERS / name).read_text(encoding="utf-8")


def _run_posix_argument_error(
    name: str,
    *arguments: str,
    locale: str = "C",
    environment_language: str | None = None,
) -> subprocess.CompletedProcess[str]:
    environment = {
        "PATH": os.environ.get("PATH", "/usr/bin:/bin"),
        "LANG": locale,
    }
    if environment_language is not None:
        environment["MESHPI_LANGUAGE"] = environment_language
    return subprocess.run(
        [POSIX_SHELL, str(INSTALLERS / name), *arguments, "--not-a-real-option"],
        check=False,
        capture_output=True,
        text=True,
        encoding="utf-8",
        env=environment,
    )


@pytest.mark.skipif(POSIX_SHELL is None, reason="krev POSIX-shell")
@pytest.mark.parametrize("name", POSIX_SCRIPTS)
def test_posix_scripts_detect_norwegian_locales(name: str) -> None:
    for locale in ("nn_NO.UTF-8", "nb_NO.UTF-8", "no_NO.UTF-8"):
        result = _run_posix_argument_error(name, locale=locale)
        assert result.returncode == 2
        assert "Ukjent argument" in result.stderr
        assert "Unknown argument" not in result.stderr


@pytest.mark.skipif(POSIX_SHELL is None, reason="krev POSIX-shell")
@pytest.mark.parametrize("name", POSIX_SCRIPTS)
def test_posix_scripts_default_to_english_and_allow_explicit_override(
    name: str,
) -> None:
    english = _run_posix_argument_error(name, locale="de_DE.UTF-8")
    assert english.returncode == 2
    assert "Unknown argument" in english.stderr

    nynorsk = _run_posix_argument_error(
        name,
        "--language=nn",
        locale="de_DE.UTF-8",
    )
    assert nynorsk.returncode == 2
    assert "Ukjent argument" in nynorsk.stderr

    env_english = _run_posix_argument_error(
        name,
        locale="nb_NO.UTF-8",
        environment_language="en",
    )
    assert env_english.returncode == 2
    assert "Unknown argument" in env_english.stderr


@pytest.mark.parametrize("name", POSIX_SCRIPTS)
def test_posix_message_catalogues_have_matching_language_keys(name: str) -> None:
    source = _text(name)
    nynorsk = set(re.findall(r"^\s{8}([a-z0-9_]+):nn\)", source, re.MULTILINE))
    english = set(re.findall(r"^\s{8}([a-z0-9_]+):en\)", source, re.MULTILINE))

    assert nynorsk
    assert nynorsk == english
    assert "--language=nn" in source
    assert "--language=en" in source
    assert "MESHPI_LANGUAGE" in source


@pytest.mark.parametrize("name", POWERSHELL_SCRIPTS)
def test_powershell_message_catalogues_are_parallel(name: str) -> None:
    source = _text(name)
    rows = re.findall(
        r"^\s{4}([a-z0-9_]+)\s*=\s*@\{\s*nn\s*=\s*'[^']*';\s*"
        r"en\s*=\s*'[^']*'\s*\}",
        source,
        re.MULTILINE,
    )

    assert rows
    assert len(rows) == len(set(rows))
    assert '[string]$Language = ""' in source
    assert "$env:MESHPI_LANGUAGE" in source
    assert '"^(nn|nb|no)(-|$)"' in source


@pytest.mark.skipif(
    shutil.which("powershell.exe") is None,
    reason="krev Windows PowerShell",
)
def test_windows_uninstaller_honours_explicit_language(tmp_path: Path) -> None:
    environment = os.environ.copy()
    environment.update(
        {
            "MESHPI_INSTALL_ROOT": str(tmp_path / "install"),
            "MESHPI_CONFIG_ROOT": str(tmp_path / "config"),
            "MESHPI_SKIP_TASK": "1",
            "MESHPI_LANGUAGE": "nn",
        }
    )
    result = subprocess.run(
        [
            "powershell.exe",
            "-NoProfile",
            "-NonInteractive",
            "-ExecutionPolicy",
            "Bypass",
            "-File",
            str(INSTALLERS / "uninstall-windows.ps1"),
            "-Language",
            "en",
        ],
        check=False,
        capture_output=True,
        text=True,
        encoding="utf-8",
        env=environment,
    )

    assert result.returncode == 0, result.stderr
    assert "Removed the MeshPi application and autostart." in result.stdout
    assert "Fjerna MeshPi-programmet" not in result.stdout


def test_windows_scripts_use_utf8_bom_for_windows_powershell() -> None:
    for name in POWERSHELL_SCRIPTS:
        assert (INSTALLERS / name).read_bytes().startswith(b"\xef\xbb\xbf")


def test_installers_only_write_language_file_for_fresh_installations() -> None:
    linux = _text("install-linux.sh")
    macos = _text("install-macos.sh")
    windows = _text("install-windows.ps1")

    for source in (linux, macos):
        assert 'FRESH_INSTALL=0' in source
        assert '[ "$FRESH_INSTALL" = "1" ]' in source
        assert 'printf \'{"language":"%s"}\\n\'' in source
        assert 'chmod 0600 "$language_tmp"' in source
        assert 'mv -f "$language_tmp" "$LANGUAGE_FILE"' in source

    assert "$freshInstall = -not (Test-Path -LiteralPath $installRoot)" in windows
    assert "$freshInstall -and -not (Test-Path -LiteralPath $languageFile)" in windows
    assert "Move-Item -LiteralPath $languageTemporary" in windows


def test_uninstallers_never_delete_preferences_without_purge() -> None:
    linux = _text("uninstall-linux.sh")
    macos = _text("uninstall-macos.sh")
    windows = _text("uninstall-windows.ps1")

    assert 'rm -f "$CONFIG_FILE"' in linux
    assert '[ "$PURGE" = "1" ]' in linux
    assert 'rm -rf "$APP_ROOT"' in macos
    assert '[ "$PURGE" = "1" ]' in macos
    assert "Remove-Item -LiteralPath $configRoot" in windows
    assert "if ($PurgeData)" in windows
