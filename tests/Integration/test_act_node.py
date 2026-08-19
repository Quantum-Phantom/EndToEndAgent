# -*- coding: utf-8 -*-

import asyncio
from typing import Any

import pytest
from langgraph.graph import END, START, StateGraph

from recap.agent.state import ReCAPState
from recap.contracts import ContractStatus
from recap.integration import create_contract_and_record
from recap.ledger import InMemoryLedgerRepository, LedgerEventType, LedgerService
from recap.nodes import (
    build_act_node,
    build_think_act_check_node,
    route_after_act,
)
from recap.schemas import ExecutionStatus, IntentCertificate
from recap.tools import ToolRegistry


TASK_ID = "task-act-001"
THREAD_ID = "thread-act-001"


class FakeTool:
    def __init__(
        self,
        name: str,
        *,
        result: Any = None,
        error: Exception | None = None,
        delay: float = 0,
    ) -> None:
        self.name = name
        self.result = result
        self.error = error
        self.delay = delay
        self.calls: list[dict[str, Any]] = []

    async def ainvoke(self, args: dict[str, Any]) -> Any:
        self.calls.append(dict(args))
        if self.delay:
            await asyncio.sleep(self.delay)
        if self.error is not None:
            raise self.error
        return self.result


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


async def approved_state(
    ledger: LedgerService,
    certificate: IntentCertificate,
    *,
    candidate: dict[str, Any] | None = None,
    allowed_tools: list[str] | None = None,
) -> ReCAPState:
    candidate = candidate or {
        "id": "model-call-001",
        "name": certificate.proposed_operation,
        "args": {"a": 3, "b": 4},
    }
    contract, _ = await create_contract_and_record(
        ledger=ledger,
        task_id=TASK_ID,
        thread_id=THREAD_ID,
        certificate=certificate,
        allowed_tools=allowed_tools or [certificate.proposed_operation],
        permissions=["arithmetic:execute"],
        policy_refs=["POLICY-ARITHMETIC-001"],
    )
    state: ReCAPState = {
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
    check_update = await build_think_act_check_node(ledger)(state)
    return {**state, **check_update}


@pytest.mark.asyncio
async def test_valid_approved_action_executes_and_routes_to_observe(
    ledger: LedgerService,
    repository: InMemoryLedgerRepository,
    certificate: IntentCertificate,
) -> None:
    tool = FakeTool("add", result=7)
    registry = ToolRegistry()
    registry.register(tool)
    state = await approved_state(ledger, certificate)

    update = await build_act_node(ledger, registry)(state)
    routed_state = {**state, **update}
    events = await repository.list_events(TASK_ID, THREAD_ID)

    assert tool.calls == [{"a": 3, "b": 4}]
    assert update["current_contract"].status == ContractStatus.EXECUTING
    assert update["current_action"].execution_status == ExecutionStatus.SUCCESS
    assert update["raw_tool_result"].success is True
    assert update["raw_tool_result"].content == 7
    assert update["next_route"] == "observe"
    assert route_after_act(routed_state) == "observe"
    assert [event.event_type for event in events] == [
        LedgerEventType.CONTRACT_CREATED,
        LedgerEventType.CONTRACT_ACTIVATED,
        LedgerEventType.ACTION_APPROVED,
        LedgerEventType.CONTRACT_EXECUTING,
        LedgerEventType.ACTION_EXECUTION_STARTED,
        LedgerEventType.ACTION_EXECUTED,
    ]
    assert await ledger.verify_chain(TASK_ID, THREAD_ID) is True


@pytest.mark.asyncio
async def test_modified_approved_action_is_blocked_before_tool_execution(
    ledger: LedgerService,
    repository: InMemoryLedgerRepository,
    certificate: IntentCertificate,
) -> None:
    tool = FakeTool("add", result=7)
    registry = ToolRegistry()
    registry.register(tool)
    state = await approved_state(ledger, certificate)
    state["candidate_tool_call"] = {
        **state["candidate_tool_call"],
        "id": "model-call-tampered",
    }

    update = await build_act_node(ledger, registry)(state)
    events = await repository.list_events(TASK_ID, THREAD_ID)
    event_types = [event.event_type for event in events]

    assert tool.calls == []
    assert update["current_contract"].status == ContractStatus.BLOCKED
    assert update["current_action"] is None
    assert update["next_route"] == "end"
    assert update["check_results"][0].violations[0].rule_id == "ACT-DIGEST-002"
    assert event_types[-3:] == [
        LedgerEventType.VIOLATION_DETECTED,
        LedgerEventType.ACTION_BLOCKED,
        LedgerEventType.CONTRACT_BLOCKED,
    ]
    assert LedgerEventType.ACTION_EXECUTION_STARTED not in event_types
    assert LedgerEventType.ACTION_EXECUTED not in event_types
    assert await ledger.verify_chain(TASK_ID, THREAD_ID) is True


@pytest.mark.asyncio
async def test_approved_but_unregistered_tool_is_blocked(
    ledger: LedgerService,
    repository: InMemoryLedgerRepository,
) -> None:
    ghost_certificate = IntentCertificate(
        round_num=1,
        subgoal="调用受控工具",
        proposed_operation="ghost_tool",
        argument_constraints={},
        authority_basis="user_request:test-ghost",
        expected_effect="返回工具结果",
        required_evidence=["tool_return"],
    )
    state = await approved_state(
        ledger,
        ghost_certificate,
        candidate={"id": "ghost-001", "name": "ghost_tool", "args": {}},
    )
    registry = ToolRegistry()

    update = await build_act_node(ledger, registry)(state)
    events = await repository.list_events(TASK_ID, THREAD_ID)
    event_types = [event.event_type for event in events]

    assert update["current_contract"].status == ContractStatus.BLOCKED
    assert update["check_results"][0].violations[0].rule_id == "ACT-REGISTRY-001"
    assert LedgerEventType.ACTION_EXECUTION_STARTED not in event_types
    assert LedgerEventType.ACTION_EXECUTED not in event_types
    assert await ledger.verify_chain(TASK_ID, THREAD_ID) is True


@pytest.mark.asyncio
async def test_tool_exception_records_failed_action_and_contract(
    ledger: LedgerService,
    repository: InMemoryLedgerRepository,
    certificate: IntentCertificate,
) -> None:
    tool = FakeTool("add", error=RuntimeError("calculator unavailable"))
    registry = ToolRegistry()
    registry.register(tool)
    state = await approved_state(ledger, certificate)

    update = await build_act_node(ledger, registry)(state)
    events = await repository.list_events(TASK_ID, THREAD_ID)

    assert tool.calls == [{"a": 3, "b": 4}]
    assert update["current_contract"].status == ContractStatus.FAILED
    assert update["current_action"].execution_status == ExecutionStatus.FAILED
    assert update["raw_tool_result"].success is False
    assert update["raw_tool_result"].error_type == "RuntimeError"
    assert update["next_route"] == "end"
    assert [event.event_type for event in events][-4:] == [
        LedgerEventType.CONTRACT_EXECUTING,
        LedgerEventType.ACTION_EXECUTION_STARTED,
        LedgerEventType.ACTION_FAILED,
        LedgerEventType.CONTRACT_FAILED,
    ]
    assert await ledger.verify_chain(TASK_ID, THREAD_ID) is True


def test_route_after_act_fails_closed() -> None:
    assert route_after_act({"messages": [], "next_route": "observe"}) == "end"
    assert route_after_act({"messages": [], "next_route": "act"}) == "end"


@pytest.mark.asyncio
async def test_graph_routes_successful_act_to_observe_node(
    ledger: LedgerService,
    certificate: IntentCertificate,
) -> None:
    tool = FakeTool("add", result=7)
    registry = ToolRegistry()
    registry.register(tool)
    state = await approved_state(ledger, certificate)
    calls = {"observe": 0}

    async def spy_observe_node(_: ReCAPState) -> dict[str, Any]:
        calls["observe"] += 1
        return {"next_route": "end"}

    graph = StateGraph(ReCAPState)
    graph.add_node("act_node", build_act_node(ledger, registry))
    graph.add_node("observe_node", spy_observe_node)
    graph.add_edge(START, "act_node")
    graph.add_conditional_edges(
        "act_node",
        route_after_act,
        {
            "observe": "observe_node",
            "replan": END,
            "human_approval": END,
            "end": END,
        },
    )
    graph.add_edge("observe_node", END)
    compiled = graph.compile()

    final_state = await compiled.ainvoke(state)

    assert tool.calls == [{"a": 3, "b": 4}]
    assert calls["observe"] == 1
    assert final_state["current_action"].execution_status == ExecutionStatus.SUCCESS
    assert final_state["current_contract"].status == ContractStatus.EXECUTING