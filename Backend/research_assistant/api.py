"""FastAPI adapter for streaming and resuming the LangGraph research workflow."""

from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
import ipaddress
import json
import logging
import re
import secrets
import threading
import time
import uuid
from collections.abc import AsyncIterator, Mapping
from datetime import datetime, timedelta, timezone
from typing import Any
from urllib.parse import parse_qs, urlparse

from fastapi import FastAPI, HTTPException, Request
from fastapi.encoders import jsonable_encoder
from fastapi.responses import JSONResponse, Response, StreamingResponse
from fastapi.middleware.cors import CORSMiddleware
from fastapi.middleware.trustedhost import TrustedHostMiddleware
from langgraph.types import Command
from pydantic import BaseModel, Field, field_validator
from tavily import TavilyClient

from research_assistant.auth import (
    UserStore,
    bearer_token,
    is_demo_user,
    create_auth_router,
    create_mailer,
    ensure_demo_user,
    signing_secret,
    store_from_settings,
    verify_access_token,
)
from research_assistant.config import Settings
from research_assistant.followup import answer_followup, summarize_changes
from research_assistant.usage import UsageTracker
from research_assistant.documents import MAX_PDF_BYTES, DocumentError, extract_pdf_text, finding_from_documents
from research_assistant.usersources import fetch_user_sources
from research_assistant.graph import build_research_graph
from research_assistant.schemas import normalize_email, ResearchOptions, ResearchQuestion, RunDetail, RunList, SharedReport

logger = logging.getLogger(__name__)


class StartResearchRequest(BaseModel):
    topic: str = Field(min_length=3, max_length=2_000)
    max_iterations: int | None = Field(default=None, ge=1, le=8)
    options: ResearchOptions | None = None

    @field_validator("topic")
    @classmethod
    def trim_topic(cls, value: str) -> str:
        value = value.strip()
        if len(value) < 3:
            raise ValueError("Topic must contain at least three non-space characters.")
        return value


class ResumeResearchRequest(BaseModel):
    """Omit questions to approve the plan as shown, or provide edited questions."""

    questions: list[ResearchQuestion] | None = Field(default=None, min_length=1, max_length=8)


class FollowUpRequest(BaseModel):
    question: str = Field(min_length=3, max_length=500)

    @field_validator("question")
    @classmethod
    def strip_question(cls, value: str) -> str:
        value = value.strip()
        if len(value) < 3:
            raise ValueError("Ask a question of at least 3 characters.")
        return value


FOLLOWUPS_PER_HOUR = 20


SCHEDULE_PERIODS = {"daily": timedelta(days=1), "weekly": timedelta(days=7), "monthly": timedelta(days=30)}


class ScheduleRequest(BaseModel):
    topic: str = Field(min_length=3, max_length=2_000)
    frequency: str = "weekly"
    max_iterations: int | None = Field(default=None, ge=1, le=8)
    options: ResearchOptions | None = None

    @field_validator("topic")
    @classmethod
    def trim_schedule_topic(cls, value: str) -> str:
        value = value.strip()
        if len(value) < 3:
            raise ValueError("Topic must contain at least three non-space characters.")
        return value

    @field_validator("frequency")
    @classmethod
    def known_frequency(cls, value: str) -> str:
        if value not in SCHEDULE_PERIODS:
            raise ValueError("Frequency must be daily, weekly or monthly.")
        return value


MAX_TEMPLATES = 20


class TemplateRequest(BaseModel):
    name: str = Field(min_length=1, max_length=60)
    topic: str = Field(default="", max_length=2_000)
    options: ResearchOptions = Field(default_factory=ResearchOptions)

    @field_validator("name", "topic")
    @classmethod
    def trim_text(cls, value: str) -> str:
        return value.strip()

    @field_validator("name")
    @classmethod
    def name_not_blank(cls, value: str) -> str:
        if not value:
            raise ValueError("Give the template a name.")
        return value


def stored_options(options: ResearchOptions | None) -> dict[str, Any]:
    """Options worth keeping for later runs: pasted PDF text is never stored."""
    return (options or ResearchOptions()).model_dump(mode="json", exclude={"documents"})


class SchedulePatch(BaseModel):
    enabled: bool


def _iso(moment: datetime) -> str:
    return moment.astimezone(timezone.utc).isoformat(timespec="microseconds")


def _next_run(frequency: str) -> str:
    return _iso(datetime.now(timezone.utc) + SCHEDULE_PERIODS[frequency])


_TAG_RE = re.compile(r"^[a-z0-9][a-z0-9 _-]{0,23}$")


class TagsRequest(BaseModel):
    tags: list[str] = Field(max_length=5)

    @field_validator("tags")
    @classmethod
    def clean_tags(cls, values: list[str]) -> list[str]:
        cleaned: list[str] = []
        for raw in values:
            tag = " ".join(raw.lower().split())
            if not _TAG_RE.match(tag):
                raise ValueError("Tags are 1-24 characters: letters, numbers, spaces, - or _.")
            if tag not in cleaned:
                cleaned.append(tag)
        return cleaned


class EmailBriefRequest(BaseModel):
    to: str | None = Field(default=None, max_length=254)

    @field_validator("to")
    @classmethod
    def valid_recipient(cls, value: str | None) -> str | None:
        return normalize_email(value) if value and value.strip() else None


EMAILS_PER_HOUR = 10


class FeedbackRequest(BaseModel):
    value: int = Field(description="1 = helpful, -1 = not helpful, 0 = clear")

    @field_validator("value")
    @classmethod
    def valid_value(cls, value: int) -> int:
        if value not in (-1, 0, 1):
            raise ValueError("Feedback must be 1, -1 or 0.")
        return value


class VisualResearchRequest(BaseModel):
    types: list[str] = Field(default_factory=lambda: ["images", "videos", "models"], min_length=1, max_length=3)

    @field_validator("types")
    @classmethod
    def validate_types(cls, values: list[str]) -> list[str]:
        allowed = {"images", "videos", "models"}
        if len(set(values)) != len(values) or not set(values) <= allowed:
            raise ValueError("Visual types must be unique values from images, videos, and models.")
        return values


def _https_url(value: Any) -> str | None:
    if not isinstance(value, str) or len(value) > 2_048:
        return None
    try:
        parsed = urlparse(value)
        hostname = parsed.hostname
        if parsed.port and not 1 <= parsed.port <= 65_535:
            return None
    except ValueError:
        return None
    if parsed.scheme != "https" or not hostname or parsed.username or parsed.password:
        return None
    if hostname.lower() in {"localhost", "localhost.localdomain"} or hostname.lower().endswith(".local"):
        return None
    try:
        if not ipaddress.ip_address(hostname).is_global:
            return None
    except ValueError:
        pass
    if not parsed.netloc:
        return None
    return value


