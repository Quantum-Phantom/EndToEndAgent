"""Ledger service with atomic per-thread append and chain verification."""

from __future__ import annotations

import asyncio
from collections import defaultdict
from typing import Protocol

from recap.ledger.models import LedgerEvent, LedgerEventType


class LedgerRepository(Protocol):
    async def last_event(self, task_id: str, thread_id: str) -> LedgerEvent | None: ...

    async def append(self, event: LedgerEvent) -> None: ...

    async def list_events(self, task_id: str, thread_id: str) -> list[LedgerEvent]: ...


class InMemoryLedgerRepository:
    """Development repository; replace with a PostgreSQL implementation."""

    def __init__(self) -> None:
        self._events: dict[tuple[str, str], list[LedgerEvent]] = defaultdict(list)

    async def last_event(self, task_id: str, thread_id: str) -> LedgerEvent | None:
        events = self._events[(task_id, thread_id)]
        return events[-1] if events else None

    async def append(self, event: LedgerEvent) -> None:
        self._events[(event.task_id, event.thread_id)].append(event)

    async def list_events(self, task_id: str, thread_id: str) -> list[LedgerEvent]:
        return list(self._events[(task_id, thread_id)])


class LedgerService:
    def __init__(self, repository: LedgerRepository) -> None:
        self.repository = repository
        self._locks: dict[tuple[str, str], asyncio.Lock] = defaultdict(asyncio.Lock)

    async def record(
        self,
        *,
        event_type: LedgerEventType,
        task_id: str,
        thread_id: str,
        round_num: int,
        actor: str,
        payload: dict,
        contract_id: str | None = None,
    ) -> LedgerEvent:
        key = (task_id, thread_id)
        async with self._locks[key]:
            previous = await self.repository.last_event(task_id, thread_id)
            event = LedgerEvent.create(
                event_type=event_type,
                task_id=task_id,
                thread_id=thread_id,
                round_num=round_num,
                actor=actor,
                payload=payload,
                contract_id=contract_id,
                previous_hash=previous.event_hash if previous else None,
            )
            await self.repository.append(event)
            return event

    async def verify_chain(self, task_id: str, thread_id: str) -> bool:
        events = await self.repository.list_events(task_id, thread_id)
        previous_hash: str | None = None
        for event in events:
            if event.previous_hash != previous_hash or not event.verify_hash():
                return False
            previous_hash = event.event_hash
        return True
