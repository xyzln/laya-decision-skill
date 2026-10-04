# 决策 schema 库（面向编码 / 运维 Agent）

## 现成 schema

放在 `assets/schemas/`，直接喂给 `laya_engine.py`（文件里已声明 `state_key` 与问题集）：

| 文件 | state 键 | 判什么 | 典型用途 |
|---|---|---|---|
| `code-review.json` | `diff` | 改动类型 / 影响范围 / 有无测试 / 是否需人审 / 是否破坏性 | PR 批量预筛，只把高风险挑给人看 |
| `issue-triage.json` | `issue` | 类别 / 严重度 / 是否可复现 / 是否要追问 / 是否重复 | Issue 自动分派与打标 |
| `command-risk.json` | `command` | 破坏性 / 影响范围 / 是否提权 / 是否需先确认 | 命令执行前的强制确认闸门 |
| `test-failure.json` | `failure` | 失败归因 / 能否定位 / 是否值得重试 / 是否需人介入 | CI 失败自动分类，先自动重试 flaky |
| `rag-relevance.json` | `passage` | 相关性 / 是否含答案 / 是否过时 / 质量分 | 检索后过滤，别把噪声塞进上下文 |
| `agent-trace.json` | `trace` | 进展状态 / 继续运行风险 / 是否需接管 / 是否在烧预算 | 长任务自我监控，卡死早停 |

## 设计一套问题的六个步骤

1. **先问"我要拿答案做什么"**。触发动作的（自动合并、自动执行）才需要严闸门；只打标看的，阈值可松。
2. **枚举答案集并穷尽**。每个 choice 必有兜底项，否则被迫硬选。
3. **给每个选项写判定标准，不要同义词**。`{"bug":"错误","defect":"缺陷"}` 会分不开；
   正例 `{"bugfix":"修正已有错误行为","feature":"新增能力"}`。
4. **noul 问可证伪的事实**。"是否明确提到要退款"✅；"用户是否满意"❌。
5. **一次问完所有问题**。一次结构化输出同时回答全部，不要拆多次。
6. **文本放到正确的键**。instructions 用反引号引用了字段（`` `diff` ``），state 必须放在那个键下。

## 反模式

| 反模式 | 后果 | 修法 |
|---|---|---|
| 把自由文本抽取当 choice 问 | 答案必落你没定义的类 | 需开放式抽取就用完整推理 |
| 选项语义重叠 | 置信度普遍偏低 | 重写 criteria 拉开距离 |
| 长文件整份丢进去 | 判之前没抽片段，易错 | 先抽关键片段 |
| 一个 schema 塞 30 个问题 | 单题预算被挤压 | 拆 2~3 次，每次 5~8 题 |
| 低置信也照样执行动作 | 把概率当事实 | `<0.5` 弃权，转 System 2 / 人工 |
| 高 stakes 只看置信度 | 过度自信致误执行 | 一律走 `human` 闸门 |

## 批量用法（量大的场景）

```bash
# 离线启发式：一个文件一行，逐行跑（基线用，真实生产请让大模型逐条判）
while IFS= read -r line; do
  python scripts/laya_engine.py decide --schema-file assets/schemas/test-failure.json \
    --state "$line" --min-confidence 0.6
done < ci_failures.txt
```

几十到几百条：让大模型按 `prompt` 子命令逐条产出答案 JSON，再批量 `validate`。
几千条以上：建议在调用方把答案聚成 JSONL 后逐行 `validate`。

## 接入新场景的最小闭环

1. 挑/改一个 schema；
2. 准备 30~100 条人工样本；
3. `python scripts/laya_engine.py selftest` 确认链路通；
4. 让大模型走 `prompt → 产出答案 → validate`，人工核对高/低置信分布；
5. 上线后按置信度分布定自动处理的覆盖率，低置信走人工 / System 2。
