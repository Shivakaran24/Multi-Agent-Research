"""Tags, emailing a brief, invite codes and the admin stats page."""

from helpers import LEGACY, auth, make_client, register, start_run


def token_for(client, email="ada@example.com", name="Ada Lovelace", **extra):
    return register(client, email=email, name=name).json()["access_token"]


def completed(client, token):
    thread_id, _ = start_run(client, token, topic="Grid storage")
    client.post(f"/research/runs/{thread_id}/resume", json={}, headers=auth(token))
    return thread_id


def test_tags_are_cleaned_saved_listed_and_owner_only(tmp_path):
    client, _ = make_client(tmp_path)
    with client:
        token = token_for(client)
        thread_id = completed(client, token)
        url = f"/research/runs/{thread_id}/tags"
        saved = client.put(url, json={"tags": [" Energy ", "batteries", "energy"]}, headers=auth(token))
        assert saved.status_code == 200 and saved.json()["tags"] == ["energy", "batteries"]
        assert client.get("/research/runs", headers=auth(token)).json()["runs"][0]["tags"] == ["energy", "batteries"]
        assert client.get(f"/research/runs/{thread_id}", headers=auth(token)).json()["tags"] == ["energy", "batteries"]
        assert client.put(url, json={"tags": ["bad<tag>"]}, headers=auth(token)).status_code == 422
        assert client.put(url, json={"tags": list("abcdef")}, headers=auth(token)).status_code == 422
        other = token_for(client, email="grace@example.com", name="Grace Hopper")
        assert client.put(url, json={"tags": ["x1"]}, headers=auth(other)).status_code == 404
        assert client.put(url, json={"tags": []}, headers=auth(token)).json()["tags"] == []


def test_email_a_brief_to_yourself_or_one_recipient(tmp_path):
    client, _ = make_client(tmp_path)
    with client:
        token = token_for(client)
        thread_id = completed(client, token)
        url = f"/research/runs/{thread_id}/email"
        own = client.post(url, json={}, headers=auth(token))
        assert own.status_code == 200 and own.json()["to"] == "ada@example.com"
        other = client.post(url, json={"to": "Grace@Example.com"}, headers=auth(token))
        assert other.json()["to"] == "grace@example.com"
        sent = client.app.state.mailer.sent
        assert [m[0] for m in sent] == ["ada@example.com", "grace@example.com"]
        assert sent[0][1].startswith("Fieldwork: Brief") and "# Brief" in sent[0][2]
        assert "Ada Lovelace sent you" in sent[1][2]
        assert client.post(url, json={"to": "not-an-email"}, headers=auth(token)).status_code == 422


def test_email_limits_demo_and_incomplete_runs(tmp_path, monkeypatch):
    client, _ = make_client(tmp_path, demo_email="demo@example.com", demo_password="demo-pass-123")
    with client:
        token = token_for(client)
        pending, _ = start_run(client, token)
        assert client.post(f"/research/runs/{pending}/email", json={}, headers=auth(token)).status_code == 409
        done = completed(client, token)
        monkeypatch.setattr("research_assistant.api.EMAILS_PER_HOUR", 2)
        codes = [client.post(f"/research/runs/{done}/email", json={}, headers=auth(token)).status_code for _ in range(3)]
        assert codes == [200, 200, 429]
        demo = client.post("/auth/login", json={"email": "demo@example.com", "password": "demo-pass-123"}).json()["access_token"]
        demo_run = completed(client, demo)
        assert client.post(f"/research/runs/{demo_run}/email", json={}, headers=auth(demo)).status_code == 403
        assert client.post(f"/research/runs/{done}/email", json={}, headers=auth(LEGACY)).status_code == 403


def test_invite_codes_gate_registration(tmp_path):
    client, _ = make_client(tmp_path, app_env="development", allow_registration=False, invite_codes="alpha-1, beta-2")
    with client:
        config = client.get("/auth/config").json()
        assert config["registration_open"] is True and config["invite_required"] is True
        body = {"full_name": "Ada Lovelace", "email": "ada@example.com", "password": "analytical1"}
        assert client.post("/auth/register", json=body).status_code == 403
        wrong = client.post("/auth/register", json={**body, "invite_code": "nope"})
        assert wrong.status_code == 403 and "invite code" in wrong.json()["detail"]
        assert client.post("/auth/register", json={**body, "invite_code": "beta-2"}).status_code == 201


def test_registration_without_invite_codes_is_unchanged(tmp_path):
    client, _ = make_client(tmp_path)
    with client:
        assert client.get("/auth/config").json()["invite_required"] is False
        assert register(client).status_code == 201


def test_admin_stats_are_for_listed_admins_only(tmp_path):
    client, _ = make_client(tmp_path, admin_emails="boss@example.com")
    with client:
        boss = register(client, email="boss@example.com", name="The Boss").json()
        assert boss["user"]["is_admin"] is True
        token = boss["access_token"]
        user = register(client, email="ada@example.com").json()
        assert user["user"]["is_admin"] is False
        thread_id = completed(client, user["access_token"])
        client.put(f"/research/runs/{thread_id}/feedback", json={"value": 1}, headers=auth(user["access_token"]))
        start_run(client, user["access_token"])  # left at plan review -> counted as a run

        assert client.get("/research/admin/stats", headers=auth(user["access_token"])).status_code == 404
        assert client.get("/research/admin/stats", headers=auth(LEGACY)).status_code == 404
        stats = client.get("/research/admin/stats", headers=auth(token)).json()
        assert stats["users"] == 2 and stats["runs_total"] == 2 and stats["runs_24h"] == 2
        assert stats["runs_by_status"]["complete"] == 1 and stats["feedback_up"] == 1 and stats["feedback_down"] == 0
        assert stats["top_users"][0]["email"] == "ada@example.com"
        assert client.get("/auth/me", headers=auth(token)).json()["is_admin"] is True
