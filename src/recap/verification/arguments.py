"""Deterministic argument constraint checks shared by verifier nodes."""

from __future__ import annotations

from typing import Any


def constraint_mismatches(
    constraints: dict[str, Any],
    actual_args: dict[str, Any],
) -> list[dict[str, Any]]:
    mismatches: list[dict[str, Any]] = []
    for field, rule in constraints.items():
        if field not in actual_args:
            mismatches.append({"field": field, "reason": "missing"})
            continue
        if not isinstance(rule, dict):
            if actual_args[field] != rule:
                mismatches.append(
                    {
                        "field": field,
                        "reason": "not_equal",
                        "expected": rule,
                        "actual": actual_args[field],
                    }
                )
            continue
        reason = _check_rule(actual_args[field], rule)
        if reason is not None:
            mismatches.append(
                {"field": field, "actual": actual_args[field], "reason": reason}
            )
    return mismatches


def _check_rule(value: Any, rule: dict[str, Any]) -> str | None:
    supported = {"eq", "in", "gte", "lte"}
    unknown = set(rule) - supported
    if unknown:
        return f"unsupported_constraints:{sorted(unknown)}"
    if "eq" in rule and value != rule["eq"]:
        return f"expected_eq:{rule['eq']!r}"
    if "in" in rule and value not in rule["in"]:
        return f"expected_in:{rule['in']!r}"
    try:
        if "gte" in rule and value < rule["gte"]:
            return f"expected_gte:{rule['gte']!r}"
        if "lte" in rule and value > rule["lte"]:
            return f"expected_lte:{rule['lte']!r}"
    except TypeError:
        return "incomparable_value"
    return None