"""洞察归并验收测试：等价复跑识别、回执、规则下发、冲突处置、顺序稳定性与追溯。

覆盖验收口径：
- 多选三份同主题结果一次批量归并，每份来源都有独立处理回执；
- R1/R2 同口径同覆盖同动作归为同一洞察，R3 覆盖/动作不同保持独立，不扩大人群；
- 重复提交不新增规则；甲、乙各依适用规则处理，丙命中互斥规则进入冲突暂缓，
  并记录优先级与暂缓依据；
- 换输入顺序重送，丙的处置稳定、规则不增加；刷新规则不新增，仍可追溯原始批次；
- 历史规则按当时来源保留原始洞察与预测数据。
"""
from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from app.insight_store import insight_store
from app.services import service_for
from app.store import store


def reset_state() -> None:
    """用种子数据重建通用仓库与洞察仓库，保证用例相互独立。"""
    from app.seed import SEED_ROWS
    store._tables = {name: [dict(row) for row in rows] for name, rows in SEED_ROWS.items()}
    insight_store.reset()


def assert_ok(result, msg=""):
    ok = result.get("ok") if isinstance(result, dict) else getattr(result, "ok", False)
    assert ok, f"{msg}: {result}"
    return result


def find_result(code: str) -> dict:
    return next(row for row in store.rows("mining_result") if row.get("结果编号") == code)


def assert_two_merges(outcome: str = "") -> tuple[dict, dict]:
    assert outcome["归并组数"] == 1, f"应只有 R1/R2 一个归并组: {outcome}"
    assert outcome["独立洞察数"] == 1, f"R3 应保持独立: {outcome}"
    merged = outcome["归并组"][0]
    independent = outcome["独立洞察"][0]
    assert merged["来源编号"] == ["R1", "R2"]
    assert merged["分析批次"] == ["BATCH-2026W46-01", "BATCH-2026W47-01"]
    assert merged["覆盖人群"] == ["丙", "甲"], "等价复跑覆盖一致，不得扩大适用人群"
    assert merged["建议动作"] == "发送权益"
    assert merged["置信度"] == 0.82
    assert independent["来源编号"] == ["R3"]
    assert independent["覆盖人群"] == ["丙", "乙"]
    assert independent["建议动作"] == "进入观察"
    assert independent["置信度"] == 0.91
    return merged, independent


# ── 1. 批量归并与逐份回执 ──
def test_merge_three_same_topic_results_with_receipts():
    reset_state()
    svc = service_for("mining_result")
    r1, r2, r3 = find_result("R1")["id"], find_result("R2")["id"], find_result("R3")["id"]

    outcome = assert_ok(svc.merge_topic_insights([r1, r2, r3]), "批量归并")
    assert_two_merges(outcome)

    # 每份来源都有独立处理回执
    receipts = {item["result_id"]: item for item in outcome["回执"]}
    assert set(receipts) == {r1, r2, r3}
    merge_no = receipts[r1]["归并编号"]
    assert receipts[r1]["处理结论"] == "归并为等价复跑"
    assert receipts[r2]["处理结论"] == "归并为等价复跑"
    assert receipts[r2]["同洞察来源编号"] == ["R1"]
    assert receipts[r1]["同洞察来源编号"] == ["R2"]
    assert receipts[r3]["处理结论"] == "保持独立"
    assert receipts[r3]["归并编号"] != merge_no
    # 回执编号各不相同
    assert len({r["回执编号"] for r in receipts.values()}) == 3

    # 处置结果回写到挖掘结果详情
    for code in ("R1", "R2"):
        row = find_result(code)
        assert row["归并编号"] == merge_no
        assert row["归并状态"] == "已归并"
        assert row["归并来源批次"] == ["BATCH-2026W46-01", "BATCH-2026W47-01"]
    assert find_result("R3")["归并编号"] != merge_no


# ── 2. 重复提交幂等：不新增归并组、不重复来源 ──
def test_resubmit_is_idempotent():
    reset_state()
    svc = service_for("mining_result")
    r1, r2, r3 = find_result("R1")["id"], find_result("R2")["id"], find_result("R3")["id"]
    first = assert_ok(svc.merge_topic_insights([r1, r2, r3]))
    merge_no = first["归并组"][0]["归并编号"]

    second = assert_ok(svc.merge_topic_insights([r2, r1]), "重复提交")
    assert len(insight_store.merges) == 2, "重复提交不得新建归并组"
    merge = insight_store.merges[merge_no]
    assert merge["来源编号"] == ["R1", "R2"], "来源编号不得重复写入"
    assert merge["分析批次"] == ["BATCH-2026W46-01", "BATCH-2026W47-01"]
    # 仍会产生新的处理回执，便于审计每次提交
    assert_ok(svc.get_merge_receipts([r1, r2]))


# ── 3. 覆盖或动作不同不得合并，禁止扩大适用人群 ──
def test_different_coverage_stays_independent():
    reset_state()
    svc = service_for("mining_result")
    r1, r3 = find_result("R1")["id"], find_result("R3")["id"]
    outcome = assert_ok(svc.merge_topic_insights([r1, r3]))
    assert outcome["归并组数"] == 0
    assert outcome["独立洞察数"] == 2
    coverages = {tuple(sorted(s["覆盖人群"])) for s in outcome["独立洞察"]}
    assert coverages == {("丙", "甲"), ("丙", "乙")}, "不能借一次归并扩大适用人群"


