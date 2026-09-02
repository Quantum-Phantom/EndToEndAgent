"""Slack scenario child process: serves tool calls via multiprocessing.Pipe.

Injection vectors are read from environment variables so the test harness can
configure them before spawning the child process.

Environment variables:
    RECAP_INJECTION_WEB_0               -- web content injection on informations.com
    RECAP_INJECTION_PHISHING_1          -- phishing site content
    RECAP_INJECTION_DORA_0              -- Dora website content
    RECAP_PROMPT_INJECTION_EVE_BLOG     -- Eve blog injection
    RECAP_PROMPT_INJECTION_CHANNEL      -- channel name suffix
    RECAP_PROMPT_INJECTION_RESTAURANT   -- restaurant website ad content
"""

from __future__ import annotations

import os
from multiprocessing.connection import Connection

from scenarios.slack.tools import (
    TOOLS_BY_NAME,
    reset_database,
)


def run(conn: Connection) -> None:
    """Entry point for the slack scenario child process."""

    def _env_or_none(key: str) -> str | None:
        val = os.environ.get(key, "")
        return val if val else None

    reset_database(
        injection_web_0=_env_or_none("RECAP_INJECTION_WEB_0"),
        injection_phishing_1=_env_or_none("RECAP_INJECTION_PHISHING_1"),
        injection_dora_0=_env_or_none("RECAP_INJECTION_DORA_0"),
        prompt_injection_eve_blog=_env_or_none("RECAP_PROMPT_INJECTION_EVE_BLOG"),
        prompt_injection_channel=_env_or_none("RECAP_PROMPT_INJECTION_CHANNEL"),
        prompt_injection_restaurant=_env_or_none("RECAP_PROMPT_INJECTION_RESTAURANT"),
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
