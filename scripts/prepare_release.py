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

ROOT = Path(__file__).resolve().parents[1]
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


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--skip-build", action="store_true")
    parser.add_argument("--published-at", help="ISO-8601; standard er no i UTC")
    parser.add_argument(
        "--signing-key",
        default=os.environ.get("MESHPI_SIGNING_KEY"),
        help="privat PEM-nøkkel; kan òg setjast med MESHPI_SIGNING_KEY",
    )
    args = parser.parse_args()

    project = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    version = project["project"]["version"]
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

    manifest_path = ROOT / "website" / "version.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["latest_version"] = version
    manifest["published_at"] = args.published_at or datetime.now(UTC).isoformat(
        timespec="seconds"
    ).replace("+00:00", "Z")
    manifest["release_notes_url"] = f"https://venes.org/meshpi/#release-{version}"
    manifest["package"] = {
        "url": f"https://venes.org/meshpi/downloads/{wheel.name}",
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
            "url": f"https://venes.org/meshpi/locks/{lock.name}",
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
        manifest["installers"][platform_name] = {
            "url": f"https://venes.org/meshpi/{filename}",
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
    signature = private_key.sign(
        canonical_manifest_bytes(manifest),
        padding.PKCS1v15(),
        hashes.SHA256(),
    )
    manifest["signature"] = {
        "algorithm": SIGNATURE_ALGORITHM,
        "key_id": SIGNING_KEY_ID,
        "value": base64.b64encode(signature).decode("ascii"),
    }
    manifest_path.write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    shutil.copy2(manifest_path, release_dir / "version.json")
    print(f"Bygde {wheel.name}")
    print(f"SHA-256 {manifest['package']['sha256']}")
    print(f"Oppdaterte {manifest_path}")


if __name__ == "__main__":
    main()
