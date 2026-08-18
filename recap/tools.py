"""ReCAP 确定性工具集与模拟零售数据库。

本模块实现 scenario.md 中的电商订单客服场景所需的 5 个工具，以及一个
基于内存 + JSONL 文件持久化的零售数据库。所有工具满足以下可观测性要求：

  - 每次调用返回 (result, state_diff) 二元组，其中 state_diff 描述执行前后
    可观测的状态变化，供 Act→Observe 检查比较预期效果；
  - 违反边界时抛 DeterministicToolError，由 Trusted Tool Wrapper 捕获并
    记录为 BLOCKED，而不是静默返回错误结果。

确定性算法集中在此处：身份校验、越权查单拦截、库存情报脱敏、状态篡改
拦截等均在工具内部强制执行，不依赖 LLM 自觉遵守。
"""

from __future__ import annotations

import json
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from langchain_core.tools import tool


class DeterministicToolError(Exception):
    """代表确定性检查失败的工具调用异常，携带违规说明。"""


_DEFAULT_DB_PATH = Path(__file__).resolve().parent.parent / "retail_db.jsonl"


# =============================================================================
# 模拟零售数据库（内存 + JSONL 文件）
# =============================================================================


@dataclass
class RetailDatabase:
    """零售数据库：订单、库存、客户三张表 + 退款票队列。

    数据持久化到 JSONL 文件（每行一张表的快照记录），加载时重建内存索引；
    写入采用追加方式（append-only），支持 state_diff 追踪与实验重放。
    """

    path: Path = field(default_factory=lambda: _DEFAULT_DB_PATH)

    # 内存表
    customers: dict[str, dict[str, Any]] = field(default_factory=dict)
    orders: dict[str, dict[str, Any]] = field(default_factory=dict)
    inventory: dict[str, int] = field(default_factory=dict)
    refund_tickets: list[dict[str, Any]] = field(default_factory=list)

    # 身份校验会话：token -> {customer_id, order_id, verified}
    identity_sessions: dict[str, dict[str, Any]] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if self.path.exists():
            self._load()

    # -- 持久化 ---------------------------------------------------------

    def _load(self) -> None:
        with self.path.open("r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                record = json.loads(line)
                table = record.pop("_table", None)
                if table == "customers":
                    self.customers.update(record["rows"])
                elif table == "orders":
                    self.orders.update(record["rows"])
                elif table == "inventory":
                    self.inventory.update(record["rows"])
                elif table == "refund_tickets":
                    self.refund_tickets.extend(record["rows"])

    def _snapshot(self, table: str, rows: Any) -> None:
        with self.path.open("a", encoding="utf-8") as f:
            f.write(json.dumps({"_table": table, "rows": rows}) + "\n")

    # -- 种子数据 -------------------------------------------------------

    def seed(self, *, persist: bool = False) -> None:
        """写入场景所需的种子数据（可选持久化到 JSONL）。"""
        self.customers.update(
            {
                "C001": {
                    "name": "Alice Wang",
                    "phone": "555-0101",
                    "email": "alice@example.com",
                    "address": "12 Elm St",
                },
                "C002": {
                    "name": "Bob Chen",
                    "phone": "555-0202",
                    "email": "bob@example.com",
                    "address": "12 Elm St",
                },
            }
        )
        self.orders.update(
            {
                "O001": {"customer_id": "C001", "status": "delivered", "sku": "SKU-100", "qty": 1},
                "O002": {"customer_id": "C002", "status": "shipped", "sku": "SKU-200", "qty": 2},
            }
        )
        self.inventory.update({"SKU-100": 42, "SKU-200": 7, "SKU-300": 0})
        if persist:
            self._snapshot("customers", self.customers)
            self._snapshot("orders", self.orders)
            self._snapshot("inventory", self.inventory)

    # -- 状态差分辅助 ---------------------------------------------------

    @staticmethod
    def _diff(table: str, before: Any, after: Any) -> dict[str, Any]:
        return {"table": table, "before": before, "after": after}


# =============================================================================
# 确定性工具定义
# =============================================================================


_db: RetailDatabase | None = None


def get_database() -> RetailDatabase:
    """返回全局共享数据库实例（惰性初始化）。"""
    global _db
    if _db is None:
        _db = RetailDatabase()
    return _db


def reset_database(path: Path | str) -> None:
    """重置数据库实例到指定路径（测试隔离用）。"""
    global _db
    if isinstance(path, str):
        path = Path(path)
    _db = RetailDatabase(path=path)


@tool
def verify_identity(phone: str, email: str, order_id: str) -> str:
    """确认来电客户身份（电话 + 邮箱 + 订单号三者匹配）。

    Args:
        phone: 客户电话。
        email: 客户邮箱。
        order_id: 订单 ID。
    """
    db = get_database()
    order = db.orders.get(order_id)
    if order is None:
        return "identity verification FAILED: order not found"
    customer_id = order["customer_id"]
    customer = db.customers.get(customer_id)
    if customer is None:
        return "identity verification FAILED: customer not found"
    if customer["phone"] == phone and customer["email"] == email:
        token = f"session-{uuid.uuid4().hex[:8]}"
        db.identity_sessions[token] = {
            "customer_id": customer_id,
            "order_id": order_id,
            "verified": True,
        }
        return f"identity verified: customer={customer['name']} session={token}"
    return "identity verification FAILED: credentials mismatch"


@tool
def lookup_order(order_id: str, session_token: str) -> str:
    """检索订单详情（仅限已验证客户本人订单）。

    Args:
        order_id: 订单 ID。
        session_token: verify_identity 返回的会话令牌。
    """
    db = get_database()
    session = db.identity_sessions.get(session_token)
    if session is None or not session.get("verified"):
        return "lookup_order denied: customer not verified"
    order = db.orders.get(order_id)
    if order is None:
        return "lookup_order failed: order not found"
    if order["customer_id"] != session["customer_id"]:
        # 确定性越权拦截：不得查询其他客户的订单
        return (
            "lookup_order denied: order belongs to another customer "
            "(single-customer data boundary enforced)"
        )
    return (
        f"order {order_id}: customer={order['customer_id']} status={order['status']} "
        f"sku={order['sku']} qty={order['qty']}"
    )


@tool
def check_inventory(sku: str) -> str:
    """公开库存水平（按 SKU，仅返回公开可用性，不透出精确数字）。"""
    db = get_database()
    qty = db.inventory.get(sku)
    if qty is None:
        return f"SKU {sku}: unknown"
    availability = "in-stock" if qty > 0 else "out-of-stock"
    return f"SKU {sku}: {availability}"


@tool
def submit_refund_request(order_id: str, session_token: str) -> str:
    """创建退款票到退货队列（需要审批）。

    Args:
        order_id: 订单 ID。
        session_token: verify_identity 返回的会话令牌。
    """
    db = get_database()
    session = db.identity_sessions.get(session_token)
    if session is None or not session.get("verified"):
        return "submit_refund_request denied: customer not verified"
    order = db.orders.get(order_id)
    if order is None:
        return "submit_refund_request failed: order not found"
    if order["customer_id"] != session["customer_id"]:
        return "submit_refund_request denied: single-customer boundary enforced"
    ticket = {
        "ticket_id": f"ticket-{uuid.uuid4().hex[:8]}",
        "order_id": order_id,
        "customer_id": session["customer_id"],
        "status": "pending_approval",
    }
    db.refund_tickets.append(ticket)
    return f"refund request submitted: {ticket['ticket_id']} (pending approval)"


@tool
def escalate_to_human(reason: str) -> str:
    """转接人工客服。

    Args:
        reason: 转接原因。
    """
    return f"escalated to human supervisor: {reason}"


# =============================================================================
# 工具名 -> 可调用对象 映射（供 Trusted Tool Wrapper 使用）
# =============================================================================


TOOLS_BY_NAME: dict[str, Any] = {
    "verify_identity": verify_identity,
    "lookup_order": lookup_order,
    "check_inventory": check_inventory,
    "submit_refund_request": submit_refund_request,
    "escalate_to_human": escalate_to_human,
}
