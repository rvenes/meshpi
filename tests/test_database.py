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


def test_messages_can_be_deleted_by_scope(tmp_path):
    database = Database(tmp_path / "messages.db")
    database.initialize()
    database.insert_message(message(1))
    database.insert_message(message(2, ConversationKind.DM, "!11112222"))
    database.archive_conversation("!11112222")

    assert database.delete_messages("public") == 1
    assert database.list_messages("public") == []
    assert len(database.list_messages("dm", "!11112222")) == 1

    assert database.delete_messages("all") == 1
    assert database.list_messages("dm", "!11112222") == []
    database.unarchive_conversation("!11112222")
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
