# 置信度、闸门与升级策略

## 置信度从哪来

本 skill 用**大模型的自我报告置信度**作为 `confidence` 字段（0~1）。它不是 Laya 原版
RLCD 训练出的真·校准概率，而是代理值——详见 `principle.md`。门控一律用这一个字段，
不要用其他语义不同的量做跨题型比较。

## 门控表

| `confidence` | 处置 |
|---|---|
| ≥ 0.70 | 直接采用，按结果分支 |
| 0.50 – 0.70 | 采用，但输出标注"低置信" |
| < 0.50（或 `abstained:true`） | **弃权**：升级到完整推理（System 2）或人工，不要静默采用 |

脚本里用 `--min-confidence 0.7` 自动把低于闸值的字段置空并打 `abstained:true`。
**生产建议设 0.6~0.7**：因为大模型普遍过度自信，0.5 太松。

## 两类升级（`routing.escalate_to`）

1. **`system2`（低置信弃权）**：有字段 `confidence < min_confidence` → 触发完整推理重判，
   或转人工。脚本会把 `abstained` 字段的 `value` 置空，避免被误用。
2. **`human`（高危闸门）**：以下字段被你判为 `true` 时强制升级人工/用户确认——
   `destructive` `privileged` `requires_confirmation` `needs_human` `needs_review`
   `breaking` `is_sensitive` `unsafe` `critical` 等（含这些关键词的字段名）。
   这是"预测与行动分离"的硬保障：**宁可卡住，不可误执行**。

## 预测与行动分离（铁律）

概率只喂策略，策略由你定。

- ✅ churn_risk = 0.95 → 自动开一条人工复核任务
- ❌ churn_risk = 0.95 → 自动退款
- ✅ "命令 destructive=true" → 强制要求用户确认后才执行
- ❌ "命令安全=false" → 直接执行

不可逆、涉钱、涉权限的动作，一律不把决策当唯一依据。

## 校准会失真的地方

1. **标签体系换了没重验**：新标签先用 `selftest` + 真实数据人工抽查一批。
2. **选项语义重叠**：两个 criteria 写得像同义词，你会分裂概率、整体偏低——标签设计问题。
3. **state 过长被你自己截断**：判之前先抽关键片段，别把整份日志丢进去。
4. **分布漂移**：业务话术变了，置信度尺度会慢慢失真，定期重测。

## 接入前必做

```bash
python scripts/laya_engine.py selftest
```

确认链路通；再用你自己的 30~100 条样本，让大模型走一遍第 2~4 步，
人工核对高/低置信样本的分布是否合理。
