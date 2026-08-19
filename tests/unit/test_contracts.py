# -*- coding: utf-8 -*-

import pytest

from recap.contracts import ContractStatus, RuntimeContract
from recap.schemas import IntentCertificate


@pytest.fixture
def certificate() -> IntentCertificate:
    return IntentCertificate(
        round_num=1,
        subgoal="计算 3 和 4 的和",
        proposed_operation="add",
        argument_constraints={
            "a": {"eq": 3},
            "b": {"eq": 4},
        },
        authority_basis="user_request:test-001",
        expected_effect="返回 7，不产生外部副作用",
        required_evidence=[
            "tool_return",
            "call_id_binding",
        ],
    )


@pytest.fixture
def contract(certificate: IntentCertificate) -> RuntimeContract:
    return RuntimeContract(
        task_id="task-test-001",
        round_num=1,
        certificate=certificate,
        granted_permissions=["arithmetic:execute"],
        policy_refs=["POLICY-ARITHMETIC-001"],
    )


def test_chinese_encoding() -> None:
    text = "计算 3 和 4 的和"
    assert text == "计算 3 和 4 的和"
    print(text)


def test_runtime_contract_created(contract: RuntimeContract) -> None:
    assert contract.contract_id.startswith("contract-")
    assert contract.task_id == "task-test-001"
    assert contract.round_num == 1
    assert contract.status == ContractStatus.DRAFT
    assert contract.certificate.proposed_operation == "add"


def test_constraints_populated_from_certificate(contract: RuntimeContract) -> None:
    assert contract.allowed_tools == ["add"]
    assert contract.argument_constraints == {
        "a": {"eq": 3},
        "b": {"eq": 4},
    }
    assert contract.expected_effects == ["返回 7，不产生外部副作用"]
    assert contract.required_evidence == ["tool_return", "call_id_binding"]
    assert contract.authority_refs == ["user_request:test-001"]


def test_tool_permission_check(contract: RuntimeContract) -> None:
    assert contract.tool_is_allowed("add") is True
    assert contract.tool_is_allowed("multiply") is False
    assert contract.tool_is_allowed("send_email") is False


@pytest.mark.parametrize(
    ("collected", "expected"),
    [
        ([], {"tool_return", "call_id_binding"}),
        (["tool_return"], {"call_id_binding"}),
        (["tool_return", "call_id_binding"], set()),
        (["tool_return", "call_id_binding", "extra"], set()),
    ],
)
def test_missing_evidence(
    contract: RuntimeContract,
    collected: list[str],
    expected: set[str],
) -> None:
    assert contract.missing_evidence(collected) == expected


def test_legal_contract_transitions(contract: RuntimeContract) -> None:
    active = contract.transition_to(ContractStatus.ACTIVE)
    executing = active.transition_to(ContractStatus.EXECUTING)
    pending = executing.transition_to(ContractStatus.EVIDENCE_PENDING)
    fulfilled = pending.transition_to(ContractStatus.FULFILLED)

    assert active.status == ContractStatus.ACTIVE
    assert executing.status == ContractStatus.EXECUTING
    assert pending.status == ContractStatus.EVIDENCE_PENDING
    assert fulfilled.status == ContractStatus.FULFILLED

    direct_fulfilled = executing.transition_to(ContractStatus.FULFILLED)
    assert direct_fulfilled.status == ContractStatus.FULFILLED


@pytest.mark.parametrize(
    ("source", "target"),
    [
        (ContractStatus.DRAFT, ContractStatus.EXECUTING),
        (ContractStatus.DRAFT, ContractStatus.FULFILLED),
        (ContractStatus.ACTIVE, ContractStatus.FULFILLED),
        (ContractStatus.FULFILLED, ContractStatus.ACTIVE),
        (ContractStatus.BLOCKED, ContractStatus.ACTIVE),
        (ContractStatus.VIOLATED, ContractStatus.EXECUTING),
        (ContractStatus.EXPIRED, ContractStatus.ACTIVE),
    ],
)
def test_illegal_contract_transitions(
    contract: RuntimeContract,
    source: ContractStatus,
    target: ContractStatus,
) -> None:
    source_contract = contract.model_copy(update={"status": source})

    with pytest.raises(ValueError, match="Illegal contract transition"):
        source_contract.transition_to(target)


def test_transition_does_not_mutate_original(contract: RuntimeContract) -> None:
    original_updated_at = contract.updated_at
    active = contract.transition_to(ContractStatus.ACTIVE)

    assert contract.status == ContractStatus.DRAFT
    assert contract.updated_at == original_updated_at
    assert active.status == ContractStatus.ACTIVE
    assert active.updated_at >= original_updated_at
    assert active is not contract
    assert active.contract_id == contract.contract_id


def test_explicit_constraints_are_preserved(
    certificate: IntentCertificate,
) -> None:
    explicit = RuntimeContract(
        task_id="task-test-002",
        round_num=1,
        certificate=certificate,
        allowed_tools=["safe_add"],
        argument_constraints={
            "a": {"gte": 0},
            "b": {"gte": 0},
        },
        granted_permissions=["safe-arithmetic:execute"],
        expected_effects=["返回非负数"],
        forbidden_effects=["网络访问", "文件写入"],
        required_evidence=["signed_tool_return"],
        authority_refs=["policy:test"],
        policy_refs=["POLICY-CUSTOM-001"],
    )

    assert explicit.allowed_tools == ["safe_add"]
    assert explicit.argument_constraints == {
        "a": {"gte": 0},
        "b": {"gte": 0},
    }
    assert explicit.granted_permissions == ["safe-arithmetic:execute"]
    assert explicit.expected_effects == ["返回非负数"]
    assert explicit.forbidden_effects == ["网络访问", "文件写入"]
    assert explicit.required_evidence == ["signed_tool_return"]
    assert explicit.authority_refs == ["policy:test"]
    assert explicit.policy_refs == ["POLICY-CUSTOM-001"]