"""Canonical append-only Ledger event definitions."""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from enum import Enum
from typing import Any

import orjson
from blake3 import blake3
from pydantic import BaseModel, Field


class LedgerEventType(str, Enum):
    TASK_CREATED = "task_created"
    CONTRACT_CREATED = "contract_created"
    CONTRACT_ACTIVATED = "contract_activated"
    CONTRACT_EXECUTING = "contract_executing"
    ACTION_PROPOSED = "action_proposed"
    ACTION_APPROVED = "action_approved"
    ACTION_EXECUTION_STARTED = "action_execution_started"
    ACTION_EXECUTED = "action_executed"
    ACTION_FAILED = "action_failed"
    ACTION_BLOCKED = "action_blocked"
    OBSERVATION_RECORDED = "observation_recorded"
    OBLIGATION_CREATED = "obligation_created"
    OBLIGATION_FULFILLED = "obligation_fulfilled"
    VIOLATION_DETECTED = "violation_detected"
    OBSERVATION_PURIFIED = "observation_purified"
    HUMAN_APPROVAL_REQUESTED = "human_approval_requested"
    HUMAN_DECISION_RECORDED = "human_decision_recorded"
    CONTRACT_EVIDENCE_PENDING = "contract_evidence_pending"
    CONTRACT_FULFILLED = "contract_fulfilled"
    CONTRACT_VIOLATED = "contract_violated"
    CONTRACT_BLOCKED = "contract_blocked"
    CONTRACT_FAILED = "contract_failed"


class LedgerEvent(BaseModel):
    event_id: str = Field(default_factory=lambda: f"evt-{uuid.uuid4().hex}")
    event_type: LedgerEventType
    task_id: str = Field(..., min_length=1)
    thread_id: str = Field(..., min_length=1)
    round_num: int = Field(..., ge=0)
    actor: str = Field(..., min_length=1)
    payload: dict[str, Any] = Field(default_factory=dict)
    contract_id: str | None = None
    previous_hash: str | None = None
    event_hash: str
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))

    @classmethod
    def create(
        cls,
        *,
        event_type: LedgerEventType,
        task_id: str,
        thread_id: str,
        round_num: int,
        actor: str,
        payload: dict[str, Any],
        contract_id: str | None = None,
        previous_hash: str | None = None,
    ) -> "LedgerEvent":
        values = {
            "event_id": f"evt-{uuid.uuid4().hex}",
            "event_type": event_type,
            "task_id": task_id,
            "thread_id": thread_id,
            "round_num": round_num,
            "actor": actor,
            "payload": payload,
            "contract_id": contract_id,
            "previous_hash": previous_hash,
            "created_at": datetime.now(timezone.utc),
        }
        return cls(
            **values,
            event_hash=blake3(_canonical_bytes(values)).hexdigest(),
        )

    def verify_hash(self) -> bool:
        values = self.model_dump(exclude={"event_hash"}, mode="python")
        return blake3(_canonical_bytes(values)).hexdigest() == self.event_hash


def _canonical_bytes(value: Any) -> bytes:
    return orjson.dumps(
        value,
        option=orjson.OPT_SORT_KEYS | orjson.OPT_UTC_Z,
        default=lambda obj: obj.value if isinstance(obj, Enum) else str(obj),
    )
