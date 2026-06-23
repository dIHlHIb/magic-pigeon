# magic-pigeon — web frontend

A ChatGPT-style React UI for the magic-pigeon agent backend (Flask + Socket.IO,
`server.py`). Vite + React + TypeScript, talking to the backend over Socket.IO
(live agent stream) and REST (sessions / memories / search / config).

The backend is **not** modified — this app speaks the protocol it already
exposes.

## Prerequisites

- Node 18+ (developed on Node 26) and npm
- The Python backend running on `http://127.0.0.1:5001` (`python server.py`)

## Setup

```bash
npm install
```

## Develop

1. Start the backend: `python ../server.py`. It prints an access URL with a
   token, e.g. `http://127.0.0.1:5001/?token=ABC123`.
2. Start the frontend dev server: `npm run dev` (serves on `http://localhost:5173`).
3. Open the dev server **carrying the same token**:
   `http://localhost:5173/?token=ABC123`.

Vite proxies `/api` and `/socket.io` to the backend (`vite.config.ts`), so the
browser only ever talks to its own origin — this sidesteps the backend's strict
CORS allowlist (and its REST API sending no CORS headers) without touching the
backend. The proxy also rewrites the `Origin` header on the WebSocket handshake
so Socket.IO's CORS check passes.

The token is read from `?token=` once and persisted in `sessionStorage`, then
sent as `X-Auth-Token` on REST calls and in the Socket.IO handshake `auth`.

## Build

```bash
npm run build     # type-checks (tsc -b) then bundles to dist/
npm run preview   # preview the production build
```

### Serving the build from Flask (optional)

The backend serves its shell from `static/` (`/` → `static/index.html`,
assets under `/static/`). To have Flask serve this app in production, build with
the static base/output and point the output at `../static` — e.g. run
`vite build --base=/static/ --outDir=../static --emptyOutDir`. This overwrites
the existing single-file frontend in `static/`, so do it deliberately. In dev
just use the proxy above.

## Architecture

```
src/
  types.ts                 protocol + view-model types (the backend contract)
  lib/
    auth.ts                token from URL → sessionStorage → X-Auth-Token
    api.ts                 REST client (sessions / memories / search / config)
    socket.ts              socket.io-client factory (same-origin + token auth)
    chat.ts                pure helpers: history → chat items, timestamp strip, …
  hooks/
    useAgentSocket.ts      owns the live connection + chat view-model + actions
    useSessions.ts         loads the sidebar session list (REST)
  components/
    Sidebar / Topbar / ChatView / MessageRow
    MarkdownMessage / CodeBlock      markdown + syntax highlight + copy
    ToolCard / DangerCard / Composer
  App.tsx                  wires it all together
```

The Socket.IO event stream is translated into a flat list of renderable chat
items in `useAgentSocket`: streamed `stream` chunks accumulate into one
assistant bubble, `tool_use`/`tool_result` become collapsible cards (matched by
id), a `dangerous` event raises a red Allow/Deny card, and the composer's send
button becomes a stop button between `turn_start` and `turn_done`.
