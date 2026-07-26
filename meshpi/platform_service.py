from __future__ import annotations

import os
import platform
import subprocess  # nosec B404
import sys
from pathlib import Path

from meshpi.config import Settings
from meshpi.lifecycle import daemon_status, start_session_daemon, stop_daemon

LAUNCHCTL = "/bin/launchctl"
SYSTEMCTL_PATHS = (Path("/usr/bin/systemctl"), Path("/bin/systemctl"))


def _run(command: list[str], hint: str | None = None) -> None:
    result = subprocess.run(command, check=False)  # nosec B603
    if result.returncode != 0:
        suffix = f" Prøv: {hint}" if hint else ""
        raise RuntimeError(f"Kommandoen feila: {' '.join(command)}.{suffix}")


def _system() -> str:
    return platform.system().lower()


def _systemctl_path() -> str:
    for path in SYSTEMCTL_PATHS:
        if path.is_file():
            return str(path)
    raise RuntimeError("Fann ikkje systemctl på ein godkjend systemsti")


def windows_powershell_path() -> str:
    if os.name != "nt":
        raise RuntimeError("Windows PowerShell er berre tilgjengeleg på Windows")
    import ctypes

    windows_directory = ctypes.create_unicode_buffer(32768)
    if not ctypes.windll.kernel32.GetSystemWindowsDirectoryW(
        windows_directory,
        len(windows_directory),
    ):
        raise RuntimeError("Fann ikkje den godkjende Windows-systemmappa")
    powershell = (
        Path(windows_directory.value)
        / "System32"
        / "WindowsPowerShell"
        / "v1.0"
        / "powershell.exe"
    )
    if not powershell.is_file():
        raise RuntimeError(f"Fann ikkje Windows PowerShell: {powershell}")
    return str(powershell)


def _linux_action(action: str) -> None:
    command = [_systemctl_path(), action, "meshpi.service"]
    hint = f"sudo {' '.join(command)}" if os.geteuid() != 0 else None
    _run(command, hint)


def _macos_job_loaded(domain: str, label: str) -> bool:
    result = subprocess.run(  # nosec B603
        [LAUNCHCTL, "print", f"{domain}/{label}"],
        check=False,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    return result.returncode == 0


def _macos_action(action: str) -> bool:
    domain = f"gui/{os.getuid()}"
    label = "org.venes.meshpi"
    plist = Path.home() / "Library/LaunchAgents/org.venes.meshpi.plist"
    if action == "start":
        if _macos_job_loaded(domain, label):
            _run([LAUNCHCTL, "kickstart", f"{domain}/{label}"])
        else:
            _run([LAUNCHCTL, "bootstrap", domain, str(plist)])
    elif action == "enable":
        if not _macos_job_loaded(domain, label):
            _run([LAUNCHCTL, "bootstrap", domain, str(plist)])
        else:
            return False
    elif action in {"stop", "disable"}:
        if _macos_job_loaded(domain, label):
            _run([LAUNCHCTL, "bootout", f"{domain}/{label}"])
        else:
            return False
    return True


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
            windows_powershell_path(),
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
        if (
            settings.background_mode == "always"
            and _system() == "darwin"
            and _macos_action("stop")
        ):
            return {"state": "stoppa", "changed": True}
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
