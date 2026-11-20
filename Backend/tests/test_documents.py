"""PDF upload, uploaded documents as sources, and the rate-limit model fallback."""

import pytest

from helpers import auth, make_client, register
from test_options import CapturingGraph
from research_assistant import nodes
from research_assistant.documents import DocumentError, best_excerpt, extract_pdf_text, finding_from_documents

pytest.importorskip("pypdf")

LINE = "Grid batteries cut peak costs across the Spanish market in 2024 according to the operator."


def make_pdf(text: str = LINE * 3) -> bytes:
    """A minimal one-page PDF with a text layer."""
    safe = text.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")
    stream = f"BT /F1 10 Tf 20 700 Td ({safe}) Tj ET".encode()
    objects = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 2000 800] /Contents 4 0 R /Resources << /Font << /F1 5 0 R >> >> >>",
        b"<< /Length %d >>\nstream\n" % len(stream) + stream + b"\nendstream",
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
    ]
    out = bytearray(b"%PDF-1.4\n")
    offsets = []
    for number, body in enumerate(objects, start=1):
        offsets.append(len(out))
        out += b"%d 0 obj\n" % number + body + b"\nendobj\n"
    xref = len(out)
    out += b"xref\n0 %d\n0000000000 65535 f \n" % (len(objects) + 1)
    for offset in offsets:
        out += b"%010d 00000 n \n" % offset
    out += b"trailer\n<< /Size %d /Root 1 0 R >>\nstartxref\n%d\n%%%%EOF" % (len(objects) + 1, xref)
    return bytes(out)


def test_extract_pdf_text_and_errors():
    assert "Grid batteries cut peak costs" in extract_pdf_text(make_pdf())
    with pytest.raises(DocumentError, match="not a PDF"):
        extract_pdf_text(b"hello")
    with pytest.raises(DocumentError, match="No readable text"):
        extract_pdf_text(make_pdf("hi"))
    with pytest.raises(DocumentError, match="could not be read"):
        extract_pdf_text(b"%PDF-1.4 broken")


def test_documents_become_a_citable_finding_with_the_relevant_passage():
    filler = "Unrelated paragraph about gardening and soil. " * 80
    text = filler + "Battery storage in Spain reduced curtailment sharply. " * 6 + filler
    assert "Battery storage in Spain" in best_excerpt(text, "Battery storage Spain")
    finding = finding_from_documents([{"name": "report.pdf", "text": text}, {"name": "tiny.pdf", "text": "x"}], "Battery storage Spain")
    assert [s.url for s in finding.sources] == ["upload:report.pdf"] and finding.query == "Documents you uploaded"
    assert finding_from_documents([{"name": "a", "text": "short"}], "t") is None


def test_upload_endpoint_validates_and_returns_text(tmp_path):
    client, _ = make_client(tmp_path)
    with client:
        token = register(client).json()["access_token"]
        headers = {**auth(token), "Content-Type": "application/pdf"}
        ok = client.post("/research/uploads/pdf?name=Spain%20report.pdf", content=make_pdf(), headers=headers)
        assert ok.status_code == 200 and ok.json()["name"] == "Spain report.pdf" and "Grid batteries" in ok.json()["text"]
        assert client.post("/research/uploads/pdf", content=b"nope", headers=headers).status_code == 422
        big = client.post("/research/uploads/pdf", content=b"%PDF" + b"0" * (5 * 1024 * 1024 + 10), headers=headers)
        assert big.status_code == 413
        assert client.post("/research/uploads/pdf", content=make_pdf()).status_code == 401


def test_uploaded_documents_reach_the_graph_but_not_the_stored_options(tmp_path):
    graph = CapturingGraph()
    client, _ = make_client(tmp_path, graph=graph)
    with client:
        token = register(client).json()["access_token"]
        body = {"topic": "Battery storage Spain", "options": {"documents": [{"name": "report.pdf", "text": LINE * 4}]}}
        assert client.post("/research/runs", json=body, headers=auth(token)).status_code == 200
        sent = graph.inputs[-1]
        assert sent["findings"][0].sources[0].url == "upload:report.pdf"
        assert "documents" not in sent["options"]
        too_many = {"topic": "Battery storage Spain", "options": {"documents": [{"name": f"{i}.pdf", "text": LINE * 4} for i in range(4)]}}
        assert client.post("/research/runs", json=too_many, headers=auth(token)).status_code == 422


class Primary:
    def with_fallbacks(self, fallbacks, exceptions_to_handle):
        self.fallbacks, self.handled = fallbacks, exceptions_to_handle
        return self


def test_fallback_is_attached_only_when_configured(monkeypatch):
    class Limited(Exception):
        pass

    monkeypatch.setattr(nodes, "_rate_limit_errors", lambda: (Limited,))
    wrapped = nodes.with_fallback(Primary(), "backup")
    assert wrapped.fallbacks == ["backup"] and wrapped.handled == (Limited,)
    plain = Primary()
    assert nodes.with_fallback(plain, None) is plain and not hasattr(plain, "fallbacks")
    monkeypatch.setattr(nodes, "_rate_limit_errors", lambda: ())
    assert not hasattr(nodes.with_fallback(Primary(), "backup"), "fallbacks")
