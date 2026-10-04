from __future__ import annotations

import math
import re
from typing import Any

from meshpi.models import sanitize_terminal_text

# Regional spans and preset parameters from Meshtastic's radio documentation:
# https://meshtastic.org/docs/configuration/radio/lora/
# https://meshtastic.org/docs/overview/radio-settings/
REGION_SPANS = {
    "US": (902.0, 928.0),
    "EU_433": (433.0, 434.0),
    "EU_868": (869.4, 869.65),
    "CN": (470.0, 510.0),
    "JP": (920.5, 923.5),
    "ANZ": (915.0, 928.0),
    "ANZ_433": (433.05, 434.79),
    "KR": (920.0, 923.0),
    "TW": (920.0, 925.0),
    "RU": (868.7, 869.2),
    "IN": (865.0, 867.0),
    "NZ_865": (864.0, 868.0),
    "TH": (920.0, 925.0),
    "UA_433": (433.0, 434.7),
    "UA_868": (868.0, 868.6),
    "MY_433": (433.0, 435.0),
    "MY_919": (919.0, 924.0),
    "SG_923": (917.0, 925.0),
    "PH_433": (433.0, 434.7),
    "PH_868": (868.0, 869.4),
    "PH_915": (915.0, 918.0),
    "KZ_433": (433.075, 434.775),
    "KZ_863": (863.0, 868.0),
    "NP_865": (865.0, 868.0),
    "BR_902": (902.0, 907.5),
}
PRESETS = {
    "LONG_FAST": (250.0, 11, 5, "LongFast"),
    "LONG_SLOW": (125.0, 12, 8, "LongSlow"),
    "VERY_LONG_SLOW": (62.5, 12, 8, "VeryLongSlow"),
    "LONG_MODERATE": (125.0, 11, 8, "LongMod"),
    "MEDIUM_SLOW": (250.0, 10, 5, "MediumSlow"),
    "MEDIUM_FAST": (250.0, 9, 5, "MediumFast"),
    "SHORT_SLOW": (250.0, 8, 5, "ShortSlow"),
    "SHORT_FAST": (250.0, 7, 5, "ShortFast"),
    "SHORT_TURBO": (500.0, 7, 5, "ShortTurbo"),
    "LONG_TURBO": (500.0, 11, 8, "LongTurbo"),
}


def _section(parent: Any, name: str) -> Any:
    if parent is None:
        return None
    has_field = getattr(parent, "HasField", None)
    if callable(has_field) and not has_field(name):
        return None
    return getattr(parent, name, None)


def _value(message: Any, name: str) -> Any:
    value = getattr(message, name, None)
    descriptor = getattr(message, "DESCRIPTOR", None)
    field = descriptor.fields_by_name.get(name) if descriptor else None
    if field and field.enum_type and value is not None:
        enum_value = field.enum_type.values_by_number.get(value)
        return enum_value.name if enum_value else f"UNKNOWN_{value}"
    if isinstance(value, str):
        return sanitize_terminal_text(value, 160)
    return value if isinstance(value, (int, float, bool)) else None


