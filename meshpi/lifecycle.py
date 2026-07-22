from __future__ import annotations

import os
import subprocess  # nosec B404
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
    if path.is_file() and path.stat().st_size >= 5 * 1024 * 1024:
        for number in range(3, 0, -1):
            source = path.with_name(f"{path.name}.{number}")
            target = path.with_name(f"{path.name}.{number + 1}")
            if number == 3:
                target.unlink(missing_ok=True)
            if source.exists():
                source.replace(target)
        path.replace(path.with_name(f"{path.name}.1"))
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

    env_path = Path(env_file).expanduser().resolve()
    command = [
        sys.executable,
        "-I",
        "-m",
        "meshpi",
        "--env-file",
        str(env_path),
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
    child_env = {
        key: value
        for key, value in os.environ.items()
        if not key.upper().startswith("PYTHON")
    }
    child_env["PYTHONDONTWRITEBYTECODE"] = "1"
    working_directory = settings.database_path.parent.resolve()
    working_directory.mkdir(parents=True, exist_ok=True)
    with log_path.open("ab") as log:
        process = subprocess.Popen(  # nosec B603
            command,
            stdin=subprocess.DEVNULL,
            stdout=log,
            stderr=subprocess.STDOUT,
            creationflags=creationflags,
            start_new_session=os.name != "nt" and not follow_parent,
            cwd=working_directory,
            env=child_env,
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


def wait_for_daemon(settings: Settings, timeout: float = 15) -> dict:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        status = daemon_status(settings)
        if status is not None:
            return status
        time.sleep(0.15)
    raise RuntimeError("Bakgrunnstenesta svarte ikkje etter start")


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
