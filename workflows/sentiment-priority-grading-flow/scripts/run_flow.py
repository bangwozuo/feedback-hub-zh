#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""情绪与优先级分级工作流 — 端到端编排脚本（纯标准库，无第三方依赖）。

编排链路：
    输入（反馈条目）
      → 步骤 1：情绪识别（情绪词表口径：正面 / 负面 / 中性 / 存疑）
      → 步骤 2：优先级打分（负面情绪强度 40% + 付费意愿 30% + 流失风险 30%，
                 高 ≥8.0 / 中 5.0-7.9 / 低 <5.0）
      → 步骤 3：流失风险标记（命中流失表态词的条目单独标记）
      → 输出（逐条打标 + 优先级分级 + 流失风险清单）

用法：
    python scripts/run_flow.py --demo                # 内置演示数据，产物写入 out/
    python scripts/run_flow.py --input input.json    # 结构化输入，产物写入 out/

输入 JSON 结构（--input）：
    {
      "input": [
        {"id": "01", "text": "发票拍照识别老是认错金额", "source": "客服邮箱", "date": "2026-09-29"}
      ]
    }

打分口径（与 SKILL.md 声明一致）：
    情绪强度分 = 负面词命中 n 个记 6+2×(n-1) 分（上限 10）；仅正面词命中记 2 分；均未命中记 5 分
    付费意愿分 = 文本含「付费/愿付」记 8 分；否则记 5 分（无信号中性）
    流失风险分 = 含流失表态词（换/卸载/不续费/退订）记 10 分；否则记 2 分
    总分 = 情绪强度×40% + 付费意愿×30% + 流失风险×30%，保留 1 位小数
    分级：高优先级 ≥8.0；中优先级 5.0-7.9；低优先级 <5.0

产物（写入 outdir，默认 out/）：
    flow_labels.csv      步骤 1 情绪打标结果（CSV）
    flow_priority.csv    步骤 2 优先级分级结果（CSV）
    flow_report.json     summary + steps + deliverable 完整结构（JSON）
