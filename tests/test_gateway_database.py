import sqlite3
from datetime import datetime, timedelta, timezone

import pytest

from meshpi.database import DATABASE_SCHEMA_VERSION, Database
from meshpi.gateway_database import GatewayDatabase
from meshpi.models import ConversationKind, Direction, Message, MessageStatus, Node, Transport

A = "!11111111"
B = "!22222222"
PEER = "!33333333"
NOW = datetime(2026, 10, 25, 12, tzinfo=timezone.utc)


def message(gateway=A, packet=1, *, age=40, direction=Direction.INCOMING,
            status=MessageStatus.RECEIVED):
    return Message(
        packet_id=packet, timestamp="1990-01-01T00:00:00+00:00", from_node=PEER,
        to_node=gateway, channel=0, kind=ConversationKind.DM, peer_node=PEER,
        text=f"Gateway {gateway} packet {packet}", direction=direction, transport=Transport.RF,
        local_node_id=gateway, received_at=(NOW - timedelta(days=age)).isoformat(), status=status,
        want_ack=direction == Direction.OUTGOING,
    )


def observations(database, gateway=A, packet=1, age=40):
    common = {
        "dedupe_key": str(packet), "node_id": PEER, "gateway_node_id": gateway,
        "sample_time": "1990-01-01T00:00:00+00:00",
        "received_at": (NOW - timedelta(days=age)).isoformat(),
    }
    database.insert_telemetry(common | {"kind": "device", "metrics": {"batteryLevel": 50}})
    database.insert_position(common | {"latitude": 60.0, "longitude": 5.0})


def action(database, gateway=A, action_id="finished", status="completed", age=40):
    database.upsert_node_action({
        "local_node_id": gateway, "action_id": action_id, "action": "traceroute",
        "node_id": PEER, "status": status,
        "started_at": (NOW - timedelta(days=age, seconds=10)).isoformat(),
        "finished_at": (NOW - timedelta(days=age)).isoformat() if status != "started" else None,
    })


def test_physical_migration_preserves_source_unknown_records_and_all_scoped_tables(tmp_path):
    source = Database(tmp_path / "meshpi.db", observation_retention_days=0)
    source.initialize()
    for gateway in (A, B):
        source.upsert_node(Node(node_id=PEER, long_name=gateway), local_node_id=gateway)
        source.insert_message(message(gateway))
        observations(source, gateway)
        action(source, gateway, action_id=gateway)
    with source._connect() as connection:
        connection.execute(
            "INSERT INTO legacy_unscoped_records VALUES (1,'nodes','unknown','unknown',?,?)",
            (NOW.isoformat(), '{"precious":"unassigned"}'),
        )
    before = list(source.export_records("test"))
    database = GatewayDatabase(source.path)
    database.initialize()
    assert list(source.export_records("test"))[1:] == before[1:]
    for gateway in (A, B):
        assert database.gateway_path(gateway).exists()
        assert database.get_node(PEER, local_node_id=gateway)["long_name"] == gateway
        assert len(database.list_messages("dm", PEER, local_node_id=gateway)) == 1
        assert database.history_settings(gateway) == {
            "local_node_id": gateway, "retention_days": 0, "needs_review": True,
            "max_messages": 50000,
            "counts": dict.fromkeys(
                ("messages", "telemetry_samples", "positions", "node_actions"), 1,
            ),
        }
        with sqlite3.connect(database.gateway_path(gateway)) as connection:
            assert connection.execute(
                "SELECT local_node_id FROM messages"
            ).fetchall() == [(gateway,)]
            assert connection.execute(
                "SELECT COUNT(*) FROM message_observations"
            ).fetchone()[0] == 1
            assert connection.execute(
                "SELECT COUNT(*) FROM legacy_unscoped_records"
            ).fetchone()[0] == 0
    with sqlite3.connect(database.snapshot_path) as connection:
        assert connection.execute("SELECT payload FROM legacy_unscoped_records").fetchone()[0]
    # Restart does not replay a deleted row from the retained migration snapshot.
    database.cleanup_history(database.preview_history_cleanup(A, scope="history"))
    restarted = GatewayDatabase(source.path)
    restarted.initialize()
    assert restarted.history_settings(A)["counts"]["messages"] == 0
    assert restarted.history_settings(B)["counts"]["messages"] == 1


