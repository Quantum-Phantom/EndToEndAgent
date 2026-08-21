"""Focused tests for the TaskContract lifecycle."""

import pytest

from recap.contracts import RuntimeContract, TaskContract
from recap.schemas import IntentCertificate


def _runtime_contract(task_id: str, round_num: int) -> RuntimeContract:
    certificate = IntentCertificate(
        round_num=round_num,
        subgoal="verify TaskContract lifecycle",
        proposed_operation="test_tool",
        argument_constraints={"value": {"gte": 1, "lte": 10}},
        authority_basis="test-authority",
        expected_effect="test_completed",
        allowed_effects=["test_completed"],
        required_effects=["test_completed"],
        required_evidence=["test_receipt"],
    )
    return RuntimeContract(
        task_id=task_id,
        round_num=round_num,
        certificate=certificate,
        granted_permissions=["test:execute"],
    )


def test_task_contract_initializes_without_round_versions() -> None:
    task = TaskContract(
        task_id="task-test",
        objective="verify TaskContract",
        capability_names=["test_tool"],
        granted_permissions=["test:execute"],
    )

    assert task.current is None
    assert task.versions == []
    assert task.pending_obligation_ids == []
    assert "no_authority_expansion" in task.normative_baseline


def test_task_contract_appends_round_versions_in_order() -> None:
    task = TaskContract(task_id="task-test", objective="verify TaskContract")
    first = _runtime_contract(task.task_id, 1)
    second = _runtime_contract(task.task_id, 2)

    updated = task.append_version(first).append_version(second)

    assert task.versions == []
    assert [item.round_num for item in updated.versions] == [1, 2]
    assert updated.current == second


def test_task_contract_rejects_contract_from_another_task() -> None:
    task = TaskContract(task_id="task-test", objective="verify TaskContract")
    foreign_contract = _runtime_contract("task-other", 1)

    with pytest.raises(ValueError, match="different task"):
        task.append_version(foreign_contract)


def test_task_contract_rejects_non_increasing_round_number() -> None:
    task = TaskContract(task_id="task-test", objective="verify TaskContract")
    task = task.append_version(_runtime_contract(task.task_id, 2))

    with pytest.raises(ValueError, match="increase monotonically"):
        task.append_version(_runtime_contract(task.task_id, 2))


def test_task_contract_deduplicates_pending_obligations() -> None:
    task = TaskContract(task_id="task-test", objective="verify TaskContract")

    updated = task.with_pending_obligations(["obl-1", "obl-1", "obl-2"])

    assert task.pending_obligation_ids == []
    assert updated.pending_obligation_ids == ["obl-1", "obl-2"]
