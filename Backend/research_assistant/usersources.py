"""Turn web pages the user supplied into a research finding the writer can cite."""

from __future__ import annotations

import logging
from typing import Any

from research_assistant.schemas import ResearchFinding, Source

logger = logging.getLogger(__name__)

PER_SOURCE_CHARS = 1_200
QUERY = "Sources you provided"


def title_from(url: str, content: str) -> str:
    first = next((line.strip(" #*-\t") for line in content.splitlines() if len(line.strip(" #*-\t")) > 8), "")
    return (first[:120] or url)


def finding_from_pages(pages: list[dict[str, Any]]) -> ResearchFinding | None:
    """Build one finding from extracted pages ({'url', 'raw_content'}). None when nothing is readable."""
    sources: list[Source] = []
    for page in pages:
        url = str(page.get("url") or "")
        content = " ".join(str(page.get("raw_content") or page.get("content") or "").split())
        if not url or len(content) < 80:
            continue
        sources.append(
            Source(title=title_from(url, str(page.get("raw_content") or "")), url=url, snippet=content[:240], content=content[:PER_SOURCE_CHARS])
        )
    if not sources:
        return None
    summary = " ".join(f"{s.title}: {s.content[:450]}" for s in sources)[:1_800]
    return ResearchFinding(query=QUERY, summary=summary, sources=sources)


def fetch_user_sources(urls: list[str], client: Any) -> ResearchFinding | None:
    """Read the pages through Tavily's extract API (blocking; run in a worker thread)."""
    if not urls:
        return None
    response = client.extract(urls=urls)
    return finding_from_pages(list(response.get("results", [])))