def test_new_gateways_share_file_across_profiles_and_export_only_current_scope(tmp_path):
    database = GatewayDatabase(tmp_path / "meshpi.db")
    first = message()
    first.gateway_profile_id = "usb"
    database.insert_message(first)
    second = message(packet=2)
    second.gateway_profile_id = "tcp"
    database.insert_message(second)
    database.insert_message(message(B))
    assert database.history_settings(A)["retention_days"] == 30
    assert database.history_settings(A)["needs_review"] is False
    assert len(list(database.directory.glob("[0-9a-f]*.sqlite3"))) == 2
    assert not database.path.exists()
    exported = list(database.export_records("test", local_node_id=A))
    assert exported[0]["local_node_id"] == A
    assert exported[0]["history_settings"]["retention_days"] == 30
    rows = [r["data"] for r in exported if r.get("table") == "messages"]
    assert {r["gateway_profile_id"] for r in rows} == {"usb", "tcp"}
    assert {r["local_node_id"] for r in rows} == {A}
    with pytest.raises(ValueError, match="gateway"):
        database.list_messages("dm", PEER)
    with pytest.raises(ValueError):
        database.for_gateway("../../outside")


def test_retention_uses_received_time_boundary_and_preserves_malformed_and_active(tmp_path):
    database = GatewayDatabase(tmp_path / "meshpi.db")
    for packet, age in ((1, 31), (2, 30), (3, 1)):
        database.insert_message(message(packet=packet, age=age))
        observations(database, packet=packet, age=age)
        action(database, action_id=str(packet), age=age)
    database.insert_message(message(packet=4, direction=Direction.OUTGOING,
                                    status=MessageStatus.QUEUED))
    action(database, action_id="running", status="started")
    database.insert_message(message(packet=5))
    with database.for_gateway(A)._connect() as connection:
        connection.execute("UPDATE messages SET received_at='not a date' WHERE packet_id=5")
    database.maintain_history(now=NOW)
    assert database.history_settings(A)["counts"] == {
        "messages": 4, "telemetry_samples": 2, "positions": 2, "node_actions": 3,
    }
    rows = database.list_messages("dm", PEER, local_node_id=A)
    assert {row["packet_id"] for row in rows} == {2, 3, 4, 5}


def test_cleanup_snapshot_excludes_new_arrivals_completed_actions_and_other_gateway(tmp_path):
    database = GatewayDatabase(tmp_path / "meshpi.db")
    for gateway in (A, B):
        database.insert_message(message(gateway))
        observations(database, gateway)
        action(database, gateway, action_id=gateway)
        database.upsert_node(Node(node_id=PEER), local_node_id=gateway)
    action(database, action_id="pending", status="started")
    preview = database.preview_history_cleanup(A, scope="history")
    database.insert_message(message(packet=2))
    observations(database, packet=2)
    action(database, action_id="pending", status="completed")
    assert database.cleanup_history(preview) == dict.fromkeys(
        ("messages", "telemetry_samples", "positions", "node_actions"), 1,
    )
    assert database.history_settings(A)["counts"] == database.history_settings(B)["counts"]
    assert len(database.list_nodes(local_node_id=A)) == 1
    with database.for_gateway(A)._connect() as connection:
        assert connection.execute("SELECT COUNT(*) FROM message_observations").fetchone()[0] == 1
    assert not any(database.cleanup_history(preview).values())


def test_messages_only_cleanup_and_policy_zero_preserve_observations(tmp_path):
    database = GatewayDatabase(tmp_path / "meshpi.db")
    database.insert_message(message())
    observations(database, age=2000)
    database.set_history_policy(A, 0)
    # Old 365-day cleanup must not run, including after 100 observation inserts/restart.
    for packet in range(2, 103):
        observations(database, packet=packet, age=2000)
    database.maintain_history(now=NOW)
    database = GatewayDatabase(database.path)
    database.initialize()
    assert database.history_settings(A)["counts"]["positions"] == 102
    deleted = database.cleanup_history(database.preview_history_cleanup(A, scope="messages"))
    assert deleted == {"messages": 1, "telemetry_samples": 0, "positions": 0, "node_actions": 0}


@pytest.mark.parametrize("days", [-1, 36501, 1.5, True, "30", None])
def test_invalid_policy_values_rejected(tmp_path, days):
    database = GatewayDatabase(tmp_path / "meshpi.db")
    with pytest.raises(ValueError):
        database.set_history_policy(A, days)
    assert not database.directory.exists()


def test_future_schema_rejected_without_touching_original_or_creating_files(tmp_path):
    path = tmp_path / "future.db"
    with sqlite3.connect(path) as connection:
        connection.execute(f"PRAGMA user_version={DATABASE_SCHEMA_VERSION + 1}")
    before = path.read_bytes()
    database = GatewayDatabase(path)
    with pytest.raises(ValueError):
        database.initialize()
    assert path.read_bytes() == before
    assert not database.directory.exists()


