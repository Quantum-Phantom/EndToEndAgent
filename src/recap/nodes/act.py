"""Trusted Act node with approval binding and fail-closed execution."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Any, Literal
import hashlib

import orjson
from langchain_core.messages import ToolMessage

from recap.agent.state import ReCAPState
from recap.contracts import ContractStatus, RuntimeContract
from recap.integration import record_violation, transition_contract_and_record
from recap.ledger import LedgerEvent, LedgerEventType, LedgerService
from recap.recovery import route_for_recovery
from recap.schemas import (
    ActionEvent,
    ExecutionStatus,
    RecoveryAction,
    TransitionResult,
    ViolationEvidence,
    ViolationType,
)
from recap.security import calculate_action_digest
from recap.tools import ToolRegistry, execute_trusted_tool
from recap.verification import constraint_mismatches

ActNode = Callable[[ReCAPState], Awaitable[dict[str, Any]]]
ActRoute = Literal["observe", "replan", "human_approval", "end"]


def build_act_node(
    ledger: LedgerService,
    registry: ToolRegistry,
    *,
    timeout_seconds: float = 30.0,
) -> ActNode:
    async def act_node(state: ReCAPState) -> dict[str, Any]:
        violation = _validate_preconditions(state, registry)
        if violation is not None:
            return await _block_before_execution(ledger, state, violation)

        contract = state["current_contract"]
        candidate = state["candidate_tool_call"]
        assert contract is not None and candidate is not None
        tool_name = candidate["name"]
        args = candidate["args"]
        thread_id = _thread_id(state)

        executing, contract_event = await transition_contract_and_record(
            ledger=ledger,
            contract=contract,
            thread_id=thread_id,
            target=ContractStatus.EXECUTING,
            event_type=LedgerEventType.CONTRACT_EXECUTING,
            actor="act_node",
            details={"tool_name": tool_name},
        )
        started_event = await ledger.record(
            event_type=LedgerEventType.ACTION_EXECUTION_STARTED,
            task_id=executing.task_id,
            thread_id=thread_id,
            round_num=executing.round_num,
            contract_id=executing.contract_id,
            actor="act_node",
            payload={
                "tool_name": tool_name,
                "args": args,
                "model_tool_call_id": candidate.get("id"),
                "action_digest": state["approved_action_digest"],
            },
        )
        result = await execute_trusted_tool(
            registry=registry,
            tool_name=tool_name,
            args=args,
            model_tool_call_id=candidate.get("id"),
            timeout_seconds=timeout_seconds,
        )
        status = ExecutionStatus.SUCCESS if result.success else ExecutionStatus.FAILED
        action = ActionEvent(
            call_id=result.call_id,
            tool_name=tool_name,
            actual_params=args,
            execution_status=status,
            certificate_id=contract.certificate.certificate_id,
        )
        action_event = await ledger.record(
            event_type=(
                LedgerEventType.ACTION_EXECUTED
                if result.success
                else LedgerEventType.ACTION_FAILED
            ),
            task_id=executing.task_id,
            thread_id=thread_id,
            round_num=executing.round_num,
            contract_id=executing.contract_id,
            actor="act_node",
            payload=_execution_payload(action, result, registry),
        )
        events = [contract_event, started_event, action_event]

        if not result.success:
            failed, failed_event = await transition_contract_and_record(
                ledger=ledger,
                contract=executing,
                thread_id=thread_id,
                target=ContractStatus.FAILED,
                event_type=LedgerEventType.CONTRACT_FAILED,
                actor="act_node",
                details={
                    "error_type": result.error_type,
                    "error_message": result.error_message,
                },
            )
            events.append(failed_event)
            return {
                "current_contract": failed,
                "current_action": action,
                "raw_tool_result": result,
                "ledger_events": events,
                "ledger_head_hash": events[-1].event_hash,
                "next_route": "end",
                "final_answer_allowed": False,
            }

        return {
            "current_contract": executing,
            "current_action": action,
            "raw_tool_result": result,
            "messages": [
                ToolMessage(
                    content=_tool_message_content(result.content),
                    tool_call_id=result.model_tool_call_id or result.call_id,
                )
            ],
            "ledger_events": events,
            "ledger_head_hash": action_event.event_hash,
            "next_route": "observe",
            "final_answer_allowed": False,
        }

    return act_node


def _execution_payload(
    action: ActionEvent,
    result: Any,
    registry: ToolRegistry,
) -> dict[str, Any]:
    action_payload = action.model_dump(mode="json")
    result_payload = result.model_dump(mode="json")
    try:
        redact_arguments = registry.get_capability(action.tool_name).risk_level == "high"
    except LookupError:
        redact_arguments = True
    if redact_arguments:
        action_payload["actual_params"] = {
            key: f"sha256:{hashlib.sha256(str(value).encode('utf-8')).hexdigest()}"
            for key, value in action.actual_params.items()
        }
        result_payload["args"] = {
            key: action_payload["actual_params"][key]
            for key in action.actual_params
        }
    return {"action": action_payload, "tool_result": result_payload}


def route_after_act(state: ReCAPState) -> ActRoute:
    requested = state.get("next_route", "end")
    if requested != "observe":
        return requested if requested in {"replan", "human_approval", "end"} else "end"
    contract = state.get("current_contract")
    action = state.get("current_action")
    if contract is None or action is None:
        return "end"
    if contract.status != ContractStatus.EXECUTING:
        return "end"
    if action.execution_status != ExecutionStatus.SUCCESS:
        return "end"
    return "observe"


def _validate_preconditions(
    state: ReCAPState,
    registry: ToolRegistry,
) -> ViolationEvidence | None:
    contract = state.get("current_contract")
    candidate = state.get("candidate_tool_call")
    results = state.get("check_results", [])
    if contract is None:
        return _violation("ACT-CONTRACT-001", "act_node 缺少合同", "RuntimeContract", None)
    if contract.status != ContractStatus.ACTIVE:
        return _violation(
            "ACT-CONTRACT-002",
            "只有 ACTIVE 合同可以执行工具",
            ContractStatus.ACTIVE.value,
            contract.status.value,
        )
    if state.get("next_route") != "act":
        return _violation("ACT-ROUTE-001", "act_node 未获得执行路由", "act", state.get("next_route"))
    if not results:
        return _violation("ACT-CHECK-001", "缺少 Think->Act 检查结果", "passed result", None)
    latest = results[-1]
    if latest.check_type != "think->act" or not latest.passed or not latest.next_allowed:
        return _violation("ACT-CHECK-002", "Think->Act 检查未通过", True, latest.model_dump(mode="json"))
    if not isinstance(candidate, dict):
        return _violation("ACT-CANDIDATE-001", "缺少候选工具调用", "tool call object", candidate)
    tool_name = candidate.get("name")
    args = candidate.get("args")
    if not isinstance(tool_name, str) or not isinstance(args, dict):
        return _violation("ACT-CANDIDATE-002", "候选工具调用格式无效", "name:str,args:dict", candidate)
    if not contract.tool_is_allowed(tool_name):
        return _violation("ACT-TOOL-001", "候选工具不在合同范围", contract.allowed_tools, tool_name)
    mismatches = constraint_mismatches(contract.argument_constraints, args)
    if mismatches:
        return _violation("ACT-ARGS-001", "候选参数不满足合同约束", contract.argument_constraints, mismatches)
    approved_digest = state.get("approved_action_digest")
    if not approved_digest:
        return _violation("ACT-DIGEST-001", "缺少动作批准摘要", "digest", approved_digest)
    current_digest = calculate_action_digest(contract, candidate)
    if current_digest != approved_digest:
        return _violation("ACT-DIGEST-002", "候选动作在批准后发生变化", approved_digest, current_digest)
    if not registry.contains(tool_name):
        return _violation("ACT-REGISTRY-001", "工具未在可信注册表中注册", "registered tool", tool_name)
    return None


async def _block_before_execution(
    ledger: LedgerService,
    state: ReCAPState,
    violation: ViolationEvidence,
) -> dict[str, Any]:
    contract = state.get("current_contract")
    task_id = contract.task_id if contract else state.get("task_id", "unknown-task")
    round_num = contract.round_num if contract else state.get("round_num", 0)
    contract_id = contract.contract_id if contract else None
    thread_id = _thread_id(state)
    if contract is not None:
        violation_event = await record_violation(
            ledger=ledger,
            contract=contract,
            thread_id=thread_id,
            actor="act_node",
            violation_payload=violation.model_dump(mode="json"),
        )
    else:
        violation_event = await ledger.record(
            event_type=LedgerEventType.VIOLATION_DETECTED,
            task_id=task_id,
            thread_id=thread_id,
            round_num=round_num,
            contract_id=None,
            actor="act_node",
            payload=violation.model_dump(mode="json"),
        )
    blocked_action = await ledger.record(
        event_type=LedgerEventType.ACTION_BLOCKED,
        task_id=task_id,
        thread_id=thread_id,
        round_num=round_num,
        contract_id=contract_id,
        actor="act_node",
        payload={
            "rule_id": violation.rule_id,
            "candidate_tool_call": state.get("candidate_tool_call"),
        },
    )
    events: list[LedgerEvent] = [violation_event, blocked_action]
    updated = contract
    if contract is not None:
        try:
            updated, blocked_contract = await transition_contract_and_record(
                ledger=ledger,
                contract=contract,
                thread_id=thread_id,
                target=ContractStatus.BLOCKED,
                event_type=LedgerEventType.CONTRACT_BLOCKED,
                actor="act_node",
                details={"rule_id": violation.rule_id},
            )
            events.append(blocked_contract)
        except ValueError:
            pass
    return {
        "current_contract": updated,
        "current_action": None,
        "check_results": [TransitionResult.blocked("think->act", [violation])],
        "ledger_events": events,
        "ledger_head_hash": events[-1].event_hash,
        "next_route": route_for_recovery(violation.decision),
        "final_answer_allowed": False,
    }


def _violation(rule_id: str, description: str, expected: Any, actual: Any) -> ViolationEvidence:
    return ViolationEvidence(
        violation_type=ViolationType.ACTION_VIOLATION,
        rule_id=rule_id,
        rule_description=description,
        expected_value=expected,
        actual_value=actual,
        decision=RecoveryAction.BLOCK,
        evidence_chain=[f"rule:{rule_id}", f"expected:{expected!r}", f"actual:{actual!r}"],
    )


def _thread_id(state: ReCAPState) -> str:
    value = state.get("thread_id")
    return value if isinstance(value, str) and value else "default-thread"


def _tool_message_content(content: Any) -> str:
    if isinstance(content, str):
        return content
    return orjson.dumps(content, default=str).decode("utf-8")
