"""Account security: change/reset password, session revocation, deletion, registration policy, CLI, migration."""

import sqlite3

import pytest

from helpers import POSTGRES_URL, SECRET, auth, make_client, register, start_run
from research_assistant import auth as auth_module
from research_assistant.auth import UserStore, create_access_token, create_user_cli, hash_password
from research_assistant.config import Settings


@pytest.fixture()
def env(tmp_path):
    client, graph = make_client(tmp_path)
    with client:
        yield client, graph


def login(client, email="ada@example.com", password="analytical1"):
    return client.post("/auth/login", json={"email": email, "password": password})


# ------------------------------------------------------------------ change password


def test_change_password_revokes_other_sessions_but_keeps_this_one(env):
    client, _ = env
    old = register(client).json()["access_token"]
    other_device = login(client).json()["access_token"]
    response = client.post(
        "/auth/change-password",
        json={"current_password": "analytical1", "new_password": "newer-pass2"},
        headers=auth(old),
    )
    assert response.status_code == 200
    fresh = response.json()["access_token"]
    assert client.get("/auth/me", headers=auth(fresh)).status_code == 200
    for stale in (old, other_device):
        assert client.get("/auth/me", headers=auth(stale)).status_code == 401
        assert client.get("/research/runs", headers=auth(stale)).status_code == 401  # middleware path too
    assert login(client, password="analytical1").status_code == 401
    assert login(client, password="newer-pass2").status_code == 200


def test_change_password_validation(env):
    client, _ = env
    token = register(client).json()["access_token"]
    url = "/auth/change-password"
    assert client.post(url, json={"current_password": "wrong-one1", "new_password": "newer-pass2"}, headers=auth(token)).status_code == 401
    assert client.post(url, json={"current_password": "analytical1", "new_password": "analytical1"}, headers=auth(token)).status_code == 422
    assert client.post(url, json={"current_password": "analytical1", "new_password": "weakpass"}, headers=auth(token)).status_code == 422
    assert client.post(url, json={"current_password": "analytical1", "new_password": "newer-pass2"}).status_code == 401


def test_password_confirmation_is_throttled(env):
    client, _ = env
    token = register(client).json()["access_token"]
    bad = {"current_password": "wrong-one1", "new_password": "newer-pass2"}
    codes = [client.post("/auth/change-password", json=bad, headers=auth(token)).status_code for _ in range(6)]
    assert codes == [401] * 5 + [429]


# ------------------------------------------------------------------ forgot / reset


def test_forgot_then_reset_full_flow(env):
    client, _ = env
    old = register(client).json()["access_token"]
    mailer = client.app.state.mailer
    response = client.post("/auth/forgot-password", json={"email": "ADA@example.com"})
    assert response.status_code == 202
    assert len(mailer.sent) == 1 and mailer.sent[0][0] == "ada@example.com"
    link = mailer.link()
    assert link.startswith("http://localhost:5173/#/reset?token=")
    token = link.split("token=")[1]

    assert client.post("/auth/reset-password", json={"token": token, "new_password": "weakpass"}).status_code == 422
    done = client.post("/auth/reset-password", json={"token": token, "new_password": "brand-new3"})
    assert done.status_code == 200
    assert login(client, password="brand-new3").status_code == 200
    assert login(client, password="analytical1").status_code == 401
    assert client.get("/auth/me", headers=auth(old)).status_code == 401  # old sessions revoked
    # Single use.
    again = client.post("/auth/reset-password", json={"token": token, "new_password": "another-pass4"})
    assert again.status_code == 400


def test_forgot_password_does_not_reveal_whether_an_account_exists(env):
    client, _ = env
    register(client)
    known = client.post("/auth/forgot-password", json={"email": "ada@example.com"})
    unknown = client.post("/auth/forgot-password", json={"email": "ghost@example.com"})
    assert known.status_code == unknown.status_code == 202 and known.json() == unknown.json()
    assert len(client.app.state.mailer.sent) == 1  # mail only for the real account


