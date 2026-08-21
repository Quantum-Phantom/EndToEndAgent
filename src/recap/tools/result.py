"""Scenario-tool return protocol consumed by the trusted wrapper."""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Mapping

from pydantic import BaseModel, Field, model_validator


class ToolOutputStatus(str, Enum):
    SUCCESS = "success"
    DENIED = "denied"
    ERROR = "error"


class TrustedAuthorizationFact(BaseModel):
    """Authorization established by a trusted observer and bound to tool output."""

    fact_id: str = Field(default_factory=lambda: f"auth-{uuid.uuid4().hex[:12]}")
    fact_type: str = Field(min_length=1)
    issuer_tool: str = Field(min_length=1)
    claims: dict[str, Any] = Field(default_factory=dict)
    source: str = "effect_observer"
    issued_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))


class StructuredToolOutput(BaseModel):
    """A tool's business result, containing no self-reported state diff/effects.

    Trusted state changes and evidence belong to ``EffectObserver`` and are
    attached later by the wrapper's ``ToolResultEnvelope``.
    """

    schema_version: str = Field(default="recap.tool-output/v1", frozen=True)
    status: ToolOutputStatus
    content: Any = None
    error_code: str | None = None
    error_message: str | None = None

    @model_validator(mode="after")
    def validate_status(self) -> "StructuredToolOutput":
        if self.status == ToolOutputStatus.SUCCESS:
            if self.error_code is not None or self.error_message is not None:
                raise ValueError("successful tool output cannot contain an error")
        elif not self.error_code or not self.error_message:
            raise ValueError("denied/error tool output requires error_code and error_message")
        return self

    @classmethod
    def ok(cls, content: Any) -> "StructuredToolOutput":
        return cls(status=ToolOutputStatus.SUCCESS, content=content)

    @classmethod
    def denied(cls, code: str, message: str) -> "StructuredToolOutput":
        return cls(
            status=ToolOutputStatus.DENIED,
            error_code=code,
            error_message=message,
        )

    @classmethod
    def error(cls, code: str, message: str) -> "StructuredToolOutput":
        return cls(
            status=ToolOutputStatus.ERROR,
            error_code=code,
            error_message=message,
        )

    @classmethod
    def parse_if_structured(cls, value: Any) -> "StructuredToolOutput | None":
        if isinstance(value, cls):
            return value
        if isinstance(value, Mapping) and value.get("schema_version") == "recap.tool-output/v1":
            return cls.model_validate(value)
        return None
