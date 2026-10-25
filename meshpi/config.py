from __future__ import annotations

import os
import re
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlsplit

from meshpi.i18n import tr

SETTING_ENV_KEYS = frozenset(
    {
        "MESHTASTIC_HOST",
        "MESHTASTIC_PORT",
        "DATABASE_PATH",
        "CONNECTIONS_PATH",
        "DISCOVERY_SUBNET",
        "IPC_HOST",
        "IPC_PORT",
        "IPC_SOCKET_GID",
        "IPC_SOCKET_PATH",
        "IPC_TOKEN",
        "IPC_TRANSPORT",
        "LOG_LEVEL",
        "LOG_FILE",
        "LOG_MAX_BYTES",
        "LOG_BACKUP_COUNT",
        "OBSERVATION_RETENTION_DAYS",
        "UPDATE_URL",
        "UPDATE_TIMEOUT",
        "BACKGROUND_MODE",
        "WEB_ENABLED", "WEB_PORT", "WEB_PUBLIC_URL", "WEB_BASE_PATH",
    }
)


def validate_web_config(port: int, public_url: str, base_path: str) -> str:
    if type(port) is not int or not 1 <= port <= 65535:
        raise ValueError("Webporten må vere mellom 1 og 65535.")
    if not re.fullmatch(r"(?:/[a-zA-Z0-9_-]+)*", base_path):
        raise ValueError("Ugyldig websti.")
    if not public_url:
        return ""
    parsed = urlsplit(public_url)
    if (parsed.scheme != "https" or not parsed.hostname or parsed.username
            or parsed.password or parsed.path not in {"", "/"} or parsed.query
            or parsed.fragment or parsed.port not in {None, 443}
            or not re.fullmatch(r"[a-z0-9](?:[a-z0-9.-]*[a-z0-9])?", parsed.hostname)
            or not parsed.hostname.endswith(".ts.net")):
        raise ValueError("Bruk den faste HTTPS-rotadressa frå Tailscale (*.ts.net).")
    return f"https://{parsed.hostname}"


def _load_env_file(path: Path) -> dict[str, str]:
    """Les berre kjende MeshPi-verdiar frå ei enkel KEY=VALUE-fil."""
    values: dict[str, str] = {}
    if not path.is_file():
        return values
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip().strip("\"'")
        if key in SETTING_ENV_KEYS:
            values[key] = value
    return values


def _env_int(
    values: dict[str, str], name: str, default: int, minimum: int, maximum: int
) -> int:
    raw = values.get(name, str(default))
    try:
        value = int(raw)
    except ValueError as exc:
        raise ValueError(tr("config.integer", name=name)) from exc
    if not minimum <= value <= maximum:
        raise ValueError(tr("config.range", name=name, minimum=minimum, maximum=maximum))
    return value


def _env_float(
    values: dict[str, str], name: str, default: float, minimum: float, maximum: float
) -> float:
    raw = values.get(name, str(default))
    try:
        value = float(raw)
    except ValueError as exc:
        raise ValueError(tr("config.number", name=name)) from exc
    if not minimum <= value <= maximum:
        raise ValueError(tr("config.range", name=name, minimum=minimum, maximum=maximum))
    return value


