import os
import sqlite3

import pytest

from meshpi.channels import ChannelBinding, logical_channel_key, public_conversation_id
from meshpi.database import Database
from meshpi.models import (
    ConversationKind,
    Direction,
    Message,
    MessageStatus,
    Node,
    Transport,
)


def message(packet_id=42, kind=ConversationKind.PUBLIC, peer=None):
    return Message(
        packet_id=packet_id,
        timestamp="2026-07-20T12:00:00+00:00",
        from_node="!11112222",
        to_node="!ffffffff" if kind == ConversationKind.PUBLIC else "!710365c8",
        channel=0,
        kind=kind,
        peer_node=peer,
        text="Test",
        direction=Direction.INCOMING,
        transport=Transport.RF,
    )


def test_database_connections_are_closed_after_each_operation(tmp_path, monkeypatch):
    original_connect = sqlite3.connect
    opened = []
    closed = []

    class TrackingConnection(sqlite3.Connection):
        def close(self):
            closed.append(self)
            super().close()

    def tracking_connect(*args, **kwargs):
        kwargs["factory"] = TrackingConnection
        connection = original_connect(*args, **kwargs)
        opened.append(connection)
        return connection

    monkeypatch.setattr("meshpi.database.sqlite3.connect", tracking_connect)
    database = Database(tmp_path / "messages.db")

    database.initialize()
    for _ in range(10):
        database.list_nodes()

    assert len(opened) == 11
    assert closed == opened


@pytest.mark.skipif(os.name != "posix", reason="POSIX-filrettar")
def test_database_is_private_on_posix(tmp_path):
    path = tmp_path / "messages.db"

    Database(path).initialize()

    assert path.stat().st_mode & 0o777 == 0o600


def test_store_retrieve_and_deduplicate_message(tmp_path):
    database = Database(tmp_path / "messages.db")
    database.initialize()
    inserted, message_id = database.insert_message(message())
    assert inserted is True
    assert message_id is not None
    duplicate, _ = database.insert_message(message())
    assert duplicate is False

    rows = database.list_messages("public")
    assert len(rows) == 1
    assert rows[0]["text"] == "Test"
    assert rows[0]["transport"] == "RF"


def test_mark_read_and_conversations(tmp_path):
    database = Database(tmp_path / "messages.db")
    database.initialize()
    database.insert_message(message())
    assert database.conversations()[0]["unread"] == 1
    database.list_messages("public", mark_read=True)
    assert database.conversations()[0]["unread"] == 0


def test_new_message_keeps_arrival_order_when_packet_time_is_old(tmp_path):
    database = Database(tmp_path / "messages.db")
    database.initialize()
    peer = "!11112222"
    first = message(1, ConversationKind.DM, peer)
    first.timestamp = "2026-07-22T11:35:00+00:00"
    first.text = "Første melding"
    second = message(2, ConversationKind.DM, peer)
    second.timestamp = "2026-06-04T02:58:00+00:00"
    second.text = "Ny melding med gammal nodetid"
    database.insert_message(first)
    database.insert_message(second)

    rows = database.list_messages("dm", peer)
    conversation = database.conversations()[0]

    assert [row["text"] for row in rows] == [first.text, second.text]
    assert conversation["last_text"] == second.text
    assert conversation["last_timestamp"] == second.timestamp


def test_dm_storage_is_separate_per_peer(tmp_path):
    database = Database(tmp_path / "messages.db")
    database.initialize()
    first = message(1, ConversationKind.DM, "!11112222")
    second = message(2, ConversationKind.DM, "!33334444")
    second.from_node = "!33334444"
    database.insert_message(first)
    database.insert_message(second)
    assert len(database.list_messages("dm", "!11112222")) == 1
    assert len(database.list_messages("dm", "!33334444")) == 1


