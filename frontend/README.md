# Fieldwork frontend

Vite + React + TypeScript client for the LangGraph research API.

## Run locally

```powershell
cd frontend
npm install
npm run dev
```

Set `VITE_API_URL` in a frontend `.env` file when the API is not at `http://127.0.0.1:8000`; it is baked in at build time, so rebuild after changing it. Set the matching exact frontend origin in the backend `CORS_ORIGINS` JSON array.

## Accounts and sessions

Users register or sign in on the first screen (registration can be closed on the server, in which case accounts are created with `research-assistant-create-user`). A shared demo login can be seeded with `DEMO_EMAIL`/`DEMO_PASSWORD`; the demo account cannot change its password or be deleted. The session token is kept in page memory only; the last open run id is kept in `sessionStorage` so a refresh restores it. Research content is never written to `localStorage`.

## Keyboard

`Ctrl+K` / `Cmd+K` starts a new research.

## Build

Run `npm run build` to produce the static bundle in `dist/`. Serve it over HTTPS and configure the API URL at build time. Keep provider keys on trusted infrastructure; never embed them in the bundle. See `DEPLOY.md` in the repository root for the Render deployment.

Completed reports include an on-demand Visual context panel. It retrieves image results, YouTube explainers, and Sketchfab models through the authenticated backend. Media embeds are loaded only when a user clicks Preview. The page links to each publisher; check its reuse terms before redistributing media. Production Content Security Policy settings should allow the selected YouTube, Sketchfab, and image hosts if embeds are enabled.
