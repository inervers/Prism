# Prism · 深度研究 Agent

[![Python](https://img.shields.io/badge/Python-3.11%2B-3776AB?logo=python&logoColor=white)](https://www.python.org/)
[![LangGraph](https://img.shields.io/badge/LangGraph-StateGraph-1C3C3C)](https://langchain-ai.github.io/langgraph/)
[![SQLite](https://img.shields.io/badge/SQLite-Checkpoint-003B57?logo=sqlite&logoColor=white)](https://www.sqlite.org/)
[![pytest](https://img.shields.io/badge/pytest-offline-0A9EDC?logo=pytest&logoColor=white)](https://pytest.org/)
[![Secrets Scan](https://github.com/inervers/Prism/actions/workflows/secrets-scan.yml/badge.svg)](https://github.com/inervers/Prism/actions/workflows/secrets-scan.yml)

一个把“并行研究、证据约束、定向重写和人工接管”做成显式状态机的深度研究 Agent。
Prism 使用 LangGraph 将主题拆解为独立研究分支，汇总结构化 evidence，生成带引用报告，
再经过有界 Reviewer 循环与可跨进程恢复的 HITL 审核。它重点解决的不是多调用几次
LLM，而是让失败、重试、终止和人工决策都可解释、可测试。

## 30 秒看懂项目

| 问题 | Prism 的回答 |
|---|---|
| 输入是什么 | 一个研究主题，以及可选的真实搜索、冻结快照或本地知识库 |
| Agent 做什么 | Planner 拆题 → Researcher 并行取证 → Aggregator 去重归组 → Writer 成文 → Reviewer 审证 → HITL 决策 |
| 输出是什么 | 带来源的 Markdown 报告、节点 trace、质量与终止状态，以及可恢复的 checkpoint |
| 如何控制成本 | 子问题、单分支证据、上下文和重写轮次均有上限；无证据直接短路 |
| 如何保证可复现 | 搜索结果支持 record / replay，评测报告绑定 commit、数据集 SHA-256、Python 与依赖版本 |
| 明确边界 | 达到重试上限只代表工作流终止，不等于质量通过；历史六任务结果不冒充当前 HEAD baseline |

## 可验证的工程证据

| 维度 | 仓库内证据 |
|---|---|
| 图编排 | `prism/graph.py`：`Send` 动态并行、条件路由、Reviewer 循环、HITL interrupt 与 END |
| 状态语义 | `prism/state.py`：`quality_passed` 与 `terminated_by_limit` 分离，保留 `remaining_issues` |
| 证据约束 | `prism/nodes/reviewer.py`：报告与结构化 evidence 同审，JSON 解析失败时 fail closed |
| 定向重写 | `prism/nodes/writer.py`：只更新 Reviewer 标记章节，其余章节复用缓存 |
| 跨进程恢复 | `prism/main.py` + `prism/memory.py`：SQLite checkpointer、唯一 `thread_id`、pause / resume |
| 可复现评测 | `eval/`：10 条行为 case、冻结搜索快照、commit / dataset / dependency 绑定 |
| 成本边界 | 真实单任务 smoke 记录 171,025 tokens，公开暴露 whole-report review 的成本问题而非包装成新基线 |
| 质量保障 | 28 项离线测试与 10/10 行为 case（2026-08-20 重跑），安全 workflow 自检纳入测试 |

## 工程设计与技术取舍

| 设计决策 | 原因与边界 |
|---|---|
| 用显式 StateGraph 而非自由对话式多 Agent | 节点输入、条件路由、重试上限和终止语义都能测试；代价是需要维护状态契约 |
| Researcher 使用 `Send` 并行，Aggregator 再统一去重 | 子问题互不阻塞，同时把 URL 归一化与上下文预算集中到单一边界 |
| Reviewer 同时读取报告与结构化 evidence | 审核 claim 是否有依据，而不是只做文风评分；解析失败按不通过处理，避免静默放行 |
| 章节级重写并复用未命中章节 | 减少无关内容漂移和重复 token；若问题跨章节，Reviewer 必须明确标出影响范围 |
| HITL 使用 interrupt + SQLite checkpoint | 人可以在另一个进程继续同一任务；恢复必须使用相同 `thread_id`，不能把新运行误当续跑 |
| 搜索支持 record / replay | 将搜索波动与 Agent 逻辑变化分开评估；快照只能代表录制时的外部信息 |
| 无证据短路 | 避免让 Writer 在空上下文上生成看似完整的报告；最终结果可以失败，但不能伪装成有据结论 |

## 架构

```text
START → planner ──Send×N──→ researcher₁...N → aggregator → writer
      → reviewer ──failed──→ writer（定向重写，最多 2 次）
                 └────────→ human_review（interrupt）→ revise / END
```

- `planner`：把主题拆为最多 5 个子问题。
- `researcher`：使用 `Send` 动态并行搜索，每个子问题产生独立研究分支。
- `aggregator`：按子问题归组，并做 URL 归一化去重。
- `writer`：并行生成章节；重写时只更新 Reviewer 标记的章节，其余复用缓存。
- `reviewer`：同时读取报告与结构化 evidence，输出 claim-level verdict；JSON 解析失败时 fail closed。
- `human_review`：通过 `interrupt()` 暂停，使用 SQLite checkpointer 与相同 `thread_id` 跨进程恢复。
- `trace`：记录节点耗时、token、搜索命中和 Reviewer 状态。
- 无证据时进入 `no_evidence` 短路，避免空转。

Reviewer 将两类语义分开记录：

- `quality_passed`：证据审查确认质量通过。
- `terminated_by_limit`：达到重试上限而结束；不计为质量通过，并保留 `remaining_issues`。

## 建议代码阅读路径

1. `prism/graph.py`：先看完整状态图和所有条件路由。
2. `prism/state.py`：确认每个节点共享的数据与质量/终止语义。
3. `prism/nodes/reviewer.py`、`prism/nodes/writer.py`：跟踪 issues 如何触发定向重写。
4. `prism/main.py`、`prism/memory.py`：理解 interrupt、checkpoint 与跨进程 resume。
5. `eval/run_behavior_eval.py`、`eval/metrics.py`：核对行为 case 和指标如何避免语义混淆。

## 快速开始

```powershell
cd Prism
python -m venv .venv
.\.venv\Scripts\pip.exe install -r requirements.txt
Copy-Item .env.example .env

# 完整流程
.\.venv\Scripts\python.exe -m prism.main "虚拟电厂商业模式分析" -v

# 到 HITL 后退出，记录输出的 thread_id
.\.venv\Scripts\python.exe -m prism.main "虚拟电厂商业模式分析" --pause-at-human

# 另一个进程恢复
.\.venv\Scripts\python.exe -m prism.main --resume <THREAD_ID> --feedback approve
```

输出报告写入 `outputs/report_*.md`，运行时 checkpoint 默认写入 `data/prism_checkpoints.db`。

## 配置

| 变量 | 默认值 | 说明 |
|---|---|---|
| `DEEPSEEK_API_KEY` | - | DeepSeek API 密钥 |
| `LLM_BASE_URL` | `https://api.deepseek.com` | OpenAI 兼容接口 |
| `LLM_MODEL` | `deepseek-v4-flash` | 模型名 |
| `SEARCH_BACKEND` | `dummy` | `dummy` / `duckduckgo` / `local` |
| `SEARCH_SNAPSHOT_PATH` | - | 从冻结的搜索快照回放 |
| `RECORD_SEARCH_SNAPSHOT_PATH` | - | 录制本次真实搜索结果 |
| `MAX_SUBQUESTIONS` | `5` | 子问题上限 |
| `MAX_EVIDENCE_PER_SUB` | `3` | 每个子问题的证据上限 |
| `CONTEXT_BUDGET_CHARS` | `6000` | 单章节 evidence 字符预算 |

`.env`、checkpoint、评测报告和搜索快照默认不进入 Git。

## 测试与行为评测

```powershell
# 离线测试
.\.venv\Scripts\python.exe -m pytest -q --basetemp .test-runtime/pytest-main -o cache_dir=.test-runtime/cache

# 10 条 LangGraph 行为 case，生成机器可读 JSON
.\.venv\Scripts\python.exe -m eval.run_behavior_eval

# 付费端到端评测；建议先单任务或先录制搜索快照
.\.venv\Scripts\python.exe -m eval.run_eval --task task-001 --record-search-snapshot eval/snapshots/search.json
.\.venv\Scripts\python.exe -m eval.run_eval --task task-001 --search-snapshot eval/snapshots/search.json
```

行为评测覆盖 Reviewer pass/fail/parse error/retry exhausted、无证据短路、HITL 路由、跨进程 resume、唯一 thread id、指标语义和数据集完整性。报告绑定 commit、dataset SHA-256、Python 与关键依赖版本。

## 评测边界

仓库保留的 2026-08-03 W4 六任务结果属于历史基线，只能说明当时版本的运行表现，不能代表当前 HEAD。旧指标把达到重试上限也计入 `review_pass_rate`，当前实现已拆分为 workflow completion、clean quality pass、parse failure 和 retry exhausted。

当前版本完成过一次真实搜索与 LLM 的单任务 smoke。该任务最终 `quality_passed=false`、`terminated_by_limit=true`，消耗 171,025 tokens；它验证了有界终止与状态语义，也暴露出 whole-report grounded review 的成本问题。由于单任务成本较高，未把未经复跑的六任务结果包装成新 baseline。

## 项目结构

```text
prism/
├── graph.py          # StateGraph、条件路由与 checkpointer 注入
├── state.py          # 共享状态与 Reviewer 语义
├── main.py           # CLI、interrupt 与跨进程 resume
├── nodes/            # planner / researcher / writer / reviewer / human
└── tools/            # dummy / DuckDuckGo / local / snapshot search

eval/
├── tasks.json
├── behavior_cases.json
├── metrics.py
├── run_eval.py
└── run_behavior_eval.py

tests/                # 28 项离线测试
```
