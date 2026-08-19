"""ReCAP 核心数据模式：公开意图证书、三类事件记录、共享账本条目与违规证据链。

本模块定义了 ReCAP 护栏系统中所有结构化数据类，服务于三个阶段转换检查：
  - Think → Act：承诺兑现检查
  - Act → Observe：真实绑定与证据校验
  - Observe → Think：隔离间接提示注入

所有模型均基于 Pydantic，支持 JSON Schema 校验与序列化。
"""

from __future__ import annotations

import ast
import re
import uuid
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Literal, Union

from pydantic import BaseModel, Field, field_validator, model_validator


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


class AuthorityBasis(str, Enum):
    """授权依据——Think 阶段声明工具调用的合法授权来源。

    LLM 必须从此受控词表中选择其一，避免将自由文本授权描述误填导致
    Think→Act 授权检查（R-AUTH-BASIS）误判。
    """

    USER_REQUEST = "user_request"
    COMPANY_POLICY = "policy"
    SYSTEM_DEFAULT = "system"
    USER_AUTHORIZATION = "user_authorization"
    VERIFIED_SESSION = "verified_session"


class ConstraintValueType(str, Enum):
    """约束值类型——argument_constraints 中每条约束的值的受控类型。

    禁止自由字符串（string）：字符串标识符（如 order_id/sku）须用 ENUM
    显式列举允许值，或用 REGEX 约束其形态。
    """

    NUMBER = "number"
    EMAIL = "email"
    ENUM = "enum"
    BOOL = "bool"


class ConstraintOperator(str, Enum):
    """约束运算符——按值类型限定可用子集。

    组合合法性由 Constraint 的 model_validator 强制：
      - number: eq/ne/ge/le/gt/lt/in/not_in/expr
      - email:  eq/ne/in/not_in/regex
      - enum:   eq/ne/in/not_in/regex/expr
      - bool:   eq/ne
    """

    EQ = "eq"
    NE = "ne"
    GE = "ge"
    LE = "le"
    GT = "gt"
    LT = "lt"
    IN = "in"
    NOT_IN = "not_in"
    REGEX = "regex"
    EXPR = "expr"


class ConstraintField(str, Enum):
    """参数约束键——全局固定有限集合。

    证书中 argument_constraints 的键必须来自此集合；新增工具参数名时须
    在此登记，从而保证键可静态枚举、可校验，而非任意自由字符串。
    """

    ORDER_ID = "order_id"
    SESSION_TOKEN = "session_token"
    SKU = "sku"
    PHONE = "phone"
    EMAIL = "email"
    REASON = "reason"
    QTY = "qty"
    CUSTOMER_ID = "customer_id"
    STATUS = "status"


# =============================================================================
# 2.0 约束键值对标准 (Constraint)
# =============================================================================

# value_type -> 允许的 operator 集合
_CONSTRAINT_OPERATOR_BY_TYPE: dict[ConstraintValueType, frozenset[ConstraintOperator]] = {
    ConstraintValueType.NUMBER: frozenset({
        ConstraintOperator.EQ,
        ConstraintOperator.NE,
        ConstraintOperator.GE,
        ConstraintOperator.LE,
        ConstraintOperator.GT,
        ConstraintOperator.LT,
        ConstraintOperator.IN,
        ConstraintOperator.NOT_IN,
        ConstraintOperator.EXPR,
    }),
    ConstraintValueType.EMAIL: frozenset({
        ConstraintOperator.EQ,
        ConstraintOperator.NE,
        ConstraintOperator.IN,
        ConstraintOperator.NOT_IN,
        ConstraintOperator.REGEX,
    }),
    ConstraintValueType.ENUM: frozenset({
        ConstraintOperator.EQ,
        ConstraintOperator.NE,
        ConstraintOperator.IN,
        ConstraintOperator.NOT_IN,
        ConstraintOperator.REGEX,
        ConstraintOperator.EXPR,
    }),
    ConstraintValueType.BOOL: frozenset({
        ConstraintOperator.EQ,
        ConstraintOperator.NE,
    }),
}


class Constraint(BaseModel):
    """一条参数约束：{key, operator, value}，其中 key 来自 ConstraintField。

    键值对标准：
      - key    ∈ ConstraintField（固定有限集合）
      - value  属于 value_type 指定的受控类型（number/email/enum/bool）
      - operator ∈ 有限集合，且须与 value_type 合法组合（见
        _CONSTRAINT_OPERATOR_BY_TYPE，由 model_validator 强制）
    """

    operator: ConstraintOperator
    value: Any
    value_type: ConstraintValueType = ConstraintValueType.ENUM
    description: str | None = Field(
        default=None,
        description="可选说明，帮助 LLM 表达约束意图（非参数值自由文本）",
    )

    @model_validator(mode="after")
    def _validate(self) -> "Constraint":
        _validate_constraint_value(self.value_type, self.value)
        allowed_ops = _CONSTRAINT_OPERATOR_BY_TYPE[self.value_type]
        if self.operator not in allowed_ops:
            raise ValueError(
                f"operator '{self.operator.value}' is not allowed for "
                f"value_type '{self.value_type.value}' (allowed: "
                f"{[o.value for o in sorted(allowed_ops, key=lambda o: o.value)]})"
            )
        return self

    def check(self, actual: Any) -> bool:
        """根据 operator 与 value_type 对实际值求值。"""
        return _eval_constraint(self.operator, self.value_type, self.value, actual)


_EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


def _validate_constraint_value(value_type: ConstraintValueType, value: Any) -> None:
    """校验 value 与其声明的 value_type 一致，错误时抛 ValueError。"""
    if value_type == ConstraintValueType.NUMBER:
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise ValueError(f"value_type 'number' requires int/float, got {type(value).__name__}")
    elif value_type == ConstraintValueType.EMAIL:
        if not isinstance(value, str) or not _EMAIL_RE.match(value):
            raise ValueError(f"value_type 'email' requires a valid email string, got {value!r}")
    elif value_type == ConstraintValueType.ENUM:
        if isinstance(value, str):
            raise ValueError(
                "value_type 'enum' requires an explicit list of allowed values, "
                f"got a bare string {value!r}"
            )
        if not isinstance(value, (list, tuple, set)) or not value:
            raise ValueError(f"value_type 'enum' requires a non-empty list, got {value!r}")
    elif value_type == ConstraintValueType.BOOL:
        if not isinstance(value, bool):
            raise ValueError(f"value_type 'bool' requires a bool, got {type(value).__name__}")
    else:
        raise ValueError(f"unknown value_type {value_type!r}")


# 沙箱 EXPR 表达式守卫：仅允许有限 AST 节点类型与内置函数，禁止属性/调用/import。
_ALLOWED_AST_NODES = (
    ast.Expression,
    ast.BoolOp,
    ast.BinOp,
    ast.UnaryOp,
    ast.Compare,
    ast.Name,
    ast.Constant,
    ast.Load,
)
_ALLOWED_AST_OPS = (
    ast.And,
    ast.Or,
    ast.Add,
    ast.Sub,
    ast.Mult,
    ast.Div,
    ast.Mod,
    ast.Eq,
    ast.NotEq,
    ast.Lt,
    ast.LtE,
    ast.Gt,
    ast.GtE,
    ast.USub,
    ast.UAdd,
    ast.Not,
)
_EXPR_FUNCS: dict[str, Any] = {
    "len": len,
    "str": str,
    "int": int,
    "float": float,
    "bool": bool,
    "abs": abs,
    "min": min,
    "max": max,
    "round": round,
}
_EXPR_MAX_LEN = 256


def _check_expr_ast(node: ast.AST) -> None:
    """递归校验表达式 AST 仅包含白名单节点/运算符。"""
    if not isinstance(node, _ALLOWED_AST_NODES):
        raise ValueError(f"disallowed AST node in constraint expr: {type(node).__name__}")
    if isinstance(node, (ast.BinOp, ast.BoolOp)):
        op = node.op
        if isinstance(node, ast.BinOp):
            allowed = (ast.Add, ast.Sub, ast.Mult, ast.Div, ast.Mod)
        else:
            allowed = (ast.And, ast.Or)
        if not isinstance(op, allowed):
            raise ValueError(f"disallowed operator in constraint expr: {type(op).__name__}")
    elif isinstance(node, ast.UnaryOp) and not isinstance(
        node.op, (ast.USub, ast.UAdd, ast.Not)
    ):
        raise ValueError(f"disallowed unary operator: {type(node.op).__name__}")
    elif isinstance(node, ast.Compare):
        for op in node.ops:
            if not isinstance(op, (ast.Eq, ast.NotEq, ast.Lt, ast.LtE, ast.Gt, ast.GtE)):
                raise ValueError(f"disallowed comparator in constraint expr: {type(op).__name__}")
        for comparator in node.comparators:
            _check_expr_ast(comparator)
        _check_expr_ast(node.left)
        return
    elif isinstance(node, ast.Name) and node.id.startswith("__"):
        raise ValueError("double-underscore names are forbidden in constraint expr")
    for child in ast.iter_child_nodes(node):
        _check_expr_ast(child)


