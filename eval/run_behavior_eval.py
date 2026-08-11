"""运行 Prism 的离线 LangGraph 行为评测并生成机器可读报告。"""

from __future__ import annotations

import argparse
import hashlib
import importlib.metadata
import json
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

EVAL_DIR = Path(__file__).resolve().parent
ROOT_DIR = EVAL_DIR.parent
DEFAULT_DATASET = EVAL_DIR / "behavior_cases.json"


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


def _package_version(name: str) -> str:
    try:
        return importlib.metadata.version(name)
    except importlib.metadata.PackageNotFoundError:
        return "not-installed"


def load_cases(path: Path = DEFAULT_DATASET) -> tuple[list[dict], str]:
    raw = path.read_bytes()
    dataset_hash = hashlib.sha256(raw).hexdigest()
    data = json.loads(raw.decode("utf-8"))
    return data["cases"], dataset_hash


def run_cases(cases: list[dict]) -> list[dict]:
    runtime_root = ROOT_DIR / ".test-runtime"
    (runtime_root / "behavior").mkdir(parents=True, exist_ok=True)
    (runtime_root / "cache").mkdir(parents=True, exist_ok=True)
    results: list[dict] = []
    for case in cases:
        started = time.perf_counter()
        completed = subprocess.run(
            [
                sys.executable,
                "-m",
                "pytest",
                case["nodeid"],
                "-q",
                "--basetemp",
                str(runtime_root / "behavior" / case["id"]),
                "-o",
                f"cache_dir={runtime_root / 'cache'}",
            ],
            cwd=ROOT_DIR,
            capture_output=True,
            text=True,
            encoding="utf-8",
            check=False,
        )
        results.append(
            {
                "id": case["id"],
                "nodeid": case["nodeid"],
                "passed": completed.returncode == 0,
                "duration_s": round(time.perf_counter() - started, 3),
                "expected_review_status": case["expected_review_status"],
                "expected_terminal": case["expected_terminal"],
                "output": (completed.stdout + completed.stderr)[-2000:],
            }
        )
    return results


def build_report(cases_path: Path = DEFAULT_DATASET) -> dict:
    cases, dataset_hash = load_cases(cases_path)
    results = run_cases(cases)
    return {
        "schema_version": 1,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "commit": _git_commit(),
        "dataset": str(cases_path.relative_to(ROOT_DIR)),
        "dataset_sha256": dataset_hash,
        "python": sys.version.split()[0],
        "dependencies": {
            "langgraph": _package_version("langgraph"),
            "langgraph-checkpoint-sqlite": _package_version(
                "langgraph-checkpoint-sqlite"
            ),
            "pydantic": _package_version("pydantic"),
        },
        "num_cases": len(results),
        "passed": sum(1 for result in results if result["passed"]),
        "failed": sum(1 for result in results if not result["passed"]),
        "results": results,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Prism 离线行为评测")
    parser.add_argument("--dataset", type=Path, default=DEFAULT_DATASET)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()

    report = build_report(args.dataset)
    output = args.output
    if output is None:
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        output = EVAL_DIR / "reports" / f"behavior_{timestamp}.json"
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(
        json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(
        f"behavior cases: {report['passed']}/{report['num_cases']} passed; "
        f"report={output}"
    )
    raise SystemExit(0 if report["failed"] == 0 else 1)


if __name__ == "__main__":
    main()
