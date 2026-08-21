"""In-process trust boundary for explicit human approval decisions."""

from __future__ import annotations

from recap.approval.models import ApprovalRequest, ApprovalStatus, HumanDecision


class HumanApprovalService:
    def __init__(self, trusted_approvers: set[str] | None = None) -> None:
        self._trusted_approvers = set(trusted_approvers or set())
        self._requests: dict[str, ApprovalRequest] = {}
        self._decisions: dict[str, HumanDecision] = {}

    def request(self, request: ApprovalRequest) -> ApprovalRequest:
        if request.request_id in self._requests:
            raise ValueError("approval request already exists")
        self._requests[request.request_id] = request
        return request

    def decide(
        self,
        request_id: str,
        *,
        approver_id: str,
        approved: bool,
        granted_permissions: list[str] | None = None,
    ) -> HumanDecision:
        if approver_id not in self._trusted_approvers:
            raise PermissionError("decision source is not a trusted approver")
        request = self._requests[request_id]
        if request.status != ApprovalStatus.PENDING or request_id in self._decisions:
            raise ValueError("approval request is no longer pending")
        granted = list(dict.fromkeys(granted_permissions or [])) if approved else []
        outside = set(granted) - set(request.requested_permissions)
        if outside:
            raise PermissionError(f"decision expands beyond requested scope: {sorted(outside)}")
        decision = HumanDecision(
            request_id=request_id,
            approved=approved,
            granted_permissions=granted,
            approver_id=approver_id,
            provenance=f"trusted_human:{approver_id}",
        )
        self._decisions[request_id] = decision
        self._requests[request_id] = request.model_copy(
            update={
                "status": ApprovalStatus.APPROVED if approved else ApprovalStatus.DENIED
            }
        )
        return decision

    def decision_for(self, request_id: str) -> HumanDecision | None:
        return self._decisions.get(request_id)
