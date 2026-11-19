"""Concrete planner, researcher, reviewer, and writer node implementations."""

from __future__ import annotations

import json
import re
from collections.abc import Callable
from functools import lru_cache
from typing import Any, TypedDict

from langchain_core.messages import HumanMessage, SystemMessage
from langchain_groq import ChatGroq
from tavily import TavilyClient

from research_assistant.config import Settings
from research_assistant.claims import check_claims
from research_assistant.sources import filter_and_rank
from research_assistant.llm_retry import invoke_with_retry
from research_assistant.schemas import (
    ClaimCheck,
    FinalReport,
    ResearchFinding,
    ResearchPlan,
    ResearchSummary,
    ReviewDecision,
    Source,
)
from research_assistant.state import ResearcherWorkerState, ResearchState

SearchFn = Callable[[str], list[dict[str, Any]]]
NodeFn = Callable[[Any], dict[str, Any]]


class NodeImplementations(TypedDict):
    lead_planner_node: NodeFn
    researcher_node: NodeFn
    reviewer_node: NodeFn
    writer_node: NodeFn


EXCERPT_FOR_WRITER = 350
_CITATION = re.compile(r"\[(\d{1,3})\]")


def sanitize_citations(markdown: str, source_count: int) -> str:
    """Drop citation markers that do not point at a real numbered source."""

    def keep(match: re.Match[str]) -> str:
        number = int(match.group(1))
        return match.group(0) if 1 <= number <= source_count else ""

    return _CITATION.sub(keep, markdown)


def _citation_context(findings: list[ResearchFinding]) -> tuple[str, str]:
    """Build evidence with stable citation numbers and a deterministic bibliography."""
    unique_sources: dict[str, Source] = {}
    citation_by_url: dict[str, int] = {}
    finding_rows: list[dict[str, Any]] = []
    for finding in findings:
        citation_ids: list[int] = []
        for source in finding.sources:
            url = str(source.url)
            if url not in unique_sources:
                unique_sources[url] = source
                citation_by_url[url] = len(unique_sources)
            citation_ids.append(citation_by_url[url])
        finding_rows.append(
            {"query": finding.query, "summary": finding.summary, "cite_with": sorted(set(citation_ids))}
        )

    source_rows = [
        {
            "citation": index,
            "title": source.title,
            "url": url,
            "excerpt": (source.content or source.snippet or "")[:EXCERPT_FOR_WRITER],
        }
        for index, (url, source) in enumerate(unique_sources.items(), start=1)
    ]
    context = json.dumps(
        {"findings": finding_rows, "sources": source_rows},
        ensure_ascii=False,
        separators=(",", ":"),
    )
    bibliography = "\n".join(
        f"[{index}] {source.title} — {url}"
        for index, (url, source) in enumerate(unique_sources.items(), start=1)
    )
    return context, bibliography


def _with_structured_output(model: Any, schema: Any) -> Any:
    """Use constrained JSON output on Groq models that guarantee schema adherence."""
    try:
        model_name = getattr(model, "model_name", getattr(model, "model", ""))
        strict = model_name in {"openai/gpt-oss-20b", "openai/gpt-oss-120b"}
        return model.with_structured_output(schema, method="json_schema", strict=strict)
    except Exception:
        return model.with_structured_output(schema)


_invoke_with_rate_limit_retry = invoke_with_retry  # retries 429s and malformed JSON; see llm_retry.py


STYLE_GUIDE = {
    "brief": (
        "Use this structure: a short 'Key takeaways' bullet list, then one '##' section per major theme with "
        "specific facts, figures, examples and comparisons, then '## Open questions and limitations'."
    ),
    "executive": (
        "Write an executive summary: '## Bottom line' (2-3 sentences), '## Key points' (5-7 bullets, each with "
        "a figure or fact), '## Risks and uncertainties', and '## Recommended next steps'. Be decisive and brief."
    ),
    "pros_cons": (
        "Structure it as: '## Overview', '## Pros' (each point with evidence), '## Cons' (each point with "
        "evidence), '## Verdict' (when it is a good idea and when it is not)."
    ),
    "comparison": (
        "Structure it as a comparison: '## At a glance' with a Markdown table (one row per option or criterion, "
        "columns for each option, concise cells), then one '##' section per important criterion explaining the "
        "difference with evidence, then '## Which to choose when'. Treat every option evenly."
    ),
}
LENGTH_GUIDE = {"short": "roughly 2500-3500", "standard": "roughly 5000-8000", "long": "roughly 8000-10000"}
AUDIENCE_GUIDE = {
    "general": "Write for an intelligent general reader; explain jargon briefly.",
    "executive": "Write for a busy executive: lead with implications and decisions, minimise jargon.",
    "technical": "Write for a technical reader: be precise, keep technical terms and quantitative detail.",
    "student": "Write for a student: explain concepts step by step with simple examples.",
}


