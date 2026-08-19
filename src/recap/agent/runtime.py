"""ReCAP 生产运行时装配。

模型配置只从进程环境或项目根目录的 .env 读取；本模块不会打印、
持久化或硬编码 API Key。
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

from dotenv import load_dotenv
from langchain_openai import ChatOpenAI

from recap.agent.graph import compile_recap_graph
from recap.ledger import InMemoryLedgerRepository, LedgerService
from recap.nodes.think import build_think_node
from recap.tools import ToolRegistry
from recap.tools.arithmetic import ARITHMETIC_TOOLS


PROJECT_ROOT = Path(__file__).resolve().parents[3]
DEFAULT_ENV_FILE = PROJECT_ROOT / ".env"


def _load_runtime_settings(env_file: str | Path | None = None) -> dict[str, Any]:
    """加载并校验模型配置；已有系统环境变量优先于 .env。"""

    dotenv_path = Path(env_file).expanduser().resolve() if env_file else DEFAULT_ENV_FILE
    load_dotenv(dotenv_path=dotenv_path, override=False)

    model = os.getenv("RECAP_MODEL") or os.getenv("MODEL_NAME")
    api_key = os.getenv("RECAP_API_KEY") or os.getenv("API_KEY")
    base_url = os.getenv("RECAP_BASE_URL") or os.getenv("BASE_URL")

    missing: list[str] = []
    if not model:
        missing.append("RECAP_MODEL（或 MODEL_NAME）")
    if not api_key:
        missing.append("RECAP_API_KEY（或 API_KEY）")

    if missing:
        location = str(dotenv_path)
        raise RuntimeError(
            f"缺少模型配置：{', '.join(missing)}。"
            f"请在环境变量或 {location} 中设置；不要把密钥写入 Python 代码。"
        )

    return {
        "model": model,
        "api_key": api_key,
        "base_url": base_url,
    }


def build_real_runtime(
    *,
    env_file: str | Path | None = None,
    temperature: float = 0.0,
    timeout_seconds: float = 60.0,
    max_retries: int = 2,
    tool_timeout_seconds: float = 30.0,
):
    """构建真实 LLM、Ledger、工具注册表和已编译 ReCAP Graph。"""

    settings = _load_runtime_settings(env_file)

    llm_kwargs: dict[str, Any] = {
        "model": settings["model"],
        "api_key": settings["api_key"],
        "temperature": temperature,
        "timeout": timeout_seconds,
        "max_retries": max_retries,
    }
    if settings["base_url"]:
        llm_kwargs["base_url"] = settings["base_url"]

    llm = ChatOpenAI(**llm_kwargs)

    ledger = LedgerService(InMemoryLedgerRepository())
    registry = ToolRegistry()
    for tool in ARITHMETIC_TOOLS:
        registry.register(tool)

    think_node = build_think_node(
        llm=llm,
        ledger=ledger,
        tools=ARITHMETIC_TOOLS,
    )

    graph = compile_recap_graph(
        think_node=think_node,
        ledger=ledger,
        registry=registry,
        tool_timeout_seconds=tool_timeout_seconds,
    )

    return graph, ledger, registry


__all__ = ["build_real_runtime"]