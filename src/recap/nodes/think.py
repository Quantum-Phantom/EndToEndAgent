from __future__ import annotations

import inspect
import json
import uuid
from collections.abc import Iterable
from typing import Any, Protocol

from langchain_core.messages import AIMessage, SystemMessage

from recap.agent.state import ReCAPState
from recap.integration import create_contract_and_record, record_violation
from recap.planning import LLMToolPlan
from recap.schemas import (
    IntentCertificate,
    RecoveryAction,
    TransitionResult,
    ViolationEvidence,
    ViolationType,
)


class StructuredChatModel(Protocol):
    def with_structured_output(self, schema: type[LLMToolPlan]) -> Any: ...


BASE_PROMPT = """You are the planning component of a guarded ReAct agent.
Choose at most one tool from AVAILABLE_TOOLS for the next atomic step.
Never invent a tool. Provide concrete JSON arguments and a concise expected effect.
Do not claim authority or broaden permissions; runtime code controls authorization.
If no tool is necessary, return final_answer. Do not reveal hidden chain-of-thought.
"""


def _tool_schema(tool: Any) -> dict[str, Any]:
    schema = getattr(tool, "args_schema", None)
    if schema is not None and hasattr(schema, "model_json_schema"):
        return schema.model_json_schema()
    return {"type": "object", "additionalProperties": True}


def _describe_tools(tools: Iterable[Any]) -> tuple[dict[str, Any], ...]:
    return tuple(
        {
            "name": tool.name,
            "description": getattr(tool, "description", ""),
            "arguments": _tool_schema(tool),
        }
        for tool in tools
    )


async def _await_if_needed(value: Any) -> Any:
    return await value if inspect.isawaitable(value) else value


def _exact_constraints(arguments: dict[str, Any]) -> dict[str, Any]:
    # 模型只能提出参数，不能自行定义更宽泛的运行时约束。
    return {name: {"eq": value} for name, value in arguments.items()}


def _required_evidence(values: list[str]) -> list[str]:
    return list(dict.fromkeys([*values, "tool_return", "call_id_binding"]))


async def _planning_violation(
    state: ReCAPState,
    ledger: Any,
    *,
    rule_id: str,
    description: str,
    actual_value: Any,
    proposed_operation: str = "__invalid_llm_plan__",
    proposed_arguments: dict[str, Any] | None = None,
) -> dict[str, Any]:
    proposed_arguments = proposed_arguments or {}
    round_num = int(state.get("round_num", 0)) + 1

    violation = ViolationEvidence(
        violation_type=ViolationType.HIGH_RISK_UNKNOWN,
        rule_id=rule_id,
        rule_description=description,
        intent_field="proposed_operation",
        expected_value="one registered tool",
        actual_value=actual_value,
        decision=RecoveryAction.BLOCK,
        evidence_chain=[description, f"actual={actual_value!r}"],
    )

    certificate = IntentCertificate(
        round_num=round_num,
        subgoal="记录并阻断未通过安全校验的模型规划",
        proposed_operation=proposed_operation,
        argument_constraints=_exact_constraints(proposed_arguments),
        authority_basis=f"user_request:{state['task_id']}",
        expected_effect="阻断候选动作，不允许产生工具副作用",
        required_evidence=[],
    )
    task_entry = state.get("task_entry")
    created = await _await_if_needed(
        create_contract_and_record(
            ledger=ledger,
            task_id=state["task_id"],
            thread_id=state["thread_id"],
            certificate=certificate,
            allowed_tools=[],
            permissions=list(
                getattr(
                    task_entry,
                    "initial_permissions",
                    getattr(task_entry, "permissions", []),
                )
                or []
            ),
            policy_refs=list(
                getattr(task_entry, "policies", getattr(task_entry, "policy_refs", []))
                or []
            ),
        )
    )
    contract, contract_created_event = created
    violation_event = await record_violation(
        ledger=ledger,
        contract=contract,
        thread_id=state["thread_id"],
        actor="think_node",
        violation_payload=violation.model_dump(mode="json"),
    )
    check = TransitionResult.blocked("think->act", [violation])
    return {
        "messages": [
            AIMessage(content="规划未通过本地安全校验，候选动作已阻断并记录到账本。")
        ],
        "round_num": round_num,
        "candidate_tool_call": None,
        "current_intent": certificate,
        "current_contract": None,
        "check_results": [check],
        "ledger_events": [contract_created_event, violation_event],
        "ledger_head_hash": getattr(violation_event, "entry_hash", None),
        "next_route": "end",
        "final_answer_allowed": False,
    }


