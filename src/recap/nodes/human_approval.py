"""Suspend for a trusted human decision, then replan within its limited grant."""

from __future__ import annotations

from typing import Any, Literal

from recap.agent.state import ReCAPState
from recap.approval import ApprovalRequest, HumanApprovalService
from recap.ledger import LedgerEventType, LedgerService

HumanApprovalRoute = Literal["replan", "end"]


def build_human_approval_node(
    ledger: LedgerService,
    approval_service: HumanApprovalService,
):
    async def human_approval_node(state: ReCAPState) -> dict[str, Any]:
        request = state.get("approval_request")
        if request is None:
            context = state.get("recovery_context") or {}
            requested = list(context.get("requested_permissions", []))
            if not requested:
                return {
                    "awaiting_approval": False,
                    "next_route": "end",
                    "task_completed": False,
                    "final_answer_allowed": False,
                }
            contract = state.get("current_contract")
            request = approval_service.request(
                ApprovalRequest(
                    task_id=state.get("task_id", contract.task_id if contract else "unknown"),
                    thread_id=state.get("thread_id", "default-thread"),
                    round_num=int(state.get("round_num", contract.round_num if contract else 0)),
                    contract_id=contract.contract_id if contract else None,
                    requested_permissions=requested,
                    reason=str(context.get("reason", "additional authority required")),
                )
            )
            event = await ledger.record(
                event_type=LedgerEventType.HUMAN_APPROVAL_REQUESTED,
                task_id=request.task_id,
                thread_id=request.thread_id,
                round_num=request.round_num,
                contract_id=request.contract_id,
                actor="human_approval_node",
                payload={
                    "request_id": request.request_id,
                    "requested_permissions": request.requested_permissions,
                    "reason": request.reason,
                },
            )
            return {
                "approval_request": request,
                "awaiting_approval": True,
                "ledger_events": [event],
                "ledger_head_hash": event.event_hash,
                "next_route": "end",
                "task_completed": False,
                "final_answer_allowed": False,
            }

        decision = approval_service.decision_for(request.request_id)
        if decision is None:
            return {
                "awaiting_approval": True,
                "next_route": "end",
                "task_completed": False,
                "final_answer_allowed": False,
            }
        event = await ledger.record(
            event_type=LedgerEventType.HUMAN_DECISION_RECORDED,
            task_id=request.task_id,
            thread_id=request.thread_id,
            round_num=request.round_num,
            contract_id=request.contract_id,
            actor="human_approval_node",
            payload={
                "request_id": request.request_id,
                "decision_id": decision.decision_id,
                "approved": decision.approved,
                "granted_permissions": decision.granted_permissions,
                "approver_id": decision.approver_id,
                "provenance": decision.provenance,
            },
        )
        if not decision.approved:
            return {
                "human_decision": decision,
                "awaiting_approval": False,
                "ledger_events": [event],
                "ledger_head_hash": event.event_hash,
                "next_route": "end",
                "task_completed": False,
                "final_answer_allowed": False,
            }

        task_contract = state.get("task_contract")
        if task_contract is None:
            raise ValueError("approved recovery requires TaskContract")
        permissions = list(
            dict.fromkeys(
                [*task_contract.granted_permissions, *decision.granted_permissions]
            )
        )
        authority_ref = f"human_approval:{decision.decision_id}"
        task_contract = task_contract.model_copy(
            update={
                "granted_permissions": permissions,
                "authority_refs": list(
                    dict.fromkeys([*task_contract.authority_refs, authority_ref])
                ),
            }
        )
        return {
            "task_contract": task_contract,
            "human_decision": decision,
            "awaiting_approval": False,
            "recovery_context": {
                "human_approval_granted": True,
                "granted_permissions": decision.granted_permissions,
                "authority_ref": authority_ref,
                "pending_obligations": list(state.get("pending_obligations", [])),
            },
            "ledger_events": [event],
            "ledger_head_hash": event.event_hash,
            "next_route": "replan",
            "task_completed": False,
            "final_answer_allowed": False,
        }

    return human_approval_node


def route_after_human_approval(state: ReCAPState) -> HumanApprovalRoute:
    return "replan" if state.get("next_route") == "replan" else "end"


__all__ = ["build_human_approval_node", "route_after_human_approval"]
