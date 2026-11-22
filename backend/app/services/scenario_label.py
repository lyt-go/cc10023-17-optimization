"""业务场景标注领域服务：数据分析场景的业务标注、规则表达与知识管理。

归并洞察下发与按人群应用的规则生命周期集中在本服务：
- 归并结果幂等下发为分群规则（重复提交不新增规则/标注）；
- 应用时同一对象命中互斥动作则进入「冲突暂缓」，不自动执行任何一方，
  记录采用的优先级与暂缓依据，等待人工裁决；
- 刷新只更新既有规则内容与优先级，不新增规则；
- 历史规则没有归并来源的，按当时来源保留原始洞察与预测数据，刷新不触碰。
"""
from __future__ import annotations

import datetime
from typing import Any

from app.insight_store import insight_store
from app.services.base import BaseModuleService
from app.store import store


class ScenarioLabelService(BaseModuleService):
    MODULE = "scenario_label"
    ENTITY = "场景标注"
    OPERATION_TERM = "关联"
    LIST_FIELDS = ["标注编号", "场景名称", "关联特征", "业务规则", "标注时间", "标注人", "标注状态", "场景版本"]
    REQUIRED_FIELDS = ["标注编号", "场景名称", "关联特征"]
    STATUS_ORDER = ["草稿", "待校验", "已生效", "已废弃"]
    ACTION_RULES = {"提交校验": "待校验", "标记生效": "已生效", "标记废弃": "已废弃"}
    NEGATIVE_ACTIONS = []

    # ── 链路6: 聚类配置 → 分群 → 场景标注 ──
    def validate_label_rules(self, label_id: int) -> dict[str, Any]:
        """校验场景标注的业务规则合法性，检查关联特征是否存在且可查询。"""
        label = store.find(self.MODULE, label_id)
        if label is None:
            return {"ok": False}
        feats = label.get("关联特征", "")
        define_rows = store.rows("feature_define")
        matched = [r for r in define_rows if r.get("特征名称", "") in feats.split(",")]
        if len(matched) == 0:
            label["status"] = "待校验"
            label["abnormal"] = True
            return {"ok": False, "reason": "关联特征均不存在于特征定义中"}
        label["status"] = "已生效"
        label["abnormal"] = False
        return {"ok": True, "label_id": label_id, "matched_features": len(matched)}

    def link_to_cluster_segments(self, label_id: int) -> dict[str, Any]:
        """将场景标注与聚类分群关联，形成聚类→分群→标注的完整链路。"""
        label = store.find(self.MODULE, label_id)
        if label is None:
            return {"ok": False}
        cluster_rows = store.rows("cluster_config")
        related = []
        for c in cluster_rows:
            if c.get("特征集") and label.get("关联特征") and any(
                f.strip() in str(c.get("特征集", "")) for f in str(label.get("关联特征", "")).split(",")
            ):
                related.append({"cluster_id": c.get("id"), "algorithm": c.get("算法类型")})
        return {"ok": True, "label_id": label_id, "related_clusters": related, "count": len(related)}

    def export_label_knowledge_base(self) -> dict[str, Any]:
        """导出全量场景标注知识库，按业务主题聚合后供给外部系统查询。"""
        rows = store.rows(self.MODULE)
        by_status = {}
        for r in rows:
            st = r.get("status", "unknown")
            if st not in by_status:
                by_status[st] = []
            by_status[st].append({
                "id": r.get("id"),
                "scene": r.get("场景名称"),
                "features": r.get("关联特征"),
                "rules": r.get("业务规则"),
                "annotator": r.get("标注人"),
            })
        return {"ok": True, "total_labels": len(rows), "by_status": by_status}

    def standardize_label_format(self, label_id: int) -> dict[str, Any]:
        """标准化场景标注的格式，确保规则表达符合统一的标注规范。"""
        label = store.find(self.MODULE, label_id)
        if label is None:
            return {"ok": False}
        raw_rules = label.get("业务规则", "")
        standardized = raw_rules.replace(" ", "_").replace("，", ",").lower()
        label["业务规则"] = standardized
        return {"ok": True, "label_id": label_id, "raw": raw_rules, "standardized": standardized}

    # ── 归并洞察 → 分群规则下发 ──
    @staticmethod
    def _rule_text(merge: dict[str, Any]) -> str:
        return f"{'/'.join(merge['关键分群'])} -> {merge['建议动作']}"

    def _next_label_id(self) -> int:
        return max((int(r.get("id", 0)) for r in store.rows(self.MODULE)), default=0) + 1

    def _next_label_code(self) -> str:
        existing = [r.get("标注编号", "") for r in store.rows(self.MODULE)]
        nums = [int(code.split("-")[1]) for code in existing if code.startswith("SCEN-") and code.split("-")[1].isdigit()]
        return f"SCEN-{max(nums, default=0) + 1:04d}"

    def publish_merge_rules(self, merge_nos: list[str] | None = None) -> dict[str, Any]:
        """把归并洞察下发为分群规则并生成场景标注。

        幂等：已下发过的归并只返回既有规则，不重复创建规则或标注，
        因此同一份归并重复提交不会多写规则。
        """
        targets = merge_nos or list(insight_store.merges.keys())
        created, reused, missing = [], [], []
        for merge_no in targets:
            merge = insight_store.merges.get(merge_no)
            if merge is None:
                missing.append(merge_no)
                continue

            if merge.get("规则编号") and merge["规则编号"] in insight_store.rules:
                reused.append(merge["规则编号"])
                continue

            rule_no = insight_store.next_rule_no()
            label_id = self._next_label_id()
            label_code = self._next_label_code()
            rule_text = self._rule_text(merge)
            rule = {
                "规则编号": rule_no,
                "归并编号": merge_no,
                "分析主题": merge["分析主题"],
                "分群规则": rule_text,
                "关键分群": list(merge["关键分群"]),
                "适用人群": list(merge["覆盖人群"]),
                "建议动作": merge["建议动作"],
                "置信度": merge["置信度"],
                "优先级": None,
                "状态": "生效中",
                "标注编号": label_code,
                "标注id": label_id,
                "来源编号": list(merge["来源编号"]),
                "分析批次": list(merge["分析批次"]),
                "来源快照": [dict(src) for src in merge["来源结果"]],
                "发布时间": merge["更新时间"],
                "刷新次数": 0,
                "刷新版本": "v1.0.0",
            }
            insight_store.rules[rule_no] = rule

            store.rows(self.MODULE).append({
                "id": label_id,
                "status": "已生效",
                "pending": False,
                "abnormal": False,
                "标注编号": label_code,
                "场景名称": f"{merge['分析主题']}_{merge['建议动作']}",
                "关联特征": ",".join(merge["关键分群"]),
                "业务规则": rule_text,
                "标注时间": merge["更新时间"],
                "标注人": "system_merge",
                "标注状态": "已生效",
                "场景版本": "v1.0.0",
                "规则来源": "洞察归并下发",
                "归并编号": merge_no,
                "规则编号": rule_no,
                "原始洞察编号": ",".join(merge["来源编号"]),
                "原始预测批次": ",".join(merge["分析批次"]),
                "来源分群规则": rule_text,
                "处置回写": [],
            })

            merge["规则编号"] = rule_no
            merge["标注编号"] = label_code
            merge["下发状态"] = "已下发"
            # 处置信息同步回写到每份原始挖掘结果
            for source in merge["来源结果"]:
                row = store.find("mining_result", source["result_id"])
                if row is not None:
                    row["规则编号"] = rule_no
                    row["标注编号"] = label_code
                    row["下发状态"] = "已下发"
            created.append(rule_no)

        self._recompute_priorities()
        return {
            "ok": True,
            "新建规则": created,
            "复用规则": reused,
            "缺失归并": missing,
            "规则总数": len(insight_store.rules),
        }

    def _recompute_priorities(self) -> None:
        """优先级：置信度高者优先，同置信度按规则编号升序，保证结果稳定可复核。"""
        active = [rule for rule in insight_store.rules.values() if rule["状态"] == "生效中"]
        active.sort(key=lambda r: (-(r["置信度"] or 0.0), r["规则编号"]))
        for index, rule in enumerate(active, start=1):
            rule["优先级"] = index

    def list_published_rules(self) -> dict[str, Any]:
        return {
            "ok": True,
            "items": [dict(rule) for rule in insight_store.rules.values()],
            "total": len(insight_store.rules),
        }

    # ── 按人群应用规则与冲突处置 ──
    def _matched_rules(self, subject: str) -> list[dict[str, Any]]:
        return [
            rule for rule in insight_store.rules.values()
            if rule["状态"] == "生效中" and subject in rule["适用人群"]
        ]

    @staticmethod
    def _rule_ref(rule: dict[str, Any]) -> dict[str, Any]:
        """规则引用快照：即使后续刷新规则，历史处置仍能追到原始批次。"""
        return {
            "规则编号": rule["规则编号"],
            "归并编号": rule["归并编号"],
            "分群规则": rule["分群规则"],
            "建议动作": rule["建议动作"],
            "置信度": rule["置信度"],
            "优先级": rule["优先级"],
            "适用人群": list(rule["适用人群"]),
            "来源编号": list(rule["来源编号"]),
            "分析批次": list(rule["分析批次"]),
        }

    def _write_back_label(self, rule: dict[str, Any], record: dict[str, Any]) -> None:
        label = store.find(self.MODULE, rule["标注id"])
        if label is None:
            return
        label.setdefault("处置回写", []).append({
            "处置对象": record["处置对象"],
            "处置状态": record["处置状态"],
            "采用动作": record.get("采用动作"),
            "命中规则编号": [item["规则编号"] for item in record["命中规则"]],
            "原始预测批次": [batch for item in record["命中规则"] for batch in item["分析批次"]],
            "暂缓依据": record.get("暂缓依据"),
            "应用编号": record["应用编号"],
            "处理时间": record["处理时间"],
        })

    def apply_published_rules(self, subject: str) -> dict[str, Any]:
        """对单个处置对象应用全部命中的有效分群规则。

        命中多个互斥动作时不执行任何动作，置为「冲突暂缓」并写明优先级与
        暂缓依据；同一对象重复应用产生新的应用记录，但处置结论保持稳定。
        """
        matched = sorted(self._matched_rules(subject), key=lambda r: r["优先级"] or 9999)
        apply_no = insight_store.next_apply_no()
        record: dict[str, Any] = {
            "应用编号": apply_no,
            "处置对象": subject,
            "处理时间": datetime.date.today().isoformat(),
            "命中规则": [self._rule_ref(rule) for rule in matched],
            "应用次数": 1,
        }

        if not matched:
            record.update({"处置状态": "未命中", "采用动作": None, "采用规则": [],
                           "说明": "没有适用于该对象的有效规则"})
        else:
            actions = sorted({rule["建议动作"] for rule in matched})
            if len(actions) == 1:
                rule = matched[0]
                record.update({
                    "处置状态": "已执行",
                    "采用动作": rule["建议动作"],
                    "采用规则": [item["规则编号"] for item in record["命中规则"]],
                    "说明": "命中规则动作一致，按适用规则处理",
                })
            else:
                top = matched[0]
                record.update({
                    "处置状态": "冲突暂缓",
                    "采用动作": None,
                    "采用规则": [],
                    "冲突动作": actions,
                    "建议优先规则": top["规则编号"],
                    "优先级依据": (
                        f"按置信度降序、规则编号升序确定优先级；{top['规则编号']}"
                        f"（置信度 {top['置信度']}，动作 {top['建议动作']}）当前优先级最高"
                    ),
                    "暂缓依据": (
                        "同一对象一次应用命中多个有效规则且建议动作互斥，"
                        "不得在一次应用中执行互斥动作，暂缓执行并转人工裁决"
                    ),
                })

        for rule in matched:
            self._write_back_label(rule, record)

        previous = insight_store.applications.get(subject)
        if previous is not None:
            record["应用次数"] = previous["应用次数"] + 1
        insight_store.applications[subject] = record
        insight_store.application_history.append(dict(record))
        return {"ok": True, "application": dict(record)}

    def resolve_rule_conflict(self, subject: str, rule_no: str,
                              reason: str, operator: str = "analyst") -> dict[str, Any]:
        """对冲突暂缓的处置进行人工裁决，采用其中一条有效规则的动作。"""
        record = insight_store.applications.get(subject)
        if record is None or record["处置状态"] != "冲突暂缓":
            return {"ok": False, "message": f"{subject} 当前不存在待裁决的规则冲突"}
        chosen = next((r for r in record["命中规则"] if r["规则编号"] == rule_no), None)
        active = insight_store.rules.get(rule_no)
        if chosen is None or active is None or active["状态"] != "生效中":
            return {"ok": False, "message": f"规则 {rule_no} 不在 {subject} 的冲突规则范围内或已失效"}

        record.update({
            "处置状态": "冲突裁决-已执行",
            "采用动作": active["建议动作"],
            "采用规则": [rule_no],
            "裁决人": operator,
            "裁决依据": reason,
            "裁决时间": datetime.date.today().isoformat(),
        })
        insight_store.application_history.append(dict(record))
        self._write_back_label(active, record)
        return {"ok": True, "application": dict(record)}

    def refresh_published_rules(self) -> dict[str, Any]:
        """按当前归并结果刷新已发布规则。

        只更新既有规则的适用人群、动作、置信度、来源快照与优先级，
        不新增规则、不新增标注；无归并来源的历史规则原样保留。
        """
        before = len(insight_store.rules)
        refreshed = []
        for rule in insight_store.rules.values():
            merge = insight_store.merges.get(rule["归并编号"])
            if merge is None:
                continue
            rule["适用人群"] = list(merge["覆盖人群"])
            rule["建议动作"] = merge["建议动作"]
            rule["置信度"] = merge["置信度"]
            rule["关键分群"] = list(merge["关键分群"])
            rule["分群规则"] = self._rule_text(merge)
            rule["来源编号"] = list(merge["来源编号"])
            rule["分析批次"] = list(merge["分析批次"])
            rule["来源快照"] = [dict(src) for src in merge["来源结果"]]
            rule["刷新次数"] += 1
            raw_version = rule["刷新版本"].lstrip("v")
            major, minor, patch = (raw_version.split(".") + ["0", "0", "0"])[:3]
            rule["刷新版本"] = f"v{major}.{minor}.{int(patch) + 1}"
            rule["刷新时间"] = datetime.date.today().isoformat()

            label = store.find(self.MODULE, rule["标注id"])
            if label is not None:
                label["业务规则"] = rule["分群规则"]
                label["原始洞察编号"] = ",".join(rule["来源编号"])
                label["原始预测批次"] = ",".join(rule["分析批次"])
                label["来源分群规则"] = rule["分群规则"]
            refreshed.append(rule["规则编号"])

        self._recompute_priorities()
        historical = [
            row.get("标注编号") for row in store.rows(self.MODULE)
            if not row.get("规则编号")
        ]
        return {
            "ok": True,
            "刷新规则数": len(refreshed),
            "规则总数": len(insight_store.rules),
            "规则数不变": len(insight_store.rules) == before,
            "刷新规则": refreshed,
            "保留的历史标注": historical,
        }

    def trace_subject_disposal(self, subject: str) -> dict[str, Any]:
        """追溯对象处置：最新处置、全部应用历史及每条规则的原始批次。"""
        latest = insight_store.applications.get(subject)
        history = [dict(item) for item in insight_store.application_history
                   if item["处置对象"] == subject]
        return {
            "ok": True,
            "处置对象": subject,
            "最新处置": dict(latest) if latest else None,
            "应用历史": history,
        }
