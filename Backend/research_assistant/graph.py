"""LangGraph wiring for parallel, iterative research and human plan approval."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from langgraph.checkpoint.memory import MemorySaver
from langgraph.graph import END, START, StateGraph
from langgraph.types import Send, interrupt

from research_assistant.config import Settings
from research_assistant.schemas import ResearchPlan, ResearchQuestion
from research_assistant.state import ResearchState

NodeFn = Callable[[Any], dict[str, Any]]


def build_research_graph(
    *,
    settings: Settings | None = None,
    llm: Any | None = None,
    fast_llm: Any | None = None,
    search: Callable[[str], list[dict[str, Any]]] | None = None,
    checkpointer: Any | None = None,
    human_review_plan: bool = True,
    lead_node: NodeFn | None = None,
    researcher_node: NodeFn | None = None,
    reviewer_node: NodeFn | None = None,
    writer_node: NodeFn | None = None,
):
    """Build the graph, with optional custom node functions and injectable services.

    When node functions are omitted, concrete defaults are created from `settings`,
    `llm`, `fast_llm`, and `search`. `lead_node` etc. let applications wire their own
    implementations while retaining this graph's fan-out, review loop, and HITL gate.
    """
    settings = settings or Settings()
    nodes: dict[str, NodeFn] = {}
    if any(node is None for node in (lead_node, researcher_node, reviewer_node, writer_node)):
        from research_assistant.nodes import create_node_implementations

        nodes = create_node_implementations(settings=settings, llm=llm, fast_llm=fast_llm, search=search)

    lead_node = lead_node or nodes["lead_planner_node"]
    researcher_node = researcher_node or nodes["researcher_node"]
    reviewer_node = reviewer_node or nodes["reviewer_node"]
    writer_node = writer_node or nodes["writer_node"]

    def human_plan_review(state: ResearchState) -> dict[str, Any]:
        """Pause with the proposed plan and validate the user's approval or edits."""
        if not human_review_plan:
            return {}

        proposed = [ResearchQuestion.model_validate(item).model_dump(mode="json") for item in state["plan"]]
        response = interrupt(
            {
                "kind": "review_research_plan",
                "instructions": (
                    "Review the planner's questions. Resume with {'questions': [...]} to approve or edit them. "
                    "Each item must include 'question' and 'rationale'."
                ),
                "questions": proposed,
            }
        )
        if isinstance(response, list):
            edited_questions = response
        elif isinstance(response, dict):
            edited_questions = response.get("questions", proposed)
        else:
            raise ValueError("Plan review response must be a list or an object containing 'questions'.")

        validated = ResearchPlan.model_validate({"questions": edited_questions})
        return {"plan": [question.model_dump(mode="json") for question in validated.questions]}

    def route_after_plan(state: ResearchState) -> list[Send]:
        return [
            Send("researcher", {"query": item["question"]})
            for item in state["plan"]
        ]

    def route_after_review(state: ResearchState) -> str | list[Send]:
        if state.get("sufficient") or state.get("iteration", 0) >= state.get(
            "max_iterations", settings.max_iterations
        ):
            return "writer"
        gaps = state.get("gaps", [])
        if not gaps:
            return "writer"
        return [Send("researcher", {"query": item["question"]}) for item in gaps]

    builder = StateGraph(ResearchState)
    builder.add_node("lead_planner", lead_node)
    builder.add_node("human_plan_review", human_plan_review)
    builder.add_node("researcher", researcher_node)
    builder.add_node("reviewer", reviewer_node)
    builder.add_node("writer", writer_node)
    builder.add_edge(START, "lead_planner")
    builder.add_edge("lead_planner", "human_plan_review")
    builder.add_conditional_edges("human_plan_review", route_after_plan, ["researcher"])
    builder.add_edge("researcher", "reviewer")
    builder.add_conditional_edges("reviewer", route_after_review, ["researcher", "writer"])
    builder.add_edge("writer", END)
    return builder.compile(checkpointer=checkpointer if checkpointer is not None else MemorySaver())
