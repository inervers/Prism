from pathlib import Path


def test_sqlite_checkpointer_is_declared():
    requirements = Path("requirements.txt").read_text(encoding="utf-8")
    assert "langgraph-checkpoint-sqlite" in requirements
