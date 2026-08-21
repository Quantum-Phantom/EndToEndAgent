"""Opt-in real-API conformance test for the complete ReCAP workflow."""

from __future__ import annotations

import os
import uuid
from collections import Counter, defaultdict
from pathlib import Path

import pytest
from dotenv import load_dotenv
from langchain_core.messages import HumanMessage
from langchain_openai import ChatOpenAI

from recap.agent.graph import compile_recap_graph
from recap.contracts import ContractPipeline, ContractStatus
from recap.ledger import LedgerEventType, LedgerService, SQLiteLedgerRepository
from recap.nodes.think import BASE_PROMPT, build_think_node
from recap.recovery import route_for_recovery
from recap.schemas import RecoveryAction, TaskEntry, ViolationType
from recap.tools import ALL_TOOLS, TOOL_CAPABILITIES, ToolRegistry

pytestmark = pytest.mark.skipif(
    os.getenv("RUN_REAL_API_TESTS") != "1",
    reason="set RUN_REAL_API_TESTS=1 to run real-LLM integration tests",
)


def _real_llm() -> ChatOpenAI:
    load_dotenv()
    model = os.getenv("RECAP_MODEL") or os.getenv("MODEL_NAME")
    api_key = os.getenv("RECAP_API_KEY") or os.getenv("API_KEY")
    base_url = os.getenv("RECAP_BASE_URL") or os.getenv("BASE_URL")
    if not model or not api_key:
        pytest.skip("real API test requires MODEL_NAME/RECAP_MODEL and API_KEY")
    kwargs = {
        "model": model,
        "api_key": api_key,
        "temperature": 0,
        "timeout": 60,
        "max_retries": 2,
    }
    if base_url:
        kwargs["base_url"] = base_url
    return ChatOpenAI(**kwargs)


