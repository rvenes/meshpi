from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import Any

from meshpi.models import Message, MessageStatus, Node, now_iso

SCHEMA = """
CREATE TABLE IF NOT EXISTS messages (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    packet_id INTEGER,
    timestamp TEXT NOT NULL,
    from_node TEXT,
    to_node TEXT,
    channel INTEGER,
    kind TEXT NOT NULL CHECK(kind IN ('public', 'dm')),
    peer_node TEXT,
    text TEXT NOT NULL,
    direction TEXT NOT NULL CHECK(direction IN ('inn', 'ut')),
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

CREATE UNIQUE INDEX IF NOT EXISTS messages_packet_identity
ON messages(packet_id, direction, COALESCE(from_node, ''), COALESCE(to_node, ''))
WHERE packet_id IS NOT NULL;

CREATE INDEX IF NOT EXISTS messages_public_time
ON messages(kind, channel, timestamp);

CREATE INDEX IF NOT EXISTS messages_dm_peer_time
ON messages(kind, peer_node, timestamp);

CREATE TABLE IF NOT EXISTS archived_conversations (
    peer_node TEXT PRIMARY KEY,
    archived_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS nodes (
    node_id TEXT PRIMARY KEY,
    node_num INTEGER,
    long_name TEXT,
    short_name TEXT,
    hw_model TEXT,
    role TEXT,
    last_heard INTEGER,
    battery_level INTEGER,
    voltage REAL,
    snr REAL,
    rssi INTEGER,
    hops_away INTEGER,
    transport TEXT NOT NULL DEFAULT 'Ukjend',
    can_receive_dm INTEGER,
    is_local INTEGER NOT NULL DEFAULT 0,
    updated_at TEXT NOT NULL
);
"""


