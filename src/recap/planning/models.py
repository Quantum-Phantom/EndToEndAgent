from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field, model_validator


class LLMToolPlan(BaseModel):
    """LLM 的非授权规划输出；真正的授权与约束由运行时生成。"""

    subgoal: str = Field(min_length=1)
    tool_name: str | None = None
    tool_args: dict[str, Any] = Field(default_factory=dict)
    expected_effect: str = Field(min_length=1)
    required_evidence: list[str] = Field(default_factory=list)
    final_answer: str | None = None
    plan_summary: str = Field(default="")

    @model_validator(mode="after")
    def validate_action_or_answer(self) -> "LLMToolPlan":
        has_tool = self.tool_name is not None
        has_answer = bool(self.final_answer)
        if has_tool == has_answer:
            raise ValueError("exactly one of tool_name or final_answer must be supplied")
        if not has_tool and self.tool_args:
            raise ValueError("tool_args must be empty when no tool is selected")
        return self