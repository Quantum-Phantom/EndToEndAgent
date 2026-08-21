from __future__ import annotations

from dataclasses import dataclass

import pytest
from langchain_core.messages import HumanMessage

from recap.ledger import InMemoryLedgerRepository, LedgerService
from recap.nodes.think import build_think_node
from recap.schemas import TaskEntry, ThinkProposal
from recap.tools import ARITHMETIC_CAPABILITIES
from recap.tools.arithmetic import ARITHMETIC_TOOLS


@dataclass
class FakeStructuredRunnable:
    result: object

    async def ainvoke(self, _messages):
        return self.result


@dataclass
class FakeStructuredLLM:
    result: object

    def with_structured_output(self, _schema):
        return FakeStructuredRunnable(self.result)


def state():
    return {
        "messages": [HumanMessage(content="计算 3 和 4 的和")],
        "task_id": "task-001",
        "thread_id": "thread-001",
        "round_num": 0,
        "ledger_events": [],
        "check_results": [],
    }


@pytest.mark.asyncio
async def test_llm_plan_creates_intent_contract_and_candidate():
    ledger = LedgerService(InMemoryLedgerRepository())
    plan = ThinkProposal(
        subgoal="计算两个整数之和",
        tool_name="add",
        tool_args={"a": 3, "b": 4},
        expected_effect="返回 7，不产生外部副作用",
        required_evidence=["tool_return"],
        plan_summary="调用 add 计算结果",
    )
    node = build_think_node(FakeStructuredLLM(plan), ledger, ARITHMETIC_TOOLS)
    update = await node(state())

    assert update["current_intent"].proposed_operation == "add"
    assert update["current_intent"].authority_basis == "user_request:task-001"
    assert update["current_intent"].argument_constraints == {
        "a": {"eq": 3}, "b": {"eq": 4}
    }
    assert update["current_contract"].allowed_tools == ["add"]
    assert update["candidate_tool_call"]["args"] == {"a": 3, "b": 4}
    assert update["next_route"] == "check"


@pytest.mark.asyncio
async def test_llm_plan_is_compiled_from_registered_tool_capability():
    ledger = LedgerService(InMemoryLedgerRepository())
    plan = ThinkProposal(
        subgoal="add two bounded integers",
        tool_name="add",
        tool_args={"a": 3, "b": 4},
        expected_effect="return the arithmetic result",
        required_evidence=["tool_return"],
        plan_summary="call add",
    )
    current_state = state()
    current_state["task_entry"] = TaskEntry(
        task_id="task-001",
        description="perform approved arithmetic",
        policies=["arithmetic-policy"],
        tools_available=["add", "multiply", "divide"],
        initial_permissions=["arithmetic:execute"],
    )

    update = await build_think_node(
        FakeStructuredLLM(plan),
        ledger,
        ARITHMETIC_TOOLS,
        capabilities=ARITHMETIC_CAPABILITIES,
    )(current_state)

    assert update["compiled_policy"] is not None
    assert update["task_contract"].task_id == "task-001"
    assert update["current_contract"].argument_constraints == {
        "a": {"eq": 3},
        "b": {"eq": 4},
    }
    assert update["current_contract"].granted_permissions == [
        "arithmetic:execute"
    ]
    assert update["ledger_events"][0].payload["compiled_policy"]


@pytest.mark.asyncio
async def test_final_answer_creates_no_contract():
    ledger = LedgerService(InMemoryLedgerRepository())
    plan = ThinkProposal(
        subgoal="回答无需工具的问题",
        expected_effect="直接回答",
        final_answer="你好！",
    )
    node = build_think_node(FakeStructuredLLM(plan), ledger, ARITHMETIC_TOOLS)
    update = await node(state())

    assert update["current_contract"] is None
    assert update["candidate_tool_call"] is None
    assert update["final_answer_allowed"] is True
    assert update["next_route"] == "end"


@pytest.mark.asyncio
async def test_unknown_tool_is_blocked_and_recorded():
    ledger = LedgerService(InMemoryLedgerRepository())
    plan = ThinkProposal(
        subgoal="发送邮件",
        tool_name="send_email",
        tool_args={"to": "x@example.com"},
        expected_effect="发送邮件",
    )
    node = build_think_node(FakeStructuredLLM(plan), ledger, ARITHMETIC_TOOLS)
    update = await node(state())

    assert update["candidate_tool_call"] is None
    assert update["current_contract"] is None
    assert update["check_results"][0].passed is False
    assert update["next_route"] == "end"


@pytest.mark.asyncio
async def test_final_answer_is_rejected_while_evidence_is_pending():
    ledger = LedgerService(InMemoryLedgerRepository())
    proposal = ThinkProposal(
        subgoal="Answer the task",
        expected_effect="Return a final answer",
        final_answer="Done",
    )
    pending_state = state()
    pending_state["pending_obligations"] = ["delivery_receipt"]

    update = await build_think_node(
        FakeStructuredLLM(proposal), ledger, ARITHMETIC_TOOLS
    )(pending_state)

    assert update["final_answer_allowed"] is False
    assert update["task_completed"] is False
    assert update["check_results"][0].violations[0].rule_id == (
        "THINK-UNPROVEN-SUCCESS-001"
    )
