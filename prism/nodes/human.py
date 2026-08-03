"""HITL 节点：报告生成后暂停，等待人工确认。

用 LangGraph 的 interrupt() 实现：图执行到这里挂起，
用户通过 Command(resume=...) 恢复。这是 checkpointer 能力的展示。

流程：
    human_review（interrupt 暂停）
        ├─ approve → END
        └─ 提意见 → revise（按意见修订）→ END
"""

from __future__ import annotations

from langgraph.types import interrupt

from prism.llm import build_llm
from prism.state import PrismState

REVISE_PROMPT = """你是报告修订者。根据用户的修改意见，修订以下报告。

要求：
1. 只按用户意见修改，其他内容保持不变
2. 修改后的内容必须保留原有的 [子问题-序号] 引用标注
3. 直接输出修订后的完整报告，不要输出其他文字

用户修改意见：
{feedback}

原报告：
{report}"""


def human_review_node(state: PrismState) -> dict:
    """暂停等待人工审核。恢复后根据反馈决定是否重新生成。"""
    report = state.get("report", "")

    feedback = interrupt(
        {
            "message": "报告已生成，请审核。直接通过请回复 approve，或提出修改意见。",
            "report_preview": report[:500] + ("..." if len(report) > 500 else ""),
        }
    )

    if isinstance(feedback, str) and feedback.strip().lower() in (
        "approve", "ok", "yes", "通过", ""
    ):
        return {"human_feedback": ""}
    return {"human_feedback": feedback if isinstance(feedback, str) else str(feedback)}


def revise_node(state: PrismState) -> dict:
    """按人工意见修订报告（轻量一次调用，不触发完整重写）。"""
    feedback = state.get("human_feedback", "")
    report = state.get("report", "")

    if not feedback:
        return {}

    llm = build_llm(temperature=0.2)
    prompt = REVISE_PROMPT.format(feedback=feedback, report=report)
    revised = llm.invoke(prompt).content

    return {
        "report": revised,
        "trace": [
            {
                "node": "revise",
                "detail": f"feedback_len={len(feedback)} revised_len={len(revised)}",
                "tokens": 0,
            }
        ],
    }


def route_after_human(state: PrismState) -> str:
    """HITL 后条件路由：有意见 → revise，否则 END。"""
    if state.get("human_feedback"):
        return "revise"
    return "end"
