"""Verify invariants that must survive contract version changes."""

from __future__ import annotations

from pydantic import BaseModel, Field

from recap.contracts.models import RuntimeContract
from recap.contracts.task import TaskContract
from recap.obligations.manager import PendingObligation


class CrossRoundViolation(BaseModel):
    rule_id: str
    description: str
    previous: list[str] = Field(default_factory=list)
    current: list[str] = Field(default_factory=list)


class CrossRoundResult(BaseModel):
    passed: bool
    violations: list[CrossRoundViolation] = Field(default_factory=list)


class CrossRoundVerifier:
    def verify(
        self,
        task: TaskContract,
        previous: RuntimeContract | None,
        current: RuntimeContract,
        pending_obligations: list[PendingObligation] | None = None,
        carried_obligation_ids: list[str] | None = None,
    ) -> CrossRoundResult:
        violations: list[CrossRoundViolation] = []
        if current.task_id != task.task_id:
            violations.append(
                CrossRoundViolation(
                    rule_id="task_identity",
                    description="round contract changed task identity",
                    previous=[task.task_id],
                    current=[current.task_id],
                )
            )
        if set(current.allowed_tools) - set(task.capability_names):
            violations.append(
                CrossRoundViolation(
                    rule_id="tool_scope",
                    description="round contract expands the task tool scope",
                    previous=task.capability_names,
                    current=current.allowed_tools,
                )
            )
        if set(current.granted_permissions) - set(task.granted_permissions):
            violations.append(
                CrossRoundViolation(
                    rule_id="no_authority_expansion",
                    description="round contract expands task permissions",
                    previous=task.granted_permissions,
                    current=current.granted_permissions,
                )
            )
        if set(task.normative_baseline) - set(current.normative_baseline):
            violations.append(
                CrossRoundViolation(
                    rule_id="normative_baseline",
                    description="round contract removes an always-on baseline rule",
                    previous=task.normative_baseline,
                    current=current.normative_baseline,
                )
            )
        if previous is not None and current.round_num <= previous.round_num:
            violations.append(
                CrossRoundViolation(
                    rule_id="round_monotonicity",
                    description="round number did not increase",
                    previous=[str(previous.round_num)],
                    current=[str(current.round_num)],
                )
            )
        expected_ids = {
            obligation.obligation_id
            for obligation in pending_obligations or []
            if obligation.task_id == task.task_id
        }
        carried_ids = set(carried_obligation_ids or [])
        if expected_ids - carried_ids:
            violations.append(
                CrossRoundViolation(
                    rule_id="preserve_pending_obligations",
                    description="a pending obligation was omitted from the next round",
                    previous=sorted(expected_ids),
                    current=sorted(carried_ids),
                )
            )
        return CrossRoundResult(passed=not violations, violations=violations)
