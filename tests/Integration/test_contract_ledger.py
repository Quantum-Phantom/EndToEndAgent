# -*- coding: utf-8 -*-

import pytest

from recap.contracts import ContractStatus, RuntimeContract
from recap.integration import (
    create_contract_and_record,
    record_violation,
    transition_contract_and_record,
)
from recap.ledger import (
    InMemoryLedgerRepository,
    LedgerEventType,
    LedgerService,
)
from recap.schemas import IntentCertificate


TASK_ID = "task-contract-ledger-001"
THREAD_ID = "thread-contract-ledger-001"


@pytest.fixture
def certificate() -> IntentCertificate:
    return IntentCertificate(
        round_num=1,
        subgoal="计算 3 和 4 的和",
        proposed_operation="add",
        argument_constraints={
            "a": {"eq": 3},
            "b": {"eq": 4},
        },
        authority_basis="user_request:test-001",
        expected_effect="返回 7，不产生外部副作用",
        required_evidence=["tool_return", "call_id_binding"],
    )


@pytest.fixture
def repository() -> InMemoryLedgerRepository:
    return InMemoryLedgerRepository()


@pytest.fixture
def ledger(repository: InMemoryLedgerRepository) -> LedgerService:
    return LedgerService(repository)


async def create_contract(
    ledger: LedgerService,
    certificate: IntentCertificate,
) -> tuple[RuntimeContract, object]:
    return await create_contract_and_record(
        ledger=ledger,
        task_id=TASK_ID,
        thread_id=THREAD_ID,
        certificate=certificate,
        allowed_tools=["add"],
        permissions=["arithmetic:execute"],
        policy_refs=["POLICY-ARITHMETIC-001"],
    )


@pytest.mark.asyncio
async def test_contract_creation_is_recorded(
    ledger: LedgerService,
    repository: InMemoryLedgerRepository,
    certificate: IntentCertificate,
) -> None:
    contract, event = await create_contract(ledger, certificate)
    events = await repository.list_events(TASK_ID, THREAD_ID)

    assert contract.status == ContractStatus.DRAFT
    assert event.event_type == LedgerEventType.CONTRACT_CREATED
    assert event.contract_id == contract.contract_id
    assert event.task_id == contract.task_id
    assert event.round_num == contract.round_num
    assert event.payload["status"] == ContractStatus.DRAFT.value
    assert events == [event]
    assert await ledger.verify_chain(TASK_ID, THREAD_ID) is True


@pytest.mark.asyncio
async def test_successful_contract_lifecycle_creates_a_valid_ledger_chain(
    ledger: LedgerService,
    repository: InMemoryLedgerRepository,
    certificate: IntentCertificate,
) -> None:
    draft, created = await create_contract(ledger, certificate)

    active, activated = await transition_contract_and_record(
        ledger=ledger,
        contract=draft,
        thread_id=THREAD_ID,
        target=ContractStatus.ACTIVE,
        event_type=LedgerEventType.CONTRACT_ACTIVATED,
        actor="think_act_check_node",
        details={"check": "passed", "tool": "add"},
    )
    executing, started = await transition_contract_and_record(
        ledger=ledger,
        contract=active,
        thread_id=THREAD_ID,
        target=ContractStatus.EXECUTING,
        event_type=LedgerEventType.CONTRACT_EXECUTING,
        actor="act_node",
        details={"call_id": "call-001"},
    )
    fulfilled, completed = await transition_contract_and_record(
        ledger=ledger,
        contract=executing,
        thread_id=THREAD_ID,
        target=ContractStatus.FULFILLED,
        event_type=LedgerEventType.CONTRACT_FULFILLED,
        actor="act_observe_check_node",
        details={
            "evidence_collected": ["tool_return", "call_id_binding"],
        },
    )
    events = await repository.list_events(TASK_ID, THREAD_ID)

    assert draft.status == ContractStatus.DRAFT
    assert active.status == ContractStatus.ACTIVE
    assert executing.status == ContractStatus.EXECUTING
    assert fulfilled.status == ContractStatus.FULFILLED
    assert [event.event_type for event in events] == [
        LedgerEventType.CONTRACT_CREATED,
        LedgerEventType.CONTRACT_ACTIVATED,
        LedgerEventType.CONTRACT_EXECUTING,
        LedgerEventType.CONTRACT_FULFILLED,
    ]
    assert events == [created, activated, started, completed]
    assert all(event.contract_id == draft.contract_id for event in events)

    for previous, current in zip(events, events[1:]):
        assert current.previous_hash == previous.event_hash

    assert await ledger.verify_chain(TASK_ID, THREAD_ID) is True


