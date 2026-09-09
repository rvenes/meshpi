"""Install a signed platform lock using a hash-verified pip wheel, not venv's pip.

Only the standard library is imported until the pinned pip wheel is verified.
This module is run from the already signature/hash-verified MeshPi wheel.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import runpy
import sys
import tempfile
from pathlib import Path
from urllib.parse import urlparse
from urllib.request import urlopen


def fetch(url: str, maximum: int) -> bytes:
    def validate(value: str) -> None:
        parsed = urlparse(value)
        if parsed.scheme != "https" or parsed.hostname not in {
            "pypi.org", "files.pythonhosted.org"
        }:
            raise ValueError("Untrusted bootstrap URL")
        if parsed.username is not None:
            raise ValueError("Invalid bootstrap URL")

    validate(url)
    with urlopen(url, timeout=30) as response:  # nosec B310
        validate(response.geturl())
        payload = response.read(maximum + 1)
    if len(payload) > maximum:
        raise ValueError("Bootstrap download exceeds size limit")
    return payload


def pip_requirement(lock: str) -> tuple[str, set[str]]:
    logical = lock.replace("\\\n", " ")
    matches = re.findall(r"(?m)^pip==([^\s]+)([^\n]*)$", logical)
    if len(matches) != 1:
        raise ValueError("Expected one pinned pip requirement")
    version, options = matches[0]
    if not re.fullmatch(r"\d+\.\d+(?:\.\d+)?", version):
        raise ValueError("Invalid pip version")
    if tuple(int(part) for part in version.split(".")) < (26, 2):
        raise ValueError("Unsafe bootstrap pip version")
    hashes = set(re.findall(r"--hash=sha256:([0-9a-f]{64})", options))
    if not hashes:
        raise ValueError("Missing signed pip hashes")
    return version, hashes


def main() -> None:
    lock = Path(sys.argv[1]).resolve()
    version, trusted_hashes = pip_requirement(lock.read_text(encoding="utf-8"))
    metadata = json.loads(fetch(f"https://pypi.org/pypi/pip/{version}/json", 1024 * 1024))
    filename = f"pip-{version}-py3-none-any.whl"
    candidates = [item for item in metadata["urls"] if item["filename"] == filename]
    if len(candidates) != 1:
        raise ValueError("Missing bootstrap wheel")
    payload = fetch(candidates[0]["url"], 10 * 1024 * 1024)
    if hashlib.sha256(payload).hexdigest() not in trusted_hashes:
        raise ValueError("Bootstrap wheel does not match the signed lock")
    with tempfile.TemporaryDirectory(prefix="meshpi-pip-") as temporary:
        wheel = Path(temporary) / filename
        wheel.write_bytes(payload)
        sys.path.insert(0, str(wheel))
        os.environ["PIP_CONFIG_FILE"] = os.devnull
        sys.argv = ["pip", "--isolated", "install", "--only-binary=:all:",
                    "--require-hashes", "--index-url", "https://pypi.org/simple", "-r", str(lock)]
        # Match `python -m pip`: this is Python, not a running pip.exe launcher.
        runpy.run_module("pip", run_name="__main__", alter_sys=True)


if __name__ == "__main__":
    main()
