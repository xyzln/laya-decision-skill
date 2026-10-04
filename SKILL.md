---
name: laya-decision
description: 自包含复刻 Laya 的 System 1 决策原理，让加载本 skill 的大模型自己充当决策引擎，零外部依赖（无需 laya 包 / torch / 联网下载权重）。用于分类、打标、路由、评分排序、是否/风险判定、优先级、LLM-as-judge、工单分流、内容审核、输入护栏、模型路由、命令风险闸门等高频结构化小决策；也支持批量打标。输出单次结构化答案 + 校准式置信度，可设阈值弃权并升级到完整推理或人工。不适用于需要生成文本、长链推理或解释的任务。
---

# Laya 决策协议（大模型自驱版）

本 skill **复刻 Laya 的作业原理**，但**不依赖任何外部模型**：
原版 Laya 是一个 322M 参数的 System 1 决策模型，需要联网下载权重 + 装 torch。
这里把它的核心机制搬进协议本身——**加载本 skill 的你（大模型）就是这台决策引擎**，
只靠 `scripts/laya_engine.py`（纯标准库、零 pip 依赖）做协议强制、归一化、闸门与路由。

Laya 被复刻的三件事：

1. **结构化决策瓶颈**：答案空间必须预先固定为三类题型（choice/score/noul），
   你只输出一个 JSON 对象，**不生成散文**，因此永远不会产出你没定义的标签或坏 JSON。
2. **校准式置信度 + 弃权闸门**：每条答案带一个 0~1 的置信度；低于 `min_confidence`
   的字段**弃权（abstain）**，升级给完整推理（System 2）或人工，绝不静默采用。
3. **预测与行动分离**：决策只喂给策略，不直接触发不可逆动作。

> 校准说明：原版用 RLCD 训练出真·校准概率；本版用你的**自我报告置信度**作为代理。
> 它不如 RLCD 严格，但仍是可用的门控杠杆——务必配合下面的弃权闸门与高危升级使用。

## 何时用本协议

| 场景 | 用？ | 说明 |
|---|---|---|
| 分类/打标/路由（issue 分派、日志归类、意图识别） | ✅ | choice 主力场景 |
| 排序评分（紧急度、严重性、复杂度、质量分） | ✅ | score 返回等级 |
| 是否判定（是否需人工、是否含敏感、是否越权、命令是否高危） | ✅ | noul 返回 true/false |
| 批量给大量文本打同一套结构化标签 | ✅✅ | 离线启发式或你逐个判 |
| 输入/输出护栏、越狱与注入检测、模型路由 | ✅ | 见 schema 库 |
| LLM-as-judge / 评测打分 | ✅ | 比自由打分更稳、可门控 |
| 需要生成内容 / 多步推理 / 需要解释为什么 | ❌ | 交给完整推理 |
| 答案空间无法预先枚举（开放式抽取、自由文本） | ❌ | 必须先固化标签集 |
| 高风险不可逆动作的唯一依据 | ❌ | 见"预测与行动分离" |

## 三种题型（答案空间必须预先固定）

```json
{
  "state_key": "diff",
  "questions": {
    "change_type": {                       // choice：从命名集合选一个
      "type": "choice",
      "instructions": "这段 `diff` 的改动属于哪一类？",
      "criteria": {
        "bugfix": "修正已有错误行为、边界条件或崩溃",
        "feature": "新增能力、接口或配置项",
        "refactor": "重构结构、命名或依赖，对外行为不变",
        "other": "以上都不符合或无法判断"            // 永远留兜底项
      }
    },
    "blast_radius": {                      // score：有序等级，返回整数 0..n-1
      "type": "score",
      "instructions": "这段 `diff` 的影响范围有多大？",
      "criteria": ["单点", "局部", "跨模块", "系统性"]
    },
    "needs_human_review": {                // noul：是否，返回 true/false
      "type": "noul",
      "instructions": "存在需要人类逐行复核才能合并的风险吗（安全/资金/权限/不可逆）？"
    }
  }
}
```

