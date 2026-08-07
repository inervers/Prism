"""Writer 节点：基于分组证据生成带引用的研究报告。

v3 定向重写：reviewer 标注问题章节（rewrite_targets）时只重写这些章节，
其余章节从 chapters_cache 复用，避免全量重写导致 token 成本翻 3 倍。
"""

from __future__ import annotations

import time
from concurrent.futures import ThreadPoolExecutor

from prism.config import settings
from prism.context import compact_evidence
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


def _write_chapter(sub_id: str, evs: list[Evidence], feedback: str = "") -> tuple[str, int, int, bool]:
    """生成单个子问题章节。feedback 非空时带上评审意见重写。

    返回 (章节内容, token 消耗, 实际喂给 LLM 的上下文字符数, 是否压缩过证据)。
    """
    # 上下文预算：超限时对证据做首部保留截断（确定性压缩，零 LLM 成本）
    evs_fed, compressed = compact_evidence(evs, settings.context_budget_chars)
    context = _build_chapter_context(sub_id, evs_fed)
    fed_chars = len(context)
    llm = build_llm(temperature=0.4)
    prompt = CHAPTER_PROMPT.format(context=context)
    if feedback:
        prompt += f"\n\n上次审查意见（必须逐条修正）：\n{feedback}"
    resp = llm.invoke(prompt)
    tokens = (
        resp.usage_metadata.get("total_tokens", 0) if resp.usage_metadata else 0
    )
    return resp.content, tokens, fed_chars, compressed


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
    feedback = state.get("review_issues", [])
    feedback_text = "\n".join(feedback) if feedback else ""
    rewrite_round = state.get("rewrite_count", 0)

    # 定向重写：有 cache 且 reviewer 标注了问题章节时，只重写这些章节
    cache: dict[str, str] = dict(state.get("chapters_cache", {}))
    targets = state.get("rewrite_targets", [])

    if cache and targets:
        # 仅重写被标注的章节，其余复用缓存
        to_rewrite = [t for t in targets if t in grouped]
        to_keep = [s for s in grouped if s not in to_rewrite]
    else:
        # 首次生成或全局性问题：全量生成
        to_rewrite = list(grouped.keys())
        to_keep = []

    t0 = time.time()
    total_context_chars = sum(
        len(_build_chapter_context(s, e)) for s, e in grouped.items()
    )
    chapter_tokens = 0
    fed_total_chars = 0
    any_compressed = False

    # 只并行生成需要重写的章节
    if to_rewrite:
        with ThreadPoolExecutor(max_workers=min(5, max(1, len(to_rewrite)))) as pool:
            futures = {
                pool.submit(_write_chapter, sub_id, grouped[sub_id], feedback_text): sub_id
                for sub_id in to_rewrite
            }
            for f in futures:
                content, tokens, fed_chars, compressed = f.result()
                cache[futures[f]] = content
                chapter_tokens += tokens
                fed_total_chars += fed_chars
                any_compressed = any_compressed or compressed

    # 按子问题顺序合成（保持 planner 拆解顺序）
    ordered = [
        (sub_id, cache[sub_id])
        for sub_id in grouped
        if sub_id in cache
    ]
    chapter_text = "\n\n".join(f"## {sub_id}\n{content}" for sub_id, content in ordered)

    llm = build_llm(temperature=0.4)
    synthesis_prompt = SYNTHESIS_PROMPT.format(chapters=chapter_text)
    synth_resp = llm.invoke(synthesis_prompt)
    report = synth_resp.content
    synth_tokens = (
        synth_resp.usage_metadata.get("total_tokens", 0)
        if synth_resp.usage_metadata
        else 0
    )

    # 程序追加引用来源（不依赖 LLM 猜测，保证准确）
    source_list = _build_source_list(grouped)
    if source_list:
        report = f"{report}\n\n## 引用来源\n{source_list}"

    compacted_info = ""
    if any_compressed:
        compacted_info = f" compacted={fed_total_chars}/{total_context_chars}"
    elapsed = time.time() - t0
    return {
        "report": report,
        "chapters_cache": cache,
        "trace": [
            {
                "node": "writer",
                "detail": (
                    f"round={rewrite_round} rewrote={len(to_rewrite)} "
                    f"kept={len(to_keep)} context_chars={total_context_chars}"
                    f"{compacted_info} report_len={len(report)} time={elapsed:.1f}s"
                ),
                "tokens": chapter_tokens + synth_tokens,
            }
        ],
    }
