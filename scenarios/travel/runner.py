"""Travel scenario child process: serves tool calls via multiprocessing.Pipe.

Injection vectors are read from environment variables so the test harness can
configure them before spawning the child process.

Environment variables:
    RECAP_INJECTION_TRAVEL_*  – injection vector values (13 total)
"""

from __future__ import annotations

import os
from multiprocessing.connection import Connection

from scenarios.travel.tools import (
    TOOLS_BY_NAME,
    reset_database,
)

# All injection vector environment variable names
_INJECTION_ENV_VARS = [
    "injection_hotels_0",
    "injection_hotels_1",
    "injection_hotels_2",
    "injection_hotels_3",
    "injection_hotels_4",
    "injection_hotels_5",
    "injection_restaurants_0",
    "injection_restaurants_1",
    "injection_restaurants_2",
    "injection_restaurants_3",
    "injection_cars_0",
    "injection_cars_1",
    "injection_cars_2",
]


def run(conn: Connection) -> None:
    """Entry point for the travel scenario child process.

    Reads injection vectors from environment variables, seeds the database,
    then loops reading ``(tool_name, args)`` from *conn*, executing the tool,
    and sending ``(status, content)`` back.  A ``None`` sentinel shuts down
    the loop.
    """

    def _env_or_none(env_key: str) -> str | None:
        """Return the env var value, or *None* if unset/empty."""
        val = os.environ.get(env_key, "")
        return val if val else None

    # Build injection kwargs from environment variables
    injection_kwargs: dict[str, str | None] = {}
    for var_name in _INJECTION_ENV_VARS:
        env_key = f"RECAP_INJECTION_TRAVEL_{var_name.upper()}"
        injection_kwargs[var_name] = _env_or_none(env_key)

    reset_database(**injection_kwargs)

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