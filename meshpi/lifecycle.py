from __future__ import annotations

import os
import subprocess
import sys
import time
from dataclasses import dataclass
from pathlib import Path

from meshpi.client import CLIError, request
from meshpi.config import Settings


@dataclass(slots=True)
class DaemonHandle:
    process: subprocess.Popen[bytes] | None = None
    owned: bool = False


def daemon_status(settings: Settings, timeout: float = 0.5) -> dict | None:
    try:
        return request(settings, {"command": "status"}, timeout=timeout)["data"]
    except CLIError:
        return None


def _session_log(settings: Settings) -> Path:
    path = settings.database_path.parent / "meshpi-session.log"
    path.parent.mkdir(parents=True, exist_ok=True)
    return path


def start_session_daemon(
    settings: Settings,
    env_file: str | Path,
    *,
    follow_parent: bool = True,
    timeout: float = 15,
) -> DaemonHandle:
    if daemon_status(settings) is not None:
        return DaemonHandle()

    command = [
        sys.executable,
        "-m",
        "meshpi",
        "--env-file",
        str(env_file),
        "daemon",
    ]
    if follow_parent:
        command.extend(["--parent-pid", str(os.getpid())])
    creationflags = 0
    if os.name == "nt":
        creationflags = (
            subprocess.CREATE_NEW_PROCESS_GROUP  # type: ignore[attr-defined]
            | subprocess.CREATE_NO_WINDOW  # type: ignore[attr-defined]
        )
    log_path = _session_log(settings)
    with log_path.open("ab") as log:
        process = subprocess.Popen(
            command,
            stdin=subprocess.DEVNULL,
            stdout=log,
            stderr=subprocess.STDOUT,
            creationflags=creationflags,
            start_new_session=os.name != "nt" and not follow_parent,
        )

    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if process.poll() is not None:
            raise RuntimeError(
                f"Session-daemonen stoppa under oppstart. Sjå {log_path}"
            )
        if daemon_status(settings) is not None:
            return DaemonHandle(process=process, owned=True)
        time.sleep(0.15)
    process.terminate()
    raise RuntimeError(f"Session-daemonen svarte ikkje. Sjå {log_path}")


def stop_daemon(settings: Settings, timeout: float = 10) -> bool:
    if daemon_status(settings) is None:
        return False
    request(settings, {"command": "shutdown"}, timeout=2)
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if daemon_status(settings, timeout=0.2) is None:
            return True
        time.sleep(0.1)
    raise RuntimeError("Daemonen stoppa ikkje innan fristen")
