import pytest

from recap.tools import ALL_TOOLS, ARITHMETIC_CAPABILITIES, TOOL_CAPABILITIES, ToolRegistry
from recap.tools.arithmetic import ARITHMETIC_TOOLS


def test_every_arithmetic_tool_has_a_capability() -> None:
    tool_names = {tool.name for tool in ARITHMETIC_TOOLS}

    assert set(ARITHMETIC_CAPABILITIES) == tool_names


def test_all_tools_can_be_registered_with_capabilities() -> None:
    registry = ToolRegistry()

    for tool in ALL_TOOLS:
        registry.register(tool, TOOL_CAPABILITIES[tool.name])

    for tool in ALL_TOOLS:
        capability = registry.get_capability(tool.name)
        assert capability.name == tool.name


def test_every_runtime_tool_has_exactly_one_capability() -> None:
    assert set(TOOL_CAPABILITIES) == {tool.name for tool in ALL_TOOLS}


def test_divide_capability_forbids_zero_divisor() -> None:
    capability = ARITHMETIC_CAPABILITIES["divide"]

    assert capability.argument_constraints["b"]["not_in"] == [0]


def test_registry_rejects_mismatched_capability_name() -> None:
    registry = ToolRegistry()
    tool = ARITHMETIC_TOOLS[0]
    wrong_capability = ARITHMETIC_CAPABILITIES["divide"]

    with pytest.raises(ValueError, match="names must match"):
        registry.register(tool, wrong_capability)
