#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""路线图建议工作流 — 端到端编排脚本（纯标准库，无第三方依赖）。

编排链路：
    输入（需求记录 + 实现成本 + 已上线清单）
      → 步骤 1：需求强度打分（频次 40% / 付费意愿 35% / 流失风险 25%，1-10 分加权）
      → 步骤 2：更新日志生成（对本期已上线需求产出 changelog 草稿条目）
      → 输出（打分分层结果 + 路线图建议 + 更新日志草稿）

用法：
    python scripts/run_flow.py --demo                # 内置演示数据，产物写入 out/
    python scripts/run_flow.py --input input.json    # 结构化输入，产物写入 out/

输入 JSON 结构（--input）：
    {
      "input": {
        "records": [
          {"id": "R01", "name": "发票拍照识别", "mentions": 47, "willing": 18,
           "sample": 60, "churn": 5, "cost_days": 20, "shipped": "v2.6.0"}
        ],
        "version": "v2.6.0",
        "release_date": "2026-09-30"
      }
    }

打分口径（与原子技能「需求强度打分」一致）：
    频次分 = clamp(round(提及/最大提及×10), 1, 10)；付费分 = clamp(round(愿付占比×20), 1, 10)
    流失分 = clamp(round(流失表态×1.6), 1, 10)；总分 = 40%/35%/25% 加权，保留 1 位小数
    分层：S ≥8.0（本迭代优先做）；A 6.0-7.9（排期做）；B 4.0-5.9（观察）；C <4.0（暂缓）

产物（写入 outdir，默认 out/）：
    roadmap_scores.csv     步骤 1 打分分层结果（CSV）
    roadmap_changelog.csv  步骤 2 更新日志草稿条目（CSV）
    roadmap_report.json    summary + steps + deliverable 完整结构（JSON）
