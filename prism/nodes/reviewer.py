"""Reviewer 节点：幻觉校验 + 质量把关。

检查报告是否满足：
1. 每个论断都有 [子问题-序号] 引用标注（引用完整性）
2. 引用的 [子问题-序号] 都在证据范围内（引用有效性，防编造编号）
3. 无证据支撑的论断（幻觉）

不通过时返回评审意见，由条件边打回 writer 重写（最多 MAX_REWRITES 次）。
"""

from __future__ import annotations

import json
import re
import time
from typing import Literal

from pydantic import BaseModel, Field

from prism.llm import build_llm
from prism.state import PrismState

MAX_REWRITES = 2

REVIEW_CONTEXT_BUDGET_CHARS = 12_000


class ClaimVerdict(BaseModel):
    """单条报告论断相对 evidence 的审查结果。"""

    claim: str
    status: Literal["supported", "partially_supported", "unsupported"]
    evidence_ids: list[str] = Field(default_factory=list)
    reason: str
    section_id: str


class ReviewerVerdict(BaseModel):
    """Reviewer 的严格结构化输出。"""

    quality_passed: bool
    claim_verdicts: list[ClaimVerdict] = Field(default_factory=list)
    issues: list[str] = Field(default_factory=list)
    rewrite_targets: list[str] = Field(default_factory=list)


REVIEWER_PROMPT = """你是研究报告质量审查员。必须只依据给出的 evidence 审查报告：

1. 提取报告中的关键 claim，逐条判断 supported、partially_supported 或 unsupported。
2. evidence_ids 只能填写输入中真实存在的 [子问题-序号]。
3. 检查具体数字、政策名称、公司名和人名是否有直接证据。
4. 检查章节引用完整性和逻辑连贯性。

只输出 JSON，格式严格如下（不要任何其他文字）：
{"quality_passed": false, "claim_verdicts": [{"claim": "论断", "status": "unsupported", "evidence_ids": [], "reason": "原因", "section_id": "q3"}], "issues": ["[q3] 问题描述"], "rewrite_targets": ["q3"]}

要求：
- 只有全部关键 claim 都得到 evidence 支撑时，quality_passed 才能为 true
- issues 列出所有发现的问题；每条必须以 [qn] 开头标注问题所在的子问题章节（如 [q1] 或 [q3]）
- 若问题是全局性的（如整个报告无引用），用 [global] 标注
- 问题描述要具体，指出哪个章节、哪个论断、缺什么证据"""


