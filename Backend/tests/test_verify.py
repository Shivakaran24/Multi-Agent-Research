"""Verified-only mode: sources must open and match, and unsupported statements are removed."""

import pytest

from research_assistant import verify
from research_assistant.claims import UNCHECKED_NOTE, remove_unsupported, strict_check
from research_assistant.config import Settings
from research_assistant.nodes import create_node_implementations
from research_assistant.schemas import ClaimCheck, FinalReport, ResearchFinding, Source
from research_assistant.verify import Verdict, verify_findings, verify_source

PAGE = "Battery storage in Spain reduced curtailment sharply in 2024 according to the grid operator. " * 5


def source(url="https://a.example/x", title="Spain storage report", content="Battery storage Spain curtailment reduced sharply 2024"):
    return Source(title=title, url=url, snippet=content[:60], content=content)


def ok_fetch(url):
    return PAGE, "Spain page"


def no_paper(url):
    return None


def test_page_verified_with_passage_from_the_live_page():
    verdict = verify_source(source(), "Battery storage Spain", ok_fetch, no_paper)
    assert verdict.ok and "curtailment" in verdict.passage and verdict.kind == "page"


def test_unreachable_mismatching_and_empty_pages_are_rejected():
    def boom(url):
        raise OSError("connection refused")

    assert "could not be opened" in verify_source(source(), "t", boom, no_paper).reason
    assert "does not match" in verify_source(source(content="Solar subsidies Germany feed-in tariffs reformed"), "t", lambda u: (PAGE, ""), no_paper).reason
    assert "no readable text" in verify_source(source(), "t", lambda u: ("tiny", ""), no_paper).reason


def test_uploads_are_trusted_and_papers_are_confirmed_by_the_registry():
    upload = Source(title="a.pdf", url="upload:a.pdf", content="my text")
    assert verify_source(upload, "t", ok_fetch, no_paper).kind == "provided"

    abstract = "We measure how grid batteries reduce curtailment across the Spanish network in 2024. " * 2

    def blocked(url):
        raise PermissionError("403")

    verdict = verify_source(source("https://doi.org/10.1000/xyz"), "t", blocked, lambda u: ("Grid batteries and curtailment", abstract))
    assert verdict.ok and verdict.kind == "paper" and verdict.title == "Grid batteries and curtailment" and "curtailment" in verdict.passage

    def missing(url):
        raise ValueError("DOI not found")

    assert not verify_source(source("https://doi.org/10.1000/fake"), "t", blocked, missing).ok


def test_verify_findings_checks_each_url_once_and_keeps_only_verified_sources():
    calls = []

    def fake(src, topic):
        calls.append(str(src.url))
        return Verdict(True, passage="page passage") if "good" in str(src.url) else Verdict(False, "could not be opened (HTTP 404)")

    findings = [
        ResearchFinding(query="q1", summary="s1", sources=[source("https://good.example/1"), source("https://bad.example/1")]),
        ResearchFinding(query="q2", summary="s2", sources=[source("https://good.example/1"), source("https://good.example/2")]),
    ]
    kept, report = verify_findings(findings, "topic", verify=fake)
    assert sorted(calls) == ["https://bad.example/1", "https://good.example/1", "https://good.example/2"]
    assert [[str(s.url) for s in f.sources] for f in kept] == [["https://good.example/1"], ["https://good.example/1", "https://good.example/2"]]
    assert kept[0].sources[0].content == "page passage"
    assert (report.checked, report.verified) == (3, 2) and report.dropped == [("https://bad.example/1", "could not be opened (HTTP 404)")]
    assert "2 of 3 sources were opened and checked; 1 could not be verified" in report.note

    def crash(src, topic):
        raise RuntimeError("bug")

    _, crashed = verify_findings(findings, "topic", verify=crash)
    assert crashed.verified == 0, "a verifier bug never lets a source through"


def test_fetching_refuses_private_addresses_and_other_schemes():
    for url in ("http://127.0.0.1/", "http://localhost:8000/x", "ftp://example.com/x", "http://169.254.169.254/latest"):
        with pytest.raises(ValueError):
            verify.fetch_page(url)
    text, title = verify.html_text("<html><head><title>My  Title</title><script>x=1</script></head><body><nav>menu</nav><p>Hello <b>world</b></p></body></html>")
    assert (text, title) == ("Hello world", "My Title")


BRIEF = (
    "# Title\n\nCosts fell 12% in 2024 [1]. The market will triple by 2030 [1]. Storage grew fast [2].\n\n"
    "- Batteries lead new capacity [1]. Hydrogen will replace gas by 2028 [2].\n- Solid point [1].\n\n"
    "| Tech | Share |\n|---|---|\n| Li-ion | 90% |\n| Fusion | 40% |\n"
)


