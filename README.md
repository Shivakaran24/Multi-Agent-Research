# Fieldwork — Multi Agent Research Assistant

Fieldwork turns a topic into a cited research brief. You type a question, an AI planner breaks it into focused sub-questions, several researcher agents search the web in parallel, a reviewer checks for gaps, and a writer produces a structured report with numbered sources. You stay in control: you can edit the plan before research starts, ask follow-up questions, export the result, share it, or schedule it to repeat.

- **Backend:** Python, FastAPI and LangGraph (`Backend/`)
- **Frontend:** React, TypeScript and Vite (`frontend/`)
- **Models and search:** Groq (`openai/gpt-oss-20b` by default) and Tavily
- **Storage:** SQLite in development, PostgreSQL in production

## What it does

1. **Plan.** The lead planner proposes research questions for your topic. You can approve or edit them (human-in-the-loop).
2. **Research.** One researcher per question runs in parallel: search, filter and rank sources, read excerpts, summarise findings.
3. **Review.** A reviewer decides whether the findings have gaps and, if so, requests another pass (1–3 passes, your choice).
4. **Write.** The writer produces the brief in your chosen style, audience, length and language. Citations are validated against the real sources, so invented numbers are stripped.
5. **Verify.** Before writing, every source is opened on the live web and must load and match what the search reported (papers with a DOI or arXiv ID are confirmed against Crossref/arXiv). Sources that fail are dropped, and the writer sees passages taken from the verified pages. After writing, a claim check removes any statement the verified sources do not support. If no source can be verified, no brief is written.

### Features

**Research quality**
- Source filtering: duplicates, thin pages and blocked domains are dropped; authoritative hosts (.gov, .edu, .int and a preferred list) rank higher.
- Per-run time window (day, week, month, year).
- Verified-only mode (default): live source checks plus removal of unsupported statements. It costs extra time and tokens per run; set `VERIFIED_ONLY=false` to go back to the older marked-as-unverified claim check.
- Your own sources: up to 5 links, up to 3 PDFs (text layer, 5 MB each) and pasted notes are treated as sources alongside web results. Uploaded PDFs are cited as uploaded files and kept only for that run.

**Control over output**
- Styles: brief, executive, deep dive, comparison. Audience, length and output language are selectable.
- Compare two topics in one report.
- Depth: 1–3 review passes.

**Workflow**
- Live progress lines show what each researcher found.
- History per user, with search and tags. Interrupted runs can be re-run; running jobs can be cancelled.
- Follow-up questions answered only from the finished brief.
- Recurring research (daily, weekly, monthly) with an emailed "what changed" note.
- Saved templates: store a topic plus all options (style, audience, length, language, time window, links, notes) under a name and apply it in one click. Recurring research can reuse the composer's options.
- Email a brief to yourself.
- Export to Markdown, HTML, Word (.docx, built in the browser) or print/PDF.
- Private-by-default read-only share links, revocable at any time.
- Thumbs up/down feedback on each brief.
- Citation hover cards showing the source behind each number.
- Related images, YouTube videos and Sketchfab 3D models on demand (uses the Tavily key).

**Accounts and cost control**
- Email and password accounts (scrypt hashes, signed expiring sessions), password reset by email, optional email verification.
- Optional invite codes for registration, and an admin usage page for emails listed in `ADMIN_EMAILS`.
- Model fallback: set `GROQ_FALLBACK_MODEL` and a rate-limited (HTTP 429) call switches to that second Groq model immediately, since Groq limits apply per model.
- Per-user daily run limit, concurrent-run limit and daily token budget, with a live "tries and tokens left" counter.
- Optional demo account for public showcases.
- Dark mode (follows the system setting; toggle in the top bar).

## Architecture

```
Browser (React + Vite)
   │  HTTPS, JSON + Server-Sent Events (progress stream)
   ▼
FastAPI  ── auth, rate limits, token budgets, request logging
   │         scheduler (background task for recurring research)
   ▼
LangGraph workflow
   START → lead planner → human plan review (interrupt)
         → researchers (parallel fan-out via Send)
         → reviewer ──(gaps? loop back)──┐
         → writer (+ citation sanitising, claim check) → END
   │                 │
   ▼                 ▼
Groq LLM         Tavily search / extract
   │
   ▼
SQLite (dev) / PostgreSQL (prod): users, runs, schedules, tokens, checkpoints
```

How the pieces fit:

- `Backend/research_assistant/graph.py` wires the graph; `nodes.py` holds the planner, researcher, reviewer and writer; `state.py` and `schemas.py` define the shared state and API models.
- The plan-review step uses LangGraph's `interrupt` and resumes with `Command(resume=...)`, so a run can wait for you across refreshes. Checkpoints are durable in PostgreSQL.
- `api.py` exposes the REST and SSE endpoints and runs limits, usage tracking and the scheduler. `auth.py` owns accounts, runs, schedules and the database schema (with idempotent migrations).
- `sources.py` (filter and rank), `claims.py` (claim check), `usersources.py` (your links), `followup.py` (follow-up answers) and `usage.py` (token counting via a LangChain callback) are small single-purpose modules.
- `frontend/src/` holds the UI. `App.tsx` is the workspace; `report.ts`, `citations.ts` and `docx.ts` render and export briefs; `runs.ts` is the API client.

## Repository layout

