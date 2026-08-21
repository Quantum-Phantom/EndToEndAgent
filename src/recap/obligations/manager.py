"""Cross-round lifecycle management for evidence and effect obligations."""

from __future__ import annotations

import uuid
from datetime import datetime, timezone

from pydantic import BaseModel, Field

from recap.contracts.models import RuntimeContract
from recap.evidence.adapter import EvidenceBundle
from recap.schemas import ObligationStatus


class PendingObligation(BaseModel):
    obligation_id: str = Field(default_factory=lambda: f"obl-{uuid.uuid4().hex[:12]}")
    task_id: str
    contract_id: str
    created_round: int
    last_seen_round: int
    kind: str = Field(pattern=r"^(evidence|effect)$")
    requirement: str
    status: ObligationStatus = ObligationStatus.PENDING
    evidence_ids: list[str] = Field(default_factory=list)
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    resolved_at: datetime | None = None


class PendingObligationManager:
    def __init__(self) -> None:
        self._obligations: dict[str, PendingObligation] = {}

    def create_from_contract(self, contract: RuntimeContract) -> list[PendingObligation]:
        created: list[PendingObligation] = []
        for kind, requirements in (
            ("evidence", contract.required_evidence),
            ("effect", contract.required_effects),
        ):
            for requirement in requirements:
                existing = self._find_pending(contract.task_id, kind, requirement)
                if existing is not None:
                    created.append(existing)
                    continue
                obligation = PendingObligation(
                    task_id=contract.task_id,
                    contract_id=contract.contract_id,
                    created_round=contract.round_num,
                    last_seen_round=contract.round_num,
                    kind=kind,
                    requirement=requirement,
                )
                self._obligations[obligation.obligation_id] = obligation
                created.append(obligation)
        return created

    def settle(self, task_id: str, bundle: EvidenceBundle) -> list[PendingObligation]:
        settled: list[PendingObligation] = []
        for obligation in self.pending(task_id):
            fulfilled = (
                obligation.requirement in bundle.evidence_types
                if obligation.kind == "evidence"
                else obligation.requirement in bundle.observed_effects
            )
            if not fulfilled:
                continue
            evidence_ids = [record.evidence_id for record in bundle.records]
            updated = obligation.model_copy(
                update={
                    "status": ObligationStatus.FULFILLED,
                    "evidence_ids": evidence_ids,
                    "resolved_at": datetime.now(timezone.utc),
                }
            )
            self._obligations[updated.obligation_id] = updated
            settled.append(updated)
        return settled

    def carry_forward(self, task_id: str, round_num: int) -> list[PendingObligation]:
        carried: list[PendingObligation] = []
        for obligation in self.pending(task_id):
            updated = obligation.model_copy(update={"last_seen_round": round_num})
            self._obligations[updated.obligation_id] = updated
            carried.append(updated)
        return carried

    def fail(self, obligation_id: str) -> PendingObligation:
        obligation = self._obligations[obligation_id]
        updated = obligation.model_copy(
            update={
                "status": ObligationStatus.FAILED,
                "resolved_at": datetime.now(timezone.utc),
            }
        )
        self._obligations[obligation_id] = updated
        return updated

    def pending(self, task_id: str) -> list[PendingObligation]:
        return [
            obligation
            for obligation in self._obligations.values()
            if obligation.task_id == task_id and obligation.status == ObligationStatus.PENDING
        ]

    def _find_pending(
        self,
        task_id: str,
        kind: str,
        requirement: str,
    ) -> PendingObligation | None:
        return next(
            (
                obligation
                for obligation in self.pending(task_id)
                if obligation.kind == kind and obligation.requirement == requirement
            ),
            None,
        )
