"""Shared test helpers: a scripted fake research graph (LangGraph's interface) and HTTP utilities.

No LLM or search API is used anywhere in the test suite.
"""

import json
import os
from types import SimpleNamespace

from fastapi.testclient import TestClient

from research_assistant.api import create_app
from research_assistant.config import Settings

SECRET = "s" * 40
LEGACY = "L" * 40
QUESTIONS = [
    {"question": "What drives grid storage demand?", "rationale": "Sets the baseline."},
    {"question": "Which technologies are scaling fastest?", "rationale": "Shows momentum."},
]
INTERRUPT = {"kind": "review_research_plan", "questions": QUESTIONS}


DEFAULT_PDF_TEXT = "Grid batteries cut peak costs across the Spanish market in 2024 according to the operator. " * 3


def make_pdf(text: str = DEFAULT_PDF_TEXT) -> bytes:
    """Create a minimal one-page PDF with a text layer for tests."""
    safe = text.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")
    stream = f"BT /F1 10 Tf 20 700 Td ({safe}) Tj ET".encode()
    objects = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 2000 800] /Contents 4 0 R /Resources << /Font << /F1 5 0 R >> >> >>",
        b"<< /Length %d >>\nstream\n" % len(stream) + stream + b"\nendstream",
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
    ]
    out = bytearray(b"%PDF-1.4\n")
    offsets = []
    for number, body in enumerate(objects, start=1):
        offsets.append(len(out))
        out += b"%d 0 obj\n" % number + body + b"\nendobj\n"
    xref = len(out)
    out += b"xref\n0 %d\n0000000000 65535 f \n" % (len(objects) + 1)
    for offset in offsets:
        out += b"%010d 00000 n \n" % offset
    out += b"trailer\n<< /Size %d /Root 1 0 R >>\nstartxref\n%d\n%%%%EOF" % (len(objects) + 1, xref)
    return bytes(out)


class FakeCheckpointer:
    def __init__(self):
        self.deleted: list[str] = []

    async def adelete_thread(self, thread_id):
        self.deleted.append(thread_id)


class RecordingMailer:
    def __init__(self):
        self.sent: list[tuple[str, str, str]] = []

    def send(self, to, subject, body):
        self.sent.append((to, subject, body))

    def link(self):
        """The reset token URL from the most recent email."""
        return next(line for line in self.sent[-1][2].splitlines() if line.startswith("http"))


class FakeGraph:
    def __init__(self):
        self.threads: dict[str, dict] = {}
        self.checkpointer = FakeCheckpointer()

    async def astream(self, graph_input, config, stream_mode="updates"):
        thread_id = config["configurable"]["thread_id"]
        if isinstance(graph_input, dict):
            self.threads[thread_id] = {"topic": graph_input["topic"], "pending": True}
            yield {"lead_planner": {"plan": QUESTIONS}}
            yield {"__interrupt__": (INTERRUPT,)}
            return
        state = self.threads[thread_id]
        state["pending"] = False
        state["resume"] = graph_input.resume
        yield {"researcher": {"findings": [{
            "query": "q", "summary": "evidence",
            "sources": [{"title": "T", "url": "https://example.com", "snippet": "s", "content": "SECRET FULL PAGE TEXT"}],
        }]}}
        yield {"reviewer": {"sufficient": True}}
        state["report"] = "# Brief\n\nFindings [1]."
        yield {"writer": {"report": state["report"]}}

    async def aget_state(self, config):
        state = self.threads.get(config["configurable"]["thread_id"])
        if state is None:
            return SimpleNamespace(values={}, tasks=())
        values = {"topic": state["topic"], "plan": QUESTIONS}
        if "report" in state:
            values["report"] = state["report"]
        tasks = (SimpleNamespace(interrupts=(INTERRUPT,)),) if state["pending"] else ()
        return SimpleNamespace(values=values, tasks=tasks)


POSTGRES_URL = os.environ.get("TEST_DATABASE_URL")  # run the whole suite against PostgreSQL when set


def reset_postgres():
    import psycopg

    with psycopg.connect(POSTGRES_URL) as conn:
        conn.execute("DROP TABLE IF EXISTS password_resets, research_runs, users CASCADE")


def make_client(tmp_path, graph=None, fresh_database=True, **overrides):
    if POSTGRES_URL:
        if fresh_database:
            reset_postgres()
        overrides.setdefault("database_url", POSTGRES_URL)
    overrides.setdefault("scheduler_enabled", False)  # tests trigger schedules explicitly
    settings = Settings(
        _env_file=None,
        auth_secret_key=SECRET,
        research_api_token=LEGACY,
        auth_db_path=tmp_path / "users.db",
        allowed_hosts=["testserver"],
        **overrides,
    )
    graph = graph or FakeGraph()
    app = create_app(graph=graph, settings=settings)
    app.state.mailer = RecordingMailer()
    return TestClient(app), graph


def register(client, email="ada@example.com", name="Ada Lovelace", password="analytical1"):
    return client.post("/auth/register", json={"full_name": name, "email": email, "password": password})


def auth(token):
    return {"Authorization": f"Bearer {token}"}


def sse(response):
    events = []
    for frame in response.text.split("\n\n"):
        lines = [line for line in frame.split("\n") if line and not line.startswith(":")]
        if len(lines) >= 2 and lines[0].startswith("event:"):
            events.append((lines[0][6:].strip(), json.loads(lines[1][5:].strip())))
    return events


def start_run(client, token, topic="How is grid storage evolving?"):
    response = client.post("/research/runs", json={"topic": topic}, headers=auth(token))
    assert response.status_code == 200
    events = sse(response)
    kinds = [kind for kind, _ in events]
    assert kinds[0] == "started" and kinds[-1] == "plan_review_required"
    return events[-1][1]["thread_id"], events