def writer_instructions(options: dict[str, Any]) -> str:
    """System prompt for the writer, shaped by the run's style, audience, length and language."""
    style = options.get("style", "brief")
    language = options.get("language") or "English"
    text = (
        f"Write a well-organized Markdown research brief ({LENGTH_GUIDE.get(options.get('length', 'standard'), LENGTH_GUIDE['standard'])} "
        "characters) answering the topic using only the supplied evidence. "
        f"{STYLE_GUIDE.get(style, STYLE_GUIDE['brief'])} "
        f"{AUDIENCE_GUIDE.get(options.get('audience', 'general'), AUDIENCE_GUIDE['general'])} "
        "Cite every factual claim with the provided numbers like [1][2], and only cite a source whose "
        "excerpt or the finding summary actually supports the claim. Do not invent facts; "
        "state uncertainty; omit the Sources section."
    )
    if language.lower() != "english":
        text += f" Write the whole brief, including headings, in {language}; keep citation numbers as digits."
    return text


def planner_hint(options: dict[str, Any]) -> str:
    hints = []
    if options.get("style") == "comparison":
        hints.append(
            "The topic compares options: include questions that cover each option on its own and head-to-head "
            "on the criteria that matter, giving every option equal attention."
        )
    if options.get("time_range"):
        hints.append(f"Focus on developments from the last {options['time_range']}.")
    return " ".join(hints)