# ── 4. 规则下发：场景标注携带分群规则，重复提交不多写规则 ──
def test_publish_rules_idempotent_and_write_back():
    reset_state()
    mining = service_for("mining_result")
    label_svc = service_for("scenario_label")
    ids = [find_result(c)["id"] for c in ("R1", "R2", "R3")]
    assert_ok(mining.merge_topic_insights(ids))

    before_labels = len(store.rows("scenario_label"))
    publish = assert_ok(label_svc.publish_merge_rules())
    assert len(publish["新建规则"]) == 2
    assert len(insight_store.rules) == 2
    assert len(store.rows("scenario_label")) == before_labels + 2

    # 场景标注从归并结果下发分群规则，出处完整
    merge_labels = [r for r in store.rows("scenario_label") if r.get("规则来源") == "洞察归并下发"]
    assert len(merge_labels) == 2
    texts = {r["业务规则"] for r in merge_labels}
    assert "高价值流失分群 -> 发送权益" in texts
    assert "高价值流失分群 -> 进入观察" in texts
    by_text = {r["业务规则"]: r for r in merge_labels}
    equity_label = by_text["高价值流失分群 -> 发送权益"]
    assert equity_label["原始洞察编号"] == "R1,R2"
    assert equity_label["原始预测批次"] == "BATCH-2026W46-01,BATCH-2026W47-01"

    # 处置结果回写到标注详情所需的规则编号也同步回原始结果
    assert find_result("R1")["规则编号"]
    assert find_result("R3")["规则编号"]

    # 重复下发：复用既有规则，不多写
    again = assert_ok(label_svc.publish_merge_rules())
    assert again["新建规则"] == []
    assert again["复用规则"] == publish["新建规则"]
    assert len(store.rows("scenario_label")) == before_labels + 2
    assert len(insight_store.rules) == 2

    # 优先级：R3 置信度 0.91 高于 R1/R2 的 0.82
    rules = list(insight_store.rules.values())
    by_action = {r["建议动作"]: r for r in rules}
    assert by_action["进入观察"]["优先级"] == 1
    assert by_action["发送权益"]["优先级"] == 2


# ── 5. 甲、乙各自适用规则；丙命中互斥动作进入冲突暂缓 ──
def test_apply_rules_and_conflict_hold():
    reset_state()
    mining = service_for("mining_result")
    label_svc = service_for("scenario_label")
    ids = [find_result(c)["id"] for c in ("R1", "R2", "R3")]
    assert_ok(mining.merge_topic_insights(ids))
    assert_ok(label_svc.publish_merge_rules())

    # 甲只命中发送权益
    a = assert_ok(label_svc.apply_published_rules("甲"))["application"]
    assert a["处置状态"] == "已执行"
    assert a["采用动作"] == "发送权益"
    assert {r["规则编号"] for r in a["命中规则"]} == set(a["采用规则"])

    # 乙只命中进入观察
    b = assert_ok(label_svc.apply_published_rules("乙"))["application"]
    assert b["处置状态"] == "已执行"
    assert b["采用动作"] == "进入观察"

    # 丙同时命中两个有效规则，动作互斥：一次应用不能执行互斥动作
    c = assert_ok(label_svc.apply_published_rules("丙"))["application"]
    assert c["处置状态"] == "冲突暂缓"
    assert c["采用动作"] is None
    assert c["采用规则"] == []
    assert set(c["冲突动作"]) == {"发送权益", "进入观察"}
    assert c["建议优先规则"]
    assert "置信度" in c["优先级依据"] and "优先级" in c["优先级依据"]
    assert "互斥" in c["暂缓依据"] and "暂缓" in c["暂缓依据"]
    # 建议优先规则应是高置信度的「进入观察」
    top = next(r for r in c["命中规则"] if r["规则编号"] == c["建议优先规则"])
    assert top["建议动作"] == "进入观察"

    # 处置结果回写到标注详情
    for label in store.rows("scenario_label"):
        if label.get("规则来源") != "洞察归并下发":
            continue
        writebacks = [w for w in label.get("处置回写", []) if w["处置对象"] == "丙"]
        assert writebacks, f"{label['标注编号']} 应回写丙的处置"
        assert writebacks[0]["处置状态"] == "冲突暂缓"
        assert writebacks[0]["暂缓依据"]

    # 人工裁决后才采用具体动作
    resolved = assert_ok(label_svc.resolve_rule_conflict(
        "丙", c["建议优先规则"], "高置信度规则优先，先观察一周"))["application"]
    assert resolved["处置状态"] == "冲突裁决-已执行"
    assert resolved["采用动作"] == "进入观察"
    assert resolved["裁决依据"] == "高置信度规则优先，先观察一周"


