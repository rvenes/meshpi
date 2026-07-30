"""Bygg ei MeshPi-utgiving og oppdater versjonsmanifestet.

Køyr frå prosjektre rota:
    python scripts/prepare_release.py
"""

from __future__ import annotations

import argparse
import base64
import hashlib
import json
import os
import shutil
import subprocess
import sys
import tomllib
from datetime import UTC, datetime
from pathlib import Path

from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding

from meshpi.signing import SignatureError, verify_manifest_signature
from meshpi.versions import VersionError, is_prerelease, version_key

ROOT = Path(__file__).resolve().parents[1]
BASE_URL = "https://venes.org/meshpi"
LOCKS = ("linux", "macos", "windows")
INSTALLERS = {
    "linux": (
        "install-linux.sh",
        "curl -fLO https://venes.org/meshpi/install-linux.sh && sudo sh install-linux.sh",
    ),
    "macos": (
        "install-macos.sh",
        "curl -fLO https://venes.org/meshpi/install-macos.sh && sh install-macos.sh",
    ),
    "windows": (
        "install-windows.ps1",
        "Invoke-WebRequest https://venes.org/meshpi/install-windows.ps1 "
        "-OutFile install-windows.ps1; .\\install-windows.ps1",
    ),
}
SIGNATURE_ALGORITHM = "rsa-pkcs1v15-sha256"
SIGNING_KEY_ID = "meshpi-release-2026-01"


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def canonical_manifest_bytes(manifest: dict) -> bytes:
    unsigned = dict(manifest)
    unsigned.pop("signature", None)
    return json.dumps(
        unsigned,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")


def sign_manifest(manifest: dict, private_key, key_id: str = SIGNING_KEY_ID) -> None:
    signature = private_key.sign(
        canonical_manifest_bytes(manifest),
        padding.PKCS1v15(),
        hashes.SHA256(),
    )
    manifest["signature"] = {
        "algorithm": SIGNATURE_ALGORITHM,
        "key_id": key_id,
        "value": base64.b64encode(signature).decode("ascii"),
    }
    try:
        verify_manifest_signature(manifest)
    except SignatureError as exc:
        raise SystemExit(
            "Den valde private nøkkelen samsvarar ikkje med key_id "
            f"«{key_id}» i det tiltrudde nøkkelregisteret"
        ) from exc


def validate_release_request(
    version: str,
    channel: str,
    release_notes: list[str] | None,
) -> None:
    try:
        version_key(version)
        prerelease = is_prerelease(version)
    except VersionError as exc:
        raise SystemExit(str(exc)) from exc
    if channel == "stable" and prerelease:
        raise SystemExit("Stable-kanalen kan ikkje byggje ei førehandsutgåve")
    if channel == "beta" and not prerelease:
        raise SystemExit("Beta-kanalen krev ein versjon som 0.9.0b1")
    if channel == "beta" and not release_notes:
        raise SystemExit("Beta-kanalen krev minst eitt --release-note")


def beta_seed_manifest(stable_manifest: dict, private_key) -> dict:
    """Lag eit signert, trygt betamanifest når ingen førehandsutgåve finst."""
    manifest = json.loads(json.dumps(stable_manifest))
    manifest.pop("signature", None)
    manifest["channel"] = "beta"
    manifest["release_notes_url"] = f"{BASE_URL}/beta/"
    for platform_name, installer in manifest["installers"].items():
        installer["update_command"] = (
            "sudo meshpi update --beta"
            if platform_name == "linux"
            else "meshpi update --beta"
        )
    sign_manifest(manifest, private_key)
    return manifest


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--skip-build", action="store_true")
    parser.add_argument("--published-at", help="ISO-8601; standard er no i UTC")
    parser.add_argument(
        "--channel",
        choices=("stable", "beta"),
        default="stable",
        help="oppdateringskanal; standard er stable",
    )
    parser.add_argument(
        "--release-note",
        action="append",
        dest="release_notes",
        help="utgåvenotat; kan gjentakast og er påkravd for beta",
    )
    parser.add_argument(
        "--seed-beta",
        action="store_true",
        help="lag eit betamanifest som peikar på den stabile utgåva",
    )
    parser.add_argument(
        "--signing-key",
        default=os.environ.get("MESHPI_SIGNING_KEY"),
        help="privat PEM-nøkkel; kan òg setjast med MESHPI_SIGNING_KEY",
    )
    args = parser.parse_args()

    project = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    version = project["project"]["version"]
    validate_release_request(version, args.channel, args.release_notes)
    if args.seed_beta and args.channel != "stable":
        raise SystemExit("--seed-beta kan berre brukast med stable-kanalen")

    source_version = (
        ROOT / "meshpi" / "__init__.py"
    ).read_text(encoding="utf-8")
    if f'__version__ = "{version}"' not in source_version:
        raise SystemExit("Versjonen i meshpi/__init__.py samsvarar ikkje med pyproject.toml")

    release_dir = ROOT / "build" / f"release-{version}"
    release_dir.mkdir(parents=True, exist_ok=True)
    wheel = release_dir / f"meshpi-{version}-py3-none-any.whl"

    if not args.skip_build:
        subprocess.run(
            [
                sys.executable,
                "-m",
                "build",
                "--wheel",
                "--outdir",
                str(release_dir),
            ],
            cwd=ROOT,
            check=True,
        )
    if not wheel.is_file():
        raise SystemExit(f"Fann ikkje {wheel}")

    channel_base_url = BASE_URL if args.channel == "stable" else f"{BASE_URL}/beta"
    manifest_path = (
        ROOT / "website" / "version.json"
        if args.channel == "stable"
        else ROOT / "website" / "beta" / "version.json"
    )
    template_path = (
        manifest_path
        if manifest_path.is_file()
        else ROOT / "website" / "version.json"
    )
    manifest = json.loads(template_path.read_text(encoding="utf-8"))
    manifest.pop("signature", None)
    manifest["channel"] = args.channel
    manifest["latest_version"] = version
    manifest["published_at"] = args.published_at or datetime.now(UTC).isoformat(
        timespec="seconds"
    ).replace("+00:00", "Z")
    manifest["release_notes_url"] = (
        f"{BASE_URL}/#release-{version}"
        if args.channel == "stable"
        else f"{BASE_URL}/beta/"
    )
    if args.release_notes:
        manifest["release_notes"] = args.release_notes
    manifest["package"] = {
        "url": f"{channel_base_url}/downloads/{wheel.name}",
        "filename": wheel.name,
        "sha256": sha256(wheel),
        "size": wheel.stat().st_size,
        "format": "wheel",
    }
    manifest["locks"] = {}
    for platform_name in LOCKS:
        lock = ROOT / "locks" / f"{platform_name}.txt"
        if not lock.is_file():
            raise SystemExit(f"Fann ikkje {lock}")
        manifest["locks"][platform_name] = {
            "url": f"{channel_base_url}/locks/{lock.name}",
            "sha256": sha256(lock),
            "size": lock.stat().st_size,
        }
    manifest["security"] = {
        "integrity": "sha256",
        "dependency_policy": "pip-require-hashes",
        "manifest_signature": SIGNATURE_ALGORITHM,
    }
    manifest["installers"] = {}
    for platform_name, (filename, update_command) in INSTALLERS.items():
        installer = ROOT / "installers" / filename
        if args.channel == "beta":
            update_command = (
                "sudo meshpi update --beta"
                if platform_name == "linux"
                else "meshpi update --beta"
            )
        manifest["installers"][platform_name] = {
            "url": f"{channel_base_url}/{filename}",
            "sha256": sha256(installer),
            "size": installer.stat().st_size,
            "update_command": update_command,
        }
    if not args.signing_key:
        raise SystemExit("Oppgi --signing-key eller MESHPI_SIGNING_KEY")
    private_key = serialization.load_pem_private_key(
        Path(args.signing_key).read_bytes(),
        password=None,
    )
    sign_manifest(manifest, private_key)
    manifest_path.parent.mkdir(parents=True, exist_ok=True)
    manifest_path.write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    shutil.copy2(manifest_path, release_dir / "version.json")
    if args.seed_beta:
        beta_manifest = beta_seed_manifest(manifest, private_key)
        beta_manifest_path = ROOT / "website" / "beta" / "version.json"
        beta_manifest_path.parent.mkdir(parents=True, exist_ok=True)
        beta_manifest_path.write_text(
            json.dumps(beta_manifest, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        shutil.copy2(beta_manifest_path, release_dir / "beta-version.json")
    print(f"Bygde {wheel.name}")
    print(f"SHA-256 {manifest['package']['sha256']}")
    print(f"Oppdaterte {manifest_path}")


if __name__ == "__main__":
    main()
