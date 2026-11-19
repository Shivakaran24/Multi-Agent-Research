"""Command line entry point."""

import argparse
import json
import uuid

from langgraph.types import Command

from research_assistant.config import Settings
from research_assistant.graph import build_research_graph


def main() -> None:
    parser = argparse.ArgumentParser(description="Research a topic with parallel LangGraph agents.")
    parser.add_argument("topic", help="Research question or topic")
    parser.add_argument("--thread-id", default=None, help="Checkpoint thread ID (generated if omitted)")
    parser.add_argument(
        "--no-plan-review",
        action="store_true",
        help="Skip the interactive plan approval step",
    )
    args = parser.parse_args()

    settings = Settings()
    graph = build_research_graph(settings=settings, human_review_plan=not args.no_plan_review)
    thread_id = args.thread_id or str(uuid.uuid4())
    result = graph.invoke(
        {"topic": args.topic, "findings": [], "iteration": 0, "max_iterations": settings.max_iterations},
        {"configurable": {"thread_id": thread_id}},
    )
    print(f"Thread: {thread_id}")
    pending = result.get("__interrupt__", ())
    if pending:
        review = pending[0].value
        print("\nPlanner questions (edit the JSON, or press Enter to approve):")
        proposed = review["questions"]
        print(json.dumps(proposed, ensure_ascii=False, indent=2))
        edited = input("\nEdited questions JSON: ").strip()
        approved_questions = json.loads(edited) if edited else proposed
        result = graph.invoke(
            Command(resume={"questions": approved_questions}),
            {"configurable": {"thread_id": thread_id}},
        )
    print(result["report"])


if __name__ == "__main__":
    main()