def _build_evidence_context(
    grouped: dict[str, list[dict]], budget_chars: int = REVIEW_CONTEXT_BUDGET_CHARS
) -> tuple[str, bool]:
    """把结构化 evidence 序列化给 Reviewer，并对正文施加确定性预算。"""
    entries: list[tuple[str, dict]] = []
    for sub_id, evs in grouped.items():
        entries.extend((f"{sub_id}-{index}", ev) for index, ev in enumerate(evs, 1))

    if not entries:
        return "（无 evidence）", False

    overhead = sum(
        len(evidence_id)
        + len(ev.get("title", ""))
        + len(ev.get("source", ""))
        + 20
        for evidence_id, ev in entries
    )
    content_budget = max(1, budget_chars - overhead)
    per_entry_budget = max(1, content_budget // len(entries))
    compacted = False
    blocks: list[str] = []
    for evidence_id, ev in entries:
        content = ev.get("content", "")
        if len(content) > per_entry_budget:
            content = content[:per_entry_budget] + "\n[Reviewer 输入已截断]"
            compacted = True
        blocks.append(
            f"[{evidence_id}] 标题：{ev.get('title', '')}\n"
            f"来源：{ev.get('source', '')}\n正文：{content}"
        )
    return "\n\n".join(blocks), compacted


def _parse_verdict(text: str) -> ReviewerVerdict:
    """从模型响应中提取并严格验证第一个 JSON object。"""
    match = re.search(r"\{.*\}", text, re.DOTALL)
    if not match:
        raise ValueError("Reviewer response did not contain a JSON object")
    return ReviewerVerdict.model_validate_json(match.group(0))


def _extract_cited_ids(report: str) -> set[str]:
    """从报告中提取所有 [x-N] 引用编号。"""
    return set(re.findall(r"\[([a-zA-Z]+\d+-\d+)\]", report))


def _extract_targets(issues: list[str]) -> list[str]:
    """从问题列表中提取需要重写的子问题 id。"""
    targets: set[str] = set()
    for issue in issues:
        m = re.match(r"\[([a-zA-Z]+\d+)\]", issue)
        if m and m.group(1) != "global":
            targets.add(m.group(1))
    return sorted(targets)


def reviewer_node(state: PrismState) -> dict:
    report = state.get("report", "")
    grouped = state.get("grouped_evidence", {})
    t0 = time.time()

    # 先做程序化检查（确定性问题，不花 token）
    issues: list[str] = []

    # 检查引用有效性：编号对应的子问题存在、序号不超证据数
    valid_ids = set()
    for sub_id, evs in grouped.items():
        for i in range(1, len(evs) + 1):
            valid_ids.add(f"{sub_id}-{i}")

    cited = _extract_cited_ids(report)
    invalid = cited - valid_ids
    if invalid:
        issues.append(
            f"[global] 引用编号无效或超出证据范围: {sorted(invalid)}（有效编号: {sorted(valid_ids)}）"
        )

    # 没有引用任何证据的章节？粗略检查：报告是否完全无引用
    if not cited:
        issues.append("[global] 报告没有任何 [子问题-序号] 引用标注")

    # LLM grounded 检查（花 token 的部分）
    llm_tokens = 0
    claim_verdicts: list[dict] = []
    review_parse_error = ""
    review_status = "failed" if issues else "passed"
    verdict_targets: list[str] = []
    context, context_compacted = _build_evidence_context(grouped)
    if not issues:  # 程序化检查已失败时跳过 LLM，直接打回
        llm = build_llm(temperature=0.0)
        resp = llm.invoke(
            REVIEWER_PROMPT
            + f"\n\n=== Evidence ===\n{context}\n\n=== 报告内容 ===\n{report}"
        )
        llm_tokens = (
            resp.usage_metadata.get("total_tokens", 0) if resp.usage_metadata else 0
        )
        try:
            verdict = _parse_verdict(resp.content)
            claim_verdicts = [item.model_dump() for item in verdict.claim_verdicts]
            verdict_targets = verdict.rewrite_targets
            issues.extend(verdict.issues)
            if not verdict.quality_passed and not issues:
                issues.append("[global] Reviewer 判定未通过但未返回具体问题")
            quality_passed = verdict.quality_passed and not issues
            review_status = "passed" if quality_passed else "failed"
        except Exception as exc:  # noqa: BLE001 - 模型输出不可信时必须 fail-closed
            quality_passed = False
            review_status = "parse_error"
            review_parse_error = f"{type(exc).__name__}: {exc}"
            issues.append(f"[global] Reviewer 结构化输出解析失败：{review_parse_error}")
    else:
        quality_passed = False

    approved = quality_passed
    rewrites = state.get("rewrite_count", 0)
    targets = sorted(set(verdict_targets or _extract_targets(issues)))

    # 达到重写上限时强制通过，避免死循环
    forced = False
    if not approved and rewrites >= MAX_REWRITES:
        forced = True
        approved = True
        issues.append(f"[global] 已重写 {rewrites} 次达到上限，强制通过")

    return {
        "review_issues": issues,
        "review_approved": approved,
        "quality_passed": quality_passed,
        "review_status": review_status,
        "remaining_issues": [],
        "terminated_by_limit": False,
        "review_parse_error": review_parse_error,
        "review_parse_attempts": state.get("review_parse_attempts", 0),
        "claim_verdicts": claim_verdicts,
        "rewrite_targets": targets,
        "rewrite_count": rewrites + 1 if not approved and not forced else rewrites,
        "trace": [
            {
                "node": "reviewer",
                "detail": (
                    f"approved={approved} issues={len(issues)} "
                    f"cited={len(cited)} rewrites={rewrites}"
                    + (" FORCED" if forced else "")
                    + f" grounded=true context_compacted={str(context_compacted).lower()}"
                    + f" time={time.time() - t0:.1f}s"
                ),
                "tokens": llm_tokens,
            }
        ],
    }


def route_after_reviewer(state: PrismState) -> str:
    """条件边：approved → human_review（HITL），否则打回 writer 重写。"""
    if state.get("review_approved", False):
        return "human_review"
    return "writer"