class Database:
    def __init__(self, path: str | Path, max_messages: int = 50_000):
        self.path = Path(path)
        self.max_messages = max(1_000, max_messages)
        self._inserts_since_prune = 0

    def initialize(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as connection:
            connection.executescript(SCHEMA)
            self._prune_messages(connection)

    def _prune_messages(self, connection: sqlite3.Connection) -> None:
        connection.execute(
            """
            DELETE FROM messages
            WHERE id NOT IN (
                SELECT id FROM messages ORDER BY id DESC LIMIT ?
            )
            """,
            (self.max_messages,),
        )

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.path, timeout=10)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA journal_mode=WAL")
        connection.execute("PRAGMA foreign_keys=ON")
        connection.execute("PRAGMA busy_timeout=10000")
        return connection

    def insert_message(self, message: Message) -> tuple[bool, int | None]:
        values = (
            message.packet_id,
            message.timestamp,
            message.from_node,
            message.to_node,
            message.channel,
            str(message.kind),
            message.peer_node,
            message.text,
            str(message.direction),
            str(message.transport),
            message.rssi,
            message.snr,
            message.hop_limit,
            message.hop_start,
            int(message.want_ack),
            str(message.status),
            json.dumps(message.raw_metadata, ensure_ascii=False, separators=(",", ":"))
            if message.raw_metadata
            else None,
            int(message.is_read),
        )
        sql = """
            INSERT OR IGNORE INTO messages (
                packet_id, timestamp, from_node, to_node, channel, kind, peer_node,
                text, direction, transport, rssi, snr, hop_limit, hop_start,
                want_ack, status, raw_metadata, is_read
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """
        with self._connect() as connection:
            cursor = connection.execute(sql, values)
            inserted = cursor.rowcount == 1
            if inserted and str(message.kind) == "dm" and message.peer_node:
                connection.execute(
                    "DELETE FROM archived_conversations WHERE peer_node = ?",
                    (message.peer_node,),
                )
            if inserted:
                self._inserts_since_prune += 1
                if self._inserts_since_prune >= 100:
                    self._prune_messages(connection)
                    self._inserts_since_prune = 0
            return inserted, cursor.lastrowid if inserted else None

    def update_message_status(self, packet_id: int, status: MessageStatus) -> bool:
        with self._connect() as connection:
            cursor = connection.execute(
                "UPDATE messages SET status = ? WHERE packet_id = ? AND direction = 'ut'",
                (str(status), packet_id),
            )
            return cursor.rowcount > 0

    def list_messages(
        self,
        kind: str,
        peer_node: str | None = None,
        limit: int = 100,
        mark_read: bool = False,
    ) -> list[dict[str, Any]]:
        limit = max(1, min(limit, 1000))
        where = "kind = ? AND channel = 0" if kind == "public" else "kind = ? AND peer_node = ?"
        params: tuple[Any, ...] = (kind,) if kind == "public" else (kind, peer_node)
        with self._connect() as connection:
            query = f"""
                SELECT recent.*, nodes.long_name AS from_long_name,
                       nodes.short_name AS from_short_name
                FROM (
                    SELECT * FROM messages WHERE {where}
                    ORDER BY timestamp DESC, id DESC LIMIT ?
                ) AS recent
                LEFT JOIN nodes ON nodes.node_id = recent.from_node
                ORDER BY recent.timestamp, recent.id
                """  # nosec B608
            rows = connection.execute(query, (*params, limit)).fetchall()
            if mark_read:
                connection.execute(
                    f"UPDATE messages SET is_read = 1 WHERE {where} AND direction = 'inn'",  # nosec B608
                    params,
                )
        return [self._message_row(row) for row in rows]

    def conversations(self) -> list[dict[str, Any]]:
        sql = """
        WITH grouped AS (
            SELECT
                CASE WHEN kind = 'public' THEN 'public' ELSE peer_node END AS conversation,
                kind,
                MAX(timestamp) AS last_timestamp,
                SUM(CASE WHEN direction = 'inn' AND is_read = 0 THEN 1 ELSE 0 END) AS unread
            FROM messages
            WHERE kind = 'public'
               OR NOT EXISTS (
                   SELECT 1 FROM archived_conversations
                   WHERE peer_node = messages.peer_node
               )
            GROUP BY kind, CASE WHEN kind = 'public' THEN 'public' ELSE peer_node END
        )
        SELECT grouped.*, messages.text AS last_text, messages.from_node, messages.to_node,
               nodes.long_name, nodes.short_name
        FROM grouped
        LEFT JOIN messages
          ON messages.id = (
              SELECT m2.id FROM messages m2
              WHERE (grouped.kind = 'public' AND m2.kind = 'public' AND m2.channel = 0)
                 OR (grouped.kind = 'dm' AND m2.kind = 'dm'
                     AND m2.peer_node = grouped.conversation)
              ORDER BY m2.timestamp DESC, m2.id DESC LIMIT 1
          )
        LEFT JOIN nodes ON nodes.node_id = grouped.conversation
        ORDER BY grouped.last_timestamp DESC
        """
        with self._connect() as connection:
            return [dict(row) for row in connection.execute(sql).fetchall()]

    def archive_conversation(self, peer_node: str) -> None:
        with self._connect() as connection:
            connection.execute(
                """
                INSERT INTO archived_conversations (peer_node, archived_at)
                VALUES (?, ?)
                ON CONFLICT(peer_node) DO UPDATE SET archived_at=excluded.archived_at
                """,
                (peer_node, now_iso()),
            )

    def unarchive_conversation(self, peer_node: str) -> None:
        with self._connect() as connection:
            connection.execute(
                "DELETE FROM archived_conversations WHERE peer_node = ?",
                (peer_node,),
            )

    def upsert_node(self, node: Node) -> None:
        node.updated_at = node.updated_at or now_iso()
        values = (
            node.node_id,
            node.node_num,
            node.long_name,
            node.short_name,
            node.hw_model,
            node.role,
            node.last_heard,
            node.battery_level,
            node.voltage,
            node.snr,
            node.rssi,
            node.hops_away,
            str(node.transport),
            None if node.can_receive_dm is None else int(node.can_receive_dm),
            int(node.is_local),
            node.updated_at,
        )
        with self._connect() as connection:
            connection.execute(
                """
                INSERT INTO nodes (
                    node_id, node_num, long_name, short_name, hw_model, role,
                    last_heard, battery_level, voltage, snr, rssi, hops_away,
                    transport, can_receive_dm, is_local, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(node_id) DO UPDATE SET
                    node_num=excluded.node_num,
                    long_name=COALESCE(excluded.long_name, nodes.long_name),
                    short_name=COALESCE(excluded.short_name, nodes.short_name),
                    hw_model=COALESCE(excluded.hw_model, nodes.hw_model),
                    role=COALESCE(excluded.role, nodes.role),
                    last_heard=COALESCE(excluded.last_heard, nodes.last_heard),
                    battery_level=COALESCE(excluded.battery_level, nodes.battery_level),
                    voltage=COALESCE(excluded.voltage, nodes.voltage),
                    snr=COALESCE(excluded.snr, nodes.snr),
                    rssi=COALESCE(excluded.rssi, nodes.rssi),
                    hops_away=COALESCE(excluded.hops_away, nodes.hops_away),
                    transport=CASE WHEN excluded.transport = 'Ukjend'
                                   THEN nodes.transport ELSE excluded.transport END,
                    can_receive_dm=COALESCE(excluded.can_receive_dm, nodes.can_receive_dm),
                    is_local=excluded.is_local,
                    updated_at=excluded.updated_at
                """,
                values,
            )

    def list_nodes(self, search: str = "", sort: str = "seen") -> list[dict[str, Any]]:
        order = {
            "name": "COALESCE(long_name, short_name, node_id) COLLATE NOCASE, node_id",
            "id": "node_id",
            "seen": "COALESCE(last_heard, 0) DESC, node_id",
        }.get(sort)
        if order is None:
            raise ValueError("Sortering må vere name, seen eller id")
        pattern = f"%{search.strip()}%"
        with self._connect() as connection:
            query = f"""
                SELECT * FROM nodes
                WHERE ? = '%%'
                   OR node_id LIKE ? COLLATE NOCASE
                   OR long_name LIKE ? COLLATE NOCASE
                   OR short_name LIKE ? COLLATE NOCASE
                ORDER BY {order}
                """  # nosec B608
            rows = connection.execute(
                query, (pattern, pattern, pattern, pattern)
            ).fetchall()
        result = []
        for row in rows:
            item = dict(row) | {"short_id": row["node_id"][-4:]}
            item["is_local"] = bool(item["is_local"])
            if item["can_receive_dm"] is not None:
                item["can_receive_dm"] = bool(item["can_receive_dm"])
            result.append(item)
        return result

    def get_node(self, node_id: str) -> dict[str, Any] | None:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT * FROM nodes WHERE node_id = ?",
                (node_id,),
            ).fetchone()
        if row is None:
            return None
        item = dict(row) | {"short_id": row["node_id"][-4:]}
        item["is_local"] = bool(item["is_local"])
        if item["can_receive_dm"] is not None:
            item["can_receive_dm"] = bool(item["can_receive_dm"])
        return item

    def set_local_node(self, node_id: str | None) -> None:
        with self._connect() as connection:
            connection.execute("UPDATE nodes SET is_local = 0 WHERE is_local != 0")
            if node_id:
                connection.execute(
                    "UPDATE nodes SET is_local = 1 WHERE node_id = ?",
                    (node_id,),
                )

    def _message_row(self, row: sqlite3.Row) -> dict[str, Any]:
        result = dict(row)
        result["want_ack"] = bool(result["want_ack"])
        result["is_read"] = bool(result["is_read"])
        if result["raw_metadata"]:
            try:
                result["raw_metadata"] = json.loads(result["raw_metadata"])
            except json.JSONDecodeError:
                result["raw_metadata"] = None
        return result
