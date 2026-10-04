# 专家团部署与协同（expert-team）

本 skill 在 WorkBuddy 中作为「**1 个决策中枢 + N 个领域专家**」运行。本文说明注册表、
两种集成方式、部署步骤与如何新增专家。

## 架构
```
laya-decision/                ← 中枢 skill（被 WorkBuddy 识别为 laya-decision）
├─ SKILL.md                   ← 专家团编排协议（四阶段工作流）
├─ scripts/laya_engine.py     ← 路由/闸门/共识/审计 中枢引擎
├─ experts/
│  ├─ _registry.json          ← 专家注册表（id / skill 名 / schema / 触发词 / 权重）
│  ├─ security-compliance/    ← 专家 = 独立可识别 skill
│  │  ├─ SKILL.md             ← 角色 + 触发词 + System 2 深度推理协议
│  │  └─ schema.json          ← 该专家的出题（固定题型）
│  ├─ code-reviewer/  ops-sre/  product-triage/  quality-gate/  （同上结构）
└─ references/  assets/  tests/
```

## 注册表 `_registry.json` 字段
```json
{
  "default_min_confidence": 0.6,
  "experts": {
    "ops-sre": {
      "skill_name": "expert-ops-sre",   // 部署后在 ~/.codebuddy/skills/ 下的目录名
      "dir": "experts/ops-sre",          // 相对 skill 根
      "schema": "experts/ops-sre/schema.json",
      "title": "运维/SRE 风险专家",
      "weight": 1.3,                      // 路由加权（越高越优先）
      "triggers": ["运维","rm","sudo",...] // 路由关键词
    }
  }
}
```

## 两种集成方式
**A · 自包含（默认）**：当前会话 LLM 直接当引擎，按 SKILL.md 四阶段在对话里切换专家人格。
读 `experts/<id>/SKILL.md` 的「System 2 深度推理协议」即可扮演该专家做深度分析。零额外 agent。

**B · 隔离并行**：用 WorkBuddy 的 **Agent 工具** spawn `expert-<id>` 作为独立子 agent 并行取证，
各专家返回结构化结论 JSON，中枢调 `consensus` 汇总。适合多专家独立、需隔离上下文的重负载场景。

## 部署到 WorkBuddy
`scripts/install.py` 把本 skill 与每个专家软链（或复制）到 `~/.codebuddy/skills/`：
```bash
# 软链（默认，便于改了立即生效）
python3 scripts/install.py
# 复制（独立、可移植，不依赖源目录）
python3 scripts/install.py --mode copy
# 指定目标 / 强制覆盖
python3 scripts/install.py --target /path/to/skills --force
# 只部署中枢，不部署专家
python3 scripts/install.py --core-only
```
部署后**重启 WorkBuddy**，`laya-decision` 与 `expert-*` 即被识别。中枢负责编排，专家可被单独触发。

## 一次完整决策（示例：破坏性命令）
```bash
# 0. 路由
python3 scripts/laya_engine.py route --state "rm -rf /var/log && sudo chmod 777 /etc"
# -> top: ops-sre(2.6), security-compliance(0.0)

# 1+2. 出题 + 作答（让 LLM 按 ops-sre/schema.json 产出 JSON）

# 3. 校验 + 闸门 + 路由
python3 scripts/laya_engine.py validate --schema-file experts/ops-sre/schema.json \
  --answer '{"destructive":{"value":true,"confidence":0.95},"privileged":{"value":true,"confidence":0.95},"requires_confirmation":{"value":true,"confidence":0.9},"blast_radius":{"value":3,"confidence":0.85},"reversible":{"value":false,"confidence":0.8}}' \
  --min-confidence 0.6 --json
# -> escalate_to: "human"（命中 destructive/privileged/requires_confirmation 三道高闸）
# 此时不要自动执行；切到 expert-ops-sre 的 System 2 协议做完整分析，再回 Laya 复核。
```

## 多专家共识示例
```bash
# 安全专家与运维专家分别对同一操作出结论，写 r_sec.json / r_ops.json，再：
python3 scripts/laya_engine.py consensus --results r_sec.json r_ops.json --json
# -> 任一 human => 团队 human；否则 system2；否则 pass
```

## 新增一个专家
1. 建目录 `experts/<your-id>/`，放 `SKILL.md`（含 name/description/System 2 协议）与 `schema.json`。
2. 在 `_registry.json` 的 `experts` 增加一项（`skill_name`/`dir`/`schema`/`title`/`weight`/`triggers`）。
3. 校验：`python3 scripts/laya_engine.py route --state "<你的领域样例>"` 应能路由到新专家。
4. 部署：`python3 scripts/install.py --force`。
5. （可选）在 `tests/samples.jsonl` 加带标签样本，跑 `batch_eval` / `calibrate` 校准。

## 审计与持续改进
- `decide`/`validate` 加 `--audit-dir` 写 JSONL；`audit` 子命令看升级率与专家命中。
- 用 `calibrate.py` 在带标签样本上扫阈值，确定你业务的 `min_confidence`。
- 用 `batch_eval.py` 做回归，监控各专家准确率是否随 schema 调整退化。
