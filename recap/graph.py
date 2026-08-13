"""ReCAP 图结构声明：每轮 3 个 ReAct 步骤 + 3 个阶段转换检查步骤。

本模块定义 ReCAP 护栏系统在 LangGraph 中的节点签名与图拓扑。
所有节点均以函数桩（stub）形式声明，暂不实现具体逻辑。
"""

from __future__ import annotations

from operator import add
from typing import Annotated, Literal, NotRequired

from langgraph.graph import END, START, StateGraph
from langgraph.graph.message import MessagesState

from recap.schemas import (
    ActionEvent,
    IntentCertificate,
    LedgerEntry,
    ObservationEvent,
    TaskEntry,
    TransitionResult,
)


class ReCAPState(MessagesState):
    """ReCAP 共享状态，继承 MessagesState 并扩展护栏专用字段。

    继承字段:
        messages: Annotated[list[AnyMessage], add_messages]

    扩展字段:
        round_num           — 当前 ReAct 轮次（首轮 0，逐轮递增）。
        task_entry          — 用户任务定义（初始化写入一次）。
        current_intent      — 本轮 Think 提交的意图证书。
        current_action      — 本轮工具调用事件。
        current_observation — 本轮环境返回观测。
        ledger_entries      — 共享契约与证据账本（追加写入，不可覆盖）。
        check_results       — 历次阶段检查结果（追加写入）。
    """

    round_num: NotRequired[int]
    task_entry: NotRequired[TaskEntry]
    current_intent: NotRequired[IntentCertificate | None]
    current_action: NotRequired[ActionEvent | None]
    current_observation: NotRequired[ObservationEvent | None]
    ledger_entries: NotRequired[Annotated[list[LedgerEntry], add]]
    check_results: NotRequired[Annotated[list[TransitionResult], add]]


def think_node(state: ReCAPState) -> dict:
    """Step 0 | Think: LLM 生成意图证书与候选工具调用。

    职责：
    1. 组装上下文（系统提示词 + 任务描述 + 策略约束 + 前轮净化数据 + 消息历史）。
    2. 调用 LLM，要求同时输出：结构化意图证书 + 标准 AIMessage（含 tool_calls）。
    3. 解析并校验证书，失败则要求 Agent 补充信息。
    4. 将证书写入 IntentEntry 追加到 ledger_entries。
    5. 更新 current_intent、round_num。

    输入: state["messages"], state["task_entry"], state["round_num"]
    输出: round_num, current_intent, messages, ledger_entries
    路由: 有 tool_calls -> think_act_check_node / 无 -> END
    """
    ...


def think_act_check_node(state: ReCAPState) -> dict:
    """Step 1 | Think->Act 检查: 承诺兑现验证。

    职责：
    1. 加载意图证书和实际工具调用参数。
    2. 四项确定性检查：
       a. 操作是否服务于声明子目标（工具名/语义对齐）。
       b. 实际参数是否落在 argument_constraints 允许范围。
       c. 授权 authority_basis 是否真实且未失效。
       d. 是否遵循最小权限原则。
    3. 违规恢复：参数越界 -> PARAMETER_FIX 自动收缩；
       目标/授权问题 -> REPLAN 回 think_node；
       高风险未知 -> BLOCK + HUMAN_ESCALATION。

    输入: state["current_intent"], state["messages"][-1].tool_calls
    输出: check_results, current_intent (可能被修复), ledger_entries (ViolationEntry)
    路由: 通过/修复 -> act_node / 重规划 -> think_node / 阻断 -> END
    """
    ...


def act_node(state: ReCAPState) -> dict:
    """Step 2 | Act: 可信工具包装器执行工具调用。

    职责：
    1. 从最新 AIMessage.tool_calls 提取工具调用列表。
    2. 对每个调用：生成唯一 call_id，记录 ActionEvent，实际执行工具。
    3. 将结果封装为 ToolMessage，追加 ActionEntry 到 ledger_entries。

    输入: state["messages"][-1].tool_calls, state["current_intent"]
    输出: current_action, messages (ToolMessage), ledger_entries (ActionEntry)
    路由: -> act_observe_check_node（无条件）
    """
    ...


def act_observe_check_node(state: ReCAPState) -> dict:
    """Step 3 | Act->Observe 检查: 真实绑定与证据校验。

    职责：
    1. 核对返回 call_id 与动作 call_id 一致（防串线）。
    2. 比较状态差分与证书预期效果（若可观察）。
    3. 检查 required_evidence 是否齐全：
       齐全 -> 关闭 ObligationEntry (FULFILLED)；
       不足 -> 保持 PENDING，禁止虚假宣布成功。
    4. 违规 -> 生成 ViolationEvidence。

    输入: state["current_action"], state["current_observation"]
    输出: check_results, ledger_entries (ObligationEntry/ViolationEntry)
    路由: 通过 -> observe_node / 违规 -> think_node 或 END
    """
    ...


