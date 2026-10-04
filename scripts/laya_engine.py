#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""laya_engine.py - 自包含 System 1 决策协议引擎。

复刻 Laya 的作业原理，但**不依赖** laya 包 / torch / 任何模型权重下载：
加载本 skill 的大模型本身就是决策引擎——它针对一组固定题型作答，并给出每条答案的校准式置信度。
本脚本负责：

  1. 加载并校验 schema（题型 + 答案空间必须预先固定）
  2. 生成 System 1 决策提示词（让大模型知道如何"当引擎"）
  3. 归一化 / 校验大模型的答案（choice 落到标签、score 限幅、置信度夹到 [0,1]）
  4. 应用置信度闸门 min_confidence -> 低置信字段弃权(abstain)
  5. 路由：任一字段弃权或命中高危闸门 -> 升级到 System 2（完整推理）或人工
  6. 内置零依赖启发式兜底，使 skill 在没有联网 / 没有外部模型时也能跑（离线 baseline）

子命令：
  prompt    为某 schema + state 生成 System 1 决策提示词
  validate  归一化 + 闸门 + 路由 一条大模型给出的答案 JSON
  decide    完整链路；给了 --answer 走 LLM 路径，否则走离线启发式
  selftest  用内置样例把整条链路跑一遍（无网络、无 torch、无 pip 依赖）

退出码：0 正常 | 2 用法错误 | 3 schema 错误。

