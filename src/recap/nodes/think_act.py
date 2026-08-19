"""Fail-closed Think -> Act contract verification node."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Any, Literal

from recap.agent.state import ReCAPState
from recap.contracts import ContractStatus, RuntimeContract
from recap.integration import record_violation, transition_contract_and_record
from recap.ledger import LedgerEvent, LedgerEventType, LedgerService
from recap.schemas import RecoveryAction, TransitionResult, ViolationEvidence, ViolationType
from recap.security import calculate_action_digest
from recap.verification import constraint_mismatches

ThinkActNode = Callable[[ReCAPState], Awaitable[dict[str, Any]]]
ThinkActRoute = Literal["act", "replan", "human_approval", "end"]


def build_think_act_check_node(ledger: LedgerService) -> ThinkActNode:
    async def think_act_check_node(state: ReCAPState) -> dict[str, Any]:
        contract = state.get("current_contract")
        candidate = state.get("candidate_tool_call")
        if contract is None:
            violation = _violation(
                "CONTRACT-MISSING-001",
                "工具执行前必须存在 RuntimeContract",
                "current_contract",
                "RuntimeContract",
                None,
            )
            return {
                "check_results": [TransitionResult.blocked("think->act", [violation])],
                "next_route": "end",
                "final_answer_allowed": False,
            }
        if not isinstance(candidate, dict):
            return await _block(
                ledger,
                contract,
                _thread_id(state),
                _violation(
                    "ACTION-MISSING-001",
                    "合同激活前必须存在候选工具调用",
                    "candidate_tool_call",
                    "tool call object",
                    candidate,
                ),
            )
        tool_name = candidate.get("name")
        actual_args = candidate.get("args", {})
        if not isinstance(actual_args, dict):
            return await _block(
                ledger,
                contract,
                _thread_id(state),
                _violation(
                    "ACTION-ARGS-001",
                    "工具调用参数必须是对象",
                    "argument_constraints",
                    contract.argument_constraints,
                    actual_args,
                ),
            )
        if not isinstance(tool_name, str) or not contract.tool_is_allowed(tool_name):
            return await _block(
                ledger,
                contract,
                _thread_id(state),
                _violation(
                    "CONTRACT-TOOL-001",
                    "候选工具不在合同允许范围内",
                    "allowed_tools",
                    contract.allowed_tools,
                    tool_name,
                ),
            )
        mismatches = constraint_mismatches(contract.argument_constraints, actual_args)
        if mismatches:
            return await _block(
                ledger,
                contract,
                _thread_id(state),
                _violation(
                    "CONTRACT-ARGS-001",
                    "工具参数不满足合同约束",
                    "argument_constraints",
                    contract.argument_constraints,
                    {"args": actual_args, "mismatches": mismatches},
                ),
            )
        active, activated = await transition_contract_and_record(
            ledger=ledger,
            contract=contract,
            thread_id=_thread_id(state),
            target=ContractStatus.ACTIVE,
            event_type=LedgerEventType.CONTRACT_ACTIVATED,
            actor="think_act_check_node",
            details={"tool": tool_name, "check": "passed"},
        )
        digest = calculate_action_digest(active, candidate)
        approved = await ledger.record(
            event_type=LedgerEventType.ACTION_APPROVED,
            task_id=active.task_id,
            thread_id=_thread_id(state),
            round_num=active.round_num,
            contract_id=active.contract_id,
            actor="think_act_check_node",
            payload={
                "tool_name": tool_name,
                "args": actual_args,
                "tool_call_id": candidate.get("id"),
                "action_digest": digest,
            },
        )
        return {
            "current_contract": active,
            "approved_action_digest": digest,
            "check_results": [TransitionResult.pass_through("think->act")],
            "ledger_events": [activated, approved],
            "ledger_head_hash": approved.event_hash,
            "next_route": "act",
            "final_answer_allowed": False,
        }

    return think_act_check_node


def route_after_think_act(state: ReCAPState) -> ThinkActRoute:
    requested = state.get("next_route", "end")
    if requested != "act":
        return requested if requested in {"replan", "human_approval", "end"} else "end"
    contract = state.get("current_contract")
    results = state.get("check_results", [])
    if not results or contract is None or not state.get("approved_action_digest"):
        return "end"
    latest = results[-1]
    if (
        latest.check_type == "think->act"
        and latest.passed
        and latest.next_allowed
        and contract.status == ContractStatus.ACTIVE
    ):
        return "act"
    return "end"


async def _block(
    ledger: LedgerService,
    contract: RuntimeContract,
    thread_id: str,
    violation: ViolationEvidence,
) -> dict[str, Any]:
    violation_event = await record_violation(
        ledger=ledger,
        contract=contract,
        thread_id=thread_id,
        actor="think_act_check_node",
        violation_payload=violation.model_dump(mode="json"),
    )
    events: list[LedgerEvent] = [violation_event]
    updated = contract
    try:
        updated, blocked = await transition_contract_and_record(
            ledger=ledger,
            contract=contract,
            thread_id=thread_id,
            target=ContractStatus.BLOCKED,
            event_type=LedgerEventType.CONTRACT_BLOCKED,
            actor="think_act_check_node",
            details={"rule_id": violation.rule_id},
        )
        events.append(blocked)
    except ValueError:
        pass
    return {
        "current_contract": updated,
        "approved_action_digest": None,
        "check_results": [TransitionResult.blocked("think->act", [violation])],
        "ledger_events": events,
        "ledger_head_hash": events[-1].event_hash,
        "next_route": "end",
        "final_answer_allowed": False,
    }


def _violation(
    rule_id: str,
    description: str,
    field: str,
    expected: Any,
    actual: Any,
) -> ViolationEvidence:
    return ViolationEvidence(
        violation_type=ViolationType.ACTION_VIOLATION,
        rule_id=rule_id,
        rule_description=description,
        intent_field=field,
        expected_value=expected,
        actual_value=actual,
        decision=RecoveryAction.BLOCK,
        evidence_chain=[
            f"rule:{rule_id}",
            f"expected:{expected!r}",
            f"actual:{actual!r}",
        ],
    )


def _thread_id(state: ReCAPState) -> str:
    value = state.get("thread_id")
    return value if isinstance(value, str) and value else "default-thread"
