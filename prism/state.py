"""LangGraph 状态定义。

整个图共享这一个 State：Planner 写入子问题，Researcher 写入证据，
Aggregator 整理证据，Writer 产出报告。trace 记录每一步供评测模块使用。
"""

import operator
from typing import Annotated, Any, TypedDict


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
    trace: Annotated[list[TraceStep], operator.add]  # 评测轨迹
    # W3 评审字段
    rewrite_count: int                    # 已重写次数（防死循环）
    review_issues: list[str]             # Reviewer 发现的问题
    review_approved: bool                # Reviewer 是否通过
    quality_passed: bool                 # Reviewer 是否确认质量通过
    review_status: str                   # passed/failed/parse_error/retry_exhausted
    remaining_issues: list[str]          # 终止时仍未解决的问题
    terminated_by_limit: bool            # 是否因重试上限终止
    review_parse_error: str              # Reviewer 结构化输出解析错误
    review_parse_attempts: int           # 连续解析失败次数
    claim_verdicts: list[dict[str, Any]] # claim 到 evidence 的审查结果
    human_feedback: str                  # HITL 人工意见（空=直接通过）
    # W3.1 定向重写字段
    chapters_cache: dict[str, str]       # 子问题 id → 已生成章节（重写时复用）
    rewrite_targets: list[str]           # 本轮需要重写的子问题 id（reviewer 标注）（追加合并）
