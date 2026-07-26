from meshpi.observations import parse_position_packet, parse_telemetry_packet


def test_telemetry_packet_builds_one_sample_per_metric_group():
    samples = parse_telemetry_packet(
        {
            "id": 123,
            "fromId": "!11112222",
            "rxTime": 1_700_000_000,
            "rxRssi": -91,
            "rxSnr": 7.5,
            "viaMqtt": True,
            "decoded": {
                "portnum": "TELEMETRY_APP",
                "telemetry": {
                    "time": 1_699_999_999,
                    "deviceMetrics": {
                        "batteryLevel": 75,
                        "voltage": 4.1,
                        "channelUtilization": 12.5,
                    },
                    "environmentMetrics": {
                        "temperature": 18.75,
                        "relativeHumidity": 72.0,
                    },
                },
            },
        }
    )

    assert [sample["kind"] for sample in samples] == ["device", "environment"]
    assert samples[0]["node_id"] == "!11112222"
    assert samples[0]["sample_time"] == "2023-11-14T22:13:19+00:00"
    assert samples[0]["metrics"]["batteryLevel"] == 75
    assert samples[0]["transport"] == "MQTT"
    assert samples[0]["rssi"] == -91
    assert samples[0]["dedupe_key"] != samples[1]["dedupe_key"]


def test_position_packet_converts_integer_coordinates_and_accuracy_fields():
    position = parse_position_packet(
        {
            "id": 456,
            "from": 0x11112222,
            "rxTime": 1_700_000_000,
            "transportMechanism": "TRANSPORT_LORA",
            "decoded": {
                "portnum": "POSITION_APP",
                "position": {
                    "latitudeI": 601234567,
                    "longitudeI": 51234567,
                    "altitude": 104,
                    "altitudeHae": 147,
                    "timestamp": 1_699_999_990,
                    "PDOP": 135,
                    "gpsAccuracy": 3000,
                    "satsInView": 9,
                    "precisionBits": 32,
                },
            },
        }
    )

    assert position is not None
    assert position["node_id"] == "!11112222"
    assert position["latitude"] == 60.1234567
    assert position["longitude"] == 5.1234567
    assert position["altitude_msl"] == 104
    assert position["altitude_hae"] == 147
    assert position["pdop"] == 1.35
    assert position["gps_accuracy_mm"] == 3000
    assert position["transport"] == "RF"


def test_position_packet_requires_valid_coordinates():
    assert (
        parse_position_packet(
            {
                "fromId": "!11112222",
                "decoded": {
                    "portnum": "POSITION_APP",
                    "position": {"altitude": 12},
                },
            }
        )
        is None
    )


def test_future_payload_timestamp_falls_back_to_gateway_receive_time():
    samples = parse_telemetry_packet(
        {
            "fromId": "!11112222",
            "rxTime": 1_700_000_000,
            "decoded": {
                "portnum": "TELEMETRY_APP",
                "telemetry": {
                    "time": 4_102_444_800,
                    "deviceMetrics": {"batteryLevel": 75},
                },
            },
        }
    )

    assert samples[0]["sample_time"] == "2023-11-14T22:13:20+00:00"
