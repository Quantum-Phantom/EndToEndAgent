"""Policy rules and compiled contract policy."""

from __future__ import annotations

from enum import Enum
from typing import Any

from pydantic import BaseModel, Field


class ConstraintOperator(str, Enum):
    EQ = "eq"
    IN = "in"
    NOT_IN = "not_in"
    GTE = "gte"
    LTE = "lte"
    REQUIRED = "required"


class PolicyRule(BaseModel):
    rule_id: str = Field(min_length=1)
    field: str = Field(min_length=1)
    operator: ConstraintOperator
    expected: Any = None
    description: str = ""
    policy_ref: str = ""


class CompiledPolicy(BaseModel):
    rules: list[PolicyRule] = Field(default_factory=list)
    allowed_tools: list[str] = Field(default_factory=list)
    granted_permissions: list[str] = Field(default_factory=list)
    allowed_effects: list[str] = Field(default_factory=list)
    forbidden_effects: list[str] = Field(default_factory=list)
    required_effects: list[str] = Field(default_factory=list)
    required_evidence: list[str] = Field(default_factory=list)
    authority_refs: list[str] = Field(default_factory=list)
    policy_refs: list[str] = Field(default_factory=list)
    normative_baseline: list[str] = Field(default_factory=list)
