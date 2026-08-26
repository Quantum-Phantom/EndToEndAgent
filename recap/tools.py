"""ReCAP generic tool executor interface and multiprocessing bridge.

Scenarios provide their own tool implementations; the core graph calls tools
through the ``ToolExecutor`` protocol so that the scenario environment can be
replaced without modifying any core code.
"""

from __future__ import annotations

from multiprocessing import Process
from multiprocessing.connection import Connection
from typing import Any, Protocol


class DeterministicToolError(Exception):
    """Deterministic check failure raised by a scenario tool."""


# ---------------------------------------------------------------------------
# ToolExecutor protocol
# ---------------------------------------------------------------------------


class ToolExecutor(Protocol):
    """Protocol for scenario tool execution."""

    def invoke(self, tool_name: str, args: dict[str, Any]) -> str:
        """Execute *tool_name* with *args* and return the result string.

        Raises ``DeterministicToolError`` when the scenario tool signals a
        deterministic boundary violation.
        """
        ...


# ---------------------------------------------------------------------------
# Multiprocessing bridge (Pipe-based)
# ---------------------------------------------------------------------------


class MultiprocessingToolExecutor:
    """Bridges tool calls to a child process via ``multiprocessing.Pipe``.

    The child process must read ``(tool_name, args)`` tuples from its
    ``Connection`` end and reply with ``(status, content)`` tuples where
    *status* is one of ``"ok"``, ``"blocked"``, ``"error"``, or ``"unknown"``.
    Send ``None`` as a sentinel to shut down the child.
    """

    def __init__(self, parent_conn: Connection, child_process: Process) -> None:
        self._conn = parent_conn
        self._process = child_process

    def invoke(self, tool_name: str, args: dict[str, Any]) -> str:
        self._conn.send((tool_name, args))
        status, content = self._conn.recv()
        if status == "blocked":
            raise DeterministicToolError(content)
        if status == "error":
            return content
        if status == "unknown":
            return content
        return content

    def shutdown(self) -> None:
        """Send shutdown sentinel and join the child process."""
        try:
            self._conn.send(None)
        except (BrokenPipeError, OSError):
            pass
        self._process.join(timeout=5)
        if self._process.is_alive():
            self._process.terminate()


# ---------------------------------------------------------------------------
# Direct (in-process) executor — useful for testing without isolation
# ---------------------------------------------------------------------------


class DirectToolExecutor:
    """Runs tools directly in the current process (no multiprocessing)."""

    def __init__(self, tools_by_name: dict[str, Any]) -> None:
        self._tools = tools_by_name

    def invoke(self, tool_name: str, args: dict[str, Any]) -> str:
        fn = self._tools.get(tool_name)
        if fn is None:
            return f"error: unknown tool '{tool_name}'"
        try:
            return fn.invoke(args)
        except DeterministicToolError:
            raise
        except Exception as e:  # noqa: BLE001
            return f"error: {e}"
