"""Offline replay of the minimum pre-Act verification facts in a witness."""

from __future__ import annotations

import hashlib
from typing import Any

from pydantic import BaseModel

from recap.contracts.compiler import PolicyCompiler
from recap.schemas import RecoveryAction, ViolationEvidence
from recap.verification.constraints import Z3ConstraintVerifier

_SENSITIVE_PARTS = ("body", "content", "secret", "token", "password", "credential")


class ReplayResult(BaseModel):
    blocked: bool
    decision: RecoveryAction | None = None
    rule_ids: list[str] = []


def _digest(value: Any) -> str:
    return "sha256:" + hashlib.sha256(repr(value).encode("utf-8")).hexdigest()


def sanitize_witness_value(value: Any, path: str = "") -> Any:
    if isinstance(value, dict):
        declared_field = str(value.get("field", "")).lower()
        sensitive_rule = any(part in declared_field for part in _SENSITIVE_PARTS)
        return {
            key: (
                _digest(item)
                if sensitive_rule and key in {"expected", "actual"}
                else sanitize_witness_value(
                    item,
                    f"{path}.{key}" if path else key,
                )
            )
            for key, item in value.items()
        }
    if any(part in path.lower() for part in _SENSITIVE_PARTS):
        return _digest(value)
    if isinstance(value, list):
        return [sanitize_witness_value(item, path) for item in value]
    return value


def public_recovery_constraints(constraints: dict[str, Any]) -> dict[str, Any]:
    """Expose executable non-sensitive bounds; keep sensitive values out of prompts."""
    return {
        "constraints": {
            key: value
            for key, value in constraints.items()
            if not any(part in key.lower() for part in _SENSITIVE_PARTS)
        },
        "sensitive_fields": sorted(
            key
            for key in constraints
            if any(part in key.lower() for part in _SENSITIVE_PARTS)
        ),
    }


def build_pre_act_replay_input(
    *,
    allowed_tools: list[str],
    argument_constraints: dict[str, Any],
    granted_permissions: list[str],
    required_permissions: list[str],
    candidate: dict[str, Any] | None,
) -> dict[str, Any]:
    candidate = candidate or {}
    return {
        "verification_type": "pre_act",
        "allowed_tools": list(allowed_tools),
        "argument_constraints": sanitize_witness_value(argument_constraints),
        "granted_permissions": list(granted_permissions),
        "required_permissions": list(required_permissions),
        "candidate": {
            "name": candidate.get("name"),
            "args": sanitize_witness_value(candidate.get("args", {})),
        },
    }


def replay_violation(witness: ViolationEvidence) -> ReplayResult:
    """Re-run deterministic checks without an LLM, graph, ledger, or tool call."""
    facts = witness.replay_input
    if not isinstance(facts, dict) or facts.get("verification_type") != "pre_act":
        return ReplayResult(
            blocked=True,
            decision=RecoveryAction.BLOCK,
            rule_ids=["REPLAY-INPUT-MISSING"],
        )
    if facts.get("indeterminate_rule"):
        return ReplayResult(
            blocked=True,
            decision=RecoveryAction.BLOCK,
            rule_ids=[str(facts["indeterminate_rule"])],
        )

    candidate = facts.get("candidate", {})
    tool_name = candidate.get("name") if isinstance(candidate, dict) else None
    if tool_name not in facts.get("allowed_tools", []):
        return ReplayResult(
            blocked=True,
            decision=RecoveryAction.BLOCK,
            rule_ids=["CONTRACT-TOOL-001"],
        )
    missing = set(facts.get("required_permissions", [])) - set(
        facts.get("granted_permissions", [])
    )
    if missing:
        return ReplayResult(
            blocked=True,
            decision=RecoveryAction.HUMAN_ESCALATION,
            rule_ids=["CONTRACT-AUTHORITY-001"],
        )
    try:
        rules = PolicyCompiler.argument_rules(facts.get("argument_constraints", {}))
        result = Z3ConstraintVerifier().verify(
            rules,
            {"arguments": candidate.get("args", {})},
        )
    except Exception:
        return ReplayResult(
            blocked=True,
            decision=RecoveryAction.BLOCK,
            rule_ids=["CONTRACT-CHECKER-001"],
        )
    if not result.passed:
        return ReplayResult(
            blocked=True,
            decision=RecoveryAction.BLOCK,
            rule_ids=result.witness_rule_ids,
        )
    return ReplayResult(blocked=False)
