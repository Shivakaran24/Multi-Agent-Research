"""User accounts: password hashing, signed session tokens, storage, mail, and /auth routes.

Dependency-light on purpose: hashing uses stdlib scrypt, tokens are HS256 JWTs signed with
stdlib hmac, mail uses smtplib, and storage is SQLite (development) or PostgreSQL via psycopg
(production, whenever DATABASE_URL is configured). The same store also keeps each user's
research history so reports can be reopened later.
"""

from __future__ import annotations

import argparse
import asyncio
import base64
import binascii
import getpass
import hashlib
import hmac
import json
import logging
import os
import secrets
import smtplib
import sqlite3
import ssl
import sys
import threading
import time
import uuid
from collections import defaultdict, deque
from collections.abc import Awaitable
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from email.message import EmailMessage
from pathlib import Path
from typing import Any, Callable, Iterator, Protocol

from fastapi import APIRouter, BackgroundTasks, HTTPException, Request, Response

from research_assistant.config import Settings
from research_assistant.schemas import (
    AuthConfig,
    AuthResponse,
    ChangePasswordRequest,
    DeleteAccountRequest,
    ForgotPasswordRequest,
    LoginRequest,
    MessageResponse,
    RegisterRequest,
    ResetPasswordRequest,
    UserPublic,
)

logger = logging.getLogger(__name__)


def _now() -> str:
    # Microsecond precision keeps "newest first" ordering deterministic for runs started in the same second.
    return datetime.now(timezone.utc).isoformat(timespec="microseconds")


# --------------------------------------------------------------------------- passwords

_SCRYPT_N, _SCRYPT_R, _SCRYPT_P = 2**14, 8, 1


def hash_password(password: str) -> str:
    salt = secrets.token_bytes(16)
    digest = hashlib.scrypt(
        password.encode(), salt=salt, n=_SCRYPT_N, r=_SCRYPT_R, p=_SCRYPT_P, dklen=32
    )
    return "scrypt${}${}${}${}${}".format(
        _SCRYPT_N, _SCRYPT_R, _SCRYPT_P,
        base64.b64encode(salt).decode(), base64.b64encode(digest).decode(),
    )


def verify_password(password: str, stored: str) -> bool:
    try:
        scheme, n, r, p, salt_b64, digest_b64 = stored.split("$")
        if scheme != "scrypt":
            return False
        salt, expected = base64.b64decode(salt_b64), base64.b64decode(digest_b64)
        actual = hashlib.scrypt(
            password.encode(), salt=salt, n=int(n), r=int(r), p=int(p), dklen=len(expected)
        )
    except (ValueError, binascii.Error):
        return False
    return hmac.compare_digest(actual, expected)


# A real hash to burn comparable time when the email is unknown (blunts user enumeration).
_DUMMY_HASH = hash_password(secrets.token_urlsafe(16))

# --------------------------------------------------------------------------- tokens