def observe_node(state: ReCAPState) -> dict:
    """Step 4 | Observe: 处理并标记环境返回结果。

    职责：
    1. 从 ToolMessage 提取返回内容。
    2. 强绑定 Observation 到 call_id 防止串线。
    3. 标记来源与信任等级（SYSTEM->HIGH, TOOL->MEDIUM, EXTERNAL->LOW）。
    4. 比较状态差分与预期效果。
    5. 创建 ObligationEntry 跟踪证据义务。
    6. 追加 ObservationEntry + ObligationEntry 到 ledger_entries。

    输入: state["current_action"], state["messages"][-1] (ToolMessage)
    输出: current_observation, ledger_entries (ObservationEntry/ObligationEntry)
    路由: -> observe_think_check_node（无条件）
    """
    ...


def observe_think_check_node(state: ReCAPState) -> dict:
    """Step 5 | Observe->Think 检查: 隔离间接提示注入。

    职责：
    1. 按来源切分 Observation (HIGH/MEDIUM/LOW)。
    2. 净化低信任内容：允许作为数据事实引用，禁止作为控制指令。
    3. 比较新旧目标与权限：目标变化须追溯用户授权；权限只缩不扩。
    4. 生成净化观察 purified_observation。
    5. 检测注入 -> 生成 ViolationEvidence(OBSERVATION_POLLUTION)。

    输入: state["current_observation"], state["current_intent"]
    输出: check_results, current_observation (可能净化), ledger_entries (ViolationEntry)
    路由: 通过/污染 -> think_node (带净化数据) / 严重 -> END
    """
    ...


def should_continue_after_think(state: ReCAPState) -> Literal["think_act_check_node", "__end__"]:
    """Think 后路由: 有 tool_calls -> think_act_check_node / 否则 -> END。

    检查最新 AIMessage 是否包含 tool_calls：
    - 有 -> 进入 think_act_check_node（启动检查流水线）
    - 无 -> END（Agent 已完成任务或给出最终回复）
    """
    ...


def build_recap_graph() -> StateGraph:
    """构建 ReCAP 护栏增强的 ReAct Agent 图。

    图拓扑（每轮完整流程）:

        START
          |
          v
      [think_node]
          |
          v (条件路由: should_continue_after_think)
          |-- 无 tool_calls --> END
          |
          v (有 tool_calls)
      [think_act_check_node]  <-- Think->Act 承诺兑现检查
          |
          v (通过/参数修复)
      [act_node]               <-- Act 可信工具执行
          |
          v
      [act_observe_check_node] <-- Act->Observe 证据校验
          |
          v (通过)
      [observe_node]           <-- Observe 结果标记
          |
          v
      [observe_think_check_node] <-- Observe->Think 注入隔离
          |
          v (进入下一轮)
      [think_node]  --> ...

    违规恢复路由:
      - think_act_check 违规 -> REPLAN: think_node 重规划
      - think_act_check 阻断 -> BLOCK: END
      - act_observe_check 违规 -> think_node 或 END
      - observe_think_check 污染 -> think_node (带净化数据)
      - observe_think_check 严重 -> END

    Returns:
        StateGraph: 未编译的图构建器，调用方需自行 .compile()。
    """
    graph = StateGraph(ReCAPState)

    graph.add_node("think_node", think_node)
    graph.add_node("think_act_check_node", think_act_check_node)
    graph.add_node("act_node", act_node)
    graph.add_node("act_observe_check_node", act_observe_check_node)
    graph.add_node("observe_node", observe_node)
    graph.add_node("observe_think_check_node", observe_think_check_node)

    graph.add_edge(START, "think_node")
    graph.add_conditional_edges(
        "think_node",
        should_continue_after_think,
        {
            "think_act_check_node": "think_act_check_node",
            "__end__": END,
        },
    )
    graph.add_edge("think_act_check_node", "act_node")
    graph.add_edge("act_node", "act_observe_check_node")
    graph.add_edge("act_observe_check_node", "observe_node")
    graph.add_edge("observe_node", "observe_think_check_node")
    graph.add_edge("observe_think_check_node", "think_node")

    return graph


recap_graph_builder = build_recap_graph()
