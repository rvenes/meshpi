from __future__ import annotations

import hashlib
import ipaddress
import json
import os
import re
import socket
import threading
from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

DEFAULT_MESHTASTIC_PORT = 4403
SUPPORTED_TRANSPORTS = {"tcp", "serial"}
WINDOWS_PORT = re.compile(r"^COM\d+$", re.IGNORECASE)


def _profile_id(transport: str, endpoint: str) -> str:
    digest = hashlib.sha256(f"{transport}:{endpoint}".encode()).hexdigest()[:12]
    return f"{transport}-{digest}"


@dataclass(frozen=True, slots=True)
class ConnectionProfile:
    profile_id: str
    name: str
    transport: str
    host: str | None = None
    port: int | None = None
    device: str | None = None

    @property
    def endpoint(self) -> str:
        if self.transport == "tcp":
            return f"{self.host}:{self.port}"
        return str(self.device)

    def as_dict(self) -> dict[str, Any]:
        return asdict(self) | {"endpoint": self.endpoint}

    @classmethod
    def tcp(
        cls,
        host: str,
        port: int = DEFAULT_MESHTASTIC_PORT,
        name: str | None = None,
    ) -> ConnectionProfile:
        host = host.strip()
        if not host:
            raise ValueError("TCP-adressa kan ikkje vere tom")
        if not 1 <= port <= 65535:
            raise ValueError("TCP-porten må vere mellom 1 og 65535")
        endpoint = f"{host}:{port}"
        return cls(
            profile_id=_profile_id("tcp", endpoint.casefold()),
            name=(name or host).strip(),
            transport="tcp",
            host=host,
            port=port,
        )

    @classmethod
    def serial(cls, device: str, name: str | None = None) -> ConnectionProfile:
        device = device.strip()
        if not device:
            raise ValueError("Seriellporten kan ikkje vere tom")
        return cls(
            profile_id=_profile_id("serial", device.casefold()),
            name=(name or Path(device).name or device).strip(),
            transport="serial",
            device=device,
        )

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> ConnectionProfile:
        transport = str(data.get("transport", "")).lower()
        if transport == "tcp":
            profile = cls.tcp(
                str(data.get("host", "")),
                int(data.get("port", DEFAULT_MESHTASTIC_PORT)),
                str(data.get("name", "") or data.get("host", "")),
            )
        elif transport == "serial":
            profile = cls.serial(
                str(data.get("device", "")),
                str(data.get("name", "") or Path(str(data.get("device", ""))).name),
            )
        else:
            raise ValueError(f"Ustøtta transport: {transport or 'tom'}")
        requested_id = str(data.get("profile_id", "")).strip()
        if requested_id:
            return cls(
                profile_id=requested_id,
                name=profile.name,
                transport=profile.transport,
                host=profile.host,
                port=profile.port,
                device=profile.device,
            )
        return profile


def parse_connection_target(target: str, name: str | None = None) -> ConnectionProfile:
    target = target.strip()
    if not target:
        raise ValueError("Tilkoplingsmålet kan ikkje vere tomt")

    if target.lower().startswith("serial://"):
        return ConnectionProfile.serial(target[9:], name=name)
    if target.lower().startswith("tcp://"):
        target = target[6:]
    elif target.startswith(("/", "./", "../")) or WINDOWS_PORT.fullmatch(target):
        return ConnectionProfile.serial(target, name=name)

    host = target
    port = DEFAULT_MESHTASTIC_PORT
    if target.startswith("[") and "]" in target:
        end = target.index("]")
        host = target[1:end]
        suffix = target[end + 1 :]
        if suffix:
            if not suffix.startswith(":"):
                raise ValueError("Ugyldig IPv6-adresse")
            port = int(suffix[1:])
    elif target.count(":") == 1:
        candidate_host, candidate_port = target.rsplit(":", 1)
        if candidate_port.isdigit():
            host, port = candidate_host, int(candidate_port)
    return ConnectionProfile.tcp(host, port, name=name)


