from __future__ import annotations

import pytest

from recap.agent.state import ReCAPState
from recap.contracts import ContractStatus, RuntimeContract
from recap.ledger import InMemoryLedgerRepository, LedgerEventType, LedgerService
from recap.nodes.act_observe import build_act_observe_check_node, route_after_act_observe
from recap.schemas import ActionEvent, ExecutionStatus, IntentCertificate, ObservationEvent


TASK_ID = "task-a2o-001"
THREAD_ID = "thread-a2o-001"


@pytest.fixture
def repository() -> InMemoryLedgerRepository:
    return InMemoryLedgerRepository()


@pytest.fixture
def ledger(repository: InMemoryLedgerRepository) -> LedgerService:
    return LedgerService(repository)


def executing_state(*, evidence: list[str], observation_call_id: str = "call-001") -> ReCAPState:
    certificate = IntentCertificate(
        round_num=1,
        subgoal="计算 3 和 4 的和",
        proposed_operation="add",
        argument_constraints={"a": {"eq": 3}, "b": {"eq": 4}},
        authority_basis="user_request:test-a2o",
        expected_effect="返回 7",
        required_evidence=["tool_return", "call_id_binding"],
    )
    contract = RuntimeContract(
        task_id=TASK_ID,
        round_num=1,
        certificate=certificate,
        allowed_tools=["add"],
        status=ContractStatus.EXECUTING,
    )
    action = ActionEvent(
        call_id="call-001",
        tool_name="add",
        actual_params={"a": 3, "b": 4},
        execution_status=ExecutionStatus.SUCCESS,
        certificate_id=certificate.certificate_id,
    )
    observation = ObservationEvent(
        call_id=observation_call_id,
        return_content=7,
        evidence_collected=evidence,
        is_complete=False,
    )
    return {
        "messages": [],
        "task_id": TASK_ID,
        "thread_id": THREAD_ID,
        "round_num": 1,
        "current_intent": certificate,
        "current_contract": contract,
        "current_action": action,
        "current_observation": observation,
        "ledger_events": [],
        "check_results": [],
    }


@pytest.mark.asyncio
async def test_complete_evidence_fulfills_contract(ledger, repository):
    state = executing_state(evidence=["tool_return", "call_id_binding"])
    update = await build_act_observe_check_node(ledger)(state)
    events = await repository.list_events(TASK_ID, THREAD_ID)

    assert update["current_contract"].status == ContractStatus.FULFILLED
    assert update["current_observation"].is_complete is True
    assert update["pending_obligations"] == []
    assert update["final_answer_allowed"] is True
    assert update["check_results"][0].passed is True
    assert update["next_route"] == "end"
    assert [event.event_type for event in events] == [
        LedgerEventType.CONTRACT_EVIDENCE_PENDING,
        LedgerEventType.OBLIGATION_FULFILLED,
        LedgerEventType.CONTRACT_FULFILLED,
    ]
    assert await ledger.verify_chain(TASK_ID, THREAD_ID) is True


@pytest.mark.asyncio
async def test_missing_tool_return_blocks_contract(ledger, repository):
    state = executing_state(evidence=["call_id_binding"])
    update = await build_act_observe_check_node(ledger)(state)
    events = await repository.list_events(TASK_ID, THREAD_ID)

    assert update["current_contract"].status == ContractStatus.BLOCKED
    assert update["check_results"][0].violations[0].rule_id == "A2O-EVIDENCE-001"
    assert update["final_answer_allowed"] is False
    assert [event.event_type for event in events] == [
        LedgerEventType.VIOLATION_DETECTED,
        LedgerEventType.CONTRACT_BLOCKED,
    ]


@pytest.mark.asyncio
async def test_missing_call_id_binding_blocks_contract(ledger):
    state = executing_state(evidence=["tool_return"])
    update = await build_act_observe_check_node(ledger)(state)

    assert update["current_contract"].status == ContractStatus.BLOCKED
    violation = update["check_results"][0].violations[0]
    assert violation.rule_id == "A2O-EVIDENCE-001"
    assert "call_id_binding" in violation.actual_value["missing"]


@pytest.mark.asyncio
async def test_mismatched_call_id_blocks_contract(ledger):
    state = executing_state(
        evidence=["tool_return", "call_id_binding"],
        observation_call_id="call-tampered",
    )
    update = await build_act_observe_check_node(ledger)(state)

    assert update["current_contract"].status == ContractStatus.BLOCKED
    assert update["check_results"][0].violations[0].rule_id == "A2O-CALL-ID-001"


@pytest.mark.asyncio
async def test_verifier_does_not_mutate_input_contract(ledger):
    state = executing_state(evidence=["tool_return", "call_id_binding"])
    original = state["current_contract"]
    update = await build_act_observe_check_node(ledger)(state)

    assert original.status == ContractStatus.EXECUTING
    assert update["current_contract"] is not original
    assert update["current_contract"].status == ContractStatus.FULFILLED


def test_route_after_act_observe_fails_closed():
    assert route_after_act_observe({"messages": [], "next_route": "act"}) == "end"
    assert route_after_act_observe({"messages": []}) == "end"