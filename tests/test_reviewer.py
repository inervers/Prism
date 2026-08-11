from prism.nodes import reviewer


class FakeResponse:
    def __init__(self, content: str, tokens: int = 0):
        self.content = content
        self.usage_metadata = {"total_tokens": tokens}


class CapturingLLM:
    def __init__(self, response: str):
        self.response = response
        self.prompt = ""

    def invoke(self, prompt: str):
        self.prompt = prompt
        return FakeResponse(self.response, tokens=10)


def test_reviewer_prompt_contains_evidence_body(monkeypatch, base_state):
    llm = CapturingLLM(
        '{"quality_passed": true, "claim_verdicts": [], '
        '"issues": [], "rewrite_targets": []}'
    )
    monkeypatch.setattr(reviewer, "build_llm", lambda **_: llm)

    result = reviewer.reviewer_node(base_state())

    assert "证据正文明确说明" in llm.prompt
    assert result["quality_passed"] is True
    assert result["review_status"] == "passed"


def test_reviewer_parse_error_fails_closed(monkeypatch, base_state):
    llm = CapturingLLM("not-json")
    monkeypatch.setattr(reviewer, "build_llm", lambda **_: llm)

    result = reviewer.reviewer_node(base_state())

    assert result["quality_passed"] is False
    assert result["review_status"] == "parse_error"
    assert result["review_parse_error"]


def test_reviewer_rejects_invalid_citation_without_calling_llm(monkeypatch, base_state):
    state = base_state()
    state["report"] = "该结论引用了不存在的证据。[q1-9]"

    def fail_if_called(**_kwargs):
        raise AssertionError("LLM should not run after deterministic validation fails")

    monkeypatch.setattr(reviewer, "build_llm", fail_if_called)
    result = reviewer.reviewer_node(state)

    assert result["quality_passed"] is False
    assert result["review_status"] == "failed"
    assert "q1-9" in result["review_issues"][0]