@pytest.mark.asyncio
async def test_missing_evidence_enters_pending_then_fulfills(
    ledger: LedgerService,
    repository: InMemoryLedgerRepository,
    certificate: IntentCertificate,
) -> None:
    draft, _ = await create_contract(ledger, certificate)
    active, _ = await transition_contract_and_record(
        ledger=ledger,
        contract=draft,
        thread_id=THREAD_ID,
        target=ContractStatus.ACTIVE,
        event_type=LedgerEventType.CONTRACT_ACTIVATED,
        actor="think_act_check_node",
    )
    executing, _ = await transition_contract_and_record(
        ledger=ledger,
        contract=active,
        thread_id=THREAD_ID,
        target=ContractStatus.EXECUTING,
        event_type=LedgerEventType.CONTRACT_EXECUTING,
        actor="act_node",
    )

    assert executing.missing_evidence(["tool_return"]) == {"call_id_binding"}

    pending, pending_event = await transition_contract_and_record(
        ledger=ledger,
        contract=executing,
        thread_id=THREAD_ID,
        target=ContractStatus.EVIDENCE_PENDING,
        event_type=LedgerEventType.CONTRACT_EVIDENCE_PENDING,
        actor="act_observe_check_node",
        details={"missing_evidence": ["call_id_binding"]},
    )
    fulfilled, fulfilled_event = await transition_contract_and_record(
        ledger=ledger,
        contract=pending,
        thread_id=THREAD_ID,
        target=ContractStatus.FULFILLED,
        event_type=LedgerEventType.CONTRACT_FULFILLED,
        actor="act_observe_check_node",
        details={"evidence_collected": ["call_id_binding"]},
    )
    events = await repository.list_events(TASK_ID, THREAD_ID)

    assert pending.status == ContractStatus.EVIDENCE_PENDING
    assert fulfilled.status == ContractStatus.FULFILLED
    assert pending_event.event_type == LedgerEventType.CONTRACT_EVIDENCE_PENDING
    assert pending_event.payload["details"]["missing_evidence"] == [
        "call_id_binding"
    ]
    assert fulfilled_event.event_type == LedgerEventType.CONTRACT_FULFILLED
    assert [event.event_type for event in events][-2:] == [
        LedgerEventType.CONTRACT_EVIDENCE_PENDING,
        LedgerEventType.CONTRACT_FULFILLED,
    ]
    assert await ledger.verify_chain(TASK_ID, THREAD_ID) is True


@pytest.mark.asyncio
async def test_disallowed_tool_records_violation_and_blocks_contract(
    ledger: LedgerService,
    repository: InMemoryLedgerRepository,
    certificate: IntentCertificate,
) -> None:
    draft, _ = await create_contract(ledger, certificate)
    proposed_tool = "multiply"

    assert draft.tool_is_allowed(proposed_tool) is False

    violation = await record_violation(
        ledger=ledger,
        contract=draft,
        thread_id=THREAD_ID,
        actor="think_act_check_node",
        violation_payload={
            "violation_type": "action_violation",
            "rule_id": "CONTRACT-TOOL-001",
            "expected_tools": draft.allowed_tools,
            "actual_tool": proposed_tool,
            "decision": "block",
        },
    )
    blocked, blocked_event = await transition_contract_and_record(
        ledger=ledger,
        contract=draft,
        thread_id=THREAD_ID,
        target=ContractStatus.BLOCKED,
        event_type=LedgerEventType.CONTRACT_BLOCKED,
        actor="think_act_check_node",
        details={"reason": "tool_not_allowed"},
    )
    events = await repository.list_events(TASK_ID, THREAD_ID)
    event_types = [event.event_type for event in events]

    assert blocked.status == ContractStatus.BLOCKED
    assert violation.event_type == LedgerEventType.VIOLATION_DETECTED
    assert violation.payload["actual_tool"] == "multiply"
    assert blocked_event.event_type == LedgerEventType.CONTRACT_BLOCKED
    assert event_types == [
        LedgerEventType.CONTRACT_CREATED,
        LedgerEventType.VIOLATION_DETECTED,
        LedgerEventType.CONTRACT_BLOCKED,
    ]
    assert LedgerEventType.ACTION_APPROVED not in event_types
    assert LedgerEventType.ACTION_EXECUTED not in event_types
    assert await ledger.verify_chain(TASK_ID, THREAD_ID) is True


@pytest.mark.asyncio
async def test_contract_ledger_events_are_tamper_evident(
    ledger: LedgerService,
    repository: InMemoryLedgerRepository,
    certificate: IntentCertificate,
) -> None:
    draft, _ = await create_contract(ledger, certificate)
    await transition_contract_and_record(
        ledger=ledger,
        contract=draft,
        thread_id=THREAD_ID,
        target=ContractStatus.ACTIVE,
        event_type=LedgerEventType.CONTRACT_ACTIVATED,
        actor="think_act_check_node",
    )
    events = await repository.list_events(TASK_ID, THREAD_ID)

    assert await ledger.verify_chain(TASK_ID, THREAD_ID) is True

    events[0].payload["allowed_tools"] = ["multiply"]

    assert events[0].verify_hash() is False
    assert await ledger.verify_chain(TASK_ID, THREAD_ID) is False