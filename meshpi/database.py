from __future__ import annotations

import json
import os
import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from meshpi.channels import (
    ChannelBinding,
    dm_conversation_id,
    public_conversation_id,
)
from meshpi.models import Message, MessageStatus, Node, now_iso

DATABASE_SCHEMA_VERSION = 3
EXPORT_FORMAT_VERSION = 1
EXPORT_TABLE_QUERIES = (
    (
        "logical_channels",
        'SELECT * FROM "logical_channels" ORDER BY rowid',
    ),
    ("nodes", 'SELECT * FROM "nodes" ORDER BY rowid'),
    ("messages", 'SELECT * FROM "messages" ORDER BY rowid'),
    (
        "channel_bindings",
        'SELECT * FROM "channel_bindings" ORDER BY rowid',
    ),
    (
        "message_observations",
        'SELECT * FROM "message_observations" ORDER BY rowid',
    ),
    (
        "telemetry_samples",
        'SELECT * FROM "telemetry_samples" ORDER BY rowid',
    ),
    ("positions", 'SELECT * FROM "positions" ORDER BY rowid'),
    ("node_actions", 'SELECT * FROM "node_actions" ORDER BY rowid'),
    (
        "archived_conversations",
        'SELECT * FROM "archived_conversations" ORDER BY rowid',
    ),
    (
        "archived_conversation_ids",
        'SELECT * FROM "archived_conversation_ids" ORDER BY rowid',
    ),
)

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
    is_read INTEGER NOT NULL DEFAULT 0,
    conversation_id TEXT,
    channel_key TEXT,
    local_node_id TEXT,
    gateway_profile_id TEXT,
    received_at TEXT
);

CREATE INDEX IF NOT EXISTS messages_public_time
ON messages(kind, channel, timestamp);

CREATE INDEX IF NOT EXISTS messages_dm_peer_time
ON messages(kind, peer_node, timestamp);

