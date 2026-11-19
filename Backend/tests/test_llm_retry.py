"""Model-call resilience: rate limits, and malformed JSON from the model."""

import pytest

from research_assistant.llm_retry import (
    failed_generation,
    invoke_with_retry,
    salvage_single_string_field,
)
from research_assistant.schemas import ResearchSummary


class ApiError(Exception):
    def __init__(self, message="boom", status_code=400, body=None):
        super().__init__(message)
        self.status_code = status_code
        self.body = body


def json_failure(raw):
    return ApiError(
        "Error code: 400 - json_validate_failed", 400, {"code": "json_validate_failed", "failed_generation": raw}
    )


class Flaky:
    def __init__(self, *outcomes):
        self.outcomes = list(outcomes)
        self.calls = 0

    def invoke(self, messages):
        self.calls += 1
        outcome = self.outcomes.pop(0)
        if isinstance(outcome, Exception):
            raise outcome
        return outcome


def test_rate_limit_is_retried_with_backoff():
    waits = []
    flaky = Flaky(ApiError("try again in 2s", 429), "ok")
    assert invoke_with_retry(flaky, [], sleep=waits.append) == "ok" and flaky.calls == 2
    assert 2 <= waits[0] < 3


def test_rate_limit_gives_up_eventually():
    flaky = Flaky(*[ApiError("slow down", 429)] * 6, "never")
    with pytest.raises(ApiError):
        invoke_with_retry(flaky, [], sleep=lambda s: None)
    assert flaky.calls == 6


def test_malformed_json_is_regenerated():
    flaky = Flaky(json_failure('{"summary": "bad'), "second try works")
    assert invoke_with_retry(flaky, [], ResearchSummary) == "second try works" and flaky.calls == 2


def test_persistent_malformed_json_is_salvaged_from_the_error():
    # The shape seen in production: the text is fine but the closing quote is escaped.
    raw = '{"summary":"Studies report bias \\u2013 across groups.\\n\\nMeasures vary.\\"}'
    flaky = Flaky(*[json_failure(raw)] * 3)
    result = invoke_with_retry(flaky, [], ResearchSummary)
    assert result.summary.startswith("Studies report bias") and result.summary.endswith("Measures vary.")
    assert flaky.calls == 3


def test_unsalvageable_output_still_raises():
    flaky = Flaky(*[json_failure("not json at all")] * 3)
    with pytest.raises(ApiError):
        invoke_with_retry(flaky, [], ResearchSummary)


def test_other_errors_are_not_swallowed():
    flaky = Flaky(ApiError("invalid api key", 401), "never")
    with pytest.raises(ApiError):
        invoke_with_retry(flaky, [], ResearchSummary)
    assert flaky.calls == 1


def test_salvage_helpers():
    assert salvage_single_string_field('{"summary": "fine"}') == {"summary": "fine"}
    assert salvage_single_string_field('{"summary":"a \\"quoted\\" word\\"}') == {"summary": 'a "quoted" word'}
    assert salvage_single_string_field("garbage") is None
    assert failed_generation(ApiError(body={"error": {"failed_generation": "x"}})) == "x"
    assert failed_generation(ApiError(body="")) is None
