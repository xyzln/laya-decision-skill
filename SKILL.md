---
name: laya-decision
description: >
  自包含复刻 Laya 的 System 1 决策原理，作为「1 个决策中枢 + N 个领域专家」的专家团在
  WorkBuddy 中运行。让加载本 skill 的大模型自己充当决策引擎，零外部依赖（无需 laya 包 /
  torch / 联网下载权重）。用于对任意 state（代码 diff、shell 命令、工单、安全操作、测试报告等）
  做固定题型的快决策，并输出校准式置信度；低置信或命中高危闸门时升级到 System 2 深度推理或人工。
  触发词：决策、路由、专家团、审批闸门、风险评估、是否放行、置信度、升级人工、代码评审、命令风险、
  安全合规、工单分诊、发布闸门、laya、system1。
version: "2.0.0"
author: "laya-decision team"
updated: "2026-10-04"
metadata:
  mode: self-contained
  external_deps: none
  runtime: llm-as-engine
---

# Laya 决策中枢 · 专家团版

把 Laya 的 **System 1 决策原理** 复刻成一个协议：加载本 skill 的大模型就是决策引擎，
针对一组**固定题型**产出结构化 JSON 答案（不写散文），每条答案带 0~1 的校准式置信度。
本 skill 是专家团的**中枢**，负责路由、闸门、共识与审计；5 个领域专家各自出题与深度推理。

## 为什么这样设计（三条原理）
1. **结构化决策瓶颈**：答案空间预先固定（choice / score / noul），大模型只能从已定义选项里选，
   不会产出你没定义的标签，也不会跑偏成闲聊。
2. **校准式置信度 + 弃权闸门**：每条答案带置信度，低于 `min_confidence` 的字段**弃权(abstain)**，
   绝不静默采用；命中高危闸门（destructive / privileged / requires_confirmation / needs_human / breaking …）
   一律**升级人工确认**，禁止自动执行不可逆动作。
3. **预测与行动分离**：决策只给「判级 + 升级建议」，真正的执行（改代码、跑命令、发消息）必须由人工或
   二次确认触发——决策引擎不碰扳机。

## 专家团架构
中枢按 `experts/_registry.json` 路由到最相关的专家；每个专家是一个可被 WorkBuddy 识别的独立 skill
（`expert-<id>`），也可由中枢在当前会话内编排。

| 专家 | skill 名 | 负责维度 | 高危闸门 |
|------|----------|----------|----------|
| 安全合规 | `expert-security-compliance` | 数据分级 / PII / 出网 / 审批 | requires_approval |
| 代码评审 | `expert-code-reviewer` | 改动类型 / 破坏性 / 测试覆盖 | breaking, needs_human_review |
| 运维风险 | `expert-ops-sre` | 破坏性 / 提权 / 爆炸半径 / 回滚 | destructive, privileged, requires_confirmation |
| 需求分诊 | `expert-product-triage` | 意图 / 紧急度 / 归属 / 是否需人工 | needs_human |
| 质量闸门 | `expert-quality-gate` | 通过/失败 / 严重度 / 发布阻塞 | blocks_release |

## 四阶段工作流（每次决策都走完）
**阶段 0 · 路由（route）** —— 读 state，调 `scripts/laya_engine.py route` 选最相关专家（top-k）。
也可用关键词/语义由你自己判断。多域相关时取 top-1 主专家，其余作旁证。

**阶段 1 · 出题（schema）** —— 加载该专家的 `schema.json`，题目与答案空间已固定。
若 state 明显跨多域，可为每个相关专家分别出题，最后做共识。

**阶段 2 · 作答（System 1）** —— 让大模型按固定题型产出答案 JSON：
`{"<qid>": {"value": <答案>, "confidence": <0~1>}}`。
用 `scripts/laya_engine.py prompt --schema-file <schema> --state <文本>` 生成标准提示词。

