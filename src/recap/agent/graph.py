"""ReCAP Graph composition root."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Any, Literal, TypeAlias

from langgraph.graph import END, START, StateGraph

from recap.agent.state import ReCAPState
from recap.ledger import LedgerService
from recap.nodes import (
    build_act_node,
    build_act_observe_check_node,
    build_observe_node,
    build_think_act_check_node,
    route_after_act,
    route_after_act_observe,
    route_after_think_act,
)
from recap.tools import ToolRegistry


NodeUpdate: TypeAlias = dict[str, Any]

ThinkNode: TypeAlias = Callable[
    [ReCAPState],
    NodeUpdate | Awaitable[NodeUpdate],
]

AfterThinkRoute: TypeAlias = Literal[
    "think_act_check_node",
    "__end__",
]


def route_after_think(
    state: ReCAPState,
) -> AfterThinkRoute:
    """只有同时存在候选调用和合同时才进入 Think→Act 检查。"""

    candidate = state.get("candidate_tool_call")
    contract = state.get("current_contract")

    if candidate is None or contract is None:
        return "__end__"

    return "think_act_check_node"


def build_recap_graph(
    *,
    think_node: ThinkNode,
    ledger: LedgerService,
    registry: ToolRegistry,
    tool_timeout_seconds: float = 30.0,
) -> StateGraph:
    """构建尚未编译的 ReCAP LangGraph。"""

    if tool_timeout_seconds <= 0:
        raise ValueError(
            "tool_timeout_seconds must be greater than zero"
        )

    think_act_check_node = build_think_act_check_node(
        ledger
    )

    act_node = build_act_node(
        ledger,
        registry,
        timeout_seconds=tool_timeout_seconds,
    )

    observe_node = build_observe_node(
        ledger
    )

    act_observe_check_node = (
        build_act_observe_check_node(ledger)
    )

    graph = StateGraph(ReCAPState)

    graph.add_node(
        "think_node",
        think_node,
    )
    graph.add_node(
        "think_act_check_node",
        think_act_check_node,
    )
    graph.add_node(
        "act_node",
        act_node,
    )
    graph.add_node(
        "observe_node",
        observe_node,
    )
    graph.add_node(
        "act_observe_check_node",
        act_observe_check_node,
    )

    graph.add_edge(
        START,
        "think_node",
    )

    graph.add_conditional_edges(
        "think_node",
        route_after_think,
        {
            "think_act_check_node": (
                "think_act_check_node"
            ),
            "__end__": END,
        },
    )

    # 只有 Think→Act 检查明确批准后，
    # 才允许进入 act_node。
    graph.add_conditional_edges(
        "think_act_check_node",
        route_after_think_act,
        {
            "act": "act_node",
            "replan": "think_node",
            "human_approval": END,
            "end": END,
        },
    )

    # 只有成功执行的动作才能进入 Observe。
    graph.add_conditional_edges(
        "act_node",
        route_after_act,
        {
            "observe": "observe_node",
            "replan": "think_node",
            "human_approval": END,
            "end": END,
        },
    )

    # Observation 必须经过证据检查，不能直接结束。
    graph.add_edge(
        "observe_node",
        "act_observe_check_node",
    )

    graph.add_conditional_edges(
        "act_observe_check_node",
        route_after_act_observe,
        {
            "replan": "think_node",
            "human_approval": END,
            "end": END,
        },
    )

    return graph


def compile_recap_graph(
    *,
    think_node: ThinkNode,
    ledger: LedgerService,
    registry: ToolRegistry,
    tool_timeout_seconds: float = 30.0,
    checkpointer: Any = None,
):
    """构建并编译 ReCAP LangGraph。"""

    builder = build_recap_graph(
        think_node=think_node,
        ledger=ledger,
        registry=registry,
        tool_timeout_seconds=(
            tool_timeout_seconds
        ),
    )

    return builder.compile(
        checkpointer=checkpointer
    )