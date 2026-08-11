import pytest


class FakeResponse:
    def __init__(self, content: str, tokens: int = 0):
        self.content = content
        self.usage_metadata = {"total_tokens": tokens}


class SequenceLLM:
    def __init__(self, responses: list[str]):
        self.responses = iter(responses)

    def invoke(self, _prompt: str):
        return FakeResponse(next(self.responses), tokens=10)


@pytest.fixture
def base_state():
    def _make_state() -> dict:
        return {
            "topic": "测试主题",
            "grouped_evidence": {
                "q1": [
                    {
                        "sub_id": "q1",
                        "content": "证据正文明确说明方案 A 降低人工处理步骤。",
                        "source": "https://example.test/a",
                        "title": "证据 A",
                    }
                ]
            },
            "report": "方案 A 可以降低人工处理步骤。[q1-1]",
            "rewrite_count": 0,
        }

    return _make_state
