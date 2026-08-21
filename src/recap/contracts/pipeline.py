"""Composable façade for the complete ReCAP contract lifecycle."""

from __future__ import annotations

from recap.contracts.capabilities import ToolCapability
from recap.contracts.compiler import PolicyCompiler
from recap.contracts.models import RuntimeContract
from recap.contracts.policy import CompiledPolicy, PolicyRule
from recap.contracts.task import TaskContract
from recap.evidence.adapter import EvidenceAdapter, EvidenceBundle
from recap.obligations.manager import PendingObligation, PendingObligationManager
from recap.recovery.manager import RecoveryManager
from recap.schemas import ActionEvent, IntentCertificate, ObservationEvent
from recap.tools.wrapper import TrustedToolResult
from recap.verification.constraints import ConstraintResult, Z3ConstraintVerifier
from recap.verification.cross_round import CrossRoundResult, CrossRoundVerifier


class ContractPipeline:
    def __init__(self) -> None:
        self.policy_compiler = PolicyCompiler()
        self.constraint_verifier = Z3ConstraintVerifier()
        self.evidence_adapter = EvidenceAdapter()
        self.cross_round_verifier = CrossRoundVerifier()
        self.obligation_manager = PendingObligationManager()
        self.recovery_manager = RecoveryManager()

    def compile_round(
        self,
        task: TaskContract,
        certificate: IntentCertificate,
        capability: ToolCapability,
        policy_rules: list[PolicyRule] | None = None,
    ) -> tuple[TaskContract, RuntimeContract, CompiledPolicy, CrossRoundResult]:
        contract, compiled = self.policy_compiler.compile(
            task,
            certificate,
            capability,
            policy_rules,
        )
        pending = self.obligation_manager.pending(task.task_id)
        carried = self.obligation_manager.carry_forward(task.task_id, contract.round_num)
        carried_ids = [item.obligation_id for item in carried]
        cross_round = self.cross_round_verifier.verify(
            task,
            task.current,
            contract,
            pending,
            carried_ids,
        )
        if not cross_round.passed:
            return task, contract, compiled, cross_round
        updated_task = task.append_version(contract).with_pending_obligations(carried_ids)
        return updated_task, contract, compiled, cross_round

    def verify_action(
        self,
        compiled: CompiledPolicy,
        arguments: dict[str, object],
    ) -> ConstraintResult:
        return self.constraint_verifier.verify(
            compiled.rules,
            {"arguments": arguments},
        )

    def verify_runtime_action(
        self,
        contract: RuntimeContract,
        arguments: dict[str, object],
    ) -> ConstraintResult:
        rules = self.policy_compiler.argument_rules(contract.argument_constraints)
        return self.constraint_verifier.verify(rules, {"arguments": arguments})

    def adapt_evidence(
        self,
        result: TrustedToolResult,
        action: ActionEvent | None = None,
        observation: ObservationEvent | None = None,
    ) -> EvidenceBundle:
        return self.evidence_adapter.adapt(result, action, observation)

    def open_obligations(self, contract: RuntimeContract) -> list[PendingObligation]:
        return self.obligation_manager.create_from_contract(contract)

    def settle_obligations(
        self,
        task_id: str,
        evidence: EvidenceBundle,
    ) -> list[PendingObligation]:
        return self.obligation_manager.settle(task_id, evidence)