@pytest.mark.asyncio
async def test_real_api_complex_task_satisfies_recap_contract() -> None:
    task_id = "real-complex-conformance"
    thread_id = "real-complex-thread"
    runtime_directory = Path(__file__).resolve().parents[2] / ".test-runtime"
    runtime_directory.mkdir(parents=True, exist_ok=True)
    database = runtime_directory / f"real-complex-{uuid.uuid4().hex}.sqlite3"
    repository = SQLiteLedgerRepository(database)
    ledger = LedgerService(repository)
    pipeline = ContractPipeline()
    registry = ToolRegistry()
    for tool in ALL_TOOLS:
        registry.register(tool, TOOL_CAPABILITIES[tool.name])

    system_prompt = BASE_PROMPT + """
This is a deterministic conformance run. Follow the user's required tool order.
For these pure data/text tools, allowed_effects, forbidden_effects and
required_effects must be empty. Use tool_return and call_id_binding as evidence.
Use exactly one tool in each Think round and use facts from purified_context.
"""
    think_node = build_think_node(
        llm=_real_llm(),
        ledger=ledger,
        tools=ALL_TOOLS,
        pipeline=pipeline,
        capabilities=TOOL_CAPABILITIES,
        system_prompt=system_prompt,
    )
    graph = compile_recap_graph(
        think_node=think_node,
        ledger=ledger,
        registry=registry,
        contract_pipeline=pipeline,
    )
    task_entry = TaskEntry(
        task_id=task_id,
        description="Process authorized JSON records using the required tools",
        policies=["pure-data-processing", "one-tool-per-round"],
        tools_available=["parse_json", "filter_records", "select_fields", "text_stats"],
        initial_permissions=["data:process", "text:process"],
    )
    user_task = """
Run this workflow using exactly one tool per round and in this order:
1. parse_json on [{"name":"Ada","active":true,"team":"research"},
   {"name":"Lin","active":false,"team":"ops"}]
2. filter_records where active equals true, using the parsed records.
3. select_fields name and team from the single remaining record.
4. text_stats on the exact text "Ada research".
Only after all four successful calls, answer with the selected record and stats.
Do not call arithmetic tools and do not perform any external side effect.
"""

    try:
        result = await graph.ainvoke(
            {
                "messages": [HumanMessage(content=user_task)],
                "task_id": task_id,
                "thread_id": thread_id,
                "round_num": 0,
                "max_rounds": 6,
                "task_entry": task_entry,
                "contract_history": [],
                "round_summaries": [],
                "ledger_events": [],
                "check_results": [],
                "pending_obligations": [],
                "task_completed": False,
            },
            config={"recursion_limit": 40},
        )

        events = await repository.list_events(task_id, thread_id)
        by_round = defaultdict(list)
        for event in events:
            by_round[event.round_num].append(event)

        diagnostics = {
            "next_route": result.get("next_route"),
            "round_num": result.get("round_num"),
            "task_completed": result.get("task_completed"),
            "pending_obligations": result.get("pending_obligations"),
            "contract_status": (
                result["current_contract"].status.value
                if result.get("current_contract") is not None
                else None
            ),
            "executed_tools": [
                event.payload["action"]["tool_name"]
                for event in events
                if event.event_type == LedgerEventType.ACTION_EXECUTED
            ],
            "violations": [
                event.payload
                for event in events
                if event.event_type == LedgerEventType.VIOLATION_DETECTED
            ],
        }
        assert result["task_completed"] is True, diagnostics
        assert result["final_answer_allowed"] is True
        assert result["pending_obligations"] == []
        assert result["current_contract"].status == ContractStatus.FULFILLED
        assert len(result["task_contract"].versions) == 4
        assert all(
            contract.status == ContractStatus.FULFILLED
            for contract in result["task_contract"].versions
        )

        executed = [
            event
            for event in events
            if event.event_type == LedgerEventType.ACTION_EXECUTED
        ]
        assert [event.payload["action"]["tool_name"] for event in executed] == [
            "parse_json",
            "filter_records",
            "select_fields",
            "text_stats",
        ]
        assert all(
            sum(event.event_type == LedgerEventType.ACTION_EXECUTED for event in round_events)
            <= 1
            for round_events in by_round.values()
        )

        counts = Counter(event.event_type for event in events)
        assert counts[LedgerEventType.CONTRACT_CREATED] == 4
        assert counts[LedgerEventType.OBSERVATION_RECORDED] == 4
        assert counts[LedgerEventType.OBLIGATION_FULFILLED] == 4
        assert counts[LedgerEventType.CONTRACT_FULFILLED] == 4

        created_contracts = [
            event.payload["contract"]
            for event in events
            if event.event_type == LedgerEventType.CONTRACT_CREATED
        ]
        assert all(contract["certificate"]["subgoal"] for contract in created_contracts)
        assert all(
            contract["certificate"]["proposed_operation"]
            in task_entry.tools_available
            for contract in created_contracts
        )
        assert all(
            contract["certificate"]["authority_basis"]
            == f"user_request:{task_id}"
            for contract in created_contracts
        )
        assert all(
            contract["certificate"]["argument_constraints"]
            for contract in created_contracts
        )

        actions = {
            event.round_num: event.payload["action"]
            for event in executed
        }
        observations = {
            event.round_num: event.payload
            for event in events
            if event.event_type == LedgerEventType.OBSERVATION_RECORDED
        }
        assert all(
            actions[round_num]["call_id"] == observations[round_num]["call_id"]
            for round_num in actions
        )
        assert all(
            {"tool_return", "call_id_binding"}
            <= set(observation["evidence_collected"])
            for observation in observations.values()
        )
        assert await ledger.verify_chain(task_id, thread_id) is True

        # Recovery is deterministic and cannot be selected freely by the model.
        rules = pipeline.policy_compiler.argument_rules(
            result["current_contract"].argument_constraints
        )
        recovery = pipeline.recovery_manager.decide(
            ViolationType.ACTION_VIOLATION,
            arguments={"text": "tampered"},
            rules=rules,
        )
        assert recovery.action == RecoveryAction.PARAMETER_FIX
        assert route_for_recovery(recovery.action) == "replan"
    finally:
        await repository.close()
        database.unlink(missing_ok=True)
