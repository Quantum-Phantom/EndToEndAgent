"""Declared capabilities for trusted tools."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field, model_validator

from recap.schemas import DataSource, TrustLevel


class AuthorizationRequirement(BaseModel):
    """Bind concrete action arguments to claims in a trusted authorization fact."""

    fact_type: str = Field(min_length=1)
    argument_claim_bindings: dict[str, str] = Field(min_length=1)


class ToolCapability(BaseModel):
    """Machine-checkable boundary declared by a tool implementation."""

    name: str = Field(min_length=1)
    argument_constraints: dict[str, Any] = Field(default_factory=dict)
    required_permissions: list[str] = Field(default_factory=list)
    allowed_effects: list[str] = Field(default_factory=list)
    forbidden_effects: list[str] = Field(default_factory=list)
    required_effects: list[str] = Field(default_factory=list)
    evidence_types: list[str] = Field(default_factory=list)
    observable_state: list[str] = Field(default_factory=list)
    authorization_requirements: list[AuthorizationRequirement] = Field(default_factory=list)
    data_source: DataSource = DataSource.TOOL
    trust_level: TrustLevel = TrustLevel.MEDIUM
    risk_level: str = Field(default="medium", pattern=r"^(low|medium|high)$")

    @model_validator(mode="after")
    def validate_effect_boundaries(self) -> "ToolCapability":
        overlap = set(self.allowed_effects) & set(self.forbidden_effects)
        if overlap:
            raise ValueError(f"effects cannot be both allowed and forbidden: {sorted(overlap)}")
        unavailable = set(self.required_effects) - set(self.allowed_effects)
        if self.allowed_effects and unavailable:
            raise ValueError(f"required effects are not declared as allowed: {sorted(unavailable)}")
        return self