def radio_details(
    lora: Any, primary_name: str | None, *, firmware_version: str | None = None,
) -> dict[str, Any]:
    if lora is None:
        return {}
    fields = (
        "region", "use_preset", "modem_preset", "bandwidth", "spread_factor",
        "coding_rate", "hop_limit", "tx_enabled", "tx_power", "channel_num",
        "frequency_offset", "override_frequency", "ignore_mqtt", "config_ok_to_mqtt",
    )
    result = {field: _value(lora, field) for field in fields}
    result.update(dict.fromkeys((
        "band_min_mhz", "band_max_mhz", "effective_spread_factor", "effective_coding_rate",
        "effective_bandwidth_khz", "frequency_mhz", "effective_slot", "frequency_source",
    )))
    # 2.8 introduces region profiles, slot padding and a new preset map.
    # https://github.com/meshtastic/firmware/releases/tag/v2.8.0.47db0e3
    version = re.match(r"^v?(\d+)\.(\d+)(?:[.\s+-]|$)", str(firmware_version or "").strip())
    result["derivation_unavailable"] = not (
        version and int(version[1]) == 2 and int(version[2]) < 8
    )
    if result["derivation_unavailable"]:
        return result
    span = REGION_SPANS.get(result["region"])
    result["band_min_mhz"], result["band_max_mhz"] = span or (None, None)
    preset = PRESETS.get(result["modem_preset"])
    bandwidth = result["bandwidth"]
    if result["use_preset"] is True:
        bandwidth, sf, cr, default_name = preset or (None, None, None, None)
        result["effective_spread_factor"] = sf
        configured_cr = result["coding_rate"]
        result["effective_coding_rate"] = (
            configured_cr if configured_cr in (5, 6, 7, 8) else cr
        )
    elif result["use_preset"] is False:
        bandwidth = {31: 31.25, 62: 62.5, 200: 203.125, 400: 406.25,
                     800: 812.5, 1600: 1625.0}.get(bandwidth, bandwidth)
        default_name = "Custom"
        result["effective_spread_factor"] = result["spread_factor"]
        result["effective_coding_rate"] = result["coding_rate"]
    else:
        bandwidth, default_name = None, None
    result["effective_bandwidth_khz"] = bandwidth
    offset = result["frequency_offset"]
    override = result["override_frequency"]
    if not isinstance(offset, (int, float)) or not math.isfinite(offset):
        return result
    if isinstance(override, (int, float)) and math.isfinite(override) and override > 0:
        result["frequency_mhz"] = round(override + offset, 6)
        result["frequency_source"] = "override"
        return result
    if not span or not isinstance(bandwidth, (int, float)) or bandwidth <= 0:
        return result
    # 2.4 GHz and unfamiliar presets stay unknown rather than assuming sub-GHz values.
    slots = math.floor(round((span[1] - span[0]) * 1000 / bandwidth, 6))
    configured_slot = result["channel_num"]
    if slots < 1 or not isinstance(configured_slot, int) or configured_slot < 0:
        return result
    if configured_slot:
        slot_index = (configured_slot - 1) % slots
    else:
        name = primary_name or default_name
        if name is None or primary_name is None:
            return result
        # Firmware uses the 32-bit djb2 hash of the primary channel's effective name.
        # https://github.com/meshtastic/firmware/blob/master/src/mesh/RadioInterface.cpp
        name_hash = 5381
        for byte in name.encode("utf-8"):
            name_hash = (name_hash * 33 + byte) & 0xFFFFFFFF
        slot_index = name_hash % slots
    result["effective_slot"] = slot_index + 1
    result["frequency_mhz"] = round(
        span[0] + bandwidth / 2000 + slot_index * bandwidth / 1000 + offset, 6
    )
    result["frequency_source"] = "calculated"
    return result


def interface_details(interface: Any) -> dict[str, Any]:
    """Read an explicit allowlist from the existing interface, without device requests."""
    local_node = getattr(interface, "localNode", None)
    config = getattr(local_node, "localConfig", None)
    device = _section(config, "device")
    metadata = getattr(interface, "metadata", None)
    primary_name = None
    channels = []
    for raw in list(getattr(local_node, "channels", None) or []):
        role = _value(raw, "role")
        if role not in (1, 2, "PRIMARY", "SECONDARY"):
            continue
        settings = _section(raw, "settings")
        name = _value(settings, "name")
        if role in (1, "PRIMARY"):
            primary_name = name
        channels.append({
            "index": _value(raw, "index"),
            "name": name,
            "role": "PRIMARY" if role in (1, "PRIMARY") else "SECONDARY",
            "uplink_enabled": _value(settings, "uplink_enabled"),
            "downlink_enabled": _value(settings, "downlink_enabled"),
        })
    nodes = getattr(interface, "nodes", None)
    firmware_version = _value(metadata, "firmware_version")
    radio = radio_details(
        _section(config, "lora"), primary_name, firmware_version=firmware_version,
    )
    for channel in channels:
        preset = (
            PRESETS.get(radio.get("modem_preset")) if not radio.get("derivation_unavailable")
            else None
        )
        default_name = preset[3] if preset and radio.get("use_preset") else None
        if radio.get("use_preset") is False and not radio.get("derivation_unavailable"):
            default_name = "Custom"
        channel["display_name"] = channel["name"] or default_name
    return {
        "firmware_version": firmware_version,
        "hw_model": _value(metadata, "hw_model"),
        "role": _value(device, "role") if device is not None else _value(metadata, "role"),
        "radio": radio,
        "channels": channels,
        "registry_count": len(nodes) if isinstance(nodes, dict) else None,
    }
