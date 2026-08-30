"""Banking scenario child process: serves tool calls via multiprocessing.Pipe.

Injection vectors are read from environment variables so the test harness can
configure them before spawning the child process.

Environment variables:
    RECAP_INJECTION_BILL_TEXT          – bill file injection content
    RECAP_INJECTION_TRANSACTION        – incoming transaction subject
    RECAP_INJECTION_LANDLORD           – landlord notice extra text
    RECAP_INJECTION_ADDRESS            – address change extra text
"""

from __future__ import annotations

import os
from multiprocessing.connection import Connection

from scenarios.banking.tools import (
    TOOLS_BY_NAME,
    reset_database,
)


def run(conn: Connection) -> None:
    """Entry point for the banking scenario child process.

    Reads injection vectors from environment variables, seeds the database,
    then loops reading ``(tool_name, args)`` from *conn*, executing the tool,
    and sending ``(status, content)`` back.  A ``None`` sentinel shuts down
    the loop.
    """

    def _env_or_none(key: str) -> str | None:
        """Return the env var value, or *None* if unset/empty."""
        val = os.environ.get(key, "")
        return val if val else None

    reset_database(
        injection_bill_text=_env_or_none("RECAP_INJECTION_BILL_TEXT"),
        injection_incoming_transaction=_env_or_none("RECAP_INJECTION_TRANSACTION"),
        injection_landlord_notice=_env_or_none("RECAP_INJECTION_LANDLORD"),
        injection_address_change=_env_or_none("RECAP_INJECTION_ADDRESS"),
    )

    from recap.tools import DeterministicToolError

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
