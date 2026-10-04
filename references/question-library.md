# 决策 schema 库与题型规范

Laya 的答案空间必须**预先固定**。一个 schema 是一个 JSON，含 `state_key`（待判断文本的键名）
与 `questions`（问题集）。每个问题只有三种题型：

| 题型 | 字段 | 答案形态 | 说明 |
|------|------|----------|------|
| `choice` | `criteria`(标签->标准描述) | 字符串标签 | 分类/路由/选型 |
| `score` | `criteria`(列表或有序字典) | 整数 0..N-1 | 等级/严重度/紧急度 |
| `noul` | 无 criteria | 布尔 true/false | 是否/有无/命中 |

每题必须有 `instructions`（题干）。`noul` 字段 id 若含高危关键词会触发人工闸门（见 calibration.md）。

## 通用 schema 库（`assets/schemas/`，单引擎期沉淀，仍可用）
- `code-review.json`：改动类型/爆炸半径/测试覆盖/是否需人工/是否破坏性
- `issue-triage.json`：工单类别/严重度/紧急度/是否需人工
- `command-risk.json`：破坏性/提权/需确认/影响范围/可逆
- `test-failure.json`：失败类型/根因层/阻塞发布/可重试
- `rag-relevance.json`：检索相关性/是否有依据/是否需补充
- `agent-trace.json`：轨迹异常/是否越权/是否需干预

## 专家 schema 库（`experts/<id>/schema.json`，专家团用）
| 专家 | schema 路径 | 题型 |
|------|-------------|------|
| 安全合规 | `experts/security-compliance/schema.json` | data_classification(choice) / involves_pii / cross_boundary / regulatory_risk(score) / requires_approval(noul,高闸) |
| 代码评审 | `experts/code-reviewer/schema.json` | change_type(choice) / breaking(noul,高闸) / test_coverage / severity(score) / needs_human_review(noul,高闸) |
| 运维风险 | `experts/ops-sre/schema.json` | destructive/privileged/requires_confirmation(noul,高闸) / blast_radius(score) / reversible |
| 需求分诊 | `experts/product-triage/schema.json` | intent(choice) / urgency(score) / category(choice) / needs_human(noul,高闸) |
| 质量闸门 | `experts/quality-gate/schema.json` | status(choice) / severity(score) / root_area(choice) / blocks_release(noul,高闸) |

## 设计新 schema 的要点
1. 一题一判：每个问题只回答一个明确的可判定事实，避免"综合性"大题。
2. 选项互斥且穷尽：choice 的 `criteria` 加 `other` 兜底；score 等级从 0 起连续编号。
3. 题干写明判定口径：`instructions` 要可操作，别写"是否合适"这种主观题。
4. 高危动作显式成 noul 闸门：把 destructive/privileged/needs_human 等单独成题，便于自动拦截。
5. 题目数 ≤ 32，选项 ≤ 32，score 等级 ≤ 10（引擎强约束）。

## 用 schema_gen.py 出草稿
```bash
python3 scripts/schema_gen.py --desc "是否破坏性(是/否)，影响范围(低/中/高/致命)，是否需提权" --out my.json
# 或
python3 scripts/schema_gen.py --example '{"status":"fail","retriable":true,"level":2}' --out my.json
```
生成的 `criteria`/`instructions` 仅为占位，**必须人工补完标准描述**后再用。