def test_reset_tokens_expire_and_garbage_is_rejected(env):
    client, _ = env
    user_id = register(client).json()["user"]["id"]
    expired = client.app.state.user_store.create_reset_token(user_id, ttl_minutes=-1)
    assert client.post("/auth/reset-password", json={"token": expired, "new_password": "brand-new3"}).status_code == 400
    assert client.post("/auth/reset-password", json={"token": "x" * 43, "new_password": "brand-new3"}).status_code == 400


def test_a_new_reset_request_invalidates_the_previous_link(env):
    client, _ = env
    register(client)
    mailer = client.app.state.mailer
    client.post("/auth/forgot-password", json={"email": "ada@example.com"})
    first = mailer.link().split("token=")[1]
    client.post("/auth/forgot-password", json={"email": "ada@example.com"})
    second = mailer.link().split("token=")[1]
    assert client.post("/auth/reset-password", json={"token": first, "new_password": "brand-new3"}).status_code == 400
    assert client.post("/auth/reset-password", json={"token": second, "new_password": "brand-new3"}).status_code == 200


def test_forgot_password_is_rate_limited(env):
    client, _ = env
    codes = [client.post("/auth/forgot-password", json={"email": f"u{i}@example.com"}).status_code for i in range(6)]
    assert codes == [202] * 5 + [429]


def test_reset_tokens_are_stored_hashed(env):
    client, _ = env
    user_id = register(client).json()["user"]["id"]
    token = client.app.state.user_store.create_reset_token(user_id, 30)
    with client.app.state.user_store._connect() as conn:
        stored = [row[0] for row in conn.execute("SELECT token_hash FROM password_resets").fetchall()]
    assert stored and token not in stored


def test_reset_is_unavailable_in_production_without_smtp(tmp_path):
    client, _ = make_client(tmp_path)
    prod = Settings(
        _env_file=None, app_env="production", groq_api_key="g", tavily_api_key="t",
        auth_secret_key=SECRET, database_url="postgresql://x", cors_origins=["https://x.example"],
        allowed_hosts=["x.example"],
    )
    assert prod.password_reset_available is False
    assert prod.registration_open is False  # secure by default; see test below
    with_smtp = prod.model_copy(update={"smtp_host": "smtp.example.com"})
    assert with_smtp.password_reset_available is True


# ------------------------------------------------------------------ registration policy


def test_registration_defaults_follow_the_environment():
    dev = Settings(_env_file=None, auth_secret_key=SECRET)
    assert dev.registration_open is True
    explicit_closed = Settings(_env_file=None, allow_registration=False)
    assert explicit_closed.registration_open is False
    prod_open = Settings(
        _env_file=None, app_env="production", groq_api_key="g", tavily_api_key="t", auth_secret_key=SECRET,
        database_url="postgresql://x", cors_origins=["https://x.example"], allowed_hosts=["x.example"],
        allow_registration=True,
    )
    assert prod_open.registration_open is True


def test_auth_config_endpoint_reports_capabilities(tmp_path):
    client, _ = make_client(tmp_path, allow_registration=False)
    with client:
        assert client.get("/auth/config").json() == {
            "registration_open": False,
            "password_reset_available": True,
            "email_verification_required": False,
            "invite_required": False,
        }


def test_blank_environment_values_are_treated_as_unset(monkeypatch):
    monkeypatch.setenv("ALLOW_REGISTRATION", "")
    monkeypatch.setenv("SMTP_HOST", "")
    monkeypatch.setenv("AUTH_SECRET_KEY", "")
    settings = Settings(_env_file=None)
    assert settings.allow_registration is None and settings.smtp_host is None and settings.auth_secret_key is None


# ------------------------------------------------------------------ delete account


