"""Daemon-owned, physically separate history stores for confirmed gateways.

The old database and a consistent pre-migration snapshot remain untouched.
Only records with an explicit gateway identity enter a gateway database.
"""
from __future__ import annotations

import hashlib
import inspect
import json
import os
import re
import sqlite3
import threading
import uuid
from array import array
from collections.abc import Iterable, Iterator
from contextlib import closing, suppress
from datetime import datetime, timedelta, timezone
from itertools import chain
from pathlib import Path
from typing import Any

from meshpi.database import DATABASE_SCHEMA_VERSION, Database
from meshpi.i18n import tr
from meshpi.models import Message, MessageStatus, normalize_node_id

SCOPED_TABLES = (
    "nodes", "messages", "channel_bindings", "message_observations",
    "telemetry_samples", "positions", "node_actions",
    "archived_conversations", "archived_conversation_ids",
)
HISTORY_TABLES = ("messages", "telemetry_samples", "positions", "node_actions")
MAX_CLEANUP_RECORDS = 1_000_000
MAINTENANCE_BATCH_RECORDS = 100_000
HISTORY_SCHEMA = """
CREATE TABLE IF NOT EXISTS history_settings (
    local_node_id TEXT PRIMARY KEY,
    retention_days INTEGER NOT NULL CHECK(retention_days BETWEEN 0 AND 36500),
    needs_review INTEGER NOT NULL CHECK(needs_review IN (0,1))
);
CREATE INDEX IF NOT EXISTS messages_received_history ON messages(received_at, id);
CREATE INDEX IF NOT EXISTS telemetry_received_history ON telemetry_samples(received_at, id);
CREATE INDEX IF NOT EXISTS positions_received_history ON positions(received_at, id);
CREATE INDEX IF NOT EXISTS actions_finished_history ON node_actions(finished_at);
"""


def _days(value: int) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or not 0 <= value <= 36500:
        raise ValueError("Vel eit heilt tal dagar mellom 0 og 36500.")
    return value


def _action_fingerprint(row: sqlite3.Row) -> bytes:
    identity = (row["action_id"], row["started_at"], row["finished_at"])
    return hashlib.blake2b(json.dumps(identity).encode("utf-8"), digest_size=16).digest()


def _history_timestamp(value: Any) -> float | None:
    if not isinstance(value, str) or "T" not in value:
        return None
    try:
        parsed = datetime.fromisoformat(value)
        if parsed.tzinfo is None:
            return None
        return parsed.timestamp()
    except (ValueError, OverflowError, OSError):
        return None


def _read_only(path: Path) -> sqlite3.Connection:
    connection = sqlite3.connect(path.resolve().as_uri() + "?mode=ro", uri=True)
    connection.row_factory = sqlite3.Row
    return connection


def _check_schema(path: Path) -> None:
    with closing(_read_only(path)) as connection:
        if connection.execute("PRAGMA user_version").fetchone()[0] > DATABASE_SCHEMA_VERSION:
            raise ValueError(tr("database.schema_too_new"))


