"""Minimal trusted Observation creation after a successful act_node.

This node records the tool return and binds it to ReCAP's call_id. Evidence
completeness and content purification remain the responsibility of the later
Act->Observe and Observe->Think verifier nodes.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Any

from recap.agent.state import ReCAPState
from recap.ledger import LedgerEventType, LedgerService
from recap.schemas import DataSource, ExecutionStatus, ObservationEvent, TrustLevel

ObserveNode = Callable[[ReCAPState], Awaitable[dict[str, Any]]]


def build_observe_node(ledger: LedgerService) -> ObserveNode:
    async def observe_node(state: ReCAPState) -> dict[str, Any]:
        contract = state.get("current_contract")
        action = state.get("current_action")
        result = state.get("raw_tool_result")
        if contract is None or action is None or result is None:
            raise ValueError("observe_node requires contract, action and tool result")
        if action.execution_status != ExecutionStatus.SUCCESS or not result.success:
            raise ValueError("observe_node accepts only successful tool execution")
        observation = ObservationEvent(
            call_id=action.call_id,
            return_content=result.content,
            state_diff=result.state_diff,
            evidence_collected=list(
                dict.fromkeys(
                    [
                        "tool_return",
                        "call_id_binding",
                        *result.evidence_collected,
                    ]
                )
            ),
            observed_effects=list(result.observed_effects),
            data_source=result.data_source,
            trust_level=result.trust_level,
            source_label=(
                f"external:{action.tool_name}"
                if result.data_source == DataSource.EXTERNAL
                else f"trusted_tool:{action.tool_name}"
            ),
            is_complete=False,
        )
        event = await ledger.record(
            event_type=LedgerEventType.OBSERVATION_RECORDED,
            task_id=contract.task_id,
            thread_id=state.get("thread_id", "default-thread"),
            round_num=contract.round_num,
            contract_id=contract.contract_id,
            actor="observe_node",
            payload=observation.model_dump(mode="json"),
        )
        return {
            "current_observation": observation,
            "ledger_events": [event],
            "ledger_head_hash": event.event_hash,
        }

    return observe_node