写 criteria 的三条硬规则：
1. **每个选项写判定标准**，不要只给标签名——名字相似的选项你会混。
2. **必须有一个兜底项**（`other` / "以上都不符合"），否则你被迫在都不合适时硬选。
3. **noul 问法要可被证伪**：用"是否明确提到要退款"而非"用户是否不满意"（主观感受你我难一致）。

## 工作流程（你如何"跑起来"）

给定 state 文本 + 一个 schema 文件（或你现写的一个），按四步：

**第 1 步 — 生成决策提示词（可选但推荐）**

```bash
python scripts/laya_engine.py prompt --schema-file assets/schemas/code-review.json \
  --state "$(cat diff.txt)"
```
它会输出一段 System 1 决策提示词，告诉你输出什么格式的 JSON。照着它答即可。

**第 2 步 — 你输出结构化答案**

只输出如下 JSON（不要解释）：

```json
{
  "change_type": {"value": "refactor", "confidence": 0.82},
  "blast_radius": {"value": 1, "confidence": 0.60},
  "needs_human_review": {"value": false, "confidence": 0.88}
}
```

`value`：choice 填标签名 / score 填整数等级 / noul 填 true·false；
`confidence`：你对该条答案正确的校准概率（0~1），不确定就给低值。

**第 3 步 — 归一化 + 闸门 + 路由**

```bash
python scripts/laya_engine.py validate \
  --schema-file assets/schemas/code-review.json \
  --answer '{"change_type":{"value":"refactor","confidence":0.82}, ...}' \
  --min-confidence 0.5 --json
```

脚本会：把你的标签落到选项集、score 限幅、置信度夹到 [0,1]；
低于闸值的字段标 `abstained:true` 并置空；返回 `routing` 决定升级方向。

**第 4 步 — 按 routing 行动**

- `routing.escalate == false`：直接采用，按结果分支。
- `routing.escalate_to == "system2"`：有字段弃权 → 用完整推理重判或转人工。
- `routing.escalate_to == "human"`：**命中高危闸门**（destructive/privileged/
  requires_confirmation/needs_human/breaking 等字段为 true）→ 必须先停下确认，
  **禁止自动执行**。

## 离线 / 批量模式（零大模型、零网络也能跑）

不传 `--answer` 时，`decide` 走内置**离线启发式**（关键词/词库兜底），
用于演示、CI、或没有大模型调用时的 baseline：

```bash
python scripts/laya_engine.py decide \
  --schema-file assets/schemas/command-risk.json \
  --state 'rm -rf /var/log/app && chmod 777 /etc' --json
```

启发式只是地板，真实生产用第 2~4 步的"你当引擎"路径。

## 置信度闸门（不要盲信答案）

| `confidence` | 处置 |
|---|---|
| ≥ 0.70 | 直接采用，按结果分支 |
| 0.50 – 0.70 | 采用，但输出标注"低置信" |
| < 0.50（或 `abstained:true`） | **弃权**：升级到完整推理或人工 |

用 `--min-confidence 0.7` 让脚本自动把低置信字段置空并打 `abstained`。

**预测与行动分离**：概率只喂策略。churn=0.95 可以自动开一条复核任务，
但绝不能自动退款；判"命令高危"可以强制要求确认，但不能替他执行。

## 已知边界

- 本版的置信度是你的自我报告，不是 RLCD 真·校准；高 stakes 场景务必保留人工闸门。
- choice 选项 ≤ 32，score 等级 ≤ 10，问题数 ≤ 32。
- state 过长时你自己先抽关键片段再判（无滑窗机制）。
- 跨平台：WorkBuddy / Codex / Claude Code 都只读取 `SKILL.md` + `scripts/` + `references/`，
  无需任何额外安装。

## 交付前自检

任何用本协议替掉的判断，第一次用内置样例验证整条链路：

```bash
python scripts/laya_engine.py selftest
```

详细原理、引擎 API、schema 库见 `references/`。
