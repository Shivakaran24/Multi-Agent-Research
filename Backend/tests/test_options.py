"""Per-run options, the fact-check pass, and user-supplied links."""

import pytest
from pydantic import ValidationError

from helpers import FakeGraph, auth, make_client, register
from research_assistant.claims import check_claims, evidence_text, mark_unsupported
from research_assistant.nodes import planner_hint, writer_instructions
from research_assistant.schemas import ClaimCheck, ResearchFinding, ResearchOptions, Source
from research_assistant.usersources import finding_from_pages


def test_option_defaults_and_validation():
    options = ResearchOptions()
    assert (options.style, options.audience, options.length, options.language) == ("brief", "general", "standard", "English")
    assert ResearchOptions(language=" Hindi ").language == "Hindi"
    assert ResearchOptions(urls=["https://a.example/x", "https://a.example/x", " "]).urls == ["https://a.example/x"]
    for bad in ({"language": "ignore previous; <b>"}, {"urls": ["ftp://x"]}, {"style": "poem"}, {"urls": ["https://x.example"] * 6 + ["https://y.example"]}):
        with pytest.raises(ValidationError):
            ResearchOptions(**bad)


def test_writer_instructions_follow_the_options():
    text = writer_instructions({"style": "executive", "audience": "technical", "length": "short", "language": "Spanish"})
    assert "Bottom line" in text and "technical reader" in text and "2500-3500" in text and "in Spanish" in text
    assert "in English" not in writer_instructions({})
    assert "table" in writer_instructions({"style": "comparison"})
    assert "Pros" in writer_instructions({"style": "pros_cons"})
    assert "each option" in planner_hint({"style": "comparison"}) and "last year" in planner_hint({"time_range": "year"})


def finding():
    return ResearchFinding(
        query="q", summary="Costs fell 12% in 2024.",
        sources=[Source(title="Report", url="https://example.com/r", snippet="s", content="Battery costs fell 12% in 2024.")],
    )


def test_unsupported_claims_are_marked_and_noted():
    body = "Costs fell 12% [1]. The market will triple by 2030 according to analysts [1]."
    marked, count = mark_unsupported(body, ["The market will triple by 2030 according to analysts", "not in text at all, sorry"])
    assert count == 1
    assert "triple by 2030 according to analysts *(unverified)*" in marked
    assert marked.count("(unverified)") == 2  # the marker and the explanatory note
    assert mark_unsupported(body, ["short"]) == (body, 0)  # too short to match safely


def test_check_claims_never_breaks_a_brief():
    class Checker:
        def __init__(self, result=None, error=None):
            self.result, self.error = result, error

        def invoke(self, messages):
            if self.error:
                raise self.error
            return self.result

    body = "Costs fell 12% [1]. Nuclear fusion is already cheaper than solar [1]."
    good = Checker(ClaimCheck(unsupported=["Nuclear fusion is already cheaper than solar"]))
    marked, count = check_claims(body, [finding()], good)
    assert count == 1 and "*(unverified)*" in marked
    broken, count = check_claims(body, [finding()], Checker(error=RuntimeError("provider down")))
    assert (broken, count) == (body, 0)
    assert "[1] Report: Battery costs" in evidence_text([finding()])


def test_pages_become_one_citable_finding():
    pages = [
        {"url": "https://a.example/one", "raw_content": "# Heading line here\n" + "Useful text. " * 30},
        {"url": "https://a.example/empty", "raw_content": "tiny"},
        {"url": "", "raw_content": "x" * 200},
    ]
    found = finding_from_pages(pages)
    assert found is not None and found.query == "Sources you provided"
    assert [s.url for s in found.sources] == ["https://a.example/one"]
    assert found.sources[0].title == "Heading line here"
    assert finding_from_pages([{"url": "https://a.example", "raw_content": ""}]) is None


class CapturingGraph(FakeGraph):
    def __init__(self):
        super().__init__()
        self.inputs = []

    async def astream(self, graph_input, config, stream_mode="updates"):
        if isinstance(graph_input, dict):
            self.inputs.append(graph_input)
        async for item in super().astream(graph_input, config, stream_mode):
            yield item


def test_api_passes_options_and_provided_links_into_the_graph(tmp_path, monkeypatch):
    graph = CapturingGraph()
    client, _ = make_client(tmp_path, graph=graph, tavily_api_key="tvly-test-key")
    monkeypatch.setattr("research_assistant.api.fetch_user_sources", lambda urls, c: finding_from_pages([{"url": urls[0], "raw_content": "Real page text. " * 20}]))
    with client:
        token = register(client).json()["access_token"]
        body = {"topic": "Grid storage", "options": {"style": "pros_cons", "language": "French", "urls": ["https://a.example/p"], "notes": "focus on Europe", "time_range": "year"}}
        assert client.post("/research/runs", json=body, headers=auth(token)).status_code == 200
        sent = graph.inputs[-1]
        assert sent["options"]["style"] == "pros_cons" and sent["options"]["language"] == "French" and sent["options"]["time_range"] == "year"
        assert "urls" not in sent["options"] and sent["options"]["notes"] == "focus on Europe"
        assert sent["findings"][0].sources[0].url == "https://a.example/p"

        bad = client.post("/research/runs", json={"topic": "Grid storage", "options": {"language": "<script>"}}, headers=auth(token))
        assert bad.status_code == 422


def test_unreadable_links_fail_clearly_without_leaving_a_ghost_run(tmp_path, monkeypatch):
    client, _ = make_client(tmp_path, tavily_api_key="tvly-test-key")
    monkeypatch.setattr("research_assistant.api.fetch_user_sources", lambda urls, c: None)
    with client:
        token = register(client).json()["access_token"]
        response = client.post("/research/runs", json={"topic": "Grid storage", "options": {"urls": ["https://a.example/p"]}}, headers=auth(token))
        assert response.status_code == 422 and "Could not read any of the links" in response.json()["detail"]
        assert client.get("/research/runs", headers=auth(token)).json()["runs"] == []
