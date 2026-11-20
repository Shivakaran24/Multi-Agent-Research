"""Validated payloads passed between the research agents, plus account/auth payloads."""

import re
from typing import Literal

from pydantic import BaseModel, Field, field_validator, model_validator

# Deliberately dependency-free (no email-validator package needed).
_EMAIL_RE = re.compile(
    r"^[A-Za-z0-9.!#$%&'*+/=?^_`{|}~-]+@"
    r"[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?"
    r"(?:\.[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?)+$"
)


def normalize_email(value: str) -> str:
    value = value.strip().lower()
    if len(value) > 254 or not _EMAIL_RE.fullmatch(value):
        raise ValueError("Enter a valid email address.")
    return value


def fit_text(value: str, limit: int) -> str:
    """Trim over-long model output to `limit` instead of failing the whole run.

    LLMs cannot count characters reliably, so a hard length error on free text turns a harmless
    overshoot into a failed research run. Prefers a sentence boundary, then a word boundary.
    """
    value = value.strip()
    if len(value) <= limit:
        return value
    cut = value[:limit]
    boundary = max(cut.rfind(". "), cut.rfind(".\n"), cut.rfind("\n\n"))
    if boundary >= limit * 0.75:
        return cut[: boundary + 1].rstrip()
    space = cut.rfind(" ")
    return (cut[:space] if space >= limit * 0.6 else cut[: limit - 1]).rstrip(" ,;:-") + "…"


class ResearchQuestion(BaseModel):
    question: str = Field(description="Focused research question")
    rationale: str = Field(description="Why this question matters")

    @field_validator("question")
    @classmethod
    def bound_question(cls, value: str) -> str:
        value = fit_text(value, 240)
        if len(value) < 8:
            raise ValueError("Question must be at least 8 characters.")
        return value

    @field_validator("rationale")
    @classmethod
    def bound_rationale(cls, value: str) -> str:
        return fit_text(value, 240)


class ResearchPlan(BaseModel):
    questions: list[ResearchQuestion]

    @field_validator("questions")
    @classmethod
    def bound_questions(cls, value: list[ResearchQuestion]) -> list[ResearchQuestion]:
        if not 1 <= len(value) <= 8:
            raise ValueError("A research plan must include between 1 and 8 questions.")
        return value


_LANGUAGE_RE = re.compile(r"^[A-Za-z][A-Za-z .()'-]{1,39}$")


class UserDocument(BaseModel):
    """Text extracted from a PDF the user uploaded (see POST /research/uploads/pdf)."""

    name: str = Field(min_length=1, max_length=120)
    text: str = Field(min_length=1, max_length=30_000)

    @field_validator("name")
    @classmethod
    def clean_name(cls, value: str) -> str:
        value = re.sub(r"[^\w .()\-]", "", value.strip())
        if not value:
            raise ValueError("Document name is not valid.")
        return value


class ResearchOptions(BaseModel):
    """Per-run choices that shape how a brief is researched and written."""

    style: Literal["brief", "executive", "pros_cons", "comparison"] = "brief"
    audience: Literal["general", "executive", "technical", "student"] = "general"
    length: Literal["short", "standard", "long"] = "standard"
    language: str = "English"
    time_range: Literal["day", "week", "month", "year"] | None = None
    urls: list[str] = Field(default_factory=list, max_length=5)
    notes: str = Field(default="", max_length=6_000)
    documents: list[UserDocument] = Field(default_factory=list, max_length=3)
    verify_claims: bool | None = None  # None = use the server default

    @field_validator("language")
    @classmethod
    def clean_language(cls, value: str) -> str:
        value = value.strip() or "English"
        if not _LANGUAGE_RE.match(value):
            raise ValueError("Language must be a name such as English, Spanish or Hindi.")
        return value

    @field_validator("urls")
    @classmethod
    def clean_urls(cls, values: list[str]) -> list[str]:
        cleaned: list[str] = []
        for raw in values:
            url = raw.strip()
            if not url:
                continue
            if len(url) > 500 or not re.match(r"^https?://[^\s]+$", url, re.I):
                raise ValueError("Each link must start with http:// or https://.")
            if url not in cleaned:
                cleaned.append(url)
        return cleaned

    @field_validator("notes")
    @classmethod
    def clean_notes(cls, value: str) -> str:
        return value.strip()


class ClaimCheck(BaseModel):
    """Quotes from a brief that none of its sources support."""

    unsupported: list[str] = Field(default_factory=list, description="Exact short quotes from the brief")

    @field_validator("unsupported")
    @classmethod
    def bound_claims(cls, values: list[str]) -> list[str]:
        return [fit_text(v, 300) for v in values if isinstance(v, str) and v.strip()][:8]


class Source(BaseModel):
    title: str
    url: str
    snippet: str = ""
    content: str = ""