@dataclass(frozen=True, slots=True)
class Settings:
    meshtastic_host: str = ""
    meshtastic_port: int = 4403
    database_path: Path = Path("./data/meshtastic.db")
    connections_path: Path = Path("./data/connections.json")
    discovery_subnet: str = ""
    ipc_host: str = "127.0.0.1"
    ipc_port: int = 8765
    ipc_transport: str = "auto"
    ipc_socket_path: Path = Path("./data/meshpi.sock")
    ipc_socket_gid: int | None = None
    ipc_token: str = ""
    log_level: str = "INFO"
    log_file: Path | None = None
    log_max_bytes: int = 5 * 1024 * 1024
    log_backup_count: int = 3
    observation_retention_days: int = 365
    update_url: str = "https://venes.org/meshpi/version.json"
    update_timeout: float = 3.0
    background_mode: str = "always"
    web_enabled: bool = False
    web_port: int = 8780
    web_public_url: str = ""
    web_base_path: str = "/meshpi"

    @property
    def web_auth_dir(self) -> Path:
        return self.database_path.parent / "web-auth"

    @property
    def web_origin(self) -> str:
        return self.web_public_url or f"http://localhost:{self.web_port}"

    @property
    def ipc_uses_unix(self) -> bool:
        return self.ipc_transport == "unix" or (
            self.ipc_transport == "auto" and os.name != "nt"
        )

    @classmethod
    def load(cls, env_file: str | Path = ".env") -> Settings:
        values = _load_env_file(Path(env_file))
        for key in SETTING_ENV_KEYS:
            if key in os.environ:
                values[key] = os.environ[key]
        host = values.get("MESHTASTIC_HOST", "").strip()
        ipc_host = values.get("IPC_HOST", "127.0.0.1").strip()
        if ipc_host not in {"127.0.0.1", "::1", "localhost"}:
            raise ValueError(tr("config.ipc_host"))
        ipc_transport = values.get("IPC_TRANSPORT", "auto").strip().lower()
        if ipc_transport not in {"auto", "tcp", "unix"}:
            raise ValueError(tr("config.ipc_transport"))
        if ipc_transport == "unix" and os.name == "nt":
            raise ValueError(tr("config.unix_windows"))
        level = values.get("LOG_LEVEL", "INFO").strip().upper()
        if level not in {"DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"}:
            raise ValueError(tr("config.log_level"))
        background_mode = values.get("BACKGROUND_MODE", "always").strip().lower()
        if background_mode not in {"always", "session"}:
            raise ValueError(tr("config.background_mode"))
        web_enabled = values.get("WEB_ENABLED", "false").strip().lower()
        if web_enabled not in {"true", "false"}:
            raise ValueError("WEB_ENABLED må vere true eller false.")
        web_port = _env_int(values, "WEB_PORT", 8780, 1, 65535)
        web_base_path = values.get("WEB_BASE_PATH", "/meshpi").strip()
        web_public_url = validate_web_config(
            web_port, values.get("WEB_PUBLIC_URL", "").strip(), web_base_path,
        )
        database_path = Path(
            values.get("DATABASE_PATH", "./data/meshtastic.db")
        ).expanduser()
        raw_log_file = values.get("LOG_FILE", "").strip()
        ipc_socket_gid = None
        raw_socket_gid = values.get("IPC_SOCKET_GID", "").strip()
        if raw_socket_gid:
            ipc_socket_gid = _env_int(
                values,
                "IPC_SOCKET_GID",
                0,
                0,
                2_147_483_647,
            )
        return cls(
            meshtastic_host=host,
            meshtastic_port=_env_int(values, "MESHTASTIC_PORT", 4403, 1, 65535),
            database_path=database_path,
            connections_path=Path(
                values.get(
                    "CONNECTIONS_PATH",
                    str(database_path.with_name("connections.json")),
                )
            ).expanduser(),
            discovery_subnet=values.get(
                "DISCOVERY_SUBNET", ""
            ).strip(),
            ipc_host=ipc_host,
            ipc_port=_env_int(values, "IPC_PORT", 8765, 1, 65535),
            ipc_transport=ipc_transport,
            ipc_socket_path=Path(
                values.get("IPC_SOCKET_PATH")
                or str(database_path.with_name("meshpi.sock"))
            ).expanduser(),
            ipc_socket_gid=ipc_socket_gid,
            ipc_token=values.get("IPC_TOKEN", "").strip(),
            log_level=level,
            log_file=Path(raw_log_file).expanduser() if raw_log_file else None,
            log_max_bytes=_env_int(
                values,
                "LOG_MAX_BYTES",
                5 * 1024 * 1024,
                64 * 1024,
                1024 * 1024 * 1024,
            ),
            log_backup_count=_env_int(
                values,
                "LOG_BACKUP_COUNT",
                3,
                1,
                20,
            ),
            observation_retention_days=_env_int(
                values,
                "OBSERVATION_RETENTION_DAYS",
                365,
                1,
                3650,
            ),
            update_url=values.get(
                "UPDATE_URL", "https://venes.org/meshpi/version.json"
            ).strip(),
            update_timeout=_env_float(values, "UPDATE_TIMEOUT", 3, 0.5, 30),
            background_mode=background_mode,
            web_enabled=web_enabled == "true",
            web_port=web_port,
            web_public_url=web_public_url,
            web_base_path=web_base_path,
        )
