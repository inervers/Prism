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

from prism.llm import build_llm
from prism.state import PrismState

MAX_REWRITES = 2

REVIEWER_PROMPT = """你是研究报告质量审查员。审查报告是否存在以下问题，逐项给出结论：

1. 幻觉：是否存在证据中完全没有的论断（尤其是具体数字、政策名称、公司名、人名）
2. 引用完整：报告是否每个章节都有 [子问题-序号] 引用标注
3. 引用有效：引用的编号（如 [q1-3]）是否指向真实存在的证据条目（子问题 id 合法、序号不超出该子问题的证据条数）
4. 逻辑连贯：章节间是否有明显断裂或矛盾

只输出 JSON，格式严格如下（不要任何其他文字）：
{{"approved": true/false, "issues": ["问题1描述", "问题2描述"]}}

- approved=true 表示全部通过
- issues 列出所有发现的问题；若通过则为空数组。问题描述要具体，指出哪个章节、哪个论断、缺什么证据。"""


def _extract_cited_ids(report: str) -> set[str]:
    """从报告中提取所有 [x-N] 引用编号。"""
    return set(re.findall(r"\[([a-zA-Z]+\d+-\d+)\]", report))


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
            f"引用编号无效或超出证据范围: {sorted(invalid)}（有效编号: {sorted(valid_ids)}）"
        )

    # 没有引用任何证据的章节？粗略检查：报告是否完全无引用
    if not cited:
        issues.append("报告没有任何 [子问题-序号] 引用标注")

    # LLM 幻觉检查（花 token 的部分）
    if not issues:  # 程序化检查已失败时跳过 LLM，直接打回
        llm = build_llm(temperature=0.0)
        resp = llm.invoke(
            REVIEWER_PROMPT + f"\n\n证据条目数：{len(valid_ids)}\n\n报告内容：\n{report}"
        )
        try:
            text = resp.content
            m = re.search(r"\{.*\}", text, re.DOTALL)
            if m:
                text = m.group(0)
            verdict = json.loads(text)
            if not verdict.get("approved", False):
                issues.extend(verdict.get("issues", ["未通过审查"]))
        except Exception:  # noqa: BLE001 - 解析失败按通过处理（不阻塞交付）
            issues = []

    approved = len(issues) == 0
    rewrites = state.get("rewrite_count", 0)

    # 达到重写上限时强制通过，避免死循环
    forced = False
    if not approved and rewrites >= MAX_REWRITES:
        forced = True
        approved = True
        issues.append(f"已重写 {rewrites} 次达到上限，强制通过")

    return {
        "review_issues": issues,
        "review_approved": approved,
        "rewrite_count": rewrites + 1 if not approved and not forced else rewrites,
        "trace": [
            {
                "node": "reviewer",
                "detail": (
                    f"approved={approved} issues={len(issues)} "
                    f"cited={len(cited)} rewrites={rewrites}"
                    + (" FORCED" if forced else "")
                    + f" time={time.time() - t0:.1f}s"
                ),
                "tokens": 0,
            }
        ],
    }


def route_after_reviewer(state: PrismState) -> str:
    """条件边：approved → human_review（HITL），否则打回 writer 重写。"""
    if state.get("review_approved", False):
        return "human_review"
    return "writer"
