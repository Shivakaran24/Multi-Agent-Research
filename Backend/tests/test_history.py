"""Research history, run lifecycle persistence, and per-user usage limits."""

import pytest

from helpers import FakeGraph, QUESTIONS, auth, make_client, register, sse, start_run


@pytest.fixture()
def env(tmp_path):
    client, graph = make_client(tmp_path)
    with client:
        yield client, graph


def token_for(client, email="ada@example.com", name="Ada Lovelace"):
    return register(client, email=email, name=name).json()["access_token"]


def runs(client, token):
    response = client.get("/research/runs", headers=auth(token))
    assert response.status_code == 200
    return response.json()


# ------------------------------------------------------------------ lifecycle persistence


def test_run_lifecycle_is_saved_as_it_progresses(env):
    client, _ = env
    token = token_for(client)
    assert runs(client, token)["runs"] == []

    thread_id, _ = start_run(client, token, topic="How is grid storage evolving?")
    listed = runs(client, token)["runs"]
    assert [(r["thread_id"], r["topic"], r["status"], r["title"]) for r in listed] == [
        (thread_id, "How is grid storage evolving?", "review", None)
    ]
    detail = client.get(f"/research/runs/{thread_id}", headers=auth(token)).json()
    assert detail["status"] == "review" and detail["report"] is None
    assert [q["question"] for q in detail["plan"]] == [q["question"] for q in QUESTIONS]

    client.post(f"/research/runs/{thread_id}/resume", json={}, headers=auth(token))
    detail = client.get(f"/research/runs/{thread_id}", headers=auth(token)).json()
    assert detail["status"] == "complete"
    assert detail["title"] == "Brief"
    assert detail["report"].startswith("# Brief")
    summary = runs(client, token)["runs"][0]
    assert summary["status"] == "complete" and "report" not in summary  # list stays lightweight


def test_history_is_newest_first_and_private_to_each_user(env):
    client, _ = env
    alice, bob = token_for(client), token_for(client, "bob@example.com", "Bob Builder")
    first, _ = start_run(client, alice, topic="first topic here")
    second, _ = start_run(client, alice, topic="second topic here")
    start_run(client, bob, topic="bobs private topic")
    assert [r["thread_id"] for r in runs(client, alice)["runs"]] == [second, first]
    assert [r["topic"] for r in runs(client, bob)["runs"]] == ["bobs private topic"]
    assert client.get(f"/research/runs/{first}", headers=auth(bob)).status_code == 404


def test_history_requires_login(env):
    client, _ = env
    nil = "00000000-0000-0000-0000-000000000000"
    assert client.get("/research/runs").status_code == 401
    assert client.get(f"/research/runs/{nil}").status_code == 401
    assert client.delete(f"/research/runs/{nil}").status_code == 401


def test_stream_failure_marks_the_run_failed(tmp_path):
    class Boom(FakeGraph):
        async def astream(self, graph_input, config, stream_mode="updates"):
            raise RuntimeError("provider exploded")
            yield  # pragma: no cover

    client, _ = make_client(tmp_path, graph=Boom())
    with client:
        token = token_for(client)
        response = client.post("/research/runs", json={"topic": "anything at all"}, headers=auth(token))
        assert [kind for kind, _ in sse(response)][-1] == "error"
        assert runs(client, token)["runs"][0]["status"] == "failed"


def test_runs_that_stopped_mid_flight_show_as_interrupted(env):
    client, _ = env
    token = token_for(client)
    user_id = client.get("/auth/me", headers=auth(token)).json()["id"]
    store = client.app.state.user_store
    store.record_run("11111111-1111-4111-8111-111111111111", user_id, "was researching")
    store.update_run("11111111-1111-4111-8111-111111111111", status="researching")
    assert runs(client, token)["runs"][0]["status"] == "interrupted"


def test_plan_review_is_interrupted_when_the_checkpoint_is_gone(tmp_path):
    first_client, _ = make_client(tmp_path)
    with first_client:
        token = token_for(first_client)
        thread_id, _ = start_run(first_client, token)
    # Same database, brand-new graph: simulates an in-memory checkpoint lost on restart.
    second_client, _ = make_client(tmp_path, fresh_database=False)
    with second_client:
        detail = second_client.get(f"/research/runs/{thread_id}", headers=auth(token)).json()
        assert detail["status"] == "interrupted" and detail["plan"]  # plan kept so it can be re-run


def test_active_run_tracking_is_cleaned_up(env):
    client, _ = env
    token = token_for(client)
    thread_id, _ = start_run(client, token)
    client.post(f"/research/runs/{thread_id}/resume", json={}, headers=auth(token))
    assert client.app.state.active_threads == set()
    assert client.app.state.user_active == {}


