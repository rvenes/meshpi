from __future__ import annotations

import hashlib
import json
from datetime import datetime, timedelta, timezone
from enum import Enum
from typing import Any

from meshpi.models import Transport, node_num_to_id, now_iso

TELEMETRY_PORTS = {"TELEMETRY_APP", 67}
POSITION_PORTS = {"POSITION_APP", 3}
TELEMETRY_KINDS = {
    "deviceMetrics": "device",
    "device_metrics": "device",
    "environmentMetrics": "environment",
    "environment_metrics": "environment",
    "airQualityMetrics": "air_quality",
    "air_quality_metrics": "air_quality",
    "powerMetrics": "power",
    "power_metrics": "power",
    "localStats": "local_stats",
    "local_stats": "local_stats",
    "healthMetrics": "health",
    "health_metrics": "health",
    "hostMetrics": "host",
    "host_metrics": "host",
    "trafficManagementStats": "traffic_management",
    "traffic_management_stats": "traffic_management",
}
MAX_FUTURE_TIMESTAMP_SKEW = timedelta(minutes=10)


def _packet_id(packet: dict[str, Any]) -> int | None:
    try:
        value = packet.get("id")
        return int(value) if value is not None else None
    except (TypeError, ValueError):
        return None


def _source_node(packet: dict[str, Any]) -> str | None:
    value = packet.get("fromId")
    if isinstance(value, str) and value.startswith("!") and len(value) == 9:
        return value.lower()
    try:
        return node_num_to_id(int(packet["from"]))
    except (KeyError, TypeError, ValueError):
        return None


def _number(packet: dict[str, Any], name: str, kind: type[int] | type[float]) -> Any:
    value = packet.get(name)
    try:
        return kind(value) if value is not None else None
    except (TypeError, ValueError):
        return None


def _transport(packet: dict[str, Any]) -> Transport:
    if packet.get("viaMqtt") is True:
        return Transport.MQTT
    mechanism = packet.get("transportMechanism")
    if isinstance(mechanism, Enum):
        mechanism = mechanism.name
    normalized = str(mechanism or "").upper()
    if normalized in {"TRANSPORT_MQTT", "MQTT"}:
        return Transport.MQTT
    if normalized in {
        "TRANSPORT_LORA",
        "TRANSPORT_LORA_ALT1",
        "TRANSPORT_LORA_ALT2",
        "TRANSPORT_LORA_ALT3",
        "LORA",
        "RF",
    }:
        return Transport.RF
    return Transport.UNKNOWN


def _iso_timestamp(value: Any) -> str | None:
    try:
        seconds = int(value)
        if seconds <= 0:
            return None
        timestamp = datetime.fromtimestamp(seconds, tz=timezone.utc)
        if timestamp > datetime.now(timezone.utc) + MAX_FUTURE_TIMESTAMP_SKEW:
            return None
        return timestamp.isoformat(timespec="seconds")
    except (TypeError, ValueError, OSError, OverflowError):
        return None


def _sample_time(payload: dict[str, Any], packet: dict[str, Any]) -> str:
    for value in (
        payload.get("timestamp"),
        payload.get("time"),
        packet.get("rxTime"),
    ):
        parsed = _iso_timestamp(value)
        if parsed:
            return parsed
    return now_iso()


def _json_value(value: Any, depth: int = 0) -> Any:
    if depth > 5:
        return None
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, Enum):
        return value.name
    if isinstance(value, dict):
        return {
            str(key): normalized
            for key, item in value.items()
            if (normalized := _json_value(item, depth + 1)) is not None
        }
    if isinstance(value, (list, tuple)):
        return [
            normalized
            for item in value
            if (normalized := _json_value(item, depth + 1)) is not None
        ]
    return str(value)


def _dedupe_key(prefix: str, node_id: str, packet_id: int | None, payload: Any) -> str:
    if packet_id is not None:
        identity = f"{prefix}:{node_id}:{packet_id}"
    else:
        canonical = json.dumps(
            payload,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )
        identity = f"{prefix}:{node_id}:{canonical}"
    return hashlib.sha256(identity.encode()).hexdigest()


def _packet_metadata(packet: dict[str, Any]) -> dict[str, Any]:
    return {
        "packet_id": _packet_id(packet),
        "transport": str(_transport(packet)),
        "rssi": _number(packet, "rxRssi", int),
        "snr": _number(packet, "rxSnr", float),
        "hop_limit": _number(packet, "hopLimit", int),
        "hop_start": _number(packet, "hopStart", int),
        "received_at": now_iso(),
    }


