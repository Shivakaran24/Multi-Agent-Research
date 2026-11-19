# Deploying a public demo (Render, free tier)

Goal: a public HTTPS link you can put on a CV, with one shared demo login so strangers cannot
burn your Groq/Tavily quota.

## 1. Before you start
- Rotate your Groq and Tavily keys if they ever sat in a file you committed or pasted anywhere.
- Push the repo to GitHub (secrets must NOT be committed; `.env` files are git-ignored).
- Decide the demo login, e.g. `demo@yourname.dev` plus a password you are happy to share.

## 2. Create the services
1. Sign in at render.com with GitHub.
2. **New -> Blueprint**, choose this repository. Render reads `render.yaml` and proposes:
   a PostgreSQL database, the API (Docker) and the static frontend.
3. When asked for secret values, enter: `GROQ_API_KEY`, `TAVILY_API_KEY`, `DEMO_EMAIL`,
   `DEMO_PASSWORD` (8+ characters with a letter and a number). `AUTH_SECRET_KEY` is generated for you.
4. Apply. The first build takes several minutes.

## 3. If Render renamed a service
Service names are global. If it created e.g. `fieldwork-api-shivakaran24-x1y2`, open the API service ->
Environment and set:
- `ALLOWED_HOSTS` = `["<your-api-host>.onrender.com"]`
- `CORS_ORIGINS` = `["https://<your-web-host>.onrender.com"]`

then open the web service -> Environment and set `VITE_API_URL` = `https://<your-api-host>.onrender.com`,
and **Manual Deploy -> Clear build cache & deploy** (the API address is baked in at build time).

## 4. Check it
- `https://<api-host>.onrender.com/readyz` returns OK.
- Open the web URL, sign in with the demo login, run a topic.

## Things to know for a showcase
- Free web services sleep after ~15 minutes idle; the first request takes about a minute.
  Open the site a couple of minutes before a demo.
- Free Render PostgreSQL databases expire after 30 days (history and the demo account are lost).
  Recreate it, or upgrade the database plan, if the link needs to live longer.
- The demo login is capped at 5 runs/day total (`MAX_RUNS_PER_USER_PER_DAY`). Raise it for a live demo. `MAX_TOKENS_PER_USER_PER_DAY` (150,000 in `render.yaml`) caps model usage the same way.
- Recurring research only runs while the API is awake. Render's free tier sleeps after about 15 minutes without traffic, so a schedule fires when the service next wakes (it catches up on the first pass). For on-time runs, keep the service awake with a free uptime monitor (for example UptimeRobot) pinging `/healthz` every 5 minutes, or use a paid instance. Emails need SMTP (see README).
- Public sign-up is off. To let others register, set `ALLOW_REGISTRATION=true` (anyone with the link
  can then create accounts and spend your API quota).
- Password reset email needs `SMTP_*` settings; without them the "Forgot password" link is hidden.