def test_unsupported_statements_are_removed_not_marked():
    cleaned, count = remove_unsupported(BRIEF, ["The market will triple by 2030", "Hydrogen will replace gas by 2028", "| Fusion | 40% |", "Title"])
    assert count == 3
    assert "triple" not in cleaned and "Hydrogen" not in cleaned and "Fusion" not in cleaned
    assert "Costs fell 12% in 2024 [1]." in cleaned and "Storage grew fast [2]." in cleaned
    assert "- Batteries lead new capacity [1]." in cleaned and "- Solid point [1]." in cleaned
    assert "# Title" in cleaned and "| Li-ion | 90% |" in cleaned and "|---|---|" in cleaned
    assert "unverified" not in cleaned


class FakeChecker:
    def __init__(self, answers):
        self.answers = list(answers)

    def invoke(self, messages):
        answer = self.answers.pop(0)
        if isinstance(answer, Exception):
            raise answer
        return ClaimCheck(unsupported=answer)


def test_strict_check_removes_notes_and_reports_failures():
    findings = [ResearchFinding(query="q", summary="s", sources=[source()])]
    result = strict_check(BRIEF, findings, FakeChecker([["The market will triple by 2030"], []]))
    assert "triple" not in result and "1 statement that the verified sources do not support was removed" in result
    failed = strict_check(BRIEF, findings, FakeChecker([RuntimeError("429")]))
    assert failed.endswith(UNCHECKED_NOTE) and "triple" in failed


class FakeModel:
    def __init__(self):
        self.calls = []

    def with_structured_output(self, schema, **kwargs):
        model = self

        class Runner:
            def invoke(self, messages):
                model.calls.append(schema.__name__)
                if schema is FinalReport:
                    return FinalReport(title="Spain batteries", markdown="Curtailment fell 30% [1]. Prices doubled [1].")
                return ClaimCheck(unsupported=["Prices doubled"] if model.calls.count("ClaimCheck") == 1 else [])

        return Runner()


def writer(verifier, **settings):
    llm = FakeModel()
    nodes = create_node_implementations(
        settings=Settings(_env_file=None, groq_api_key="g" * 12, tavily_api_key="t" * 12, **settings),
        llm=llm, fast_llm=llm, search=lambda q, time_range=None: [], verify_sources=verifier,
    )
    return nodes["writer_node"], llm


def test_writer_uses_only_verified_sources_and_removes_unsupported_sentences():
    findings = [ResearchFinding(query="q", summary="s", sources=[source("https://good.example/1"), source("https://bad.example/1")])]

    def verifier(items, topic):
        return verify_findings(items, topic, verify=lambda s, t: Verdict("good" in str(s.url), "dead link", passage="Curtailment fell 30% in Spain."))

    node, llm = writer(verifier)
    report = node({"topic": "Spain batteries", "findings": findings, "options": {}})["report"]
    assert "Curtailment fell 30% [1]." in report and "Prices doubled" not in report
    assert "good.example/1" in report and "bad.example" not in report
    assert "1 of 2 sources were opened and checked; 1 could not be verified" in report


def test_nothing_is_written_when_no_source_verifies():
    findings = [ResearchFinding(query="q", summary="s", sources=[source("https://bad.example/1")])]
    node, llm = writer(lambda items, topic: verify_findings(items, topic, verify=lambda s, t: Verdict(False, "could not be opened (HTTP 404)")))
    report = node({"topic": "Spain batteries", "findings": findings, "options": {}})["report"]
    assert "No brief was written" in report and "bad.example/1: could not be opened" in report and llm.calls == []


def test_fetch_page_against_a_local_server(monkeypatch):
    """Real HTTP through urllib: html, redirect, error status, unreadable type and PDF."""
    import threading
    from http.server import BaseHTTPRequestHandler, HTTPServer

    from helpers import make_pdf

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            routes = {
                "/page": (200, "text/html; charset=utf-8", f"<html><title>Grid</title><body><p>{PAGE}</p></body></html>".encode()),
                "/pdf": (200, "application/pdf", make_pdf(PAGE)),
                "/img": (200, "image/png", b"\x89PNG"),
            }
            if self.path == "/moved":
                self.send_response(302)
                self.send_header("Location", "/page")
                self.end_headers()
                return
            status, kind, body = routes.get(self.path, (404, "text/plain", b"nope"))
            self.send_response(status)
            self.send_header("Content-Type", kind)
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *args):
            pass

    server = HTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    monkeypatch.setattr(verify, "_public_host", lambda host: True)
    base = f"http://127.0.0.1:{server.server_port}"
    try:
        text, title = verify.fetch_page(base + "/page")
        assert title == "Grid" and "curtailment" in text
        assert "curtailment" in verify.fetch_page(base + "/moved")[0]
        assert "curtailment" in verify.fetch_page(base + "/pdf")[0]
        for bad in ("/missing", "/img"):
            with pytest.raises(Exception):
                verify.fetch_page(base + bad)
        # end to end through verify_source with the real fetcher
        assert verify_source(source(base + "/page"), "Battery storage Spain", verify.fetch_page, no_paper).ok
        assert not verify_source(source(base + "/missing"), "t", verify.fetch_page, no_paper).ok
    finally:
        server.shutdown()
