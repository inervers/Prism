import json
import subprocess
import sys
from pathlib import Path

from eval.metrics import aggregate_metrics, compute_task_metrics


def _state(**overrides):
    state = {
        "topic": "指标测试",
        "report": "x" * 200,
        "subquestions": [{"id": "q1"}],
        "evidence": [{"source": "s"}],
        "grouped_evidence": {"q1": [{"source": "s"}]},
        "trace": [],
        "quality_passed": False,
        "review_status": "retry_exhausted",
        "terminated_by_limit": True,
    }
    state.update(overrides)
    return state


def test_retry_exhausted_is_not_quality_pass():
    result = compute_task_metrics(_state(), 1.0, "case-exhausted")

    assert result["workflow_completed"] is True
    assert result["quality_passed"] is False
    assert result["review_status"] == "retry_exhausted"
    assert result["terminated_by_limit"] is True


def test_parse_error_is_counted_from_reviewer_trace():
    state = _state(
        review_status="passed",
        quality_passed=True,
        terminated_by_limit=False,
        trace=[
            {
                "node": "reviewer",
                "detail": "quality_passed=False status=parse_error issues=1",
                "tokens": 0,
            },
            {
                "node": "reviewer",
                "detail": "quality_passed=True status=passed issues=0",
                "tokens": 0,
            },
        ],
    )

    result = compute_task_metrics(state, 1.0, "case-parse")

    assert result["parse_error_count"] == 1


def test_aggregate_separates_completion_from_clean_pass():
    completed_but_exhausted = compute_task_metrics(_state(), 1.0, "exhausted")
    clean = compute_task_metrics(
        _state(
            quality_passed=True,
            review_status="passed",
            terminated_by_limit=False,
        ),
        1.0,
        "clean",
    )

    result = aggregate_metrics([completed_but_exhausted, clean])

    assert result["workflow_completion_rate"] == 1.0
    assert result["clean_pass_rate"] == 0.5
    assert result["retry_exhausted_rate"] == 0.5


def test_behavior_dataset_defines_ten_unique_cases():
    path = Path("eval/behavior_cases.json")

    assert path.exists()
    cases = json.loads(path.read_text(encoding="utf-8"))["cases"]
    ids = [case["id"] for case in cases]
    assert len(ids) == 10
    assert len(set(ids)) == 10


def test_behavior_runner_writes_machine_readable_report(tmp_path):
    output = tmp_path / "behavior.json"
    completed = subprocess.run(
        [
            sys.executable,
            "-m",
            "eval.run_behavior_eval",
            "--output",
            str(output),
        ],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=False,
    )

    assert completed.returncode == 0, completed.stdout + completed.stderr
    report = json.loads(output.read_text(encoding="utf-8"))
    assert report["num_cases"] == 10
    assert report["passed"] == 10
    assert report["dataset_sha256"]


def test_eval_cli_exposes_search_snapshot_modes():
    completed = subprocess.run(
        [sys.executable, "-m", "eval.run_eval", "--help"],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=False,
    )

    assert completed.returncode == 0
    assert "--search-snapshot" in completed.stdout
    assert "--record-search-snapshot" in completed.stdout


def test_eval_detects_langgraph_v1_interrupt_event():
    from eval.run_eval import _is_interrupt_event

    assert _is_interrupt_event("__interrupt__", (object(),)) is True
    assert _is_interrupt_event("writer", {"report": "done"}) is False


def test_eval_progress_markers_are_gbk_safe():
    from eval.run_eval import _progress_marker

    for kind in ("running", "passed", "failed"):
        _progress_marker(kind).encode("gbk")
