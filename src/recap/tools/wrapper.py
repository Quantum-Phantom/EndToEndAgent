"""Timeout-bounded trusted tool execution."""

from __future__ import annotations

import asyncio
import uuid
from datetime import datetime, timezone
from typing import Any

from pydantic import BaseModel, Field

from recap.tools.registry import ToolRegistry


class TrustedToolResult(BaseModel):
    call_id: str
    model_tool_call_id: str | None = None
    tool_name: str
    args: dict[str, Any] = Field(default_factory=dict)
    success: bool
    content: Any = None
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
        content = await asyncio.wait_for(
            tool.ainvoke(args),
            timeout=timeout_seconds,
        )
        return TrustedToolResult(
            call_id=call_id,
            model_tool_call_id=model_tool_call_id,
            tool_name=tool_name,
            args=args,
            success=True,
            content=content,
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