from typing import Any

import pytest

from recap.tools import ToolRegistry


class FakeTool:
    def __init__(self, name: str) -> None:
        self.name = name

    async def ainvoke(self, args: dict[str, Any]) -> Any:
        return args


def test_register_and_get_tool() -> None:
    registry = ToolRegistry()
    tool = FakeTool("add")
    registry.register(tool)

    assert registry.contains("add") is True
    assert registry.get("add") is tool


def test_unknown_tool_is_rejected() -> None:
    registry = ToolRegistry()

    assert registry.contains("missing") is False
    with pytest.raises(LookupError, match="Unknown tool"):
        registry.get("missing")


def test_duplicate_tool_name_is_rejected() -> None:
    registry = ToolRegistry()
    registry.register(FakeTool("add"))

    with pytest.raises(ValueError, match="already registered"):
        registry.register(FakeTool("add"))


def test_empty_tool_name_is_rejected() -> None:
    registry = ToolRegistry()

    with pytest.raises(ValueError, match="must not be empty"):
        registry.register(FakeTool(""))