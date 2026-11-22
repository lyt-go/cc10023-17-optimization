"""HTTP 端到端验收脚本：按题面顺序走通归并、下发、应用、冲突、重送、刷新、追溯。

运行：python -I scripts/acceptance_http.py
"""
from __future__ import annotations

import json
import urllib.parse
import urllib.request

BASE = "http://127.0.0.1:8000"


def call(method: str, path: str, payload: dict | None = None) -> dict:
    data = json.dumps(payload).encode() if payload is not None else None
    req = urllib.request.Request(
        BASE + path, data=data, method=method,
        headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req) as resp:
        return json.loads(resp.read().decode())


def show(title: str, obj: dict) -> None:
    print(f"\n===== {title} =====")
    print(json.dumps(obj, ensure_ascii=False, indent=2))


def main() -> None:
    # 找到 R1/R2/R3 的 id
    rows = call("GET", "/api/mining_result?size=200")["items"]
    ids = {row["结果编号"]: row["id"] for row in rows if row["结果编号"] in {"R1", "R2", "R3"}}
    assert ids == {"R1": 4, "R2": 5, "R3": 6}, ids

    # 1. 多选三份同主题结果一次批量归并，逐份核对回执
    merged = call("POST", "/api/insight/merge", {"result_ids": [ids["R1"], ids["R2"], ids["R3"]]})
    show("批量归并回执（三份来源各自一条）", {
        "归并组数": merged["归并组数"], "独立洞察数": merged["独立洞察数"],
        "归并组": merged["归并组"], "独立洞察": merged["独立洞察"],
        "回执": merged["回执"],
    })

    # 2. 下发分群规则
    published = call("POST", "/api/insight/publish-rules", {})
    show("下发分群规则（应新建2条）", published)
    again = call("POST", "/api/insight/publish-rules", {})
    show("重复下发（应全部复用，不多写）", again)
    rules = call("GET", "/api/insight/rules")
    show("已发布规则（含优先级与出处）", rules)

    # 3. 甲、乙、丙分别应用
    for subject in ("甲", "乙", "丙"):
        result = call("POST", "/api/insight/apply", {"subject": subject})
        show(f"规则应用：{subject}", result["application"])

    # 4. 换输入顺序重送后再次应用，核对丙处置稳定、规则不增加
    reordered = call("POST", "/api/insight/merge",
                     {"result_ids": [ids["R3"], ids["R1"], ids["R2"]]})
    show("逆序重送归并结果", {
        "归并组数": reordered["归并组数"], "独立洞察数": reordered["独立洞察数"]})
    republish = call("POST", "/api/insight/publish-rules", {})
    show("重送后再次下发（规则不应增加）", republish)
    bing = call("POST", "/api/insight/apply", {"subject": "丙"})
    show("丙再次应用（处置稳定，应用次数+1）", bing["application"])

    # 5. 刷新已发布规则
    refresh = call("POST", "/api/insight/refresh-rules", {})
    show("刷新已发布规则（数量不变、历史标注保留）", refresh)

    # 6. 追溯三人处置到原始批次
    for subject in ("甲", "乙", "丙"):
        trace = call("GET", f"/api/insight/trace/{urllib.parse.quote(subject)}")
        show(f"处置追溯：{subject}", {
            "处置状态": trace["最新处置"]["处置状态"],
            "采用动作": trace["最新处置"].get("采用动作"),
            "命中规则与原始批次": [
                {"规则编号": r["规则编号"], "动作": r["建议动作"],
                 "来源编号": r["来源编号"], "分析批次": r["分析批次"]}
                for r in trace["最新处置"]["命中规则"]
            ],
            "应用历史条数": len(trace["应用历史"]),
        })

    # 7. 人工裁决丙的冲突
    top_rule = bing["application"]["建议优先规则"]
    resolved = call("POST", "/api/insight/resolve-conflict",
                    {"subject": "丙", "rule_no": top_rule,
                     "reason": "高置信度规则优先，先观察一周", "operator": "analyst_wang"})
    show("丙的冲突人工裁决", resolved["application"])


if __name__ == "__main__":
    main()
