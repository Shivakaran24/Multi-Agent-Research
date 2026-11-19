"""LangGraph state definitions, including isolated worker input."""

import operator
from typing import Annotated, TypedDict

from research_assistant.schemas import ResearchFinding


class ResearchState(TypedDict, total=False):
    topic: str
    # Keep checkpointed state composed of JSON-safe values instead of custom model classes.
    plan: list[dict[str, str]]
    findings: Annotated[list[ResearchFinding], operator.add]
    iteration: int
    max_iterations: int
    gaps: list[dict[str, str]]
    sufficient: bool
    review_notes: str
    report: str


class ResearcherWorkerState(TypedDict):
    """Only the assigned query is sent to each parallel researcher."""

    query: str
