#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""schema_gen.py - 从自然语言描述 / JSON 示例生成 Laya 决策题型(schema)草稿。

纯标准库，离线。生成的草稿请人工补完每题的 instructions 与 criteria 标准描述。

用法：
  python3 schema_gen.py --desc "判断操作是否破坏性(是/否)，影响范围(低/中/高)，是否需提权" --out schema.json
  python3 schema_gen.py --example '{"status":"fail","retriable":true,"level":2}' --out schema.json
启发式规则：
  - 含「是否/能否/需不需要」+ 二值 → noul
  - 含枚举如「高/中/低」「A/B/C」「严重/一般」→ choice（≥4 档或含数字则 score）
  - 含「等级/评分/level/0-N」 → score
  - JSON 示例：bool→noul，int 小范围→score，str 受限枚举→choice，其余→choice 兜底
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys

_CJK = re.compile(r"[\u4e00-\u9fff]")

ENUM_HINT = re.compile(r"([一-龥A-Za-z]+/[一-龥A-Za-z]+(?:/[一-龥A-Za-z]+)+)")
BOOL_HINT = re.compile(r"(是否|能否|需[不]?需要|有没有|是不是|可[否]?以?|应[不]?应该)")
SCORE_HINT = re.compile(r"(等级|评分|打分|程度|level|score|0[-~]?\d|分级)")


def _dedup_preserve(seq):
    seen = set()
    out = []
    for x in seq:
        if x not in seen:
            seen.add(x)
            out.append(x)
    return out


def from_desc(desc: str) -> dict:
    questions = {}
    # 按顿号/逗号/分号拆分为子句
    clauses = re.split(r"[，,；;、\n]", desc)
    idx = 0
    for clause in clauses:
        clause = clause.strip().strip("()（）")
        if not clause:
            continue
        # 枚举：「X(高/中/低)」「影响范围 低/中/高」
        enum_m = ENUM_HINT.search(clause)
        if BOOL_HINT.search(clause) and not enum_m:
            qid = "q%d" % (idx + 1)
            questions[qid] = {"type": "noul",
                              "instructions": clause + "？",
                              "note": "请补全标准描述"}
            idx += 1
            continue
        if enum_m:
            opts = [o.strip() for o in enum_m.group(1).split("/") if o.strip()]
            opts = _dedup_preserve(opts)
            qid = "q%d" % (idx + 1)
            if SCORE_HINT.search(clause) or (len(opts) >= 4):
                questions[qid] = {"type": "score",
                                  "instructions": clause,
                                  "criteria": opts,
                                  "note": "score 按列表顺序编号 0..N"}
            else:
                questions[qid] = {"type": "choice",
                                  "instructions": clause,
                                  "criteria": {o: "（请补全：%s 的含义）" % o for o in opts}}
            idx += 1
            continue
        if SCORE_HINT.search(clause):
            qid = "q%d" % (idx + 1)
            questions[qid] = {"type": "score",
                              "instructions": clause,
                              "criteria": ["（低）", "（中）", "（高）"],
                              "note": "请补全等级标准"}
            idx += 1
            continue
        # 兜底：当作一道 choice
        qid = "q%d" % (idx + 1)
        questions[qid] = {"type": "choice",
                          "instructions": clause,
                          "criteria": {"yes": "是", "no": "否", "other": "其他"}}
        idx += 1
    return {"state_key": "request", "title": "（待命名）决策题型",
            "questions": questions}


def from_example(obj: dict) -> dict:
    questions = {}
    for k, v in obj.items():
        if isinstance(v, bool):
            questions[k] = {"type": "noul", "instructions": "字段 %s 的判定？" % k}
        elif isinstance(v, int) and 0 <= v <= 5:
            questions[k] = {"type": "score",
                            "instructions": "字段 %s 的等级(0..N)？" % k,
                            "criteria": ["等级0", "等级1", "等级2", "等级3", "等级4", "等级5"]}
        elif isinstance(v, str):
            questions[k] = {"type": "choice",
                            "instructions": "字段 %s 的取值？" % k,
                            "criteria": {v: "（示例值，请补全枚举）", "other": "其他"}}
        else:
            questions[k] = {"type": "choice",
                            "instructions": "字段 %s 的类别？" % k,
                            "criteria": {"a": "（请补全）", "other": "其他"}}
    return {"state_key": "request", "title": "（由示例推断）决策题型",
            "questions": questions}


def main():
    ap = argparse.ArgumentParser(description="从描述/示例生成 schema 草稿")
    g = ap.add_mutually_exclusive_group(required=True)
    g.add_argument("--desc", help="自然语言描述（含枚举/是否）")
    g.add_argument("--example", help="JSON 示例对象")
    ap.add_argument("--out", help="输出 schema.json 路径")
    ap.add_argument("--state-key", default="request")
    args = ap.parse_args()

    if args.desc:
        schema = from_desc(args.desc)
    else:
        try:
            obj = json.loads(args.example)
        except json.JSONDecodeError as exc:
            print("schema_gen: 示例非 JSON: %s" % exc, file=sys.stderr)
            return 2
        if not isinstance(obj, dict):
            print("schema_gen: 示例必须是 JSON 对象", file=sys.stderr)
            return 2
        schema = from_example(obj)
    schema["state_key"] = args.state_key

    text = json.dumps(schema, ensure_ascii=False, indent=2)
    if args.out:
        with open(args.out, "w", encoding="utf-8") as fh:
            fh.write(text)
        print("schema 草稿已写入 %s" % args.out)
    else:
        print(text)
    print("\n[提示] 草稿中的 instructions/criteria 仅为占位，请人工补完标准描述后再使用。",
          file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
