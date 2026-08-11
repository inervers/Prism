"""Prism 评测执行器：批量运行任务并产出评测报告。

用法：
    python -m eval.run_eval                       # 跑全部任务
    python -m eval.run_eval --max-tasks 3          # 只跑前 3 个
    python -m eval.run_eval --task task-001        # 只跑指定任务

输出：
    eval/reports/eval_YYYYMMDD_HHMMSS.json   # 原始数据（可作回归基线）
    eval/reports/eval_YYYYMMDD_HHMMSS.md     # 人读报告
"""

from __future__ import annotations

import argparse
import hashlib
import importlib.metadata
import json
import subprocess
import sys
import time
import uuid
from datetime import datetime
from pathlib import Path

from eval.metrics import aggregate_metrics, compute_task_metrics
from prism.config import ROOT_DIR, settings
from prism.graph import get_app

EVAL_DIR = ROOT_DIR / "eval"
REPORT_DIR = EVAL_DIR / "reports"


def load_tasks() -> list[dict]:
    tasks_file = EVAL_DIR / "tasks.json"
    if not tasks_file.exists():
        print(f"任务集不存在: {tasks_file}", file=sys.stderr)
        sys.exit(1)
    return json.loads(tasks_file.read_text(encoding="utf-8"))["tasks"]


def _is_interrupt_event(node: str, update) -> bool:
    """识别 LangGraph v1 顶层 interrupt 事件及旧版节点内事件。"""
    return node == "__interrupt__" or (
        node == "human_review"
        and isinstance(update, dict)
        and "__interrupt__" in update
    )


def _progress_marker(kind: str) -> str:
    """返回 Windows GBK 终端也可安全输出的进度标记。"""
    return {"running": ">", "passed": "OK", "failed": "FAIL"}[kind]


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _git_commit() -> str:
    completed = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=ROOT_DIR,
        capture_output=True,
        text=True,
        encoding="utf-8",
        check=False,
    )
    return completed.stdout.strip() if completed.returncode == 0 else "unknown"


def _version(package: str) -> str:
    try:
        return importlib.metadata.version(package)
    except importlib.metadata.PackageNotFoundError:
        return "not-installed"


def run_single(task: dict, verbose: bool = False) -> dict:
    """运行单个评测任务，返回指标。用 stream 模式实时打印进度。"""
    topic = task["topic"]
    task_id = task["id"]
    run_id = f"eval-{task_id}-{uuid.uuid4().hex}"
    config = {"configurable": {"thread_id": run_id}}
    app = get_app()

    t0 = time.time()
    try:
        # 评测一律 no-human：先跑，遇 HITL 中断自动 approve 恢复
        # stream 模式：每个节点完成立即打印，避免长时间静默误判卡死
        from langgraph.types import Command

        interrupted = False
        for event in app.stream({"topic": topic}, config=config, stream_mode="updates"):
            for node, update in event.items():
                if _is_interrupt_event(node, update):
                    interrupted = True
                elif verbose:
                    detail = _brief(node, update)
                    if detail:
                        print(
                            f"  {task_id} [{_progress_marker('running')}] "
                            f"{node:12s} {detail}"
                        )

        if interrupted:
            if verbose:
                print(f"  {task_id} [{_progress_marker('running')}] HITL 自动 approve")
            for event in app.stream(Command(resume="approve"), config=config, stream_mode="updates"):
                if verbose:
                    for node, update in event.items():
                        detail = _brief(node, update)
                        if detail:
                            print(
                                f"  {task_id} [{_progress_marker('running')}] "
                                f"{node:12s} {detail}"
                            )

        state = app.get_state(config).values
        elapsed = time.time() - t0
        metrics = compute_task_metrics(state, elapsed, task_id)
        metrics["topic"] = topic
        metrics["run_id"] = run_id
        if verbose:
            status = _progress_marker(
                "passed" if metrics["completed"] else "failed"
            )
            print(
                f"  [{status}] {task_id} {topic[:30]:32s} "
                f"hit={metrics['tool_hit_rate']} ev={metrics['evidence_deduped']} "
                f"tok={metrics['total_tokens']} t={metrics['total_time_s']}s"
            )
        return metrics

    except Exception as exc:  # noqa: BLE001 - 单任务失败不阻塞整体
        if verbose:
            print(
                f"  [{_progress_marker('failed')}] {task_id} "
                f"{topic[:30]:32s} ERROR: {exc}"
            )
        return {
            "task_id": task_id,
            "topic": topic,
            "completed": False,
            "error": str(exc),
            "total_time_s": round(time.time() - t0, 1),
        }


def _brief(node: str, update: dict) -> str:
    """把节点输出压缩成一行摘要。"""
    if node == "planner":
        return f"拆解 {len(update.get('subquestions', []))} 个子问题"
    if node == "researcher":
        evs = update.get("evidence", [])
        return f"收集 {len(evs)} 条证据" if evs else "无结果"
    if node == "aggregator":
        grouped = update.get("grouped_evidence", {})
        return f"归组 {sum(len(v) for v in grouped.values())} 条 / {len(grouped)} 个子问题"
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
        return f"按意见修订（{len(update.get('report', ''))} 字符）"
    if node == "no_evidence":
        return "无证据，提前终止"
    return ""


