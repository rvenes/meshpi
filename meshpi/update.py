from __future__ import annotations

import hashlib
import json
import os
import re
import subprocess  # nosec B404
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any, BinaryIO
from urllib.parse import urlparse
from urllib.request import Request, urlopen

from meshpi import __version__
from meshpi.config import Settings
from meshpi.platform_service import windows_powershell_path
from meshpi.signing import SignatureError, verify_manifest_signature
from meshpi.versions import VersionError, version_key

MAX_MANIFEST_BYTES = 128 * 1024
MAX_INSTALLER_BYTES = 2 * 1024 * 1024
MAX_LOCK_BYTES = 5 * 1024 * 1024
MAX_PACKAGE_BYTES = 50 * 1024 * 1024
SHA256 = re.compile(r"^[0-9a-f]{64}$")
BETA_UPDATE_URL = "https://venes.org/meshpi/beta/version.json"
UPDATE_CHANNELS = frozenset({"stable", "beta"})


class UpdateCheckError(RuntimeError):
    pass


@dataclass(frozen=True, slots=True)
class UpdateNotice:
    current_version: str
    latest_version: str
    command: str
    release_notes_url: str | None = None
    channel: str = "stable"


@dataclass(frozen=True, slots=True)
class UpdateArtifact:
    label: str
    filename: str
    url: str
    sha256: str
    size: int
    maximum_size: int


@dataclass(frozen=True, slots=True)
class UpdatePlan:
    current_version: str
    latest_version: str
    platform: str
    manifest: dict[str, Any]
    manifest_bytes: bytes
    installer: UpdateArtifact
    package: UpdateArtifact
    lock: UpdateArtifact
    channel: str = "stable"


def _channel(value: str) -> str:
    channel = value.strip().lower()
    if channel not in UPDATE_CHANNELS:
        raise UpdateCheckError(f"Ugyldig oppdateringskanal: {value}")
    return channel


def platform_key(platform_name: str | None = None) -> str:
    value = platform_name or sys.platform
    if value.startswith("win"):
        return "windows"
    if value == "darwin":
        return "macos"
    return "linux"


def _https_url(value: Any, label: str) -> str:
    url = str(value or "").strip()
    parsed = urlparse(url)
    if parsed.scheme != "https" or not parsed.netloc or parsed.username is not None:
        raise UpdateCheckError(f"{label} må bruke ei gyldig HTTPS-adresse")
    return url


def _artifact(
    data: Any,
    *,
    label: str,
    maximum_size: int,
    filename: str | None = None,
) -> UpdateArtifact:
    if not isinstance(data, dict):
        raise UpdateCheckError(f"Versjonsmanifestet manglar {label}")
    url = _https_url(data.get("url"), label)
    expected_hash = str(data.get("sha256", "")).strip().lower()
    if SHA256.fullmatch(expected_hash) is None:
        raise UpdateCheckError(f"{label} har ugyldig SHA-256")
    try:
        size = int(data.get("size", 0))
    except (TypeError, ValueError) as exc:
        raise UpdateCheckError(f"{label} har ugyldig storleik") from exc
    if not 0 < size <= maximum_size:
        raise UpdateCheckError(f"{label} har ugyldig storleik")
    artifact_name = filename or Path(urlparse(url).path).name
    if (
        not artifact_name
        or artifact_name != Path(artifact_name).name
        or "\x00" in artifact_name
    ):
        raise UpdateCheckError(f"{label} har ugyldig filnamn")
    return UpdateArtifact(
        label=label,
        filename=artifact_name,
        url=url,
        sha256=expected_hash,
        size=size,
        maximum_size=maximum_size,
    )


