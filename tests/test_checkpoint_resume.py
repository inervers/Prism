import json
import subprocess
import sys
from pathlib import Path

from langgraph.checkpoint.memory import MemorySaver

from prism import main
from prism.graph import build_graph
from prism.memory import build_checkpointer


def test_build_graph_uses_supplied_checkpointer():
    saver = MemorySaver()

    graph = build_graph(checkpointer=saver)

    assert graph.checkpointer is saver


def test_new_thread_ids_are_unique():
    first = main.new_thread_id()
    second = main.new_thread_id()

    assert first.startswith("prism-")
    assert second.startswith("prism-")
    assert first != second


def test_build_checkpointer_accepts_explicit_database_path(tmp_path):
    saver = build_checkpointer(tmp_path / "checkpoints.db")

    assert saver is not None


def _worker_result(*args: str) -> dict:
    worker = Path(__file__).parent / "fixtures" / "checkpoint_worker.py"
    completed = subprocess.run(
        [sys.executable, str(worker), *args],
        cwd=Path(__file__).parents[1],
        capture_output=True,
        text=True,
        encoding="utf-8",
        check=False,
    )
    assert completed.returncode == 0, completed.stderr
    return json.loads(completed.stdout.strip().splitlines()[-1])


def test_cross_process_interrupt_resume(tmp_path):
    database = str(tmp_path / "cross-process.db")
    thread_id = "cross-process-thread"

    interrupted = _worker_result("start", database, thread_id)
    resumed = _worker_result("resume", database, thread_id, "approve")

    assert interrupted["status"] == "interrupted"
    assert resumed["status"] == "completed"
    assert resumed["thread_id"] == thread_id
    assert resumed["human_feedback"] == ""
    assert resumed["draft_steps"] == 1
