from recap.contracts import RuntimeContract
from recap.schemas import IntentCertificate
from recap.security import calculate_action_digest


def make_contract() -> RuntimeContract:
    certificate = IntentCertificate(
        round_num=1,
        subgoal="计算 3 和 4 的和",
        proposed_operation="add",
        argument_constraints={"a": {"eq": 3}, "b": {"eq": 4}},
        authority_basis="user_request:test-001",
        expected_effect="返回 7",
        required_evidence=["tool_return"],
    )
    return RuntimeContract(
        task_id="task-digest-001",
        round_num=1,
        certificate=certificate,
    )


def test_same_action_has_same_digest() -> None:
    contract = make_contract()
    candidate = {"id": "call-1", "name": "add", "args": {"a": 3, "b": 4}}

    assert calculate_action_digest(contract, candidate) == calculate_action_digest(
        contract, candidate
    )


def test_argument_order_does_not_change_digest() -> None:
    contract = make_contract()
    left = {"id": "call-1", "name": "add", "args": {"a": 3, "b": 4}}
    right = {"name": "add", "args": {"b": 4, "a": 3}, "id": "call-1"}

    assert calculate_action_digest(contract, left) == calculate_action_digest(
        contract, right
    )


def test_tool_name_change_changes_digest() -> None:
    contract = make_contract()
    approved = {"id": "call-1", "name": "add", "args": {"a": 3, "b": 4}}
    modified = {"id": "call-1", "name": "multiply", "args": {"a": 3, "b": 4}}

    assert calculate_action_digest(contract, approved) != calculate_action_digest(
        contract, modified
    )


def test_argument_change_changes_digest() -> None:
    contract = make_contract()
    approved = {"id": "call-1", "name": "add", "args": {"a": 3, "b": 4}}
    modified = {"id": "call-1", "name": "add", "args": {"a": 3, "b": 40}}

    assert calculate_action_digest(contract, approved) != calculate_action_digest(
        contract, modified
    )


def test_model_call_id_change_changes_digest() -> None:
    contract = make_contract()
    approved = {"id": "call-1", "name": "add", "args": {"a": 3, "b": 4}}
    modified = {"id": "call-2", "name": "add", "args": {"a": 3, "b": 4}}

    assert calculate_action_digest(contract, approved) != calculate_action_digest(
        contract, modified
    )


def test_contract_change_changes_digest() -> None:
    first = make_contract()
    second = make_contract()
    candidate = {"id": "call-1", "name": "add", "args": {"a": 3, "b": 4}}

    assert calculate_action_digest(first, candidate) != calculate_action_digest(
        second, candidate
    )