"""Fail-closed Think -> Act contract verification node."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Any, Literal

from recap.agent.state import ReCAPState
from recap.contracts import ContractPipeline, ContractStatus, RuntimeContract, TaskContract
from recap.integration import record_violation, transition_contract_and_record
from recap.ledger import LedgerEvent, LedgerEventType, LedgerService
from recap.recovery import route_for_recovery
from recap.schemas import RecoveryAction, TransitionResult, ViolationEvidence, ViolationType
from recap.security import calculate_action_digest
from recap.tools import ToolRegistry
from recap.verification import (
    build_pre_act_replay_input,
    public_recovery_constraints,
    sanitize_witness_value,
)

ThinkActNode = Callable[[ReCAPState], Awaitable[dict[str, Any]]]
ThinkActRoute = Literal["act", "replan", "human_approval", "end"]


def build_think_act_check_node(
    ledger: LedgerService,
    pipeline: ContractPipeline | None = None,
    registry: ToolRegistry | None = None,
) -> ThinkActNode:
    pipeline = pipeline or ContractPipeline()

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
                state,
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
                state,
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
                state,
            )
        if registry is not None:
            try:
                capability = registry.get_capability(tool_name)
            except LookupError:
                return await _block(
                    ledger,
                    contract,
                    _thread_id(state),
                    _violation(
                        "CONTRACT-CAPABILITY-001",
                        "Candidate tool has no trusted capability metadata",
                        "tool_capability",
                        "registered capability",
                        tool_name,
                    ),
                    state,
                )
            missing_permissions = set(capability.required_permissions) - set(
                contract.granted_permissions
            )
            if missing_permissions:
                return await _block(
                    ledger,
                    contract,
                    _thread_id(state),
                    _violation(
                        "CONTRACT-AUTHORITY-001",
                        "Candidate action lacks required tool permission",
                        "granted_permissions",
                        sorted(capability.required_permissions),
                        sorted(contract.granted_permissions),
                        decision=RecoveryAction.HUMAN_ESCALATION,
                    ),
                    state,
                )
        try:
            constraint_result = pipeline.verify_runtime_action(contract, actual_args)
        except Exception as exc:
            return await _block(
                ledger,
                contract,
                _thread_id(state),
                _violation(
                    "CONTRACT-CHECKER-001",
                    "Runtime constraint evaluation failed closed",
                    "argument_constraints",
                    "decidable constraints",
                    type(exc).__name__,
                ),
                state,
            )
        if not constraint_result.passed:
            return await _block(
                ledger,
                contract,
                _thread_id(state),
                _violation(
                    "CONTRACT-ARGS-001",
                    "工具参数不满足合同约束",
                    "argument_constraints",
                    contract.argument_constraints,
                    {
                        "args": actual_args,
                        "violations": [
                            item.model_dump(mode="json")
                            for item in constraint_result.violations
                        ],
                        "witness_rule_ids": constraint_result.witness_rule_ids,
                    },
                ),
                state,
            )
        task_contract = _task_contract(state, contract)
        pending = pipeline.obligation_manager.pending(contract.task_id)
        cross_round = pipeline.cross_round_verifier.verify(
            task_contract,
            task_contract.current,
            contract,
            pending,
            task_contract.pending_obligation_ids,
        )
        if not cross_round.passed:
            return await _block(
                ledger,
                contract,
                _thread_id(state),
                _violation(
                    "CONTRACT-CROSS-ROUND-001",
                    "Cross-round contract invariants failed",
                    "task_contract",
                    "monotonic authority, baseline and pending obligations",
                    [item.model_dump(mode="json") for item in cross_round.violations],
                ),
                state,
            )
        task_contract = task_contract.append_version(contract)
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
                "argument_fields": sorted(actual_args),
                "tool_call_id": candidate.get("id"),
                "action_digest": digest,
            },
        )
        return {
            "current_contract": active,
            "task_contract": task_contract.replace_current_version(active),
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
    state: ReCAPState | None = None,
) -> dict[str, Any]:
    candidate = (state or {}).get("candidate_tool_call")
    required_permissions = (
        list(violation.expected_value)
        if violation.rule_id == "CONTRACT-AUTHORITY-001"
        and isinstance(violation.expected_value, list)
        else []
    )
    violation = violation.model_copy(
        update={
            "task_id": contract.task_id,
            "thread_id": thread_id,
            "round_num": contract.round_num,
            "contract_id": contract.contract_id,
            "action_digest": (
                calculate_action_digest(contract, candidate)
                if isinstance(candidate, dict)
                else None
            ),
            "authority_refs": list(contract.authority_refs),
            "expected_value": sanitize_witness_value(violation.expected_value),
            "actual_value": sanitize_witness_value(violation.actual_value),
            "evidence_chain": [
                f"contract:{contract.contract_id}",
                f"rule:{violation.rule_id}",
                "candidate:action_digest",
            ],
            "replay_input": build_pre_act_replay_input(
                allowed_tools=contract.allowed_tools,
                argument_constraints=contract.argument_constraints,
                granted_permissions=contract.granted_permissions,
                required_permissions=required_permissions,
                candidate=candidate if isinstance(candidate, dict) else None,
            )
            | (
                {"indeterminate_rule": violation.rule_id}
                if violation.rule_id == "CONTRACT-CHECKER-001"
                else {}
            ),
        }
    )
    violation_event = await record_violation(
        ledger=ledger,
        contract=contract,
        thread_id=thread_id,
        actor="think_act_check_node",
        violation_payload=violation.model_dump(mode="json"),
    )
    events: list[LedgerEvent] = [violation_event]
    blocked_action = await ledger.record(
        event_type=LedgerEventType.ACTION_BLOCKED,
        task_id=contract.task_id,
        thread_id=thread_id,
        round_num=contract.round_num,
        contract_id=contract.contract_id,
        actor="think_act_check_node",
        payload={
            "rule_id": violation.rule_id,
            "tool_name": candidate.get("name") if isinstance(candidate, dict) else None,
            "action_digest": (
                calculate_action_digest(contract, candidate)
                if isinstance(candidate, dict)
                else None
            ),
            "argument_fields": (
                sorted(candidate.get("args", {}))
                if isinstance(candidate, dict) and isinstance(candidate.get("args"), dict)
                else []
            ),
        },
    )
    events.append(blocked_action)
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
    task_contract = (state or {}).get("task_contract")
    if task_contract is not None:
        if task_contract.current is None:
            task_contract = task_contract.append_version(updated)
        elif task_contract.current.contract_id == updated.contract_id:
            task_contract = task_contract.replace_current_version(updated)
    return {
        "current_contract": updated,
        **({"task_contract": task_contract} if task_contract is not None else {}),
        "approved_action_digest": None,
        "check_results": [TransitionResult.blocked("think->act", [violation])],
        "ledger_events": events,
        "ledger_head_hash": events[-1].event_hash,
        "next_route": (
            "human_approval"
            if violation.decision == RecoveryAction.HUMAN_ESCALATION
            else "replan"
        ),
        "recovery_context": {
            "previous_action_blocked": True,
            "rule_id": violation.rule_id,
            "reason": violation.rule_description,
            "authorized_constraints": public_recovery_constraints(
                contract.argument_constraints
            ),
            "authority_refs": contract.authority_refs,
            "pending_obligations": list((state or {}).get("pending_obligations", [])),
            "requested_permissions": required_permissions,
        },
        "task_completed": False,
        "final_answer_allowed": False,
    }


def _violation(
    rule_id: str,
    description: str,
    field: str,
    expected: Any,
    actual: Any,
    decision: RecoveryAction = RecoveryAction.BLOCK,
) -> ViolationEvidence:
    return ViolationEvidence(
        violation_type=ViolationType.ACTION_VIOLATION,
        rule_id=rule_id,
        rule_description=description,
        intent_field=field,
        expected_value=expected,
        actual_value=actual,
        decision=decision,
        evidence_chain=[
            f"rule:{rule_id}",
            f"expected:{expected!r}",
            f"actual:{actual!r}",
        ],
    )


def _thread_id(state: ReCAPState) -> str:
    value = state.get("thread_id")
    return value if isinstance(value, str) and value else "default-thread"


def _task_contract(state: ReCAPState, contract: RuntimeContract) -> TaskContract:
    existing = state.get("task_contract")
    if existing is not None:
        return existing
    task_entry = state.get("task_entry")
    tools = list(getattr(task_entry, "tools_available", []) or contract.allowed_tools)
    permissions = list(
        getattr(task_entry, "initial_permissions", []) or contract.granted_permissions
    )
    return TaskContract(
        task_id=contract.task_id,
        objective=getattr(task_entry, "description", contract.certificate.subgoal),
        capability_names=tools,
        granted_permissions=permissions,
        authority_refs=list(contract.authority_refs),
        policy_refs=list(contract.policy_refs),
        normative_baseline=list(contract.normative_baseline),
        pending_obligation_ids=list(state.get("pending_obligations", [])),
    )
