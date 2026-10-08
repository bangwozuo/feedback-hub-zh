#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""周报生成工作流 — 端到端编排脚本（纯标准库，无第三方依赖）。

编排链路：
    输入（本期/上期指标 + Top 需求 + 流失预警）
      → 步骤 1：指标汇总（变化量 / 变化率 / 符号翻转标注）
      → 步骤 2：Top 需求与流失预警汇总
      → 输出（决策周报：headline + 指标表 + 驱动因素 + 行动建议）

用法：
    python scripts/run_flow.py --demo                # 内置演示数据，产物写入 out/
    python scripts/run_flow.py --input input.json    # 结构化输入，产物写入 out/

输入 JSON 结构（--input）：
    {
      "input": {
        "metrics": [{"name": "反馈总量", "current": 128, "previous": 97, "unit": "条"}],
        "top_demands": [{"name": "修复安卓端闪退", "mentions": 41}],
        "churn_alerts": 6,
        "period": "2026-09-28 至 2026-10-04"
      }
    }

计算口径（与原子技能「周报生成」一致）：
    变化量 = 本期 − 上期；变化率 = 变化量 ÷ |上期| × 100%，保留 1 位小数
    上期为 0 记「不可比」；符号翻转（正转负 / 负转正）单独标注
    驱动因素只列变化幅度超 ±10% 的指标；建议由恶化指标映射建议库生成

产物（写入 outdir，默认 out/）：
    flow_metrics.csv        指标对比明细（CSV）
    flow_weekly_report.json summary + steps + deliverable 完整结构（JSON）
