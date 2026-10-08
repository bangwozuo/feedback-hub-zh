#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""反馈聚类 — 确定性关键词聚类脚本（纯标准库，无第三方依赖）。

用法：
    python scripts/cluster.py --demo                # 内置演示数据，产物写入 out/
    python scripts/cluster.py --input input.json    # 结构化输入，产物写入 out/
    python scripts/cluster.py --input input.json --outdir out

输入 JSON 结构（--input）：
    {
      "items": [
        {"id": "01", "text": "发票拍照识别老是不准"}
      ],
      "keywords": [ {"topic": "发票识别", "patterns": ["发票", "识别"]} ]   # 选填
    }

聚类口径（与 SKILL.md 声明一致）：
    1. 每个主题由一组关键词模式定义，条目命中任一模式即归入该主题
    2. 一条条目可归入多个主题（跨主题重复保留，不计入「其他」）
    3. 未命中任何模式的条目归入「其他/未分类」
    4. 主题按频次降序排列；洞察按组内条目数降序生成，附证据索引

产物（写入 outdir，默认 out/）：
    clusters.csv   分组结果（CSV）
    report.json    clusters + insights + evidence 完整结构（JSON）
"""
import argparse
import csv
import json
import os
import sys

DEFAULT_KEYWORDS = [
    {"topic": "发票识别能力不足", "patterns": ["发票", "识别", "专票", "普票"]},
    {"topic": "多账本 / 分账需求", "patterns": ["多账本", "分账", "账本切换", "分开记"]},
    {"topic": "月报表导出 Excel", "patterns": ["导出", "报表", "会计", "Excel"]},
    {"topic": "安卓端闪退", "patterns": ["闪退", "崩溃", "退出"]},
    {"topic": "界面深色模式", "patterns": ["深色", "夜间", "刺眼"]},
]

DEMO_ITEMS = [
    {"id": "01", "text": "发票拍照识别老是不准，小数点后的金额经常识别错"},
    {"id": "02", "text": "希望发票识别能支持增值税专用发票，现在只能识别普票"},
    {"id": "03", "text": "能不能加个多账本功能？店里和家里开支想分开记"},
    {"id": "04", "text": "多账本刚需，个体户和家用混在一起太乱了"},
    {"id": "05", "text": "求一个账本切换功能，我和合伙人一人一本"},
    {"id": "06", "text": "月报表能不能导出 Excel 给会计看"},
    {"id": "07", "text": "导出功能什么时候上？现在只能截图发会计"},
    {"id": "08", "text": "App 在我的 Huawei Mate 40 上经常闪退，一开发票识别就崩"},
    {"id": "09", "text": "安卓闪退严重，Xiaomi 13 打开报表页面直接退出"},
    {"id": "10", "text": "希望记账界面支持深色模式，晚上记账太刺眼"},
]


def parse_items(items):
    """兼容两种形态：字符串（编号+书名号）或结构化数组。"""
    if isinstance(items, list):
        return [{"id": str(x.get("id", i + 1)), "text": str(x.get("text", ""))}
                for i, x in enumerate(items)]
    return []


def cluster_items(items, keywords):
    """确定性关键词聚类，返回 (clusters, insights, evidence)。"""
    assignments = {k["topic"]: [] for k in keywords}
    other = []
    for item in items:
        hit = False
        for k in keywords:
            if any(p in item["text"] for p in k["patterns"]):
                assignments[k["topic"]].append(item["id"])
                hit = True
        if not hit:
            other.append(item["id"])

    clusters = []
    for topic, ids in sorted(assignments.items(), key=lambda kv: -len(kv[1])):
        if ids:
            clusters.append({"topic": topic, "items": ids, "count": len(ids)})
    if other:
        clusters.append({"topic": "其他 / 未分类", "items": other, "count": len(other)})

    insights = []
    evidence = []
    for c in clusters:
        insights.append("「%s」共 %d 条反馈，占本批总量的 %d%%。" % (
            c["topic"], c["count"], round(c["count"] / len(items) * 100) if items else 0))
        evidence.append("洞察「%s」 → 条目 %s" % (c["topic"], "、".join(c["items"])))
    return clusters, insights, evidence


def write_outputs(clusters, insights, evidence, outdir):
    os.makedirs(outdir, exist_ok=True)
    clusters_path = os.path.join(outdir, "clusters.csv")
    with open(clusters_path, "w", newline="", encoding="utf-8-sig") as f:
        writer = csv.writer(f)
        writer.writerow(["#", "topic", "items", "count"])
        for i, c in enumerate(clusters, 1):
            writer.writerow([i, c["topic"], "、".join(c["items"]), c["count"]])

    report_path = os.path.join(outdir, "report.json")
    report = {
        "clusters": [{"#": str(i), "主题": c["topic"], "包含条目": "、".join(c["items"]),
                      "频次": str(c["count"])} for i, c in enumerate(clusters, 1)],
        "insights": insights,
        "evidence": evidence,
        "assumption": [
            "聚类采用确定性关键词匹配口径：每个主题由一组关键词模式定义，条目命中任一模式即归入该主题。",
            "一条条目可归入多个主题；未命中任何模式的条目归入「其他 / 未分类」。",
            "主题与洞察均按组内条目数降序排列，占比 = 组内条目数 ÷ 条目总数 × 100%。",
        ],
    }
    with open(report_path, "w", encoding="utf-8") as f:
        json.dump(report, f, ensure_ascii=False, indent=2)
    return [clusters_path, report_path]


def main():
    parser = argparse.ArgumentParser(description="反馈聚类 — 确定性关键词聚类脚本")
    parser.add_argument("--input", help="输入 JSON 文件路径")
    parser.add_argument("--outdir", default="out", help="产物输出目录，默认 out")
    parser.add_argument("--demo", action="store_true", help="使用内置演示数据运行")
    args = parser.parse_args()

    if args.demo:
        items, keywords = DEMO_ITEMS, DEFAULT_KEYWORDS
    elif args.input:
        with open(args.input, "r", encoding="utf-8") as f:
            data = json.load(f)
        items = parse_items(data.get("items"))
        keywords = data.get("keywords") or DEFAULT_KEYWORDS
        if not items:
            print("输入条目为空，改用内置演示数据", file=sys.stderr)
            items, keywords = DEMO_ITEMS, DEFAULT_KEYWORDS
    else:
        parser.error("必须指定 --input 或 --demo 之一")
        return 1

    clusters, insights, evidence = cluster_items(items, keywords)
    paths = write_outputs(clusters, insights, evidence, args.outdir)

    print("聚类完成：%d 条条目 → %d 个主题" % (len(items), len(clusters)))
    for p in paths:
        print("已写出：%s" % p)
    return 0


if __name__ == "__main__":
    sys.exit(main())
