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
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from langgraph.types import Command

from prism.config import ROOT_DIR
from prism.graph import get_app
from prism.memory import write_memory

OUTPUT_DIR = ROOT_DIR / "outputs"


@dataclass(frozen=True)
class RunResult:
    """一次 start/resume 调用的可序列化运行结果。"""

    status: Literal["interrupted", "completed"]
    thread_id: str
    report: str
    interrupt_payload: dict | None = None


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


def _interrupt_value(update) -> dict | None:
    """兼容 LangGraph v1 interrupt event，提取 JSON payload。"""
    interrupts = update.get("__interrupt__", []) if isinstance(update, dict) else update
    if not interrupts:
        return None
    first = interrupts[0]
    value = getattr(first, "value", first)
    return value if isinstance(value, dict) else {"value": value}


def _stream_updates(graph, input_, config: dict, verbose: bool = False):
    """推进 graph，并产出 HITL interrupt payload。"""
    for event in graph.stream(input_, config=config, stream_mode="updates"):
        for node, update in event.items():
            if node == "__interrupt__" or (
                node == "human_review" and isinstance(update, dict)
            ):
                payload = _interrupt_value(update)
                if payload is not None:
                    yield payload
                    continue
            if verbose:
                detail = _brief(node, update)
                if detail:
                    print(f"  ▶ {node:12s} {detail}")


def start_run(
    topic: str,
    *,
    thread_id: str,
    graph,
    verbose: bool = False,
) -> RunResult:
    """启动新任务，运行到 HITL interrupt 或工作流结束。"""
    config = {"configurable": {"thread_id": thread_id}}
    interrupt_payload = next(
        iter(_stream_updates(graph, {"topic": topic}, config, verbose)), None
    )
    state = graph.get_state(config).values
    return RunResult(
        status="interrupted" if interrupt_payload is not None else "completed",
        thread_id=thread_id,
        report=state.get("report", ""),
        interrupt_payload=interrupt_payload,
    )


def resume_run(
    *,
    thread_id: str,
    feedback: str,
    graph,
    verbose: bool = False,
) -> RunResult:
    """使用同一 thread id 从持久化 interrupt 恢复任务。"""
    config = {"configurable": {"thread_id": thread_id}}
    interrupt_payload = next(
        iter(_stream_updates(graph, Command(resume=feedback), config, verbose)), None
    )
    state = graph.get_state(config).values
    return RunResult(
        status="interrupted" if interrupt_payload is not None else "completed",
        thread_id=thread_id,
        report=state.get("report", ""),
        interrupt_payload=interrupt_payload,
    )


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
    active_thread_id = thread_id or new_thread_id()
    config = {"configurable": {"thread_id": active_thread_id}}

    result = start_run(
        topic, thread_id=active_thread_id, graph=graph, verbose=verbose
    )

    # 处理 HITL 中断
    if result.status == "interrupted":
        interrupt_payload = result.interrupt_payload or {}
        report_preview = interrupt_payload.get("report_preview", "")
        if no_human:
            resume = "approve"
        else:
            print("\n=== 人工审核 ===")
            print(f"报告已生成，预览：\n{report_preview}\n")
            feedback = input("通过请直接回车，或输入修改意见: ").strip()
            resume = feedback if feedback else "approve"
        result = resume_run(
            thread_id=active_thread_id,
            feedback=resume,
            graph=graph,
            verbose=verbose,
        )

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
    parser.add_argument("--thread-id", default="", help="为新任务显式指定 thread id")
    parser.add_argument(
        "--pause-at-human", action="store_true", help="到 HITL 后退出，稍后跨进程恢复"
    )
    parser.add_argument("--resume", metavar="THREAD_ID", help="恢复指定 thread id")
    parser.add_argument("--feedback", default="approve", help="恢复时提交的审核意见")
    args = parser.parse_args()

    if args.resume:
        result = resume_run(
            thread_id=args.resume,
            feedback=args.feedback,
            graph=get_app(),
            verbose=args.verbose,
        )
        print(result.report)
        return

    topic = args.topic or input("请输入研究主题: ").strip()
    if not topic:
        print("主题不能为空", file=sys.stderr)
        sys.exit(1)

    if args.pause_at_human:
        thread_id = args.thread_id or new_thread_id()
        result = start_run(
            topic, thread_id=thread_id, graph=get_app(), verbose=args.verbose
        )
        print(f"status={result.status} thread_id={thread_id}")
        if result.interrupt_payload:
            print(result.interrupt_payload.get("report_preview", ""))
        return

    report = run(
        topic,
        verbose=args.verbose,
        no_human=args.no_human,
        thread_id=args.thread_id or None,
    )
    print(report)


if __name__ == "__main__":
    main()
