# Prism · 深度研究 Agent

Prism 是一个基于 LangGraph 的深度研究 Agent：把主题拆成子问题，并行收集证据，生成带引用的报告，再经过有界 Reviewer 循环与 HITL 人工审核。

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

tests/                # 27 项离线测试
```
