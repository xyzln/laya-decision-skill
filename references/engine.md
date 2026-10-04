# 引擎 API（`scripts/laya_engine.py`）

纯标准库实现，**零外部依赖**（无需 torch / transformers / huggingface_hub / laya 包），
跨平台（Windows / Linux / macOS）。退出码：0 正常 | 2 用法错误 | 3 schema 错误。

## 命令行

```bash
# 1) 生成 System 1 决策提示词（给大模型照着答）
python scripts/laya_engine.py prompt --schema-file assets/schemas/code-review.json \
  --state "$(cat diff.txt)"

# 2) 归一化 + 闸门 + 路由 一条大模型答案
python scripts/laya_engine.py validate \
  --schema-file assets/schemas/code-review.json \
  --answer '{"change_type":{"value":"refactor","confidence":0.82}, ...}' \
  --min-confidence 0.5 --json

# 3) 完整链路：给了 --answer 走 LLM 路径，否则走离线启发式
python scripts/laya_engine.py decide \
  --schema-file assets/schemas/command-risk.json \
  --state 'rm -rf /var/log/app && chmod 777 /etc' --json

# 4) 自检（内置样例，无网络/无 torch）
python scripts/laya_engine.py selftest
```

`--answer` 也可从文件读：`--answer-file path.json`（`-` 为 stdin）。
`--min-confidence` 默认 0.5，建议生产设 0.6~0.7。

## Python 直调

```python
import sys, json
sys.path.insert(0, "scripts")
import laya_engine as L

state_key, questions = L.load_schema("assets/schemas/code-review.json")
prompt = L.build_decision_prompt(diff_text, state_key, questions)   # 给大模型当提示词

# 大模型产出 answer 后：
decision = L.normalize_answer(questions, llm_answer_json)
L.apply_gate(decision, min_confidence=0.6)
routing = L.route(questions, decision)
# routing["escalate"] / ["escalate_to"] / ["blocking_flags"]

# 或直接走离线启发式（无大模型）：
res = L.run_decide(state_text, state_key, questions, None, 0.5, "heuristic")
```

## 输出结构

```json
{
  "state_key": "diff",
  "decision": {
    "<qid>": {"type": "choice|score|noul", "value": <标签|等级|布尔>,
              "confidence": 0.0, "abstained": false}
  },
  "routing": {
    "escalate": false,
    "escalate_to": null,            // "system2" 低置信弃权 | "human" 高危闸门
    "abstained": [],
    "blocking_flags": [],
    "note": "..."
  },
  "source": "llm | heuristic",
  "usage": {"generated_tokens_estimate": 0, "external_model": false}
}
```

`usage.external_model` 恒为 `false`：本协议**不调用任何外部模型**，完全自包含。