def _image_assets(client: TavilyClient, topic: str) -> list[dict[str, Any]]:
    result = client.search(
        query=f"{topic} important images diagrams",
        search_depth="basic",
        max_results=5,
        include_images=True,
        include_image_descriptions=True,
    )
    images: list[dict[str, Any]] = []
    seen: set[str] = set()
    candidates = list(result.get("images", []))
    for page in result.get("results", []):
        page_url = _https_url(page.get("url"))
        for image in page.get("images", []):
            candidates.append({**image, "source_url": page_url, "page_title": page.get("title")})
    for item in candidates:
        if not isinstance(item, Mapping):
            continue
        image_url = _https_url(item.get("url"))
        if not image_url or image_url in seen:
            continue
        seen.add(image_url)
        images.append(
            {
                "kind": "image",
                "title": str(item.get("page_title") or item.get("description") or "Related image")[:200],
                "url": image_url,
                "description": str(item.get("description") or "")[:500],
                "source_url": _https_url(item.get("source_url")) or image_url,
            }
        )
        if len(images) >= 8:
            break
    return images


def _video_assets(client: TavilyClient, topic: str) -> list[dict[str, Any]]:
    result = client.search(
        query=f"{topic} explainer lecture demonstration",
        search_depth="basic",
        max_results=6,
        include_domains=["youtube.com", "youtu.be"],
    )
    videos: list[dict[str, Any]] = []
    seen: set[str] = set()
    for item in result.get("results", []):
        source_url = _https_url(item.get("url"))
        if not source_url:
            continue
        parsed = urlparse(source_url)
        host = (parsed.hostname or "").lower()
        query = parse_qs(parsed.query)
        video_id = query.get("v", [""])[0]
        if host == "youtu.be":
            video_id = parsed.path.strip("/").split("/")[0]
        elif host in {"www.youtube.com", "youtube.com", "m.youtube.com"}:
            parts = parsed.path.strip("/").split("/")
            if len(parts) == 2 and parts[0] in {"shorts", "embed", "live"}:
                video_id = parts[1]
        else:
            continue
        if not re.fullmatch(r"[A-Za-z0-9_-]{11}", video_id) or video_id in seen:
            continue
        seen.add(video_id)
        videos.append(
            {
                "kind": "video",
                "title": str(item.get("title") or "Related video")[:200],
                "url": source_url,
                "description": str(item.get("content") or "")[:500],
                "source_url": source_url,
                "thumbnail_url": f"https://i.ytimg.com/vi/{video_id}/hqdefault.jpg",
                "embed_url": f"https://www.youtube-nocookie.com/embed/{video_id}?rel=0",
            }
        )
        if len(videos) >= 6:
            break
    return videos


def _model_assets(client: TavilyClient, topic: str) -> list[dict[str, Any]]:
    result = client.search(
        query=f"{topic} interactive 3D model",
        search_depth="basic",
        max_results=6,
        include_domains=["sketchfab.com"],
    )
    models: list[dict[str, Any]] = []
    seen: set[str] = set()
    for item in result.get("results", []):
        source_url = _https_url(item.get("url"))
        if not source_url or (urlparse(source_url).hostname or "").lower() not in {"sketchfab.com", "www.sketchfab.com"}:
            continue
        match = re.search(r"(?i)(?<![a-f0-9])([a-f0-9]{32})(?![a-f0-9])", urlparse(source_url).path)
        if not match or match.group(1).lower() in seen:
            continue
        model_id = match.group(1).lower()
        seen.add(model_id)
        models.append(
            {
                "kind": "model",
                "title": str(item.get("title") or "Interactive 3D model")[:200],
                "url": source_url,
                "description": str(item.get("content") or "")[:500],
                "source_url": source_url,
                "embed_url": f"https://sketchfab.com/models/{model_id}/embed?autostart=0",
            }
        )
        if len(models) >= 6:
            break
    return models


def _sse(event: str, data: Any) -> str:
    payload = json.dumps(jsonable_encoder(data), ensure_ascii=False, separators=(",", ":"))
    return f"event: {event}\ndata: {payload}\n\n"


def _interrupt_value(value: Any) -> Any:
    return getattr(value, "value", value)


def _plan_interrupt(snapshot: Any) -> dict[str, Any] | None:
    for task in getattr(snapshot, "tasks", ()):
        for pending in getattr(task, "interrupts", ()):
            value = _interrupt_value(pending)
            if isinstance(value, dict) and value.get("kind") == "review_research_plan":
                return value
    return None


def _public_update(node: str, update: Any) -> Any:
    """Avoid sending scraped full page text in progress events."""
    if node == "researcher" and isinstance(update, Mapping):
        findings = []
        for finding in update.get("findings", []):
            finding_data = jsonable_encoder(finding)
            findings.append(
                {
                    "query": finding_data.get("query"),
                    "summary": finding_data.get("summary"),
                    "sources": [
                        {key: source.get(key) for key in ("title", "url", "snippet")}
                        for source in finding_data.get("sources", [])
                    ],
                }
            )
        return {"findings": findings}
    if node == "writer":
        return {"report_ready": True}
    return jsonable_encoder(update)


def _public_error(exc: Exception) -> dict[str, str]:
    """Map provider failures to actionable SSE messages without leaking raw responses."""
    message = str(exc).lower()
    error_type = type(exc).__name__.lower()
    if any(marker in message for marker in ("quota", "resource exhausted", "resource_exhausted")):
        return {
            "code": "provider_quota_exhausted",
            "message": "Groq API quota or usage limit was reached. Check your Groq console limits and billing, then retry.",
        }
    if "ratelimit" in error_type or "rate limit" in message or "429" in message:
        return {
            "code": "provider_rate_limited",
            "message": "The model provider rate-limited this run. Wait briefly and retry.",
        }
    return {"code": "research_run_failed", "message": "Research run failed. Check server logs."}


