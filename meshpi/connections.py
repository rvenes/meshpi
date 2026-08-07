from __future__ import annotations

import hashlib
import ipaddress
import json
import os
import re
import socket
import sys
import tempfile
import threading
from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict, dataclass, replace
from pathlib import Path
from typing import Any

DEFAULT_MESHTASTIC_PORT = 4403
CONNECTION_FILE_VERSION = 3
SUPPORTED_TRANSPORTS = {"tcp", "serial", "ble"}
WINDOWS_PORT = re.compile(r"^COM\d+$", re.IGNORECASE)
LINUX_LEGACY_SERIAL = re.compile(r"^/dev/ttyS\d+$")
BLE_MAC_ADDRESS = re.compile(r"^(?:[0-9A-Fa-f]{2}:){5}[0-9A-Fa-f]{2}$")
BLE_UUID = re.compile(
    r"^[0-9A-Fa-f]{8}-(?:[0-9A-Fa-f]{4}-){3}[0-9A-Fa-f]{12}$"
)


class SerialIdentityMismatchError(RuntimeError):
    pass


def _profile_id(transport: str, endpoint: str) -> str:
    digest = hashlib.sha256(f"{transport}:{endpoint}".encode()).hexdigest()[:12]
    return f"{transport}-{digest}"


def canonical_ble_identifier(identifier: str) -> str:
    """Normaliser kjende BLE-adresser, men bevar ugjennomsiktige plattform-ID-ar."""
    identifier = identifier.strip()
    if BLE_MAC_ADDRESS.fullmatch(identifier) or BLE_UUID.fullmatch(identifier):
        return identifier.upper()
    return identifier


@dataclass(frozen=True, slots=True)
class ConnectionProfile:
    profile_id: str
    name: str
    transport: str
    host: str | None = None
    port: int | None = None
    device: str | None = None
    ble_identifier: str | None = None
    serial_number: str | None = None
    vid: int | None = None
    pid: int | None = None
    last_local_node_id: str | None = None

    @property
    def endpoint(self) -> str:
        if self.transport == "tcp":
            return f"{self.host}:{self.port}"
        if self.transport == "ble":
            return str(self.ble_identifier)
        return str(self.device)

    def as_dict(self) -> dict[str, Any]:
        data = asdict(self)
        if self.transport != "ble":
            data.pop("ble_identifier")
        if self.transport != "serial":
            for key in ("serial_number", "vid", "pid"):
                data.pop(key)
        if self.transport == "ble":
            for key in ("host", "port", "device"):
                data.pop(key)
        return data | {"endpoint": self.endpoint}

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
    def serial(
        cls,
        device: str,
        name: str | None = None,
        *,
        serial_number: str | None = None,
        vid: int | None = None,
        pid: int | None = None,
    ) -> ConnectionProfile:
        device = device.strip()
        if not device:
            raise ValueError("Seriellporten kan ikkje vere tom")
        return cls(
            profile_id=_profile_id("serial", device.casefold()),
            name=(name or Path(device).name or device).strip(),
            transport="serial",
            device=device,
            serial_number=(serial_number or "").strip() or None,
            vid=vid,
            pid=pid,
        )

    @classmethod
    def ble(
        cls,
        identifier: str,
        name: str | None = None,
    ) -> ConnectionProfile:
        identifier = canonical_ble_identifier(identifier)
        if not identifier:
            raise ValueError("BLE-identifikatoren kan ikkje vere tom")
        return cls(
            profile_id=_profile_id("ble", identifier),
            name=(name or identifier).strip(),
            transport="ble",
            ble_identifier=identifier,
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
                serial_number=str(data.get("serial_number", "") or "") or None,
                vid=_optional_int(data.get("vid")),
                pid=_optional_int(data.get("pid")),
            )
        elif transport == "ble":
            identifier = str(
                data.get("ble_identifier", "") or data.get("endpoint", "")
            )
            profile = cls.ble(
                identifier,
                str(data.get("name", "") or identifier),
            )
        else:
            raise ValueError(f"Ustøtta transport: {transport or 'tom'}")
        changes: dict[str, Any] = {}
        requested_id = str(data.get("profile_id", "")).strip()
        if requested_id and profile.transport != "ble":
            changes["profile_id"] = requested_id
        last_local_node_id = str(
            data.get("last_local_node_id", "") or ""
        ).strip()
        if last_local_node_id:
            if not re.fullmatch(r"![0-9A-Fa-f]{8}", last_local_node_id):
                raise ValueError("Tilkoplingsprofilen har ugyldig lokal node-ID")
            changes["last_local_node_id"] = last_local_node_id.lower()
        return replace(profile, **changes) if changes else profile


