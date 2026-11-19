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


def answer_followup(report: str, question: str, settings: Settings, llm: Any | None = None) -> str:
    """Return a Markdown answer grounded in ``report`` (blocking; run in a worker thread)."""
    if llm is None:
        if not settings.groq_api_key:
            raise ValueError("Set GROQ_API_KEY before asking follow-up questions.")
        llm = ChatGroq(
            model=settings.groq_model,
            api_key=settings.groq_api_key.get_secret_value(),
            max_tokens=settings.groq_summary_max_tokens,
            **(
                {"reasoning_effort": "low", "reasoning_format": "hidden"}
                if settings.groq_model in {"openai/gpt-oss-20b", "openai/gpt-oss-120b"}
                else {}
            ),
        )
    message = invoke_with_retry(llm, build_messages(report, question))
    content = getattr(message, "content", message)
    text = content if isinstance(content, str) else "".join(
        part.get("text", "") if isinstance(part, dict) else str(part) for part in content
    )
    return text.strip()[:MAX_ANSWER_CHARS]