"""
import argparse
import csv
import json
import os
import sys

NEG_WORDS = ["不准", "认错", "闪退", "退出", "崩溃", "没法用", "寒心", "一星", "修一下", "卸载", "没用", "太乱", "投诉", "差评"]
POS_WORDS = ["顺手", "五星", "好评", "清爽", "好用", "喜欢", "推荐", "满意", "点赞", "完美"]
CHURN_WORDS = ["换", "卸载", "不续费", "退订"]
PAY_WORDS = ["付费", "愿付"]

WEIGHTS = {"intensity": 0.40, "willingness": 0.30, "churn": 0.30}

DEMO_INPUT = [
    {"id": "01", "text": "发票拍照识别老是认错金额，9.50 识别成 95.0，急用，再不准我就换随手记了", "source": "客服邮箱", "date": "2026-09-29"},
    {"id": "02", "text": "Huawei Mate 40 一点发票识别就闪退，卸了重装也没用，一星", "source": "应用市场", "date": "2026-09-30"},
    {"id": "03", "text": "记账很顺手，五星，就差多账本功能了", "source": "应用市场", "date": "2026-10-02"},
    {"id": "04", "text": "群里有没有人遇到 Xiaomi 13 打开报表页就退出的？我这边天天如此", "source": "微信群", "date": "2026-10-03"},
    {"id": "05", "text": "多账本功能什么时候上？分开记账刚需，付费也行", "source": "微信群", "date": "2026-10-04"},
    {"id": "06", "text": "你们再不修闪退我就卸载了，用了两年真的寒心", "source": "微信群", "date": "2026-10-04"},
]


def clamp(v, low, high):
    return max(low, min(high, v))


def hits(text, words):
    return [w for w in words if w in text]


def step1_sentiment(items):
    """情绪识别：词表口径打标。"""
    labeled = []
    for item in items:
        text = item.get("text", "")
        neg = hits(text, NEG_WORDS)
        pos = hits(text, POS_WORDS)
        if neg and pos:
            label = "存疑"
        elif neg:
            label = "负面"
        elif pos:
            label = "正面"
        else:
            label = "中性"
        labeled.append({**item, "label": label, "neg_hits": neg, "pos_hits": pos})
    return labeled


def step2_priority(labeled):
    """优先级打分：情绪强度 40% + 付费意愿 30% + 流失风险 30%。"""
    rows = []
    for x in labeled:
        neg_n = len(x["neg_hits"])
        if x["label"] in ("负面", "存疑") and neg_n:
            intensity = clamp(6 + 2 * (neg_n - 1), 1, 10)
        elif x["label"] == "正面":
            intensity = 2
        else:
            intensity = 5
        willingness = 8 if any(w in x.get("text", "") for w in PAY_WORDS) else 5
        churn_hit = [w for w in CHURN_WORDS if w in x.get("text", "")]
        churn_risk = 10 if churn_hit else 2
        total = round(intensity * WEIGHTS["intensity"] + willingness * WEIGHTS["willingness"]
                      + churn_risk * WEIGHTS["churn"], 1)
        level = "高" if total >= 8.0 else ("中" if total >= 5.0 else "低")
        rows.append({**x, "intensity": intensity, "willingness": willingness,
                     "churn_risk": churn_risk, "churn_hit": churn_hit,
                     "total": total, "level": level})
    rows.sort(key=lambda r: {"高": 0, "中": 1, "低": 2}[r["level"]])
    return rows


def step3_churn_marks(rows):
    """流失风险标记：命中流失表态词的条目。"""
    return [r for r in rows if r["churn_hit"]]


def write_outputs(labeled, rows, churn_items, outdir):
    os.makedirs(outdir, exist_ok=True)
    labels_path = os.path.join(outdir, "flow_labels.csv")
    with open(labels_path, "w", newline="", encoding="utf-8-sig") as f:
        writer = csv.writer(f)
        writer.writerow(["#", "条目", "情绪", "负面词", "正面词"])
        for x in labeled:
            writer.writerow([x.get("id", ""), x.get("text", ""), x["label"],
                             "/".join(x["neg_hits"]) or "-", "/".join(x["pos_hits"]) or "-"])

    priority_path = os.path.join(outdir, "flow_priority.csv")
    with open(priority_path, "w", newline="", encoding="utf-8-sig") as f:
        writer = csv.writer(f)
        writer.writerow(["优先级", "#", "条目", "情绪", "情绪分", "付费分", "流失分", "总分"])
        for r in rows:
            writer.writerow([r["level"], r.get("id", ""), r.get("text", ""), r["label"],
                             r["intensity"], r["willingness"], r["churn_risk"], r["total"]])

    n = len(rows) or 1
    dist = {lv: sum(1 for r in rows if r["level"] == lv) for lv in ("高", "中", "低")}
    summary = "工作流执行完成：%d 条条目完成情绪识别与优先级分级（高/中/低 = %d/%d/%d），流失风险条目 %d 条。" % (
        len(rows), dist["高"], dist["中"], dist["低"], len(churn_items))

    report_path = os.path.join(outdir, "flow_report.json")
    report = {
        "summary": summary,
        "steps": {
            "step1_情绪识别": "%d 条完成打标（负面 %d / 正面 %d / 中性 %d / 存疑 %d）" % (
                len(labeled),
                sum(1 for x in labeled if x["label"] == "负面"),
                sum(1 for x in labeled if x["label"] == "正面"),
                sum(1 for x in labeled if x["label"] == "中性"),
                sum(1 for x in labeled if x["label"] == "存疑")),
            "step2_优先级打分": "高/中/低 = %d/%d/%d" % (dist["高"], dist["中"], dist["低"]),
            "step3_流失风险标记": "%d 条命中流失表态词" % len(churn_items),
        },
        "deliverable": {
            "labels": [{"#": x.get("id", ""), "情绪": x["label"]} for x in labeled],
            "priority": [{"优先级": r["level"], "#": r.get("id", ""), "总分": str(r["total"]),
                          "情绪": r["label"]} for r in rows],
            "churn_watchlist": [r.get("id", "") for r in churn_items],
        },
        "assumption": [
            "步骤 1 打标采用情绪词表口径（负面 %d 词 / 正面 %d 词）；正负面同时命中计存疑。" % (len(NEG_WORDS), len(POS_WORDS)),
            "步骤 2 打分口径：情绪强度 40%（负面词命中 n 个记 6+2×(n-1) 分）、付费意愿 30%（含付费表述记 8 分，否则 5 分）、流失风险 30%（含流失表态记 8 分，否则 2 分）。",
            "步骤 3 流失风险标记仅覆盖命中流失表态词（换/卸载/不续费/退订）的条目，回访动作需人工确认。",
        ],
    }
    with open(report_path, "w", encoding="utf-8") as f:
        json.dump(report, f, ensure_ascii=False, indent=2)
    return [labels_path, priority_path, report_path]


def main():
    parser = argparse.ArgumentParser(description="情绪与优先级分级工作流 — 端到端编排脚本")
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
        items = raw if isinstance(raw, list) and raw else DEMO_INPUT
        if not items:
            print("输入为空，改用内置演示数据", file=sys.stderr)
            items = DEMO_INPUT
    else:
        parser.error("必须指定 --input 或 --demo 之一")
        return 1

    labeled = step1_sentiment(items)
    rows = step2_priority(labeled)
    churn_items = step3_churn_marks(rows)
    paths = write_outputs(labeled, rows, churn_items, args.outdir)

    print("工作流执行完成：%d 条条目分级，流失风险 %d 条" % (len(rows), len(churn_items)))
    for p in paths:
        print("已写出：%s" % p)
    return 0


if __name__ == "__main__":
    sys.exit(main())
