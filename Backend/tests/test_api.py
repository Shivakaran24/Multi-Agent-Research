"""HTTP-level tests: accounts, access control, and the streamed research flow."""

import time

import pytest

from helpers import LEGACY, QUESTIONS, SECRET, auth, make_client, register, sse, start_run
from research_assistant.auth import create_access_token


@pytest.fixture()
def client(tmp_path):
    test_client, _ = make_client(tmp_path)
    with test_client:
        yield test_client


# ------------------------------------------------------------------ accounts


def test_register_returns_session_and_never_leaks_password(client):
    response = register(client, email="  Ada@Example.COM ")
    assert response.status_code == 201
    body = response.json()
    assert body["user"]["email"] == "ada@example.com"
    assert body["user"]["full_name"] == "Ada Lovelace"
    assert body["token_type"] == "bearer" and body["expires_in"] == 720 * 60
    assert "analytical1" not in response.text and "password" not in response.text.lower()
    me = client.get("/auth/me", headers=auth(body["access_token"]))
    assert me.status_code == 200 and me.json()["id"] == body["user"]["id"]


def test_duplicate_email_is_rejected_case_insensitively(client):
    assert register(client).status_code == 201
    again = register(client, email="ADA@example.com", name="Someone Else")
    assert again.status_code == 409
    assert "already exists" in again.json()["detail"]


@pytest.mark.parametrize(
    "payload",
    [
        {"full_name": "Ada", "email": "nope", "password": "analytical1"},
        {"full_name": "Ada", "email": "ada@example.com", "password": "short1"},
        {"full_name": "Ada", "email": "ada@example.com", "password": "nodigitshere"},
        {"full_name": "A", "email": "ada@example.com", "password": "analytical1"},
        {"email": "ada@example.com", "password": "analytical1"},
    ],
)
def test_register_validation_errors_are_422(client, payload):
    assert client.post("/auth/register", json=payload).status_code == 422


def test_login_success_and_uniform_failures(client):
    register(client)
    ok = client.post("/auth/login", json={"email": "ADA@example.com", "password": "analytical1"})
    assert ok.status_code == 200 and ok.json()["access_token"]
    wrong = client.post("/auth/login", json={"email": "ada@example.com", "password": "wrongpass1"})
    unknown = client.post("/auth/login", json={"email": "ghost@example.com", "password": "wrongpass1"})
    assert wrong.status_code == unknown.status_code == 401
    assert wrong.json() == unknown.json()  # no account enumeration


def test_login_is_throttled_after_repeated_failures(client):
    register(client)
    bad = {"email": "ada@example.com", "password": "wrongpass1"}
    assert [client.post("/auth/login", json=bad).status_code for _ in range(5)] == [401] * 5
    blocked = client.post("/auth/login", json=bad)
    assert blocked.status_code == 429 and int(blocked.headers["retry-after"]) > 0
    # Even the right password is refused while locked out, then a different account is unaffected.
    assert client.post("/auth/login", json={**bad, "password": "analytical1"}).status_code == 429
    register(client, email="grace@example.com", name="Grace Hopper")
    assert client.post("/auth/login", json={"email": "grace@example.com", "password": "analytical1"}).status_code == 200


def test_registration_can_be_disabled(tmp_path):
    test_client, _ = make_client(tmp_path, allow_registration=False)
    with test_client:
        assert register(test_client).status_code == 403


def test_me_rejects_missing_forged_and_expired_tokens(client):
    uid = register(client).json()["user"]["id"]
    assert client.get("/auth/me").status_code == 401
    assert client.get("/auth/me", headers=auth("garbage")).status_code == 401
    assert client.get("/auth/me", headers=auth(create_access_token(uid, "z" * 40, 60))).status_code == 401
    assert client.get("/auth/me", headers=auth(create_access_token(uid, SECRET, -1))).status_code == 401
    assert client.get("/auth/me", headers=auth(create_access_token("no-such-user", SECRET, 60))).status_code == 401


# ------------------------------------------------------------------ access control


@pytest.mark.parametrize("headers", [{}, auth("garbage"), auth("a.b.c"), {"Authorization": "Basic abc"}])
def test_research_requires_a_valid_login(client, headers):
    response = client.post("/research/runs", json={"topic": "grid storage"}, headers=headers)
    assert response.status_code == 401


def test_forged_and_expired_tokens_cannot_start_research(client):
    uid = register(client).json()["user"]["id"]
    for token in (create_access_token(uid, "z" * 40, 60), create_access_token(uid, SECRET, -1)):
        assert client.post("/research/runs", json={"topic": "grid storage"}, headers=auth(token)).status_code == 401


def test_health_endpoints_stay_public(client):
    assert client.get("/healthz").status_code == 200
    assert client.get("/readyz").status_code == 200


def test_security_headers_and_cors(client):
    response = client.get("/healthz")
    assert response.headers["x-content-type-options"] == "nosniff"
    assert response.headers["cache-control"] == "no-store"
    preflight = client.options(
        "/auth/login",
        headers={
            "Origin": "http://localhost:5173",
            "Access-Control-Request-Method": "POST",
            "Access-Control-Request-Headers": "content-type,authorization",
        },
    )
    assert preflight.status_code == 200
    assert preflight.headers["access-control-allow-origin"] == "http://localhost:5173"


