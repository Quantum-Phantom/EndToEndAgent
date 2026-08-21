"""LangGraph state integrating multi-round contracts, actions and Ledger events."""

from __future__ import annotations

from operator import add
from typing import Annotated, Any, Literal, NotRequired

from langgraph.graph.message import MessagesState

from recap.contracts import (
    CompiledPolicy,
    RuntimeContract,
    TaskContract,
)
from recap.evidence import EvidenceBundle
from recap.ledger.models import LedgerEvent
from recap.schemas import (
    ActionEvent,
    IntentCertificate,
    ObservationEvent,
    TaskEntry,
    TransitionResult,
)
from recap.approval import ApprovalRequest, HumanDecision

RouteName = Literal[
    "think",
    "check",
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
    max_rounds: NotRequired[int]
    task_entry: NotRequired[TaskEntry]
    task_contract: NotRequired[TaskContract]
    compiled_policy: NotRequired[CompiledPolicy | None]
    current_intent: NotRequired[IntentCertificate | None]
    current_contract: NotRequired[RuntimeContract | None]
    previous_contract: NotRequired[RuntimeContract | None]
    contract_history: NotRequired[Annotated[list[RuntimeContract], add]]

    current_action: NotRequired[ActionEvent | None]
    current_observation: NotRequired[ObservationEvent | None]
    candidate_tool_call: NotRequired[dict[str, Any] | None]
    approved_action_digest: NotRequired[str | None]
    raw_tool_result: NotRequired[Any]
    evidence_bundle: NotRequired[EvidenceBundle]

    purified_context: NotRequired[Any]
    consumed_purified_context: NotRequired[bool]
    round_summaries: NotRequired[Annotated[list[dict[str, Any]], add]]

    ledger_events: NotRequired[Annotated[list[LedgerEvent], add]]
    ledger_head_hash: NotRequired[str | None]
    check_results: NotRequired[Annotated[list[TransitionResult], add]]
    pending_obligations: NotRequired[list[str]]

    next_route: NotRequired[RouteName]
    retry_count: NotRequired[int]
    max_retries: NotRequired[int]
    awaiting_approval: NotRequired[bool]
    approval_request: NotRequired[ApprovalRequest | None]
    human_decision: NotRequired[HumanDecision | None]
    recovery_context: NotRequired[dict[str, Any] | None]

    task_completed: NotRequired[bool]
    final_answer: NotRequired[str | None]
    final_answer_allowed: NotRequired[bool]
