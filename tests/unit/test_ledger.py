# -*- coding: utf-8 -*-

import asyncio

import pytest

from recap.ledger import (
    InMemoryLedgerRepository,
    LedgerEventType,
    LedgerService,
)


TASK_ID = "task-ledger-001"
THREAD_ID = "thread-ledger-001"


@pytest.fixture
def repository() -> InMemoryLedgerRepository:
    return InMemoryLedgerRepository()


@pytest.fixture
def ledger(repository: InMemoryLedgerRepository) -> LedgerService:
    return LedgerService(repository)


async def record_event(
    ledger: LedgerService,
    *,
    event_type: LedgerEventType = LedgerEventType.TASK_CREATED,
    task_id: str = TASK_ID,
    thread_id: str = THREAD_ID,
    round_num: int = 0,
    actor: str = "test",
    payload: dict | None = None,
    contract_id: str | None = None,
):
    return await ledger.record(
        event_type=event_type,
        task_id=task_id,
        thread_id=thread_id,
        round_num=round_num,
        actor=actor,
        payload=payload or {"value": "test"},
        contract_id=contract_id,
    )


@pytest.mark.asyncio
async def test_record_first_event(
    ledger: LedgerService,
    repository: InMemoryLedgerRepository,
) -> None:
    event = await record_event(
        ledger,
        payload={"description": "创建测试任务"},
    )
    events = await repository.list_events(TASK_ID, THREAD_ID)

    assert len(events) == 1
    assert events[0] is event
    assert event.event_id.startswith("evt-")
    assert event.event_type == LedgerEventType.TASK_CREATED
    assert event.task_id == TASK_ID
    assert event.thread_id == THREAD_ID
    assert event.previous_hash is None
    assert len(event.event_hash) == 64
    assert event.verify_hash() is True


@pytest.mark.asyncio
async def test_append_builds_hash_chain(
    ledger: LedgerService,
    repository: InMemoryLedgerRepository,
) -> None:
    first = await record_event(ledger)
    second = await record_event(
        ledger,
        event_type=LedgerEventType.CONTRACT_CREATED,
        contract_id="contract-001",
        payload={"status": "draft"},
    )
    third = await record_event(
        ledger,
        event_type=LedgerEventType.CONTRACT_ACTIVATED,
        contract_id="contract-001",
        payload={"status": "active"},
    )
    events = await repository.list_events(TASK_ID, THREAD_ID)

    assert events == [first, second, third]
    assert first.previous_hash is None
    assert second.previous_hash == first.event_hash
    assert third.previous_hash == second.event_hash
    assert all(event.verify_hash() for event in events)


@pytest.mark.asyncio
async def test_verify_valid_chain(ledger: LedgerService) -> None:
    await record_event(ledger)
    await record_event(
        ledger,
        event_type=LedgerEventType.CONTRACT_CREATED,
        contract_id="contract-001",
    )
    await record_event(
        ledger,
        event_type=LedgerEventType.ACTION_APPROVED,
        contract_id="contract-001",
    )

    assert await ledger.verify_chain(TASK_ID, THREAD_ID) is True


@pytest.mark.asyncio
async def test_detect_payload_tampering(
    ledger: LedgerService,
    repository: InMemoryLedgerRepository,
) -> None:
    await record_event(ledger, payload={"approved": False})
    events = await repository.list_events(TASK_ID, THREAD_ID)

    events[0].payload["approved"] = True

    assert events[0].verify_hash() is False
    assert await ledger.verify_chain(TASK_ID, THREAD_ID) is False


@pytest.mark.asyncio
async def test_detect_hash_tampering(
    ledger: LedgerService,
    repository: InMemoryLedgerRepository,
) -> None:
    await record_event(ledger)
    events = await repository.list_events(TASK_ID, THREAD_ID)

    events[0].event_hash = "0" * 64

    assert events[0].verify_hash() is False
    assert await ledger.verify_chain(TASK_ID, THREAD_ID) is False


@pytest.mark.asyncio
async def test_detect_previous_hash_tampering(
    ledger: LedgerService,
    repository: InMemoryLedgerRepository,
) -> None:
    await record_event(ledger)
    await record_event(
        ledger,
        event_type=LedgerEventType.CONTRACT_CREATED,
        contract_id="contract-001",
    )
    events = await repository.list_events(TASK_ID, THREAD_ID)

    events[1].previous_hash = "f" * 64

    assert events[1].verify_hash() is False
    assert await ledger.verify_chain(TASK_ID, THREAD_ID) is False


@pytest.mark.asyncio
async def test_threads_are_isolated(
    ledger: LedgerService,
    repository: InMemoryLedgerRepository,
) -> None:
    event_a1 = await record_event(ledger, thread_id="thread-A")
    event_a2 = await record_event(
        ledger,
        thread_id="thread-A",
        event_type=LedgerEventType.CONTRACT_CREATED,
        contract_id="contract-A",
    )
    event_b1 = await record_event(ledger, thread_id="thread-B")

    events_a = await repository.list_events(TASK_ID, "thread-A")
    events_b = await repository.list_events(TASK_ID, "thread-B")

    assert events_a == [event_a1, event_a2]
    assert events_b == [event_b1]
    assert event_a1.previous_hash is None
    assert event_a2.previous_hash == event_a1.event_hash
    assert event_b1.previous_hash is None
    assert await ledger.verify_chain(TASK_ID, "thread-A") is True
    assert await ledger.verify_chain(TASK_ID, "thread-B") is True


@pytest.mark.asyncio
async def test_tasks_are_isolated(
    ledger: LedgerService,
    repository: InMemoryLedgerRepository,
) -> None:
    task_a = await record_event(ledger, task_id="task-A")
    task_b = await record_event(ledger, task_id="task-B")

    events_a = await repository.list_events("task-A", THREAD_ID)
    events_b = await repository.list_events("task-B", THREAD_ID)

    assert events_a == [task_a]
    assert events_b == [task_b]
    assert task_a.previous_hash is None
    assert task_b.previous_hash is None


@pytest.mark.asyncio
async def test_concurrent_appends_keep_chain_valid(
    ledger: LedgerService,
    repository: InMemoryLedgerRepository,
) -> None:
    count = 50

    created = await asyncio.gather(
        *(
            record_event(
                ledger,
                event_type=LedgerEventType.OBSERVATION_RECORDED,
                round_num=index,
                payload={"index": index},
            )
            for index in range(count)
        )
    )
    events = await repository.list_events(TASK_ID, THREAD_ID)

    assert len(created) == count
    assert len(events) == count
    assert {event.event_id for event in events} == {
        event.event_id for event in created
    }
    assert events[0].previous_hash is None

    for previous, current in zip(events, events[1:]):
        assert current.previous_hash == previous.event_hash

    assert await ledger.verify_chain(TASK_ID, THREAD_ID) is True