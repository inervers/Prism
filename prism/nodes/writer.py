"""Writer 节点：基于分组证据生成带引用的研究报告。

v2 拆分策略：逐子问题生成章节（并行）→ 合成完整报告。
解决 v1 单次超长生成慢（110s）的问题：短上下文调用更快，并行进一步提速。
"""

from __future__ import annotations

import time
from concurrent.futures import ThreadPoolExecutor

from prism.config import settings
from prism.llm import build_llm
from prism.state import Evidence, PrismState


def _build_chapter_context(sub_id: str, evs: list[Evidence]) -> str:
    """单个子问题的证据上下文。"""
    blocks = [f"## 子问题 {sub_id}"]
    for i, ev in enumerate(evs, 1):
        blocks.append(
            f"[{sub_id}-{i}] 来源《{ev['title']}》 {ev['source']}\n{ev['content']}"
        )
    return "\n\n".join(blocks)


CHAPTER_PROMPT = """你是专业研究报告章节撰写者。基于提供的证据材料，撰写一个章节。

要求：
1. 结构清晰，论点引用对应证据（标注 [子问题-序号]）
2. 只使用提供的证据，不要编造证据之外的事实
3. 每个子问题证据的 [子问题-序号] 编号必须与证据材料中的标注完全一致
4. 中文输出，篇幅适中

证据材料：
{context}"""


SYNTHESIS_PROMPT = """你是专业研究报告主编。将以下各章节整合为一份完整的研究报告。

要求：
1. 写一个简短的引言（概述研究主题与章节结构）
2. 按顺序保留各章节正文（不要改写章节内容）
3. 写一个综合结论（跨章节提炼要点）

各章节内容：
{chapters}"""


def _write_chapter(sub_id: str, evs: list[Evidence]) -> str:
    """生成单个子问题章节。"""
    context = _build_chapter_context(sub_id, evs)
    llm = build_llm(temperature=0.4)
    prompt = CHAPTER_PROMPT.format(context=context)
    return llm.invoke(prompt).content


def _build_source_list(grouped: dict[str, list[Evidence]]) -> str:
    """从证据中收集引用来源（程序生成，保证准确且去重）。"""
    seen: set[str] = set()
    lines: list[str] = []
    for evs in grouped.values():
        for ev in evs:
            src = ev.get("source", "")
            title = ev.get("title", "")
            if not src or src in seen:
                continue
            seen.add(src)
            lines.append(f"- 《{title}》 {src}")
    return "\n".join(lines)


def writer_node(state: PrismState) -> dict:
    grouped = state.get("grouped_evidence", {})
    t0 = time.time()
    total_context_chars = sum(
        len(_build_chapter_context(s, e)) for s, e in grouped.items()
    )

    # 并行生成各章节（每章上下文短，独立 LLM 调用）
    with ThreadPoolExecutor(max_workers=min(5, max(1, len(grouped)))) as pool:
        futures = {
            pool.submit(_write_chapter, sub_id, evs): sub_id
            for sub_id, evs in grouped.items()
        }
        chapters = {
            futures[f]: f.result()
            for f in futures
        }

    # 按子问题顺序合成（保持 planner 拆解顺序）
    ordered = [
        (sub_id, chapters[sub_id])
        for sub_id in grouped.keys()
    ]
    chapter_text = "\n\n".join(f"## {sub_id}\n{content}" for sub_id, content in ordered)

    llm = build_llm(temperature=0.4)
    synthesis_prompt = SYNTHESIS_PROMPT.format(chapters=chapter_text)
    report = llm.invoke(synthesis_prompt).content

    # 程序追加引用来源（不依赖 LLM 猜测，保证准确）
    source_list = _build_source_list(grouped)
    if source_list:
        report = f"{report}\n\n## 引用来源\n{source_list}"

    elapsed = time.time() - t0
    return {
        "report": report,
        "trace": [
            {
                "node": "writer",
                "detail": (
                    f"chapters={len(ordered)} parallel "
                    f"context_chars={total_context_chars} "
                    f"report_len={len(report)} time={elapsed:.1f}s"
                ),
                "tokens": 0,
            }
        ],
    }
