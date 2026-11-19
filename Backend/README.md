# Multi Agent Research Assistant

A Python research workflow built with LangGraph. A planner creates focused questions, parallel researcher workers search the web and summarize evidence, a reviewer requests targeted follow-up research, and a writer produces a cited Markdown report.

## Setup

Requires Python 3.11+. Run these commands from the repository root to install the backend and configure Groq and Tavily credentials:

```powershell
Set-Location Backend
py -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -e .
Copy-Item .env.example .env
```

Set `GROQ_API_KEY` and `TAVILY_API_KEY` in `.env`. `GROQ_MODEL` defaults to `openai/gpt-oss-120b`. Settings also include the number of initial questions, search results per query, and maximum review iterations. Create an API key in the [Groq console](https://console.groq.com/keys).

## Evaluations

The versioned cases in `evals/dataset.jsonl` exercise several research topics. The live evaluation command runs the full research graph without plan approval, scores report coverage, grounding, citation quality, and clarity with a Groq structured-output judge, and separately checks that inline citation numbers resolve to the generated source list.

```powershell
research-assistant-eval
research-assistant-eval --limit 1 --output evals\latest-results.json
```

Evaluations make live Groq and Tavily requests and may consume account quota. `GROQ_EVAL_MODEL` optionally selects a separate judge model; it defaults to `GROQ_MODEL`. The JSON result contains per-case scores and rationales, not the full report text. Judge scores are useful for tracking regressions across versions, but they are not a substitute for human review.

## Run

```powershell
research-assistant "How are heat pumps changing residential energy use?"
```

## FastAPI and SSE

Install the backend dependencies, then start the API from the `Backend` directory:

```powershell
python -m uvicorn research_assistant.api:app --host 127.0.0.1 --port 8000 --reload
```

Run it from `Backend/` so Python can resolve the package. This invokes Uvicorn through the active Python environment and targets the exported `app` object. For a non-reloading server, remove `--reload`.

The API reads `GROQ_API_KEY`, `GROQ_MODEL`, `TAVILY_API_KEY`, and `RESEARCH_API_TOKEN` from the environment or `.env`. Create a strong random `RESEARCH_API_TOKEN` (at least 32 random bytes), keep it server-side, and give authorized users the token through a secure channel. Research endpoints require `Authorization: Bearer <token>`; `/healthz` is public and does not expose provider configuration. `CORS_ORIGINS` is a JSON array of exact trusted browser origins. Do not use wildcard origins in production. Start a run with an HTTP client that can read a streaming POST response:

```http
POST /research/runs
Authorization: Bearer <token>
Content-Type: application/json

{"topic":"Current state of solid-state battery commercialization in EVs"}
```

The SSE stream sends `started` with the generated `thread_id`, `progress` events as graph nodes finish, and then either `plan_review_required`, `completed`, or `error`. The plan-review event contains the proposed questions. Model calls use the configured Groq API key and model. Provider errors happen after streaming begins, so they arrive as SSE `error` events even though the HTTP response itself has already started. Resume that thread with approved or edited questions:

```http
POST /research/runs/{thread_id}/resume
Authorization: Bearer <token>
Content-Type: application/json

{"questions":[{"question":"What EV solid-state battery pilot lines are operating?","rationale":"Identify commercial readiness."}]}
```

Send `{}` to approve the proposed plan unchanged. Resume streams further `progress` events and the final `completed` report. `GET /healthz` reports whether the graph has been initialized. The default checkpointer is in-memory, so keep one API process running for the lifetime of a thread; use the documented Postgres checkpointer for durable or multi-worker deployments.

### Visual research context

After a report is complete, the frontend can request related images, video explainers, and Sketchfab 3D models from `POST /research/runs/{thread_id}/visuals` with `{"types":["images","videos","models"]}` and the same bearer token. The search runs only when requested and uses Tavily for all three types. Images use Tavily image search; video and model links are restricted to YouTube and Sketchfab. YouTube and Sketchfab previews load only after the user chooses Preview. These visuals are for orientation, not evidence: use the report citations for factual claims and check the publisher's reuse terms before redistributing media.

### Windows Application Control

The current LangGraph checkpoint serializer depends on the native `ormsgpack` extension. If Windows Application Control blocks `Backend/.venv/Lib/site-packages/ormsgpack/ormsgpack.cp314-win_amd64.pyd`, the API worker cannot import LangGraph. `python -m uvicorn` avoids a blocked `uvicorn.exe` launcher, but it cannot override a policy that blocks this extension. Ask the device administrator to allow the Python extension or use an approved runtime where it is allowed.

The local CLI uses a LangGraph `MemorySaver` checkpoint and a generated thread ID. Pass `--thread-id` to choose the ID. The in-memory saver persists only for the lifetime of the process. The production API uses `AsyncPostgresSaver` for durable resumable runs; see the root production deployment guide.

By default, the CLI pauses after planning and asks you to review the question JSON. Press Enter to approve it, or paste an edited JSON array. To skip this step:

```powershell
research-assistant "Your research question" --no-plan-review
```

For a long-running application, compile with a durable checkpointer and expose a resume operation using the same `thread_id`. The graph builder accepts any LangGraph-compatible `checkpointer`; install the `postgres` extra to use `PostgresSaver` in a deployment. Keep the saver open for the lifetime of the graph and call `setup()` once when provisioning its tables:

```python
from langgraph.checkpoint.postgres import PostgresSaver
from research_assistant import build_research_graph

with PostgresSaver.from_conn_string("postgresql://user:password@host/database") as saver:
    saver.setup()
    graph = build_research_graph(checkpointer=saver)
    # Invoke and resume graph runs here while saver remains open.
```

Plan review uses LangGraph's in-node `interrupt()` so the proposed questions are surfaced as a resumable interrupt. The review node validates the user's replacement questions with Pydantic before any researcher runs. Resume a paused run with `graph.invoke(Command(resume={"questions": edited_questions}), config)` using the same `thread_id` and checkpointer.

## Human review from Python

```python
from langgraph.types import Command
from research_assistant import build_research_graph

graph = build_research_graph()  # Plan approval is enabled by default.
config = {"configurable": {"thread_id": "research-001"}}
paused = graph.invoke(
    {"topic": "Your question", "findings": [], "iteration": 0, "max_iterations": 2},
    config,
)

# The interrupt value contains the proposed questions for a UI to display.
approval = paused["__interrupt__"][0].value
edited_questions = approval["questions"]  # Replace/edit before resuming.
result = graph.invoke(Command(resume={"questions": edited_questions}), config)
print(result["report"])
```

## Python API

```python
from langgraph.types import Command
from research_assistant import build_research_graph

graph = build_research_graph()
config = {"configurable": {"thread_id": "research-001"}}
paused = graph.invoke(
    {"topic": "Your question", "findings": [], "iteration": 0, "max_iterations": 2},
    config,
)
proposed = paused["__interrupt__"][0].value["questions"]
result = graph.invoke(Command(resume={"questions": proposed}), config)
print(result["report"])
```

The planner, reviewer, and writer use Pydantic structured outputs. Researchers receive only their assigned `query`, search through Tavily for raw page content, and return typed findings. Findings are merged through the `operator.add` reducer. The writer receives a deduplicated, numbered source catalog so its inline citations map to concrete URLs. The reviewer increments `iteration` before routing, so the graph stops requesting new research at `max_iterations`. The model, search callable, settings, and checkpointer can be injected into `build_research_graph` for alternate providers and isolated development.

## Supplying custom node functions

The built-in node implementations are in `research_assistant/nodes.py`. To replace any node while keeping the graph's fan-out, review loop, and plan-review interrupt, pass callables to `build_research_graph`:

```python
from research_assistant import build_research_graph
from research_assistant import lead_planner_node, researcher_node, reviewer_node, writer_node

graph = build_research_graph(
    lead_node=lead_planner_node,
    researcher_node=researcher_node,
    reviewer_node=reviewer_node,
    writer_node=writer_node,
)
```

These named functions lazily create the environment-configured Groq and Tavily clients. For custom providers or isolated runs, call `create_node_implementations(settings=..., llm=..., fast_llm=..., search=...)` and inject the returned functions. Custom nodes use the same state keys as the built-in nodes: `plan`, `findings`, `gaps`, `sufficient`, `review_notes`, `iteration`, and `report`. Each researcher receives only `{"query": ...}`.
