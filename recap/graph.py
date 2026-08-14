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
    路由: -> observe_node（无条件）
    """
    ...


def observe_node(state: ReCAPState) -> dict:
    """Step 3 | Observe: 处理并标记环境返回结果，产生 ObservationEvent。

    职责：
    1. 从 ToolMessage 提取返回内容。
    2. 强绑定 Observation 到 call_id 防止串线。
    3. 标记来源与信任等级（SYSTEM->HIGH, TOOL->MEDIUM, EXTERNAL->LOW）。
    4. 采集执行前后的状态差分（若环境可观察）。
    5. 创建 ObligationEntry 跟踪证据义务。
    6. 追加 ObservationEntry + ObligationEntry 到 ledger_entries。

    输入: state["current_action"], state["messages"][-1] (ToolMessage)
    输出: current_observation, ledger_entries (ObservationEntry/ObligationEntry)
    路由: -> act_observe_check_node（无条件）
    """
    ...


def act_observe_check_node(state: ReCAPState) -> dict:
    """Step 4 | Act->Observe 检查: 真实绑定与证据校验。

    职责：
    1. 核对 Observation.call_id 与 Action.call_id 一致（防串线）。
    2. 比较状态差分与证书预期效果（若可观察）；
       无法证实 -> 将效果标记为 unknown。
    3. 检查 required_evidence 是否已收集齐全：
       齐全 -> 关闭 ObligationEntry (FULFILLED)；
       不足 -> 保持 PENDING，禁止 Agent 虚假宣布成功。
    4. 若违规 -> 生成 ViolationEvidence(EVIDENCE_INSUFFICIENT)。

    输入: state["current_action"], state["current_observation"],
          state["current_intent"].required_evidence
    输出: check_results, ledger_entries (ObligationEntry/ViolationEntry),
          current_observation (可能更新 is_complete/state_diff)
    路由: 通过 -> observe_think_check_node / 违规 -> think_node 或 END
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


def repair_node(state: ReCAPState) -> dict:
    """Step R1 | Repair: 数据级恢复——参数修复与观测净化。

    职责：
    1. 读取最近一条 check_result，确定恢复动作类型。
    2. PARAMETER_FIX：将越界参数收缩到 argument_constraints 允许范围内，
       更新 current_intent 中的参数约束，追加 RepairEntry 到 ledger_entries。
    3. PURIFY：对低信任 Observation 进行净化——剥离控制指令，
       保留带来源标签的数据事实，填充 purified_observation。
    4. 对不需要修复的状态（如通过检查后进入），作为 no-op 透传。

    输入: state["check_results"][-1], state["current_intent"],
          state["current_observation"]
    输出: current_intent (可能被修复), current_observation (可能被净化),
          ledger_entries (RepairEntry)
    路由: -> replan_node（无条件）
    """
    ...


def replan_node(state: ReCAPState) -> dict:
    """Step R2 | Replan: 控制流级恢复——重规划编排。

    职责：
    1. 读取最近一条 check_result，确定恢复动作类型。
    2. REPLAN：标记当前意图证书为 rejected，清理 current_intent/current_action，
       设置 replan_context（包含违规摘要与约束提示），
       追加 ReplanEntry 到 ledger_entries。
    3. KEEP_UNFINISHED：保持未完成的 ObligationEntry 为 PENDING，
       向上下文注入"任务未完成，禁止虚假宣布成功"的约束。
    4. 对不需要重规划的状态，作为 no-op 透传。

    输入: state["check_results"][-1], state["current_intent"],
          state["ledger_entries"]
    输出: current_intent (可能被清空), ledger_entries (ReplanEntry),
          messages (可能追加约束提示)
    路由: -> think_node（无条件，进入下一轮）
    """
    ...


def route_after_think_act_check(
    state: ReCAPState,
) -> Literal["act_node", "repair_node", "__end__"]:
    """Think→Act 检查后路由。

    根据 TransitionResult 决定后续路径：
    - passed=True 或 recovery=PARAMETER_FIX -> act_node（继续执行）
    - recovery=REPLAN/KEEP_UNFINISHED -> repair_node（进入恢复流水线）
    - recovery=BLOCK/HUMAN_ESCALATION -> END（终止）
    """
    ...


def route_after_act_observe_check(
    state: ReCAPState,
) -> Literal["observe_think_check_node", "repair_node", "__end__"]:
    """Act→Observe 检查后路由。

    根据 TransitionResult 决定后续路径：
    - passed=True -> observe_think_check_node（继续检查流水线）
    - recovery=REPLAN/KEEP_UNFINISHED -> repair_node（进入恢复流水线）
    - recovery=BLOCK/HUMAN_ESCALATION -> END（终止）
    """
    ...


