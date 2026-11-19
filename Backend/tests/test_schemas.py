"""Model output that overshoots a length cap is trimmed, not fatal."""

from research_assistant.schemas import FinalReport, ResearchQuestion, ResearchSummary, ReviewDecision


def test_overlong_summary_is_trimmed_at_a_sentence():
    text = ("Evidence shows heat adaptation is uneven. " * 30).strip()
    out = ResearchSummary.model_validate({"summary": text + "\n"}).summary
    assert len(out) <= 2000 and out.endswith(".")


def test_trims_on_word_boundary_when_no_sentence_break():
    out = ResearchSummary.model_validate({"summary": "word " * 900}).summary
    assert len(out) <= 2000 and out.endswith("…") and "wor…" not in out


def test_short_values_pass_through_stripped():
    assert ResearchSummary.model_validate({"summary": " ok \n"}).summary == "ok"


def test_other_llm_fields_are_trimmed():
    q = ResearchQuestion.model_validate({"question": "Why? " * 100, "rationale": "x " * 300})
    assert len(q.question) <= 240 and len(q.rationale) <= 240
    assert len(ReviewDecision.model_validate({"sufficient": True, "rationale": "a " * 400}).rationale) <= 300
    r = FinalReport.model_validate({"title": "T" * 500, "markdown": "para\n\n" * 5000})
    assert len(r.title) <= 120 and len(r.markdown) <= 12000


def test_too_short_question_still_rejected():
    import pytest
    with pytest.raises(Exception):
        ResearchQuestion.model_validate({"question": "hi", "rationale": ""})


def test_citation_sanitizer_and_excerpts():
    from research_assistant.nodes import _citation_context, sanitize_citations
    from research_assistant.schemas import ResearchFinding, Source

    assert sanitize_citations("A [1] B [3][2] C [9].", 3) == "A [1] B [3][2] C ."
    assert sanitize_citations("List [0] and [link](http://x)", 3) == "List  and [link](http://x)"
    finding = ResearchFinding(
        query="q", summary="s",
        sources=[Source(title="T", url="https://example.com/a", snippet="snip", content="x" * 900)],
    )
    context, bibliography = _citation_context([finding])
    assert '"excerpt":"' + "x" * 350 + '"' in context and "x" * 351 not in context
    assert bibliography.startswith("[1] T")
