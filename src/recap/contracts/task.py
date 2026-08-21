"""Task-level contract that owns all round contract versions."""

from __future__ import annotations

import uuid
from datetime import datetime, timezone

from pydantic import BaseModel, Field

from recap.contracts.models import RuntimeContract


class TaskContract(BaseModel):
    task_id: str = Field(default_factory=lambda: f"task-{uuid.uuid4().hex[:12]}")
    objective: str = Field(min_length=1)
    capability_names: list[str] = Field(default_factory=list)
    granted_permissions: list[str] = Field(default_factory=list)
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
    versions: list[RuntimeContract] = Field(default_factory=list)
    pending_obligation_ids: list[str] = Field(default_factory=list)
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))

    @property
    def current(self) -> RuntimeContract | None:
        return self.versions[-1] if self.versions else None

    def append_version(self, contract: RuntimeContract) -> "TaskContract":
        if contract.task_id != self.task_id:
            raise ValueError("round contract belongs to a different task")
        if self.current is not None and contract.round_num <= self.current.round_num:
            raise ValueError("round contract versions must increase monotonically")
        return self.model_copy(update={"versions": [*self.versions, contract]})

    def replace_current_version(self, contract: RuntimeContract) -> "TaskContract":
        if self.current is None:
            raise ValueError("cannot replace a missing round contract")
        if contract.task_id != self.task_id:
            raise ValueError("round contract belongs to a different task")
        if contract.contract_id != self.current.contract_id:
            raise ValueError("replacement must keep the current contract identity")
        return self.model_copy(update={"versions": [*self.versions[:-1], contract]})

    def with_pending_obligations(self, obligation_ids: list[str]) -> "TaskContract":
        unique_ids = list(dict.fromkeys(obligation_ids))
        return self.model_copy(update={"pending_obligation_ids": unique_ids})