def _b64url(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode()


def _b64url_decode(text: str) -> bytes:
    return base64.urlsafe_b64decode(text + "=" * (-len(text) % 4))


def create_access_token(user_id: str, secret: str, expires_in: int, version: int = 0) -> str:
    """`version` is the user's token_version; bumping it (password change/reset) revokes old tokens."""
    now = int(time.time())
    header = _b64url(json.dumps({"alg": "HS256", "typ": "JWT"}, separators=(",", ":")).encode())
    payload = _b64url(
        json.dumps(
            {"sub": user_id, "ver": version, "iat": now, "exp": now + expires_in}, separators=(",", ":")
        ).encode()
    )
    signature = hmac.new(secret.encode(), f"{header}.{payload}".encode(), hashlib.sha256).digest()
    return f"{header}.{payload}.{_b64url(signature)}"


def verify_access_token(token: str, secret: str) -> dict[str, Any] | None:
    """Return the claims for a correctly signed, unexpired token, otherwise None."""
    try:
        header_b64, payload_b64, signature_b64 = token.split(".")
        header = json.loads(_b64url_decode(header_b64))
        if header.get("alg") != "HS256":
            return None
        expected = hmac.new(secret.encode(), f"{header_b64}.{payload_b64}".encode(), hashlib.sha256).digest()
        if not hmac.compare_digest(expected, _b64url_decode(signature_b64)):
            return None
        claims = json.loads(_b64url_decode(payload_b64))
        if not isinstance(claims, dict) or not isinstance(claims.get("sub"), str):
            return None
        if int(claims.get("exp", 0)) <= time.time():
            return None
        int(claims.get("ver", 0))
        return claims
    except (ValueError, TypeError, binascii.Error, json.JSONDecodeError):
        return None


_EPHEMERAL_SECRET = secrets.token_urlsafe(48)


def signing_secret(settings: Settings) -> str:
    if settings.auth_secret_key is not None:
        return settings.auth_secret_key.get_secret_value()
    return _EPHEMERAL_SECRET


def bearer_token(request: Request) -> str:
    supplied = request.headers.get("authorization", "")
    return supplied[7:].strip() if supplied.lower().startswith("bearer ") else ""


# --------------------------------------------------------------------------- storage


class EmailTaken(Exception):
    pass


@dataclass(frozen=True)
class User:
    id: str
    email: str
    full_name: str
    password_hash: str
    created_at: str
    token_version: int = 0

    def public(self) -> UserPublic:
        return UserPublic(id=self.id, email=self.email, full_name=self.full_name, created_at=self.created_at)


_SCHEMA = (
    """CREATE TABLE IF NOT EXISTS users (
        id TEXT PRIMARY KEY,
        email TEXT NOT NULL UNIQUE,
        full_name TEXT NOT NULL,
        password_hash TEXT NOT NULL,
        created_at TEXT NOT NULL
    )""",
    """CREATE TABLE IF NOT EXISTS research_runs (
        thread_id TEXT PRIMARY KEY,
        user_id TEXT NOT NULL REFERENCES users(id),
        topic TEXT NOT NULL,
        created_at TEXT NOT NULL
    )""",
    """CREATE TABLE IF NOT EXISTS password_resets (
        token_hash TEXT PRIMARY KEY,
        user_id TEXT NOT NULL REFERENCES users(id),
        expires_at TEXT NOT NULL,
        used INTEGER NOT NULL DEFAULT 0
    )""",
)

# Columns added after the first release. Applied idempotently so existing databases upgrade in place.
_MIGRATIONS = (
    ("users", "token_version", "INTEGER NOT NULL DEFAULT 0"),
    ("research_runs", "status", "TEXT NOT NULL DEFAULT 'planning'"),
    ("research_runs", "title", "TEXT"),
    ("research_runs", "plan", "TEXT"),
    ("research_runs", "report", "TEXT"),
    ("research_runs", "updated_at", "TEXT"),
)

_USER_COLUMNS = "id, email, full_name, password_hash, created_at, token_version"
_RUN_COLUMNS = "thread_id, user_id, topic, title, status, created_at, COALESCE(updated_at, created_at), plan, report"


class UserStore:
    """Synchronous store; call from threads (FastAPI sync routes or asyncio.to_thread)."""

    def __init__(self, *, database_url: str | None, sqlite_path: Path) -> None:
        self._url = database_url
        self._path = sqlite_path
        self._postgres = database_url is not None
        self._ph = "%s" if self._postgres else "?"
        self._integrity: tuple[type[BaseException], ...] = (sqlite3.IntegrityError,)
        if self._postgres:
            import psycopg  # type: ignore[import-not-found]

            self._integrity = (psycopg.errors.IntegrityError,)
        else:
            self._path.parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as conn:
            for statement in _SCHEMA:
                conn.execute(statement)
            self._migrate(conn)

    def _migrate(self, conn: Any) -> None:
        for table, column, ddl in _MIGRATIONS:
            if self._postgres:
                conn.execute(f"ALTER TABLE {table} ADD COLUMN IF NOT EXISTS {column} {ddl}")
            else:
                existing = {row[1] for row in conn.execute(f"PRAGMA table_info({table})").fetchall()}
                if column not in existing:
                    conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {ddl}")

    @contextmanager
    def _connect(self) -> Iterator[Any]:
        if self._postgres:
            import psycopg  # type: ignore[import-not-found]

            with psycopg.connect(self._url) as conn:  # commits on clean exit, rolls back on error
                yield conn
            return
        conn = sqlite3.connect(self._path, timeout=10)
        try:
            conn.execute("PRAGMA foreign_keys = ON")
            with conn:  # commit / rollback
                yield conn
        finally:
            conn.close()

    def _sql(self, query: str) -> str:
        return query.replace("?", self._ph)

    @staticmethod
    def _user(row: Any) -> User | None:
        return None if row is None else User(*row)

    # ---- users

    def create_user(self, *, email: str, full_name: str, password_hash: str) -> User:
        user = User(
            id=str(uuid.uuid4()), email=email, full_name=full_name, password_hash=password_hash, created_at=_now()
        )
        try:
            with self._connect() as conn:
                conn.execute(
                    self._sql("INSERT INTO users (id, email, full_name, password_hash, created_at) VALUES (?, ?, ?, ?, ?)"),
                    (user.id, user.email, user.full_name, user.password_hash, user.created_at),
                )
        except self._integrity as exc:
            raise EmailTaken(email) from exc
        return user

    def get_by_email(self, email: str) -> User | None:
        with self._connect() as conn:
            row = conn.execute(self._sql(f"SELECT {_USER_COLUMNS} FROM users WHERE email = ?"), (email,)).fetchone()
        return self._user(row)

    def get_by_id(self, user_id: str) -> User | None:
        with self._connect() as conn:
            row = conn.execute(self._sql(f"SELECT {_USER_COLUMNS} FROM users WHERE id = ?"), (user_id,)).fetchone()
        return self._user(row)

    def check_session(self, user_id: str, version: int) -> bool:
        """True when the account still exists and the token has not been revoked."""
        with self._connect() as conn:
            row = conn.execute(self._sql("SELECT token_version FROM users WHERE id = ?"), (user_id,)).fetchone()
        return row is not None and int(row[0]) == version

    def set_password(self, user_id: str, password_hash: str) -> User | None:
        """Change the password and revoke every previously issued session token."""
        with self._connect() as conn:
            conn.execute(
                self._sql("UPDATE users SET password_hash = ?, token_version = token_version + 1 WHERE id = ?"),
                (password_hash, user_id),
            )
            conn.execute(self._sql("DELETE FROM password_resets WHERE user_id = ?"), (user_id,))
            row = conn.execute(self._sql(f"SELECT {_USER_COLUMNS} FROM users WHERE id = ?"), (user_id,)).fetchone()
        return self._user(row)

    def delete_user(self, user_id: str) -> list[str]:
        """Remove the account and all its data. Returns the run thread ids so checkpoints can be purged."""
        with self._connect() as conn:
            threads = [
                row[0]
                for row in conn.execute(
                    self._sql("SELECT thread_id FROM research_runs WHERE user_id = ?"), (user_id,)
                ).fetchall()
            ]
            conn.execute(self._sql("DELETE FROM password_resets WHERE user_id = ?"), (user_id,))
            conn.execute(self._sql("DELETE FROM research_runs WHERE user_id = ?"), (user_id,))
            conn.execute(self._sql("DELETE FROM users WHERE id = ?"), (user_id,))
        return threads

    # ---- password reset

    @staticmethod
    def _hash_token(token: str) -> str:
        return hashlib.sha256(token.encode()).hexdigest()

    def create_reset_token(self, user_id: str, ttl_minutes: int) -> str:
        """Issue a single-use token (only its hash is stored); replaces any earlier one."""
        token = secrets.token_urlsafe(32)
        expires = (datetime.now(timezone.utc) + timedelta(minutes=ttl_minutes)).isoformat(timespec="microseconds")
        with self._connect() as conn:
            conn.execute(self._sql("DELETE FROM password_resets WHERE user_id = ?"), (user_id,))
            conn.execute(
                self._sql("INSERT INTO password_resets (token_hash, user_id, expires_at, used) VALUES (?, ?, ?, 0)"),
                (self._hash_token(token), user_id, expires),
            )
        return token

    def consume_reset_token(self, token: str) -> str | None:
        """Atomically mark a valid, unused, unexpired token as used and return its user id."""
        digest = self._hash_token(token)
        with self._connect() as conn:
            cursor = conn.execute(
                self._sql("UPDATE password_resets SET used = 1 WHERE token_hash = ? AND used = 0 AND expires_at > ?"),
                (digest, _now()),
            )
            if cursor.rowcount != 1:
                return None
            row = conn.execute(
                self._sql("SELECT user_id FROM password_resets WHERE token_hash = ?"), (digest,)
            ).fetchone()
        return row[0] if row else None

    # ---- research runs / history

    def record_run(self, thread_id: str, user_id: str, topic: str) -> None:
        now = _now()
        try:
            with self._connect() as conn:
                conn.execute(
                    self._sql(
                        "INSERT INTO research_runs (thread_id, user_id, topic, created_at, status, updated_at) "
                        "VALUES (?, ?, ?, ?, 'planning', ?)"
                    ),
                    (thread_id, user_id, topic[:2_000], now, now),
                )
        except self._integrity:
            # Unknown user (e.g. deleted account with a still-valid token): treat as unauthorized.
            raise PermissionError("Unknown user.") from None

    def update_run(
        self,
        thread_id: str,
        *,
        status: str | None = None,
        plan: list[dict[str, Any]] | None = None,
        report: str | None = None,
        title: str | None = None,
    ) -> None:
        sets, values = ["updated_at = ?"], [_now()]
        for column, value in (("status", status), ("title", title), ("report", report)):
            if value is not None:
                sets.append(f"{column} = ?")
                values.append(value)
        if plan is not None:
            sets.append("plan = ?")
            values.append(json.dumps(plan, ensure_ascii=False))
        with self._connect() as conn:
            conn.execute(
                self._sql(f"UPDATE research_runs SET {', '.join(sets)} WHERE thread_id = ?"), (*values, thread_id)
            )

    @staticmethod
    def _run(row: Any, *, detail: bool) -> dict[str, Any]:
        thread_id, user_id, topic, title, status, created_at, updated_at, plan, report = row
        data: dict[str, Any] = {
            "thread_id": thread_id, "user_id": user_id, "topic": topic, "title": title,
            "status": status, "created_at": created_at, "updated_at": updated_at,
        }
        if detail:
            data["plan"] = json.loads(plan) if plan else None
            data["report"] = report
        return data

    def list_runs(self, user_id: str, limit: int = 50) -> list[dict[str, Any]]:
        with self._connect() as conn:
            rows = conn.execute(
                self._sql(
                    f"SELECT {_RUN_COLUMNS} FROM research_runs WHERE user_id = ? "
                    "ORDER BY created_at DESC, thread_id LIMIT ?"
                ),
                (user_id, limit),
            ).fetchall()
        return [self._run(row, detail=False) for row in rows]

    def get_run(self, thread_id: str, user_id: str | None) -> dict[str, Any] | None:
        """Fetch one run. `user_id=None` skips the ownership filter (legacy service token)."""
        query = f"SELECT {_RUN_COLUMNS} FROM research_runs WHERE thread_id = ?"
        params: tuple[Any, ...] = (thread_id,)
        if user_id is not None:
            query += " AND user_id = ?"
            params += (user_id,)
        with self._connect() as conn:
            row = conn.execute(self._sql(query), params).fetchone()
        return None if row is None else self._run(row, detail=True)

    def delete_run(self, thread_id: str, user_id: str | None) -> bool:
        query = "DELETE FROM research_runs WHERE thread_id = ?"
        params: tuple[Any, ...] = (thread_id,)
        if user_id is not None:
            query += " AND user_id = ?"
            params += (user_id,)
        with self._connect() as conn:
            return conn.execute(self._sql(query), params).rowcount == 1

    def owns_run(self, thread_id: str, user_id: str) -> bool:
        with self._connect() as conn:
            row = conn.execute(
                self._sql("SELECT 1 FROM research_runs WHERE thread_id = ? AND user_id = ?"), (thread_id, user_id)
            ).fetchone()
        return row is not None

    def count_runs_since(self, user_id: str, since_iso: str) -> int:
        with self._connect() as conn:
            row = conn.execute(
                self._sql("SELECT COUNT(*) FROM research_runs WHERE user_id = ? AND created_at >= ?"),
                (user_id, since_iso),
            ).fetchone()
        return int(row[0])


def store_from_settings(settings: Settings) -> UserStore:
    return UserStore(
        database_url=settings.database_url.get_secret_value() if settings.database_url else None,
        sqlite_path=settings.auth_db_path,
    )


# --------------------------------------------------------------------------- mail


class Mailer(Protocol):
    def send(self, to: str, subject: str, body: str) -> None: ...


class SmtpMailer:
    def __init__(self, settings: Settings) -> None:
        self._host, self._port = settings.smtp_host or "", settings.smtp_port
        self._username = settings.smtp_username
        self._password = settings.smtp_password.get_secret_value() if settings.smtp_password else None
        self._from = settings.smtp_from or settings.smtp_username or "no-reply@localhost"
        self._starttls = settings.smtp_starttls

    def send(self, to: str, subject: str, body: str) -> None:
        message = EmailMessage()
        message["From"], message["To"], message["Subject"] = self._from, to, subject
        message.set_content(body)
        context = ssl.create_default_context()
        if self._port == 465:
            client: smtplib.SMTP = smtplib.SMTP_SSL(self._host, self._port, timeout=15, context=context)
        else:
            client = smtplib.SMTP(self._host, self._port, timeout=15)
        with client:
            if self._port != 465 and self._starttls:
                client.starttls(context=context)
            if self._username and self._password:
                client.login(self._username, self._password)
            client.send_message(message)


class LogMailer:
    """Development fallback: prints the message (including the reset link) to the API console."""

    def send(self, to: str, subject: str, body: str) -> None:
        logger.warning("Email to %s (SMTP not configured; development only)\nSubject: %s\n%s", to, subject, body)


class NullMailer:
    """Production without SMTP: never logs the secret link, just records that nothing was sent."""

    def send(self, to: str, subject: str, body: str) -> None:
        logger.error("SMTP is not configured; the %r email to %s was not sent.", subject, to)


def create_mailer(settings: Settings) -> Mailer:
    if settings.smtp_host:
        return SmtpMailer(settings)
    return NullMailer() if settings.app_env == "production" else LogMailer()


# --------------------------------------------------------------------------- throttling


class AttemptLimiter:
    """In-memory sliding window. Per-process, which matches the single-worker deployment."""

    def __init__(self, limit: int, window_seconds: int) -> None:
        self.limit, self.window = limit, window_seconds
        self._events: dict[str, deque[float]] = defaultdict(deque)
        self._lock = threading.Lock()

    def _prune(self, key: str, now: float) -> deque[float]:
        events = self._events[key]
        while events and now - events[0] > self.window:
            events.popleft()
        if not events:
            self._events.pop(key, None)
        return events

    def retry_after(self, key: str) -> int:
        """Seconds to wait if the key is over its limit, else 0."""
        now = time.monotonic()
        with self._lock:
            events = self._prune(key, now)
            if len(events) >= self.limit:
                return max(1, int(self.window - (now - events[0])))
        return 0

    def record(self, key: str) -> None:
        with self._lock:
            self._events[key].append(time.monotonic())

    def clear(self, key: str) -> None:
        with self._lock:
            self._events.pop(key, None)


# --------------------------------------------------------------------------- routes


def create_auth_router(
    *,
    get_settings: Callable[[], Settings],
    get_store: Callable[[], UserStore],
    get_mailer: Callable[[], Mailer] | None = None,
    on_account_deleted: Callable[[list[str]], Awaitable[None]] | None = None,
) -> APIRouter:
    router = APIRouter(prefix="/auth", tags=["auth"])
    login_limiter = AttemptLimiter(limit=5, window_seconds=15 * 60)
    register_limiter = AttemptLimiter(limit=10, window_seconds=60 * 60)
    forgot_limiter = AttemptLimiter(limit=5, window_seconds=60 * 60)
    password_limiter = AttemptLimiter(limit=5, window_seconds=15 * 60)

    def issue(user: User, settings: Settings) -> AuthResponse:
        expires_in = settings.access_token_expire_minutes * 60
        return AuthResponse(
            access_token=create_access_token(user.id, signing_secret(settings), expires_in, user.token_version),
            expires_in=expires_in,
            user=user.public(),
        )

    def client_ip(request: Request) -> str:
        return request.client.host if request.client else "unknown"

    def too_many(wait: int) -> HTTPException:
        return HTTPException(
            status_code=429,
            detail=f"Too many attempts. Try again in {max(1, wait // 60 + (1 if wait % 60 else 0))} minute(s).",
            headers={"Retry-After": str(wait)},
        )

    def current_user(request: Request) -> User:
        claims = verify_access_token(bearer_token(request), signing_secret(get_settings()))
        user = get_store().get_by_id(claims["sub"]) if claims else None
        if user is None or user.token_version != int(claims.get("ver", 0)):
            raise HTTPException(status_code=401, detail="Your session has expired. Please sign in again.")
        return user

    def confirm_password(user: User, password: str) -> None:
        key = f"pw|{user.id}"
        if wait := password_limiter.retry_after(key):
            raise too_many(wait)
        if not verify_password(password, user.password_hash):
            password_limiter.record(key)
            raise HTTPException(status_code=401, detail="Incorrect password.")
        password_limiter.clear(key)

    @router.get("/config", response_model=AuthConfig)
    def auth_config() -> AuthConfig:
        settings = get_settings()
        return AuthConfig(
            registration_open=settings.registration_open,
            password_reset_available=settings.password_reset_available,
        )

    @router.post("/register", status_code=201, response_model=AuthResponse)
    def register(body: RegisterRequest, request: Request) -> AuthResponse:
        settings = get_settings()
        if not settings.registration_open:
            raise HTTPException(status_code=403, detail="Registration is currently disabled.")
        ip = client_ip(request)
        if wait := register_limiter.retry_after(ip):
            raise too_many(wait)
        register_limiter.record(ip)
        try:
            user = get_store().create_user(
                email=body.email, full_name=body.full_name, password_hash=hash_password(body.password)
            )
        except EmailTaken:
            raise HTTPException(status_code=409, detail="An account with this email already exists.") from None
        return issue(user, settings)

    @router.post("/login", response_model=AuthResponse)
    def login(body: LoginRequest, request: Request) -> AuthResponse:
        settings = get_settings()
        key = f"{client_ip(request)}|{body.email}"
        if wait := login_limiter.retry_after(key):
            raise too_many(wait)
        user = get_store().get_by_email(body.email)
        valid = verify_password(body.password, user.password_hash if user else _DUMMY_HASH)
        if user is None or not valid:
            login_limiter.record(key)
            raise HTTPException(status_code=401, detail="Incorrect email or password.")
        login_limiter.clear(key)
        return issue(user, settings)

    @router.get("/me", response_model=UserPublic)
    def me(request: Request) -> UserPublic:
        return current_user(request).public()

    @router.post("/change-password", response_model=AuthResponse)
    def change_password(body: ChangePasswordRequest, request: Request) -> AuthResponse:
        """Sets a new password and returns a fresh session; every other device is signed out."""
        user = current_user(request)
        confirm_password(user, body.current_password)
        if body.new_password == body.current_password:
            raise HTTPException(status_code=422, detail="Choose a password different from your current one.")
        updated = get_store().set_password(user.id, hash_password(body.new_password))
        if updated is None:
            raise HTTPException(status_code=401, detail="Your session has expired. Please sign in again.")
        return issue(updated, get_settings())

    @router.post("/forgot-password", status_code=202, response_model=MessageResponse)
    def forgot_password(body: ForgotPasswordRequest, request: Request, background: BackgroundTasks) -> MessageResponse:
        """Always answers the same way so the endpoint cannot be used to discover who has an account."""
        settings = get_settings()
        reply = MessageResponse(detail="If an account exists for that email, a reset link is on its way.")
        if not settings.password_reset_available:
            raise HTTPException(status_code=503, detail="Password reset is not available on this server.")
        ip = client_ip(request)
        if wait := forgot_limiter.retry_after(ip):
            raise too_many(wait)
        forgot_limiter.record(ip)
        user = get_store().get_by_email(body.email)
        if user is not None:
            token = get_store().create_reset_token(user.id, settings.reset_token_ttl_minutes)
            link = f"{settings.reset_link_base}/#/reset?token={token}"
            mailer = get_mailer() if get_mailer else create_mailer(settings)

            def deliver() -> None:
                try:
                    mailer.send(
                        user.email,
                        "Reset your Fieldwork password",
                        f"Hi {user.full_name},\n\nUse this link to choose a new password. It works once and expires in "
                        f"{settings.reset_token_ttl_minutes} minutes:\n\n{link}\n\n"
                        "If you did not ask for this, you can ignore this email; your password has not changed.\n",
                    )
                except Exception:  # never surface mail failures to the (unauthenticated) caller
                    logger.exception("Could not send password reset email")

            background.add_task(deliver)
        return reply

    @router.post("/reset-password", response_model=MessageResponse)
    def reset_password(body: ResetPasswordRequest, request: Request) -> MessageResponse:
        ip = f"reset|{client_ip(request)}"
        if wait := login_limiter.retry_after(ip):
            raise too_many(wait)
        user_id = get_store().consume_reset_token(body.token)
        if user_id is None:
            login_limiter.record(ip)
            raise HTTPException(status_code=400, detail="This reset link is invalid or has expired. Request a new one.")
        get_store().set_password(user_id, hash_password(body.new_password))
        return MessageResponse(detail="Password updated. Sign in with your new password.")

    @router.post("/delete-account", status_code=204)
    async def delete_account(body: DeleteAccountRequest, request: Request) -> Response:
        def work() -> list[str]:
            user = current_user(request)
            confirm_password(user, body.password)
            return get_store().delete_user(user.id)

        thread_ids = await asyncio.to_thread(work)
        if on_account_deleted is not None:
            try:
                await on_account_deleted(thread_ids)
            except Exception:
                logger.exception("Could not purge research checkpoints for a deleted account")
        return Response(status_code=204)

    return router


# --------------------------------------------------------------------------- command line


def ensure_demo_user(store: UserStore, settings: Settings) -> bool:
    """Create the configured demo account if missing. Returns True when it was created.

    Never changes an existing account, so rotating DEMO_PASSWORD later has no effect.
    """
    if not settings.demo_email or not settings.demo_password:
        return False
    request = RegisterRequest(
        full_name=settings.demo_name, email=settings.demo_email, password=settings.demo_password.get_secret_value()
    )
    if store.get_by_email(request.email) is not None:
        return False
    try:
        store.create_user(email=request.email, full_name=request.full_name, password_hash=hash_password(request.password))
    except EmailTaken:  # created concurrently by another process
        return False
    return True


def create_user_cli(argv: list[str] | None = None) -> int:
    """`research-assistant-create-user --email you@example.com --name "Your Name"`.

    Lets operators onboard people while public registration is closed. The password is read
    from CREATE_USER_PASSWORD, or prompted for interactively (never passed on the command line).
    """
    parser = argparse.ArgumentParser(prog="research-assistant-create-user", description=create_user_cli.__doc__)
    parser.add_argument("--email", required=True)
    parser.add_argument("--name", required=True, help="Full name")
    args = parser.parse_args(argv)
    password = os.environ.get("CREATE_USER_PASSWORD") or getpass.getpass("Password: ")
    try:
        request = RegisterRequest(full_name=args.name, email=args.email, password=password)
    except ValueError as exc:
        print(f"Invalid details: {exc}", file=sys.stderr)
        return 2
    store = store_from_settings(Settings())
    try:
        user = store.create_user(
            email=request.email, full_name=request.full_name, password_hash=hash_password(request.password)
        )
    except EmailTaken:
        print("An account with that email already exists.", file=sys.stderr)
        return 1
    print(f"Created account {user.email} ({user.id}).")
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(create_user_cli())