"""
import argparse
import csv
import json
import os
import sys

DECLINE_IS_BAD = {"会员净增", "NPS 调研", "会员数", "留存率"}
NEUTRAL = {"反馈总量", "多账本需求提及"}
SUGGESTION_LIB = [
    {"keyword": "闪退", "action": "紧急发布热修复版本，优先解决闪退问题，并在应用市场评论区逐一回复受影响用户"},
    {"keyword": "投诉", "action": "对相关投诉逐条安排客服回访，说明修复时间表"},
    {"keyword": "会员", "action": "对流失预警用户安排客服一对一回访，尝试挽回"},
    {"keyword": "NPS", "action": "复核本期差评集中点，将修复动作纳入下期计划并跟踪 NPS 回升"},
]
FALLBACK_SUGGESTION = "将变化最大的指标列入下期专项跟踪，明确责任人与复查时间点"

DEMO_INPUT = {
    "metrics": [
        {"name": "反馈总量", "current": 128, "previous": 97, "unit": "条"},
        {"name": "闪退类投诉", "current": 41, "previous": 18, "unit": "条"},
        {"name": "发票识别准确相关反馈", "current": 33, "previous": 29, "unit": "条"},
        {"name": "多账本需求提及", "current": 26, "previous": 24, "unit": "次"},
        {"name": "会员净增", "current": -12, "previous": 35, "unit": "人"},
        {"name": "NPS 调研", "current": 31, "previous": 44, "unit": "分"},
    ],
    "top_demands": [
        {"name": "修复安卓端闪退", "mentions": 41},
        {"name": "发票识别准确率优化", "mentions": 33},
        {"name": "月报表导出 Excel", "mentions": 15},
    ],
    "churn_alerts": 6,
    "period": "2026-09-28 至 2026-10-04",
}


def is_worse(m):
    if m["name"] in NEUTRAL:
        return False
    worse_by_rise = m["name"] not in DECLINE_IS_BAD and m["current"] > m["previous"]
    worse_by_fall = m["name"] in DECLINE_IS_BAD and m["current"] < m["previous"]
    return worse_by_rise or worse_by_fall


def change_text(cur, prev):
    delta = cur - prev
    sign = "+" if delta > 0 else ("-" if delta < 0 else "±0")
    delta_s = "%s%d" % (sign, abs(delta)) if delta == int(delta) else "%s%.1f" % (sign, abs(delta))
    if prev == 0:
        return delta_s + "（上期为 0，变化率不可比）"
    if (prev > 0) != (cur > 0) and cur != 0:
        return "%s（%s）" % (delta_s, "由正转负" if cur < 0 else "由负转正")
    return "%s（%+.1f%%）" % (delta_s, delta / abs(prev) * 100)


def step1_metrics(metrics, period):
    rows = []
    for m in metrics:
        rows.append({**m, "change": change_text(float(m["current"]), float(m["previous"])),
                     "worse": is_worse(m)})
    pool = [r for r in rows if r["previous"] != 0]
    if pool:
        worse_pool = [r for r in pool if r["worse"]]
        pick = max(worse_pool or pool,
                   key=lambda r: abs((float(r["current"]) - float(r["previous"])) / abs(float(r["previous"]))))
        tail = "需优先处理。" if pick["worse"] else "建议持续关注。"
        headline = "本期「%s」%s，%s" % (pick["name"], pick["change"], tail)
        if period:
            headline = "统计周期 %s。" % period + headline
    else:
        headline = "本期各指标变化率不可比，建议补充上期数据后重新生成。"

    drivers = []
    for r in sorted(pool, key=lambda x: -abs((float(x["current"]) - float(x["previous"])) / abs(float(x["previous"])))):
        rate = (float(r["current"]) - float(r["previous"])) / abs(float(r["previous"])) * 100
        if abs(rate) > 10:
            drivers.append("「%s」%s，变化幅度 %.1f%%，超出 ±10%% 关注线。" % (
                r["name"], r["change"], rate))
    return rows, headline, drivers


def step2_highlights(top_demands, churn_alerts, rows):
    """Top 需求表 + 行动建议。"""
    top = [{"排序": str(i), "需求": d.get("name", ""), "本期提及": "%s 次" % d.get("mentions", 0)}
           for i, d in enumerate(top_demands, 1)]
    suggestions = []
    for r in [x for x in rows if x["worse"]]:
        matched = next((s["action"] for s in SUGGESTION_LIB if s["keyword"] in r["name"]), None)
        action = matched or FALLBACK_SUGGESTION
        if action not in suggestions:
            suggestions.append(action)
    if churn_alerts:
        action = "对 %d 位流失预警用户安排客服一对一回访，说明修复时间表，尝试挽回" % churn_alerts
        if action not in suggestions:
            suggestions.insert(0, action)
    if not suggestions:
        suggestions = [FALLBACK_SUGGESTION]
    return top, suggestions


def write_outputs(rows, headline, drivers, top, suggestions, data, outdir):
    os.makedirs(outdir, exist_ok=True)
    csv_path = os.path.join(outdir, "flow_metrics.csv")
    with open(csv_path, "w", newline="", encoding="utf-8-sig") as f:
        writer = csv.writer(f)
        writer.writerow(["指标", "本期", "上期", "变化", "恶化/改善/关注"])
        for r in rows:
            tag = "恶化" if r["worse"] else ("关注" if r["name"] in NEUTRAL else "改善")
            writer.writerow([r["name"], r["current"], r["previous"], r["change"], tag])

    report_path = os.path.join(outdir, "flow_weekly_report.json")
    summary = "工作流执行完成：%d 项指标完成环比汇总，Top 需求 %d 项，流失预警 %s 条。" % (
        len(rows), len(top), data.get("churn_alerts", 0))
    report = {
        "summary": summary,
        "steps": {
            "step1_指标汇总": "%d 项指标完成变化量与变化率计算" % len(rows),
            "step2_要点汇总": "Top 需求 %d 项，流失预警 %s 条" % (len(top), data.get("churn_alerts", 0)),
        },
        "deliverable": {
            "headline": headline,
            "metrics_summary": [{"指标": r["name"], "本期": str(r["current"]),
                                 "上期": str(r["previous"]), "变化": r["change"]} for r in rows],
            "drivers": drivers,
            "top_demands": top,
            "suggestion": suggestions,
        },
        "period": data.get("period", ""),
        "assumption": [
            "变化率 = 变化量 ÷ |上期| × 100%，保留 1 位小数；上期为 0 记「不可比」，符号翻转单独标注。",
            "驱动因素只列变化幅度超 ±10% 的指标；行动建议由恶化指标与流失预警映射建议库生成，供人工复核。",
        ],
    }
    with open(report_path, "w", encoding="utf-8") as f:
        json.dump(report, f, ensure_ascii=False, indent=2)
    return [csv_path, report_path]


def main():
    parser = argparse.ArgumentParser(description="周报生成工作流 — 端到端编排脚本")
    parser.add_argument("--input", help="输入 JSON 文件路径")
    parser.add_argument("--outdir", default="out", help="产物输出目录，默认 out")
    parser.add_argument("--demo", action="store_true", help="使用内置演示数据运行")
    args = parser.parse_args()

    if args.demo:
        inner = DEMO_INPUT
    elif args.input:
        with open(args.input, "r", encoding="utf-8") as f:
            data = json.load(f)
        inner = data.get("input") if isinstance(data.get("input"), dict) else data
        if not isinstance(inner, dict) or not inner.get("metrics"):
            print("输入 metrics 为空，改用内置演示数据", file=sys.stderr)
            inner = DEMO_INPUT
    else:
        parser.error("必须指定 --input 或 --demo 之一")
        return 1

    rows, headline, drivers = step1_metrics(inner["metrics"], inner.get("period", ""))
    top, suggestions = step2_highlights(inner.get("top_demands", []), inner.get("churn_alerts", 0), rows)
    paths = write_outputs(rows, headline, drivers, top, suggestions, inner, args.outdir)

    print("工作流执行完成：headline:", headline)
    for p in paths:
        print("已写出：%s" % p)
    return 0


if __name__ == "__main__":
    sys.exit(main())