# ── 6. 换输入顺序重送：分组、丙的处置与规则数量保持稳定 ──
def test_reorder_inputs_keeps_stable_disposal():
    reset_state()
    mining = service_for("mining_result")
    label_svc = service_for("scenario_label")
    r1, r2, r3 = (find_result(c)["id"] for c in ("R1", "R2", "R3"))

    first = assert_ok(mining.merge_topic_insights([r1, r2, r3]))
    assert_two_merges(first)
    assert_ok(label_svc.publish_merge_rules())
    c1 = assert_ok(label_svc.apply_published_rules("丙"))["application"]
    assert c1["处置状态"] == "冲突暂缓"

    # 换一个输入顺序重送
    second = assert_ok(mining.merge_topic_insights([r3, r1, r2]), "逆序重送")
    assert_two_merges(second)
    assert len(insight_store.merges) == 2
    republish = assert_ok(label_svc.publish_merge_rules())
    assert republish["新建规则"] == [], "重送后规则没有增加"
    assert len(insight_store.rules) == 2

    c2 = assert_ok(label_svc.apply_published_rules("丙"))["application"]
    assert c2["处置状态"] == c1["处置状态"] == "冲突暂缓"
    assert c2["冲突动作"] == c1["冲突动作"]
    assert c2["建议优先规则"] == c1["建议优先规则"], "丙的处置依据应稳定"
    assert c2["应用次数"] == 2, "重复应用应累计次数而非改写历史"


# ── 7. 刷新规则不新增；刷新后仍能从三人处置追到原始批次 ──
def test_refresh_rules_and_trace_origin_batches():
    reset_state()
    mining = service_for("mining_result")
    label_svc = service_for("scenario_label")
    ids = [find_result(c)["id"] for c in ("R1", "R2", "R3")]
    assert_ok(mining.merge_topic_insights(ids))
    assert_ok(label_svc.publish_merge_rules())
    for subject in ("甲", "乙", "丙"):
        assert_ok(label_svc.apply_published_rules(subject))

    refresh = assert_ok(label_svc.refresh_published_rules())
    assert refresh["规则数不变"] is True
    assert len(insight_store.rules) == 2
    for rule in insight_store.rules.values():
        assert rule["刷新次数"] == 1
        assert rule["刷新版本"] == "v1.0.1"

    # 历史规则按当时来源保留，刷新不触碰
    historical = next(r for r in store.rows("scenario_label") if r["标注编号"] == "SCEN-0004")
    assert historical["规则来源"] == "历史直接下发"
    assert historical["原始洞察编号"] == "MINI-0003"
    assert historical["原始预测批次"] == "PRED-BATCH-20260810"
    assert "SCEN-0004" in refresh["保留的历史标注"]

    # 刷新后从每个人的处置都能追到使用或发生冲突的原始批次
    expectations = {
        "甲": ("已执行", {"BATCH-2026W46-01", "BATCH-2026W47-01"}),
        "乙": ("已执行", {"BATCH-2026W47-02"}),
        "丙": ("冲突暂缓", {"BATCH-2026W46-01", "BATCH-2026W47-01", "BATCH-2026W47-02"}),
    }
    for subject, (status, batches) in expectations.items():
        trace = assert_ok(label_svc.trace_subject_disposal(subject))
        latest = trace["最新处置"]
        assert latest["处置状态"] == status
        got_batches = {
            batch for rule in latest["命中规则"] for batch in rule["分析批次"]
        }
        assert got_batches == batches, f"{subject} 应追溯到原始批次 {batches}，实际 {got_batches}"
        got_codes = {
            code for rule in latest["命中规则"] for code in rule["来源编号"]
        }
        if subject == "丙":
            assert got_codes == {"R1", "R2", "R3"}
        assert trace["应用历史"], "应保留应用历史"


# ── 8. 缺失结果与缺失字段的回执也逐份返回，不中断整批归并 ──
def test_missing_and_invalid_sources_get_receipts():
    reset_state()
    svc = service_for("mining_result")
    r1 = find_result("R1")["id"]
    outcome = assert_ok(svc.merge_topic_insights([r1, 99999]))
    by_id = {r["result_id"]: r for r in outcome["回执"]}
    assert by_id[99999]["处理结论"] == "未找到"
    assert by_id[r1]["处理结论"] == "保持独立"
    assert outcome["未找到"] == [99999]


if __name__ == "__main__":
    tests = [
        test_merge_three_same_topic_results_with_receipts,
        test_resubmit_is_idempotent,
        test_different_coverage_stays_independent,
        test_publish_rules_idempotent_and_write_back,
        test_apply_rules_and_conflict_hold,
        test_reorder_inputs_keeps_stable_disposal,
        test_refresh_rules_and_trace_origin_batches,
        test_missing_and_invalid_sources_get_receipts,
    ]
    passed = failed = 0
    for test in tests:
        try:
            test()
            print(f"[PASS] {test.__name__}")
            passed += 1
        except Exception as exc:  # noqa: BLE001
            print(f"[FAIL] {test.__name__}: {exc}")
            failed += 1
    print(f"\n总测试: {len(tests)}, 通过: {passed}, 失败: {failed}")
    if failed:
        sys.exit(1)
