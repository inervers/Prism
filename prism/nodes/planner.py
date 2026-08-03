"""Planner 节点：把研究主题拆解为子问题。

输出到 state.subquestions。拆解质量决定整个研究的覆盖度。
"""

from __future__ import annotations

import json
import re
import time

from prism.config import settings
from prism.llm import build_llm
from prism.state import PrismState, SubQuestion

PLANNER_PROMPT = """你是研究规划专家。把用户的研究主题拆解为 {max_n} 个相互独立、覆盖全面的子问题。

要求：
1. 子问题之间尽量不重叠
2. 每个子问题给出简短 rationale（为什么需要研究它）
3. 数量不超过 {max_n} 个

只输出 JSON，格式严格如下（不要任何其他文字）：
{{"subquestions": [{{"id": "q1", "question": "子问题文本", "rationale": "理由"}}]}}"""


def planner_node(state: PrismState) -> dict:
    topic = state.get("topic", "")
    t0 = time.time()
    llm = build_llm(temperature=0.2)
    prompt = PLANNER_PROMPT.format(max_n=settings.max_subquestions)
    resp = llm.invoke(prompt + f"\n\n研究主题：{topic}")
    elapsed = time.time() - t0

    subquestions: list[SubQuestion] = []
    try:
        text = resp.content
        # 容忍模型输出被 markdown 代码块包裹
        m = re.search(r"\{.*\}", text, re.DOTALL)
        if m:
            text = m.group(0)
        data = json.loads(text)
        for item in data.get("subquestions", []):
            subquestions.append(
                {
                    "id": item.get("id", f"q{len(subquestions) + 1}"),
                    "question": item["question"],
                    "rationale": item.get("rationale", ""),
                }
            )
    except Exception:  # noqa: BLE001 - 解析失败时降级为单子问题
        subquestions = [
            {"id": "q1", "question": topic, "rationale": "默认单子问题（解析失败降级）"}
        ]

    return {
        "subquestions": subquestions,
        "trace": [
            {
                "node": "planner",
                "detail": f"topic={topic[:30]} subquestions={len(subquestions)} time={elapsed:.1f}s",
                "tokens": resp.usage_metadata.get("total_tokens", 0)
                if resp.usage_metadata
                else 0,
            }
        ],
    }
