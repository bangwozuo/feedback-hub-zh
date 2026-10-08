#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""需求强度打分 — 确定性打分脚本（纯标准库，无第三方依赖）。

用法：
    python scripts/score.py --demo                # 内置演示数据，产物写入 out/
    python scripts/score.py --input input.json    # 结构化输入，产物写入 out/
    python scripts/score.py --input input.json --outdir out

输入 JSON 结构（--input）：
    {
      "records": [
        {"id": "R01", "name": "功能名", "mentions": 47, "willing": 18, "sample": 60, "churn": 5}
      ]
    }

打分口径（与 SKILL.md 声明一致）：
    频次分  = clamp(round(提及次数 / 最大提及次数 * 10), 1, 10)
    付费分  = clamp(round(愿付人数 / 样本数 * 20), 1, 10)   # 30% 愿付记 6 分
    流失分  = clamp(round(流失表态人数 * 1.6), 1, 10)
    总分    = 频次×40% + 付费×35% + 流失×25%，保留 1 位小数
    分层    = S ≥8.0；A 6.0-7.9；B 4.0-5.9；C <4.0

产物（写入 outdir，默认 out/）：
    scores.csv    逐条明细（CSV）
    segments.csv  分层汇总（CSV）
    report.json   scores + segments + assumption 完整结构（JSON）
"""
import argparse
import csv
import json
import os
import sys

WEIGHTS = {"frequency": 0.40, "willingness": 0.35, "churn": 0.25}
THRESHOLDS = [
    ("S", 8.0, "本迭代优先做"),
    ("A", 6.0, "排期做"),
    ("B", 4.0, "观察"),
    ("C", 0.0, "暂缓"),
]

DEMO_RECORDS = [
    {"id": "R01", "name": "发票拍照自动识别金额与抬头", "mentions": 47, "willing": 18, "sample": 60, "churn": 5},
    {"id": "R02", "name": "多账本切换", "mentions": 26, "willing": 8, "sample": 60, "churn": 0},
    {"id": "R03", "name": "月度经营报表导出Excel", "mentions": 15, "willing": 11, "sample": 60, "churn": 2},
    {"id": "R04", "name": "深色模式", "mentions": 22, "willing": 3, "sample": 60, "churn": 1},
]


def clamp(value, low, high):
    return max(low, min(high, value))


def score_records(records, weights=WEIGHTS):
    """确定性打分：逐条计算三维度分与加权总分，并按阈值分层。"""
    max_mentions = max((r.get("mentions", 0) for r in records), default=0) or 1
    rows = []
    for r in records:
        mentions = int(r.get("mentions", 0))
        willing = int(r.get("willing", 0))
        sample = max(int(r.get("sample", 0)), 1)
        churn = int(r.get("churn", 0))

        freq_score = clamp(round(mentions / max_mentions * 10), 1, 10)
        willing_score = clamp(round(willing / sample * 20), 1, 10)
        churn_score = clamp(round(churn * 1.6), 1, 10)
        total = round(
            freq_score * weights["frequency"]
            + willing_score * weights["willingness"]
            + churn_score * weights["churn"],
            1,
        )
        tier = next(name for name, th, _ in THRESHOLDS if total >= th)
        rows.append({
            "id": r.get("id", ""),
            "name": r.get("name", ""),
            "mentions": mentions,
            "willing": willing,
            "sample": sample,
            "churn": churn,
            "freq_score": freq_score,
            "willing_score": willing_score,
            "churn_score": churn_score,
            "total": total,
            "tier": tier,
        })
    rows.sort(key=lambda x: x["total"], reverse=True)
    return rows


def summarize(rows):
    """分层汇总：各层数量、占比、建议动作。"""
    n = len(rows) or 1
    segments = []
    for i, (name, th, action) in enumerate(THRESHOLDS):
        members = [r for r in rows if r["tier"] == name]
        if name == "S":
            threshold = "≥%.1f" % th
        elif name == "C":
            threshold = "<%.1f" % th
        else:
            threshold = "%.1f-%.1f" % (th, THRESHOLDS[i - 1][1] - 0.1)
        segments.append({
            "tier": name,
            "threshold": threshold,
            "count": len(members),
            "ratio": "%.0f%%" % (len(members) / n * 100),
            "action": action,
            "members": [r["id"] for r in members],
        })
    return segments


def write_outputs(rows, segments, outdir):
    os.makedirs(outdir, exist_ok=True)
    scores_path = os.path.join(outdir, "scores.csv")
    with open(scores_path, "w", newline="", encoding="utf-8-sig") as f:
        writer = csv.writer(f)
        writer.writerow(["id", "name", "mentions", "willing", "sample", "churn",
                         "freq_score", "willing_score", "churn_score", "total", "tier"])
        for r in rows:
            writer.writerow([r["id"], r["name"], r["mentions"], r["willing"], r["sample"], r["churn"],
                             r["freq_score"], r["willing_score"], r["churn_score"], r["total"], r["tier"]])

    segments_path = os.path.join(outdir, "segments.csv")
    with open(segments_path, "w", newline="", encoding="utf-8-sig") as f:
        writer = csv.writer(f)
        writer.writerow(["tier", "threshold", "count", "ratio", "action", "members"])
        for s in segments:
            writer.writerow([s["tier"], s["threshold"], s["count"], s["ratio"], s["action"], " ".join(s["members"])])

    report_path = os.path.join(outdir, "report.json")
    report = {
        "scores": rows,
        "segments": segments,
        "assumption": [
            "维度权重采用默认口径：频次 40%、付费意愿 35%、流失风险 25%，各维度按 1-10 分打分后加权求和。",
            "频次分按 30 天提及次数相对最大值映射（最大值记 10 分）；付费分按愿付人数占比映射（占比 30% 记 6 分）；流失分按明确流失表态人数映射（每人 1.6 分）。",
            "分层阈值采用默认口径：总分 ≥8.0 为 S 级、6.0-7.9 为 A 级、4.0-5.9 为 B 级、<4.0 为 C 级；阈值为给定假设，不代表唯一正解，结果供排序参考。",
        ],
    }
    with open(report_path, "w", encoding="utf-8") as f:
        json.dump(report, f, ensure_ascii=False, indent=2)

    return [scores_path, segments_path, report_path]


def main():
    parser = argparse.ArgumentParser(description="需求强度打分 — 确定性打分脚本")
    parser.add_argument("--input", help="输入 JSON 文件路径（结构化 records 数组）")
    parser.add_argument("--outdir", default="out", help="产物输出目录，默认 out")
    parser.add_argument("--demo", action="store_true", help="使用内置演示数据运行")
    args = parser.parse_args()

    if args.demo:
        records = DEMO_RECORDS
    elif args.input:
        with open(args.input, "r", encoding="utf-8") as f:
            data = json.load(f)
        records = data.get("records") if isinstance(data.get("records"), list) else DEMO_RECORDS
        if not records:
            print("输入文件 records 为空，改用内置演示数据", file=sys.stderr)
            records = DEMO_RECORDS
    else:
        parser.error("必须指定 --input 或 --demo 之一")
        return 1

    rows = score_records(records)
    segments = summarize(rows)
    paths = write_outputs(rows, segments, args.outdir)

    print("打分完成：%d 条记录，S/A/B/C 分布 %s" % (
        len(rows),
        "/".join(str(s["count"]) for s in segments),
    ))
    for p in paths:
        print("已写出：%s" % p)
    return 0


if __name__ == "__main__":
    sys.exit(main())