def _optional_int(value: Any) -> int | None:
    if value is None or value == "":
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def parse_connection_target(target: str, name: str | None = None) -> ConnectionProfile:
    target = target.strip()
    if not target:
        raise ValueError("Tilkoplingsmålet kan ikkje vere tomt")

    if target.lower().startswith("serial://"):
        return ConnectionProfile.serial(target[9:], name=name)
    if target.lower().startswith("ble://"):
        return ConnectionProfile.ble(target[6:], name=name)
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
                data = self._read()
                active_id = str(data.get("active_profile_id") or "")
                active_profile_id: str | None = None
                profiles_by_id: dict[str, tuple[str, ConnectionProfile]] = {}
                for item in data["profiles"]:
                    if not isinstance(item, dict):
                        continue
                    profile = ConnectionProfile.from_dict(item)
                    requested_id = str(item.get("profile_id") or "")
                    current = profiles_by_id.get(profile.profile_id)
                    if current is None or requested_id == active_id:
                        profiles_by_id[profile.profile_id] = (requested_id, profile)
                    if requested_id == active_id:
                        active_profile_id = profile.profile_id
                profiles = [entry[1].as_dict() for entry in profiles_by_id.values()]
                normalized = {
                    "version": CONNECTION_FILE_VERSION,
                    "active_profile_id": active_profile_id or (
                        active_id if any(
                            profile["profile_id"] == active_id for profile in profiles
                        ) else None
                    ),
                    "profiles": profiles,
                }
                if data != normalized:
                    self._write(normalized)
                return
            self._write(
                {
                    "version": CONNECTION_FILE_VERSION,
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
        version = data.get("version", 1)
        if version not in {1, 2, CONNECTION_FILE_VERSION}:
            raise ValueError(f"Ustøtta versjon av tilkoplingsfila: {version}")
        data["version"] = version
        return data

    def _write(self, data: dict[str, Any]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        descriptor, temporary_name = tempfile.mkstemp(
            prefix=f".{self.path.name}.",
            suffix=".tmp",
            dir=self.path.parent,
            text=True,
        )
        temporary = Path(temporary_name)
        try:
            with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n") as output:
                output.write(json.dumps(data, ensure_ascii=False, indent=2) + "\n")
            os.replace(temporary, self.path)
        finally:
            temporary.unlink(missing_ok=True)

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
                        "version": CONNECTION_FILE_VERSION,
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
                    "version": CONNECTION_FILE_VERSION,
                    "active_profile_id": profile.profile_id,
                    "profiles": [current.as_dict() for current in profiles],
                }
            )
        return profile

    def activate(self, profile_id: str) -> ConnectionProfile:
        profile = self.get(profile_id)
        return self.save_and_activate(profile)

    def remember_local_node(
        self,
        profile_id: str,
        local_node_id: str,
    ) -> ConnectionProfile:
        normalized = local_node_id.strip().lower()
        if not re.fullmatch(r"![0-9a-f]{8}", normalized):
            raise ValueError("Ugyldig lokal node-ID")
        with self._lock:
            data = self._read()
            profiles = [
                ConnectionProfile.from_dict(item)
                for item in data["profiles"]
                if isinstance(item, dict)
            ]
            updated: ConnectionProfile | None = None
            for index, profile in enumerate(profiles):
                if profile.profile_id == profile_id:
                    updated = replace(profile, last_local_node_id=normalized)
                    profiles[index] = updated
                    break
            if updated is None:
                raise ValueError(f"Fann ikkje tilkoplingsprofilen {profile_id}")
            self._write(
                {
                    "version": CONNECTION_FILE_VERSION,
                    "active_profile_id": data.get("active_profile_id"),
                    "profiles": [profile.as_dict() for profile in profiles],
                }
            )
        return updated


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


def _recommended_serial(system_device: str, device: str) -> bool:
    if not sys.platform.startswith("linux"):
        return True
    if device.startswith("/dev/serial/by-id/"):
        return True
    return LINUX_LEGACY_SERIAL.fullmatch(system_device) is None


def discover_serial() -> list[dict[str, Any]]:
    from serial.tools import list_ports

    stable = _stable_serial_paths()
    devices: list[dict[str, Any]] = []
    for port in list_ports.comports():
        device = stable.get(str(Path(port.device).resolve()), port.device)
        recommended = _recommended_serial(str(port.device), str(device))
        devices.append(
            {
                "transport": "serial",
                "target": device,
                "device": device,
                "system_device": port.device,
                "name": port.description or Path(device).name,
                "description": port.description,
                "serial_number": port.serial_number,
                "vid": getattr(port, "vid", None),
                "pid": getattr(port, "pid", None),
                "hwid": port.hwid,
                "recommended": recommended,
            }
        )
    return sorted(
        devices,
        key=lambda item: (
            not bool(item["recommended"]),
            str(item["name"]).casefold(),
        ),
    )


def resolve_serial_profile(
    profile: ConnectionProfile,
    devices: list[dict[str, Any]],
) -> ConnectionProfile:
    """Oppdater seriellsti berre når USB-identiteten gir eitt sikkert treff."""
    if profile.transport != "serial":
        return profile

    current = [
        item
        for item in devices
        if profile.device in {item.get("device"), item.get("system_device")}
    ]
    if not profile.serial_number and len(current) == 1:
        item = current[0]
        serial_number = str(item.get("serial_number") or "").strip() or None
        vid = _optional_int(item.get("vid"))
        pid = _optional_int(item.get("pid"))
        return replace(
            profile,
            device=str(item.get("device") or profile.device),
            serial_number=serial_number or profile.serial_number,
            vid=vid if vid is not None else profile.vid,
            pid=pid if pid is not None else profile.pid,
        )

    if not profile.serial_number:
        return profile

    matches = []
    for item in devices:
        if str(item.get("serial_number") or "").strip() != profile.serial_number:
            continue
        if profile.vid is not None and _optional_int(item.get("vid")) != profile.vid:
            continue
        if profile.pid is not None and _optional_int(item.get("pid")) != profile.pid:
            continue
        if not str(item.get("device") or "").strip():
            continue
        matches.append(item)

    if len(matches) != 1:
        if current and not any(item in matches for item in current):
            raise SerialIdentityMismatchError(
                f"Seriellporten {profile.device} høyrer ikkje lenger til "
                "den lagra USB-eininga"
            )
        return profile

    match = matches[0]
    return replace(
        profile,
        device=str(match["device"]),
        serial_number=str(match["serial_number"]).strip(),
        vid=_optional_int(match.get("vid")),
        pid=_optional_int(match.get("pid")),
    )


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