def _eval_expr(expr: str, actual: Any) -> Any:
    """沙箱化求值毫秒级 Python 表达式，将 actual 绑定为变量 'x'。

    仅允许白名单内建与运算符，禁止属性访问、调用、导入与下划线名，
    表达式长度受限以规避超时风险。
    """
    if not isinstance(expr, str) or len(expr) > _EXPR_MAX_LEN:
        raise ValueError("constraint expr must be a short string expression")
    tree = ast.parse(expr, mode="eval")
    _check_expr_ast(tree)
    env: dict[str, Any] = dict(_EXPR_FUNCS)
    env["x"] = actual
    result = eval(compile(tree, "<constraint-expr>", "eval"), {"__builtins__": {}}, env)  # noqa: S307
    return result


def _eval_constraint(
    operator: ConstraintOperator,
    value_type: ConstraintValueType,
    expected: Any,
    actual: Any,
) -> bool:
    """根据 operator 对实际值求值，返回是否满足约束。"""
    op = operator
    if op == ConstraintOperator.IN:
        return actual in expected
    if op == ConstraintOperator.NOT_IN:
        return actual not in expected
    if op == ConstraintOperator.REGEX:
        return re.search(str(expected), str(actual)) is not None
    if op == ConstraintOperator.EXPR:
        return bool(_eval_expr(str(expected), actual))

    if op == ConstraintOperator.EQ:
        return actual == expected
    if op == ConstraintOperator.NE:
        return actual != expected

    # 数值比较：实际值必须为数值，否则视为不满足
    if not isinstance(actual, (int, float)) or isinstance(actual, bool):
        return False
    expected_num = expected
    if isinstance(expected_num, bool) or not isinstance(expected_num, (int, float)):
        return False
    if op == ConstraintOperator.GE:
        return actual >= expected_num
    if op == ConstraintOperator.LE:
        return actual <= expected_num
    if op == ConstraintOperator.GT:
        return actual > expected_num
    if op == ConstraintOperator.LT:
        return actual < expected_num
    raise ValueError(f"unsupported operator {op!r}")


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
    argument_constraints: dict[ConstraintField, Constraint] = Field(
        default_factory=dict,
        description="参数约束（键值对标准）：键来自 ConstraintField，值为 Constraint 对象。"
        "例如 {\"order_id\": {\"operator\": \"in\", \"value\": [\"O001\"], \"value_type\": \"enum\"}}",
    )
    authority_basis: AuthorityBasis = Field(
        ...,
        description="授权依据（受控词表）："
        + " / ".join(a.value for a in AuthorityBasis),
    )
    expected_effect: str = Field(..., min_length=1, description="预期效果：允许和禁止的状态变化")
    required_evidence: list[str] = Field(
        default_factory=list,
        description="执行后必须获得的回执或来源证明（如 delivery_receipt、state_diff）",
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
    intent_field: str | None = Field(
        default=None,
        description="关联的意图证书字段名（subgoal/proposed_operation/argument_constraints/"
        "authority_basis/expected_effect/required_evidence）",
    )
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
    check_type: Literal["think->act", "act->observe", "observe->think"] = Field(
        ...,
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

    entry_type: Literal["task"] = Field(default="task", frozen=True)
    task_id: str = Field(default_factory=lambda: f"task-{uuid.uuid4().hex[:12]}")
    description: str = Field(..., min_length=1, description="用户原始任务描述")
    policies: list[str] = Field(default_factory=list, description="适用的安全策略 ID 列表")
    tools_available: list[str] = Field(default_factory=list, description="可用工具名列表")
    initial_permissions: list[str] = Field(default_factory=list, description="初始权限集")
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))


class IntentEntry(BaseModel):
    """意图条目——Think 阶段提交证书后写入账本。"""

    entry_type: Literal["intent"] = Field(default="intent", frozen=True)
    certificate: IntentCertificate
    timestamp: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))


class ActionEntry(BaseModel):
    """动作条目——工具调用执行前后写入账本。"""

    entry_type: Literal["action"] = Field(default="action", frozen=True)
    action: ActionEvent
    timestamp: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))


class ObservationEntry(BaseModel):
    """观测条目——环境返回结果后写入账本。"""

    entry_type: Literal["observation"] = Field(default="observation", frozen=True)
    observation: ObservationEvent
    timestamp: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))


class ObligationEntry(BaseModel):
    """证据义务条目——跟踪未完成的证据义务。"""

    entry_type: Literal["obligation"] = Field(default="obligation", frozen=True)
    obligation_id: str = Field(default_factory=lambda: f"obl-{uuid.uuid4().hex[:12]}")
    description: str = Field(..., min_length=1)
    status: ObligationStatus = Field(default=ObligationStatus.PENDING)
    certificate_id: str = Field(default="", description="关联的意图证书 ID")
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    fulfilled_at: datetime | None = Field(default=None)


class ViolationEntry(BaseModel):
    """违规条目——检测到违规时追加写入账本。"""

    entry_type: Literal["violation"] = Field(default="violation", frozen=True)
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