async def _stream_graph(
    graph: Any,
    graph_input: Any,
    config: dict[str, Any],
    thread_id: str,
) -> AsyncIterator[str]:
    """Translate LangGraph update events into named SSE events, with keep-alives."""
    yield _sse("started", {"thread_id": thread_id})
    stream = graph.astream(graph_input, config=config, stream_mode="updates")
    iterator = stream.__aiter__()
    next_chunk: asyncio.Task[Any] | None = None
    interrupt_sent = False
    try:
        next_chunk = asyncio.create_task(anext(iterator))
        while True:
            done, _ = await asyncio.wait({next_chunk}, timeout=15)
            if not done:
                yield ": keep-alive\n\n"
                continue
            try:
                update = next_chunk.result()
            except StopAsyncIteration:
                break
            next_chunk = None

            if isinstance(update, Mapping) and "__interrupt__" in update:
                values = update["__interrupt__"]
                for item in values if isinstance(values, (list, tuple)) else (values,):
                    value = _interrupt_value(item)
                    if isinstance(value, dict) and value.get("kind") == "review_research_plan":
                        yield _sse("plan_review_required", {"thread_id": thread_id, **value})
                        interrupt_sent = True
                if interrupt_sent:
                    break
                next_chunk = asyncio.create_task(anext(iterator))
                continue

            if isinstance(update, Mapping):
                for node, node_update in update.items():
                    yield _sse(
                        "progress",
                        {"thread_id": thread_id, "node": node, "update": _public_update(node, node_update)},
                    )
            next_chunk = asyncio.create_task(anext(iterator))

        if interrupt_sent:
            return
        snapshot = await graph.aget_state(config)
        pending = _plan_interrupt(snapshot)
        if pending is not None:
            yield _sse("plan_review_required", {"thread_id": thread_id, **pending})
            return
        values = snapshot.values or {}
        if values.get("report"):
            yield _sse("completed", {"thread_id": thread_id, "report": values["report"]})
    except asyncio.CancelledError:
        raise
    except Exception as exc:
        logger.exception("Research graph stream failed for thread %s", thread_id)
        yield _sse("error", {"thread_id": thread_id, **_public_error(exc)})
    finally:
        if next_chunk is not None and not next_chunk.done():
            next_chunk.cancel()
            try:
                await next_chunk
            except (asyncio.CancelledError, StopAsyncIteration):
                pass
        close = getattr(iterator, "aclose", None)
        if close is not None:
            await close()


