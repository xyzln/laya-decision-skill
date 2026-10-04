#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""batch_eval.py - 批量评估/回归测试。

对一份样本集（jsonl）逐条跑 decide，输出每专家、每题的准确率/弃权率/升级率，
以及 verdict 分布，可写入审计日志。纯标准库。

用法：
  python3 batch_eval.py --samples samples.jsonl --out report.md [--audit-dir DIR]
样本行：
  {"schema_file":"experts/ops-sre/schema.json","state":"rm -rf /","answer":{...},"expected":{...}}
无 answer 时走离线启发式（仅用于回归基线，真实评估请带大模型 answer）。
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from collections import defaultdict

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import laya_engine as le  # noqa: E402


def load_samples(path):
    out = []
    with open(path, encoding="utf-8") as fh:
        for ln, line in enumerate(fh, 1):
            line = line.strip()
            if not line:
                continue
            try:
                out.append(json.loads(line))
            except json.JSONDecodeError as exc:
                print("batch_eval: 样本行 %d 非 JSON: %s" % (ln, exc), file=sys.stderr)
    return out


def resolve_schema(p):
    if os.path.isabs(p):
        return p
    return os.path.join(os.path.dirname(HERE), p)


def main():
    ap = argparse.ArgumentParser(description="Laya 批量评估")
    ap.add_argument("--samples", required=True)
    ap.add_argument("--out", help="markdown 报表输出路径")
    ap.add_argument("--audit-dir", help="写入审计日志的目录")
    ap.add_argument("--min-confidence", type=float, default=0.6)
    args = ap.parse_args()

    samples = load_samples(args.samples)
    if not samples:
        print("batch_eval: 无有效样本", file=sys.stderr)
        return 2

    per_expert = defaultdict(lambda: {"n": 0, "fields": 0, "adopted": 0,
                                      "correct": 0, "escalated": 0})
    per_q = defaultdict(lambda: {"fields": 0, "adopted": 0, "correct": 0})
    verdicts = defaultdict(int)

    for s in samples:
        sf = resolve_schema(s["schema_file"])
        _, questions = le.load_schema(sf)
        raw = s.get("answer")
        exp = s.get("expected", {})
        expert = s.get("expert") or os.path.basename(os.path.dirname(sf))
        if raw is not None:
            decision = le.normalize_answer(questions, raw)
            source = "llm"
        else:
            decision = le.heuristic_decide(s.get("state", ""), questions)
            source = "heuristic"
        le.apply_gate(decision, args.min_confidence)
        routing = le.route(questions, decision)
        pe = per_expert[expert]
        pe["n"] += 1
        for qid, ans in decision.items():
            pe["fields"] += 1
            per_q[qid]["fields"] += 1
            if ans["abstained"]:
                continue
            pe["adopted"] += 1
            per_q[qid]["adopted"] += 1
            if exp.get(qid) is not None and ans["value"] == exp.get(qid):
                pe["correct"] += 1
                per_q[qid]["correct"] += 1
        if routing["escalate"]:
            pe["escalated"] += 1
            verdicts[routing["escalate_to"] or "escalate"] += 1
        else:
            verdicts["pass"] += 1
        if args.audit_dir:
            le.write_audit({"expert": expert, "decision": decision,
                            "routing": routing, "source": source,
                            "min_confidence": args.min_confidence,
                            "state": s.get("state", "")[:200]}, args.audit_dir)

    lines = ["# Laya 批量评估报告", "",
             "- 样本数：%d" % len(samples),
             "- min_confidence：%s" % args.min_confidence, "", "## 各专家", "",
             "| 专家 | 样本 | 字段 | 采用率 | 采用准确率 | 升级率 |",
             "|------|------|------|--------|------------|--------|"]
    for e, v in sorted(per_expert.items()):
        adopt = v["adopted"] / v["fields"] if v["fields"] else 0
        acc = v["correct"] / v["adopted"] if v["adopted"] else 0
        esc = v["escalated"] / v["n"] if v["n"] else 0
        lines.append("| %s | %d | %d | %.2f | %.2f | %.2f |" % (
            e, v["n"], v["fields"], adopt, acc, esc))
    lines += ["", "## 各题型", "",
              "| 题型 | 采用率 | 采用准确率 |",
              "|------|--------|------------|"]
    for q, v in sorted(per_q.items()):
        adopt = v["adopted"] / v["fields"] if v["fields"] else 0
        acc = v["correct"] / v["adopted"] if v["adopted"] else 0
        lines.append("| %s | %.2f | %.2f |" % (q, adopt, acc))
    lines += ["", "## 结论分布", ""]
    for k, v in sorted(verdicts.items(), key=lambda kv: -kv[1]):
        lines.append("- %s：%d" % (k, v))
    lines.append("")
    if any("answer" not in s for s in samples):
        lines.append("> 注意：含无 answer 的样本，走离线启发式，仅作回归基线，不代表真实 LLM 表现。")
    report = "\n".join(lines)

    if args.out:
        with open(args.out, "w", encoding="utf-8") as fh:
            fh.write(report)
        print("评估报表已写入 %s" % args.out)
    else:
        print(report)
    return 0


if __name__ == "__main__":
    sys.exit(main())
