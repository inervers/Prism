from pathlib import Path

import pytest

from prism.nodes.reviewer import route_after_reviewer


def test_sqlite_checkpointer_is_declared():
    requirements = Path("requirements.txt").read_text(encoding="utf-8")
    assert "langgraph-checkpoint-sqlite" in requirements


@pytest.mark.parametrize(
    ("state", "expected"),
    [
        ({"quality_passed": True, "review_status": "passed"}, "human_review"),
        (
            {
                "quality_passed": False,
                "review_status": "failed",
                "rewrite_count": 0,
            },
            "writer",
        ),
        (
            {
                "quality_passed": False,
                "review_status": "parse_error",
                "review_parse_attempts": 1,
            },
            "reviewer",
        ),
        (
            {
                "quality_passed": False,
                "review_status": "retry_exhausted",
                "terminated_by_limit": True,
            },
            "human_review",
        ),
    ],
)
def test_reviewer_routes_by_quality_status(state, expected):
    assert route_after_reviewer(state) == expected
