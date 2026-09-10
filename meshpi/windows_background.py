"""Windowless entry point for the per-user Windows startup shortcut."""

from __future__ import annotations

import subprocess  # nosec B404
import sys

from meshpi.platform_service import windows_powershell_path


def main() -> None:
    subprocess.Popen(  # nosec B603
        [
            windows_powershell_path(),
            "-NoProfile",
            "-WindowStyle",
            "Hidden",
            "-ExecutionPolicy",
            "Bypass",
            "-File",
            sys.argv[1],
        ],
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        creationflags=subprocess.CREATE_NO_WINDOW,
        close_fds=True,
    )


if __name__ == "__main__":
    main()
