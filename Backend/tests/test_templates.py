"""Saved research templates and options stored on recurring schedules."""

from helpers import auth, make_client, register
from test_options import CapturingGraph
from test_schedules import make_due, run_due


def token_for(client, email="ada@example.com", name="Ada Lovelace"):
    return register(client, email=email, name=name).json()["access_token"]


def test_template_lifecycle_limits_and_privacy(tmp_path):
    client, _ = make_client(tmp_path)
    with client:
        token = token_for(client)
        body = {"name": " Weekly scan ", "topic": "Grid storage", "options": {"style": "executive", "language": "Hindi", "documents": [{"name": "a.pdf", "text": "x" * 100}]}}
        made = client.post("/research/templates", json=body, headers=auth(token))
        assert made.status_code == 201
        template = made.json()
        assert template["name"] == "Weekly scan" and template["options"]["style"] == "executive"
        assert "documents" not in template["options"], "pasted PDF text is never saved"

        listing = client.get("/research/templates", headers=auth(token)).json()
        assert [t["name"] for t in listing["templates"]] == ["Weekly scan"] and listing["limit"] == 20
        assert client.post("/research/templates", json={"name": "  "}, headers=auth(token)).status_code == 422
        assert client.post("/research/templates", json={"name": "x", "options": {"style": "poem"}}, headers=auth(token)).status_code == 422

        other = token_for(client, email="grace@example.com", name="Grace Hopper")
        assert client.get("/research/templates", headers=auth(other)).json()["templates"] == []
        assert client.delete(f"/research/templates/{template['id']}", headers=auth(other)).status_code == 404
        assert client.delete(f"/research/templates/{template['id']}", headers=auth(token)).status_code == 204
        assert client.get("/research/templates", headers=auth(token)).json()["templates"] == []
        assert client.get("/research/templates").status_code == 401


def test_template_count_is_capped(tmp_path):
    client, _ = make_client(tmp_path)
    with client:
        token = token_for(client)
        for i in range(20):
            assert client.post("/research/templates", json={"name": f"t{i}"}, headers=auth(token)).status_code == 201
        full = client.post("/research/templates", json={"name": "one more"}, headers=auth(token))
        assert full.status_code == 409 and "up to 20" in full.json()["detail"]


def test_scheduled_runs_use_the_saved_options(tmp_path):
    graph = CapturingGraph()
    client, _ = make_client(tmp_path, graph=graph, max_schedules_per_user=2)
    with client:
        token = token_for(client)
        made = client.post(
            "/research/schedules",
            json={"topic": "Grid storage costs", "frequency": "daily", "options": {"style": "executive", "language": "French", "urls": ["https://a.example/p"]}},
            headers=auth(token),
        )
        assert made.status_code == 201 and made.json()["options"]["language"] == "French"
        plain = client.post("/research/schedules", json={"topic": "Another topic", "frequency": "daily"}, headers=auth(token))
        assert plain.json()["options"] is None
        make_due(client, token, made.json()["id"])
        run_due(client)
        sent = graph.inputs[0]
        assert sent["options"]["style"] == "executive" and sent["options"]["language"] == "French"
        assert "urls" not in sent["options"] and "documents" not in sent["options"]