def _streaming_response(stream: AsyncIterator[str]) -> StreamingResponse:
    return StreamingResponse(
        stream,
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache, no-transform",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


def _parse_sse(frame: str) -> tuple[str, dict[str, Any]] | None:
    """Read back a frame produced by _sse so the run lifecycle can be persisted."""
    if not frame.startswith("event: "):
        return None
    head, _, rest = frame.partition("\n")
    if not rest.startswith("data: "):
        return None
    try:
        payload = json.loads(rest[6:].strip())
    except ValueError:
        return None
    return (head[7:].strip(), payload) if isinstance(payload, dict) else None


def _report_title(report: str) -> str | None:
    first = report.lstrip().split("\n", 1)[0]
    return first[2:].strip()[:200] or None if first.startswith("# ") else None


class HealthAwareTrustedHostMiddleware(TrustedHostMiddleware):
    """Host allow-list for everything except the liveness/readiness probes.

    Platform health checks (Render, Kubernetes, load balancers) call the service by its internal
    address, which can never be on a public hostname allow-list. The probes return no user data.
    """

    PROBE_PATHS = frozenset({"/healthz", "/readyz"})

    async def __call__(self, scope, receive, send) -> None:
        if scope["type"] == "http" and scope.get("path") in self.PROBE_PATHS:
            await self.app(scope, receive, send)
            return
        await super().__call__(scope, receive, send)


def create_app(*, graph: Any | None = None, settings: Settings | None = None) -> FastAPI:
    """Create the API app. A graph can be injected, or initialized lazily on first run."""
    active_settings = settings or Settings()

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        async with base_lifespan(app):
            task = asyncio.create_task(scheduler_loop()) if active_settings.scheduler_enabled else None
            try:
                yield
            finally:
                if task is not None:
                    task.cancel()
                    await asyncio.gather(task, return_exceptions=True)

    @asynccontextmanager
    async def base_lifespan(app: FastAPI):
        if not logging.getLogger().handlers:
            logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
        app.state.database_ready = active_settings.database_url is None
        app.state.run_semaphore = asyncio.Semaphore(active_settings.max_concurrent_runs)
        try:
            if await asyncio.to_thread(ensure_demo_user, get_store(), active_settings):
                logger.info("Created demo account %s", active_settings.demo_email)
        except Exception:  # a bad demo config must not stop the service
            logger.exception("Could not create the demo account")
        if app.state.research_graph is None and active_settings.database_url is not None:
            from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver  # type: ignore[import-not-found]

            async with AsyncPostgresSaver.from_conn_string(
                active_settings.database_url.get_secret_value()
            ) as checkpointer:
                await checkpointer.setup()
                app.state.research_checkpointer = checkpointer
                app.state.research_graph = build_research_graph(
                    settings=active_settings,
                    checkpointer=checkpointer,
                )
                app.state.database_ready = True
                yield
            return
        if active_settings.app_env == "production" and app.state.research_graph is None:
            raise RuntimeError("Production requires a PostgreSQL checkpointer.")
        yield

    production = active_settings.app_env == "production"
    app = FastAPI(
        title="Multi Agent Research Assistant API",
        version="1.0.0",
        lifespan=lifespan,
        docs_url=None if production else "/docs",
        redoc_url=None if production else "/redoc",
        openapi_url=None if production else "/openapi.json",
    )
    app.state.research_settings = settings
    app.state.research_graph = graph
    app.state.research_checkpointer = None
    app.state.database_ready = active_settings.database_url is None
    app.state.run_semaphore = asyncio.Semaphore(active_settings.max_concurrent_runs)
    graph_init_lock = threading.Lock()
    store_lock = threading.Lock()
    app.state.user_store = None
    app.state.mailer = None
    app.state.active_threads = set()  # runs currently streaming in this process
    app.state.user_active = {}  # user id -> number of streaming runs

    def live_settings() -> Settings:
        return app.state.research_settings or Settings()

    def get_store() -> UserStore:
        with store_lock:
            if app.state.user_store is None:
                app.state.user_store = store_from_settings(live_settings())
        return app.state.user_store

    def get_mailer():
        if app.state.mailer is None:
            app.state.mailer = create_mailer(live_settings())
        return app.state.mailer

    async def purge_threads(thread_ids: list[str]) -> None:
        """Best-effort removal of LangGraph checkpoints (which hold the full research content)."""
        checkpointer = getattr(app.state.research_graph, "checkpointer", None) or app.state.research_checkpointer
        if checkpointer is None:
            return
        for thread_id in thread_ids:
            try:
                if hasattr(checkpointer, "adelete_thread"):
                    await checkpointer.adelete_thread(thread_id)
                elif hasattr(checkpointer, "delete_thread"):
                    await asyncio.to_thread(checkpointer.delete_thread, thread_id)
            except Exception:
                logger.warning("Could not delete checkpoint for thread %s", thread_id, exc_info=True)

    async def require_run_access(request: Request, thread_id: str) -> None:
        """Users may only touch runs they started. The legacy shared token is unrestricted."""
        user_id = getattr(request.state, "user_id", None)
        if user_id is None:
            return
        owned = await asyncio.to_thread(get_store().owns_run, thread_id, user_id)
        if not owned:
            raise HTTPException(status_code=404, detail="Research thread was not found.")

    @app.middleware("http")
    async def secure_api(request: Request, call_next):
        def secure_headers(response):
            response.headers["X-Content-Type-Options"] = "nosniff"
            response.headers["X-Frame-Options"] = "DENY"
            response.headers["Referrer-Policy"] = "no-referrer"
            response.headers["Cache-Control"] = "no-store"
            return response

        if request.method != "OPTIONS" and request.url.path.startswith("/research/"):
            current_settings = live_settings()
            token = bearer_token(request)
            claims = verify_access_token(token, signing_secret(current_settings)) if token else None
            legacy = (
                current_settings.research_api_token.get_secret_value()
                if current_settings.research_api_token
                else None
            )
            request.state.user_id = None
            if claims:
                active = await asyncio.to_thread(get_store().check_session, claims["sub"], int(claims.get("ver", 0)))
                if not active:  # account deleted, or password changed since this token was issued
                    return secure_headers(
                        JSONResponse(status_code=401, content={"detail": "Your session has expired. Please sign in again."})
                    )
                request.state.user_id = claims["sub"]
            elif not (legacy and token and secrets.compare_digest(token, legacy)):
                return secure_headers(
                    JSONResponse(status_code=401, content={"detail": "Please sign in to continue."})
                )
        return secure_headers(await call_next(request))

    # CORS is outermost so even early auth failures retain the correct browser headers.
    @app.middleware("http")
    async def request_log(request: Request, call_next):
        """Tag each request with an id (also returned as X-Request-ID) and log one line per request."""
        request_id = secrets.token_hex(6)
        request.state.request_id = request_id
        started = time.monotonic()
        try:
            response = await call_next(request)
        except Exception:
            logger.exception("req=%s %s %s -> unhandled error", request_id, request.method, request.url.path)
            raise
        response.headers["X-Request-ID"] = request_id
        if request.url.path not in HealthAwareTrustedHostMiddleware.PROBE_PATHS:
            logger.info(
                "req=%s %s %s -> %s %.0fms user=%s",
                request_id, request.method, request.url.path, response.status_code,
                (time.monotonic() - started) * 1000, getattr(request.state, "user_id", None) or "-",
            )
        return response

    app.add_middleware(HealthAwareTrustedHostMiddleware, allowed_hosts=active_settings.allowed_hosts)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=active_settings.cors_origins,
        allow_credentials=False,
        allow_methods=["GET", "POST", "PUT", "DELETE"],
        allow_headers=["Authorization", "Content-Type"],
        max_age=600,
    )

    app.include_router(
        create_auth_router(
            get_settings=live_settings,
            get_store=get_store,
            get_mailer=get_mailer,
            on_account_deleted=purge_threads,
        )
    )

    def get_graph() -> tuple[Any, Settings]:
        live_settings = app.state.research_settings or Settings()
        with graph_init_lock:
            if app.state.research_graph is None:
                try:
                    app.state.research_graph = build_research_graph(
                        settings=live_settings,
                        checkpointer=app.state.research_checkpointer,
                    )
                except ValueError as exc:
                    raise HTTPException(status_code=503, detail=str(exc)) from exc
        return app.state.research_graph, live_settings

    async def enforce_token_budget(user_id: str, settings: Settings) -> None:
        """Refuse new work once a user has spent their rolling 24-hour token budget."""
        budget = settings.max_tokens_per_user_per_day
        if not budget:
            return
        since = (datetime.now(timezone.utc) - timedelta(hours=24)).isoformat(timespec="microseconds")
        spent = await asyncio.to_thread(get_store().tokens_since, user_id, since)
        if spent >= budget:
            raise HTTPException(
                status_code=429,
                detail=f"Daily token budget reached ({budget:,} tokens per 24 hours). Try again later.",
            )

    def limited_stream(
        graph: Any,
        graph_input: Any,
        config: dict[str, Any],
        thread_id: str,
        *,
        user_id: str | None = None,
        status_on_start: str | None = None,
    ) -> StreamingResponse:
        """Stream a run, enforcing capacity and (for signed-in users) saving its lifecycle.

        `status_on_start` is set when resuming; None means this is a brand-new run.
        """

        async def save(**fields: Any) -> None:
            if user_id is not None:
                await asyncio.to_thread(lambda: get_store().update_run(thread_id, **fields))

        async def reject(code: str, message: str) -> str:
            if status_on_start is None:  # a new run that never started
                await save(status="failed")
            return _sse("error", {"thread_id": thread_id, "code": code, "message": message})

        async def remember(frame: str) -> None:
            parsed = _parse_sse(frame)
            if parsed is None:
                return
            name, payload = parsed
            if name == "plan_review_required":
                await save(status="review", plan=payload.get("questions") or [])
            elif name == "completed":
                report = str(payload.get("report") or "")
                await save(status="complete", report=report, title=_report_title(report))
            elif name == "error":
                await save(status="failed")

        async def stream_with_capacity() -> AsyncIterator[str]:
            current = live_settings()
            if user_id is not None and app.state.user_active.get(user_id, 0) >= current.max_active_runs_per_user:
                yield await reject(
                    "run_in_progress",
                    "You already have a research run in progress. Wait for it to finish or cancel it first.",
                )
                return
            semaphore: asyncio.Semaphore = app.state.run_semaphore
            try:
                await asyncio.wait_for(semaphore.acquire(), timeout=0.05)
            except TimeoutError:
                yield await reject(
                    "capacity_exceeded",
                    "The service is handling its maximum number of concurrent runs. Retry shortly.",
                )
                return
            if user_id is not None:
                app.state.user_active[user_id] = app.state.user_active.get(user_id, 0) + 1
            app.state.active_threads.add(thread_id)
            tracker = UsageTracker()
            tracked = {**config, "callbacks": [*(config.get("callbacks") or []), tracker]}
            try:
                if status_on_start:
                    await save(status=status_on_start)
                async for event in _stream_graph(graph, graph_input, tracked, thread_id):
                    await remember(event)
                    yield event
            finally:
                if user_id is not None and tracker.total:
                    try:
                        get_store().add_tokens(thread_id, tracker.total)
                    except Exception:
                        logger.exception("Could not save token usage for thread %s", thread_id)
                semaphore.release()
                app.state.active_threads.discard(thread_id)
                if user_id is not None:
                    remaining = app.state.user_active.get(user_id, 1) - 1
                    if remaining > 0:
                        app.state.user_active[user_id] = remaining
                    else:
                        app.state.user_active.pop(user_id, None)

        return _streaming_response(stream_with_capacity())

    def public_run(row: dict[str, Any]) -> dict[str, Any]:
        status = row["status"]
        if status in {"planning", "researching"} and row["thread_id"] not in app.state.active_threads:
            status = "interrupted"  # the browser disconnected or the server restarted mid-run
        return {key: value for key, value in row.items() if key != "user_id"} | {"status": status}

    @app.get("/healthz")
    async def healthz() -> dict[str, Any]:
        return {"status": "ok"}

    @app.get("/readyz")
    async def readyz() -> dict[str, Any]:
        ready = app.state.database_ready and app.state.research_graph is not None
        if active_settings.app_env == "production" and not ready:
            raise HTTPException(status_code=503, detail="Service is not ready.")
        return {"status": "ready" if ready else "starting"}

    @app.post("/research/runs")
    async def start_research(body: StartResearchRequest, request: Request) -> StreamingResponse:
        graph, active_settings = get_graph()
        thread_id = str(uuid.uuid4())
        user_id = getattr(request.state, "user_id", None)
        if user_id is not None:
            daily_limit = active_settings.max_runs_per_user_per_day
            if daily_limit:
                since = (datetime.now(timezone.utc) - timedelta(hours=24)).isoformat(timespec="microseconds")
                used = await asyncio.to_thread(get_store().count_runs_since, user_id, since)
                if used >= daily_limit:
                    raise HTTPException(
                        status_code=429,
                        detail=f"Daily research limit reached ({daily_limit} runs per 24 hours). Try again later.",
                    )
            await enforce_token_budget(user_id, active_settings)
            if active_settings.email_verification_required:
                account = await asyncio.to_thread(get_store().get_by_id, user_id)
                if account is not None and not account.email_verified:
                    raise HTTPException(
                        status_code=403,
                        detail="Confirm your email address before starting research. Check your inbox or resend the link.",
                    )
        config = {
            "configurable": {"thread_id": thread_id},
            "max_concurrency": active_settings.max_parallel_graph_tasks,
        }
        options = body.options or ResearchOptions()
        provided = []
        if options.urls:
            if not active_settings.tavily_api_key:
                raise HTTPException(status_code=503, detail="Reading your links is not configured on the server.")
            try:
                found = await asyncio.to_thread(
                    fetch_user_sources,
                    options.urls,
                    TavilyClient(api_key=active_settings.tavily_api_key.get_secret_value()),
                )
            except Exception as exc:
                logger.warning("Could not read user links: %s", type(exc).__name__)
                found = None
            if found is None:
                raise HTTPException(
                    status_code=422,
                    detail="Could not read any of the links you provided. Check they are public pages, or remove them.",
                )
            provided = [found]
        if options.documents:
            uploaded = finding_from_documents([d.model_dump() for d in options.documents], body.topic)
            if uploaded is not None:
                provided.append(uploaded)
        if user_id is not None:
            try:
                await asyncio.to_thread(get_store().record_run, thread_id, user_id, body.topic)
            except PermissionError:
                raise HTTPException(status_code=401, detail="Your account no longer exists. Please sign in again.") from None
        graph_input = {
            "topic": body.topic.strip(),
            "findings": provided,
            "iteration": 0,
            "max_iterations": body.max_iterations or active_settings.max_iterations,
            "options": options.model_dump(mode="json", exclude={"urls", "documents"}),
        }
        return limited_stream(graph, graph_input, config, thread_id, user_id=user_id)

    @app.post("/research/uploads/pdf")
    async def upload_pdf(request: Request, name: str = "document.pdf") -> dict[str, Any]:
        """Read a PDF sent as the raw request body and return its text for use as a source."""
        declared = request.headers.get("content-length")
        if declared and declared.isdigit() and int(declared) > MAX_PDF_BYTES:
            raise HTTPException(status_code=413, detail="That PDF is larger than 5 MB.")
        data = bytearray()
        async for chunk in request.stream():
            data.extend(chunk)
            if len(data) > MAX_PDF_BYTES:
                raise HTTPException(status_code=413, detail="That PDF is larger than 5 MB.")
        try:
            text = await asyncio.to_thread(extract_pdf_text, bytes(data))
        except DocumentError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from None
        label = re.sub(r"[^\w .()\-]", "", name.strip())[:120] or "document.pdf"
        return {"name": label, "chars": len(text), "text": text}

    @app.get("/research/runs", response_model=RunList)
    async def list_runs(request: Request) -> dict[str, Any]:
        """The signed-in user's recent runs plus their usage for today."""
        current = live_settings()
        user_id = getattr(request.state, "user_id", None)
        runs: list[dict[str, Any]] = []
        used = 0
        tokens_today = 0
        if user_id is not None:
            store = get_store()
            runs = [public_run(row) for row in await asyncio.to_thread(store.list_runs, user_id, 200)]
            since = (datetime.now(timezone.utc) - timedelta(hours=24)).isoformat(timespec="microseconds")
            used = await asyncio.to_thread(store.count_runs_since, user_id, since)
            tokens_today = await asyncio.to_thread(store.tokens_since, user_id, since)
        return {
            "runs": runs,
            "usage": {
                "used_today": used,
                "daily_limit": current.max_runs_per_user_per_day or None,
                "default_iterations": current.max_iterations,
                "tokens_today": tokens_today,
                "token_limit": current.max_tokens_per_user_per_day or None,
            },
        }

    @app.get("/research/runs/{thread_id}", response_model=RunDetail)
    async def get_run(thread_id: str, request: Request) -> dict[str, Any]:
        try:
            uuid.UUID(thread_id)
        except ValueError as exc:
            raise HTTPException(status_code=404, detail="Research thread was not found.") from exc
        row = await asyncio.to_thread(get_store().get_run, thread_id, getattr(request.state, "user_id", None))
        if row is None:
            raise HTTPException(status_code=404, detail="Research thread was not found.")
        run = public_run(row)
        if run["status"] == "review":
            # A plan can only be approved if the checkpoint still exists (it does not after an in-memory restart).
            graph_now = app.state.research_graph
            snapshot = await graph_now.aget_state({"configurable": {"thread_id": thread_id}}) if graph_now else None
            if snapshot is None or not snapshot.values or _plan_interrupt(snapshot) is None:
                run["status"] = "interrupted"
        return run

    @app.delete("/research/runs/{thread_id}", status_code=204)
    async def delete_run(thread_id: str, request: Request) -> Response:
        try:
            uuid.UUID(thread_id)
        except ValueError as exc:
            raise HTTPException(status_code=404, detail="Research thread was not found.") from exc
        removed = await asyncio.to_thread(get_store().delete_run, thread_id, getattr(request.state, "user_id", None))
        if not removed:
            raise HTTPException(status_code=404, detail="Research thread was not found.")
        await purge_threads([thread_id])
        return Response(status_code=204)

    @app.post("/research/runs/{thread_id}/resume")
    async def resume_research(thread_id: str, body: ResumeResearchRequest, request: Request) -> StreamingResponse:
        try:
            uuid.UUID(thread_id)
        except ValueError as exc:
            raise HTTPException(status_code=404, detail="Research thread was not found.") from exc
        await require_run_access(request, thread_id)
        graph, active_settings = get_graph()
        config = {
            "configurable": {"thread_id": thread_id},
            "max_concurrency": active_settings.max_parallel_graph_tasks,
        }
        snapshot = await graph.aget_state(config)
        if not snapshot.values:
            raise HTTPException(status_code=404, detail="Research thread was not found.")
        pending = _plan_interrupt(snapshot)
        if pending is None:
            raise HTTPException(status_code=409, detail="This thread is not waiting for plan review.")

        questions = body.questions
        if questions is None:
            questions = [ResearchQuestion.model_validate(item) for item in snapshot.values.get("plan", [])]
        if not questions:
            raise HTTPException(status_code=422, detail="No plan questions are available to approve.")

        resume_user = getattr(request.state, "user_id", None)
        if resume_user is not None:
            await enforce_token_budget(resume_user, active_settings)
        command = Command(resume={"questions": [question.model_dump(mode="json") for question in questions]})
        return limited_stream(
            graph, command, config, thread_id,
            user_id=getattr(request.state, "user_id", None), status_on_start="researching",
        )

    @app.put("/research/runs/{thread_id}/feedback", status_code=204)
    async def set_feedback(thread_id: str, body: FeedbackRequest, request: Request) -> Response:
        try:
            uuid.UUID(thread_id)
        except ValueError as exc:
            raise HTTPException(status_code=404, detail="Research thread was not found.") from exc
        user_id = getattr(request.state, "user_id", None)
        if not await asyncio.to_thread(get_store().set_feedback, thread_id, user_id, body.value):
            raise HTTPException(status_code=404, detail="Research thread was not found.")
        return Response(status_code=204)

    @app.put("/research/runs/{thread_id}/tags")
    async def set_tags(thread_id: str, body: TagsRequest, request: Request) -> dict[str, Any]:
        try:
            uuid.UUID(thread_id)
        except ValueError as exc:
            raise HTTPException(status_code=404, detail="Research thread was not found.") from exc
        user_id = getattr(request.state, "user_id", None)
        if not await asyncio.to_thread(get_store().set_tags, thread_id, user_id, body.tags):
            raise HTTPException(status_code=404, detail="Research thread was not found.")
        return {"tags": body.tags}

    @app.post("/research/runs/{thread_id}/email")
    async def email_brief(thread_id: str, body: EmailBriefRequest, request: Request) -> dict[str, Any]:
        """Email a finished brief to the signed-in user, or to one address they choose."""
        try:
            uuid.UUID(thread_id)
        except ValueError as exc:
            raise HTTPException(status_code=404, detail="Research thread was not found.") from exc
        current = live_settings()
        user_id = getattr(request.state, "user_id", None)
        if user_id is None:
            raise HTTPException(status_code=403, detail="Emailing a brief needs a signed-in account.")
        account = await asyncio.to_thread(get_store().get_by_id, user_id)
        row = await asyncio.to_thread(get_store().get_run, thread_id, user_id)
        if account is None or row is None:
            raise HTTPException(status_code=404, detail="Research thread was not found.")
        if is_demo_user(account, current):
            raise HTTPException(status_code=403, detail="The shared demo account cannot send email. Create your own account.")
        if not current.password_reset_available:
            raise HTTPException(status_code=503, detail="Email is not set up on this server.")
        report = row.get("report")
        if not report:
            raise HTTPException(status_code=409, detail="Email is available once the report is complete.")
        now = datetime.now(timezone.utc).timestamp()
        recent = [t for t in app.state.email_times.get(user_id, []) if now - t < 3600]
        if len(recent) >= EMAILS_PER_HOUR:
            raise HTTPException(status_code=429, detail="Email limit reached. Try again in a little while.")
        recent.append(now)
        app.state.email_times[user_id] = recent
        recipient = body.to or account.email
        title = row.get("title") or row["topic"]
        text = (
            f"{account.full_name} sent you a research brief from Fieldwork.\n\n"
            if recipient != account.email
            else f"Hi {account.full_name}, here is your research brief.\n\n"
        ) + f"{report}\n\n---\nGenerated by an AI research assistant from public web sources. Check the cited sources before relying on it.\n"
        try:
            await asyncio.to_thread(get_mailer().send, recipient, f"Fieldwork: {title}", text)
        except Exception as exc:
            logger.warning("Could not email brief %s: %s", thread_id, type(exc).__name__)
            raise HTTPException(status_code=502, detail="The email could not be sent. Try again later.") from exc
        return {"detail": f"Sent to {recipient}.", "to": recipient}

    @app.get("/research/admin/stats")
    async def admin_stats(request: Request) -> dict[str, Any]:
        user_id = getattr(request.state, "user_id", None)
        account = await asyncio.to_thread(get_store().get_by_id, user_id) if user_id else None
        if account is None or account.email.lower() not in live_settings().admin_email_list:
            raise HTTPException(status_code=404, detail="Not found.")
        now = datetime.now(timezone.utc)
        return await asyncio.to_thread(
            get_store().admin_stats, _iso(now - timedelta(hours=24)), _iso(now - timedelta(days=7))
        )

    @app.post("/research/runs/{thread_id}/share")
    async def create_share(thread_id: str, request: Request) -> dict[str, Any]:
        return {"share_token": await _share(thread_id, request, enable=True)}

    @app.delete("/research/runs/{thread_id}/share", status_code=204)
    async def revoke_share(thread_id: str, request: Request) -> Response:
        await _share(thread_id, request, enable=False)
        return Response(status_code=204)

    async def _share(thread_id: str, request: Request, *, enable: bool) -> str | None:
        try:
            uuid.UUID(thread_id)
        except ValueError as exc:
            raise HTTPException(status_code=404, detail="Research thread was not found.") from exc
        user_id = getattr(request.state, "user_id", None)
        row = await asyncio.to_thread(get_store().get_run, thread_id, user_id)
        if row is None:
            raise HTTPException(status_code=404, detail="Research thread was not found.")
        if enable and not row.get("report"):
            raise HTTPException(status_code=409, detail="A link can be created once the report is complete.")
        return await asyncio.to_thread(get_store().set_share_token, thread_id, user_id, enable)

    @app.get("/shared/{token}", response_model=SharedReport)
    async def shared_report(token: str) -> dict[str, Any]:
        """Public, read-only view of a brief whose owner created a share link."""
        row = await asyncio.to_thread(get_store().get_shared, token) if 20 <= len(token) <= 100 else None
        if row is None:
            raise HTTPException(status_code=404, detail="This shared brief does not exist or the link was turned off.")
        return row

    # ------------------------------------------------------------------ recurring research

    async def run_schedule(schedule: dict[str, Any]) -> None:
        """Run one due schedule end to end (plan auto-approved) and email the result."""
        store = get_store()
        user_id, schedule_id = schedule["user_id"], schedule["id"]
        user = await asyncio.to_thread(store.get_by_id, user_id)
        if user is None:
            return
        current = live_settings()
        retry_at = _iso(datetime.now(timezone.utc) + timedelta(hours=1))
        since = _iso(datetime.now(timezone.utc) - timedelta(hours=24))
        if current.max_runs_per_user_per_day:
            used = await asyncio.to_thread(store.count_runs_since, user_id, since)
            if used >= current.max_runs_per_user_per_day:
                await asyncio.to_thread(store.finish_schedule, schedule_id, None, "waiting: daily run limit", retry_at)
                return
        if current.max_tokens_per_user_per_day:
            spent = await asyncio.to_thread(store.tokens_since, user_id, since)
            if spent >= current.max_tokens_per_user_per_day:
                await asyncio.to_thread(store.finish_schedule, schedule_id, None, "waiting: daily token budget", retry_at)
                return

        graph, active = get_graph()
        thread_id = str(uuid.uuid4())
        await asyncio.to_thread(store.record_run, thread_id, user_id, schedule["topic"], schedule_id)
        config = {"configurable": {"thread_id": thread_id}, "max_concurrency": active.max_parallel_graph_tasks}
        start_input = {
            "topic": schedule["topic"], "findings": [], "iteration": 0,
            "max_iterations": schedule.get("depth") or active.max_iterations,
            # Saved options apply to every run; links are not re-read, so they are left out.
            "options": {k: v for k, v in (schedule.get("options") or {}).items() if k not in {"urls", "documents"}},
        }

        async def drain(response: StreamingResponse) -> list[tuple[str, dict[str, Any]]]:
            events = []
            async for frame in response.body_iterator:
                parsed = _parse_sse(frame.decode() if isinstance(frame, bytes) else frame)
                if parsed:
                    events.append(parsed)
            return events

        events = await drain(limited_stream(graph, start_input, config, thread_id, user_id=user_id))
        plan = next((p for n, p in events if n == "plan_review_required"), None)
        if plan and plan.get("questions"):
            resume = Command(resume={"questions": plan["questions"]})
            events += await drain(
                limited_stream(graph, resume, config, thread_id, user_id=user_id, status_on_start="researching")
            )
        failure = next((p for n, p in events if n == "error"), None)
        done = next((p for n, p in events if n == "completed"), None)
        if done is None:
            busy = failure and failure.get("code") in {"capacity_exceeded", "run_in_progress"}
            await asyncio.to_thread(
                store.finish_schedule, schedule_id, thread_id, "waiting: service busy" if busy else "failed",
                _iso(datetime.now(timezone.utc) + timedelta(minutes=10)) if busy else None,
            )
            return
        await asyncio.to_thread(store.finish_schedule, schedule_id, thread_id, "complete")
        await asyncio.to_thread(email_schedule_result, schedule, user, str(done.get("report") or ""), current)

    def email_schedule_result(schedule: dict[str, Any], user: Any, report: str, current: Settings) -> None:
        """Send the new brief (with a short 'what changed') to the account's email. Never raises."""
        try:
            title = _report_title(report) or schedule["topic"]
            changes = ""
            previous = schedule.get("last_thread_id")
            if previous:
                old = get_store().get_run(previous, user.id)
                if old and old.get("report"):
                    try:
                        changes = summarize_changes(old["report"], report, current)
                    except Exception:
                        logger.warning("Could not summarize changes for schedule %s", schedule["id"])
            excerpt = report.split("\n## Sources", 1)[0][:2_500]
            body = (
                f"Hi {user.full_name},\n\nYour {schedule['frequency']} Fieldwork brief is ready: {title}\n\n"
                + (f"What changed since last time:\n{changes}\n\n" if changes else "")
                + f"{excerpt}\n\n(Shortened. Open the full brief with its sources in Fieldwork: {current.reset_link_base}/)\n\n"
                "To stop these emails, open Recurring research in Fieldwork and pause or delete this schedule.\n"
            )
            get_mailer().send(user.email, f"Fieldwork: {title}", body)
        except Exception:
            logger.exception("Could not email the result of schedule %s", schedule["id"])

    async def run_due_schedules() -> int:
        """Run every due schedule once. Returns how many were started."""
        due = await asyncio.to_thread(get_store().claim_due, _iso(datetime.now(timezone.utc)), _next_run, 3)
        for schedule in due:
            try:
                await run_schedule(schedule)
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.exception("Scheduled run %s failed", schedule["id"])
                await asyncio.to_thread(get_store().finish_schedule, schedule["id"], None, "failed")
        return len(due)

    async def scheduler_loop() -> None:
        wake = app.state.scheduler_wakeup
        while True:
            try:
                await run_due_schedules()
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.exception("Scheduler pass failed")
            try:
                await asyncio.wait_for(wake.wait(), timeout=live_settings().scheduler_interval_seconds)
            except TimeoutError:
                pass
            wake.clear()

    def schedule_owner(request: Request) -> str:
        user_id = getattr(request.state, "user_id", None)
        if user_id is None:
            raise HTTPException(status_code=403, detail="Recurring research needs a signed-in account.")
        return user_id

    def public_schedule(row: dict[str, Any]) -> dict[str, Any]:
        return {key: value for key, value in row.items() if key != "user_id"}

    @app.get("/research/schedules")
    async def list_schedules(request: Request) -> dict[str, Any]:
        user_id = schedule_owner(request)
        rows = await asyncio.to_thread(get_store().list_schedules, user_id)
        return {"schedules": [public_schedule(r) for r in rows], "limit": live_settings().max_schedules_per_user}

    @app.post("/research/schedules", status_code=201)
    async def create_schedule(body: ScheduleRequest, request: Request) -> dict[str, Any]:
        user_id = schedule_owner(request)
        current = live_settings()
        account = await asyncio.to_thread(get_store().get_by_id, user_id)
        if account is None:
            raise HTTPException(status_code=401, detail="Your account no longer exists. Please sign in again.")
        if is_demo_user(account, current):
            raise HTTPException(status_code=403, detail="The shared demo account cannot schedule research. Create your own account.")
        if await asyncio.to_thread(get_store().count_schedules, user_id) >= current.max_schedules_per_user:
            raise HTTPException(
                status_code=409,
                detail=f"You can keep up to {current.max_schedules_per_user} recurring researches. Delete one to add another.",
            )
        row = await asyncio.to_thread(
            get_store().create_schedule, user_id, body.topic, body.frequency, body.max_iterations, _next_run(body.frequency),
            stored_options(body.options) if body.options else None,
        )
        return public_schedule(row)

    @app.get("/research/templates")
    async def list_templates(request: Request) -> dict[str, Any]:
        user_id = schedule_owner(request)
        return {"templates": await asyncio.to_thread(get_store().list_templates, user_id), "limit": MAX_TEMPLATES}

    @app.post("/research/templates", status_code=201)
    async def create_template(body: TemplateRequest, request: Request) -> dict[str, Any]:
        user_id = schedule_owner(request)
        store = get_store()
        if await asyncio.to_thread(store.count_templates, user_id) >= MAX_TEMPLATES:
            raise HTTPException(status_code=409, detail=f"You can keep up to {MAX_TEMPLATES} templates. Delete one to add another.")
        return await asyncio.to_thread(store.create_template, user_id, body.name, body.topic, stored_options(body.options))

    @app.delete("/research/templates/{template_id}", status_code=204)
    async def delete_template(template_id: str, request: Request) -> Response:
        user_id = schedule_owner(request)
        if not await asyncio.to_thread(get_store().delete_template, template_id, user_id):
            raise HTTPException(status_code=404, detail="Template not found.")
        return Response(status_code=204)

    @app.patch("/research/schedules/{schedule_id}")
    async def patch_schedule(schedule_id: str, body: SchedulePatch, request: Request) -> dict[str, Any]:
        user_id = schedule_owner(request)
        store = get_store()
        row = await asyncio.to_thread(store.get_schedule, schedule_id, user_id)
        if row is None:
            raise HTTPException(status_code=404, detail="Schedule not found.")
        # Resuming starts a fresh period instead of firing immediately for a long-past date.
        await asyncio.to_thread(
            store.update_schedule, schedule_id, user_id, enabled=body.enabled,
            next_run_at=_next_run(row["frequency"]) if body.enabled and not row["enabled"] else None,
        )
        return public_schedule(await asyncio.to_thread(store.get_schedule, schedule_id, user_id))

    @app.delete("/research/schedules/{schedule_id}", status_code=204)
    async def remove_schedule(schedule_id: str, request: Request) -> Response:
        if not await asyncio.to_thread(get_store().delete_schedule, schedule_id, schedule_owner(request)):
            raise HTTPException(status_code=404, detail="Schedule not found.")
        return Response(status_code=204)

    @app.post("/research/schedules/{schedule_id}/run", status_code=202)
    async def run_schedule_now(schedule_id: str, request: Request) -> dict[str, Any]:
        user_id = schedule_owner(request)
        store = get_store()
        row = await asyncio.to_thread(store.get_schedule, schedule_id, user_id)
        if row is None:
            raise HTTPException(status_code=404, detail="Schedule not found.")
        await asyncio.to_thread(
            store.update_schedule, schedule_id, user_id, enabled=True, next_run_at=_iso(datetime.now(timezone.utc))
        )
        app.state.scheduler_wakeup.set()
        return {"detail": "Started. The new brief will appear in your history shortly."}

    @app.post("/research/runs/{thread_id}/ask")
    async def ask_followup(thread_id: str, body: FollowUpRequest, request: Request) -> dict[str, Any]:
        try:
            uuid.UUID(thread_id)
        except ValueError as exc:
            raise HTTPException(status_code=404, detail="Research thread was not found.") from exc
        user_id = getattr(request.state, "user_id", None)
        row = await asyncio.to_thread(get_store().get_run, thread_id, user_id)
        if row is None:
            raise HTTPException(status_code=404, detail="Research thread was not found.")
        report = public_run(row).get("report")
        if not report:
            raise HTTPException(status_code=409, detail="Follow-up questions are available after the report is complete.")
        now = datetime.now(timezone.utc).timestamp()
        key = str(user_id or "service")
        recent = [t for t in app.state.followup_times.get(key, []) if now - t < 3600]
        if len(recent) >= FOLLOWUPS_PER_HOUR:
            raise HTTPException(status_code=429, detail="Follow-up limit reached. Try again in a little while.")
        recent.append(now)
        app.state.followup_times[key] = recent
        try:
            answer = await asyncio.to_thread(
                app.state.followup_fn, report, body.question, live_settings()
            )
        except ValueError as exc:
            raise HTTPException(status_code=503, detail="Follow-up questions are not configured on the server.") from exc
        except Exception as exc:
            logger.warning("Follow-up failed on thread %s: %s", thread_id, type(exc).__name__)
            raise HTTPException(status_code=502, detail="The model could not answer right now. Try again.") from exc
        if not answer:
            raise HTTPException(status_code=502, detail="The model returned an empty answer. Try again.")
        return {"question": body.question, "answer": answer}

    @app.post("/research/runs/{thread_id}/visuals")
    async def research_visuals(thread_id: str, body: VisualResearchRequest, request: Request) -> dict[str, Any]:
        try:
            uuid.UUID(thread_id)
        except ValueError as exc:
            raise HTTPException(status_code=404, detail="Research thread was not found.") from exc
        await require_run_access(request, thread_id)
        graph = app.state.research_graph
        if graph is None:
            raise HTTPException(status_code=404, detail="Research thread was not found.")
        snapshot = await graph.aget_state({"configurable": {"thread_id": thread_id}})
        values = snapshot.values or {}
        if not values.get("report"):
            raise HTTPException(status_code=409, detail="Visual search is available after the report is complete.")
        active_settings = live_settings()
        if not active_settings.tavily_api_key:
            raise HTTPException(status_code=503, detail="Visual search is not configured on the server.")

        topic = str(values.get("topic") or "")[:1_000]
        searchers = {
            "images": _image_assets,
            "videos": _video_assets,
            "models": _model_assets,
        }
        selected = {name: searchers[name] for name in body.types}
        outcomes = await asyncio.gather(
            *(
                asyncio.to_thread(
                    searcher,
                    TavilyClient(api_key=active_settings.tavily_api_key.get_secret_value()),
                    topic,
                )
                for searcher in selected.values()
            ),
            return_exceptions=True,
        )
        response: dict[str, Any] = {"images": [], "videos": [], "models": [], "warnings": []}
        for name, outcome in zip(selected, outcomes, strict=True):
            if isinstance(outcome, Exception):
                logger.warning("Visual search failed for %s on thread %s: %s", name, thread_id, type(outcome).__name__)
                response["warnings"].append(f"{name.title()} could not be loaded right now.")
            else:
                response[name] = outcome
        return response

    app.state.followup_fn = answer_followup
    app.state.scheduler_wakeup = asyncio.Event()
    app.state.run_due_schedules = run_due_schedules
    app.state.followup_times = {}
    app.state.email_times = {}
    return app


app = create_app()


def run() -> None:
    import uvicorn

    uvicorn.run("research_assistant.api:app", host="127.0.0.1", port=8000, reload=False)
