"""Researcher 节点：单个子问题 → 搜索 → 证据。

W2 升级为 Send 动态并行：每个子问题派生一个 Researcher 实例，
证据通过 state.evidence 的 operator.add reducer 并行合并。

节点入参是 Send 派发的局部状态（sub_id / sub_question），
不是全局 PrismState，因此与 planner/writer 签名不同。
"""

from __future__ import annotations

import time

from prism.config import settings
from prism.state import Evidence
from prism.tools import build_search_tool


def researcher_node(state: dict) -> dict:
    """处理单个子问题（Send 派发）。"""
    sub_id = state["sub_id"]
    sub_question = state["sub_question"]

    t0 = time.time()
    tool = build_search_tool(settings.search_backend, proxy=settings.search_proxy)
    results = tool.search(sub_question, max_results=settings.max_evidence_per_sub)
    elapsed = time.time() - t0

    evidences: list[Evidence] = []
    for r in results:
        if not r.title or not r.snippet:
            continue
        evidences.append(
            {
                "sub_id": sub_id,
                "content": r.snippet,
                "source": r.url,
                "title": r.title,
            }
        )

    # 记录评测轨迹：搜索耗时 + 命中数
    trace_step = {
        "node": "researcher",
        "detail": f"sub={sub_id} query={sub_question[:30]} hits={len(evidences)} time={elapsed:.1f}s",
        "tokens": 0,
    }
    return {"evidence": evidences, "trace": [trace_step]}


def dispatch_researchers(state: dict) -> list:
    """Planner 之后的派生函数：每个子问题 Send 一个 Researcher。"""
    from langgraph.types import Send

    return [
        Send("researcher", {"sub_id": sub["id"], "sub_question": sub["question"]})
        for sub in state.get("subquestions", [])
    ]
