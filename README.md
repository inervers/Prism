# Prism · 深度研究 Agent

把输入主题拆解为子问题，收集证据，聚合后生成带引用的研究报告。
名字含义：棱镜把白光分解成光谱再重组——对应"拆解 → 并行研究 → 聚合"。

## 架构（W3.1）

```
START → planner -[Send×N]-→ researcher₁...N(并行) → aggregator → writer(并行章节+合成)
      → reviewer -fail→ writer(定向重写,≤2次) → human_review(HITL) → [revise|END]
```

- **planner**：LLM 把主题拆成 ≤5 个子问题（JSON 结构化输出）
- **researcher**：`Send` 动态并行，每个子问题派生一个实例调搜索工具
- **aggregator**：按子问题归组 + URL 归一化去重
- **writer**：逐子问题生成章节（并行）→ 合成，程序生成引用来源列表
- **reviewer**：程序化引用校验 + LLM 幻觉检查，不通过打回重写（≤2 次）
- **W3.1 定向重写**：reviewer 按 `[qn]` 标注问题章节，writer 只重写被标注章节，其余从 `chapters_cache` 复用（实测避免 4 倍 token 膨胀）
- **human_review**：HITL interrupt，人工可 approve 或提意见（→ revise 修订）
- **trace**：每个节点写入评测轨迹（token / 耗时 / 命中数），供评测模块消费
- 无证据时 `no_evidence` 短路终止，不空转

已实现：W1 骨架 / W2 Send 并行+真实搜索 / W3 Reviewer 循环+HITL / W3.1 定向重写 / W4 评测模块

## 快速开始

```bash
cd Prism
pip install -r requirements.txt          # 网络受限时: pip install -i https://mirrors.aliyun.com/pypi/simple/ -r requirements.txt
copy .env.example .env                    # 填入 DEEPSEEK_API_KEY
python -m prism.main "虚拟电厂商业模式分析" -v          # 完整流程（含 HITL 审核）
python -m prism.main "虚拟电厂商业模式分析" -v --no-human  # 跳过人工审核
```

输出报告写入 `outputs/report_*.md`。

## 配置（.env）

| 变量 | 默认 | 说明 |
|---|---|---|
| DEEPSEEK_API_KEY | - | DeepSeek 密钥 |
| LLM_BASE_URL | https://api.deepseek.com | OpenAI 兼容接口 |
| LLM_MODEL | deepseek-v4-flash | 模型名 |
| SEARCH_BACKEND | dummy | dummy=离线骨架联调 / duckduckgo=真实搜索 |
| MAX_SUBQUESTIONS | 5 | 子问题上限 |
| MAX_EVIDENCE_PER_SUB | 3 | 每个子问题最多证据条数 |

## 项目结构

```
prism/
├── config.py        # 配置（.env）
├── llm.py           # DeepSeek 客户端封装
├── state.py         # LangGraph State 定义（含评测轨迹 trace）
├── graph.py         # StateGraph 编排（含 HITL/评审/短路）
├── nodes/           # planner / researcher / aggregator / writer / reviewer / human / abort
├── tools/           # SearchTool 抽象 + dummy/duckduckgo/local 实现
└── main.py          # CLI 入口（stream 实时进度 + HITL）

eval/
├── tasks.json       # 评测任务集（6 个真实主题）
├── metrics.py       # 指标计算（完成/工具/质量/成本四层）
└── run_eval.py      # 评测执行器 → JSON 数据 + Markdown 报告
```

## Agent 评测（W4）

```bash
python -m eval.run_eval --max-tasks 2     # 先跑小批量验证
python -m eval.run_eval                    # 全量（约 6 任务 × 2-6 分钟）
python -m eval.run_eval --task task-001    # 单任务
```

指标分四层：
- **L1 完成**：完成率、报告长度、证据数
- **L2 工具**：搜索命中率、去重率
- **L3 质量**：评审通过率、重写次数、引用有效数
- **L4 成本**：token 消耗、节点耗时

每次评测生成 `eval/reports/eval_时间戳.json`（原始数据）+ `.md`（报告），
归档基线存 `eval/baselines/`（入库，可回归对比）。

### 基线（2026-08-03，6 任务全量）

| 指标 | 值 |
|---|---|
| 完成率 | 100%（6/6，含冷门主题量子点显示） |
| 工具命中率 | 100%（30/30 搜索全命中） |
| 重写触发率 | 50%（3/6 任务触发 reviewer 打回） |
| 一次通过成本 | ~26k token（task-001/002/004） |
| 触发重写成本 | 80k~115k token（task-003/005/006） |
| 单任务耗时 | 142s~644s（含网络搜索） |

**评测驱动的两个发现**：
1. 重写循环让 token 成本最高膨胀 4 倍（26k → 115k）→ 实现定向重写，重写轮章节生成量降 60-80%（实测 task-005 第三轮仅重写 1 章、task-006 第二轮仅重写 3 章）
2. LLM 审查存在随机波动：同一任务重复跑可能 1 轮通过或 3 轮打回 → 结论：Agent 评测要看多任务趋势，不能看单次绝对值（同 RAGNEXUS 的 LLM-as-judge 方法论）
