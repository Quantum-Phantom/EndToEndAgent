"""Bridge functions called by ReCAP graph nodes."""

from __future__ import annotations

from typing import Any

from recap.contracts.models import ContractStatus, RuntimeContract
from recap.ledger.models import LedgerEvent, LedgerEventType
from recap.ledger.service import LedgerService
from recap.schemas import IntentCertificate


async def create_contract_and_record(
    *,
    ledger: LedgerService,
    task_id: str,
    thread_id: str,
    certificate: IntentCertificate,
    allowed_tools: list[str],
    permissions: list[str],
    policy_refs: list[str],
) -> tuple[RuntimeContract, LedgerEvent]:
    contract = RuntimeContract(
        task_id=task_id,
        round_num=certificate.round_num,
        certificate=certificate,
        allowed_tools=allowed_tools,
        granted_permissions=permissions,
        policy_refs=policy_refs,
    )
    event = await ledger.record(
        event_type=LedgerEventType.CONTRACT_CREATED,
        task_id=task_id,
        thread_id=thread_id,
        round_num=certificate.round_num,
        contract_id=contract.contract_id,
        actor="think_node",
        payload=contract.model_dump(mode="json"),
    )
    return contract, event


async def transition_contract_and_record(
    *,
    ledger: LedgerService,
    contract: RuntimeContract,
    thread_id: str,
    target: ContractStatus,
    event_type: LedgerEventType,
    actor: str,
    details: dict[str, Any] | None = None,
) -> tuple[RuntimeContract, LedgerEvent]:
    updated = contract.transition_to(target)
    event = await ledger.record(
        event_type=event_type,
        task_id=updated.task_id,
        thread_id=thread_id,
        round_num=updated.round_num,
        contract_id=updated.contract_id,
        actor=actor,
        payload={
            "previous_status": contract.status.value,
            "new_status": updated.status.value,
            "details": details or {},
        },
    )
    return updated, event


async def record_violation(
    *,
    ledger: LedgerService,
    contract: RuntimeContract,
    thread_id: str,
    actor: str,
    violation_payload: dict[str, Any],
) -> LedgerEvent:
    return await ledger.record(
        event_type=LedgerEventType.VIOLATION_DETECTED,
        task_id=contract.task_id,
        thread_id=thread_id,
        round_num=contract.round_num,
        contract_id=contract.contract_id,
        actor=actor,
        payload=violation_payload,
    )
