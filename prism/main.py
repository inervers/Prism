"""Prism CLI 入口。

用法：
    python -m prism.main "虚拟电厂商业模式分析"
"""

from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

from prism.config import ROOT_DIR, settings
from prism.graph import app

OUTPUT_DIR = ROOT_DIR / "outputs"


def run(topic: str, verbose: bool = False) -> str:
    t0 = time.time()
    result = app.invoke({"topic": topic})

    report = result.get("report", "")
    subquestions = result.get("subquestions", [])
    evidence = result.get("evidence", [])
    trace = result.get("trace", [])

    if verbose:
        print(f"[Planner] 子问题 {len(subquestions)} 个:")
        for sq in subquestions:
            print(f"  {sq['id']}  {sq['question']}")
        print(f"[Researcher] 证据 {len(evidence)} 条（并行）")
        print(f"[Writer] 报告 {len(report)} 字符")
        print(f"[Total] {time.time() - t0:.1f}s")
        print("[Trace] 评测轨迹:")
        for step in trace:
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
    parser.add_argument("-v", "--verbose", action="store_true", help="打印过程信息")
    args = parser.parse_args()

    topic = args.topic or input("请输入研究主题: ").strip()
    if not topic:
        print("主题不能为空", file=sys.stderr)
        sys.exit(1)

    report = run(topic, verbose=args.verbose)
    print(report)


if __name__ == "__main__":
    main()