def test_public_conversations_are_separate_per_logical_channel(tmp_path):
    database = Database(tmp_path / "messages.db")
    database.initialize()
    first = message(1)
    first.channel = 1
    first.channel_key = "global:ops:123"
    first.conversation_id = public_conversation_id(first.channel_key)
    second = message(2)
    second.channel = 1
    second.channel_key = "local:!aaaaaaaa:1:ops"
    second.conversation_id = public_conversation_id(second.channel_key)

    database.insert_message(first)
    database.insert_message(second)

    conversations = database.conversations()
    assert {item["conversation"] for item in conversations} == {
        first.conversation_id,
        second.conversation_id,
    }
    assert len(
        database.list_messages(
            "public",
            conversation_id=first.conversation_id,
        )
    ) == 1


def test_same_public_packet_has_one_message_and_two_gateway_observations(tmp_path):
    database = Database(tmp_path / "messages.db")
    database.initialize()
    first = message(7)
    first.channel_key = "global:ops:123"
    first.conversation_id = public_conversation_id(first.channel_key)
    first.local_node_id = "!aaaaaaaa"
    first.gateway_profile_id = "tcp-a"
    second = message(7)
    second.channel_key = first.channel_key
    second.conversation_id = first.conversation_id
    second.local_node_id = "!bbbbbbbb"
    second.gateway_profile_id = "serial-b"

    assert database.insert_message(first)[0] is True
    assert database.insert_message(second)[0] is False

    with sqlite3.connect(database.path) as connection:
        assert connection.execute("SELECT COUNT(*) FROM messages").fetchone()[0] == 1
        assert (
            connection.execute(
                "SELECT COUNT(*) FROM message_observations"
            ).fetchone()[0]
            == 2
        )
    assert database.list_messages(
        "public",
        conversation_id=first.conversation_id,
    )[0]["observation_count"] == 2


def test_packet_id_collision_on_two_channels_is_not_deduplicated(tmp_path):
    database = Database(tmp_path / "messages.db")
    database.initialize()
    first = message(9)
    first.channel_key = "global:first:1"
    first.conversation_id = public_conversation_id(first.channel_key)
    second = message(9)
    second.channel_key = "global:second:2"
    second.conversation_id = public_conversation_id(second.channel_key)

    assert database.insert_message(first)[0] is True
    assert database.insert_message(second)[0] is True


def test_channel_bindings_use_global_id_across_nodes_and_local_fallback(tmp_path):
    database = Database(tmp_path / "messages.db")
    database.initialize()
    shared_a = logical_channel_key("!aaaaaaaa", 1, "Ops", 1234)
    shared_b = logical_channel_key("!bbbbbbbb", 3, "Ops", 1234)
    local_a = logical_channel_key("!aaaaaaaa", 2, "Lokal", None)
    local_b = logical_channel_key("!bbbbbbbb", 2, "Lokal", None)
    assert shared_a == shared_b
    assert local_a != local_b
    assert logical_channel_key("!bbbbbbbb", 3, "ops", 1234) != shared_a
    assert logical_channel_key("!bbbbbbbb", 3, "Nytt namn", 1234) != shared_a

    database.sync_channel_bindings(
        "!aaaaaaaa",
        "tcp-a",
        [
            ChannelBinding(
                "!aaaaaaaa",
                1,
                shared_a,
                "Ops",
                "SECONDARY",
                1234,
                "tcp-a",
            )
        ],
    )
    rows = database.list_channel_bindings("!aaaaaaaa", active_only=True)
    assert rows[0]["conversation"] == public_conversation_id(shared_a)
    assert rows[0]["display_name"] == "Ops"

    largest_id = (1 << 32) - 1
    largest_key = logical_channel_key("!aaaaaaaa", 4, "Stor", largest_id)
    database.sync_channel_bindings(
        "!aaaaaaaa",
        "tcp-a",
        [
            ChannelBinding(
                "!aaaaaaaa",
                4,
                largest_key,
                "Stor",
                "SECONDARY",
                largest_id,
                "tcp-a",
            )
        ],
    )
    assert database.list_channel_bindings("!aaaaaaaa", active_only=True)[0][
        "meshtastic_id"
    ] == str(largest_id)