def _parse_update_plan(
    manifest: dict[str, Any],
    manifest_bytes: bytes,
    *,
    current_version: str,
    platform_name: str | None,
    channel: str,
) -> UpdatePlan | None:
    try:
        verify_manifest_signature(manifest)
    except SignatureError as exc:
        raise UpdateCheckError(str(exc)) from exc
    if manifest.get("schema_version") != 1:
        raise UpdateCheckError("Ustøtta versjonsmanifest")
    expected_channel = _channel(channel)
    manifest_channel = str(manifest.get("channel", "stable")).strip().lower()
    if manifest_channel not in UPDATE_CHANNELS:
        raise UpdateCheckError("Versjonsmanifestet har ein ugyldig kanal")
    if manifest_channel != expected_channel:
        raise UpdateCheckError(
            f"Versjonsmanifestet er for {manifest_channel}-kanalen, "
            f"ikkje {expected_channel}-kanalen"
        )
    latest = str(manifest.get("latest_version", "")).strip()
    try:
        available_key = version_key(latest)
        current_key = version_key(current_version)
    except VersionError as exc:
        raise UpdateCheckError(str(exc)) from exc
    if available_key <= current_key:
        return None
    platform = platform_key(platform_name)
    installers = manifest.get("installers")
    locks = manifest.get("locks")
    if not isinstance(installers, dict) or not isinstance(locks, dict):
        raise UpdateCheckError("Versjonsmanifestet manglar plattformfiler")
    installer = _artifact(
        installers.get(platform),
        label=f"{platform}-installatøren",
        maximum_size=MAX_INSTALLER_BYTES,
    )
    package_data = manifest.get("package")
    package_filename = (
        str(package_data.get("filename", "")).strip()
        if isinstance(package_data, dict)
        else ""
    )
    package = _artifact(
        package_data,
        label="MeshPi-pakken",
        maximum_size=MAX_PACKAGE_BYTES,
        filename=package_filename,
    )
    lock = _artifact(
        locks.get(platform),
        label=f"{platform}-låsefila",
        maximum_size=MAX_LOCK_BYTES,
    )
    return UpdatePlan(
        current_version=current_version,
        latest_version=latest,
        platform=platform,
        manifest=manifest,
        manifest_bytes=manifest_bytes,
        installer=installer,
        package=package,
        lock=lock,
        channel=expected_channel,
    )


def parse_update_manifest(
    manifest: dict[str, Any],
    *,
    current_version: str = __version__,
    platform_name: str | None = None,
    background_mode: str = "always",
    channel: str = "stable",
) -> UpdateNotice | None:
    raw = json.dumps(manifest, ensure_ascii=False).encode("utf-8")
    plan = _parse_update_plan(
        manifest,
        raw,
        current_version=current_version,
        platform_name=platform_name,
        channel=channel,
    )
    if plan is None:
        return None
    notes = str(manifest.get("release_notes_url", "")).strip() or None
    if notes is not None:
        notes = _https_url(notes, "Utgåvenotata")
    selected_channel = _channel(channel)
    command_suffix = " --beta" if selected_channel == "beta" else ""
    return UpdateNotice(
        current_version=current_version,
        latest_version=plan.latest_version,
        command=(
            f"sudo meshpi update{command_suffix}"
            if plan.platform == "linux" and background_mode == "always"
            else f"meshpi update{command_suffix}"
        ),
        release_notes_url=notes,
        channel=selected_channel,
    )


def _read_limited(response: BinaryIO, maximum: int, label: str) -> bytes:
    raw = response.read(maximum + 1)
    if len(raw) > maximum:
        raise UpdateCheckError(f"{label} er for stor")
    return raw


def _fetch_manifest(
    settings: Settings,
    *,
    channel: str = "stable",
) -> tuple[dict[str, Any], bytes]:
    selected_channel = _channel(channel)
    configured_url = (
        settings.update_url if selected_channel == "stable" else BETA_UPDATE_URL
    )
    url = _https_url(configured_url, "Oppdateringsadressa")
    request = Request(
        url,
        headers={
            "Accept": "application/json",
            "User-Agent": f"MeshPi/{__version__}",
        },
    )
    try:
        # URL-en og den endelege adressa blir eksplisitt kontrollerte som HTTPS.
        with urlopen(request, timeout=settings.update_timeout) as response:  # nosec B310
            _https_url(response.geturl(), "Den endelege oppdateringsadressa")
            raw = _read_limited(response, MAX_MANIFEST_BYTES, "Versjonsmanifestet")
    except UpdateCheckError:
        raise
    except OSError as exc:
        raise UpdateCheckError(f"Klarte ikkje sjekke oppdatering: {exc}") from exc
    try:
        manifest = json.loads(raw)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise UpdateCheckError("Versjonsmanifestet er ikkje gyldig JSON") from exc
    if not isinstance(manifest, dict):
        raise UpdateCheckError("Versjonsmanifestet må vere eit JSON-objekt")
    return manifest, raw


