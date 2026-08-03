"""Prism 状态图编排：LangGraph StateGraph。

W2: START -> planner -[Send]-> researcher ×N(并行) -> aggregator -> writer -> END
W3: + reviewer 反思循环 + HITL 审核节点
"""

from __future__ import annotations

from langgraph.graph import END, START, StateGraph

from prism.nodes.aggregator import aggregator_node
from prism.nodes.planner import planner_node
from prism.nodes.researcher import dispatch_researchers, researcher_node
from prism.nodes.writer import writer_node
from prism.state import PrismState


def build_graph():
    graph = StateGraph(PrismState)

    graph.add_node("planner", planner_node)
    graph.add_node("researcher", researcher_node)
    graph.add_node("aggregator", aggregator_node)
    graph.add_node("writer", writer_node)

    graph.add_edge(START, "planner")
    # Planner 后动态派生 N 个并行 Researcher（每个子问题一个）
    graph.add_conditional_edges("planner", dispatch_researchers, ["researcher"])
    graph.add_edge("researcher", "aggregator")
    graph.add_edge("aggregator", "writer")
    graph.add_edge("writer", END)

    return graph.compile()


app = build_graph()
