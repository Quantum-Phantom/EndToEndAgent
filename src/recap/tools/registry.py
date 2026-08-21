"""Explicit allow-listed tool registry."""

from __future__ import annotations

from typing import Any, Protocol

from recap.contracts.capabilities import ToolCapability


class AsyncTool(Protocol):
    name: str

    async def ainvoke(self, args: dict[str, Any]) -> Any: ...


class ToolRegistry:
    def __init__(self) -> None:
        self._tools: dict[str, AsyncTool] = {}
        self._capabilities: dict[str, ToolCapability] = {}

    def register(
        self,
        tool: AsyncTool,
        capability: ToolCapability | None = None,
    ) -> None:
        if not tool.name:
            raise ValueError("Tool name must not be empty")
        if tool.name in self._tools:
            raise ValueError(f"Tool already registered: {tool.name}")
        if capability is not None and capability.name != tool.name:
            raise ValueError("Tool and capability names must match")
        self._tools[tool.name] = tool
        if capability is not None:
            self._capabilities[tool.name] = capability

    def contains(self, name: str) -> bool:
        return name in self._tools

    def get(self, name: str) -> AsyncTool:
        try:
            return self._tools[name]
        except KeyError as exc:
            raise LookupError(f"Unknown tool: {name}") from exc

    def get_capability(self, name: str) -> ToolCapability:
        try:
            return self._capabilities[name]
        except KeyError as exc:
            raise LookupError(f"No capability declared for tool: {name}") from exc
