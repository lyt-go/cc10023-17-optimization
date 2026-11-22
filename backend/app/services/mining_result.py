"""挖掘结果管理领域服务：分析结果、洞察发现与关键发现的管理。

归并口径（核心）：
- 仅在同一「分析主题」内识别等价复跑；
- 等价键 = 模型指标口径 + 关键分群 + 覆盖人群 + 建议动作（均做规范化）；
- 等价复跑归为同一洞察，纳入所有原始编号与分析批次，覆盖人群取复跑间一致的
  集合，绝不借归并扩大适用人群；
- 覆盖不同或建议动作不同的结果保持独立；
- 同一份结果重复提交只更新归并来源、不新增归并组，规则下发的幂等在
  ``scenario_label`` 服务里保证。
"""
from __future__ import annotations

import datetime
from typing import Any

from app.insight_store import insight_store
from app.services.base import BaseModuleService
from app.store import store


def _today() -> str:
    return datetime.date.today().isoformat()


def normalize_text(raw: Any) -> str:
    """规范化文本字段：去空白、统一中英文逗号，便于口径比较。"""
    return " ".join(str(raw or "").replace("，", ",").split()).strip()


def normalize_group(raw: Any) -> frozenset[str]:
    """把逗号分隔的人群/分群字段规范化为无序集合，避免顺序差异破坏等价判断。"""
    text = normalize_text(raw)
    if not text:
        return frozenset()
    return frozenset(part.strip() for part in text.split(",") if part.strip())


