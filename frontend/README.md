# Fieldwork frontend

Vite + React + TypeScript client for the LangGraph research API.

## Run locally

```powershell
cd frontend
npm install
npm run dev
```

Set `VITE_API_URL` in a frontend `.env` file when the API is not at `http://127.0.0.1:8000`. Set the matching exact frontend origin in the backend `CORS_ORIGINS` JSON array. The API requires a bearer token; enter it in Workspace settings. The client keeps the token in page memory only and never writes credentials or research content to browser storage.

Run `npm run build` to produce the static production bundle in `dist/`. Serve it over HTTPS behind a production web server and configure the API URL at build time. Keep provider keys and the research API token on trusted infrastructure; do not embed them in the frontend bundle.

Completed reports include an on-demand Visual context panel. It retrieves image results, YouTube explainers, and Sketchfab models through the authenticated backend. Media embeds are loaded only when a user clicks Preview. The page links to each publisher; check its reuse terms before redistributing media. Production Content Security Policy settings should allow the selected YouTube, Sketchfab, and image hosts if embeds are enabled.
