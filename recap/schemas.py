"""ReCAP 核心数据模式：公开意图证书、三类事件记录、共享账本条目与违规证据链。

本模块定义了 ReCAP 护栏系统中所有结构化数据类，服务于三个阶段转换检查：
  - Think → Act：承诺兑现检查
  - Act → Observe：真实绑定与证据校验
  - Observe → Think：隔离间接提示注入

所有模型均基于 Pydantic，支持 JSON Schema 校验与序列化。
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Union

from pydantic import BaseModel, Field, field_validator


# =============================================================================
# 枚举定义
# =============================================================================


class TrustLevel(str, Enum):
    """数据信任等级。

    HIGH:   可信系统数据（策略引擎、账本自身）。
    MEDIUM: 工具事实（经过包装器校验的工具返回）。
    LOW:    低信任外部内容（第三方 API、用户邮件等）。
    """

    HIGH = "high"
    MEDIUM = "medium"
    LOW = "low"


class DataSource(str, Enum):
    """数据来源分类。"""

    SYSTEM = "system"
    TOOL = "tool"
    EXTERNAL = "external"


class ExecutionStatus(str, Enum):
    """工具调用执行状态。"""

    PENDING = "pending"
    EXECUTING = "executing"
    SUCCESS = "success"
    FAILED = "failed"
    BLOCKED = "blocked"
    UNKNOWN = "unknown"


class ViolationType(str, Enum):
    """违规类型。

    INTENT_VIOLATION:      意图违规（证书不满足约束或授权）。
    ACTION_VIOLATION:      动作违规（工具调用参数越界或权限不足）。
    OBSERVATION_POLLUTION: 返回污染（低信任数据包含控制指令）。
    EVIDENCE_INSUFFICIENT: 证据不足（无法证明状态变化或回执缺失）。
    HIGH_RISK_UNKNOWN:     高风险未知操作（超出已知规则覆盖范围）。
    """

    INTENT_VIOLATION = "intent_violation"
    ACTION_VIOLATION = "action_violation"
    OBSERVATION_POLLUTION = "observation_pollution"
    EVIDENCE_INSUFFICIENT = "evidence_insufficient"
    HIGH_RISK_UNKNOWN = "high_risk_unknown"


class RecoveryAction(str, Enum):
    """恢复动作（由违规类型映射到有限操作）。"""

    BLOCK = "block"
    REPLAN = "replan"
    PARAMETER_FIX = "parameter_fix"
    PURIFY = "purify"
    KEEP_UNFINISHED = "keep_unfinished"
    HUMAN_ESCALATION = "human_escalation"


class ObligationStatus(str, Enum):
    """证据义务状态。"""

    PENDING = "pending"
    FULFILLED = "fulfilled"
    FAILED = "failed"


# =============================================================================
# 2.1 公开意图证书 (Public Intent Certificate)
# =============================================================================


class IntentCertificate(BaseModel):
    """Agent 在 Think 阶段显式提交的结构化契约承诺（6 维字段）。

    这是 ReCAP 护栏的核心契约载体，只有携带有效证书的工具调用才被允许执行。
    证书解析失败、关键字段缺失或语义抽取置信度过低时，系统应要求 Agent
    补充信息或重新规划，而非默认放行。
    """

    certificate_id: str = Field(default_factory=lambda: f"cert-{uuid.uuid4().hex[:12]}")
    round_num: int = Field(default=0, ge=0, description="当前 ReAct 轮次")
    subgoal: str = Field(..., min_length=1, description="本轮具体子目标")
    proposed_operation: str = Field(..., min_length=1, description="拟执行的工具/动作名称")
    argument_constraints: dict[str, Any] = Field(
        default_factory=dict,
        description="参数约束：允许的数据范围、操作对象及接收方",
    )
    authority_basis: str = Field(..., min_length=1, description="授权依据：用户指令或政策凭证")
    expected_effect: str = Field(..., min_length=1, description="预期效果：允许和禁止的状态变化")
    required_evidence: list[str] = Field(
        default_factory=list,
        description="执行后必须获得的回执或来源证明",
    )

    @field_validator("required_evidence")
    @classmethod
    def _ensure_unique_evidence(cls, v: list[str]) -> list[str]:
        """确保证据列表去重。"""
        return list(dict.fromkeys(v))

    def summary(self) -> str:
        """生成人类可读的证书摘要。"""
        return (
            f"[Cert {self.certificate_id}] Round {self.round_num}: "
            f"'{self.subgoal}' via {self.proposed_operation}"
        )


# =============================================================================
# 2.2 动作事件 (Action Event)
# =============================================================================


class ActionEvent(BaseModel):
    """在工具执行前/后记录的工具调用详情。

    ReCAP 的 Trusted Tool Wrapper 在每次调用前生成唯一 call_id，
    并记录参数摘要，用于后续 Act→Observe 的真实绑定校验。
    """

    call_id: str = Field(default_factory=lambda: f"call-{uuid.uuid4().hex[:12]}")
    tool_name: str = Field(..., min_length=1)
    actual_params: dict[str, Any] = Field(default_factory=dict)
    param_summary: str = Field(default="", description="参数摘要，用于快速比对")
    execution_status: ExecutionStatus = Field(default=ExecutionStatus.PENDING)
    certificate_id: str = Field(default="", description="关联的意图证书 ID")
    timestamp: datetime = Field(
        default_factory=lambda: datetime.now(timezone.utc),
        description="事件时间戳（UTC）",
    )

    def model_post_init(self, __context: Any) -> None:
        """自动生成参数摘要（若未显式提供）。"""
        if not self.param_summary:
            params = {k: v for k, v in self.actual_params.items()}
            self.param_summary = str(params)
# =============================================================================
# 2.3 观测事件 (Observation Event)
# =============================================================================


class ObservationEvent(BaseModel):
    """在环境返回结果时记录的观测详情。

    将 Observation 强绑定到具体的工具调用 ID 和参数摘要，防止结果串线；
    为返回数据标记来源与信任等级，支撑 Observe→Think 的间接提示注入隔离。
    """

    call_id: str = Field(..., min_length=1)
    return_content: Any = Field(default=None)
    state_diff: dict[str, Any] | None = Field(
        default=None,
        description="执行前后状态差分，若无法观察则为 None",
    )
    evidence_collected: list[str] = Field(default_factory=list)
    data_source: DataSource = Field(default=DataSource.TOOL)
    trust_level: TrustLevel = Field(default=TrustLevel.MEDIUM)
    source_label: str = Field(default="", description="来源标签")
    is_complete: bool = Field(default=True, description="返回内容是否证据齐全")
    timestamp: datetime = Field(
        default_factory=lambda: datetime.now(timezone.utc),
    )

    def has_external_content(self) -> bool:
        """判断是否包含外部（低信任）内容。"""
        return self.data_source == DataSource.EXTERNAL or self.trust_level == TrustLevel.LOW


# =============================================================================
# 2.4 违规证据 (Violation Evidence)
# =============================================================================


class ViolationEvidence(BaseModel):
    """当规则冲突时提取的最小事实集合，生成可重放证据链。

    每条规则和账本事实分配唯一编号。验证失败时从冲突约束中提取最小
    冲突集合，映射回原始规则、意图字段、工具参数和 Observation 来源。
    """

    violation_id: str = Field(default_factory=lambda: f"viol-{uuid.uuid4().hex[:12]}")
    violation_type: ViolationType
    rule_id: str = Field(..., min_length=1, description="被违反的规则编号")
    rule_description: str = Field(..., min_length=1)
    intent_field: str | None = Field(default=None, description="关联的意图证书字段")
    expected_value: Any = Field(default=None, description="期望值（来自证书/策略）")
    actual_value: Any = Field(default=None, description="实际值（来自工具调用/返回）")
    decision: RecoveryAction = Field(default=RecoveryAction.BLOCK)
    evidence_chain: list[str] = Field(
        default_factory=list,
        description="最小违规证据链，每个元素为一条可追溯的事实或约束",
    )

    def format_evidence(self) -> str:
        """生成可读的违规证据报告，便于日志和人工审计。"""
        lines = [
            f"Violation: {self.violation_type.value}",
            f"Rule: {self.rule_description}",
        ]
        if self.intent_field:
            lines.append(f"Intent: {self.intent_field} = {self.expected_value}")
        lines.append(f"Actual: {self.actual_value}")
        lines.append(f"Decision: {self.decision.value}")
        if self.evidence_chain:
            lines.append("Evidence Chain:")
            for i, e in enumerate(self.evidence_chain, 1):
                lines.append(f"  {i}. {e}")
        return "\n".join(lines)


# =============================================================================
# 2.5 转换检查结果 (Transition Result)
# =============================================================================


class TransitionResult(BaseModel):
    """阶段转换检查的统一返回结构。"""

    passed: bool
    check_type: str = Field(
        ...,
        pattern=r"^(think->act|act->observe|observe->think)$",
        description="检查类型：think->act / act->observe / observe->think",
    )
    violations: list[ViolationEvidence] = Field(default_factory=list)
    recovery_actions: list[RecoveryAction] = Field(default_factory=list)
    purified_observation: Any = Field(
        default=None,
        description="净化后的 Observation，仅 observe->think 检查后填充",
    )
    next_allowed: bool = Field(default=True, description="是否允许进入下一阶段")

    @classmethod
    def pass_through(cls, check_type: str) -> "TransitionResult":
        """创建通过结果。"""
        return cls(passed=True, check_type=check_type, next_allowed=True)

    @classmethod
    def blocked(
        cls,
        check_type: str,
        violations: list[ViolationEvidence],
        recovery_actions: list[RecoveryAction] | None = None,
    ) -> "TransitionResult":
        """创建阻断结果。"""
        return cls(
            passed=False,
            check_type=check_type,
            violations=violations,
            recovery_actions=recovery_actions or [v.decision for v in violations],
            next_allowed=False,
        )


# =============================================================================
# 2.6 共享账本条目录 (Shared Ledger Entries)
# =============================================================================


class TaskEntry(BaseModel):
    """任务定义条目——初始化时写入账本。"""

    entry_type: str = Field(default="task", frozen=True)
    task_id: str = Field(default_factory=lambda: f"task-{uuid.uuid4().hex[:12]}")
    description: str = Field(..., min_length=1, description="用户原始任务描述")
    policies: list[str] = Field(default_factory=list, description="适用的安全策略 ID 列表")
    tools_available: list[str] = Field(default_factory=list, description="可用工具名列表")
    initial_permissions: list[str] = Field(default_factory=list, description="初始权限集")
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))


class IntentEntry(BaseModel):
    """意图条目——Think 阶段提交证书后写入账本。"""

    entry_type: str = Field(default="intent", frozen=True)
    certificate: IntentCertificate
    timestamp: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))


class ActionEntry(BaseModel):
    """动作条目——工具调用执行前后写入账本。"""

    entry_type: str = Field(default="action", frozen=True)
    action: ActionEvent
    timestamp: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))


class ObservationEntry(BaseModel):
    """观测条目——环境返回结果后写入账本。"""

    entry_type: str = Field(default="observation", frozen=True)
    observation: ObservationEvent
    timestamp: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))


class ObligationEntry(BaseModel):
    """证据义务条目——跟踪未完成的证据义务。"""

    entry_type: str = Field(default="obligation", frozen=True)
    obligation_id: str = Field(default_factory=lambda: f"obl-{uuid.uuid4().hex[:12]}")
    description: str = Field(..., min_length=1)
    status: ObligationStatus = Field(default=ObligationStatus.PENDING)
    certificate_id: str = Field(default="", description="关联的意图证书 ID")
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    fulfilled_at: datetime | None = Field(default=None)


class ViolationEntry(BaseModel):
    """违规条目——检测到违规时追加写入账本。"""

    entry_type: str = Field(default="violation", frozen=True)
    violation: ViolationEvidence
    timestamp: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))


# 共享账本条目的联合类型
LedgerEntry = Union[
    TaskEntry,
    IntentEntry,
    ActionEntry,
    ObservationEntry,
    ObligationEntry,
    ViolationEntry,
]
