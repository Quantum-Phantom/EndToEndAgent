"""Act -> Observe evidence verification and contract settlement."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Any, Literal

from recap.agent.state import ReCAPState
from recap.contracts import ContractPipeline, ContractStatus, RuntimeContract
from recap.integration import record_violation, transition_contract_and_record
from recap.ledger import LedgerEvent, LedgerEventType, LedgerService
from recap.recovery import route_for_recovery
from recap.schemas import (
    DataSource,
    ExecutionStatus,
    ObservationEvent,
    RecoveryAction,
    TransitionResult,
    ViolationEvidence,
    ViolationType,
)

ActObserveNode = Callable[[ReCAPState], Awaitable[dict[str, Any]]]
AfterActObserveRoute = Literal["observe_think", "replan", "human_approval", "end"]


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
    decision: RecoveryAction = RecoveryAction.BLOCK,
) -> ViolationEvidence:
    return ViolationEvidence(
        violation_type=violation_type,
        rule_id=rule_id,
        rule_description=description,
        intent_field=intent_field,
        expected_value=expected,
        actual_value=actual,
        decision=decision,
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
            "next_route": route_for_recovery(violation.decision),
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


async def _keep_evidence_pending(
    *,
    ledger: LedgerService,
    contract: RuntimeContract,
    state: ReCAPState,
    observation: ObservationEvent,
    violation: ViolationEvidence,
    missing: set[str],
) -> dict[str, Any]:
    violation_event = await record_violation(
        ledger=ledger,
        contract=contract,
        thread_id=_thread_id(state),
        actor="act_observe_check_node",
        violation_payload=violation.model_dump(mode="json"),
    )
    pending_contract, pending_event = await transition_contract_and_record(
        ledger=ledger,
        contract=contract,
        thread_id=_thread_id(state),
        target=ContractStatus.EVIDENCE_PENDING,
        event_type=LedgerEventType.CONTRACT_EVIDENCE_PENDING,
        actor="act_observe_check_node",
        details={"missing": sorted(missing)},
    )
    obligation_event = await ledger.record(
        event_type=LedgerEventType.OBLIGATION_CREATED,
        task_id=pending_contract.task_id,
        thread_id=_thread_id(state),
        round_num=pending_contract.round_num,
        contract_id=pending_contract.contract_id,
        actor="act_observe_check_node",
        payload={"status": "pending", "missing": sorted(missing)},
    )
    check = TransitionResult.blocked(
        "act->observe", [violation], [RecoveryAction.KEEP_UNFINISHED]
    )
    return {
        "current_contract": pending_contract,
        "current_observation": observation.model_copy(update={"is_complete": False}),
        "check_results": [check],
        "ledger_events": [violation_event, pending_event, obligation_event],
        "ledger_head_hash": obligation_event.event_hash,
        "pending_obligations": sorted(missing),
        "round_summaries": [
            {
                "round_num": pending_contract.round_num,
                "contract_id": pending_contract.contract_id,
                "status": pending_contract.status.value,
                "tool_name": pending_contract.certificate.proposed_operation,
                "subgoal": pending_contract.certificate.subgoal,
                "pending_obligations": sorted(missing),
                "result": (
                    observation.return_content
                    if observation.data_source != DataSource.EXTERNAL
                    else None
                ),
            }
        ],
        "next_route": route_for_recovery(violation.decision),
        "task_completed": False,
        "final_answer_allowed": False,
    }


def build_act_observe_check_node(
    ledger: LedgerService,
    pipeline: ContractPipeline | None = None,
) -> ActObserveNode:
    """Build a verifier that binds observations and settles evidence obligations."""

    pipeline = pipeline or ContractPipeline()

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
        "next_route": route_for_recovery(RecoveryAction.KEEP_UNFINISHED),
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

        result = state.get("raw_tool_result")
        evidence_bundle = (
            pipeline.adapt_evidence(result, action, observation)
            if result is not None
            else None
        )
        if evidence_bundle is not None and not evidence_bundle.valid_binding:
            violation = _violation(
                violation_type=ViolationType.ACTION_VIOLATION,
                rule_id="A2O-TOOL-BINDING-001",
                description="Trusted result must match action call_id, tool and parameters",
                intent_field="action_binding",
                expected={
                    "call_id": action.call_id,
                    "tool_name": action.tool_name,
                    "args": action.actual_params,
                },
                actual={"binding_errors": evidence_bundle.binding_errors},
            )
            return await _block_contract(
                ledger=ledger, contract=contract, state=state, violation=violation
            )

        observed_effects = set(observation.observed_effects)
        semantic_effects = observed_effects - {"tool_return", "call_id_binding"}
        forbidden = semantic_effects & set(contract.forbidden_effects)
        unexpected = (
            semantic_effects
            - set(contract.allowed_effects)
            - set(contract.required_effects)
            if contract.allowed_effects
            else set()
        )
        if forbidden or unexpected:
            violation = _violation(
                violation_type=ViolationType.ACTION_VIOLATION,
                rule_id="A2O-EFFECT-BOUNDARY-001",
                description="Observed effects violate the contract effect boundary",
                intent_field="allowed_effects/forbidden_effects",
                expected={
                    "allowed": sorted(contract.allowed_effects),
                    "forbidden": sorted(contract.forbidden_effects),
                },
                actual={
                    "observed": sorted(semantic_effects),
                    "forbidden": sorted(forbidden),
                    "unexpected": sorted(unexpected),
                },
            )
            return await _block_contract(
                ledger=ledger, contract=contract, state=state, violation=violation
            )

        pipeline.open_obligations(contract)
        if evidence_bundle is not None:
            pipeline.settle_obligations(contract.task_id, evidence_bundle)
            pending = pipeline.obligation_manager.pending(contract.task_id)
            missing = {
                item.requirement if item.kind == "evidence" else f"effect:{item.requirement}"
                for item in pending
            }
        else:
            missing_evidence = contract.missing_evidence(observation.evidence_collected)
            missing_effects = set(contract.required_effects) - semantic_effects
            missing = set(missing_evidence) | {
                f"effect:{effect}" for effect in missing_effects
            }
        if missing:
            violation = _violation(
                violation_type=ViolationType.EVIDENCE_INSUFFICIENT,
                rule_id="A2O-EVIDENCE-001",
                description="Contract effects or evidence obligations are incomplete",
                intent_field="required_effects/required_evidence",
                expected={
                    "effects": sorted(contract.required_effects),
                    "evidence": sorted(contract.required_evidence),
                },
                actual={
                    "observed_effects": sorted(semantic_effects),
                    "collected": sorted(observation.evidence_collected),
                    "missing": sorted(missing),
                },
                decision=RecoveryAction.KEEP_UNFINISHED,
            )
            update = await _keep_evidence_pending(
                ledger=ledger,
                contract=contract,
                state=state,
                observation=observation,
                violation=violation,
                missing=missing,
            )
            task_contract = state.get("task_contract")
            if task_contract is not None:
                pending_ids = [
                    item.obligation_id
                    for item in pipeline.obligation_manager.pending(contract.task_id)
                ]
                update["task_contract"] = task_contract.replace_current_version(
                    update["current_contract"]
                ).with_pending_obligations(pending_ids)
            if evidence_bundle is not None:
                update["evidence_bundle"] = evidence_bundle
            return update

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
        completed_observation: ObservationEvent = observation.model_copy(
            update={"is_complete": True}
        )
        settled_task_contract = _settled_task_contract(state, evidence_contract)
        return {
            "current_contract": evidence_contract,
            **(
                {"task_contract": settled_task_contract}
                if settled_task_contract is not None
                else {}
            ),
            "current_observation": completed_observation,
            **({"evidence_bundle": evidence_bundle} if evidence_bundle is not None else {}),
            "check_results": [TransitionResult.pass_through("act->observe")],
            "ledger_events": [pending_event, obligation_event],
            "ledger_head_hash": obligation_event.event_hash,
            "pending_obligations": [],
            "next_route": "observe_think",
            "final_answer_allowed": False,
        }

    return act_observe_check_node


def _settled_task_contract(state: ReCAPState, contract: RuntimeContract):
    task_contract = state.get("task_contract")
    if task_contract is None:
        return None
    return task_contract.replace_current_version(contract).with_pending_obligations([])


def route_after_act_observe(state: ReCAPState) -> AfterActObserveRoute:
    """Fail closed unless the verifier produced a terminal valid state."""

    contract = state.get("current_contract")
    route = state.get("next_route")
    if (
        route == "observe_think"
        and contract is not None
        and contract.status == ContractStatus.EVIDENCE_PENDING
    ):
        return "observe_think"
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
