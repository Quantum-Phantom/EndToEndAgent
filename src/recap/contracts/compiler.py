"""Compile task, tool and policy declarations into a round contract."""

from __future__ import annotations

from typing import Any

from recap.contracts.capabilities import ToolCapability
from recap.contracts.models import RuntimeContract
from recap.contracts.policy import CompiledPolicy, ConstraintOperator, PolicyRule
from recap.contracts.task import TaskContract
from recap.schemas import IntentCertificate


class PolicyCompiler:
    def compile(
        self,
        task: TaskContract,
        certificate: IntentCertificate,
        capability: ToolCapability,
        policy_rules: list[PolicyRule] | None = None,
    ) -> tuple[RuntimeContract, CompiledPolicy]:
        if certificate.proposed_operation != capability.name:
            raise ValueError("certificate operation does not match tool capability")
        if capability.name not in task.capability_names:
            raise ValueError(f"tool is outside the task contract: {capability.name}")

        missing_permissions = set(capability.required_permissions) - set(task.granted_permissions)
        if missing_permissions:
            raise PermissionError(f"missing tool permissions: {sorted(missing_permissions)}")

        constraints = self._merge_constraints(
            capability.argument_constraints,
            certificate.argument_constraints,
        )
        rules = [*self.argument_rules(constraints), *(policy_rules or [])]
        policy_refs = list(
            dict.fromkeys([*task.policy_refs, *(r.policy_ref for r in rules if r.policy_ref)])
        )
        allowed_effects = self._intersection_or_declared(
            capability.allowed_effects,
            certificate.allowed_effects,
        )
        required_effects = list(
            dict.fromkeys([*capability.required_effects, *certificate.required_effects])
        )
        forbidden_effects = list(
            dict.fromkeys([*capability.forbidden_effects, *certificate.forbidden_effects])
        )
        if set(required_effects) & set(forbidden_effects):
            raise ValueError("a required effect is forbidden by the compiled policy")

        compiled = CompiledPolicy(
            rules=rules,
            allowed_tools=[capability.name],
            granted_permissions=list(capability.required_permissions),
            allowed_effects=allowed_effects,
            forbidden_effects=forbidden_effects,
            required_effects=required_effects,
            required_evidence=list(
                dict.fromkeys([*capability.evidence_types, *certificate.required_evidence])
            ),
            authority_refs=list(dict.fromkeys([*task.authority_refs, certificate.authority_basis])),
            policy_refs=policy_refs,
            normative_baseline=task.normative_baseline,
        )
        contract = RuntimeContract(
            task_id=task.task_id,
            round_num=certificate.round_num,
            certificate=certificate,
            allowed_tools=compiled.allowed_tools,
            argument_constraints=constraints,
            granted_permissions=compiled.granted_permissions,
            allowed_effects=compiled.allowed_effects,
            forbidden_effects=compiled.forbidden_effects,
            required_effects=compiled.required_effects,
            required_evidence=compiled.required_evidence,
            authority_refs=compiled.authority_refs,
            policy_refs=compiled.policy_refs,
            normative_baseline=compiled.normative_baseline,
        )
        return contract, compiled

    @staticmethod
    def _merge_constraints(
        base: dict[str, Any],
        requested: dict[str, Any],
    ) -> dict[str, Any]:
        merged: dict[str, Any] = {}
        if set(requested) - set(base):
            unknown = sorted(set(requested) - set(base))
            raise ValueError(f"certificate introduces undeclared arguments: {unknown}")

        for field, capability_rule in base.items():
            if field not in requested:
                rules = (
                    capability_rule
                    if isinstance(capability_rule, dict)
                    else {"eq": capability_rule}
                )
                if rules.get("required", True):
                    raise ValueError(f"certificate omits required argument: {field}")
                continue
            requested_rule = requested[field]
            if requested_rule == capability_rule:
                merged[field] = requested_rule
                continue
            if not isinstance(requested_rule, dict) or set(requested_rule) != {"eq"}:
                raise ValueError(f"certificate must commit to one exact value: {field}")

            value = requested_rule["eq"]
            rules = (
                capability_rule
                if isinstance(capability_rule, dict)
                else {"eq": capability_rule}
            )
            if "eq" in rules and value != rules["eq"]:
                raise ValueError(f"argument exceeds capability: {field}")
            if "in" in rules and value not in rules["in"]:
                raise ValueError(f"argument exceeds capability: {field}")
            if "not_in" in rules and value in rules["not_in"]:
                raise ValueError(f"argument is forbidden: {field}")
            if "gte" in rules and value < rules["gte"]:
                raise ValueError(f"argument is below capability bound: {field}")
            if "lte" in rules and value > rules["lte"]:
                raise ValueError(f"argument exceeds capability bound: {field}")
            merged[field] = requested_rule
        return merged

    @staticmethod
    def argument_rules(constraints: dict[str, Any]) -> list[PolicyRule]:
        rules: list[PolicyRule] = []
        for field, constraint in constraints.items():
            values = constraint if isinstance(constraint, dict) else {"eq": constraint}
            for operator, expected in values.items():
                rules.append(
                    PolicyRule(
                        rule_id=f"argument.{field}.{operator}",
                        field=f"arguments.{field}",
                        operator=ConstraintOperator(operator),
                        expected=expected,
                    )
                )
        return rules

    @staticmethod
    def _intersection_or_declared(declared: list[str], requested: list[str]) -> list[str]:
        if not requested:
            return list(declared)
        outside = set(requested) - set(declared)
        if outside:
            raise ValueError(f"certificate requests undeclared effects: {sorted(outside)}")
        return list(requested)
