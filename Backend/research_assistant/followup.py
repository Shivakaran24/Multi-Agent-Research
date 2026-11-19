"""Answer follow-up questions about a finished report using only that report."""

from __future__ import annotations

from typing import Any

from langchain_core.messages import HumanMessage, SystemMessage
from langchain_groq import ChatGroq

from research_assistant.config import Settings
from research_assistant.llm_retry import invoke_with_retry

MAX_REPORT_CHARS = 14_000
MAX_ANSWER_CHARS = 4_000

SYSTEM_PROMPT = (
    "You answer follow-up questions about a research brief. Use only the brief provided; it is the "
    "only evidence available. If the brief does not cover the question, say so plainly and suggest "
    "what to research next instead of guessing. Keep the answer focused (roughly 600-2500 characters), "
    "in Markdown, and reuse the brief's own citation numbers like [1] or [2][3] for every factual claim. "
    "Never invent sources or numbers."
)


def build_messages(report: str, question: str) -> list[Any]:
    return [
        SystemMessage(content=SYSTEM_PROMPT),
        HumanMessage(content=f"Research brief:\n{report[:MAX_REPORT_CHARS]}\n\nFollow-up question: {question}"),
    ]


def _make_llm(settings: Settings) -> Any:
    if not settings.groq_api_key:
        raise ValueError("Set GROQ_API_KEY before calling the model.")
    return ChatGroq(
        model=settings.groq_model,
        api_key=settings.groq_api_key.get_secret_value(),
        max_tokens=settings.groq_summary_max_tokens,
        **(
            {"reasoning_effort": "low", "reasoning_format": "hidden"}
            if settings.groq_model in {"openai/gpt-oss-20b", "openai/gpt-oss-120b"}
            else {}
        ),
    )


def _text(message: Any) -> str:
    content = getattr(message, "content", message)
    if isinstance(content, str):
        return content.strip()
    return "".join(part.get("text", "") if isinstance(part, dict) else str(part) for part in content).strip()


def answer_followup(report: str, question: str, settings: Settings, llm: Any | None = None) -> str:
    """Return a Markdown answer grounded in ``report`` (blocking; run in a worker thread)."""
    message = invoke_with_retry(llm or _make_llm(settings), build_messages(report, question))
    return _text(message)[:MAX_ANSWER_CHARS]


CHANGES_PROMPT = (
    "You compare two versions of a recurring research brief on the same topic. Write 3-6 short Markdown "
    "bullets describing what is NEW or CHANGED in the newer brief (new facts, figures, developments, "
    "shifts in the evidence). Do not repeat what stayed the same. If nothing material changed, say so in "
    "one sentence. Use only the two briefs; keep the citation numbers of the newer brief."
)


def summarize_changes(old_report: str, new_report: str, settings: Settings, llm: Any | None = None) -> str:
    """Short 'what changed since last time' note for a recurring brief."""
    message = invoke_with_retry(
        llm or _make_llm(settings),
        [
            SystemMessage(content=CHANGES_PROMPT),
            HumanMessage(content=f"Previous brief:\n{old_report[:6000]}\n\nNewer brief:\n{new_report[:6000]}"),
        ],
    )
    return _text(message)[:2_000]
