"""Runtime contract aggregate for ReCAP."""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from enum import Enum
from typing import Any

from pydantic import BaseModel, Field, model_validator

from recap.schemas import IntentCertificate


class ContractStatus(str, Enum):
    DRAFT = "draft"
    ACTIVE = "active"
    EXECUTING = "executing"
    EVIDENCE_PENDING = "evidence_pending"
    FULFILLED = "fulfilled"
    VIOLATED = "violated"
    BLOCKED = "blocked"
    FAILED = "failed"
    EXPIRED = "expired"


_ALLOWED_TRANSITIONS: dict[ContractStatus, set[ContractStatus]] = {
    ContractStatus.DRAFT: {
        ContractStatus.ACTIVE,
        ContractStatus.VIOLATED,
        ContractStatus.BLOCKED,
        ContractStatus.EXPIRED,
    },
    ContractStatus.ACTIVE: {
        ContractStatus.EXECUTING,
        ContractStatus.VIOLATED,
        ContractStatus.BLOCKED,
        ContractStatus.EXPIRED,
    },
    ContractStatus.EXECUTING: {
        ContractStatus.EVIDENCE_PENDING,
        ContractStatus.FULFILLED,
        ContractStatus.VIOLATED,
        ContractStatus.BLOCKED,
        ContractStatus.FAILED,
    },
    ContractStatus.EVIDENCE_PENDING: {
        ContractStatus.FULFILLED,
        ContractStatus.VIOLATED,
        ContractStatus.BLOCKED,
        ContractStatus.FAILED,
        ContractStatus.EXPIRED,
    },
    ContractStatus.FULFILLED: set(),
    ContractStatus.VIOLATED: set(),
    ContractStatus.BLOCKED: set(),
    ContractStatus.FAILED: set(),
    ContractStatus.EXPIRED: set(),
}


class RuntimeContract(BaseModel):
    contract_id: str = Field(
        default_factory=lambda: f"contract-{uuid.uuid4().hex[:12]}"
    )
    task_id: str = Field(..., min_length=1)
    round_num: int = Field(..., ge=0)
    certificate: IntentCertificate
    allowed_tools: list[str] = Field(default_factory=list)
    argument_constraints: dict[str, Any] = Field(default_factory=dict)
    granted_permissions: list[str] = Field(default_factory=list)
    expected_effects: list[str] = Field(default_factory=list)
    allowed_effects: list[str] = Field(default_factory=list)
    forbidden_effects: list[str] = Field(default_factory=list)
    required_effects: list[str] = Field(default_factory=list)
    required_evidence: list[str] = Field(default_factory=list)
    authority_refs: list[str] = Field(default_factory=list)
    policy_refs: list[str] = Field(default_factory=list)
    normative_baseline: list[str] = Field(
        default_factory=lambda: [
            "fail_closed",
            "no_authority_expansion",
            "no_unproven_success",
            "preserve_pending_obligations",
        ]
    )
    status: ContractStatus = ContractStatus.DRAFT
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    updated_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    expires_at: datetime | None = None

    @model_validator(mode="after")
    def populate_from_certificate(self) -> "RuntimeContract":
        if not self.allowed_tools:
            self.allowed_tools = [self.certificate.proposed_operation]
        if not self.argument_constraints:
            self.argument_constraints = dict(self.certificate.argument_constraints)
        if not self.expected_effects:
            self.expected_effects = [self.certificate.expected_effect]
        if not self.allowed_effects:
            self.allowed_effects = list(self.certificate.allowed_effects)
        if not self.forbidden_effects:
            self.forbidden_effects = list(self.certificate.forbidden_effects)
        if not self.required_effects:
            self.required_effects = list(self.certificate.required_effects)
        if not self.required_evidence:
            self.required_evidence = list(self.certificate.required_evidence)
        if not self.authority_refs:
            self.authority_refs = [self.certificate.authority_basis]
        return self

    def transition_to(self, target: ContractStatus) -> "RuntimeContract":
        if target not in _ALLOWED_TRANSITIONS[self.status]:
            raise ValueError(
                f"Illegal contract transition: {self.status.value} -> {target.value}"
            )
        return self.model_copy(
            update={"status": target, "updated_at": datetime.now(timezone.utc)}
        )

    def missing_evidence(self, collected: list[str]) -> set[str]:
        return set(self.required_evidence) - set(collected)

    def tool_is_allowed(self, tool_name: str) -> bool:
        return tool_name in self.allowed_tools
