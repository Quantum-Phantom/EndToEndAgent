"""Timeout-bounded trusted tool execution."""

from __future__ import annotations

import asyncio
import uuid
from datetime import datetime, timezone
from typing import Any

from pydantic import BaseModel, Field

from recap.schemas import DataSource, TrustLevel
from recap.tools.registry import ToolRegistry


class TrustedToolResult(BaseModel):
    call_id: str
    model_tool_call_id: str | None = None
    tool_name: str
    args: dict[str, Any] = Field(default_factory=dict)
    success: bool
    content: Any = None
    data_source: DataSource = DataSource.TOOL
    trust_level: TrustLevel = TrustLevel.MEDIUM
    evidence_collected: list[str] = Field(default_factory=list)
    observed_effects: list[str] = Field(default_factory=list)
    state_before: dict[str, Any] | None = None
    state_after: dict[str, Any] | None = None
    state_diff: dict[str, Any] | None = None
    error_type: str | None = None
    error_message: str | None = None
    started_at: datetime
    completed_at: datetime


async def execute_trusted_tool(
    *,
    registry: ToolRegistry,
    tool_name: str,
    args: dict[str, Any],
    model_tool_call_id: str | None,
    timeout_seconds: float,
) -> TrustedToolResult:
    call_id = f"call-{uuid.uuid4().hex[:12]}"
    started_at = datetime.now(timezone.utc)
    try:
        tool = registry.get(tool_name)
        metadata = getattr(tool, "metadata", None) or {}
        snapshot = metadata.get("recap_state_snapshot")
        state_before = snapshot() if callable(snapshot) else None
        content = await asyncio.wait_for(
            tool.ainvoke(args),
            timeout=timeout_seconds,
        )
        state_after = snapshot() if callable(snapshot) else None
        state_diff = _state_diff(
            state_before,
            state_after,
            content,
            metadata.get("recap_effect_receipt_fields", []),
        )
        try:
            capability = registry.get_capability(tool_name)
            declared_evidence = capability.evidence_types
            data_source = capability.data_source
            trust_level = capability.trust_level
        except LookupError:
            declared_evidence = []
            data_source = DataSource.TOOL
            trust_level = TrustLevel.MEDIUM
        has_trusted_state_change = state_diff is not None
        observed_effects = list(metadata.get("recap_observed_effects", []))
        effect_evidence = list(metadata.get("recap_effect_evidence", []))
        if callable(snapshot) and not has_trusted_state_change:
            observed_effects = []
            effect_evidence = []
        declared_evidence = [
            item
            for item in declared_evidence
            if item != "state_diff" or has_trusted_state_change
        ]
        return TrustedToolResult(
            call_id=call_id,
            model_tool_call_id=model_tool_call_id,
            tool_name=tool_name,
            args=args,
            success=True,
            content=content,
            data_source=data_source,
            trust_level=trust_level,
            evidence_collected=list(
                dict.fromkeys(
                    [
                        "tool_return",
                        "call_id_binding",
                        *declared_evidence,
                        *effect_evidence,
                        *(["state_diff"] if state_diff is not None else []),
                    ]
                )
            ),
            observed_effects=list(dict.fromkeys(["tool_return", *observed_effects])),
            state_before=state_before,
            state_after=state_after,
            state_diff=state_diff,
            started_at=started_at,
            completed_at=datetime.now(timezone.utc),
        )
    except TimeoutError as exc:
        return TrustedToolResult(
            call_id=call_id,
            model_tool_call_id=model_tool_call_id,
            tool_name=tool_name,
            args=args,
            success=False,
            error_type=type(exc).__name__,
            error_message=f"Tool timed out after {timeout_seconds} seconds",
            started_at=started_at,
            completed_at=datetime.now(timezone.utc),
        )
    except Exception as exc:
        return TrustedToolResult(
            call_id=call_id,
            model_tool_call_id=model_tool_call_id,
            tool_name=tool_name,
            args=args,
            success=False,
            error_type=type(exc).__name__,
            error_message=str(exc),
            started_at=started_at,
            completed_at=datetime.now(timezone.utc),
        )


def _state_diff(
    before: dict[str, Any] | None,
    after: dict[str, Any] | None,
    content: Any,
    receipt_fields: list[str],
) -> dict[str, Any] | None:
    if before is None or after is None or before == after:
        return None
    diff: dict[str, Any] = {"before": before, "after": after}
    if isinstance(content, dict) and receipt_fields:
        diff["effect_receipt"] = {
            key: content[key]
            for key in receipt_fields
            if key in content
        }
    return diff