CREATE TABLE IF NOT EXISTS archived_conversations (
    peer_node TEXT PRIMARY KEY,
    archived_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS node_actions (
    action_id TEXT PRIMARY KEY,
    action TEXT NOT NULL,
    node_id TEXT NOT NULL,
    status TEXT NOT NULL,
    started_at TEXT NOT NULL,
    finished_at TEXT,
    packet_id INTEGER,
    result TEXT,
    error TEXT
);

CREATE INDEX IF NOT EXISTS node_actions_node_time
ON node_actions(node_id, started_at);

CREATE TABLE IF NOT EXISTS telemetry_samples (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    dedupe_key TEXT NOT NULL UNIQUE,
    packet_id INTEGER,
    node_id TEXT NOT NULL,
    kind TEXT NOT NULL,
    sample_time TEXT NOT NULL,
    received_at TEXT NOT NULL,
    metrics TEXT NOT NULL,
    transport TEXT NOT NULL,
    rssi INTEGER,
    snr REAL,
    hop_limit INTEGER,
    hop_start INTEGER,
    gateway_profile_id TEXT,
    gateway_node_id TEXT,
    gateway_transport TEXT
);

CREATE TABLE IF NOT EXISTS archived_conversation_ids (
    conversation_id TEXT PRIMARY KEY,
    archived_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS logical_channels (
    channel_key TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    meshtastic_id TEXT,
    first_seen_at TEXT NOT NULL,
    last_seen_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS channel_bindings (
    local_node_id TEXT NOT NULL,
    channel_index INTEGER NOT NULL,
    channel_key TEXT NOT NULL REFERENCES logical_channels(channel_key),
    name TEXT NOT NULL,
    role TEXT NOT NULL,
    meshtastic_id TEXT,
    gateway_profile_id TEXT,
    first_seen_at TEXT NOT NULL,
    last_seen_at TEXT NOT NULL,
    is_active INTEGER NOT NULL DEFAULT 1,
    PRIMARY KEY(local_node_id, channel_index)
);

CREATE INDEX IF NOT EXISTS channel_bindings_key
ON channel_bindings(channel_key, local_node_id);

CREATE TABLE IF NOT EXISTS message_observations (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    message_id INTEGER NOT NULL REFERENCES messages(id) ON DELETE CASCADE,
    observed_at TEXT NOT NULL,
    local_node_id TEXT,
    gateway_profile_id TEXT,
    channel_index INTEGER,
    channel_key TEXT,
    transport TEXT NOT NULL,
    rssi INTEGER,
    snr REAL,
    hop_limit INTEGER,
    hop_start INTEGER
);

CREATE UNIQUE INDEX IF NOT EXISTS message_observation_identity
ON message_observations(
    message_id,
    COALESCE(local_node_id, ''),
    COALESCE(gateway_profile_id, ''),
    COALESCE(channel_index, -1),
    transport
);

CREATE INDEX IF NOT EXISTS telemetry_node_kind_time
ON telemetry_samples(node_id, kind, sample_time DESC, id DESC);

CREATE TABLE IF NOT EXISTS positions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    dedupe_key TEXT NOT NULL UNIQUE,
    packet_id INTEGER,
    node_id TEXT NOT NULL,
    sample_time TEXT NOT NULL,
    received_at TEXT NOT NULL,
    latitude REAL NOT NULL,
    longitude REAL NOT NULL,
    altitude_msl INTEGER,
    altitude_hae INTEGER,
    geoidal_separation INTEGER,
    pdop REAL,
    hdop REAL,
    vdop REAL,
    gps_accuracy_mm INTEGER,
    ground_speed REAL,
    ground_track REAL,
    fix_quality INTEGER,
    fix_type INTEGER,
    sats_in_view INTEGER,
    location_source TEXT,
    altitude_source TEXT,
    precision_bits INTEGER,
    metadata TEXT,
    transport TEXT NOT NULL,
    rssi INTEGER,
    snr REAL,
    hop_limit INTEGER,
    hop_start INTEGER,
    gateway_profile_id TEXT,
    gateway_node_id TEXT,
    gateway_transport TEXT
);

CREATE INDEX IF NOT EXISTS positions_node_time
ON positions(node_id, sample_time DESC, id DESC);

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
    def __init__(
        self,
        path: str | Path,
        max_messages: int = 50_000,
        observation_retention_days: int = 365,
    ):
        self.path = Path(path)
        self.max_messages = max(1_000, max_messages)
        self.observation_retention_days = max(1, observation_retention_days)
        self._inserts_since_prune = 0
        self._observations_since_prune = 0

    def initialize(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        descriptor = os.open(self.path, os.O_CREAT | os.O_APPEND, 0o600)
        os.close(descriptor)
        if os.name == "posix":
            self.path.chmod(0o600)
        with self._connect() as connection:
            current_version = int(
                connection.execute("PRAGMA user_version").fetchone()[0]
            )
            connection.executescript(SCHEMA)
            self._migrate_message_schema(connection, current_version)
            self._ensure_message_indexes(connection)
            # Eldre versjonar kalla også den førebelse, implisitte ACK-en
            # «stadfesta». Det gav eit sterkare leveringsløfte enn protokollen.
            connection.execute(
                "UPDATE messages SET status = ? WHERE status = 'stadfesta'",
                (str(MessageStatus.ACKNOWLEDGED),),
            )
            self._prune_messages(connection)
            self._prune_observations(connection)

    @staticmethod
    def _migrate_message_schema(
        connection: sqlite3.Connection,
        current_version: int,
    ) -> None:
        if current_version >= DATABASE_SCHEMA_VERSION:
            return
        columns = {
            str(row["name"])
            for row in connection.execute("PRAGMA table_info(messages)").fetchall()
        }
        additions = {
            "conversation_id": "TEXT",
            "channel_key": "TEXT",
            "local_node_id": "TEXT",
            "gateway_profile_id": "TEXT",
            "received_at": "TEXT",
        }
        for name, declaration in additions.items():
            if name not in columns:
                connection.execute(
                    f"ALTER TABLE messages ADD COLUMN {name} {declaration}"
                )  # nosec B608 -- namn og deklarasjon kjem frå lokal konstant
        if current_version < 2:
            legacy_rows = connection.execute(
                """
                SELECT id, kind, peer_node, channel, timestamp, raw_metadata
                FROM messages
                WHERE conversation_id IS NULL
                   OR received_at IS NULL
                """
            ).fetchall()
            for row in legacy_rows:
                gateway_profile_id = None
                raw = row["raw_metadata"]
                if raw:
                    try:
                        metadata = json.loads(raw)
                        if isinstance(metadata, dict):
                            gateway_profile_id = metadata.get("gateway_id")
                    except (TypeError, json.JSONDecodeError):
                        pass
                channel_key = None
                if row["kind"] == "public":
                    profile_scope = str(gateway_profile_id or "ukjend")
                    channel_index = (
                        int(row["channel"])
                        if row["channel"] is not None
                        else 0
                    )
                    channel_key = f"legacy:{profile_scope}:{channel_index}"
                    conversation_id = public_conversation_id(channel_key)
                    connection.execute(
                        """
                        INSERT OR IGNORE INTO logical_channels (
                            channel_key, name, meshtastic_id,
                            first_seen_at, last_seen_at
                        ) VALUES (?, '', NULL, ?, ?)
                        """,
                        (channel_key, row["timestamp"], row["timestamp"]),
                    )
                else:
                    conversation_id = row["peer_node"]
                connection.execute(
                    """
                    UPDATE messages
                    SET conversation_id=COALESCE(conversation_id, ?),
                        channel_key=COALESCE(channel_key, ?),
                        received_at=COALESCE(received_at, timestamp),
                        gateway_profile_id=COALESCE(gateway_profile_id, ?)
                    WHERE id=?
                    """,
                    (
                        conversation_id,
                        channel_key,
                        gateway_profile_id,
                        row["id"],
                    ),
                )

            connection.execute("DROP INDEX IF EXISTS messages_packet_identity")
            connection.execute(
                "DROP INDEX IF EXISTS messages_packet_context_identity"
            )
            connection.execute(
                """
                CREATE UNIQUE INDEX IF NOT EXISTS messages_packet_context_identity
                ON messages(
                    packet_id,
                    direction,
                    COALESCE(from_node, ''),
                    COALESCE(to_node, ''),
                    COALESCE(conversation_id, ''),
                    text
                )
                WHERE packet_id IS NOT NULL
                """
            )

        if current_version < 3:
            connection.execute(
                """
                UPDATE messages
                SET local_node_id=from_node
                WHERE local_node_id IS NULL
                  AND direction='ut'
                  AND from_node GLOB '![0-9A-Fa-f][0-9A-Fa-f][0-9A-Fa-f][0-9A-Fa-f]'
                                     || '[0-9A-Fa-f][0-9A-Fa-f][0-9A-Fa-f][0-9A-Fa-f]'
                """
            )
            connection.execute(
                """
                CREATE INDEX IF NOT EXISTS messages_conversation_time
                ON messages(conversation_id, id DESC)
                """
            )

        connection.execute(f"PRAGMA user_version={DATABASE_SCHEMA_VERSION}")

    @staticmethod
    def _ensure_message_indexes(connection: sqlite3.Connection) -> None:
        connection.execute(
            """
            CREATE UNIQUE INDEX IF NOT EXISTS messages_packet_context_identity
            ON messages(
                packet_id,
                direction,
                COALESCE(from_node, ''),
                COALESCE(to_node, ''),
                COALESCE(conversation_id, ''),
                text
            )
            WHERE packet_id IS NOT NULL
            """
        )
        connection.execute(
            """
            CREATE INDEX IF NOT EXISTS messages_conversation_time
            ON messages(conversation_id, id DESC)
            """
        )
        connection.execute(
            """
            CREATE INDEX IF NOT EXISTS messages_channel_binding
            ON messages(local_node_id, channel_key, kind)
            """
        )

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

    def _prune_observations(self, connection: sqlite3.Connection) -> None:
        cutoff = (
            datetime.now(timezone.utc)
            - timedelta(days=self.observation_retention_days)
        ).isoformat(timespec="seconds")
        connection.execute(
            "DELETE FROM telemetry_samples WHERE received_at < ?",
            (cutoff,),
        )
        connection.execute(
            "DELETE FROM positions WHERE received_at < ?",
            (cutoff,),
        )

    def _record_observation_insert(self, connection: sqlite3.Connection) -> None:
        self._observations_since_prune += 1
        if self._observations_since_prune >= 100:
            self._prune_observations(connection)
            self._observations_since_prune = 0

    @contextmanager
    def _connect(self) -> Iterator[sqlite3.Connection]:
        connection = sqlite3.connect(self.path, timeout=10)
        try:
            connection.row_factory = sqlite3.Row
            connection.execute("PRAGMA journal_mode=WAL")
            connection.execute("PRAGMA foreign_keys=ON")
            connection.execute("PRAGMA busy_timeout=10000")
            with connection:
                yield connection
        finally:
            connection.close()

    def export_records(self, meshpi_version: str) -> Iterator[dict[str, Any]]:
        """Strøym eit konsistent, tekstvenleg bilete av alle brukardata."""
        with self._connect() as connection:
            connection.execute("BEGIN")
            connection.execute(
                "SELECT 1 FROM sqlite_master LIMIT 1"
            ).fetchone()
            schema_version = int(
                connection.execute("PRAGMA user_version").fetchone()[0]
            )
            yield {
                "record": "metadata",
                "format": "meshpi-database-export",
                "format_version": EXPORT_FORMAT_VERSION,
                "meshpi_version": meshpi_version,
                "database_schema_version": schema_version,
                "exported_at": now_iso(),
            }
            counts: dict[str, int] = {}
            for table, query in EXPORT_TABLE_QUERIES:
                count = 0
                rows = connection.execute(query)
                for row in rows:
                    yield {
                        "record": "row",
                        "table": table,
                        "data": dict(row),
                    }
                    count += 1
                counts[table] = count
            yield {
                "record": "complete",
                "tables": counts,
                "rows": sum(counts.values()),
            }

    def insert_message(self, message: Message) -> tuple[bool, int | None]:
        conversation_id = message.conversation_id
        if not conversation_id:
            conversation_id = (
                "public" if str(message.kind) == "public" else message.peer_node
            )
        received_at = message.received_at or now_iso()
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
            conversation_id,
            message.channel_key,
            message.local_node_id,
            message.gateway_profile_id,
            received_at,
        )
        sql = """
            INSERT OR IGNORE INTO messages (
                packet_id, timestamp, from_node, to_node, channel, kind, peer_node,
                text, direction, transport, rssi, snr, hop_limit, hop_start,
                want_ack, status, raw_metadata, is_read, conversation_id,
                channel_key, local_node_id, gateway_profile_id, received_at
            ) VALUES (
                ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?,
                ?, ?, ?, ?, ?
            )
        """
        with self._connect() as connection:
            cursor = connection.execute(sql, values)
            inserted = cursor.rowcount == 1
            message_id = cursor.lastrowid if inserted else None
            if not inserted and message.packet_id is not None:
                existing = connection.execute(
                    """
                    SELECT id FROM messages
                    WHERE packet_id=? AND direction=?
                      AND COALESCE(from_node, '')=COALESCE(?, '')
                      AND COALESCE(to_node, '')=COALESCE(?, '')
                      AND COALESCE(conversation_id, '')=COALESCE(?, '')
                      AND text=?
                    ORDER BY id DESC LIMIT 1
                    """,
                    (
                        message.packet_id,
                        str(message.direction),
                        message.from_node,
                        message.to_node,
                        conversation_id,
                        message.text,
                    ),
                ).fetchone()
                message_id = int(existing["id"]) if existing else None
            if message_id is not None:
                connection.execute(
                    """
                    INSERT OR IGNORE INTO message_observations (
                        message_id, observed_at, local_node_id, gateway_profile_id,
                        channel_index, channel_key, transport, rssi, snr,
                        hop_limit, hop_start
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        message_id,
                        received_at,
                        message.local_node_id,
                        message.gateway_profile_id,
                        message.channel,
                        message.channel_key,
                        str(message.transport),
                        message.rssi,
                        message.snr,
                        message.hop_limit,
                        message.hop_start,
                    ),
                )
            if inserted and str(message.kind) == "dm" and message.peer_node:
                connection.execute(
                    "DELETE FROM archived_conversations WHERE peer_node = ?",
                    (message.peer_node,),
                )
                connection.execute(
                    "DELETE FROM archived_conversation_ids WHERE conversation_id = ?",
                    (conversation_id,),
                )
            if inserted:
                self._inserts_since_prune += 1
                if self._inserts_since_prune >= 100:
                    self._prune_messages(connection)
                    self._inserts_since_prune = 0
            return inserted, message_id

    def insert_telemetry(self, sample: dict[str, Any]) -> bool:
        metrics = sample.get("metrics")
        if not isinstance(metrics, dict) or not metrics:
            raise ValueError("Telemetrimålinga manglar verdiar")
        values = (
            str(sample.get("dedupe_key") or ""),
            sample.get("packet_id"),
            str(sample.get("node_id") or ""),
            str(sample.get("kind") or ""),
            str(sample.get("sample_time") or ""),
            str(sample.get("received_at") or ""),
            json.dumps(metrics, ensure_ascii=False, sort_keys=True, separators=(",", ":")),
            str(sample.get("transport") or "Ukjend"),
            sample.get("rssi"),
            sample.get("snr"),
            sample.get("hop_limit"),
            sample.get("hop_start"),
            sample.get("gateway_profile_id"),
            sample.get("gateway_node_id"),
            sample.get("gateway_transport"),
        )
        if any(not values[index] for index in (0, 2, 3, 4, 5)):
            raise ValueError("Telemetrimålinga manglar identitet eller tidspunkt")
        with self._connect() as connection:
            cursor = connection.execute(
                """
                INSERT OR IGNORE INTO telemetry_samples (
                    dedupe_key, packet_id, node_id, kind, sample_time, received_at,
                    metrics, transport, rssi, snr, hop_limit, hop_start,
                    gateway_profile_id, gateway_node_id, gateway_transport
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                values,
            )
            inserted = cursor.rowcount == 1
            if inserted:
                self._record_observation_insert(connection)
            return inserted

    def insert_position(self, position: dict[str, Any]) -> bool:
        values = (
            str(position.get("dedupe_key") or ""),
            position.get("packet_id"),
            str(position.get("node_id") or ""),
            str(position.get("sample_time") or ""),
            str(position.get("received_at") or ""),
            position.get("latitude"),
            position.get("longitude"),
            position.get("altitude_msl"),
            position.get("altitude_hae"),
            position.get("geoidal_separation"),
            position.get("pdop"),
            position.get("hdop"),
            position.get("vdop"),
            position.get("gps_accuracy_mm"),
            position.get("ground_speed"),
            position.get("ground_track"),
            position.get("fix_quality"),
            position.get("fix_type"),
            position.get("sats_in_view"),
            position.get("location_source"),
            position.get("altitude_source"),
            position.get("precision_bits"),
            json.dumps(
                position.get("metadata"),
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
            )
            if position.get("metadata")
            else None,
            str(position.get("transport") or "Ukjend"),
            position.get("rssi"),
            position.get("snr"),
            position.get("hop_limit"),
            position.get("hop_start"),
            position.get("gateway_profile_id"),
            position.get("gateway_node_id"),
            position.get("gateway_transport"),
        )
        if any(not values[index] for index in (0, 2, 3, 4)):
            raise ValueError("Posisjonen manglar identitet eller tidspunkt")
        if values[5] is None or values[6] is None:
            raise ValueError("Posisjonen manglar koordinatar")
        with self._connect() as connection:
            cursor = connection.execute(
                """
                INSERT OR IGNORE INTO positions (
                    dedupe_key, packet_id, node_id, sample_time, received_at,
                    latitude, longitude, altitude_msl, altitude_hae,
                    geoidal_separation, pdop, hdop, vdop, gps_accuracy_mm,
                    ground_speed, ground_track, fix_quality, fix_type, sats_in_view,
                    location_source, altitude_source, precision_bits, metadata,
                    transport, rssi, snr, hop_limit, hop_start, gateway_profile_id,
                    gateway_node_id, gateway_transport
                ) VALUES (
                    ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?,
                    ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?
                )
                """,
                values,
            )
            inserted = cursor.rowcount == 1
            if inserted:
                self._record_observation_insert(connection)
            return inserted

    def list_telemetry(
        self,
        node_id: str,
        *,
        kind: str | None = None,
        limit: int = 100,
        before_id: int | None = None,
    ) -> list[dict[str, Any]]:
        limit = max(1, min(limit, 1000))
        where = ["node_id = ?"]
        params: list[Any] = [node_id]
        if kind:
            where.append("kind = ?")
            params.append(kind)
        if before_id is not None:
            where.append("id < ?")
            params.append(before_id)
        query = f"""
            SELECT * FROM telemetry_samples
            WHERE {' AND '.join(where)}
            ORDER BY id DESC LIMIT ?
        """  # nosec B608 -- berre faste lokale WHERE-ledd
        with self._connect() as connection:
            rows = connection.execute(query, (*params, limit)).fetchall()
        return [self._telemetry_row(row) for row in rows]

    def list_positions(
        self,
        node_id: str,
        *,
        limit: int = 100,
        before_id: int | None = None,
    ) -> list[dict[str, Any]]:
        limit = max(1, min(limit, 1000))
        params: list[Any] = [node_id]
        before = ""
        if before_id is not None:
            before = "AND id < ?"
            params.append(before_id)
        with self._connect() as connection:
            rows = connection.execute(
                f"""
                SELECT * FROM positions
                WHERE node_id = ? {before}
                ORDER BY id DESC LIMIT ?
                """,  # nosec B608 -- før-leddet er ein fast lokal konstant
                (*params, limit),
            ).fetchall()
        return [self._position_row(row) for row in rows]

    def node_observation_summary(self, node_id: str) -> dict[str, Any]:
        with self._connect() as connection:
            telemetry_rows = connection.execute(
                """
                SELECT current.* FROM telemetry_samples AS current
                WHERE current.node_id = ?
                  AND NOT EXISTS (
                      SELECT 1 FROM telemetry_samples AS candidate
                      WHERE candidate.node_id = current.node_id
                        AND candidate.kind = current.kind
                        AND (
                            candidate.sample_time > current.sample_time
                            OR (
                                candidate.sample_time = current.sample_time
                                AND candidate.id > current.id
                            )
                        )
                  )
                ORDER BY current.kind
                """,
                (node_id,),
            ).fetchall()
            position = connection.execute(
                """
                SELECT * FROM positions
                WHERE node_id = ?
                ORDER BY sample_time DESC, id DESC LIMIT 1
                """,
                (node_id,),
            ).fetchone()
            telemetry_count = int(
                connection.execute(
                    "SELECT COUNT(*) FROM telemetry_samples WHERE node_id = ?",
                    (node_id,),
                ).fetchone()[0]
            )
            position_count = int(
                connection.execute(
                    "SELECT COUNT(*) FROM positions WHERE node_id = ?",
                    (node_id,),
                ).fetchone()[0]
            )
            traceroute_count = int(
                connection.execute(
                    """
                    SELECT COUNT(*) FROM node_actions
                    WHERE node_id = ? AND action = 'traceroute'
                    """,
                    (node_id,),
                ).fetchone()[0]
            )
        latest = {
            str(row["kind"]): self._telemetry_row(row)
            for row in telemetry_rows
        }
        return {
            "latest_telemetry": latest,
            "latest_position": (
                self._position_row(position) if position is not None else None
            ),
            "counts": {
                "telemetry": telemetry_count,
                "positions": position_count,
                "traceroutes": traceroute_count,
            },
        }

    def update_message_status(
        self,
        packet_id: int,
        status: MessageStatus,
        local_node_id: str | None = None,
        failure_reason: str | None = None,
    ) -> bool:
        if status == MessageStatus.ACKNOWLEDGED:
            allowed_statuses = (str(MessageStatus.QUEUED), "stadfesta")
        elif status == MessageStatus.FAILED:
            allowed_statuses = (
                str(MessageStatus.QUEUED),
                str(MessageStatus.ACKNOWLEDGED),
                "stadfesta",
            )
        else:
            allowed_statuses = (
                str(MessageStatus.QUEUED),
                str(MessageStatus.ACKNOWLEDGED),
                str(MessageStatus.FAILED),
                "stadfesta",
            )
        placeholders = ", ".join("?" for _ in allowed_statuses)
        local_filter = "AND local_node_id = ?" if local_node_id else ""
        local_params: tuple[Any, ...] = (
            (local_node_id,) if local_node_id else ()
        )
        with self._connect() as connection:
            row = connection.execute(
                f"""
                SELECT id, raw_metadata FROM messages
                WHERE packet_id = ? AND direction = 'ut'
                  AND status IN ({placeholders})
                  {local_filter}
                ORDER BY id DESC LIMIT 1
                """,  # nosec B608 -- ledda kjem frå lokale, faste konstantar
                (
                    packet_id,
                    *allowed_statuses,
                    *local_params,
                ),
            ).fetchone()
            if row is None:
                return False
            metadata: dict[str, Any] = {}
            if row["raw_metadata"]:
                try:
                    parsed = json.loads(row["raw_metadata"])
                    if isinstance(parsed, dict):
                        metadata = parsed
                except (TypeError, json.JSONDecodeError):
                    pass
            if failure_reason:
                metadata["failure_reason"] = failure_reason
            elif status != MessageStatus.FAILED:
                metadata.pop("failure_reason", None)
            cursor = connection.execute(
                f"""
                UPDATE messages
                SET status=?, raw_metadata=?
                WHERE id=? AND status IN ({placeholders})
                """,  # nosec B608 -- plasshaldarane er lokalt bygde
                (
                    str(status),
                    (
                        json.dumps(
                            metadata,
                            ensure_ascii=False,
                            separators=(",", ":"),
                        )
                        if metadata
                        else None
                    ),
                    row["id"],
                    *allowed_statuses,
                ),
            )
            return cursor.rowcount > 0

    def outgoing_message(
        self,
        packet_id: int,
        local_node_id: str | None = None,
    ) -> dict[str, Any] | None:
        local_filter = "AND local_node_id = ?" if local_node_id else ""
        params: tuple[Any, ...] = (
            (packet_id, local_node_id)
            if local_node_id
            else (packet_id,)
        )
        with self._connect() as connection:
            row = connection.execute(
                f"""
                SELECT * FROM messages
                WHERE packet_id = ? AND direction = 'ut'
                  {local_filter}
                ORDER BY id DESC LIMIT 1
                """,  # nosec B608 -- filteret er ein fast lokal konstant
                params,
            ).fetchone()
        return self._message_row(row) if row is not None else None

    def list_messages(
        self,
        kind: str,
        peer_node: str | None = None,
        conversation_id: str | None = None,
        conversation_ids: list[str] | None = None,
        channel_index: int | None = None,
        limit: int = 100,
        mark_read: bool = False,
    ) -> list[dict[str, Any]]:
        limit = max(1, min(limit, 1000))
        if conversation_ids:
            placeholders = ", ".join("?" for _ in conversation_ids)
            where = f"kind = ? AND conversation_id IN ({placeholders})"
            params = (kind, *conversation_ids)
        elif conversation_id:
            where = "conversation_id = ?"
            params: tuple[Any, ...] = (conversation_id,)
        elif kind == "public":
            where = "kind = ? AND channel = 0"
            params = (kind,)
        else:
            if channel_index is None:
                where = "kind = ? AND peer_node = ?"
                params = (kind, peer_node)
            else:
                where = "kind = ? AND peer_node = ? AND channel = ?"
                params = (kind, peer_node, channel_index)
        with self._connect() as connection:
            query = f"""
                SELECT recent.*, nodes.long_name AS from_long_name,
                       nodes.short_name AS from_short_name,
                       (
                           SELECT COUNT(*) FROM message_observations
                           WHERE message_id=recent.id
                       ) AS observation_count
                FROM (
                    SELECT * FROM messages WHERE {where}
                    ORDER BY id DESC LIMIT ?
                ) AS recent
                LEFT JOIN nodes ON nodes.node_id = recent.from_node
                ORDER BY recent.id
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
                COALESCE(
                    conversation_id,
                    CASE WHEN kind = 'public' THEN 'public' ELSE peer_node END
                ) AS conversation,
                kind,
                MAX(id) AS last_message_id,
                SUM(CASE WHEN direction = 'inn' AND is_read = 0 THEN 1 ELSE 0 END) AS unread
            FROM messages
            WHERE kind = 'public'
               OR (kind = 'dm' AND NOT EXISTS (
                   SELECT 1 FROM archived_conversations
                   WHERE peer_node = messages.peer_node
               ) AND NOT EXISTS (
                   SELECT 1 FROM archived_conversation_ids
                   WHERE conversation_id = messages.conversation_id
               ))
            GROUP BY kind, COALESCE(
                conversation_id,
                CASE WHEN kind = 'public' THEN 'public' ELSE peer_node END
            )
        )
        SELECT grouped.conversation, grouped.kind, grouped.last_message_id,
               messages.timestamp AS last_timestamp,
               grouped.unread, messages.text AS last_text, messages.from_node, messages.to_node,
               messages.channel, messages.channel_key, messages.local_node_id,
               messages.peer_node, messages.gateway_profile_id,
               channels.name AS channel_name,
               nodes.long_name, nodes.short_name
        FROM grouped
        LEFT JOIN messages ON messages.id = grouped.last_message_id
        LEFT JOIN logical_channels AS channels
               ON channels.channel_key = messages.channel_key
        LEFT JOIN nodes ON nodes.node_id = messages.peer_node
        ORDER BY grouped.last_message_id DESC
        """
        with self._connect() as connection:
            return [dict(row) for row in connection.execute(sql).fetchall()]

    def delete_messages(self, scope: str) -> int:
        where = {
            "public": "kind = 'public'",
            "dm": "kind = 'dm'",
            "all": "kind IN ('public', 'dm')",
        }.get(scope)
        if where is None:
            raise ValueError("Omfang må vere public, dm eller all")
        with self._connect() as connection:
            cursor = connection.execute(f"DELETE FROM messages WHERE {where}")  # nosec B608
            if scope in {"dm", "all"}:
                connection.execute("DELETE FROM archived_conversations")
                connection.execute("DELETE FROM archived_conversation_ids")
            return cursor.rowcount

    def upsert_node_action(self, action: dict[str, Any]) -> None:
        action_id = str(action.get("action_id") or "")
        node_id = str(action.get("node_id") or "")
        started_at = str(action.get("started_at") or "")
        if not action_id or not node_id or not started_at:
            raise ValueError("Nodehandlinga manglar ID, node eller starttid")
        result = action.get("result")
        with self._connect() as connection:
            connection.execute(
                """
                INSERT INTO node_actions (
                    action_id, action, node_id, status, started_at, finished_at,
                    packet_id, result, error
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(action_id) DO UPDATE SET
                    status=excluded.status,
                    finished_at=excluded.finished_at,
                    packet_id=excluded.packet_id,
                    result=excluded.result,
                    error=excluded.error
                """,
                (
                    action_id,
                    str(action.get("action") or ""),
                    node_id,
                    str(action.get("status") or "started"),
                    started_at,
                    action.get("finished_at"),
                    action.get("packet_id"),
                    json.dumps(result, ensure_ascii=False, separators=(",", ":"))
                    if result is not None
                    else None,
                    action.get("error"),
                ),
            )

    def list_node_actions(
        self,
        node_id: str,
        action: str = "traceroute",
        limit: int = 100,
    ) -> list[dict[str, Any]]:
        limit = max(1, min(limit, 1000))
        with self._connect() as connection:
            rows = connection.execute(
                """
                SELECT * FROM (
                    SELECT * FROM node_actions
                    WHERE node_id = ? AND action = ?
                    ORDER BY started_at DESC, action_id DESC LIMIT ?
                ) AS recent
                ORDER BY recent.started_at, recent.action_id
                """,
                (node_id, action, limit),
            ).fetchall()
        result = []
        for row in rows:
            item = dict(row)
            if item["result"]:
                try:
                    item["result"] = json.loads(item["result"])
                except json.JSONDecodeError:
                    item["result"] = None
            result.append(item)
        return result

    def fail_started_node_actions(self, error: str) -> int:
        with self._connect() as connection:
            cursor = connection.execute(
                """
                UPDATE node_actions
                SET status = 'failed', finished_at = ?, error = ?
                WHERE status = 'started'
                """,
                (now_iso(), error),
            )
            return cursor.rowcount

    def archive_conversation(
        self,
        peer_node: str,
        conversation_id: str | None = None,
    ) -> None:
        with self._connect() as connection:
            if conversation_id:
                connection.execute(
                    """
                    INSERT INTO archived_conversation_ids (
                        conversation_id, archived_at
                    ) VALUES (?, ?)
                    ON CONFLICT(conversation_id)
                    DO UPDATE SET archived_at=excluded.archived_at
                    """,
                    (conversation_id, now_iso()),
                )
            else:
                connection.execute(
                    """
                    INSERT INTO archived_conversations (peer_node, archived_at)
                    VALUES (?, ?)
                    ON CONFLICT(peer_node)
                    DO UPDATE SET archived_at=excluded.archived_at
                    """,
                    (peer_node, now_iso()),
                )

    def unarchive_conversation(
        self,
        peer_node: str,
        conversation_id: str | None = None,
    ) -> None:
        with self._connect() as connection:
            if conversation_id:
                connection.execute(
                    "DELETE FROM archived_conversation_ids WHERE conversation_id = ?",
                    (conversation_id,),
                )
            else:
                connection.execute(
                    "DELETE FROM archived_conversations WHERE peer_node = ?",
                    (peer_node,),
                )

    def rebind_provisional_channel(
        self,
        local_node_id: str,
        channel_index: int,
        provisional_key: str,
        channel_key: str,
    ) -> int:
        if provisional_key == channel_key:
            return 0
        rebound = 0
        with self._connect() as connection:
            rows = connection.execute(
                """
                SELECT *
                FROM messages
                WHERE local_node_id=?
                  AND channel_key=?
                ORDER BY id
                """,
                (local_node_id, provisional_key),
            ).fetchall()
            for row in rows:
                conversation_id = (
                    public_conversation_id(channel_key)
                    if row["kind"] == "public"
                    else dm_conversation_id(
                        local_node_id,
                        str(row["peer_node"] or ""),
                        channel_key,
                    )
                )
                duplicate = None
                if row["packet_id"] is not None:
                    duplicate = connection.execute(
                        """
                        SELECT id
                        FROM messages
                        WHERE id<>?
                          AND packet_id=?
                          AND direction=?
                          AND COALESCE(from_node, '')=COALESCE(?, '')
                          AND COALESCE(to_node, '')=COALESCE(?, '')
                          AND COALESCE(conversation_id, '')=?
                          AND text=?
                        ORDER BY id
                        LIMIT 1
                        """,
                        (
                            row["id"],
                            row["packet_id"],
                            row["direction"],
                            row["from_node"],
                            row["to_node"],
                            conversation_id,
                            row["text"],
                        ),
                    ).fetchone()
                if duplicate is not None:
                    connection.execute(
                        """
                        INSERT OR IGNORE INTO message_observations (
                            message_id, observed_at, local_node_id,
                            gateway_profile_id, channel_index, channel_key,
                            transport, rssi, snr, hop_limit, hop_start
                        )
                        SELECT ?, observed_at, local_node_id,
                               gateway_profile_id, ?, ?,
                               transport, rssi, snr, hop_limit, hop_start
                        FROM message_observations
                        WHERE message_id=?
                        """,
                        (
                            duplicate["id"],
                            channel_index,
                            channel_key,
                            row["id"],
                        ),
                    )
                    connection.execute(
                        "DELETE FROM messages WHERE id=?",
                        (row["id"],),
                    )
                else:
                    connection.execute(
                        """
                        UPDATE messages
                        SET channel=?, channel_key=?, conversation_id=?
                        WHERE id=?
                        """,
                        (
                            channel_index,
                            channel_key,
                            conversation_id,
                            row["id"],
                        ),
                    )
                    connection.execute(
                        """
                        UPDATE message_observations
                        SET channel_index=?, channel_key=?
                        WHERE message_id=?
                        """,
                        (channel_index, channel_key, row["id"]),
                    )
                old_conversation_id = str(row["conversation_id"] or "")
                if old_conversation_id:
                    connection.execute(
                        """
                        INSERT OR IGNORE INTO archived_conversation_ids (
                            conversation_id, archived_at
                        )
                        SELECT ?, archived_at
                        FROM archived_conversation_ids
                        WHERE conversation_id=?
                        """,
                        (conversation_id, old_conversation_id),
                    )
                    connection.execute(
                        """
                        DELETE FROM archived_conversation_ids
                        WHERE conversation_id=?
                        """,
                        (old_conversation_id,),
                    )
                rebound += 1
        return rebound

    def rebind_legacy_primary_dms(
        self,
        local_node_id: str,
        channel_index: int,
        channel_key: str,
    ) -> int:
        """Bind trygg, eldre DM-historikk til den stadfesta primærkanalen."""
        if channel_index != 0:
            raise ValueError("Eldre DM-historikk kan berre bindast til primærkanalen")
        rebound = 0
        with self._connect() as connection:
            rows = connection.execute(
                """
                SELECT *
                FROM messages
                WHERE kind='dm'
                  AND channel=0
                  AND channel_key IS NULL
                  AND conversation_id=peer_node
                  AND peer_node IS NOT NULL
                  AND LOWER(peer_node)<>LOWER(?)
                  AND (
                      local_node_id IS NULL
                      OR LOWER(local_node_id)=LOWER(?)
                  )
                  AND (
                      (
                          LOWER(from_node)=LOWER(?)
                          AND LOWER(to_node)=LOWER(peer_node)
                      )
                      OR (
                          LOWER(to_node)=LOWER(?)
                          AND LOWER(from_node)=LOWER(peer_node)
                      )
                  )
                ORDER BY id
                """,
                (
                    local_node_id,
                    local_node_id,
                    local_node_id,
                    local_node_id,
                ),
            ).fetchall()
            for row in rows:
                peer_node = str(row["peer_node"]).lower()
                conversation_id = dm_conversation_id(
                    local_node_id,
                    peer_node,
                    channel_key,
                )
                duplicate = None
                if row["packet_id"] is not None:
                    duplicate = connection.execute(
                        """
                        SELECT id
                        FROM messages
                        WHERE id<>?
                          AND packet_id=?
                          AND direction=?
                          AND COALESCE(from_node, '')=COALESCE(?, '')
                          AND COALESCE(to_node, '')=COALESCE(?, '')
                          AND COALESCE(conversation_id, '')=?
                          AND text=?
                        ORDER BY id
                        LIMIT 1
                        """,
                        (
                            row["id"],
                            row["packet_id"],
                            row["direction"],
                            row["from_node"],
                            row["to_node"],
                            conversation_id,
                            row["text"],
                        ),
                    ).fetchone()
                observations = connection.execute(
                    """
                    SELECT *
                    FROM message_observations
                    WHERE message_id=?
                    ORDER BY id
                    """,
                    (row["id"],),
                ).fetchall()
                target_message_id = (
                    int(duplicate["id"]) if duplicate is not None else int(row["id"])
                )
                if duplicate is None:
                    connection.execute(
                        """
                        UPDATE messages
                        SET local_node_id=?, channel=?, channel_key=?,
                            conversation_id=?
                        WHERE id=?
                        """,
                        (
                            local_node_id.lower(),
                            channel_index,
                            channel_key,
                            conversation_id,
                            row["id"],
                        ),
                    )
                    connection.execute(
                        "DELETE FROM message_observations WHERE message_id=?",
                        (row["id"],),
                    )
                for observation in observations:
                    connection.execute(
                        """
                        INSERT OR IGNORE INTO message_observations (
                            message_id, observed_at, local_node_id,
                            gateway_profile_id, channel_index, channel_key,
                            transport, rssi, snr, hop_limit, hop_start
                        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                        """,
                        (
                            target_message_id,
                            observation["observed_at"],
                            local_node_id.lower(),
                            observation["gateway_profile_id"],
                            channel_index,
                            channel_key,
                            observation["transport"],
                            observation["rssi"],
                            observation["snr"],
                            observation["hop_limit"],
                            observation["hop_start"],
                        ),
                    )
                if duplicate is not None:
                    connection.execute(
                        "DELETE FROM messages WHERE id=?",
                        (row["id"],),
                    )
                old_conversation_id = str(row["conversation_id"] or "")
                if old_conversation_id:
                    connection.execute(
                        """
                        INSERT OR IGNORE INTO archived_conversation_ids (
                            conversation_id, archived_at
                        )
                        SELECT ?, archived_at
                        FROM archived_conversation_ids
                        WHERE conversation_id=?
                        """,
                        (conversation_id, old_conversation_id),
                    )
                    connection.execute(
                        """
                        DELETE FROM archived_conversation_ids
                        WHERE conversation_id=?
                        """,
                        (old_conversation_id,),
                    )
                rebound += 1
        return rebound

    def sync_channel_bindings(
        self,
        local_node_id: str,
        gateway_profile_id: str | None,
        bindings: list[ChannelBinding],
    ) -> None:
        seen_at = now_iso()
        with self._connect() as connection:
            connection.execute(
                "UPDATE channel_bindings SET is_active=0 WHERE local_node_id=?",
                (local_node_id,),
            )
            for binding in bindings:
                connection.execute(
                    """
                    INSERT INTO logical_channels (
                        channel_key, name, meshtastic_id,
                        first_seen_at, last_seen_at
                    ) VALUES (?, ?, ?, ?, ?)
                    ON CONFLICT(channel_key) DO UPDATE SET
                        name=excluded.name,
                        meshtastic_id=COALESCE(
                            excluded.meshtastic_id,
                            logical_channels.meshtastic_id
                        ),
                        last_seen_at=excluded.last_seen_at
                    """,
                    (
                        binding.channel_key,
                        binding.name,
                        (
                            str(binding.meshtastic_id)
                            if binding.meshtastic_id is not None
                            else None
                        ),
                        seen_at,
                        seen_at,
                    ),
                )
                connection.execute(
                    """
                    INSERT INTO channel_bindings (
                        local_node_id, channel_index, channel_key, name, role,
                        meshtastic_id, gateway_profile_id, first_seen_at,
                        last_seen_at, is_active
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 1)
                    ON CONFLICT(local_node_id, channel_index) DO UPDATE SET
                        channel_key=excluded.channel_key,
                        name=excluded.name,
                        role=excluded.role,
                        meshtastic_id=excluded.meshtastic_id,
                        gateway_profile_id=excluded.gateway_profile_id,
                        last_seen_at=excluded.last_seen_at,
                        is_active=1
                    """,
                    (
                        local_node_id,
                        binding.channel_index,
                        binding.channel_key,
                        binding.name,
                        binding.role,
                        (
                            str(binding.meshtastic_id)
                            if binding.meshtastic_id is not None
                            else None
                        ),
                        gateway_profile_id,
                        seen_at,
                        seen_at,
                    ),
                )

    def list_channel_bindings(
        self,
        local_node_id: str | None = None,
        *,
        active_only: bool = False,
    ) -> list[dict[str, Any]]:
        where: list[str] = []
        params: list[Any] = []
        if local_node_id:
            where.append("bindings.local_node_id=?")
            params.append(local_node_id)
        if active_only:
            where.append("bindings.is_active=1")
        clause = f"WHERE {' AND '.join(where)}" if where else ""
        with self._connect() as connection:
            rows = connection.execute(
                f"""
                SELECT bindings.*, channels.name AS channel_name
                FROM channel_bindings AS bindings
                JOIN logical_channels AS channels
                  ON channels.channel_key=bindings.channel_key
                {clause}
                ORDER BY bindings.local_node_id, bindings.channel_index
                """,  # nosec B608 -- WHERE-ledda er faste lokale konstantar
                params,
            ).fetchall()
        result = []
        for row in rows:
            item = dict(row)
            item["is_active"] = bool(item["is_active"])
            item["display_name"] = item["name"] or f"Kanal {item['channel_index']}"
            item["conversation"] = f"channel:{item['channel_key']}"
            item["kind"] = "public"
            result.append(item)
        return result

    def upsert_node(self, node: Node) -> None:
        node.updated_at = node.updated_at or now_iso()
        # Manglande last_heard frå ein annan gateway er ikkje prov på at
        # registerdataa er nyare. Bevar derfor siste tidsfesta observasjon,
        # men fyll framleis inn felt som enno ikkje er kjende.
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
                    node_num=COALESCE(excluded.node_num, nodes.node_num),
                    long_name=CASE
                        WHEN nodes.long_name IS NULL THEN excluded.long_name
                        WHEN excluded.long_name IS NOT NULL
                         AND (nodes.last_heard IS NULL OR excluded.last_heard >= nodes.last_heard)
                        THEN excluded.long_name ELSE nodes.long_name END,
                    short_name=CASE
                        WHEN nodes.short_name IS NULL THEN excluded.short_name
                        WHEN excluded.short_name IS NOT NULL
                         AND (nodes.last_heard IS NULL OR excluded.last_heard >= nodes.last_heard)
                        THEN excluded.short_name ELSE nodes.short_name END,
                    hw_model=CASE
                        WHEN nodes.hw_model IS NULL THEN excluded.hw_model
                        WHEN excluded.hw_model IS NOT NULL
                         AND (nodes.last_heard IS NULL OR excluded.last_heard >= nodes.last_heard)
                        THEN excluded.hw_model ELSE nodes.hw_model END,
                    role=CASE
                        WHEN nodes.role IS NULL THEN excluded.role
                        WHEN excluded.role IS NOT NULL
                         AND (nodes.last_heard IS NULL OR excluded.last_heard >= nodes.last_heard)
                        THEN excluded.role ELSE nodes.role END,
                    last_heard=CASE
                        WHEN excluded.last_heard IS NULL THEN nodes.last_heard
                        WHEN nodes.last_heard IS NULL OR excluded.last_heard >= nodes.last_heard
                        THEN excluded.last_heard ELSE nodes.last_heard END,
                    battery_level=CASE
                        WHEN nodes.battery_level IS NULL THEN excluded.battery_level
                        WHEN excluded.battery_level IS NOT NULL
                         AND (nodes.last_heard IS NULL OR excluded.last_heard >= nodes.last_heard)
                        THEN excluded.battery_level ELSE nodes.battery_level END,
                    voltage=CASE
                        WHEN nodes.voltage IS NULL THEN excluded.voltage
                        WHEN excluded.voltage IS NOT NULL
                         AND (nodes.last_heard IS NULL OR excluded.last_heard >= nodes.last_heard)
                        THEN excluded.voltage ELSE nodes.voltage END,
                    snr=CASE
                        WHEN nodes.snr IS NULL THEN excluded.snr
                        WHEN excluded.snr IS NOT NULL
                         AND (nodes.last_heard IS NULL OR excluded.last_heard >= nodes.last_heard)
                        THEN excluded.snr ELSE nodes.snr END,
                    rssi=CASE
                        WHEN nodes.rssi IS NULL THEN excluded.rssi
                        WHEN excluded.rssi IS NOT NULL
                         AND (nodes.last_heard IS NULL OR excluded.last_heard >= nodes.last_heard)
                        THEN excluded.rssi ELSE nodes.rssi END,
                    hops_away=CASE
                        WHEN nodes.hops_away IS NULL THEN excluded.hops_away
                        WHEN excluded.hops_away IS NOT NULL
                         AND (nodes.last_heard IS NULL OR excluded.last_heard >= nodes.last_heard)
                        THEN excluded.hops_away ELSE nodes.hops_away END,
                    transport=CASE
                        WHEN excluded.transport = 'Ukjend' THEN nodes.transport
                        WHEN nodes.last_heard IS NULL OR excluded.last_heard >= nodes.last_heard
                        THEN excluded.transport ELSE nodes.transport END,
                    can_receive_dm=CASE
                        WHEN nodes.can_receive_dm IS NULL THEN excluded.can_receive_dm
                        WHEN excluded.can_receive_dm IS NOT NULL
                         AND (nodes.last_heard IS NULL OR excluded.last_heard >= nodes.last_heard)
                        THEN excluded.can_receive_dm ELSE nodes.can_receive_dm END,
                    is_local=excluded.is_local,
                    updated_at=CASE
                        WHEN nodes.last_heard IS NULL OR excluded.last_heard >= nodes.last_heard
                        THEN excluded.updated_at ELSE nodes.updated_at END
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
        term = search.strip()
        escaped = term.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
        pattern = f"%{escaped}%"
        with self._connect() as connection:
            query = f"""
                SELECT * FROM nodes
                WHERE ? = ''
                   OR node_id LIKE ? ESCAPE '\\' COLLATE NOCASE
                   OR long_name LIKE ? ESCAPE '\\' COLLATE NOCASE
                   OR short_name LIKE ? ESCAPE '\\' COLLATE NOCASE
                ORDER BY {order}
                """  # nosec B608
            rows = connection.execute(
                query, (term, pattern, pattern, pattern)
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

    @staticmethod
    def _telemetry_row(row: sqlite3.Row) -> dict[str, Any]:
        result = dict(row)
        try:
            result["metrics"] = json.loads(result["metrics"])
        except (TypeError, json.JSONDecodeError):
            result["metrics"] = {}
        return result

    @staticmethod
    def _position_row(row: sqlite3.Row) -> dict[str, Any]:
        result = dict(row)
        if result.get("metadata"):
            try:
                result["metadata"] = json.loads(result["metadata"])
            except (TypeError, json.JSONDecodeError):
                result["metadata"] = None
        return result
