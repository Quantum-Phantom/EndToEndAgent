"""检查 .env 配置的模型是否支持 langchain 的 tool_calls（工具调用）。

运行方式（在项目根目录）:
    .venv/Scripts/python.exe scripts/check_tool_calls.py

流程:
    1. 读取 .env 中的 BASE_URL / API_KEY / MODEL_NAME。
    2. 绑定一个最小工具，发送一条必然触发工具调用的指令。
    3. 解析响应，报告是否返回了 tool_calls，并打印原始响应便于诊断。
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from langchain_core.messages import SystemMessage, HumanMessage
from langchain_core.tools import tool
from langchain_openai import ChatOpenAI


@tool
def add(a: int, b: int) -> int:
    """两数相加。"""
    return a + b


def main() -> int:
    base_url = os.environ.get("BASE_URL")
    api_key = os.environ.get("API_KEY")
    model = os.environ.get("MODEL_NAME")
    if not base_url or not api_key or not model:
        print("缺少 .env 配置：BASE_URL / API_KEY / MODEL_NAME")
        return 1

    print(f"BASE_URL  = {base_url}")
    print(f"MODEL     = {model}")
    print(f"API_KEY   = {'***' if api_key else '(缺失)'}")

    llm = ChatOpenAI(
        base_url=base_url,
        api_key=api_key,
        model=model,
        temperature=0,
    ).bind_tools([add])

    system = SystemMessage(
        content=(
            "你是工具调用测试助手。用户请求计算时，你必须调用 add 工具，"
            "不得只输出文本。"
        )
    )
    human = HumanMessage(content="请用 add 工具计算 3 + 4。")

    print("\n--- 发起请求 ---")
    try:
        response = llm.invoke([system, human])
    except Exception as e:  # noqa: BLE001
        print(f"\n[错误] 模型调用失败: {type(e).__name__}: {e}")
        return 1

    print("\n--- 响应 ---")
    print(f"type: {type(response).__name__}")
    print(f"content: {response.content!r}")
    print(f"tool_calls: {response.tool_calls!r}")

    if response.tool_calls:
        print("\n[结果] 模型支持 tool_calls")
        for tc in response.tool_calls:
            print(f"  - name={tc.get('name')} args={tc.get('args')} id={tc.get('id')}")
        return 0

    print("\n[结果] 模型未返回 tool_calls（或响应格式不含工具调用）")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