# ------------------------------------------------------------------ deleting


def test_delete_run_removes_it_and_purges_its_checkpoint(env):
    client, graph = env
    token = token_for(client)
    thread_id, _ = start_run(client, token)
    assert client.delete(f"/research/runs/{thread_id}", headers=auth(token)).status_code == 204
    assert runs(client, token)["runs"] == []
    assert graph.checkpointer.deleted == [thread_id]
    assert client.get(f"/research/runs/{thread_id}", headers=auth(token)).status_code == 404
    assert client.delete(f"/research/runs/{thread_id}", headers=auth(token)).status_code == 404


def test_cannot_delete_someone_elses_run(env):
    client, graph = env
    alice, bob = token_for(client), token_for(client, "bob@example.com", "Bob Builder")
    thread_id, _ = start_run(client, alice)
    assert client.delete(f"/research/runs/{thread_id}", headers=auth(bob)).status_code == 404
    assert graph.checkpointer.deleted == []
    assert len(runs(client, alice)["runs"]) == 1


def test_bad_ids_are_404(env):
    client, _ = env
    token = token_for(client)
    assert client.get("/research/runs/not-a-uuid", headers=auth(token)).status_code == 404
    assert client.delete("/research/runs/not-a-uuid", headers=auth(token)).status_code == 404


# ------------------------------------------------------------------ usage limits


def test_daily_limit_blocks_extra_runs_and_is_reported(tmp_path):
    client, _ = make_client(tmp_path, max_runs_per_user_per_day=2)
    with client:
        token = token_for(client)
        start_run(client, token, topic="first topic here")
        start_run(client, token, topic="second topic here")
        blocked = client.post("/research/runs", json={"topic": "third topic here"}, headers=auth(token))
        assert blocked.status_code == 429 and "Daily research limit" in blocked.json()["detail"]
        data = runs(client, token)
        assert data["usage"] == {"used_today": 2, "daily_limit": 2, "default_iterations": 1, "tokens_today": 0}
        assert len(data["runs"]) == 2  # the blocked attempt left no ghost entry
        # Another account has its own allowance.
        other = token_for(client, "bob@example.com", "Bob Builder")
        start_run(client, other, topic="bobs topic here")


def test_daily_limit_zero_means_unlimited(tmp_path):
    client, _ = make_client(tmp_path, max_runs_per_user_per_day=0)
    with client:
        token = token_for(client)
        for i in range(3):
            start_run(client, token, topic=f"topic number {i}")
        assert runs(client, token)["usage"]["daily_limit"] is None


def test_old_runs_fall_out_of_the_daily_window(tmp_path):
    client, _ = make_client(tmp_path, max_runs_per_user_per_day=1)
    with client:
        token = token_for(client)
        user_id = client.get("/auth/me", headers=auth(token)).json()["id"]
        store = client.app.state.user_store
        store.record_run("22222222-2222-4222-8222-222222222222", user_id, "yesterday topic")
        with store._connect() as conn:  # age it past the 24h window
            conn.execute("UPDATE research_runs SET created_at = '2020-01-01T00:00:00+00:00'")
        start_run(client, token, topic="today topic here")  # allowed again


def test_second_concurrent_run_for_one_user_is_refused(env):
    client, _ = env
    token = token_for(client)
    user_id = client.get("/auth/me", headers=auth(token)).json()["id"]
    client.app.state.user_active[user_id] = 1  # a run of theirs is streaming right now
    response = client.post("/research/runs", json={"topic": "a second parallel run"}, headers=auth(token))
    events = sse(response)
    assert events[-1][0] == "error" and events[-1][1]["code"] == "run_in_progress"
    assert runs(client, token)["runs"][0]["status"] == "failed"  # a never-started new run is marked failed
    # Other users are unaffected.
    other = token_for(client, "bob@example.com", "Bob Builder")
    start_run(client, other)


def test_rejected_resume_keeps_the_plan_reviewable(env):
    client, _ = env
    token = token_for(client)
    user_id = client.get("/auth/me", headers=auth(token)).json()["id"]
    thread_id, _ = start_run(client, token)
    client.app.state.user_active[user_id] = 1
    response = client.post(f"/research/runs/{thread_id}/resume", json={}, headers=auth(token))
    assert sse(response)[-1][1]["code"] == "run_in_progress"
    assert client.get(f"/research/runs/{thread_id}", headers=auth(token)).json()["status"] == "review"


