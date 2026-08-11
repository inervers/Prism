"""Prism 状态图编排：LangGraph StateGraph。

W3 全图：
    START
      → planner
      → researcher ×N（Send 并行）
      → aggregator
          ├─ 无证据 → no_evidence（短路终止，避免浪费 LLM）
          └─ 有证据 → writer（并行章节 + 合成）
      → reviewer（幻觉/引用校验）
          ├─ fail → writer（重写，带评审意见，最多 MAX_REWRITES 次）
          └─ pass → human_review（HITL interrupt）
              ├─ approve → END
              └─ 提意见 → revise（按意见修订）→ END
"""

from __future__ import annotations

from langgraph.graph import END, START, StateGraph

from prism.memory import build_checkpointer
from prism.nodes.abort import no_evidence_node, route_after_aggregator
from prism.nodes.aggregator import aggregator_node
from prism.nodes.human import human_review_node, revise_node, route_after_human
from prism.nodes.planner import planner_node
from prism.nodes.researcher import dispatch_researchers, researcher_node
from prism.nodes.reviewer import reviewer_node, route_after_reviewer
from prism.nodes.writer import writer_node
from prism.state import PrismState

# checkpointer：SQLite 持久化（缺包时降级 MemorySaver），支持 HITL 中断恢复
checkpointer = build_checkpointer()


def build_graph():
    graph = StateGraph(PrismState)

    graph.add_node("planner", planner_node)
    graph.add_node("researcher", researcher_node)
    graph.add_node("aggregator", aggregator_node)
    graph.add_node("writer", writer_node)
    graph.add_node("reviewer", reviewer_node)
    graph.add_node("human_review", human_review_node)
    graph.add_node("revise", revise_node)
    graph.add_node("no_evidence", no_evidence_node)

    graph.add_edge(START, "planner")
    # Planner 后动态派生 N 个并行 Researcher（每个子问题一个）
    graph.add_conditional_edges("planner", dispatch_researchers, ["researcher"])
    graph.add_edge("researcher", "aggregator")
    # 无证据短路，避免空转
    graph.add_conditional_edges(
        "aggregator", route_after_aggregator, ["writer", "no_evidence"]
    )
    graph.add_edge("writer", "reviewer")
    # Reviewer 条件路由：解析失败先自重试，质量失败打回 writer，
    # 通过或重试耗尽进入 HITL（耗尽不等于质量通过）。
    graph.add_conditional_edges(
        "reviewer", route_after_reviewer, ["reviewer", "writer", "human_review"]
    )
    # HITL 条件路由：有意见 → revise，否则 END
    graph.add_conditional_edges("human_review", route_after_human, ["revise", END])
    graph.add_edge("revise", END)
    graph.add_edge("no_evidence", END)

    return graph.compile(checkpointer=checkpointer)


app = build_graph()
