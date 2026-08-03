"""Prism Agent 评测指标计算。

从任务运行的最终 state 提取结构化指标。
指标分四层：
  L1 任务完成（completion）：是否产出报告、证据是否充足
  L2 工具调用（tool use）：搜索命中率、去重率
  L3 质量把关（quality）：评审是否通过、引用有效性、重写次数
  L4 成本效率（cost）：token 消耗、节点耗时

对比 RAGNEXUS 的检索层/生成层评测：这里是 Agent 行为评测，
关注"规划→工具→把关"全链路的执行质量，而不是单次检索的准确性。
"""

from __future__ import annotations

import re
import time


def compute_task_metrics(state: dict, elapsed: float, task_id: str) -> dict:
    """从最终 state 计算单任务指标。"""

    # ---- L1 任务完成 ----
    report = state.get("report", "")
    subquestions = state.get("subquestions", [])
    evidence = state.get("evidence", [])
    grouped = state.get("grouped_evidence", {})

    completed = bool(report and len(report) > 100 and grouped)
    evidence_blocks = sum(len(v) for v in grouped.values())

    # ---- L2 工具调用 ----
    # 从 trace 提取 researcher 命中情况
    researcher_steps = [s for s in state.get("trace", []) if s["node"] == "researcher"]
    search_attempts = len(researcher_steps)
    search_hits = sum(
        1
        for s in researcher_steps
        if re.search(r"hits=(\d+)", s["detail"]) and int(re.search(r"hits=(\d+)", s["detail"]).group(1)) > 0
    )
    tool_hit_rate = search_hits / search_attempts if search_attempts else 0.0

    # 去重率：raw vs 去重后
    agg_steps = [s for s in state.get("trace", []) if s["node"] == "aggregator"]
    raw_count = len(evidence)
    deduped_count = evidence_blocks
    dedup_rate = 1 - (deduped_count / raw_count) if raw_count else 0.0

    # ---- L3 质量把关 ----
    review_approved = state.get("review_approved", False)
    rewrite_count = state.get("rewrite_count", 0)
    review_issues = state.get("review_issues", [])

    # 引用有效性：程序化检查报告的 [qX-N] 是否都在证据范围内
    review_steps = [s for s in state.get("trace", []) if s["node"] == "reviewer"]
    citation_check = None
    for s in review_steps:
        m = re.search(r"cited=(\d+)", s["detail"])
        if m:
            citation_check = int(m.group(1))

    # ---- L4 成本效率 ----
    total_tokens = sum(s.get("tokens", 0) for s in state.get("trace", []))
    node_times: dict[str, float] = {}
    for s in state.get("trace", []):
        m = re.search(r"time=([\d.]+)s", s["detail"])
        if m:
            node_times.setdefault(s["node"], 0.0)
            node_times[s["node"]] += float(m.group(1))

    return {
        "task_id": task_id,
        "topic": state.get("topic", ""),
        "completed": completed,
        "report_len": len(report),
        "subquestions": len(subquestions),
        "evidence_raw": raw_count,
        "evidence_deduped": deduped_count,
        "subs_with_evidence": len(grouped),
        "search_attempts": search_attempts,
        "search_hits": search_hits,
        "tool_hit_rate": round(tool_hit_rate, 3),
        "dedup_rate": round(dedup_rate, 3),
        "review_approved": review_approved,
        "rewrite_count": rewrite_count,
        "review_issues": review_issues,
        "citation_count": citation_check,
        "total_tokens": total_tokens,
        "node_times": node_times,
        "total_time_s": round(elapsed, 1),
    }


def aggregate_metrics(results: list[dict]) -> dict:
    """汇总所有任务指标，输出均值与分布。"""
    n = len(results)

    def avg(key, default=0.0):
        vals = [r[key] for r in results if r.get(key) is not None]
        return round(sum(vals) / len(vals), 3) if vals else default

    completed = sum(1 for r in results if r["completed"])
    approved = sum(1 for r in results if r.get("review_approved"))
    hit_tasks = [r for r in results if r["search_attempts"] > 0]

    return {
        "num_tasks": n,
        "completion_rate": round(completed / n, 3) if n else 0.0,
        "review_pass_rate": round(approved / n, 3) if n else 0.0,
        "avg_tool_hit_rate": avg("tool_hit_rate"),
        "avg_dedup_rate": avg("dedup_rate"),
        "avg_evidence_raw": avg("evidence_raw"),
        "avg_evidence_deduped": avg("evidence_deduped"),
        "avg_subquestions": avg("subquestions"),
        "avg_rewrite_count": avg("rewrite_count"),
        "avg_total_tokens": round(avg("total_tokens")),
        "avg_total_time_s": avg("total_time_s"),
        "avg_report_len": round(avg("report_len")),
        "per_task": results,
    }
