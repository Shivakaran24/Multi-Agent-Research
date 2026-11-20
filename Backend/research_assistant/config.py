"""Environment-backed settings."""

from pathlib import Path
from typing import Literal
from urllib.parse import urlparse

from pydantic import Field, SecretStr, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

_BACKEND_DIR = Path(__file__).resolve().parent.parent
_ENV_FILE = _BACKEND_DIR / ".env"


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=_ENV_FILE,
        env_file_encoding="utf-8",
        extra="ignore",
        env_ignore_empty=True,
        hide_input_in_errors=True,
    )

    app_env: Literal["development", "production"] = "development"
    groq_api_key: SecretStr | None = None
    groq_model: str = "openai/gpt-oss-20b"
    groq_eval_model: str | None = None
    # Optional second Groq model used when the main one is rate limited (Groq limits are per model),
    # e.g. llama-3.3-70b-versatile. Empty disables the fallback.
    groq_fallback_model: str | None = None
    tavily_api_key: SecretStr | None = None
    # Optional legacy shared token (scripts/CI). Normal users sign in with an account instead.
    require_email_verification: bool = False
    research_api_token: SecretStr | None = Field(default=None, min_length=32)
    # Signs user session tokens. Required in production; in development a random per-process
    # key is used if unset, which simply means sessions end when the server restarts.
    auth_secret_key: SecretStr | None = Field(default=None, min_length=32)
    access_token_expire_minutes: int = Field(default=720, ge=5, le=10_080)
    # None = open in development, closed in production (create accounts with the
    # research-assistant-create-user command, or set true during controlled onboarding).
    allow_registration: bool | None = None
    # Comma-separated invite codes. When set, anyone with a valid code can register (even in
    # production), and nobody without one can. Remove a code to stop that batch of signups.
    invite_codes: str | None = None
    # Comma-separated emails allowed to open the admin page.
    admin_emails: str | None = None
    # Fair-use limits for signed-in users (0 disables the daily cap). They protect provider quota.
    max_runs_per_user_per_day: int = Field(default=20, ge=0, le=10_000)
    max_active_runs_per_user: int = Field(default=1, ge=1, le=8)
    # Model tokens a user may spend per rolling 24 hours (0 = unlimited). Protects the Groq quota.
    max_tokens_per_user_per_day: int = Field(default=0, ge=0, le=100_000_000)
    # Recurring research: each user may keep this many schedules; the scheduler wakes every N seconds.
    max_schedules_per_user: int = Field(default=2, ge=0, le=50)
    scheduler_enabled: bool = True
    scheduler_interval_seconds: int = Field(default=60, ge=5, le=3_600)
    # Password reset email. Without SMTP_HOST, development logs the link to the console and
    # production sends nothing (the sign-in page then hides "Forgot password").
    reset_token_ttl_minutes: int = Field(default=30, ge=5, le=1_440)
    app_base_url: str | None = None  # defaults to the first CORS origin
    # Optional showcase account created at startup (for hosts without a shell). Pair with
    # ALLOW_REGISTRATION=false so one capped login is the only way in.
    demo_email: str | None = None
    demo_password: SecretStr | None = None
    demo_name: str = "Demo Visitor"
    smtp_host: str | None = None
    smtp_port: int = Field(default=587, ge=1, le=65_535)
    smtp_username: str | None = None
    smtp_password: SecretStr | None = None
    smtp_from: str | None = None
    smtp_starttls: bool = True
    # SQLite file for accounts when DATABASE_URL (PostgreSQL) is not configured.
    auth_db_path: Path = _BACKEND_DIR / "data" / "users.db"
    database_url: SecretStr | None = None
    cors_origins: list[str] = Field(default_factory=lambda: ["http://localhost:5173"])
    allowed_hosts: list[str] = Field(default_factory=lambda: ["localhost", "127.0.0.1"])
    max_iterations: int = Field(default=1, ge=1, le=8)
    initial_questions: int = Field(default=4, ge=1, le=8)
    search_results_per_query: int = Field(default=4, ge=1, le=10)
    # Source filtering: only recent pages (day|week|month|year, empty = any age), never these
    # domains, and a ranking bonus for authoritative ones (.gov/.edu/.int plus this list).
    # Run a fact-check pass after writing; users can override per run.
    claim_check: bool = True
    # Verified-only mode: every source is opened and checked before it can be cited, and statements the
    # verified sources do not support are removed (not just marked). Turn off only for offline tests.
    verified_only: bool = True
    search_time_range: Literal["day", "week", "month", "year"] | None = None
    blocked_domains: list[str] = Field(
        default_factory=lambda: [
            "pinterest.com", "facebook.com", "instagram.com", "tiktok.com", "quora.com",
            "x.com", "twitter.com", "linkedin.com", "youtube.com", "alibaba.com", "aliexpress.com",
        ]
    )
    preferred_domains: list[str] = Field(
        default_factory=lambda: [
            "iea.org", "who.int", "worldbank.org", "oecd.org", "un.org", "nature.com", "science.org",
            "sciencedirect.com", "nih.gov", "arxiv.org", "reuters.com", "apnews.com", "bbc.com",
            "ft.com", "economist.com", "bloomberg.com", "mckinsey.com", "ember-climate.org",
        ]
    )
    groq_max_output_tokens: int = Field(default=3500, ge=128, le=8192)
    groq_summary_max_tokens: int = Field(default=1024, ge=128, le=4096)
    # How much of each search result the researcher reads (characters).
    source_excerpt_chars: int = Field(default=1200, ge=200, le=4000)
    # Conservative defaults suit Groq's 8k TPM on-demand tier; raise after checking limits.
    max_concurrent_runs: int = Field(default=1, ge=1, le=32)
    max_parallel_graph_tasks: int = Field(default=1, ge=1, le=8)

    @field_validator("research_api_token", "auth_secret_key", mode="before")
    @classmethod
    def blank_secret_is_unset(cls, value: object) -> object:
        return None if isinstance(value, str) and not value.strip() else value

    @field_validator("cors_origins")
    @classmethod
    def require_explicit_origins(cls, origins: list[str]) -> list[str]:
        if not origins or "*" in origins:
            raise ValueError("CORS_ORIGINS must list one or more explicit browser origins.")
        cleaned = [origin.rstrip("/") for origin in origins]
        for origin in cleaned:
            parsed = urlparse(origin)
            if (
                parsed.scheme not in {"http", "https"}
                or not parsed.netloc
                or parsed.path
                or parsed.query
                or parsed.fragment
                or parsed.username
                or parsed.password
            ):
                raise ValueError("CORS_ORIGINS values must be exact HTTP or HTTPS origins.")
        return cleaned

    @field_validator("allowed_hosts")
    @classmethod
    def require_explicit_hosts(cls, hosts: list[str]) -> list[str]:
        if not hosts or "*" in hosts or any(not host.strip() for host in hosts):
            raise ValueError("ALLOWED_HOSTS must list one or more explicit hostnames.")
        return [host.strip().lower() for host in hosts]

    @property
    def invite_code_list(self) -> list[str]:
        return [c.strip() for c in (self.invite_codes or "").split(",") if c.strip()]

    @property
    def invite_required(self) -> bool:
        return bool(self.invite_code_list)

    @property
    def admin_email_list(self) -> list[str]:
        return [e.strip().lower() for e in (self.admin_emails or "").split(",") if e.strip()]

    @property
    def registration_open(self) -> bool:
        if self.invite_required:
            return True
        if self.allow_registration is not None:
            return self.allow_registration
        return self.app_env != "production"

    @property
    def password_reset_available(self) -> bool:
        return bool(self.smtp_host) or self.app_env != "production"

    @property
    def email_verification_required(self) -> bool:
        """New accounts must confirm their email before researching (needs working mail)."""
        return self.require_email_verification and (bool(self.smtp_host) or self.app_env != "production")

    @property
    def reset_link_base(self) -> str:
        return (self.app_base_url or self.cors_origins[0]).rstrip("/")

    @model_validator(mode="after")
    def validate_production_settings(self) -> "Settings":
        if self.app_env == "production":
            required = {
                "GROQ_API_KEY": self.groq_api_key,
                "TAVILY_API_KEY": self.tavily_api_key,
                "AUTH_SECRET_KEY": self.auth_secret_key,
                "DATABASE_URL": self.database_url,
            }
            missing = [
                name for name, value in required.items()
                if value is None or not value.get_secret_value().strip()
            ]
            if missing:
                raise ValueError(f"Production configuration is missing: {', '.join(missing)}")
            if any(not origin.startswith("https://") for origin in self.cors_origins):
                raise ValueError("Production CORS_ORIGINS must use HTTPS.")
        return self