**阶段 3 · 校准 / 闸门 / 共识 / 审计**
- `validate` 归一化答案、按 `min_confidence` 闸门、给出路由（升级 system2 / human / pass）。
- 任一字段弃权或命中高危闸门 → **不要自动执行**：切到该专家 `SKILL.md` 的
  「System 2 深度推理协议」做完整分析，再回 Laya 复核。
- 多专家结论用 `consensus` 融合：任一专家判 `human` → 团队级 `human`；否则 `system2`；否则 `pass`。
- 每次决策写审计 JSONL（见 `audit`）。

## 与 WorkBuddy 的两种集成方式
- **方式 A · 自包含（默认，零额外 agent）**：当前会话的 LLM 直接当引擎，按本协议的四个阶段在对话里
  切换专家人格（读 `experts/<id>/SKILL.md` 的 System 2 提示词即可扮演该专家）。最省资源。
- **方式 B · 隔离并行（重负载/需隔离）**：用 WorkBuddy 的 Agent 工具 spawn `expert-<id>` 作为独立子
  agent 并行分析，把各自的结构化结论回传中枢，调 `consensus` 汇总。适合多专家独立取证。

## 脚本速查（`${CODEBUDDY_SKILL_DIR}` 指向本 skill 根目录）
```bash
# 专家路由：state -> 最相关专家
python3 ${CODEBUDDY_SKILL_DIR}/scripts/laya_engine.py route --state "rm -rf / && sudo ..."

# 生成 System 1 决策提示词
python3 ${CODEBUDDY_SKILL_DIR}/scripts/laya_engine.py prompt --schema-file ${CODEBUDDY_SKILL_DIR}/experts/ops-sre/schema.json --state "..."

# 归一化 + 闸门 + 路由 一条大模型答案
python3 ${CODEBUDDY_SKILL_DIR}/scripts/laya_engine.py validate --schema-file <schema> --answer '<JSON>' --min-confidence 0.6 --json

# 离线兜底（无大模型/无网络）：直接对 state 出启发式决策
python3 ${CODEBUDDY_SKILL_DIR}/scripts/laya_engine.py decide --schema-file <schema> --state "..." --min-confidence 0.6 --json

# 多专家共识
python3 ${CODEBUDDY_SKILL_DIR}/scripts/laya_engine.py consensus --results r1.json r2.json --json

# 审计统计
python3 ${CODEBUDDY_SKILL_DIR}/scripts/laya_engine.py audit --json

# 部署专家团到 WorkBuddy
python3 ${CODEBUDDY_SKILL_DIR}/scripts/install.py            # 软链到 ~/.codebuddy/skills
python3 ${CODEBUDDY_SKILL_DIR}/scripts/install.py --mode copy # 复制（独立、可移植）

# 辅佐功能
python3 ${CODEBUDDY_SKILL_DIR}/scripts/calibrate.py  --samples tests/samples.jsonl --out cal.md   # 阈值扫描
python3 ${CODEBUDDY_SKILL_DIR}/scripts/batch_eval.py  --samples tests/samples.jsonl --out eval.md  # 批量评估
python3 ${CODEBUDDY_SKILL_DIR}/scripts/schema_gen.py  --desc "是否破坏性(是/否)，影响范围(低/中/高)" # 生成 schema 草稿
```

## 诚实边界（务必遵守）
- 本版的置信度是**大模型自我报告**，不是 Laya 原版的 RLCD 真·校准。因此 `min_confidence` 建议设
  **0.6~0.7**；任何高 stakes（删数据、提权、出网、退款、发布）一律走 `human` 闸门，**不要自动执行**。
- 离线启发式仅是 baseline / CI / 批量用，不能替代大模型判断。
- 专家 schema 与 System 2 提示词需要你按业务持续打磨；`schema_gen.py` 只出草稿。

详细协议、引擎 API、校准与专家团协同见 `references/`（`principle.md` / `engine.md` / `calibration.md` /
`question-library.md` / `expert-team.md`）。
