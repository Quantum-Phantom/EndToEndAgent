from __future__ import annotations

import pytest

from recap.ledger import LedgerEventType, LedgerService, SQLiteLedgerRepository


@pytest.mark.asyncio
async def test_sqlite_ledger_persists_and_restores_hash_chain(tmp_path) -> None:
    database = tmp_path / "recap-ledger.sqlite3"
    repository = SQLiteLedgerRepository(database)
    ledger = LedgerService(repository)

    first = await ledger.record(
        event_type=LedgerEventType.TASK_CREATED,
        task_id="task-sqlite",
        thread_id="thread-1",
        round_num=0,
        actor="test",
        payload={"objective": "persist events"},
    )
    second = await ledger.record(
        event_type=LedgerEventType.CONTRACT_CREATED,
        task_id="task-sqlite",
        thread_id="thread-1",
        round_num=1,
        contract_id="contract-1",
        actor="test",
        payload={"status": "draft"},
    )
    assert second.previous_hash == first.event_hash
    await repository.close()

    restored_repository = SQLiteLedgerRepository(database)
    restored_ledger = LedgerService(restored_repository)
    restored = await restored_repository.list_events("task-sqlite", "thread-1")

    assert [event.event_id for event in restored] == [first.event_id, second.event_id]
    assert await restored_ledger.verify_chain("task-sqlite", "thread-1") is True
    await restored_repository.close()


@pytest.mark.asyncio
async def test_sqlite_ledger_keeps_threads_isolated(tmp_path) -> None:
    repository = SQLiteLedgerRepository(tmp_path / "isolated.sqlite3")
    ledger = LedgerService(repository)
    for thread_id in ("thread-a", "thread-b"):
        await ledger.record(
            event_type=LedgerEventType.TASK_CREATED,
            task_id="task-shared",
            thread_id=thread_id,
            round_num=0,
            actor="test",
            payload={"thread": thread_id},
        )

    assert len(await repository.list_events("task-shared", "thread-a")) == 1
    assert len(await repository.list_events("task-shared", "thread-b")) == 1
    assert await ledger.verify_chain("task-shared", "thread-a") is True
    assert await ledger.verify_chain("task-shared", "thread-b") is True
    await repository.close()
