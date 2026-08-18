"""端到端验证：用假 LLM 驱动完整 ReCAP 图跑一轮。"""

from pathlib import Path
from tempfile import TemporaryDirectory

from langchain_core.messages import AIMessage, HumanMessage

from recap.graph import build_recap_graph, set_llm
from recap.schemas import TaskEntry
from recap.tools import TOOLS_BY_NAME, get_database, reset_database


class FakeLLM:
    def __init__(self, script):
        self.script = list(script)
    def invoke(self, messages):
        if not self.script:
            return AIMessage(content="任务完成。")
        return self.script.pop(0)


def main():
    tmp = TemporaryDirectory()
    reset_database(Path(tmp.name) / "db.jsonl")
    db = get_database()
    db.seed()

    cert_json = (
        '{"subgoal": "track verified customer order", '
        '"proposed_operation": "verify_identity", '
        '"argument_constraints": {"order_id": ["O001"]}, '
        '"authority_basis": "verified_session", '
        '"expected_effect": "identity session created", '
        '"required_evidence": []}'
    )

    ai1 = AIMessage(
        content=cert_json,
        tool_calls=[{"name": "verify_identity", "args": {"phone": "555-0101", "email": "alice@example.com", "order_id": "O001"}, "id": "tc-1"}],
    )

    llm = FakeLLM([ai1, AIMessage(content="已完成订单查询，客户已通过身份校验。")])
    set_llm(llm)

    graph = build_recap_graph().compile()

    task = TaskEntry(
        description="Help authenticated customers track orders",
        tools_available=list(TOOLS_BY_NAME),
        initial_permissions=["verify_identity", "lookup_order", "check_inventory"],
    )

    result = graph.invoke({
        "messages": [HumanMessage(content="请帮我查我的订单 O001")],
        "task_entry": task,
    })

    print("=== 最终消息 ===")
    for m in result["messages"]:
        print(type(m).__name__, "->", getattr(m, "content", "")[:80])
    print("\n=== 账本条目类型 ===")
    from collections import Counter
    print(Counter(e.entry_type for e in result.get("ledger_entries", [])))
    print("\n=== 检查结果 ===")
    for cr in result.get("check_results", []):
        print(cr.check_type, "passed=", cr.passed, "recovery=", [a.value for a in cr.recovery_actions])


if __name__ == "__main__":
    main()
