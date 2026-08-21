from typing import Any

import pytest

from recap.contracts import ConstraintOperator, PolicyRule, ToolCapability
from recap.runtime import RuntimeScenario
from recap.tools import (
    StructuredToolOutput,
    ToolOutputStatus,
    ToolRegistry,
    execute_trusted_tool,
)


class Tool:
    name = "write_record"

    async def ainvoke(self, args: dict[str, Any]) -> Any:
        return {"id": args["id"]}


class Observer:
    def __init__(self) -> None:
        self.value = 0

    def snapshot(self) -> dict[str, Any]:
        self.value += 1
        return {"version": self.value}

    def effects(self, before, after, tool_return):
        return ["record_written"], ["record_receipt"], {
            "before": before,
            "after": after,
            "receipt": tool_return,
        }


def capability() -> ToolCapability:
    return ToolCapability(
        name="write_record",
        argument_constraints={"id": {"required": True}},
        required_permissions=["record:write"],
        allowed_effects=["record_written"],
        required_effects=["record_written"],
        evidence_types=["record_receipt"],
    )


def test_scenario_is_the_complete_task_boundary() -> None:
    rule = PolicyRule(
        rule_id="record-id",
        field="arguments.id",
        operator=ConstraintOperator.EQ,
        expected="safe",
        policy_ref="policy:records",
    )
    scenario = RuntimeScenario(
        tools=[Tool()],
        capabilities={"write_record": capability()},
        policy_rules=[rule],
        initial_permissions=["record:write"],
        observers={"write_record": Observer()},
    )

    entry = scenario.task_entry("write the authorized record", task_id="task-1")
    assert entry.tools_available == ["write_record"]
    assert entry.initial_permissions == ["record:write"]
    assert entry.policies == ["policy:records"]


def test_scenario_rejects_incomplete_capability_manifest() -> None:
    with pytest.raises(ValueError, match="exactly one capability"):
        RuntimeScenario(tools=[Tool()], capabilities={})


@pytest.mark.asyncio
async def test_tool_result_envelope_uses_declared_observer() -> None:
    registry = ToolRegistry()
    observer = Observer()
    registry.register(Tool(), capability(), observer)

    result = await execute_trusted_tool(
        registry=registry,
        tool_name="write_record",
        args={"id": "safe"},
        model_tool_call_id="model-1",
        timeout_seconds=1,
    )

    assert result.schema_version == "recap.tool-result/v1"
    assert result.observed_effects == ["tool_return", "record_written"]
    assert "record_receipt" in result.evidence_collected
    assert result.state_diff["before"] == {"version": 1}
    assert result.state_diff["after"] == {"version": 2}


class DeniedTool:
    name = "denied_tool"

    async def ainvoke(self, args: dict[str, Any]) -> Any:
        return StructuredToolOutput.denied("not_authorized", "operation is not authorized")


@pytest.mark.asyncio
async def test_structured_denial_is_normalized_as_failure() -> None:
    registry = ToolRegistry()
    registry.register(DeniedTool())

    result = await execute_trusted_tool(
        registry=registry,
        tool_name="denied_tool",
        args={},
        model_tool_call_id="model-denied",
        timeout_seconds=1,
    )

    assert result.success is False
    assert result.status == ToolOutputStatus.DENIED
    assert result.error_type == "ToolDenied"
    assert result.error_message == "not_authorized: operation is not authorized"
    assert result.content is None


@pytest.mark.asyncio
async def test_capability_evidence_is_an_obligation_not_collected_evidence() -> None:
    registry = ToolRegistry()
    declared_only = capability().model_copy(
        update={"evidence_types": ["tool_return", "call_id_binding", "phantom_receipt"]}
    )
    registry.register(Tool(), declared_only)

    result = await execute_trusted_tool(
        registry=registry,
        tool_name="write_record",
        args={"id": "safe"},
        model_tool_call_id="model-no-observer",
        timeout_seconds=1,
    )

    assert result.evidence_collected == ["tool_return", "call_id_binding"]
    assert "phantom_receipt" not in result.evidence_collected
