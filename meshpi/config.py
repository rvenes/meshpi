from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path


def _load_env_file(path: Path) -> None:
    """Last inn enkle KEY=VALUE-linjer utan å overskrive miljøvariablar."""
    if not path.is_file():
        return
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip().strip("\"'")
        if key:
            os.environ.setdefault(key, value)


def _env_int(name: str, default: int, minimum: int, maximum: int) -> int:
    raw = os.environ.get(name, str(default))
    try:
        value = int(raw)
    except ValueError as exc:
        raise ValueError(f"{name} må vere eit heiltal") from exc
    if not minimum <= value <= maximum:
        raise ValueError(f"{name} må vere mellom {minimum} og {maximum}")
    return value


def _env_float(name: str, default: float, minimum: float, maximum: float) -> float:
    raw = os.environ.get(name, str(default))
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
    log_level: str = "INFO"
    update_url: str = "https://venes.org/meshpi/version.json"
    update_timeout: float = 3.0
    background_mode: str = "always"

    @classmethod
    def load(cls, env_file: str | Path = ".env") -> Settings:
        _load_env_file(Path(env_file))
        host = os.environ.get("MESHTASTIC_HOST", "").strip()
        ipc_host = os.environ.get("IPC_HOST", "127.0.0.1").strip()
        if ipc_host not in {"127.0.0.1", "::1", "localhost"}:
            raise ValueError("IPC_HOST må vere ei lokal loopback-adresse")
        level = os.environ.get("LOG_LEVEL", "INFO").strip().upper()
        if level not in {"DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"}:
            raise ValueError("Ugyldig LOG_LEVEL")
        background_mode = os.environ.get("BACKGROUND_MODE", "always").strip().lower()
        if background_mode not in {"always", "session"}:
            raise ValueError("BACKGROUND_MODE må vere «always» eller «session»")
        database_path = Path(
            os.environ.get("DATABASE_PATH", "./data/meshtastic.db")
        ).expanduser()
        return cls(
            meshtastic_host=host,
            meshtastic_port=_env_int("MESHTASTIC_PORT", 4403, 1, 65535),
            database_path=database_path,
            connections_path=Path(
                os.environ.get(
                    "CONNECTIONS_PATH",
                    str(database_path.with_name("connections.json")),
                )
            ).expanduser(),
            discovery_subnet=os.environ.get(
                "DISCOVERY_SUBNET", ""
            ).strip(),
            ipc_host=ipc_host,
            ipc_port=_env_int("IPC_PORT", 8765, 1, 65535),
            log_level=level,
            update_url=os.environ.get(
                "UPDATE_URL", "https://venes.org/meshpi/version.json"
            ).strip(),
            update_timeout=_env_float("UPDATE_TIMEOUT", 3, 0.5, 30),
            background_mode=background_mode,
        )
