"""LangGraph state integrating contracts, actions and Ledger events."""

from __future__ import annotations

from operator import add
from typing import Annotated, Any, Literal, NotRequired

from langgraph.graph.message import MessagesState

from recap.contracts import RuntimeContract
from recap.ledger.models import LedgerEvent
from recap.schemas import (
    ActionEvent,
    IntentCertificate,
    ObservationEvent,
    TaskEntry,
    TransitionResult,
)

RouteName = Literal[
    "act",
    "observe",
    "observe_think",
    "replan",
    "human_approval",
    "end",
]


class ReCAPState(MessagesState):
    task_id: NotRequired[str]
    thread_id: NotRequired[str]
    round_num: NotRequired[int]
    task_entry: NotRequired[TaskEntry]
    current_intent: NotRequired[IntentCertificate | None]
    current_contract: NotRequired[RuntimeContract | None]
    current_action: NotRequired[ActionEvent | None]
    current_observation: NotRequired[ObservationEvent | None]
    candidate_tool_call: NotRequired[dict[str, Any] | None]
    approved_action_digest: NotRequired[str | None]
    raw_tool_result: NotRequired[Any]
    purified_context: NotRequired[Any]
    ledger_events: NotRequired[Annotated[list[LedgerEvent], add]]
    ledger_head_hash: NotRequired[str | None]
    check_results: NotRequired[Annotated[list[TransitionResult], add]]
    pending_obligations: NotRequired[list[str]]
    next_route: NotRequired[RouteName]
    retry_count: NotRequired[int]
    max_retries: NotRequired[int]
    awaiting_approval: NotRequired[bool]
    final_answer_allowed: NotRequired[bool]