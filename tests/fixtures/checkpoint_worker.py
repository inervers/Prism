"""Two-process fixture for validating Prism HITL checkpoint recovery."""

from __future__ import annotations

import json
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(PROJECT_ROOT))

from langgraph.graph import END, START, StateGraph

from prism.main import resume_run, start_run
from prism.memory import build_checkpointer
from prism.nodes.human import human_review_node
from prism.state import PrismState


def draft_node(_state: PrismState) -> dict:
    return {
        "report": "可恢复的测试报告。[q1-1]",
        "grouped_evidence": {
            "q1": [
                {
                    "sub_id": "q1",
                    "content": "测试证据",
                    "source": "https://example.test/evidence",
                    "title": "测试来源",
                }
            ]
        },
        "quality_passed": True,
        "review_status": "passed",
        "trace": [{"node": "draft", "detail": "generated once", "tokens": 0}],
    }


def build_test_graph(database: str):
    builder = StateGraph(PrismState)
    builder.add_node("draft", draft_node)
    builder.add_node("human_review", human_review_node)
    builder.add_edge(START, "draft")
    builder.add_edge("draft", "human_review")
    builder.add_edge("human_review", END)
    return builder.compile(checkpointer=build_checkpointer(database))


def main() -> None:
    action, database, thread_id, *rest = sys.argv[1:]
    graph = build_test_graph(database)
    if action == "start":
        result = start_run("跨进程恢复测试", thread_id=thread_id, graph=graph)
    elif action == "resume":
        result = resume_run(
            thread_id=thread_id,
            feedback=rest[0] if rest else "approve",
            graph=graph,
        )
    else:
        raise SystemExit(f"unknown action: {action}")

    state = graph.get_state({"configurable": {"thread_id": thread_id}}).values
    print(
        json.dumps(
            {
                "status": result.status,
                "thread_id": result.thread_id,
                "human_feedback": state.get("human_feedback"),
                "draft_steps": sum(
                    1 for step in state.get("trace", []) if step["node"] == "draft"
                ),
            },
            ensure_ascii=False,
        )
    )


if __name__ == "__main__":
    main()
