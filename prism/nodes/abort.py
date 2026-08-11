"""终止节点：研究失败时短路出口（无证据等场景），避免浪费 LLM 调用。"""

from __future__ import annotations

from prism.state import PrismState


def no_evidence_node(state: PrismState) -> dict:
    """搜索无结果时终止，给出失败报告而不是让 writer 编造。"""
    report = (
        "## 研究失败\n\n所有子问题的搜索均未返回有效证据。\n"
        "可能原因：搜索工具不可用、网络超时、或主题过于冷门。\n"
        "建议：检查 SEARCH_BACKEND / SEARCH_PROXY 配置后重试。"
    )
    return {
        "report": report,
        "review_approved": False,
        "quality_passed": False,
        "review_status": "no_evidence",
        "remaining_issues": ["[global] 所有子问题均无可用证据"],
        "terminated_by_limit": False,
        "trace": [
            {
                "node": "no_evidence",
                "detail": "no evidence, early exit",
                "tokens": 0,
            }
        ],
    }


def route_after_aggregator(state: PrismState) -> str:
    """有证据 → writer；无证据 → no_evidence 短路。"""
    if state.get("grouped_evidence"):
        return "writer"
    return "no_evidence"