class ConnectionStore:
    def __init__(
        self,
        path: str | Path,
        default_profile: ConnectionProfile | None = None,
    ):
        self.path = Path(path)
        self._lock = threading.RLock()
        self._default_profile = default_profile
        self._ensure_initialized()

    def _ensure_initialized(self) -> None:
        with self._lock:
            if self.path.is_file():
                self._read()
                return
            self._write(
                {
                    "version": 1,
                    "active_profile_id": (
                        self._default_profile.profile_id if self._default_profile else None
                    ),
                    "profiles": (
                        [self._default_profile.as_dict()] if self._default_profile else []
                    ),
                }
            )

    def _read(self) -> dict[str, Any]:
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise ValueError(f"Klarte ikkje lese tilkoplingsprofilar: {exc}") from exc
        if not isinstance(data, dict) or not isinstance(data.get("profiles"), list):
            raise ValueError("Tilkoplingsfila har ugyldig format")
        return data

    def _write(self, data: dict[str, Any]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.path.with_suffix(self.path.suffix + ".tmp")
        temporary.write_text(
            json.dumps(data, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        os.replace(temporary, self.path)

    def list_profiles(self) -> list[ConnectionProfile]:
        with self._lock:
            return [
                ConnectionProfile.from_dict(item)
                for item in self._read()["profiles"]
                if isinstance(item, dict)
            ]

    def active_profile(self) -> ConnectionProfile | None:
        with self._lock:
            data = self._read()
            active_id = str(data.get("active_profile_id", ""))
            profiles = [
                ConnectionProfile.from_dict(item)
                for item in data["profiles"]
                if isinstance(item, dict)
            ]
            for profile in profiles:
                if profile.profile_id == active_id:
                    return profile
            if profiles:
                return profiles[0]
            if self._default_profile is not None:
                self._write(
                    {
                        "version": 1,
                        "active_profile_id": self._default_profile.profile_id,
                        "profiles": [self._default_profile.as_dict()],
                    }
                )
            return self._default_profile

    def get(self, profile_id: str) -> ConnectionProfile:
        for profile in self.list_profiles():
            if profile.profile_id == profile_id:
                return profile
        raise ValueError(f"Fann ikkje tilkoplingsprofilen {profile_id}")

    def save_and_activate(self, profile: ConnectionProfile) -> ConnectionProfile:
        with self._lock:
            data = self._read()
            profiles = [
                ConnectionProfile.from_dict(item)
                for item in data["profiles"]
                if isinstance(item, dict)
            ]
            profiles = [
                current for current in profiles if current.profile_id != profile.profile_id
            ]
            profiles.append(profile)
            self._write(
                {
                    "version": 1,
                    "active_profile_id": profile.profile_id,
                    "profiles": [current.as_dict() for current in profiles],
                }
            )
        return profile

    def activate(self, profile_id: str) -> ConnectionProfile:
        profile = self.get(profile_id)
        return self.save_and_activate(profile)


def _stable_serial_paths() -> dict[str, str]:
    result: dict[str, str] = {}
    directory = Path("/dev/serial/by-id")
    if not directory.is_dir():
        return result
    for path in directory.iterdir():
        try:
            result[str(path.resolve())] = str(path)
        except OSError:
            continue
    return result


def discover_serial() -> list[dict[str, Any]]:
    from serial.tools import list_ports

    stable = _stable_serial_paths()
    devices: list[dict[str, Any]] = []
    for port in list_ports.comports():
        device = stable.get(str(Path(port.device).resolve()), port.device)
        devices.append(
            {
                "transport": "serial",
                "target": device,
                "device": device,
                "name": port.description or Path(device).name,
                "description": port.description,
                "serial_number": port.serial_number,
                "hwid": port.hwid,
            }
        )
    return sorted(devices, key=lambda item: str(item["name"]).casefold())


def discover_tcp(subnet: str, port: int = DEFAULT_MESHTASTIC_PORT) -> list[dict[str, Any]]:
    network = ipaddress.ip_network(subnet, strict=False)
    if network.num_addresses > 1024:
        raise ValueError("Oppdagingsnettet kan ikkje vere større enn /22")

    def is_open(address: str) -> str | None:
        try:
            with socket.create_connection((address, port), timeout=0.18):
                return address
        except OSError:
            return None

    addresses = [str(address) for address in network.hosts()]
    with ThreadPoolExecutor(max_workers=min(64, max(1, len(addresses)))) as executor:
        found = [address for address in executor.map(is_open, addresses) if address]
    return [
        {
            "transport": "tcp",
            "target": f"{address}:{port}",
            "host": address,
            "port": port,
            "name": address,
        }
        for address in found
    ]


def discover_local_subnets() -> list[str]:
    """Finn relevante lokale IPv4-/24-nett utan ein installasjonsspesifikk standard."""
    addresses: set[str] = set()
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as probe:
            probe.connect(("1.1.1.1", 80))
            addresses.add(str(probe.getsockname()[0]))
    except OSError:
        pass
    try:
        for info in socket.getaddrinfo(socket.gethostname(), None, socket.AF_INET):
            addresses.add(str(info[4][0]))
    except OSError:
        pass

    networks: set[str] = set()
    for address in addresses:
        try:
            parsed = ipaddress.ip_address(address)
        except ValueError:
            continue
        if parsed.is_loopback or parsed.is_link_local or parsed.is_unspecified:
            continue
        networks.add(str(ipaddress.ip_network(f"{parsed}/24", strict=False)))
    return sorted(networks)
