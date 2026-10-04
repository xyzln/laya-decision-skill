#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""calibrate.py - 置信度闸门校准扫描。

给定一份带标签样本集（jsonl，每行含 schema_file/state/answer/expected），
扫描 min_confidence 网格，统计每个阈值下的：采用率、采用字段准确率、弃权率、升级率，
给出推荐阈值（在可接受弃权率内最大化准确率）。

纯标准库；样本里 answer 应是大模型产出的 {qid:{value,confidence}}，离线时也可省略 answer 走启发式。

用法：
  python3 calibrate.py --samples samples.jsonl --out report.md
样本行格式：
  {"schema_file":"assets/schemas/code-review.json","state":"...","answer":{...},"expected":{"change_type":"refactor",...}}
"""
from __future__ import annotations

import argparse
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import laya_engine as le  # noqa: E402


def load_samples(path: str):
    out = []
    with open(path, encoding="utf-8") as fh:
        for ln, line in enumerate(fh, 1):
            line = line.strip()
            if not line:
                continue
            try:
                out.append(json.loads(line))
            except json.JSONDecodeError as exc:
                print("calibrate: 样本行 %d 非 JSON: %s" % (ln, exc), file=sys.stderr)
    return out


def eval_at(samples, mc):
    """在给定 min_confidence 下评估全部样本，返回汇总指标。"""
    total_fields = 0
    adopted = 0          # 未被弃权的字段
    adopted_correct = 0 # 采用且答案正确
    escalated = 0
    for s in samples:
        sp = s["schema_file"] if os.path.isabs(s["schema_file"]) \
            else os.path.join(os.path.dirname(HERE), s["schema_file"])
        sk, questions = le.load_schema(sp)
        raw = s.get("answer")
        if raw is not None:
            decision = le.normalize_answer(questions, raw)
        else:
            decision = le.heuristic_decide(s.get("state", ""), questions)
        le.apply_gate(decision, mc)
        routing = le.route(questions, decision)
        exp = s.get("expected", {})
        for qid, ans in decision.items():
            total_fields += 1
            if ans["abstained"]:
                continue
            adopted += 1
            want = exp.get(qid)
            if want is not None and ans["value"] == want:
                adopted_correct += 1
        if routing["escalate"]:
            escalated += 1
    adopt_rate = adopted / total_fields if total_fields else 0.0
    acc = adopted_correct / adopted if adopted else 0.0
    esc_rate = escalated / len(samples) if samples else 0.0
    return {
        "min_confidence": mc,
        "total_fields": total_fields,
        "adopt_rate": round(adopt_rate, 3),
        "adopt_accuracy": round(acc, 3),
        "abstain_rate": round(1 - adopt_rate, 3),
        "escalate_rate": round(esc_rate, 3),
    }


def main():
    ap = argparse.ArgumentParser(description="Laya 置信度闸门校准扫描")
    ap.add_argument("--samples", required=True, help="带标签样本 jsonl")
    ap.add_argument("--out", help="输出 markdown 报表路径")
    ap.add_argument("--lo", type=float, default=0.3)
    ap.add_argument("--hi", type=float, default=0.9)
    ap.add_argument("--step", type=float, default=0.05)
    ap.add_argument("--max-abstain", type=float, default=0.35,
                    help="推荐阈值的最大可容忍弃权率")
    args = ap.parse_args()

    samples = load_samples(args.samples)
    if not samples:
        print("calibrate: 无有效样本", file=sys.stderr)
        return 2

    grid = []
    mc = args.lo
    while mc <= args.hi + 1e-9:
        grid.append(eval_at(samples, round(mc, 3)))
        mc += args.step

    # 推荐：弃权率 <= max_abstain 中准确率最高者；若都超，取弃权率最低者
    feasible = [g for g in grid if g["abstain_rate"] <= args.max_abstain]
    if feasible:
        best = max(feasible, key=lambda g: g["adopt_accuracy"])
    else:
        best = min(grid, key=lambda g: g["abstain_rate"])

    lines = ["# Laya 置信度闸门校准报告", "",
             "- 样本数：%d" % len(samples),
             "- 推荐阈值 `min_confidence = %s`（弃权率 %.2f，采用准确率 %.2f）" % (
                 best["min_confidence"], best["abstain_rate"], best["adopt_accuracy"]),
             "", "## 阈值扫描", "",
             "| min_conf | 采用率 | 采用准确率 | 弃权率 | 升级率 |",
             "|----------|--------|------------|--------|--------|"]
    for g in grid:
        lines.append("| %s | %s | %s | %s | %s |" % (
            g["min_confidence"], g["adopt_rate"], g["adopt_accuracy"],
            g["abstain_rate"], g["escalate_rate"]))
    lines.append("")
    lines.append("> 说明：采用准确率只在被采纳字段上计算；弃权字段升级到 System 2/人工，")
    lines.append("> 不计入错误。阈值越高越保守（弃权多、召回稳），越低越激进（可能误采纳）。")
    report = "\n".join(lines)

    if args.out:
        with open(args.out, "w", encoding="utf-8") as fh:
            fh.write(report)
        print("校准报表已写入 %s" % args.out)
    else:
        print(report)
    print("\n推荐 min_confidence = %s" % best["min_confidence"], file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