def _snapshot(source: Path, target: Path) -> None:
    """Publish a complete SQLite snapshot without exposing a partial database."""
    temporary = target.with_name(f".{target.name}.{uuid.uuid4().hex}.tmp")
    try:
        descriptor = os.open(temporary, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
        os.close(descriptor)
        with closing(_read_only(source)) as original, closing(sqlite3.connect(temporary)) as copy:
            original.backup(copy)
            copy.execute("PRAGMA journal_mode=DELETE")
            if copy.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
                raise ValueError("Databasesikringa kunne ikkje kontrollerast.")
        os.replace(temporary, target)
    finally:
        temporary.unlink(missing_ok=True)


def _content_digest(path: Path) -> bytes:
    # Logical content avoids false alarms when SQLite checkpoints an unchanged WAL.
    digest = hashlib.sha256()
    with closing(_read_only(path)) as connection:
        connection.execute("BEGIN")
        for line in connection.iterdump():
            digest.update(line.encode("utf-8"))
            digest.update(b"\n")
    return digest.digest()


class GatewayDatabase:
    """Route the existing Database API by its explicit local_node_id argument."""

    def __init__(self, path: str | Path, max_messages: int = 50_000):
        self.path = Path(path)
        self.directory = self.path.with_name(self.path.stem + "-gateways")
        self.snapshot_path = self.directory / "legacy.sqlite3"
        self.migration_path = self.directory / "legacy-upgraded.sqlite3"
        self.max_messages = max(1000, max_messages)
        self._databases: dict[str, Database] = {}
        self._lock = threading.RLock()
        self._initialized = False

    def initialize(self) -> None:
        with self._lock:
            if self._initialized:
                return
            # Reject future schemas before touching either source or destination.
            if self.path.exists():
                _check_schema(self.path)
            self.directory.mkdir(mode=0o700, parents=True, exist_ok=True)
            if os.name == "posix":
                self.directory.chmod(0o700)
            if self.path.exists() and not self.snapshot_path.exists():
                _snapshot(self.path, self.snapshot_path)
            if (self.path.exists() and self.snapshot_path.exists()
                    and _content_digest(self.path) != _content_digest(self.snapshot_path)):
                raise ValueError(
                    "Den gamle databasen er endra etter oppdelinga. "
                    "Begge historikkane er bevarte. Stopp eldre MeshPi og få hjelp "
                    "til å flette dei før du startar denne versjonen igjen."
                )
            if self.snapshot_path.exists() and not self.migration_path.exists():
                _check_schema(self.snapshot_path)
                work = self.directory / f".migration-{uuid.uuid4().hex}.sqlite3"
                try:
                    _snapshot(self.snapshot_path, work)
                    Database(work, observation_retention_days=0).initialize(prune=False)
                    _snapshot(work, self.migration_path)
                finally:
                    # These are private disposable migration files, never user sources.
                    for suffix in ("", "-wal", "-shm"):
                        Path(str(work) + suffix).unlink(missing_ok=True)
            gateway_ids = set()
            if self.migration_path.exists():
                _check_schema(self.migration_path)
                with closing(_read_only(self.migration_path)) as source:
                    for table in SCOPED_TABLES:
                        rows = source.execute(
                            f'SELECT DISTINCT local_node_id FROM "{table}"'
                        )  # nosec B608 -- fixed table constants
                        for row in rows:
                            with suppress(ValueError):
                                gateway_ids.add(normalize_node_id(row[0] or ""))
            for path in self.directory.glob("*.sqlite3"):
                if re.fullmatch(r"[0-9a-f]{8}", path.stem):
                    gateway_ids.add(normalize_node_id(path.stem))
            for gateway_id in sorted(gateway_ids):
                self._open_gateway(gateway_id)
            self._initialized = True

    def gateway_path(self, local_node_id: str) -> Path:
        gateway_id = normalize_node_id(local_node_id)
        return self.directory / f"{gateway_id[1:]}.sqlite3"

    def for_gateway(self, local_node_id: str) -> Database:
        gateway_id = normalize_node_id(local_node_id)
        with self._lock:
            if not self._initialized:
                self.initialize()
            return self._open_gateway(gateway_id)

    def _open_gateway(self, gateway_id: str) -> Database:
        if gateway_id in self._databases:
            return self._databases[gateway_id]
        target = self.gateway_path(gateway_id)
        if not target.exists():
            self._create_gateway(gateway_id, target)
        database = Database(target, self.max_messages, observation_retention_days=0)
        database.initialize(prune=False)
        with database._connect() as connection:
            connection.executescript(HISTORY_SCHEMA)
            settings = connection.execute(
                "SELECT local_node_id FROM history_settings"
            ).fetchall()
            if len(settings) != 1 or settings[0][0] != gateway_id:
                raise ValueError("Gateway-databasen har feil eller manglande nodeidentitet.")
        self._databases[gateway_id] = database
        return database

    def _create_gateway(self, gateway_id: str, target: Path) -> None:
        temporary = self.directory / f".{target.stem}-{uuid.uuid4().hex}.sqlite3"
        migrated = False
        try:
            database = Database(temporary, self.max_messages, observation_retention_days=0)
            database.initialize(prune=False)
            with database._connect() as destination:
                destination.executescript(HISTORY_SCHEMA)
                destination.execute("BEGIN IMMEDIATE")
                if self.migration_path.exists():
                    with closing(_read_only(self.migration_path)) as source:
                        # Channels have no gateway column. Copy only keys referenced here.
                        keys = {row[0] for row in source.execute(
                            "SELECT channel_key FROM channel_bindings WHERE LOWER(local_node_id)=? "
                            "UNION SELECT channel_key FROM messages WHERE LOWER(local_node_id)=?",
                            (gateway_id, gateway_id),
                        ) if row[0]}
                        for key in keys:
                            rows = source.execute(
                                "SELECT * FROM logical_channels WHERE channel_key=?", (key,)
                            ).fetchall()
                            self._copy_rows(destination, "logical_channels", rows)
                        for table in SCOPED_TABLES:
                            rows = source.execute(
                                f'SELECT * FROM "{table}" WHERE LOWER(local_node_id)=?',
                                (gateway_id,),
                            )  # nosec B608 -- fixed table constants
                            migrated = self._copy_rows(destination, table, rows) or migrated
                destination.execute(
                    "INSERT INTO history_settings VALUES (?, ?, ?)",
                    (gateway_id, 0 if migrated else 30, int(migrated)),
                )
                if destination.execute("PRAGMA foreign_key_check").fetchone() is not None:
                    raise ValueError("Gateway-historikken inneheld ugyldige referansar.")
            # Checkpoint into a standalone private file before the atomic rename.
            with closing(sqlite3.connect(temporary)) as connection:
                connection.execute("PRAGMA wal_checkpoint(TRUNCATE)")
                connection.execute("PRAGMA journal_mode=DELETE")
                if connection.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
                    raise ValueError("Gateway-databasen kunne ikkje kontrollerast.")
            os.replace(temporary, target)
        finally:
            for suffix in ("", "-wal", "-shm"):
                Path(str(temporary) + suffix).unlink(missing_ok=True)

    @staticmethod
    def _copy_rows(
        destination: sqlite3.Connection, table: str, rows: Iterable[sqlite3.Row],
    ) -> bool:
        iterator = iter(rows)
        first = next(iterator, None)
        if first is None:
            return False
        columns = first.keys()
        names = ",".join('"' + column.replace('"', '""') + '"' for column in columns)
        placeholders = ",".join("?" for _ in columns)
        destination.executemany(
            f'INSERT INTO "{table}" ({names}) VALUES ({placeholders})',
            (tuple(row) for row in chain((first,), iterator)),
        )  # nosec B608 -- table is fixed, column names are quoted schema identifiers
        return True

    def __getattr__(self, name: str) -> Any:
        method = getattr(Database, name, None)
        if name.startswith("_") or not callable(method):
            raise AttributeError(name)
        signature = inspect.signature(method)

        def routed(*args: Any, **kwargs: Any) -> Any:
            bound = signature.bind_partial(None, *args, **kwargs)
            gateway_id = bound.arguments.get("local_node_id")
            if not gateway_id:
                for value in bound.arguments.values():
                    if isinstance(value, Message):
                        gateway_id = value.local_node_id
                    elif isinstance(value, dict):
                        gateway_id = value.get("local_node_id") or value.get("gateway_node_id")
                    if gateway_id:
                        break
            if not gateway_id:
                raise ValueError("Vel ein gateway før du opnar historikken.")
            return getattr(self.for_gateway(gateway_id), name)(*args, **kwargs)
        return routed

    def fail_started_node_actions(self, error: str) -> int:
        self.initialize()
        with self._lock:
            databases = list(self._databases.values())
        return sum(database.fail_started_node_actions(error) for database in databases)

    def export_records(
        self, meshpi_version: str, *, local_node_id: str,
    ) -> Iterator[dict[str, Any]]:
        gateway_id = normalize_node_id(local_node_id)
        policy = self.history_settings(gateway_id)
        for record in self.for_gateway(gateway_id).export_records(meshpi_version):
            if record["record"] == "metadata":
                record = {**record, "local_node_id": gateway_id, "history_settings": {
                    "retention_days": policy["retention_days"],
                    "needs_review": policy["needs_review"],
                    "max_messages": policy["max_messages"],
                }}
            yield record

    def history_settings(self, local_node_id: str) -> dict[str, Any]:
        gateway_id = normalize_node_id(local_node_id)
        database = self.for_gateway(gateway_id)
        with database._connect() as connection:
            connection.execute("BEGIN")
            row = connection.execute("SELECT * FROM history_settings").fetchone()
            counts = {
                table: connection.execute(f'SELECT COUNT(*) FROM "{table}"').fetchone()[0]
                for table in HISTORY_TABLES
            }  # nosec B608 -- fixed table constants
        return {
            "local_node_id": gateway_id, "retention_days": row["retention_days"],
            "needs_review": bool(row["needs_review"]), "max_messages": self.max_messages,
            "counts": counts,
        }

    def set_history_policy(self, local_node_id: str, days: int) -> dict[str, Any]:
        days = _days(days)
        database = self.for_gateway(local_node_id)
        with database._connect() as connection:
            connection.execute(
                "UPDATE history_settings SET retention_days=?, needs_review=0", (days,)
            )
        return self.history_settings(local_node_id)

    def preview_history_cleanup(
        self, local_node_id: str, *, scope: str = "messages", older_than_days: int = 0,
        now: datetime | None = None,
    ) -> dict[str, Any]:
        return self._preview_history_cleanup(
            local_node_id, scope=scope, older_than_days=older_than_days, now=now,
            maximum=MAX_CLEANUP_RECORDS, partial=False,
        )

    def _preview_history_cleanup(
        self, local_node_id: str, *, scope: str, older_than_days: int,
        now: datetime | None, maximum: int, partial: bool,
    ) -> dict[str, Any]:
        days = _days(older_than_days)
        if scope not in {"messages", "history"}:
            raise ValueError("Vel meldingar eller all historikk.")
        gateway_id = normalize_node_id(local_node_id)
        database = self.for_gateway(gateway_id)
        now = now or datetime.now(timezone.utc)
        cutoff = (now - timedelta(days=days)).isoformat(timespec="microseconds") if days else None
        tables = HISTORY_TABLES if scope == "history" else ("messages",)
        snapshot: dict[str, array] = {}
        action_fingerprints = bytearray()
        remaining = maximum
        with database._connect() as connection:
            connection.create_function("meshpi_history_timestamp", 1, _history_timestamp)
            connection.execute("BEGIN")
            for table in tables:
                if partial and remaining == 0:
                    break
                column = "finished_at" if table == "node_actions" else "received_at"
                where = "1=1"
                params: list[Any] = []
                if cutoff:
                    # Invalid/missing local timestamps cannot justify age-based deletion.
                    where += f" AND meshpi_history_timestamp({column}) < ?"
                    params.append(_history_timestamp(cutoff))
                if table == "messages":
                    where += " AND NOT (direction='ut' AND (status=? OR (status=? AND want_ack=1)))"
                    params.extend((str(MessageStatus.UNCERTAIN), str(MessageStatus.QUEUED)))
                elif table == "node_actions":
                    where += " AND status IN ('completed','failed') AND finished_at IS NOT NULL"
                fields = (
                    "rowid AS history_rowid, action_id, started_at, finished_at"
                    if table == "node_actions" else "id"
                )
                rows = connection.execute(
                    f'SELECT {fields} FROM "{table}" WHERE {where} ORDER BY rowid LIMIT ?',
                    (*params, remaining if partial else remaining + 1),
                )  # nosec B608 -- fixed local table/column clauses
                ids = array("q")
                snapshot[table] = ids
                for row in rows:
                    if remaining == 0:
                        raise ValueError(
                            "Utvalet har meir enn éin million historikkpostar. "
                            "Vel ein eldre periode eller rydd meldingar først. "
                            "Automatisk opprydding tek store utval i mindre omgangar."
                        )
                    if table == "node_actions":
                        ids.append(row["history_rowid"])
                        action_fingerprints.extend(_action_fingerprint(row))
                    else:
                        ids.append(row["id"])
                    remaining -= 1
        return {
            "local_node_id": gateway_id, "scope": scope, "older_than_days": days,
            "cutoff": cutoff, "created_at": now.isoformat(),
            "counts": {table: len(snapshot.get(table, [])) for table in HISTORY_TABLES},
            "_snapshot": snapshot, "_action_fingerprints": bytes(action_fingerprints),
        }

    def cleanup_history(self, preview: dict[str, Any]) -> dict[str, int]:
        """Execute a daemon-held preview, never a client-provided deletion plan."""
        database = self.for_gateway(preview["local_node_id"])
        deleted = dict.fromkeys(HISTORY_TABLES, 0)
        snapshot = preview["_snapshot"]
        with database._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            if "_policy_days" in preview:
                policy = connection.execute("SELECT * FROM history_settings").fetchone()
                if policy["needs_review"] or policy["retention_days"] != preview["_policy_days"]:
                    return deleted
            for table in HISTORY_TABLES:
                ids = snapshot.get(table, ())
                if table == "node_actions":
                    fingerprints = preview.get("_action_fingerprints", b"")
                    if len(fingerprints) != 16 * len(ids):
                        raise ValueError("Førehandsvisinga av nodehandlingane er ugyldig.")
                    for index, identity in enumerate(ids):
                        row = connection.execute(
                            "SELECT action_id, started_at, finished_at FROM node_actions "
                            "WHERE rowid=? AND status IN ('completed','failed')", (identity,),
                        ).fetchone()
                        if row is None or _action_fingerprint(row) != fingerprints[
                            index * 16:(index + 1) * 16
                        ]:
                            continue
                        cursor = connection.execute(
                            "DELETE FROM node_actions WHERE rowid=?", (identity,),
                        )
                        deleted[table] += cursor.rowcount
                elif table == "messages":
                    cursor = connection.executemany(
                        "DELETE FROM messages WHERE id=? "
                        "AND NOT (direction='ut' AND (status=? OR (status=? AND want_ack=1)))",
                        ((identity, str(MessageStatus.UNCERTAIN), str(MessageStatus.QUEUED))
                         for identity in ids),
                    )
                    deleted[table] = cursor.rowcount
                else:
                    cursor = connection.executemany(
                        f'DELETE FROM "{table}" WHERE id=?', ((identity,) for identity in ids),
                    )  # nosec B608 -- fixed table constants
                    deleted[table] = cursor.rowcount
        return deleted

    def maintain_history(self, *, now: datetime | None = None) -> dict[str, dict[str, int]]:
        """Called by daemon maintenance; zero days explicitly disables age pruning."""
        self.initialize()
        with self._lock:
            gateway_ids = list(self._databases)
        result = {}
        for gateway_id in gateway_ids:
            policy = self.history_settings(gateway_id)
            if policy["needs_review"] or not policy["retention_days"]:
                continue
            preview = self._preview_history_cleanup(
                gateway_id, scope="history", older_than_days=policy["retention_days"], now=now,
                maximum=MAINTENANCE_BATCH_RECORDS, partial=True,
            )
            preview["_policy_days"] = policy["retention_days"]
            result[gateway_id] = self.cleanup_history(preview)
        return result