def test_delete_account_removes_everything(env):
    client, graph = env
    token = register(client).json()["access_token"]
    thread_id, _ = start_run(client, token)
    assert client.post("/auth/delete-account", json={"password": "wrong-one1"}, headers=auth(token)).status_code == 401
    assert client.post("/auth/delete-account", json={"password": "analytical1"}).status_code == 401
    assert client.post("/auth/delete-account", json={"password": "analytical1"}, headers=auth(token)).status_code == 204
    assert login(client).status_code == 401
    assert client.get("/auth/me", headers=auth(token)).status_code == 401
    assert graph.checkpointer.deleted == [thread_id]
    # The email can be used again, and the new account starts empty.
    fresh = register(client).json()["access_token"]
    assert client.get("/research/runs", headers=auth(fresh)).json()["runs"] == []


def test_deleting_one_account_leaves_others_alone(env):
    client, _ = env
    alice = register(client).json()["access_token"]
    bob = register(client, email="bob@example.com", name="Bob Builder").json()["access_token"]
    thread_id, _ = start_run(client, bob)
    client.post("/auth/delete-account", json={"password": "analytical1"}, headers=auth(alice))
    assert client.get("/auth/me", headers=auth(bob)).status_code == 200
    assert client.get(f"/research/runs/{thread_id}", headers=auth(bob)).status_code == 200


# ------------------------------------------------------------------ token versioning


def test_tokens_without_a_version_claim_still_work_until_revoked(env):
    client, _ = env
    user_id = register(client).json()["user"]["id"]
    legacy_style = create_access_token(user_id, SECRET, 600)  # version defaults to 0
    assert client.get("/auth/me", headers=auth(legacy_style)).status_code == 200


# ------------------------------------------------------------------ CLI


def test_create_user_cli(tmp_path, monkeypatch, capsys):
    db = tmp_path / "cli.db"
    monkeypatch.setenv("AUTH_DB_PATH", str(db))
    monkeypatch.setenv("CREATE_USER_PASSWORD", "operator-pass1")
    monkeypatch.setattr(auth_module, "Settings", lambda: Settings(_env_file=None))
    assert create_user_cli(["--email", "New@Example.com", "--name", "New Person"]) == 0
    assert "Created account new@example.com" in capsys.readouterr().out
    store = UserStore(database_url=None, sqlite_path=db)
    assert store.get_by_email("new@example.com").full_name == "New Person"
    assert create_user_cli(["--email", "new@example.com", "--name", "Dup"]) == 1  # duplicate
    monkeypatch.setenv("CREATE_USER_PASSWORD", "short")
    assert create_user_cli(["--email", "x@example.com", "--name", "Weak"]) == 2  # weak password


# ------------------------------------------------------------------ migration


def test_existing_databases_are_upgraded_in_place(tmp_path):
    path = tmp_path / "old.db"
    conn = sqlite3.connect(path)
    conn.executescript(
        """
        CREATE TABLE users (id TEXT PRIMARY KEY, email TEXT NOT NULL UNIQUE, full_name TEXT NOT NULL,
                            password_hash TEXT NOT NULL, created_at TEXT NOT NULL);
        CREATE TABLE research_runs (thread_id TEXT PRIMARY KEY, user_id TEXT NOT NULL REFERENCES users(id),
                                    topic TEXT NOT NULL, created_at TEXT NOT NULL);
        INSERT INTO users VALUES ('u1', 'old@example.com', 'Old User', 'scrypt$x', '2026-01-01T00:00:00+00:00');
        INSERT INTO research_runs VALUES ('t1', 'u1', 'an old topic', '2026-01-02T00:00:00+00:00');
        """
    )
    conn.commit()
    conn.close()

    store = UserStore(database_url=None, sqlite_path=path)  # runs the migration
    store2 = UserStore(database_url=None, sqlite_path=path)  # and is safe to repeat
    user = store2.get_by_email("old@example.com")
    assert user.token_version == 0 and store.check_session("u1", 0)
    run = store.list_runs("u1")[0]
    assert run["topic"] == "an old topic" and run["status"] == "planning"
    assert run["updated_at"] == "2026-01-02T00:00:00+00:00"  # falls back to created_at
    store.update_run("t1", status="complete", report="# Old\n\nbody", title="Old")
    assert store.get_run("t1", "u1")["report"].startswith("# Old")


