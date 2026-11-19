"""Tests for accounts: hashing, tokens, storage, throttling, and request validation."""

import time

import pytest
from pydantic import ValidationError

from research_assistant.auth import (
    AttemptLimiter,
    EmailTaken,
    UserStore,
    create_access_token,
    hash_password,
    verify_access_token,
    verify_password,
)
from research_assistant.schemas import LoginRequest, RegisterRequest

SECRET = "x" * 40


@pytest.fixture()
def store(tmp_path):
    return UserStore(database_url=None, sqlite_path=tmp_path / "data" / "users.db")


def test_password_hash_roundtrip_and_salting():
    first, second = hash_password("correct horse 9"), hash_password("correct horse 9")
    assert first != second  # unique salts
    assert verify_password("correct horse 9", first)
    assert not verify_password("wrong horse 9", first)
    assert not verify_password("anything", "not-a-valid-hash")


def test_token_roundtrip_expiry_and_tampering():
    token = create_access_token("user-1", SECRET, 60)
    assert verify_access_token(token, SECRET)["sub"] == "user-1"
    assert verify_access_token(token, "y" * 40) is None  # wrong key
    head, payload, sig = token.split(".")
    assert verify_access_token(f"{head}.{payload}x.{sig}", SECRET) is None  # tampered
    assert verify_access_token(create_access_token("u", SECRET, -5), SECRET) is None  # expired
    assert verify_access_token("garbage", SECRET) is None
    assert verify_access_token("", SECRET) is None


def test_alg_none_is_rejected():
    import base64
    import json

    def enc(obj):
        return base64.urlsafe_b64encode(json.dumps(obj).encode()).rstrip(b"=").decode()

    forged = f"{enc({'alg': 'none', 'typ': 'JWT'})}.{enc({'sub': 'u', 'exp': int(time.time()) + 999})}."
    assert verify_access_token(forged, SECRET) is None


def test_store_create_lookup_and_duplicate_email(store):
    user = store.create_user(email="a@example.com", full_name="Ada", password_hash="h")
    assert store.get_by_email("a@example.com").id == user.id
    assert store.get_by_id(user.id).full_name == "Ada"
    assert store.get_by_email("missing@example.com") is None
    with pytest.raises(EmailTaken):
        store.create_user(email="a@example.com", full_name="Other", password_hash="h2")


def test_run_ownership(store):
    alice = store.create_user(email="a@example.com", full_name="Alice", password_hash="h")
    bob = store.create_user(email="b@example.com", full_name="Bob", password_hash="h")
    store.record_run("thread-1", alice.id, "quantum computing")
    assert store.owns_run("thread-1", alice.id)
    assert not store.owns_run("thread-1", bob.id)
    assert not store.owns_run("unknown", alice.id)
    with pytest.raises(PermissionError):
        store.record_run("thread-2", "no-such-user", "topic")


def test_attempt_limiter_blocks_then_clears():
    limiter = AttemptLimiter(limit=3, window_seconds=60)
    for _ in range(3):
        assert limiter.retry_after("k") == 0
        limiter.record("k")
    assert limiter.retry_after("k") > 0
    assert limiter.retry_after("other") == 0
    limiter.clear("k")
    assert limiter.retry_after("k") == 0


def test_register_request_normalizes_and_validates():
    ok = RegisterRequest(full_name="  Ada   Lovelace ", email="  Ada@Example.COM ", password="abcdefg1")
    assert ok.full_name == "Ada Lovelace"
    assert ok.email == "ada@example.com"
    for bad in (
        {"email": "not-an-email"},
        {"email": "a@b"},
        {"password": "short1"},
        {"password": "onlyletters"},
        {"password": "12345678"},
        {"full_name": " a "},
    ):
        payload = {"full_name": "Ada", "email": "ada@example.com", "password": "abcdefg1", **bad}
        with pytest.raises(ValidationError):
            RegisterRequest(**payload)


def test_login_request_normalizes_email():
    assert LoginRequest(email="  Ada@Example.com ", password="x").email == "ada@example.com"
