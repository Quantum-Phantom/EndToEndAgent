"""Generic policy constraint evaluation backed by Z3."""

from __future__ import annotations

from typing import Any

import z3
from pydantic import BaseModel, Field

from recap.contracts.policy import ConstraintOperator, PolicyRule


class ConstraintViolation(BaseModel):
    rule_id: str
    field: str
    expected: Any = None
    actual: Any = None
    reason: str


class ConstraintResult(BaseModel):
    passed: bool
    violations: list[ConstraintViolation] = Field(default_factory=list)
    witness_rule_ids: list[str] = Field(default_factory=list)


class Z3ConstraintVerifier:
    """Evaluate concrete execution facts and expose a minimal Z3 unsat witness."""

    def verify(self, rules: list[PolicyRule], facts: dict[str, Any]) -> ConstraintResult:
        solver = z3.Solver()
        solver.set(unsat_core=True)
        violations: list[ConstraintViolation] = []

        for index, rule in enumerate(rules):
            found, actual = self._resolve(facts, rule.field)
            passed, reason = self._evaluate(found, actual, rule)
            label = z3.Bool(f"rule_{index}")
            solver.assert_and_track(z3.BoolVal(passed), label)
            if not passed:
                violations.append(
                    ConstraintViolation(
                        rule_id=rule.rule_id,
                        field=rule.field,
                        expected=rule.expected,
                        actual=actual if found else None,
                        reason=reason,
                    )
                )

        if solver.check() == z3.sat:
            return ConstraintResult(passed=True)
        core_indexes = {int(str(item).removeprefix("rule_")) for item in solver.unsat_core()}
        witness = [rule.rule_id for index, rule in enumerate(rules) if index in core_indexes]
        return ConstraintResult(
            passed=False,
            violations=violations,
            witness_rule_ids=witness,
        )

    @staticmethod
    def _resolve(facts: dict[str, Any], path: str) -> tuple[bool, Any]:
        value: Any = facts
        for part in path.split("."):
            if not isinstance(value, dict) or part not in value:
                return False, None
            value = value[part]
        return True, value

    @staticmethod
    def _evaluate(found: bool, actual: Any, rule: PolicyRule) -> tuple[bool, str]:
        if rule.operator == ConstraintOperator.REQUIRED:
            return found and actual is not None, "required field is missing"
        if not found:
            return False, "field is missing"
        try:
            if rule.operator == ConstraintOperator.EQ:
                return actual == rule.expected, "values are not equal"
            if rule.operator == ConstraintOperator.IN:
                return actual in rule.expected, "value is outside the allowed set"
            if rule.operator == ConstraintOperator.NOT_IN:
                return actual not in rule.expected, "value is in the forbidden set"
            if rule.operator == ConstraintOperator.GTE:
                return actual >= rule.expected, "value is below the lower bound"
            if rule.operator == ConstraintOperator.LTE:
                return actual <= rule.expected, "value exceeds the upper bound"
        except (TypeError, ValueError):
            return False, "value is not comparable with the constraint"
        return False, "unsupported constraint operator"
