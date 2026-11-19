"""Claim check: ask a model which statements in a finished brief its sources do not support."""

from __future__ import annotations

import logging
import re
from typing import Any

from langchain_core.messages import HumanMessage, SystemMessage

from research_assistant.llm_retry import invoke_with_retry
from research_assistant.schemas import ClaimCheck, ResearchFinding

logger = logging.getLogger(__name__)

NOTE = (
    "> ⚠ Statements marked *(unverified)* could not be matched to the retrieved source excerpts. "
    "Check them against the cited sources before relying on them."
)

SYSTEM = (
    "You are a strict fact-checker. You get a research brief and the evidence it was written from "
    "(numbered source excerpts and research summaries). List up to 6 statements in the brief that the "
    "evidence does NOT support: specific figures, dates, names or causal claims that appear in no "
    "excerpt or summary. Ignore opinions, hedged wording and general framing. Return each as an exact "
    "short quote copied from the brief (at most 200 characters, no citation markers). If everything is "
    "supported, return an empty list."
)


def evidence_text(findings: list[ResearchFinding], *, summary_chars: int = 500, excerpt_chars: int = 250) -> str:
    """Compact evidence with the same source numbering the writer used."""
    numbering: dict[str, int] = {}
    lines: list[str] = []
    for finding in findings:
        lines.append(f"Summary: {finding.summary[:summary_chars]}")
        for source in finding.sources:
            if source.url not in numbering:
                numbering[source.url] = len(numbering) + 1
                excerpt = (source.content or source.snippet or "")[:excerpt_chars]
                lines.append(f"[{numbering[source.url]}] {source.title}: {excerpt}")
    return "\n".join(lines)


def _find(markdown: str, quote: str) -> tuple[int, int] | None:
    """Locate a quote, tolerating whitespace differences and a trailing citation marker."""
    quote = re.sub(r"\[\d+\]", "", quote).strip(" \"'“”")
    if len(quote) < 12:
        return None
    index = markdown.find(quote)
    if index >= 0:
        return index, index + len(quote)
    pattern = r"\s+".join(re.escape(part) for part in quote.split())
    match = re.search(pattern, markdown, re.I)
    return (match.start(), match.end()) if match else None


def mark_unsupported(markdown: str, quotes: list[str]) -> tuple[str, int]:
    """Append an *(unverified)* tag after each located quote and a note at the end."""
    spans = sorted({span for q in quotes if (span := _find(markdown, q))}, reverse=True)
    for start, end in spans:
        markdown = markdown[:end] + " *(unverified)*" + markdown[end:]
    if spans:
        markdown = markdown.rstrip() + "\n\n" + NOTE
    return markdown, len(spans)


def check_claims(markdown: str, findings: list[ResearchFinding], checker: Any) -> tuple[str, int]:
    """Return the brief with unsupported statements marked. Never raises: a failed check changes nothing.

    `checker` is a model already bound to the ClaimCheck schema.
    """
    try:
        result = invoke_with_retry(
            checker,
            [
                SystemMessage(content=SYSTEM),
                HumanMessage(content=f"Brief:\n{markdown[:9000]}\n\nEvidence:\n{evidence_text(findings)}"),
            ],
            ClaimCheck,
        )
        return mark_unsupported(markdown, result.unsupported)
    except Exception:
        logger.warning("Claim check failed; leaving the brief unmarked", exc_info=True)
        return markdown, 0
