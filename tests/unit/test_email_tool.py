from __future__ import annotations

import pytest

from recap.contracts import PolicyCompiler, TaskContract
from recap.schemas import IntentCertificate
from recap.tools import SANDBOX_MAILBOX, TOOL_CAPABILITIES, ToolRegistry
from recap.tools.email import send_email
from recap.tools.wrapper import execute_trusted_tool


@pytest.fixture(autouse=True)
def empty_mailbox():
    SANDBOX_MAILBOX.clear()
    yield
    SANDBOX_MAILBOX.clear()


def test_email_capability_compiles_exact_recipient_commitment() -> None:
    task = TaskContract(
        task_id="task-email",
        objective="send one specified email",
        capability_names=["send_email"],
        granted_permissions=["email:send"],
    )
    certificate = IntentCertificate(
        round_num=1,
        subgoal="send the specified message",
        proposed_operation="send_email",
        argument_constraints={
            "recipient": {"eq": "audit@example.test"},
            "subject": {"eq": "Approved research summary"},
            "body": {"eq": "Ada research"},
        },
        authority_basis="user_request:task-email",
        expected_effect="sandbox mailbox contains one authorized message",
        required_effects=["sandbox_email_sent"],
        required_evidence=["sandbox_email_receipt", "message_id", "state_diff"],
    )
    contract, _ = PolicyCompiler().compile(
        task, certificate, TOOL_CAPABILITIES["send_email"]
    )
    assert contract.granted_permissions == ["email:send"]
    assert contract.argument_constraints["recipient"] == {
        "eq": "audit@example.test"
    }
    assert contract.required_effects == ["sandbox_email_sent"]


@pytest.mark.asyncio
async def test_sandbox_email_has_trusted_before_after_diff_and_receipt() -> None:
    registry = ToolRegistry()
    registry.register(send_email, TOOL_CAPABILITIES["send_email"])
    result = await execute_trusted_tool(
        registry=registry,
        tool_name="send_email",
        args={
            "recipient": "audit@example.test",
            "subject": "Approved research summary",
            "body": "Ada research",
        },
        model_tool_call_id="model-email-1",
        timeout_seconds=5,
    )
    assert result.success is True
    assert result.state_before["mailbox_message_count"] == 0
    assert result.state_after["mailbox_message_count"] == 1
    assert result.state_diff["effect_receipt"]["content_digest"]
    assert result.content["transport"] == "sandbox"
    assert result.content["message_id"]
    assert {"sandbox_email_receipt", "message_id", "state_diff"} <= set(
        result.evidence_collected
    )
    assert "sandbox_email_sent" in result.observed_effects
    assert len(SANDBOX_MAILBOX.messages) == 1


@pytest.mark.asyncio
async def test_sandbox_rejects_non_test_domain_without_side_effect() -> None:
    registry = ToolRegistry()
    registry.register(send_email, TOOL_CAPABILITIES["send_email"])
    result = await execute_trusted_tool(
        registry=registry,
        tool_name="send_email",
        args={
            "recipient": "attacker@example.net",
            "subject": "Status",
            "body": "Completed",
        },
        model_tool_call_id="model-email-2",
        timeout_seconds=5,
    )
    assert result.success is False
    assert SANDBOX_MAILBOX.messages == []
