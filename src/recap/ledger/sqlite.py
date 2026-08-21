"""SQLite-backed append-only Ledger repository."""

from __future__ import annotations

import asyncio
import sqlite3
import threading
from pathlib import Path

from recap.ledger.models import LedgerEvent


class SQLiteLedgerRepository:
    def __init__(self, database: str | Path) -> None:
        path = str(database)
        if path != ":memory:":
            Path(path).expanduser().resolve().parent.mkdir(parents=True, exist_ok=True)
        self._connection = sqlite3.connect(path, check_same_thread=False)
        self._lock = threading.Lock()
        with self._connection:
            self._connection.execute(
                """
                CREATE TABLE IF NOT EXISTS ledger_events (
                    sequence_id INTEGER PRIMARY KEY AUTOINCREMENT,
                    event_id TEXT NOT NULL UNIQUE,
                    task_id TEXT NOT NULL,
                    thread_id TEXT NOT NULL,
                    event_data TEXT NOT NULL
                )
                """
            )
            self._connection.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_ledger_task_thread_sequence
                ON ledger_events(task_id, thread_id, sequence_id)
                """
            )

    async def last_event(self, task_id: str, thread_id: str) -> LedgerEvent | None:
        return await asyncio.to_thread(self._last_event, task_id, thread_id)

    async def append(self, event: LedgerEvent) -> None:
        await asyncio.to_thread(self._append, event)

    async def list_events(self, task_id: str, thread_id: str) -> list[LedgerEvent]:
        return await asyncio.to_thread(self._list_events, task_id, thread_id)

    async def close(self) -> None:
        await asyncio.to_thread(self._connection.close)

    def _last_event(self, task_id: str, thread_id: str) -> LedgerEvent | None:
        with self._lock:
            row = self._connection.execute(
                """
                SELECT event_data FROM ledger_events
                WHERE task_id = ? AND thread_id = ?
                ORDER BY sequence_id DESC LIMIT 1
                """,
                (task_id, thread_id),
            ).fetchone()
        return LedgerEvent.model_validate_json(row[0]) if row else None

    def _append(self, event: LedgerEvent) -> None:
        with self._lock, self._connection:
            self._connection.execute(
                """
                INSERT INTO ledger_events(event_id, task_id, thread_id, event_data)
                VALUES (?, ?, ?, ?)
                """,
                (
                    event.event_id,
                    event.task_id,
                    event.thread_id,
                    event.model_dump_json(),
                ),
            )

    def _list_events(self, task_id: str, thread_id: str) -> list[LedgerEvent]:
        with self._lock:
            rows = self._connection.execute(
                """
                SELECT event_data FROM ledger_events
                WHERE task_id = ? AND thread_id = ?
                ORDER BY sequence_id ASC
                """,
                (task_id, thread_id),
            ).fetchall()
        return [LedgerEvent.model_validate_json(row[0]) for row in rows]
