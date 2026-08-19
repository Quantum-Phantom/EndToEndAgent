"""Act -> Observe evidence verification and contract settlement."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Any, Literal

from recap.agent.state import ReCAPState
from recap.contracts import ContractStatus, RuntimeContract
from recap.integration import record_violation, transition_contract_and_record
from recap.ledger import LedgerEvent, LedgerEventType, LedgerService
from recap.schemas import (
    ExecutionStatus,
    ObservationEvent,
    RecoveryAction,
    TransitionResult,
    ViolationEvidence,
    ViolationType,
)

ActObserveNode = Callable[[ReCAPState], Awaitable[dict[str, Any]]]
AfterActObserveRoute = Literal["replan", "human_approval", "end"]


def _thread_id(state: ReCAPState) -> str:
    return state.get("thread_id", "default-thread")


def _violation(
    *,
    violation_type: ViolationType,
    rule_id: str,
    description: str,
    intent_field: str,
    expected: Any,
    actual: Any,
) -> ViolationEvidence:
    return ViolationEvidence(
        violation_type=violation_type,
        rule_id=rule_id,
        rule_description=description,
        intent_field=intent_field,
        expected_value=expected,
        actual_value=actual,
        decision=RecoveryAction.BLOCK,
        evidence_chain=[
            f"rule={rule_id}",
            f"expected={expected!r}",
            f"actual={actual!r}",
        ],
    )


async def _block_contract(
    *,
    ledger: LedgerService,
    contract: RuntimeContract,
    state: ReCAPState,
    violation: ViolationEvidence,
) -> dict[str, Any]:
    violation_event = await record_violation(
        ledger=ledger,
        contract=contract,
        thread_id=_thread_id(state),
        actor="act_observe_check_node",
        violation_payload=violation.model_dump(mode="json"),
    )
    if contract.status in {
        ContractStatus.FULFILLED,
        ContractStatus.VIOLATED,
        ContractStatus.BLOCKED,
        ContractStatus.FAILED,
        ContractStatus.EXPIRED,
    }:
        check = TransitionResult.blocked("act->observe", [violation])
        return {
            "current_contract": contract,
            "check_results": [check],
            "ledger_events": [violation_event],
            "ledger_head_hash": violation_event.event_hash,
            "pending_obligations": list(contract.required_evidence),
            "next_route": "end",
            "final_answer_allowed": False,
        }
    blocked_contract, blocked_event = await transition_contract_and_record(
        ledger=ledger,
        contract=contract,
        thread_id=_thread_id(state),
        target=ContractStatus.BLOCKED,
        event_type=LedgerEventType.CONTRACT_BLOCKED,
        actor="act_observe_check_node",
        details={
            "rule_id": violation.rule_id,
            "violation_id": violation.violation_id,
        },
    )
    check = TransitionResult.blocked("act->observe", [violation])
    return {
        "current_contract": blocked_contract,
        "check_results": [check],
        "ledger_events": [violation_event, blocked_event],
        "ledger_head_hash": blocked_event.event_hash,
        "pending_obligations": list(blocked_contract.required_evidence),
        "next_route": "end",
        "final_answer_allowed": False,
    }


def build_act_observe_check_node(ledger: LedgerService) -> ActObserveNode:
    """Build a verifier that binds observations and settles evidence obligations."""

    async def act_observe_check_node(state: ReCAPState) -> dict[str, Any]:
        contract = state.get("current_contract")
        action = state.get("current_action")
        observation = state.get("current_observation")

        # No contract means there is no safe ledger identity to mutate.
        if contract is None:
            violation = _violation(
                violation_type=ViolationType.EVIDENCE_INSUFFICIENT,
                rule_id="A2O-PRECONDITION-001",
                description="Act-to-Observe verification requires a RuntimeContract",
                intent_field="current_contract",
                expected="RuntimeContract",
                actual=None,
            )
            return {
                "check_results": [TransitionResult.blocked("act->observe", [violation])],
                "next_route": "end",
                "final_answer_allowed": False,
            }

        if contract.status != ContractStatus.EXECUTING:
            violation = _violation(
                violation_type=ViolationType.ACTION_VIOLATION,
                rule_id="A2O-CONTRACT-001",
                description="Contract must be EXECUTING before evidence settlement",
                intent_field="contract.status",
                expected=ContractStatus.EXECUTING.value,
                actual=contract.status.value,
            )
            return await _block_contract(
                ledger=ledger, contract=contract, state=state, violation=violation
            )

        if action is None or action.execution_status != ExecutionStatus.SUCCESS:
            violation = _violation(
                violation_type=ViolationType.ACTION_VIOLATION,
                rule_id="A2O-ACTION-001",
                description="A successful ActionEvent is required",
                intent_field="current_action.execution_status",
                expected=ExecutionStatus.SUCCESS.value,
                actual=(action.execution_status.value if action else None),
            )
            return await _block_contract(
                ledger=ledger, contract=contract, state=state, violation=violation
            )

        if observation is None:
            violation = _violation(
                violation_type=ViolationType.EVIDENCE_INSUFFICIENT,
                rule_id="A2O-OBSERVATION-001",
                description="ObservationEvent is required after successful execution",
                intent_field="current_observation",
                expected="ObservationEvent",
                actual=None,
            )
            return await _block_contract(
                ledger=ledger, contract=contract, state=state, violation=violation
            )

        if observation.call_id != action.call_id:
            violation = _violation(
                violation_type=ViolationType.ACTION_VIOLATION,
                rule_id="A2O-CALL-ID-001",
                description="Observation call_id must match Action call_id",
                intent_field="call_id_binding",
                expected=action.call_id,
                actual=observation.call_id,
            )
            return await _block_contract(
                ledger=ledger, contract=contract, state=state, violation=violation
            )

        missing = contract.missing_evidence(observation.evidence_collected)
        if missing:
            violation = _violation(
                violation_type=ViolationType.EVIDENCE_INSUFFICIENT,
                rule_id="A2O-EVIDENCE-001",
                description="Contract evidence obligations are incomplete",
                intent_field="required_evidence",
                expected=sorted(contract.required_evidence),
                actual={
                    "collected": sorted(observation.evidence_collected),
                    "missing": sorted(missing),
                },
            )
            return await _block_contract(
                ledger=ledger, contract=contract, state=state, violation=violation
            )

        evidence_contract, pending_event = await transition_contract_and_record(
            ledger=ledger,
            contract=contract,
            thread_id=_thread_id(state),
            target=ContractStatus.EVIDENCE_PENDING,
            event_type=LedgerEventType.CONTRACT_EVIDENCE_PENDING,
            actor="act_observe_check_node",
            details={"collected": observation.evidence_collected},
        )
        obligation_event: LedgerEvent = await ledger.record(
            event_type=LedgerEventType.OBLIGATION_FULFILLED,
            task_id=evidence_contract.task_id,
            thread_id=_thread_id(state),
            round_num=evidence_contract.round_num,
            contract_id=evidence_contract.contract_id,
            actor="act_observe_check_node",
            payload={
                "required": evidence_contract.required_evidence,
                "collected": observation.evidence_collected,
                "missing": [],
            },
        )
        fulfilled_contract, fulfilled_event = await transition_contract_and_record(
            ledger=ledger,
            contract=evidence_contract,
            thread_id=_thread_id(state),
            target=ContractStatus.FULFILLED,
            event_type=LedgerEventType.CONTRACT_FULFILLED,
            actor="act_observe_check_node",
            details={"obligations_fulfilled": evidence_contract.required_evidence},
        )
        completed_observation: ObservationEvent = observation.model_copy(
            update={"is_complete": True}
        )
        return {
            "current_contract": fulfilled_contract,
            "current_observation": completed_observation,
            "check_results": [TransitionResult.pass_through("act->observe")],
            "ledger_events": [pending_event, obligation_event, fulfilled_event],
            "ledger_head_hash": fulfilled_event.event_hash,
            "pending_obligations": [],
            "next_route": "end",
            "final_answer_allowed": True,
        }

    return act_observe_check_node


def route_after_act_observe(state: ReCAPState) -> AfterActObserveRoute:
    """Fail closed unless the verifier produced a terminal valid state."""

    contract = state.get("current_contract")
    route = state.get("next_route")
    if route == "replan" and contract is not None:
        return "replan"
    if route == "human_approval" and contract is not None:
        return "human_approval"
    if (
        route == "end"
        and contract is not None
        and contract.status in {ContractStatus.FULFILLED, ContractStatus.BLOCKED}
    ):
        return "end"
    return "end"


__all__ = ["build_act_observe_check_node", "route_after_act_observe"]