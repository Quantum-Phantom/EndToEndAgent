from __future__ import annotations

import asyncio
from typing import Any

from dotenv import load_dotenv
from langchain_core.messages import HumanMessage

from recap.agent.runtime import build_real_runtime
from recap.schemas import TaskEntry


def print_ledger_event(entry: Any) -> None:
    event_type = getattr(
        entry,
        "event_type",
        "unknown",
    )
    event_hash = getattr(
        entry,
        "event_hash",
        None,
    )
    payload = getattr(
        entry,
        "payload",
        {},
    )

    if hasattr(event_type, "value"):
        event_type = event_type.value

    print(
        f" - event_type={event_type}, "
        f"event_hash={event_hash}"
    )

    if event_type == "violation_detected":
        print("   violation_payload:")

        if hasattr(payload, "model_dump"):
            payload = payload.model_dump(
                mode="json"
            )

        for key, value in payload.items():
            print(f"     {key}: {value}")


async def main() -> None:
    load_dotenv()

    graph, ledger, registry = build_real_runtime()

    result = await graph.ainvoke(
        {
    "messages": [
        HumanMessage(
            content="Add 3 and 4."
        )
    ],
    "task_id": "real-llm-demo-001",
    "thread_id": "thread-001",
    "round_num": 0,
    "max_rounds": 8,
    "task_entry": TaskEntry(
        task_id="real-llm-demo-001",
        description="Complete the approved arithmetic task",
        policies=["arithmetic-policy"],
        tools_available=[
            "add",
            "multiply",
            "divide",
            "text_stats",
            "find_text",
            "replace_text",
            "parse_json",
            "select_fields",
            "filter_records",
        ],
        initial_permissions=[
            "arithmetic:execute",
            "text:process",
            "data:process",
        ],
    ),
    "contract_history": [],
    "round_summaries": [],
    "ledger_events": [],
    "check_results": [],
    "task_completed": False,
}
    )

    messages = result.get("messages", [])
    answer = (
        messages[-1].content
        if messages
        else None
    )

    print("answer:", answer)
    print(
        "intent:",
        result.get("current_intent"),
    )
    print(
        "contract:",
        result.get("current_contract"),
    )
    print(
        "next_route:",
        result.get("next_route"),
    )

    entries = result.get(
        "ledger_events",
        [],
    )

    print("ledger:")

    if not entries:
        print(
            " - 当前 Graph 状态中没有 "
            "ledger_events"
        )
        return

    for entry in entries:
        print_ledger_event(entry)


if __name__ == "__main__":
    asyncio.run(main())