# ------------------------------------------------------------------ the research flow


def test_full_run_plan_review_then_report(client):
    token = register(client).json()["access_token"]
    thread_id, events = start_run(client, token)
    assert [q["question"] for q in events[-1][1]["questions"]] == [q["question"] for q in QUESTIONS]

    resumed = client.post(f"/research/runs/{thread_id}/resume", json={}, headers=auth(token))
    assert resumed.status_code == 200
    kinds = [kind for kind, _ in sse(resumed)]
    assert kinds[0] == "started" and kinds[-1] == "completed"
    assert "SECRET FULL PAGE TEXT" not in resumed.text  # scraped text never streamed to the browser
    completed = sse(resumed)[-1][1]
    assert completed["report"].startswith("# Brief")


def test_edited_plan_is_what_the_graph_receives(client, tmp_path):
    test_client, graph = make_client(tmp_path)
    with test_client:
        token = register(test_client).json()["access_token"]
        thread_id, _ = start_run(test_client, token)
        edited = [{"question": "An edited, focused question?", "rationale": "Because."}]
        response = test_client.post(f"/research/runs/{thread_id}/resume", json={"questions": edited}, headers=auth(token))
        assert response.status_code == 200
        assert graph.threads[thread_id]["resume"] == {"questions": edited}


def test_cannot_resume_twice(client):
    token = register(client).json()["access_token"]
    thread_id, _ = start_run(client, token)
    assert client.post(f"/research/runs/{thread_id}/resume", json={}, headers=auth(token)).status_code == 200
    assert client.post(f"/research/runs/{thread_id}/resume", json={}, headers=auth(token)).status_code == 409


def test_users_cannot_touch_each_others_runs(client):
    alice = register(client).json()["access_token"]
    bob = register(client, email="bob@example.com", name="Bob Builder").json()["access_token"]
    thread_id, _ = start_run(client, alice)
    assert client.post(f"/research/runs/{thread_id}/resume", json={}, headers=auth(bob)).status_code == 404
    assert client.post(f"/research/runs/{thread_id}/visuals", json={}, headers=auth(bob)).status_code == 404
    # The owner is unaffected, and the run was not advanced by Bob's attempt.
    assert client.post(f"/research/runs/{thread_id}/resume", json={}, headers=auth(alice)).status_code == 200


def test_owner_reaches_visuals_gate_but_not_before_report(client):
    token = register(client).json()["access_token"]
    thread_id, _ = start_run(client, token)
    early = client.post(f"/research/runs/{thread_id}/visuals", json={}, headers=auth(token))
    assert early.status_code == 409  # report not finished yet
    client.post(f"/research/runs/{thread_id}/resume", json={}, headers=auth(token))
    late = client.post(f"/research/runs/{thread_id}/visuals", json={}, headers=auth(token))
    assert late.status_code == 503  # past the ownership + report gates; Tavily simply isn't configured


def test_invalid_thread_ids_are_404(client):
    token = register(client).json()["access_token"]
    assert client.post("/research/runs/not-a-uuid/resume", json={}, headers=auth(token)).status_code == 404


def test_legacy_shared_token_still_works_and_is_unrestricted(client):
    user = register(client).json()["access_token"]
    thread_id, _ = start_run(client, user)
    assert client.post(f"/research/runs/{thread_id}/resume", json={}, headers=auth(LEGACY)).status_code == 200
    thread_id2, _ = start_run(client, LEGACY, topic="a script-started run")
    assert thread_id2


def test_token_for_a_deleted_account_cannot_start_runs(client, tmp_path):
    ghost = create_access_token("00000000-0000-0000-0000-000000000000", SECRET, 600)
    response = client.post("/research/runs", json={"topic": "grid storage"}, headers=auth(ghost))
    assert response.status_code == 401 and "session has expired" in response.json()["detail"]


def test_topic_validation(client):
    token = register(client).json()["access_token"]
    assert client.post("/research/runs", json={"topic": "  a "}, headers=auth(token)).status_code == 422
    assert client.post("/research/runs", json={"topic": "x" * 2001}, headers=auth(token)).status_code == 422


def test_sessions_survive_a_server_restart_when_secret_is_configured(tmp_path):
    first, _ = make_client(tmp_path)
    with first:
        token = register(first).json()["access_token"]
    second, _ = make_client(tmp_path, fresh_database=False)  # new app instance, same secret + same database
    with second:
        assert second.get("/auth/me", headers=auth(token)).status_code == 200
        assert second.post("/auth/login", json={"email": "ada@example.com", "password": "analytical1"}).status_code == 200


def test_token_expiry_is_enforced_by_the_clock(client):
    uid = register(client).json()["user"]["id"]
    short = create_access_token(uid, SECRET, 1)
    assert client.get("/auth/me", headers=auth(short)).status_code == 200
    time.sleep(2.1)
    assert client.get("/auth/me", headers=auth(short)).status_code == 401
