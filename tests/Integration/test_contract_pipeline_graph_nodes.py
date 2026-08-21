"""Integration coverage for one ContractPipeline shared by all transition nodes."""

from datetime import datetime, timezone

import pytest

from recap.contracts import ContractPipeline, ContractStatus, RuntimeContract
from recap.ledger import InMemoryLedgerRepository, LedgerService
from recap.nodes.act_observe import build_act_observe_check_node
from recap.nodes.observe_think import build_observe_think_check_node
from recap.nodes.think_act import build_think_act_check_node
from recap.schemas import (
    ActionEvent,
    ExecutionStatus,
    IntentCertificate,
    ObservationEvent,
)
from recap.tools.wrapper import TrustedToolResult


def _draft_contract() -> RuntimeContract:
    certificate = IntentCertificate(
        round_num=1,
        subgoal="execute one bounded tool call",
        proposed_operation="bounded_tool",
        argument_constraints={"count": {"gte": 1, "lte": 3}},
        authority_basis="user_request:task-pipeline",
        expected_effect="tool returns a receipt",
        required_evidence=["tool_return", "call_id_binding"],
    )
    return RuntimeContract(
        task_id="task-pipeline",
        round_num=1,
        certificate=certificate,
        allowed_tools=["bounded_tool"],
        granted_permissions=["tool:execute"],
    )


@pytest.mark.asyncio
async def test_shared_pipeline_tracks_contract_across_all_transitions() -> None:
    ledger = LedgerService(InMemoryLedgerRepository())
    pipeline = ContractPipeline()
    contract = _draft_contract()
    state = {
        "messages": [],
        "task_id": contract.task_id,
        "thread_id": "thread-pipeline",
        "round_num": 1,
        "current_contract": contract,
        "candidate_tool_call": {
            "id": "model-call-1",
            "name": "bounded_tool",
            "args": {"count": 2},
            "type": "tool_call",
        },
        "pending_obligations": [],
        "check_results": [],
    }

    think_update = await build_think_act_check_node(ledger, pipeline)(state)
    assert think_update["current_contract"].status == ContractStatus.ACTIVE
    assert think_update["task_contract"].current.status == ContractStatus.ACTIVE

    executing = think_update["current_contract"].transition_to(
        ContractStatus.EXECUTING
    )
    task_contract = think_update["task_contract"].replace_current_version(executing)
    now = datetime.now(timezone.utc)
    result = TrustedToolResult(
        call_id="call-1",
        tool_name="bounded_tool",
        args={"count": 2},
        success=True,
        content={"receipt": "ok"},
        observed_effects=["tool_return"],
        started_at=now,
        completed_at=now,
    )
    action = ActionEvent(
        call_id=result.call_id,
        tool_name=result.tool_name,
        actual_params=result.args,
        execution_status=ExecutionStatus.SUCCESS,
        certificate_id=contract.certificate.certificate_id,
    )
    observation = ObservationEvent(
        call_id=result.call_id,
        return_content=result.content,
        evidence_collected=["tool_return", "call_id_binding"],
        observed_effects=result.observed_effects,
        source_label="trusted_tool:bounded_tool",
        is_complete=False,
    )
    act_observe_state = {
        **state,
        **think_update,
        "current_contract": executing,
        "task_contract": task_contract,
        "current_action": action,
        "current_observation": observation,
        "raw_tool_result": result,
    }

    evidence_update = await build_act_observe_check_node(ledger, pipeline)(
        act_observe_state
    )
    assert evidence_update["evidence_bundle"].valid_binding
    assert evidence_update["pending_obligations"] == []
    assert evidence_update["current_contract"].status == ContractStatus.EVIDENCE_PENDING

    observe_think_state = {**act_observe_state, **evidence_update}
    fulfilled_update = await build_observe_think_check_node(ledger, pipeline)(
        observe_think_state
    )

    assert fulfilled_update["current_contract"].status == ContractStatus.FULFILLED
    assert fulfilled_update["task_contract"].current.status == ContractStatus.FULFILLED
    assert fulfilled_update["task_contract"].pending_obligation_ids == []
    assert pipeline.obligation_manager.pending(contract.task_id) == []


@pytest.mark.asyncio
async def test_think_act_uses_z3_constraints_from_pipeline() -> None:
    ledger = LedgerService(InMemoryLedgerRepository())
    pipeline = ContractPipeline()
    contract = _draft_contract()
    state = {
        "messages": [],
        "task_id": contract.task_id,
        "thread_id": "thread-z3",
        "round_num": 1,
        "current_contract": contract,
        "candidate_tool_call": {
            "id": "model-call-invalid",
            "name": "bounded_tool",
            "args": {"count": 9},
        },
        "check_results": [],
    }

    update = await build_think_act_check_node(ledger, pipeline)(state)

    violation = update["check_results"][0].violations[0]
    assert update["current_contract"].status == ContractStatus.BLOCKED
    assert violation.rule_id == "CONTRACT-ARGS-001"
    assert violation.actual_value["witness_rule_ids"] == ["argument.count.lte"]
