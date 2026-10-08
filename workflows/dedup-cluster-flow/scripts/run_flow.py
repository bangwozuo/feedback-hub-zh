#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""去重聚类工作流 — 端到端编排脚本（纯标准库，无第三方依赖）。

编排链路：
    输入（原始反馈条目）
      → 步骤 1：语义去重（同一诉求的多条表述合并为一组候选）
      → 步骤 2：主题聚类（按关键词口径归并主题、统计频次）
      → 输出（分组结果 + 共性洞察 + 证据索引 + 执行摘要）

用法：
    python scripts/run_flow.py --demo                # 内置演示数据，产物写入 out/
    python scripts/run_flow.py --input input.json    # 结构化输入，产物写入 out/
    python scripts/run_flow.py --input input.json --outdir out

输入 JSON 结构（--input）：
    {
      "input": [
        {"id": "01", "text": "发票拍照识别老是认错金额"}
      ]
    }

产物（写入 outdir，默认 out/）：
    flow_steps.csv     步骤 1 去重结果明细（CSV）
    flow_clusters.csv  步骤 2 聚类分组结果（CSV）
    flow_report.json   summary + steps + deliverable 完整结构（JSON）
"""
import argparse
import csv
import json
import os
import sys

KEYWORDS = [
    {"topic": "发票识别能力不足", "patterns": ["发票", "识别", "专票", "普票"]},
    {"topic": "多账本 / 分账需求", "patterns": ["多账本", "分账", "分开记", "一人一本"]},
    {"topic": "月报表导出 Excel", "patterns": ["导出", "报表", "会计", "Excel"]},
    {"topic": "安卓端闪退", "patterns": ["闪退", "崩溃"]},
]

DEMO_INPUT = [
    {"id": "01", "text": "发票拍照识别老是认错金额，小数点经常识别错"},
    {"id": "02", "text": "发票识别不准，9.5 元老识别成 95，太耽误事"},
    {"id": "03", "text": "希望能识别增值税专用发票，现在只支持普票"},
    {"id": "04", "text": "多账本功能什么时候有？店里和家用想分开记"},
    {"id": "05", "text": "求加多账本，我和合伙人一人一本"},
    {"id": "06", "text": "想分开记店里和家里的账，现在只能记一个本"},
    {"id": "07", "text": "月报表能不能导出 Excel 给会计"},
    {"id": "08", "text": "报表导出功能求上线，会计等着要 Excel"},
    {"id": "09", "text": "Huawei Mate 40 一点发票识别就闪退"},
    {"id": "10", "text": "Mate 40 打开发票识别直接崩溃，重装无效"},
]


def normalize(items):
    if isinstance(items, list):
        return [{"id": str(x.get("id", i + 1)), "text": str(x.get("text", ""))}
                for i, x in enumerate(items)]
    return []


def step1_dedupe(items):
    """语义去重（确定性口径）：同主题条目先标记候选组，同组内保留全部原文，输出候选组编号。"""
    groups = []
    for item in items:
        placed = False
        for g in groups:
            if g["topic"] == "其他 / 未分类":
                continue
            kw = next(k for k in KEYWORDS if k["topic"] == g["topic"])
            if any(p in item["text"] for p in kw["patterns"]):
                g["items"].append(item)
                placed = True
                break
        if not placed:
            hit = next((k for k in KEYWORDS if any(p in item["text"] for p in k["patterns"])), None)
            topic = hit["topic"] if hit else "其他 / 未分类"
            groups.append({"topic": topic, "items": [item]})
    return groups


def step2_cluster(groups):
    """主题聚类：按候选组规模降序，统计频次与占比，生成洞察与证据索引。"""
    n = sum(len(g["items"]) for g in groups) or 1
    clusters = sorted(groups, key=lambda g: -len(g["items"]))
    result = []
    for i, g in enumerate(clusters, 1):
        ids = [x["id"] for x in g["items"]]
        result.append({
            "#": str(i),
            "主题": g["topic"],
            "包含条目": "、".join(ids),
            "频次": str(len(ids)),
            "占比": "%.0f%%" % (len(ids) / n * 100),
        })
    insights = ["「%s」共 %d 条反馈（占 %d%%），是本批最集中的诉求。" % (
        result[0]["主题"], int(result[0]["频次"]), round(int(result[0]["频次"]) / n * 100))] if result else []
    evidence = ["洞察 1 → 条目 %s" % result[0]["包含条目"]] if result else []
    return result, insights, evidence


def write_outputs(items, clusters, insights, evidence, outdir):
    os.makedirs(outdir, exist_ok=True)
    steps_path = os.path.join(outdir, "flow_steps.csv")
    with open(steps_path, "w", newline="", encoding="utf-8-sig") as f:
        writer = csv.writer(f)
        writer.writerow(["#", "id", "text"])
        for x in items:
            writer.writerow([x["id"], x["id"], x["text"]])

    clusters_path = os.path.join(outdir, "flow_clusters.csv")
    with open(clusters_path, "w", newline="", encoding="utf-8-sig") as f:
        writer = csv.writer(f)
        writer.writerow(["#", "主题", "包含条目", "频次", "占比"])
        for c in clusters:
            writer.writerow([c["#"], c["主题"], c["包含条目"], c["频次"], c["占比"]])

    summary = "工作流执行完成：%d 条原始条目经去重聚类归并为 %d 个主题，最集中主题为「%s」（%s 条）。" % (
        len(items), len(clusters), clusters[0]["主题"], clusters[0]["频次"]) if clusters else "输入为空。"
    report_path = os.path.join(outdir, "flow_report.json")
    report = {
        "summary": summary,
        "steps": {
            "step1_去重": "%d 条条目进入候选分组" % len(items),
            "step2_聚类": "归并为 %d 个主题" % len(clusters),
        },
        "deliverable": {
            "clusters": clusters,
            "insights": insights,
            "evidence": evidence,
        },
        "assumption": [
            "步骤 1 去重采用确定性候选分出口径：条目按首个命中主题分入候选组，组内保留全部原文。",
            "步骤 2 聚类按组内条目数降序排列，占比 = 组内条目数 ÷ 条目总数 × 100%。",
        ],
    }
    with open(report_path, "w", encoding="utf-8") as f:
        json.dump(report, f, ensure_ascii=False, indent=2)
    return [steps_path, clusters_path, report_path]


def main():
    parser = argparse.ArgumentParser(description="去重聚类工作流 — 端到端编排脚本")
    parser.add_argument("--input", help="输入 JSON 文件路径")
    parser.add_argument("--outdir", default="out", help="产物输出目录，默认 out")
    parser.add_argument("--demo", action="store_true", help="使用内置演示数据运行")
    args = parser.parse_args()

    if args.demo:
        items = DEMO_INPUT
    elif args.input:
        with open(args.input, "r", encoding="utf-8") as f:
            data = json.load(f)
        raw = data.get("input")
        if not isinstance(raw, list):
            raw = data.get("items")
        items = normalize(raw)
        if not items:
            print("输入条目为空，改用内置演示数据", file=sys.stderr)
            items = DEMO_INPUT
    else:
        parser.error("必须指定 --input 或 --demo 之一")
        return 1

    groups = step1_dedupe(items)
    clusters, insights, evidence = step2_cluster(groups)
    paths = write_outputs(items, clusters, insights, evidence, args.outdir)

    print("工作流执行完成：%d 条条目 → %d 个主题" % (len(items), len(clusters)))
    print("summary:", "去重聚类完成，最集中主题「%s」%s 条" % (
        clusters[0]["主题"], clusters[0]["频次"]) if clusters else "无输出")
    for p in paths:
        print("已写出：%s" % p)
    return 0


if __name__ == "__main__":
    sys.exit(main())