def test_migration_failure_is_atomic_and_can_resume(tmp_path, monkeypatch):
    source = Database(tmp_path / "meshpi.db")
    source.initialize()
    source.insert_message(message())
    original = GatewayDatabase._copy_rows

    def broken(*args):
        raise OSError("test failure")

    monkeypatch.setattr(GatewayDatabase, "_copy_rows", staticmethod(broken))
    database = GatewayDatabase(source.path)
    with pytest.raises(OSError, match="test failure"):
        database.initialize()
    assert not database.gateway_path(A).exists()
    assert source.list_messages("dm", PEER, local_node_id=A)
    monkeypatch.setattr(GatewayDatabase, "_copy_rows", staticmethod(original))
    database.initialize()
    assert database.history_settings(A)["counts"]["messages"] == 1


def test_legacy_write_after_rollback_stops_safely_without_losing_either_history(tmp_path):
    source = Database(tmp_path / "meshpi.db")
    source.initialize()
    source.insert_message(message())
    database = GatewayDatabase(source.path)
    database.initialize()
    database.insert_message(message(packet=2))
    source.insert_message(message(packet=3))
    with pytest.raises(ValueError, match="gamle databasen er endra"):
        GatewayDatabase(source.path).initialize()
    assert len(source.list_messages("dm", PEER, local_node_id=A)) == 2
    assert database.history_settings(A)["counts"]["messages"] == 2


def test_policy_disabled_after_maintenance_preview_does_not_delete(tmp_path):
    database = GatewayDatabase(tmp_path / "meshpi.db")
    database.insert_message(message())
    preview = database.preview_history_cleanup(A, scope="history", older_than_days=30, now=NOW)
    preview["_policy_days"] = 30
    database.set_history_policy(A, 0)
    assert not any(database.cleanup_history(preview).values())
    assert database.history_settings(A)["counts"]["messages"] == 1


def test_cleanup_retains_inflight_send_but_removes_completed_public_handoff(tmp_path):
    database = GatewayDatabase(tmp_path / "meshpi.db")
    inflight = message(packet=1, direction=Direction.OUTGOING, status=MessageStatus.UNCERTAIN)
    database.insert_message(inflight)
    public = message(packet=2, direction=Direction.OUTGOING, status=MessageStatus.QUEUED)
    public.want_ack = False
    public.kind = ConversationKind.PUBLIC
    public.to_node = "!ffffffff"
    public.channel_key = f"local:{A}:0:"
    database.insert_message(public)
    result = database.cleanup_history(database.preview_history_cleanup(A, scope="history"))
    assert result["messages"] == 1
    assert database.history_settings(A)["counts"]["messages"] == 1
    assert database.list_messages("dm", PEER, local_node_id=A)[0]["packet_id"] == 1


@pytest.mark.parametrize("timestamp", [None, "", "2020", "now", "2020-02-30T12:00:00+00:00",
                                      "2020-01-01T12:00:00"])
def test_age_cleanup_never_guesses_missing_or_malformed_local_times(tmp_path, timestamp):
    database = GatewayDatabase(tmp_path / "meshpi.db")
    database.insert_message(message())
    with database.for_gateway(A)._connect() as connection:
        connection.execute("UPDATE messages SET received_at=?", (timestamp,))
    preview = database.preview_history_cleanup(A, older_than_days=7, now=NOW)
    assert preview["counts"]["messages"] == 0
    # Explicit all-history deletion is allowed to remove undated history.
    assert database.preview_history_cleanup(A)["counts"]["messages"] == 1


def test_safety_cap_keeps_unresolved_sends_and_counts_completed_history_per_gateway(tmp_path):
    database = Database(tmp_path / "cap.db", max_messages=1000)
    database.initialize()
    for packet, status in ((1, MessageStatus.UNCERTAIN), (2, MessageStatus.QUEUED)):
        database.insert_message(message(packet=packet, direction=Direction.OUTGOING, status=status))
    with database._connect() as connection:
        connection.executemany(
            "INSERT INTO messages(timestamp,kind,text,direction,transport,status,local_node_id) "
            "VALUES (?,'dm','old','inn','RF','motteken',?)",
            [(NOW.isoformat(), A)] * 1100,
        )
    database.initialize()
    with database._connect() as connection:
        assert connection.execute("SELECT COUNT(*) FROM messages").fetchone()[0] == 1002
        assert connection.execute(
            "SELECT packet_id FROM messages WHERE direction='ut' ORDER BY id"
        ).fetchall()[0][0] == 1


