"""Local configuration and health checks; never configure networking implicitly."""
from __future__ import annotations

import json
import os
import re
import shutil
import stat
import subprocess  # nosec B404
import tempfile
from dataclasses import replace
from http.client import HTTPConnection
from pathlib import Path

from meshpi.config import Settings, validate_web_config


def tailscale_address() -> tuple[str, str]:
    """Read only this machine's DNS name; never return peer or login data."""
    executable = shutil.which("tailscale")
    if not executable:
        return "", "missing"
    try:
        result = subprocess.run(  # nosec B603
            [executable, "status", "--json"], capture_output=True, text=True,
            encoding="utf-8", timeout=3, check=False,
        )
        if result.returncode:
            return "", "unavailable"
        data = json.loads(result.stdout)
        if not isinstance(data, dict):
            return "", "unavailable"
        if data.get("BackendState") != "Running":
            return "", "inactive"
        own = data.get("Self")
        name = own.get("DNSName") if isinstance(own, dict) else None
        if not isinstance(name, str):
            return "", "unavailable"
        name = name.removesuffix(".").lower()
        if (len(name) > 253 or not name.endswith(".ts.net")
                or not all(re.fullmatch(r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?", label)
                           for label in name.split("."))):
            return "", "unavailable"
        return f"https://{name}", "running"
    except (OSError, ValueError, subprocess.TimeoutExpired):
        return "", "unavailable"


def web_info(settings: Settings) -> dict:
    origin, state = tailscale_address()
    suffix = settings.web_base_path + "/"
    return {
        "enabled": settings.web_enabled,
        "responding_locally": check_web(settings),
        "login_url": settings.web_origin + suffix,
        "local_url": f"http://localhost:{settings.web_port}{suffix}",
        "public_url": settings.web_public_url + suffix if settings.web_public_url else "",
        "tailscale_url": origin + suffix if origin else "",
        "tailscale_state": state,
        "passkeys_enabled": bool(settings.web_public_url),
    }


def web_settings(settings: Settings, *, port=None, public_url=None, base_path=None) -> Settings:
    port = settings.web_port if port is None else port
    base_path = settings.web_base_path if base_path is None else base_path
    public_url = settings.web_public_url if public_url is None else public_url
    public_url = validate_web_config(port, public_url, base_path)
    return replace(settings, web_port=port, web_public_url=public_url, web_base_path=base_path)


def configure_web(
    env_file: str | Path, settings: Settings, *, enabled: bool | None = None,
    port: int | None = None, public_url: str | None = None, base_path: str | None = None,
) -> Settings:
    updated = web_settings(settings, port=port, public_url=public_url, base_path=base_path)
    if enabled is not None:
        updated = replace(updated, web_enabled=enabled)
    path = Path(env_file).expanduser().absolute()
    if path.is_symlink():
        raise ValueError("Konfigurasjonsfila kan ikkje vere ei symbolsk lenkje.")
    info = path.stat() if path.exists() else None
    if info is not None and not stat.S_ISREG(info.st_mode):
        raise ValueError("Konfigurasjonen må vere ei vanleg fil.")
    values = {
        "WEB_ENABLED": str(updated.web_enabled).lower(),
        "WEB_PORT": str(updated.web_port),
        "WEB_PUBLIC_URL": updated.web_public_url,
        "WEB_BASE_PATH": updated.web_base_path,
    }
    original = path.read_text(encoding="utf-8") if info else ""
    lines = [line for line in original.splitlines()
             if line.split("=", 1)[0].strip() not in values]
    lines.extend(f"{key}={value}" for key, value in values.items())
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = None
    try:
        fd, name = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
        temporary = Path(name)
        with os.fdopen(fd, "w", encoding="utf-8") as output:
            output.write("\n".join(lines) + "\n")
            output.flush()
            if info and os.name == "posix":
                os.fchown(output.fileno(), info.st_uid, info.st_gid)
                os.fchmod(output.fileno(), stat.S_IMODE(info.st_mode))
            os.fsync(output.fileno())
        os.replace(temporary, path)
    except PermissionError as exc:
        raise RuntimeError(
            "Manglar rettar til konfigurasjonsmappa. "
            "Bruk administratorrettar for systeminstallasjonen."
        ) from exc
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)
    return updated


def check_web(settings: Settings) -> bool:
    connection = HTTPConnection("127.0.0.1", settings.web_port, timeout=3)
    try:
        connection.request("GET", settings.web_base_path + "/api/health")
        response = connection.getresponse()
        data = json.loads(response.read(1024))
        return (response.status == 200 and isinstance(data, dict)
                and data.get("service") == "meshpi-web")
    except (OSError, ValueError):
        return False
    finally:
        connection.close()


def setup_guidance(
    settings: Settings, env_file: str | Path, info: dict, *, executable: str, platform: str,
) -> str:
    """Render manual setup commands for this installation; execute nothing."""
    import shlex

    from meshpi.i18n import tr

    windows = platform == "win32"
    system_linux = platform.startswith("linux") and settings.background_mode == "always"

    def command(parts: list[str], *, admin: bool = False) -> str:
        if windows:
            return "& " + " ".join("'" + part.replace("'", "''") + "'" for part in parts)
        return shlex.join((["sudo"] if admin else []) + parts)

    prefix = [executable, "-m", "meshpi", "--env-file",
              str(Path(env_file).expanduser().absolute())]

    def meshpi(*parts: str, admin: bool = False) -> str:
        return command(prefix + list(parts), admin=admin and system_linux)

    detected = info["tailscale_url"]
    saved = info["public_url"]
    if saved and detected and saved != detected:
        return tr("web.setup_mismatch")
    origin = settings.web_public_url
    if not origin and detected:
        origin = detected.removesuffix(settings.web_base_path + "/")
    lines = [tr("web.setup_heading" if origin else "web.setup_local_heading")]
    if origin:
        lines += [tr("web.setup_serve_separate", path=settings.web_base_path or "/"),
                  "  tailscale serve status", ""]
    options = ["web-configure", "--enable"]
    if origin:
        options += ["--public-url", origin, "--base-path", settings.web_base_path]
    lines += [tr("web.setup_enable"), "  " + meshpi(*options, admin=True), ""]
    if settings.background_mode == "session":
        lines += [tr("web.setup_session"), "  " + meshpi("web"), ""]
    else:
        lines += [tr("web.setup_restart")]
        lines += ["  " + meshpi("service", action, admin=True)
                  for action in ("enable", "stop", "start")]
        lines += [""]
    lines += [tr("web.setup_check"), "  " + meshpi("web-check"), ""]
    if origin:
        serve = command(["tailscale", "serve", "--bg", "--set-path",
                         settings.web_base_path or "/", f"http://127.0.0.1:{settings.web_port}"],
                        admin=not windows)
        lines += [tr("web.setup_serve_start"), "  " + serve, "  tailscale serve status", "",
                  tr("web.setup_serve_persistent"),
                  tr("web.setup_open", url=origin + settings.web_base_path + "/")]
    else:
        lines += [tr("web.setup_tailscale_first")]
    lines += ["", tr("web.setup_refresh"), "  " + meshpi("web-info")]
    return "\n".join(lines)