def test_legacy_token_has_no_history_but_can_read_by_id(env):
    client, _ = env
    token = token_for(client)
    thread_id, _ = start_run(client, token)
    legacy = {"Authorization": "Bearer " + "L" * 40}
    assert client.get("/research/runs", headers=legacy).json()["runs"] == []
    assert client.get(f"/research/runs/{thread_id}", headers=legacy).status_code == 200


# ------------------------------------------------------------------ follow-up questions


def completed_run(client, token):
    thread_id, _ = start_run(client, token, topic="Grid storage")
    client.post(f"/research/runs/{thread_id}/resume", json={}, headers=auth(token))
    return thread_id


def test_follow_up_answers_from_the_saved_report(env):
    client, _ = env
    token = token_for(client)
    thread_id = completed_run(client, token)
    seen = {}

    def fake(report, question, settings):
        seen.update(report=report, question=question)
        return "Answer from the brief [1]."

    client.app.state.followup_fn = fake
    response = client.post(f"/research/runs/{thread_id}/ask", json={"question": "  Why?  "}, headers=auth(token))
    assert response.status_code == 200
    assert response.json() == {"question": "Why?", "answer": "Answer from the brief [1]."}
    assert seen["report"].startswith("# Brief") and seen["question"] == "Why?"


def test_follow_up_requires_a_finished_report_and_ownership(env):
    client, _ = env
    token = token_for(client)
    thread_id, _ = start_run(client, token, topic="Grid storage")  # still awaiting plan review
    client.app.state.followup_fn = lambda *a: "x"
    assert client.post(f"/research/runs/{thread_id}/ask", json={"question": "Why?"}, headers=auth(token)).status_code == 409
    done = completed_run(client, token)
    other = token_for(client, email="grace@example.com", name="Grace Hopper")
    assert client.post(f"/research/runs/{done}/ask", json={"question": "Why?"}, headers=auth(other)).status_code == 404
    assert client.post(f"/research/runs/{done}/ask", json={"question": "?"}, headers=auth(token)).status_code == 422


def test_follow_up_failures_and_rate_limit(env, monkeypatch):
    client, _ = env
    token = token_for(client)
    thread_id = completed_run(client, token)

    def boom(*args):
        raise RuntimeError("provider down")

    client.app.state.followup_fn = boom
    assert client.post(f"/research/runs/{thread_id}/ask", json={"question": "Why?"}, headers=auth(token)).status_code == 502
    client.app.state.followup_fn = lambda *a: "ok"
    monkeypatch.setattr("research_assistant.api.FOLLOWUPS_PER_HOUR", 2)
    client.app.state.followup_times.clear()
    codes = [client.post(f"/research/runs/{thread_id}/ask", json={"question": "Why?"}, headers=auth(token)).status_code for _ in range(3)]
    assert codes == [200, 200, 429]


# ------------------------------------------------------------------ token usage


class _Msg:
    def __init__(self, i, o):
        self.usage_metadata = {"input_tokens": i, "output_tokens": o}


class _Gen:
    def __init__(self, i, o):
        self.message = _Msg(i, o)


class _Resp:
    def __init__(self, i, o):
        self.generations = [[_Gen(i, o)]]
        self.llm_output = None


def test_token_usage_is_saved_per_run_and_totalled_for_today(tmp_path):
    class TokenGraph(FakeGraph):
        async def astream(self, graph_input, config, stream_mode="updates"):
            tracker = next(c for c in config["callbacks"] if hasattr(c, "on_llm_end"))
            tracker.on_llm_end(_Resp(1000, 250))  # one model call
            tracker.on_llm_end(_Resp(500, 50))
            async for item in super().astream(graph_input, config, stream_mode):
                yield item

    client, _ = make_client(tmp_path, graph=TokenGraph())
    with client:
        token = token_for(client)
        assert runs(client, token)["usage"]["tokens_today"] == 0
        thread_id, _ = start_run(client, token)
        assert client.get(f"/research/runs/{thread_id}", headers=auth(token)).json()["tokens_used"] == 1800
        client.post(f"/research/runs/{thread_id}/resume", json={}, headers=auth(token))  # second segment adds
        listing = runs(client, token)
        assert listing["runs"][0]["tokens_used"] == 3600
        assert listing["usage"]["tokens_today"] == 3600


def test_usage_tracker_falls_back_to_llm_output():
    from research_assistant.usage import UsageTracker

    class Old:
        generations = [[object()]]
        llm_output = {"token_usage": {"prompt_tokens": 7, "completion_tokens": 3}}

    tracker = UsageTracker()
    tracker.on_llm_end(Old())
    assert tracker.total == 10
