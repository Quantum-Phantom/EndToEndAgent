"""Explicit allow-listed tool registry."""

from __future__ import annotations

from typing import Any, Protocol


class AsyncTool(Protocol):
    name: str

    async def ainvoke(self, args: dict[str, Any]) -> Any: ...


class ToolRegistry:
    def __init__(self) -> None:
        self._tools: dict[str, AsyncTool] = {}

    def register(self, tool: AsyncTool) -> None:
        if not tool.name:
            raise ValueError("Tool name must not be empty")
        if tool.name in self._tools:
            raise ValueError(f"Tool already registered: {tool.name}")
        self._tools[tool.name] = tool

    def contains(self, name: str) -> bool:
        return name in self._tools

    def get(self, name: str) -> AsyncTool:
        try:
            return self._tools[name]
        except KeyError as exc:
            raise LookupError(f"Unknown tool: {name}") from exc