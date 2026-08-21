from __future__ import annotations

import pytest

from recap.contracts import ContractPipeline, TaskContract
from recap.ledger import InMemoryLedgerRepository, LedgerEventType, LedgerService
from recap.nodes import (
    build_act_node,
    build_act_observe_check_node,
    build_observe_node,
    build_observe_think_check_node,
    build_think_act_check_node,
)
from recap.schemas import IntentCertificate, RecoveryAction, TaskEntry
from recap.tools import SANDBOX_MAILBOX, TOOL_CAPABILITIES, ToolRegistry
from recap.tools.email import send_email
from recap.verification import replay_violation

AUTHORIZED = {
    "recipient": "audit@example.test",
    "subject": "Approved research summary",
    "body": "Ada research",
}


def _certificate(round_num: int) -> IntentCertificate:
    return IntentCertificate(
        round_num=round_num,
        subgoal="send the authorized research summary",
        proposed_operation="send_email",
        argument_constraints={key: {"eq": value} for key, value in AUTHORIZED.items()},
        authority_basis="user_request:email-recovery",
        expected_effect="one authorized sandbox email is recorded",
        allowed_effects=["sandbox_email_sent"],
        forbidden_effects=[
            "email_sent_to_unauthorized_recipient",
            "secret_disclosed",
        ],
        required_effects=["sandbox_email_sent"],
        required_evidence=[
            "tool_return",
            "call_id_binding",
            "sandbox_email_receipt",
            "message_id",
            "state_diff",
        ],
    )


def _runtime():
    repository = InMemoryLedgerRepository()
    ledger = LedgerService(repository)
    pipeline = ContractPipeline()
    registry = ToolRegistry()
    registry.register(send_email, TOOL_CAPABILITIES["send_email"])
    task = TaskContract(
        task_id="email-recovery",
        objective="send only the authorized summary",
        capability_names=["send_email"],
        granted_permissions=["email:send"],
        authority_refs=["user_request:email-recovery"],
        policy_refs=["no-unauthorized-recipient", "no-secret-disclosure"],
    )
    entry = TaskEntry(
        task_id=task.task_id,
        description=task.objective,
        policies=task.policy_refs,
        tools_available=task.capability_names,
        initial_permissions=task.granted_permissions,
    )
    return repository, ledger, pipeline, registry, task, entry


async def _record_contract(ledger, contract) -> None:
    await ledger.record(
        event_type=LedgerEventType.CONTRACT_CREATED,
        task_id=contract.task_id,
        thread_id="thread-email-recovery",
        round_num=contract.round_num,
        contract_id=contract.contract_id,
        actor="test_contract_source",
        payload={"contract": contract.model_dump(mode="json")},
    )


@pytest.fixture(autouse=True)
def empty_mailbox():
    SANDBOX_MAILBOX.clear()
    yield
    SANDBOX_MAILBOX.clear()


@pytest.mark.asyncio
async def test_illegal_email_is_blocked_before_trusted_execution() -> None:
    repository, ledger, pipeline, registry, task, entry = _runtime()
    contract, _ = pipeline.policy_compiler.compile(
        task, _certificate(1), TOOL_CAPABILITIES["send_email"]
    )
    await _record_contract(ledger, contract)
    illegal = {
        "id": "illegal-email",
        "name": "send_email",
        "args": {
            **AUTHORIZED,
            "recipient": "external-attacker@example.test",
            "body": "TEST_SECRET_DO_NOT_SEND_7F3A",
        },
    }
    state = {
        "messages": [],
        "task_id": task.task_id,
        "thread_id": "thread-email-recovery",
        "round_num": 1,
        "task_entry": entry,
        "task_contract": task,
        "current_contract": contract,
        "candidate_tool_call": illegal,
        "pending_obligations": ["existing-obligation"],
        "check_results": [],
        "task_completed": False,
    }
    update = await build_think_act_check_node(ledger, pipeline, registry)(state)
    events = await repository.list_events(task.task_id, state["thread_id"])
    types = [event.event_type for event in events]
    assert update["next_route"] == "replan"
    assert update["check_results"][0].violations[0].rule_id == "CONTRACT-ARGS-001"
    assert update["recovery_context"]["pending_obligations"] == ["existing-obligation"]
    assert LedgerEventType.VIOLATION_DETECTED in types
    assert LedgerEventType.ACTION_BLOCKED in types
    assert LedgerEventType.ACTION_APPROVED not in types
    assert LedgerEventType.ACTION_EXECUTED not in types
    assert SANDBOX_MAILBOX.messages == []
    assert update["task_completed"] is False
    assert update["final_answer_allowed"] is False
    witness = update["check_results"][0].violations[0]
    replay = replay_violation(witness)
    assert replay.blocked is True
    assert replay.decision == witness.decision
    assert "TEST_SECRET_DO_NOT_SEND_7F3A" not in witness.model_dump_json()
    assert await ledger.verify_chain(task.task_id, state["thread_id"])


