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


def evidence_text(
    findings: list[ResearchFinding], *, summary_chars: int = 500, excerpt_chars: int = 250, include_summaries: bool = True
) -> str:
    """Compact evidence with the same source numbering the writer used."""
    numbering: dict[str, int] = {}
    lines: list[str] = []
    for finding in findings:
        if include_summaries:
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


# --- strict mode: only statements the verified sources support survive -----------------------------

STRICT_NOTE = "> Verification: {n} statement{s} that the verified sources do not support {were} removed."
UNCHECKED_NOTE = (
    "> ⚠ The independent claim check could not run for this brief, so its statements were not checked "
    "against the sources. Verify important points yourself."
)
_SENTENCE_END = re.compile(r"(?<=[.!?])\s+(?=[A-Z0-9\"“(\[])")


def _table_block(lines: list[str], index: int) -> tuple[int, int]:
    start = index
    while start > 0 and lines[start - 1].lstrip().startswith("|"):
        start -= 1
    end = index
    while end + 1 < len(lines) and lines[end + 1].lstrip().startswith("|"):
        end += 1
    return start, end


def remove_unsupported(markdown: str, quotes: list[str]) -> tuple[str, int]:
    """Delete the sentence (or list item / table row) containing each unsupported quote. Headings stay."""
    removed = 0
    for quote in quotes:
        span = _find(markdown, quote)
        if span is None:
            continue
        lines = markdown.split("\n")
        offset = 0
        for index, line in enumerate(lines):
            if offset <= span[0] <= offset + len(line):
                break
            offset += len(line) + 1
        else:
            continue
        stripped = line.lstrip()
        if stripped.startswith("#") or stripped.startswith(">"):
            continue
        if stripped.startswith("|"):
            first, _ = _table_block(lines, index)
            if index - first < 2:  # header and separator rows stay
                continue
            del lines[index]
        else:
            lead = re.match(r"\s*(?:[-*+]|\d+\.)\s+", line)
            prefix = lead.group(0) if lead else ""
            body = line[len(prefix):]
            local = span[0] - offset - len(prefix)
            pieces, position, keep = [], 0, []
            for part in _SENTENCE_END.split(body):
                begin = body.find(part, position)
                position = begin + len(part)
                if not (begin <= local < position or begin < local + (span[1] - span[0]) <= position):
                    keep.append(part)
                pieces.append(part)
            if len(keep) == len(pieces):
                continue
            if keep:
                lines[index] = prefix + " ".join(keep)
            else:
                del lines[index]
        markdown = re.sub(r"\n{3,}", "\n\n", "\n".join(lines))
        removed += 1
    return markdown, removed


def strict_check(markdown: str, findings: list[ResearchFinding], checker: Any, *, passes: int = 2) -> str:
    """Remove statements the verified source passages do not support; say so (or say the check failed)."""
    total = 0
    try:
        for _ in range(passes):
            result = invoke_with_retry(
                checker,
                [
                    SystemMessage(content=SYSTEM),
                    HumanMessage(
                        content=f"Brief:\n{markdown[:9000]}\n\nEvidence:\n"
                        + evidence_text(findings, excerpt_chars=700, include_summaries=False)
                    ),
                ],
                ClaimCheck,
            )
            markdown, removed = remove_unsupported(markdown, result.unsupported)
            total += removed
            if not removed:
                break
    except Exception:
        logger.warning("Strict claim check failed", exc_info=True)
        return markdown.rstrip() + "\n\n" + UNCHECKED_NOTE
    if total:
        markdown = markdown.rstrip() + "\n\n" + STRICT_NOTE.format(n=total, s="" if total == 1 else "s", were="was" if total == 1 else "were")
    return markdown
