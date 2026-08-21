"""Normalize trusted tool output into contract evidence."""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Any

from pydantic import BaseModel, Field

from recap.schemas import ActionEvent, ObservationEvent
from recap.tools.wrapper import TrustedToolResult


class EvidenceRecord(BaseModel):
    evidence_id: str = Field(default_factory=lambda: f"evidence-{uuid.uuid4().hex[:12]}")
    evidence_type: str
    call_id: str
    source: str
    value: Any = None
    collected_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))


class EvidenceBundle(BaseModel):
    call_id: str
    tool_name: str
    records: list[EvidenceRecord] = Field(default_factory=list)
    observed_effects: list[str] = Field(default_factory=list)
    valid_binding: bool = True
    binding_errors: list[str] = Field(default_factory=list)

    @property
    def evidence_types(self) -> set[str]:
        return {record.evidence_type for record in self.records}


class EvidenceAdapter:
    def adapt(
        self,
        result: TrustedToolResult,
        action: ActionEvent | None = None,
        observation: ObservationEvent | None = None,
    ) -> EvidenceBundle:
        errors: list[str] = []
        if action is not None:
            if action.call_id != result.call_id:
                errors.append("action call_id does not match trusted result")
            if action.tool_name != result.tool_name:
                errors.append("action tool name does not match trusted result")
            if action.actual_params != result.args:
                errors.append("action arguments do not match trusted result")
        if observation is not None and observation.call_id != result.call_id:
            errors.append("observation call_id does not match trusted result")

        records = [
            EvidenceRecord(
                evidence_type="tool_receipt",
                call_id=result.call_id,
                source=result.tool_name,
                value={"success": result.success, "content": result.content},
            ),
            EvidenceRecord(
                evidence_type="argument_binding",
                call_id=result.call_id,
                source="trusted_tool_wrapper",
                value=result.args,
            ),
        ]
        if result.state_diff is not None:
            records.append(
                EvidenceRecord(
                    evidence_type="state_diff",
                    call_id=result.call_id,
                    source="trusted_tool_wrapper",
                    value=result.state_diff,
                )
            )
        if observation is not None:
            records.extend(
                EvidenceRecord(
                    evidence_type=evidence_type,
                    call_id=result.call_id,
                    source=observation.source_label or observation.data_source.value,
                    value=observation.return_content,
                )
                for evidence_type in observation.evidence_collected
            )

        effects = list(
            dict.fromkeys(
                [
                    *result.observed_effects,
                    *(observation.observed_effects if observation is not None else []),
                ]
            )
        )
        return EvidenceBundle(
            call_id=result.call_id,
            tool_name=result.tool_name,
            records=records,
            observed_effects=effects,
            valid_binding=not errors,
            binding_errors=errors,
        )