"""
import argparse
import csv
import json
import os
import sys

WEIGHTS = {"frequency": 0.40, "willingness": 0.35, "churn": 0.25}
THRESHOLDS = [("S", 8.0, "本迭代优先做"), ("A", 6.0, "排期做"), ("B", 4.0, "观察"), ("C", 0.0, "暂缓")]

DEMO_INPUT = {
    "records": [
        {"id": "R01", "name": "发票拍照自动识别金额与抬头", "mentions": 47, "willing": 18, "sample": 60, "churn": 5, "cost_days": 20, "shipped": "v2.6.0"},
        {"id": "R02", "name": "多账本切换", "mentions": 26, "willing": 8, "sample": 60, "churn": 0, "cost_days": 5, "shipped": "v2.6.0"},
        {"id": "R03", "name": "月度经营报表导出Excel", "mentions": 15, "willing": 11, "sample": 60, "churn": 2, "cost_days": 8, "shipped": ""},
        {"id": "R04", "name": "深色模式", "mentions": 22, "willing": 3, "sample": 60, "churn": 1, "cost_days": 3, "shipped": ""},
    ],
    "version": "v2.6.0",
    "release_date": "2026-09-30",
}


def clamp(v, low, high):
    return max(low, min(high, v))


def step1_score(records):
    """需求强度打分（与原子技能口径一致）。"""
    max_mentions = max((r.get("mentions", 0) for r in records), default=0) or 1
    rows = []
    for r in records:
        mentions = int(r.get("mentions", 0))
        willing = int(r.get("willing", 0))
        sample = max(int(r.get("sample", 0)), 1)
        churn = int(r.get("churn", 0))
        freq = clamp(round(mentions / max_mentions * 10), 1, 10)
        pay = clamp(round(willing / sample * 20), 1, 10)
        risk = clamp(round(churn * 1.6), 1, 10)
        total = round(freq * WEIGHTS["frequency"] + pay * WEIGHTS["willingness"] + risk * WEIGHTS["churn"], 1)
        tier = next(name for name, th, _ in THRESHOLDS if total >= th)
        action = next(a for n, t, a in THRESHOLDS if n == tier)
        rows.append({
            "id": r.get("id", ""), "name": r.get("name", ""),
            "cost_days": r.get("cost_days", 0), "shipped": r.get("shipped", ""),
            "freq": freq, "pay": pay, "risk": risk, "total": total,
            "tier": tier, "action": action,
        })
    rows.sort(key=lambda x: x["total"], reverse=True)
    return rows


def step2_changelog(rows, version, release_date):
    """更新日志生成：对已上线需求产出 changelog 草稿条目。"""
    shipped = [r for r in rows if r["shipped"]]
    entries = []
    for i, r in enumerate(shipped, 1):
        entries.append({
            "#": str(i),
            "类型": "新增",
            "内容": "%s功能上线，已进入 %s 版本。" % (r["name"], r["shipped"]) if "修复" not in r["name"] else r["name"],
            "受影响范围": "全部用户",
        })
    if not entries:
        entries.append({"#": "1", "类型": "说明", "内容": "本期无已上线需求，更新日志缺变更清单，待补充。", "受影响范围": "-"})
    meta = {"version": version, "release_date": release_date}
    return entries, meta


def write_outputs(rows, entries, meta, outdir):
    os.makedirs(outdir, exist_ok=True)
    scores_path = os.path.join(outdir, "roadmap_scores.csv")
    with open(scores_path, "w", newline="", encoding="utf-8-sig") as f:
        writer = csv.writer(f)
        writer.writerow(["#", "id", "需求", "频次分", "付费分", "流失分", "总分", "分层", "建议动作", "成本(人日)", "已上线版本"])
        for i, r in enumerate(rows, 1):
            writer.writerow([i, r["id"], r["name"], r["freq"], r["pay"], r["risk"], r["total"],
                             r["tier"], r["action"], r["cost_days"], r["shipped"] or "-"])

    log_path = os.path.join(outdir, "roadmap_changelog.csv")
    with open(log_path, "w", newline="", encoding="utf-8-sig") as f:
        writer = csv.writer(f)
        writer.writerow(["#", "类型", "变更内容", "受影响范围"])
        for e in entries:
            writer.writerow([e["#"], e["类型"], e["内容"], e["受影响范围"]])

    top = rows[0] if rows else None
    summary = "工作流执行完成：%d 条需求完成打分分层（S/A/B/C = %s），建议本迭代优先处理「%s」（总分 %.1f）；%s" % (
        len(rows),
        "/".join(str(sum(1 for r in rows if r["tier"] == t[0])) for t in THRESHOLDS),
        top["name"], top["total"],
        "已上线需求 %d 条已生成更新日志草稿。" % len(entries)) if top else "输入为空。"

    report_path = os.path.join(outdir, "roadmap_report.json")
    report = {
        "summary": summary,
        "steps": {
            "step1_需求强度打分": "%d 条需求完成打分与分层" % len(rows),
            "step2_更新日志生成": "%d 条已上线需求生成 changelog 草稿（%s，%s）" % (
                len(entries), meta.get("version", ""), meta.get("release_date", "")),
        },
        "deliverable": {
            "scores": [{"#": str(i), "需求": r["name"], "总分": str(r["total"]),
                        "分层": r["tier"], "建议动作": r["action"]} for i, r in enumerate(rows, 1)],
            "changelog": entries,
            "roadmap_advice": [
                "S 级需求本迭代优先做，A 级排期做，B 级观察，C 级暂缓；结合成本（人日）做性价比排序。",
                "已上线需求（%s）转入验证与运营环节，更新日志草稿需产品与法务复核后发布。" % (
                    "、".join(r["id"] for r in rows if r["shipped"]) or "无"),
            ],
        },
        "assumption": [
            "步骤 1 打分口径：频次 40%、付费意愿 35%、流失风险 25%，各维度 1-10 分加权求和；S ≥8.0 / A 6.0-7.9 / B 4.0-5.9 / C <4.0。",
            "步骤 2 更新日志仅覆盖输入中标记已上线（shipped）的需求；草稿不构成任何功能效果承诺，发布前需人工确认。",
        ],
    }
    with open(report_path, "w", encoding="utf-8") as f:
        json.dump(report, f, ensure_ascii=False, indent=2)
    return [scores_path, log_path, report_path]


def main():
    parser = argparse.ArgumentParser(description="路线图建议工作流 — 端到端编排脚本")
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
        if not isinstance(inner, dict) or not inner.get("records"):
            print("输入 records 为空，改用内置演示数据", file=sys.stderr)
            inner = DEMO_INPUT
    else:
        parser.error("必须指定 --input 或 --demo 之一")
        return 1

    rows = step1_score(inner["records"])
    entries, meta = step2_changelog(rows, inner.get("version", ""), inner.get("release_date", ""))
    paths = write_outputs(rows, entries, meta, args.outdir)

    print("工作流执行完成：%d 条需求打分，%d 条生成 changelog 草稿" % (len(rows), len(entries)))
    for p in paths:
        print("已写出：%s" % p)
    return 0


if __name__ == "__main__":
    sys.exit(main())