def check_for_update(
    settings: Settings,
    *,
    channel: str = "stable",
) -> UpdateNotice | None:
    if not settings.update_url.strip():
        return None
    manifest, _raw = _fetch_manifest(settings, channel=channel)
    return parse_update_manifest(
        manifest,
        background_mode=settings.background_mode,
        channel=channel,
    )


def prepare_update(
    settings: Settings,
    *,
    current_version: str = __version__,
    platform_name: str | None = None,
    channel: str = "stable",
) -> UpdatePlan | None:
    manifest, raw = _fetch_manifest(settings, channel=channel)
    return _parse_update_plan(
        manifest,
        raw,
        current_version=current_version,
        platform_name=platform_name,
        channel=channel,
    )


def _private_write(path: Path, payload: bytes) -> None:
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(descriptor, "wb") as output:
        output.write(payload)


def _download_artifact(
    artifact: UpdateArtifact,
    destination: Path,
    *,
    timeout: float,
) -> None:
    request = Request(
        artifact.url,
        headers={"User-Agent": f"MeshPi/{__version__}"},
    )
    descriptor = os.open(
        destination,
        os.O_WRONLY | os.O_CREAT | os.O_EXCL,
        0o600,
    )
    digest = hashlib.sha256()
    total = 0
    try:
        with (
            os.fdopen(descriptor, "wb") as output,
            urlopen(request, timeout=timeout) as response,  # nosec B310
        ):
            _https_url(response.geturl(), f"Den endelege adressa til {artifact.label}")
            while True:
                chunk = response.read(1024 * 1024)
                if not chunk:
                    break
                total += len(chunk)
                if total > artifact.maximum_size or total > artifact.size:
                    raise UpdateCheckError(f"{artifact.label} er større enn signert")
                digest.update(chunk)
                output.write(chunk)
    except Exception as exc:
        destination.unlink(missing_ok=True)
        if isinstance(exc, UpdateCheckError):
            raise
        raise UpdateCheckError(
            f"Klarte ikkje laste ned {artifact.filename}: {exc}"
        ) from exc
    if total != artifact.size:
        destination.unlink(missing_ok=True)
        raise UpdateCheckError(f"Storleiken på {artifact.label} stemmer ikkje")
    if digest.hexdigest() != artifact.sha256:
        destination.unlink(missing_ok=True)
        raise UpdateCheckError(f"SHA-256 for {artifact.label} stemmer ikkje")


def _safe_installer_environment(
    plan: UpdatePlan,
    settings: Settings,
    *,
    manifest_path: Path,
    package_path: Path,
    lock_path: Path,
) -> dict[str, str]:
    allowed = {
        "HOME",
        "HOMEDRIVE",
        "HOMEPATH",
        "LANG",
        "LC_ALL",
        "LC_CTYPE",
        "LOCALAPPDATA",
        "LOGNAME",
        "APPDATA",
        "ALLUSERSPROFILE",
        "COMSPEC",
        "PROGRAMDATA",
        "PROGRAMFILES",
        "PROGRAMFILES(X86)",
        "PROGRAMW6432",
        "SUDO_GID",
        "SUDO_UID",
        "SUDO_USER",
        "SYSTEMDRIVE",
        "SYSTEMROOT",
        "TEMP",
        "TMP",
        "TMPDIR",
        "USER",
        "USERNAME",
        "USERPROFILE",
        "WINDIR",
    }
    environment = {
        key: value
        for key, value in os.environ.items()
        if key.upper() in allowed or key.upper().startswith("LC_")
    }
    if plan.platform == "linux":
        environment["PATH"] = "/usr/sbin:/usr/bin:/sbin:/bin"
    elif plan.platform == "macos":
        environment["PATH"] = "/usr/bin:/bin:/usr/sbin:/sbin"
    else:
        environment["PATH"] = os.environ.get("PATH", "")
        environment["PATHEXT"] = ".COM;.EXE;.BAT;.CMD"
    environment.update(
        {
            "MESHPI_MANIFEST_FILE": str(manifest_path),
            "MESHPI_PACKAGE_FILE": str(package_path),
            "MESHPI_LOCK_FILE": str(lock_path),
            "MESHPI_MODE": settings.background_mode,
            "MESHPI_PYTHON": sys.executable,
        }
    )
    return environment


