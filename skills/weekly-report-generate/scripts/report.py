#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""周报生成 — 确定性指标汇总脚本（纯标准库，无第三方依赖）。

用法：
    python scripts/report.py --demo                # 内置演示数据，产物写入 out/
    python scripts/report.py --input input.json    # 结构化输入，产物写入 out/
    python scripts/report.py --input input.json --outdir out

输入 JSON 结构（--input）：
    {
      "metrics": [
        {"name": "反馈总量", "current": 128, "previous": 97, "unit": "条"}
      ],
      "period": "2026-09-28 至 2026-10-04"    # 选填
    }

计算口径（与 SKILL.md 声明一致）：
    1. 变化量 = 本期 − 上期；变化率 = 变化量 ÷ |上期| × 100%，保留 1 位小数
    2. 上期为 0 时变化率记「不可比」；符号翻转（正转负 / 负转正）单独标注
    3. headline 取绝对变化率最大的恶化指标（或全部改善时取改善最大的指标）
    4. drivers 列出变化率超 ±10% 的指标；suggestion 按恶化指标映射建议库生成

产物（写入 outdir，默认 out/）：
    metrics_summary.csv   指标对比明细（CSV）
    weekly_report.json    headline + metrics_summary + drivers + suggestion（JSON）
"""
import argparse
import csv
import json
import os
import sys

SUGGESTION_LIB = [
    {"keyword": "闪退", "action": "紧急发布热修复版本，优先解决闪退问题，并在应用市场评论区逐一回复受影响用户"},
    {"keyword": "投诉", "action": "对相关投诉逐条安排客服回访，说明修复时间表"},
    {"keyword": "会员", "action": "对流失预警用户安排客服一对一回访，尝试挽回"},
    {"keyword": "NPS", "action": "复核本期差评集中点，将修复动作纳入下期计划并跟踪 NPS 回升"},
]
FALLBACK_SUGGESTION = "将变化最大的指标列入下期专项跟踪，明确责任人与复查时间点"

DEMO_METRICS = [
    {"name": "反馈总量", "current": 128, "previous": 97, "unit": "条"},
    {"name": "闪退类投诉", "current": 41, "previous": 18, "unit": "条"},
    {"name": "发票识别准确相关反馈", "current": 33, "previous": 29, "unit": "条"},
    {"name": "多账本需求提及", "current": 26, "previous": 24, "unit": "次"},
    {"name": "会员净增", "current": -12, "previous": 35, "unit": "人"},
    {"name": "NPS 调研", "current": 31, "previous": 44, "unit": "分"},
]

# 「恶化」的定义：数值上升即恶化（投诉/反馈类）或数值下降即恶化（会员/NPS 类）
DECLINE_IS_BAD = {"会员净增", "NPS 调研", "会员数", "留存率"}
# 中性指标：上升或下降均不直接判定为恶化，仅标记「关注」
NEUTRAL = {"反馈总量", "多账本需求提及"}


def parse_metrics(metrics):
    if isinstance(metrics, list):
        return [{"name": str(m.get("name", "指标%d" % (i + 1))),
                 "current": float(m.get("current", 0)),
                 "previous": float(m.get("previous", 0)),
                 "unit": str(m.get("unit", ""))}
                for i, m in enumerate(metrics)]
    return []


def fmt(value, unit):
    if value == int(value):
        return "%d %s" % (int(value), unit) if unit else "%d" % int(value)
    return "%.1f %s" % (value, unit) if unit else "%.1f" % value


def change_text(cur, prev):
    delta = cur - prev
    sign = "+" if delta > 0 else ("-" if delta < 0 else "±0")
    delta_s = "%s%s" % (sign, fmt(abs(delta), ""))
    if prev == 0:
        return delta_s + "（上期为 0，变化率不可比）"
    if (prev > 0) != (cur > 0) and cur != 0 and prev != 0:
        return "%s（%s）" % (delta_s, "由正转负" if cur < 0 else "由负转正")
    rate = delta / abs(prev) * 100
    return "%s（%+.1f%%）" % (delta_s, rate)


def is_worse(m):
    if m["name"] in NEUTRAL:
        return False
    worse_by_rise = m["name"] not in DECLINE_IS_BAD and m["current"] > m["previous"]
    worse_by_fall = m["name"] in DECLINE_IS_BAD and m["current"] < m["previous"]
    return worse_by_rise or worse_by_fall


def compute(metrics, period=None):
    rows = []
    for m in metrics:
        delta = m["current"] - m["previous"]
        rate = None
        if m["previous"] != 0:
            rate = delta / abs(m["previous"]) * 100
        rows.append({**m, "delta": delta, "rate": rate, "change": change_text(m["current"], m["previous"]),
                     "worse": is_worse(m)})

    # headline：恶化指标中取绝对变化率最大者；无恶化则取绝对变化率最大者
    pool = [r for r in rows if r["rate"] is not None]
    if pool:
        worse_pool = [r for r in pool if r["worse"]]
        pick = max(worse_pool or pool, key=lambda r: abs(r["rate"]))
        if pick["worse"]:
            tail = "需优先处理。"
        else:
            tail = "建议持续关注。"
        headline = "本期「%s」%s，%s" % (pick["name"], pick["change"], tail)
        if period:
            headline = "统计周期 %s。" % period + headline
    else:
        headline = "本期各指标变化率不可比，建议补充上期数据后重新生成。"

    drivers = []
    for r in sorted(pool, key=lambda x: -abs(x["rate"] or 0)):
        if r["rate"] is not None and abs(r["rate"]) > 10:
            drivers.append("「%s」%s，变化幅度 %s，超出 ±10%% 关注线。" % (r["name"], r["change"], "恶化" if r["worse"] else "改善"))

    suggestions = []
    for r in [x for x in rows if x["worse"]]:
        matched = next((s["action"] for s in SUGGESTION_LIB if s["keyword"] in r["name"]), None)
        action = matched or FALLBACK_SUGGESTION
        if action not in suggestions:
            suggestions.append(action)

    return rows, headline, drivers, suggestions or [FALLBACK_SUGGESTION]


def write_outputs(rows, headline, drivers, suggestions, period, outdir):
    os.makedirs(outdir, exist_ok=True)
    csv_path = os.path.join(outdir, "metrics_summary.csv")
    with open(csv_path, "w", newline="", encoding="utf-8-sig") as f:
        writer = csv.writer(f)
        writer.writerow(["指标", "本期", "上期", "变化", "恶化/改善"])
        for r in rows:
            tag = "恶化" if r["worse"] else ("关注" if r["name"] in NEUTRAL else "改善")
            writer.writerow([r["name"], fmt(r["current"], r["unit"]), fmt(r["previous"], r["unit"]),
                             r["change"], tag])

    report_path = os.path.join(outdir, "weekly_report.json")
    report = {
        "headline": headline,
        "metrics_summary": [{"指标": r["name"], "本期": fmt(r["current"], r["unit"]),
                             "上期": fmt(r["previous"], r["unit"]), "变化": r["change"]}
                            for r in rows],
        "drivers": drivers,
        "suggestion": suggestions,
        "period": period or "",
        "assumption": [
            "变化率 = 变化量 ÷ |上期| × 100%，保留 1 位小数；上期为 0 时记「不可比」，符号翻转单独标注。",
            "恶化/改善方向按指标语义判定（投诉类上升为恶化，会员/NPS 类下降为恶化）。",
            "驱动因素只列变化幅度超 ±10% 的指标；建议由恶化指标映射建议库生成，供人工复核。",
        ],
    }
    with open(report_path, "w", encoding="utf-8") as f:
        json.dump(report, f, ensure_ascii=False, indent=2)
    return [csv_path, report_path]


def main():
    parser = argparse.ArgumentParser(description="周报生成 — 确定性指标汇总脚本")
    parser.add_argument("--input", help="输入 JSON 文件路径")
    parser.add_argument("--outdir", default="out", help="产物输出目录，默认 out")
    parser.add_argument("--demo", action="store_true", help="使用内置演示数据运行")
    args = parser.parse_args()

    if args.demo:
        metrics, period = DEMO_METRICS, "2026-09-28 至 2026-10-04"
    elif args.input:
        with open(args.input, "r", encoding="utf-8") as f:
            data = json.load(f)
        metrics = parse_metrics(data.get("metrics"))
        period = data.get("period")
        if not metrics:
            print("输入 metrics 为空，改用内置演示数据", file=sys.stderr)
            metrics, period = DEMO_METRICS, "2026-09-28 至 2026-10-04"
    else:
        parser.error("必须指定 --input 或 --demo 之一")
        return 1

    rows, headline, drivers, suggestions = compute(metrics, period)
    paths = write_outputs(rows, headline, drivers, suggestions, period, args.outdir)

    print("headline:", headline)
    for p in paths:
        print("已写出：%s" % p)
    return 0


if __name__ == "__main__":
    sys.exit(main())
