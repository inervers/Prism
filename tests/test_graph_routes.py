from pathlib import Path

import pytest

from prism.nodes.reviewer import route_after_reviewer
from prism.context import TRUNCATE_SUFFIX, compact_evidence
from prism.nodes.abort import no_evidence_node, route_after_aggregator
from prism.nodes.human import route_after_human


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


def test_no_evidence_short_circuit_is_not_a_quality_pass():
    assert route_after_aggregator({"grouped_evidence": {}}) == "no_evidence"

    result = no_evidence_node({})

    assert result["quality_passed"] is False
    assert result["review_status"] == "no_evidence"


def test_hitl_feedback_routes_to_revision():
    assert route_after_human({"human_feedback": "补充风险说明"}) == "revise"


def test_context_overflow_is_compacted_without_dropping_evidence():
    evidence = [
        {
            "sub_id": "q1",
            "content": "证据" * 500,
            "source": "https://example.test/long",
            "title": "长证据",
        }
    ]

    compacted, changed = compact_evidence(evidence, budget_chars=300)

    assert changed is True
    assert len(compacted) == 1
    assert compacted[0]["content"].endswith(TRUNCATE_SUFFIX)