def test_legacy_message_schema_is_migrated_without_data_loss(tmp_path):
    path = tmp_path / "legacy.db"
    with sqlite3.connect(path) as connection:
        connection.executescript(
            """
            CREATE TABLE messages (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                packet_id INTEGER,
                timestamp TEXT NOT NULL,
                from_node TEXT,
                to_node TEXT,
                channel INTEGER,
                kind TEXT NOT NULL,
                peer_node TEXT,
                text TEXT NOT NULL,
                direction TEXT NOT NULL,
                transport TEXT NOT NULL,
                rssi INTEGER,
                snr REAL,
                hop_limit INTEGER,
                hop_start INTEGER,
                want_ack INTEGER NOT NULL DEFAULT 0,
                status TEXT NOT NULL,
                raw_metadata TEXT,
                is_read INTEGER NOT NULL DEFAULT 0
            );
            """
        )
        connection.execute(
            """
            INSERT INTO messages (
                packet_id, timestamp, from_node, to_node, channel, kind,
                text, direction, transport, status, raw_metadata
            ) VALUES (
                42, '2026-07-20T12:00:00+00:00', '!11112222',
                '!ffffffff', 0, 'public', 'Historikk', 'inn', 'RF',
                'motteken', '{"gateway_id":"tcp-gammal"}'
            )
            """
        )

    database = Database(path)
    database.initialize()

    row = database.list_messages("public")[0]
    assert row["text"] == "Historikk"
    assert row["conversation_id"] == "channel:legacy:tcp-gammal:0"
    assert row["channel_key"] == "legacy:tcp-gammal:0"
    assert row["gateway_profile_id"] == "tcp-gammal"
    with sqlite3.connect(path) as connection:
        assert connection.execute("PRAGMA user_version").fetchone()[0] == 3
        indexes = {
            row[1] for row in connection.execute("PRAGMA index_list(messages)")
        }
        assert "messages_conversation_time" in indexes


def test_migration_backfills_local_node_for_outgoing_ack_scope(tmp_path):
    path = tmp_path / "version-2.db"
    database = Database(path)
    database.initialize()
    with sqlite3.connect(path) as connection:
        connection.execute("PRAGMA user_version=2")
        connection.execute(
            """
            INSERT INTO messages (
                packet_id, timestamp, from_node, to_node, channel, kind,
                peer_node, text, direction, transport, want_ack, status,
                is_read, conversation_id, received_at
            ) VALUES (
                77, '2026-07-20T12:00:00+00:00', '!aaaaaaaa',
                '!11112222', 0, 'dm', '!11112222', 'Ventande', 'ut',
                'Ukjend', 1, 'sendt', 1, '!11112222',
                '2026-07-20T12:00:00+00:00'
            )
            """
        )

    database.initialize()

    assert database.outgoing_message(77, "!aaaaaaaa")["local_node_id"] == "!aaaaaaaa"


def test_provisional_rebind_merges_duplicate_observations(tmp_path):
    database = Database(tmp_path / "messages.db")
    database.initialize()
    provisional = message(42)
    provisional.local_node_id = "!aaaaaaaa"
    provisional.channel = None
    provisional.channel_key = "provisional:!aaaaaaaa:2"
    provisional.conversation_id = "channel:provisional:!aaaaaaaa:2"
    confirmed = message(42)
    confirmed.local_node_id = "!aaaaaaaa"
    confirmed.channel = 2
    confirmed.gateway_profile_id = "tcp-b"
    confirmed.channel_key = "global:Ops:1234"
    confirmed.conversation_id = "channel:global:Ops:1234"
    assert database.insert_message(provisional)[0] is True
    assert database.insert_message(confirmed)[0] is True

    rebound = database.rebind_provisional_channel(
        "!aaaaaaaa",
        2,
        provisional.channel_key,
        confirmed.channel_key,
    )
    rows = database.list_messages(
        "public",
        conversation_id=confirmed.conversation_id,
    )

    assert rebound == 1
    assert len(rows) == 1
    assert rows[0]["channel"] == 2
    assert rows[0]["observation_count"] == 2


def test_version_three_recreates_missing_message_indexes(tmp_path):
    path = tmp_path / "messages.db"
    database = Database(path)
    database.initialize()
    with sqlite3.connect(path) as connection:
        connection.execute("DROP INDEX messages_packet_context_identity")
        connection.execute("DROP INDEX messages_conversation_time")
        assert connection.execute("PRAGMA user_version").fetchone()[0] == 3

    database.initialize()

    with sqlite3.connect(path) as connection:
        indexes = {
            row[1] for row in connection.execute("PRAGMA index_list(messages)")
        }
    assert {
        "messages_packet_context_identity",
        "messages_conversation_time",
    } <= indexes