def build_think_node(
    llm: StructuredChatModel,
    ledger: Any,
    tools: Iterable[Any],
    *,
    system_prompt: str = BASE_PROMPT,
):
    """构造真实 LLM Think 节点；Contract/权限/约束均由本地运行时生成。"""

    tool_specs = _describe_tools(tools)
    tool_names = {item["name"] for item in tool_specs}
    planner = llm.with_structured_output(LLMToolPlan)

    async def think_node(state: ReCAPState) -> dict[str, Any]:
        prompt = SystemMessage(
            content=system_prompt
            + "\nAVAILABLE_TOOLS:\n"
            + json.dumps(tool_specs, ensure_ascii=False, indent=2)
        )
        try:
            plan = await planner.ainvoke([prompt, *state.get("messages", [])])
            if not isinstance(plan, LLMToolPlan):
                plan = LLMToolPlan.model_validate(plan)
        except Exception as exc:
            error_detail = f"{type(exc).__name__}: {str(exc)}"
            return await _planning_violation(
                state,
                ledger,
                rule_id="THINK-STRUCTURED-OUTPUT",
                description="LLM output did not satisfy LLMToolPlan",
                actual_value=error_detail,
            )

        if plan.final_answer:
            return {
                "messages": [AIMessage(content=plan.final_answer)],
                "candidate_tool_call": None,
                "current_intent": None,
                "current_contract": None,
                "next_route": "end",
                "final_answer_allowed": True,
            }

        if plan.tool_name not in tool_names:
            return await _planning_violation(
                state,
                ledger,
                rule_id="THINK-TOOL-ALLOWLIST",
                description="LLM selected an unregistered tool",
                actual_value=plan.tool_name,
                proposed_operation=plan.tool_name,
                proposed_arguments=plan.tool_args,
            )

        round_num = int(state.get("round_num", 0)) + 1
        candidate = {
            "id": f"model-call-{uuid.uuid4().hex[:12]}",
            "name": plan.tool_name,
            "args": plan.tool_args,
            "type": "tool_call",
        }
        certificate = IntentCertificate(
            round_num=round_num,
            subgoal=plan.subgoal,
            proposed_operation=plan.tool_name,
            argument_constraints=_exact_constraints(plan.tool_args),
            # 授权依据来自可信任务上下文，禁止由 LLM 生成。
            authority_basis=f"user_request:{state['task_id']}",
            expected_effect=plan.expected_effect,
            required_evidence=_required_evidence(plan.required_evidence),
        )
        task_entry = state.get("task_entry")
        created = await _await_if_needed(
            create_contract_and_record(
                ledger=ledger,
                task_id=state["task_id"],
                thread_id=state["thread_id"],
                certificate=certificate,
                allowed_tools=[plan.tool_name],
                permissions=list(
                    getattr(
                        task_entry,
                        "initial_permissions",
                        getattr(task_entry, "permissions", []),
                    )
                    or []
                ),
                policy_refs=list(
                    getattr(task_entry, "policies", getattr(task_entry, "policy_refs", []))
                    or []
                ),
            )
        )
        # integration.py 的标准返回为 (contract, ledger_entry)。
        contract, ledger_entry = created
        ai_message = AIMessage(
            content=plan.plan_summary,
            tool_calls=[candidate],
        )
        return {
            "messages": [ai_message],
            "round_num": round_num,
            "current_intent": certificate,
            "current_contract": contract,
            "candidate_tool_call": candidate,
            "ledger_events": [ledger_entry],
            "ledger_head_hash": getattr(ledger_entry, "entry_hash", None),
            "next_route": "check",
            "final_answer_allowed": False,
        }

    return think_node