def route_after_observe_think_check(
    state: ReCAPState,
) -> Literal["repair_node", "__end__"]:
    """Observe→Think 检查后路由。

    根据 TransitionResult 决定后续路径：
    - passed=True 或 recovery=PURIFY -> repair_node（进入恢复流水线，
      正常通过时为 no-op 透传）
    - recovery=BLOCK/HUMAN_ESCALATION -> END（终止）
    """
    ...


def build_recap_graph() -> StateGraph:
    """构建 ReCAP 护栏增强的 ReAct Agent 图。

    图拓扑（每轮完整流程）:

                              ┌── 违规时跳过中间节点 ──────────────────┐
                              │  think_act_check ──(replan)────────┐   │
                              │  act_observe_check ──(replan)──────┤   │
                              │  observe_think_check ──(purify)────┤   │
                              │                                     │   │
        START                  │                                     │   │
          |                    │                                     │   │
          v                    │                                     │   │
      [think_node]  Step 0    │                                     │   │
          |                    │                                     │   │
          v (有 tool_calls)    │                                     │   │
      [think_act_check_node]  Step 1                                │   │
          |                    │                                     │   │
          | (pass/fix)         │                                     │   │
          v                    │                                     │   │
      [act_node]  Step 2      │                                     │   │
          |                    │                                     │   │
          v                    │                                     │   │
      [observe_node]  Step 3  │                                     │   │
          |                    │                                     │   │
          v                    │                                     │   │
      [act_observe_check_node] Step 4                                │   │
          |                    │                                     │   │
          | (pass)             │                                     │   │
          v                    │                                     │   │
      [observe_think_check_node] Step 5                              │   │
          |                    │                                     │   │
          | (pass/purify)      │                                     │   │
          v                    v                                     │   │
      [repair_node]  Step R1  <─────────────────────────────────────┘   │
          |                                                             │
          v                                                             │
      [replan_node]  Step R2                                            │
          |                                                             │
          v (下一轮)                                                     │
      [think_node] --> ...                                              │
          |                                                             │
          v (无 tool_calls)                                              │
         END                                                            │

    违规恢复路由:
      - think_act_check pass/fix        -> act_node（继续执行）
      - think_act_check replan/keep     -> repair_node（进入恢复流水线，
                                          跳过 act/observe/后续检查）
      - think_act_check block/escalate  -> END（终止）

      - act_observe_check pass          -> observe_think_check_node
      - act_observe_check replan/keep   -> repair_node（进入恢复流水线，
                                          跳过 observe_think_check）
      - act_observe_check block/escalate -> END

      - observe_think_check pass/purify -> repair_node（进入恢复流水线，
                                          正常通过时为 no-op 透传）
      - observe_think_check block/escalate -> END

    恢复流水线:
      repair_node -> replan_node -> think_node（下一轮）

    Returns:
        StateGraph: 未编译的图构建器，调用方需自行 .compile()。
    """
    graph = StateGraph(ReCAPState)

    # ── 核心步骤节点 ──
    graph.add_node("think_node", think_node)
    graph.add_node("think_act_check_node", think_act_check_node)
    graph.add_node("act_node", act_node)
    graph.add_node("observe_node", observe_node)
    graph.add_node("act_observe_check_node", act_observe_check_node)
    graph.add_node("observe_think_check_node", observe_think_check_node)

    # ── 恢复节点 ──
    graph.add_node("repair_node", repair_node)
    graph.add_node("replan_node", replan_node)

    # ── 入口 ──
    graph.add_edge(START, "think_node")

    # ── Think 后路由：有 tool_calls 进入检查流水线，否则结束 ──
    graph.add_conditional_edges(
        "think_node",
        should_continue_after_think,
        {
            "think_act_check_node": "think_act_check_node",
            "__end__": END,
        },
    )

    # ── Think→Act 检查后路由 ──
    graph.add_conditional_edges(
        "think_act_check_node",
        route_after_think_act_check,
        {
            "act_node": "act_node",
            "repair_node": "repair_node",
            "__end__": END,
        },
    )

    # ── 正常执行流水线（通过检查时） ──
    graph.add_edge("act_node", "observe_node")
    graph.add_edge("observe_node", "act_observe_check_node")

    # ── Act→Observe 检查后路由 ──
    graph.add_conditional_edges(
        "act_observe_check_node",
        route_after_act_observe_check,
        {
            "observe_think_check_node": "observe_think_check_node",
            "repair_node": "repair_node",
            "__end__": END,
        },
    )

    # ── Observe→Think 检查后路由 ──
    graph.add_conditional_edges(
        "observe_think_check_node",
        route_after_observe_think_check,
        {
            "repair_node": "repair_node",
            "__end__": END,
        },
    )

    # ── 恢复流水线：repair -> replan -> think（下一轮） ──
    graph.add_edge("repair_node", "replan_node")
    graph.add_edge("replan_node", "think_node")

    return graph


recap_graph_builder = build_recap_graph()