@pytest.mark.asyncio
async def test_missing_email_permission_fails_closed() -> None:
    _, ledger, pipeline, registry, task, entry = _runtime()
    contract, _ = pipeline.policy_compiler.compile(
        task, _certificate(1), TOOL_CAPABILITIES["send_email"]
    )
    contract = contract.model_copy(update={"granted_permissions": []})
    update = await build_think_act_check_node(ledger, pipeline, registry)(
        {
            "messages": [],
            "task_id": task.task_id,
            "thread_id": "thread-missing-authority",
            "task_entry": entry,
            "task_contract": task,
            "current_contract": contract,
            "candidate_tool_call": {
                "id": "missing-authority",
                "name": "send_email",
                "args": dict(AUTHORIZED),
            },
            "pending_obligations": [],
            "check_results": [],
        }
    )
    assert update["check_results"][0].violations[0].rule_id == "CONTRACT-AUTHORITY-001"
    assert update["next_route"] == "human_approval"
    assert (
        update["check_results"][0].violations[0].decision
        == RecoveryAction.HUMAN_ESCALATION
    )
    assert SANDBOX_MAILBOX.messages == []


@pytest.mark.asyncio
async def test_constraint_checker_exception_fails_closed(monkeypatch) -> None:
    _, ledger, pipeline, registry, task, entry = _runtime()
    contract, _ = pipeline.policy_compiler.compile(
        task, _certificate(1), TOOL_CAPABILITIES["send_email"]
    )

    def fail_checker(*_args, **_kwargs):
        raise RuntimeError("indeterminate safety fact")

    monkeypatch.setattr(pipeline.constraint_verifier, "verify", fail_checker)
    update = await build_think_act_check_node(ledger, pipeline, registry)(
        {
            "messages": [],
            "task_id": task.task_id,
            "thread_id": "thread-checker-failure",
            "task_entry": entry,
            "task_contract": task,
            "current_contract": contract,
            "candidate_tool_call": {
                "id": "checker-failure",
                "name": "send_email",
                "args": dict(AUTHORIZED),
            },
            "pending_obligations": [],
            "check_results": [],
        }
    )
    assert update["check_results"][0].violations[0].rule_id == "CONTRACT-CHECKER-001"
    assert update["next_route"] == "replan"
    assert SANDBOX_MAILBOX.messages == []


@pytest.mark.asyncio
async def test_block_then_replan_executes_only_authorized_email() -> None:
    repository, ledger, pipeline, registry, task, entry = _runtime()
    first, _ = pipeline.policy_compiler.compile(
        task, _certificate(1), TOOL_CAPABILITIES["send_email"]
    )
    await _record_contract(ledger, first)
    state = {
        "messages": [],
        "task_id": task.task_id,
        "thread_id": "thread-email-recovery",
        "round_num": 1,
        "task_entry": entry,
        "task_contract": task,
        "current_contract": first,
        "candidate_tool_call": {
            "id": "illegal-email",
            "name": "send_email",
            "args": {**AUTHORIZED, "recipient": "external-attacker@example.test"},
        },
        "pending_obligations": [],
        "check_results": [],
        "task_completed": False,
    }
    blocked = await build_think_act_check_node(ledger, pipeline, registry)(state)
    assert SANDBOX_MAILBOX.messages == []

    second, _ = pipeline.policy_compiler.compile(
        blocked["task_contract"], _certificate(2), TOOL_CAPABILITIES["send_email"]
    )
    await _record_contract(ledger, second)
    recovered = {
        **state,
        **blocked,
        "round_num": 2,
        "current_contract": second,
        "candidate_tool_call": {
            "id": "legal-email",
            "name": "send_email",
            "args": dict(AUTHORIZED),
        },
        "check_results": [],
    }
    approved = await build_think_act_check_node(ledger, pipeline, registry)(recovered)
    acted = await build_act_node(ledger, registry)({**recovered, **approved})
    observed = await build_observe_node(ledger)({**recovered, **approved, **acted})
    checked = await build_act_observe_check_node(ledger, pipeline)(
        {**recovered, **approved, **acted, **observed}
    )
    fulfilled = await build_observe_think_check_node(ledger, pipeline)(
        {**recovered, **approved, **acted, **observed, **checked}
    )

    events = await repository.list_events(task.task_id, state["thread_id"])
    executed = [
        event.payload["action"]
        for event in events
        if event.event_type == LedgerEventType.ACTION_EXECUTED
    ]
    assert len(executed) == 1
    assert set(executed[0]["actual_params"]) == set(AUTHORIZED)
    assert all(
        value.startswith("sha256:")
        for value in executed[0]["actual_params"].values()
    )
    assert len(SANDBOX_MAILBOX.messages) == 1
    assert SANDBOX_MAILBOX.messages[0].recipient == "audit@example.test"
    assert SANDBOX_MAILBOX.messages[0].body == "Ada research"
    assert "TEST_SECRET_DO_NOT_SEND_7F3A" not in SANDBOX_MAILBOX.messages[0].body
    assert acted["raw_tool_result"].state_diff["effect_receipt"]
    assert checked["pending_obligations"] == []
    assert fulfilled["current_contract"].status.value == "fulfilled"
    assert len(fulfilled["task_contract"].versions) == 2
    assert await ledger.verify_chain(task.task_id, state["thread_id"])
