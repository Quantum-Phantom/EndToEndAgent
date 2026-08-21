"""PostgreSQL-backed append-only Ledger repository."""

from __future__ import annotations

import json
import asyncio
from typing import Any

import asyncpg

from recap.ledger.models import LedgerEvent


class PostgresLedgerRepository:
    def __init__(
        self,
        pool: asyncpg.Pool | None = None,
        *,
        dsn: str | None = None,
        min_size: int = 1,
        max_size: int = 10,
    ) -> None:
        if pool is None and not dsn:
            raise ValueError("either pool or dsn must be supplied")
        self._pool = pool
        self._dsn = dsn
        self._min_size = min_size
        self._max_size = max_size
        self._pool_lock = asyncio.Lock()
        self._initialized = False

    @classmethod
    def from_dsn(
        cls,
        dsn: str,
        *,
        min_size: int = 1,
        max_size: int = 10,
    ) -> "PostgresLedgerRepository":
        return cls(dsn=dsn, min_size=min_size, max_size=max_size)

    @classmethod
    async def connect(
        cls,
        dsn: str,
        *,
        min_size: int = 1,
        max_size: int = 10,
    ) -> "PostgresLedgerRepository":
        pool = await asyncpg.create_pool(dsn, min_size=min_size, max_size=max_size)
        repository = cls(pool)
        await repository.initialize()
        return repository

    async def initialize(self) -> None:
        pool = await self._ensure_pool()
        if self._initialized:
            return
        async with pool.acquire() as connection:
            await connection.execute(
                """
                CREATE TABLE IF NOT EXISTS recap_ledger_events (
                    sequence_id BIGSERIAL PRIMARY KEY,
                    event_id TEXT NOT NULL UNIQUE,
                    task_id TEXT NOT NULL,
                    thread_id TEXT NOT NULL,
                    event_data JSONB NOT NULL
                )
                """
            )
            await connection.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_recap_ledger_task_thread_sequence
                ON recap_ledger_events(task_id, thread_id, sequence_id)
                """
            )
        self._initialized = True

    async def last_event(self, task_id: str, thread_id: str) -> LedgerEvent | None:
        await self.initialize()
        assert self._pool is not None
        row = await self._pool.fetchrow(
            """
            SELECT event_data FROM recap_ledger_events
            WHERE task_id = $1 AND thread_id = $2
            ORDER BY sequence_id DESC LIMIT 1
            """,
            task_id,
            thread_id,
        )
        return self._event(row["event_data"]) if row else None

    async def append(self, event: LedgerEvent) -> None:
        await self.initialize()
        assert self._pool is not None
        await self._pool.execute(
            """
            INSERT INTO recap_ledger_events(event_id, task_id, thread_id, event_data)
            VALUES ($1, $2, $3, $4::jsonb)
            """,
            event.event_id,
            event.task_id,
            event.thread_id,
            event.model_dump_json(),
        )

    async def list_events(self, task_id: str, thread_id: str) -> list[LedgerEvent]:
        await self.initialize()
        assert self._pool is not None
        rows = await self._pool.fetch(
            """
            SELECT event_data FROM recap_ledger_events
            WHERE task_id = $1 AND thread_id = $2
            ORDER BY sequence_id ASC
            """,
            task_id,
            thread_id,
        )
        return [self._event(row["event_data"]) for row in rows]

    async def close(self) -> None:
        if self._pool is not None:
            await self._pool.close()
            self._pool = None
            self._initialized = False

    async def _ensure_pool(self) -> asyncpg.Pool:
        if self._pool is not None:
            return self._pool
        async with self._pool_lock:
            if self._pool is None:
                assert self._dsn is not None
                self._pool = await asyncpg.create_pool(
                    self._dsn,
                    min_size=self._min_size,
                    max_size=self._max_size,
                )
        return self._pool

    @staticmethod
    def _event(value: Any) -> LedgerEvent:
        if isinstance(value, str):
            value = json.loads(value)
        return LedgerEvent.model_validate(value)
