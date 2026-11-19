# Multi Agent Research Assistant

Repository layout:

- `Backend/` contains the Python LangGraph workflow, CLI, package configuration, and backend setup documentation.
- `frontend/` is the home for the user interface.

## Application

- `Backend/` contains the FastAPI API and LangGraph planner, parallel researchers, reviewer, and report writer.
- `frontend/` contains the Vite, React, and TypeScript research workspace.

See [Backend/README.md](Backend/README.md) for provider credentials, the authenticated SSE API, and durable checkpoint deployment. See [frontend/README.md](frontend/README.md) for client setup and build instructions.

Run the research evaluation suite from `Backend/` with `research-assistant-eval` after configuring Groq and Tavily credentials.

Completed research reports can also fetch visual context on demand: related images, YouTube videos, and interactive Sketchfab 3D models. Visual search uses the configured Tavily key and does not require additional provider keys.

For local development, configure the backend `.env` with Groq and Tavily keys, an optional `AUTH_SECRET_KEY` (sessions then survive restarts) and `CORS_ORIGINS=["http://127.0.0.1:5173","http://localhost:5173"]`. The frontend `.env` points Vite to `http://127.0.0.1:8000`. Start FastAPI from `Backend/`, then run `npm install` and `npm run dev` from `frontend/`.

## Production deployment

The repository includes a Docker Compose deployment with PostgreSQL checkpoint persistence, a non-root API container, an unprivileged static frontend, and Caddy for HTTPS termination. Users sign in with their own accounts (scrypt-hashed passwords, signed expiring sessions); runs are private to their owner.

1. Point a DNS name at the server and allow inbound TCP ports 80 and 443.
2. Copy `.env.production.example` to `.env.production`. Set `SITE_ADDRESS`, and make `CORS_ORIGINS` and `ALLOWED_HOSTS` match that exact hostname.
3. Generate random secrets locally (for example, `python -c "import secrets; print(secrets.token_hex(32))"`) for `POSTGRES_PASSWORD` and `AUTH_SECRET_KEY` (and `RESEARCH_API_TOKEN` if using legacy clients). Add fresh Groq and Tavily API keys. Registration is disabled by default in production; set `ALLOW_REGISTRATION=true` only during controlled onboarding. Keep `.env.production` and database backups out of source control.
4. Start the stack with `docker compose --env-file .env.production -f compose.production.yaml up -d --build`.
5. Create the first account with `research-assistant-create-user` (run inside the API container), since registration is closed by default. `RESEARCH_API_TOKEN` is only a legacy credential for scripts; it is not needed for the web app.

Caddy obtains and renews TLS certificates when DNS and firewall routing are correct. PostgreSQL data is stored in the `postgres_data` volume; configure encrypted off-host backups and test restores before relying on the service. The API container runs one worker because its concurrency limit and current run orchestration are process-local. Scale-out needs shared rate limiting and concurrency controls, migration coordination, and load testing. Pin container images by digest and scan/update them as part of release operations.

The default research budget is moderate: four research questions, one review pass, four search results per question (1200-character excerpts), and model outputs of up to 3500 tokens for the report and 1024 per researcher summary, producing a brief of roughly 5-8 thousand characters. Free-tier Groq limits can throttle this; lower `INITIAL_QUESTIONS`, `SEARCH_RESULTS_PER_QUERY` and the token settings if you see rate-limit errors. One run and one graph task execute at a time, and transient Groq 429s retry with backoff. Increase `INITIAL_QUESTIONS`, `MAX_ITERATIONS`, `SEARCH_RESULTS_PER_QUERY`, or the `GROQ_*_MAX_TOKENS` settings when a topic needs more depth and your provider tier supports the added usage.

Accounts support password change and email reset (set `SMTP_*`; without SMTP, production hides password reset). Each user is capped by `MAX_RUNS_PER_USER_PER_DAY` and `MAX_ACTIVE_RUNS_PER_USER`. Not yet implemented: email verification, OIDC/SSO, audit logging, and multi-worker rate limiting (login throttling is in-memory, so keep one worker). In production, startup refuses missing provider secrets, a durable database, wildcard CORS, or non-HTTPS browser origins. The development in-memory checkpoint is for local use only.

## Features

- Research history: every run is saved per user; reopen, delete, or resume after a refresh. Interrupted runs are flagged and can be re-run.
- Cancel a running job, and pick research depth (1-3 review passes) per run.
- Export a brief as Markdown, a standalone HTML page (opens in Word), or print/save as PDF. Citations render as a Sources list.
- Follow-up questions on a finished brief, answered only from that brief; the writer sees source excerpts and invalid citation numbers are stripped.

## Testing

`cd Backend && pip install -e ".[dev]" && pytest`. Set `TEST_DATABASE_URL` (libpq string) to also run the suite against PostgreSQL. CI (`.github/workflows/ci.yml`) runs backend tests with a Postgres service and the frontend build.

## Public demo

See [DEPLOY.md](DEPLOY.md) for a free-tier Render deployment (`render.yaml`) with a single capped demo login.
