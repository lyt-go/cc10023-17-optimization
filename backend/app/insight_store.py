"""洞察归并专用内存仓库。

与通用 ``store`` 分开，承载归并洞察、处理回执、已发布分群规则与规则应用
（含冲突暂缓）四类数据。真实项目会替换为数据库表，当前只依赖标准库。
"""
from __future__ import annotations

from typing import Any


class InsightStore:
    def __init__(self) -> None:
        # 归并编号 -> 归并洞察
        self.merges: dict[str, dict[str, Any]] = {}
        # 等价键 -> 归并编号，保证换输入顺序得到稳定的归并结果
        self.merge_key_index: dict[tuple[Any, ...], str] = {}
        # 全部处理回执（每次提交都留痕，重复提交也可追溯）
        self.receipts: list[dict[str, Any]] = []
        # 规则编号 -> 已发布分群规则
        self.rules: dict[str, dict[str, Any]] = {}
        # 处置对象 -> 最新一次规则应用记录
        self.applications: dict[str, dict[str, Any]] = {}
        # 规则应用历史，便于刷新后仍能追溯每次处置
        self.application_history: list[dict[str, Any]] = []
        self.merge_seq = 0
        self.receipt_seq = 0
        self.rule_seq = 0
        self.apply_seq = 0

    # ── 编号分配 ──
    def next_merge_no(self) -> str:
        self.merge_seq += 1
        return f"MERGE-{self.merge_seq:04d}"

    def next_receipt_no(self) -> str:
        self.receipt_seq += 1
        return f"RCPT-{self.receipt_seq:06d}"

    def next_rule_no(self) -> str:
        self.rule_seq += 1
        return f"RULE-{self.rule_seq:04d}"

    def next_apply_no(self) -> str:
        self.apply_seq += 1
        return f"APPLY-{self.apply_seq:04d}"

    def reset(self) -> None:
        self.__init__()


insight_store = InsightStore()
