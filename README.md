# GarhAI ✦

A personal AI assistant powered by Claude — a lightweight full-stack chat app with real-time streaming responses.

## Stack

- **Backend** — Node.js 20+, TypeScript, Express 5
- **AI** — [`@anthropic-ai/sdk`](https://github.com/anthropics/anthropic-sdk-typescript) calling Claude Opus 5 (`claude-opus-5`) with adaptive thinking and server-side refusal fallbacks
- **Frontend** — dependency-free vanilla JS chat UI with Server-Sent Events streaming, a live "thinking" panel, and safe Markdown rendering
- **Hosting** — [Railway](https://railway.com), deployed automatically from GitHub

## Local development

```bash
npm install
cp .env.example .env   # add your ANTHROPIC_API_KEY
npm run dev            # http://localhost:3000
```

Production build:

```bash
npm run build
npm start
```

## API

| Endpoint | Method | Description |
|---|---|---|
| `/` | GET | Chat UI |
| `/health` | GET | Health/status JSON (used by Railway healthcheck) |
| `/api/chat` | POST | `{messages: [{role, content}, ...]}` → SSE stream of `{type: "text"\|"thinking"\|"notice"\|"error"\|"done", ...}` events |

## Deployment (Railway)

The app deploys on Railway from this GitHub repo. `railway.json` configures the build (`npm ci && npm run build`), start command (`npm start`), and the `/health` healthcheck.

Required service variable:

- `ANTHROPIC_API_KEY` — your Anthropic API key ([console.anthropic.com](https://console.anthropic.com/settings/keys))

Optional:

- `GARHAI_MODEL` — override the Claude model (defaults to `claude-opus-5`)

Until the API key is set, the app boots fine and the UI shows a setup banner; chat requests return a friendly configuration error.

## Environment variables

See [.env.example](.env.example).