def _installer_command(plan: UpdatePlan, installer_path: Path, settings: Settings) -> list[str]:
    if plan.platform == "windows":
        return [
            windows_powershell_path(),
            "-NoProfile",
            "-ExecutionPolicy",
            "Bypass",
            "-File",
            str(installer_path),
            "-Mode",
            settings.background_mode.capitalize(),
        ]
    return ["/bin/sh", str(installer_path)]


def apply_update(
    settings: Settings,
    *,
    current_version: str = __version__,
    platform_name: str | None = None,
    expected_version: str | None = None,
    channel: str = "stable",
) -> str | None:
    platform = platform_key(platform_name)
    if platform == "linux" and hasattr(os, "geteuid"):
        is_root = os.geteuid() == 0
        if settings.background_mode == "always" and not is_root:
            raise UpdateCheckError(
                "Always-installasjonen må oppdaterast med «sudo meshpi update»"
            )
        if settings.background_mode == "session" and is_root:
            raise UpdateCheckError("Session-installasjonen skal oppdaterast utan sudo")
    if platform == "macos" and hasattr(os, "geteuid") and os.geteuid() == 0:
        raise UpdateCheckError("macOS-installasjonen skal oppdaterast utan sudo")
    plan = prepare_update(
        settings,
        current_version=current_version,
        platform_name=platform_name,
        channel=channel,
    )
    if plan is None:
        return None
    if expected_version is not None and plan.latest_version != expected_version:
        raise UpdateCheckError(
            "Den tilgjengelege versjonen endra seg; køyr oppdateringa på nytt"
        )
    with tempfile.TemporaryDirectory(
        prefix="meshpi-update-",
        ignore_cleanup_errors=platform == "windows",
    ) as temporary:
        root = Path(temporary)
        root.chmod(0o700)
        manifest_path = root / "version.json"
        installer_path = root / plan.installer.filename
        package_path = root / plan.package.filename
        lock_path = root / plan.lock.filename
        _private_write(manifest_path, plan.manifest_bytes)
        for artifact, destination in (
            (plan.installer, installer_path),
            (plan.package, package_path),
            (plan.lock, lock_path),
        ):
            _download_artifact(
                artifact,
                destination,
                timeout=max(30, settings.update_timeout),
            )
        command = _installer_command(plan, installer_path, settings)
        environment = _safe_installer_environment(
            plan,
            settings,
            manifest_path=manifest_path,
            package_path=package_path,
            lock_path=lock_path,
        )
        try:
            subprocess.run(  # nosec B603
                command,
                check=True,
                env=environment,
                cwd=root,
            )
        except subprocess.CalledProcessError as exc:
            manual_install = ""
            if plan.platform == "windows":
                beta_option = ""
                if plan.channel == "beta":
                    beta_base_url = BETA_UPDATE_URL.removesuffix("/version.json")
                    beta_option = f" -BaseUrl {beta_base_url}"
                manual_install = (
                    "\nPrøv den direkte Windows-installatøren i PowerShell:\n"
                    f"Invoke-WebRequest {plan.installer.url} "
                    "-OutFile install-windows.ps1; "
                    f".\\install-windows.ps1{beta_option}"
                )
            raise UpdateCheckError(
                f"Installatøren stoppa med status {exc.returncode}{manual_install}"
            ) from exc
    return plan.latest_version