def render_markdown(agg: dict) -> str:
    """把汇总结果渲染成 Markdown 报告。"""
    lines = [
        "# Prism Agent 评测报告",
        "",
        f"- 时间：{datetime.now().strftime('%Y-%m-%d %H:%M')}",
        f"- 模型：{settings.model}",
        f"- 搜索后端：{settings.search_backend}（proxy={'on' if settings.search_proxy else 'off'}）",
        "",
        "## 汇总",
        "",
        "| 指标 | 值 |",
        "|---|---|",
        f"| 任务数 | {agg['num_tasks']} |",
        f"| 工作流完成率 | {agg['workflow_completion_rate']:.1%} |",
        f"| Clean quality pass rate | {agg['clean_pass_rate']:.1%} |",
        f"| Reviewer 解析失败任务率 | {agg['parse_error_rate']:.1%} |",
        f"| 重试耗尽任务率 | {agg['retry_exhausted_rate']:.1%} |",
        f"| 平均工具命中率 | {agg['avg_tool_hit_rate']:.1%} |",
        f"| 平均去重率 | {agg['avg_dedup_rate']:.1%} |",
        f"| 平均证据数（去重后） | {agg['avg_evidence_deduped']} |",
        f"| 平均子问题数 | {agg['avg_subquestions']} |",
        f"| 平均重写次数 | {agg['avg_rewrite_count']} |",
        f"| 平均 token 消耗 | {agg['avg_total_tokens']} |",
        f"| 平均总耗时 | {agg['avg_total_time_s']}s |",
        f"| 平均报告长度 | {agg['avg_report_len']} 字符 |",
        "",
        "## 单任务明细",
        "",
        "| 任务 | 完成 | 工具命中率 | 证据(去重) | 评审通过 | 重写 | token | 耗时 | 报告长度 |",
        "|---|---|---|---|---|---|---|---|---|",
    ]

    for r in agg["per_task"]:
        lines.append(
            f"| {r['task_id']} | {'✓' if r.get('completed') else '✗'} "
            f"| {r.get('tool_hit_rate', 0):.0%} | {r.get('evidence_deduped', 0)} "
            f"| {'✓' if r.get('review_approved') else '✗'} | {r.get('rewrite_count', 0)} "
            f"| {r.get('total_tokens', 0)} | {r.get('total_time_s', 0)}s "
            f"| {r.get('report_len', 0)} |"
        )

    lines += ["", "## 失败任务详情", ""]
    failed = [r for r in agg["per_task"] if not r.get("completed")]
    if not failed:
        lines.append("无失败任务。")
    else:
        for r in failed:
            lines.append(f"- **{r['task_id']}** {r.get('topic', '')}: {r.get('error', '未完成')}")
    lines.append("")
    return "\n".join(lines)


def main() -> None:
    parser = argparse.ArgumentParser(description="Prism Agent 评测")
    parser.add_argument("--max-tasks", type=int, default=0, help="只跑前 N 个任务")
    parser.add_argument("--task", default="", help="只跑指定任务 id")
    parser.add_argument("-v", "--verbose", action="store_true", help="打印每个任务进度")
    snapshot_group = parser.add_mutually_exclusive_group()
    snapshot_group.add_argument(
        "--search-snapshot", type=Path, help="只使用冻结搜索快照，禁止联网回退"
    )
    snapshot_group.add_argument(
        "--record-search-snapshot", type=Path, help="记录本次真实搜索输入"
    )
    args = parser.parse_args()

    settings.search_snapshot_path = (
        str(args.search_snapshot.resolve()) if args.search_snapshot else ""
    )
    settings.record_search_snapshot_path = (
        str(args.record_search_snapshot.resolve())
        if args.record_search_snapshot
        else ""
    )

    tasks = load_tasks()
    if args.task:
        tasks = [t for t in tasks if t["id"] == args.task]
    elif args.max_tasks:
        tasks = tasks[: args.max_tasks]

    if not tasks:
        print("没有匹配的任务", file=sys.stderr)
        sys.exit(1)

    print(f"评测 {len(tasks)} 个任务，模型={settings.model}，搜索={settings.search_backend}")
    results: list[dict] = []
    for task in tasks:
        results.append(run_single(task, verbose=args.verbose))

    agg = aggregate_metrics(results)
    tasks_path = EVAL_DIR / "tasks.json"
    active_snapshot = args.search_snapshot or args.record_search_snapshot
    agg["metadata"] = {
        "commit": _git_commit(),
        "model": settings.model,
        "search_backend": settings.search_backend,
        "dataset_sha256": _sha256(tasks_path),
        "search_snapshot_sha256": (
            _sha256(active_snapshot) if active_snapshot and active_snapshot.exists() else ""
        ),
        "langgraph_version": _version("langgraph"),
        "checkpoint_sqlite_version": _version("langgraph-checkpoint-sqlite"),
    }

    # 落盘
    REPORT_DIR.mkdir(parents=True, exist_ok=True)
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    json_path = REPORT_DIR / f"eval_{ts}.json"
    md_path = REPORT_DIR / f"eval_{ts}.md"
    json_path.write_text(json.dumps(agg, ensure_ascii=False, indent=2), encoding="utf-8")
    md_path.write_text(render_markdown(agg), encoding="utf-8")

    print(f"\n评测完成：{agg['completion_rate']:.0%} 完成率，"
          f"平均 {agg['avg_total_tokens']} token / {agg['avg_total_time_s']}s")
    print(f"报告：{md_path}")
    print(f"数据：{json_path}")


if __name__ == "__main__":
    main()