```
Backend/        FastAPI app, LangGraph workflow, CLI, tests, evals
frontend/       Vite + React + TypeScript client and unit tests
render.yaml     Render Blueprint (free-tier deployment)
compose.production.yaml, Caddyfile   Docker Compose + HTTPS deployment
DEPLOY.md       Free-tier public demo guide
```

## Setup (local development)

You need Python 3.11+, Node 22.6+, a [Groq](https://console.groq.com) API key and a [Tavily](https://tavily.com) API key.

**Backend**
```bash
cd Backend
python -m venv .venv && source .venv/bin/activate    # Windows: .venv\Scripts\activate
pip install -e ".[dev]"
cp .env.example .env                                  # then add GROQ_API_KEY and TAVILY_API_KEY
research-assistant-api                                 # serves on http://127.0.0.1:8000
```
Leave `AUTH_SECRET_KEY` empty in development (sessions then end on restart), or set it to keep sessions.

**Frontend**
```bash
cd frontend
npm install
echo VITE_API_URL=http://127.0.0.1:8000 > .env
npm run dev                                           # http://localhost:5173
```
Create an account on the sign-in page and start researching. `CORS_ORIGINS` in the backend `.env` must include `http://localhost:5173` and `http://127.0.0.1:5173` (the example file does).

**Tests**
```bash
cd Backend && pytest              # set TEST_DATABASE_URL to also run against PostgreSQL
cd frontend && npm test           # Node 22.6+
```
Evaluation suite (needs real keys): `research-assistant-eval` from `Backend/`.

## Configuration

All settings are environment variables; see `Backend/.env.example` for the full list. The important ones:

| Setting | Purpose |
|---|---|
| `GROQ_API_KEY`, `TAVILY_API_KEY` | Model and search providers (required) |
| `AUTH_SECRET_KEY` | Signs sessions (required in production) |
| `DATABASE_URL` | PostgreSQL connection string (required in production) |
| `CORS_ORIGINS`, `ALLOWED_HOSTS` | Exact frontend origin and API hostname (production refuses wildcards) |
| `ALLOW_REGISTRATION`, `INVITE_CODES`, `ADMIN_EMAILS` | Who can sign up and who sees admin stats |
| `MAX_RUNS_PER_USER_PER_DAY`, `MAX_TOKENS_PER_USER_PER_DAY`, `MAX_ACTIVE_RUNS_PER_USER` | Cost controls (0 disables a cap) |
| `INITIAL_QUESTIONS`, `MAX_ITERATIONS`, `SEARCH_RESULTS_PER_QUERY`, `GROQ_*_MAX_TOKENS` | Research depth and size |
| `SMTP_*`, `APP_BASE_URL` | Password reset, verification, emailed briefs and schedules |
| `REQUIRE_EMAIL_VERIFICATION`, `CLAIM_CHECK`, `SCHEDULER_ENABLED` | Feature switches |
| `GROQ_FALLBACK_MODEL` | Second Groq model used when the main one is rate limited (e.g. `llama-3.3-70b-versatile`) |

The default budget is moderate (four questions, one review pass, four results per question). Free Groq tiers can rate-limit it; lower `INITIAL_QUESTIONS`, `SEARCH_RESULTS_PER_QUERY` or the token settings if you see 429 errors. Transient 429s are retried with backoff.

## Deployment

**Free public demo (Render):** follow [DEPLOY.md](DEPLOY.md). `render.yaml` creates the API, a PostgreSQL database and the static frontend. Set `CORS_ORIGINS` and `ALLOWED_HOSTS` to your real service URLs (hostname only for `ALLOWED_HOSTS`); a mismatch shows up as a 400 error on `/auth/config`. Free instances sleep after about 15 minutes idle, and recurring research only fires while the API is awake (ping `/healthz` with an uptime monitor to keep it alive).

**Self-hosted (Docker Compose + HTTPS):**
1. Point a DNS name at the server and open TCP ports 80 and 443.
2. Copy `.env.production.example` to `.env.production`; set `SITE_ADDRESS` and make `CORS_ORIGINS` and `ALLOWED_HOSTS` match it.
3. Generate secrets (`python -c "import secrets; print(secrets.token_hex(32))"`) for `POSTGRES_PASSWORD` and `AUTH_SECRET_KEY`, and add the Groq and Tavily keys. Keep this file out of source control.
4. `docker compose --env-file .env.production -f compose.production.yaml up -d --build`
5. Create the first account with `research-assistant-create-user` inside the API container (registration is closed by default).

Caddy obtains and renews TLS certificates. The API runs a single worker because rate limiting and run orchestration are process-local; scaling out needs shared limits and load testing. Configure off-host database backups and test restores.

## Limitations

- Google/OIDC sign-in, audit logging and multi-worker rate limiting are not implemented.
- PDFs must have a text layer (scanned images are not read); only the first 60 pages and 30,000 characters are used. Scheduled runs reuse saved options but not links or PDFs.
- The brief appears when the writer finishes, not word by word.
- Verification proves a source opens and matches what was reported, and the claim check is a model judgement. Neither guarantees a claim is true, and paywalled sites that block automated requests are dropped. Read the cited sources for anything important.

More detail: [Backend/README.md](Backend/README.md) (API, credentials, checkpoints) and [frontend/README.md](frontend/README.md).