def test_archived_dm_is_hidden_until_a_new_message_arrives(tmp_path):
    database = Database(tmp_path / "messages.db")
    database.initialize()
    database.insert_message(message(1, ConversationKind.DM, "!11112222"))

    database.archive_conversation("!11112222")
    assert database.conversations() == []
    assert len(database.list_messages("dm", "!11112222")) == 1

    database.insert_message(message(2, ConversationKind.DM, "!11112222"))
    assert database.conversations()[0]["conversation"] == "!11112222"


def test_conversation_can_be_unarchived_without_new_message(tmp_path):
    database = Database(tmp_path / "messages.db")
    database.initialize()
    database.insert_message(message(1, ConversationKind.DM, "!11112222"))
    database.archive_conversation("!11112222")

    database.unarchive_conversation("!11112222")

    assert database.conversations()[0]["conversation"] == "!11112222"


def test_archiving_one_dm_route_does_not_hide_another_route(tmp_path):
    database = Database(tmp_path / "messages.db")
    database.initialize()
    first = message(1, ConversationKind.DM, "!11112222")
    first.local_node_id = "!aaaaaaaa"
    first.channel_key = "global:ops:1"
    first.conversation_id = "dm:!aaaaaaaa:!11112222:global:ops:1"
    second = message(2, ConversationKind.DM, "!11112222")
    second.local_node_id = "!bbbbbbbb"
    second.channel_key = "global:ops:1"
    second.conversation_id = "dm:!bbbbbbbb:!11112222:global:ops:1"
    database.insert_message(first)
    database.insert_message(second)

    database.archive_conversation("!11112222", first.conversation_id)

    assert [item["conversation"] for item in database.conversations()] == [
        second.conversation_id
    ]


def test_messages_can_be_deleted_by_scope(tmp_path):
    database = Database(tmp_path / "messages.db")
    database.initialize()
    database.insert_message(message(1))
    routed = message(2, ConversationKind.DM, "!11112222")
    routed.local_node_id = "!aaaaaaaa"
    routed.channel_key = "global:Privat:2"
    routed.conversation_id = "dm:!aaaaaaaa:!11112222:global:Privat:2"
    database.insert_message(routed)
    database.archive_conversation("!11112222")
    database.archive_conversation("!11112222", routed.conversation_id)

    assert database.delete_messages("public") == 1
    assert database.list_messages("public") == []
    assert len(database.list_messages("dm", "!11112222")) == 1

    assert database.delete_messages("all") == 1
    assert database.list_messages("dm", "!11112222") == []
    database.unarchive_conversation("!11112222")
    database.unarchive_conversation("!11112222", routed.conversation_id)
    assert database.conversations() == []


def test_traceroute_history_is_upserted_and_returned_in_time_order(tmp_path):
    database = Database(tmp_path / "messages.db")
    database.initialize()
    first = {
        "action_id": "trace-1",
        "action": "traceroute",
        "node_id": "!11112222",
        "status": "started",
        "started_at": "2026-07-21T12:00:00+00:00",
        "packet_id": 42,
    }
    second = {
        "action_id": "trace-2",
        "action": "traceroute",
        "node_id": "!11112222",
        "status": "failed",
        "started_at": "2026-07-21T12:01:00+00:00",
        "finished_at": "2026-07-21T12:01:30+00:00",
        "error": "Ingen rute",
    }
    database.upsert_node_action(first)
    database.upsert_node_action(second)
    database.upsert_node_action(
        first
        | {
            "status": "completed",
            "finished_at": "2026-07-21T12:00:10+00:00",
            "result": {"forward": [{"node_id": "!11112222", "snr": 6.0}]},
        }
    )

    history = database.list_node_actions("!11112222")

    assert [item["action_id"] for item in history] == ["trace-1", "trace-2"]
    assert history[0]["status"] == "completed"
    assert history[0]["result"]["forward"][0]["snr"] == 6.0
    assert history[1]["error"] == "Ingen rute"


