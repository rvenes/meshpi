import json
from types import SimpleNamespace

import pytest
from meshtastic.protobuf import channel_pb2, config_pb2, localonly_pb2, mesh_pb2

from meshpi.local_node import interface_details, radio_details


def lora(region="EU_868", preset="LONG_FAST", **kwargs):
    return config_pb2.Config.LoRaConfig(
        region=config_pb2.Config.LoRaConfig.RegionCode.Value(region),
        modem_preset=config_pb2.Config.LoRaConfig.ModemPreset.Value(preset),
        use_preset=True, **kwargs,
    )


def legacy_radio_details(config, primary_name):
    return radio_details(config, primary_name, firmware_version="2.7.11")


@pytest.mark.parametrize("region,slot,frequency", [
    ("EU_868", 1, 869.525), ("EU_433", 4, 433.875), ("US", 20, 906.875),
])
def test_frequency_matches_documented_longfast_defaults(region, slot, frequency):
    result = legacy_radio_details(lora(region), "")
    assert result["effective_slot"] == slot
    assert result["frequency_mhz"] == pytest.approx(frequency)
    assert result["frequency_source"] == "calculated"


def test_explicit_slot_and_override_include_offset():
    result = legacy_radio_details(lora("US", channel_num=1, frequency_offset=0.01), "Ops")
    assert result["frequency_mhz"] == pytest.approx(902.135)
    result = legacy_radio_details(lora(override_frequency=868.25, frequency_offset=-0.005), "")
    assert result["frequency_mhz"] == pytest.approx(868.245)
    assert result["frequency_source"] == "override"
    assert result["effective_slot"] is None


def test_custom_bandwidth_and_channel_name_hash():
    custom = config_pb2.Config.LoRaConfig(
        region=2, bandwidth=62, spread_factor=12, coding_rate=8,
        channel_num=2, frequency_offset=0,
    )
    result = legacy_radio_details(custom, "Ops")
    assert result["effective_bandwidth_khz"] == 62.5
    assert result["frequency_mhz"] == pytest.approx(433.09375)
    assert legacy_radio_details(lora("US"), "Ops")["frequency_mhz"] != 906.875


def test_missing_and_unknown_config_does_not_invent_frequency():
    assert radio_details(None, None) == {}
    assert legacy_radio_details(lora("UNSET"), "")["frequency_mhz"] is None
    assert legacy_radio_details(lora("LORA_24"), "")["frequency_mhz"] is None
    assert legacy_radio_details(lora("US"), None)["frequency_mhz"] is None
    assert legacy_radio_details(lora("US", preset="NARROW_FAST"), "")["frequency_mhz"] is None


def test_protobuf_defaults_are_preserved_without_exposing_private_configuration():
    config = localonly_pb2.LocalConfig()
    config.lora.CopyFrom(lora(tx_enabled=False))
    config.device.role = config_pb2.Config.DeviceConfig.CLIENT
    config.security.private_key = b"PRIVATE_SECRET"
    config.network.wifi_psk = "WIFI_SECRET"
    channel = channel_pb2.Channel(index=0, role=channel_pb2.Channel.PRIMARY)
    channel.settings.name = "Ops"
    channel.settings.psk = b"CHANNEL_SECRET"
    interface = SimpleNamespace(
        localNode=SimpleNamespace(localConfig=config, channels=[channel]),
        metadata=mesh_pb2.DeviceMetadata(firmware_version="2.7.test", hw_model=1),
        nodes={"!aaaaaaaa": {}},
    )
    result = interface_details(interface)
    assert result["role"] == "CLIENT"
    assert result["radio"]["tx_enabled"] is False
    assert result["radio"]["channel_num"] == 0
    assert result["radio"]["modem_preset"] == "LONG_FAST"
    assert result["firmware_version"] == "2.7.test"
    assert result["registry_count"] == 1
    assert "SECRET" not in json.dumps(result)
    assert interface_details(SimpleNamespace(localNode=SimpleNamespace(
        localConfig=localonly_pb2.LocalConfig(), channels=[]
    )))["radio"] == {}


@pytest.mark.parametrize("version", [
    "2.8.0.47db0e3", "v2.8.1", "2.10.0", "3.0.0", "custom", "", None,
])
@pytest.mark.parametrize("use_preset", [True, False])
def test_newer_or_unknown_firmware_keeps_reported_fields_without_legacy_derivations(
    version, use_preset,
):
    config = localonly_pb2.LocalConfig()
    config.lora.CopyFrom(lora(
        channel_num=4, bandwidth=62, spread_factor=9, coding_rate=8,
        override_frequency=868.25, frequency_offset=0.01, tx_enabled=False,
    ))
    config.lora.use_preset = use_preset
    channel = channel_pb2.Channel(index=0, role=channel_pb2.Channel.PRIMARY)
    channel.settings.name = ""
    interface = SimpleNamespace(
        localNode=SimpleNamespace(localConfig=config, channels=[channel]),
        metadata=SimpleNamespace(firmware_version=version),
    )
    result = interface_details(interface)
    radio = result["radio"]
    assert result["firmware_version"] == version
    assert radio["region"] == "EU_868"
    assert radio["modem_preset"] == "LONG_FAST"
    assert radio["bandwidth"] == 62
    assert radio["spread_factor"] == 9
    assert radio["coding_rate"] == 8
    assert radio["channel_num"] == 4
    assert radio["tx_enabled"] is False
    assert radio["override_frequency"] == pytest.approx(868.25)
    assert radio["derivation_unavailable"] is True
    assert all(radio[field] is None for field in (
        "frequency_mhz", "frequency_source", "band_min_mhz", "band_max_mhz",
        "effective_slot", "effective_bandwidth_khz", "effective_spread_factor",
        "effective_coding_rate",
    ))
    assert result["channels"][0]["display_name"] is None
    channel.settings.name = "Ops"
    assert interface_details(interface)["channels"][0]["display_name"] == "Ops"


@pytest.mark.parametrize("version", ["2.7.15.567b8ea", "v2.7.11", "2.7.test"])
def test_known_legacy_firmware_keeps_calculated_details(version):
    result = radio_details(lora(), "", firmware_version=version)
    assert result["derivation_unavailable"] is False
    assert result["frequency_mhz"] == pytest.approx(869.525)
    assert result["band_min_mhz"] == 869.4
    assert result["effective_bandwidth_khz"] == 250.0
    assert result["effective_spread_factor"] == 11
    assert result["effective_coding_rate"] == 5
