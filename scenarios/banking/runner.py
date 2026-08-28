"""Banking scenario child process: serves tool calls via multiprocessing.Pipe."""

from __future__ import annotations

from multiprocessing.connection import Connection

from scenarios.banking.tools import (
    DeterministicToolError,
    TOOLS_BY_NAME,
    reset_database,
)


def run(conn: Connection) -> None:
    """Entry point for the banking scenario child process.

    Seeds the database then loops reading (tool_name, args) from *conn*,
    executing the tool, and sending (status, content) back.
    A ``None`` sentinel shuts down the loop.
    """
    reset_database()

    while True:
        msg = conn.recv()
        if msg is None:
            break
        tool_name, args = msg
        fn = TOOLS_BY_NAME.get(tool_name)
        if fn is None:
            conn.send(("unknown", f"error: unknown tool '{tool_name}'"))
            continue
        try:
            result = fn.invoke(args)
            conn.send(("ok", str(result)))
        except DeterministicToolError as e:
            conn.send(("blocked", str(e)))
        except Exception as e:  # noqa: BLE001
            conn.send(("error", f"error: {e}"))
