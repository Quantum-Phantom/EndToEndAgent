"""The complete declaration a benchmark supplies to the ReCAP runtime."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field, model_validator

from recap.contracts import PolicyRule, ToolCapability
from recap.schemas import TaskEntry
from recap.tools.observer import EffectObserver


class RuntimeScenario(BaseModel):
    """Data-only benchmark adapter; the core runtime contains no scenario logic."""

    model_config = ConfigDict(arbitrary_types_allowed=True)

    tools: list[Any] = Field(min_length=1)
    capabilities: dict[str, ToolCapability]
    policy_rules: list[PolicyRule] = Field(default_factory=list)
    initial_permissions: list[str] = Field(default_factory=list)
    observers: dict[str, EffectObserver] = Field(default_factory=dict)

    @model_validator(mode="after")
    def validate_manifest(self) -> "RuntimeScenario":
        names = [getattr(tool, "name", None) for tool in self.tools]
        if any(not name for name in names) or len(names) != len(set(names)):
            raise ValueError("scenario tools must have unique, non-empty names")
        if set(names) != set(self.capabilities):
            raise ValueError("scenario must declare exactly one capability per tool")
        unknown_observers = set(self.observers) - set(names)
        if unknown_observers:
            raise ValueError(f"observers reference unknown tools: {sorted(unknown_observers)}")
        declared_permissions = {
            permission
            for capability in self.capabilities.values()
            for permission in capability.required_permissions
        }
        unknown_permissions = set(self.initial_permissions) - declared_permissions
        if unknown_permissions:
            raise ValueError(
                f"initial permissions are not used by any capability: {sorted(unknown_permissions)}"
            )
        return self

    def task_entry(self, description: str, *, task_id: str | None = None) -> TaskEntry:
        """Create the only supported initial task boundary for this scenario."""
        values: dict[str, Any] = {
            "description": description,
            "policies": list(dict.fromkeys(
                rule.policy_ref or rule.rule_id for rule in self.policy_rules
            )),
            "tools_available": [tool.name for tool in self.tools],
            "initial_permissions": list(self.initial_permissions),
        }
        if task_id is not None:
            values["task_id"] = task_id
        return TaskEntry(**values)
