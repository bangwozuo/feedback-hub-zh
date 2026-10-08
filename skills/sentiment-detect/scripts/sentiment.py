#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""情绪识别 — 确定性情绪词打标脚本（纯标准库，无第三方依赖）。

用法：
    python scripts/sentiment.py --demo                # 内置演示数据，产物写入 out/
    python scripts/sentiment.py --input input.json    # 结构化输入，产物写入 out/
    python scripts/sentiment.py --input input.json --outdir out

输入 JSON 结构（--input）：
    {
      "items": [
        {"id": "01", "text": "发票拍照识别老是认错金额，急用"}
      ],
      "negative_words": ["闪退", "不准"],   # 选填，缺省用内置词表
      "positive_words": ["顺手", "五星"]    # 选填，缺省用内置词表
    }

打标口径（与 SKILL.md 声明一致）：
    1. 负面：命中负面词表任一词条
    2. 正面：命中正面词表任一词条
    3. 存疑：正负面词同时命中，归入「存疑项」并说明双方依据
    4. 中性：正负面词均未命中
    5. 分布：占比 = 类别条数 ÷ 总条数 × 100%，四舍五入取整

产物（写入 outdir，默认 out/）：
    labeled.csv   逐条打标结果（CSV）
    report.json   labeled + distribution + ambiguous 完整结构（JSON）
"""
import argparse
import csv
import json
import os
import sys

DEFAULT_NEGATIVE = ["不准", "认错", "闪退", "退出", "崩溃", "没法用", "寒心", "一星", "换", "修一下", "卸载", "没用", "太乱", "投诉", "差评"]
DEFAULT_POSITIVE = ["顺手", "五星", "好评", "清爽", "好用", "喜欢", "推荐", "满意", "点赞", "完美"]

DEMO_ITEMS = [
    {"id": "01", "text": "发票拍照识别老是认错金额，急用，能不能修一下"},
    {"id": "02", "text": "Huawei Mate 40 一点发票识别就闪退，卸了重装也没用，一星"},
    {"id": "03", "text": "记账很顺手，五星，就差多账本功能了"},
    {"id": "04", "text": "月报表希望支持导出 Excel，会计要用"},
    {"id": "05", "text": "群里有没有人遇到 Xiaomi 13 打开报表页就退出的？我这边天天如此"},
    {"id": "06", "text": "你们再不修闪退我就换随手记了，用了两年真的寒心"},
    {"id": "07", "text": "新版界面挺清爽的，报表导出功能要是能上线就完美了"},
    {"id": "08", "text": "发票识别不准 + 闪退，这版本没法用"},
]


def hits(text, words):
    return [w for w in words if w in text]


def label_items(items, neg_words, pos_words):
    """确定性打标，返回 (labeled, distribution, ambiguous)。"""
    labeled = []
    ambiguous = []
    counts = {"负面": 0, "正面": 0, "中性": 0}
    for item in items:
        text = item.get("text", "")
        neg = hits(text, neg_words)
        pos = hits(text, pos_words)
        if neg and pos:
            label = "存疑"
            reason = "同时命中正面词 %s 与负面词 %s，需人工复核" % (
                "/".join(pos), "/".join(neg))
            ambiguous.append("条目 %s：%s" % (item.get("id", ""), reason))
        elif neg:
            label = "负面"
            reason = "命中负面词：%s" % "/".join(neg)
            counts["负面"] += 1
        elif pos:
            label = "正面"
            reason = "命中正面词：%s" % "/".join(pos)
            counts["正面"] += 1
        else:
            label = "中性"
            reason = "正负面词表均未命中，仅陈述需求或事实"
            counts["中性"] += 1
        labeled.append({"id": item.get("id", ""), "text": text, "label": label, "reason": reason})

    n = len(items) or 1
    distribution = [
        {"label": k, "count": v, "ratio": "%.0f%%" % (v / n * 100)}
        for k, v in counts.items()
    ]
    return labeled, distribution, ambiguous


def write_outputs(labeled, distribution, ambiguous, outdir):
    os.makedirs(outdir, exist_ok=True)
    labeled_path = os.path.join(outdir, "labeled.csv")
    with open(labeled_path, "w", newline="", encoding="utf-8-sig") as f:
        writer = csv.writer(f)
        writer.writerow(["#", "条目", "类别", "判定依据"])
        for x in labeled:
            writer.writerow([x["id"], x["text"], x["label"], x["reason"]])

    dist_path = os.path.join(outdir, "distribution.csv")
    with open(dist_path, "w", newline="", encoding="utf-8-sig") as f:
        writer = csv.writer(f)
        writer.writerow(["类别", "数量", "占比"])
        for d in distribution:
            writer.writerow([d["label"], d["count"], d["ratio"]])

    report_path = os.path.join(outdir, "report.json")
    report = {
        "labeled": [{"#": x["id"], "条目": x["text"], "类别": x["label"],
                     "判定依据": x["reason"]} for x in labeled],
        "distribution": [{"类别": d["label"], "数量": str(d["count"]),
                          "占比": d["ratio"]} for d in distribution],
        "ambiguous": ambiguous,
        "assumption": [
            "打标采用确定性情绪词表口径：负面 %d 词、正面 %d 词，条目命中即归类。" % (len(DEFAULT_NEGATIVE), len(DEFAULT_POSITIVE)),
            "正负面词同时命中计入存疑项；均未命中归为中性。占比 = 类别条数 ÷ 总条数 × 100%。",
        ],
    }
    with open(report_path, "w", encoding="utf-8") as f:
        json.dump(report, f, ensure_ascii=False, indent=2)
    return [labeled_path, dist_path, report_path]


def main():
    parser = argparse.ArgumentParser(description="情绪识别 — 确定性情绪词打标脚本")
    parser.add_argument("--input", help="输入 JSON 文件路径")
    parser.add_argument("--outdir", default="out", help="产物输出目录，默认 out")
    parser.add_argument("--demo", action="store_true", help="使用内置演示数据运行")
    args = parser.parse_args()

    if args.demo:
        items, neg_words, pos_words = DEMO_ITEMS, DEFAULT_NEGATIVE, DEFAULT_POSITIVE
    elif args.input:
        with open(args.input, "r", encoding="utf-8") as f:
            data = json.load(f)
        raw = data.get("items")
        if isinstance(raw, list) and raw:
            items = [{"id": str(x.get("id", i + 1)), "text": str(x.get("text", ""))}
                     for i, x in enumerate(raw)]
        else:
            print("输入 items 为空，改用内置演示数据", file=sys.stderr)
            items = DEMO_ITEMS
        neg_words = data.get("negative_words") or DEFAULT_NEGATIVE
        pos_words = data.get("positive_words") or DEFAULT_POSITIVE
    else:
        parser.error("必须指定 --input 或 --demo 之一")
        return 1

    labeled, distribution, ambiguous = label_items(items, neg_words, pos_words)
    paths = write_outputs(labeled, distribution, ambiguous, args.outdir)

    print("打标完成：%d 条，分布 %s" % (len(items),
          " ".join("%s %d" % (d["label"], d["count"]) for d in distribution)))
    for p in paths:
        print("已写出：%s" % p)
    return 0


if __name__ == "__main__":
    sys.exit(main())