def test_unscoped_legacy_messages_never_assigned_to_first_connected_gateway(tmp_path):
    source = Database(tmp_path / "meshpi.db")
    source.initialize()
    source.insert_message(message(A))
    with source._connect() as connection:
        connection.execute(
            "INSERT INTO messages(timestamp,kind,text,direction,transport,status,"
            "from_node,to_node) "
            "VALUES (?,'dm','unknown gateway','inn','RF','motteken',?,?)",
            (NOW.isoformat(), PEER, A),
        )
    database = GatewayDatabase(source.path)
    database.initialize()
    assert database.history_settings(A)["counts"]["messages"] == 1
    assert database.history_settings(B)["counts"]["messages"] == 0
    with sqlite3.connect(database.snapshot_path) as connection:
        assert connection.execute(
            "SELECT text FROM messages WHERE local_node_id IS NULL"
        ).fetchone()[0] == "unknown gateway"
    database.rebind_legacy_primary_dms(A, 0, f"local:{A}:0:")
    assert database.history_settings(A)["counts"]["messages"] == 1


def test_cleanup_failure_rolls_back_every_history_table(tmp_path):
    database = GatewayDatabase(tmp_path / "meshpi.db")
    database.insert_message(message())
    observations(database)
    action(database)
    before = database.history_settings(A)["counts"]
    preview = database.preview_history_cleanup(A, scope="history")
    with database.for_gateway(A)._connect() as connection:
        connection.execute(
            "CREATE TRIGGER stop_delete BEFORE DELETE ON positions "
            "BEGIN SELECT RAISE(ABORT,'simulated storage failure'); END"
        )
    with pytest.raises(sqlite3.IntegrityError, match="simulated storage failure"):
        database.cleanup_history(preview)
    assert database.history_settings(A)["counts"] == before
    with database.for_gateway(A)._connect() as connection:
        assert connection.execute("SELECT COUNT(*) FROM message_observations").fetchone()[0] == 1


def test_manual_preview_cap_is_shared_across_tables_and_never_deletes(tmp_path, monkeypatch):
    monkeypatch.setattr("meshpi.gateway_database.MAX_CLEANUP_RECORDS", 3)
    database = GatewayDatabase(tmp_path / "meshpi.db")
    database.insert_message(message())
    observations(database)
    # Exactly the limit is accepted, represented without Python lists per identity.
    preview = database.preview_history_cleanup(A, scope="history")
    assert sum(preview["counts"].values()) == 3
    assert sum(len(ids) * ids.itemsize for ids in preview["_snapshot"].values()) == 24
    action(database)
    before = database.history_settings(A)["counts"]
    with pytest.raises(ValueError, match="million historikkpostar"):
        database.preview_history_cleanup(A, scope="history")
    assert database.history_settings(A)["counts"] == before


def test_maintenance_drains_large_history_in_bounded_batches(tmp_path, monkeypatch):
    monkeypatch.setattr("meshpi.gateway_database.MAINTENANCE_BATCH_RECORDS", 2)
    database = GatewayDatabase(tmp_path / "meshpi.db")
    for packet in range(1, 4):
        database.insert_message(message(packet=packet))
        observations(database, packet=packet)
        action(database, action_id=str(packet))
    assert sum(database.history_settings(A)["counts"].values()) == 12
    for _ in range(6):
        deleted = database.maintain_history(now=NOW)
        assert sum(deleted[A].values()) == 2
    assert not any(database.history_settings(A)["counts"].values())


def test_compact_action_snapshot_does_not_delete_reused_rowid(tmp_path):
    database = GatewayDatabase(tmp_path / "meshpi.db")
    action(database, action_id="old action")
    preview = database.preview_history_cleanup(A, scope="history")
    assert len(preview["_action_fingerprints"]) == 16
    with database.for_gateway(A)._connect() as connection:
        old_rowid = connection.execute("SELECT rowid FROM node_actions").fetchone()[0]
        connection.execute("DELETE FROM node_actions")
    action(database, action_id="new action")
    with database.for_gateway(A)._connect() as connection:
        assert connection.execute("SELECT rowid FROM node_actions").fetchone()[0] == old_rowid
    assert database.cleanup_history(preview)["node_actions"] == 0
    assert database.history_settings(A)["counts"]["node_actions"] == 1
