"""Recurring research: schedule CRUD, limits, and running due schedules end to end."""

from datetime import datetime, timedelta, timezone

from helpers import auth, make_client, register


def token_for(client, email="ada@example.com", name="Ada Lovelace"):
    return register(client, email=email, name=name).json()["access_token"]


def create(client, token, topic="Grid storage costs", frequency="weekly"):
    return client.post("/research/schedules", json={"topic": topic, "frequency": frequency}, headers=auth(token))


def make_due(client, token, schedule_id):
    store = client.app.state.user_store
    user_id = client.get("/auth/me", headers=auth(token)).json()["id"]
    past = (datetime.now(timezone.utc) - timedelta(minutes=5)).isoformat(timespec="microseconds")
    assert store.update_schedule(schedule_id, user_id, next_run_at=past)


def run_due(client):
    return client.portal.call(client.app.state.run_due_schedules)


def test_schedule_lifecycle_limits_and_ownership(tmp_path):
    client, _ = make_client(tmp_path, max_schedules_per_user=2)
    with client:
        token = token_for(client)
        made = create(client, token)
        assert made.status_code == 201
        schedule = made.json()
        assert schedule["frequency"] == "weekly" and schedule["enabled"] is True and "user_id" not in schedule
        assert datetime.fromisoformat(schedule["next_run_at"]) > datetime.now(timezone.utc) + timedelta(days=6)

        assert create(client, token, topic="Another topic", frequency="daily").status_code == 201
        full = create(client, token, topic="Third topic")
        assert full.status_code == 409 and "up to 2" in full.json()["detail"]
        assert create(client, token, topic="ab").status_code == 422
        assert create(client, token, frequency="hourly").status_code == 422

        listing = client.get("/research/schedules", headers=auth(token)).json()
        assert len(listing["schedules"]) == 2 and listing["limit"] == 2

        other = token_for(client, email="grace@example.com", name="Grace Hopper")
        sid = schedule["id"]
        assert client.get("/research/schedules", headers=auth(other)).json()["schedules"] == []
        assert client.patch(f"/research/schedules/{sid}", json={"enabled": False}, headers=auth(other)).status_code == 404
        assert client.delete(f"/research/schedules/{sid}", headers=auth(other)).status_code == 404

        paused = client.patch(f"/research/schedules/{sid}", json={"enabled": False}, headers=auth(token)).json()
        assert paused["enabled"] is False
        assert client.delete(f"/research/schedules/{sid}", headers=auth(token)).status_code == 204
        assert len(client.get("/research/schedules", headers=auth(token)).json()["schedules"]) == 1


def test_shared_demo_account_and_service_token_cannot_schedule(tmp_path):
    from helpers import LEGACY

    client, _ = make_client(tmp_path, demo_email="demo@example.com", demo_password="demo-pass-123")
    with client:
        demo = client.post("/auth/login", json={"email": "demo@example.com", "password": "demo-pass-123"}).json()["access_token"]
        assert create(client, demo).status_code == 403
        assert create(client, LEGACY).status_code == 403


def test_due_schedule_runs_the_whole_research_and_emails_the_result(tmp_path):
    client, graph = make_client(tmp_path)
    with client:
        token = token_for(client)
        sid = create(client, token).json()["id"]
        assert run_due(client) == 0  # not due yet

        make_due(client, token, sid)
        assert run_due(client) == 1
        assert run_due(client) == 0  # claimed: fires once

        runs = client.get("/research/runs", headers=auth(token)).json()["runs"]
        assert len(runs) == 1 and runs[0]["status"] == "complete" and runs[0]["schedule_id"] == sid
        schedule = client.get("/research/schedules", headers=auth(token)).json()["schedules"][0]
        assert schedule["last_status"] == "complete" and schedule["last_thread_id"] == runs[0]["thread_id"]
        assert datetime.fromisoformat(schedule["next_run_at"]) > datetime.now(timezone.utc) + timedelta(days=6)

        sent = client.app.state.mailer.sent
        assert len(sent) == 1 and sent[0][0] == "ada@example.com" and "Brief" in sent[0][1]
        assert "What changed" not in sent[0][2]  # nothing to compare on the first run


def test_second_run_emails_what_changed(tmp_path, monkeypatch):
    client, _ = make_client(tmp_path)
    monkeypatch.setattr("research_assistant.api.summarize_changes", lambda old, new, settings: "- A new figure appeared [1]")
    with client:
        token = token_for(client)
        sid = create(client, token).json()["id"]
        for _ in range(2):
            make_due(client, token, sid)
            assert run_due(client) == 1
        sent = client.app.state.mailer.sent
        assert len(sent) == 2
        assert "What changed since last time" in sent[1][2] and "A new figure appeared" in sent[1][2]


def test_schedule_waits_when_the_daily_run_limit_is_reached(tmp_path):
    client, _ = make_client(tmp_path, max_runs_per_user_per_day=1)
    with client:
        token = token_for(client)
        sid = create(client, token).json()["id"]
        make_due(client, token, sid)
        run_due(client)  # uses the single allowed run
        make_due(client, token, sid)
        run_due(client)
        schedule = client.get("/research/schedules", headers=auth(token)).json()["schedules"][0]
        assert schedule["last_status"] == "waiting: daily run limit"
        assert len(client.get("/research/runs", headers=auth(token)).json()["runs"]) == 1
        assert datetime.fromisoformat(schedule["next_run_at"]) < datetime.now(timezone.utc) + timedelta(hours=2)


def test_run_now_marks_it_due_and_wakes_the_scheduler(tmp_path):
    client, _ = make_client(tmp_path)
    with client:
        token = token_for(client)
        sid = create(client, token).json()["id"]
        assert client.post(f"/research/schedules/{sid}/run", headers=auth(token)).status_code == 202
        assert client.app.state.scheduler_wakeup.is_set()
        assert run_due(client) == 1
        assert client.post("/research/schedules/nope/run", headers=auth(token)).status_code == 404
