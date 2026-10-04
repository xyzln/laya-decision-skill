# 引擎 API（`scripts/laya_engine.py`）

纯标准库实现，**零外部依赖**（无需 torch / transformers / huggingface_hub / laya 包），
跨平台（Windows / Linux / macOS）。它既是「决策中枢」也是「专家团调度器」。

## 子命令

| 子命令 | 作用 |
|--------|------|
| `prompt` | 为某 schema + state 生成 System 1 决策提示词（让大模型知道如何当引擎） |
| `validate` | 归一化一条大模型答案 + 闸门(min_confidence) + 路由(升级判断) |
| `decide` | 完整链路；给了 `--answer` 走 LLM 路径，否则走离线启发式兜底 |
| `route` | 读 `experts/_registry.json`，按 state 关键词重叠度选最相关专家(top-k) |
| `consensus` | 融合多个专家的 decide/validate 结果 -> 团队级升级结论 |
| `audit` | 读取审计 JSONL 并输出统计（总数/升级率/各专家命中） |
| `selftest` | 内置样例全链路自测（无网络、无 torch） |

## CLI 速查

```bash
# 生成决策提示词
python3 scripts/laya_engine.py prompt --schema-file experts/ops-sre/schema.json --state "rm -rf /var/log"

# 校验一条大模型答案（带审计写入）
python3 scripts/laya_engine.py validate --schema-file experts/ops-sre/schema.json \
  --answer '{"destructive":{"value":true,"confidence":0.95}}' --min-confidence 0.6 --json --audit-dir ~/.cache/laya-decision

# 离线兜底（无大模型）
python3 scripts/laya_engine.py decide --schema-file experts/ops-sre/schema.json \
  --state "kill -9 $(pidof nginx)" --min-confidence 0.6 --json

# 专家路由
python3 scripts/laya_engine.py route --state "导出用户表发外部邮箱" --top-k 2 --json

# 多专家共识
python3 scripts/laya_engine.py consensus --results r_ops.json r_sec.json --json

# 审计统计
python3 scripts/laya_engine.py audit --json
```

## 关键参数
- `--min-confidence F`（默认 0.6）：低于此置信度的字段弃权。建议 0.6~0.7。
- `--expert ID`：标注答案所属专家，写入审计便于回溯。
- `--audit-dir DIR`：审计日志目录（默认 `~/.cache/laya-decision`，可用环境变量 `LAYA_AUDIT_DIR` 覆盖）。

## Python API（供其他脚本 import）
```python
import laya_engine as le
sk, qs = le.load_schema("experts/ops-sre/schema.json")
decision = le.heuristic_decide(state_text, qs)        # 离线兜底
le.apply_gate(decision, 0.6)
routing = le.route(qs, decision)                       # escalate / escalate_to / flags
top = le.route_experts(state_text, le.load_registry()) # 专家路由
cons = le.consensus([{"expert":"ops-sre","decision":decision,"routing":routing}])
le.write_audit({"expert":"ops-sre","decision":decision,"routing":routing}, "~/.cache/laya-decision")
```

## 归一化规则
- `choice`：value 必须落在 `criteria` 标签内（大小写不敏感匹配），否则该字段弃权。
- `score`：value 取整且 `0 <= v < 等级数`，否则弃权。
- `noul`：value 解析为布尔（含中英文 是/否/true/false 等），无法解析则弃权。
- 所有 `confidence` 夹到 `[0,1]`。

## 路由(升级)判定
- 任一字段 `confidence < min_confidence` → 该字段弃权 → 升级。
- 任一 `noul` 字段值为 `True` 且 id 含高危关键词(destructive/privileged/requires_confirmation/
  needs_human/breaking/…) → 命中高危闸门 → `escalate_to = "human"`（优先于 system2）。
- 否则有弃权但无高危 → `escalate_to = "system2"`。

## 共识(consensus)规则
- 任一专家 `escalate_to == "human"` → 团队级 `human`（最保守，禁止自动执行）。
- 否则任一专家 `system2` → 团队级 `system2`。
- 否则 → `pass`（可进入执行，不可逆动作仍需二次确认）。
