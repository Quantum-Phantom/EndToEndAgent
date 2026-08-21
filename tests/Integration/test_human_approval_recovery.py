from __future__ import annotations

import pytest

from recap.approval import HumanApprovalService
from recap.contracts import ContractPipeline, TaskContract
from recap.ledger import InMemoryLedgerRepository, LedgerEventType, LedgerService
from recap.nodes import build_act_node, build_human_approval_node, build_think_act_check_node
from recap.schemas import IntentCertificate, RecoveryAction, TaskEntry
from recap.tools import SANDBOX_MAILBOX, TOOL_CAPABILITIES, ToolRegistry
from recap.tools.email import send_email


ARGS = {
    "recipient": "audit@example.test",
    "subject": "Approved research summary",
    "body": "Ada research",
}


def _certificate(round_num: int) -> IntentCertificate:
    return IntentCertificate(
        round_num=round_num,
        subgoal="send the approved summary",
        proposed_operation="send_email",
        argument_constraints={key: {"eq": value} for key, value in ARGS.items()},
        authority_basis="user_request:approval-task",
        expected_effect="one sandbox message",
        allowed_effects=["sandbox_email_sent"],
        required_effects=["sandbox_email_sent"],
        required_evidence=["sandbox_email_receipt", "message_id", "state_diff"],
    )


@pytest.mark.asyncio
async def test_human_approval_suspends_then_grants_limited_authority_and_rechecks() -> None:
    SANDBOX_MAILBOX.clear()
    repository = InMemoryLedgerRepository()
    ledger = LedgerService(repository)
    pipeline = ContractPipeline()
    registry = ToolRegistry()
    registry.register(send_email, TOOL_CAPABILITIES["send_email"])
    service = HumanApprovalService(trusted_approvers={"security-reviewer"})
    task = TaskContract(
        task_id="approval-task",
        objective="send an approved summary",
        capability_names=["send_email"],
        granted_permissions=[],
        authority_refs=["user_request:approval-task"],
    )
    entry = TaskEntry(
        task_id=task.task_id,
        description=task.objective,
        tools_available=["send_email"],
        initial_permissions=[],
    )
    authorized_task = task.model_copy(update={"granted_permissions": ["email:send"]})
    contract, _ = pipeline.policy_compiler.compile(
        authorized_task, _certificate(1), TOOL_CAPABILITIES["send_email"]
    )
    contract = contract.model_copy(update={"granted_permissions": []})
    state = {
        "messages": [],
        "task_id": task.task_id,
        "thread_id": "approval-thread",
        "round_num": 1,
        "task_entry": entry,
        "task_contract": task,
        "current_contract": contract,
        "candidate_tool_call": {
            "id": "approval-candidate",
            "name": "send_email",
            "args": dict(ARGS),
        },
        "pending_obligations": ["existing-obligation"],
        "check_results": [],
        "task_completed": False,
    }

    blocked = await build_think_act_check_node(ledger, pipeline, registry)(state)
    violation = blocked["check_results"][0].violations[0]
    assert violation.decision == RecoveryAction.HUMAN_ESCALATION
    assert blocked["next_route"] == "human_approval"
    assert SANDBOX_MAILBOX.messages == []

    approval_node = build_human_approval_node(ledger, service)
    suspended = await approval_node({**state, **blocked})
    request = suspended["approval_request"]
    assert suspended["awaiting_approval"] is True
    assert suspended["next_route"] == "end"
    assert suspended["task_completed"] is False
    assert suspended["final_answer_allowed"] is False
    assert request.requested_permissions == ["email:send"]
    assert SANDBOX_MAILBOX.messages == []

    with pytest.raises(PermissionError, match="trusted approver"):
        service.decide(
            request.request_id,
            approver_id="agent",
            approved=True,
            granted_permissions=["email:send"],
        )
    with pytest.raises(PermissionError, match="requested scope"):
        service.decide(
            request.request_id,
            approver_id="security-reviewer",
            approved=True,
            granted_permissions=["email:send", "admin"],
        )

    decision = service.decide(
        request.request_id,
        approver_id="security-reviewer",
        approved=True,
        granted_permissions=["email:send"],
    )
    resumed = await approval_node({**state, **blocked, **suspended})
    assert resumed["awaiting_approval"] is False
    assert resumed["next_route"] == "replan"
    assert resumed["human_decision"].decision_id == decision.decision_id
    assert resumed["task_contract"].granted_permissions == ["email:send"]
    assert "admin" not in resumed["task_contract"].granted_permissions
    assert f"human_approval:{decision.decision_id}" in resumed["task_contract"].authority_refs
    assert resumed["recovery_context"]["pending_obligations"] == ["existing-obligation"]
    assert SANDBOX_MAILBOX.messages == []

    next_contract, _ = pipeline.policy_compiler.compile(
        resumed["task_contract"], _certificate(2), TOOL_CAPABILITIES["send_email"]
    )
    replan_state = {
        **state,
        **blocked,
        **suspended,
        **resumed,
        "round_num": 2,
        "current_contract": next_contract,
        "candidate_tool_call": {
            "id": "approved-candidate",
            "name": "send_email",
            "args": dict(ARGS),
        },
        "check_results": [],
    }
    approved = await build_think_act_check_node(ledger, pipeline, registry)(replan_state)
    assert approved["next_route"] == "act"
    acted = await build_act_node(ledger, registry)({**replan_state, **approved})
    assert acted["raw_tool_result"].success is True
    assert len(SANDBOX_MAILBOX.messages) == 1

    events = await repository.list_events(task.task_id, state["thread_id"])
    event_types = [event.event_type for event in events]
    assert LedgerEventType.HUMAN_APPROVAL_REQUESTED in event_types
    assert LedgerEventType.HUMAN_DECISION_RECORDED in event_types
    decision_event = next(
        event for event in events if event.event_type == LedgerEventType.HUMAN_DECISION_RECORDED
    )
    assert decision_event.payload["provenance"] == "trusted_human:security-reviewer"
    assert event_types.index(LedgerEventType.HUMAN_DECISION_RECORDED) < event_types.index(
        LedgerEventType.ACTION_EXECUTED
    )
    assert await ledger.verify_chain(task.task_id, state["thread_id"])
    SANDBOX_MAILBOX.clear()
