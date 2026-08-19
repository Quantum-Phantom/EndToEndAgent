# -*- coding: utf-8 -*-

import pytest
from langgraph.graph import END, START, StateGraph

from recap.agent.state import ReCAPState
from recap.contracts import ContractStatus
from recap.integration import create_contract_and_record
from recap.ledger import (
    InMemoryLedgerRepository,
    LedgerEventType,
    LedgerService,
)
from recap.nodes.think_act import (
    build_think_act_check_node,
    route_after_think_act,
)
from recap.schemas import IntentCertificate


TASK_ID = "task-think-act-001"
THREAD_ID = "thread-think-act-001"


@pytest.fixture
def certificate() -> IntentCertificate:
    return IntentCertificate(
        round_num=1,
        subgoal="计算 3 和 4 的和",
        proposed_operation="add",
        argument_constraints={"a": {"eq": 3}, "b": {"eq": 4}},
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


async def contract_state(
    ledger: LedgerService,
    certificate: IntentCertificate,
    candidate: dict,
) -> ReCAPState:
    contract, _ = await create_contract_and_record(
        ledger=ledger,
        task_id=TASK_ID,
        thread_id=THREAD_ID,
        certificate=certificate,
        allowed_tools=["add"],
        permissions=["arithmetic:execute"],
        policy_refs=["POLICY-ARITHMETIC-001"],
    )
    return {
        "messages": [],
        "task_id": TASK_ID,
        "thread_id": THREAD_ID,
        "round_num": 1,
        "current_intent": certificate,
        "current_contract": contract,
        "candidate_tool_call": candidate,
        "check_results": [],
        "ledger_events": [],
    }


@pytest.mark.asyncio
async def test_allowed_tool_activates_contract_and_routes_to_act(
    ledger: LedgerService,
    repository: InMemoryLedgerRepository,
    certificate: IntentCertificate,
) -> None:
    state = await contract_state(
        ledger,
        certificate,
        {"id": "tool-call-001", "name": "add", "args": {"a": 3, "b": 4}},
    )
    node = build_think_act_check_node(ledger)
    update = await node(state)
    routed_state = {**state, **update}
    events = await repository.list_events(TASK_ID, THREAD_ID)

    assert update["current_contract"].status == ContractStatus.ACTIVE
    assert update["next_route"] == "act"
    assert update["check_results"][0].passed is True
    assert update["check_results"][0].next_allowed is True
    assert route_after_think_act(routed_state) == "act"
    assert [event.event_type for event in events] == [
        LedgerEventType.CONTRACT_CREATED,
        LedgerEventType.CONTRACT_ACTIVATED,
        LedgerEventType.ACTION_APPROVED,
    ]
    assert await ledger.verify_chain(TASK_ID, THREAD_ID) is True


@pytest.mark.asyncio
async def test_disallowed_tool_is_blocked_and_never_approved(
    ledger: LedgerService,
    repository: InMemoryLedgerRepository,
    certificate: IntentCertificate,
) -> None:
    state = await contract_state(
        ledger,
        certificate,
        {
            "id": "tool-call-attack",
            "name": "multiply",
            "args": {"a": 3, "b": 4},
        },
    )
    node = build_think_act_check_node(ledger)
    update = await node(state)
    routed_state = {**state, **update}
    events = await repository.list_events(TASK_ID, THREAD_ID)
    event_types = [event.event_type for event in events]

    assert update["current_contract"].status == ContractStatus.BLOCKED
    assert update["next_route"] == "end"
    assert update["check_results"][0].passed is False
    assert update["check_results"][0].next_allowed is False
    assert route_after_think_act(routed_state) == "end"
    assert event_types == [
        LedgerEventType.CONTRACT_CREATED,
        LedgerEventType.VIOLATION_DETECTED,
        LedgerEventType.CONTRACT_BLOCKED,
    ]
    assert LedgerEventType.ACTION_APPROVED not in event_types
    assert LedgerEventType.ACTION_EXECUTED not in event_types
    assert await ledger.verify_chain(TASK_ID, THREAD_ID) is True


@pytest.mark.asyncio
async def test_argument_mismatch_is_blocked(
    ledger: LedgerService,
    repository: InMemoryLedgerRepository,
    certificate: IntentCertificate,
) -> None:
    state = await contract_state(
        ledger,
        certificate,
        {"id": "tool-call-002", "name": "add", "args": {"a": 3, "b": 40}},
    )
    update = await build_think_act_check_node(ledger)(state)
    events = await repository.list_events(TASK_ID, THREAD_ID)

    assert update["current_contract"].status == ContractStatus.BLOCKED
    assert update["next_route"] == "end"
    violation = update["check_results"][0].violations[0]
    assert violation.rule_id == "CONTRACT-ARGS-001"
    assert violation.actual_value["args"] == {"a": 3, "b": 40}
    assert LedgerEventType.ACTION_APPROVED not in {
        event.event_type for event in events
    }
    assert await ledger.verify_chain(TASK_ID, THREAD_ID) is True


def test_route_fails_closed_without_a_verified_result(
    certificate: IntentCertificate,
) -> None:
    state: ReCAPState = {
        "messages": [],
        "next_route": "act",
        "check_results": [],
        "current_contract": None,
        "current_intent": certificate,
    }

    assert route_after_think_act(state) == "end"


@pytest.mark.asyncio
async def test_langgraph_does_not_enter_act_node_for_disallowed_tool(
    ledger: LedgerService,
    repository: InMemoryLedgerRepository,
    certificate: IntentCertificate,
) -> None:
    state = await contract_state(
        ledger,
        certificate,
        {
            "id": "tool-call-attack",
            "name": "multiply",
            "args": {"a": 3, "b": 4},
        },
    )
    calls = {"act_node": 0}

    async def spy_act_node(_: ReCAPState) -> dict:
        calls["act_node"] += 1
        return {}

    graph = StateGraph(ReCAPState)
    graph.add_node("think_act_check_node", build_think_act_check_node(ledger))
    graph.add_node("act_node", spy_act_node)
    graph.add_edge(START, "think_act_check_node")
    graph.add_conditional_edges(
        "think_act_check_node",
        route_after_think_act,
        {
            "act": "act_node",
            "replan": END,
            "human_approval": END,
            "end": END,
        },
    )
    graph.add_edge("act_node", END)
    compiled = graph.compile()

    final_state = await compiled.ainvoke(state)
    events = await repository.list_events(TASK_ID, THREAD_ID)
    event_types = [event.event_type for event in events]

    assert calls["act_node"] == 0
    assert final_state["next_route"] == "end"
    assert final_state["current_contract"].status == ContractStatus.BLOCKED
    assert LedgerEventType.VIOLATION_DETECTED in event_types
    assert LedgerEventType.ACTION_APPROVED not in event_types
    assert LedgerEventType.ACTION_EXECUTED not in event_types
    assert await ledger.verify_chain(TASK_ID, THREAD_ID) is True