def create_node_implementations(
    *,
    settings: Settings | None = None,
    llm: Any | None = None,
    fast_llm: Any | None = None,
    search: SearchFn | None = None,
) -> NodeImplementations:
    """Create injectable nodes; defaults use Groq and Tavily from environment settings."""
    settings = settings or Settings()
    using_default_model = llm is None
    if llm is None:
        if not settings.groq_api_key:
            raise ValueError("Set GROQ_API_KEY before creating the default language model.")
        reasoning_options = (
            {"reasoning_effort": "low", "reasoning_format": "hidden"}
            if settings.groq_model in {"openai/gpt-oss-20b", "openai/gpt-oss-120b"}
            else {}
        )
        llm = ChatGroq(
            model=settings.groq_model,
            api_key=settings.groq_api_key.get_secret_value(),
            max_tokens=settings.groq_max_output_tokens,
            **reasoning_options,
        )
    if fast_llm is None and using_default_model:
        fast_llm = ChatGroq(
            model=settings.groq_model,
            api_key=settings.groq_api_key.get_secret_value(),
            max_tokens=settings.groq_summary_max_tokens,
            **reasoning_options,
        )
    fast_llm = fast_llm or llm

    if search is None:
        if not settings.tavily_api_key:
            raise ValueError("Set TAVILY_API_KEY before creating the default search client.")
        tavily = TavilyClient(api_key=settings.tavily_api_key.get_secret_value())

        def search(query: str, time_range: str | None = None) -> list[dict[str, Any]]:
            options: dict[str, Any] = {}
            if time_range or settings.search_time_range:
                options["time_range"] = time_range or settings.search_time_range
            if settings.blocked_domains:
                options["exclude_domains"] = settings.blocked_domains[:150]
            result = tavily.search(
                query=query,
                search_depth="basic",
                # Ask for extra results so filtering and ranking still leave a full set.
                max_results=min(settings.search_results_per_query * 2, 12),
                include_raw_content=False,
                **options,
            )
            return filter_and_rank(
                result.get("results", []),
                limit=settings.search_results_per_query,
                blocked=settings.blocked_domains,
                preferred=settings.preferred_domains,
            )

    def lead_planner_node(state: ResearchState) -> dict[str, Any]:
        """Break the topic into a small set of focused, non-overlapping questions."""
        if state.get("gaps"):
            questions = state["gaps"]
        else:
            planner = _with_structured_output(llm, ResearchPlan)
            plan = _invoke_with_rate_limit_retry(
                planner,
                [
                    SystemMessage(
                        content=(
                            "You are a lead research strategist. Break the user's topic into "
                            f"{settings.initial_questions} distinct, focused questions that cover "
                            "key facts, relevant context, and multiple credible perspectives. "
                            "Respond in valid JSON matching the schema. "
                            "Each question must be independently researchable and include a rationale. "
                            + planner_hint(state.get("options") or {})
                        )
                    ),
                    HumanMessage(
                        content=f"Research topic: {state['topic']}"
                        + (
                            "\n\nBackground from the user (use it to focus the questions; it is not evidence): "
                            + str((state.get("options") or {}).get("notes", ""))[:1500]
                            if (state.get("options") or {}).get("notes")
                            else ""
                        )
                    ),
                ]
            )
            questions = plan.questions[: settings.initial_questions]
        return {
            "plan": [question.model_dump(mode="json") for question in questions],
            "gaps": [],
        }

    def researcher_node(worker: ResearcherWorkerState) -> dict[str, Any]:
        """Search Tavily for one query and synthesize its source-backed evidence."""
        query = worker["query"]
        time_range = worker.get("time_range")
        results = search(query, time_range=time_range) if time_range else search(query)
        sources: list[Source] = []
        for result in results:
            url = result.get("url")
            if not url:
                continue
            try:
                sources.append(
                    Source(
                        title=str(result.get("title") or "Untitled"),
                        url=url,
                        snippet=str(result.get("content") or result.get("snippet") or "")[:240],
                        content=str(result.get("content") or result.get("snippet") or "")[: settings.source_excerpt_chars],
                    )
                )
            except ValueError:
                # Ignore malformed URLs; a citation must be usable by the writer.
                continue

        context = "\n\n".join(
            f"{source.title} ({source.url}): {source.content or source.snippet}"
            for source in sources
        ) or "No usable search results were returned."
        summarizer = _with_structured_output(fast_llm, ResearchSummary)
        summary = _invoke_with_rate_limit_retry(
            summarizer,
            [
                SystemMessage(
                    content=(
                            "Answer the query using only the supplied sources. Write a substantive evidence summary of "
                            "2-3 short paragraphs (roughly 1000-1600 characters): include concrete facts, figures, "
                            "named places/organizations and dates the sources give, note where sources disagree, "
                            "and flag uncertainty. Write plain text only: no markdown, asterisks, double quotes or "
                            "backslashes. Return the required JSON; do not invent facts or citations."
                    )
                ),
                HumanMessage(content=f"Query: {query}\n\nSearch results:\n{context}"),
            ],
            ResearchSummary,
        )
        summary_text = summary.summary

        finding = ResearchFinding(query=query, summary=summary_text, sources=sources)
        return {"findings": [finding]}

    def reviewer_node(state: ResearchState) -> dict[str, Any]:
        """Judge evidence quality and request focused follow-up questions where needed."""
        findings_text = "\n\n".join(
            f"{finding.query}: {finding.summary[:900]} "
            f"Sources: {[str(source.url) for source in finding.sources[:4]]}"
            for finding in state.get("findings", [])
        )
        reviewer = _with_structured_output(llm, ReviewDecision)
        decision = _invoke_with_rate_limit_retry(
            reviewer,
            [
                SystemMessage(
                    content=(
                        "Check whether evidence answers the topic, citations support claims, or sources conflict. "
                        "Return the required JSON. Request up to 2 focused follow-up questions only for important gaps."
                    ),
                ),
                HumanMessage(
                    content=(
                        f"Main topic: {state['topic']}\n"
                        f"Iteration {state.get('iteration', 0) + 1}/"
                        f"{state.get('max_iterations', settings.max_iterations)}. Evidence:\n{findings_text}"
                    ),
                ),
            ]
        )

        return {
            "review_notes": decision.rationale,
            "gaps": [gap.model_dump(mode="json") for gap in decision.gaps] if not decision.sufficient else [],
            "sufficient": decision.sufficient,
            "iteration": state.get("iteration", 0) + 1,
        }

    def writer_node(state: ResearchState) -> dict[str, Any]:
        """Synthesize findings into a report and append a deterministic source list."""
        evidence, bibliography = _citation_context(state.get("findings", []))
        options = state.get("options") or {}
        writer = _with_structured_output(llm, FinalReport)
        notes = str(options.get("notes") or "")[:3000]
        report = _invoke_with_rate_limit_retry(
            writer,
            [
                SystemMessage(content=writer_instructions(options)),
                HumanMessage(
                    content=(
                        f"Main topic: {state['topic']}\n\n"
                        f"Reviewer notes: {state.get('review_notes', 'None')}\n\n"
                        + (f"User background notes (context only, not citable evidence): {notes}\n\n" if notes else "")
                        + f"Verified findings and numbered sources:\n{evidence}"
                    )
                ),
            ]
        )
        title = report.title
        source_count = len(bibliography.splitlines()) if bibliography else 0
        markdown = sanitize_citations(report.markdown, source_count)
        verify = options.get("verify_claims")
        if (settings.claim_check if verify is None else verify) and state.get("findings"):
            markdown, _ = check_claims(markdown, state["findings"], _with_structured_output(fast_llm, ClaimCheck))

        sources = bibliography or "No web sources were returned."
        return {"report": f"# {title}\n\n{markdown}\n\n## Sources\n\n{sources}"}

    return {
        "lead_planner_node": lead_planner_node,
        "researcher_node": researcher_node,
        "reviewer_node": reviewer_node,
        "writer_node": writer_node,
    }


@lru_cache(maxsize=1)
def _default_node_implementations() -> NodeImplementations:
    """Lazily create environment-configured functions for direct graph injection."""
    return create_node_implementations()


def lead_planner_node(state: ResearchState) -> dict[str, Any]:
    return _default_node_implementations()["lead_planner_node"](state)


def researcher_node(state: ResearcherWorkerState) -> dict[str, Any]:
    return _default_node_implementations()["researcher_node"](state)


def reviewer_node(state: ResearchState) -> dict[str, Any]:
    return _default_node_implementations()["reviewer_node"](state)


def writer_node(state: ResearchState) -> dict[str, Any]:
    return _default_node_implementations()["writer_node"](state)