def test_reset_token_is_single_use_at_the_store_level(tmp_path):
    """The used-flag must hold even if the password update that normally follows never happens."""
    store = UserStore(database_url=None, sqlite_path=tmp_path / "s.db")
    user = store.create_user(email="a@example.com", full_name="Ada", password_hash=hash_password("analytical1"))
    token = store.create_reset_token(user.id, 30)
    assert store.consume_reset_token(token) == user.id
    assert store.consume_reset_token(token) is None
    assert store.consume_reset_token("never-issued-" + "x" * 30) is None


@pytest.mark.skipif(not POSTGRES_URL, reason="set TEST_DATABASE_URL to a disposable PostgreSQL database")
def test_postgres_databases_are_upgraded_in_place():
    import psycopg

    with psycopg.connect(POSTGRES_URL) as conn:
        conn.execute("DROP TABLE IF EXISTS password_resets, research_runs, users CASCADE")
        conn.execute(
            "CREATE TABLE users (id TEXT PRIMARY KEY, email TEXT NOT NULL UNIQUE, full_name TEXT NOT NULL,"
            " password_hash TEXT NOT NULL, created_at TEXT NOT NULL)"
        )
        conn.execute(
            "CREATE TABLE research_runs (thread_id TEXT PRIMARY KEY, user_id TEXT NOT NULL REFERENCES users(id),"
            " topic TEXT NOT NULL, created_at TEXT NOT NULL)"
        )
        conn.execute("INSERT INTO users VALUES ('u1', 'old@example.com', 'Old User', 'scrypt$x', '2026-01-01T00:00:00+00:00')")
        conn.execute("INSERT INTO research_runs VALUES ('t1', 'u1', 'an old topic', '2026-01-02T00:00:00+00:00')")
    UserStore(database_url=POSTGRES_URL, sqlite_path="unused")
    store = UserStore(database_url=POSTGRES_URL, sqlite_path="unused")  # idempotent
    assert store.get_by_email("old@example.com").token_version == 0
    run = store.list_runs("u1")[0]
    assert run["status"] == "planning" and run["updated_at"] == "2026-01-02T00:00:00+00:00"


def test_demo_account_is_seeded_once_and_can_log_in(tmp_path):
    options = dict(
        allow_registration=False, demo_email="Demo@Example.com", demo_password="showcase123", demo_name="Recruiter Demo"
    )
    client, _ = make_client(tmp_path, **options)
    with client:
        reply = client.post("/auth/login", json={"email": "demo@example.com", "password": "showcase123"})
        assert reply.status_code == 200 and reply.json()["user"]["full_name"] == "Recruiter Demo"
        denied = client.post("/auth/register", json={"full_name": "X Y", "email": "x@y.com", "password": "abcdefg1"})
        assert denied.status_code == 403
    # A restart against the same database must not fail or duplicate the account.
    again, _ = make_client(tmp_path, fresh_database=False, **options)
    with again:
        assert again.post("/auth/login", json={"email": "demo@example.com", "password": "showcase123"}).status_code == 200


def test_shared_demo_account_cannot_be_locked_or_deleted(tmp_path):
    client, _ = make_client(
        tmp_path, allow_registration=False, demo_email="demo@example.com", demo_password="showcase123"
    )
    with client:
        login = client.post("/auth/login", json={"email": "demo@example.com", "password": "showcase123"})
        headers = {"Authorization": f"Bearer {login.json()['access_token']}"}
        changed = client.post(
            "/auth/change-password", headers=headers, json={"current_password": "showcase123", "new_password": "hacked123"}
        )
        assert changed.status_code == 403 and "demo account" in changed.json()["detail"]
        deleted = client.post("/auth/delete-account", headers=headers, json={"password": "showcase123"})
        assert deleted.status_code == 403
        # Reset emails are never issued for it, and the original password still works.
        client.post("/auth/forgot-password", json={"email": "demo@example.com"})
        assert not client.app.state.mailer.sent
        assert client.post("/auth/login", json={"email": "demo@example.com", "password": "showcase123"}).status_code == 200
