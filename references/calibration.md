# 置信度、闸门与升级策略

## 置信度从哪来
本版让**加载 skill 的大模型自我报告**每条答案的 `confidence`（0~1）。这不等同于 Laya 原版的
RLCD 真·校准，模型可能过于自信。因此闸门要设得保守。

## 闸门 min_confidence
- 每条答案低于 `min_confidence` 的字段被标记为 `abstained`（choice/score 的答案清零，noul 保留值但标记）。
- 缺字段、值越界、无法解析布尔 → 直接弃权。
- **建议值 0.6~0.7**。高 stakes 场景即便高置信也走高危闸门，不要只看阈值。

## 高危闸门（命中即"human"优先）
当某个 `noul` 字段值为 `True` 且字段 id 含以下关键词，判定为高危闸门，路由到 **人工确认**，
禁止自动执行：
```
destructive, privileged, requires_confirmation, needs_human,
needs_review, breaking, is_sensitive, unsafe, critical
```
各专家的默认高危闸门：
- 安全合规：`requires_approval`
- 代码评审：`breaking`, `needs_human_review`
- 运维风险：`destructive`, `privileged`, `requires_confirmation`
- 需求分诊：`needs_human`
- 质量闸门：`blocks_release`

## 升级路由优先级
1. 命中高危闸门 → `human`（最保守，先停下等人工）。
2. 有弃权但无高危 → `system2`（交给该专家做完整深度推理，再回 Laya 复核）。
3. 全达标 → `pass`（可进入执行，不可逆动作仍需二次确认）。

## 多专家共识（consensus）
- 任一专家 `human` → 团队级 `human`。
- 否则任一专家 `system2` → 团队级 `system2`。
- 否则 → `pass`。

## 校准扫描（calibrate.py）
用带标签样本集扫描 `min_confidence` 网格，统计每个阈值的采用率/采用准确率/弃权率/升级率，
给出在可接受弃权率内最大化准确率的推荐阈值。

```bash
python3 scripts/calibrate.py --samples tests/samples.jsonl --out cal.md
```
样本格式（每行 JSON）：
```json
{"schema_file":"experts/ops-sre/schema.json","state":"rm -rf /","answer":{...},"expected":{...}}
```
- 有 `answer`：评估大模型真实表现。
- 无 `answer`：走离线启发式，仅作回归基线。

## 批量评估（batch_eval.py）
对样本集逐条 `decide`，输出各专家/各题型的准确率、弃权率、升级率与结论分布，可写审计。
```bash
python3 scripts/batch_eval.py --samples tests/samples.jsonl --out eval.md --audit-dir ~/.cache/laya-decision
```

## 审计（audit）
每次 `decide` / `validate` 带 `--audit-dir` 会把结果追加到 `audit.jsonl`；
`audit` 子命令输出升级率、各专家命中统计，便于回溯与持续改进。