class MiningResultService(BaseModuleService):
    MODULE = "mining_result"
    ENTITY = "挖掘结果"
    OPERATION_TERM = "存储"
    LIST_FIELDS = [
        "结果编号", "分析主题", "算法类型", "模型指标口径", "洞察摘要", "关键分群",
        "覆盖人群", "建议动作", "置信度", "分析批次", "结果文件", "结果版本",
    ]
    REQUIRED_FIELDS = ["结果编号", "分析主题", "算法类型"]
    STATUS_ORDER = ["草稿", "审核中", "已采纳", "已搁置"]
    ACTION_RULES = {"提交审核": "审核中", "确认采纳": "已采纳", "标记搁置": "已搁置"}
    NEGATIVE_ACTIONS = []

    # 归并判等所需的业务字段
    MERGE_REQUIRED_FIELDS = ["分析主题", "模型指标口径", "关键分群", "覆盖人群", "建议动作", "分析批次"]

    # ── 链路4: 预测 → 挖掘结果 ──
    def export_insights_to_labels(self, result_id: int) -> dict[str, Any]:
        """将挖掘结果的关键发现导出到业务场景标注模块，形成可复用的场景规则。"""
        result = store.find(self.MODULE, result_id)
        if result is None:
            return {"ok": False}
        label_rows = store.rows("scenario_label")
        next_id = max((int(r.get("id", 0)) for r in label_rows), default=0) + 1
        result["status"] = "已采纳"
        result["pending"] = False
        label_rows.append({
            "id": next_id,
            "status": "草稿",
            "pending": True,
            "abnormal": False,
            "标注编号": f"LABEL-{next_id:04d}",
            "场景名称": f"挖掘场景_{result.get('分析主题', 'unknown')}",
            "关联特征": result.get("关键分群", ""),
            "业务规则": result.get("洞察摘要", ""),
            "标注时间": _today(),
            "标注人": "system_auto",
            "标注状态": "草稿",
            "场景版本": "v1.0.0",
            "规则来源": "挖掘结果直接导出",
            "原始洞察编号": result.get("结果编号", ""),
            "原始预测批次": result.get("分析批次", ""),
            "来源分群规则": result.get("关键分群", ""),
        })
        return {"ok": True, "result_id": result_id, "label_id": next_id}

    def aggregate_cluster_insights(self, result_id: int) -> dict[str, Any]:
        """聚合挖掘结果中关键分群的洞察数据并关联到聚类分析配置。"""
        result = store.find(self.MODULE, result_id)
        if result is None:
            return {"ok": False}
        cluster_rows = store.rows("cluster_config")
        related_clusters = [
            r.get("id") for r in cluster_rows
            if result.get("关键分群", "") and any(g.strip() in str(r.get("特征集", ""))
            for g in result.get("关键分群", "").split(","))
        ]
        return {
            "ok": True,
            "result_id": result_id,
            "clusters": result.get("关键分群", "").split(","),
            "related_cluster_configs": related_clusters,
        }

    def convert_insight_to_feature_source(self, result_id: int) -> dict[str, Any]:
        """将采纳的挖掘洞察转换为新的特征数据源，丰富下游模型的特征输入。"""
        result = store.find(self.MODULE, result_id)
        if result is None:
            return {"ok": False}
        source_rows = store.rows("feature_source")
        next_id = max((int(r.get("id", 0)) for r in source_rows), default=0) + 1
        source_rows.append({
            "id": next_id,
            "status": "草稿",
            "pending": True,
            "abnormal": False,
            "源编号": f"SRC-R{result_id:04d}",
            "源名称": f"洞察衍生特征_{result.get('分析主题', '')}",
            "数据表名": f"insight_derived_{result_id}",
            "连接配置": "hive_metastore",
            "字段清单": "insight_score,cluster_label,confidence",
            "采样比例": "0.5",
            "缓存策略": "memory_1h",
            "源版本": "v1.0.0",
        })
        return {"ok": True, "source_id": next_id, "result_id": result_id}

    # ── 洞察归并 ──
    def equivalence_key(self, row: dict[str, Any]) -> tuple[Any, ...]:
        """同主题下的等价复跑判等键：口径、分群、覆盖人群与建议动作全部一致。"""
        return (
            normalize_text(row.get("分析主题")),
            normalize_text(row.get("模型指标口径")),
            normalize_group(row.get("关键分群")),
            normalize_group(row.get("覆盖人群")),
            normalize_text(row.get("建议动作")),
        )

    @staticmethod
    def _source_snapshot(row: dict[str, Any]) -> dict[str, Any]:
        return {
            "result_id": row.get("id"),
            "结果编号": row.get("结果编号"),
            "分析批次": row.get("分析批次"),
            "关键分群": sorted(normalize_group(row.get("关键分群"))),
            "覆盖人群": sorted(normalize_group(row.get("覆盖人群"))),
            "建议动作": normalize_text(row.get("建议动作")),
            "置信度": row.get("置信度"),
            "结果版本": row.get("结果版本"),
        }

    def _build_merge(self, key: tuple[Any, ...], row: dict[str, Any]) -> dict[str, Any]:
        merge_no = insight_store.next_merge_no()
        confidence = self._as_float(row.get("置信度"))
        merge = {
            "归并编号": merge_no,
            "分析主题": normalize_text(row.get("分析主题")),
            "模型指标口径": normalize_text(row.get("模型指标口径")),
            "关键分群": sorted(key[2]),
            # 等价复跑覆盖一致，取共同集合；不同覆盖不会进入同一归并组
            "覆盖人群": sorted(key[3]),
            "建议动作": key[4],
            "置信度": confidence,
            "置信度明细": [{"结果编号": row.get("结果编号"), "置信度": confidence}],
            "来源结果": [self._source_snapshot(row)],
            "来源编号": [row.get("结果编号")],
            "分析批次": [row.get("分析批次")],
            "规则编号": None,
            "标注编号": None,
            "下发状态": "未下发",
            "创建时间": _today(),
            "更新时间": _today(),
        }
        insight_store.merges[merge_no] = merge
        insight_store.merge_key_index[key] = merge_no
        return merge

    @staticmethod
    def _as_float(value: Any) -> float | None:
        try:
            return round(float(value), 4)
        except (TypeError, ValueError):
            return None

    def _append_receipt(self, row: dict[str, Any] | None, result_id: int,
                        conclusion: str, merge_no: str | None,
                        peer_codes: list[str], detail: str) -> dict[str, Any]:
        receipt = {
            "回执编号": insight_store.next_receipt_no(),
            "result_id": result_id,
            "结果编号": (row or {}).get("结果编号", f"ID-{result_id}"),
            "分析批次": (row or {}).get("分析批次"),
            "处理结论": conclusion,
            "归并编号": merge_no,
            "同洞察来源编号": peer_codes,
            "说明": detail,
            "处理时间": _today(),
        }
        insight_store.receipts.append(receipt)
        return receipt

    def merge_topic_insights(self, result_ids: list[int]) -> dict[str, Any]:
        """批量归并同主题挖掘结果，返回每份来源各自的处理回执。

        排序后按等价键归并，保证任意输入顺序下分组结果一致；重复提交不会
        新建归并组，只补充来源与回执。
        """
        ordered_ids = sorted({int(rid) for rid in result_ids})
        receipts: list[dict[str, Any]] = []
        touched_merge_nos: list[str] = []
        not_found: list[int] = []
        invalid: list[dict[str, Any]] = []

        for rid in ordered_ids:
            row = store.find(self.MODULE, rid)
            if row is None:
                not_found.append(rid)
                receipts.append(self._append_receipt(
                    None, rid, "未找到", None, [], "挖掘结果不存在或已归档，未参与归并"))
                continue

            missing = [f for f in self.MERGE_REQUIRED_FIELDS if not normalize_text(row.get(f))]
            if missing:
                invalid.append({"result_id": rid, "缺少字段": missing})
                receipts.append(self._append_receipt(
                    row, rid, "字段缺失", None, [], f"缺少归并必需字段：{'、'.join(missing)}"))
                continue

            key = self.equivalence_key(row)
            merge_no = insight_store.merge_key_index.get(key)
            if merge_no is None:
                merge = self._build_merge(key, row)
                merge_no = merge["归并编号"]
                # 同批次内是否还有等价复跑，在全部处理完后再回填结论
                conclusion = "保持独立"
                detail = "同主题下未发现口径、分群、覆盖与动作完全一致的复跑，作为独立洞察保留"
            else:
                merge = insight_store.merges[merge_no]
                conclusion = "归并为等价复跑"
                detail = f"与归并 {merge_no} 内已有结果的主题、口径、分群、覆盖人群和建议动作一致"
                self._absorb_into_merge(merge, row)

            if merge_no not in touched_merge_nos:
                touched_merge_nos.append(merge_no)

            # 处置结果回写到挖掘结果（标注详情在下发/应用环节继续回写）
            row["归并编号"] = merge_no
            row["归并状态"] = "已归并"
            row["归并时间"] = _today()
            row["归并来源批次"] = merge["分析批次"]

            peer_codes = [code for code in merge["来源编号"] if code != row.get("结果编号")]
            receipts.append(self._append_receipt(
                row, rid, conclusion, merge_no, peer_codes, detail))

        # 同批次内首批建立归并组的结果，在组内出现等价复跑后同样标记为「归并为等价复跑」
        for receipt in receipts:
            merge_no = receipt["归并编号"]
            if merge_no and merge_no in insight_store.merges:
                merge = insight_store.merges[merge_no]
                if len(merge["来源结果"]) > 1 and receipt["处理结论"] == "保持独立":
                    receipt["处理结论"] = "归并为等价复跑"
                    receipt["说明"] = (
                        f"与同批次其他结果构成归并 {merge_no}，主题、口径、分群、"
                        "覆盖人群和建议动作一致，作为等价复跑保留全部出处"
                    )
                    receipt["同洞察来源编号"] = [
                        code for code in merge["来源编号"]
                        if code != receipt["结果编号"]
                    ]

        groups, independent = [], []
        for merge_no in touched_merge_nos:
            merge = insight_store.merges[merge_no]
            summary = self._merge_summary(merge)
            if len(merge["来源结果"]) > 1:
                groups.append(summary)
            else:
                independent.append(summary)

        return {
            "ok": True,
            "提交数量": len(ordered_ids),
            "归并组数": len(groups),
            "独立洞察数": len(independent),
            "归并组": groups,
            "独立洞察": independent,
            "回执": receipts,
            "未找到": not_found,
            "字段缺失": invalid,
        }

    def _absorb_into_merge(self, merge: dict[str, Any], row: dict[str, Any]) -> None:
        """把等价复跑吸收进已有归并组；重复来源只刷新明细，不重复计数。"""
        existing_ids = {src["result_id"] for src in merge["来源结果"]}
        if row.get("id") in existing_ids:
            # 重复提交：更新该来源的快照，不新增来源编号/批次
            for idx, src in enumerate(merge["来源结果"]):
                if src["result_id"] == row.get("id"):
                    merge["来源结果"][idx] = self._source_snapshot(row)
                    break
        else:
            merge["来源结果"].append(self._source_snapshot(row))
            merge["来源编号"].append(row.get("结果编号"))
            if row.get("分析批次") not in merge["分析批次"]:
                merge["分析批次"].append(row.get("分析批次"))

        confidence = self._as_float(row.get("置信度"))
        merge["置信度明细"] = [
            item for item in merge["置信度明细"] if item["结果编号"] != row.get("结果编号")
        ]
        merge["置信度明细"].append({"结果编号": row.get("结果编号"), "置信度": confidence})
        values = [item["置信度"] for item in merge["置信度明细"] if item["置信度"] is not None]
        merge["置信度"] = max(values) if values else None
        merge["更新时间"] = _today()

    @staticmethod
    def _merge_summary(merge: dict[str, Any]) -> dict[str, Any]:
        return {
            "归并编号": merge["归并编号"],
            "分析主题": merge["分析主题"],
            "模型指标口径": merge["模型指标口径"],
            "关键分群": merge["关键分群"],
            "覆盖人群": merge["覆盖人群"],
            "建议动作": merge["建议动作"],
            "置信度": merge["置信度"],
            "来源编号": merge["来源编号"],
            "分析批次": merge["分析批次"],
            "来源数量": len(merge["来源结果"]),
            "规则编号": merge["规则编号"],
            "下发状态": merge["下发状态"],
        }

    def list_merged_insights(self, topic: str | None = None) -> dict[str, Any]:
        """列出全部归并洞察，可按分析主题过滤，供按人群应用前核对。"""
        items = [
            self._merge_summary(merge) for merge in insight_store.merges.values()
            if not topic or topic.casefold() in merge["分析主题"].casefold()
        ]
        return {"ok": True, "items": items, "total": len(items)}

    def get_merge_receipts(self, result_ids: list[int] | None = None) -> dict[str, Any]:
        """查询处理回执；不传编号时返回全部回执。"""
        wanted = {int(rid) for rid in (result_ids or [])}
        receipts = [
            receipt for receipt in insight_store.receipts
            if not wanted or receipt["result_id"] in wanted
        ]
        return {"ok": True, "items": receipts, "total": len(receipts)}

    def deduplicate_mining_results(self, result_ids: list[int]) -> dict[str, Any]:
        """兼容旧入口：按等价复跑口径执行归并，替代原先仅按主题去重的粗粒度逻辑。"""
        outcome = self.merge_topic_insights(result_ids)
        duplicates = [
            {"id": src["result_id"], "similar_to": summary["归并编号"]}
            for summary in outcome["归并组"]
            for src in insight_store.merges[summary["归并编号"]]["来源结果"][1:]
        ]
        outcome.update({
            "unique_count": outcome["归并组数"] + outcome["独立洞察数"],
            "duplicate_count": len(duplicates),
            "duplicates": duplicates,
        })
        return outcome
