"""Prism CLI 入口。

用法：
    python -m prism.main "虚拟电厂商业模式分析"        # 完整流程（含 HITL 审核）
    python -m prism.main "主题" --no-human             # 跳过人工审核（自动通过）
    python -m prism.main "主题" -v                     # 实时打印每个节点的进度
"""

from __future__ import annotations

import argparse
import sys
import time
import uuid
from pathlib import Path

from langgraph.types import Command

from prism.config import ROOT_DIR
from prism.graph import get_app
from prism.memory import write_memory

OUTPUT_DIR = ROOT_DIR / "outputs"


def new_thread_id(prefix: str = "prism") -> str:
    """为一次新运行生成唯一 thread id，避免意外复用旧 checkpoint。"""
    return f"{prefix}-{uuid.uuid4().hex}"


def _brief(node: str, update: dict) -> str:
    """把节点输出压缩成一行摘要。"""
    if node == "planner":
        return f"拆解 {len(update.get('subquestions', []))} 个子问题"
    if node == "researcher":
        evs = update.get("evidence", [])
        return f"收集 {len(evs)} 条证据" if evs else "无结果"
    if node == "aggregator":
        grouped = update.get("grouped_evidence", {})
        return f"归组 {sum(len(v) for v in grouped.values())} 条证据 / {len(grouped)} 个子问题"
    if node == "writer":
        detail = update.get("trace", [{}])[0].get("detail", "")
        import re as _re
        m = _re.search(r"rewrote=(\d+) kept=(\d+)", detail)
        suffix = f"（重写{m.group(1)}章，复用{m.group(2)}章）" if m else ""
        return f"生成报告 {len(update.get('report', ''))} 字符{suffix}"
    if node == "reviewer":
        ok = update.get("review_approved")
        issues = update.get("review_issues", [])
        return f"{'通过' if ok else '打回'}（{len(issues)} 个问题）"
    if node == "revise":
        return f"按人工意见修订（{len(update.get('report', ''))} 字符）"
    if node == "no_evidence":
        return "无证据，提前终止"
    return ""


def run(
    topic: str,
    verbose: bool = False,
    no_human: bool = False,
    *,
    graph=None,
    thread_id: str | None = None,
) -> str:
    t0 = time.time()
    graph = graph or get_app()
    config = {"configurable": {"thread_id": thread_id or new_thread_id()}}

    # 用 stream 模式实时推进：每个节点完成立即打印，HITL 中断时暂停
    def _stream(input_):
        for event in graph.stream(input_, config=config, stream_mode="updates"):
            for node, update in event.items():
                if node == "human_review" and isinstance(update, dict) and "__interrupt__" in update:
                    yield node, update
                elif verbose:
                    detail = _brief(node, update)
                    if detail:
                        print(f"  ▶ {node:12s} {detail}")

    # 第一段：执行到 HITL 中断点（或结束）
    interrupt_payload = None
    for node, update in _stream({"topic": topic}):
        if node == "human_review":
            payload = update["__interrupt__"][0]
            interrupt_payload = payload

    # 处理 HITL 中断
    if interrupt_payload is not None:
        report_preview = interrupt_payload.get("report_preview", "")
        if no_human:
            resume = "approve"
        else:
            print("\n=== 人工审核 ===")
            print(f"报告已生成，预览：\n{report_preview}\n")
            feedback = input("通过请直接回车，或输入修改意见: ").strip()
            resume = feedback if feedback else "approve"
        # 第二段：恢复执行（approve 或带意见 → revise）
        for node, update in _stream(Command(resume=resume)):
            pass

    # 取最终状态
    state = graph.get_state(config).values
    report = state.get("report", "")

    # 任务完成：沉淀长期记忆（结论摘要 + 引用来源），供后续任务复用
    if report and state.get("grouped_evidence"):
        sources = [
            ev.get("source", "")
            for evs in state["grouped_evidence"].values()
            for ev in evs
        ]
        write_memory(topic, report[:300], sources)
        if verbose:
            print(f"[Memory] 已沉淀记忆（引用来源 {len(set(sources))} 条）")

    if verbose:
        print(f"[Total] {time.time() - t0:.1f}s")
        print("[Trace] 评测轨迹:")
        for step in state.get("trace", []):
            print(f"  {step['node']:12s} {step['detail']}  tokens={step['tokens']}")

    # 报告落盘
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    safe_name = topic.replace("/", "_").replace("\\", "_").replace(":", "：")[:40]
    out_path = OUTPUT_DIR / f"report_{safe_name}.md"
    out_path.write_text(report, encoding="utf-8")
    if verbose:
        print(f"[Saved] {out_path}")
    return report


def main() -> None:
    parser = argparse.ArgumentParser(description="Prism 深度研究 Agent")
    parser.add_argument("topic", nargs="?", help="研究主题")
    parser.add_argument("-v", "--verbose", action="store_true", help="实时打印节点进度")
    parser.add_argument("--no-human", action="store_true", help="跳过人工审核")
    args = parser.parse_args()

    topic = args.topic or input("请输入研究主题: ").strip()
    if not topic:
        print("主题不能为空", file=sys.stderr)
        sys.exit(1)

    report = run(topic, verbose=args.verbose, no_human=args.no_human)
    print(report)


if __name__ == "__main__":
    main()