纯标准库实现，跨平台（Windows / Linux / macOS 均可），无需 pip install 任何东西。
"""
from __future__ import annotations

import argparse
import json
import math
import os
import re
import sys
from typing import Any, Dict, List, Optional, Tuple

HERE = os.path.dirname(os.path.abspath(__file__))
DEFAULT_SCHEMAS = os.path.join(os.path.dirname(HERE), "assets", "schemas")

EXIT_USAGE = 2
EXIT_SCHEMA = 3

VALID_TYPES = ("choice", "score", "noul")

# noul / 含这些关键词的字段命中为 true 时，被视为"需要确认/升级"的高危闸门。
WATCH_KEYWORDS = ("destructive", "privileged", "requires_confirmation", "needs_human",
                  "needs_review", "breaking", "is_sensitive", "unsafe", "critical")


# --------------------------------------------------------------------------- #
# schema 加载与校验
# --------------------------------------------------------------------------- #
def die(code: int, *msg: str) -> "NoReturn":  # type: ignore[name-defined]
    for line in msg:
        print("laya_engine: %s" % line, file=sys.stderr)
    raise SystemExit(code)


def load_schema(path: str) -> Tuple[str, Dict[str, Any]]:
    """读取 JSON schema，返回 (state_key, questions)。"""
    try:
        with open(path, encoding="utf-8") as handle:
            raw = json.load(handle)
    except FileNotFoundError:
        die(EXIT_SCHEMA, "找不到 schema 文件: %s" % path)
    except json.JSONDecodeError as exc:
        die(EXIT_SCHEMA, "%s 不是合法 JSON (%s)" % (path, exc))
    if not isinstance(raw, dict):
        die(EXIT_SCHEMA, "schema 顶层必须是对象")
    questions = raw.get("questions")
    if not isinstance(questions, dict) or not questions:
        die(EXIT_SCHEMA, "%s 必须有非空的 questions 对象" % path)
    state_key = raw.get("state_key") or "request"
    validate_questions(questions)
    return state_key, questions


def validate_questions(questions: Dict[str, Any]) -> None:
    if len(questions) > 32:
        die(EXIT_SCHEMA, "问题数 %d 超过 32 上限" % len(questions))
    for qid, qdef in questions.items():
        if not isinstance(qdef, dict):
            die(EXIT_SCHEMA, "问题 %r 必须是对象" % qid)
        qtype = qdef.get("type")
        if qtype not in VALID_TYPES:
            die(EXIT_SCHEMA, "问题 %r 的 type=%r，必须是 choice/score/noul" % (qid, qtype))
        if "instructions" not in qdef:
            die(EXIT_SCHEMA, "问题 %r 缺少 instructions" % qid)
        if qtype == "choice":
            crit = qdef.get("criteria")
            if not isinstance(crit, dict) or not crit:
                die(EXIT_SCHEMA, "choice 问题 %r 需要非空的 criteria 字典" % qid)
            if len(crit) > 32:
                die(EXIT_SCHEMA, "choice 问题 %r 选项 %d 超过 32" % (qid, len(crit)))
        elif qtype == "score":
            crit = qdef.get("criteria")
            if not isinstance(crit, (list, dict)) or not crit:
                die(EXIT_SCHEMA, "score 问题 %r 需要非空的 criteria 列表/字典" % qid)
            levels = list(crit) if isinstance(crit, dict) else crit
            if len(levels) > 10:
                die(EXIT_SCHEMA, "score 问题 %r 等级 %d 超过 10" % (qid, len(levels)))
        # noul 不需要 criteria


# --------------------------------------------------------------------------- #
# 分词（中英混合，纯标准库，无 jieba）
# --------------------------------------------------------------------------- #
_CJK = re.compile(r"[\u4e00-\u9fff]")
_ASCII_WORD = re.compile(r"[A-Za-z][A-Za-z0-9_]+")
_DIGIT = re.compile(r"\d+")


def keywords(text: str) -> set:
    """从一段文本抽出关键词集合：CJK 二元组 + 长度>=2 的 ASCII 词 + 数字。"""
    out: set = set()
    # 去掉所有标点/空白，得到纯字符序列用于 CJK 二元组
    cleaned = re.sub(r"\s+", "", text)
    chars = [c for c in cleaned if _CJK.match(c)]
    for i in range(len(chars) - 1):
        out.add(chars[i] + chars[i + 1])
    for m in _ASCII_WORD.findall(text):
        if len(m) >= 2:
            out.add(m.lower())
    for m in _DIGIT.findall(text):
        out.add(m)
    return out


def overlap(a: set, b: set) -> int:
    return len(a & b)


# --------------------------------------------------------------------------- #
# 离线启发式（零依赖 baseline；无网络、无 torch）
# --------------------------------------------------------------------------- #
# 通用 noul 词库：正向(命中为 true)/负向(命中为 false) 线索。仅作兜底 baseline，
# 真实引擎是大模型本身。覆盖中英文常见安全/只读/破坏性表达。
_POS_CUES = {
    # 中文
    "删除", "覆盖", "修改", "写入", "重写", "不可逆", "破坏", "失效", "泄露", "越权",
    "崩溃", "丢失", "高危", "危险", "敏感", "提权", "授权", "强制", "必须", "需要",
    "明确", "涉及", "包含", "sudo", "root",
    # 英文 / 命令
    "delete", "drop", "truncate", "format", "rm", "mv", "chmod", "chown", "kill",
    "shutdown", "reboot", "privilege", "sudo", "destructive", "overwrite", "mutate",
    "wipe", "purge", "reset", "migrate",
}
_NEG_CUES = {
    # 中文
    "否", "不", "无", "没", "未", "非", "仅", "只", "只读", "查看", "搜索", "打印",
    "显示", "列出", "读取", "安全", "无副作用",
    # 英文 / 命令
    "no", "not", "none", "readonly", "read-only", "safe", "dry-run", "dryrun", "list",
    "grep", "cat", "ls", "head", "tail", "find", "print", "echo", "read", "view",
    "search", "show", "select", "get", "query",
}


def _heuristic_choice(state_keys: set, crit: Dict[str, str]) -> Tuple[Optional[str], float]:
    scores = {}
    for label, standard in crit.items():
        kw = keywords(standard)
        scores[label] = overlap(state_keys, kw)
    ranked = sorted(scores.items(), key=lambda kv: kv[1], reverse=True)
    top_label, top = ranked[0]
    second = ranked[1][1] if len(ranked) > 1 else 0
    # 命中数为 0 时回退到兜底项（若存在），否则弃权
    if top == 0:
        if "other" in scores:
            return "other", 0.0
        return None, 0.0
    # 置信度来自 top 与 second 的差距（margin），映射到 [0,1]
    margin = (top - second) / (top + second + 1e-6)
    conf = round(min(0.95, 0.4 + 0.55 * margin), 3)
    return top_label, conf


def _heuristic_score(state_keys: set, levels: List[str]) -> Tuple[int, float]:
    scores = [overlap(state_keys, keywords(lvl)) for lvl in levels]
    best = max(range(len(scores)), key=lambda i: scores[i])
    top = scores[best]
    if top == 0:
        # 没有任何等级词命中：用中间值 + 极低置信，标记弃权
        return len(levels) // 2, 0.0
    second = sorted(scores, reverse=True)[1] if len(scores) > 1 else 0
    margin = (top - second) / (top + second + 1e-6)
    conf = round(min(0.95, 0.4 + 0.55 * margin), 3)
    return best, conf


def _heuristic_noul(state_text: str) -> Tuple[bool, float]:
    # 直接统计正向/负向线索词出现次数（更稳，纯标准库即可）
    pos_hits = sum(1 for c in _POS_CUES if c in state_text)
    neg_hits = sum(1 for c in _NEG_CUES if c in state_text)
    score = pos_hits - neg_hits
    prob = 1.0 / (1.0 + math.exp(-1.2 * score))
    conf = round(min(0.9, 0.3 + 0.4 * abs(score)), 3)
    # 当无任何线索时给中性 0.5，但置信度拉到最低（强制弃权/升级）
    if pos_hits == 0 and neg_hits == 0:
        return False, 0.0
    return (prob >= 0.5), conf


def heuristic_decide(state_text: str, questions: Dict[str, Any]) -> Dict[str, Any]:
    state_keys = keywords(state_text)
    out: Dict[str, Any] = {}
    for qid, qdef in questions.items():
        qtype = qdef["type"]
        if qtype == "choice":
            val, conf = _heuristic_choice(state_keys, qdef["criteria"])
            out[qid] = {"type": "choice", "value": val, "confidence": conf,
                        "abstained": val is None}
        elif qtype == "score":
            levels = qdef["criteria"]
            levels = list(levels.values()) if isinstance(levels, dict) else levels
            val, conf = _heuristic_score(state_keys, levels)
            out[qid] = {"type": "score", "value": val, "confidence": conf,
                        "abstained": conf <= 0.0}
        else:
            val, conf = _heuristic_noul(state_text)
            out[qid] = {"type": "noul", "value": val, "confidence": conf,
                        "abstained": conf <= 0.0}
    return out


# --------------------------------------------------------------------------- #
# 归一化 / 校验大模型答案
# --------------------------------------------------------------------------- #
def _as_bool(v: Any) -> Optional[bool]:
    if isinstance(v, bool):
        return v
    if isinstance(v, (int, float)) and not isinstance(v, bool):
        return bool(v)
    if isinstance(v, str):
        s = v.strip().lower()
        if s in ("true", "yes", "y", "1", "是", "需要", "要"):
            return True
        if s in ("false", "no", "n", "0", "否", "不", "无", "未"):
            return False
    return None


def normalize_answer(questions: Dict[str, Any], raw: Dict[str, Any]
                     ) -> Dict[str, Any]:
    """把大模型产出的 answer JSON 归一化为统一结构。"""
    out: Dict[str, Any] = {}
    for qid, qdef in questions.items():
        if qid not in raw:
            out[qid] = {"type": qdef["type"], "value": None, "confidence": 0.0,
                        "abstained": True, "note": "missing"}
            continue
        item = raw[qid] if isinstance(raw[qid], dict) else {"value": raw[qid]}
        qtype = qdef["type"]
        conf = float(item.get("confidence", item.get("answer_confidence", 0.0)))
        conf = min(1.0, max(0.0, conf))
        if qtype == "choice":
            crit = qdef["criteria"]
            want = str(item.get("value", "")).strip()
            key = None
            if want in crit:
                key = want
            else:
                low = {k.lower(): k for k in crit}
                key = low.get(want.lower())
            out[qid] = {"type": "choice", "value": key, "confidence": conf,
                        "abstained": key is None}
            if key is None:
                out[qid]["note"] = "value %r 不在选项集内" % want
        elif qtype == "score":
            levels = qdef["criteria"]
            n = len(levels)
            try:
                val = int(round(float(item.get("value"))))
            except (TypeError, ValueError):
                val = None
            if val is None or not (0 <= val < n):
                out[qid] = {"type": "score", "value": None, "confidence": 0.0,
                            "abstained": True, "note": "score 越界或非整数"}
            else:
                out[qid] = {"type": "score", "value": val, "confidence": conf,
                            "abstained": False}
        else:  # noul
            val = _as_bool(item.get("value"))
            out[qid] = {"type": "noul", "value": val, "confidence": conf,
                        "abstained": val is None}
            if val is None:
                out[qid]["note"] = "noul 值无法解析为布尔"
    return out


# --------------------------------------------------------------------------- #
# 闸门 + 路由
# --------------------------------------------------------------------------- #
def apply_gate(decision: Dict[str, Any], min_confidence: float) -> Dict[str, Any]:
    for qid, ans in decision.items():
        if ans["confidence"] < min_confidence:
            ans["abstained"] = True
            if ans["type"] in ("choice", "score"):
                ans["value"] = None
            # noul 保留 value 但标记弃权，由路由决定升级
    return decision


def route(questions: Dict[str, Any], decision: Dict[str, Any]) -> Dict[str, Any]:
    abstained = [qid for qid, a in decision.items() if a["abstained"]]
    flags = []
    for qid, a in decision.items():
        if a["type"] == "noul" and a["value"] is True:
            if any(k in qid.lower() for k in WATCH_KEYWORDS):
                flags.append(qid)
    escalate = bool(abstained) or bool(flags)
    escalate_to = None
    if escalate:
        # 有高危闸门命中 -> 人工/用户确认优先；否则低置信 -> System 2 完整推理
        escalate_to = "human" if flags else "system2"
    return {
        "escalate": escalate,
        "escalate_to": escalate_to,
        "abstained": abstained,
        "blocking_flags": flags,
        "note": _route_note(escalate, escalate_to, abstained, flags),
    }


def _route_note(escalate, to, abstained, flags) -> str:
    if not escalate:
        return "全部字段置信度达标，可直接采用（仍不可触发不可逆动作）。"
    if to == "human":
        return ("命中高危闸门 %s：必须先停下进行人工/用户确认，禁止自动执行。" %
                ", ".join(flags))
    return ("字段 %s 置信度低于闸门，弃权：升级到 System 2 完整推理或转人工，"
            "不要静默采用。" % ", ".join(abstained))


# --------------------------------------------------------------------------- #
# 提示词生成（让大模型知道如何当 System 1 引擎）
# --------------------------------------------------------------------------- #
def build_decision_prompt(state_text: str, state_key: str,
                          questions: Dict[str, Any]) -> str:
    lines = [
        "你是一个 System 1 决策引擎。给定一段 state 和一组**固定题型**的问题，",
        "你只输出一个 JSON 对象，绝不要解释、不要多余文字。",
        "",
        "输出格式：{\"<问题id>\": {\"value\": <答案>, \"confidence\": <0到1的校准概率>}}",
        "  - choice 题：value 是 criteria 里的某个标签名（字符串）",
        "  - score 题：value 是整数等级（0 起，上界为等级数-1）",
        "  - noul 题：value 是 true / false（布尔）",
        "  - confidence：你对该条答案正确的校准概率（0~1），不确定就给低值",
        "",
        "state（键=%s）：" % state_key,
        state_text,
        "",
        "问题集：",
    ]
    for qid, qdef in questions.items():
        qtype = qdef["type"]
        instr = qdef.get("instructions", "")
        lines.append("- %s [%s] %s" % (qid, qtype, instr))
        if qtype == "choice":
            for label, std in qdef["criteria"].items():
                lines.append("    · %s：%s" % (label, std))
        elif qtype == "score":
            crit = qdef["criteria"]
            crit = list(crit.values()) if isinstance(crit, dict) else crit
            for i, lvl in enumerate(crit):
                lines.append("    · %d：%s" % (i, lvl))
        # noul 无选项
    lines.append("")
    lines.append("只输出 JSON：")
    return "\n".join(lines)


# --------------------------------------------------------------------------- #
# 决策入口
# --------------------------------------------------------------------------- #
def run_decide(state_text: str, state_key: str, questions: Dict[str, Any],
               raw_answer: Optional[Dict[str, Any]], min_confidence: float,
               source_label: str) -> Dict[str, Any]:
    if raw_answer is not None:
        decision = normalize_answer(questions, raw_answer)
    else:
        decision = heuristic_decide(state_text, questions)
    apply_gate(decision, min_confidence)
    routing = route(questions, decision)
    return {
        "state_key": state_key,
        "decision": decision,
        "routing": routing,
        "source": source_label,
        "usage": {"generated_tokens_estimate": 0, "external_model": False},
        "min_confidence": min_confidence,
    }


# --------------------------------------------------------------------------- #
# 内置样例 / 自测
# --------------------------------------------------------------------------- #
SELFTEST_CASES = [
    {
        "name": "代码评审-破坏性改动",
        "state_key": "diff",
        "state": ("diff --git a/auth.py b/auth.py\n"
                  "-def verify(token):\n-    return cache.get(token)\n"
                  "+def verify(token, secret=None):\n+    if secret != MASTER:\n"
                  "+        raise PermissionError('denied')\n"
                  " 删除了旧的短路返回，增加提权校验，改动涉及公共接口签名"),
        "questions": {
            "change_type": {"type": "choice", "instructions": "改动属于哪类？",
                            "criteria": {"bugfix": "修正错误行为", "feature": "新增能力",
                                         "refactor": "重构结构不改行为", "other": "以上都不符合"}},
            "breaking_change": {"type": "noul",
                                "instructions": "会破坏已有公开行为/签名吗？"},
        },
        "expect": {"change_type": "feature", "breaking_change": True},
    },
    {
        "name": "命令风险-只读",
        "state_key": "command",
        "state": "grep -rn 'TODO' ./src | head -20",
        "questions": {
            "destructive": {"type": "noul", "instructions": "会删除/覆盖/不可逆修改吗？"},
            "privileged": {"type": "noul", "instructions": "需要提权/sudo/root 吗？"},
            "scope": {"type": "score", "instructions": "影响范围？",
                      "criteria": ["只读：查看/搜索/打印", "工作区内：仅改项目目录文件",
                                   "系统级：改环境/全局配置/服务", "外部：影响远端/生产"]},
        },
        "expect": {"destructive": False, "privileged": False, "scope": 0},
    },
]


def selftest() -> int:
    print("laya_engine 自测（零依赖、无联网、无 torch）\n")
    all_ok = True
    for case in SELFTEST_CASES:
        qs = case["questions"]
        # 走 LLM 路径用 mock 答案（模拟大模型自身判断），以验证归一化+闸门+路由
        mock = {}
        exp = case["expect"]
        for qid, qdef in qs.items():
            qtype = qdef["type"]
            if qtype == "choice":
                mock[qid] = {"value": exp[qid], "confidence": 0.86}
            elif qtype == "noul":
                mock[qid] = {"value": exp[qid], "confidence": 0.91}
            else:
                mock[qid] = {"value": exp[qid], "confidence": 0.8}
        res = run_decide(case["state"], case["state_key"], qs, mock, 0.5, "llm(mock)")
        ok = all(res["decision"][q]["value"] == exp[q] for q in exp)
        all_ok = all_ok and ok and not res["routing"]["escalate"]
        print("[%s] %s" % ("PASS" if ok else "FAIL", case["name"]))
        print("  decision: %s" % json.dumps(res["decision"], ensure_ascii=False))
        print("  routing : %s" % json.dumps(res["routing"], ensure_ascii=False))
        print()

    # 离线启发式也跑一遍，证明无外部模型也能产出
    h = run_decide(SELFTEST_CASES[1]["state"], SELFTEST_CASES[1]["state_key"],
                   SELFTEST_CASES[1]["questions"], None, 0.5, "heuristic")
    print("[INFO] 离线启发式(无需大模型/网络):")
    print("  decision: %s" % json.dumps(h["decision"], ensure_ascii=False))
    print("  source  : %s | external_model=%s" %
          (h["source"], h["usage"]["external_model"]))
    return 0 if all_ok else EXIT_SCHEMA


# --------------------------------------------------------------------------- #
# CLI
# --------------------------------------------------------------------------- #
def read_answer(path: Optional[str], inline: Optional[str]) -> Optional[Dict[str, Any]]:
    if inline is None and path is None:
        return None
    src = inline if inline is not None else _read_file(path)
    try:
        parsed = json.loads(src)
    except json.JSONDecodeError as exc:
        die(EXIT_USAGE, "answer 不是合法 JSON (%s)" % exc)
    if not isinstance(parsed, dict):
        die(EXIT_USAGE, "answer 必须是问题id -> {value, confidence} 的对象")
    return parsed


def _read_file(path: str) -> str:
    if path == "-":
        return sys.stdin.read()
    try:
        with open(path, encoding="utf-8") as handle:
            return handle.read()
    except OSError as exc:
        die(EXIT_USAGE, "无法读取 %s (%s)" % (path, exc))


def emit(payload: Dict[str, Any], as_json: bool) -> None:
    if as_json:
        print(json.dumps(payload, ensure_ascii=False, indent=2))
    else:
        print(json.dumps(payload, ensure_ascii=False, default=str))


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        prog="laya_engine",
        description="自包含 System 1 决策协议引擎（复刻 Laya 原理，零外部依赖）。")
    json_parent = argparse.ArgumentParser(add_help=False)
    json_parent.add_argument("--json", action="store_true", help="以 JSON 输出")
    sub = parser.add_subparsers(dest="cmd", required=True)

    p_prompt = sub.add_parser("prompt", parents=[json_parent],
                              help="生成 System 1 决策提示词")
    p_prompt.add_argument("--schema-file", required=True)
    p_prompt.add_argument("--state", required=True, help="待判断的文本")

    p_val = sub.add_parser("validate", parents=[json_parent],
                           help="归一化+闸门+路由 一条答案")
    p_val.add_argument("--schema-file", required=True)
    p_val.add_argument("--answer", help="答案 JSON 字符串")
    p_val.add_argument("--answer-file", help="答案 JSON 文件('-'为stdin)")
    p_val.add_argument("--min-confidence", type=float, default=0.5)

    p_dec = sub.add_parser("decide", parents=[json_parent],
                           help="完整链路(LLM答案或离线启发式)")
    p_dec.add_argument("--schema-file", required=True)
    p_dec.add_argument("--state", required=True)
    p_dec.add_argument("--answer", help="大模型答案 JSON 字符串")
    p_dec.add_argument("--answer-file", help="大模型答案 JSON 文件")
    p_dec.add_argument("--min-confidence", type=float, default=0.5)
    p_dec.add_argument("--source", default="llm", help="来源标签，默认 llm")

    sub.add_parser("selftest", parents=[json_parent],
                   help="跑内置样例(无网络/无torch)")

    args = parser.parse_args(argv)

    if args.cmd == "selftest":
        return selftest()

    if args.cmd == "prompt":
        sk, questions = load_schema(args.schema_file)
        print(build_decision_prompt(args.state, sk, questions))
        return 0

    if args.cmd == "validate":
        _, questions = load_schema(args.schema_file)
        raw = read_answer(args.answer_file, args.answer)
        if raw is None:
            die(EXIT_USAGE, "validate 需要 --answer 或 --answer-file")
        decision = normalize_answer(questions, raw)
        apply_gate(decision, args.min_confidence)
        payload = {"decision": decision, "routing": route(questions, decision),
                   "source": "llm", "min_confidence": args.min_confidence}
        emit(payload, args.json)
        return 0

    if args.cmd == "decide":
        sk, questions = load_schema(args.schema_file)
        raw = read_answer(args.answer_file, args.answer)
        source = "heuristic" if raw is None else (args.source or "llm")
        payload = run_decide(args.state, sk, questions, raw, args.min_confidence, source)
        emit(payload, args.json)
        return 0
    return 0


if __name__ == "__main__":
    sys.exit(main())
