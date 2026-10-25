from __future__ import annotations

import os
import platform
import subprocess  # nosec B404
import sys
import time
from pathlib import Path

from meshpi.config import Settings
from meshpi.i18n import tr
from meshpi.lifecycle import daemon_status, start_session_daemon, stop_daemon

LAUNCHCTL = "/bin/launchctl"
SYSTEMCTL_PATHS = (Path("/usr/bin/systemctl"), Path("/bin/systemctl"))
LINUX_WEB_UNIT = Path("/etc/systemd/system/meshpi-web.service")


def _run(command: list[str], hint: str | None = None) -> None:
    result = subprocess.run(command, check=False)  # nosec B603
    if result.returncode != 0:
        suffix = tr("service.command_hint", hint=hint) if hint else ""
        raise RuntimeError(tr("service.command_failed", command=" ".join(command), suffix=suffix))


def _system() -> str:
    return platform.system().lower()


def _systemctl_path() -> str:
    for path in SYSTEMCTL_PATHS:
        if path.is_file():
            return str(path)
    raise RuntimeError(tr("service.systemctl_missing"))


def windows_powershell_path() -> str:
    if os.name != "nt":
        raise RuntimeError(tr("service.powershell_windows_only"))
    import ctypes

    windows_directory = ctypes.create_unicode_buffer(32768)
    if not ctypes.windll.kernel32.GetSystemWindowsDirectoryW(
        windows_directory,
        len(windows_directory),
    ):
        raise RuntimeError(tr("service.windows_directory_missing"))
    powershell = (
        Path(windows_directory.value) / "System32" / "WindowsPowerShell" / "v1.0" / "powershell.exe"
    )
    if not powershell.is_file():
        raise RuntimeError(tr("service.powershell_missing", path=powershell))
    return str(powershell)


def _linux_action(action: str, unit: str = "meshpi.service") -> None:
    command = [_systemctl_path(), action, unit]
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


def _macos_action(action: str, *, web: bool = False) -> bool:
    domain = f"gui/{os.getuid()}"
    label = "org.venes.meshpi.web" if web else "org.venes.meshpi"
    plist = Path.home() / "Library/LaunchAgents" / f"{label}.plist"
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
            command = [LAUNCHCTL, "bootout", f"{domain}/{label}"]
            _run(command)
            # bootout can return while the old job is still exiting.
            for _ in range(50):
                if not _macos_job_loaded(domain, label):
                    return True
                time.sleep(0.1)
            raise RuntimeError(tr("service.command_failed", command=" ".join(command), suffix=""))
        else:
            return False
    return True


def _windows_action(action: str) -> bool:
    try:
        install_root = Path(sys.executable).parents[4]
    except IndexError as exc:
        raise RuntimeError(tr("service.install_root_missing")) from exc
    manager = install_root / "bin" / "meshpi-service.ps1"
    if not manager.is_file():
        if action == "stop":
            return False
        raise RuntimeError(tr("service.manager_missing", path=manager))
    # Old installations have no supervised web process and support only IPC stop.
    if action == "stop" and "# MeshPi managed stop v1" not in manager.read_text(
        encoding="utf-8-sig"
    ):
        return False
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
    return True


def _web_service_installed(system: str) -> bool:
    if system == "linux":
        return LINUX_WEB_UNIT.is_file()
    if system == "darwin":
        return (Path.home() / "Library/LaunchAgents/org.venes.meshpi.web.plist").is_file()
    return False


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
        changed = False
        if settings.background_mode == "always":
            system = _system()
            if system == "linux" and _web_service_installed(system):
                _linux_action("stop", "meshpi-web.service")
                _linux_action("stop")
                return {"state": "stoppa", "changed": True}
            if system == "darwin":
                if _web_service_installed(system):
                    changed = _macos_action("stop", web=True)
                if _macos_action("stop"):
                    return {"state": "stoppa", "changed": True}
            if system == "windows":
                # Let SQLite and the radio close normally before stopping the supervisor.
                stopped = stop_daemon(settings)
                managed = _windows_action("stop")
                return {"state": "stoppa", "changed": stopped or managed}
        stopped = stop_daemon(settings)
        return {"state": "stoppa", "changed": stopped or changed}
    if action == "start" and settings.background_mode == "session":
        handle = start_session_daemon(settings, env_file, follow_parent=False)
        return {
            "state": "starta",
            "changed": handle.owned,
            "daemon_pid": handle.process.pid if handle.process else None,
        }

    system = _system()
    control_web = (
        settings.background_mode == "always"
        and _web_service_installed(system)
        and (settings.web_enabled or action == "disable")
    )
    if system == "linux":
        if control_web and action == "disable":
            _linux_action(action, "meshpi-web.service")
        _linux_action(action)
        if control_web and action != "disable":
            _linux_action(action, "meshpi-web.service")
    elif system == "darwin":
        if control_web and action == "disable":
            _macos_action(action, web=True)
        _macos_action(action)
        if control_web and action != "disable":
            _macos_action(action, web=True)
    elif system == "windows":
        _windows_action(action)
    else:
        raise RuntimeError(tr("service.unsupported_system", system=system))
    return {"state": action, "changed": True}
