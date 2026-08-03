"""LangGraph 状态定义。

整个图共享这一个 State：Planner 写入子问题，Researcher 写入证据，
Aggregator 整理证据，Writer 产出报告。trace 记录每一步供评测模块使用。
"""

import operator
from typing import Annotated, TypedDict


class SubQuestion(TypedDict):
    """Planner 拆解出的子问题。"""

    id: str          # 如 q1, q2
    question: str    # 子问题文本
    rationale: str   # 为什么需要研究这个子问题


class Evidence(TypedDict):
    """Researcher 收集到的单条证据。"""

    sub_id: str      # 属于哪个子问题
    content: str     # 证据内容（原文或摘要）
    source: str      # 来源 URL/标题
    title: str       # 来源标题


class TraceStep(TypedDict):
    """评测轨迹：记录一次节点执行。"""

    node: str            # 节点名
    detail: str          # 摘要（如调用的工具、花费 token）
    tokens: int          # 该步消耗的 token


class PrismState(TypedDict, total=False):
    topic: str                            # 用户输入的研究主题
    subquestions: list[SubQuestion]       # Planner 输出
    evidence: Annotated[list[Evidence], operator.add]  # Researcher 输出（并行合并）
    grouped_evidence: dict[str, list[Evidence]]  # Aggregator 输出（按子问题分组）
    report: str                           # Writer 最终报告
    trace: Annotated[list[TraceStep], operator.add]  # 评测轨迹（追加合并）
