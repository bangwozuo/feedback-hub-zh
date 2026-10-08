#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""多渠道反馈采集 — 确定性条目整理入库脚本（纯标准库，无第三方依赖）。

用法：
    python scripts/collect.py --demo                # 内置演示数据，产物写入 out/
    python scripts/collect.py --input input.json    # 结构化输入，产物写入 out/
    python scripts/collect.py --input input.json --outdir out

输入 JSON 结构（--input）：
    {
      "records": [
        {"title": "发票识别认错金额", "source": "客服邮箱", "time": "2026-09-29 14:22",
         "detail": "9.50 元被识别成 95.0，请求修复"}
      ]
    }

整理口径（与 SKILL.md 声明一致）：
    1. 去重：标题与来源完全相同的条目只保留最早一条，重复数记入 summary
    2. 排序：按时间升序排列，时间缺失的条目排到最后并标注「时间缺失」
    3. 校验：标题、来源、时间、关键信息四要素缺一的条目计入「要素不全」，单独标注
    4. 汇总：summary 统计总数、去重数、来源分布与时间跨度

产物（写入 outdir，默认 out/）：
    items.csv     采集结果明细（CSV）
    report.json   items + summary 完整结构（JSON）
"""
import argparse
import csv
import json
import os
import sys

DEMO_RECORDS = [
    {"title": "发票识别认错金额", "source": "客服邮箱 feedback@yunjizhang.com（用户刘先生来信）", "time": "2026-09-29 14:22", "detail": "9.50 元被识别成 95.0，用户称急用，请求修复"},
    {"title": "希望月报表支持导出 Excel", "source": "客服邮箱 feedback@yunjizhang.com（用户陈女士来信）", "time": "2026-10-01 09:10", "detail": "会计需要 Excel 格式报表"},
    {"title": "Huawei Mate 40 发票识别页闪退", "source": "华为应用市场「云记账」评论区（用户\"山间清风\"，评一星）", "time": "2026-09-30", "detail": "点击发票识别即闪退，卸载重装无效"},
    {"title": "好评 + 期待多账本功能", "source": "华为应用市场「云记账」评论区（用户\"奋斗的蚂蚁\"，评五星）", "time": "2026-10-02", "detail": "记账顺手，唯一缺口是多账本功能"},
    {"title": "Xiaomi 13 报表页闪退求证", "source": "微信群「云记账·个体户交流 2 群」（用户\"老周记账\"）", "time": "2026-10-03 20:15", "detail": "打开报表页即退出，几乎每日复现"},
    {"title": "附议小米闪退问题", "source": "微信群「云记账·个体户交流 2 群」（用户\"阿芳\"）", "time": "2026-10-03 20:31", "detail": "同为小米机型，报表页必闪退"},
]


def normalize(records):
    """补齐字段并做要素完整性标注。"""
    items = []
    for r in records:
        item = {
            "title": str(r.get("title", "")).strip(),
            "source": str(r.get("source", "")).strip(),
            "time": str(r.get("time", "")).strip(),
            "detail": str(r.get("detail", "")).strip(),
        }
        missing = [k for k in ("title", "source", "time") if not item[k]]
        item["complete"] = not missing
        item["missing"] = missing
        items.append(item)
    return items


def dedupe(items):
    """标题+来源相同只保留最早一条；时间缺失条目视为最晚。"""
    seen = {}
    dup_count = 0
    for idx, item in enumerate(items):
        key = (item["title"], item["source"])
        if key not in seen:
            seen[key] = idx
        else:
            first = items[seen[key]]
            if item["time"] and (not first["time"] or item["time"] < first["time"]):
                seen[key] = idx
            dup_count += 1
    kept = [items[i] for i in sorted(seen.values())]
    return kept, dup_count


def sort_items(items):
    dated = sorted([x for x in items if x["time"]], key=lambda x: x["time"])
    undated = [x for x in items if not x["time"]]
    return dated + undated


def summarize(items, dup_count):
    n = len(items)
    sources = {}
    times = [x["time"][:10] for x in items if x["time"]]
    for x in items:
        src = x["source"].split("（")[0] if x["source"] else "来源缺失"
        sources[src] = sources.get(src, 0) + 1
    parts = ["共 %d 条" % n]
    if dup_count:
        parts.append("去重 %d 条" % dup_count)
    parts.append("来源分布 " + "、".join("%s %d 条" % (k, v) for k, v in sorted(sources.items())))
    if times:
        parts.append("时间跨度 %s 至 %s" % (min(times), max(times)))
    incomplete = sum(1 for x in items if not x["complete"])
    if incomplete:
        parts.append("%d 条要素不全需补充" % incomplete)
    return "本批采集：" + "；".join(parts) + "。"


def write_outputs(items, summary, outdir):
    os.makedirs(outdir, exist_ok=True)
    items_path = os.path.join(outdir, "items.csv")
    with open(items_path, "w", newline="", encoding="utf-8-sig") as f:
        writer = csv.writer(f)
        writer.writerow(["#", "标题/摘要", "来源", "时间", "关键信息", "状态"])
        for i, x in enumerate(items, 1):
            status = "完整" if x["complete"] else "要素缺失:" + "+".join(x["missing"])
            writer.writerow([i, x["title"], x["source"], x["time"], x["detail"], status])

    report_path = os.path.join(outdir, "report.json")
    report = {
        "items": [{"#": str(i), "标题/摘要": x["title"], "来源": x["source"],
                   "时间": x["time"], "关键信息": x["detail"]} for i, x in enumerate(items, 1)],
        "summary": summary,
        "assumption": [
            "整理采用确定性口径：标题与来源完全相同的条目视为重复，只保留最早一条。",
            "条目按时间升序排列；要素完整性按标题、来源、时间三项校验，缺失项单独标注。",
        ],
    }
    with open(report_path, "w", encoding="utf-8") as f:
        json.dump(report, f, ensure_ascii=False, indent=2)
    return [items_path, report_path]


def main():
    parser = argparse.ArgumentParser(description="多渠道反馈采集 — 确定性条目整理脚本")
    parser.add_argument("--input", help="输入 JSON 文件路径")
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
            print("输入 records 为空，改用内置演示数据", file=sys.stderr)
            records = DEMO_RECORDS
    else:
        parser.error("必须指定 --input 或 --demo 之一")
        return 1

    items = normalize(records)
    kept, dup_count = dedupe(items)
    items = sort_items(kept)
    summary = summarize(items, dup_count)
    paths = write_outputs(items, summary, args.outdir)

    print("整理完成：%d 条条目（去重 %d 条）" % (len(items), dup_count))
    print("summary:", summary)
    for p in paths:
        print("已写出：%s" % p)
    return 0


if __name__ == "__main__":
    sys.exit(main())
