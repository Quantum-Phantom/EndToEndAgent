from typing import Any

import pytest

from recap.contracts import AuthorizationRequirement, ToolCapability
from recap.integration import create_contract_and_record
from recap.ledger import InMemoryLedgerRepository, LedgerEventType, LedgerService
from recap.nodes.think_act import build_think_act_check_node
from recap.schemas import IntentCertificate
from recap.tools import ToolRegistry, TrustedAuthorizationFact


class ProtectedTool:
    name = "protected"

    async def ainvoke(self, args: dict[str, Any]) -> Any:
        return args


def protected_capability() -> ToolCapability:
    return ToolCapability(
        name="protected",
        argument_constraints={
            "session_token": {"required": True},
            "order_id": {"required": True},
        },
        required_permissions=["protected:use"],
        authorization_requirements=[
            AuthorizationRequirement(
                fact_type="verified_session",
                argument_claim_bindings={
                    "session_token": "session_token",
                    "order_id": "order_id",
                },
            )
        ],
    )


async def state(ledger: LedgerService, facts: list[TrustedAuthorizationFact]):
    certificate = IntentCertificate(
        round_num=1,
        subgoal="use protected tool",
        proposed_operation="protected",
        argument_constraints={
            "session_token": {"eq": "session-1"},
            "order_id": {"eq": "O001"},
        },
        authority_basis="user_request:auth-test",
        expected_effect="read authorized record",
    )
    contract, _ = await create_contract_and_record(
        ledger=ledger,
        task_id="auth-task",
        thread_id="auth-thread",
        certificate=certificate,
        allowed_tools=["protected"],
        permissions=["protected:use"],
        policy_refs=[],
    )
    return {
        "messages": [],
        "task_id": "auth-task",
        "thread_id": "auth-thread",
        "round_num": 1,
        "current_contract": contract,
        "candidate_tool_call": {
            "id": "candidate-1",
            "name": "protected",
            "args": {"session_token": "session-1", "order_id": "O001"},
        },
        "authorization_facts": facts,
        "check_results": [],
    }


@pytest.mark.asyncio
async def test_pre_act_consumes_matching_fact_and_records_audit_event() -> None:
    repository = InMemoryLedgerRepository()
    ledger = LedgerService(repository)
    registry = ToolRegistry()
    registry.register(ProtectedTool(), protected_capability())
    fact = TrustedAuthorizationFact(
        fact_type="verified_session",
        issuer_tool="verify_identity",
        claims={"session_token": "session-1", "order_id": "O001"},
    )

    update = await build_think_act_check_node(ledger, registry=registry)(
        await state(ledger, [fact])
    )
    events = await repository.list_events("auth-task", "auth-thread")

    assert update["next_route"] == "act"
    assert events[-1].event_type == LedgerEventType.AUTHORIZATION_FACT_CONSUMED
    assert events[-1].payload["fact_id"] == fact.fact_id


@pytest.mark.asyncio
async def test_pre_act_blocks_missing_or_mismatched_fact() -> None:
    repository = InMemoryLedgerRepository()
    ledger = LedgerService(repository)
    registry = ToolRegistry()
    registry.register(ProtectedTool(), protected_capability())
    mismatched = TrustedAuthorizationFact(
        fact_type="verified_session",
        issuer_tool="verify_identity",
        claims={"session_token": "session-1", "order_id": "O002"},
    )

    update = await build_think_act_check_node(ledger, registry=registry)(
        await state(ledger, [mismatched])
    )

    assert update["next_route"] == "replan"
    assert update["check_results"][0].violations[0].rule_id == (
        "CONTRACT-AUTHORIZATION-FACT-001"
    )