class ResearchFinding(BaseModel):
    query: str
    summary: str
    sources: list[Source] = Field(default_factory=list)


class ResearchSummary(BaseModel):
    summary: str = Field(description="Concise evidence-based answer")

    @field_validator("summary")
    @classmethod
    def bound_summary(cls, value: str) -> str:
        return fit_text(value, 2_000)


class ReviewDecision(BaseModel):
    sufficient: bool = Field(description="Whether the evidence is adequate to answer the request")
    gaps: list[ResearchQuestion] = Field(default_factory=list)
    rationale: str

    @model_validator(mode="after")
    def bound_review(self) -> "ReviewDecision":
        self.gaps = self.gaps[:8]
        self.rationale = fit_text(self.rationale, 300)
        return self


class FinalReport(BaseModel):
    title: str
    markdown: str = Field(description="Concise report body with inline numbered citations")

    @model_validator(mode="after")
    def bound_report(self) -> "FinalReport":
        self.title = fit_text(self.title, 120)
        self.markdown = fit_text(self.markdown, 12_000)
        return self


def validate_password_strength(value: str) -> str:
    if not re.search(r"[A-Za-z]", value) or not re.search(r"\d", value):
        raise ValueError("Password must include at least one letter and one number.")
    return value


class RegisterRequest(BaseModel):
    full_name: str = Field(min_length=2, max_length=80)
    email: str = Field(max_length=254)
    password: str = Field(min_length=8, max_length=128)
    invite_code: str | None = Field(default=None, max_length=100)

    @field_validator("full_name")
    @classmethod
    def clean_name(cls, value: str) -> str:
        value = " ".join(value.split())
        if len(value) < 2:
            raise ValueError("Enter your full name.")
        return value

    @field_validator("email")
    @classmethod
    def clean_email(cls, value: str) -> str:
        return normalize_email(value)

    @field_validator("password")
    @classmethod
    def strong_enough(cls, value: str) -> str:
        return validate_password_strength(value)


class LoginRequest(BaseModel):
    email: str = Field(max_length=254)
    password: str = Field(min_length=1, max_length=128)

    @field_validator("email")
    @classmethod
    def clean_email(cls, value: str) -> str:
        return value.strip().lower()


class UserPublic(BaseModel):
    id: str
    email: str
    full_name: str
    created_at: str
    email_verified: bool = True
    is_admin: bool = False


class AuthResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"
    expires_in: int = Field(description="Seconds until the access token expires")
    user: UserPublic


class ChangePasswordRequest(BaseModel):
    current_password: str = Field(min_length=1, max_length=128)
    new_password: str = Field(min_length=8, max_length=128)

    @field_validator("new_password")
    @classmethod
    def strong_enough(cls, value: str) -> str:
        return validate_password_strength(value)


class ForgotPasswordRequest(BaseModel):
    email: str = Field(max_length=254)

    @field_validator("email")
    @classmethod
    def clean_email(cls, value: str) -> str:
        return value.strip().lower()


class ResetPasswordRequest(BaseModel):
    token: str = Field(min_length=20, max_length=200)
    new_password: str = Field(min_length=8, max_length=128)

    @field_validator("new_password")
    @classmethod
    def strong_enough(cls, value: str) -> str:
        return validate_password_strength(value)


class DeleteAccountRequest(BaseModel):
    password: str = Field(min_length=1, max_length=128)


class VerifyEmailRequest(BaseModel):
    token: str = Field(min_length=1, max_length=200)


class MessageResponse(BaseModel):
    detail: str


class AuthConfig(BaseModel):
    registration_open: bool
    password_reset_available: bool
    email_verification_required: bool = False
    invite_required: bool = False


class RunSummary(BaseModel):
    thread_id: str
    topic: str
    title: str | None = None
    status: str = Field(description="planning | review | researching | complete | failed | interrupted")
    created_at: str
    updated_at: str
    feedback: int | None = Field(default=None, description="1 = helpful, -1 = not helpful")
    share_token: str | None = Field(default=None, description="Present while a read-only link is active")
    tokens_used: int = Field(default=0, description="Model tokens spent on this run")
    schedule_id: str | None = Field(default=None, description="Set when a recurring schedule started this run")
    tags: list[str] = Field(default_factory=list)


class SharedReport(BaseModel):
    topic: str
    title: str | None = None
    report: str
    updated_at: str


class RunDetail(RunSummary):
    plan: list[dict[str, str]] | None = None
    report: str | None = None


class Usage(BaseModel):
    used_today: int
    daily_limit: int | None = Field(description="Runs allowed per rolling 24 hours; null when unlimited")
    default_iterations: int
    tokens_today: int = Field(default=0, description="Model tokens spent in the last 24 hours")
    token_limit: int | None = Field(default=None, description="Tokens allowed per rolling 24 hours; null when unlimited")


class RunList(BaseModel):
    runs: list[RunSummary]
    usage: Usage

