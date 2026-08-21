"""Trusted human-approval records used by recovery."""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from enum import Enum

from pydantic import BaseModel, Field


class ApprovalStatus(str, Enum):
    PENDING = "pending"
    APPROVED = "approved"
    DENIED = "denied"


class ApprovalRequest(BaseModel):
    request_id: str = Field(default_factory=lambda: f"approval-{uuid.uuid4().hex[:12]}")
    task_id: str
    thread_id: str
    round_num: int
    contract_id: str | None = None
    requested_permissions: list[str] = Field(default_factory=list)
    reason: str
    status: ApprovalStatus = ApprovalStatus.PENDING
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))


class HumanDecision(BaseModel):
    decision_id: str = Field(default_factory=lambda: f"decision-{uuid.uuid4().hex[:12]}")
    request_id: str
    approved: bool
    granted_permissions: list[str] = Field(default_factory=list)
    approver_id: str
    provenance: str
    decided_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
