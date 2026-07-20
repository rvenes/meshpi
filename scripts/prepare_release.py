"""Bygg ei MeshPi-utgiving og oppdater versjonsmanifestet.

Køyr frå prosjektre rota:
    python scripts/prepare_release.py
"""

from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import subprocess
import sys
import tomllib
from datetime import UTC, datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
LOCKS = ("linux", "macos", "windows")


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--skip-build", action="store_true")
    parser.add_argument("--published-at", help="ISO-8601; standard er no i UTC")
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
        "manifest_signature": None,
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
