"""Workspace scenario child process: serves tool calls via multiprocessing.Pipe.

Injection vectors are read from environment variables so the test harness can
configure them before spawning the child process.

Environment variables:
    RECAP_INJECTION_WORKSPACE_*  – injection vector values (19 total)
"""

from __future__ import annotations

import os
from multiprocessing.connection import Connection

from scenarios.workspace.tools import (
    TOOLS_BY_NAME,
    reset_database,
)

# All injection vector environment variable names
_INJECTION_ENV_VARS = [
    "calendar_introductory_meeting_injection",
    "calendar_networking_event_injection_email",
    "calendar_sports_and_yoga_class_injection",
    "calendar_dinner_with_clients_injection",
    "calendar_fake_deadline_description",
    "calendar_project_kickoff_description",
    "calendar_moving_event_reason_injection",
    "email_facebook_injection",
    "email_hiking_injection",
    "email_events_injection",
    "email_events_follow_up_injection",
    "email_password_reset_injection",
    "drive_feedback_injection",
    "drive_recipe_injection",
    "drive_team_building_injection",
    "drive_keywords_stuffing_injection",
]


def run(conn: Connection) -> None:
    """Entry point for the workspace scenario child process.

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
        env_key = f"RECAP_INJECTION_WORKSPACE_{var_name.upper()}"
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
