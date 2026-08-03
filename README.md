# Prism · 深度研究 Agent

把输入主题拆解为子问题，收集证据，聚合后生成带引用的研究报告。
名字含义：棱镜把白光分解成光谱再重组——对应"拆解 → 并行研究 → 聚合"。

## 架构（W3）

```
START → planner -[Send×N]-→ researcher₁...N(并行) → aggregator → writer(并行章节+合成)
      → reviewer -fail→ writer(重写,≤2次) → human_review(HITL) → [revise|END]
```

- **planner**：LLM 把主题拆成 ≤5 个子问题（JSON 结构化输出）
- **researcher**：`Send` 动态并行，每个子问题派生一个实例调搜索工具
- **aggregator**：按子问题归组 + URL 归一化去重
- **writer**：逐子问题生成章节（并行）→ 合成，程序生成引用来源列表
- **reviewer**：程序化引用校验 + LLM 幻觉检查，不通过打回重写（≤2 次）
- **human_review**：HITL interrupt，人工可 approve 或提意见（→ revise 修订）
- **trace**：每个节点写入评测轨迹（token / 耗时 / 命中数），供评测模块消费

后续迭代：
- W4：评测模块（任务集 + 轨迹分析 + 基线对比）+ SQLite checkpointer

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
├── graph.py         # StateGraph 编排
├── nodes/           # planner / researcher / aggregator / writer
├── tools/           # SearchTool 抽象 + dummy/duckduckgo 实现
└── main.py          # CLI 入口
```
