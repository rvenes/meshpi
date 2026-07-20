from __future__ import annotations

import os
import platform
import subprocess
import sys
from pathlib import Path

from meshpi.config import Settings
from meshpi.lifecycle import daemon_status, start_session_daemon, stop_daemon


def _run(command: list[str], hint: str | None = None) -> None:
    result = subprocess.run(command, check=False)
    if result.returncode != 0:
        suffix = f" Prøv: {hint}" if hint else ""
        raise RuntimeError(f"Kommandoen feila: {' '.join(command)}.{suffix}")


def _system() -> str:
    return platform.system().lower()


def _linux_action(action: str) -> None:
    command = ["systemctl", action, "meshpi.service"]
    hint = f"sudo {' '.join(command)}" if os.geteuid() != 0 else None
    _run(command, hint)


def _macos_action(action: str) -> None:
    domain = f"gui/{os.getuid()}"
    label = "org.venes.meshpi"
    plist = Path.home() / "Library/LaunchAgents/org.venes.meshpi.plist"
    if action == "start":
        _run(["launchctl", "kickstart", f"{domain}/{label}"])
    elif action == "enable":
        _run(["launchctl", "bootstrap", domain, str(plist)])
    elif action == "disable":
        _run(["launchctl", "bootout", f"{domain}/{label}"])


def _windows_action(action: str) -> None:
    try:
        install_root = Path(sys.executable).parents[4]
    except IndexError as exc:
        raise RuntimeError("Fann ikkje MeshPi-installasjonsmappa") from exc
    manager = install_root / "bin" / "meshpi-service.ps1"
    if not manager.is_file():
        raise RuntimeError(f"Fann ikkje Windows-tenestestyringa: {manager}")
    _run(
        [
            "powershell.exe",
            "-NoProfile",
            "-ExecutionPolicy",
            "Bypass",
            "-File",
            str(manager),
            action,
        ]
    )


def manage_service(
    action: str,
    settings: Settings,
    env_file: str | Path,
) -> dict:
    if action == "status":
        status = daemon_status(settings)
        return status or {
            "state": "stoppa",
            "background_mode": settings.background_mode,
        }
    if action == "stop":
        stopped = stop_daemon(settings)
        return {"state": "stoppa", "changed": stopped}
    if action == "start" and settings.background_mode == "session":
        handle = start_session_daemon(settings, env_file, follow_parent=False)
        return {
            "state": "starta",
            "changed": handle.owned,
            "daemon_pid": handle.process.pid if handle.process else None,
        }

    system = _system()
    if system == "linux":
        _linux_action(action)
    elif system == "darwin":
        _macos_action(action)
    elif system == "windows":
        _windows_action(action)
    else:
        raise RuntimeError(f"Ustøtta operativsystem: {system}")
    return {"state": action, "changed": True}
