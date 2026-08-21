"""Deterministic recovery decisions for contract violations."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from pydantic import BaseModel, Field

from recap.schemas import RecoveryAction, ViolationEvidence, ViolationType

if TYPE_CHECKING:
    from recap.contracts.policy import PolicyRule


class RecoveryDecision(BaseModel):
    action: RecoveryAction
    reason: str
    repaired_arguments: dict[str, Any] | None = None
    witness_ids: list[str] = Field(default_factory=list)


class RecoveryManager:
    _ACTIONS = {
        ViolationType.INTENT_VIOLATION: RecoveryAction.REPLAN,
        ViolationType.ACTION_VIOLATION: RecoveryAction.PARAMETER_FIX,
        ViolationType.OBSERVATION_POLLUTION: RecoveryAction.PURIFY,
        ViolationType.EVIDENCE_INSUFFICIENT: RecoveryAction.KEEP_UNFINISHED,
        ViolationType.HIGH_RISK_UNKNOWN: RecoveryAction.HUMAN_ESCALATION,
    }

    def decide(
        self,
        violation_type: ViolationType,
        *,
        arguments: dict[str, Any] | None = None,
        rules: list[PolicyRule] | None = None,
        witness: list[ViolationEvidence] | None = None,
    ) -> RecoveryDecision:
        action = self._ACTIONS[violation_type]
        repaired: dict[str, Any] | None = None
        if action == RecoveryAction.PARAMETER_FIX:
            repaired = self._contract_arguments(arguments or {}, rules or [])
            if repaired == (arguments or {}):
                action = RecoveryAction.BLOCK
                repaired = None
        return RecoveryDecision(
            action=action,
            reason=f"deterministic recovery for {violation_type.value}",
            repaired_arguments=repaired,
            witness_ids=[item.violation_id for item in witness or []],
        )

    @staticmethod
    def _contract_arguments(
        arguments: dict[str, Any],
        rules: list[PolicyRule],
    ) -> dict[str, Any]:
        repaired = dict(arguments)
        for rule in rules:
            if not rule.field.startswith("arguments."):
                continue
            field = rule.field.removeprefix("arguments.")
            operator = rule.operator.value
            if operator == "eq":
                repaired[field] = rule.expected
            elif operator == "in":
                choices = list(rule.expected)
                if repaired.get(field) not in choices and choices:
                    repaired[field] = choices[0]
            elif operator == "gte" and field in repaired:
                try:
                    repaired[field] = max(repaired[field], rule.expected)
                except TypeError:
                    continue
            elif operator == "lte" and field in repaired:
                try:
                    repaired[field] = min(repaired[field], rule.expected)
                except TypeError:
                    continue
        return repaired