def parse_telemetry_packet(packet: dict[str, Any]) -> list[dict[str, Any]]:
    decoded = packet.get("decoded")
    if not isinstance(decoded, dict) or decoded.get("portnum") not in TELEMETRY_PORTS:
        return []
    telemetry = decoded.get("telemetry")
    node_id = _source_node(packet)
    if not isinstance(telemetry, dict) or node_id is None:
        return []

    metadata = _packet_metadata(packet)
    samples: list[dict[str, Any]] = []
    for field, kind in TELEMETRY_KINDS.items():
        metrics = telemetry.get(field)
        if not isinstance(metrics, dict):
            continue
        normalized = _json_value(metrics)
        if not isinstance(normalized, dict) or not normalized:
            continue
        identity_payload = {
            "kind": kind,
            "sample_time": _sample_time(telemetry, packet),
            "metrics": normalized,
        }
        samples.append(
            metadata
            | {
                "node_id": node_id,
                "kind": kind,
                "sample_time": identity_payload["sample_time"],
                "metrics": normalized,
                "dedupe_key": _dedupe_key(
                    f"telemetry:{kind}",
                    node_id,
                    metadata["packet_id"],
                    identity_payload,
                ),
            }
        )
    return samples


def _field(payload: dict[str, Any], *names: str) -> Any:
    for name in names:
        if name in payload:
            return payload[name]
    return None


def _optional_number(
    payload: dict[str, Any],
    names: tuple[str, ...],
    kind: type[int] | type[float],
    *,
    scale: float = 1,
) -> int | float | None:
    value = _field(payload, *names)
    try:
        return kind(value) * scale if value is not None else None
    except (TypeError, ValueError):
        return None


def parse_position_packet(packet: dict[str, Any]) -> dict[str, Any] | None:
    decoded = packet.get("decoded")
    if not isinstance(decoded, dict) or decoded.get("portnum") not in POSITION_PORTS:
        return None
    position = decoded.get("position")
    node_id = _source_node(packet)
    if not isinstance(position, dict) or node_id is None:
        return None

    latitude = _optional_number(position, ("latitude",), float)
    longitude = _optional_number(position, ("longitude",), float)
    if latitude is None:
        latitude = _optional_number(position, ("latitudeI", "latitude_i"), float, scale=1e-7)
    if longitude is None:
        longitude = _optional_number(
            position, ("longitudeI", "longitude_i"), float, scale=1e-7
        )
    if (
        latitude is None
        or longitude is None
        or not -90 <= latitude <= 90
        or not -180 <= longitude <= 180
    ):
        return None

    metadata = _packet_metadata(packet)
    values = {
        "node_id": node_id,
        "sample_time": _sample_time(position, packet),
        "latitude": latitude,
        "longitude": longitude,
        "altitude_msl": _optional_number(position, ("altitude",), int),
        "altitude_hae": _optional_number(
            position, ("altitudeHae", "altitude_hae"), int
        ),
        "geoidal_separation": _optional_number(
            position,
            ("altitudeGeoidalSeparation", "altitude_geoidal_separation"),
            int,
        ),
        "pdop": _optional_number(position, ("PDOP", "pdop"), float, scale=0.01),
        "hdop": _optional_number(position, ("HDOP", "hdop"), float, scale=0.01),
        "vdop": _optional_number(position, ("VDOP", "vdop"), float, scale=0.01),
        "gps_accuracy_mm": _optional_number(
            position, ("gpsAccuracy", "gps_accuracy"), int
        ),
        "ground_speed": _optional_number(
            position, ("groundSpeed", "ground_speed"), float
        ),
        "ground_track": _optional_number(
            position, ("groundTrack", "ground_track"), float, scale=0.01
        ),
        "fix_quality": _optional_number(
            position, ("fixQuality", "fix_quality"), int
        ),
        "fix_type": _optional_number(position, ("fixType", "fix_type"), int),
        "sats_in_view": _optional_number(
            position, ("satsInView", "sats_in_view"), int
        ),
        "location_source": _json_value(
            _field(position, "locationSource", "location_source")
        ),
        "altitude_source": _json_value(
            _field(position, "altitudeSource", "altitude_source")
        ),
        "precision_bits": _optional_number(
            position, ("precisionBits", "precision_bits"), int
        ),
        "metadata": _json_value(position),
    }
    identity_payload = {
        key: value
        for key, value in values.items()
        if key not in {"metadata"}
    }
    return (
        metadata
        | values
        | {
            "dedupe_key": _dedupe_key(
                "position",
                node_id,
                metadata["packet_id"],
                identity_payload,
            )
        }
    )
