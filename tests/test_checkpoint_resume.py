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
