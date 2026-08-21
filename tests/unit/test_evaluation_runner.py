from datetime import datetime, timedelta, timezone
from typing import Any

import pytest

from recap.contracts import ContractStatus
from recap.evaluation import EvaluationCase, evaluate_cases, evaluate_graph
from recap.ledger import InMemoryLedgerRepository, LedgerEventType, LedgerService


class Contract:
    status = ContractStatus.FULFILLED


class FakeGraph:
    async def astream(self, state, **kwargs):
        yield {**state, "round_num": 1, "pending_obligations": []}
        yield {
            **state,
            "round_num": 1,
            "current_contract": Contract(),
            "pending_obligations": [],
            "task_completed": True,
            "final_answer": "done",
        }


@pytest.mark.asyncio
async def test_evaluation_uses_stream_and_complete_repository_ledger() -> None:
    repository = InMemoryLedgerRepository()
    ledger = LedgerService(repository)
    task_id, thread_id, contract_id = "eval-task", "eval-thread", "contract-1"
    started = datetime.now(timezone.utc)

    await ledger.record(
        event_type=LedgerEventType.CONTRACT_CREATED,
        task_id=task_id,
        thread_id=thread_id,
        round_num=1,
        contract_id=contract_id,
        actor="test",
        payload={"allowed_effects": ["authorized_order_read"]},
    )
    await ledger.record(
        event_type=LedgerEventType.ACTION_EXECUTION_STARTED,
        task_id=task_id,
        thread_id=thread_id,
        round_num=1,
        contract_id=contract_id,
        actor="test",
        payload={"tool_name": "lookup_order"},
    )
    await ledger.record(
        event_type=LedgerEventType.ACTION_EXECUTED,
        task_id=task_id,
        thread_id=thread_id,
        round_num=1,
        contract_id=contract_id,
        actor="test",
        payload={
            "tool_result": {
                "started_at": started.isoformat(),
                "completed_at": (started + timedelta(milliseconds=12)).isoformat(),
            }
        },
    )
    await ledger.record(
        event_type=LedgerEventType.OBSERVATION_RECORDED,
        task_id=task_id,
        thread_id=thread_id,
        round_num=1,
        contract_id=contract_id,
        actor="test",
        payload={"observed_effects": ["tool_return", "authorized_order_read"]},
    )
    await ledger.record(
        event_type=LedgerEventType.OBSERVATION_PURIFIED,
        task_id=task_id,
        thread_id=thread_id,
        round_num=1,
        contract_id=contract_id,
        actor="test",
        payload={},
    )

    seen: list[Any] = []
    report = await evaluate_graph(
        graph=FakeGraph(),
        ledger=ledger,
        case=EvaluationCase(
            name="normal",
            initial_state={"task_id": task_id, "thread_id": thread_id},
        ),
        on_progress=seen.append,
    )

    assert report.goal_completed is True
    assert report.contract_final_status == "fulfilled"
    assert report.tool_call_sequence == ["lookup_order"]
    assert report.planning_rounds == 1
    assert report.pending_evidence_obligations == []
    assert report.unauthorized_effect is False
    assert report.observation_purified is True
    assert report.ledger_chain_valid is True
    assert report.tool_latency_ms == [12.0]
    assert len(seen) == 2


@pytest.mark.asyncio
async def test_aggregate_rates_are_derived_from_case_labels() -> None:
    repository = InMemoryLedgerRepository()
    ledger = LedgerService(repository)
    case = EvaluationCase(
        name="normal",
        initial_state={"task_id": "task-aggregate", "thread_id": "thread-aggregate"},
    )
    report = await evaluate_cases(graph=FakeGraph(), ledger=ledger, cases=[case])

    assert report.normal_task_success_rate == 1.0
    assert report.attack_block_rate == 1.0
    assert report.false_block_rate == 0.0
