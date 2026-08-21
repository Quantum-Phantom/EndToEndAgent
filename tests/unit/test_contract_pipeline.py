from datetime import datetime, timezone

from recap.contracts import (
    ConstraintOperator,
    ContractPipeline,
    PolicyRule,
    TaskContract,
    ToolCapability,
)
from recap.evidence import EvidenceAdapter
from recap.recovery import RecoveryManager
from recap.schemas import (
    ActionEvent,
    ExecutionStatus,
    IntentCertificate,
    ObservationEvent,
    RecoveryAction,
    ViolationType,
)
from recap.tools.wrapper import TrustedToolResult


def _task() -> TaskContract:
    return TaskContract(
        task_id="task-1",
        objective="write an approved record",
        capability_names=["write_record"],
        granted_permissions=["record:write"],
        authority_refs=["user-request"],
        policy_refs=["policy-1"],
    )


def _capability() -> ToolCapability:
    return ToolCapability(
        name="write_record",
        argument_constraints={"count": {"gte": 1, "lte": 5}},
        required_permissions=["record:write"],
        allowed_effects=["record_written"],
        required_effects=["record_written"],
        evidence_types=["receipt"],
    )


def _certificate(round_num: int = 1) -> IntentCertificate:
    return IntentCertificate(
        round_num=round_num,
        subgoal="write one record",
        proposed_operation="write_record",
        argument_constraints={"count": {"gte": 1, "lte": 5}},
        authority_basis="user-request",
        expected_effect="record_written",
        allowed_effects=["record_written"],
        required_effects=["record_written"],
        required_evidence=["receipt"],
    )


def test_pipeline_compiles_and_versions_task_contract() -> None:
    pipeline = ContractPipeline()

    task, contract, compiled, cross_round = pipeline.compile_round(
        _task(), _certificate(), _capability()
    )

    assert cross_round.passed
    assert task.current == contract
    assert compiled.granted_permissions == ["record:write"]
    assert contract.required_evidence == ["receipt"]


def test_z3_constraint_verifier_returns_rule_witness() -> None:
    pipeline = ContractPipeline()
    _, _, compiled, _ = pipeline.compile_round(_task(), _certificate(), _capability())

    result = pipeline.verify_action(compiled, {"count": 9})

    assert not result.passed
    assert "argument.count.lte" in result.witness_rule_ids


def test_policy_compiler_rejects_missing_tool_permission() -> None:
    task = _task().model_copy(update={"granted_permissions": []})

    try:
        ContractPipeline().compile_round(task, _certificate(), _capability())
    except PermissionError as exc:
        assert "record:write" in str(exc)
    else:
        raise AssertionError("missing permission must fail closed")


def test_evidence_adapter_detects_cross_call_binding() -> None:
    now = datetime.now(timezone.utc)
    result = TrustedToolResult(
        call_id="call-1",
        tool_name="write_record",
        args={"count": 1},
        success=True,
        content={"id": "record-1"},
        observed_effects=["record_written"],
        started_at=now,
        completed_at=now,
    )
    action = ActionEvent(
        call_id="call-2",
        tool_name="write_record",
        actual_params={"count": 1},
        execution_status=ExecutionStatus.SUCCESS,
    )

    bundle = EvidenceAdapter().adapt(result, action)

    assert not bundle.valid_binding
    assert bundle.binding_errors == ["action call_id does not match trusted result"]


def test_pending_obligations_are_settled_by_normalized_evidence() -> None:
    pipeline = ContractPipeline()
    task, contract, _, _ = pipeline.compile_round(_task(), _certificate(), _capability())
    obligations = pipeline.open_obligations(contract)
    now = datetime.now(timezone.utc)
    trusted_result = TrustedToolResult(
        call_id="call-1",
        tool_name="write_record",
        args={"count": 1},
        success=True,
        content={"id": "record-1"},
        observed_effects=["record_written"],
        started_at=now,
        completed_at=now,
    )
    observation = ObservationEvent(
        call_id="call-1",
        evidence_collected=["receipt"],
        observed_effects=["record_written"],
    )

    bundle = pipeline.adapt_evidence(trusted_result, observation=observation)
    settled = pipeline.settle_obligations(task.task_id, bundle)

    assert len(obligations) == 2
    assert {item.requirement for item in settled} == {"receipt", "record_written"}
    assert pipeline.obligation_manager.pending(task.task_id) == []


def test_cross_round_verifier_detects_dropped_pending_obligation() -> None:
    pipeline = ContractPipeline()
    task, first, _, _ = pipeline.compile_round(_task(), _certificate(), _capability())
    pending = pipeline.open_obligations(first)
    second, _ = pipeline.policy_compiler.compile(task, _certificate(2), _capability())

    result = pipeline.cross_round_verifier.verify(task, first, second, pending, [])

    assert not result.passed
    assert result.violations[-1].rule_id == "preserve_pending_obligations"


def test_recovery_manager_contracts_out_of_range_argument() -> None:
    decision = RecoveryManager().decide(
        ViolationType.ACTION_VIOLATION,
        arguments={"count": 9},
        rules=[
            PolicyRule(
                rule_id="max-count",
                field="arguments.count",
                operator=ConstraintOperator.LTE,
                expected=5,
            )
        ],
    )

    assert decision.action == RecoveryAction.PARAMETER_FIX
    assert decision.repaired_arguments == {"count": 5}


def test_recovery_manager_keeps_missing_evidence_unfinished() -> None:
    decision = RecoveryManager().decide(ViolationType.EVIDENCE_INSUFFICIENT)

    assert decision.action == RecoveryAction.KEEP_UNFINISHED