def test_started_traceroute_can_be_marked_as_interrupted(tmp_path):
    database = Database(tmp_path / "messages.db")
    database.initialize()
    database.upsert_node_action(
        {
            "action_id": "trace-started",
            "action": "traceroute",
            "node_id": "!11112222",
            "status": "started",
            "started_at": "2026-07-21T12:00:00+00:00",
        }
    )

    assert database.fail_started_node_actions("Tenesta stoppa") == 1
    saved = database.list_node_actions("!11112222")[0]
    assert saved["status"] == "failed"
    assert saved["error"] == "Tenesta stoppa"
    assert saved["finished_at"] is not None


def test_update_outgoing_status(tmp_path):
    database = Database(tmp_path / "messages.db")
    database.initialize()
    outgoing = message()
    outgoing.direction = Direction.OUTGOING
    outgoing.status = MessageStatus.QUEUED
    outgoing.is_read = True
    database.insert_message(outgoing)
    assert database.update_message_status(42, MessageStatus.ACKNOWLEDGED)
    assert database.list_messages("public")[0]["status"] == "ACK"
    assert database.update_message_status(42, MessageStatus.DELIVERED)
    assert database.list_messages("public")[0]["status"] == "levert"
    assert not database.update_message_status(42, MessageStatus.ACKNOWLEDGED)
    assert database.list_messages("public")[0]["status"] == "levert"


def test_initialize_migrates_old_confirmation_to_plain_ack(tmp_path):
    database = Database(tmp_path / "messages.db")
    database.initialize()
    outgoing = message()
    outgoing.direction = Direction.OUTGOING
    outgoing.status = MessageStatus.QUEUED
    outgoing.is_read = True
    database.insert_message(outgoing)
    with database._connect() as connection:
        connection.execute(
            "UPDATE messages SET status = 'stadfesta' WHERE packet_id = 42"
        )

    database.initialize()

    assert database.list_messages("public")[0]["status"] == "ACK"


def test_nodes_are_upserted_and_sorted(tmp_path):
    database = Database(tmp_path / "messages.db")
    database.initialize()
    database.upsert_node(
        Node(
            node_id="!11112222",
            long_name="Zulu",
            last_heard=100,
            can_receive_dm=True,
        )
    )
    database.upsert_node(
        Node(node_id="!33334444", long_name="Alfa", last_heard=200, is_local=True)
    )
    nodes = database.list_nodes(sort="name")
    assert [item["long_name"] for item in nodes] == ["Alfa", "Zulu"]
    assert nodes[0]["is_local"] is True
    assert database.get_node("!11112222")["can_receive_dm"] is True
    assert database.list_nodes(search="Zulu")[0]["node_id"] == "!11112222"


def test_older_gateway_registry_does_not_replace_newer_node_data(tmp_path):
    database = Database(tmp_path / "nodes.db")
    database.initialize()
    database.upsert_node(
        Node(
            node_id="!11112222",
            long_name="Nytt namn",
            last_heard=200,
            battery_level=80,
            snr=9.0,
        )
    )
    database.upsert_node(
        Node(
            node_id="!11112222",
            long_name="Gamalt namn",
            last_heard=100,
            battery_level=20,
            snr=1.0,
        )
    )

    node = database.get_node("!11112222")
    assert node["long_name"] == "Nytt namn"
    assert node["last_heard"] == 200
    assert node["battery_level"] == 80
    assert node["snr"] == 9.0


def test_gateway_registry_without_timestamp_only_fills_unknown_node_fields(
    tmp_path,
):
    database = Database(tmp_path / "nodes.db")
    database.initialize()
    database.upsert_node(
        Node(
            node_id="!11112222",
            long_name="Tidsfesta namn",
            last_heard=200,
            battery_level=80,
        )
    )
    database.upsert_node(
        Node(
            node_id="!11112222",
            long_name="Ukjend alder",
            short_name="NY",
            last_heard=None,
            battery_level=20,
        )
    )

    node = database.get_node("!11112222")
    assert node["long_name"] == "Tidsfesta namn"
    assert node["short_name"] == "NY"
    assert node["battery_level"] == 80


