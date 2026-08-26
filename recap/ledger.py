"""ReCAP 证据检测器与 JSONL 账本存储。

本模块集中证据匹配（受控词表 + 确定性正则）与账本 JSONL 追加持久化，
数据模式见 recap.schemas。账本只增不改，证据义务按 obligation_id 去重取
最新状态。

与 scenarios 中的数据库保持相同的 JSONL 追加风格：
每行一个 JSON 对象，携带已序列化的账本条目。
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from recap.schemas import (
    LedgerEntry,
    ObligationEntry,
    ObligationStatus,
)


class EvidenceDetector:
    """确定性证据检测器：来源工具 + 正则共同判定。

    当 action.tool_name 与 source_tool 一致，且 Observation 文本与 pattern
    匹配时，evidence 才被视为收集到。
    """

    def __init__(self, source_tool: str, pattern: str) -> None:
        self.source_tool = source_tool
        self.pattern = re.compile(pattern)

    def extract(self, observation: Any) -> str | None:
        """在观测文本中匹配，命中则返回捕获组或整体匹配文本，否则返回 None。"""
        text = str(observation or "")
        m = self.pattern.search(text)
        if m is None:
            return None
        return m.group(1) if m.groups() else m.group(0)


def evidence_sources(detectors: dict[str, EvidenceDetector]) -> dict[str, list[str]]:
    """返回 tool_name -> 可产出证据类型列表 的映射。

    供 think_node 注入提示词与 think_act_check_node 做证据可行性检查：
    required_evidence 中声明的证据必须能由 proposed_operation 产出。
    """
    sources: dict[str, list[str]] = {}
    for evidence_name, detector in detectors.items():
        sources.setdefault(detector.source_tool, []).append(evidence_name)
    return sources


def source_tool_for(evidence_name: str, detectors: dict[str, EvidenceDetector]) -> str:
    """返回能产出指定证据类型的来源工具名（未注册时返回空字符串）。"""
    detector = detectors.get(evidence_name)
    return detector.source_tool if detector is not None else ""


def collect_evidence(
    action: Any,
    observation: Any,
    detectors: dict[str, EvidenceDetector],
) -> dict[str, str]:
    """检测给定动作对应的观测中收集到的证据。

    仅当 action 的工具名与检测器声明的 source_tool 一致时才尝试匹配。
    返回键为证据名、值为提取的字符串/整体匹配文本的字典。
    """
    collected: dict[str, str] = {}
    tool_name = getattr(action, "tool_name", "")
    for evidence_name, detector in detectors.items():
        if detector.source_tool != tool_name:
            continue
        value = detector.extract(observation)
        if value is not None:
            collected[evidence_name] = value
    return collected


class LedgerStore:
    """只增账本：内存列表 + JSONL 文件追加持久化。

    每个 Append 立即写入 JSONL 一行（含 entry_type 判别字段）。
    """

    def __init__(self, path: Path | str) -> None:
        self.path = Path(path)
        self.entries: list[LedgerEntry] = []
        if self.path.exists():
            self._load()

    def _load(self) -> None:
        with self.path.open("r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                record = json.loads(line)
                entry_type = record.pop("entry_type", None)
                self._append_by_type(entry_type, record)

    def _append_by_type(self, entry_type: str | None, record: dict[str, Any]) -> None:
        # 延迟导入避免循环
        from recap import schemas

        cls_map = {
            "task": schemas.TaskEntry,
            "intent": schemas.IntentEntry,
            "action": schemas.ActionEntry,
            "observation": schemas.ObservationEntry,
            "obligation": schemas.ObligationEntry,
            "violation": schemas.ViolationEntry,
            "repair": schemas.RepairEntry,
            "replan": schemas.ReplanEntry,
        }
        cls = cls_map.get(entry_type)
        if cls is not None:
            try:
                self.entries.append(cls.model_validate(record))
            except Exception:
                pass

    def append(self, entry: LedgerEntry) -> None:
        """追加到内存并持久化到 JSONL 文件的一行。"""
        self.entries.append(entry)
        record = entry.model_dump(mode="json")
        record["entry_type"] = record.get("entry_type", entry.entry_type) or entry.entry_type
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(record, ensure_ascii=False) + "\n")

    def open_obligations(self) -> list[ObligationEntry]:
        """按 obligation_id 去重取最新状态，返回仍未完成的义务。"""
        latest: dict[str, ObligationEntry] = {}
        for e in self.entries:
            if e.entry_type != "obligation":
                continue
            if not isinstance(e, ObligationEntry):
                continue
            latest[e.obligation_id] = e
        return [e for e in latest.values() if e.status == ObligationStatus.PENDING]


_DEFAULT_LEDGER_PATH = Path(__file__).resolve().parent.parent / "ledger.jsonl"

_store: LedgerStore | None = None


def get_ledger_store() -> LedgerStore:
    """返回全局共享账本实例（惰性初始化）。"""
    global _store
    if _store is None:
        _store = LedgerStore(_DEFAULT_LEDGER_PATH)
    return _store


def reset_ledger_store(path: Path | str | None = None) -> None:
    """重置账本实例到指定路径（测试隔离用）。"""
    global _store
    if isinstance(path, str):
        path = Path(path)
    _store = LedgerStore(path or _DEFAULT_LEDGER_PATH)
