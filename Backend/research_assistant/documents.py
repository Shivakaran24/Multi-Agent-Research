"""Read uploaded PDFs and turn them into a citable finding."""

from __future__ import annotations

import io
import re
from typing import Any

from research_assistant.schemas import ResearchFinding, Source

MAX_PDF_BYTES = 5 * 1024 * 1024
MAX_PDF_PAGES = 60
MAX_DOCUMENT_CHARS = 30_000
CHUNK_CHARS = 600
EXCERPT_CHARS = 1_800
QUERY = "Documents you uploaded"
_WORD = re.compile(r"[a-z0-9]{4,}")


class DocumentError(ValueError):
    """The upload cannot be used; the message is safe to show to the user."""


def extract_pdf_text(data: bytes) -> str:
    """Plain text of a PDF (first MAX_PDF_PAGES pages, MAX_DOCUMENT_CHARS characters)."""
    if not data.startswith(b"%PDF"):
        raise DocumentError("That file is not a PDF.")
    try:
        from pypdf import PdfReader
    except ImportError as exc:
        raise DocumentError("PDF reading is not installed on the server (pip install pypdf).") from exc
    try:
        reader = PdfReader(io.BytesIO(data))
        if reader.is_encrypted:
            raise DocumentError("That PDF is password protected.")
        parts: list[str] = []
        total = 0
        for page in reader.pages[:MAX_PDF_PAGES]:
            text = " ".join((page.extract_text() or "").split())
            if text:
                parts.append(text)
                total += len(text)
            if total >= MAX_DOCUMENT_CHARS:
                break
    except DocumentError:
        raise
    except Exception as exc:
        raise DocumentError("That PDF could not be read. It may be damaged.") from exc
    text = " ".join(parts)[:MAX_DOCUMENT_CHARS]
    if len(text) < 80:
        raise DocumentError("No readable text found. Scanned PDFs without a text layer are not supported.")
    return text


def best_excerpt(text: str, topic: str) -> str:
    """The passages of `text` that share the most words with the topic, kept in reading order."""
    if len(text) <= EXCERPT_CHARS:
        return text
    wanted = set(_WORD.findall(topic.lower()))
    chunks = [text[i : i + CHUNK_CHARS] for i in range(0, len(text), CHUNK_CHARS)]
    scored = sorted(range(len(chunks)), key=lambda i: (-len(wanted & set(_WORD.findall(chunks[i].lower()))), i))
    chosen = sorted(scored[: EXCERPT_CHARS // CHUNK_CHARS])
    return " … ".join(chunks[i] for i in chosen)


def finding_from_documents(documents: list[dict[str, Any]], topic: str) -> ResearchFinding | None:
    sources: list[Source] = []
    for document in documents:
        name = str(document.get("name") or "document.pdf")
        text = " ".join(str(document.get("text") or "").split())
        if len(text) < 80:
            continue
        excerpt = best_excerpt(text, topic)
        sources.append(Source(title=name, url=f"upload:{name}", snippet=text[:240], content=excerpt))
    if not sources:
        return None
    summary = " ".join(f"{s.title}: {s.content[:450]}" for s in sources)[:1_800]
    return ResearchFinding(query=QUERY, summary=summary, sources=sources)