def test_node_search_treats_sql_wildcards_as_text(tmp_path):
    database = Database(tmp_path / "db.sqlite")
    database.initialize()
    database.upsert_node(Node(node_id="!11112222", long_name="Prosent%node"))
    database.upsert_node(Node(node_id="!33334444", long_name="Under_strek"))
    database.upsert_node(Node(node_id="!55556666", long_name="Vanleg node"))

    assert [item["node_id"] for item in database.list_nodes(search="%")] == [
        "!11112222"
    ]
    assert [item["node_id"] for item in database.list_nodes(search="_")] == [
        "!33334444"
    ]


def test_telemetry_and_positions_are_deduplicated_and_summarized(tmp_path):
    database = Database(tmp_path / "observations.db")
    database.initialize()
    common = {
        "node_id": "!11112222",
        "sample_time": "2026-07-26T12:00:00+00:00",
        "received_at": "2026-07-26T12:00:01+00:00",
        "packet_id": 42,
        "transport": "RF",
        "rssi": -90,
        "snr": 7.5,
        "hop_limit": 3,
        "hop_start": 3,
        "gateway_profile_id": "tcp-home",
        "gateway_node_id": "!aaaaaaaa",
        "gateway_transport": "tcp",
    }
    telemetry = common | {
        "dedupe_key": "telemetry-42",
        "kind": "device",
        "metrics": {"batteryLevel": 75, "channelUtilization": 12.5},
    }
    position = common | {
        "dedupe_key": "position-42",
        "latitude": 60.123,
        "longitude": 5.456,
        "altitude_msl": 104,
        "altitude_hae": None,
        "geoidal_separation": None,
        "pdop": None,
        "hdop": None,
        "vdop": None,
        "gps_accuracy_mm": 3000,
        "ground_speed": None,
        "ground_track": None,
        "fix_quality": None,
        "fix_type": 3,
        "sats_in_view": 9,
        "location_source": "LOC_INTERNAL",
        "altitude_source": "ALT_INTERNAL",
        "precision_bits": 32,
        "metadata": {},
    }

    assert database.insert_telemetry(telemetry) is True
    assert database.insert_telemetry(telemetry) is False
    assert database.insert_position(position) is True
    assert database.insert_position(position) is False

    assert database.list_telemetry("!11112222")[0]["metrics"]["batteryLevel"] == 75
    assert database.list_positions("!11112222")[0]["altitude_msl"] == 104
    summary = database.node_observation_summary("!11112222")
    assert summary["counts"] == {
        "telemetry": 1,
        "positions": 1,
        "traceroutes": 0,
    }
    assert summary["latest_telemetry"]["device"]["gateway_node_id"] == "!aaaaaaaa"

    older = common | {
        "packet_id": 43,
        "sample_time": "2025-07-26T12:00:00+00:00",
        "received_at": "2026-07-26T12:01:00+00:00",
    }
    database.insert_telemetry(
        older
        | {
            "dedupe_key": "telemetry-43",
            "kind": "device",
            "metrics": {"batteryLevel": 10},
        }
    )
    database.insert_position(
        older
        | {
            "dedupe_key": "position-43",
            "latitude": 61.0,
            "longitude": 6.0,
            "altitude_msl": 5,
        }
    )

    summary = database.node_observation_summary("!11112222")
    assert summary["latest_telemetry"]["device"]["metrics"]["batteryLevel"] == 75
    assert summary["latest_position"]["altitude_msl"] == 104


def test_observation_retention_prunes_expired_samples_on_initialize(tmp_path):
    path = tmp_path / "retention.db"
    database = Database(path, observation_retention_days=1)
    database.initialize()
    database.insert_telemetry(
        {
            "dedupe_key": "expired-telemetry",
            "node_id": "!11112222",
            "kind": "device",
            "sample_time": "2000-01-01T00:00:00+00:00",
            "received_at": "2000-01-01T00:00:01+00:00",
            "metrics": {"batteryLevel": 50},
        }
    )
    assert len(database.list_telemetry("!11112222")) == 1

    database.initialize()

    assert database.list_telemetry("!11112222") == []
