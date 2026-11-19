"""Feedback, read-only share links, and optional email verification."""

import re

from helpers import auth, make_client, register, start_run


def token_for(client, email="ada@example.com", name="Ada Lovelace"):
    return register(client, email=email, name=name).json()["access_token"]


def completed(client, token):
    thread_id, _ = start_run(client, token, topic="Grid storage")
    client.post(f"/research/runs/{thread_id}/resume", json={}, headers=auth(token))
    return thread_id


def test_feedback_is_saved_cleared_and_owner_only(tmp_path):
    client, _ = make_client(tmp_path)
    with client:
        token = token_for(client)
        thread_id = completed(client, token)
        url = f"/research/runs/{thread_id}/feedback"
        assert client.put(url, json={"value": 1}, headers=auth(token)).status_code == 204
        assert client.get(f"/research/runs/{thread_id}", headers=auth(token)).json()["feedback"] == 1
        assert client.put(url, json={"value": -1}, headers=auth(token)).status_code == 204
        assert client.get("/research/runs", headers=auth(token)).json()["runs"][0]["feedback"] == -1
        assert client.put(url, json={"value": 0}, headers=auth(token)).status_code == 204
        assert client.get(f"/research/runs/{thread_id}", headers=auth(token)).json()["feedback"] is None
        assert client.put(url, json={"value": 5}, headers=auth(token)).status_code == 422
        other = token_for(client, email="grace@example.com", name="Grace Hopper")
        assert client.put(url, json={"value": 1}, headers=auth(other)).status_code == 404


def test_share_link_is_public_read_only_and_revocable(tmp_path):
    client, _ = make_client(tmp_path)
    with client:
        token = token_for(client)
        pending, _ = start_run(client, token)  # plan review, no report yet
        assert client.post(f"/research/runs/{pending}/share", headers=auth(token)).status_code == 409

        thread_id = completed(client, token)
        shared = client.post(f"/research/runs/{thread_id}/share", headers=auth(token)).json()["share_token"]
        assert len(shared) >= 20
        # same token while the link stays on; shown to the owner in run details
        assert client.post(f"/research/runs/{thread_id}/share", headers=auth(token)).json()["share_token"] == shared
        assert client.get(f"/research/runs/{thread_id}", headers=auth(token)).json()["share_token"] == shared

        public = client.get(f"/shared/{shared}")  # no credentials
        assert public.status_code == 200
        body = public.json()
        assert body["topic"] == "Grid storage" and body["report"].startswith("# Brief")
        assert "user_id" not in body and "thread_id" not in body and "share_token" not in body

        other = token_for(client, email="grace@example.com", name="Grace Hopper")
        assert client.delete(f"/research/runs/{thread_id}/share", headers=auth(other)).status_code == 404
        assert client.delete(f"/research/runs/{thread_id}/share", headers=auth(token)).status_code == 204
        assert client.get(f"/shared/{shared}").status_code == 404
        assert client.get("/shared/short").status_code == 404


def test_deleting_a_run_removes_its_share_link(tmp_path):
    client, _ = make_client(tmp_path)
    with client:
        token = token_for(client)
        thread_id = completed(client, token)
        shared = client.post(f"/research/runs/{thread_id}/share", headers=auth(token)).json()["share_token"]
        client.delete(f"/research/runs/{thread_id}", headers=auth(token))
        assert client.get(f"/shared/{shared}").status_code == 404


def test_email_verification_gates_research_until_confirmed(tmp_path):
    client, _ = make_client(tmp_path, require_email_verification=True)
    with client:
        assert client.get("/auth/config").json()["email_verification_required"] is True
        auth_body = register(client).json()
        token = auth_body["access_token"]
        assert auth_body["user"]["email_verified"] is False
        mailer = client.app.state.mailer
        assert len(mailer.sent) == 1 and "verify?token=" in mailer.sent[0][2]

        blocked = client.post("/research/runs", json={"topic": "Grid storage"}, headers=auth(token))
        assert blocked.status_code == 403 and "Confirm your email" in blocked.json()["detail"]

        link_token = re.search(r"verify\?token=([\w-]+)", mailer.sent[0][2]).group(1)
        assert client.post("/auth/verify-email", json={"token": "bad"}).status_code == 400
        assert client.post("/auth/verify-email", json={"token": link_token}).status_code == 200
        assert client.post("/auth/verify-email", json={"token": link_token}).status_code == 400  # single use
        assert client.get("/auth/me", headers=auth(token)).json()["email_verified"] is True
        start_run(client, token)  # now allowed


def test_resend_verification_and_default_off(tmp_path):
    client, _ = make_client(tmp_path, require_email_verification=True)
    with client:
        token = token_for(client)
        assert client.post("/auth/resend-verification", headers=auth(token)).status_code == 202
        assert len(client.app.state.mailer.sent) == 2
    default, _ = make_client(tmp_path / "d")
    with default:
        body = register(default).json()
        assert body["user"]["email_verified"] is True
        assert default.get("/auth/config").json()["email_verification_required"] is False
        assert default.app.state.mailer.sent == []


def test_browser_preflight_allows_the_methods_the_frontend_uses(tmp_path):
    client, _ = make_client(tmp_path, cors_origins=["https://app.example.com"])
    with client:
        for method in ("PUT", "DELETE", "POST"):
            response = client.options(
                "/research/runs/abc/feedback",
                headers={
                    "Origin": "https://app.example.com",
                    "Access-Control-Request-Method": method,
                    "Access-Control-Request-Headers": "authorization,content-type",
                },
            )
            assert response.status_code == 200, method
            assert method in response.headers["access-control-allow-methods"]
