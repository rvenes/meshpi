from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

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
        "OBSERVATION_RETENTION_DAYS",
        "UPDATE_URL",
        "UPDATE_TIMEOUT",
        "BACKGROUND_MODE",
    }
)


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
        raise ValueError(f"{name} må vere eit heiltal") from exc
    if not minimum <= value <= maximum:
        raise ValueError(f"{name} må vere mellom {minimum} og {maximum}")
    return value


def _env_float(
    values: dict[str, str], name: str, default: float, minimum: float, maximum: float
) -> float:
    raw = values.get(name, str(default))
    try:
        value = float(raw)
    except ValueError as exc:
        raise ValueError(f"{name} må vere eit tal") from exc
    if not minimum <= value <= maximum:
        raise ValueError(f"{name} må vere mellom {minimum} og {maximum}")
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
    observation_retention_days: int = 365
    update_url: str = "https://venes.org/meshpi/version.json"
    update_timeout: float = 3.0
    background_mode: str = "always"

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
            raise ValueError("IPC_HOST må vere ei lokal loopback-adresse")
        ipc_transport = values.get("IPC_TRANSPORT", "auto").strip().lower()
        if ipc_transport not in {"auto", "tcp", "unix"}:
            raise ValueError("IPC_TRANSPORT må vere «auto», «tcp» eller «unix»")
        if ipc_transport == "unix" and os.name == "nt":
            raise ValueError("IPC_TRANSPORT=unix er ikkje støtta på Windows")
        level = values.get("LOG_LEVEL", "INFO").strip().upper()
        if level not in {"DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"}:
            raise ValueError("Ugyldig LOG_LEVEL")
        background_mode = values.get("BACKGROUND_MODE", "always").strip().lower()
        if background_mode not in {"always", "session"}:
            raise ValueError("BACKGROUND_MODE må vere «always» eller «session»")
        database_path = Path(
            values.get("DATABASE_PATH", "./data/meshtastic.db")
        ).expanduser()
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
        )
